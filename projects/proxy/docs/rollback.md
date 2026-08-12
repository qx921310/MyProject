# 回滚说明

## 1. 自动回滚触发条件（真实部署时任一命中即回滚）

- 新单元启动失败或反复退出（`systemctl start` 失败）
- `test_health.sh new` 任一端口/服务检查失败
- `test_health.sh e2e` 端到端检查失败
- 切换后 60 秒内外连异常（人工判断）

自动回滚由 `deploy.sh --real` 内置调用：停新 → 恢复备份 → 启旧 → 复检。

## 2. 手动回滚

```bash
./scripts/rollback.sh --list              # 列出可用备份
./scripts/rollback.sh --backup /var/backups/proxy/proxy-backup-<时间戳>.tar.gz
```

步骤：
1. 停止并 disabled 三个 `proxy-*` 单元（不删除单元文件）
2. 从备份 tarball 恢复旧配置/单元/证书/订阅产物/二进制/identity 到原路径
3. `systemctl daemon-reload`
4. 启动旧三单元（hysteria-server / xray / sub-server）
5. `test_health.sh old` 复检

## 3. 恢复演练（建议每季度一次）

在测试机/新机执行「新机还原」：

```bash
# 1. 恢复备份 tarball
tar -xzf /var/backups/proxy/proxy-backup-*.tar.gz -C / --preserve-permissions
# 2. 校验 identity 可解密 secrets
./scripts/decrypt-secrets.sh
# 3. 渲染 + 校验（不切换）
./scripts/deploy.sh --dry-run
# 4. 真实部署
./scripts/deploy.sh --real --yes
```

演练同时验证 secrets 解密链路与备份可恢复性。

## 4. 观察期约定

切换成功后旧单元与备份保留 ≥2 周；未发现异常前不得清理。
