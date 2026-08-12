# 架构说明

## 1. 拓扑与流量路径

服务器（173.242.113.39, LA）同时提供两条出口路径与一个订阅发布面：

| 服务 | 协议/端口 | 角色 | 说明 |
|---|---|---|---|
| Hysteria 2 | UDP 38475（QUIC） | 主节点 my-node | 自签证书（CN/SAN=bing.com），masquerade 到 bing.com（rewriteHost=true 防探测） |
| VLESS+Reality | TCP 443 | 备用节点 my-node-tcp | dest 固定 IPv4 `142.251.15.91:443`（dl.google.com），UDP 被 QoS 限速时的首选 |
| 订阅服务器 | TCP 8080 | 发布面 | `python3 -m http.server`，无鉴权，目录名即 token（唯一防线） |

流量路径：客户端 → 公网 IP:端口 → 服务端出口（freedom 直连）。

## 2. 服务与文件对照

| 维度 | 旧（现状，保持运行） | 新（本项目，disabled 待切换） |
|---|---|---|
| Hysteria 单元 | `/etc/systemd/system/hysteria-server.service` | `/etc/systemd/system/proxy-hysteria.service`（模板） |
| Hysteria 配置 | `/etc/hysteria/config.yaml` | `/etc/proxy/hysteria/config.yaml`（渲染产物） |
| Xray 单元 | `/etc/systemd/system/xray.service` | `/etc/systemd/system/proxy-xray.service`（模板） |
| Xray 配置 | `/usr/local/etc/xray/config.json` | `/etc/proxy/xray/config.json`（渲染产物） |
| 订阅单元 | `/etc/systemd/system/sub-server.service` | `/etc/systemd/system/proxy-sub.service`（模板） |
| 订阅根目录 | `/var/www/sub/` | `/etc/proxy/sub/` |
| links/token 文档 | `/root/{sub-token,subscription-links,node-link}.txt` | 渲染产物 `/etc/proxy/links/`，部署时同步回 `/root/` |
| 证书 | `/etc/hysteria/server.{crt,key}` | 部署时复制到 `/etc/proxy/hysteria/`，key 640 root:hysteria |

旧单元与备份在切换后保留 ≥2 周观察期，之后由人工清理。

## 3. 配置分层

```text
templates/（占位符 {{VAR}}）
   └─ render.py 渲染（输入：.env 非敏感 + secrets/*.enc 解密值）
        └─ /etc/proxy/（运行时产物，仓库外）
```

- 非敏感变量（IP、端口、SNI、dest 等）→ `.env.example`
- 敏感变量（密码、UUID、Reality 私钥、shortId、订阅 token）→ `secrets/*.enc`（age X25519 加密）
- `XRAY_PUBLIC_KEY`（pbk）不单独存放，由脚本从私钥推导（`scripts/x25519.py`），杜绝公私不匹配

## 4. 安全基线（现状与待办）

已落实：
- age identity `/root/secrets/age-identity.txt` 权限 600、目录 700
- secrets 只存 `*.enc`，明文不落仓库；decrypt 输出只走管道
- hysteria 单元沿用 User=hysteria + capability 裁剪；证书 key 640 root:hysteria、cert 644
- Reality dest 避开 www.microsoft.com（已知证书尺寸 bug，见 skill）

待 Hermes 决策的改进（本项目不擅自实施）：
- xray 单元降权（当前与旧单元一致以 root 运行；建议 User=xray + NoNewPrivileges + capability 裁剪）
- xray loglevel 由 debug 改为 warning（本项目为保持渲染产物与现状无差异，维持 debug）
- sub-server 加鉴权（nginx/caddy basic auth）——当前维持 http.server + 长 token
- 仓库建立私有 remote 做异地备份
- 订阅自动更新（cron/systemd timer 一次性任务，1GB 小机不建议常驻）

## 5. 已知风险

- 切换是「停旧启新」瞬时操作，三个端口无法并行占位，窗口内订阅拉取闪断
- sub-server 无鉴权：token 泄漏即订阅公开（长随机 token 是唯一防线）
- 服务器无全局 IPv6：Reality dest 固定 IPv4，客户端 SNI 用域名（dl.google.com）
