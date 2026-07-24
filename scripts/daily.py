"""日抓：发现各监测账号的新发布笔记，并采集其首组互动指标。

流程：校验登录 → 逐账号抓主页笔记列表（evaluate 一次提取）
     → 与 notes.jsonl 比对识别新增 → 对新增笔记「点击卡片开模态」采赞/藏/评/发布时间
     （禁止直跳详情页 URL，会触发 App 扫码风控墙）
     → 追加 notes.jsonl / metrics.jsonl → 输出 JSON 摘要（含 validation 自检）。
存量笔记的指标刷新由 weekly.py 负责，日抓不重复回访。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (STATE_DIR, NOTES_PATH, METRICS_PATH, ensure_playwright, launch,
                    check_login, detect_block, human_delay, append_jsonl, load_jsonl,
                    load_config, parse_count, parse_publish_time, today_str,
                    open_note_modal, close_note_modal, extract_detail_guarded,
                    validate_detail, confidence, save_validation_shot,
                    EXTRACT_CARDS_JS, emit)  # noqa: E402


def scan_profile(page, profile_url, max_scrolls):
    """打开账号主页，滚动加载后一次 evaluate 提取全部笔记卡片。"""
    page.goto(profile_url, timeout=45000, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    for _ in range(max_scrolls):
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(1800)
    return page.evaluate(EXTRACT_CARDS_JS)


def main():
    ensure_playwright()
    from playwright.sync_api import sync_playwright

    try:
        cfg = load_config()
    except FileNotFoundError as e:
        emit(status="config_missing", message=str(e))
        return

    state_file = os.path.join(STATE_DIR, "observer.json")
    if not os.path.exists(state_file):
        emit(status="need_login", message="未找到登录态，请先运行 login_bootstrap.py")
        return

    rate = cfg.get("rate", {})
    delay = (rate.get("min_delay", 3), rate.get("max_delay", 7))
    budget = rate.get("daily_detail_budget", 30)
    max_scrolls = cfg.get("daily", {}).get("max_scrolls", 6)
    cooldown = rate.get("wall_cooldown_seconds", 90)

    known = {n["note_id"] for n in load_jsonl(NOTES_PATH)}
    today = today_str()
    summary = {"status": "ok", "date": today, "accounts": [], "new_notes": 0,
               "errors": []}
    val = {"total_details": 0, "bad_details": 0, "detail_wall_hits": 0,
           "screenshots": []}

    with sync_playwright() as pw:
        browser, ctx = launch(pw, state_file)
        page = ctx.new_page()

        if not check_login(page):
            emit(status="need_login", message="登录态已失效，请重新运行 login_bootstrap.py")
            browser.close()
            return

        for acc in cfg.get("accounts", []):
            name, url = acc.get("name"), acc.get("profile_url", "")
            if "/user/profile/" not in url:
                summary["errors"].append({"account": name, "error": "profile_url 未配置或非法"})
                continue
            try:
                cards = scan_profile(page, url, max_scrolls)
            except Exception as e:
                summary["errors"].append({"account": name, "error": f"主页抓取失败: {e}"})
                continue

            if detect_block(page):
                emit(status="blocked",
                     message=f"抓取账号「{name}」时触发风控，已熔断。请手动打开小红书完成一次验证，明日再恢复任务。")
                browser.close()
                return

            new_cards = [c for c in cards if c["note_id"] not in known]
            acc_new = 0
            for c in new_cards:
                if budget <= 0:
                    summary["errors"].append({"account": name,
                                              "error": "详情页访问预算耗尽，剩余新笔记明日补抓"})
                    break
                budget -= 1
                try:
                    open_note_modal(page, c["note_id"])
                except Exception as e:
                    summary["errors"].append({"account": name, "note_id": c["note_id"],
                                              "error": f"模态打开失败: {e}"})
                    continue

                d, walled = extract_detail_guarded(page, c["note_id"], cooldown)
                val["total_details"] += 1
                if walled:
                    val["detail_wall_hits"] += 1
                    shot = save_validation_shot(page, c["note_id"])
                    if shot:
                        val["screenshots"].append(shot)
                    emit(status="blocked",
                         message="详情页持续命中「App 扫码查看」风控墙（冷却重试后仍被拦截），已熔断。"
                                 "建议 1-2 小时后或明日恢复任务；本地跑可用 XHS_HEADLESS=0 有头模式降低触发率。")
                    browser.close()
                    return

                missing = validate_detail(d)
                if missing:
                    val["bad_details"] += 1
                    shot = save_validation_shot(page, c["note_id"])
                    if shot:
                        val["screenshots"].append(shot)
                    summary["errors"].append({"account": name, "note_id": c["note_id"],
                                              "error": f"自检缺失字段: {','.join(missing)}"})

                note = {
                    "note_id": c["note_id"], "url": c["url"],
                    "account": name, "type": acc.get("type", "competitor"),
                    "title": (d.get("title") or c.get("title") or "") if d else c.get("title", ""),
                    "publish_time": parse_publish_time(d.get("date")) if d else None,
                    "first_seen": today,
                }
                append_jsonl(NOTES_PATH, note)
                append_jsonl(METRICS_PATH, {
                    "note_id": c["note_id"], "account": name, "date": today,
                    "likes": (parse_count(d.get("like")) if d else None)
                             or parse_count(c.get("likes_text")),
                    "collects": parse_count(d.get("collect")) if d else None,
                    "comments": parse_count(d.get("comment")) if d else None,
                    "views": None, "source": "frontend",
                })
                known.add(c["note_id"])
                acc_new += 1
                close_note_modal(page)
                human_delay(*delay)

            summary["accounts"].append({
                "name": name, "cards_seen": len(cards), "new_notes": acc_new})
            summary["new_notes"] += acc_new
            human_delay(*delay)

        browser.close()

    val["overall_confidence"] = confidence(val["total_details"], val["bad_details"])
    summary["validation"] = val
    if summary["errors"] and summary["new_notes"] == 0 and not summary["accounts"]:
        summary["status"] = "failed"
    elif summary["errors"] or val["bad_details"]:
        summary["status"] = "partial"
    emit(**summary)


if __name__ == "__main__":
    main()
