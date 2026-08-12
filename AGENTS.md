# CodeHub — Codex 项目指令（AGENTS.md）

这是 Codex 在 CodeHub 仓库工作时**自动读取**的指令文件，每次运行都会加载。由 Hermes 维护，与用户约定保持一致。

## 项目定位

CodeHub 是服务器上的统一代码仓库，存放所有由 Codex 编写的脚本、工具和项目。

## 目录结构

- `scripts/` — 独立可运行的脚本（数据采集、自动化任务等）
- `projects/` — 完整项目（多文件应用、工具集）
- `docs/` — 文档、笔记、流程说明

## 工作规范（必须遵守）

1. **代码审查**：所有代码提交（commit）前必须经过代码审查（code review），审查通过才能提交。审查流程由 Hermes 负责（`requesting-code-review` 管线），Codex 负责写代码和响应审查意见。
2. **敏感信息**：API key、密码、token 等敏感信息**一律不入库**，放 `~/.hermes/.env` 或环境变量。
3. **语言**：与用户沟通、代码注释、文档均使用**中文**（代码标识符保留英文惯例）。
4. **部署约定**：定时任务用的脚本部署到 `~/.hermes/scripts/`；部署前在仓库内验证通过。
5. **临时文件**：用完即清理，不残留临时文件到仓库。

## 技术环境

- Python：3.11（Hermes venv: `/usr/local/lib/hermes-agent/venv/bin/python3`，`pip3` 装包）
- 已装工具：trafilatura（网页正文提取，2.2.0）
- 时区：北京时间（UTC+8）
- 服务器内存 1GB：不要跑本地大模型/重服务

## Skill 共享说明

Hermes 的部分 skill 通过软链共享到 `~/.codex/skills/`（如 vpn-proxy-server）。涉及这些领域（VPN/代理配置、行情数据等）时，Codex 应加载对应 skill 再动手，不要凭记忆瞎写。

## GitHub 托管与分支协作规范（2026-08-12 用户定）

仓库已托管到 GitHub：`git@github.com:qx921310/MyProject.git`（SSH）。Hermes（金仔）与 OpenClaw（钻仔）共同维护，**各自一个专属工作分支，合并到 main 前必须交叉 review**。

### 分支约定

- `main` — 合并目标（最终稳定版），**不直接在上面开发**
- `hermes-main` — Hermes（金仔）专属工作分支，由搬瓦工 `/root/CodeHub` 推送
- `openclaw-main` — 钻仔（OpenClaw）专属工作分支，由首尔服务器推送
- 每人在自己分支上开发；需要合入稳定版时，从自己分支向 `main` 开 Pull Request

### 交叉 review 铁律（用户明确要求）

1. **合并前必须 review**：任何分支向 `main` 的 PR，必须由**另一方**（不是作者自己）审查通过后才能合并
   - Hermes 的 PR → 钻仔 review
   - 钻仔的 PR → Hermes review
2. **review 内容**：代码正确性、安全（敏感信息泄露）、与仓库规范的符合度；发现的问题在 PR 上指出，作者修复后再合
3. **禁止自我合入**：作者不得在自己 review 自己的 PR（和本仓库"无代理自审"原则一致）
4. 仓库级规则（本文件）与各分支内容冲突时，以本文件 + 用户最终决定为准

### 日常流程（Hermes 侧）

```bash
cd /root/CodeHub
git pull origin hermes-main        # 拉最新（含钻仔改动同步时）
git add -A && git commit -m "..."  # 提交（仍走 requesting-code-review 管线）
git push origin hermes-main        # 推到自己分支
# 需要合入 main 时：开 PR，等钻仔 review
```

### 注意事项

- 敏感信息（API key/密码/token）仍一律不入库（见工作规范第 2 条）；`projects/proxy/secrets/*.enc` 是 age 加密产物，**必须入库**（加密后的凭证），不要忽略也不要解密后提交
- 钻仔首次接入：在首尔生成 GitHub SSH 密钥 → 公钥交用户加到 GitHub → 克隆仓库 → 建 `openclaw-main` 分支
- 分支同步：双方各自 push 后，如需在对方分支基础上工作，先 `git fetch origin && git checkout -b xxx origin/xxx` 或合并对方分支
