# 部署说明

## 1. 前置条件

- 已安装 age（`apt-get install age`，本项目已装 1.0.0）
- age identity：`/root/secrets/age-identity.txt`（权限 600，目录 700）
- secrets/*.enc 已加密（见 `secrets/README.md`）
- 非敏感变量在 `.env.example`（缺省即用它；如需覆盖复制为 `.env`）

## 2. 渲染 + 校验（dry-run，不切换）

```bash
cd /root/CodeHub/projects/proxy
./scripts/deploy.sh --dry-run
```

执行内容：解密 secrets → 合并环境变量 → 渲染全部模板到临时目录 → 校验
（占位符无残留、JSON 合法、xray `-test`、hysteria 冒烟、sub64 与 sub.txt 一致、links 文档齐全）。
**不做任何系统变更**；临时目录退出即清理。

## 3. 真实切换（Hermes 批准后手动执行）

```bash
./scripts/deploy.sh --real
# 或跳过交互确认：
./scripts/deploy.sh --real --yes
```

执行步骤：
1. 前置门禁：`test_health.sh old`（旧三服务必须健康）
2. 基线备份：`backup.sh` → `/var/backups/proxy/proxy-backup-<时间戳>.tar.gz`
3. 落盘 `/etc/proxy/{hysteria,xray,sub,links,client}/`（全新路径，不触碰旧路径）
4. 从 `/etc/hysteria/` 复制证书并设置权限（key 640 root:hysteria、cert 644）
5. 同步 `/root/` 下的 token/links 文档
6. 安装 `proxy-*.service` 单元并 `daemon-reload`（旧单元不删除）
7. 最终路径下再校验（hysteria 冒烟 + xray -test）
8. 停旧启新（窗口内订阅闪断，客户端自动重连）
9. `test_health.sh new` + `test_health.sh e2e` 验收；任一失败自动 `rollback.sh`

## 4. 切换前检查清单（pre-flight）

- [ ] 旧三服务 active：`systemctl status hysteria-server xray sub-server`
- [ ] 端口占用正常：`ss -ulnp | grep 38475`、`ss -tlnp | grep -E ':443|:8080'`
- [ ] 磁盘余量充足：`df -h /`（备份约 60-80MB）
- [ ] 最近一次 `deploy.sh --dry-run` 全绿
- [ ] 时间同步正常：`timedatectl`（Reality 依赖时间）
- [ ] 避开用户活跃时段（日志显示有常驻客户端）

## 5. 观察期

切换后保留旧单元与备份 ≥2 周。确认稳定后由人工清理：

```bash
systemctl disable hysteria-server xray sub-server   # 旧单元已停止，仅清理启用态
# 删除旧备份由 backup.sh 轮转自动完成；如需立即清理请人工确认
```
