# 选择器模式与维护手册

小红书前端类名哈希混淆、不定期改版，提取失效是常态。所有选择器集中在
`scripts/common.py` 顶部的两个 JS 常量（`EXTRACT_CARDS_JS` / `EXTRACT_DETAIL_JS`），
失效时只改这一处，daily.py 与 weekly.py 同时生效。

## 1. 笔记 ID 识别（URL 模式，最稳定的一层）

按优先级匹配锚点 href：

| 模式 | 正则 | 场景 |
|---|---|---|
| 探索页 | `/(?:explore\|discovery\/item)\/([0-9a-f]{24})` | 主页卡片、搜索结果 |
| 主页内链 | `/user/profile/[0-9a-f]{24}/([0-9a-f]{24})` | 新版个人主页卡片 |

详情页 URL 必须带 `xsec_token` 参数，否则 404；token 约 5 分钟过期且与入口动作绑定。
因此 v1.1 起不再存储/复用详情 URL：daily 与 weekly 均通过点击主页卡片开模态进入详情
（URL 模式仅用于从卡片 href 识别 note_id，不用于导航）。

## 2. 字段选择器回退表

每个字段按列表顺序尝试，命中即止：

| 字段 | 选择器（按优先级） |
|---|---|
| 卡片标题 | `.title` → `[class*="title"]` → 卡片 img 的 alt → 锚点 title 属性 |
| 卡片赞数 | `.like-wrapper .count` → `[class*="like"] .count` → `.count` |
| 详情标题 | `#detail-title` → `.note-content .title` → `h1[class*="title"]` → `h1` |
| 作者 | `.author-container .username` → `.username` → `[class*="author"] [class*="name"]` |
| 发布日期 | `.bottom-container .date` → `.date` → `[class*="date"]` |
| 点赞 | `.engage-bar .like-wrapper .count` → `.like-wrapper .count` |
| 收藏 | `.engage-bar .collect-wrapper .count` → `.collect-wrapper .count` |
| 评论 | `.engage-bar .chat-wrapper .count` → `.chat-wrapper .count` |

日期文本形如「昨天 12:30」「3天前」「07-20」「2026-07-20」，由
`common.parse_publish_time` 归一为 ISO 日期；解析失败保留原文，人工核对。

数字文本可能带「万」（1.2万 → 12000），由 `common.parse_count` 处理。

## 3. 失效校准流程（选择器改版时）

1. 用 login_bootstrap.py 的登录态手动打开一个详情页（或在无头脚本里加 `page.pause()` 临时调试）；
2. 在页面 Console 里单测新选择器，确认能取到值；
3. 把新选择器**插到对应回退列表的队首**（旧选择器保留做兜底）；
4. 小样本试跑：先跑 1 个账号、预算调到 3，确认输出字段非空再恢复正常任务。

## 4. 风控信号关键词

- 页面级风控：`滑块` / `安全验证` / `操作频繁` / `账号异常` / `环境异常`
  （`common.detect_block`，命中即熔断）
- 详情页扫码墙：`当前笔记暂时无法浏览` / `笔记暂时无法浏览` / `请打开小红书App扫码查看` / `App扫码查看`
  （`common.detect_detail_unavailable`，命中走冷却重试，见 runtime-rules.md §4）

遇到新形式的风控文案时往对应清单追加。

## 5. 模态说明

v1.1 起详情一律走「主页点击卡片 → SPA 弹模态」。模态打开后当前笔记的
engage-bar 在 document 内唯一，`EXTRACT_DETAIL_JS` 无需改动即可命中；
关闭模态用 Escape（`close_note_modal`）。若模态打不开，优先检查卡片锚点
`a[href*="{note_id}"]` 是否仍存在于主页 DOM（翻屏深度不够时老帖不在，属正常，
weekly.py 会计入 `skipped_unseen`）。
