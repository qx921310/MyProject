#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股简报自动推送脚本（钻仔 / 小钻钻）
=========================================
- 数据源（免费，零 token）：
    腾讯行情接口（三大指数，东财兜底）+ 东方财富接口（行业板块涨跌幅榜、涨跌家数、7x24 快讯）；
    东财 5xx 时自动切换腾讯行业板块/全 A 涨跌家数，快讯失败降级为空。
- 行情分析（轻量模型直连，每次约 0.3 美分，远低于完整 agent）：
    DeepSeek chat completions，key 读自 openclaw.json env.vars.DEEPSEEK_API_KEY（不回显）
- 推送：飞书开放平台 API 发送到「大威天龙」群（凭据读自 openclaw.json，不回显）
- 双模式：
    --mode morning  早盘简报，工作日 09:20 北京推送（指数 + 领涨板块 + 消息面 + 分析）
    --mode close    收盘简报，工作日 15:20 北京推送（指数 + 涨跌家数 + 领涨/领跌 + 消息面 + 分析）
- 降级：模型调用失败自动降级为纯数据+消息面模板，推送不中断；--no-llm 可强制纯模板
- 去重：内容指纹与上次一致则跳过（如节假日），按模式分别记录
- 日志：logs/briefing.log，带操作人字段（钻仔）+ 北京时间（UTC+8）

用法：
  python3 briefing.py --mode morning            # 抓数据+生成基础版+推送（旧行为，一般不直接用）
  python3 briefing.py --mode morning --save --no-push   # 只抓数据存本地数据源（不推送）
  python3 briefing.py --mode morning --dry-run # 只生成文本打印，不推送
  python3 briefing.py --push-pending morning   # 读待推送文件并推送（定时推送用）
  python3 briefing.py --mode morning --no-llm  # 强制纯模板（不调模型）

数据源输出（--save）：
  写入 logs/data/latest-{mode}.json（结构化数据：指数/板块/涨跌家数/新闻/基础简报）
  和 logs/data/latest-{mode}.md（基础版简报文本），供钻仔/金仔各自基于数据做分析测评。

待推送（--push-pending）：
  读 logs/pending/{mode}.txt（AI 分析版）推送到群里，成功后归档；
  若文件不存在则降级推送基础版，保证不漏推。
"""

import argparse
import fcntl
import hashlib
import json
import math
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "logs", "briefing.log")
STATE_FILE = os.path.join(BASE_DIR, "logs", "briefing.state.json")
DEGRADE_STATE_FILE = os.path.join(BASE_DIR, "logs", "degrade_state.json")
DATA_DIR = os.path.join(BASE_DIR, "logs", "data")
PENDING_DIR = os.path.join(BASE_DIR, "logs", "pending")
OPENCLAW_JSON_CANDIDATES = (
    "/home/ubuntu/.openclaw/openclaw.json",
    "/home/node/.openclaw/openclaw.json",
    os.path.expanduser("~/.openclaw/openclaw.json"),
)
CHAT_ID = "oc_88de0009625d420532c0e0bd075c285e"  # 大威天龙群
API_BASE = "https://open.feishu.cn/open-apis"
OPERATOR = "钻仔"  # KOKO 立规：日志必须带操作人字段

# 连续降级告警阈值：同一数据源降级按自然日累计，达到阈值后升级 ERROR 并写入简报。
DEGRADE_ALERT_THRESHOLD = 3
# 腾讯全 A 涨跌家数兜底的分页统计总耗时上限，防止慢接口拖垮简报任务。
BREADTH_QQ_MAX_SECONDS = 60
BREADTH_QQ_MAX_PAGES = 28
BREADTH_QQ_PAGE_TIMEOUT = 15

TZ_UTC8 = timezone(timedelta(hours=8))

INDEX_QQ = "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
US_STOCKS_QQ = "https://qt.gtimg.cn/q=usDJI,usIXIC,usINX"
GOLD_QQ = "https://qt.gtimg.cn/q=hf_GC,hf_XAU"
# 美股三大指数腾讯接口返回名称，顺序对应 usDJI / usIXIC / usINX。
US_EXPECTED_NAMES = ["道琼斯", "纳斯达克", "标普500"]
# 黄金行情腾讯接口可能出现的历史/别名名称，只接受已知名称，避免把日期/空字段误当名称。
GOLD_KNOWN_NAMES = {
    "COMEX纽约金", "纽约黄金", "COMEX黄金",
    "伦敦金现货", "伦敦金（现货黄金）",
}
# Yahoo Finance 美股指数期货代码表（顺序即简报输出顺序）。
US_FUTURES_YAHOO_SYMBOLS = {
    "NQ=F": "纳斯达克100期货",
    "ES=F": "标普500期货",
    "YM=F": "道指期货",
}
US_FUTURES_YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=5m&range=1d"
)
# 腾讯标普500指数期货兜底数据源。
US_FUTURES_QQ_ES = "https://qt.gtimg.cn/q=hf_ES"
# 腾讯 hf_ES 返回名称 → 简报白名单名称。
US_FUTURES_QQ_NAME_MAP = {"标普500指数期货": "标普500期货"}
# 美股期货名称白名单，只接受已知名称，避免误读日期/空字段。
US_FUTURES_KNOWN_NAMES = {"纳斯达克100期货", "标普500期货", "道指期货"}
INDEX_EM = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get?"
    "fltt=2&invt=2&fields=f2,f3,f4,f12,f14,f15,f16,f17,f18"
    "&secids=1.000001,0.399001,0.399006"
)
SECTOR_EM = (
    "https://push2.eastmoney.com/api/qt/clist/get?"
    "pn=1&pz=8&po={po}&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2"
    "&fields=f2,f3,f4,f12,f14"
)
SECTOR_QQ = (
    "https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank?"
    "board_type=hy&sort_type=priceRatio&direct={direct}&offset=0&count={count}"
)
BREADTH_EM = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get?"
    "fltt=2&invt=2&fields=f104,f105,f106&secids=1.000001"
)
BREADTH_QQ = (
    "https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList?"
    "_appver=11.17.0&board_code=aStock&sort_type=priceRatio&direct=down"
    "&offset={offset}&count={count}"
)
NEWS_EM = (
    "https://np-weblist.eastmoney.com/comm/web/getFastNewsList?"
    "client=web&biz=web_724&fastColumn=102&sortEnd=&pageSize=20&req_trace="
)

# 财经相关性关键词：快讯标题含任一关键词才保留（过滤娱乐/八卦快讯）
FIN_KEYWORDS = [
    "股", "证监会", "央行", "财政部", "发改委", "国务院", "政策", "市场",
    "指数", "板块", "美股", "港股", "关税", "美联储", "降息", "加息", "利率",
    "CPI", "PPI", "GDP", "半导体", "芯片", "AI", "人工智能", "新能源",
    "光伏", "锂电", "汽车", "房地产", "楼市", "消费", "医药", "黄金",
    "原油", "石油", "人民币", "汇率", "融资", "IPO", "并购", "重组",
    "基金", "保险", "银行", "证券", "期货", "债券", "统计", "工信",
    "机器人", "算力", "量子", "航天", "军工", "白酒", "煤炭", "钢铁",
    "有色", "化工", "腾讯", "阿里", "华为", "小米", "比亚迪", "宁德",
    "中芯", "英伟达", "特斯拉", "苹果", "央企", "国企", "北向", "南向",
    "涨停", "跌停", "数据", "数字",
]

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) briefing/2.0"


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


def _note_degradation(degradations: list[str] | None, reason: str) -> None:
    """把降级原因追加到本次运行收集器中，供连续降级状态更新使用。"""
    if degradations is not None:
        degradations.append(reason)


def _load_degrade_state() -> dict:
    try:
        with open(DEGRADE_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_degrade_state(state: dict) -> None:
    """原子写入降级状态：临时文件 + os.replace，避免并发/中断产生半截文件。"""
    tmp_path = None
    try:
        state_dir = os.path.dirname(DEGRADE_STATE_FILE)
        os.makedirs(state_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".degrade_state.", suffix=".tmp", dir=state_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, DEGRADE_STATE_FILE)
        tmp_path = None
    except Exception as e:
        log(f"WARN 连续降级状态写入失败: {e}")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _lock_degrade_state():
    """锁定降级状态文件，覆盖 load-modify-save 全流程，防止并发丢更新。"""
    fd = None
    try:
        os.makedirs(os.path.dirname(DEGRADE_STATE_FILE), exist_ok=True)
        fd = os.open(DEGRADE_STATE_FILE + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd
    except Exception as e:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        log(f"WARN 连续降级状态加锁失败，本次更新不做并发保护: {e}")
        return None


def _unlock_degrade_state(lock_fd) -> None:
    if lock_fd is None:
        return
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        try:
            os.close(lock_fd)
        except OSError:
            pass


def update_degradation_state(degradations: list[str]) -> int:
    """按自然日累计连续降级天数；同一自然日多次运行只计一次。返回当前连续天数。"""
    today = datetime.now(TZ_UTC8).strftime("%Y-%m-%d")
    lock_fd = _lock_degrade_state()
    try:
        state = _load_degrade_state()
        last_date = state.get("date")
        last_count = 0
        try:
            last_count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            last_count = 0

        if degradations:
            if last_date == today:
                consecutive = max(last_count, 1)
            else:
                consecutive = last_count + 1
            state["date"] = today
            state["count"] = consecutive
            state["last_reasons"] = list(dict.fromkeys(degradations))[:10]
        elif last_date == today:
            # 同一自然日先降级后成功：成功只记录 last_ok，不清零当天降级计数。
            state["last_ok"] = today
            consecutive = last_count
        else:
            state["date"] = today
            state["count"] = 0
            state["last_ok"] = today
            state["last_reasons"] = []
            consecutive = 0

        _save_degrade_state(state)
    finally:
        _unlock_degrade_state(lock_fd)
    return consecutive


def http_get(url: str, timeout: int = 15, retries: int = 2) -> bytes:
    """GET 请求，带短重试。5xx/429/408/网络错误最多重试 retries 次。"""
    host = urllib.parse.urlparse(url).hostname or ""
    headers = {"User-Agent": USER_AGENT}
    if "eastmoney" in host:
        headers["Referer"] = "https://quote.eastmoney.com"
    elif "gtimg" in host or "qq.com" in host:
        headers["Referer"] = "https://finance.qq.com"

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code not in (408, 429, 500, 502, 503, 504):
                raise
        except Exception as e:  # URLError / timeout / connection reset 等
            last_error = e
        if attempt < retries:
            time.sleep(0.3 * (2 ** attempt))
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"GET 失败: {url}")


def find_openclaw_json() -> str:
    """动态探测 openclaw.json：优先新主机路径，兼容旧容器路径。"""
    seen = set()
    for path in OPENCLAW_JSON_CANDIDATES:
        if path in seen:
            continue
        seen.add(path)
        if os.path.isfile(path):
            log(f"openclaw.json 使用: {path}")
            return path
    attempted = "、".join(OPENCLAW_JSON_CANDIDATES)
    raise FileNotFoundError(f"未找到 openclaw.json，已尝试: {attempted}")


def http_post_json(url: str, payload: dict, headers: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def now_beijing() -> str:
    return datetime.now(TZ_UTC8).strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# 数据抓取
# ---------------------------------------------------------------------------
def _parse_qq_indices(raw: str) -> list[dict]:
    """解析腾讯 qt.gtimg.cn 指数行情。"""
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
        raise RuntimeError(f"腾讯指数解析异常，仅拿到 {len(result)} 条")
    return result


def _parse_em_indices(raw: str) -> list[dict]:
    """解析东财 ulist.np 指数行情。"""
    data = json.loads(raw)
    diff = (data.get("data") or {}).get("diff") or []
    result = []
    for item in diff:
        try:
            price = float(item["f2"])
            prev_close = float(item["f18"])
            pct = float(item["f3"])
            change = float(item["f4"])
            high = float(item["f15"])
            low = float(item["f16"])
        except (KeyError, TypeError, ValueError) as e:
            raise RuntimeError(f"东财指数字段解析异常: {e}")
        result.append({
            "name": item.get("f14", ""),
            "price": price,
            "prev_close": prev_close,
            "change": change,
            "pct": pct,
            "high": high,
            "low": low,
        })
    if len(result) < 3:
        raise RuntimeError(f"东财指数解析异常，仅拿到 {len(result)} 条")
    return result


def fetch_indices() -> list[dict]:
    """三大指数：腾讯优先，东财兜底。"""
    try:
        return _parse_qq_indices(http_get(INDEX_QQ).decode("gbk", errors="replace"))
    except Exception as e:
        log(f"WARN 腾讯指数抓取失败，切换东财指数兜底: {e}")
    try:
        return _parse_em_indices(http_get(INDEX_EM).decode("utf-8", errors="replace"))
    except Exception as e:
        log(f"ERROR 东财指数兜底也失败: {e}")
        raise


def _parse_qq_compact_quotes(raw: str, label: str) -> list[dict]:
    """解析腾讯 US/黄金行情：兼容美股的 ~ 分隔与黄金的 , 分隔。"""
    result = []
    for line in raw.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        payload = line.split("=", 1)[1].strip().strip('"')
        if not payload:
            continue
        sep = "~" if "~" in payload else ","
        fields = [f.strip() for f in payload.split(sep)]
        if sep == "~":
            # 美股：名称=1，最新价=3，时间=30，涨跌幅=32
            if len(fields) < 33 or not fields[1]:
                continue
            try:
                price = float(fields[3])
                pct = float(fields[32])
            except (TypeError, ValueError):
                continue
            result.append({
                "name": fields[1], "price": price, "pct": pct, "time": fields[30],
            })
        else:
            # 黄金：最新价=0，涨跌幅=1，日内高=4，日内低=5，时间=6，名称=最后一个
            if len(fields) < 7 or not fields[0] or not fields[1]:
                continue
            name = next((f for f in reversed(fields) if f in GOLD_KNOWN_NAMES), "")
            if not name:
                continue
            try:
                price = float(fields[0])
                pct = float(fields[1])
                high = float(fields[4]) if fields[4] else None
                low = float(fields[5]) if fields[5] else None
            except (TypeError, ValueError):
                continue
            result.append({
                "name": name, "price": price, "pct": pct, "time": fields[6],
                "high": high, "low": low,
            })
    if not result:
        raise RuntimeError(f"腾讯{label}行情解析为空")
    return result


def fetch_us_stocks(degradations: list[str] | None = None) -> list[dict] | None:
    """美股三大指数：道指/纳指/标普。失败返回 None，走降级标注。"""
    try:
        result = _parse_qq_compact_quotes(
            http_get(US_STOCKS_QQ).decode("gbk", errors="replace"), "美股"
        )
        if len(result) < 3:
            raise RuntimeError(f"美股三大指数仅解析到 {len(result)} 条")
        names = [item["name"] for item in result]
        if names != US_EXPECTED_NAMES:
            raise RuntimeError(f"美股名称自校验失败: {names}")
        return result
    except Exception as e:
        _note_degradation(degradations, "美股三大指数失败，字段降级为不可用")
        log(f"WARN 美股三大指数抓取失败，字段降级为不可用: {e}")
        return None


def fetch_gold(degradations: list[str] | None = None) -> list[dict] | None:
    """黄金行情：COMEX 纽约金 / 伦敦金现货。失败返回 None，走降级标注。"""
    try:
        result = _parse_qq_compact_quotes(
            http_get(GOLD_QQ).decode("gbk", errors="replace"), "黄金"
        )
        if len(result) < 2:
            raise RuntimeError(f"黄金行情仅解析到 {len(result)} 条")
        if len({item["name"] for item in result}) != 2:
            raise RuntimeError("黄金行情名称互异校验失败")
        return result
    except Exception as e:
        _note_degradation(degradations, "黄金行情失败，字段降级为不可用")
        log(f"WARN 黄金行情抓取失败，字段降级为不可用: {e}")
        return None


def _fetch_yahoo_us_future(symbol: str, name: str) -> dict:
    """抓取并自校验单条 Yahoo 美股指数期货。"""
    raw = http_get(
        US_FUTURES_YAHOO_CHART_URL.format(symbol=symbol), timeout=8, retries=0
    ).decode("utf-8", errors="replace")
    data = json.loads(raw)
    results = ((data.get("chart") or {}).get("result")) or []
    if not results:
        raise RuntimeError("Yahoo chart.result 为空")
    meta = results[0].get("meta") or {}
    meta_symbol = str(meta.get("symbol") or "").strip().upper()
    if meta_symbol != symbol.upper():
        raise RuntimeError(f"Yahoo {symbol} 返回合约不匹配: {meta_symbol}")
    try:
        price = float(meta["regularMarketPrice"])
        prev_close = float(meta.get("chartPreviousClose") or meta.get("previousClose"))
        market_time = meta.get("regularMarketTime")
    except (KeyError, TypeError, ValueError) as e:
        raise RuntimeError(f"Yahoo {symbol} meta 解析异常: {e}")
    if (
        not math.isfinite(price)
        or not math.isfinite(prev_close)
        or price <= 0
        or prev_close <= 0
    ):
        raise RuntimeError(f"Yahoo {symbol} 价格自校验失败: price={price}, prev_close={prev_close}")
    pct = round((price - prev_close) / prev_close * 100, 2)
    if abs(pct) > 15:
        raise RuntimeError(f"Yahoo {symbol} 涨跌幅自校验失败: {pct}%")
    if not market_time:
        raise RuntimeError(f"Yahoo {symbol} 缺少 regularMarketTime")
    try:
        market_ts = float(market_time)
    except (TypeError, ValueError) as e:
        raise RuntimeError(f"Yahoo {symbol} regularMarketTime 解析异常: {e}")
    if not (946684800 <= market_ts <= 1893456000):
        raise RuntimeError(f"Yahoo {symbol} regularMarketTime 超出合理范围: {market_ts}")
    try:
        time_str = datetime.fromtimestamp(market_ts, TZ_UTC8).strftime(
            "%Y-%m-%d %H:%M"
        )
    except (TypeError, ValueError, OSError, OverflowError) as e:
        raise RuntimeError(f"Yahoo {symbol} 时间解析异常: {e}")
    if name not in US_FUTURES_KNOWN_NAMES:
        raise RuntimeError(f"Yahoo {symbol} 名称不在白名单: {name}")
    return {"name": name, "price": price, "pct": pct, "time": time_str}


def _fetch_qq_us_future_es() -> dict:
    """腾讯 hf_ES 兜底：标普500指数期货。"""
    raw = http_get(US_FUTURES_QQ_ES).decode("gbk", errors="replace")
    for line in raw.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        payload = line.split("=", 1)[1].strip().strip('"')
        if not payload:
            continue
        fields = [f.strip() for f in payload.split(",")]
        if len(fields) < 8:
            continue
        name = US_FUTURES_QQ_NAME_MAP.get(fields[-1], "")
        if name not in US_FUTURES_KNOWN_NAMES:
            continue
        try:
            price = float(fields[0])
            pct = float(fields[1])
        except (TypeError, ValueError):
            continue
        time_raw = fields[6].strip()
        if (
            not math.isfinite(price)
            or not math.isfinite(pct)
            or price <= 0
            or abs(pct) > 15
            or not time_raw
        ):
            continue
        today = datetime.now(TZ_UTC8).strftime("%Y-%m-%d")
        clock = time_raw.split(" ")[-1]
        clock_parts = clock.split(":")
        if len(clock_parts) < 2:
            continue
        time_str = f"{today} {clock_parts[0].zfill(2)}:{clock_parts[1].zfill(2)}"
        return {"name": name, "price": price, "pct": pct, "time": time_str}
    raise RuntimeError("腾讯 hf_ES 行情解析为空")


def fetch_us_futures(degradations: list[str] | None = None) -> list[dict] | None:
    """美股指数期货：Yahoo 三条优先，成功 >=2 条即返回；否则回退腾讯 hf_ES。"""
    yahoo_results = []
    yahoo_errors = []
    for symbol, name in US_FUTURES_YAHOO_SYMBOLS.items():
        try:
            yahoo_results.append(_fetch_yahoo_us_future(symbol, name))
        except Exception as e:
            yahoo_errors.append(f"{symbol}: {e}")
    if len(yahoo_results) >= 2:
        return yahoo_results

    if yahoo_results:
        log(f"WARN Yahoo 美股期货仅成功 {len(yahoo_results)} 条，回退腾讯 hf_ES")
    else:
        detail = "；".join(yahoo_errors) if yahoo_errors else "无返回"
        log(f"WARN Yahoo 美股期货全部失败，回退腾讯 hf_ES: {detail}")

    try:
        item = _fetch_qq_us_future_es()
        if item["name"] not in US_FUTURES_KNOWN_NAMES:
            raise RuntimeError(f"腾讯 hf_ES 名称不在白名单: {item['name']}")
        _note_degradation(degradations, "美股期货 Yahoo 主源失败，已降级腾讯")
        return [item]
    except Exception as e:
        _note_degradation(degradations, "美股期货失败，字段降级为不可用")
        log(f"WARN 美股期货抓取失败，字段降级为不可用: {e}")
        return None


def fetch_sectors_em(po: int = 1, top: int = 5) -> list[dict]:
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
    if not result:
        raise RuntimeError("东财板块榜单为空")
    return result


def fetch_sectors_qq(direction: str, top: int = 5) -> list[dict]:
    """腾讯行业板块榜单。direction: up=领涨，down=领跌。"""
    direct = "down" if direction == "up" else "up"
    raw = http_get(SECTOR_QQ.format(direct=direct, count=top)).decode("utf-8", errors="replace")
    data = json.loads(raw)
    if data.get("code") != 0:
        raise RuntimeError(f"腾讯板块接口错误: code={data.get('code')} msg={data.get('msg')}")
    rank_list = (data.get("data") or {}).get("rank_list") or []
    result = []
    for item in rank_list:
        name = item.get("name", "")
        pct = item.get("zdf")
        if name and pct is not None:
            try:
                result.append({"name": name, "pct": float(pct)})
            except (TypeError, ValueError):
                continue
        if len(result) >= top:
            break
    if not result:
        raise RuntimeError("腾讯板块榜单为空")
    return result


def fetch_sectors_with_source(po: int = 1, top: int = 5,
                              degradations: list[str] | None = None) -> tuple[list[dict], str]:
    """行业板块榜单：东财优先，失败自动切换腾讯；双失败返回空列表。"""
    direction = "up" if po == 1 else "down"
    try:
        return fetch_sectors_em(po, top), "东方财富"
    except Exception as e:
        _note_degradation(degradations, f"东财{'领涨' if po == 1 else '领跌'}板块失败，已降级腾讯")
        log(f"WARN 东财{'领涨' if po == 1 else '领跌'}板块抓取失败，切换腾讯板块兜底: {e}")
    try:
        return fetch_sectors_qq(direction, top), "腾讯"
    except Exception as e:
        _note_degradation(degradations, f"腾讯{direction}板块失败，{'领涨' if po == 1 else '领跌'}板块置空")
        log(f"WARN 腾讯{direction}板块抓取也失败，该板块字段将置空: {e}")
        return [], "不可用"


def fetch_breadth_em() -> dict:
    """东财上证涨跌家数：f104 上涨 / f105 下跌 / f106 平盘。"""
    raw = http_get(BREADTH_EM).decode("utf-8", errors="replace")
    data = json.loads(raw)
    diff = (data.get("data") or {}).get("diff") or []
    if not diff:
        raise RuntimeError("东财涨跌家数解析异常")
    item = diff[0]
    return {
        "up": int(item.get("f104", 0)),
        "down": int(item.get("f105", 0)),
        "flat": int(item.get("f106", 0)),
    }


def fetch_breadth_qq() -> dict:
    """腾讯全 A 涨跌家数兜底：按股票涨跌幅排行分页统计。"""
    up = down = flat = 0
    offset = 0
    pages = 0
    deadline = time.monotonic() + BREADTH_QQ_MAX_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"腾讯涨跌家数统计超过 {BREADTH_QQ_MAX_SECONDS}s 上限")
        url = BREADTH_QQ.format(offset=offset, count=200)
        raw = http_get(url, timeout=min(remaining, BREADTH_QQ_PAGE_TIMEOUT), retries=1).decode("utf-8", errors="replace")
        if time.monotonic() > deadline:
            raise RuntimeError(f"腾讯涨跌家数统计超过 {BREADTH_QQ_MAX_SECONDS}s 上限")
        data = json.loads(raw)
        if data.get("code") != 0:
            raise RuntimeError(f"腾讯涨跌家数接口错误: code={data.get('code')} msg={data.get('msg')}")
        body = data.get("data") or {}
        items = body.get("rank_list") or []
        if not items:
            break
        for item in items:
            if item.get("state") == "S":
                continue
            zdf_raw = item.get("zdf")
            if zdf_raw is None or zdf_raw == "":
                continue
            try:
                zdf = float(zdf_raw)
            except (TypeError, ValueError):
                continue
            if zdf > 0:
                up += 1
            elif zdf < 0:
                down += 1
            else:
                flat += 1
        pages += 1
        offset += len(items)
        total = int(body.get("total") or 0)
        natural_done = len(items) < 200 or (total and offset >= total)
        if natural_done or pages >= BREADTH_QQ_MAX_PAGES:
            if not natural_done:
                log(f"WARN 腾讯涨跌家数统计达到 BREADTH_QQ_MAX_PAGES={BREADTH_QQ_MAX_PAGES} 上限，结果已截断，可能偏少")
            break
    if pages == 0:
        raise RuntimeError("腾讯涨跌家数统计无数据")
    return {"up": up, "down": down, "flat": flat}


def fetch_breadth_with_source(degradations: list[str] | None = None) -> tuple[dict, str]:
    """涨跌家数：东财优先，失败自动切换腾讯全 A；双失败返回占位数据。"""
    try:
        return fetch_breadth_em(), "东方财富"
    except Exception as e:
        _note_degradation(degradations, "东财涨跌家数失败，已降级腾讯全A")
        log(f"WARN 东财涨跌家数抓取失败，切换腾讯全 A 口径兜底: {e}")
    try:
        return fetch_breadth_qq(), "腾讯全A"
    except Exception as e:
        _note_degradation(degradations, "腾讯全A涨跌家数失败，字段置空")
        log(f"WARN 腾讯涨跌家数抓取也失败，该字段将置空: {e}")
        return {"up": 0, "down": 0, "flat": 0}, "不可用"


def fetch_news(top: int = 8, degradations: list[str] | None = None) -> list[dict]:
    """东财 7x24 快讯，按财经相关性关键词过滤；失败降级为空消息面。"""
    try:
        raw = http_get(NEWS_EM).decode("utf-8", errors="replace")
        data = json.loads(raw)
        items = (data.get("data") or {}).get("fastNewsList") or []
        result = []
        for it in items:
            title = (it.get("title") or "").strip()
            if not title:
                title = (it.get("summary") or "").strip()
            if not title:
                continue
            if not any(kw in title for kw in FIN_KEYWORDS):
                continue
            show_time = (it.get("showTime") or "")[5:16]  # MM-DD HH:MM
            result.append({"time": show_time, "title": title})
            if len(result) >= top:
                break
        if not result:
            _note_degradation(degradations, "东财快讯为空，消息面置空")
            log("WARN 东财快讯为空，消息面将置空")
        return result
    except Exception as e:
        _note_degradation(degradations, "东财快讯失败，消息面置空")
        log(f"WARN 东财快讯抓取失败，消息面降级为空: {e}")
        return []


# ---------------------------------------------------------------------------
# 行情分析（DeepSeek 直连，轻量）
# ---------------------------------------------------------------------------
def _load_deepseek_config() -> tuple[str, str, str]:
    """返回 (api_key, base_url, model_id)，均从 openclaw.json 读取。"""
    with open(find_openclaw_json(), encoding="utf-8") as f:
        cfg = json.load(f)
    api_key = (cfg.get("env", {}).get("vars", {}) or {}).get("DEEPSEEK_API_KEY", "")
    models_cfg = cfg.get("models", {}) or {}
    providers = models_cfg.get("providers", {}) or {}
    ds = providers.get("deepseek", {}) or {}
    base_url = ds.get("baseUrl", "https://api.deepseek.com") or "https://api.deepseek.com"
    model_id = "deepseek-v4-flash"
    for m in ds.get("models", []) or []:
        if m.get("id"):
            model_id = m["id"]
            break
    return api_key, base_url, model_id


def llm_analyze(mode: str, indices: list[dict], up_sectors: list[dict],
                down_sectors: list[dict], breadth: dict, news: list[dict],
                us_stocks: list[dict] | None = None,
                gold: list[dict] | None = None,
                up_source: str = "东方财富", down_source: str = "东方财富",
                breadth_source: str = "东方财富") -> str:
    """调用 DeepSeek 生成行情分析。早盘与收盘均为五段结构。"""
    api_key, base_url, model_id = _load_deepseek_config()
    if not api_key:
        raise RuntimeError("未找到 DEEPSEEK_API_KEY")

    idx_lines = "；".join(
        f"{i['name']} {i['price']:.2f} ({i['pct']:+.2f}%)" for i in indices
    )
    up_lines = "、".join(f"{s['name']}{s['pct']:+.2f}%" for s in up_sectors[:5])
    down_lines = "、".join(f"{s['name']}{s['pct']:+.2f}%" for s in down_sectors[:5])
    news_lines = "\n".join(f"{n['time']} {n['title']}" for n in news[:8]) or "无"

    if up_source == "不可用" or not up_sectors:
        up_block = "【领涨板块】数据不可用（东财与腾讯均失败），请勿引用该字段。"
    else:
        up_block = f"【领涨板块（{up_source}口径）】{up_lines or '无'}"
    if down_source == "不可用" or not down_sectors:
        if mode == "morning":
            down_block = "【领跌板块】数据不可用（早盘模式不取该字段），请勿引用该字段。"
        else:
            down_block = "【领跌板块】数据不可用（东财与腾讯均失败），请勿引用该字段。"
    else:
        down_block = f"【领跌板块（{down_source}口径）】{down_lines or '无'}"

    if mode == "close":
        if breadth_source == "不可用":
            breadth_block = "【涨跌家数】数据不可用（东财与腾讯全A均失败），请勿引用该字段。"
        else:
            breadth_label = "东方财富上证口径" if breadth_source == "东方财富" else f"{breadth_source}口径"
            breadth_block = (
                f"【涨跌家数（{breadth_label}）】"
                f"上涨{breadth['up']}/下跌{breadth['down']}/平盘{breadth['flat']}"
            )
    else:
        breadth_block = None

    if us_stocks:
        us_lines = "；".join(
            f"{i['name']} {i['price']:.2f} ({i['pct']:+.2f}%) {i['time']}（美东时间）"
            for i in us_stocks
        )
        us_block = f"【美盘收盘】{us_lines}"
    else:
        us_block = "【美盘收盘】数据不可用（勿引用）。"

    if gold:
        gold_lines = "；".join(
            f"{g['name']} {g['price']:.2f} ({g['pct']:+.2f}%) {g['time']}（北京时间）"
            for g in gold
        )
        gold_block = f"【黄金实时】{gold_lines}"
    else:
        gold_block = "【黄金实时】数据不可用（勿引用）。"

    if mode == "morning":
        data_blocks = [
            f"【指数】{idx_lines}",
            us_block,
            gold_block,
            up_block,
            f"【财经快讯】\n{news_lines}",
        ]
        data_lines = "\n".join(data_blocks)
        system_content = "你是一位严谨、简洁的市场分析师。"
        max_tokens = 900
        prompt = (
            "你是严谨的市场分析师。基于以下【真实数据】写早盘市场分析，中文，"
            "只输出分析正文，严格按五段结构输出：\n"
            "①美盘收盘：归纳美股三大指数收盘表现；仅当快讯明确说明美盘涨跌原因时才引用归因，否则只陈述涨跌，不推测归因；\n"
            "②黄金实时：说明COMEX黄金/伦敦金现价与涨跌；\n"
            "③A股消息面：提炼财经快讯中的关键要点；\n"
            "④行情分析：判断A股板块主线，并分析外盘传导影响；传导判断必须基于提供的数据或快讯中的明确信号，不得臆测；\n"
            "⑤行情展望：仅列出今日关注变量，不编造具体点位。\n"
            "严禁编造数据，只使用提供的信息；若某字段标注数据不可用，则不要引用该字段；"
            "不给买卖建议。\n\n"
            f"{data_lines}"
        )
    else:
        idx_lines_close = "；".join(
            f"{i['name']} {i['price']:.2f} ({i['pct']:+.2f}%) "
            f"高{i['high']:.2f}/低{i['low']:.2f}" for i in indices
        )
        if gold:
            gold_lines_close = "；".join(
                f"{g['name']} {g['price']:.2f} ({g['pct']:+.2f}%)"
                + (
                    f" 日内高 {g['high']:.2f}/低 {g['low']:.2f}"
                    if g.get("high") is not None and g.get("low") is not None else ""
                )
                + f" {g['time']}（北京时间）"
                for g in gold
            )
            gold_block_close = f"【黄金实时】{gold_lines_close}"
        else:
            gold_block_close = "【黄金实时】数据不可用（勿引用）。"
        data_blocks = [
            f"【指数】{idx_lines_close}",
            breadth_block,
            up_block,
            down_block,
            gold_block_close,
            f"【财经快讯】\n{news_lines}",
        ]
        data_lines = "\n".join(data_blocks)
        system_content = "你是一位严谨的市场分析师。"
        max_tokens = 800
        prompt = (
            "你是严谨的市场分析师。基于以下【真实数据】写A股收盘市场分析，中文，"
            "只输出分析正文，严格按五段结构输出：\n"
            "①A股收盘概况：三大指数收盘与涨跌家数，一句话强弱判断；\n"
            "②科技板块重点：从领涨/领跌板块中识别科技方向（电子、通信、半导体、计算机、AI算力、消费电子等）并说明主线逻辑，无科技板块则说明当日风格；\n"
            "③黄金实时：伦敦金/现货黄金现价、涨跌、日内高低（KOKO 中仓黄金，单独成段）；\n"
            "④消息面：当日快讯要点；\n"
            "⑤行情分析：结合消息面与技术面（指数当日高低点、振幅、板块强弱）分析后市，传导判断必须基于提供的数据或快讯中的明确信号，不得臆测。\n"
            "严禁编造数据，只使用提供的信息；若某字段标注数据不可用，则不要引用该字段；"
            "不给买卖建议。\n\n"
            f"{data_lines}"
        )

    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    try:
        data = http_post_json(
            f"{base_url.rstrip('/')}/chat/completions",
            payload,
            {"Authorization": f"Bearer {api_key}"},
        )
    except urllib.error.HTTPError as e:
        # 模型 id 不可用时回退 deepseek-chat
        if e.code in (400, 404):
            payload["model"] = "deepseek-chat"
            data = http_post_json(
                f"{base_url.rstrip('/')}/chat/completions",
                payload,
                {"Authorization": f"Bearer {api_key}"},
            )
        else:
            raise
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    return content.strip() or "（模型未返回分析）"

# ---------------------------------------------------------------------------
# 简报生成
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


def _news_block(news: list[dict]) -> list[str]:
    if not news:
        return []
    lines = ["📰 消息面："]
    for n in news[:5]:
        lines.append(f"· {n['time']} {n['title']}")
    return lines


def build_morning(indices: list[dict], sectors: list[dict],
                  news: list[dict], analysis: str | None,
                  us_stocks: list[dict] | None = None,
                  gold: list[dict] | None = None,
                  sector_source: str = "东方财富",
                  alert: str | None = None) -> str:
    lines = ["📊 早盘简报 · " + now_beijing() + "（北京时间）", "━━━━━━━━━━━━━━"]
    lines += _fmt_indices(indices)

    lines.append("━━━━━━━━━━━━━━")
    if us_stocks:
        lines.append("【美盘收盘】")
        for i, item in enumerate(us_stocks, 1):
            arrow = "🔺" if item["pct"] > 0 else ("🔻" if item["pct"] < 0 else "➖")
            time_part = f"  {item['time']}（美东时间）" if item.get("time") else ""
            lines.append(
                f"{i}. {item['name']} {item['price']:.2f}  "
                f"{arrow}{item['pct']:+.2f}%{time_part}"
            )
    else:
        lines.append("【美盘收盘】数据不可用")

    lines.append("━━━━━━━━━━━━━━")
    if gold:
        lines.append("【黄金实时】")
        for i, item in enumerate(gold, 1):
            arrow = "🔺" if item["pct"] > 0 else ("🔻" if item["pct"] < 0 else "➖")
            time_part = f"  {item['time']}（北京时间）" if item.get("time") else ""
            lines.append(f"{i}. {item['name']} {item['price']:.2f}  {arrow}{item['pct']:+.2f}%{time_part}")
    else:
        lines.append("【黄金实时】数据不可用")

    if sectors:
        lines.append("━━━━━━━━━━━━━━")
        label = "🔥 领涨板块：" if sector_source == "东方财富" else f"🔥 领涨板块（{sector_source}口径）："
        lines.append(label)
        for i, s in enumerate(sectors, 1):
            lines.append(f"{i}. {s['name']} {s['pct']:+.2f}%")
    else:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("🔥 领涨板块：数据源暂不可用")
    news_lines = _news_block(news)
    if news_lines:
        lines.append("━━━━━━━━━━━━━━")
        lines += news_lines
    lines.append("━━━━━━━━━━━━━━")
    if analysis:
        lines.append(f"💬 行情分析：{analysis}")
    else:
        lines.append(f"💡 简评：{_mood_line([i['pct'] for i in indices])}；"
                     f"资金主线集中在「{sectors[0]['name']}」方向。" if sectors else
                     f"💡 简评：{_mood_line([i['pct'] for i in indices])}。")
    if alert:
        lines.append(alert)
    lines.append("⚠️ 数据仅供参考，不构成投资建议。")
    return "\n".join(lines)

def save_data_source(mode: str, indices: list[dict], up_sectors: list[dict],
                     down_sectors: list[dict], breadth: dict, news: list[dict],
                     briefing: str, sources: dict | None = None,
                     us_stocks: list[dict] | None = None,
                     gold: list[dict] | None = None,
                     us_futures: list[dict] | None = None) -> str:
    """把数据源基础版写入本地 logs/data/，返回主文件路径。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "mode": mode,
        "time": now_beijing(),
        "indices": indices,
        "up_sectors": up_sectors,
        "down_sectors": down_sectors,
        "breadth": breadth,
        "news": news,
        "briefing": briefing,
        "sources": sources or {},
    }
    if mode in ("morning", "close"):
        payload["us_stocks"] = us_stocks
        payload["gold"] = gold
    if mode == "close":
        payload["us_futures"] = us_futures
    json_path = os.path.join(DATA_DIR, f"latest-{mode}.json")
    md_path = os.path.join(DATA_DIR, f"latest-{mode}.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(briefing + "\n")
    log(f"数据源已写入: {json_path}")
    return json_path

def build_close(indices: list[dict], up_sectors: list[dict], down_sectors: list[dict],
                breadth: dict, news: list[dict], analysis: str | None,
                us_stocks: list[dict] | None = None,
                gold: list[dict] | None = None,
                us_futures: list[dict] | None = None,
                up_source: str = "东方财富", down_source: str = "东方财富",
                breadth_source: str = "东方财富",
                alert: str | None = None) -> str:
    lines = ["📊 收盘简报 · " + now_beijing() + "（北京时间）", "━━━━━━━━━━━━━━"]
    lines += _fmt_indices(indices)
    lines.append("━━━━━━━━━━━━━━")
    if breadth_source == "不可用":
        lines.append("📈 涨跌家数：数据源暂不可用")
    elif breadth_source == "东方财富":
        lines.append(f"📈 涨跌家数：上涨 {breadth['up']} / 下跌 {breadth['down']} / 平盘 {breadth['flat']}")
    else:
        lines.append(f"📈 涨跌家数（{breadth_source}口径）：上涨 {breadth['up']} / 下跌 {breadth['down']} / 平盘 {breadth['flat']}")
    lines.append("━━━━━━━━━━━━━━")
    if gold:
        lines.append("【黄金实时】")
        for i, item in enumerate(gold, 1):
            arrow = "🔺" if item["pct"] > 0 else ("🔻" if item["pct"] < 0 else "➖")
            time_part = f"  {item['time']}（北京时间）" if item.get("time") else ""
            high = item.get("high")
            low = item.get("low")
            range_part = ""
            if high is not None and low is not None:
                range_part = f"  日内高 {high:.2f}/低 {low:.2f}"
            lines.append(
                f"{i}. {item['name']} {item['price']:.2f}  "
                f"{arrow}{item['pct']:+.2f}%{range_part}{time_part}"
            )
    else:
        lines.append("【黄金实时】数据不可用")
    if us_stocks:
        us_parts = [
            f"{item['name']} {item['price']:.2f} ({item['pct']:+.2f}%)"
            for item in us_stocks
        ]
        lines.append("")
        lines.append("美盘收盘：" + " | ".join(us_parts))
    else:
        lines.append("")
        lines.append("美盘收盘：数据源暂不可用")
    if us_futures:
        parts = [
            f"{item['name']} {item['price']:.2f} {item['pct']:+.2f}%"
            for item in us_futures
        ]
        futures_time = str(us_futures[0].get("time") or "")
        hhmm = futures_time.split(" ")[-1][-5:]
        if len(hhmm) < 5 or ":" not in hhmm:
            hhmm = ""
        time_suffix = f"，{hhmm}" if hhmm else ""
        lines.append(f"美股期货（交易中{time_suffix}）：" + " | ".join(parts))
    else:
        lines.append("美股期货：数据源暂不可用")
    if up_sectors:
        lines.append("")
        lines.append("🔥 领涨板块：" if up_source == "东方财富" else f"🔥 领涨板块（{up_source}口径）：")
        for i, s in enumerate(up_sectors, 1):
            lines.append(f"{i}. {s['name']} {s['pct']:+.2f}%")
    else:
        lines.append("")
        lines.append("🔥 领涨板块：数据源暂不可用")
    if down_sectors:
        lines.append("")
        lines.append("🧊 领跌板块：" if down_source == "东方财富" else f"🧊 领跌板块（{down_source}口径）：")
        for i, s in enumerate(down_sectors, 1):
            lines.append(f"{i}. {s['name']} {s['pct']:+.2f}%")
    else:
        lines.append("")
        lines.append("🧊 领跌板块：数据源暂不可用")
    news_lines = _news_block(news)
    if news_lines:
        lines.append("━━━━━━━━━━━━━━")
        lines += news_lines
    lines.append("━━━━━━━━━━━━━━")
    if analysis:
        lines.append(f"💬 行情分析：{analysis}")
    else:
        pcts = [i["pct"] for i in indices]
        main_line = f"资金主线集中在「{up_sectors[0]['name']}」方向" if up_sectors else "板块轮动较快"
        lines.append(f"💡 简评：{_mood_line(pcts)}；{main_line}。")
    if alert:
        lines.append(alert)
    lines.append("⚠️ 数据仅供参考，不构成投资建议。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 飞书推送
# ---------------------------------------------------------------------------
def feishu_send(text: str) -> bool:
    """读取 openclaw.json 中的应用凭据 → tenant_access_token → 发群消息。"""
    with open(find_openclaw_json(), encoding="utf-8") as f:
        cfg = json.load(f)
    feishu_cfg = cfg.get("channels", {}).get("feishu", {})
    app_id = feishu_cfg.get("appId")
    app_secret = feishu_cfg.get("appSecret")
    if not app_id or not app_secret:
        raise RuntimeError("openclaw.json 缺少 feishu appId/appSecret")

    # 1. 换 token
    token_data = http_post_json(
        f"{API_BASE}/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
        {},
    )
    if token_data.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {token_data.get('msg')}")
    token = token_data["tenant_access_token"]

    # 2. 发消息
    send_data = http_post_json(
        f"{API_BASE}/im/v1/messages?receive_id_type=chat_id",
        {
            "receive_id": CHAT_ID,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        },
        {"Authorization": f"Bearer {token}"},
    )
    if send_data.get("code") != 0:
        raise RuntimeError(f"发送消息失败: {send_data.get('msg')}")
    return True


def push_pending(mode: str) -> int:
    """读待推送分析版文件并推送到群里；文件缺失则降级推基础版。返回退出码。"""
    pending_path = os.path.join(PENDING_DIR, f"{mode}.txt")
    text = None
    if os.path.exists(pending_path):
        with open(pending_path, encoding="utf-8") as f:
            text = f.read().strip()
        if not text:
            text = None
    if text:
        log(f"待推送分析版已就绪（{len(text)} 字），开始推送")
    else:
        # 降级：用数据源基础版推送，保证不漏
        md_path = os.path.join(DATA_DIR, f"latest-{mode}.md")
        if os.path.exists(md_path):
            with open(md_path, encoding="utf-8") as f:
                text = f.read().strip()
            log(f"WARN 待推送分析版不存在，降级推送基础版")
        else:
            log(f"ERROR 待推送文件与数据源均不存在: {pending_path}")
            return 1
    try:
        feishu_send(text)
    except Exception as e:
        log(f"ERROR 推送失败: {e}")
        return 1
    # 成功归档待推送文件
    if os.path.exists(pending_path):
        archive = f"{pending_path}.{datetime.now(TZ_UTC8).strftime('%Y%m%d%H%M%S')}.sent"
        try:
            os.rename(pending_path, archive)
        except Exception as e:
            log(f"WARN 归档待推送文件失败: {e}")
    log(f"{mode} 推送成功 ✅")
    return 0


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="A股简报自动推送")
    parser.add_argument("--mode", choices=["morning", "close"], default="morning",
                        help="morning=早盘(09:20)，close=收盘(15:20)")
    parser.add_argument("--dry-run", action="store_true", help="只生成文本打印，不推送")
    parser.add_argument("--no-llm", action="store_true", help="强制纯模板，不调用模型")
    parser.add_argument("--save", action="store_true", help="把数据源基础版写入本地 logs/data/")
    parser.add_argument("--no-push", action="store_true", help="抓数据+存数据源但不推送（基础版供 AI 分析用）")
    parser.add_argument("--push-pending", choices=["morning", "close"], default=None,
                        help="读 logs/pending/{mode}.txt 分析版推送（定时推送任务用）")
    args = parser.parse_args()

    # 待推送模式：只负责按时把分析版推出去
    if args.push_pending:
        if args.dry_run:
            log("dry-run 模式，不推送（--push-pending 仅拦截，不发送）")
            return 0
        if args.no_push:
            log("--no-push 与 --push-pending 组合，已拦截，不推送")
            return 0
        log(f"待推送任务开始 mode={args.push_pending}")
        return push_pending(args.push_pending)

    log(f"A股简报任务开始 mode={args.mode}" + ("（dry-run）" if args.dry_run else "")
        + ("（--no-llm）" if args.no_llm else ""))
    try:
        indices = fetch_indices()
    except Exception as e:
        log(f"ERROR 指数数据双源抓取均失败，简报中止: {e}")
        return 1

    degradations: list[str] = []
    if args.mode == "morning":
        us_stocks = fetch_us_stocks(degradations=degradations)
        gold = fetch_gold(degradations=degradations)
        us_futures = None
    else:
        us_stocks = fetch_us_stocks(degradations=degradations)
        gold = fetch_gold(degradations=degradations)
        us_futures = fetch_us_futures(degradations=degradations)
    up_sectors, up_source = fetch_sectors_with_source(po=1, degradations=degradations)
    if args.mode == "close":
        down_sectors, down_source = fetch_sectors_with_source(po=0, degradations=degradations)
        breadth, breadth_source = fetch_breadth_with_source(degradations=degradations)
    else:
        down_sectors, down_source = [], "不可用"
        breadth = {"up": 0, "down": 0, "flat": 0}
        breadth_source = "不可用"
    news = fetch_news(degradations=degradations)
    if args.mode == "morning":
        us_status = "可用" if us_stocks else "不可用"
        gold_status = "可用" if gold else "不可用"
        log(f"数据源状态: 指数=腾讯/东财兜底 领涨板块={up_source} 领跌板块={down_source} 涨跌家数={breadth_source} 美股={us_status} 黄金={gold_status} 快讯={'东方财富' if news else '空'}")
    else:
        us_status = "可用" if us_stocks else "不可用"
        gold_status = "可用" if gold else "不可用"
        futures_status = "可用" if us_futures else "不可用"
        log(f"数据源状态: 指数=腾讯/东财兜底 领涨板块={up_source} 领跌板块={down_source} 涨跌家数={breadth_source} 美股={us_status} 美股期货={futures_status} 黄金={gold_status} 快讯={'东方财富' if news else '空'}")

    consecutive = update_degradation_state(degradations)
    degradation_alert = None
    if degradations:
        reasons = "；".join(dict.fromkeys(degradations))
        if consecutive >= DEGRADE_ALERT_THRESHOLD:
            log(f"ERROR 数据源已连续 {consecutive} 个交易日降级，今日降级: {reasons}")
            degradation_alert = f"⚠️ 数据源已连续 {consecutive} 个交易日降级，请检查东财/腾讯接口稳定性"
        else:
            log(f"WARN 数据源发生降级（连续 {consecutive} 个交易日），今日降级: {reasons}")

    # 行情分析（失败降级纯模板，不影响推送）
    analysis = None
    if not args.no_llm and not (args.mode == "close" and breadth_source == "不可用"):
        try:
            analysis = llm_analyze(args.mode, indices, up_sectors, down_sectors, breadth, news,
                                   us_stocks, gold, up_source, down_source, breadth_source)
            log(f"行情分析生成成功（{len(analysis)} 字）")
        except Exception as e:
            log(f"WARN 行情分析生成失败，降级纯模板: {e}")
    elif args.mode == "close" and breadth_source == "不可用":
        log("WARN 涨跌家数数据不可用，跳过AI分析，使用纯模板")

    try:
        if args.mode == "morning":
            briefing = build_morning(indices, up_sectors, news, analysis, us_stocks, gold,
                                     up_source, degradation_alert)
        else:
            briefing = build_close(indices, up_sectors, down_sectors, breadth, news, analysis,
                                   us_stocks, gold, us_futures, up_source, down_source,
                                   breadth_source, degradation_alert)
    except Exception as e:
        log(f"ERROR 简报生成失败: {e}")
        return 1

    log("简报生成成功：\n" + briefing)

    # 数据源输出（不推送也保存，供 bot 分析用）
    if args.save:
        save_data_source(args.mode, indices, up_sectors, down_sectors, breadth, news, briefing,
                         {"up_sectors": up_source, "down_sectors": down_source,
                          "breadth": breadth_source},
                         us_stocks, gold, us_futures)

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

    if args.no_push:
        log("基础版不推送（--no-push），仅供 AI 分析用")
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
