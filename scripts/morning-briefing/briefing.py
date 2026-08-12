#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股简报自动推送脚本（钻仔 / 小钻钻）
=========================================
- 数据源：腾讯行情接口（三大指数）+ 东方财富接口（行业板块涨幅/跌幅榜、涨跌家数），均无需鉴权
- 推送：飞书开放平台 API 发送到「大威天龙」群（凭据读自 openclaw.json，不回显）
- 零模型消耗：纯 Python 抓取 + 模板生成，不调用任何 LLM
- 双模式：
    --mode morning  早盘简报，工作日 09:00 北京推送（指数 + 领涨板块）
    --mode close    收盘简报，工作日 15:20 北京推送（指数 + 领涨/领跌板块 + 涨跌家数）
- 去重：内容指纹与上次一致则跳过（如节假日），按模式分别记录
- 日志：logs/morning-briefing.log，带操作人字段（钻仔）+ 北京时间（UTC+8）

用法：
  python3 briefing.py --mode morning          # 早盘推送
  python3 briefing.py --mode close            # 收盘推送
  python3 briefing.py --mode morning --dry-run  # 只生成文本打印，不推送
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "logs", "briefing.log")
STATE_FILE = os.path.join(BASE_DIR, "logs", "briefing.state.json")
OPENCLAW_JSON = "/home/node/.openclaw/openclaw.json"
CHAT_ID = "oc_88de0009625d420532c0e0bd075c285e"  # 大威天龙群
API_BASE = "https://open.feishu.cn/open-apis"
OPERATOR = "钻仔"  # KOKO 立规：日志必须带操作人字段

TZ_UTC8 = timezone(timedelta(hours=8))

INDEX_QQ = "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
SECTOR_EM = (
    "https://push2.eastmoney.com/api/qt/clist/get?"
    "pn=1&pz=8&po={po}&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2"
    "&fields=f2,f3,f4,f12,f14"
)
BREADTH_EM = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get?"
    "fltt=2&invt=2&fields=f104,f105,f106&secids=1.000001"
)

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) briefing/1.0"


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
    """腾讯行情接口：三大指数数据。"""
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


def fetch_sectors(po: int = 1, top: int = 5) -> list[dict]:
    """东方财富行业板块榜单。po=1 涨幅榜，po=0 跌幅榜。"""
    raw = http_get(SECTOR_EM.format(po=po)).decode("utf-8", errors="replace")
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


def fetch_breadth() -> dict:
    """上证涨跌家数：f104 上涨 / f105 下跌 / f106 平盘。"""
    raw = http_get(BREADTH_EM).decode("utf-8", errors="replace")
    data = json.loads(raw)
    diff = (data.get("data") or {}).get("diff") or []
    if not diff:
        raise RuntimeError("涨跌家数解析异常")
    item = diff[0]
    return {
        "up": int(item.get("f104", 0)),
        "down": int(item.get("f105", 0)),
        "flat": int(item.get("f106", 0)),
    }


# ---------------------------------------------------------------------------
# 简报生成（纯模板，不调模型）
# ---------------------------------------------------------------------------
def _fmt_indices(indices: list[dict]) -> list[str]:
    lines = []
    for idx in indices:
        arrow = "🔺" if idx["pct"] > 0 else ("🔻" if idx["pct"] < 0 else "➖")
        lines.append(
            f"{idx['name']} {idx['price']:.2f}  {arrow}{idx['pct']:+.2f}%"
            f"  (高{idx['high']:.2f}/低{idx['low']:.2f})"
        )
    return lines


def _mood_line(pcts: list[float]) -> str:
    up_count = sum(1 for p in pcts if p > 0)
    down_count = sum(1 for p in pcts if p < 0)
    if up_count == len(pcts):
        return "三大指数集体上涨，市场情绪偏暖"
    if down_count == len(pcts):
        return "三大指数集体下跌，市场情绪偏弱"
    return "三大指数涨跌分化，结构性行情为主"


def build_morning(indices: list[dict], sectors: list[dict]) -> str:
    lines = ["📊 早盘简报 · " + now_beijing() + "（北京时间）", "━━━━━━━━━━━━━━"]
    lines += _fmt_indices(indices)
    if sectors:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("🔥 领涨板块：")
        for i, s in enumerate(sectors, 1):
            lines.append(f"{i}. {s['name']} {s['pct']:+.2f}%")
    lines.append("━━━━━━━━━━━━━━")
    lines.append(f"💡 简评：{_mood_line([i['pct'] for i in indices])}；"
                 f"资金主线集中在「{sectors[0]['name']}」方向。" if sectors else
                 f"💡 简评：{_mood_line([i['pct'] for i in indices])}。")
    lines.append("⚠️ 数据仅供参考，不构成投资建议。")
    return "\n".join(lines)


def build_close(indices: list[dict], up_sectors: list[dict], down_sectors: list[dict], breadth: dict) -> str:
    lines = ["📊 收盘简报 · " + now_beijing() + "（北京时间）", "━━━━━━━━━━━━━━"]
    lines += _fmt_indices(indices)
    lines.append("━━━━━━━━━━━━━━")
    lines.append(f"📈 涨跌家数：上涨 {breadth['up']} / 下跌 {breadth['down']} / 平盘 {breadth['flat']}")
    if up_sectors:
        lines.append("")
        lines.append("🔥 领涨板块：")
        for i, s in enumerate(up_sectors, 1):
            lines.append(f"{i}. {s['name']} {s['pct']:+.2f}%")
    if down_sectors:
        lines.append("")
        lines.append("🧊 领跌板块：")
        for i, s in enumerate(down_sectors, 1):
            lines.append(f"{i}. {s['name']} {s['pct']:+.2f}%")
    lines.append("━━━━━━━━━━━━━━")
    pcts = [i["pct"] for i in indices]
    main_line = f"资金主线集中在「{up_sectors[0]['name']}」方向" if up_sectors else "板块轮动较快"
    lines.append(f"💡 简评：{_mood_line(pcts)}；{main_line}。")
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
    parser = argparse.ArgumentParser(description="A股简报自动推送")
    parser.add_argument("--mode", choices=["morning", "close"], default="morning",
                        help="morning=早盘(9:00)，close=收盘(15:20)")
    parser.add_argument("--dry-run", action="store_true", help="只生成文本打印，不推送")
    args = parser.parse_args()

    log(f"A股简报任务开始 mode={args.mode}" + ("（dry-run）" if args.dry_run else ""))
    try:
        indices = fetch_indices()
        if args.mode == "morning":
            sectors = fetch_sectors(po=1)
            briefing = build_morning(indices, sectors)
        else:
            up_sectors = fetch_sectors(po=1)
            down_sectors = fetch_sectors(po=0)
            breadth = fetch_breadth()
            briefing = build_close(indices, up_sectors, down_sectors, breadth)
    except Exception as e:
        log(f"ERROR 数据抓取/生成失败: {e}")
        return 1

    log("简报生成成功：\n" + briefing)

    # 去重：按模式分别记录指纹
    fingerprint = hashlib.sha256(briefing.encode("utf-8")).hexdigest()
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    if state.get(args.mode) == fingerprint and not args.dry_run:
        log(f"内容与上次{args.mode}推送一致（可能为非交易日），跳过推送")
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
        state[args.mode] = fingerprint
        state[args.mode + "_time"] = now_beijing()
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception as e:
        log(f"WARN 状态写入失败: {e}")

    log(f"{args.mode} 简报推送成功 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
