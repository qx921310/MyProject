#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenClaw 会话治理脚本共享工具（仅标准库，无第三方依赖）。"""

import json
import os
import sqlite3
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import quote


BEIJING_TZ = timezone(timedelta(hours=8))


def beijing_now():
    """当前北京时间（UTC+8），脚本所有输出与状态时间戳统一使用。"""
    return datetime.now(BEIJING_TZ)


def beijing_iso(dt=None):
    """北京时间 ISO 字符串（秒精度）。"""
    return (dt or beijing_now()).isoformat(timespec="seconds")


def operator():
    """当前操作人：优先取 sudo 调用者，其次普通用户。"""
    return (
        os.environ.get("SUDO_USER")
        or os.environ.get("USER")
        or os.environ.get("LOGNAME")
        or "unknown"
    )


def step(msg, dry_run=False):
    """打印带操作人 + 北京时间的步骤信息。"""
    prefix = "(dry-run) " if dry_run else ""
    print(f"[{beijing_iso()}] 操作人:{operator()} {prefix}{msg}")


def script_dir():
    """脚本同级目录。"""
    return os.path.dirname(os.path.abspath(__file__))


def load_config(defaults, config_path=None):
    """配置加载：内置默认值 + 同级 config.json（可被 --config 指定路径覆盖）。"""
    cfg = dict(defaults)
    if not config_path:
        config_path = os.path.join(script_dir(), "config.json")
    if os.path.isfile(config_path):
        with open(config_path, encoding="utf-8") as fp:
            cfg.update(json.load(fp))
    return cfg


def run_cmd(cmd, timeout=60):
    """执行外部命令，返回 (返回码, stdout, stderr)；命令缺失/超时也返回可读错误。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"命令不存在: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"命令超时（{timeout}秒）: {' '.join(cmd)}"


def open_readonly(db_path):
    """只读打开 sqlite（mode=ro URI + query_only 双保险），绝不写运行中的 gateway 库。"""
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    conn = sqlite3.connect(f"file:{quote(os.path.abspath(db_path))}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def open_rw(db_path):
    """以 rw 模式打开 sqlite（用于停服后的清理）；显式启用外键约束，删除阶段即受保护。"""
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    conn = sqlite3.connect(f"file:{quote(os.path.abspath(db_path))}?mode=rw", uri=True)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def db_meta(conn):
    """枚举全部表及其列：返回 (表名集合, {表名: [(列名, 类型), ...]})。"""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cols = {}
    for t in tables:
        safe = t.replace('"', '""')
        cols[t] = [(r[1], r[2]) for r in conn.execute(f'PRAGMA table_info("{safe}")')]
    return tables, cols


def session_where(cols, key, sid):
    """按目标会话拼 WHERE：优先 session_key，其次 session_id；值一律走参数绑定。"""
    parts, params = [], {}
    if "session_key" in cols:
        parts.append('"session_key" = :key')
        params["key"] = key
    if "session_id" in cols and sid:
        parts.append('"session_id" = :sid')
        params["sid"] = sid
    if not parts:
        return "1 = 0", params
    return "(" + " OR ".join(parts) + ")", params


def write_json_atomic(path, data):
    """原子写 JSON 状态文件（先写临时文件再 rename）。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def send_feishu(text, webhook_url, open_id, timeout=10):
    """发送飞书文本消息并 @ 指定 open_id；返回 (是否成功, 说明)。"""
    if not webhook_url:
        return False, "未设置 FEISHU_WEBHOOK_URL（请配置环境变量）"
    if open_id:
        text = f'<at user_id="{open_id}">@KOKO</at> {text}'
    payload = json.dumps({"msg_type": "text", "content": {"text": text}}).encode("utf-8")
    try:
        req = urllib.request.Request(
            webhook_url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
        ok = False
        try:
            obj = json.loads(body)
            if isinstance(obj, dict):
                ok = obj.get("code") == 0 or obj.get("StatusCode") == 0
        except (json.JSONDecodeError, ValueError):
            ok = False
        if not ok:
            # 兼容非严格 JSON 响应：同时识别飞书自定义机器人的 StatusCode 与常规机器人的 code
            ok = (
                '"code":0' in body or '"code": 0' in body
                or '"StatusCode":0' in body or '"StatusCode": 0' in body
            )
        return ok, body[:200]
    except (urllib.error.URLError, OSError) as exc:
        return False, str(exc)
