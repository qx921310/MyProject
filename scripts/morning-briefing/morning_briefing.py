#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
早盘简报自动推送脚本（钻仔 / 小钻钻）
=========================================
- 数据源：腾讯行情接口（三大指数）+ 东方财富接口（行业板块涨幅榜），均无需鉴权
- 推送：飞书开放平台 API 发送到「大威天龙」群（凭据读自 openclaw.json，不回显）
- 零模型消耗：纯 Python 抓取 + 模板生成，不调用任何 LLM
- 去重：内容指纹与上次相同（如节假日）则跳过推送，避免重复刷屏
- 日志：logs/morning-briefing.log，带操作人字段（钻仔）+ 北京时间（UTC+8）

用法：
  python3 morning_briefing.py            # 正常推送
  python3 morning_briefing.py --dry-run  # 只生成文本并打印，不推送
"""

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "logs", "morning-briefing.log")
STATE_FILE = os.path.join(BASE_DIR, "logs", "morning-briefing.state.json")
OPENCLAW_JSON = "/home/node/.openclaw/openclaw.json"
CHAT_ID = "oc_88de0009625d420532c0e0bd075c285e"  # 大威天龙群
API_BASE = "https://open.feishu.cn/open-apis"
OPERATOR = "钻仔"  # KOKO 立规：日志必须带操作人字段

TZ_UTC8 = timezone(timedelta(hours=8))

INDEX_QQ = "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
SECTOR_EM = (
    "https://push2.eastmoney.com/api/qt/clist/get?"
    "pn=1&pz=8&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2"
    "&fields=f2,f3,f4,f12,f14"
)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) morning-briefing/1.0"


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    """日志：北京时间 + 操作人字段。"""
    ts = datetime.now(TZ_UTC8).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [操作人:{OPERATOR}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:  # 日志失败不影响主流程
        print(f"[log-write-failed] {e}", flush=True)


def http_get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def now_beijing() -> str:
    return datetime.now(TZ_UTC8).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# 数据抓取
# ---------------------------------------------------------------------------
def fetch_indices() -> list[dict]:
    """腾讯行情接口：三大指数实时/最新数据。返回列表，每项含名称/最新/昨收/涨跌额/涨跌幅/最高/最低。"""
    raw = http_get(INDEX_QQ).decode("gbk", errors="replace")
    result = []
    for line in raw.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        payload = line.split("=", 1)[1].strip().strip('"')
        fields = payload.split("~")
        if len(fields) < 40:
            continue
        name = fields[1]
        price = float(fields[3])
        prev_close = float(fields[4])
        change = float(fields[31])
        pct = float(fields[32])
        high = float(fields[33])
        low = float(fields[34])
        # 自校验：涨跌幅 ≈ (最新价-昨收)/昨收*100
        calc_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0.0
        if abs(calc_pct - pct) > 0.5:
            log(f"WARN 指数自校验偏差 {name}: 接口涨跌幅 {pct}% vs 计算 {calc_pct}%")
        result.append({
            "name": name, "price": price, "prev_close": prev_close,
            "change": change, "pct": pct, "high": high, "low": low,
        })
    if len(result) < 3:
        raise RuntimeError(f"指数解析异常，仅拿到 {len(result)} 条")
    return result


def fetch_sectors(top: int = 5) -> list[dict]:
    """东方财富行业板块涨幅榜。"""
    raw = http_get(SECTOR_EM).decode("utf-8", errors="replace")
    data = json.loads(raw)
    diff = (data.get("data") or {}).get("diff") or []
    result = []
    for item in diff:
        name = item.get("f14", "")
        pct = item.get("f3")
        if name and pct is not None:
            result.append({"name": name, "pct": float(pct)})
        if len(result) >= top:
            break
    return result


# ---------------------------------------------------------------------------
# 简报生成（纯模板，不调模型）
# ---------------------------------------------------------------------------
def build_briefing(indices: list[dict], sectors: list[dict]) -> str:
    lines = []
    lines.append("📊 早盘简报 · " + now_beijing() + "（北京时间）")
    lines.append("━━━━━━━━━━━━━━")

    # 指数部分
    for idx in indices:
        arrow = "🔺" if idx["pct"] > 0 else ("🔻" if idx["pct"] < 0 else "➖")
        lines.append(
            f"{idx['name']} {idx['price']:.2f}  {arrow}{idx['pct']:+.2f}%"
            f"  (高{idx['high']:.2f}/低{idx['low']:.2f})"
        )

    # 板块主线
    if sectors:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("🔥 领涨板块：")
        for i, s in enumerate(sectors, 1):
            lines.append(f"{i}. {s['name']} {s['pct']:+.2f}%")

    # 简短解读（规则模板）
    lines.append("━━━━━━━━━━━━━━")
    pcts = [i["pct"] for i in indices]
    up_count = sum(1 for p in pcts if p > 0)
    down_count = sum(1 for p in pcts if p < 0)
    if up_count == len(pcts):
        mood = "三大指数集体上涨，市场情绪偏暖"
    elif down_count == len(pcts):
        mood = "三大指数集体下跌，市场情绪偏弱"
    else:
        mood = "三大指数涨跌分化，结构性行情为主"
    main_line = f"资金主线集中在「{sectors[0]['name']}」方向" if sectors else "板块轮动较快"
    lines.append(f"💡 简评：{mood}；{main_line}。")
    lines.append("⚠️ 数据仅供参考，不构成投资建议。")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 飞书推送
# ---------------------------------------------------------------------------
def feishu_send(text: str) -> bool:
    """读取 openclaw.json 中的应用凭据 → tenant_access_token → 发群消息。"""
    with open(OPENCLAW_JSON, encoding="utf-8") as f:
        cfg = json.load(f)
    feishu_cfg = cfg.get("channels", {}).get("feishu", {})
    app_id = feishu_cfg.get("appId")
    app_secret = feishu_cfg.get("appSecret")
    if not app_id or not app_secret:
        raise RuntimeError("openclaw.json 缺少 feishu appId/appSecret")

    # 1. 换 token
    token_req = urllib.request.Request(
        f"{API_BASE}/auth/v3/tenant_access_token/internal",
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(token_req, timeout=15) as resp:
        token_data = json.loads(resp.read().decode("utf-8"))
    if token_data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {token_data.get('msg')}")
    token = token_data["tenant_access_token"]

    # 2. 发消息
    msg_req = urllib.request.Request(
        f"{API_BASE}/im/v1/messages?receive_id_type=chat_id",
        data=json.dumps({
            "receive_id": CHAT_ID,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(msg_req, timeout=15) as resp:
        send_data = json.loads(resp.read().decode("utf-8"))
    if send_data.get("code") != 0:
        raise RuntimeError(f"发送消息失败: {send_data.get('msg')}")
    return True


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="早盘简报自动推送")
    parser.add_argument("--dry-run", action="store_true", help="只生成文本打印，不推送")
    args = parser.parse_args()

    log("早盘简报任务开始" + ("（dry-run）" if args.dry_run else ""))
    try:
        indices = fetch_indices()
        sectors = fetch_sectors()
        briefing = build_briefing(indices, sectors)
    except Exception as e:
        log(f"ERROR 数据抓取/生成失败: {e}")
        return 1

    log("简报生成成功：\n" + briefing)

    # 去重：内容指纹与上次一致则跳过（覆盖节假日重复推送场景）
    fingerprint = hashlib.sha256(briefing.encode("utf-8")).hexdigest()
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            last = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        last = {}
    if last.get("fingerprint") == fingerprint and not args.dry_run:
        log("内容与上次推送一致（可能为非交易日），跳过推送")
        return 0

    if args.dry_run:
        log("dry-run 模式，不推送")
        return 0

    try:
        feishu_send(briefing)
    except Exception as e:
        log(f"ERROR 推送失败: {e}")
        return 1

    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"fingerprint": fingerprint, "time": now_beijing()}, f, ensure_ascii=False)
    except Exception as e:
        log(f"WARN 状态写入失败: {e}")

    log("早盘简报推送成功 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
