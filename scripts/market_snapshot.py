#!/usr/bin/env python3
"""Market snapshot: A-share indices, spot gold (伦敦金), and US indices.

Sources:
  - A-share indices: Tencent quote API (GBK encoded)
  - Spot gold: TradingView scanner API
  - US indices: Yahoo Finance chart API
"""

import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

try:
    from zoneinfo import ZoneInfo
    BJ_TZ = ZoneInfo("Asia/Shanghai")
except ImportError:  # Python < 3.9
    BJ_TZ = timezone(timedelta(hours=8))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TIMEOUT = 15


def fmt_num(value, digits=2):
    """Format a number, or '-' when the value is missing."""
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def fmt_chg(value):
    """Format a percentage change with a sign, or '-' when missing."""
    if value is None:
        return "-"
    return f"{value:+.2f}%"


def bj_now():
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d %H:%M:%S")


def format_tencent_ts(raw):
    """Tencent timestamps look like 20260810150003."""
    raw = (raw or "").strip()
    if len(raw) == 14 and raw.isdigit():
        try:
            return datetime.strptime(raw, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return raw


def fetch_ashare():
    """Fetch 上证指数 / 创业板指 / 科创50 from the Tencent quote API."""
    url = "https://qt.gtimg.cn/q=sh000001,sz399006,sh000688"
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    text = resp.content.decode("gbk", errors="replace")

    rows = 0
    for line in text.split(";"):
        line = line.strip()
        if "=" not in line:
            continue
        payload = line.split("=", 1)[1].strip().strip('"').strip("'")
        fields = payload.split("~")
        if len(fields) <= 32 or not fields[1]:
            continue
        try:
            price = float(fields[3])
            change_pct = float(fields[32])
        except ValueError:
            continue
        print(
            f"  {fields[1]}: {price:.2f}   {change_pct:+.2f}%   "
            f"时间: {format_tencent_ts(fields[30])}"
        )
        rows += 1

    if rows == 0:
        raise ValueError("未能解析出任何指数数据")


def fetch_gold():
    """Fetch spot gold (XAUUSD) from the TradingView scanner API."""
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
        "columns": [
            "name", "close", "open", "high", "low",
            "change", "change_abs", "Recommend.All", "RSI",
        ],
        "range": [0, 50],
    }

    resp = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("data") or []
    if not items:
        raise ValueError("扫描器返回空数据")

    # Prefer the OANDA spot feed, fall back to TVC:GOLD if absent.
    row = next((item for item in items if item.get("s") == "OANDA:XAUUSD"), items[0])
    values = row.get("d") or []

    def value_at(idx):
        try:
            v = values[idx]
            return None if v is None else float(v)
        except (IndexError, TypeError, ValueError):
            return None

    price, high, low = value_at(1), value_at(3), value_at(4)
    change, rsi = value_at(5), value_at(8)
    print(
        f"  现价: {fmt_num(price)}   涨跌幅: {fmt_chg(change)}   "
        f"最高: {fmt_num(high)}   最低: {fmt_num(low)}   RSI: {fmt_num(rsi, 1)}"
    )


def yahoo_quote(symbol):
    """Return (last_close, previous_close, timestamp) for a Yahoo chart symbol."""
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(symbol, safe='')}?interval=1d&range=5d"
    )
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    result = payload["chart"]["result"][0]
    meta = result.get("meta", {})
    quote_data = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = [c for c in quote_data.get("close", []) if c is not None]

    close = meta.get("regularMarketPrice")
    prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
    ts = meta.get("regularMarketTime")

    if close is None and closes:
        close = closes[-1]
    if prev_close is None and len(closes) >= 2:
        prev_close = closes[-2]
    if ts is None and result.get("timestamp"):
        ts = result["timestamp"][-1]

    if close is None or not prev_close:
        raise ValueError("缺少收盘价或前收盘价")
    return float(close), float(prev_close), ts


def fetch_yahoo():
    """Fetch Nasdaq, S&P 500 and Dow from Yahoo Finance."""
    indices = [
        ("^IXIC", "纳斯达克"),
        ("^GSPC", "标普500"),
        ("^DJI", "道琼斯"),
    ]
    for symbol, label in indices:
        try:
            close, prev_close, ts = yahoo_quote(symbol)
        except Exception as exc:
            print(f"  {label} ({symbol}): 获取失败 - {exc}")
            continue
        change_pct = (close / prev_close - 1.0) * 100.0
        close_time = (
            datetime.fromtimestamp(ts, BJ_TZ).strftime("%Y-%m-%d %H:%M")
            if ts else "-"
        )
        print(
            f"  {label}: {close:.2f}   {change_pct:+.2f}%   "
            f"收盘时间(北京时间): {close_time}"
        )


def main():
    print("================== 市场快照 ==================")
    print(f"抓取时间: {bj_now()} (北京时间)")
    print()

    sections = [
        ("A股指数", fetch_ashare),
        ("伦敦金 (XAUUSD)", fetch_gold),
        ("美股指数", fetch_yahoo),
    ]
    failed = 0
    for label, fn in sections:
        print(f"【{label}】")
        try:
            fn()
        except Exception as exc:
            failed += 1
            print(f"  ❌ 获取失败: {exc}")
        print()

    if failed == len(sections):
        sys.exit(1)


if __name__ == "__main__":
    main()
