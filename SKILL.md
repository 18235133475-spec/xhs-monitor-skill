---
name: xhs-monitor
description: 小红书账号媒体效果监测工具。对用户指定的一组账号（自有或竞品）做定时只读抓取——每日发现新发布笔记（标题）并采集点赞/收藏/评论，每周回访全部在监测笔记刷新指标并生成环比周报。当用户要求监测小红书账号表现、抓取指定账号的发帖记录或互动数据（赞/藏/评/阅读量）、生成小红书账号日报或周报、配置相关定时任务时使用。仅只读监测，不用于发帖、评论、私信等任何写操作。
---

# 小红书账号监测（xhs-monitor）

只读型监测工具：抓数据 → 落库 → 出周报。无发布/评论模块，设计上继承了
xiaohongshu-ops 的 evaluate 提取与风控熔断思路，按监测场景做了裁剪。

## 路径约定

- 技能目录：本 SKILL.md 所在目录（代码与文档，只读使用）
- 运行目录：`/mnt/agents/xhs-monitor/`（配置、登录态、数据，跨会话持久）
- 浏览器：Playwright + 系统 Chromium，脚本首次运行自动安装 playwright

## 首次配置（只做一次）

1. 初始化运行目录并复制配置模板：
   `mkdir -p /mnt/agents/xhs-monitor && cp <技能目录>/config/accounts.json /mnt/agents/xhs-monitor/`
2. 向用户索取监测账号，填入 `accounts.json` 的 `accounts` 数组（name / type / profile_url）。
   profile_url 获取法：小红书搜索账号名 → 进入主页 → 复制地址栏链接（含 `/user/profile/`）。
   `type` 填 `own`（自有）或 `competitor`（竞品）；自有账号如需阅读量，另见
   references/creator-center.md（可选模块，默认关闭）。
3. 登录引导：`python3 <技能目录>/scripts/login_bootstrap.py`，
   把输出的二维码图片展示给用户，用小红书 App 扫码，出现 `login_ok` 即完成。
   同一登录态可抓所有前台页面。

## 日常任务

### 日抓（每天一次）

```bash
python3 <技能目录>/scripts/daily.py
```

校验登录 → 逐账号抓主页笔记列表（evaluate 一次提取）→ 比对库内识别新增 →
仅对新增笔记进详情页采「赞/藏/评/发布时间」→ 追加 notes.jsonl / metrics.jsonl。
完成后向用户汇报：各账号新增几篇、标题列表、当前互动值。无新增也要明确说「今日无新帖」。

### 周刷 + 周报（每周一次）

```bash
python3 <技能目录>/scripts/weekly.py
```

刷新详情页链接 → 回访 90 天内全部在册笔记刷新指标 → 与 7 天前快照算环比 →
生成 Markdown 周报到 `knowledge-base/reports/`。完成后把周报内容推送给用户，
重点讲：各账号互动增量、赞增量 TOP 帖、异常波动（增量为 null 或骤降的帖）。

## 输出协议（脚本 stdout 为 JSON 行，解析后转述，勿原样转贴）

| status | 含义 | 处置 |
|---|---|---|
| `ok` | 正常完成 | 汇报摘要 |
| `partial` | 部分条目失败 | 汇报成功与跳过明细，已获数据保留 |
| `need_login` | 登录态失效 | 重跑 login_bootstrap.py 并展示二维码 |
| `blocked` | 触发风控 | 立即停止当日任务，只报告一个手动动作（完成一次验证，明日恢复），不得重试 |
| `config_missing` | 配置缺失 | 回到「首次配置」第 1-2 步 |

## 执行规则

必读 references/runtime-rules.md。核心：evaluate 优先不截图；拟人间隔 3–7 秒；
单动作最多重试 1 次；详情页访问不超预算；命中风控关键词即熔断。

## 维护

- 选择器失效（输出字段大面积为空）→ 按 references/selectors.md §3 流程校准，
  只改 `scripts/common.py` 顶部的两个 JS 常量。
- 数据 schema、环比口径、衍生分析指标 → references/data-schema.md。
- 自有账号阅读量接入 → references/creator-center.md。

## 定时任务（Kimi 环境）

配置完成后，用 add_cron_job 建两个任务（时间可与用户确认后调整）：

- 每日 08:30：运行 daily.py 并汇报「昨日各账号新增笔记标题 + 当前赞/藏/评」；
- 每周一 09:00：运行 weekly.py 并推送周报全文。

定时任务的指令中写明技能目录绝对路径；运行结束若输出 `need_login` 或
`blocked`，按输出协议处置并在推送中说明。
