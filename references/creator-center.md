# 创作者中心接入（自有账号阅读量，可选模块）

前台页面不公开阅读量，只有账号本人登录「创作者中心」可见。本模块默认关闭，
首次启用需一次有人值守的校准运行。

## 1. 前提

- 仅对 config 中 `type=own` 且 `creator=true` 的账号启用。
- 每个自有账号需要**独立的登录态**：用该账号扫码，存为
  `state/creator_{账号名}.json`（复用 login_bootstrap.py，把输出 state 改名即可）。
- 竞品账号无此路径，不要尝试。

## 2. 数据位置

创作者中心 `https://creator.xiaohongshu.com` → 「数据中心」→「笔记分析」：
按笔记列出曝光、阅读、点赞、收藏、评论、分享、涨粉，比前台多出
**曝光量与阅读量**两项核心指标，且结构为后台表格，比前台卡片稳定。

## 3. 接入要点

1. 用 creator state 启动浏览器（`launch(pw, state_file=...)` 参数换成对应文件）；
2. 导航到笔记分析页，必要时按时间范围筛选；
3. evaluate 提取表格行（先按 selectors.md §3 的流程人工校准一次选择器）；
4. 写入 metrics.jsonl 时 `source="creator"`，views/曝光填实数；
   同一 note_id 同时存在 frontend 与 creator 行时，以 creator 行为准。

## 4. 注意

- 后台接口常有签名校验，一律走页面内 evaluate 提取渲染后的表格，不要直接 fetch 接口。
- 多账号串行切换时，每个账号用独立 context，互不混用 cookie。
- 该路径页面结构随后台改版变化，首次运行务必小样本验证字段非空再并入周刷。
