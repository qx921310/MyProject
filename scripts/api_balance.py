#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek API 余额查询脚本（仅用 Python 标准库）。"""

import json
import os
import sys
import urllib.error
import urllib.request

ENV_PATH = "/root/.hermes/.env"
API_URL = "https://api.deepseek.com/user/balance"


def read_api_key(path=ENV_PATH):
    """从 .env 读取 DEEPSEEK_API_KEY，容忍引号和首尾空格。"""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                value = line.split("=", 1)[1].strip()
                # 去掉成对的外层引号（单引号或双引号）
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                return value
    return None


def mask_key(key):
    """脱敏显示，形如 sk-xxxx..."""
    return key[:3] + "xxxx..." if key else ""


def query_balance(api_key):
    """请求 DeepSeek 余额接口，返回解析后的 JSON。"""
    req = urllib.request.Request(
        API_URL,
        headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def status_mark(info):
    """按币种给余额打状态标记。"""
    total = float(info.get("total_balance") or 0)
    if total <= 0:
        return "❌ 余额为零"
    currency = info.get("currency", "")
    low = (currency == "CNY" and total < 5) or (currency == "USD" and total < 1)
    return "⚠️ 余额低" if low else "✅ 有余额"


def format_report(data, key):
    """拼装中文可读报告。"""
    lines = ["DeepSeek API 余额", f"Key：{mask_key(key)}"]
    lines.append("账户可用：" + ("是" if data.get("is_available") else "否"))
    for info in data.get("balance_infos") or []:
        lines.append(
            f"{status_mark(info)} {info.get('currency')}：总额 {info.get('total_balance')}"
            f"（赠送 {info.get('granted_balance')} / 充值 {info.get('topped_up_balance')}）"
        )
    return "\n".join(lines)


def fail(want_json, msg, code=1):
    """统一错误出口：--json 时输出 JSON 到 stdout，否则输出中文到 stderr。"""
    if want_json:
        print(json.dumps({"error": msg}, ensure_ascii=False))
    else:
        print(msg, file=sys.stderr)
    sys.exit(code)


def main():
    want_json = "--json" in sys.argv
    key = read_api_key()
    if not key:
        fail(want_json, "未找到 DEEPSEEK_API_KEY，请检查 /root/.hermes/.env", 2)

    try:
        data = query_balance(key)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        if e.code == 401:
            fail(want_json, f"key 无效（认证失败，HTTP 401）。当前 key：{mask_key(key)}")
        fail(want_json, f"接口返回错误（HTTP {e.code}）：{body}")
    except urllib.error.URLError as e:
        fail(want_json, f"网络请求失败：{e.reason}")
    except Exception as e:
        fail(want_json, f"请求或响应解析失败：{e}")

    if want_json:
        print(json.dumps(data, ensure_ascii=False))
    else:
        print(format_report(data, key))


if __name__ == "__main__":
    main()
