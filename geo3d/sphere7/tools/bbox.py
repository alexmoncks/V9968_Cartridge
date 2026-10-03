#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Per shown page: the sphere's box (pixels whose colour is not 0, 14 or 15:
black space, the stars and the HUD; the night side is colour 1), the
contacts with the edges, and a summary. Works on run_z80.py's frames.npz and
on the openMSX captures (capture.py). bbox.py NPZ [first last]"""
import sys

import numpy as np


def boxes(npz):
    d = np.load(npz)
    out = []
    for k in range(len(d["pages"])):
        a = d["pages"][k].reshape(212, 256)
        pix = np.empty((212, 512), dtype=np.uint8)
        pix[:, 0::2] = a >> 4
        pix[:, 1::2] = a & 15
        sph = (pix != 0) & (pix != 14) & (pix != 15)
        ys, xs = np.nonzero(sph)
        if len(xs) < 50:
            out.append(None)
        else:
            out.append((int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max()), int(len(xs))))
    return out


if __name__ == "__main__":
    b = boxes(sys.argv[1])
    lo = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    hi = int(sys.argv[3]) if len(sys.argv) > 3 else len(b)
    for k in range(lo, min(hi, len(b))):
        print(k, b[k])
    c = {"left": 0, "right": 0, "top": 0, "bottom": 0}
    for x in b:
        if x:
            c["left"] += x[0] == 0
            c["right"] += x[1] == 511
            c["top"] += x[2] == 0
            c["bottom"] += x[3] == 211
    print("frames touching:", c)
