#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""早盘简报自动推送（纯 Python，无 LLM，零 token 消耗）。

抓取 A股指数 / 伦敦金 / 美股隔夜收盘，格式化为简报文本直接输出。
配合 cron no_agent 模式：stdout 即投递内容（飞书群）。

数据源（复用 market-data-briefing 技能已验证的源）：
  - A股指数：AKShare 新浪源（主用），腾讯行情（备用回退）
  - 伦敦金：TradingView scanner API（cfd）
  - 美股：Yahoo Finance chart API
时间统一北京时间（UTC+8）。
"""

import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

try:
    from zoneinfo import ZoneInfo
    BJ_TZ = ZoneInfo("Asia/Shanghai")
except ImportError:
    BJ_TZ = timezone(timedelta(hours=8))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TIMEOUT = 15


def bj_now():
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


FP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".morning_brief.fp")


def fingerprint_guard(text):
    """内容指纹去重：与上次指纹相同返回 False（调用方应静默退出，不推送）。

    指纹只对数据内容计算，排除每次必变的「时间」行，
    因此节假日/周末数据未变时自动跳过，不重复刷屏。
    """
    data_lines = [ln for ln in text.splitlines() if not ln.startswith("🕗")]
    fp = hashlib.sha256("\n".join(data_lines).encode("utf-8")).hexdigest()
    old = ""
    try:
        with open(FP_FILE, encoding="utf-8") as f:
            old = f.read().strip()
    except OSError:
        pass
    if fp == old:
        return False
    with open(FP_FILE, "w", encoding="utf-8") as f:
        f.write(fp)
    return True


def fmt_num(value, digits=2):
    return "-" if value is None else f"{value:.{digits}f}"


def fmt_chg(value):
    return "-" if value is None else f"{value:+.2f}%"


def fetch_ashare():
    """返回 [(label, price, chg_pct), ...]，新浪源优先、腾讯回退。"""
    try:
        return fetch_ashare_sina()
    except Exception as exc:
        sys.stderr.write(f"[提示] 新浪源失败: {exc}，回退腾讯\n")
        return fetch_ashare_tencent()


def fetch_ashare_sina():
    import contextlib
    import io

    import akshare as ak

    with contextlib.redirect_stderr(io.StringIO()):
        df = ak.stock_zh_index_spot_sina()
    targets = [("sh000001", "上证指数"), ("sz399006", "创业板指"), ("sh000688", "科创50")]
    rows = []
    for code, label in targets:
        matched = df[df["代码"] == code]
        if matched.empty:
            continue
        rec = matched.iloc[0]
        rows.append((label, float(rec["最新价"]), float(rec["涨跌幅"])))
    if not rows:
        raise ValueError("新浪源未找到目标指数")
    return rows


def fetch_ashare_tencent():
    url = "https://qt.gtimg.cn/q=sh000001,sz399006,sh000688"
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    text = resp.content.decode("gbk", errors="replace")
    rows = []
    for line in text.split(";"):
        line = line.strip()
        if "=" not in line:
            continue
        payload = line.split("=", 1)[1].strip().strip('"').strip("'")
        fields = payload.split("~")
        if len(fields) <= 32 or not fields[1]:
            continue
        try:
            rows.append((fields[1], float(fields[3]), float(fields[32])))
        except ValueError:
            continue
    if not rows:
        raise ValueError("腾讯源未解析出指数")
    return rows


def fetch_gold():
    """返回 (price, change_pct, high, low, rsi)。"""
    url = "https://scanner.tradingview.com/cfd/scan"
    headers = {
        "authority": "scanner.tradingview.com",
        "accept": "text/plain, */*; q=0.01",
        "content-type": "application/json; charset=UTF-8",
        "user-agent": USER_AGENT,
        "origin": "https://www.tradingview.com",
        "referer": "https://www.tradingview.com/",
        "accept-language": "en-US,en;q=0.9",
    }
    body = {
        "markets": ["cfd"],
        "symbols": {"query": {"types": []}, "tickers": ["OANDA:XAUUSD", "TVC:GOLD"]},
        "options": {"lang": "en"},
        "columns": ["name", "close", "open", "high", "low", "change", "change_abs",
                    "Recommend.All", "RSI"],
        "range": [0, 50],
    }
    resp = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)
    resp.raise_for_status()
    items = resp.json().get("data") or []
    if not items:
        raise ValueError("TradingView 返回空")
    row = next((it for it in items if it.get("s") == "OANDA:XAUUSD"), items[0])
    vals = row.get("d") or []

    def at(idx):
        try:
            v = vals[idx]
            return None if v is None else float(v)
        except (IndexError, TypeError, ValueError):
            return None

    return at(1), at(5), at(3), at(4), at(8)


def yahoo_quote(symbol):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{quote(symbol, safe='')}?interval=1d&range=5d")
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    result = payload["chart"]["result"][0]
    meta = result.get("meta", {})
    quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = [c for c in quote_data.get("close", []) if c is not None]
    close = meta.get("regularMarketPrice")
    prev_close = closes[-2] if len(closes) >= 2 else (meta.get("chartPreviousClose")
                                                      or meta.get("previousClose"))
    if close is None and closes:
        close = closes[-1]
    if prev_close is None and len(closes) >= 2:
        prev_close = closes[-2]
    if close is None or not prev_close:
        raise ValueError("缺少收盘价")
    return float(close), float(prev_close)


def fetch_us():
    """返回 [(label, close, chg_pct), ...]（隔夜美股收盘）。"""
    out = []
    for symbol, label in [("^IXIC", "纳斯达克"), ("^GSPC", "标普500"), ("^DJI", "道琼斯")]:
        try:
            close, prev = yahoo_quote(symbol)
            out.append((label, close, (close / prev - 1.0) * 100.0))
        except Exception as exc:
            sys.stderr.write(f"[提示] {label} 获取失败: {exc}\n")
    return out


def main():
    lines = []
    lines.append("📊 早盘简报")
    lines.append(f"🕗 时间: {bj_now()}（北京时间）")
    lines.append("")

    # A股（上一交易日收盘 / 今日盘前参考）
    lines.append("📈 A股指数")
    try:
        for label, price, chg in fetch_ashare():
            emoji = "📈" if chg >= 0 else "📉"
            lines.append(f"  {emoji} {label} {fmt_num(price)}（{fmt_chg(chg)}）")
    except Exception as exc:
        lines.append(f"  ⚠️ 获取失败: {exc}")
    lines.append("")

    # 伦敦金
    lines.append("🥇 伦敦金（XAUUSD）")
    try:
        price, chg, high, low, rsi = fetch_gold()
        lines.append(f"  💰 现价 {fmt_num(price)}（{fmt_chg(chg)}） 高 {fmt_num(high)} 低 {fmt_num(low)} RSI {fmt_num(rsi, 1)}")
    except Exception as exc:
        lines.append(f"  ⚠️ 获取失败: {exc}")
    lines.append("")

    # 美股隔夜
    lines.append("🇺🇸 美股隔夜收盘")
    us = fetch_us()
    if us:
        for label, close, chg in us:
            emoji = "📈" if chg >= 0 else "📉"
            lines.append(f"  {emoji} {label} {fmt_num(close)}（{fmt_chg(chg)}）")
    else:
        lines.append("  ⚠️ 获取失败")
    lines.append("")
    lines.append("（自动简报 · 无分析 · 数据仅供参考）")

    text = "\n".join(lines)
    if not fingerprint_guard(text):
        return  # 内容未变，静默跳过（no_agent 空输出=不推送）
    print(text)


if __name__ == "__main__":
    main()
