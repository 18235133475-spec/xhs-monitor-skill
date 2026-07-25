# xhs-monitor-skill

小红书账号媒体效果监测 Skill（只读）。对指定账号（自有/竞品）每日发现新发布笔记并采集点赞/收藏/评论，每周回访全部在监测笔记刷新指标并生成环比周报。

设计上继承 xiaohongshu-ops 的 evaluate 提取与风控熔断思路，按只读监测场景裁剪：无发帖、评论等任何写操作。

## 拉取

```bash
git clone https://github.com/18235133475-spec/xhs-monitor-skill.git
```

或拉取单文件（SKILL.md 位于仓库根目录）：

```bash
curl -L https://raw.githubusercontent.com/18235133475-spec/xhs-monitor-skill/main/SKILL.md
```

## 使用

从 `SKILL.md` 开始，按其「首次配置」章节执行：

1. 复制 `config/accounts.json` 到运行目录并填入监测账号主页链接
2. 运行 `scripts/login_bootstrap.py`，用小红书 App 扫码登录
3. 之后 `scripts/daily.py`（日抓）/ `scripts/weekly.py`（周刷+周报）定时执行

## 环境变量

| 变量 | 作用 |
|---|---|
| `XHS_RUNTIME_DIR` | 覆盖运行目录（默认 `/mnt/agents/xhs-monitor`；macOS 示例 `~/.openclaw/workspace/agents/xhs-monitor`），无需改源码 |
| `XHS_HEADLESS` | 设 `0` 为有头模式（本地 Mac 推荐，撞风控墙率低） |
| `XHS_CHROME_PATH` | 指定浏览器路径，如 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` |

## v1.1 变更（2026-07-24）

- **详情页改模态点击进入**：禁止直跳详情 URL（xsec_token 与入口动作绑定，直跳必触发「请打开小红书App扫码查看」风控墙），改为主页点击卡片开模态提取
- **撞墙冷却重试**：命中扫码墙冷却 90 秒重试一次，仍命中才熔断
- **自检机制**：`validate_detail` 字段校验 + 失败截图存证 + validation 置信度报告
- **路径/浏览器环境变量化**：`XHS_RUNTIME_DIR` / `XHS_HEADLESS` / `XHS_CHROME_PATH`
- weekly 不再依赖存储的详情 URL，xsec_token 过期问题消除

## v1.2 变更（2026-07-25）

- **滚动到底全量采集**：主页滚动直至卡片数不再增长（`scroll.max_rounds`/`stable_rounds` 可配），修复只采到前 18 篇的问题
- **登录态持久化**：优先 `launch_persistent_context` 持久化 profile（含 IndexedDB/设备指纹），不支持时回落 storage_state
- **登录墙识别**：主页出现「登录即可查看 Ta 的笔记」时明确报 `need_login`，不再当作空账号
- **模态点击兜底**：locator 点击失败时 evaluate 内 querySelector+scrollIntoView+click
- **登录态纪律**：同一账号固定有头/无头模式，混用会导致指纹漂移被降级

## v1.3 变更（2026-07-25）

- **修复标题错位**：卡片标题改「标题锚点自取」（类名含 title 且 href 含 note_id 的 `<a>` 文本）+ 封面 img alt 兜底，不再用宽泛的 `[class*="title"]`（会命中正文小标题/别篇标题）
- **详情标题收紧**：仅 `#detail-title` / `.note-content .title`
- **一致性校验**：daily 对卡片标题与详情标题做 title_mismatch 检查，以详情为准

## v1.4 变更（2026-07-25）

- **修复 `anchor not found` 批量失败**：根因是 XHS 主页虚拟滚动会回收屏幕外卡片的 DOM，「先滚到底收集再回头点击」时早期卡片已不在 DOM 中
- **改边滚边处理**：新增 `iterate_profile_cards`，每轮滚动对新出现在 DOM 中的卡片立即回调处理（打开模态→提取→关闭），daily/weekly 均重构为该模式
- `scroll_collect_cards` 保留为兼容包装
- 已用虚拟滚动回收的合成 fixture 实测：40 张卡全部在 DOM 存活期内完成处理，0 缺失

运行规则、选择器维护、数据 schema 详见 `references/` 目录。
