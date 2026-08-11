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
