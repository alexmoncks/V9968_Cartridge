#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Reference of the geo3d BASIC ROM's integer math (g3bank1.asm), bit for
bit: angles, sine, the rotation R = Ry * Rx * Rz, the camera matrix, unit
vectors, G3DATA's model processing (the VRAM model area), G3FRAME's geo3d
register values, and the VDP LINE rasteriser that turns geo3d's commands
(sim/gen_scenes.py) into SCREEN 5 pixels.

Used by test_math.py (the Z80 routines run in an emulator against these)
and check_frames.py (openMSX VRAM against this model). The float versions
(float_*) give the ideal values the integer ones are compared with.
"""
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "sim"))

from gen_tables import sintab  # noqa: E402

SIN = sintab()
ONE = 16384
IDENT = [ONE, 0, 0, 0, ONE, 0, 0, 0, ONE]


def s16(x):
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


# ---------------------------------------------------------------- angles
def getsin(a):
    s = (a + 2) & 0xFFFF
    q = s >> 14
    i = (s >> 2) & 0xFFF
    if q & 1:
        i = 4096 - i
    v = ONE if i == 4096 else SIN[i]
    return -v if q & 2 else v


def getcos(a):
    return getsin((a + 16384) & 0xFFFF)


def ang_units(r, g, neg):
    t = r * 65536 + g + 180
    u = 0 if (t >> 16) == 360 else t // 360
    return (-u) & 0xFFFF if neg else u & 0xFFFF


def dacang(valtyp, dac):
    """DE of dacang for VALTYP 2/4/8 and the 8 DAC bytes."""
    if valtyp == 2:
        v = s16(dac[2] | dac[3] << 8)
        return ang_units(abs(v) % 360, 0, v < 0)
    nd = 6 if valtyp == 4 else 14
    if dac[0] & 0x7F == 0:
        return 0
    neg = bool(dac[0] & 0x80)
    e = (dac[0] & 0x7F) - 64
    digits = []
    for b in dac[1:1 + (nd + 1) // 2]:
        digits += [b >> 4, b & 15]
    digits = digits[:nd]
    r = 0
    for i in range(1, e + 1):
        d = digits[i - 1] if i <= nd else 0
        r = (r * 10 + d) % 360
    g = 0
    for i in range(nd, max(e, 0), -1):
        g = (digits[i - 1] * 65536 + g) // 10
    if e < 0:
        for _ in range(-e):
            g //= 10
    return ang_units(r, g, neg)


def deg_units(deg):
    """The angle BASIC passes as `deg` (exactly representable values such as
    integers and halves), as dacang converts it."""
    neg = deg < 0
    a = abs(deg)
    r = int(a) % 360
    frac = a - int(a)
    # the decimal fraction digits, as BCD keeps them (up to 14 digits)
    s = ("%.14f" % frac)[2:]
    g = 0
    for ch in reversed(s):
        g = (int(ch) * 65536 + g) // 10
    return ang_units(r, g, neg)


# ---------------------------------------------------------------- Q2.14
def q(terms):
    """(sum of sign * a * b + 8192) >> 14, 32-bit, as qeval: (value, overflow)."""
    acc = (8192 + sum(sg * a * b for sg, a, b in terms)) & 0xFFFFFFFF
    ovf = ((acc >> 29) & 7) not in (0, 7)
    return s16(acc >> 14), ovf


def qv(terms):
    return q(terms)[0]


def rotmat(ax, ay, az):
    sx, cx = getsin(ax), getcos(ax)
    sy, cy = getsin(ay), getcos(ay)
    sz, cz = getsin(az), getcos(az)
    t1 = qv([(1, sx, sz)])
    t2 = qv([(1, sx, cz)])
    return [qv([(1, cy, cz), (1, sy, t1)]),
            qv([(1, cy, sz), (-1, sy, t2)]),
            qv([(1, sy, cx)]),
            qv([(-1, cx, sz)]),
            qv([(1, cx, cz)]),
            qv([(1, sx, ONE)]),
            qv([(1, cy, t1), (-1, sy, cz)]),
            qv([(-1, sy, sz), (-1, cy, t2)]),
            qv([(1, cy, cx)])]


def float_rot(ax, ay, az):
    """R = Ry(ay) Rx(ax) Rz(az), degrees, spec 3.2 conventions."""
    a, b, c = (math.radians(v) for v in (ax, ay, az))
    sx, cx, sy, cy, sz, cz = math.sin(a), math.cos(a), math.sin(b), math.cos(b), math.sin(c), math.cos(c)
    ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rx = [[1, 0, 0], [0, cx, sx], [0, -sx, cx]]
    rz = [[cz, sz, 0], [-sz, cz, 0], [0, 0, 1]]

    def mul(p, r):
        return [[sum(p[i][k] * r[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    m = mul(ry, mul(rx, rz))
    return [m[i][j] for i in range(3) for j in range(3)]


def matmul(c, r):
    return [qv([(1, c[3 * i + k], r[3 * k + j]) for k in range(3)]) for i in range(3) for j in range(3)]


def matvec(c, v):
    out, ovf = [], False
    for i in range(3):
        x, o = q([(1, c[3 * i + k], v[k]) for k in range(3)])
        out.append(x)
        ovf |= o
    return out, ovf


# ---------------------------------------------------------------- vectors
def rsqrt(s):
    if s == 0:
        return 0
    x = 0xFFFF
    while True:
        y = (x + s // x) >> 1
        if y >= x:
            break
        x = y
    return x + 1 if s - x * x > x else x


def qdivu(m, b):
    return (m * ONE + b // 2) // b


def qdiv(a, b):
    v = qdivu(abs(a), b)
    return -v if a < 0 else v


def shift3(m):
    while True:
        o = m[0] | m[1] | m[2]
        if o >= 1 << 15:
            m = [x >> 1 for x in m]
        elif o >= 1 << 14:
            return m
        elif o == 0:
            return None
        else:
            m = [x << 1 for x in m]


def nrm3(v):
    m = shift3([abs(x) for x in v])
    if m is None:
        return [0, 0, 0]
    ln = rsqrt(sum(x * x for x in m))
    return [(-1 if v[i] < 0 else 1) * qdivu(m[i], ln) for i in range(3)]


def camset(cam, look, light):
    """(C, cami, light in camera space) as camset."""
    d = [look[i] - cam[i] for i in range(3)]
    m = shift3([abs(x) for x in d])
    if m is None:
        c = list(IDENT)
    else:
        dv = [-m[i] if d[i] < 0 else m[i] for i in range(3)]
        dh2 = dv[0] * dv[0] + dv[2] * dv[2]
        dh = rsqrt(dh2)
        dl = rsqrt(dh2 + dv[1] * dv[1])
        if dh == 0:
            s = -ONE if dv[1] < 0 else ONE
            c = [ONE, 0, 0, 0, 0, -s, 0, s, 0]
        else:
            rx, rz = qdiv(dv[0], dh), qdiv(dv[2], dh)
            fx, fy, fz = qdiv(dv[0], dl), qdiv(dv[1], dl), qdiv(dv[2], dl)
            uy = qdiv(dh, dl)
            c = [rz, 0, -rx, qv([(-1, fy, rx)]), uy, qv([(-1, fy, rz)]), fx, fy, fz]
    cami = 1 if c == IDENT else 0
    ln = nrm3(list(light))
    lc = matvec(c, ln)[0]
    return c, cami, lc


def float_cam(cam, look):
    d = [look[i] - cam[i] for i in range(3)]
    ln = math.sqrt(sum(x * x for x in d))
    if ln == 0:
        return [1, 0, 0, 0, 1, 0, 0, 0, 1]
    f = [x / ln for x in d]
    h = math.hypot(f[0], f[2])
    if h == 0:
        s = 1 if f[1] > 0 else -1
        return [1, 0, 0, 0, 0, -s, 0, s, 0]
    r = [f[2] / h, 0, -f[0] / h]
    u = [f[1] * r[2] - f[2] * r[1], f[2] * r[0] - f[0] * r[2], f[0] * r[1] - f[1] * r[0]]
    return r + u + f


# ---------------------------------------------------------------- G3DATA
def basic_number(item):
    """A DATA item as READ reads it (the forms the tests use)."""
    item = item.strip().upper()
    if not item:
        return 0.0
    if item.startswith("&H"):
        v = int(item[2:], 16)
        return float(v - 0x10000 if v & 0x8000 else v)
    if item.startswith("&O"):
        v = int(item[2:], 8)
        return float(v - 0x10000 if v & 0x8000 else v)
    if item.startswith("&B"):
        v = int(item[2:], 2)
        return float(v - 0x10000 if v & 0x8000 else v)
    return float(item.rstrip("!#%"))


def read_data(path, start_line):
    """The numbers of the DATA statements of a BASIC text file from line
    start_line on, in READ order."""
    vals = []
    for raw in open(path, "rb").read().decode("ascii").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        num, _, rest = raw.partition(" ")
        if int(num) < start_line:
            continue
        for stmt in rest.split(":"):
            stmt = stmt.strip()
            if stmt.upper().startswith("DATA"):
                for item in stmt[4:].split(","):
                    vals.append(basic_number(item))
    return vals


def rnd16(v):
    """dacint: rounded half away from zero."""
    return int(math.floor(abs(v) + 0.5)) * (1 if v >= 0 else -1)


class Model:
    """A model as G3DATA stores it (VRAM bytes, directory values)."""

    def __init__(self, vals, o=1, ramps=(1, 8)):
        it = iter(vals)
        nx = lambda: rnd16(next(it))  # noqa: E731
        nv, nf, ne, t = nx(), nx(), nx(), nx()
        self.nv, self.nf, self.t, self.o = nv, nf, t, o
        verts = [tuple(nx() for _ in range(3)) for _ in range(nv)]
        raw_faces = [tuple(nx() for _ in range(5)) for _ in range(nf)]
        edges = [tuple(nx() for _ in range(2)) for _ in range(ne)]
        uvs = [tuple(nx() for _ in range(8)) for _ in range(nf)] if t else []
        self.used = 4 + 3 * nv + 5 * nf + 2 * ne + (8 * nf if t else 0)
        self.verts = verts
        mx = 0
        for v in verts:
            for c in v:
                mx |= abs(c)
        k = 0
        while mx >= 8192:
            mx >>= 1
            k += 1
        self.k = k
        cen = []
        for a in range(3):
            s = sum(v[a] for v in verts)
            c = abs(s) // nv
            cen.append((-c if s < 0 else c) >> k)
        self.cen = cen
        self.ramps = set(ramps)
        self.faces, self.perms = [], []
        for (a, b, c, d, col) in raw_faces:
            rec, perm, n = self.face(a, b, c, d, col)
            self.faces.append((rec, n, col))
            self.perms.append(perm)
        self.uvs = [[uv[2 * perm[j] + h] for j in range(4) for h in range(2)]
                    for uv, perm in zip(uvs, self.perms)]
        if ne:
            self.edges, self.derived = list(edges), False
        elif nf:
            seen, out = set(), []
            for rec, _, _ in self.faces:
                for j in range(4):
                    x, y = rec[j], rec[(j + 1) & 3]
                    if x == y:
                        continue
                    e = (min(x, y), max(x, y))
                    if e not in seen:
                        seen.add(e)
                        out.append(e)
            self.edges, self.derived = out, True
        else:
            self.edges, self.derived = [], False
        self.col = raw_faces[0][4] if nf else 15

    def sv(self, i):
        return [c >> self.k for c in self.verts[i]]

    def face(self, a, b, c, d, col):
        idx = [a, b, c, d]
        kept = [(idx[j], j) for j in range(4) if idx[j] != idx[(j + 1) & 3]]
        if len(kept) == 3:
            rec = [kept[0][0], kept[1][0], kept[2][0], kept[2][0]]
            perm = [kept[0][1], kept[1][1], kept[2][1], kept[2][1]]
        else:
            rec, perm = list(idx), [0, 1, 2, 3]
        dist = len(kept)
        n = [0, 0, 0]
        ramp = col in self.ramps
        if dist >= 3 and (ramp or self.o & 1):
            nq = self.normal(rec)
            if self.o & 1:
                vs = [self.sv(r) for r in rec]
                dot = 0
                for ax in range(3):
                    cent = (vs[0][ax] + vs[1][ax] + vs[2][ax] + vs[3][ax]) >> 2
                    dot += nq[ax] * (cent - self.cen[ax])
                if dot < 0:
                    if dist == 4:
                        rec = [rec[0], rec[3], rec[2], rec[1]]
                        perm = [perm[0], perm[3], perm[2], perm[1]]
                    else:
                        rec = [rec[0], rec[2], rec[1], rec[1]]
                        perm = [perm[0], perm[2], perm[1], perm[1]]
                    nq = [-x for x in nq]
            if ramp:
                n = nq
        return rec, perm, n

    def normal(self, rec):
        v = [self.sv(r) for r in rec]
        a = [v[2][i] - v[0][i] for i in range(3)]
        b = [v[3][i] - v[1][i] for i in range(3)]
        cr = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
        return nrm3(cr)

    def vbytes(self):
        return b"".join(struct.pack("<hhh", *v) for v in self.verts)

    def fbytes(self):
        return b"".join(bytes(rec) + struct.pack("<hhh", *n) + bytes([col]) for rec, n, col in self.faces)

    def ubytes(self):
        return b"".join(bytes(u) for u in self.uvs)

    def ebytes(self):
        return b"".join(bytes(e) for e in self.edges)

    def vram(self):
        return self.vbytes() + self.fbytes() + (self.ubytes() if self.t else b"") + self.ebytes()

    def size(self):
        return (len(self.vram()) + 127) & ~127

    def geo_faces(self):
        """(i0, i1, i2, i3, nx, ny, nz, base) as geo3d holds them."""
        return [tuple(rec) + tuple(n) + (col,) for rec, n, col in self.faces]


# ---------------------------------------------------------------- frames
def geo_cfg():
    return [0] * 12 + [256, 128, 106, 16, 256, 212]


def object_regs(ang, pos, cam=(0, 0, -300), look=(0, 0, 0), light=(-1, 1, -1)):
    """M (9) and T (3) of one object as G3FRAME writes them, the light."""
    c, cami, lc = camset(cam, look, light)
    r = rotmat(*ang)
    m = r if cami else matmul(c, r)
    d = [s16(pos[i] - cam[i]) for i in range(3)]
    t = d if cami else matvec(c, d)[0]
    return m, t, lc


def render(model, m, t, lc, style, page):
    """geo3d commands of one RUN of `model` (gen_scenes reference)."""
    from gen_scenes import render as render_edges, render_faces
    cfg = m + t + geo_cfg()[12:]
    verts = model.verts
    if style == 1 and model.nf:
        cmds, skip, draw, cull = render_faces(cfg, verts, model.geo_faces(), 0, page * 256, lc)
        return [cmds], (skip, draw, cull)
    col = model.col + 6 if model.col in model.ramps else model.col
    out, tot = [], [0, 0, 0]
    for p in range(0, len(model.edges), 255):
        cmds, skip, draw, cull = render_edges(cfg, verts, model.edges[p:p + 255], col, 0, page * 256)
        out.append(cmds)
        tot = [tot[0] + skip, tot[1] + draw, tot[2] + cull]
    return out, tuple(tot)


def line_pixels(c):
    """The pixels (x, y) of a VDP LINE command (11 bytes from R#36), as
    openMSX's VDPCmdEngine draws them in GRAPHIC 4 (V9968: 11-bit Y)."""
    dx = c[0] | (c[1] & 1) << 8
    dy = c[2] | (c[3] & 7) << 8
    nx = c[4] | (c[5] & 3) << 8
    ny = (c[6] | (c[7] & 3) << 8) & 1023
    arg = c[9]
    tx = -1 if arg & 4 else 1
    ty = -1 if arg & 8 else 1
    asx = ((nx - 1) & 0xFFFFFFFF) >> 1
    adx, anx = dx, 0
    out = []
    while True:
        out.append((adx, dy & 0x7FF))
        if not arg & 1:
            adx += tx
            if anx == nx or (adx & 256):
                break
            anx += 1
            if asx < ny:
                asx += nx
                dy += ty
                if ty < 0 and dy < 0:
                    break
            asx = (asx - ny) & 1023
        else:
            dy += ty
            if ty < 0 and dy < 0:
                break
            if asx < ny:
                asx += nx
                adx += tx
            asx = (asx - ny) & 1023
            if anx == nx or (adx & 256):
                break
            anx += 1
    return out


def raster(runs, page, base=None):
    """SCREEN 5 bytes of lines 0-211 of `page` after the LINE commands of
    the RUNs (lists of 11-byte LINE entries) on a cleared page."""
    buf = bytearray(base) if base is not None else bytearray(212 * 128)
    for cmds in runs:
        for c in cmds:
            assert len(c) == 11 and (c[10] & 0xF0) == 0x70, c
            col = c[8] & 15
            for x, y in line_pixels(c):
                y -= page * 256
                if 0 <= y < 212 and 0 <= x < 256:
                    a = y * 128 + (x >> 1)
                    if x & 1:
                        buf[a] = (buf[a] & 0xF0) | col
                    else:
                        buf[a] = (buf[a] & 0x0F) | (col << 4)
    return bytes(buf)
