#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模板渲染引擎（python3 stdlib，无第三方依赖）。

职责：
1. 读取合并后的环境变量文件（非敏感 .env + age 解密后的敏感字段，KEY=VALUE 行）；
2. 将 templates/ 下所有模板渲染为 {{VAR}} 占位符被替换的产物；
3. 派生变量（XRAY_PUBLIC_KEY 由 XRAY_PRIVATE_KEY 推导）；
4. 生成 sub64.txt（sub.txt 的 base64）；
5. 内建校验：占位符无残留、JSON 可解析、sub64 与 sub.txt 一致。

输出目录结构：
    <out>/systemd/proxy-*.service
    <out>/hysteria/config.yaml
    <out>/xray/config.json
    <out>/sub/<SUB_TOKEN>/{clash.yaml,sub.txt,sub64.txt}
    <out>/links/{subscription-links.txt,node-link.txt}
    <out>/client/*.yaml

用法：
    python3 render.py --env-file <env> --templates-dir <templates> --output-dir <out>
"""

import argparse
import base64
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from x25519 import derive_public_key  # noqa: E402


PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")

# 必需变量：缺失即报错（防止模板渲染出残缺配置）
REQUIRED_VARS = [
    "SERVER_IP",
    "HYSTERIA_PORT",
    "XRAY_PORT",
    "SUB_SERVER_PORT",
    "HYSTERIA_CERT",
    "HYSTERIA_KEY",
    "MASQUERADE_URL",
    "HYSTERIA_TLS_SNI",
    "REALITY_DEST",
    "REALITY_SERVER_NAME",
    "REALITY_FLOW",
    "CLIENT_FINGERPRINT",
    "SUB_BASE_URL",
    # 敏感字段（由 decrypt-secrets.sh 注入）
    "HYSTERIA_AUTH_PASSWORD",
    "XRAY_UUID",
    "XRAY_PRIVATE_KEY",
    "XRAY_SHORT_ID",
    "SUB_TOKEN",
]

# 订阅产物中属于「web 根目录」的文件 → 放入 <out>/sub/<SUB_TOKEN>/
WEB_ROOT_FILES = {"clash.yaml", "sub.txt"}
# links 文档 → 放入 <out>/links/（不在 web 根目录，避免 token URL 公开暴露）
LINKS_FILES = {"subscription-links.txt", "node-link.txt"}


def parse_env_file(path):
    """解析 KEY=VALUE 环境文件；后出现的同名键覆盖先前的；忽略 # 注释与空行。"""
    env = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            env[key] = value
    return env


def resolve_var(name, env):
    """取变量值；XRAY_PUBLIC_KEY 为派生变量，由私钥推导。"""
    if name == "XRAY_PUBLIC_KEY":
        return derive_public_key(env["XRAY_PRIVATE_KEY"])
    return env[name]


def render_text(text, env):
    """渲染单段文本；返回 (渲染结果, 缺失变量集合)。"""
    missing = set()

    def _repl(m):
        name = m.group(1)
        if name in env or name == "XRAY_PUBLIC_KEY":
            return resolve_var(name, env)
        missing.add(name)
        return m.group(0)

    return PLACEHOLDER_RE.sub(_repl, text), missing


def render_file(src_path, dst_path, env):
    """渲染单个模板文件；缺失变量或残留占位符即抛错。"""
    with open(src_path, encoding="utf-8") as f:
        text = f.read()
    rendered, missing = render_text(text, env)
    if missing:
        raise ValueError(
            "%s: 缺少变量: %s" % (src_path, ", ".join(sorted(missing)))
        )
    if PLACEHOLDER_RE.search(rendered):
        raise ValueError("%s: 渲染后仍残留 {{VAR}} 占位符" % src_path)
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    with open(dst_path, "w", encoding="utf-8") as f:
        f.write(rendered)
    return rendered


def _validate_json(path):
    """JSON 产物合法性校验。"""
    with open(path, encoding="utf-8") as f:
        json.load(f)


def render_all(templates_dir, output_dir, env, groups=None):
    """渲染全部模板（groups 可限定为 systemd/hysteria/xray/sub/client 子集）。

    返回渲染产物相对路径列表（用于部署与报告）。
    """
    groups = set(groups) if groups else None
    rendered = []
    token = env["SUB_TOKEN"]
    sub_txt_rendered = None

    for root, _dirs, files in os.walk(templates_dir):
        rel = os.path.relpath(root, templates_dir)
        if groups and rel != ".":
            group = rel.split(os.sep)[0]
            if group not in groups:
                continue
        for name in sorted(files):
            src = os.path.join(root, name)
            rel_src = os.path.relpath(src, templates_dir)
            if name == "sub64.txt":
                # sub64.txt 没有模板：由 sub.txt 渲染结果派生，跳过此处
                continue
            if name in LINKS_FILES:
                dst = os.path.join(output_dir, "links", name)
            elif name in WEB_ROOT_FILES:
                dst = os.path.join(output_dir, "sub", token, name)
            else:
                dst = os.path.join(output_dir, rel_src)
            text = render_file(src, dst, env)
            if name == "sub.txt":
                sub_txt_rendered = text
            rendered.append(rel_src)

    # 派生 sub64.txt：base64(sub.txt 全文)，无尾换行（与现状产物一致）
    if sub_txt_rendered is None:
        raise ValueError("templates/sub/sub.txt 缺失，无法派生 sub64.txt")
    sub64_path = os.path.join(output_dir, "sub", token, "sub64.txt")
    with open(sub64_path, "w", encoding="ascii") as f:
        f.write(base64.b64encode(sub_txt_rendered.encode("utf-8")).decode("ascii"))
    rendered.append("sub/%s/sub64.txt" % token)

    # 内建校验
    xray_json = os.path.join(output_dir, "xray", "config.json")
    if os.path.exists(xray_json):
        _validate_json(xray_json)
    with open(sub64_path, encoding="ascii") as f:
        sub64 = f.read()
    if sub64 != base64.b64encode(sub_txt_rendered.encode("utf-8")).decode("ascii"):
        raise ValueError("sub64.txt 与 sub.txt 不一致")
    # 全量扫描：任何产物残留 {{ 都视为失败
    for root, _dirs, files in os.walk(output_dir):
        for name in files:
            p = os.path.join(root, name)
            with open(p, encoding="utf-8", errors="replace") as f:
                if "{{" in f.read():
                    raise ValueError("产物残留占位符: %s" % p)
    return rendered


def main(argv=None):
    parser = argparse.ArgumentParser(description="proxy 模板渲染引擎")
    parser.add_argument("--env-file", required=True, help="合并后的环境变量文件")
    parser.add_argument("--templates-dir", required=True, help="templates 目录")
    parser.add_argument("--output-dir", required=True, help="渲染产物输出目录")
    parser.add_argument("--group", action="append",
                        help="只渲染指定组（systemd/hysteria/xray/sub/client），可多次指定")
    args = parser.parse_args(argv)

    env = parse_env_file(args.env_file)
    missing = [v for v in REQUIRED_VARS if v not in env]
    if missing:
        sys.stderr.write("错误: 环境变量缺失: %s\n" % ", ".join(missing))
        return 1

    rendered = render_all(args.templates_dir, args.output_dir, env,
                          groups=args.group)
    print("渲染完成: %d 个产物 -> %s" % (len(rendered), args.output_dir))
    for rel in rendered:
        print("  - %s" % rel)
    print("派生公钥 pbk: %s" % derive_public_key(env["XRAY_PRIVATE_KEY"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
