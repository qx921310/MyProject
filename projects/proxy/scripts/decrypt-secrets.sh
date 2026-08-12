#!/usr/bin/env bash
# ============================================================
# age 解密脚本：把 secrets/*.enc 解密为 KEY=value 行输出到标准输出
#
# 用法:
#   ./decrypt-secrets.sh [secrets_dir] [identity_file]
#   环境变量 AGE_IDENTITY 可覆盖 identity 路径
#
# 安全约定:
#   - 明文只走标准输出（管道给 render.py / generate_sub.py 使用），绝不落盘
#   - identity 权限必须为 600，secrets 目录内只认 *.enc
#   - 退出码非 0 表示解密失败，调用方应中止
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SECRETS_DIR="${1:-$PROJECT_DIR/secrets}"
IDENTITY="${2:-${AGE_IDENTITY:-/root/secrets/age-identity.txt}}"

fail() { echo "错误: $*" >&2; exit 1; }

command -v age >/dev/null 2>&1 || fail "age 未安装（apt-get install age）"
[ -f "$IDENTITY" ] || fail "identity 不存在: $IDENTITY"
[ "$(stat -c %a "$IDENTITY")" = "600" ] || fail "identity 权限应为 600，当前为 $(stat -c %a "$IDENTITY")"
[ -d "$SECRETS_DIR" ] || fail "secrets 目录不存在: $SECRETS_DIR"

count=0
for enc in "$SECRETS_DIR"/*.enc; do
  [ -f "$enc" ] || continue
  name="$(basename "$enc" .enc)"
  value="$(age -d -i "$IDENTITY" "$enc")" || fail "解密失败: $enc"
  printf '%s=%s\n' "$name" "$value"
  count=$((count + 1))
done

[ "$count" -gt 0 ] || fail "secrets 目录下没有 *.enc 文件"
echo "提示: 已解密 $count 个字段（仅输出到标准输出，未写盘）" >&2
