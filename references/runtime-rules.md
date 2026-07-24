# 运行规则（改编自 xiaohongshu-ops，面向只读监测场景裁剪）

## 1. 低 token 与低打扰约束

- 一律用 evaluate 在页面内完成筛选与结构化，只把 JSON 结果带出浏览器；禁止 dump 整页 DOM 给模型。
- 截图仅用于两处：登录二维码、风控现场存证。其余环节不截图、不做 fullPage。
- 脚本 stdout 是 JSON 行协议，解析后向用户转述，不要把原始输出整段贴给用户。

## 2. 拟人节奏（防风控核心）

- 详情页之间间隔 3–7 秒随机（config.rate.min_delay / max_delay），不得调小到 0。
- 单次运行详情页访问不超预算：日抓默认 30、周刷默认 120。预算耗尽记录未完成部分，次日优先补，不得当次硬跑完。
- 滚动加载每屏等待 ≥1.5 秒，模拟阅读节奏。

## 3. 失败处理（固定三步）

1. 同策略重试一次；
2. 仍失败则跳过该条并记入 errors，继续后续条目；
3. 运行结束时汇总报告「成功多少、跳过多少、原因是什么」，已落库数据一律保留。

## 4. 风控熔断（最高优先级）

- 命中滑块/安全验证/操作频繁/账号异常任一信号 → 立即输出 `{"status":"blocked"}` 并终止本次运行。
- 熔断后只向用户报告一个手动动作：「打开小红书 App 或网页完成一次验证，明日恢复任务」。当日不得重试。
- 登录态失效（`need_login`）→ 重跑 login_bootstrap.py，把二维码图展示给用户扫码，不要尝试账号密码登录。

## 5. 浏览器稳定规则

- 使用系统 Chromium + Playwright，启动参数固定含 `--disable-blink-features=AutomationControlled`，注入 webdriver 抹除脚本（common.py 已实现，勿删）。
- 页面加载用 `domcontentloaded` + 固定等待，不用 `networkidle`（小红书长连接多，易超时）。
- 同一运行内复用同一 page 串行访问，不开多 tab 并发。

## 6. 数据纪律

- 只追加不改写：notes.jsonl / metrics.jsonl 均为 append-only，历史快照永不动，环比才有意义。
- 指标解析失败写 `null`，禁止编造数字。
- 分享数与阅读量前台不公开，默认 `null`；自有账号要阅读量需按 creator-center.md 单独接入。
