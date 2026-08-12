#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""订阅产物单一来源生成脚本。

所有订阅相关产物（clash.yaml / sub.txt / sub64.txt / 两份 links 文档）
都由本脚本从同一份变量来源生成，避免轮换时漏同步（skill 记录的既有事故）。

用法：
    python3 generate_sub.py --output-dir <dir> [--env-file F] [--identity I] [--templates-dir T]

输出结构（output-dir 为订阅根，例如 /etc/proxy）：
    <out>/sub/<SUB_TOKEN>/{clash.yaml,sub.txt,sub64.txt}    # web 根目录内容
    <out>/links/{subscription-links.txt,node-link.txt}      # 供发布到 /root，不进 web 根
"""

import argparse
import base64
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from render import parse_env_file, render_all  # noqa: E402


def build_env(env_file, secrets_dir, identity):
    """合并 .env（非敏感）与 decrypt-secrets.sh 输出（敏感，后者覆盖同名）。"""
    env = parse_env_file(env_file)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    decrypt = [os.path.join(script_dir, "decrypt-secrets.sh"), secrets_dir, identity]
    proc = subprocess.run(decrypt, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("decrypt-secrets.sh 失败:\n" + proc.stderr)
    for line in proc.stdout.splitlines():
        key, _, value = line.partition("=")
        if key:
            env[key] = value
    return env


def main(argv=None):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)
    parser = argparse.ArgumentParser(description="订阅产物生成（单一来源）")
    parser.add_argument("--output-dir", required=True,
                        help="订阅根目录（写入 sub/<token>/ 与 links/）")
    parser.add_argument("--env-file", default=os.path.join(project_dir, ".env"),
                        help="非敏感环境文件（缺省 .env，无则回退 .env.example）")
    parser.add_argument("--identity", default=os.environ.get("AGE_IDENTITY",
                                                             "/root/secrets/age-identity.txt"),
                        help="age identity 路径")
    parser.add_argument("--templates-dir",
                        default=os.path.join(project_dir, "templates"),
                        help="templates 目录")
    parser.add_argument("--secrets-dir",
                        default=os.path.join(project_dir, "secrets"),
                        help="secrets 目录")
    args = parser.parse_args(argv)

    env_file = args.env_file
    if not os.path.exists(env_file):
        env_file = os.path.join(project_dir, ".env.example")
        print("提示: 使用 .env.example 作为非敏感变量来源")

    env = build_env(env_file, args.secrets_dir, args.identity)
    rendered = render_all(args.templates_dir, args.output_dir, env,
                          groups=["sub"])

    token = env["SUB_TOKEN"]
    print("订阅产物生成完成: %d 个文件 -> %s" % (len(rendered), args.output_dir))
    for rel in rendered:
        print("  - %s" % rel)
    print("校验: sub64 与 sub.txt 一致（render 内建）")
    print("clash URL : %s/%s/clash.yaml" % (env["SUB_BASE_URL"], token))
    print("sub  URL  : %s/%s/sub.txt" % (env["SUB_BASE_URL"], token))
    print("sub64 URL : %s/%s/sub64.txt" % (env["SUB_BASE_URL"], token))
    return 0


if __name__ == "__main__":
    sys.exit(main())
