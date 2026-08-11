#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务器晨检脚本（配合每日简报 cron 使用）。

输出一行服务器健康摘要（中文、emoji），由 cron 把脚本输出注入简报 prompt，
让 AI 在生成早盘简报时顺带汇报服务器状态。
正常时也输出（供简报引用）；异常时标出 ❌ 让 AI 提醒主人。

只用 Python 标准库，适合 1GB VPS。
"""

import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

BEIJING_TZ = timezone(timedelta(hours=8))
LOW_MEM_MB = 150  # 可用内存低于 150MB 才算低
LOW_DISK_PCT = 80.0


def beijing_now():
    return datetime.now(BEIJING_TZ)


def mem_info():
    """返回 (available_mb, used_mb, total_mb)。"""
    with open('/proc/meminfo', 'r', encoding='utf-8') as fp:
        data = {}
        for line in fp:
            k, _, v = line.partition(':')
            data[k.strip()] = int(v.split()[0])
    total = data.get('MemTotal', 0) // 1024
    avail = data.get('MemAvailable', 0) // 1024
    return avail, total - avail, total


def disk_info():
    """返回 (used_pct, used_gb, total_gb)。"""
    try:
        out = subprocess.run(
            ['df', '-h', '/'], capture_output=True, text=True, timeout=10
        ).stdout
        line = out.splitlines()[1]
        parts = line.split()
        # df 输出: Filesystem Size Used Avail Use% Mounted
        total = parts[1]
        used = parts[2]
        pct = parts[4].rstrip('%')
        return float(pct), used, total
    except Exception:
        return None, None, None


def service_status():
    """检查关键服务，返回 {服务名: 'active'|'inactive'|'unknown'}。

    hermes-gateway 是 user 级 systemd 服务，需要用 --user 查询；
    其余是系统级服务。全部在服务名后标 U 表示 user 级。
    """
    services = {
        'hysteria-server': False,
        'xray': False,
        'sub-server': False,
        'hermes-gateway': True,   # user 级
        'hermes-serve': False,
    }
    result = {}
    for svc, is_user in services.items():
        try:
            cmd = ['systemctl', 'is-active', svc]
            if is_user:
                cmd = ['systemctl', '--user', 'is-active', svc]
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            result[svc] = out
        except Exception:
            result[svc] = 'unknown'
    return result


def orphan_serve_count():
    """统计孤儿 hermes serve 进程数（PPID=1 + session scope）。"""
    count = 0
    for name in os.listdir('/proc'):
        if not name.isdigit():
            continue
        try:
            with open(f'/proc/{name}/cmdline', 'rb') as fp:
                cmd = fp.read().replace(b'\x00', b' ').decode('utf-8', 'replace')
            with open(f'/proc/{name}/cgroup', 'r', encoding='utf-8') as fp:
                cg = fp.read()
            if 'serve --isolated' in cmd or 'serve --host' in cmd:
                if 'session-' in cg and '.service' not in cg:
                    count += 1
        except OSError:
            continue
    return count


def main():
    avail_mb, used_mb, total_mb = mem_info()
    disk_pct, disk_used, disk_total = disk_info()
    services = service_status()
    orphans = orphan_serve_count()

    problems = []
    if avail_mb < LOW_MEM_MB:
        problems.append(f'内存低（可用{avail_mb}MB）')
    if disk_pct is not None and disk_pct > LOW_DISK_PCT:
        problems.append(f'磁盘使用率{disk_pct:.0f}%')
    if orphans > 0:
        problems.append(f'{orphans}个孤儿进程')
    for svc, st in services.items():
        if st != 'active':
            problems.append(f'{svc}={st}')

    if problems:
        flag = '❌'
        detail = '；'.join(problems)
    else:
        flag = '✅'
        detail = '全部正常'

    line = (
        f'{flag}【服务器晨检】{beijing_now():%m-%d %H:%M} 内存'
        f'{used_mb}/{total_mb}MB(可用{avail_mb}MB) 磁盘'
        f'{disk_pct:.0f}%({disk_used}/{disk_total}) 服务'
        f'{" ".join(f"{k}={v}" for k, v in services.items())[:60]}'
    )
    if problems:
        line += f' 异常:{detail}'
    else:
        line += f' {detail}'
    print(line)


if __name__ == '__main__':
    main()
