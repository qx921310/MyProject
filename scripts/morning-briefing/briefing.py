#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股简报自动推送脚本（钻仔 / 小钻钻）
=========================================
- 数据源（免费，零 token）：
    腾讯行情接口（三大指数）+ 东方财富接口（行业板块涨跌幅榜、涨跌家数、7x24 快讯）
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
  python3 briefing.py --mode morning            # 早盘推送
  python3 briefing.py --mode close              # 收盘推送
  python3 briefing.py --mode morning --dry-run  # 只生成文本打印，不推送
  python3 briefing.py --mode morning --no-llm   # 强制纯模板（不调模型）
  python3 briefing.py --mode morning --save     # 额外把数据源写入 logs/data/（供 bot 分析用）

数据源输出（--save）：
  写入 logs/data/latest-{mode}.json（结构化数据：指数/板块/涨跌家数/新闻/基础简报）
  和 logs/data/latest-{mode}.md（基础版简报文本），供钻仔/金仔各自基于数据做分析测评。
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
DATA_DIR = os.path.join(BASE_DIR, "logs", "data")
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


def http_get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


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


def fetch_news(top: int = 8) -> list[dict]:
    """东财 7x24 快讯，按财经相关性关键词过滤。"""
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
    return result


# ---------------------------------------------------------------------------
# 行情分析（DeepSeek 直连，轻量）
# ---------------------------------------------------------------------------
def _load_deepseek_config() -> tuple[str, str, str]:
    """返回 (api_key, base_url, model_id)，均从 openclaw.json 读取。"""
    with open(OPENCLAW_JSON, encoding="utf-8") as f:
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
                down_sectors: list[dict], breadth: dict, news: list[dict]) -> str:
    """调用 DeepSeek 生成行情分析（150 字内）。失败抛异常由调用方降级。"""
    api_key, base_url, model_id = _load_deepseek_config()
    if not api_key:
        raise RuntimeError("未找到 DEEPSEEK_API_KEY")

    idx_lines = "；".join(
        f"{i['name']} {i['price']:.2f} ({i['pct']:+.2f}%)" for i in indices
    )
    up_lines = "、".join(f"{s['name']}{s['pct']:+.2f}%" for s in up_sectors[:5]) or "无"
    down_lines = "、".join(f"{s['name']}{s['pct']:+.2f}%" for s in down_sectors[:5]) or "无"
    news_lines = "\n".join(f"{n['time']} {n['title']}" for n in news[:8]) or "无"

    mode_label = "早盘" if mode == "morning" else "收盘"
    prompt = (
        f"你是严谨的A股分析师。基于以下【真实数据】和【财经快讯】写{mode_label}行情分析，"
        f"不超过150字，中文，只输出分析正文。要求：①先给一句话盘面强弱判断；"
        f"②指出主线板块与逻辑；③挑1-2条关键消息面并说明对盘面的潜在影响；"
        f"④不给买卖建议。严禁编造数据，只使用提供的信息。\n\n"
        f"【指数】{idx_lines}\n"
        f"【涨跌家数】上涨{breadth['up']}/下跌{breadth['down']}/平盘{breadth['flat']}\n"
        f"【领涨板块】{up_lines}\n"
        f"【领跌板块】{down_lines}\n"
        f"【财经快讯】\n{news_lines}"
    )

    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": "你是一位严谨、简洁的A股市场分析师。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 400,
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
                  news: list[dict], analysis: str | None) -> str:
    lines = ["📊 早盘简报 · " + now_beijing() + "（北京时间）", "━━━━━━━━━━━━━━"]
    lines += _fmt_indices(indices)
    if sectors:
        lines.append("━━━━━━━━━━━━━━")
        lines.append("🔥 领涨板块：")
        for i, s in enumerate(sectors, 1):
            lines.append(f"{i}. {s['name']} {s['pct']:+.2f}%")
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
    lines.append("⚠️ 数据仅供参考，不构成投资建议。")
    return "\n".join(lines)


def save_data_source(mode: str, indices: list[dict], up_sectors: list[dict],
                     down_sectors: list[dict], breadth: dict, news: list[dict],
                     briefing: str) -> str:
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
    }
    json_path = os.path.join(DATA_DIR, f"latest-{mode}.json")
    md_path = os.path.join(DATA_DIR, f"latest-{mode}.md")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(briefing + "\n")
    log(f"数据源已写入: {json_path}")
    return json_path


def build_close(indices: list[dict], up_sectors: list[dict], down_sectors: list[dict],
                breadth: dict, news: list[dict], analysis: str | None) -> str:
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
    args = parser.parse_args()

    log(f"A股简报任务开始 mode={args.mode}" + ("（dry-run）" if args.dry_run else "")
        + ("（--no-llm）" if args.no_llm else ""))
    try:
        indices = fetch_indices()
        up_sectors = fetch_sectors(po=1)
        down_sectors = fetch_sectors(po=0) if args.mode == "close" else []
        breadth = fetch_breadth() if args.mode == "close" else {"up": 0, "down": 0, "flat": 0}
        news = fetch_news()
    except Exception as e:
        log(f"ERROR 数据抓取失败: {e}")
        return 1

    # 行情分析（失败降级纯模板，不影响推送）
    analysis = None
    if not args.no_llm:
        try:
            analysis = llm_analyze(args.mode, indices, up_sectors, down_sectors, breadth, news)
            log(f"行情分析生成成功（{len(analysis)} 字）")
        except Exception as e:
            log(f"WARN 行情分析生成失败，降级纯模板: {e}")

    try:
        if args.mode == "morning":
            briefing = build_morning(indices, up_sectors, news, analysis)
        else:
            briefing = build_close(indices, up_sectors, down_sectors, breadth, news, analysis)
    except Exception as e:
        log(f"ERROR 简报生成失败: {e}")
        return 1

    log("简报生成成功：\n" + briefing)

    # 数据源输出（不推送也保存，供 bot 分析用）
    if args.save:
        save_data_source(args.mode, indices, up_sectors, down_sectors, breadth, news, briefing)

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
