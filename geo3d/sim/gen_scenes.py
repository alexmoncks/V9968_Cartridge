#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Bit-exact reference model of geo3d_engine (phase 2) and stimulus generator.

Outputs:
  scenes_stim.txt     Z80 operations replayed by tb_engine.v, one per line:
                        W <sel> <byte>    OUT to port (sel 0 = index, 1 = data)
                        R                 wait until RUN busy clears (polls port 0)
                        K                 read the SKIPPED/DRAWN/CULLED counters
                        F <n>             frame marker (logged)
  scenes_expect.txt   expected log: F lines, L lines (11 command bytes), K lines
"""
import math
import random
import sys

from gen_vectors import model as core_model, rot_matrix, sat


def ocode(x, y, w, h):
    return (x < 0) | ((x >= w) << 1) | ((y < 0) << 2) | ((y >= h) << 3)


def mid(a, b):
    return (a + b) >> 1   # floor, like the RTL


def clip_edge(A, B, w, h):
    """Returns ('skip'|'cull'|'draw', (x1,y1,x2,y2))."""
    ax, ay = A
    bx, by = B
    ca, cb = ocode(ax, ay, w, h), ocode(bx, by, w, h)
    if ca & cb:
        return "cull", None
    if ca == 0:
        p = (ax, ay)
    elif cb == 0:
        p = (bx, by)
    else:
        lo, hi = (ax, ay), (bx, by)
        p = None
        for _ in range(16):
            m = (mid(lo[0], hi[0]), mid(lo[1], hi[1]))
            cm = ocode(*m, w, h)
            if cm == 0:
                p = m
                break
            if ocode(*lo, w, h) & cm:
                lo = m
            elif cm & ocode(*hi, w, h):
                hi = m
            else:
                return "skip", None
        if p is None:
            return "skip", None

    def bisect(inside, outside):
        lo, hi = inside, outside
        for _ in range(16):
            m = (mid(lo[0], hi[0]), mid(lo[1], hi[1]))
            if ocode(*m, w, h) == 0:
                lo = m
            else:
                hi = m
        return lo

    e1 = (ax, ay) if ca == 0 else bisect(p, (ax, ay))
    e2 = (bx, by) if cb == 0 else bisect(p, (bx, by))
    return "draw", (e1[0], e1[1], e2[0], e2[1])


def line_bytes(x1, y1, x2, y2, color, lop, ypage):
    dx, dy = x2 - x1, y2 - y1
    ax, ay = abs(dx), abs(dy)
    maj = ay > ax
    nx = ay if maj else ax
    ny = ax if maj else ay
    arg = (int(dy < 0) << 3) | (int(dx < 0) << 2) | int(maj)
    dyf = ((y1 & 0x7FF) + ypage) & 0x7FF
    return [x1 & 0xFF, (x1 >> 8) & 1, dyf & 0xFF, dyf >> 8,
            nx & 0xFF, (nx >> 8) & 7, ny & 0xFF, (ny >> 8) & 7,
            color & 0xFF, arg, 0x70 | (lop & 0xF)]


def render(cfg, verts, edges, color, lop, ypage):
    w, h = cfg[16], cfg[17]
    proj = []
    for v in verts:
        sx, sy, z, fl = core_model(cfg, v)
        proj.append((sx, sy, fl))
    cmds, skip, cull = [], 0, 0
    for a, b in edges:
        pa, pb = proj[a], proj[b]
        if (pa[2] & 7) or (pb[2] & 7):
            skip += 1
            continue
        kind, seg = clip_edge((pa[0], pa[1]), (pb[0], pb[1]), w, h)
        if kind == "skip":
            skip += 1
        elif kind == "cull":
            cull += 1
        else:
            cmds.append(line_bytes(*seg, color, lop, ypage))
    return cmds, skip, len(cmds), cull


# ---------------------------------------------------------------- filled faces
def sat18(x):
    return max(-131071, min(131071, x))


def render_faces(cfg, verts, faces, lop, ypage, light):
    """faces: (i0, i1, i2, i3, nx, ny, nz, base). Returns (cmds, skip, draw, cull)."""
    w, h = cfg[16], cfg[17]
    m = cfg[0:9]
    proj = [core_model(cfg, v) for v in verts]            # sx, sy, z, flags
    lm = [sat18(sum(m[3 * i + j] * light[i] for i in range(3)) >> 14) for j in range(3)]
    vis, skip, cull = [], 0, 0
    for fidx, f in enumerate(faces):
        pv = [proj[f[k]] for k in range(4)]
        if any(p[3] & 7 for p in pv):
            skip += 1
            continue
        (x0, y0), (x1, y1), (x2, y2) = [(p[0], p[1]) for p in pv[:3]]
        area = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
        if area <= 0:
            cull += 1
            continue
        shade = sum(lm[j] * f[4 + j] for j in range(3)) >> 14
        lvl = 0 if shade <= 0 else min(6, (shade * 7) >> 14)
        key = sum(p[2] for p in pv)
        vis.append((key, fidx, (f[7] + lvl) & 0xFF))
    cmds = []
    for key, fidx, col in sorted(vis, key=lambda e: (-e[0], e[1])):
        f = faces[fidx]
        xs = [proj[f[k]][0] for k in range(4)]
        ys = [proj[f[k]][1] for k in range(4)]
        ymin, ymax = max(0, min(ys)), min(h - 1, max(ys))
        for y in range(ymin, ymax + 1):
            xl, xr = None, None
            for k in range(4):
                xa, ya, xb, yb = xs[k], ys[k], xs[(k + 1) & 3], ys[(k + 1) & 3]
                cand = []
                if ya == yb:
                    if y == ya:
                        cand = [xa, xb]
                elif min(ya, yb) <= y <= max(ya, yb):
                    cand = [xa + ((y - ya) * (xb - xa)) // (yb - ya)]
                for c in cand:
                    xl = c if xl is None or c < xl else xl
                    xr = c if xr is None or c > xr else xr
            if xl is None:
                continue
            cl, cr = max(xl, 0), min(xr, w - 1)
            if cl > cr:
                continue
            dy = (y + ypage) & 0x7FF
            nx = cr - cl
            cmds.append([cl & 0xFF, (cl >> 8) & 1, dy & 0xFF, dy >> 8,
                         nx & 0xFF, (nx >> 8) & 7, 0, 0, col, 0, 0x70 | (lop & 0xF)])
    return cmds, skip, len(vis), cull


# ---------------------------------------------------------------- models
def cube(s):
    v = [(x, y, z) for x in (-s, s) for y in (-s, s) for z in (-s, s)]
    e = [(i, j) for i in range(8) for j in range(i + 1, 8)
         if bin(i ^ j).count("1") == 1]
    return v, e


def random_mesh(rng, nv, ne, r):
    v = [tuple(rng.randint(-r, r) for _ in range(3)) for _ in range(nv)]
    e = [(rng.randrange(nv), rng.randrange(nv)) for _ in range(ne)]
    return v, e


def sphere(rng, n_lat, n_lon, r):
    v, e = [], []
    for i in range(n_lat):
        th = math.pi * (i + 1) / (n_lat + 1)
        for j in range(n_lon):
            ph = 2 * math.pi * j / n_lon
            v.append((round(r * math.sin(th) * math.cos(ph)),
                      round(r * math.cos(th)),
                      round(r * math.sin(th) * math.sin(ph))))
    for i in range(n_lat):
        for j in range(n_lon):
            k = i * n_lon + j
            e.append((k, i * n_lon + (j + 1) % n_lon))
            if i + 1 < n_lat:
                e.append((k, k + n_lon))
    return v, e[:255]


def s16(x):
    return x & 0xFFFF


class Stim:
    def __init__(self):
        self.ops = []

    def w(self, sel, b):
        self.ops.append(f"W {sel} {b & 0xFF:02x}")

    def words(self, start_idx, words):
        self.w(0, start_idx)
        for x in words:
            self.w(1, s16(x) & 0xFF)
            self.w(1, s16(x) >> 8)


def main():
    n_scenes = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    rng = random.Random(99680)
    st = Stim()
    frame = 0
    totals = {"draw": 0, "skip": 0, "cull": 0}

    for sc in range(n_scenes):
        kind = sc % 4
        if kind == 0:
            verts, edges = cube(rng.choice([100, 300, 800]))
        elif kind == 1:
            verts, edges = sphere(rng, rng.randint(4, 9), rng.randint(6, 14), rng.choice([300, 900]))
        elif kind == 2:
            verts, edges = random_mesh(rng, rng.randint(2, 200), rng.randint(1, 255), rng.choice([200, 1500, 6000]))
        else:
            verts, edges = random_mesh(rng, rng.randint(2, 60), rng.randint(1, 120), 20000)
        verts = verts[:255]
        edges = [(a % len(verts), b % len(verts)) for a, b in edges][:255]

        w, h = rng.choice([(256, 212), (512, 212), (256, 192), (512, 424)])
        f = rng.choice([128, 192, 256, 320])
        color = rng.randint(0, 255)
        lop = rng.randint(0, 15)
        ypage = rng.choice([0, 256, 512, 768])

        # upload static config + model once per scene
        st.words(0x18, [f, w // 2, h // 2, rng.choice([8, 16, 32]), w, h])
        st.w(0, 0x40)
        for x in (0, 0, len(verts), len(edges), color, lop, ypage & 0xFF, ypage >> 8):
            st.w(1, x)
        st.w(0, 0x50)
        for v in verts:
            for c in v:
                st.w(1, s16(c) & 0xFF)
                st.w(1, s16(c) >> 8)
        st.w(0, 0x51)
        for a, b in edges:
            st.w(1, a)
            st.w(1, b)

        # frames: only the matrix + translation + RUN go over the bus
        for fr in range(rng.randint(2, 4)):
            m = rot_matrix(rng, rng.choice([1.0, 1.0, 0.7, 1.5]))
            tz = rng.choice([400, 1200, 3000, 8000, rng.randint(-500, 500)])
            t = [rng.randint(-600, 600), rng.randint(-600, 600), tz]
            st.words(0x00, m + t)
            st.w(0, 0x48)
            st.w(1, 0x01)
            st.ops.append("R")
            st.ops.append("K")
            st.ops.append(f"F {frame}")
            frame += 1

    # expected log = the stimulus replayed through the reference model
    log = replay(st.ops, totals)
    with open("scenes_stim.txt", "w") as fh:
        fh.write("\n".join(st.ops) + "\n")
    with open("scenes_expect.txt", "w") as fh:
        fh.write("\n".join(log) + "\n")
    print(f"{n_scenes} cenas, {frame} quadros, {sum(1 for o in st.ops if o.startswith('W'))} escritas do Z80; "
          f"LINEs esperados: {totals['draw']}, skip: {totals['skip']}, cull: {totals['cull']}")


def replay(ops, totals):
    """Interprets the Z80 byte stream exactly like the engine's register window."""
    regs = {"widx": 0, "lo": 0, "vaddr": 0, "eaddr": 0, "nvert": 0, "nedge": 0,
            "color": 15, "lop": 0, "ypage": 0, "vbuf": [], "ebuf": [], "fbuf": [],
            "faddr": 0, "nface": 0}
    light = [0, 0, -16384]
    fmem = [(0, 0, 0, 0, 0, 0, 0, 0)] * 256
    cfg = [0] * 9 + [0, 0, 0] + [256, 128, 106, 16, 256, 212]
    vmem = [(0, 0, 0)] * 256
    emem = [(0, 0)] * 256
    log = []
    pending = None
    last_counts = (0, 0, 0)

    def signed16(x):
        return x - 0x10000 if x & 0x8000 else x

    for op in ops:
        p = op.split()
        if p[0] == "W":
            sel, b = int(p[1]), int(p[2], 16)
            if sel == 0:
                regs["widx"] = b
                regs["vbuf"], regs["ebuf"], regs["fbuf"] = [], [], []
                continue
            i = regs["widx"]
            if i < 0x2A:
                if i % 2 == 0:
                    regs["lo"] = b
                else:
                    word = (b << 8) | regs["lo"]
                    wi = i >> 1
                    if wi < 18:
                        cfg[wi] = word if wi == 12 else signed16(word)
            elif i == 0x40: regs["vaddr"] = b
            elif i == 0x41: regs["eaddr"] = b
            elif i == 0x42: regs["nvert"] = b
            elif i == 0x43: regs["nedge"] = b
            elif i == 0x44: regs["color"] = b
            elif i == 0x45: regs["lop"] = b & 0xF
            elif i == 0x46: regs["ypage"] = (regs["ypage"] & 0x700) | b
            elif i == 0x47: regs["ypage"] = (regs["ypage"] & 0xFF) | ((b & 7) << 8)
            elif i == 0x48 and (b & 1):
                verts = vmem[:regs["nvert"]]
                if b & 2:
                    cmds, skip, draw, cull = render_faces(cfg, verts, fmem[:regs["nface"]],
                                                          regs["lop"], regs["ypage"], light)
                else:
                    edges = emem[:regs["nedge"]]
                    cmds, skip, draw, cull = render(cfg, verts, edges, regs["color"], regs["lop"], regs["ypage"])
                pending = cmds
                last_counts = (skip, draw, cull)
                totals["draw"] += draw
                totals["skip"] += skip
                totals["cull"] += cull
            elif i == 0x50:
                regs["vbuf"].append(b)
                if len(regs["vbuf"]) == 6:
                    vb = regs["vbuf"]
                    vmem[regs["vaddr"]] = tuple(signed16(vb[2 * k] | (vb[2 * k + 1] << 8)) for k in range(3))
                    regs["vaddr"] = (regs["vaddr"] + 1) & 0xFF
                    regs["vbuf"] = []
            elif i == 0x52:
                regs["fbuf"].append(b)
                if len(regs["fbuf"]) == 11:
                    fb = regs["fbuf"]
                    fmem[regs["faddr"]] = (fb[0], fb[1], fb[2], fb[3],
                                           signed16(fb[4] | fb[5] << 8), signed16(fb[6] | fb[7] << 8),
                                           signed16(fb[8] | fb[9] << 8), fb[10])
                    regs["faddr"] = (regs["faddr"] + 1) & 0xFF
                    regs["fbuf"] = []
            elif i == 0x58: regs["faddr"] = b
            elif i == 0x59: regs["nface"] = b
            elif i in (0x5A, 0x5C, 0x5E): regs["lo"] = b
            elif i in (0x5B, 0x5D, 0x5F): light[(i - 0x5B) // 2] = signed16((b << 8) | regs["lo"])
            elif i == 0x51:
                regs["ebuf"].append(b)
                if len(regs["ebuf"]) == 2:
                    emem[regs["eaddr"]] = tuple(regs["ebuf"])
                    regs["eaddr"] = (regs["eaddr"] + 1) & 0xFF
                    regs["ebuf"] = []
            # index advance (same rule as next_idx in RTL)
            if i >> 4 == 4:
                regs["widx"] = 0x40 | ((i + 1) & 0xF)
            elif i >> 3 == 0x0B:
                regs["widx"] = 0x58 | ((i + 1) & 0x7)
            elif i == 0x29:
                regs["widx"] = 0x24
            elif i < 0x40:
                regs["widx"] = i + 1
        elif p[0] == "R":
            for c in pending or []:
                log.append("L " + " ".join(f"{x:02x}" for x in c))
            pending = None
        elif p[0] == "K":
            log.append("K %04x %04x %04x" % last_counts)
        elif p[0] == "F":
            log.append(op)
    return log


if __name__ == "__main__":
    main()
