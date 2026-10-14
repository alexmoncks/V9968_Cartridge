#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Benchmark stimuli for geo3d v2.0 against v1.0.0 (run by run_v2.sh).

  bench_v2.py gen [gen.json]    writes
      bench_res1_stim.txt   a resident scene the v1.0.0 way: a figure of
                            rigid parts, one transform-only RUN per part
                            (NVERT = the end of the part: every RUN goes over
                            the vertices from 0 again), a RUN that hides
                            everything, a last RUN that sorts and paints
      bench_res2_stim.txt   the same frames the v2.0 way: VFIRST and NVERT
                            give each RUN its own part only, FFIRST and
                            NFACE give the last RUN the figures' faces only
                            (no hiding RUN)
      bench_big_stim.txt    v2.0 only: 1023 faces, all visible
    and the *_expect.txt of each (the model). The two resident stimuli must
    paint the same picture: the L lines of their logs are compared.
    With gen.json (GEO FIGHTER's out/gen.json) the figures are that game's
    two fighters and its ring, in poses of a fight; without it, two figures
    of 11 boxes each over a floor.
  bench_v2.py report NAME=TIMEFILE ...
      a table of the engine clocks with RUN busy = 1 per frame (the +time
      output of tb_engine.v), in clocks and in ms at 42.95 MHz
"""
import json
import random
import sys

from gen_scenes import replay
from gen_face_scenes import box
from gen_v2_scenes import S2, grid, rnd_normal, rot_xyz

CLK = 42.955e6
IDENT = [16384, 0, 0, 0, 16384, 0, 0, 0, 16384]


def boxes_scene(rng):
    """two figures of 11 boxes each and a floor -> the dict build() takes"""
    verts, faces, ranges = [], [], [[], []]
    floor_f = []
    for slot in range(2):
        for part in range(11):
            a = len(verts)
            v, f = box(0, 0, 0, rng.randint(8, 16), rng.randint(14, 30), rng.randint(8, 16), 16 + 16 * slot, 24 + 16 * slot, a)
            verts += v
            faces += f
            ranges[slot].append((a, len(verts)))
    a = len(verts)
    verts += [(-240, 0, -240), (240, 0, -240), (240, 0, 240), (-240, 0, 240)]
    floor_f.append((a, a + 3, a + 2, a + 1, 0, 16384, 0, 40))
    cam = rot_xyz(0.42, 0.49, 0.0)
    frames = []
    for fr in range(8):
        recs = []
        for slot in range(2):
            x0 = (-60, 60)[slot] + rng.randint(-20, 20)
            rs = []
            for part in range(11):
                m = rot_xyz(rng.uniform(-0.6, 0.6), rng.uniform(-3, 3), rng.uniform(-0.6, 0.6))
                rs.append(m + [x0 + rng.randint(-25, 25), rng.randint(-60, 80), 560 + rng.randint(-30, 30)])
            recs.append(rs)
        frames.append((cam + [0, -57, 585], recs))
    return dict(verts=verts, nring=1, faces=floor_f + faces, ranges=ranges, frames=frames,
                view=dict(F=360, CX=128, CY=104, ZNEAR=16, W=256, H=192),
                light=[-1376, 8083, -14184], light_ring=[5444, 2028, -15319])


def fighter_scene(path, rng):
    """GEO FIGHTER's scene (its out/gen.json), in poses of a fight"""
    g = json.load(open(path))
    k = g["kvec"]
    poses = [p for p in (0, 2, 6, 19, 28, 36, 43, 64, 71, 78, 87, 98, 108, 114) if p < g["poses"]]
    frames = []
    for fr in range(8):
        x = [-rng.randint(36, 90), rng.randint(36, 90)]
        camx = (x[0] + x[1]) // 2
        recs = []
        for slot in range(2):
            pose = rng.choice(poses)
            rs = []
            for rec in g["pose_recs"][slot][pose][slot]:
                d = x[slot] - camx
                rs.append(rec[:9] + [rec[9 + i] + ((abs(d) * k[i]) >> 14) * (1 if d >= 0 else -1) for i in range(3)])
            recs.append(rs)
        ring = g["ring_rec"]
        frames.append((ring[:9] + [ring[9 + i] + ((abs(camx) * k[i]) >> 14) * (-1 if camx >= 0 else 1) for i in range(3)], recs))
    faces = [(*f[0], *f[1], f[2]) for f in g["faces"]]
    return dict(verts=[tuple(int(c) for c in v) for v in g["vfight"]], nring=g["nface_ring"], faces=faces,
                ranges=g["ranges"], frames=frames, view=g["view"], light=g["light0"], light_ring=g["light_ring"])


def build(sc, v2):
    """the Z80 traffic of the scene's frames; faces: the ring's first, the
    ring's vertices after the figures'"""
    st = S2()
    vw = sc["view"]
    st.words(0x18, [vw["F"], vw["CX"], vw["CY"], vw["ZNEAR"], vw["W"], vw["H"]])
    st.view(15, 0, 0)
    nv, nf, nring = len(sc["verts"]), len(sc["faces"]), sc["nring"]
    nvfig = sc["ranges"][1][-1][1]
    wide = nv > 256 or nf > 256                            # (geo3d v2.0 only)
    if wide:
        st.ext(1)
    st.set10("vaddr", 0)
    st.vertices(sc["verts"])
    st.set10("faddr", 0)
    st.faces(sc["faces"], wide)
    st.reg(0x43, 0)                                         # no edges
    for ring, recs in sc["frames"]:
        # the ring
        st.light(sc["light_ring"])
        if v2:
            st.first("vfirst", nvfig)
            st.set10("nvert", nv - nvfig)
            st.first("ffirst", 0)
        else:
            st.set10("nvert", nv)
        st.set10("nface", nring)
        st.matrix(ring[:9], ring[9:])
        st.run(0x03, mark=False)
        st.set10("nface", 0)
        if not v2:
            st.reg(0x16, 0x00, 0xF8)                        # TZ = -2048: every vertex hidden
            st.run(0x03, mark=False)
        # one transform-only RUN per part: the second figure, then the first
        for slot in (1, 0):
            for part in range(len(recs[slot]) - 1, -1, -1):
                a, b = sc["ranges"][slot][part]
                if v2:
                    st.first("vfirst", a)
                    st.set10("nvert", b - a)
                else:
                    st.set10("nvert", b)
                st.matrix(recs[slot][part][:9], recs[slot][part][9:])
                st.run(0x03, mark=False)
        # sort and paint the figures
        st.light(sc["light"])
        st.set10("nvert", 0)
        if v2:
            st.first("ffirst", nring)
            st.set10("nface", nf - nring)
        else:
            st.set10("nface", nf)
        st.matrix(IDENT, [0, 0, 0])
        st.run(0x03)
    return st


def big(rng):
    """v2.0 only: 1023 faces, all visible (see gen_v2_scenes.py)"""
    st = S2()
    verts, quads = grid(32, 30, 24, rng, 20)
    faces = [(*q, *rnd_normal(rng), rng.choice([8, 16, 40, 100])) for q in quads]
    while len(faces) < 1023:
        faces.append((*quads[rng.randrange(len(quads))], *rnd_normal(rng), 60))
    rng.shuffle(faces)
    st.camera(256, 256, 212, 16)
    st.view(15, 0, 0)
    st.light([4000, 9000, -12000])
    st.ext(1)
    st.set10("vaddr", 0)
    st.vertices(verts)
    st.set10("faddr", 0)
    st.faces(faces, True)
    st.set10("nvert", len(verts))
    st.set10("nface", len(faces))
    for fr in range(4):
        st.matrix(rot_xyz(0.1 * fr, -0.08 * fr, 0.3 * fr), [0, 0, 1800 + 150 * fr])
        st.run(0x03)
    return st


def write(name, st):
    totals = {"draw": 0, "skip": 0, "cull": 0}
    log = replay(st.ops, totals)
    open(name + "_stim.txt", "w").write("\n".join(st.ops) + "\n")
    open(name + "_expect.txt", "w").write("\n".join(log) + "\n")
    return log, totals


def gen(path):
    rng = random.Random(99201)
    sc = fighter_scene(path, rng) if path else boxes_scene(rng)
    spans = lambda l: [x for x in l if x[0] in "LM"]
    l2, t2 = write("bench_res2", build(sc, True))
    if len(sc["verts"]) <= 255 and len(sc["faces"]) <= 255:
        l1, t1 = write("bench_res1", build(sc, False))
        if spans(l1) != spans(l2):
            sys.exit("the two resident stimuli do not paint the same picture")
        same = "the v1.0.0 and the v2.0 traffic paint the same picture"
    else:
        same = "too large for v1.0.0: no bench_res1"
    lb, tb = write("bench_big", big(rng))
    print("resident scene (%s): %d vertices, %d faces, %d frames, %d faces painted, %d spans; %s"
          % ("GEO FIGHTER" if path else "boxes", len(sc["verts"]), len(sc["faces"]), len(sc["frames"]),
             t2["draw"], len(spans(l2)), same))
    print("big scene: %d faces painted in 4 frames, %d spans" % (tb["draw"], len(spans(lb))))


def report(args):
    print("%-34s %7s %12s %12s %9s %9s" % ("", "frames", "clocks/frame", "max", "ms/frame", "max ms"))
    for a in args:
        name, path = a.split("=", 1)
        c = [int(l.split()[1]) for l in open(path) if l.strip()]
        avg, mx = sum(c) / len(c), max(c)
        print("%-34s %7d %12.0f %12d %9.2f %9.2f" % (name, len(c), avg, mx, 1000 * avg / CLK, 1000 * mx / CLK))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report(sys.argv[2:])
    else:
        gen(sys.argv[2] if len(sys.argv) > 2 else None)
