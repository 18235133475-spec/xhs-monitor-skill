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

运行规则、选择器维护、数据 schema 详见 `references/` 目录。
