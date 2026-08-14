#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenClaw 会话卡死一键修复（默认 dry-run，--execute 才真删）。

流程：
1. 备份数据库（sqlite backup API 在线一致性快照，保留 7 天）；
2. 官方命令优先：openclaw sessions delete <key> --yes（走 RPC，需 gateway 在跑）；
3. 官方命令失败才停服 + sqlite 兜底：动态枚举全部表，找出含 session_id/session_key
   列的表按目标会话清引用（不硬编码表清单），保留 conversations 主会话种子；
4. PRAGMA foreign_key_check 必须为 0，否则中止并提示回滚；
5. 重启服务并验证（journalctl 无新 dispatch 错误 + openclaw sessions list 确认恢复）。

安全：默认只打印将要执行的操作；任何异常退出码非 0 并给出回滚方法。
敏感信息：一律从环境变量读取，脚本不硬编码。
"""

import argparse
import json
import os
import sqlite3
import sys
import time

from openclaw_common import (
    beijing_now,
    db_meta,
    load_config,
    open_readonly,
    open_rw,
    run_cmd,
    session_where,
    step,
)


DEFAULTS = {
    "db_path": "/home/ubuntu/.openclaw/agents/main/agent/openclaw-agent.sqlite",
    "data_dir": "/home/ubuntu/.openclaw",
    "openclaw_bin": "openclaw",
    "service": "openclaw-gateway.service",
    "backup_keep_days": 7,
    "verify_wait_seconds": 10,
    "journalctl_window_minutes": 5,
    "dispatch_error_pattern": "changed while starting work",
}

# conversations 种子保留：thread_id 为 NULL 且 peer 匹配；peer 列按这些关键字在会话表里找
PEER_HINTS = ("peer", "contact", "jid", "username", "nickname", "chat_id")
SEED_SESSION_TABLES = ("session_nodes", "session_windows", "session_conversations", "session_members")


def parse_args():
    parser = argparse.ArgumentParser(description="OpenClaw 会话卡死一键修复")
    parser.add_argument("--session-key", required=True, help="目标卡死会话的 session_key（必填）")
    parser.add_argument("--execute", action="store_true", help="真删开关；不加只做 dry-run 预演")
    parser.add_argument("--config", help="config.json 路径（默认取脚本同级 config.json）")
    parser.add_argument("--db-path", help="覆盖数据库路径（测试用）")
    parser.add_argument("--data-dir", help="覆盖数据目录（测试用）")
    parser.add_argument("--service", help="覆盖 systemd 服务名（测试用）")
    return parser.parse_args()


def do_backup(cfg, dry):
    """用 sqlite backup API 做一致性备份（gateway 运行中也可用），并按保留天数清理旧备份。"""
    backup_dir = os.path.join(cfg["data_dir"], "backups")
    dest = os.path.join(
        backup_dir, f"openclaw-agent.sqlite.bak-{beijing_now().strftime('%Y%m%d-%H%M%S')}"
    )
    step(f"备份数据库：{cfg['db_path']} → {dest}", dry)
    if dry:
        prune_backups(cfg, backup_dir, dry)
        return dest
    try:
        os.makedirs(backup_dir, exist_ok=True)
        src = open_readonly(cfg["db_path"])
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    except Exception as exc:
        step(f"备份失败（中止，未改动任何数据）: {exc}")
        return None
    step(f"备份完成：{dest}（{os.path.getsize(dest)} 字节）")
    prune_backups(cfg, backup_dir, dry)
    return dest


def prune_backups(cfg, backup_dir, dry):
    """删除超过保留天数的备份文件。"""
    if not os.path.isdir(backup_dir):
        return
    cutoff = time.time() - cfg["backup_keep_days"] * 86400
    for name in sorted(os.listdir(backup_dir)):
        path = os.path.join(backup_dir, name)
        if name.startswith("openclaw-agent.sqlite.bak-") and os.path.getmtime(path) < cutoff:
            step(f"删除过期备份（保留 {cfg['backup_keep_days']} 天）: {path}", dry)
            if not dry:
                os.remove(path)


def official_delete(cfg, key, dry):
    """官方命令优先：openclaw sessions delete <key> --yes；成功返回 True，失败返回 False。"""
    cmd = [cfg["openclaw_bin"], "sessions", "delete", key, "--yes"]
    step(f"尝试官方命令：{' '.join(cmd)}", dry)
    if dry:
        # dry-run 也至少探测一次官方命令可用性（只读 list --json，不执行删除）
        probe_cmd = [cfg["openclaw_bin"], "sessions", "list", "--json"]
        rc, out, err = run_cmd(probe_cmd, timeout=60)
        if rc == 0:
            step(f"官方命令可用性探测通过：{' '.join(probe_cmd)} (rc=0)")
        else:
            step(f"官方命令可用性探测失败（rc={rc}）：{(err or out).strip()[:200]}")
        return None
    rc, out, err = run_cmd(cmd, timeout=120)
    detail = (out + err).strip()
    step(f"官方命令返回码 {rc}：{detail[-500:]}")
    if rc == 0:
        return True
    step("官方命令失败，转入 sqlite 兜底流程")
    return False


def service_active(cfg):
    rc, out, _ = run_cmd(["systemctl", "is-active", cfg["service"]], timeout=30)
    return rc == 0 and out.strip() == "active"


def stop_service(cfg, dry):
    step(f"停止服务：systemctl stop {cfg['service']}", dry)
    if dry:
        return True
    rc, out, err = run_cmd(["systemctl", "stop", cfg["service"]], timeout=120)
    if rc != 0:
        step(f"停止服务失败：{(err or out).strip()[:200]}")
        return False
    for _ in range(10):
        if not service_active(cfg):
            step("服务已停止（inactive）")
            return True
        time.sleep(1)
    step("等待服务停止超时")
    return False


def start_service(cfg, dry):
    step(f"启动服务：systemctl start {cfg['service']}", dry)
    if dry:
        return True
    rc, out, err = run_cmd(["systemctl", "start", cfg["service"]], timeout=120)
    if rc != 0:
        step(f"启动失败：{(err or out).strip()[:200]}")
        return False
    for _ in range(cfg.get("verify_wait_seconds", 10)):
        if service_active(cfg):
            step("服务已启动（active）")
            return True
        time.sleep(1)
    step("等待服务启动超时")
    return False


def find_peer(conn, cols, key, sid):
    """在会话表里找 peer 列及值（conversations 种子保留依据）。"""
    for table in SEED_SESSION_TABLES:
        if table not in cols:
            continue
        names = [c[0] for c in cols[table]]
        where, params = session_where(names, key, sid)
        if where == "1 = 0":
            continue
        for col in names:
            if not any(h in col.lower() for h in PEER_HINTS):
                continue
            try:
                safe = table.replace(chr(34), chr(34) * 2)
                safe_col = col.replace(chr(34), chr(34) * 2)
                row = conn.execute(
                    f'SELECT "{safe_col}" FROM "{safe}" WHERE {where} LIMIT 1', params
                ).fetchone()
            except sqlite3.Error:
                continue
            if row and row[0] not in (None, ""):
                return col, row[0]
    return None, None


def delete_from_table(conn, table, where, params):
    """普通 DELETE；FTS5 等虚拟表不支持时降级尝试 'delete' 命令，仍失败则返回错误说明。"""
    safe = table.replace(chr(34), chr(34) * 2)
    try:
        n = conn.execute(f'DELETE FROM "{safe}" WHERE {where}', params).rowcount
        return None, n
    except sqlite3.Error as exc:
        # 普通 DELETE 对常规 FTS5 表已生效（sqlite 3.53 实测支持），此处只会命中
        # contentless/external-content 等不支持 DELETE 的虚拟表：这类表不存内容列，
        # FTS5 的 'delete' 特殊 INSERT 只需 (表名, rowid)，无需也不能补内容列值。
        try:
            rows = conn.execute(f'SELECT rowid FROM "{safe}" WHERE {where}', params).fetchall()
            for (rid,) in rows:
                conn.execute(
                    f"INSERT INTO \"{safe}\"(\"{safe}\", rowid) VALUES('delete', ?)", (rid,)
                )
            return None, len(rows)
        except sqlite3.Error as exc2:
            return f"{table}: {exc2}", 0


def cleanup_conversations(conn, cols, key, sid, where, params):
    """清 conversations 对目标会话的引用，但保留主会话种子（thread_id IS NULL 且 peer 匹配）。

    where/params 由调用方用 table_where 生成（含映射防护），与 dry-run 计数口径一致；
    不再在函数内另起 session_where，避免 OR 条件绕过映射防护而误删其他会话。
    """
    if "conversations" not in cols:
        return None
    names = [c[0] for c in cols["conversations"]]
    if where == "1 = 0":
        return "conversations 无 session 列，跳过"
    if "thread_id" not in names:
        n = conn.execute(f'DELETE FROM "conversations" WHERE {where}', params).rowcount
        return f"已清理 conversations: {n} 行（无 thread_id 列，未保留种子）"
    peer_col, peer_val = find_peer(conn, cols, key, sid)
    if peer_col and peer_val:
        params["peer"] = peer_val
        safe_peer = peer_col.replace(chr(34), chr(34) * 2)
        sql = (
            f'DELETE FROM "conversations" WHERE {where} '
            f'AND NOT (thread_id IS NULL AND "{safe_peer}" = :peer)'
        )
        note = f"保留种子：thread_id IS NULL 且 {peer_col}={peer_val!r}"
    else:
        sql = 'DELETE FROM "conversations" WHERE {where} AND NOT (thread_id IS NULL)'.format(
            where=where
        )
        note = "保留种子：thread_id IS NULL（未确认 peer，保守保留全部 NULL-thread 行）"
    n = conn.execute(sql, params).rowcount
    return f"已清理 conversations: {n} 行（{note}）"


def table_where(conn, table, names, key, sid):
    """按表生成删除条件。同时含 session_key/session_id 两列时，先核对该表里
    session_key 对应的 session_id 是否等于目标 sid；不一致或缺失则只按 session_key
    删除，避免 OR 条件误删其他会话。"""
    if "session_key" in names and "session_id" in names and sid:
        safe = table.replace(chr(34), chr(34) * 2)
        try:
            mapped = conn.execute(
                f'SELECT DISTINCT "session_id" FROM "{safe}" WHERE "session_key" = :key',
                {"key": key},
            ).fetchall()
        except sqlite3.Error:
            mapped = []
        mapped_sids = {r[0] for r in mapped if r[0] is not None}
        if mapped_sids == {sid}:
            return session_where(names, key, sid)
        # 映射缺失或不等于目标 sid：只按 session_key 删除
        return '"session_key" = :key', {"key": key}
    return session_where(names, key, sid)


def cleanup_db(cfg, key, dry):
    """动态枚举含 session_id/session_key 列的表并清引用；返回外键校验是否通过。"""
    conn = (open_readonly if dry else open_rw)(cfg["db_path"])
    try:
        try:
            tables, cols = db_meta(conn)
        except sqlite3.Error as exc:
            step(f"枚举数据库结构失败: {exc}")
            return False
        candidates = sorted(
            t for t, c in cols.items()
            if "session_key" in [x[0] for x in c] or "session_id" in [x[0] for x in c]
        )
        step(f"枚举含 session 列的表 {len(candidates)} 张：{', '.join(candidates)}", dry)

        sid = None
        for table in ("session_nodes", "session_windows", "session_conversations"):
            if table not in cols:
                continue
            names = [c[0] for c in cols[table]]
            if "session_id" not in names:
                continue
            if "session_key" not in names:
                # 无 session_key 列，无法按 key 解析 session_id，跳过该表
                continue
            safe = table.replace(chr(34), chr(34) * 2)
            row = conn.execute(
                f'SELECT "session_id" FROM "{safe}" WHERE "session_key" = :key LIMIT 1',
                {"key": key},
            ).fetchone()
            if row:
                sid = row[0]
                break
        step(f"目标 session_key={key}，解析到 session_id={sid or '未找到（仅按 key 清理）'}", dry)

        if dry:
            for table in candidates:
                names = [c[0] for c in cols[table]]
                where, params = table_where(conn, table, names, key, sid)
                safe = table.replace(chr(34), chr(34) * 2)
                n = conn.execute(
                    f'SELECT COUNT(*) FROM "{safe}" WHERE {where}', params
                ).fetchone()[0]
                step(f"将清理 {table}: {n} 行", dry)

            # 只读预演：现有外键违规基线 + 外键引用候选表但不在清理清单的子表（漏删风险）
            fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk_rows:
                step(f"预演提示：当前库已有 {len(fk_rows)} 条外键违规（清理前基线）", dry)
                for row in fk_rows[:10]:
                    step(f"  现有违规: {row}", dry)
            for table in sorted(tables):
                if table in candidates:
                    continue
                safe = table.replace(chr(34), chr(34) * 2)
                try:
                    refs = conn.execute(f'PRAGMA foreign_key_list("{safe}")').fetchall()
                except sqlite3.Error:
                    continue
                for ref in refs:
                    if ref[2] in candidates:
                        step(
                            f"预演提示：子表 {table} 外键引用候选表 {ref[2]}，"
                            f"若其行仍指向目标会话可能产生违规（漏删风险）",
                            dry,
                        )
                        break
            step("执行模式将做：事务内清引用 → PRAGMA foreign_key_check=0 校验 → 启动服务 → 验证", dry)
            return True

        conn.execute("BEGIN")
        manual = []
        try:
            for table in candidates:
                names = [c[0] for c in cols[table]]
                where, params = table_where(conn, table, names, key, sid)
                if table == "conversations":
                    note = cleanup_conversations(conn, cols, key, sid, where, params)
                    if note:
                        step(note)
                    continue
                warning, n = delete_from_table(conn, table, where, params)
                if warning:
                    manual.append(warning)
                elif n:
                    step(f"已清理 {table}: {n} 行")

            # 虚拟表/FTS5 清理失败 → fail-closed：必须在 commit 之前输出清单并回滚，
            # 清理不完整不得当作成功，也不得照常 commit 后继续启动服务。
            if manual:
                conn.rollback()
                step(
                    f"======== 待人工处理清单（虚拟表/FTS5 清理失败，共 {len(manual)} 张），"
                    f"已回滚中止 ========"
                )
                for item in manual:
                    step(f"  待人工处理: {item}")
                return False

            # 外键校验必须在事务内、commit 之前进行；违规即回滚
            fk = conn.execute("PRAGMA foreign_key_check").fetchall()
            if fk:
                conn.rollback()
                step(f"PRAGMA foreign_key_check 返回 {len(fk)} 条违规，事务已回滚，中止！")
                for row in fk[:10]:
                    step(f"  违规: {row}")
                return False
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            step(f"清理异常，事务已回滚: {exc}")
            return False

        step("PRAGMA foreign_key_check = 0，外键完整")
        return True
    except sqlite3.Error as exc:
        step(f"数据库清理异常（已中止，未改动数据）: {exc}")
        return False
    finally:
        conn.close()


def extract_session_keys(data, key_fields=None):
    """从 openclaw sessions list --json 的输出里提取会话 key 列表（兼容常见结构）。

    key_fields 用于限定只取 session_key 类字段（verify 时避免与 session_id 混用）；
    默认同时兼容 session_key / session_id 等多种字段。
    """
    if key_fields is None:
        key_fields = ("session_key", "key", "sessionKey", "session_id", "sessionId", "id")
    if isinstance(data, dict):
        for k in ("sessions", "data", "items", "result"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
        else:
            return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict):
            for k in key_fields:
                if item.get(k) is not None:
                    out.append(str(item[k]))
                    break
        elif isinstance(item, str):
            out.append(item)
    return out


def verify(cfg, key, dry):
    """验证：服务 active + journalctl 无新 dispatch 错误 + sessions list 中目标会话已移除。"""
    if dry:
        step(
            f"执行模式将验证：systemctl is-active；journalctl 检查 dispatch 错误；"
            f"{cfg['openclaw_bin']} sessions list --json 确认 {key} 已移除",
            dry,
        )
        return True
    time.sleep(cfg.get("verify_wait_seconds", 10))
    if not service_active(cfg):
        step("服务未处于 active")
        return False
    step("服务状态：active")

    rc, out, err = run_cmd(
        ["journalctl", "-u", cfg["service"], "--since",
         f"{cfg['journalctl_window_minutes']} minutes ago", "--no-pager"],
        timeout=60,
    )
    lines = [l for l in out.splitlines() if cfg["dispatch_error_pattern"] in l]
    step(f"journalctl 近 {cfg['journalctl_window_minutes']} 分钟 dispatch 错误：{len(lines)} 条")
    if lines:
        for l in lines[-3:]:
            step(f"  {l[:200]}")
        return False

    for attempt in range(1, 4):
        rc, out, err = run_cmd([cfg["openclaw_bin"], "sessions", "list", "--json"], timeout=120)
        if rc != 0:
            time.sleep(5)
            continue
        try:
            # 只用 session_key 比对（不与 session_id 混用）
            keys = extract_session_keys(
                json.loads(out), key_fields=("session_key", "key", "sessionKey")
            )
        except (json.JSONDecodeError, TypeError) as exc:
            step(f"sessions list JSON 解析失败（第 {attempt} 次）: {exc}")
            time.sleep(5)
            continue
        if not keys:
            # 列表里只有 session_id 或根本没有 session_key，无法确认是否移除 → 视为未验证
            step(
                f"openclaw sessions list：未提取到 session_key 字段，"
                f"无法确认 {key} 是否移除（未验证）"
            )
            return False
        present = str(key) in keys
        step(
            f"openclaw sessions list：目标会话 {key} 是否仍在："
            f"{'是（未恢复）' if present else '否（已移除）'}"
        )
        return not present
    step(f"openclaw sessions list 连续失败：{(err or '').strip()[-200:]}")
    return False


def print_rollback(cfg, backup_path):
    """异常中止时给出回滚方法。"""
    step("======== 回滚方法 ========")
    step(f"1) systemctl stop {cfg['service']}")
    step(f"2) cp {backup_path} {cfg['db_path']}")
    step(f"3) systemctl start {cfg['service']}")


def main():
    args = parse_args()
    cfg = load_config(DEFAULTS, args.config)
    for attr, key in (("db_path", "db_path"), ("data_dir", "data_dir"), ("service", "service")):
        if getattr(args, attr):
            cfg[key] = getattr(args, attr)
    dry = not args.execute
    key = args.session_key.strip()
    if not key:
        step("错误：--session-key 不能为空")
        return 2
    if not os.path.isfile(cfg["db_path"]):
        step(f"错误：数据库不存在 {cfg['db_path']}")
        return 2

    step(f"开始处理卡死会话 {key}（模式：{'dry-run 预演' if dry else '执行'}）", dry)

    backup_path = do_backup(cfg, dry)
    if backup_path is None:
        return 1

    official = official_delete(cfg, key, dry)
    if official is True:
        step("官方命令删除成功，无需 sqlite 兜底")
        return 0 if verify(cfg, key, dry) else 1

    if not stop_service(cfg, dry):
        step("停止服务失败，中止（数据库未改动）")
        return 1

    if not cleanup_db(cfg, key, dry):
        step("数据库清理未通过校验，中止")
        start_service(cfg, False)
        print_rollback(cfg, backup_path)
        return 1

    if not start_service(cfg, dry):
        step("重启服务失败，需人工介入")
        print_rollback(cfg, backup_path)
        return 1

    if verify(cfg, key, dry):
        step("验证通过：会话已恢复，dispatch 无新错误")
        return 0
    step("验证未通过，请按回滚方法处理")
    print_rollback(cfg, backup_path)
    return 1


if __name__ == "__main__":
    sys.exit(main())
