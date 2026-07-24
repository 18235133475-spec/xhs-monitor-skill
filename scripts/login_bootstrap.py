"""首次登录引导 / 登录态续期。

流程：打开登录页 → 截取二维码 → 等用户用小红书 App 扫码 → 保存 storage_state。
输出 JSON 行：
  {"status":"scan_required","qr_image":...}  先把二维码图片展示给用户
  {"status":"login_ok","state":...}          登录成功，登录态已保存
  {"status":"login_timeout"}                 超时未扫码，需重新运行
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import STATE_DIR, ensure_playwright, launch, save_state, emit  # noqa: E402

TIMEOUT = int(os.environ.get("XHS_BOOTSTRAP_TIMEOUT", "180"))


def main():
    ensure_playwright()
    from playwright.sync_api import sync_playwright

    state_file = os.path.join(STATE_DIR, "observer.json")
    qr_path = os.path.join(STATE_DIR, "login_qr.png")

    with sync_playwright() as pw:
        browser, ctx = launch(pw)  # 不带旧 state，拿全新二维码
        page = ctx.new_page()
        page.goto("https://www.xiaohongshu.com/login", timeout=45000,
                  wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        # 二维码是 base64 data-url 的 img.qrcode-img：优先直接解码保存（最稳），
        # 其次元素截图，最后整页截图兜底。注意：逗号选择器按文档序返回首个匹配，
        # 会误中 logo，必须按优先级逐个查询。
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
            save_state(ctx, state_file)
            emit(status="login_ok", state=state_file,
                 message="登录态已保存，可执行 daily.py / weekly.py")
        else:
            emit(status="login_timeout",
                 message=f"{TIMEOUT} 秒内未检测到登录成功，请重新运行本脚本")
        browser.close()


if __name__ == "__main__":
    main()
