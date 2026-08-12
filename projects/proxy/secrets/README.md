# secrets 目录说明

本目录只存放 **age（X25519）加密后的敏感字段**（`*.enc`），是仓库内唯一的明文替代品。

## 字段清单

| 文件 | 字段 | 来源 | 用途 | 谁需要 |
|---|---|---|---|---|
| `HYSTERIA_AUTH_PASSWORD.enc` | Hysteria auth.password | 现有 `/etc/hysteria/config.yaml` | 服务端鉴权 + 订阅产物 | hysteria 服务、客户端 |
| `XRAY_UUID.enc` | VLESS 客户端 UUID | 现有 `/usr/local/etc/xray/config.json` | 服务端鉴权 + 订阅产物 | xray 服务、客户端 |
| `XRAY_PRIVATE_KEY.enc` | Reality 服务端私钥 | 现有 config.json `realitySettings.privateKey` | 服务端握手；公钥 pbk 由脚本推导 | xray 服务 |
| `XRAY_SHORT_ID.enc` | Reality shortIds[0] | 现有 config.json | 握手标识（半敏感） | xray 服务、客户端 |
| `SUB_TOKEN.enc` | 订阅 token | 现有 `/root/sub-token.txt` | 订阅 URL 目录名（http.server 唯一防线） | 订阅服务器、客户端 |

## 加解密方法

```bash
# 公钥（identity 文件头一行可见）：
age17mmhhrz552dqy7g25uqfkynhve8srr5kz04ae7qm6pwykgfymgjq8hkrkf

# 加密（新值入库时）：
printf '%s' "明文值" | age -r age17mmhhrz552dqy7g25uqfkynhve8srr5kz04ae7qm6pwykgfymgjq8hkrkf \
  > secrets/字段名.enc

# 解密（仅校验用，输出到终端/管道，勿写盘）：
age -d -i /root/secrets/age-identity.txt secrets/字段名.enc

# 批量解密为环境变量（供渲染）：
./scripts/decrypt-secrets.sh
```

## 安全要求

- identity：`/root/secrets/age-identity.txt`，权限 **600**，目录 `/root/secrets` 权限 **700**
- identity 必须纳入备份（`backup.sh` 已覆盖），否则 *.enc 全军覆没不可恢复
- 轮换任何字段后：重新加密对应 .enc → `deploy.sh --dry-run` 校验 → 按 `docs/rotation.md` 全量同步订阅产物
- 仓库内任何明文出现都视为事故，立即清理并考虑轮换

## 威胁模型（诚实边界）

- 保护：仓库内容泄漏场景（拿到 repo 无法直接读明文）
- 不保护：服务器被 root 入侵、运行时内存/日志泄漏、订阅产物本身公开
