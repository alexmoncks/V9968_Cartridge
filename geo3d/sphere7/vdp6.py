#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Python model of the V9968 command engine in GRAPHIC6 (SCREEN 7: 512 x 212,
16 colours, 256 bytes per line, 4 pages of 256 lines in 256 KB), for the
commands the SCREEN 7 sphere demo sends: LRMM (geo3d's textured spans), LINE,
HMMV, HMMM and PSET. It follows geo3d/sim/hra/check_lrmm.py (the SCREEN 5
model checked against HRA!'s vdp_command.v), with the G6 differences read from
vdp_command.v:
  - address = {DY[9:0], DX[8:1]} for the destination and {SY[17:8], SX[16:9]}
    for the LRMM source (byte = y * 256 + x / 2, even x in the high nibble);
  - 512-pixel lines: a command stops where the next DX would leave 0..511;
  - HMMV / HMMM move whole bytes (2 pixels).

Checked against the unmodified RTL (sim/tb_hra_cmd_g6.v, both with and without
the G6 VRAM interleave of vdp.v):

    vdp6.py check [n_cmds] [ilv]

which also prints the clocks per pixel (or byte) the RTL took for each
command kind at 85.9 MHz in high-speed mode, VRAM always ready.
"""
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HRA = os.path.join(ROOT, "..", "fpga", "V9968_Cartridge_TangNano20K", "src", "v9968")
SIM = os.path.join(HERE, "out", "hra6")

W6 = 512
ROW = 256                      # bytes per line


def s(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


class Vram6:
    def __init__(self, data=b""):
        self.b = bytearray(data) + bytearray(262144 - len(data))

    def get(self, x, y):
        v = self.b[(y & 0x3FF) * ROW + ((x >> 1) & 0xFF)]
        return (v & 15) if (x & 1) else (v >> 4)

    def put(self, x, y, p):
        a = (y & 0x3FF) * ROW + ((x >> 1) & 0xFF)
        if x & 1:
            self.b[a] = (self.b[a] & 0xF0) | (p & 15)
        else:
            self.b[a] = (self.b[a] & 0x0F) | ((p & 15) << 4)

    def page(self, y0, lines=212):
        return bytes(self.b[y0 * ROW:(y0 + lines) * ROW])


def lop(op, src, dst):
    """As check_lrmm.lop: TIMP tests the whole CLR byte, the low nibble is written."""
    if op & 8 and src == 0:
        return dst
    o = op & 7
    return {0: src, 1: src & dst, 2: src | dst, 3: src ^ dst, 4: (~src) & 15}.get(o, src)


def lrmm(vr, c):
    """Single-row LRMM (NY = 1), no XHR, DIX from c["dix"]."""
    sx = (c["sx"] & 0xFFF) << 8
    sy = (c["sy"] & 0x1FFF) << 8
    dx, dy = c["dx"], c["dy"]
    vx, vy = s(c["vx"], 16), s(c["vy"], 16)
    wsx, wex, wsy, wey = c.get("win", (0, 511, 0, 2047))
    for _ in range(c["nx"]):
        ix, iy = s(sx >> 8, 12), s(sy >> 8, 13)
        if ix < wsx or ix > wex or iy < wsy or iy > wey:
            src = c["clr"] & 0xFF
        else:
            src = vr.get(ix, iy)
        vr.put(dx, dy, lop(c["lop"], src, vr.get(dx, dy)))
        dx += -1 if c.get("dix") else 1
        if dx < 0 or dx > W6 - 1:
            break
        sx = (sx + vx) & 0xFFFFF
        sy = (sy + vy) & 0x1FFFFF


def line(vr, c):
    x, y, nx, ny, maj = c["dx"], c["dy"], c["nx"], c["ny"], c["maj"]
    sxd = -1 if c["dix"] else 1
    syd = -1 if c["diy"] else 1
    nyb = ((nx - 1) & 0x7FF) >> 1
    for i in range(nx + 1):
        vr.put(x, y, lop(c["lop"], c["clr"] & 0xFF, vr.get(x, y)))
        if i == nx or (x == 0 if c["dix"] else x == W6 - 1) or (c["diy"] and y == 0):
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
        y &= 0x7FF


def hmmv(vr, c):
    """DIX = DIY = 0, even DX and NX, inside the line."""
    n = c["nx"] >> 1
    for y in range(c["ny"]):
        a = ((c["dy"] + y) & 0x3FF) * ROW + ((c["dx"] >> 1) & 0xFF)
        vr.b[a:a + n] = bytes([c["clr"]]) * n


def hmmm(vr, c):
    n = c["nx"] >> 1
    for y in range(c["ny"]):
        sa = ((c["sy"] + y) & 0x3FF) * ROW + ((c["sx"] >> 1) & 0xFF)
        da = ((c["dy"] + y) & 0x3FF) * ROW + ((c["dx"] >> 1) & 0xFF)
        vr.b[da:da + n] = vr.b[sa:sa + n]


def pset(vr, c):
    vr.put(c["dx"], c["dy"], lop(c["lop"], c["clr"] & 0xFF, vr.get(c["dx"], c["dy"])))


def apply_regs(vr, r, win=None):
    """The command R#46 starts, from the register file r (64 entries), as
    the engine's model applies it (Z80 commands and geo3d's spans alike)."""
    cmd, lp = r[46] >> 4, r[46] & 15
    dx = r[36] | (r[37] & 1) << 8
    dy = r[38] | (r[39] & 7) << 8
    nx = r[40] | (r[41] & 7) << 8
    ny = r[42] | (r[43] & 7) << 8
    if cmd == 0x3:
        c = dict(sx=r[32] | (r[33] & 15) << 8, sy=r[34] | (r[35] & 31) << 8, dx=dx, dy=dy,
                 nx=nx, clr=r[44], dix=(r[45] >> 2) & 1, vx=r[47] | r[48] << 8,
                 vy=r[49] | r[50] << 8, lop=lp)
        c["win"] = (r[51] | (r[52] & 1) << 8, r[55] | (r[56] & 1) << 8,
                    r[53] | (r[54] & 7) << 8, r[57] | (r[58] & 7) << 8)
        lrmm(vr, c)
    elif cmd == 0x7:
        line(vr, dict(dx=dx, dy=dy, nx=nx & 0x3FF, ny=ny & 0x3FF, clr=r[44], maj=r[45] & 1,
                      dix=(r[45] >> 2) & 1, diy=(r[45] >> 3) & 1, lop=lp))
    elif cmd == 0xC:
        hmmv(vr, dict(dx=dx, dy=dy, nx=nx, ny=ny, clr=r[44]))
    elif cmd == 0xD:
        hmmm(vr, dict(sx=r[32] | (r[33] & 1) << 8, sy=r[34] | (r[35] & 7) << 8,
                      dx=dx, dy=dy, nx=nx, ny=ny))
    elif cmd == 0x5:
        pset(vr, dict(dx=dx, dy=dy, clr=r[44], lop=lp))
    else:
        raise ValueError(f"command {r[46]:02x} not modelled")


# ------------------------------------------------------------------ check
def build_tb():
    os.makedirs(SIM, exist_ok=True)
    vvp = os.path.join(SIM, "tbhra6.vvp")
    srcs = [os.path.join(HERE, "sim", "tb_hra_cmd_g6.v"),
            os.path.join(HRA, "vdp_command.v"), os.path.join(HRA, "vdp_command_cache.v")]
    if not os.path.exists(vvp) or any(os.path.getmtime(f) > os.path.getmtime(vvp) for f in srcs):
        subprocess.run(["iverilog", "-g2012", "-o", vvp] + srcs, check=True)
    return vvp


def check(n, ilv):
    rng = random.Random(7968 + ilv)
    init = bytearray(262144)
    for y in range(512, 1024):                      # textures in pages 2-3
        for xb in range(ROW):
            init[y * ROW + xb] = rng.randrange(256)
    for y in range(0, 256):
        for xb in range(ROW):
            init[y * ROW + xb] = rng.randrange(256) if rng.random() < 0.2 else 0
    vr = Vram6(init)
    ops, kinds = [], []
    for t in range(n):
        kind = rng.choice(["lrmm"] * 6 + ["line", "hmmv", "hmmm", "pset", "pset"])
        r = [0] * 64
        r[51:59] = [0, 0, 0, 0, 0xFF, 1, 0xFF, 7]
        if kind == "lrmm":
            sx, sy = rng.randrange(0, 352), 512 + rng.randrange(0, 448)
            vx = rng.choice([256, 128, 300, 64, rng.randrange(0, 1024), (-rng.randrange(0, 600)) & 0xFFFF])
            vy = rng.choice([0, 32, (-48) & 0xFFFF, rng.randrange(0, 200), (-rng.randrange(0, 200)) & 0xFFFF])
            dx = rng.randrange(0, 512)
            c = dict(sx=sx, sy=sy, dx=dx, dy=rng.randrange(0, 512), nx=rng.randrange(1, 300),
                     vx=vx, vy=vy, clr=rng.choice([0x81, 0x82, rng.randrange(256)]), dix=0,
                     lop=rng.choice([0, 0, 0, 8, 3]))
            if rng.random() < 0.5:                  # the demo's window: one texture, levels 1..4
                wsx = rng.choice([0, 171, 342])
                wsy = rng.choice([512, 768])
                c["win"] = (wsx, wsx + 160, wsy, wsy + 255)
            r[32:36] = [sx & 255, sx >> 8, sy & 255, sy >> 8]
            r[36:46] = [dx & 255, dx >> 8, c["dy"] & 255, c["dy"] >> 8, c["nx"] & 255, c["nx"] >> 8, 1, 0, c["clr"], 0]
            r[47:51] = [vx & 255, vx >> 8, vy & 255, vy >> 8]
            if "win" in c:
                wsx, wex, wsy, wey = c["win"]
                r[51:59] = [wsx & 255, wsx >> 8, wsy & 255, wsy >> 8, wex & 255, wex >> 8, wey & 255, wey >> 8]
            r[46] = 0x30 | c["lop"]
            seq = list(range(32, 46)) + list(range(47, 59)) + [46]
        elif kind == "line":
            dx, dy, nx = rng.randrange(0, 512), rng.randrange(0, 512), rng.randrange(0, 400)
            r[36:46] = [dx & 255, dx >> 8, dy & 255, dy >> 8, nx & 255, nx >> 8, 0, 0, rng.randrange(256), rng.choice([0, 4])]
            r[46] = 0x70 | rng.choice([0, 8, 3])
            seq = list(range(36, 47))
        elif kind == "hmmv":
            dx = rng.randrange(0, 256) * 2
            nx = rng.randrange(1, (512 - dx) // 2 + 1) * 2
            dy, ny = rng.randrange(0, 512 - 64), rng.randrange(1, 64)
            r[36:46] = [dx & 255, dx >> 8, dy & 255, dy >> 8, nx & 255, nx >> 8, ny, 0, rng.randrange(256), 0]
            r[46] = 0xC0
            seq = list(range(36, 47))
        elif kind == "hmmm":
            sx, dx = rng.randrange(0, 128) * 2, rng.randrange(0, 128) * 2
            nx = rng.randrange(1, 129) * 2
            sy, dy, ny = rng.randrange(212, 256), rng.randrange(0, 448), rng.randrange(1, 40)
            ny = min(ny, 256 - sy)
            r[32:46] = [sx & 255, sx >> 8, sy & 255, sy >> 8, dx & 255, dx >> 8, dy & 255, dy >> 8,
                        nx & 255, nx >> 8, ny, 0, 0, 0]
            r[46] = 0xD0
            seq = list(range(32, 47))
        else:
            dx, dy = rng.randrange(0, 512), rng.randrange(0, 512)
            r[36:46] = [dx & 255, dx >> 8, dy & 255, dy >> 8, 0, 0, 0, 0, rng.randrange(256), 0]
            r[46] = 0x50 | rng.choice([0, 0, 8, 2])
            seq = list(range(36, 47))
        apply_regs(vr, r)
        ops.append([(k, r[k]) for k in seq])
        kinds.append((kind, r))
    vvp = build_tb()
    cmds = os.path.join(SIM, "hra6_cmds.txt")
    with open(os.path.join(SIM, "hra6_init.hex"), "w") as f:
        f.write("\n".join(f"{b:02x}" for b in init) + "\n")
    with open(cmds, "w") as f:
        for o in ops:
            for k, v in o:
                f.write(f"W {k} {v & 0xFF:02x}\n")
            f.write("E\n")
    subprocess.run(["vvp", "-n", vvp, f"+ilv={ilv}", f"+cmds={cmds}",
                    f"+init={os.path.join(SIM, 'hra6_init.hex')}",
                    f"+dump={os.path.join(SIM, 'hra6_dump.hex')}",
                    f"+log={os.path.join(SIM, 'hra6_log.txt')}"],
                   check=True, stdout=subprocess.DEVNULL)
    got = bytearray(int(x, 16) for x in open(os.path.join(SIM, "hra6_dump.hex")) if x.strip())
    bad = [i for i in range(262144) if got[i] != vr.b[i]]
    cyc = [int(x.split()[1]) for x in open(os.path.join(SIM, "hra6_log.txt"))]
    per = {}
    for (kind, r), cy in zip(kinds, cyc):
        nx = r[40] | (r[41] & 7) << 8
        ny = r[42] | (r[43] & 7) << 8
        units = {"lrmm": nx, "line": nx + 1, "hmmv": nx // 2 * ny, "hmmm": nx // 2 * ny, "pset": 1}[kind]
        a = per.setdefault(kind, [0, 0, 0])
        a[0] += units
        a[1] += cy
        a[2] += 1
    print(f"G6 {n} commands, interleave {ilv}: {len(bad)} bytes differ from vdp_command.v")
    for kind, (u, cy, k) in sorted(per.items()):
        unit = "byte" if kind in ("hmmv", "hmmm") else "pixel" if kind != "pset" else "command"
        print(f"  {kind:5s} x{k:4d}: {cy / max(1, u):6.2f} clocks per {unit} at 85.9 MHz")
    if bad:
        i = bad[0]
        print(f"first difference: byte {i:05x} (y={i // ROW}, x={2 * (i % ROW)}): RTL {got[i]:02x}, model {vr.b[i]:02x}")
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        sys.exit(check(int(sys.argv[2]) if len(sys.argv) > 2 else 400,
                       int(sys.argv[3]) if len(sys.argv) > 3 else 0))
    print(__doc__)
