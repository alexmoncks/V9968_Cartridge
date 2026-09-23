#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Random textured-face scenes for geo3d_engine (CTRL = RUN | faces | textures).

Writes tex_stim.txt / tex_expect.txt. Mixes textured (BASE bit7) and solid
faces, random texture coordinates (flipped, stretched, degenerate), boxes and
quad soups, off-screen and near-plane cameras, texture origins and strides.
"""
import random
import sys

from gen_face_scenes import box
from gen_scenes import Stim, replay, rot_matrix, s16


def main():
    n_scenes = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    rng = random.Random(99682)
    st = Stim()
    frames = 0
    for sc in range(n_scenes):
        verts, faces = [], []
        if sc % 3 != 2:
            for _ in range(rng.randint(1, 6)):
                v, f = box(rng.randint(-400, 400), rng.randint(-300, 300), rng.randint(-400, 400),
                           rng.randint(30, 220), rng.randint(30, 220), rng.randint(30, 220),
                           rng.choice([1, 8]), rng.choice([1, 8]), len(verts))
                verts += v
                faces += f
        else:
            nv = rng.randint(4, 80)
            verts = [tuple(rng.randint(-rng.choice([300, 3000]), 300) for _ in range(3)) for _ in range(nv)]
            for _ in range(rng.randint(1, 120)):
                i = [rng.randrange(nv) for _ in range(4)]
                if rng.random() < 0.3:
                    i[3] = i[2]
                faces.append((*i, rng.randint(-16384, 16384), rng.randint(-16384, 16384),
                              rng.randint(-16384, 16384), rng.randint(0, 127)))
        # texture flag and coordinates per face
        uvs = []
        for k, fc in enumerate(faces):
            base = fc[7] | (0x80 if rng.random() < 0.7 else 0)
            faces[k] = (*fc[:7], base)
            kind = rng.random()
            if kind < 0.5:
                s_ = rng.choice([15, 31, 63, 127, 255])
                uv = [0, 0, s_, 0, s_, s_, 0, s_]
                r = rng.randrange(4)
                uv = uv[2 * r:] + uv[:2 * r]            # rotated mapping
            else:
                uv = [rng.randrange(256) for _ in range(8)]
            uvs.append(uv)

        w, h = rng.choice([(256, 212), (256, 192)])
        f = rng.choice([160, 256, 320])
        lop = rng.choice([0, 0, 8, 3])
        ypage = rng.choice([0, 256])
        light = [rng.randint(-20000, 20000) for _ in range(3)]
        texx, texy, tstride = rng.choice([0, 32, 300]), rng.choice([512, 520, 768, 7000]), rng.choice([0, 32, 36, 255])

        st.words(0x18, [f, w // 2, h // 2, rng.choice([8, 16, 64]), w, h])
        st.w(0, 0x40)
        for x in (0, 0, len(verts), 0, 15, lop, ypage & 0xFF, ypage >> 8):
            st.w(1, x)
        st.w(0, 0x58)
        for x in [0, len(faces)] + [b for l in light for b in (s16(l) & 0xFF, s16(l) >> 8)]:
            st.w(1, x)
        st.w(0, 0x60)
        for x in (texx & 0xFF, texx >> 8, texy & 0xFF, texy >> 8, tstride, 0):
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
        st.w(0, 0x53)
        for uv in uvs:
            for b in uv:
                st.w(1, b)

        for fr in range(rng.randint(2, 3)):
            m = rot_matrix(rng, rng.choice([1.0, 1.0, 0.8, 1.4]))
            tz = rng.choice([500, 900, 1500, 3000, rng.randint(-300, 400)])
            t = [rng.randint(-500, 500), rng.randint(-400, 400), tz]
            st.words(0x00, m + t)
            st.w(0, 0x48)
            st.w(1, rng.choice([0x07, 0x07, 0x07, 0x03]))   # textures on (mostly)
            st.ops += ["R", "K", f"F {frames}"]
            frames += 1

    totals = {"draw": 0, "skip": 0, "cull": 0}
    log = replay(st.ops, totals)
    open("tex_stim.txt", "w").write("\n".join(st.ops) + "\n")
    open("tex_expect.txt", "w").write("\n".join(log) + "\n")
    nm = sum(1 for l in log if l.startswith("M"))
    nl = sum(1 for l in log if l.startswith("L"))
    print(f"{n_scenes} cenas, {frames} quadros: {totals['draw']} faces, {nm} spans LRMM, {nl} spans LINE")


if __name__ == "__main__":
    main()
