"""xhs-monitor 公共模块：浏览器启动、登录态管理、提取脚本、落库工具。

所有可变数据（配置/登录态/数据）放在运行目录 RUNTIME_DIR，跨会话持久；
技能目录本身只放代码与文档。
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

RUNTIME_DIR = "/mnt/agents/xhs-monitor"
STATE_DIR = os.path.join(RUNTIME_DIR, "state")
DATA_DIR = os.path.join(RUNTIME_DIR, "knowledge-base")
CONFIG_PATH = os.path.join(RUNTIME_DIR, "accounts.json")
NOTES_PATH = os.path.join(DATA_DIR, "notes.jsonl")
METRICS_PATH = os.path.join(DATA_DIR, "metrics.jsonl")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
"""

CST = timezone(timedelta(hours=8))  # 东八区，定时任务环境可能为 UTC


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
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    raise RuntimeError("未找到系统 Chromium，无法启动浏览器")


def launch(pw, state_file=None):
    browser = pw.chromium.launch(
        executable_path=chromium_path(), headless=True,
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
    kwargs = dict(user_agent=UA, viewport={"width": 1440, "height": 900},
                  locale="zh-CN", timezone_id="Asia/Shanghai")
    if state_file and os.path.exists(state_file):
        kwargs["storage_state"] = state_file
    ctx = browser.new_context(**kwargs)
    ctx.add_init_script(STEALTH_JS)
    return browser, ctx


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


def human_delay(a=3.0, b=7.0):
    time.sleep(random.uniform(a, b))


# ---------------- 提取脚本（选择器失效时只改这里，维护方法见 references/selectors.md） ----------------

# 账号主页笔记卡片：扫描所有锚点，按 URL 模式识别笔记 ID，容器内取标题与赞数
EXTRACT_CARDS_JS = r"""() => {
  const items = new Map();
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || '';
    let m = href.match(/\/(?:explore|discovery\/item)\/([0-9a-f]{24})/);
    if (!m) m = href.match(/\/user\/profile\/[0-9a-f]{24}\/([0-9a-f]{24})/);
    if (!m) continue;
    const id = m[1];
    if (items.has(id)) continue;
    const card = a.closest('section') || a.closest('[class*="note"]') || a;
    const q = (sel) => card.querySelector(sel)?.textContent?.trim() || '';
    const imgAlt = card.querySelector('img')?.getAttribute('alt')?.trim() || '';
    items.set(id, {
      note_id: id,
      url: href.startsWith('http') ? href : 'https://www.xiaohongshu.com' + href,
      title: q('.title') || q('[class*="title"]') || imgAlt || (a.getAttribute('title') || '').trim(),
      likes_text: q('.like-wrapper .count') || q('[class*="like"] .count') || q('.count')
    });
  }
  return [...items.values()];
}"""

# 笔记详情页：标题、作者、发布日期、赞/藏/评（分享数前台不公开，不采集）
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
    title: pick(['#detail-title', '.note-content .title', 'h1[class*="title"]', 'h1']),
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
