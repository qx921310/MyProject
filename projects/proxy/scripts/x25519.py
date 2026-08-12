#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X25519 公钥推导（纯 Python，仅依赖 stdlib）。

用途：从 Xray Reality 服务端私钥推导客户端公钥（pbk），
避免在 secrets 中重复存放公钥导致私/公不匹配（skill 明确记录过此类事故）。
实现参考 RFC 7748 的 Montgomery 阶梯，仅用于固定基点(9)的标量乘法。

命令行用法：
    python3 x25519.py derive <base64url 私钥>     # 输出 base64url 公钥
    python3 x25519.py verify <私钥> <公钥>        # 校验配对，输出 MATCH/MISMATCH
"""

import base64
import sys


_P = 2**255 - 19
_A24 = 121665
_BASE_POINT_U = 9


def _decode_b64url(s):
    """解码 base64url（自动补 padding）。"""
    s = s.strip()
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def _encode_b64url(b):
    """base64url 编码并去掉 padding。"""
    return base64.b64encode(b).decode().replace("+", "-").replace("/", "_").rstrip("=")


def _clamp(k):
    """RFC 7748 私钥 clamp。"""
    k = bytearray(k)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    return bytes(k)


def _scalarmult(k, u):
    """X25519 标量乘法（Montgomery 阶梯），u 为 little-endian 32 字节。"""
    x1 = int.from_bytes(u, "little")
    x2, z2 = 1, 0
    x3, z3 = x1, 1
    swap = 0
    for t in range(254, -1, -1):
        kt = (k[t >> 3] >> (t & 7)) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt
        a = (x2 + z2) % _P
        aa = (a * a) % _P
        b = (x2 - z2) % _P
        bb = (b * b) % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = (d * a) % _P
        cb = (c * b) % _P
        x3 = ((da + cb) % _P) ** 2 % _P
        z3 = (x1 * ((da - cb) % _P) ** 2) % _P
        x2 = (aa * bb) % _P
        z2 = (e * (aa + _A24 * e % _P)) % _P
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return (x2 * pow(z2, _P - 2, _P)) % _P


def derive_public_key(private_b64):
    """由 base64url 私钥推导 base64url 公钥（X25519 基点 9）。"""
    priv = _decode_b64url(private_b64)
    if len(priv) != 32:
        raise ValueError("私钥长度必须是 32 字节")
    u = _BASE_POINT_U.to_bytes(32, "little")
    pub = _scalarmult(_clamp(priv), u)
    return _encode_b64url(pub.to_bytes(32, "little"))


def verify_pair(private_b64, public_b64):
    """校验私钥/公钥是否配对。"""
    return derive_public_key(private_b64) == public_b64.strip()


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) < 2 or argv[0] not in ("derive", "verify"):
        sys.stderr.write(__doc__)
        return 2
    if argv[0] == "derive":
        print(derive_public_key(argv[1]))
        return 0
    if verify_pair(argv[1], argv[2]):
        print("MATCH")
        return 0
    print("MISMATCH")
    return 1


if __name__ == "__main__":
    sys.exit(main())
