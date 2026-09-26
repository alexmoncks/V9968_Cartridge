#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""The shooter's vector models (geo3d filled faces) and their export.

Models are written here in pixels (common.py: 4 geo3d units = 1 pixel at
the play depth Z0) with +X forward on screen, +Y up and +Z into the screen.
Each part names a point inside it, and every face is turned so that
cross(p1 - p0, p2 - p0) points away from that point: the geo3d winding
(docs/BASIC_API.md 3.7: clockwise seen from outside). Double-sided thin
parts (fins, wings of the enemies) get both windings.

A face is (i0, i1, i2, i3, colour); a triangle repeats its third vertex.
colour RAMP_BLUE (0) or RAMP_WARM (8) is a 7-tone ramp (geo3d adds the
light level 0-6); any other colour is flat and gets a zero normal, so the
level is 0 and the face shows exactly that palette entry. Normals are the
ROM's (basic/g3ref.py Model.normal: cross of the diagonals, nrm3, Q2.14).

Orientation (R = Ry(ay) Rx(ax) Rz(az), the Z80 rotmat of g3bank1.asm,
angles in 65536 units per turn, see ATTITUDES):
  player    nose +X; ax = roll (bank). Rest attitude ax = BANK0 shows the
            top a little; moving up/down adds +/- BANK_STEP.
  dart      nose -X (flies left); ax rolls, az pitches (dives).
  saucer    stored lying (spin axis +Z): az spins it, ax = SAUCER_TILT
            stands it up with the top towards the viewer.
  rock      any: tumbles on all three angles.
  gunship   nose -X; ax small rock, az small pitch.
  debris    any: tumbles.
"""
import math
import os
import sys

import common as C

sys.path.insert(0, os.path.join(C.GEO3D, "basic"))
import g3ref  # noqa: E402

DEG = 65536 / 360


def ang(deg):
    return int(round(deg * DEG)) & 0xFFFF


# attitudes (degrees) used by the previews and suggested for the game code
BANK0 = 20           # player at rest: top tilted 20 degrees towards the viewer (ax > 0)
BANK_STEP = 22       # extra roll while climbing (+) or diving (-)
SAUCER_TILT = 110    # saucer: ax 90 stands it up, 110 tilts its top 20 degrees to the viewer


def sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


class Mesh:
    def __init__(self, name, desc, scale=1.0):
        self.name, self.desc, self.scale = name, desc, scale
        self.v = []          # pixels (float)
        self.f = []          # [i0, i1, i2, i3, colour]

    def vert(self, x, y, z):
        p = (float(x), float(y), float(z))
        for i, q in enumerate(self.v):
            if max(abs(p[k] - q[k]) for k in range(3)) < 1e-9:
                return i
        self.v.append(p)
        return len(self.v) - 1

    def verts(self, pts):
        return [self.vert(*p) for p in pts]

    def _normal(self, idx):
        p = [self.v[i] for i in idx]
        if len(idx) == 3:
            return cross(sub(p[1], p[0]), sub(p[2], p[0]))
        return cross(sub(p[2], p[0]), sub(p[3], p[1]))

    def face(self, idx, colour, inside=None, double=False):
        idx = list(idx)
        assert len(idx) in (3, 4) and len(set(idx)) == len(idx), idx
        if inside is not None:
            cen = [sum(self.v[i][k] for i in idx) / len(idx) for k in range(3)]
            if dot(self._normal(idx), sub(cen, inside)) < 0:
                idx = [idx[0]] + idx[1:][::-1]
        rec = idx + [idx[2]] if len(idx) == 3 else idx
        self.f.append(rec + [colour])
        if double:
            back = [idx[0]] + idx[1:][::-1]
            self.f.append((back + [back[2]] if len(back) == 3 else back) + [colour])

    # ---- geo3d form
    def units(self):
        if getattr(self, "_units", None) is None or len(self._units) != len(self.v):
            self._units = [tuple(int(round(c * self.scale * C.UNIT)) for c in p) for p in self.v]
        return self._units

    def geo_faces(self):
        """(i0, i1, i2, i3, nx, ny, nz, base) as geo3d's face RAM holds them."""
        if getattr(self, "_gf", None) is not None and len(self._gf) == len(self.f):
            return self._gf
        vu = self.units()
        out = []
        for a, b, c, d, col in self.f:
            if col in C.RAMPS:
                p = [vu[i] for i in (a, b, c, d)]
                n = g3ref.nrm3(cross(sub(p[2], p[0]), sub(p[3], p[1])))
            else:
                n = [0, 0, 0]
            out.append((a, b, c, d, n[0], n[1], n[2], col))
        self._gf = out
        return out

    def check(self):
        vu = self.units()
        assert len(vu) <= 255 and len(self.f) <= 255
        assert max(abs(c) for p in vu for c in p) <= 8000
        for a, b, c, d, col in self.f:
            p = [vu[i] for i in (a, b, c, d)]
            n = cross(sub(p[1], p[0]), sub(p[2], p[0]))
            ln = math.sqrt(dot(n, n))
            assert ln > 0, (self.name, "degenerate face", (a, b, c, d))
            if d != c:
                off = abs(dot(n, sub(p[3], p[0]))) / ln
                assert off <= 1.0 * C.UNIT, (self.name, "non planar quad", (a, b, c, d), off)
                # convex: the 4 corner turns have the same sign
                turns = [dot(n, cross(sub(p[(k + 1) % 4], p[k]), sub(p[(k + 2) % 4], p[(k + 1) % 4])))
                         for k in range(4)]
                assert all(t > 0 for t in turns), (self.name, "concave quad", (a, b, c, d))
            assert col < 16
        return True

    def radius_px(self):
        return max(math.sqrt(dot(p, p)) for p in self.v) * self.scale

    def edges(self):
        seen, out = set(), []
        for a, b, c, d, _ in self.f:
            for x, y in ((a, b), (b, c), (c, d), (d, a)):
                if x != y and (min(x, y), max(x, y)) not in seen:
                    seen.add((min(x, y), max(x, y)))
                    out.append((min(x, y), max(x, y)))
        return out


# ------------------------------------------------------------------ helpers
def hexring(x, h, w, yc=0.0):
    """Hexagonal cross-section at x: top, upper-far, lower-far, bottom,
    lower-near, upper-near (near = -Z, towards the viewer)."""
    return [(x, yc + h, 0), (x, yc + h / 2, w), (x, yc - h / 2, w),
            (x, yc - h, 0), (x, yc - h / 2, -w), (x, yc + h / 2, -w)]


def band(m, r0, r1, colours, inside):
    n = len(r0)
    for i in range(n):
        j = (i + 1) % n
        col = colours[i] if isinstance(colours, list) else colours
        m.face([r0[i], r0[j], r1[j], r1[i]], col, inside)


def fan(m, ring, tip, colours, inside):
    n = len(ring)
    for i in range(n):
        col = colours[i] if isinstance(colours, list) else colours
        m.face([ring[i], ring[(i + 1) % n], tip], col, inside)


def hexcap(m, ring, colour, inside):
    m.face([ring[0], ring[1], ring[2], ring[3]], colour, inside)
    m.face([ring[3], ring[4], ring[5], ring[0]], colour, inside)


def box(m, x0, x1, y0, y1, z0, z1, colours, skip=()):
    """Axis-aligned box; colours: dict side -> colour (sides -x +x -y +y -z +z)."""
    v = {(i, j, k): m.vert((x0, x1)[i], (y0, y1)[j], (z0, z1)[k])
         for i in (0, 1) for j in (0, 1) for k in (0, 1)}
    inside = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
    sides = {"-x": [(0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1)],
             "+x": [(1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)],
             "-y": [(0, 0, 0), (0, 0, 1), (1, 0, 1), (1, 0, 0)],
             "+y": [(0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)],
             "-z": [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
             "+z": [(0, 0, 1), (0, 1, 1), (1, 1, 1), (1, 0, 1)]}
    for s, corners in sides.items():
        if s not in skip:
            m.face([v[c] for c in corners], colours.get(s, colours.get("all")), inside)


def thin_quad_prism(m, pts, y0, y1, top, bottom, edge, skip_edges=()):
    """A flat part (wing) with planform pts (x, z) between heights y0 < y1."""
    lo = [m.vert(x, y0, z) for x, z in pts]
    hi = [m.vert(x, y1, z) for x, z in pts]
    cx = sum(p[0] for p in pts) / len(pts)
    cz = sum(p[1] for p in pts) / len(pts)
    inside = (cx, (y0 + y1) / 2, cz)
    m.face(hi, top, inside)
    m.face(lo, bottom, inside)
    n = len(pts)
    for i in range(n):
        if i in skip_edges:
            continue
        j = (i + 1) % n
        m.face([lo[i], lo[j], hi[j], hi[i]], edge, inside)


def thin_tri_prism_xy(m, pts, z0, z1, side, edge, skip_edges=()):
    """A flat part (fin) with profile pts (x, y) between depths z0 < z1."""
    a = [m.vert(x, y, z0) for x, y in pts]
    b = [m.vert(x, y, z1) for x, y in pts]
    inside = (sum(p[0] for p in pts) / 3, sum(p[1] for p in pts) / 3, (z0 + z1) / 2)
    m.face(a, side, inside)
    m.face(b, side, inside)
    for i in range(3):
        if i in skip_edges:
            continue
        j = (i + 1) % 3
        m.face([a[i], a[j], b[j], b[i]], edge, inside)


# ------------------------------------------------------------------ models
B, W = C.RAMP_BLUE, C.RAMP_WARM


def player():
    m = Mesh("player", "player fighter, nose +X", scale=0.8)
    inside = (0, 0, 0)
    rear = m.verts(hexring(-22, 4.5, 5.0))
    mid = m.verts(hexring(6, 6.0, 6.0 * 5.0 / 4.5))
    nose = m.vert(27, -1, 0)
    band(m, rear, mid, B, inside)
    fan(m, mid, nose, B, inside)
    hexcap(m, rear, C.AMBER, inside)                # engine exhaust
    # canopy: on the top ridge, cyan
    ridge = lambda x: 4.5 + 1.5 * (x + 22) / 28 if x <= 6 else 6 - 7 * (x - 6) / 21  # noqa: E731
    A = m.vert(-8, ridge(-8), 0)
    Bf = m.vert(15, ridge(15), 0)
    T = m.vert(1, 9.5, 0)
    hw = lambda x: (5.0 + (6.0 * 5 / 4.5 - 5.0) * (x + 22) / 28)  # noqa: E731
    h = lambda x: 4.5 + 1.5 * (x + 22) / 28  # noqa: E731
    zc = 3.2
    yc = h(1) - (h(1) - h(1) / 2) * zc / hw(1)
    Ln = m.vert(1, yc, -zc)
    Rf = m.vert(1, yc, zc)
    cin = (1, ridge(1) - 0.5, 0)
    m.face([A, T, Ln], C.CYAN, cin)
    m.face([T, Bf, Ln], C.CYAN, cin)
    m.face([A, Rf, T], C.CYAN, cin)
    m.face([T, Rf, Bf], C.CYAN, cin)
    # swept wings (warm), root edge hidden in the fuselage
    for s in (-1, 1):
        pts = [(4, 5 * s), (-12, 24 * s), (-20, 24 * s), (-15, 5 * s)]
        thin_quad_prism(m, pts, -2.0, -0.5, W, B, W, skip_edges=(3,))
    # tail fin
    thin_tri_prism_xy(m, [(-10, h(-10) - 0.3), (-26, 13), (-21, h(-21) - 0.3)], -0.8, 0.8,
                      W, W, skip_edges=(2,))
    return m


def dart():
    m = Mesh("dart", "fighter, nose -X", scale=1.15)
    inside = (0, 0, 0)
    ring = m.verts([(2, 4, 0), (2, 0, 5), (2, -4, 0), (2, 0, -5)])     # top, far, bottom, near
    nose = m.vert(-15, 0, 0)
    tail = m.vert(10, 0, 0)
    for i in range(4):
        j = (i + 1) % 4
        col = C.MAGENTA if (i, j) == (3, 0) else W
        m.face([ring[i], ring[j], nose], col, inside)
        m.face([ring[i], ring[j], tail], B, inside)
    for s in (-1, 1):
        tri = m.verts([(-1, 0, 4 * s), (8, 0, 4 * s), (13, -1.5, 15 * s)])
        m.face(tri, W, double=True)
    fin = m.verts([(1, 3.5, 0), (9, 0.5, 0), (13, 8, 0)])
    m.face(fin, W, double=True)
    return m


def saucer():
    m = Mesh("saucer", "spinning saucer, stored lying (spin axis +Z)", scale=1.0)
    n = 8

    def ring(r, y):
        return [(r * math.cos(2 * math.pi * (i + 0.5) / n), y, r * math.sin(2 * math.pi * (i + 0.5) / n))
                for i in range(n)]
    # built with the axis on +Y, then turned: (x, y, z) -> (x, -z, y), a
    # proper rotation, so the windings stay right
    turn = lambda p: (p[0], -p[2], p[1])  # noqa: E731
    lo = m.verts([turn(p) for p in ring(15.0, -1.5)])       # rim, lower edge
    hi = m.verts([turn(p) for p in ring(15.0, 1.5)])        # rim, upper edge
    up = m.verts([turn(p) for p in ring(7.0, 4.0)])         # dome base
    top = m.vert(*turn((0, 7.5, 0)))
    bot = m.vert(*turn((0, -5.5, 0)))
    inside = turn((0, 0.5, 0))
    band(m, lo, hi, [W if i % 2 == 0 else B for i in range(n)], inside)   # striped rim shows the spin
    band(m, hi, up, B, inside)                                            # upper hull
    fan(m, up, top, C.MAGENTA, inside)                                    # glowing dome
    fan(m, lo, bot, W, inside)                                            # underside
    return m


def rock():
    m = Mesh("rock", "tumbling asteroid", scale=1.0)
    phi = (1 + 5 ** 0.5) / 2
    base = []
    for a in (-1, 1):
        for b in (-1, 1):
            base += [(0, a, b * phi), (a, b * phi, 0), (b * phi, 0, a)]
    # deterministic bumps
    bumps = [1.08, 0.86, 1.0, 1.12, 0.92, 1.04, 0.84, 1.1, 0.96, 1.02, 0.9, 1.14]
    r = 11.0 / math.sqrt(1 + phi * phi)
    pts = [tuple(c * r * k for c in p) for p, k in zip(base, bumps)]
    idx = m.verts(pts)
    faces = []
    for i in range(12):
        for j in range(i + 1, 12):
            for k in range(j + 1, 12):
                d = [math.dist(base[x], base[y]) for x, y in ((i, j), (j, k), (i, k))]
                if all(abs(e - 2) < 1e-6 for e in d):
                    faces.append((i, j, k))
    assert len(faces) == 20
    lava = {3, 11}
    for n_, (i, j, k) in enumerate(faces):
        m.face([idx[i], idx[j], idx[k]], C.ORANGE if n_ in lava else B, (0, 0, 0))
    return m


def gunship():
    m = Mesh("gunship", "heavy gunship, nose -X", scale=1.0)
    inside = (0, 0, 0)
    rear = m.verts(hexring(22, 8, 9))
    front = m.verts(hexring(-12, 8, 9))
    nose = m.verts(hexring(-24, 4, 4.5))
    band(m, rear, front, W, inside)
    band(m, front, nose, [W, W, W, W, C.MAGENTA, W], inside)   # near nose face: the eye
    hexcap(m, nose, B, inside)
    hexcap(m, rear, C.AMBER, inside)
    for s in (-1, 1):                                          # engine pods
        z0, z1 = (8, 15) if s > 0 else (-15, -8)
        box(m, 6, 27, -3.5, 3.0, z0, z1, {"all": B, "+x": C.AMBER},
            skip=("+z",) if s < 0 else ("-z",))
    box(m, -32, -8, -9.5, -6.0, -2.2, 2.2, {"all": B})         # cannon
    box(m, -3, 11, 6.0, 11.5, -3.2, 3.2, {"all": W, "-z": C.CYAN}, skip=("-y",))   # bridge
    return m


def debris(k, colour):
    shapes = [[(0, 0, 0), (6, 1, 0), (2, 5, 1), (2, 1, -4)],
              [(0, 0, 0), (10, 1, 0), (5, 2.5, 2), (4, -1, -1.5)],
              [(0, 0, 0), (5, 0, 0), (2.5, 4.5, 0), (2, 1.5, 3.5)]]
    pts = shapes[k]
    c = [sum(p[i] for p in pts) / 4 for i in range(3)]
    pts = [tuple(p[i] - c[i] for i in range(3)) for p in pts]
    name = f"debris{k}{'w' if colour == W else 'b'}"
    m = Mesh(name, f"explosion shard {k} ({'warm' if colour == W else 'blue'})", scale=1.0)
    idx = m.verts(pts)
    for tri in ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)):
        m.face([idx[t] for t in tri], colour, (0, 0, 0))
    return m


def all_models():
    ms = [player(), dart(), saucer(), rock(), gunship()]
    ms += [debris(k, W) for k in range(3)] + [debris(k, B) for k in range(3)]
    for m in ms:
        m.check()
    return ms


# sprite hitboxes per model (see sprites.py), pixels around the screen centre
HITBOX = {"player": "P_HIT", "dart": "H_DART", "saucer": "H_SAUCER",
          "rock": "H_ROCK", "gunship": ("H_GUN_T", "H_GUN_B")}

# attitudes the previews use (ax, ay, az in degrees)
ATTITUDES = {
    "player": [("rest", (BANK0, 0, 0)), ("climb", (BANK0 + BANK_STEP, 0, 0)),
               ("dive", (BANK0 - BANK_STEP, 0, 0)), ("side", (0, 0, 0))],
    "dart": [("fly", (20, 0, 0)), ("roll", (60, 0, 0)), ("dive", (20, 0, 20)), ("side", (0, 0, 0))],
    "saucer": [("spin 0", (SAUCER_TILT, 0, 0)), ("spin 22", (SAUCER_TILT, 0, 22)),
               ("spin 45", (SAUCER_TILT, 0, 45)), ("steep", (135, 0, 10))],
    "rock": [("t0", (10, 20, 30)), ("t1", (60, 80, 10)), ("t2", (120, 30, 200)), ("t3", (200, 150, 90))],
    "gunship": [("fly", (15, 0, 0)), ("pitch", (15, 0, -8)), ("high", (35, 0, 0)), ("side", (0, 0, 0))],
}


# ------------------------------------------------------------------ attitudes
# The game never computes a rotation: every object picks a precomputed
# matrix (Q2.14, M00..M22, the rotmat of g3bank1.asm, bit exact) from its
# attitude table by an index. Mode 0 (bank): index = centre - vy / 3,
# eased by 1 per tick (the player: towards 16 climbing, 0 diving, 8 at
# rest); mode 1: +1 per tick, wrapping; mode 2: +1 every 2 ticks.
def att_tables():
    return {
        "player": [(BANK0 + (k - 8) * BANK_STEP / 8, 0, 0) for k in range(17)],
        "dart": [(20 + (k - 8) * 2.5, 0, (k - 8) * 2.5) for k in range(17)],
        "gunship": [(15 + (k - 8) * 1.5, 0, (k - 8) * 0.75) for k in range(17)],
        "saucer": [(SAUCER_TILT, 0, k * 90 / 16) for k in range(16)],     # 4-fold rim: 90 degrees loop
        "rock": [(k * 360 / 64, 2 * k * 360 / 64, k * 360 / 64) for k in range(64)],
        "debris": [(k * 360 / 32, 2 * k * 360 / 32, 3 * k * 360 / 32) for k in range(32)],
        # the title ship: rest roll, turning about the vertical axis
        "title": [(BANK0, k * 360 / 32, 0) for k in range(32)],
    }


ATT_ORDER = ["player", "dart", "saucer", "rock", "gunship", "debris", "title"]


def att_matrix(att):
    return g3ref.rotmat(ang(att[0]), ang(att[1]), ang(att[2]))


def export_attitudes(verbose=True):
    t = att_tables()
    lines = ["; Attitude tables: per entry the 9 matrix words M00..M22 (Q2.14 LE), in",
             "; geo3d register order: write index 0x00, then these 18 bytes and TX, TY, TZ.",
             "; att_index: per table (ATT_ ids): dw address ; db entries"]
    for i, name in enumerate(ATT_ORDER):
        lines.append(f"ATT_{name.upper()}: equ {i}")
    lines.append("att_index:")
    for name in ATT_ORDER:
        lines.append(f"        dw att_{name}")
        lines.append(f"        db {len(t[name])}")
    total = 0
    for name in ATT_ORDER:
        lines.append(f"att_{name}:\t\t; {len(t[name])} entries")
        for k, att in enumerate(t[name]):
            m = att_matrix(att)
            b = b"".join(C.le16(v) for v in m)
            total += len(b)
            lines += [ln + f"\t; {k}: ax {att[0]:.1f} ay {att[1]:.1f} az {att[2]:.1f}"
                      for ln in C.asm_bytes(b, 18)]
    path = C.write_asm("attitudes.asm", "models.py", lines)
    if verbose:
        print(f"  attitudes: {sum(len(v) for v in t.values())} matrices, {total} bytes")
    return path, total


# ------------------------------------------------------------------ culling
# The game uploads a model's faces to geo3d whenever the model changes in a
# frame, 11 bytes a face (the Z80's biggest geo3d cost). Faces that face
# away from the camera in every attitude the game draws the model with,
# anywhere on the screen, can never be drawn: game_faces() leaves them out.
# The test is the back-face rule with a margin: a face stays when
# n . c < MARGIN * |n| |c| for some attitude and position (n the rotated
# normal, c the rotated face centre plus the translation; camera at the
# origin looking along +Z). The player keeps its full model for the title,
# where it turns all the way round.
CULL_MARGIN = 0.03                      # about 2 degrees past edge-on
GAME_TABLES = {"player": "player", "dart": "dart", "saucer": "saucer", "gunship": "gunship"}


def game_faces(mesh, table):
    import numpy as np
    vu = np.array(mesh.units(), float)
    mats = [np.array(att_matrix(a), float).reshape(3, 3) / 16384 for a in att_tables()[table]]
    pos = [((x - C.CX) * C.UNIT, (C.CY - y) * C.UNIT, C.Z0)
           for x in range(-32, 289, 8) for y in range(-8, 221, 8)]
    T = np.array(pos, float)                                  # (P, 3)
    keep = []
    for k, (a, b, c, d, col) in enumerate(mesh.f):
        p = vu[[a, b, c, d]]
        n = np.cross(p[1] - p[0], p[2] - p[0])
        cen = p.mean(axis=0)
        vis = False
        for M in mats:
            nr = M @ n
            cc = (M @ cen)[None, :] + T                         # (P, 3)
            dots = cc @ nr
            lim = CULL_MARGIN * np.linalg.norm(nr) * np.linalg.norm(cc, axis=1)
            if np.any(dots < lim):
                vis = True
                break
        if vis:
            keep.append(k)
    return keep


def game_models(ms):
    """The models as the game uploads them: dart, saucer, gunship without
    their never-visible faces, and 'player_game' (id 11, the player's
    vertices, the faces it can show in play) after the others."""
    out = []
    extra = None
    for m in ms:
        if m.name in GAME_TABLES:
            keep = game_faces(m, GAME_TABLES[m.name])
            r = Mesh(m.name if m.name != "player" else "player_game", m.desc + " (visible faces)",
                     m.scale)
            r.v = list(m.v)
            r.f = [m.f[k] for k in keep]
            if m.name == "player":
                out.append(m)
                extra = r
            else:
                out.append(r)
        else:
            out.append(m)
    out.append(extra)
    return out


# ------------------------------------------------------------------ export
# Vertex pool order: geo3d transforms the pool from vertex 0 up to NVERT - 1
# on every RUN (geo3d_engine.v, transform phase), and the game sets NVERT =
# the model's base + its count, so a model late in the pool pays for the
# vertices of every model before it (about 1.2 us each on the FPGA). The
# shards (4 vertices, often several per frame), the darts and the rocks go
# first, the gunship (one per round) last.
POOL_ORDER = ["debris0w", "debris1w", "debris2w", "dart", "rock", "saucer", "player", "gunship"]


def export(ms, verbose=True, local=False):
    """out/inc/models.asm: the packed vertex pool and, per model, its face
    stream with pool indices; with local=True also each model's own vertex
    and face streams (local indices; the game does not use them, and its ROM
    bank has no room to spare: their mdl_index words are 0 without them)."""
    lines = [
        "; geo3d models of the shooter (geo3d/game/tools/models.py)",
        ";",
        "; Units: 4 per pixel at TZ = 1024 (F = 256). Axes: +X forward on screen,",
        "; +Y up, +Z into the screen. Winding: cross(p1-p0, p2-p0) points out.",
        ";",
        "; Per model <m>:",
        ";   MDL_<m>_NV, MDL_<m>_NF   vertex and face counts",
        ";   MDL_<m>_R                bounding radius in pixels",
        ";   mdl_<m>_v   NV x (x, y, z) int16 LE: stream to VDATA (index 0x50)",
        ";   mdl_<m>_f   NF x (i0, i1, i2, i3, nx, ny, nz int16 LE Q2.14, base):",
        ";               11 bytes, stream to FDATA (index 0x52); indices local 0..NV-1",
        ";   mdl_<m>_pf  the same faces with pool indices (MDL_<m>_PB + local)",
        ";",
        "; Pool use: upload pool_v once (POOL_NV vertices, VADDR = 0), then per",
        "; model type upload only its _pf faces (FADDR = 0) and RUN with",
        "; NVERT = MDL_<m>_PB + MDL_<m>_NV (or POOL_NV) and NFACE = MDL_<m>_NF.",
        "; base 0 / 8 with a normal: the blue / warm ramp; normal 0: flat colour.",
        "",
    ]
    # the pool: models in POOL_ORDER first (geo3d transforms vertices 0 to
    # NVERT - 1 at every RUN, NVERT = base + count, so the small, often
    # drawn models go first); models with the same vertices share them
    pool_v, pool_n, placed = [], 0, {}
    for m in sorted(ms, key=lambda m: POOL_ORDER.index(m.name) if m.name in POOL_ORDER
                    else len(POOL_ORDER)):
        key = tuple(m.units())
        if key not in placed:
            placed[key] = pool_n
            pool_v += m.units()
            pool_n += len(m.units())
    stats = []
    for m in ms:
        vu = m.units()
        gf = m.geo_faces()
        u = m.name.upper()
        key = tuple(vu)
        pb = placed[key]
        lines += [f"MDL_{u}_NV: equ {len(vu)}", f"MDL_{u}_NF: equ {len(gf)}",
                  f"MDL_{u}_PB: equ {pb}", f"MDL_{u}_R: equ {int(math.ceil(m.radius_px()))}"]
        vb = b"".join(C.le16(c) for p in vu for c in p)
        if local:
            lines.append(f"mdl_{m.name}_v:\t\t; {m.desc}")
            lines += C.asm_bytes(vb, 12)
        else:
            lines.append(f"; {m.name}: {m.desc}")
        for tag, base in ((("f", 0), ("pf", pb)) if local else (("pf", pb),)):
            lines.append(f"mdl_{m.name}_{tag}:")
            fb = b"".join(bytes([a + base, b + base, c + base, d + base]) + C.le16(nx) + C.le16(ny)
                          + C.le16(nz) + bytes([col]) for a, b, c, d, nx, ny, nz, col in gf)
            lines += C.asm_bytes(fb, 11)
        lines.append("")
        stats.append((m.name, len(vu), len(gf), pb, m.radius_px(), len(vb), 11 * len(gf)))
    assert pool_n <= 255
    lines += [f"POOL_NV: equ {pool_n}", "pool_v:"]
    lines += C.asm_bytes(b"".join(C.le16(c) for p in pool_v for c in p), 12)
    lines.append("; model index (id = position: 0 player, 1 dart, 2 saucer, 3 rock, 4 gunship,")
    lines.append(";   5-7 debris warm, 8-10 debris blue, 11 player_game): db NV, NF, PB ; dw _v, _f, _pf")
    lines.append("mdl_index:")
    for m in ms:
        u = m.name.upper()
        lines.append(f"        db MDL_{u}_NV, MDL_{u}_NF, MDL_{u}_PB")
        if local:
            lines.append(f"        dw mdl_{m.name}_v, mdl_{m.name}_f, mdl_{m.name}_pf")
        else:
            lines.append(f"        dw 0, 0, mdl_{m.name}_pf")
    path = C.write_asm("models.asm", "models.py", lines)
    if verbose:
        for name, nv, nf, pb, r, vb, fb in stats:
            print(f"  model {name:9s}: {nv:3d} vertices, {nf:3d} faces, radius {r:4.1f} px, "
                  f"pool base {pb:3d}, {vb} + {fb} bytes")
        print(f"  vertex pool: {pool_n} vertices, {6 * pool_n} bytes")
    return path, stats


if __name__ == "__main__":
    export(all_models())
