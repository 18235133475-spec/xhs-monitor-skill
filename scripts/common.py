"""xhs-monitor 公共模块：浏览器启动、登录态管理、提取脚本、落库工具。

所有可变数据（配置/登录态/数据）放在运行目录，跨会话持久；技能目录只放代码与文档。
环境变量：
  XHS_RUNTIME_DIR  运行目录（默认 /mnt/agents/xhs-monitor；macOS 可设 ~/.openclaw/...）
  XHS_HEADLESS     "0" 表示有头模式（本地 Mac 调试用，指纹更干净；默认无头）
  XHS_CHROME_PATH  指定浏览器可执行文件路径（如 /Applications/Google Chrome.app/...）

v1.2：登录态改用持久化浏览器 profile（state/profile/），完整保留 cookies、
localStorage、IndexedDB 与设备指纹，且每次运行指纹一致，显著优于 storage_state。
注意：同一账号必须固定同一种运行模式（有头/无头二选一），混用会被平台降级。
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

RUNTIME_DIR = os.environ.get("XHS_RUNTIME_DIR", "/mnt/agents/xhs-monitor")
STATE_DIR = os.path.join(RUNTIME_DIR, "state")
PROFILE_DIR = os.path.join(STATE_DIR, "profile")
DATA_DIR = os.path.join(RUNTIME_DIR, "knowledge-base")
CONFIG_PATH = os.path.join(RUNTIME_DIR, "accounts.json")
NOTES_PATH = os.path.join(DATA_DIR, "notes.jsonl")
METRICS_PATH = os.path.join(DATA_DIR, "metrics.jsonl")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")
VALIDATION_DIR = os.path.join(DATA_DIR, "validation")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
"""

CST = timezone(timedelta(hours=8))  # 东八区，定时任务环境可能为 UTC


class LoginWallError(Exception):
    """主页滚动时撞「登录即可查看 Ta 的笔记」：登录态掉线/被降级。"""


def today_str():
    return datetime.now(CST).date().isoformat()


# ---------------- 环境 ----------------

def ensure_playwright():
    try:
        import playwright  # noqa: F401
        return
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "playwright"],
                       check=True)


def chromium_path():
    override = os.environ.get("XHS_CHROME_PATH")
    if override and os.path.exists(override):
        return override
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    raise RuntimeError("未找到系统 Chromium，可用环境变量 XHS_CHROME_PATH 指定浏览器路径")


def has_login_state():
    """是否存在可用登录态：持久化 profile（优先）或旧版 observer.json。"""
    if os.path.isdir(PROFILE_DIR) and os.listdir(PROFILE_DIR):
        return True
    return os.path.exists(os.path.join(STATE_DIR, "observer.json"))


def launch(pw, state_file=None):
    """启动浏览器。返回 (browser, ctx)；persistent 模式下 browser 为 None，
    关闭时统一调用 shutdown(browser, ctx)。

    优先使用持久化 profile（登录态完整、指纹一致）；profile 不存在时回落
    storage_state（兼容 v1.0/v1.1 的 observer.json）。
    """
    headless = os.environ.get("XHS_HEADLESS", "1") != "0"
    exe = chromium_path()
    args = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]
    kwargs = dict(user_agent=UA, viewport={"width": 1440, "height": 900},
                  locale="zh-CN", timezone_id="Asia/Shanghai")

    if os.path.isdir(PROFILE_DIR) and os.listdir(PROFILE_DIR):
        try:
            ctx = pw.chromium.launch_persistent_context(
                PROFILE_DIR, executable_path=exe, headless=headless, args=args, **kwargs)
            ctx.add_init_script(STEALTH_JS)
            return None, ctx
        except Exception:
            pass  # 某些挂载盘不支持 Chromium profile（如缺 symlink），回落

    browser = pw.chromium.launch(executable_path=exe, headless=headless, args=args)
    legacy = state_file or os.path.join(STATE_DIR, "observer.json")
    if os.path.exists(legacy):
        kwargs["storage_state"] = legacy
    ctx = browser.new_context(**kwargs)
    ctx.add_init_script(STEALTH_JS)
    return browser, ctx


def shutdown(browser, ctx):
    try:
        ctx.close()
    finally:
        if browser is not None:
            browser.close()


def save_state(ctx, state_file):
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    ctx.storage_state(path=state_file)


# ---------------- 页面状态判定 ----------------

def check_login(page):
    """访问 explore，若被重定向到 /login 则登录态失效。"""
    page.goto("https://www.xiaohongshu.com/explore", timeout=45000,
              wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    return "/login" not in page.url


def detect_block(page):
    """检测风控：验证码/滑块/频繁。命中即熔断，不得硬闯。"""
    url = page.url or ""
    if "captcha" in url or "/verify" in url:
        return True
    try:
        t = page.evaluate("() => document.body.innerText.slice(0, 600)")
        return any(k in t for k in ("滑块", "安全验证", "操作频繁", "账号异常", "环境异常"))
    except Exception:
        return False


# 主页「登录墙」：登录态掉线/被降级时出现，此时滚动不会有任何新卡片
LOGIN_WALL_KEYS = ("登录即可查看", "登录后即可查看")


def detect_login_wall(page):
    try:
        t = page.evaluate("() => document.body.innerText.slice(0, 600)")
        return any(k in t for k in LOGIN_WALL_KEYS)
    except Exception:
        return False


# 详情页「App 扫码墙」关键词：命中说明详情被平台临时风控，不是选择器失效。
# 该墙可恢复（冷却一段时间后可再访问），处理流程见 runtime-rules.md §4。
DETAIL_WALL_KEYS = ("当前笔记暂时无法浏览", "笔记暂时无法浏览",
                    "请打开小红书App扫码查看", "App扫码查看")


def detect_detail_unavailable(page):
    """检测详情页是否被平台返回「请打开小红书App扫码查看」拦截页。"""
    try:
        t = page.evaluate("() => document.body.innerText.slice(0, 800)")
        return any(k in t for k in DETAIL_WALL_KEYS)
    except Exception:
        return False


def human_delay(a=3.0, b=7.0):
    time.sleep(random.uniform(a, b))


def cool_down(seconds=90):
    """撞详情墙后的冷却，等平台解除临时风控。"""
    time.sleep(seconds)


# ---------------- 主页滚动采集 ----------------
# 关键认知（v1.4）：XHS 主页是虚拟滚动，滚远的卡片会被回收出 DOM。
# 因此「先滚到底收集、再回头点击」必然大面积 anchor not found——
# 必须边滚边处理（iterate_profile_cards），卡片还在 DOM 时就完成点击提取。

def iterate_profile_cards(page, on_card, max_rounds=40, stable_rounds=2, wait_ms=1800):
    """滚动主页，对每轮出现在 DOM 中的新卡片立即回调处理。
    on_card(card) -> bool：返回 False 立即中止整体迭代（预算耗尽/需要熔断）。
    撞登录墙抛 LoginWallError。
    返回 (cards, aborted)：cards 为全程见过的去重卡片列表。"""
    cards, processed = {}, set()
    last_count, stable, aborted = -1, 0, False
    for _ in range(max_rounds):
        if detect_login_wall(page):
            raise LoginWallError("主页弹出登录墙，登录态掉线或被平台降级")
        for c in page.evaluate(EXTRACT_CARDS_JS):
            cards[c["note_id"]] = c
            if c["note_id"] in processed:
                continue
            processed.add(c["note_id"])
            if on_card(c) is False:
                aborted = True
                break
        if aborted:
            break
        if len(cards) == last_count:
            stable += 1
            if stable >= stable_rounds:
                break
        else:
            stable = 0
            last_count = len(cards)
        page.mouse.wheel(0, 2200)
        page.wait_for_timeout(wait_ms)
    return list(cards.values()), aborted


def scroll_collect_cards(page, max_rounds=40, stable_rounds=2, wait_ms=1800):
    """兼容旧调用：纯滚动采集不处理卡片。新代码请用 iterate_profile_cards。"""
    cards, _ = iterate_profile_cards(page, lambda c: True, max_rounds,
                                     stable_rounds, wait_ms)
    return cards


# ---------------- 详情页模态操作 ----------------
# 铁律：禁止直接 goto 详情页 URL（xsec_token 与入口动作绑定，直跳必撞墙）。
# 一律在主页/列表页点击卡片，让 SPA 弹模态，token 上下文由点击动作自然生成。

def open_note_modal(page, note_id):
    """在当前列表页点击对应笔记卡片，打开详情模态。locator 失败降级 JS 点击。"""
    sel = f'a[href*="{note_id}"]'
    try:
        loc = page.locator(sel).first
        loc.scroll_into_view_if_needed(timeout=6000)
        loc.click(timeout=6000)
    except Exception:
        page.evaluate("""(sel) => {
            const el = document.querySelector(sel);
            if (!el) throw new Error('anchor not found: ' + sel);
            el.scrollIntoView({block: 'center'});
            el.click();
        }""", sel)
    page.wait_for_timeout(3500)


def close_note_modal(page):
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    page.wait_for_timeout(1200)


def extract_detail_guarded(page, note_id, cooldown_seconds=90):
    """模态内提取详情；撞扫码墙则冷却重试一次。
    返回 (data, walled)：walled=True 表示冷却重试后仍被拦截，调用方应熔断。"""
    for attempt in (1, 2):
        if detect_detail_unavailable(page):
            if attempt == 1:
                close_note_modal(page)
                cool_down(cooldown_seconds)
                open_note_modal(page, note_id)
                continue
            return None, True
        return page.evaluate(EXTRACT_DETAIL_JS), False
    return None, True


def validate_detail(d):
    """详情数据质量自检：返回缺失字段列表，空列表表示通过。"""
    if not d:
        return ["no_data"]
    missing = []
    if not (d.get("title") or "").strip():
        missing.append("title")
    if not (d.get("date") or "").strip():
        missing.append("date")
    if not any((d.get(k) or "").strip() for k in ("like", "collect", "comment")):
        missing.append("metrics")
    return missing


def confidence(total, bad):
    if total == 0:
        return "none"
    if bad == 0:
        return "high"
    if bad <= total * 0.2:
        return "medium"
    if bad < total:
        return "low"
    return "none"


def save_validation_shot(page, note_id):
    os.makedirs(VALIDATION_DIR, exist_ok=True)
    path = os.path.join(VALIDATION_DIR, f"{today_str()}_{note_id}.png")
    try:
        page.screenshot(path=path)
        return path
    except Exception:
        return None


# ---------------- 提取脚本（选择器失效时只改这里，维护方法见 references/selectors.md） ----------------

# 账号主页笔记卡片：按 URL 模式识别笔记 ID。
# v1.3 修复：标题不再从模糊容器里 querySelector（容器解析错误会导致标题串行错位），
# 改为「标题锚点自取」——页脚标题本身就是 href 含 note_id、类名含 title 的 <a>；
# 兜底用封面 img 的 alt（XHS 封面 alt 即笔记标题）。赞数仍从卡片容器取（容错）。
EXTRACT_CARDS_JS = r"""() => {
  const items = new Map();
  const pickId = (href) => {
    let m = (href || '').match(/\/(?:explore|discovery\/item)\/([0-9a-f]{24})/);
    if (!m) m = (href || '').match(/\/user\/profile\/[0-9a-f]{24}\/([0-9a-f]{24})/);
    return m ? m[1] : null;
  };
  for (const a of document.querySelectorAll('a[href]')) {
    const id = pickId(a.getAttribute('href'));
    if (!id) continue;
    const item = items.get(id) || {note_id: id, url: '', title: '', likes_text: ''};
    if (!item.url) {
      const href = a.getAttribute('href');
      item.url = href.startsWith('http') ? href : 'https://www.xiaohongshu.com' + href;
    }
    // 主路径：标题锚点（类名含 title 的链接）自身文本
    if (!item.title && /title/i.test(a.className || '')) {
      const t = (a.textContent || '').trim();
      if (t) item.title = t;
    }
    // 兜底：封面图 alt
    if (!item.title) {
      const alt = a.querySelector('img')?.getAttribute('alt')?.trim();
      if (alt) item.title = alt;
    }
    if (!item.likes_text) {
      const card = a.closest('section') || a;
      const like = card.querySelector('.like-wrapper .count')
                || card.querySelector('[class*="like"] .count');
      if (like) item.likes_text = like.textContent.trim();
    }
    items.set(id, item);
  }
  return [...items.values()];
}"""

# 笔记详情（模态打开后 document 内唯一 engage-bar 即当前笔记）：标题、作者、日期、赞/藏/评
EXTRACT_DETAIL_JS = r"""() => {
  const pick = (sels) => {
    for (const s of sels) {
      const el = document.querySelector(s);
      const t = el?.textContent?.trim();
      if (t) return t;
    }
    return '';
  };
  return {
    title: pick(['#detail-title', '.note-content .title']),
    author: pick(['.author-container .username', '.username', '[class*="author"] [class*="name"]']),
    date: pick(['.bottom-container .date', '.date', '[class*="date"]']),
    like: pick(['.engage-bar .like-wrapper .count', '.like-wrapper .count', '[class*="like-wrapper"] .count']),
    collect: pick(['.engage-bar .collect-wrapper .count', '.collect-wrapper .count', '[class*="collect-wrapper"] .count']),
    comment: pick(['.engage-bar .chat-wrapper .count', '.chat-wrapper .count', '[class*="chat-wrapper"] .count'])
  };
}"""


# ---------------- 数据 ----------------

def append_jsonl(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            f"未找到配置 {CONFIG_PATH}，请先从技能目录复制 config/accounts.json 并填入监测账号")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def parse_count(v):
    """'1.2万' -> 12000；'3,456' -> 3456；空/无法解析 -> None"""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    m = re.match(r"^([\d.]+)\s*万?$", s)
    if not m:
        return None
    n = float(m.group(1))
    if "万" in s:
        n *= 10000
    return int(n)


def parse_publish_time(s):
    """'昨天 12:30'/'3天前'/'07-20'/'2026-07-20' 归一到 ISO 日期；失败返回原文。"""
    if not s:
        return None
    s = s.strip()
    today = datetime.now(CST).date()
    if "今天" in s:
        return today.isoformat()
    if "昨天" in s:
        return (today - timedelta(days=1)).isoformat()
    if "前天" in s:
        return (today - timedelta(days=2)).isoformat()
    m = re.search(r"(\d+)\s*天前", s)
    if m:
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{1,2})[-/月](\d{1,2})", s)
    if m:
        return f"{today.year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return s


def emit(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)
