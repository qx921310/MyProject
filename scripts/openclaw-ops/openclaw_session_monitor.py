#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenClaw 会话卡死只读监控（给 cron 每 5 分钟执行一次，单次运行即退出）。

职责：
1. 只读扫描 session_nodes/session_windows：status='running' 且连续 N 分钟（默认 30）
   无任何活动（last_activity_at/updated_at 早于阈值）→ 判定卡死并告警；
2. 统计 journalctl 中 'changed while starting work' 近 5 分钟出现次数，超过阈值告警；
3. 同一会话 1 小时（冷却期）内不重复告警；状态恢复后发送「已恢复」；
4. 每次检测把卡死会话快照（JSON Lines）追加到 data/session-snapshots.log。

只读：sqlite 以 mode=ro 打开并加 query_only，绝不写运行中的 gateway 库。
敏感信息：FEISHU_WEBHOOK_URL / FEISHU_ALERT_OPEN_ID 从环境变量读取，不硬编码。
"""

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta

from openclaw_common import (
    BEIJING_TZ,
    beijing_iso,
    beijing_now,
    db_meta,
    load_config,
    open_readonly,
    run_cmd,
    send_feishu,
    session_where,
    write_json_atomic,
)


DEFAULTS = {
    "db_path": "/home/ubuntu/.openclaw/agents/main/agent/openclaw-agent.sqlite",
    "openclaw_bin": "openclaw",
    "service": "openclaw-gateway.service",
    "dispatch_error_pattern": "changed while starting work",
    "journalctl_window_minutes": 5,
    "dispatch_error_threshold": 3,
    "idle_minutes": 30,
    "cooldown_minutes": 60,
    "cron_interval_minutes": 5,
    "snapshot_log": "data/session-snapshots.log",
    "state_file": "data/session-alert-state.json",
    "http_timeout_seconds": 10,
}

# 活动时间列候选（全部存在时按多信号校验，任一近期活动即不算卡死）
ACTIVITY_COLUMNS = ("last_activity_at", "last_activity", "updated_at", "modified_at", "activity_at")

# transcript 事件表及时间列候选（用于多信号校验的 transcript 最新事件时间）
TRANSCRIPT_TABLES = ("transcript_events", "session_transcript_active_events")
TRANSCRIPT_TIME_HINTS = ("created_at", "timestamp", "ts", "time", "at", "updated_at", "event_time")

# 标记扫描：只在名称含这些关键字的表里查，避免全库 LIKE 拖慢只读监控
MARKER_TABLE_HINTS = ("session", "transcript", "conversation")
TEXTISH_HINTS = ("payload", "content", "text", "json", "body", "message", "state", "data", "value")


def parse_activity(value):
    """把数据库里的活动时间解析为北京时间 datetime；支持 epoch 秒/毫秒与 ISO 字符串。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
    else:
        s = str(value).strip()
        try:
            v = float(s)
        except ValueError:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                # 无时区字符串按北京时间解释（本方案部署的服务器时钟与北京相差一小时以内）
                dt = dt.replace(tzinfo=BEIJING_TZ)
            return dt.astimezone(BEIJING_TZ)
    if v > 1e11:  # 毫秒时间戳
        v /= 1000.0
    if v < 1e9 or v > 2e9:  # 明显不是秒级时间戳
        return None
    return datetime.fromtimestamp(v, tz=BEIJING_TZ)


def journalctl_errors(cfg):
    """取 journalctl 近 N 分钟日志中匹配 dispatch 错误的全部行；失败返回 (空列表, 说明)。"""
    window = cfg["journalctl_window_minutes"]
    rc, out, err = run_cmd(
        ["journalctl", "-u", cfg["service"], "--since", f"{window} minutes ago", "--no-pager"],
        timeout=60,
    )
    if rc != 0:
        return [], f"journalctl 失败（rc={rc}）: {(err or out).strip()[:200]}"
    return [line for line in out.splitlines() if cfg["dispatch_error_pattern"] in line], None


def latest_transcript_time(conn, cols, key, sid):
    """返回目标会话 transcript 最新事件时间（北京时间 datetime）；无表/无时间列/查询失败返回 None。"""
    for table in TRANSCRIPT_TABLES:
        if table not in cols:
            continue
        names = [c[0] for c in cols[table]]
        time_col = next((c for c in names if c.lower() in TRANSCRIPT_TIME_HINTS), None)
        if time_col is None:
            continue
        where, params = session_where(names, key, sid)
        if where == "1 = 0":
            continue
        safe = table.replace(chr(34), chr(34) * 2)
        try:
            row = conn.execute(
                f'SELECT MAX("{time_col}") FROM "{safe}" WHERE {where}', params
            ).fetchone()
        except sqlite3.Error:
            continue
        if row and row[0] is not None:
            return parse_activity(row[0])
    return None


def find_stuck_sessions(conn, tables, cols, idle_delta, now):
    """扫描 session_nodes/session_windows，多信号校验：status='running' 且所有活动时间列
    及 transcript 最新事件时间都早于阈值才判定卡死（任一近期活动即不算卡死）。"""
    stuck = []
    for table in ("session_nodes", "session_windows"):
        if table not in tables:
            continue
        names = [c[0] for c in cols[table]]
        if "status" not in names:
            continue
        act_cols = [c for c in ACTIVITY_COLUMNS if c in names]
        if not act_cols:
            print(f"[监控] {table} 未找到活动时间列，跳过该表", file=sys.stderr)
            continue
        key_col = "session_key" if "session_key" in names else None
        id_col = "session_id" if "session_id" in names else None
        sel = [f'"{c}"' for c in act_cols] + [f'"{c}"' for c in (key_col, id_col) if c]
        safe = table.replace(chr(34), chr(34) * 2)
        sql = f'SELECT {", ".join(sel)} FROM "{safe}" WHERE status = \'running\''
        for row in conn.execute(sql):
            key_val = row[len(act_cols)] if key_col else None
            id_val = row[len(act_cols) + (1 if key_col else 0)] if id_col else None

            # 多信号：解析全部活动时间列，任一近期活动即不算卡死
            pairs = []
            recent = False
            for raw in row[:len(act_cols)]:
                dt = parse_activity(raw)
                if dt is None:
                    continue  # 无有效时间，忽略该信号
                pairs.append((dt, raw))
                if dt > now or now - dt <= idle_delta:
                    recent = True
                    break
            if recent or not pairs:
                continue

            # transcript 最新事件时间仍活跃则不算卡死
            lt = latest_transcript_time(conn, cols, key_val, id_val)
            if lt is not None and (lt > now or now - lt <= idle_delta):
                continue

            best_dt, best_raw = max(pairs, key=lambda p: p[0])
            item = {
                "activity_raw": best_raw,
                "last_activity": beijing_iso(best_dt),
                "status": "running",
                "table": table,
            }
            if key_col:
                item["session_key"] = key_val
            if id_col:
                item["session_id"] = id_val
            item["alert_key"] = str(
                item.get("session_key") or item.get("session_id") or f"{table}:{best_raw}"
            )
            stuck.append(item)
    # 同一会话可能同时出现在两张表，去重
    return list({s["alert_key"]: s for s in stuck}.values())


def count_session_events(conn, cols, key, sid):
    """统计目标会话的 transcript 事件数（优先 transcript_events，其次 active_events）。"""
    for table in ("transcript_events", "session_transcript_active_events"):
        if table not in cols:
            continue
        names = [c[0] for c in cols[table]]
        where, params = session_where(names, key, sid)
        if where == "1 = 0":
            continue
        try:
            safe = table.replace(chr(34), chr(34) * 2)
            return conn.execute(
                f'SELECT COUNT(*) FROM "{safe}" WHERE {where}', params
            ).fetchone()[0]
        except sqlite3.Error:
            continue
    return None


def _textish(col, typ):
    """判断列是否可能是文本/JSON 列（用于标记扫描）。"""
    if typ and any(t in typ.upper() for t in ("TEXT", "VARCHAR", "CHAR", "JSON", "BLOB")):
        return True
    low = col.lower()
    return any(h in low for h in TEXTISH_HINTS)


def has_marker(conn, cols, key, sid, marker):
    """在目标会话相关的文本列中查找 pendingDeliveryNotice / tombstone 标记。"""
    for table, colinfo in cols.items():
        low = table.lower()
        if not any(h in low for h in MARKER_TABLE_HINTS):
            continue
        names = [c[0] for c in colinfo]
        where, params = session_where(names, key, sid)
        if where == "1 = 0":
            continue
        text_cols = [c[0] for c in colinfo if _textish(c[0], c[1])]
        for col in text_cols:
            try:
                safe = table.replace(chr(34), chr(34) * 2)
                row = conn.execute(
                    f'SELECT 1 FROM "{safe}" WHERE {where} AND CAST("{col}" AS TEXT) LIKE :m LIMIT 1',
                    {**params, "m": f"%{marker}%"},
                ).fetchone()
            except sqlite3.Error:
                continue
            if row:
                return True
    return False


def openclaw_version(cfg):
    """取 OpenClaw 版本；命令不可用返回 unknown，不阻断监控。"""
    rc, out, err = run_cmd([cfg["openclaw_bin"], "--version"], timeout=30)
    for text in (out, err):
        if rc == 0 and text.strip():
            return text.strip().splitlines()[0]
    return "unknown"


def append_snapshot(cfg, snap):
    """把卡死会话快照追加到 data/session-snapshots.log（JSON Lines）。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), cfg["snapshot_log"])
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(snap, ensure_ascii=False) + "\n")


def build_session_alert(cfg, s, err_lines):
    lines = [
        "【OpenClaw 会话卡死告警】",
        f"时间：{beijing_iso()}",
        f"会话：{s.get('session_key') or s.get('session_id')}",
        f"状态：running 且连续 {cfg['idle_minutes']} 分钟无活动",
        f"上次活动：{s.get('activity_raw')}",
        f"事件数：{s.get('event_count')}",
        f"版本：{s.get('openclaw_version')} / DB schema v{s.get('db_schema_version')}",
    ]
    if err_lines:
        lines.append(f"dispatch 错误（近{cfg['journalctl_window_minutes']}分钟）：{len(err_lines)} 条")
        lines.extend(err_lines[-3:])
    return "\n".join(lines)


def build_dispatch_alert(cfg, err_lines):
    lines = [
        "【OpenClaw dispatch 错误告警】",
        f"时间：{beijing_iso()}",
        f"journalctl 近 {cfg['journalctl_window_minutes']} 分钟出现 "
        f"'{cfg['dispatch_error_pattern']}' {len(err_lines)} 次（阈值 >= "
        f"{cfg['dispatch_error_threshold']}）",
    ]
    lines.extend(err_lines[-3:])
    return "\n".join(lines)


def build_recovery(state_item):
    """根据此前告警类型生成「已恢复」消息。"""
    who = state_item.get("session_key") or state_item.get("session_id")
    if state_item.get("kind") == "dispatch":
        return "【OpenClaw dispatch 错误已恢复】最近窗口内未再出现 'changed while starting work'。"
    return f"【OpenClaw 会话已恢复】{who} 已不再处于卡死状态。"


def since_iso(iso, now):
    """解析状态文件里的告警时间，用于冷却判断；无法解析视为已过冷却。"""
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BEIJING_TZ)
        return now - dt
    except (TypeError, ValueError):
        return timedelta(days=1)


def main():
    parser = argparse.ArgumentParser(description="OpenClaw 会话卡死只读监控（单次运行，供 cron 调用）")
    parser.add_argument("--config", help="config.json 路径（默认取脚本同级 config.json）")
    args = parser.parse_args()
    cfg = load_config(DEFAULTS, args.config)
    now = beijing_now()
    idle_delta = timedelta(minutes=cfg["idle_minutes"])
    cooldown = timedelta(minutes=cfg["cooldown_minutes"])
    webhook = os.environ.get("FEISHU_WEBHOOK_URL", "")
    open_id = os.environ.get("FEISHU_ALERT_OPEN_ID", "")
    timeout = cfg.get("http_timeout_seconds", 10)

    state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), cfg["state_file"])
    state = {}
    if os.path.isfile(state_path):
        with open(state_path, encoding="utf-8") as fp:
            try:
                state = json.load(fp)
            except json.JSONDecodeError:
                print(f"[监控] 状态文件损坏，重建: {state_path}", file=sys.stderr)
                state = {}

    err_lines, err_note = journalctl_errors(cfg)
    if err_note:
        print(f"[监控] {err_note}", file=sys.stderr)

    stuck = []
    user_version = None
    db_failed = False
    try:
        with closing(open_readonly(cfg["db_path"])) as conn:
            tables, cols = db_meta(conn)
            user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            stuck = find_stuck_sessions(conn, tables, cols, idle_delta, now)
            for s in stuck:
                s["event_count"] = count_session_events(
                    conn, cols, s.get("session_key"), s.get("session_id")
                )
                s["pending_delivery_notice"] = has_marker(
                    conn, cols, s.get("session_key"), s.get("session_id"), "pendingDeliveryNotice"
                )
                s["tombstone"] = has_marker(
                    conn, cols, s.get("session_key"), s.get("session_id"), "tombstone"
                )
    except Exception as exc:
        print(f"[监控] 打开只读数据库失败（本次跳过 DB 检测，保留告警状态）: {exc}", file=sys.stderr)
        stuck = []
        db_failed = True

    version = openclaw_version(cfg)

    # 快照：每次检测到卡死都追加（不受告警冷却影响）
    for s in stuck:
        append_snapshot(cfg, {
            "ts": beijing_iso(now),
            "session_key": s.get("session_key"),
            "session_id": s.get("session_id"),
            "status": s.get("status"),
            "event_count": s.get("event_count"),
            "last_activity": s.get("last_activity"),
            "activity_raw": str(s.get("activity_raw")),
            "idle_minutes": cfg["idle_minutes"],
            "openclaw_version": version,
            "db_schema_version": user_version,
            "journalctl_errors": err_lines[-3:],
            "pending_delivery_notice": s.get("pending_delivery_notice"),
            "tombstone": s.get("tombstone"),
        })

    # 告警（带冷却）与恢复
    active_keys = set()
    for s in stuck:
        k = s["alert_key"]
        active_keys.add(k)
        if k in state and since_iso(state[k].get("alerted_at"), now) < cooldown:
            continue  # 冷却期内不重复告警
        msg = build_session_alert(cfg, s, err_lines)
        ok, note = send_feishu(msg, webhook, open_id, timeout)
        if ok:
            state[k] = {
                "alerted_at": beijing_iso(now),
                "kind": "session",
                "session_key": s.get("session_key") or s.get("session_id"),
            }
            print(f"[告警] 已发送飞书: {k}", file=sys.stderr)
        else:
            # 发送失败不记录 alerted_at，冷却外保留重试机会
            print(f"[告警] 发送失败({note})，原文如下:\n{msg}", file=sys.stderr)

    dispatch_active = len(err_lines) >= cfg["dispatch_error_threshold"]
    if dispatch_active:
        k = "__dispatch_errors__"
        active_keys.add(k)
        if k not in state or since_iso(state[k].get("alerted_at"), now) >= cooldown:
            msg = build_dispatch_alert(cfg, err_lines)
            ok, note = send_feishu(msg, webhook, open_id, timeout)
            if ok:
                state[k] = {"alerted_at": beijing_iso(now), "kind": "dispatch"}
                print("[告警] 已发送飞书: dispatch 错误", file=sys.stderr)
            else:
                # 发送失败不记录 alerted_at，冷却外保留重试机会
                print(f"[告警] 发送失败({note})，原文如下:\n{msg}", file=sys.stderr)

    for k in list(state):
        if k in active_keys:
            continue
        if db_failed:
            # DB 读取失败：无法确认会话是否真正恢复，跳过恢复通知、保留告警状态（fail-closed）
            print(f"[监控] DB 读取失败，跳过恢复通知（保留告警状态）: {k}", file=sys.stderr)
            continue
        msg = build_recovery(state[k])
        ok, note = send_feishu(msg, webhook, open_id, timeout)
        if ok:
            del state[k]
            print(f"[恢复] 已发送飞书: {k}", file=sys.stderr)
        else:
            # 发送失败则保留状态，下次继续补发恢复通知
            print(f"[恢复] 发送失败({note})，稍后重试: {k}", file=sys.stderr)

    write_json_atomic(state_path, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
