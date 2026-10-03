#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Analyses an openMSX capture of the sphere demo (tools/capture.tcl):

  - the frame rate and the late flips (vertical blanks between flips more
    than PACE), from the ROM's own counters at every flip;
  - geo3d idle (status bit 0) and CE = 0 at every flip: the page shown is
    finished (no tearing), and the page shown alternates;
  - contacts with each edge (the sphere's box, tools/bbox.py), the texture
    change at each side wall, the rotation step;
  - with --model NPZ (run_z80.py's frames.npz of the same ROM): every page
    captured compared with the model's page of the same flip, pixel for
    pixel, and the palettes.
Writes DIR/frames.npz (pages and RGB palettes, as run_z80.py does).

Usage: analyze.py DIR [--pace N] [--model NPZ] [--skip N]
"""
import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from bbox import boxes  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("dir")
ap.add_argument("--pace", type=int, default=1)
ap.add_argument("--model", default=None)
ap.add_argument("--skip", type=int, default=1)
ap.add_argument("--rotstep", type=int, default=None)
opt = ap.parse_args()
rotstep = opt.rotstep or (1 if opt.pace == 1 else 2)

log = []
end = None
for ln in open(os.path.join(opt.dir, "log.txt")):
    p = ln.split()
    if p[0] == "F":
        log.append([int(p[1]), float(p[2])] + [int(x) for x in p[3:]])
    elif p[0] == "END":
        end = ln.strip()
# the ROM's flips: from the first one (flip 1, vbl > 0) on; earlier lines
# are the BIOS's and the RAM clear's writes to the same address
start = next(k for k, r in enumerate(log) if r[0] == 1 and r[2] > 0)
log = log[start:]
n_cap = len(log)
rec = 54272 + 32
raw = open(os.path.join(opt.dir, "pages.bin"), "rb").read()
nrec = len(raw) // rec
# pages.bin also holds the records of the lines before `start` taken while skip
# divided their (garbage) flip number: count them out
cap_lines = []
allf = [ln.split() for ln in open(os.path.join(opt.dir, "log.txt")) if ln.startswith("F ")]
for k, p in enumerate(allf):
    if int(p[1]) % opt.skip == 0:
        cap_lines.append(k)
first_rec = sum(1 for k in cap_lines if k < start)
pages, pals, flipno = [], [], []
for i, k in enumerate(cap_lines):
    if k < start or i >= nrec:
        continue
    blk = raw[i * rec:(i + 1) * rec]
    pages.append(np.frombuffer(blk[:54272], dtype=np.uint8))
    w = np.frombuffer(blk[54272:], dtype="<u2")
    b, r, g = w & 31, (w >> 5) & 31, (w >> 10) & 31
    pals.append(np.stack([r, g, b], -1) * 255 // 31)
    flipno.append(allf[k][1])
pages = np.array(pages)
pals = np.array(pals, dtype=np.uint8)
np.savez_compressed(os.path.join(opt.dir, "frames.npz"), pages=pages, pals=pals,
                    flip=np.array([int(x) for x in flipno]))

f = np.array(log)
n, t, vbl, late, page, tex, px, py, rot, kx, ky, geo, ce = f.T
gaps = np.diff(vbl)
vals, cnt = np.unique(gaps, return_counts=True)
dur = t[-1] - t[0]
print(f"{opt.dir}: {len(f)} flips in {dur:.2f} s of emulated time = {(len(f) - 1) / dur:.2f} fps; "
      f"vertical blanks between flips {dict(zip(vals.tolist(), cnt.tolist()))}; "
      f"late flips (ROM counter) {int(late[-1])}; {end}")
print(f"  flips with geo3d busy: {int(((geo.astype(int) & 1) == 1).sum())}, with CE = 1: {int(ce.sum())}; "
      f"page alternates: {bool(np.all(np.diff(page) != 0))}")
drot = (np.diff(rot) % 256)
print(f"  rotation step per flip: {sorted(set(drot.tolist()))} (expected {rotstep})")
# contacts and texture changes
a = json.load(open(os.path.join(os.path.dirname(HERE), "out", "assets.json")))
xmin, xmax, ymin, ymax = a["X_MIN"], a["X_MAX"], a["Y_MIN"], a["Y_MAX"]
side = [k for k in range(len(f)) if px[k] in (xmin, xmax)]
flo = [k for k in range(len(f)) if py[k] in (ymin, ymax)]
tchg = [k for k in range(1, len(f)) if tex[k] != tex[k - 1]]
print(f"  centre at a side limit on {len(side)} flips, at the floor/ceiling limit on {len(flo)}; "
      f"texture changes on {len(tchg)} flips: "
      f"{'all at a side contact' if all(k in side for k in tchg) else 'some elsewhere: ' + str([k for k in tchg if k not in side][:8])}")
bx = boxes(os.path.join(opt.dir, "frames.npz"))
touch = {"left": 0, "right": 0, "top": 0, "bottom": 0}
near = {"left": [], "right": [], "top": [], "bottom": []}
for b in bx:
    if b:
        touch["left"] += b[0] == 0
        touch["right"] += b[1] == 511
        touch["top"] += b[2] == 0
        touch["bottom"] += b[3] == 211
        near["left"].append(b[0])
        near["right"].append(511 - b[1])
        near["top"].append(b[2])
        near["bottom"].append(211 - b[3])
print(f"  captured pages touching each edge: {touch}; closest gaps: "
      f"{ {k: min(v) for k, v in near.items() if v} }")
# the drawn page at each contact frame: centre limit -> the gap at that edge
if opt.skip == 1:
    cg = {"left": [], "right": [], "top": [], "bottom": []}
    for k in range(len(f)):
        b = bx[k] if k < len(bx) else None
        if not b:
            continue
        # the page shown at flip k is the frame drawn before: its centre is
        # the log line of flip k - 1 (the state is advanced after each RUN)
        if k == 0:
            continue
        if px[k - 1] == xmin:
            cg["left"].append(b[0])
        if px[k - 1] == xmax:
            cg["right"].append(511 - b[1])
        if py[k - 1] == ymin:
            cg["top"].append(b[2])
        if py[k - 1] == ymax:
            cg["bottom"].append(211 - b[3])
    print(f"  contact frames, gap to the edge in pixels: {cg}")
if opt.model:
    md = np.load(opt.model)
    keep = md["frame"] >= 0                     # the flips (R#2 writes before frame 0: set-up)
    mp = np.concatenate([md["pages"][:1], md["pages"][keep]])   # mp[n] = flip n
    mpal = np.concatenate([md["pals"][:1], md["pals"][keep]])
    same, diff, pdiff, nc = 0, [], 0, 0
    for i, fl in enumerate(flipno):
        fl = int(fl)
        if fl >= len(mp):
            break
        nc += 1
        if np.array_equal(mp[fl], pages[i]):
            same += 1
        else:
            diff.append(fl)
        if np.abs(np.array(mpal[fl], dtype=int) - np.array(pals[i], dtype=int)).max() > 1:
            pdiff += 1
    print(f"  against the model: {same} of {nc} pages identical"
          + (f", differ at flips {diff[:10]}" if diff else "") + f"; palettes differing: {pdiff}")
