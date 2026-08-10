# CodeHub 🗂️

服务器上的统一代码仓库 —— 存放所有由 Codex 编写的脚本、工具和项目。

## 目录结构

```
CodeHub/
├── scripts/          # 独立可运行的脚本（数据采集、自动化任务等）
├── projects/         # 完整项目（多文件应用、工具集）
└── docs/             # 文档、笔记、流程说明
```

## 当前内容

- `scripts/market_snapshot.py` — 市场数据快照采集（A股三指数 + 伦敦金 + 美股三大指数）
  - 数据源：腾讯行情（A股）、TradingView（伦敦金）、Yahoo Finance（美股）
  - 部署位置：`~/.hermes/scripts/market_snapshot.py`（供定时任务调用）
  - 定时任务：每日简报（北京时间 09:20 早盘 / 15:20 收盘）

## 使用约定

- 新脚本先在此仓库开发测试，验证通过后部署到 `~/.hermes/scripts/` 供 cron 调用
- **质量门槛（强制）**：所有代码提交前必须经过代码审查（code review）——由 Codex 审查（或 Hermes 调用 requesting-code-review 技能），审查通过才能 commit，不可省略
- 每次修改记得 commit，保持仓库可追溯
- 敏感信息（API key 等）绝不入库，一律放 `~/.hermes/.env`
