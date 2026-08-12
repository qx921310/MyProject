#!/usr/bin/env bash
# ============================================================
# 全量备份脚本
#
# 输出: /var/backups/proxy/proxy-backup-<时间戳>.tar.gz
# 覆盖: 旧服务配置/证书/订阅产物/单元/二进制、/root 凭据文档、age identity、
#       新运行目录 /etc/proxy（若已部署）
# 保留: 最近 KEEP 份（默认 7），自动清理更早的
#
# 用法: ./backup.sh
# ============================================================
set -euo pipefail

BACKUP_DIR=/var/backups/proxy
KEEP="${PROXY_BACKUP_KEEP:-7}"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# 逐个检查存在性，缺失项跳过（新机未部署 /etc/proxy 属正常）
INCLUDE=()
add() {
  if [ -e "$1" ]; then
    INCLUDE+=("$1")
  fi
  return 0
}
add /etc/hysteria
add /usr/local/etc/xray
add /var/www/sub
add /etc/systemd/system/hysteria-server.service
add /etc/systemd/system/xray.service
add /etc/systemd/system/sub-server.service
add /etc/systemd/system/proxy-hysteria.service
add /etc/systemd/system/proxy-xray.service
add /etc/systemd/system/proxy-sub.service
add /root/sub-token.txt
add /root/subscription-links.txt
add /root/node-link.txt
add /root/secrets/age-identity.txt
add /usr/local/bin/hysteria
add /opt/xray/xray
add /etc/proxy

[ "${#INCLUDE[@]}" -gt 0 ] || { echo "错误: 没有可备份的内容" >&2; exit 1; }

TS="$(date +%Y%m%d-%H%M%S)"
TARBALL="$BACKUP_DIR/proxy-backup-$TS.tar.gz"

echo ">>> 备份 ${#INCLUDE[@]} 项 -> $TARBALL"
tar -czf "$TARBALL" --preserve-permissions -C / "${INCLUDE[@]#/}"

# 完整性校验：能列出即认为可读
tar -tzf "$TARBALL" >/dev/null
echo ">>> 完整性校验通过，大小: $(du -h "$TARBALL" | cut -f1)"

# 清理旧备份（保留最近 KEEP 份）
mapfile -t OLD < <(ls -1t "$BACKUP_DIR"/proxy-backup-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)))
for f in "${OLD[@]}"; do
  echo ">>> 清理旧备份: $f"
  rm -f -- "$f"
done

echo "完成。"
