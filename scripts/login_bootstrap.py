"""首次登录引导 / 登录态续期（v1.2：持久化 profile 方案）。

流程：用持久化 profile 打开登录页 → 截取二维码 → 等用户用小红书 App 扫码
     → 登录成功后 profile 目录自动持久全部登录态（cookies/IndexedDB/设备指纹）。
       挂载盘不支持 profile 时自动回落普通浏览器 + storage_state。
输出 JSON 行：
  {"status":"scan_required","qr_image":...}  先把二维码图片展示给用户
  {"status":"login_ok","state":...}          登录成功
  {"status":"login_timeout"}                 超时未扫码，需重新运行
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (STATE_DIR, PROFILE_DIR, ensure_playwright, chromium_path,
                    save_state, emit, UA, STEALTH_JS)  # noqa: E402

TIMEOUT = int(os.environ.get("XHS_BOOTSTRAP_TIMEOUT", "180"))


def main():
    ensure_playwright()
    from playwright.sync_api import sync_playwright

    qr_path = os.path.join(STATE_DIR, "login_qr.png")
    headless = os.environ.get("XHS_HEADLESS", "1") != "0"

    with sync_playwright() as pw:
        ctx, browser, persistent = None, None, True
        try:
            os.makedirs(PROFILE_DIR, exist_ok=True)
            ctx = pw.chromium.launch_persistent_context(
                PROFILE_DIR, executable_path=chromium_path(), headless=headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                user_agent=UA, viewport={"width": 1440, "height": 900},
                locale="zh-CN", timezone_id="Asia/Shanghai")
        except Exception:
            # 某些挂载盘不支持 Chromium profile（缺 symlink/lock 支持）：
            # 回落为普通浏览器，登录态以 storage_state 保存（兼容性优先）。
            persistent = False
            browser = pw.chromium.launch(
                executable_path=chromium_path(), headless=headless,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
            ctx = browser.new_context(
                user_agent=UA, viewport={"width": 1440, "height": 900},
                locale="zh-CN", timezone_id="Asia/Shanghai")
        ctx.add_init_script(STEALTH_JS)

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.xiaohongshu.com/login", timeout=45000,
                  wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        # 二维码是 base64 data-url 的 img.qrcode-img：优先直接解码保存（最稳），
        # 其次元素截图，最后整页截图兜底。逗号选择器按文档序返回首个匹配，会误中
        # logo，必须按优先级逐个查询。
        try:
            src = page.evaluate(
                "() => document.querySelector('img.qrcode-img')?.getAttribute('src') || ''")
            if src.startswith("data:image"):
                import base64
                os.makedirs(STATE_DIR, exist_ok=True)
                with open(qr_path, "wb") as f:
                    f.write(base64.b64decode(src.split(",", 1)[1]))
            else:
                raise ValueError("no data-url qrcode")
        except Exception:
            try:
                qr = (page.query_selector("img.qrcode-img")
                      or page.query_selector(".login-container canvas")
                      or page.query_selector(".login-container img"))
                (qr.screenshot(path=qr_path) if qr else page.screenshot(path=qr_path))
            except Exception:
                page.screenshot(path=qr_path)

        emit(status="scan_required", qr_image=qr_path,
             message=f"请用小红书 App 扫码登录，脚本轮询等待 {TIMEOUT} 秒")

        deadline = time.time() + TIMEOUT
        ok = False
        while time.time() < deadline:
            page.wait_for_timeout(3000)
            if "/login" not in page.url:
                ok = True
                break

        if ok:
            page.wait_for_timeout(2000)
            state_used = PROFILE_DIR if persistent else os.path.join(STATE_DIR, "observer.json")
            try:  # 两种模式都存一份 observer.json 做兼容备份
                save_state(ctx, os.path.join(STATE_DIR, "observer.json"))
            except Exception:
                pass
            emit(status="login_ok", state=state_used, persistent=persistent,
                 message="登录成功，可执行 daily.py / weekly.py。"
                         "注意：后续运行请固定使用同一种模式（有头或无头），混用会被平台降级。")
        else:
            emit(status="login_timeout",
                 message=f"{TIMEOUT} 秒内未检测到登录成功，请重新运行本脚本")
        ctx.close()
        if browser is not None:
            browser.close()


if __name__ == "__main__":
    main()
