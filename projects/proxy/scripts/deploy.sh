#!/usr/bin/env bash
# ============================================================
# 部署脚本（项目化迁移）
#
# 用法:
#   ./deploy.sh                 # 默认 = --dry-run：只渲染 + 校验，不做任何系统变更
#   ./deploy.sh --dry-run       # 同上（显式）
#   ./deploy.sh --real          # 真实切换：备份→渲染→校验→停旧→启新→验收→失败自动回滚
#   ./deploy.sh --real --yes    # 真实切换（跳过交互确认，供手动执行）
#
# 硬性约束（脚本内强制）:
#   - 不修改 /etc/hysteria、/usr/local/etc/xray、/var/www/sub 下任何现有文件
#   - 新单元 proxy-*.service + 新运行路径 /etc/proxy/，与旧单元并存
#   - 真实切换前必须由 Hermes 批准后手动执行
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

MODE="dry-run"
CONFIRM="no"
for arg in "$@"; do
  case "$arg" in
    --dry-run) MODE="dry-run" ;;
    --real) MODE="real" ;;
    --yes) CONFIRM="yes" ;;
    *)
      echo "错误: 未知参数: $arg（支持 --dry-run / --real / --yes）" >&2
      exit 2
      ;;
  esac
done

ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
[ -f "$ENV_FILE" ] || ENV_FILE="$PROJECT_DIR/.env.example"
TEMPLATES_DIR="$PROJECT_DIR/templates"
SECRETS_DIR="$PROJECT_DIR/secrets"
IDENTITY="${AGE_IDENTITY:-/root/secrets/age-identity.txt}"
RUN_DIR="${PROXY_RUN_DIR:-/etc/proxy}"          # 真实切换的最终运行目录
OLD_UNITS=(hysteria-server.service xray.service sub-server.service)
NEW_UNITS=(proxy-hysteria.service proxy-xray.service proxy-sub.service)

# 临时目录：渲染产物与合并后的环境文件（退出时自动清理，不留仓库/系统残留）
RENDER_OUT="$(mktemp -d "${TMPDIR:-/tmp}/proxy-render.XXXXXX")"
TMP_ENV="$(mktemp "${TMPDIR:-/tmp}/proxy-env.XXXXXX")"
trap 'rm -rf "$RENDER_OUT" "$TMP_ENV"' EXIT

step() { echo; echo "==> $*"; }
ok() { printf '  [通过] '; printf '%s\n' "$*"; }
warn() { printf '  [警告] '; printf '%s\n' "$*" >&2; }
fail() { printf '  [失败] '; printf '%s\n' "$*" >&2; exit 1; }

# ---------- 前置检查 ----------
preflight() {
  step "前置检查"
  command -v age >/dev/null 2>&1 || fail "age 未安装"
  command -v python3 >/dev/null 2>&1 || fail "python3 未安装"
  command -v systemctl >/dev/null 2>&1 || fail "systemctl 不可用"
  [ -f "$IDENTITY" ] || fail "age identity 不存在: $IDENTITY"
  [ "$(stat -c %a "$IDENTITY")" = "600" ] || fail "identity 权限应为 600"
  [ -d "$TEMPLATES_DIR" ] || fail "templates 目录不存在"
  [ -d "$SECRETS_DIR" ] || fail "secrets 目录不存在"
  [ -n "$(ls "$SECRETS_DIR"/*.enc 2>/dev/null)" ] || fail "secrets 目录没有 *.enc"
  ok "age / python3 / identity / templates / secrets 就绪"
}

# ---------- 解密并合并环境 ----------
merge_env() {
  step "解密 secrets 并合并环境变量（临时文件，退出自动清理）"
  { cat "$ENV_FILE"; "$SCRIPT_DIR/decrypt-secrets.sh" "$SECRETS_DIR" "$IDENTITY"; } > "$TMP_ENV"
  ok "环境变量合并完成（$(basename "$ENV_FILE") + 敏感字段已注入）"
}

# ---------- 渲染 ----------
render() {
  step "渲染模板 -> $RENDER_OUT"
  python3 "$SCRIPT_DIR/render.py" --env-file "$TMP_ENV" \
    --templates-dir "$TEMPLATES_DIR" --output-dir "$RENDER_OUT"
}

# 对指定 hysteria 配置做冒烟
# dry-run 模式：仅配置结构校验（不启动进程、不打端口）
# 真实模式：启动进程冒烟（解析即过；端口被旧服务占用属预期）
smoke_hysteria() {
  local cfg="$1"
  [ -x /usr/local/bin/hysteria ] || { warn "未找到 hysteria 二进制，跳过冒烟"; return 0; }

  if [ "$MODE" = "dry-run" ]; then
    [ -f "$cfg" ] || fail "hysteria 配置文件不存在: $cfg"
    grep -q 'listen:' "$cfg" || fail "hysteria 配置缺少 listen 字段: $cfg"
    grep -q -E '(acme:|tls:)' "$cfg" || warn "hysteria 配置未检测到证书/acme 配置（真实部署会先复制证书再校验）"
    ok "hysteria dry-run 配置结构校验通过（listen + 证书字段存在）"
    return 0
  fi

  local out rc
  set +e
  out="$(timeout 3 /usr/local/bin/hysteria server -c "$cfg" 2>&1)"
  rc=$?
  set -e
  if echo "$out" | grep -q "failed to read server config"; then
    echo "$out" >&2
    fail "hysteria 配置解析失败: $cfg"
  fi
  if [ "$rc" -eq 124 ]; then
    ok "hysteria 配置冒烟通过（进程存活至超时，配置可正常加载）"
  elif echo "$out" | grep -q "address already in use"; then
    ok "hysteria 配置冒烟通过（配置解析正常，端口被现有服务占用属预期）"
  elif echo "$out" | grep -q "no such file"; then
    warn "hysteria 配置结构正常，但证书尚未就位（真实部署会先复制证书再校验）"
  else
    echo "$out" >&2
    fail "hysteria 冒烟输出无法识别: $cfg"
  fi
}

validate_rendered() {
  step "校验渲染产物"
  local cfg
  cfg="$RENDER_OUT/xray/config.json"
  if [ -x /opt/xray/xray ]; then
    /opt/xray/xray run -test -c "$cfg" >/dev/null 2>&1 \
      || fail "xray -test 校验失败: $cfg"
    ok "xray -test 通过（$cfg）"
  else
    warn "未找到 /opt/xray/xray，跳过 xray -test"
  fi
  smoke_hysteria "$RENDER_OUT/hysteria/config.yaml"
  # 订阅产物一致性（render.py 已内建，这里再显式复核一次）
  local token sub_dir
  token="$(grep -E '^SUB_TOKEN=' "$TMP_ENV" | head -1 | cut -d= -f2-)"
  sub_dir="$RENDER_OUT/sub/$token"
  [ -f "$sub_dir/sub.txt" ] || fail "缺少 sub.txt"
  [ -f "$sub_dir/sub64.txt" ] || fail "缺少 sub64.txt"
  [ "$(cat "$sub_dir/sub64.txt")" = "$(base64 -w0 < "$sub_dir/sub.txt")" ] \
    || fail "sub64.txt 与 sub.txt 不一致"
  ok "订阅产物存在且 sub64 与 sub.txt 一致"
  [ -f "$RENDER_OUT/links/subscription-links.txt" ] || fail "缺少 links 文档"
  ok "links 文档已生成"
}

# ---------- 真实部署步骤 ----------
real_deploy() {
  step "真实部署（备份 -> 落盘 -> 装单元 -> 停旧 -> 启新 -> 验收）"
  # 1) 切换前基线备份
  "$SCRIPT_DIR/backup.sh"

  # 2) 创建运行目录并落盘渲染产物（/etc/proxy/，全新路径，不触碰旧路径）
  mkdir -p "$RUN_DIR/hysteria" "$RUN_DIR/xray" "$RUN_DIR/sub" "$RUN_DIR/links" "$RUN_DIR/client"
  cp "$RENDER_OUT/hysteria/config.yaml" "$RUN_DIR/hysteria/config.yaml"
  cp "$RENDER_OUT/xray/config.json" "$RUN_DIR/xray/config.json"
  cp -r "$RENDER_OUT/sub"/* "$RUN_DIR/sub/"
  cp -r "$RENDER_OUT/links"/* "$RUN_DIR/links/"
  cp -r "$RENDER_OUT/client"/* "$RUN_DIR/client/"

  # 3) 证书：从旧路径复制到新路径（只读旧路径；权限满足 User=hysteria 要求）
  cp /etc/hysteria/server.crt "$RUN_DIR/hysteria/server.crt"
  cp /etc/hysteria/server.key "$RUN_DIR/hysteria/server.key"
  chown root:hysteria "$RUN_DIR/hysteria/server.crt" "$RUN_DIR/hysteria/server.key"
  chmod 644 "$RUN_DIR/hysteria/server.crt"
  chmod 640 "$RUN_DIR/hysteria/server.key"
  ok "配置/证书/订阅产物已落盘 $RUN_DIR"

  # 4) 发布 links 文档与 token 到 /root（与现状位置一致）
  cp "$RENDER_OUT/links/subscription-links.txt" /root/subscription-links.txt
  cp "$RENDER_OUT/links/node-link.txt" /root/node-link.txt
  grep -E '^SUB_TOKEN=' "$TMP_ENV" | cut -d= -f2- > /root/sub-token.txt
  ok "/root 下订阅文档已同步"

  # 5) 安装新单元（不删除旧单元）
  install -m 644 "$RENDER_OUT/systemd/proxy-hysteria.service" /etc/systemd/system/proxy-hysteria.service
  install -m 644 "$RENDER_OUT/systemd/proxy-xray.service" /etc/systemd/system/proxy-xray.service
  install -m 644 "$RENDER_OUT/systemd/proxy-sub.service" /etc/systemd/system/proxy-sub.service
  systemctl daemon-reload
  ok "新单元已安装并 daemon-reload（保持 disabled）"

  # 6) 最终路径下再校验一次
  smoke_hysteria "$RUN_DIR/hysteria/config.yaml"
  /opt/xray/xray run -test -c "$RUN_DIR/xray/config.json" >/dev/null 2>&1 \
    || fail "xray -test 最终校验失败"
  ok "最终路径配置校验通过"

  # 7) 切换：停旧 -> 启新（窗口内订阅拉取会闪断，客户端自动重连）
  step "停旧启新"
  for u in "${OLD_UNITS[@]}"; do
    systemctl stop "$u" 2>/dev/null || true
    systemctl disable "$u" 2>/dev/null || true
    echo "  已停止旧单元: $u"
  done
  for u in "${NEW_UNITS[@]}"; do
    systemctl start "$u"
    systemctl enable "$u"
    echo "  已启动新单元: $u"
  done

  # 8) 验收：失败自动回滚
  if ! "$SCRIPT_DIR/test_health.sh" new; then
    echo ">>> 新服务验收失败，自动回滚" >&2
    "$SCRIPT_DIR/rollback.sh"
    exit 1
  fi
  if ! "$SCRIPT_DIR/test_health.sh" e2e; then
    echo ">>> 端到端验收失败，自动回滚" >&2
    "$SCRIPT_DIR/rollback.sh"
    exit 1
  fi
  ok "真实部署完成：新服务已接管，旧单元保留 disabled（观察期 ≥2 周）"
}

# ---------- 主流程 ----------
main() {
  preflight
  merge_env
  render
  validate_rendered

  if [ "$MODE" = "dry-run" ]; then
    echo
    echo "============================================================"
    echo "dry-run 完成：仅渲染 + 校验，未做任何系统变更。"
    echo "真实切换将执行（需 Hermes 批准后手动运行 --real）："
    echo "  备份 -> 落盘 $RUN_DIR -> 安装 proxy-*.service -> 停旧启新 -> 验收 -> 失败自动回滚"
    echo "============================================================"
    return 0
  fi

  # 真实模式：确认保护
  echo
  echo ">>> 即将执行真实切换（停用旧单元并启用新单元），操作不可逆（可回滚）。"
  if [ "$CONFIRM" != "yes" ]; then
    read -r -p "输入 YES 继续: " answer
    [ "$answer" = "YES" ] || { echo "已取消。"; exit 1; }
  fi
  # 切换前置门禁：旧三服务必须健康
  "$SCRIPT_DIR/test_health.sh" old || { echo ">>> 旧服务健康检查未通过，中止切换" >&2; exit 1; }
  real_deploy
}

main "$@"
