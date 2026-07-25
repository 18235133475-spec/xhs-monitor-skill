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
| `XHS_HEADLESS` | 设 `0` 为有头模式（本地 Mac 推荐，撞风控墙率低）。**登录与日常运行必须固定同一模式** |
| `XHS_CHROME_PATH` | 指定浏览器路径，如 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` |

## 变更记录

### v1.3（2026-07-25）

- **修复标题错位事故**：卡片标题废弃容器内 `.title` 宽泛查询（容器解析错误会取到别篇标题），改为「类名含 title 且 href 同含 note_id 的标题锚点」自身文本，封面 img alt 兜底
- **详情标题砍兜底**：仅保留 `#detail-title` / `.note-content .title`（实测 `h1[class*="title"]` 会命中正文小标题）
- **标题一致性校验**：卡片标题与详情标题不一致时记 `title_mismatch`，以详情页为准

### v1.2（2026-07-24）

- 滚动到底采集（修复「只采到首屏 18 篇」）
- 持久化 profile 登录态，解决扫码登录态短命掉线
- 登录墙识别、模态点击 JS 兜底、登录态纪律（固定有头/无头模式）

### v1.1（2026-07-24）

- 详情页改模态点击进入（直跳 URL 必触发「App 扫码查看」风控墙）
- 撞墙冷却重试、自检机制、环境变量化

运行规则、选择器维护、数据 schema 详见 `references/` 目录。
