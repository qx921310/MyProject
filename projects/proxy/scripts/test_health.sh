#!/usr/bin/env bash
# ============================================================
# 验收检查脚本
#
# 用法:
#   ./test_health.sh static   # 基线：identity/secrets/仓库无明文/订阅与密钥一致性
#   ./test_health.sh old      # 旧三服务运行状态 + 端口（观察期要求）
#   ./test_health.sh new      # 新三服务运行状态 + 端口 + 渲染配置
#   ./test_health.sh e2e      # 端到端（需要新服务已切换；hysteria 回环 + 订阅拉取）
#   ./test_health.sh all      # 全部可用检查
#
# 输出: 每项 [通过]/[失败]/[警告]，最终退出码 0=全部通过
# ============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
IDENTITY="${AGE_IDENTITY:-/root/secrets/age-identity.txt}"
RUN_DIR="${PROXY_RUN_DIR:-/etc/proxy}"

declare -i PASS=0 FAIL=0
ok() { echo "  [通过] $*"; PASS+=1; }
bad() { echo "  [失败] $*"; FAIL+=1; }
warn() { echo "  [警告] $*"; }

secret_value() {
  # 解密指定字段名（不输出明文）
  local name="$1"
  age -d -i "$IDENTITY" "$PROJECT_DIR/secrets/$name.enc" 2>/dev/null || true
}

check_identity() {
  echo "--- 检查: identity 权限"
  if [ -d /root/secrets ] && [ "$(stat -c %a /root/secrets)" = "700" ]; then
    ok "/root/secrets 权限 700"
  else
    bad "/root/secrets 权限不是 700"
  fi
  if [ -f "$IDENTITY" ] && [ "$(stat -c %a "$IDENTITY")" = "600" ]; then
    ok "identity 权限 600"
  else
    bad "identity 不存在或权限不是 600: $IDENTITY"
  fi
}

check_decrypt() {
  echo "--- 检查: secrets 可解密"
  command -v age >/dev/null 2>&1 || { bad "age 未安装"; return; }
  local all_ok=1 n=0
  for enc in "$PROJECT_DIR"/secrets/*.enc; do
    [ -f "$enc" ] || continue
    n=$((n + 1))
    age -d -i "$IDENTITY" "$enc" >/dev/null 2>&1 || { bad "解密失败: $enc"; all_ok=0; }
  done
  [ "$n" -gt 0 ] || { bad "secrets 目录没有 *.enc"; return; }
  [ "$all_ok" = 1 ] && ok "$n 个 *.enc 全部可解密"
}

check_no_plaintext() {
  echo "--- 检查: 仓库无明文凭据（排除 .git 与 secrets/）"
  local leaked=0 n=0
  for enc in "$PROJECT_DIR"/secrets/*.enc; do
    [ -f "$enc" ] || continue
    n=$((n + 1))
    local value
    value="$(age -d -i "$IDENTITY" "$enc" 2>/dev/null)" || continue
    if grep -rIl -F -- "$value" "$PROJECT_DIR" \
        --exclude-dir=.git --exclude-dir=secrets >/dev/null 2>&1; then
      bad "仓库中出现明文（字段: $(basename "$enc" .enc)）"
      leaked=1
    fi
  done
  [ "$leaked" = 0 ] && ok "$n 个敏感字段均未在仓库出现明文"
}

check_pbk() {
  echo "--- 检查: Reality 私钥与订阅产物公钥配对"
  local priv pub actual
  priv="$(secret_value XRAY_PRIVATE_KEY)"
  [ -n "$priv" ] || { bad "无法读取 XRAY_PRIVATE_KEY"; return; }
  pub="$(python3 "$SCRIPT_DIR/x25519.py" derive "$priv" 2>/dev/null)" || {
    bad "pbk 推导失败"; return; }
  actual=""
  if [ -d /etc/proxy/sub ]; then
    actual="$(grep -h 'public-key:' /etc/proxy/sub/*/clash.yaml 2>/dev/null | head -1 \
      | awk '{print $2}' | tr -d '\r')"
  fi
  if [ -z "$actual" ]; then
    actual="$(grep -h 'public-key:' /var/www/sub/*/clash.yaml 2>/dev/null | head -1 \
      | awk '{print $2}' | tr -d '\r')"
  fi
  if [ -n "$actual" ]; then
    if [ "$pub" = "$actual" ]; then
      ok "pbk 与线上订阅一致（推导: $pub）"
    else
      bad "pbk 不匹配：推导=$pub 线上=$actual"
    fi
  else
    warn "未找到线上 clash.yaml 的 public-key（只报告推导值: $pub）"
  fi
}

check_sub_consistency() {
  echo "--- 检查: 订阅产物一致性（渲染产物若存在）"
  local token sub_dir
  token="$(secret_value SUB_TOKEN)"
  [ -n "$token" ] || { bad "无法读取 SUB_TOKEN"; return; }
  sub_dir="$RUN_DIR/sub/$token"
  if [ ! -f "$sub_dir/sub.txt" ]; then
    warn "渲染产物不存在（$sub_dir），跳过（真实部署后检查）"
    return
  fi
  if [ "$(cat "$sub_dir/sub64.txt")" = "$(base64 -w0 < "$sub_dir/sub.txt")" ]; then
    ok "sub64 与 sub.txt 一致"
  else
    bad "sub64 与 sub.txt 不一致"
  fi
}

check_xray_cfg() {
  echo "--- 检查: 渲染后的 xray 配置"
  if [ -f "$RUN_DIR/xray/config.json" ] && [ -x /opt/xray/xray ]; then
    /opt/xray/xray run -test -c "$RUN_DIR/xray/config.json" >/dev/null 2>&1 \
      && ok "xray -test 通过" || bad "xray -test 失败"
  else
    warn "渲染配置或 xray 二进制不存在，跳过"
  fi
}

check_services() {
  local kind="$1"
  local units=()
  if [ "$kind" = "old" ]; then
    units=(hysteria-server.service xray.service sub-server.service)
  else
    units=(proxy-hysteria.service proxy-xray.service proxy-sub.service)
  fi
  echo "--- 检查: $kind 服务状态与端口"
  for u in "${units[@]}"; do
    if systemctl is-active --quiet "$u" 2>/dev/null; then
      ok "$u active"
    else
      bad "$u 未运行"
    fi
    if [ "$kind" = "new" ]; then
      systemctl is-enabled --quiet "$u" 2>/dev/null \
        && ok "$u enabled" || bad "$u 未 enabled"
    fi
  done
  # 端口（hysteria 是 UDP，必须用 ss -ulnp）
  ss -ulnp 2>/dev/null | grep -q ":38475 " && ok "UDP 38475 监听中" || bad "UDP 38475 未监听"
  ss -tlnp 2>/dev/null | grep -q ":443 " && ok "TCP 443 监听中" || bad "TCP 443 未监听"
  ss -tlnp 2>/dev/null | grep -q ":8080 " && ok "TCP 8080 监听中" || bad "TCP 8080 未监听"
}

check_e2e() {
  echo "--- 检查: 端到端（hysteria 本地回环 + 订阅拉取）"
  local client_cfg="$RUN_DIR/client/hysteria2-client.yaml"
  if ! systemctl is-active --quiet proxy-hysteria.service 2>/dev/null; then
    warn "新 hysteria 未运行，跳过端到端（真实部署后检查）"
    return
  fi
  [ -f "$client_cfg" ] || { bad "缺少客户端模板: $client_cfg"; return; }
  local logf pid rc
  logf="$(mktemp "${TMPDIR:-/tmp}/proxy-e2e.XXXXXX")"
  trap 'rm -f "$logf"' EXIT
  /usr/local/bin/hysteria client -c "$client_cfg" >"$logf" 2>&1 &
  pid=$!
  sleep 2
  local code ip expected_ip
  code="$(curl -s -m 10 --socks5 127.0.0.1:1080 -o /dev/null -w '%{http_code}' https://www.google.com 2>/dev/null)"
  ip="$(curl -s -m 10 --socks5 127.0.0.1:1080 https://api.ipify.org 2>/dev/null)"
  local ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
  [ -f "$ENV_FILE" ] || ENV_FILE="$PROJECT_DIR/.env.example"
  expected_ip="$(grep -E '^SERVER_IP=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  rm -f -- "$logf"
  [ "$code" = "200" ] && ok "hysteria 回环 curl google = 200" || bad "hysteria 回环 curl google = ${code:-超时}"
  if [ "$ip" = "$expected_ip" ] && [ -n "$expected_ip" ]; then
    ok "出口 IP = $ip（与服务器一致）"
  else
    bad "出口 IP 不符: $ip（期望 $expected_ip）"
  fi

  # 订阅拉取（经公网 IP 用 token URL）
  local token url
  token="$(secret_value SUB_TOKEN)"
  url="http://$expected_ip:8080/$token/clash.yaml"
  [ "$(curl -s -m 10 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null)" = "200" ] \
    && ok "订阅 URL 返回 200" || bad "订阅 URL 未返回 200: $url"

  # VLESS+Reality 回环（xray 客户端；若握手失败改用 mihomo，见 docs/operations.md）
  warn "VLESS+Reality 端到端：需 mihomo 或 xray 客户端（见 docs/operations.md），本检查项仅提示"
}

summary() {
  echo
  echo "检查完成: 通过 $PASS 项, 失败 $FAIL 项"
  [ "$FAIL" -eq 0 ]
}

case "${1:-all}" in
  static) check_identity; check_decrypt; check_no_plaintext; check_pbk; check_sub_consistency; check_xray_cfg ;;
  old) check_services old ;;
  new) check_services new; check_sub_consistency; check_xray_cfg ;;
  e2e) check_e2e ;;
  all)
    check_identity; check_decrypt; check_no_plaintext; check_pbk
    check_sub_consistency; check_xray_cfg; check_services old
    if systemctl is-active --quiet proxy-hysteria.service 2>/dev/null; then
      check_services new; check_e2e
    fi
    ;;
  *) echo "用法: $0 static|old|new|e2e|all" >&2; exit 2 ;;
esac

summary
