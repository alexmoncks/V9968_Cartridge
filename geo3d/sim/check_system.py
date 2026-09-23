#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Cross-checks the pages painted by HRA!'s vdp_command.v in tb_system.v
(texdemo_frames.hex) against the Python VDP model (sim/hra/check_lrmm.py,
itself verified byte-exact against the same RTL) applied to the command log
of the textured demo (texdemo_got.txt): page clear, then every LRMM / LINE.

Usage: check_system.py [frames.hex] [got.txt] [tex_init.hex]
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "hra"))
from check_lrmm import Vram, model_line, model_lrmm  # noqa: E402

PAGE = 212 * 128
TEXADDR = 0x10000


def load_log(path):
    frames, cur = [], []
    for line in open(path):
        p = line.split()
        if not p:
            continue
        if p[0] in ("L", "M"):
            cur.append((p[0], [int(x, 16) for x in p[1:]]))
        elif p[0] == "F":
            frames.append(cur)
            cur = []
    return frames


def w16(b, i):
    return b[i] | (b[i + 1] << 8)


def apply(vr, kind, b):
    if kind == "M":
        c = dict(sx=w16(b, 0), sy=w16(b, 2), dx=w16(b, 4) & 0x1FF, dy=w16(b, 6) & 0x7FF,
                 nx=w16(b, 8) & 0x3FF, clr=b[12], dix=(b[13] >> 2) & 1,
                 vx=w16(b, 14), vy=w16(b, 16), lop=b[18] & 15)
        model_lrmm(vr, c)
    else:
        arg = b[9]
        c = dict(dx=w16(b, 0) & 0x1FF, dy=w16(b, 2) & 0x7FF, nx=w16(b, 4) & 0x3FF,
                 ny=w16(b, 6) & 0x3FF, clr=b[8], maj=arg & 1, dix=(arg >> 2) & 1,
                 diy=(arg >> 3) & 1, lop=b[10] & 15)
        model_line(vr, c)


def main():
    fhex = sys.argv[1] if len(sys.argv) > 1 else "texdemo_frames.hex"
    fgot = sys.argv[2] if len(sys.argv) > 2 else "texdemo_got.txt"
    ftex = sys.argv[3] if len(sys.argv) > 3 else "../z80/tex_init.hex"
    raw = bytes(int(l, 16) for l in open(fhex) if l.strip())
    pages = [raw[i:i + PAGE] for i in range(0, len(raw) - PAGE + 1, PAGE)]
    log = load_log(fgot)
    tex = bytes(int(l, 16) for l in open(ftex) if l.strip())
    vr = Vram(b"")
    vr.b[TEXADDR:TEXADDR + len(tex)] = tex
    bad = 0
    n = min(len(log), len(pages) - 1)
    for k in range(n):
        y0 = 256 if k % 2 == 0 else 0            # frame k is drawn on the hidden page
        vr.b[y0 * 128:(y0 + 212) * 128] = bytes(PAGE)   # the Z80's HMMV clear
        for kind, b in log[k]:
            apply(vr, kind, b)
        exp = bytes(vr.b[y0 * 128:(y0 + 212) * 128])
        got = pages[k + 1]
        if exp != got:
            i = next(j for j in range(PAGE) if exp[j] != got[j])
            if bad < 5:
                print(f"quadro {k}: divergência em y={i // 128} x={2 * (i % 128)}: "
                      f"RTL={got[i]:02x} modelo={exp[i]:02x}")
            bad += 1
    print(f"{n} páginas comparadas (HRA vdp_command.v x modelo Python): {bad} divergentes")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
