# 数据 Schema 与周报口径

数据落在运行目录 `/mnt/agents/xhs-monitor/knowledge-base/`，JSONL 追加式，一行一个 JSON。

## 1. notes.jsonl —— 笔记台账（首次发现时写入一次）

| 字段 | 类型 | 说明 |
|---|---|---|
| note_id | string | 24 位十六进制，从详情 URL 提取，全局主键 |
| url | string | 含 xsec_token 的详情页链接（token 会过期，仅兜底用） |
| account | string | 所属账号名（与 config.accounts.name 一致） |
| type | string | `own` 自有 / `competitor` 竞品 |
| title | string | 笔记标题（详情页取不到时回退卡片标题） |
| publish_time | string | ISO 日期；解析失败保留原文 |
| first_seen | string | 首次入库日期（判断「本周新增」的依据） |

## 2. metrics.jsonl —— 指标快照（每次采集追加一行）

| 字段 | 类型 | 说明 |
|---|---|---|
| note_id | string | 关联 notes.jsonl |
| account | string | 冗余账号名，便于直接聚合 |
| date | string | 快照日期（东八区） |
| likes / collects / comments | int\|null | 前台公开值；解析失败为 null |
| views | int\|null | 阅读量。前台不公开，默认 null；接入创作者中心后由 source=creator 的行携带 |
| source | string | `frontend` 前台抓取 / `creator` 创作者中心 |

同一 note_id 同一天可能有多行（手动补跑），取该日最后一行。

## 3. 环比口径（weekly.py 周报算法）

- **基线**：该笔记 date ≤ 今天-7 的最新一条快照；无基线则标记「🆕 新入库」，不计增量。
- **周增量** = 最新快照 − 基线快照；任一端为 null 则增量记 null（展示为「—」），不参与汇总。
- **账号汇总**：本周新增笔记数按 first_seen 判定；互动增量为该账号全部在册笔记增量之和。
- **TOP 榜**：按赞增量降序，取 config.weekly.top_n。

## 4. 周报产物

路径：`knowledge-base/reports/weekly_{ISO年}W{ISO周}.md`，
每次周刷覆盖同周旧版。内容包括：各账号总览（在册数/新增数/互动增量）、
明细表（限量行，按赞增量排序）、赞增量 TOP 榜。

## 5. 衍生分析建议（用户临时提问时）

直接读 JSONL 用 Python/pandas 聚合即可，常见口径：
- 互动率代理指标 = (赞+藏+评)，跨账号对比时按发文数归一；
- 赞藏比 = 赞/藏，>2 偏情绪共鸣，<1 偏干货收藏向；
- 发帖频率 = 时间窗内 first_seen 计数 / 周数。
