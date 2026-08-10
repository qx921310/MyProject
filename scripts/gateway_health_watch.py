#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermes gateway 健康監控告警 watchdog（給 Hermes cron 使用，每 5 分鐘跑一次）。

正常時 stdout 保持空白且退出碼為 0；任何異常時在 stdout 輸出中文告警文本，
由 cron 原樣投遞到微信 + WhatsApp。

只使用 Python 標準庫，適合 1GB VPS 輕量運行。
"""

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone


LOG_PATH = os.path.expanduser('~/.hermes/logs/gateway.log')
LOG_TAIL_BYTES = 256 * 1024
PROCESS_PATTERN = 'hermes_cli.main gateway run'
WHATSAPP_HEALTH_URL = 'http://127.0.0.1:3000/health'
HTTP_TIMEOUT_SECONDS = 8
LOW_MEMORY_KB = 150 * 1024
RESTART_STUCK_MINUTES = 4

BEIJING_TZ = timezone(timedelta(hours=8))

# 日誌時間戳格式：'2026-08-10 16:33:57,344 INFO ...'（UTC）
TS_RE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')

# 重啟等待完成/啟動標記，出現任一即表示 gateway 已脫離重啟等待
RESTART_DONE_MARKERS = (
    'response ready',
    'Restart deferred wait complete',
    'Starting Hermes Gateway',
)


def beijing_now():
    """當前北京時間（UTC+8）。"""
    return datetime.now(BEIJING_TZ)


def read_log_tail(path, max_bytes):
    """讀取檔案尾部最多 max_bytes 位元組，缺位元組轉為可讀文字。"""
    with open(path, 'rb') as fp:
        size = os.fstat(fp.fileno()).st_size
        if size > max_bytes:
            fp.seek(size - max_bytes)
        data = fp.read()
    return data.decode('utf-8', errors='replace')


def parse_log_timestamp(line):
    """從日誌行開頭解析 UTC 時間戳；失敗回傳 None。"""
    match = TS_RE.match(line)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def check_process():
    """檢查 1：hermes-gateway 進程是否存在。"""
    try:
        result = subprocess.run(
            ['pgrep', '-f', PROCESS_PATTERN],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        if result.returncode != 0:
            return '❌ hermes-gateway 進程不存在（pgrep 未找到 hermes_cli.main gateway run）'
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f'❌ 檢查 hermes-gateway 進程失敗：{exc}'
    return None


def check_restart_stuck():
    """檢查 2：gateway 是否卡在重啟等待（Restart deferred 後無完成/啟動標記）。"""
    try:
        tail = read_log_tail(LOG_PATH, LOG_TAIL_BYTES)
    except OSError as exc:
        return f'❌ 無法讀取 gateway 日誌（{LOG_PATH}）：{exc}'

    lines = tail.splitlines()
    # 從 256KB 中間截斷時第一行可能是殘缺行，且不帶時間戳，丟掉它
    if lines and not TS_RE.match(lines[0]):
        lines = lines[1:]

    deferred_line = None
    deferred_ts = None
    done_seen_after_deferred = False

    for line in lines:
        # 注意順序：'Restart deferred wait complete' 本身含 'Restart deferred'
        # 子字串，必須先判斷完成/啟動標記，避免把完成行誤當成新的 deferred
        if any(marker in line for marker in RESTART_DONE_MARKERS):
            done_seen_after_deferred = True
        elif 'Restart deferred' in line:
            deferred_line = line
            deferred_ts = parse_log_timestamp(line)
            done_seen_after_deferred = False

    if deferred_line is None:
        # 日誌中根本沒有 Restart deferred，跳過此檢查
        return None
    if done_seen_after_deferred:
        return None
    if deferred_ts is None:
        # 無法解析時間戳，無法判斷是否超過 4 分鐘，不誤報
        return None

    age = datetime.now(timezone.utc) - deferred_ts.replace(tzinfo=timezone.utc)
    if age > timedelta(minutes=RESTART_STUCK_MINUTES):
        return (
            '❌ Gateway 疑似卡在重啟等待：最後一次 "Restart deferred" 距今超過 '
            f'{RESTART_STUCK_MINUTES} 分鐘且無完成標記，建議執行 '
            '請讓雲管家強制重啟 hermes-gateway 服務'
        )
    return None


def check_whatsapp_bridge():
    """檢查 3：WhatsApp bridge 健康檢查，status 必須為 connected。"""
    try:
        with urllib.request.urlopen(
            WHATSAPP_HEALTH_URL, timeout=HTTP_TIMEOUT_SECONDS
        ) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        if payload.get('status') != 'connected':
            return (
                f'⚠️ WhatsApp bridge 狀態異常：status={payload.get("status")!r}'
                '（預期 connected）'
            )
    except Exception as exc:
        return (
            f'⚠️ WhatsApp bridge 健康檢查失敗（{WHATSAPP_HEALTH_URL}）：{exc}'
        )
    return None


def check_memory():
    """檢查 4：可用記憶體（MemAvailable）是否低於 150 MB。"""
    try:
        with open('/proc/meminfo', 'r', encoding='utf-8') as fp:
            for line in fp:
                if line.startswith('MemAvailable:'):
                    available_kb = int(line.split()[1])
                    if available_kb < LOW_MEMORY_KB:
                        return (
                            f'⚠️ 可用記憶體不足：{available_kb / 1024:.0f} MB'
                            '（低於 150 MB），請檢查是否有孤兒 hermes serve 進程'
                        )
                    return None
        return '⚠️ /proc/meminfo 中找不到 MemAvailable 欄位'
    except (OSError, ValueError) as exc:
        return f'⚠️ 無法讀取 /proc/meminfo：{exc}'


def main():
    checks = (
        check_process,
        check_restart_stuck,
        check_whatsapp_bridge,
        check_memory,
    )
    issues = []
    for check in checks:
        try:
            issue = check()
        except Exception as exc:  # 任何未預期錯誤都轉成告警，不讓腳本崩潰
            issue = f'❌ 健康檢查 {check.__name__} 發生未預期錯誤：{exc}'
        if issue:
            issues.append(issue)

    if not issues:
        return  # 正常：保持靜默

    now_bj = beijing_now()
    lines = [f'🚨【Gateway 健康告警】{now_bj:%Y-%m-%d %H:%M} (北京時間)']
    lines.extend(issues)
    lines.append('請回覆「查一下 gateway」讓雲管家處理。')
    print('\n'.join(lines))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # 最外層防護：即使主流程出錯也輸出告警並以退出碼 0 結束
        print(f'🚨【Gateway 健康告警】{beijing_now():%Y-%m-%d %H:%M} (北京時間)')
        print(f'❌ 健康監控腳本執行發生未預期錯誤：{exc}')
        print('請回覆「查一下 gateway」讓雲管家處理。')
    sys.exit(0)
