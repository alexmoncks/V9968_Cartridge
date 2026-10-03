#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Tiles shown pages from run_z80.py's frames.npz (or an openMSX capture
directory's frames.npz): tile.py NPZ OUT.png first count [cols] [scale]"""
import sys

import numpy as np
from PIL import Image

d = np.load(sys.argv[1])
first, count = int(sys.argv[3]), int(sys.argv[4])
cols = int(sys.argv[5]) if len(sys.argv) > 5 else 3
scale = float(sys.argv[6]) if len(sys.argv) > 6 else 0.5
ims = []
for k in range(first, min(first + count, len(d["pages"]))):
    a = d["pages"][k].reshape(212, 256)
    pix = np.empty((212, 512), dtype=np.uint8)
    pix[:, 0::2] = a >> 4
    pix[:, 1::2] = a & 15
    im = Image.fromarray(d["pals"][k].astype(np.uint8)[pix], "RGB").resize((512, 424), Image.NEAREST)
    if scale != 1:
        im = im.resize((int(512 * scale), int(424 * scale)), Image.LANCZOS)
    ims.append(im)
w, h = ims[0].size
rows = (len(ims) + cols - 1) // cols
sheet = Image.new("RGB", (cols * (w + 4), rows * (h + 4)), (60, 60, 70))
for i, im in enumerate(ims):
    sheet.paste(im, ((i % cols) * (w + 4), (i // cols) * (h + 4)))
sheet.save(sys.argv[2])
