# 运维手册

## 1. 日常状态检查

```bash
# 新单元（切换后）
systemctl status proxy-hysteria proxy-xray proxy-sub --no-pager
systemctl is-enabled proxy-hysteria proxy-xray proxy-sub

# 端口（注意：Hysteria 是 UDP，必须用 -u）
ss -ulnp | grep 38475
ss -tlnp | grep -E ':443|:8080'

# 日志
journalctl -u proxy-hysteria -n 20 --no-pager
journalctl -u proxy-xray -n 20 --no-pager
journalctl -u proxy-sub -n 20 --no-pager
```

## 2. 排障要点

| 现象 | 排查方向 |
|---|---|
| `ss -tlnp` 看不到 38475 | 正常！Hysteria 是 UDP，看 `ss -ulnp` |
| xray 日志刷 `REALITY: processed invalid connection` | 客户端 Reality 握手未解密（key/sid/sni 不匹配或客户端 bug）；先本地回环测试，勿急着改服务器 |
| hysteria 启动报证书权限 | key 必须 640 root:hysteria、cert 644（skill 踩坑记录） |
| 端口被占 | 旧单元未停或新单元未启，核对 `systemctl list-units` |
| 客户端显示错误地区 | 检查是否真的走了代理（节点延迟 ≠ 流量经过） |

## 3. 重新生成订阅产物（轮换后必须做）

```bash
python3 scripts/generate_sub.py --output-dir /etc/proxy
cp /etc/proxy/links/subscription-links.txt /root/subscription-links.txt
cp /etc/proxy/links/node-link.txt /root/node-link.txt
```

所有订阅产物（clash.yaml / sub.txt / sub64.txt / links / node-link）同批更新，
杜绝轮换漏同步（skill 记录的既有事故）。

## 4. 本地回环端到端验证

```bash
# Hysteria2（CA 固定客户端模板）
/usr/local/bin/hysteria client -c /etc/proxy/client/hysteria2-client.yaml
curl --socks5 127.0.0.1:1080 https://www.google.com
curl --socks5 127.0.0.1:1080 https://api.ipify.org   # 应为 173.242.113.39

# VLESS+Reality：优先用 mihomo（skill 实测 xray 客户端有握手兼容问题）
# 单节点全局模式 + curl -x http://127.0.0.1:<port> https://ipinfo.io/json
```

## 5. 备份与恢复

```bash
./scripts/backup.sh                 # 全量备份
./scripts/rollback.sh --list        # 查看备份
./scripts/rollback.sh               # 回滚到最新备份（停新启旧）
```

详见 `docs/backup.md` 与 `docs/rollback.md`。
