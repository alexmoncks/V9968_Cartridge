#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Random filled-face scenes for geo3d_engine (CTRL bit1 = 1).

Writes face_stim.txt / face_expect.txt in the same format as gen_scenes.py.
Scenes mix closed boxes (proper winding and normals, exercises culling and
painter's order), random quad soups (non-planar quads, triangles, arbitrary
normals and colours), arbitrary lights, off-screen and near-plane cameras.
"""
import random
import sys

from gen_scenes import Stim, replay, rot_matrix, s16


def box(cx, cy, cz, sx, sy, sz, base_front, base_side, first):
    v = [(cx + dx * sx, cy + dy * sy, cz + dz * sz)
         for dx in (-1, 1) for dy in (-1, 1) for dz in (-1, 1)]
    idx = lambda dx, dy, dz: first + ((dx > 0) << 2 | (dy > 0) << 1 | (dz > 0))
    faces = []
    for axis in range(3):
        for sgn in (-1, 1):
            n = [0, 0, 0]
            n[axis] = sgn
            # the 4 corners of this face, cyclic
            a, b = [k for k in range(3) if k != axis]
            corners = []
            for u, w in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                d = [0, 0, 0]
                d[axis], d[a], d[b] = sgn, u, w
                corners.append(d)
            p = [[c[0] * sx, c[1] * sy, c[2] * sz] for c in corners]
            e1 = [p[1][k] - p[0][k] for k in range(3)]
            e2 = [p[2][k] - p[0][k] for k in range(3)]
            cr = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
            if sum(cr[k] * n[k] for k in range(3)) < 0:
                corners.reverse()
            ids = [idx(*c) for c in corners]
            base = base_front if axis == 2 else base_side
            faces.append((*ids, n[0] * 16384, n[1] * 16384, n[2] * 16384, base))
    return v, faces


def main():
    n_scenes = int(sys.argv[1]) if len(sys.argv) > 1 else 16
    rng = random.Random(99681)
    st = Stim()
    frames = 0
    for sc in range(n_scenes):
        verts, faces = [], []
        if sc % 3 != 2:
            for _ in range(rng.randint(1, 8)):
                if len(verts) + 8 > 255 or len(faces) + 6 > 255:
                    break
                v, f = box(rng.randint(-400, 400), rng.randint(-300, 300), rng.randint(-400, 400),
                           rng.randint(20, 200), rng.randint(20, 200), rng.randint(20, 200),
                           rng.choice([1, 8]), rng.choice([1, 8]), len(verts))
                verts += v
                faces += f
        else:
            nv = rng.randint(4, 120)
            verts = [tuple(rng.randint(-rng.choice([300, 3000]), 300) for _ in range(3)) for _ in range(nv)]
            for _ in range(rng.randint(1, 200)):
                i = [rng.randrange(nv) for _ in range(4)]
                if rng.random() < 0.3:
                    i[3] = i[2]                       # triangle
                faces.append((*i, rng.randint(-16384, 16384), rng.randint(-16384, 16384),
                              rng.randint(-16384, 16384), rng.randint(0, 255)))

        w, h = rng.choice([(256, 212), (512, 212), (256, 192), (512, 424)])
        f = rng.choice([160, 256, 320])
        lop = rng.randint(0, 15)
        ypage = rng.choice([0, 256, 512])
        light = [rng.randint(-20000, 20000) for _ in range(3)]

        st.words(0x18, [f, w // 2, h // 2, rng.choice([8, 16, 64]), w, h])
        st.w(0, 0x40)
        for x in (0, 0, len(verts), 0, 15, lop, ypage & 0xFF, ypage >> 8):
            st.w(1, x)
        st.w(0, 0x58)
        for x in [0, len(faces)] + [b for l in light for b in (s16(l) & 0xFF, s16(l) >> 8)]:
            st.w(1, x)
        st.w(0, 0x50)
        for v in verts:
            for c in v:
                st.w(1, s16(c) & 0xFF)
                st.w(1, s16(c) >> 8)
        st.w(0, 0x52)
        for fc in faces:
            for b in fc[:4]:
                st.w(1, b)
            for nrm in fc[4:7]:
                st.w(1, s16(nrm) & 0xFF)
                st.w(1, s16(nrm) >> 8)
            st.w(1, fc[7])

        for fr in range(rng.randint(2, 3)):
            m = rot_matrix(rng, rng.choice([1.0, 1.0, 0.8, 1.4]))
            tz = rng.choice([500, 900, 1500, 3000, rng.randint(-300, 400)])
            t = [rng.randint(-500, 500), rng.randint(-400, 400), tz]
            st.words(0x00, m + t)
            st.w(0, 0x48)
            st.w(1, 0x03)            # RUN, filled faces
            st.ops += ["R", "K", f"F {frames}"]
            frames += 1

    totals = {"draw": 0, "skip": 0, "cull": 0}
    log = replay(st.ops, totals)
    open("face_stim.txt", "w").write("\n".join(st.ops) + "\n")
    open("face_expect.txt", "w").write("\n".join(log) + "\n")
    spans = sum(1 for l in log if l.startswith("L"))
    print(f"{n_scenes} cenas, {frames} quadros: {totals['draw']} faces desenhadas, "
          f"{totals['cull']} de costas, {totals['skip']} perto/saturadas, {spans} spans (LINE)")


if __name__ == "__main__":
    main()
