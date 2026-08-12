# 凭证与证书轮换 SOP

## 1. 轮换原则

- **同批原子轮换**：任何服务端凭据变更，必须同批更新全部订阅产物，否则现有客户端全部失效
- 轮换前先备份：`./scripts/backup.sh`
- 轮换后重新加密 secrets 并重跑 `deploy.sh --dry-run` 校验

## 2. Reality 密钥对 + UUID + shortId（xray 节点）

```bash
cd /opt/xray
./xray x25519          # 新 PrivateKey / PublicKey
./xray uuid            # 新 UUID
# shortId: 随机 16 位 hex（如 openssl rand -hex 8）
```

同步清单（全部必须同批更新）：
1. `secrets/XRAY_PRIVATE_KEY.enc`、`secrets/XRAY_UUID.enc`、`secrets/XRAY_SHORT_ID.enc` 重新加密
2. 重新渲染：`./scripts/deploy.sh --dry-run` 或 `generate_sub.py --output-dir /etc/proxy`
3. 更新 `/etc/proxy/xray/config.json`（真实部署后为运行配置）并重启 `proxy-xray`
4. 发布订阅：`clash.yaml`、`sub.txt`、`sub64.txt`、`subscription-links.txt`、`node-link.txt`
5. 校验：`test_health.sh static`（pbk 推导一致性）+ 端到端

> 公钥 pbk 由脚本从私钥推导，不手工复制，杜绝私/公不匹配。

## 3. Hysteria 密码轮换

1. 改 `secrets/HYSTERIA_AUTH_PASSWORD.enc`（先与 Hermes 确认：改变会静默断开所有现有客户端）
2. 重新渲染服务端配置 + 订阅产物并重启 `proxy-hysteria`
3. 全量发布订阅（同第 2 节清单）

## 4. Hysteria 证书轮换

```bash
# EC 私钥 + SAN（CN 与伪装域名一致）
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 \
  -keyout server.key -out server.crt -days 3650 -nodes \
  -subj "/CN=bing.com" -addext "subjectAltName=DNS:bing.com,IP:173.242.113.39"
cp server.crt /etc/proxy/hysteria/server.crt
cp server.key /etc/proxy/hysteria/server.key
chown root:hysteria /etc/proxy/hysteria/server.key
chmod 640 /etc/proxy/hysteria/server.key
```

客户端 CA 固定同步顺序：**先向使用 CA 固定的客户端分发新证书，再重启服务**。
订阅产物中的 insecure=1 节点不受证书影响，无需分发。

## 5. 订阅 token 轮换

1. 新 token = `openssl rand -hex 16`（长随机，防扫描）
2. 重新加密 `secrets/SUB_TOKEN.enc`
3. `generate_sub.py` 重新生成（web 目录名 = 新 token）
4. 删除旧 token 目录并同步 `/root/sub-token.txt` 与新链接文档
5. 通知所有客户端重新拉取订阅

## 6. age identity 轮换

1. `age-keygen -o /root/secrets/age-identity.txt && chmod 600 ...`
2. 用新公钥重新加密全部 `secrets/*.enc`
3. 备份新 identity；旧 identity 在确认全部 *.enc 可解密后销毁
