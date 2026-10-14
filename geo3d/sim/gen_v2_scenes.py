#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Scenes for what geo3d v2.0 adds to v1.0.0 (run by run_v2.sh).

Writes v2_stim.txt / v2_expect.txt in the format of gen_scenes.py, whose
replay() is the reference model. What the scenes exercise:

  big      models of up to 1023 vertices, edges and faces in the extended
           stream formats (3-byte edges, 12-byte faces), textured or not,
           with faces as far as one another (the order then goes by the
           face number) and all the faces visible at once (the deepest heap)
  ranges   VFIRST, EFIRST and FFIRST: a RUN that transforms, draws edges or
           paints faces from any entry on, also around the end of the RAMs
  resident a scene loaded once and moved part by part: one transform-only
           RUN per part (its own vertices only), then RUNs that only paint
  compat   the write pointers in the v1.0.0 mode (the low byte wraps, the
           high bits stay) and in the extended mode (10 bits), v1.0.0
           streams with the 10-bit counts, the registers read back
"""
import math
import random
import sys

from gen_scenes import Stim, replay, rot_matrix, s16

IDX = {"vaddr": (0x40, 0x68), "eaddr": (0x41, 0x69), "nvert": (0x42, 0x6A), "nedge": (0x43, 0x6B),
       "faddr": (0x58, 0x6C), "nface": (0x59, 0x6D), "taddr": (0x65, 0x6E)}
FIRST = {"vfirst": 0x54, "ffirst": 0x56, "efirst": 0x66}


def q14(x):
    return int(round(x * 16384))


def rot_xyz(ax, ay, az, scale=1.0):
    """Rz * Ry * Rx as 9 Q2.14 words"""
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    mul = lambda a, b: [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    m = mul(rz, mul(ry, rx))
    return [q14(m[i][j] * scale) for i in range(3) for j in range(3)]


class S2(Stim):
    """Stimulus with the v2.0 registers"""
    def __init__(self):
        super().__init__()
        self.frame = 0

    def reg(self, idx, *data):
        self.w(0, idx)
        for b in data:
            self.w(1, b)

    def set10(self, name, value):
        """a 10-bit register: its low byte, then its high bits"""
        lo, hi = IDX[name]
        self.reg(lo, value & 0xFF)
        self.reg(hi, value >> 8)

    def first(self, name, value):
        self.reg(FIRST[name], value & 0xFF, value >> 8)

    def ext(self, on):
        self.reg(0x6F, 1 if on else 0)

    def camera(self, f, w, h, znear):
        self.words(0x18, [f, w // 2, h // 2, znear, w, h])

    def view(self, color, lop, ypage):
        self.reg(0x44, color, lop, ypage & 0xFF, ypage >> 8)

    def light(self, l):
        self.reg(0x5A, *[b for x in l for b in (s16(x) & 0xFF, s16(x) >> 8)])

    def vertices(self, verts):
        self.w(0, 0x50)
        for v in verts:
            for c in v:
                self.w(1, s16(c) & 0xFF)
                self.w(1, s16(c) >> 8)

    def edges(self, edges, ext):
        self.w(0, 0x51)
        for a, b in edges:
            self.w(1, a & 0xFF)
            self.w(1, b & 0xFF)
            if ext:
                self.w(1, (a >> 8) | ((b >> 8) << 2))

    def faces(self, faces, ext):
        self.w(0, 0x52)
        for fc in faces:
            for i in fc[:4]:
                self.w(1, i & 0xFF)
            if ext:
                self.w(1, sum((fc[k] >> 8) << (2 * k) for k in range(4)))
            for nrm in fc[4:7]:
                self.w(1, s16(nrm) & 0xFF)
                self.w(1, s16(nrm) >> 8)
            self.w(1, fc[7])

    def uvs(self, uvs):
        self.w(0, 0x53)
        for uv in uvs:
            for b in uv:
                self.w(1, b)

    def texture(self, texx, texy, tstride):
        self.reg(0x60, texx & 0xFF, texx >> 8, texy & 0xFF, texy >> 8, tstride)

    def matrix(self, m, t):
        self.words(0x00, m + t)

    def run(self, ctrl, mark=True):
        self.reg(0x48, ctrl)
        self.ops.append("R")
        if mark:
            self.ops += ["K", "F %d" % self.frame]
            self.frame += 1

    def get(self, idx, count=1):
        self.ops.append("G %02x %d" % (idx, count))


def grid(gx, gy, step, rng, bump):
    """(gx + 1) x (gy + 1) vertices, gx x gy quads seen from -z"""
    verts = [((i - gx / 2) * step, (j - gy / 2) * step, rng.randint(-bump, bump))
             for j in range(gy + 1) for i in range(gx + 1)]
    verts = [tuple(int(c) for c in v) for v in verts]
    v = lambda i, j: j * (gx + 1) + i
    quads = [(v(i, j), v(i, j + 1), v(i + 1, j + 1), v(i + 1, j)) for j in range(gy) for i in range(gx)]
    return verts, quads


def rnd_normal(rng):
    return [rng.randint(-16384, 16384) for _ in range(3)]


def scene_big_faces(st, rng, textured):
    """a grid of nearly 1023 vertices and faces, all visible when it faces
    the camera; faces repeated at other numbers (as far as the original)"""
    gx, gy = rng.choice([(30, 31), (32, 30), (40, 23)])
    verts, quads = grid(gx, gy, rng.choice([24, 30]), rng, rng.choice([0, 20, 60]))
    faces = []
    for q in quads:
        base = rng.choice([8, 16, 40, 100])
        if textured and rng.random() < 0.5:
            base |= 0x80
        faces.append((*q, *rnd_normal(rng), base))
    while len(faces) < 1023:                # the same quads again, other numbers
        src = faces[rng.randrange(len(quads))]
        faces.append((*src[:4], *rnd_normal(rng), src[7]))
    rng.shuffle(faces)
    w, h = rng.choice([(256, 212), (512, 424)])
    st.camera(rng.choice([200, 256]), w, h, 16)
    st.view(15, rng.choice([0, 0, 3]), rng.choice([0, 256]))
    st.light([rng.randint(-20000, 20000) for _ in range(3)])
    st.ext(1)
    st.set10("vaddr", 0)
    st.vertices(verts)
    st.set10("faddr", 0)
    st.faces(faces, True)
    if textured:
        st.set10("taddr", 0)
        st.uvs([[rng.randrange(256) for _ in range(8)] for _ in range(1024)])    # (all: back at 0)
        st.texture(rng.choice([0, 64]), rng.choice([512, 768]), rng.choice([0, 32]))
    st.set10("nvert", len(verts))
    st.set10("nface", len(faces))
    st.first("vfirst", 0)
    st.first("ffirst", 0)
    ctrl = 0x07 if textured else 0x03
    for fr in range(3):
        a = [0.0, 0.0, 0.0] if fr == 0 else [rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5), rng.uniform(-3, 3)]
        st.matrix(rot_xyz(*a), [rng.randint(-60, 60), rng.randint(-40, 40), rng.choice([1500, 2200, 3000])])
        st.run(ctrl)
    # only some of the faces: from FFIRST on, around the end of the face RAM
    st.set10("faddr", 1023)
    st.faces([faces[5]], True)              # face 1023, then the pointer wraps to 0
    ff = rng.randint(900, 1015)
    st.first("ffirst", ff)
    st.set10("nface", 1024 - ff + rng.randint(1, 40))
    st.set10("nvert", 0)                    # the projections of the last frame
    st.run(ctrl)
    st.first("ffirst", 0)


def scene_big_edges(st, rng):
    """a cloud of up to 1023 vertices and 1023 edges"""
    nv = rng.randint(600, 1023)
    r = rng.choice([400, 1500])
    verts = [tuple(rng.randint(-r, r) for _ in range(3)) for _ in range(nv)]
    edges = [(rng.randrange(nv), rng.randrange(nv)) for _ in range(1024)]
    w, h = rng.choice([(256, 212), (512, 212)])
    st.camera(rng.choice([128, 256]), w, h, rng.choice([8, 32]))
    st.view(rng.randint(0, 255), rng.randint(0, 15), rng.choice([0, 256, 768]))
    st.ext(1)
    st.set10("vaddr", 0)
    st.vertices(verts)
    st.set10("eaddr", 0)
    st.edges(edges, True)                   # all 1024: the pointer is back at 0
    st.set10("nvert", nv)
    st.first("vfirst", 0)
    for fr in range(3):
        ne = rng.randint(500, 1023)
        ef = 0 if fr == 0 else rng.randint(0, 1023)     # may go around the end
        st.set10("nedge", ne)
        st.first("efirst", ef)
        st.matrix(rot_matrix(rng, 1.0), [rng.randint(-300, 300), rng.randint(-300, 300),
                                         rng.choice([1200, 3000, 6000])])
        st.run(0x01)
    # a second transform of a part of the cloud only, with another matrix
    vf = rng.randint(100, nv - 50)
    st.first("vfirst", vf)
    st.set10("nvert", rng.randint(1, nv - vf))
    st.matrix(rot_matrix(rng, 1.3), [0, 0, rng.choice([900, 2500])])
    st.run(0x01)
    st.first("vfirst", 0)
    st.first("efirst", 0)


def box(cx, cy, cz, sx, sy, sz, first):
    from gen_face_scenes import box as box1
    return box1(cx, cy, cz, sx, sy, sz, 8, 24, first)


def scene_resident(st, rng):
    """parts spread over the vertex RAM, each one moved by its own
    transform-only RUN; then RUNs that only paint"""
    nparts = rng.randint(20, 60)
    slots = rng.sample(range(0, 1016, 8), nparts)          # 8 vertices each, anywhere
    verts, faces, part_faces = {}, [], []
    f0 = rng.randint(0, 1023 - 6 * nparts)
    for p, v0 in enumerate(slots):
        v, f = box(0, 0, 0, rng.randint(15, 60), rng.randint(15, 60), rng.randint(15, 60), v0)
        verts[v0] = v
        part_faces.append((f0 + len(faces), len(f)))
        faces += f
    w, h = 256, rng.choice([192, 212])
    st.camera(rng.choice([256, 360]), w, h, 16)
    st.view(15, 0, rng.choice([0, 256]))
    st.light([-3000, 9000, -13000])
    st.ext(1)
    for v0, v in verts.items():
        st.set10("vaddr", v0)
        st.vertices(v)
    st.set10("faddr", f0)
    st.faces(faces, True)
    st.set10("nedge", 0)
    for fr in range(3):
        # each part: its own 8 vertices, its own matrix and place
        for p, v0 in enumerate(slots):
            st.first("vfirst", v0)
            st.set10("nvert", 8)
            a = [rng.uniform(-3, 3) for _ in range(3)]
            st.matrix(rot_xyz(*a), [rng.randint(-250, 250), rng.randint(-180, 180), rng.randint(700, 1800)])
            if rng.random() < 0.5:
                st.set10("nface", 0)
                st.run(0x03, mark=False)                    # face mode, no faces
            else:
                st.run(0x01, mark=False)                    # edge mode, no edges
        # paint: everything in one RUN ...
        st.set10("nvert", 0)
        st.first("ffirst", f0)
        st.set10("nface", len(faces))
        st.matrix(rot_xyz(0, 0, 0), [0, 0, 0])              # (the light goes with it)
        st.run(0x03)
        # ... and a few parts alone
        for p in rng.sample(range(nparts), 3):
            st.first("ffirst", part_faces[p][0])
            st.set10("nface", part_faces[p][1])
            st.run(0x03)
    st.first("vfirst", 0)
    st.first("ffirst", 0)


def scene_compat(st, rng):
    """the write pointers of v1.0.0 and of the extended mode, v1.0.0
    streams with 10-bit counts, the registers read back"""
    w, h = 256, 212
    st.camera(256, w, h, 16)
    st.view(rng.randint(1, 255), 0, 0)
    cloud = lambda n, r=500: [tuple(rng.randint(-r, r) for _ in range(3)) for _ in range(n)]
    # v1.0.0 mode with the high bits set: 300 vertices from 0x1E0 on go
    # around inside 0x100-0x1FF
    st.ext(0)
    st.set10("vaddr", 0x1E0)
    st.vertices(cloud(300))
    st.get(0x40, 8)
    st.get(0x68)
    st.get(0x6F)
    # ... and the v1.0.0 edges (2 bytes) reach vertices 0-255 only
    st.set10("vaddr", 0)
    st.vertices(cloud(256))
    st.set10("eaddr", 0x2F0)
    v1_edges = [(rng.randrange(256), rng.randrange(256)) for _ in range(40)]
    st.edges(v1_edges, False)               # 0x2F0-0x2FF, then 0x200-0x217
    st.get(0x41)
    st.get(0x69)
    st.set10("nvert", 512)                  # a 10-bit count in the v1.0.0 mode
    st.first("vfirst", 0)
    st.matrix(rot_matrix(rng, 1.0), [0, 0, 2000])
    st.first("efirst", 0x2F0)
    st.set10("nedge", 16)
    st.run(0x01)
    st.first("efirst", 0x200)
    st.set10("nedge", 24)
    st.set10("nvert", 0)
    st.run(0x01)
    # extended edges over the vertices that went around
    st.ext(1)
    st.set10("eaddr", 0)
    st.edges([(0x100 + rng.randrange(256), 0x100 + rng.randrange(256)) for _ in range(200)], True)
    st.first("efirst", 0)
    st.set10("nedge", 200)
    st.run(0x01)
    # extended mode: 40 vertices from 0x3F0 on go around the end of the RAM
    st.set10("vaddr", 0x3F0)
    st.vertices(cloud(40))
    st.get(0x40)
    st.get(0x68)
    st.first("vfirst", 0x3F0)
    st.set10("nvert", 40)                   # 0x3F0-0x3FF, then 0-0x17
    st.matrix(rot_matrix(rng, 1.0), [50, -30, 1500])
    st.set10("eaddr", 0)
    st.edges([((0x3F0 + rng.randrange(40)) & 0x3FF, (0x3F0 + rng.randrange(40)) & 0x3FF)
              for _ in range(60)], True)
    st.set10("nedge", 60)
    st.run(0x01)
    # v1.0.0 faces (11 bytes) and texture coordinates with the high bits set
    st.ext(0)
    v, f = box(0, 0, 0, 120, 90, 150, 0)
    f = [(*fc[:7], fc[7] | (0x80 if k % 2 else 0)) for k, fc in enumerate(f)]
    st.set10("vaddr", 0)
    st.vertices(v)
    st.set10("faddr", 0x3FD)
    st.faces(f, False)                      # 0x3FD-0x3FF, then 0x300-0x302
    st.get(0x58)
    st.get(0x6C)
    st.set10("taddr", 0x3FD)
    st.uvs([[rng.randrange(256) for _ in range(8)] for _ in range(3)])      # 0x3FD-0x3FF
    st.get(0x65)
    st.get(0x6E)
    st.set10("taddr", 0x300)
    st.uvs([[rng.randrange(256) for _ in range(8)] for _ in range(3)])
    st.texture(16, 512, 32)
    st.light([4000, 8000, -12000])
    st.first("vfirst", 0)
    st.set10("nvert", 8)
    for ff, nf in ((0x3FD, 3), (0x300, 3)):
        st.first("ffirst", ff)
        st.set10("nface", nf)
        st.matrix(rot_xyz(0.6, 0.7, 0.2), [0, 0, 900])
        st.run(0x07)
    # every v2.0 register, written through its block and read back
    st.reg(0x68, 1, 2, 3, 0, 1, 2, 3, 1)    # 68h-6Fh in one go
    st.reg(0x54, 0x21, 3, 0x43, 2)          # VFIRST, FFIRST
    st.reg(0x66, 0x65, 1)                   # EFIRST (the block goes on to 60h)
    for i in (0x49, 0x54, 0x55, 0x56, 0x57, 0x66, 0x67, 0x68, 0x69, 0x6A, 0x6B, 0x6C, 0x6D, 0x6E, 0x6F,
              0x50, 0x60, 0x70):
        st.get(i)
    st.get(0x40, 8)
    # back to the reset values, as a v1.0.0 program expects them
    st.reg(0x68, 0, 0, 0, 0, 0, 0, 0, 0)
    st.reg(0x54, 0, 0, 0, 0)
    st.reg(0x66, 0, 0)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    out = sys.argv[2] if len(sys.argv) > 2 else "v2"
    rng = random.Random(99200)
    st = S2()
    st.get(0x49)
    for k in range(n):
        scene_compat(st, rng)
        scene_big_edges(st, rng)
        scene_resident(st, rng)
        scene_big_faces(st, rng, textured=False)
        scene_big_faces(st, rng, textured=True)
        scene_compat(st, rng)               # (v1.0.0 mode again after the big ones)
    totals = {"draw": 0, "skip": 0, "cull": 0}
    log = replay(st.ops, totals)
    open(out + "_stim.txt", "w").write("\n".join(st.ops) + "\n")
    open(out + "_expect.txt", "w").write("\n".join(log) + "\n")
    print("%d rounds, %d frames, %d Z80 writes: %d drawn, %d culled, %d skipped; %d LINE, %d LRMM, %d register reads"
          % (n, st.frame, sum(1 for o in st.ops if o.startswith("W")), totals["draw"], totals["cull"],
             totals["skip"], sum(1 for l in log if l.startswith("L")), sum(1 for l in log if l.startswith("M")),
             sum(1 for l in log if l.startswith("G"))))


if __name__ == "__main__":
    main()
