#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Checks a Python model of the V9968 LRMM (and LINE) command against HRA!'s
original RTL (vdp_command.v), simulated by tb_hra_cmd.v in SCREEN 5.

Usage: check_lrmm.py <n_tests> [hs]
"""
import random
import subprocess
import sys

rng = random.Random(9968)
TEX_Y = 512                      # texture in page 2 (y 512..767)


def s(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


class Vram:
    def __init__(self, data):
        self.b = bytearray(data) + bytearray(262144 - len(data))   # 256 KB VRAM

    def get(self, x, y):
        a = (y & 0x7FF) * 128 + ((x >> 1) & 0x7F)
        v = self.b[a]
        return (v & 15) if (x & 1) else (v >> 4)

    def put(self, x, y, p):
        a = (y & 0x7FF) * 128 + ((x >> 1) & 0x7F)
        if x & 1:
            self.b[a] = (self.b[a] & 0xF0) | (p & 15)
        else:
            self.b[a] = (self.b[a] & 0x0F) | ((p & 15) << 4)


def lop(op, src, dst):
    t = op & 8
    if t and src == 0:
        return dst
    o = op & 7
    return {0: src, 1: src & dst, 2: src | dst, 3: src ^ dst, 4: (~src) & 15}.get(o, src)


def model_lrmm(vr, c):
    """Single-row LRMM (NY = 1), no XHR. c: dict of fields."""
    sx = (c["sx"] & 0xFFF) << 8
    sy = (c["sy"] & 0x1FFF) << 8
    dx, dy = c["dx"], c["dy"]
    vx, vy = s(c["vx"], 16), s(c["vy"], 16)
    wsx, wex, wsy, wey = c.get("win", (0, 511, 0, 2047))
    for k in range(c["nx"]):
        ix, iy = s(sx >> 8, 12), s(sy >> 8, 13)
        if ix < wsx or ix > wex or iy < wsy or iy > wey:
            src = c["clr"] & 15
        else:
            src = vr.get(ix, iy)
        vr.put(dx, dy, lop(c["lop"], src, vr.get(dx, dy)))
        dx += -1 if c["dix"] else 1
        if dx < 0 or dx > 255:
            break
        sx = (sx + vx) & 0xFFFFF
        sy = (sy + vy) & 0x1FFFFF


def model_line(vr, c):
    x, y, nx, ny, maj = c["dx"], c["dy"], c["nx"], c["ny"], c["maj"]
    sxd = -1 if c["dix"] else 1
    syd = -1 if c["diy"] else 1
    nyb = ((nx - 1) & 0x7FF) >> 1
    for i in range(nx + 1):
        vr.put(x, y, lop(c["lop"], c["clr"] & 15, vr.get(x, y)))
        # vdp_command.v ends LINE after this dot when NX is reached, when the
        # next DX would leave 0..255 (checked even if X does not step), or when
        # moving up from DY = 0
        if i == nx or (x == 0 if c["dix"] else x == 255) or (c["diy"] and y == 0):
            break
        nb = nyb - ny
        shift = nb < 0
        nyb = nb + nx if shift else nb
        if maj:
            y += syd
            x += sxd if shift else 0
        else:
            x += sxd
            y += syd if shift else 0
        y &= 0x7FF                       # 256 KB (V9968) mode: DY wraps at 2048


def regs_lrmm(c):
    arg = (c["dix"] << 2)
    out = [(32, c["sx"] & 0xFF), (33, (c["sx"] >> 8) & 0x0F),
           (34, c["sy"] & 0xFF), (35, (c["sy"] >> 8) & 0x1F),
           (36, c["dx"] & 0xFF), (37, (c["dx"] >> 8) & 1),
           (38, c["dy"] & 0xFF), (39, (c["dy"] >> 8) & 7),
           (40, c["nx"] & 0xFF), (41, (c["nx"] >> 8) & 3),
           (42, 1), (43, 0), (44, c["clr"]), (45, arg),
           (47, c["vx"] & 0xFF), (48, (c["vx"] >> 8) & 0xFF),
           (49, c["vy"] & 0xFF), (50, (c["vy"] >> 8) & 0xFF)]
    if "win" in c:
        wsx, wex, wsy, wey = c["win"]
        out += [(51, wsx & 0xFF), (52, wsx >> 8), (53, wsy & 0xFF), (54, wsy >> 8),
                (55, wex & 0xFF), (56, wex >> 8), (57, wey & 0xFF), (58, wey >> 8)]
    return out + [(46, 0x30 | c["lop"])]


def regs_line(c):
    arg = (c["diy"] << 3) | (c["dix"] << 2) | c["maj"]
    return [(36, c["dx"] & 0xFF), (37, 0), (38, c["dy"] & 0xFF), (39, (c["dy"] >> 8) & 7),
            (40, c["nx"] & 0xFF), (41, c["nx"] >> 8), (42, c["ny"] & 0xFF), (43, c["ny"] >> 8),
            (44, c["clr"]), (45, arg), (46, 0x70 | c["lop"])]


def main():
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    HS = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    init = bytearray(131072)
    for y in range(TEX_Y, TEX_Y + 64):
        for xb in range(128):
            init[y * 128 + xb] = rng.randrange(256)        # random 16-colour texture
    vr = Vram(init)
    cmds, kinds = [], []
    win_default = (0, 511, 0, 2047)
    for t in range(N):
        kind = "line" if t % 5 == 4 else "lrmm"
        dy = rng.randrange(0, 212)
        if kind == "lrmm":
            c = dict(sx=rng.randrange(0, 256), sy=TEX_Y + rng.randrange(0, 64),
                     dx=rng.randrange(0, 256), dy=dy, nx=rng.randrange(1, 200),
                     vx=rng.choice([256, 128, 64, 384, rng.randrange(0, 65536)]),
                     vy=rng.choice([0, 0, 32, 65536 - 48, rng.randrange(0, 65536)]),
                     dix=rng.randrange(2), clr=rng.randrange(16),
                     lop=rng.choice([0, 0, 0, 8, 3, 1, 2]))
            if rng.random() < 0.3:
                wsx = rng.randrange(0, 128)
                wsy = TEX_Y + rng.randrange(0, 32)
                c["win"] = (wsx, wsx + rng.randrange(8, 120), wsy, wsy + rng.randrange(4, 31))
            model_lrmm(vr, c)
            cmds.append(regs_lrmm(c))
        else:
            c = dict(dx=rng.randrange(0, 256), dy=dy, nx=rng.randrange(0, 200),
                     ny=0, maj=0, dix=rng.randrange(2), diy=0,
                     clr=rng.randrange(16), lop=rng.choice([0, 8, 3]))
            if rng.random() < 0.5:
                c["ny"] = rng.randrange(0, c["nx"] + 1)
                c["maj"] = rng.randrange(2)
                c["diy"] = rng.randrange(2)
                if c["maj"]:
                    c["nx"] = rng.randrange(0, 100)
                    c["ny"] = rng.randrange(0, c["nx"] + 1)
            model_line(vr, c)
            cmds.append(regs_line(c))
        kinds.append((kind, c))

    with open("hra_init.hex", "w") as f:
        f.write("\n".join(f"{b:02x}" for b in init) + "\n")
    with open("hra_cmds.txt", "w") as f:
        for k, (kind, c) in zip(cmds, kinds):
            for r, v in k:
                f.write(f"W {r} {v & 0xFF:02x}\n")
            f.write("E\n")
            if "win" in c and c["win"] != win_default:
                for r, v in [(51, 0), (52, 0), (53, 0), (54, 0), (55, 0xFF), (56, 1), (57, 0xFF), (58, 7)]:
                    f.write(f"W {r} {v:02x}\n")

    subprocess.run(["vvp", "-n", "tbhra.vvp", f"+hs={HS}"], check=True, stdout=subprocess.DEVNULL)
    got = bytearray(int(l, 16) for l in open("hra_dump.hex") if l.strip())
    exp = vr.b
    bad = [i for i in range(262144) if got[i] != exp[i]]
    cyc = [int(l.split()[1]) for l in open("hra_log.txt")]
    px_l = [(c["nx"], cy) for (kd, c), cy in zip(kinds, cyc) if kd == "lrmm"]
    px_n = [(c["nx"] + 1, cy) for (kd, c), cy in zip(kinds, cyc) if kd == "line"]
    per = lambda lst: sum(cy for _, cy in lst) / max(1, sum(p for p, _ in lst))
    mode = "alta velocidade" if HS else "compatível (V9938)"
    print(f"{N} comandos ({len(px_l)} LRMM, {len(px_n)} LINE), modo {mode}: "
          f"{len(bad)} bytes divergentes")
    print(f"ciclos por pixel a 85,9 MHz: LRMM {per(px_l):.1f}, LINE {per(px_n):.1f}")
    if bad:
        i = bad[0]
        print(f"primeira divergência: byte {i} (y={i // 128}, x={2 * (i % 128)}): "
              f"RTL={got[i]:02x} modelo={exp[i]:02x}")
        sys.exit(1)


if __name__ == "__main__":
    main()
