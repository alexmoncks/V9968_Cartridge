#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Compares the pages painted end to end by sim/tb_system.v (geo3d_bus + HRA!'s
unmodified vdp_command.v) on the sampled frames with the showcase model.

Usage: check_sample.py <scene>
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = 212 * 128


def load(path):
    return bytes(int(l, 16) for l in open(path) if l.strip())


def main():
    name = sys.argv[1]
    base = os.path.join(HERE, "out", name)
    got = load(base + "_sample_got.hex")
    exp = load(base + "_sample_expect.hex")
    frames = open(base + "_sample_frames.txt").read().split()
    bad = 0
    for i, fr in enumerate(frames):
        g, e = got[i * PAGE:(i + 1) * PAGE], exp[i * PAGE:(i + 1) * PAGE]
        if len(g) < PAGE or g != e:
            bad += 1
            j = next((k for k in range(min(len(g), PAGE)) if g[k] != e[k]), None)
            where = f"y={j // 128} x={2 * (j % 128)}" if j is not None else "página ausente"
            print(f"  quadro {fr}: divergência em {where}")
    print(f"{name}: {len(frames)} quadros amostrados, RTL (geo3d + vdp_command.v do HRA!) x modelo: "
          f"{bad} divergentes")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
