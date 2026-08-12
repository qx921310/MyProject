#!/usr/bin/env bash
# ============================================================
# 回滚脚本：停新单元 -> 恢复最近备份 -> 启旧单元
#
# 用法:
#   ./rollback.sh --list                 # 列出可用备份
#   ./rollback.sh [--backup <文件>]      # 回滚（缺省用最新备份）
#
# 说明:
#   - 新单元只停止并 disabled，不删除文件（观察期 2 周后由人工清理）
#   - 备份覆盖旧配置/单元/证书/订阅产物/二进制/identity，恢复到原路径
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR=/var/backups/proxy
NEW_UNITS=(proxy-hysteria.service proxy-xray.service proxy-sub.service)
OLD_UNITS=(hysteria-server.service xray.service sub-server.service)

list_backups() {
  ls -1t "$BACKUP_DIR"/proxy-backup-*.tar.gz 2>/dev/null || true
}

if [ "${1:-}" = "--list" ]; then
  list_backups
  exit 0
fi

BACKUP_FILE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --backup) BACKUP_FILE="${2:-}"; shift 2 ;;
    *) echo "错误: 未知参数: $1" >&2; exit 2 ;;
  esac
done
[ -n "$BACKUP_FILE" ] || BACKUP_FILE="$(list_backups | head -1)"
[ -n "$BACKUP_FILE" ] || { echo "错误: 没有可用备份（$BACKUP_DIR 为空）" >&2; exit 1; }
[ -f "$BACKUP_FILE" ] || { echo "错误: 备份文件不存在: $BACKUP_FILE" >&2; exit 1; }

echo ">>> 停止并禁用新单元..."
for u in "${NEW_UNITS[@]}"; do
  if systemctl list-unit-files "$u" >/dev/null 2>&1; then
    systemctl stop "$u" 2>/dev/null || true
    systemctl disable "$u" 2>/dev/null || true
    echo "  已停止: $u"
  fi
done

echo ">>> 恢复备份: $BACKUP_FILE"
tar -xzf "$BACKUP_FILE" -C / --preserve-permissions

echo ">>> 重新加载 systemd 并启动旧单元..."
systemctl daemon-reload
for u in "${OLD_UNITS[@]}"; do
  systemctl start "$u"
  systemctl enable "$u" 2>/dev/null || true
  echo "  已启动: $u"
done

"$SCRIPT_DIR/test_health.sh" old || { echo ">>> 回滚后旧服务复检未通过，请人工介入" >&2; exit 1; }
echo "回滚完成。"
