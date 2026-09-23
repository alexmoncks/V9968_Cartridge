#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Modelo de referência (bit-exato) do geo3d_core e gerador de vetores de teste.

Cada linha do arquivo de saída tem 26 palavras hex de 16 bits:
  [0]      modo: 0 = grava configuração completa + vértice, 1 = streaming (só vértice)
  [1..18]  M00..M22, TX, TY, TZ, F, CX, CY, ZNEAR, W, H
  [19..21] VX, VY, VZ
  [22..25] SX, SY, Z, FLAGS esperados
"""
import math
import random
import sys


def sat(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def s16(x):
    return x & 0xFFFF


def model(cfg, vtx):
    m = cfg[0:9]
    t = cfg[9:12]
    f, cx, cy, znear, w, h = cfg[12:18]
    acc = [m[3 * r] * vtx[0] + m[3 * r + 1] * vtx[1] + m[3 * r + 2] * vtx[2] for r in range(3)]
    xc, yc, zc = [sat((acc[r] >> 14) + t[r], -131071, 131071) for r in range(3)]
    zout = sat(zc, -32768, 32767)
    if zc < znear or zc <= 0:
        return 0, 0, zout, 0x01

    flags = 0
    q = []
    for axis, c in enumerate((xc, yc)):
        a = abs(c)
        num = a * f
        if num >= (zc << 15):
            qq = 32767
            flags |= 1 << (1 + axis)
        else:
            qq = num // zc
        q.append(-qq if c < 0 else qq)
    sx = sat(cx + q[0], -32768, 32767)
    sy = sat(cy - q[1], -32768, 32767)
    outc = (sx < 0) | ((sx >= w) << 1) | ((sy < 0) << 2) | ((sy >= h) << 3)
    flags |= outc << 4
    return sx, sy, zout, flags


def rot_matrix(rng, scale=1.0):
    ax, ay, az = (rng.uniform(-math.pi, math.pi) for _ in range(3))
    cxa, sxa = math.cos(ax), math.sin(ax)
    cya, sya = math.cos(ay), math.sin(ay)
    cza, sza = math.cos(az), math.sin(az)
    rx = [[1, 0, 0], [0, cxa, -sxa], [0, sxa, cxa]]
    ry = [[cya, 0, sya], [0, 1, 0], [-sya, 0, cya]]
    rz = [[cza, -sza, 0], [sza, cza, 0], [0, 0, 1]]

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]

    r = mul(rz, mul(ry, rx))
    return [sat(round(r[i][j] * scale * 16384), -32768, 32767) for i in range(3) for j in range(3)]


def make_cfg(rng, kind):
    if kind == "stress":
        m = [rng.randint(-32768, 32767) for _ in range(9)]
        t = [rng.randint(-32768, 32767) for _ in range(3)]
        f = rng.randint(0, 65535)
        znear = rng.randint(-100, 2000)
    else:
        m = rot_matrix(rng, rng.choice([1.0, 1.0, 0.5, 1.9]))
        t = [rng.randint(-500, 500), rng.randint(-500, 500), rng.randint(200, 6000)]
        f = rng.choice([128, 192, 256, 320, 512, rng.randint(1, 1024)])
        znear = rng.choice([1, 8, 16, 32, 64])
    return m + t + [f, 128, 106, znear, 256, 212]


def make_vtx(rng, kind):
    if kind == "stress":
        return [rng.randint(-32768, 32767) for _ in range(3)]
    r = rng.choice([100, 500, 2000])
    return [rng.randint(-r, r) for _ in range(3)]


def main():
    n_groups = int(sys.argv[1]) if len(sys.argv) > 1 else 250
    out = sys.argv[2] if len(sys.argv) > 2 else "vectors.hex"
    rng = random.Random(9968)
    lines = []
    stats = {"near": 0, "ovf": 0, "outcode": 0, "normal": 0}
    for g in range(n_groups):
        kind = "stress" if g % 4 == 3 else "normal"
        cfg = make_cfg(rng, kind)
        for k in range(8):
            vtx = make_vtx(rng, kind)
            res = model(cfg, vtx)
            fl = res[3]
            if fl & 1:
                stats["near"] += 1
            elif fl & 6:
                stats["ovf"] += 1
            elif fl & 0xF0:
                stats["outcode"] += 1
            else:
                stats["normal"] += 1
            words = [0 if k == 0 else 1] + cfg + vtx + list(res)
            lines.append(" ".join(f"{s16(x):04x}" for x in words))
    with open(out, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"{len(lines)} vetores gerados em {out}; distribuição: {stats}")


if __name__ == "__main__":
    main()
