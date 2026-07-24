"""周刷：回访全部在监测笔记刷新指标，并生成环比周报。

流程：校验登录 → 逐账号扫主页（多翻几屏覆盖老帖）→ 对在册笔记逐一点击卡片
     开模态刷新指标（禁止直跳 URL，xsec_token 过期问题随之消失）
     → 追加 metrics.jsonl → 与 7 天前快照比对算环比
     → 生成 Markdown 周报到 knowledge-base/reports/。
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (STATE_DIR, NOTES_PATH, METRICS_PATH, REPORTS_DIR, CST,
                    ensure_playwright, launch, check_login, detect_block,
                    human_delay, append_jsonl, load_jsonl, load_config,
                    parse_count, today_str, open_note_modal, close_note_modal,
                    extract_detail_guarded, validate_detail, confidence,
                    save_validation_shot, EXTRACT_CARDS_JS, emit)  # noqa: E402


def note_age_days(note, today_d):
    d = note.get("publish_time") or note.get("first_seen") or ""
    try:
        return (today_d - datetime.fromisoformat(str(d)[:10]).date()).days
    except ValueError:
        return 0  # 日期解析失败的一律纳入刷新，宁多勿漏


def latest_snapshots(metrics):
    """note_id -> 按日期排序的快照列表"""
    by_note = {}
    for m in metrics:
        by_note.setdefault(m["note_id"], []).append(m)
    for v in by_note.values():
        v.sort(key=lambda x: x.get("date", ""))
    return by_note


def delta(latest, baseline, key):
    a, b = latest.get(key), (baseline or {}).get(key)
    return (a - b) if (a is not None and b is not None) else None


def build_report(notes, metrics, today_s, top_n):
    today_d = datetime.fromisoformat(today_s).date()
    week_ago = (today_d - timedelta(days=7)).isoformat()
    by_note = latest_snapshots(metrics)

    def baseline_for(snaps):
        cands = [s for s in snaps if s.get("date", "") <= week_ago]
        return cands[-1] if cands else None

    accounts = {}
    for n in notes:
        accounts.setdefault(n["account"], {"notes": [], "new_this_week": 0})
        accounts[n["account"]]["notes"].append(n)
        if (n.get("first_seen") or "") > week_ago:
            accounts[n["account"]]["new_this_week"] += 1

    L = [f"# 小红书账号监测周报（{week_ago} ~ {today_s}）", ""]
    for acc, info in accounts.items():
        rows, tot = [], {"likes": 0, "collects": 0, "comments": 0}
        for n in info["notes"]:
            snaps = by_note.get(n["note_id"], [])
            if not snaps:
                continue
            latest, base = snaps[-1], baseline_for(snaps)
            dl = delta(latest, base, "likes")
            dc = delta(latest, base, "collects")
            dm = delta(latest, base, "comments")
            for k, v in (("likes", dl), ("collects", dc), ("comments", dm)):
                if v:
                    tot[k] += v
            rows.append((n, latest, dl, dc, dm, base is None))
        rows.sort(key=lambda r: (r[2] or 0), reverse=True)

        L.append(f"## {acc}")
        L.append(f"- 在册笔记 {len(info['notes'])} 篇，本周新增 {info['new_this_week']} 篇")
        L.append(f"- 本周互动增量：赞 +{tot['likes']} / 藏 +{tot['collects']} / 评 +{tot['comments']}")
        L.append("")
        L.append("| 笔记 | 发布日期 | 赞 | 藏 | 评 | 周增量(赞/藏/评) |")
        L.append("|---|---|---|---|---|---|")
        for n, latest, dl, dc, dm, is_new in rows[: max(top_n * 3, 15)]:
            fmt = lambda v: "—" if v is None else (f"+{v}" if v >= 0 else str(v))
            title = (n.get("title") or "（无标题）")[:24].replace("|", " ")
            tag = " 🆕" if is_new else ""
            L.append(f"| {title}{tag} | {n.get('publish_time') or '未知'} | "
                     f"{latest.get('likes')} | {latest.get('collects')} | "
                     f"{latest.get('comments')} | {fmt(dl)}/{fmt(dc)}/{fmt(dm)} |")
        L.append("")
        top = [r for r in rows if r[2]][:top_n]
        if top:
            L.append(f"**本周赞增量 TOP{len(top)}：**")
            for n, _, dl, *_ in top:
                L.append(f"- 《{(n.get('title') or '（无标题）')[:30]}》 +{dl} 赞  {n.get('url','')}")
            L.append("")
    return "\n".join(L)


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
    budget = rate.get("weekly_detail_budget", 120)
    cooldown = rate.get("wall_cooldown_seconds", 90)
    wcfg = cfg.get("weekly", {})
    max_age = wcfg.get("max_age_days", 90)
    top_n = wcfg.get("top_n", 3)
    max_scrolls = cfg.get("daily", {}).get("max_scrolls", 6)

    today_s = today_str()
    today_d = datetime.now(CST).date()
    notes = load_jsonl(NOTES_PATH)
    active = [n for n in notes if note_age_days(n, today_d) <= max_age]
    if not active:
        emit(status="ok", message="库内无在监测笔记，请先跑 daily.py", refreshed=0)
        return

    by_account = {}
    for n in active:
        by_account.setdefault(n["account"], []).append(n)

    errors, refreshed = [], 0
    val = {"total_details": 0, "bad_details": 0, "detail_wall_hits": 0,
           "screenshots": [], "skipped_unseen": 0}

    with sync_playwright() as pw:
        browser, ctx = launch(pw, state_file)
        page = ctx.new_page()

        if not check_login(page):
            emit(status="need_login", message="登录态已失效，请重新运行 login_bootstrap.py")
            browser.close()
            return

        for acc in cfg.get("accounts", []):
            name, url = acc.get("name"), acc.get("profile_url", "")
            acc_notes = by_account.get(name, [])
            if not acc_notes or "/user/profile/" not in url:
                continue
            try:
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                for _ in range(max_scrolls * 2):  # 周刷多翻几屏，覆盖老帖
                    page.mouse.wheel(0, 2500)
                    page.wait_for_timeout(1500)
                seen = {c["note_id"] for c in page.evaluate(EXTRACT_CARDS_JS)}
            except Exception as e:
                errors.append({"account": name, "error": f"主页扫描失败: {e}"})
                continue

            if detect_block(page):
                emit(status="blocked", message="扫描主页时触发风控，已熔断。请手动验证后明日再试。")
                browser.close()
                return

            for n in acc_notes:
                if n["note_id"] not in seen:
                    val["skipped_unseen"] += 1  # 翻屏深度内未出现的老帖，下周优先
                    continue
                if budget <= 0:
                    errors.append({"error": "详情页访问预算耗尽，其余笔记下周优先刷新"})
                    break
                budget -= 1
                try:
                    open_note_modal(page, n["note_id"])
                except Exception as e:
                    errors.append({"note_id": n["note_id"], "error": f"模态打开失败: {e}"})
                    continue

                d, walled = extract_detail_guarded(page, n["note_id"], cooldown)
                val["total_details"] += 1
                if walled:
                    val["detail_wall_hits"] += 1
                    shot = save_validation_shot(page, n["note_id"])
                    if shot:
                        val["screenshots"].append(shot)
                    emit(status="blocked",
                         message="详情页持续命中「App 扫码查看」风控墙，已熔断。"
                                 "建议 1-2 小时后或明日恢复；可用 XHS_HEADLESS=0 有头模式降低触发率。")
                    browser.close()
                    return

                if validate_detail(d):
                    val["bad_details"] += 1
                    shot = save_validation_shot(page, n["note_id"])
                    if shot:
                        val["screenshots"].append(shot)
                    errors.append({"note_id": n["note_id"], "error": "自检字段缺失，已记录截图"})

                append_jsonl(METRICS_PATH, {
                    "note_id": n["note_id"], "account": n["account"], "date": today_s,
                    "likes": parse_count(d.get("like")) if d else None,
                    "collects": parse_count(d.get("collect")) if d else None,
                    "comments": parse_count(d.get("comment")) if d else None,
                    "views": None, "source": "frontend",
                })
                refreshed += 1
                close_note_modal(page)
                human_delay(*delay)

            human_delay(*delay)

        browser.close()

    metrics = load_jsonl(METRICS_PATH)
    report = build_report(notes, metrics, today_s, top_n)
    iso = datetime.now(CST).isocalendar()
    os.makedirs(REPORTS_DIR, exist_ok=True)
    report_path = os.path.join(REPORTS_DIR, f"weekly_{iso[0]}W{iso[1]:02d}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    val["overall_confidence"] = confidence(val["total_details"], val["bad_details"])
    status = "ok" if not errors else ("partial" if refreshed else "failed")
    emit(status=status, date=today_s, refreshed=refreshed, active_notes=len(active),
         report=report_path, validation=val, errors=errors[:10])


if __name__ == "__main__":
    main()
