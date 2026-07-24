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

运行规则、选择器维护、数据 schema 详见 `references/` 目录。
