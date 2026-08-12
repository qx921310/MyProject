# 梯子基础设施项目（proxy）

统一管理服务器上三个代理服务：**Hysteria 2（UDP）**、**VLESS+Reality（TCP）**、**订阅文件服务器（http.server）**。

本项目采用「模板 + 加密凭证 + 脚本」分层：现有三个服务保持运行状态不变，本项目产出**新单元 `proxy-*` 与新配置路径 `/etc/proxy/`**，与旧单元并存但保持 disabled。真正切换由 Hermes 批准后手动执行 `deploy.sh --real`。

## 目录结构

```text
projects/proxy/
├── README.md              # 本文件
├── .env.example           # 非敏感变量（真实值，可入库）
├── .gitignore             # 兜底：明文/中间产物永不入库
├── docs/                  # architecture / deploy / operations / rollback / rotation / backup
├── templates/             # systemd 单元、hysteria/xray 配置、订阅产物、客户端模板
├── scripts/               # render / decrypt-secrets / deploy / rollback / backup / generate_sub / test_health
└── secrets/               # age 加密凭证（*.enc 是唯一入库的明文替代品）
```

## 快速上手

```bash
# 1. 前置：age + identity（/root/secrets/age-identity.txt，权限 600）
# 2. 加密敏感字段到 secrets/*.enc（见 secrets/README.md）
# 3. 渲染 + 校验（不切换、不改系统）：
./scripts/deploy.sh --dry-run
# 4. Hermes 批准后手动执行真实切换：
./scripts/deploy.sh --real
```

## 变量来源

| 类型 | 位置 | 说明 |
|---|---|---|
| 非敏感变量 | `.env.example` | 服务器 IP、端口、SNI、Reality 目标站等 |
| 敏感变量 | `secrets/*.enc` | age（X25519）加密，解密用 `/root/secrets/age-identity.txt` |
| 运行目录 | `/etc/proxy/` | 渲染产物落盘位置（仓库外），部署时创建 |

敏感字段清单见 `secrets/README.md`；完整部署/回滚/轮换流程见 `docs/`。

## 安全边界（诚实声明）

- age 加密保护的是「仓库内容泄漏」场景：拿到仓库的人无法直接读明文。
- **不保护** root 被入侵（identity 与 secrets 同机）、运行时内存/日志泄漏、订阅产物公开（sub-server 无鉴权，token 是唯一防线）。
- 项目内任何位置出现明文凭据都视为事故，立即删除并考虑轮换。
