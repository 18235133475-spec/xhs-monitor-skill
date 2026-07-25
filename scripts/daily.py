"""日抓：发现各监测账号的新发布笔记，并采集其首组互动指标。

流程（v1.4 边滚边处理）：校验登录 → 逐账号滚动主页，每轮对当前 DOM 内的新卡片
立即点击开模态采「赞/藏/评/发布时间」（XHS 虚拟滚动会回收屏幕外卡片 DOM，
先收集再回头点必然 anchor not found）→ 追加 notes.jsonl / metrics.jsonl
→ 输出 JSON 摘要（含 validation 自检）。存量笔记指标刷新由 weekly.py 负责。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (NOTES_PATH, METRICS_PATH, ensure_playwright, launch, shutdown,
                    has_login_state, check_login, detect_block, human_delay,
                    append_jsonl, load_jsonl, load_config, parse_count,
                    parse_publish_time, today_str, iterate_profile_cards,
                    LoginWallError, open_note_modal, close_note_modal,
                    extract_detail_guarded, validate_detail, confidence,
                    save_validation_shot, on_profile_page, recover_profile,
                    emit)  # noqa: E402


def main():
    ensure_playwright()
    from playwright.sync_api import sync_playwright

    try:
        cfg = load_config()
    except FileNotFoundError as e:
        emit(status="config_missing", message=str(e))
        return

    if not has_login_state():
        emit(status="need_login", message="未找到登录态，请先运行 login_bootstrap.py")
        return

    rate = cfg.get("rate", {})
    delay = (rate.get("min_delay", 3), rate.get("max_delay", 7))
    budget = rate.get("daily_detail_budget", 30)
    cooldown = rate.get("wall_cooldown_seconds", 90)
    scroll = cfg.get("scroll", {})
    max_rounds = scroll.get("max_rounds", 40)
    stable_rounds = scroll.get("stable_rounds", 2)

    known = {n["note_id"] for n in load_jsonl(NOTES_PATH)}
    today = today_str()
    summary = {"status": "ok", "date": today, "accounts": [], "new_notes": 0,
               "errors": []}
    val = {"total_details": 0, "bad_details": 0, "detail_wall_hits": 0,
           "screenshots": []}

    with sync_playwright() as pw:
        browser, ctx = launch(pw)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        if not check_login(page):
            emit(status="need_login",
                 message="登录态已失效或被平台降级，请重新运行 login_bootstrap.py 扫码")
            shutdown(browser, ctx)
            return

        for acc in cfg.get("accounts", []):
            name, url = acc.get("name"), acc.get("profile_url", "")
            if "/user/profile/" not in url:
                summary["errors"].append({"account": name, "error": "profile_url 未配置或非法"})
                continue
            try:
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
            except Exception as e:
                summary["errors"].append({"account": name, "error": f"主页打开失败: {e}"})
                continue

            run = {"budget": budget, "stop": None, "acc_new": 0,
                   "recover": None, "failed": set()}

            def on_card(c):
                """对当前 DOM 内的一张新卡片立即处理；返回 False 中止本轮迭代。
                run["recover"] 置位表示页面已离开主页，需回主页恢复后重新迭代。"""
                nid = c["note_id"]
                if nid in known or nid in run["failed"]:
                    return True
                if run["budget"] <= 0:
                    summary["errors"].append({"account": name,
                                              "error": "详情页访问预算耗尽，剩余新笔记明日补抓"})
                    return False
                run["budget"] -= 1
                try:
                    open_note_modal(page, nid)
                except Exception as e:
                    summary["errors"].append({"account": name, "note_id": nid,
                                              "error": f"模态打开失败: {e}"})
                    run["failed"].add(nid)
                    if not on_profile_page(page):
                        # v1.4.2：撞墙后路由被跳走，必须回主页恢复，否则整轮空转
                        run["recover"] = "left_profile"
                        return False
                    return True

                try:
                    d, walled = extract_detail_guarded(page, nid, cooldown)
                except Exception as e:
                    # v1.4.1 总兜底：单卡任何异常只记错误、跳过，绝不中止整轮
                    summary["errors"].append({"account": name, "note_id": nid,
                                              "error": f"详情提取失败: {e}"})
                    run["failed"].add(nid)
                    if not on_profile_page(page):
                        run["recover"] = "left_profile"
                        return False
                    close_note_modal(page)
                    return True
                val["total_details"] += 1
                if walled:
                    val["detail_wall_hits"] += 1
                    shot = save_validation_shot(page, nid)
                    if shot:
                        val["screenshots"].append(shot)
                    run["stop"] = "blocked"
                    return False

                if d is None:
                    # 冷却重开后卡片被回收/页面跳走：不入库，回主页恢复后继续其余卡
                    summary["errors"].append({"account": name, "note_id": nid,
                                              "error": "撞墙冷却重开失败，回主页恢复后继续，本条明日补抓"})
                    run["failed"].add(nid)
                    run["recover"] = "card_recycled"
                    return False

                missing = validate_detail(d)
                if missing:
                    val["bad_details"] += 1
                    shot = save_validation_shot(page, nid)
                    if shot:
                        val["screenshots"].append(shot)
                    summary["errors"].append({"account": name, "note_id": nid,
                                              "error": f"自检缺失字段: {','.join(missing)}"})

                # v1.3 标题一致性校验：不一致记 title_mismatch，以详情页为准
                card_title = (c.get("title") or "").strip()
                detail_title = (d.get("title") or "").strip() if d else ""
                if card_title and detail_title and card_title != detail_title:
                    summary["errors"].append({
                        "account": name, "note_id": nid,
                        "error": f"title_mismatch: 卡片[{card_title[:20]}] != 详情[{detail_title[:20]}]，已采用详情标题"})

                append_jsonl(NOTES_PATH, {
                    "note_id": nid, "url": c["url"],
                    "account": name, "type": acc.get("type", "competitor"),
                    "title": detail_title or card_title,
                    "publish_time": parse_publish_time(d.get("date")) if d else None,
                    "first_seen": today,
                })
                append_jsonl(METRICS_PATH, {
                    "note_id": nid, "account": name, "date": today,
                    "likes": (parse_count(d.get("like")) if d else None)
                             or parse_count(c.get("likes_text")),
                    "collects": parse_count(d.get("collect")) if d else None,
                    "comments": parse_count(d.get("comment")) if d else None,
                    "views": None, "source": "frontend",
                })
                known.add(nid)
                run["acc_new"] += 1
                close_note_modal(page)
                human_delay(*delay)
                return True

            # v1.4.2 恢复循环：on_card 置 recover 时回主页重进继续抓，最多 2 次
            cards, reloads = [], 0
            while True:
                try:
                    cards, _ = iterate_profile_cards(page, on_card, max_rounds, stable_rounds)
                except LoginWallError:
                    emit(status="need_login",
                         message=f"抓取账号「{name}」时主页弹出登录墙，登录态掉线或被降级。"
                                 "请重新运行 login_bootstrap.py，并确认运行模式（有头/无头）与登录时一致。")
                    shutdown(browser, ctx)
                    return
                except Exception as e:
                    summary["errors"].append({"account": name, "error": f"主页抓取失败: {e}"})
                    break
                if run["recover"] and run["stop"] != "blocked":
                    if reloads >= 2:
                        summary["errors"].append({"account": name,
                            "error": "详情墙/卡片回收反复出现，回主页恢复 2 次仍失败，账号本轮中止"})
                        break
                    reloads += 1
                    run["recover"] = None
                    human_delay(*delay)
                    try:
                        recover_profile(page, url)
                    except Exception as e:
                        summary["errors"].append({"account": name,
                                                  "error": f"回主页恢复失败: {e}"})
                        break
                    continue
                break

            if run["stop"] == "blocked":
                emit(status="blocked",
                     message="详情页持续命中「App 扫码查看」风控墙（冷却重试后仍被拦截），已熔断。"
                             "建议 1-2 小时后或明日恢复任务；本地跑可用 XHS_HEADLESS=0 有头模式降低触发率。")
                shutdown(browser, ctx)
                return

            if detect_block(page):
                emit(status="blocked",
                     message=f"抓取账号「{name}」时触发风控，已熔断。请手动打开小红书完成一次验证，明日再恢复任务。")
                shutdown(browser, ctx)
                return

            budget = run["budget"]
            summary["accounts"].append({
                "name": name, "cards_seen": len(cards), "new_notes": run["acc_new"]})
            summary["new_notes"] += run["acc_new"]
            human_delay(*delay)

        shutdown(browser, ctx)

    val["overall_confidence"] = confidence(val["total_details"], val["bad_details"])
    summary["validation"] = val
    if summary["errors"] and summary["new_notes"] == 0 and not summary["accounts"]:
        summary["status"] = "failed"
    elif summary["errors"] or val["bad_details"]:
        summary["status"] = "partial"
    emit(**summary)


if __name__ == "__main__":
    main()
