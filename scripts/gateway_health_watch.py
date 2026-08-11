#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermes gateway 健康監控告警 watchdog（給 Hermes cron 使用，每 5 分鐘跑一次）。

正常時 stdout 保持空白且退出碼為 0；任何異常時在 stdout 輸出中文告警文本，
由 cron 原樣投遞到微信 + WhatsApp。

只使用 Python 標準庫，適合 1GB VPS 輕量運行。

================ 已知問題特徵庫（KNOWN ISSUE REGISTRY） ================
分類：可自動處理（auto-fix）vs 僅告警（alarm-only）
----------------------------------------------------------------------
1. 孤兒 hermes serve 進程（PPID=1、cgroup 在 session-NNN.scope、無活躍連接）
   → auto-fix：記憶體低於 150MB 時自動掃描並終止，輸出「✅ 已自動清理 …」
     （2026-08-11 起：啟動 >=5 分鐘且連續 2 次健康檢查（間隔 5 分鐘）均無
     活躍連接才自動清理；觀察狀態存 ~/.hermes/scripts/.orphan_state.json）
2. 孤兒 hermes serve 進程但存在活躍連接（ESTABLISHED）
   → 僅告警：不自動終止，提示人工確認（同時打斷「連續無活躍」的確認）
3. gateway 進程消失（check_process）             → 僅告警
4. gateway 卡在重啟等待（check_restart_stuck）   → 僅告警
5. WhatsApp bridge 異常（check_whatsapp_bridge） → 僅告警
6. 清理後記憶體仍不足 / 掃描或 ss 檢查失敗       → 僅告警
======================================================================
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
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

# 孤兒進程診斷相關（見模組頭部「已知問題特徵庫」第 1、2 條）
ORPHAN_CMDLINE_MARKERS = ('serve --isolated', 'serve --host')
SESSION_SCOPE_RE = re.compile(r'(?:^|/)session-\d+\.scope(?:/|$)')
SS_TIMEOUT_SECONDS = 10
KILL_WAIT_STEP = 0.1   # SIGTERM 後輪詢存活間隔（秒）
KILL_WAIT_MAX = 2.0    # SIGTERM 後最長等待（秒），超時再 SIGKILL

# 孤兒進程時間判定（問題 2，2026-08-11 起）：
ORPHAN_STATE_PATH = os.path.expanduser('~/.hermes/scripts/.orphan_state.json')
ORPHAN_MIN_AGE_SECONDS = 5 * 60   # 進程啟動 <5 分鐘不自動清理，僅告警觀察
ORPHAN_CONFIRM_SECONDS = 5 * 60   # 連續 2 次（間隔 5 分鐘）無活躍連接才清理

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
    """檢查 1：hermes-gateway 進程是否存在。【僅告警】"""
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
    """檢查 2：gateway 是否卡在重啟等待（Restart deferred 後無完成/啟動標記）。【僅告警】"""
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
    """檢查 3：WhatsApp bridge 健康檢查，status 必須為 connected。【僅告警】"""
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


def read_mem_available_kb():
    """讀取 /proc/meminfo 的 MemAvailable（KB）；找不到欄位時拋 ValueError。"""
    try:
        with open('/proc/meminfo', 'r', encoding='utf-8') as fp:
            for line in fp:
                if line.startswith('MemAvailable:'):
                    return int(line.split()[1])
        raise ValueError('/proc/meminfo 中找不到 MemAvailable 欄位')
    except OSError:
        raise


def scan_orphan_serve_processes():
    """掃描 hermes serve 孤兒進程（見特徵庫第 1 條，auto-fix 的對象）。

    判定特徵：cmdline 含 'serve --isolated' 或 'serve --host'，
    PPID=1，且 cgroup 落在 session-NNN.scope（而非 systemd 單元）。
    返回 [{pid, cmdline, rss_kb}]；直接讀 /proc，不依賴 ps。
    """
    orphans = []
    for name in os.listdir('/proc'):
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open(f'/proc/{pid}/cmdline', 'rb') as fp:
                cmdline = fp.read().replace(b'\x00', b' ').decode('utf-8', 'replace').strip()
            with open(f'/proc/{pid}/stat', 'r', encoding='utf-8') as fp:
                stat = fp.read()
            with open(f'/proc/{pid}/cgroup', 'r', encoding='utf-8') as fp:
                cgroup = fp.read()
            with open(f'/proc/{pid}/status', 'r', encoding='utf-8') as fp:
                status = fp.read()
        except OSError:
            continue  # 進程已退出或權限不足，跳過

        if not any(marker in cmdline for marker in ORPHAN_CMDLINE_MARKERS):
            continue
        # /proc/<pid>/stat 的 comm 可能含空格，從最後一個 ')' 後開始切欄位
        stat_rest = stat[stat.rfind(')') + 2:].split()
        if len(stat_rest) < 2 or stat_rest[1] != '1':
            continue  # 必須 PPID=1（被 reparent 的孤兒）
        cgroup_paths = (line.split(':', 2)[-1] for line in cgroup.splitlines())
        if not any(SESSION_SCOPE_RE.search(path) for path in cgroup_paths):
            continue  # 必須在 session-NNN.scope，systemd 單元（*.service）不算孤兒

        rss_kb = 0
        for sl in status.splitlines():
            if sl.startswith('VmRSS:'):
                rss_kb = int(sl.split()[1])
                break
        orphans.append({'pid': pid, 'cmdline': cmdline, 'rss_kb': rss_kb})
    return orphans


def read_process_age_seconds(pid):
    """讀取進程已運行秒數（/proc/<pid>/stat 的 starttime + /proc/stat 的 btime）。

    只用 Python 標準庫，不依賴外部命令；讀取失敗或欄位缺失時回傳 None，
    調用方應保守處理（不自動清理）。
    """
    try:
        clk_tck = os.sysconf('SC_CLK_TCK')
        with open(f'/proc/{pid}/stat', 'r', encoding='utf-8') as fp:
            stat = fp.read()
        # comm 可能含空格，從最後一個 ')' 後開始切欄位；starttime 為第 22 欄（索引 19）
        stat_rest = stat[stat.rfind(')') + 2:].split()
        if len(stat_rest) < 20:
            return None
        starttime = int(stat_rest[19])
        with open('/proc/stat', 'r', encoding='utf-8') as fp:
            btime = None
            for line in fp:
                if line.startswith('btime '):
                    btime = int(line.split()[1])
                    break
        if btime is None:
            return None
        start_ts = btime + starttime / float(clk_tck)
        return max(0.0, time.time() - start_ts)
    except (OSError, ValueError):
        return None


def find_active_orphan_pids(pids):
    """用 ss -tnp 判斷哪些 pid 有活躍（ESTABLISHED）連接。

    CLOSE-WAIT 等其他狀態不算活躍。ss 不存在或執行失敗時返回
    (None, 錯誤訊息)，調用方此時必須放棄自動清理（fail-safe）。
    """
    try:
        result = subprocess.run(
            ['ss', '-tnp'],
            capture_output=True,
            text=True,
            timeout=SS_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if result.returncode != 0:
        return None, f'ss 退出碼 {result.returncode}'

    active = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if not fields or fields[0] != 'ESTAB':
            continue
        for pid in pids:
            # 只匹配完整 pid 數字，避免 pid=123 誤命中 pid=1234
            if re.search(rf'pid={pid}(?:,|\))', line):
                active.add(pid)
    return active, None


def load_orphan_state():
    """讀取孤兒觀察狀態（pid -> {first_seen, cmdline}）；檔案缺失/損壞回傳空 dict。"""
    try:
        with open(ORPHAN_STATE_PATH, 'r', encoding='utf-8') as fp:
            data = json.load(fp)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_orphan_state(state):
    """持久化觀察狀態（先寫暫存檔再原子改名）。成功回傳 None，失敗回傳錯誤資訊。"""
    try:
        os.makedirs(os.path.dirname(ORPHAN_STATE_PATH), exist_ok=True)
        tmp_path = ORPHAN_STATE_PATH + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as fp:
            json.dump(state, fp, ensure_ascii=False, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_path, ORPHAN_STATE_PATH)
        return None
    except OSError as exc:
        return f'無法寫入孤兒進程觀察狀態（{ORPHAN_STATE_PATH}）：{exc}'


def process_is_orphan(pid):
    """重新讀取 /proc/<pid>/cmdline，確認仍匹配孤兒特徵（TOCTOU 防護，問題 1）。

    返回 (still_orphan, still_alive)：
    - 進程不存在或已是殭屍（已死、等待父進程回收） → (False, False)
    - 仍在運行但 cmdline 不匹配 → (False, True)（PID 可能已被複用）
    """
    try:
        with open(f'/proc/{pid}/stat', 'r', encoding='utf-8') as fp:
            stat = fp.read()
    except OSError:
        return False, False
    stat_rest = stat[stat.rfind(')') + 2:].split()
    if not stat_rest or stat_rest[0] == 'Z':
        return False, False  # 已退出（殭屍態也是死進程，只差父進程回收）
    try:
        with open(f'/proc/{pid}/cmdline', 'rb') as fp:
            cmdline = fp.read().replace(b'\x00', b' ').decode('utf-8', 'replace').strip()
    except OSError:
        return False, False
    return any(marker in cmdline for marker in ORPHAN_CMDLINE_MARKERS), True


def process_is_alive(pid):
    """判斷進程是否仍在運行（殭屍態視為已死，不阻塞清理流程）。"""
    try:
        with open(f'/proc/{pid}/stat', 'r', encoding='utf-8') as fp:
            stat = fp.read()
    except OSError:
        return False
    stat_rest = stat[stat.rfind(')') + 2:].split()
    return bool(stat_rest) and stat_rest[0] != 'Z'


def terminate_process(pid):
    """終止孤兒進程（含 TOCTOU 防護）。

    每次發送信號前都重新讀取 /proc/<pid>/cmdline，確認仍匹配
    ORPHAN_CMDLINE_MARKERS 才殺；進程已退出視為成功，cmdline 不再匹配
    （PID 已被複用給其他進程）則放棄並返回錯誤資訊。
    先 SIGTERM，等待 KILL_WAIT_MAX 秒未退出再 SIGKILL。

    返回 (是否已確認進程消失, 錯誤訊息或 None)。
    """
    still_orphan, still_alive = process_is_orphan(pid)
    if not still_alive:
        return True, None  # 已自行退出（或已是殭屍）
    if not still_orphan:
        return False, 'PID 已被複用（cmdline 不再匹配孤兒特徵），放棄終止'

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True, None  # 已自行退出
    except OSError as exc:
        return False, str(exc)

    deadline = time.monotonic() + KILL_WAIT_MAX
    while time.monotonic() < deadline:
        if not process_is_alive(pid):
            return True, None
        time.sleep(KILL_WAIT_STEP)

    still_orphan, still_alive = process_is_orphan(pid)
    if not still_alive:
        return True, None
    if not still_orphan:
        return False, 'SIGTERM 後 PID 已被複用（cmdline 不再匹配孤兒特徵），放棄 SIGKILL'

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True, None
    except OSError as exc:
        return False, str(exc)
    time.sleep(0.2)
    return (not process_is_alive(pid)), None


def diagnose_and_clean_orphans():
    """孤兒進程診斷 + 自動清理（已知問題特徵庫第 1 條，auto-fix）。

    自動清理需同時滿足（2026-08-11 起，問題 2）：
    1. 進程啟動時間 >= 5 分鐘（read_process_age_seconds）；
    2. 連續 2 次健康檢查（間隔 5 分鐘）均無活躍（ESTAB）連接，首次無活躍
       發現記錄在 ORPHAN_STATE_PATH，跨 cron 運行記憶；進程消失或 PID 被
       複用（cmdline 不同）時清除對應記錄。

    返回 dict：cleaned_pids / cleaned_rss_kb（已清理）、blocked_pids
    （有活躍連接、僅告警）、observed_pids（僅觀察、未達清理條件）、
    errors（掃描/ss/終止/狀態寫入失敗，僅告警）。
    """
    result = {
        'cleaned_pids': [],
        'cleaned_rss_kb': 0,
        'blocked_pids': [],
        'observed_pids': [],
        'errors': [],
    }
    try:
        orphans = scan_orphan_serve_processes()
    except OSError as exc:
        result['errors'].append(f'孤兒進程掃描失敗：{exc}')
        return result

    state = load_orphan_state()
    current = {str(o['pid']): o['cmdline'] for o in orphans}
    state_changed = False
    for pid in list(state):
        entry = state.get(pid)
        if not (
            isinstance(entry, dict)
            and isinstance(entry.get('first_seen'), (int, float))
            and isinstance(entry.get('cmdline'), str)
        ):
            del state[pid]  # 記錄格式無效，丟棄
            state_changed = True
            continue
        if pid not in current:
            del state[pid]  # 進程已消失，清除記錄
            state_changed = True
        elif entry['cmdline'] != current[pid]:
            del state[pid]  # PID 被複用且 cmdline 不同，清除記錄
            state_changed = True

    if not orphans:
        if state_changed:
            err = save_orphan_state(state)
            if err:
                result['errors'].append(err)
        return result

    active, ss_error = find_active_orphan_pids([o['pid'] for o in orphans])
    if ss_error is not None:
        result['errors'].append(
            f'無法檢查孤兒進程活躍連接（ss 失敗：{ss_error}），已跳過自動清理'
        )
        return result  # fail-safe：不自動清理，觀察狀態也不更新

    now_ts = time.time()
    for orphan in orphans:
        pid = orphan['pid']
        key = str(pid)
        if orphan['pid'] in active:
            # 特徵庫第 2 條：有活躍連接，不自動終止，僅告警
            result['blocked_pids'].append(pid)
            if key in state:
                del state[key]  # 打斷「連續無活躍」確認，清除記錄
                state_changed = True
            continue

        # 無活躍連接 → 兩層時間判定：進程年齡 + 連續 2 次確認
        age = read_process_age_seconds(pid)
        if key not in state:
            state[key] = {'first_seen': now_ts, 'cmdline': orphan['cmdline']}
            state_changed = True
            result['observed_pids'].append(
                (
                    pid,
                    '首次發現僅觀察：需連續 2 次健康檢查（間隔 5 分鐘）'
                    '均無活躍連接才自動清理',
                )
            )
            continue

        elapsed = now_ts - state[key]['first_seen']
        if (
            age is not None
            and age >= ORPHAN_MIN_AGE_SECONDS
            and elapsed >= ORPHAN_CONFIRM_SECONDS
        ):
            killed, err = terminate_process(pid)
            if killed:
                result['cleaned_pids'].append(pid)
                result['cleaned_rss_kb'] += orphan['rss_kb']
                del state[key]
                state_changed = True
            else:
                result['errors'].append(
                    f'無法終止孤兒進程 PID {pid}：{err or "仍在運行"}'
                )
        else:
            if age is None:
                reason = '無法讀取啟動時間，僅觀察不清理'
            elif age < ORPHAN_MIN_AGE_SECONDS:
                reason = (
                    f'啟動約 {int(age // 60)} 分鐘（<5 分鐘），僅觀察不清理'
                )
            else:
                reason = '確認間隔未滿 5 分鐘（尚未連續 2 次無活躍連接），僅觀察不清理'
            result['observed_pids'].append((pid, reason))

    if state_changed:
        err = save_orphan_state(state)
        if err:
            result['errors'].append(err)
    return result


def check_memory():
    """檢查 4：可用記憶體（MemAvailable）低於 150 MB 時觸發孤兒進程診斷。

    流程：記憶體低 → 先掃描/清理孤兒（auto-fix）→ 重新讀記憶體。
    - 清理後恢復（>=150MB）：只輸出「✅ 已自動清理 …」
    - 清理後仍低：追加「⚠️ 清理後記憶體仍不足，可能有其他占用」
    - 有活躍連接的孤兒 / 掃描或 ss 失敗：僅告警，不自動終止
    """
    try:
        available_kb = read_mem_available_kb()
    except (OSError, ValueError) as exc:
        return f'⚠️ 無法讀取 /proc/meminfo：{exc}'
    if available_kb >= LOW_MEMORY_KB:
        return None

    result = diagnose_and_clean_orphans()
    try:
        after_kb = read_mem_available_kb()
    except (OSError, ValueError):
        after_kb = None

    lines = []
    if result['cleaned_pids']:
        pids_str = ', '.join(str(pid) for pid in result['cleaned_pids'])
        freed_mb = round(result['cleaned_rss_kb'] / 1024)
        lines.append(
            f'✅ 已自動清理 {len(result["cleaned_pids"])} 個孤兒 hermes serve 進程'
            f'（PID {pids_str}），釋放約 {freed_mb} MB 記憶體'
        )
    if result['blocked_pids']:
        lines.append(
            '⚠️ 發現孤兒進程但存在活躍連接，需人工確認'
            f'（PID {", ".join(str(pid) for pid in result["blocked_pids"])}）'
        )
    for pid, reason in result['observed_pids']:
        lines.append(f'⚠️ 發現孤兒 hermes serve 進程（PID {pid}）：{reason}')
    lines.extend(result['errors'])

    if after_kb is not None and after_kb < LOW_MEMORY_KB:
        lines.append(
            f'⚠️ 清理後記憶體仍不足：當前可用 {after_kb / 1024:.0f} MB'
            '（低於 150 MB），可能有其他占用'
        )
    if not lines:
        # 沒有可清理的孤兒，也沒有其他錯誤：原樣報告低記憶體
        lines.append(
            f'⚠️ 可用記憶體不足：{available_kb / 1024:.0f} MB（低於 150 MB），'
            '未發現可自動清理的孤兒 hermes serve 進程，可能有其他占用'
        )
    return '\n'.join(lines)


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
