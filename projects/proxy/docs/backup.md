# 备份与恢复

## 1. 备份范围（backup.sh 全量覆盖）

| 类别 | 内容 |
|---|---|
| 旧服务配置 | `/etc/hysteria/`（含证书）、`/usr/local/etc/xray/` |
| 订阅产物 | `/var/www/sub/` |
| systemd 单元 | 旧三单元 + 新三单元（若已安装） |
| 凭据文档 | `/root/{sub-token,subscription-links,node-link}.txt` |
| age identity | `/root/secrets/age-identity.txt`（**丢了它 *.enc 不可恢复**） |
| 二进制 | `/usr/local/bin/hysteria`、`/opt/xray/xray` |
| 新运行目录 | `/etc/proxy/`（若已部署） |

## 2. 执行与轮转

```bash
./scripts/backup.sh
```

- 输出：`/var/backups/proxy/proxy-backup-<时间戳>.tar.gz`（目录 700）
- 保留：最近 7 份（可用 `PROXY_BACKUP_KEEP` 调整），自动清理更早的
- 建议：用 cron 每日一次（一次性任务，非常驻进程），例：

```cron
0 4 * * * /root/CodeHub/projects/proxy/scripts/backup.sh >> /var/log/proxy-backup.log 2>&1
```

## 3. 恢复

```bash
tar -xzf /var/backups/proxy/proxy-backup-<时间戳>.tar.gz -C / --preserve-permissions
systemctl daemon-reload
```

恢复后按 `docs/rollback.md` 的演练流程完成「新机还原」验证。

## 4. 待办（需 Hermes 决策）

- 仓库无 git remote，备份目前只在本机 `/var/backups/`；建议建立私有 remote 或异地备份
