#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Animated GIF from a capture (analyze.py's frames.npz of an openMSX run):
every STEP-th page flip from FIRST on, for SECONDS, 512 x 424 (each SCREEN 7
line twice, for the 4:3 aspect), the real palettes (every colour of the six
textures fits one GIF palette, so nothing is dithered), each frame shown for
STEP / 60 s. Then gifsicle -O3.

Usage: make_gif.py NPZ OUT.gif [first] [seconds] [step]
"""
import subprocess
import sys

import numpy as np
from PIL import Image

npz, out = sys.argv[1], sys.argv[2]
first = int(sys.argv[3]) if len(sys.argv) > 3 else 0
secs = float(sys.argv[4]) if len(sys.argv) > 4 else 20
step = int(sys.argv[5]) if len(sys.argv) > 5 else 3
d = np.load(npz)
pages, pals = d["pages"], d["pals"]
idx = list(range(first, min(len(pages), first + int(secs * 60)), step))
rgbs = []
for k in idx:
    a = pages[k].reshape(212, 256)
    pix = np.empty((212, 512), dtype=np.uint8)
    pix[:, 0::2] = a >> 4
    pix[:, 1::2] = a & 15
    rgbs.append(np.repeat(pals[k].astype(np.uint8)[pix], 2, axis=0))
cols = np.unique(np.concatenate([r.reshape(-1, 3) for r in rgbs]), axis=0)
assert len(cols) <= 256, len(cols)
key = (cols[:, 0].astype(np.int64) << 16) | (cols[:, 1].astype(np.int64) << 8) | cols[:, 2]
flat = list(cols.reshape(-1)) + [0] * (768 - 3 * len(cols))
frames = []
for r in rgbs:
    k = (r[..., 0].astype(np.int64) << 16) | (r[..., 1].astype(np.int64) << 8) | r[..., 2]
    im = Image.fromarray(np.searchsorted(key, k).astype(np.uint8), "P")
    im.putpalette(flat)
    frames.append(im)
ms = round(1000 * step / 59.94)
frames[0].save(out, save_all=True, append_images=frames[1:], duration=ms, loop=0, optimize=False)
subprocess.run(["gifsicle", "-O3", "--batch", out], check=True)
print(f"{out}: {len(frames)} frames, {len(frames) * ms / 1000:.1f} s, {len(cols)} colours")
