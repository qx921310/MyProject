#!/usr/bin/env python3
"""Local credential vault backed by GPG AES-256 symmetric encryption.

Plaintext values are only ever kept in memory.  GPG passphrases are passed
via a dedicated file descriptor (never as a command-line argument) and values
are encrypted/decrypted through stdin/stdout, so no plaintext temp file is
ever created.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


GPG_BIN = os.environ.get("GPG_BIN", "gpg")
VAULT_DIR = Path.home() / ".vault"
ENTRIES_DIR = VAULT_DIR / "entries"
VAULT_DIR_MODE = 0o700
ENTRY_FILE_MODE = 0o600
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class VaultError(Exception):
    """User-facing error; its message is safe to print."""


def _getpass(prompt: str) -> str:
    """Read a secret with echo disabled via getpass (never shell history)."""
    value = getpass.getpass(prompt)
    if not value:
        raise VaultError("输入不能为空")
    return value


def _input(prompt: str) -> str:
    value = input(prompt).strip()
    if not value:
        raise VaultError("输入不能为空")
    return value


def _ensure_store_dirs() -> None:
    """Create ~/.vault and ~/.vault/entries with mode 700."""
    try:
        VAULT_DIR.mkdir(mode=VAULT_DIR_MODE, parents=True, exist_ok=True)
        ENTRIES_DIR.mkdir(mode=VAULT_DIR_MODE, parents=True, exist_ok=True)
    except OSError as exc:
        raise VaultError(f"无法创建存储目录: {exc}") from exc

    for directory in (VAULT_DIR, ENTRIES_DIR):
        try:
            os.chmod(directory, VAULT_DIR_MODE)
        except OSError as exc:
            raise VaultError(f"无法设置目录权限 {directory}: {exc}") from exc


def _entry_path(name: str) -> Path:
    if not NAME_RE.fullmatch(name):
        raise VaultError("条目名只能包含字母、数字、点、下划线和连字符，且不能以符号开头")
    return ENTRIES_DIR / f"{name}.gpg"


def _gpg_cmd(operation: list[str], passphrase_fd: int) -> list[str]:
    return [
        GPG_BIN,
        "--no-options",
        "--batch",
        "--yes",
        "--no-tty",
        "--quiet",
        "--pinentry-mode",
        "loopback",
        "--passphrase-fd",
        str(passphrase_fd),
        "--output",
        "-",
        *operation,
    ]


def _run_gpg(operation: list[str], data: bytes, passphrase: str) -> bytes:
    """Run GPG with the passphrase on fd 3 and data on stdin/stdout."""
    pass_r, pass_w = os.pipe()
    try:
        try:
            proc = subprocess.Popen(
                _gpg_cmd(operation, pass_r),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(pass_r,),
                close_fds=True,
            )
        except FileNotFoundError as exc:
            raise VaultError("未找到 gpg 可执行文件，请确认系统已安装 GPG") from exc
        except OSError as exc:
            raise VaultError(f"无法启动 GPG: {exc}") from exc
    finally:
        # pass_r is inherited by the child; close it in the parent immediately.
        os.close(pass_r)

    try:
        try:
            with os.fdopen(pass_w, "wb", closefd=True) as pass_stream:
                pass_stream.write(passphrase.encode("utf-8"))
        except BrokenPipeError:
            # GPG may fail early (for example an invalid option); fall through
            # to communicate() and use the return code for a clean error.
            pass

        stdout, stderr = proc.communicate(input=data)

        if proc.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            # GPG messages should not contain plaintext, but keep errors
            # deliberately generic so they can never leak a value.
            if any("decrypt" in arg for arg in operation) and message:
                lowered = message.lower()
                if "session key" in lowered or "decryption failed" in lowered:
                    raise VaultError("主密码错误或条目数据已损坏")
            if message:
                raise VaultError(f"GPG 操作失败: {message}")
            raise VaultError("GPG 操作失败")
        return stdout
    finally:
        try:
            os.close(pass_w)
        except OSError:
            pass


def _encrypt(plaintext: bytes, passphrase: str) -> bytes:
    return _run_gpg(["--symmetric", "--cipher-algo", "AES256"], plaintext, passphrase)


def _decrypt(ciphertext: bytes, passphrase: str) -> bytes:
    return _run_gpg(["--decrypt"], ciphertext, passphrase)


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ciphertext atomically with mode 600; no plaintext ever touches disk."""
    _ensure_store_dirs()
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp_path, ENTRY_FILE_MODE)
        os.replace(tmp_path, path)
        os.chmod(path, ENTRY_FILE_MODE)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _prompt_new_passphrase() -> str:
    first = _getpass("设置主密码（不可见）: ")
    second = _getpass("再次输入主密码: ")
    if first != second:
        raise VaultError("两次输入的主密码不一致")
    return first


def _prompt_passphrase() -> str:
    return _getpass("主密码: ")


def _load_entry(name: str) -> tuple[dict[str, Any], bytes]:
    path = _entry_path(name)
    if not path.exists():
        raise VaultError(f"条目不存在: {name}")
    try:
        ciphertext = path.read_bytes()
    except OSError as exc:
        raise VaultError(f"无法读取条目 {name}: {exc}") from exc
    return _decode_entry(_decrypt(ciphertext, _prompt_passphrase()), name)


def _decode_entry(plaintext: bytes, name: str) -> tuple[dict[str, Any], bytes]:
    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VaultError(f"条目数据格式错误: {name}") from exc

    if not isinstance(payload, dict):
        raise VaultError(f"条目数据格式错误: {name}")
    value = payload.get("value")
    entry_type = payload.get("type", "password")
    if not isinstance(value, str):
        raise VaultError(f"条目数据缺少明文值: {name}")
    if entry_type not in {"password", "key", "account"}:
        entry_type = "password"
    return {"type": entry_type, "value": value}, plaintext


def _mask_value(entry_type: str, value: str) -> str:
    """Mask by type: passwords are fully hidden; keys/accounts keep head/tail."""
    if entry_type == "password":
        return "******"
    if len(value) <= 7:
        return "******"
    return f"{value[:3]}***{value[-4:]}"


def cmd_init(_: argparse.Namespace) -> int:
    _ensure_store_dirs()
    _prompt_new_passphrase()
    print("已初始化", VAULT_DIR)
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    _ensure_store_dirs()
    name = args.name
    path = _entry_path(name)
    if path.exists():
        raise VaultError(f"条目已存在: {name}")

    entry_type = _input("类型 (password/key/account): ").strip().lower()
    if entry_type not in {"password", "key", "account"}:
        raise VaultError("类型必须是 password、key 或 account")
    value = _getpass("值（不可见）: ")
    passphrase = _prompt_passphrase()

    payload = json.dumps({"type": entry_type, "value": value}, ensure_ascii=False)
    try:
        ciphertext = _encrypt(payload.encode("utf-8"), passphrase)
        _atomic_write(path, ciphertext)
    except OSError as exc:
        raise VaultError(f"无法写入条目 {name}: {exc}") from exc

    print(f"已添加 {name}")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    _ensure_store_dirs()
    entry, _ = _load_entry(args.name)
    print(_mask_value(entry["type"], entry["value"]))
    return 0


def cmd_reveal(args: argparse.Namespace) -> int:
    _ensure_store_dirs()
    entry, _ = _load_entry(args.name)
    print(entry["value"])
    return 0


def cmd_list(_: argparse.Namespace) -> int:
    _ensure_store_dirs()
    names = sorted(path.stem for path in ENTRIES_DIR.glob("*.gpg"))
    for name in names:
        print(name)
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    _ensure_store_dirs()
    path = _entry_path(args.name)
    if not path.exists():
        raise VaultError(f"条目不存在: {args.name}")
    try:
        path.unlink()
    except OSError as exc:
        raise VaultError(f"无法删除条目 {args.name}: {exc}") from exc
    print(f"已删除 {args.name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vault",
        description="本地 GPG AES-256 加密凭证库",
        epilog=(
            "示例:\n"
            "  vault init\n"
            "  vault add my-api-key\n"
            "  vault get my-api-key\n"
            "  vault reveal my-api-key\n"
            "  vault list\n"
            "  vault rm my-api-key\n\n"
            "主密码和值使用 getpass 输入，不可见且不会进入 shell history；"
            "明文仅保存在内存中，不写入磁盘。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_init = subparsers.add_parser("init", help="初始化存储目录并设置主密码")
    p_init.set_defaults(func=cmd_init)

    p_add = subparsers.add_parser("add", help="加密添加一个条目")
    p_add.add_argument("name", help="条目名称（仅允许字母、数字、点、下划线、连字符）")
    p_add.set_defaults(func=cmd_add)

    p_get = subparsers.add_parser("get", help="打码显示条目值")
    p_get.add_argument("name", help="条目名称")
    p_get.set_defaults(func=cmd_get)

    p_reveal = subparsers.add_parser("reveal", help="显式输出完整明文值")
    p_reveal.add_argument("name", help="条目名称")
    p_reveal.set_defaults(func=cmd_reveal)

    p_list = subparsers.add_parser("list", help="只列出条目名称")
    p_list.set_defaults(func=cmd_list)

    p_rm = subparsers.add_parser("rm", help="删除一个条目")
    p_rm.add_argument("name", help="条目名称")
    p_rm.set_defaults(func=cmd_rm)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except VaultError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
