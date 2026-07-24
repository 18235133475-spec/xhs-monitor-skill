"""日抓：发现各监测账号的新发布笔记，并采集其首组互动指标。

流程：校验登录 → 逐账号抓主页笔记列表（evaluate 一次提取）
     → 与 notes.jsonl 比对识别新增 → 仅对新增笔记进详情页采「赞/藏/评/发布时间」
     → 追加 notes.jsonl / metrics.jsonl → 输出 JSON 摘要。
存量笔记的指标刷新由 weekly.py 负责，日抓不重复回访。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (STATE_DIR, NOTES_PATH, METRICS_PATH, ensure_playwright, launch,
                    check_login, detect_block, human_delay, append_jsonl, load_jsonl,
                    load_config, parse_count, parse_publish_time, today_str,
                    EXTRACT_CARDS_JS, EXTRACT_DETAIL_JS, emit)  # noqa: E402


def scan_profile(page, profile_url, max_scrolls):
    """打开账号主页，滚动加载后一次 evaluate 提取全部笔记卡片。"""
    page.goto(profile_url, timeout=45000, wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    for _ in range(max_scrolls):
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(1800)
    return page.evaluate(EXTRACT_CARDS_JS)


def visit_detail(page, url):
    page.goto(url, timeout=45000, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    return page.evaluate(EXTRACT_DETAIL_JS)


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

    known = {n["note_id"] for n in load_jsonl(NOTES_PATH)}
    today = today_str()
    summary = {"status": "ok", "date": today, "accounts": [], "new_notes": 0,
               "errors": []}

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
                    d = visit_detail(page, c["url"])
                except Exception as e:
                    summary["errors"].append({"account": name, "note_id": c["note_id"],
                                              "error": f"详情页失败: {e}"})
                    continue
                if detect_block(page):
                    emit(status="blocked",
                         message="详情页抓取触发风控，已熔断。请手动完成一次验证，明日再恢复。")
                    browser.close()
                    return

                note = {
                    "note_id": c["note_id"], "url": c["url"],
                    "account": name, "type": acc.get("type", "competitor"),
                    "title": d.get("title") or c.get("title") or "",
                    "publish_time": parse_publish_time(d.get("date")),
                    "first_seen": today,
                }
                append_jsonl(NOTES_PATH, note)
                append_jsonl(METRICS_PATH, {
                    "note_id": c["note_id"], "account": name, "date": today,
                    "likes": parse_count(d.get("like")) or parse_count(c.get("likes_text")),
                    "collects": parse_count(d.get("collect")),
                    "comments": parse_count(d.get("comment")),
                    "views": None, "source": "frontend",
                })
                known.add(c["note_id"])
                acc_new += 1
                human_delay(*delay)

            summary["accounts"].append({
                "name": name, "cards_seen": len(cards), "new_notes": acc_new})
            summary["new_notes"] += acc_new
            human_delay(*delay)

        browser.close()

    if summary["errors"] and summary["new_notes"] == 0 and not summary["accounts"]:
        summary["status"] = "failed"
    elif summary["errors"]:
        summary["status"] = "partial"
    emit(**summary)


if __name__ == "__main__":
    main()
