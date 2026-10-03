#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Assets of the SCREEN 7 sphere demo (sphere7.asm), all original and
procedural:

  - the UV sphere for geo3d: 20 longitudes x 10 latitude bands, 182 vertices,
    200 faces (the polar bands are triangles), texture coordinates per face
    corner, normals scaled so that the light level runs 0..4;
  - 256 rotation matrices (one turn about the planet's own axis, tilted
    sideways and towards the viewer), with the SCREEN 7 aspect folded in:
    rows Y and Z are halved, so X / Z comes out twice as wide in pixels;
  - six textures, 160 x 51 texels plus a wrap column, five pre-shaded copies
    each (light levels 0..4, level 0 the night side, TSTRIDE 51 rows),
    quantised to a 16-colour palette per texture (0 black, 14 grey and 15
    white are the same in all six, for the stars and the HUD);
  - the starfield, the HUD bitmaps (an original 5 x 7 pixel font) and the
    palettes (15-bit for EPAL, or 9-bit with --pal9).

Writes out/inc/sphere7_equ.asm (equates for the Z80 code), out/bank1.bin
(tables), out/tex.bin (VRAM pages 2-3, 128 KB), out/hud.bin (VRAM rows
214-247 of page 0), out/assets.json (for the checks) and preview PNGs in
out/img/ (contact sheet of the textures).

Usage: gen_assets.py [--pal9] [--pace N]
"""
import json
import math
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "sim"))
from gen_vectors import model as core_model  # noqa: E402

OUT = os.path.join(HERE, "out")
W, H = 512, 212
NLON, NLAT = 20, 10
RMOD = 1024                   # sphere radius, model units
TZ = 4096                     # camera distance 8192, halved with rows Y and Z
FOCAL = 368
ZNEAR = 16
NROT = 256
TILT_SIDE = math.radians(-24)  # the spin axis leans to the right ...
TILT_VIEW = math.radians(20)   # ... and towards the viewer
UT, HT = 160, 51              # texels around, texel rows pole to pole
TSTRIDE = 51                  # rows between the shaded copies
LEVELS = 5                    # stored light levels 0..4 (0: the night side)
SLOTS = [(1, 512), (172, 512), (343, 512), (1, 768), (172, 768), (343, 768)]
LIGHT = (-0.72, 0.42, -0.55)  # towards the light, camera space (x right, y up, z ahead)
NSCALE = 0.630                # normal length: level = floor(7 * 0.63 * cos) = 0..4, also
                              # with the squash's stretch (x 1.125)
BASE = 0x81                   # bit 7: textured (CLR = BASE + level is only used outside the window: never)
BRIGHT = [0.045, 0.22, 0.48, 0.78, 1.00]   # albedo factor of the copies, levels 0..4
FIX = {0: (0, 0, 0), 14: (0.56, 0.58, 0.62), 15: (1.0, 1.0, 1.0)}   # sRGB, every palette
HUD_Y = 214                   # first HUD source row (page 0, below the 212 shown)
NAME_BOX = (8, 202, 160, 7)   # x, y, w, h of the name on screen
CREDIT_BOX = (444, 202, 60, 7)


def q14(x):
    return max(-32768, min(32767, round(x * 16384)))


def norm(v):
    n = math.sqrt(sum(c * c for c in v))
    return [c / n for c in v]


# ------------------------------------------------------------------ geometry
def sphere_model():
    verts = [(0, RMOD, 0)]
    for i in range(1, NLAT):
        th = math.pi * i / NLAT
        for j in range(NLON):
            ph = math.pi + 2 * math.pi * j / NLON
            verts.append((round(RMOD * math.sin(th) * math.cos(ph)), round(RMOD * math.cos(th)),
                          round(RMOD * math.sin(th) * math.sin(ph))))
    verts.append((0, -RMOD, 0))
    south = len(verts) - 1

    def ring(i, j):
        if i == 0:
            return 0
        if i == NLAT:
            return south
        return 1 + (i - 1) * NLON + (j % NLON)

    vband = [round((HT - 1) * b / NLAT) for b in range(NLAT + 1)]
    faces, uvs = [], []
    for b in range(NLAT):
        for j in range(NLON):
            u0, u1 = 8 * j, 8 * (j + 1)
            um = (u0 + u1) // 2
            v0, v1 = vband[b], vband[b + 1]
            if b == 0:
                ids = (0, ring(1, j + 1), ring(1, j), ring(1, j))
                uv = (um, v0, u1, v1, u0, v1, u0, v1)
            elif b == NLAT - 1:
                ids = (ring(b, j), ring(b, j + 1), south, south)
                uv = (u0, v0, u1, v0, um, v1, um, v1)
            else:
                ids = (ring(b, j), ring(b, j + 1), ring(b + 1, j + 1), ring(b + 1, j))
                uv = (u0, v0, u1, v0, u1, v1, u0, v1)
            c = norm([sum(verts[k][a] for k in set(ids)) for a in range(3)])
            n = tuple(q14(NSCALE * x) for x in c)
            faces.append((ids, n, BASE))
            uvs.append(uv)
    return verts, faces, uvs, vband


def mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def rotation(k):
    a = -2 * math.pi * k / NROT
    ca, sa = math.cos(a), math.sin(a)
    ry = [[ca, 0, sa], [0, 1, 0], [-sa, 0, ca]]
    cx, sx = math.cos(TILT_VIEW), math.sin(TILT_VIEW)
    rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    cz, sz = math.cos(TILT_SIDE), math.sin(TILT_SIDE)
    rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    r = mat_mul(rz, mat_mul(rx, ry))
    s = (1.0, 0.5, 0.5)
    return [q14(s[i] * r[i][j]) for i in range(3) for j in range(3)]


def light_words():
    lx, ly, lz = norm(LIGHT)
    return [q14(lx), q14(2 * ly), q14(2 * lz)]


def cfg_for(m, cx=0, cy=0):
    return list(m) + [0, 0, TZ, FOCAL, cx, cy, ZNEAR, W, H]


def asr(x, n):
    return x >> n


def squash_rows(m, kx, ky):
    """The Z80's squash (sphere7.asm sq_apply): row X then row Y scaled by
    shift-and-add factors. k = 0 none, 1: x - x/16, 2: x - x/8, 3: x - x/4;
    the other axis is stretched by x + x/32, x + x/16, x + x/8."""
    m = list(m)

    def sq(v, k):
        return v - (v >> (5 - k)) if k else v   # k=1: >>4, 2: >>3, 3: >>2

    def st(v, k):
        return v + (v >> (6 - k)) if k else v   # k=1: >>5, 2: >>4, 3: >>3
    for j in range(3):
        m[j] = st(sq(m[j], kx), ky)
        m[3 + j] = st(sq(m[3 + j], ky), kx)
    return m


def extents(verts, mats):
    """Projected extents around (CX, CY) over the given matrices, and the
    largest light level."""
    xl = xr = yt = yb = 0
    for m in mats:
        cfg = cfg_for(m)
        for v in verts:
            sx, sy, z, fl = core_model(cfg, v)
            assert fl & 7 == 0
            xl, xr = max(xl, -sx), max(xr, sx)
            yt, yb = max(yt, -sy), max(yb, sy)
    return xl, xr, yt, yb


# ------------------------------------------------------------------ noise
class Perlin:
    def __init__(self, seed):
        rng = np.random.default_rng(seed)
        self.perm = np.concatenate([rng.permutation(256)] * 2)
        g = rng.normal(size=(256, 3))
        self.grad = g / np.linalg.norm(g, axis=1, keepdims=True)

    def __call__(self, p):
        pi = np.floor(p).astype(np.int64)
        pf = p - pi
        w = pf * pf * pf * (pf * (pf * 6 - 15) + 10)
        out = 0
        for dx in (0, 1):
            for dy in (0, 1):
                for dz in (0, 1):
                    h = self.perm[(self.perm[(self.perm[(pi[..., 0] + dx) & 255] + pi[..., 1] + dy) & 255]
                                   + pi[..., 2] + dz) & 255]
                    g = self.grad[h]
                    d = pf - np.array([dx, dy, dz])
                    dot = (g * d).sum(-1)
                    wx = w[..., 0] if dx else 1 - w[..., 0]
                    wy = w[..., 1] if dy else 1 - w[..., 1]
                    wz = w[..., 2] if dz else 1 - w[..., 2]
                    out = out + dot * wx * wy * wz
        return out


def fbm(noise, p, octaves, lac=2.0, gain=0.5):
    tot, amp, f = 0.0, 1.0, 1.0
    norm_ = 0.0
    for _ in range(octaves):
        tot = tot + amp * noise(p * f)
        norm_ += amp
        amp *= gain
        f *= lac
    return tot / norm_ * 1.6


def smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def mix(a, b, t):
    t = np.asarray(t)[..., None]
    return np.asarray(a) * (1 - t) + np.asarray(b) * t


def lin(c):
    """sRGB triple (0..1) -> linear."""
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


# ------------------------------------------------------------------ textures
def texel_grid(vband):
    """Unit vectors, latitude and longitude of the texel centres (HT rows,
    UT + 1 columns: the last column repeats the first)."""
    vb = np.array(vband, dtype=float)
    thb = np.pi * np.arange(NLAT + 1) / NLAT
    v = np.arange(HT) + 0.5
    th = np.interp(v, vb, thb)
    u = np.arange(UT + 1) % UT + 0.5
    ph = np.pi + 2 * np.pi * u / UT
    TH, PH = np.meshgrid(th, ph, indexing="ij")
    p = np.stack([np.sin(TH) * np.cos(PH), np.cos(TH), np.sin(TH) * np.sin(PH)], -1)
    lat = np.pi / 2 - TH
    lon = (PH - np.pi) % (2 * np.pi)
    return p, lat, lon


def tex_ocean(p, lat, lon):
    n1, n2, n3 = Perlin(11), Perlin(12), Perlin(13)
    e = fbm(n1, p * 1.7 + 3.1, 6) - 0.06
    m = fbm(n2, p * 2.3 + 7.7, 4)
    deep, shallow = lin((0.03, 0.10, 0.34)), lin((0.07, 0.33, 0.58))
    col = mix(deep, shallow, smooth(-0.30, 0.0, e))
    alat = np.abs(lat) * 180 / np.pi
    jungle, plain, desert = lin((0.12, 0.42, 0.10)), lin((0.36, 0.52, 0.20)), lin((0.80, 0.66, 0.38))
    mount, tundra = lin((0.48, 0.38, 0.28)), lin((0.55, 0.56, 0.50))
    land = mix(jungle, plain, smooth(-0.1, 0.25, m))
    dry = smooth(0.15, -0.25, m) * smooth(8, 18, alat) * smooth(42, 30, alat)
    land = mix(land, desert, dry)
    land = mix(land, tundra, smooth(48, 62, alat))
    land = mix(land, mount, smooth(0.22, 0.40, e))
    col = np.where((e > 0)[..., None], land, col)
    ice = smooth(66, 74, alat + 6 * fbm(n3, p * 4, 3))
    col = mix(col, lin((0.92, 0.95, 1.0)), ice)
    stretch = p * np.array([2.6, 6.0, 2.6]) + 1.3
    c = fbm(n3, stretch, 5) + 0.25 * np.sin(lat * 7.0)
    cloud = smooth(0.22, 0.55, c) * 0.85
    col = mix(col, lin((0.97, 0.97, 0.98)), cloud)
    return col, None


def tex_moon(p, lat, lon):
    n1, n2 = Perlin(21), Perlin(22)
    g = 0.52 + 0.08 * fbm(n1, p * 6, 5)
    maria = smooth(-0.05, -0.25, fbm(n2, p * 1.6 + 2.0, 4))
    g = g * (1 - 0.42 * maria)
    rng = np.random.default_rng(23)
    for _ in range(120):
        c = rng.normal(size=3)
        c /= np.linalg.norm(c)
        r = math.radians(math.exp(rng.uniform(math.log(1.6), math.log(15))))
        d = np.arccos(np.clip((p * c).sum(-1), -1, 1)) / r
        floor = smooth(1.0, 0.85, d)
        rim = smooth(0.8, 1.0, d) * smooth(1.45, 1.0, d)
        peak = smooth(0.25, 0.0, d) * (r > math.radians(7))
        g = g * (1 - 0.30 * floor) * (1 + 0.42 * rim) * (1 + 0.5 * peak)
    col = np.stack([g * 0.98, g * 0.98, g * 1.02], -1)
    return lin(np.clip(col, 0, 1)), None


def tex_giant(p, lat, lon):
    n1, n2 = Perlin(31), Perlin(32)
    y = np.sin(lat) + 0.05 * fbm(n1, p * np.array([3, 9, 3]), 5) + 0.02 * fbm(n2, p * 14, 3)
    t = 0.5 + 0.5 * np.sin(y * 13.0 + 0.6 * np.sin(y * 5.0))
    t2 = 0.5 + 0.5 * np.sin(y * 31.0 + 1.0)
    cream, tan, brown = lin((0.95, 0.86, 0.66)), lin((0.78, 0.57, 0.36)), lin((0.46, 0.28, 0.17))
    col = mix(cream, tan, smooth(0.25, 0.75, t))
    col = mix(col, brown, smooth(0.65, 0.95, t) * 0.8)
    col = mix(col, lin((0.98, 0.95, 0.88)), smooth(0.8, 1.0, t2) * 0.35)
    # an oval storm
    slat, slon = math.radians(-24), math.radians(110)
    dl = (lon - slon + np.pi) % (2 * np.pi) - np.pi
    d = np.sqrt((dl / math.radians(17)) ** 2 + ((lat - slat) / math.radians(8)) ** 2)
    swirl = 0.5 + 0.5 * np.sin(d * 9 + np.arctan2(lat - slat, dl) * 2)
    storm = smooth(1.0, 0.6, d)
    col = mix(col, mix(lin((0.88, 0.42, 0.18)), lin((0.97, 0.70, 0.45)), swirl * 0.5), storm)
    col = mix(col, lin((0.98, 0.93, 0.85)), smooth(1.25, 1.0, d) * smooth(0.85, 1.0, d) * 0.6)
    return col, None


def tex_beach(p, lat, lon):
    cols = [lin(c) for c in ((0.88, 0.10, 0.10), (0.97, 0.97, 0.95), (0.10, 0.32, 0.86),
                             (0.97, 0.97, 0.95), (0.98, 0.80, 0.08), (0.97, 0.97, 0.95))]
    k = np.floor(lon / (2 * np.pi / 6)).astype(int) % 6
    col = np.zeros(lon.shape + (3,))
    for i, c in enumerate(cols):
        col[k == i] = c
    alat = np.abs(lat) * 180 / np.pi
    cap = alat > 76
    col[cap] = lin((0.10, 0.32, 0.86))
    col[(alat > 72) & ~cap] = lin((0.97, 0.97, 0.95))
    return col, None


def tex_checker(p, lat, lon):
    k = (np.floor(lon / (2 * np.pi / 10)).astype(int) + np.floor((np.pi / 2 - lat) / (np.pi / 5)).astype(int)) & 1
    col = np.where(k[..., None] == 1, lin((0.90, 0.07, 0.16)), lin((0.96, 0.96, 0.94)))
    return col, None


def tex_lava(p, lat, lon):
    n1, n2, n3 = Perlin(61), Perlin(62), Perlin(63)
    crust = 0.05 + 0.04 * fbm(n1, p * 5, 4)
    col = np.stack([crust * 1.3, crust * 0.9, crust * 0.8], -1)
    r = 1 - np.abs(fbm(n2, p * 3.2 + 4.0, 5))
    lake = smooth(0.30, 0.45, fbm(n3, p * 1.4 + 9.0, 3))
    heat = np.maximum(smooth(0.86, 0.97, r), lake * (0.65 + 0.35 * smooth(0.6, 0.9, r)))
    glow = mix(lin((0.45, 0.04, 0.02)), lin((0.97, 0.36, 0.04)), smooth(0.2, 0.7, heat))
    glow = mix(glow, lin((1.0, 0.86, 0.35)), smooth(0.75, 1.0, heat))
    emit = glow * smooth(0.05, 0.3, heat)[..., None]
    return col, emit


TEXTURES = [
    ("OCEAN WORLD", tex_ocean, (0.02, 0.03, 0.12)),
    ("CRATERED MOON", tex_moon, (0.05, 0.05, 0.06)),
    ("GAS GIANT", tex_giant, (0.09, 0.05, 0.03)),
    ("BEACH BALL", tex_beach, (0.20, 0.20, 0.22)),
    ("CHECKER BALL", tex_checker, (0.08, 0.02, 0.03)),
    ("LAVA WORLD", tex_lava, (0.16, 0.03, 0.01)),
]


def oklab(c):
    """linear RGB -> Oklab (for palette distances)."""
    m1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929],
                   [0.2119034982, 0.6806995451, 0.1073969566],
                   [0.0883024619, 0.2817188376, 0.6299787005]])
    m2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468],
                   [1.9779984951, -2.4285922050, 0.4505937099],
                   [0.0259040371, 0.7827717662, -0.8086757660]])
    lms = np.cbrt(np.maximum(c @ m1.T, 0))
    return lms @ m2.T


def quant5(c, bits):
    q = (1 << bits) - 1
    return np.round(np.clip(c, 0, 1) * q) / q


def make_palette(levels_lin, night, bits, seed):
    """16 sRGB colours: 0 black, 1 night, 14 grey, 15 white fixed; 2..13 by
    k-means (Oklab) over the shaded copies."""
    px = np.concatenate([lv.reshape(-1, 3) for lv in levels_lin])
    lab = oklab(px)
    rng = np.random.default_rng(seed)
    fixed_s = np.array([FIX[0], night, FIX[14], FIX[15]])
    fixed_s = quant5(fixed_s, bits)
    fixed_lab = oklab(lin(fixed_s))
    k = 12
    cent = lab[rng.choice(len(lab), k, replace=False)]
    for _ in range(40):
        allc = np.concatenate([fixed_lab, cent])
        d = ((lab[:, None, :] - allc[None]) ** 2).sum(-1)
        a = d.argmin(1)
        for i in range(k):
            sel = lab[a == 4 + i]
            if len(sel):
                cent[i] = sel.mean(0)
            else:
                cent[i] = lab[rng.integers(len(lab))]
    # back to linear: average the pixels of each cluster in linear light
    allc = np.concatenate([fixed_lab, cent])
    a = ((lab[:, None, :] - allc[None]) ** 2).sum(-1).argmin(1)
    free = []
    for i in range(k):
        sel = px[a == 4 + i]
        free.append(sel.mean(0) if len(sel) else px[rng.integers(len(px))])
    free_s = quant5(srgb(np.array(free)), bits)
    order = np.argsort(oklab(lin(free_s))[:, 0])        # dark to light
    pal = np.zeros((16, 3))
    pal[0], pal[1], pal[14], pal[15] = fixed_s
    pal[2:14] = free_s[order]
    return pal


def dither(img_lin, pal_s, allowed):
    """Floyd-Steinberg (serpentine) in linear light, nearest by Oklab."""
    pal_lin = lin(pal_s)
    pal_lab = oklab(pal_lin)
    h, w, _ = img_lin.shape
    work = img_lin.copy()
    out = np.zeros((h, w), dtype=np.uint8)
    idx = np.array(allowed)
    for y in range(h):
        xs = range(w) if y % 2 == 0 else range(w - 1, -1, -1)
        dx = 1 if y % 2 == 0 else -1
        for x in xs:
            c = np.clip(work[y, x], 0, 1)
            d = ((pal_lab[idx] - oklab(c[None])[0]) ** 2).sum(-1)
            k = idx[d.argmin()]
            out[y, x] = k
            err = (c - pal_lin[k]) * 0.8
            if 0 <= x + dx < w:
                work[y, x + dx] += err * 7 / 16
            if y + 1 < h:
                if 0 <= x - dx < w:
                    work[y + 1, x - dx] += err * 3 / 16
                work[y + 1, x] += err * 5 / 16
                if 0 <= x + dx < w:
                    work[y + 1, x + dx] += err * 1 / 16
    return out


def build_textures(vband, bits):
    """VRAM rows 511-1023 (row 511: the guard row of the first slots). Each
    slot: columns x0 - 1 (a guard copy of column 159) to x0 + 160 (a copy of
    column 0), rows y0 - 1 (a guard copy of the first row) to y0 + 5 * 51 - 1.
    geo3d's per-pixel steps are floor divisions, so a span may start or end
    up to one texel before u = 0 or v = 0: the guard texels keep every texel
    the LRMM reads inside the source window (the V9968 would paint the CLR
    colour outside it, and openMSX handles that case differently)."""
    p, lat, lon = texel_grid(vband)
    vram = bytearray(513 * 256)
    pals, previews = [], []

    def put(x, y, c):
        a = (y - 511) * 256 + (x >> 1)
        if x & 1:
            vram[a] = (vram[a] & 0xF0) | c
        else:
            vram[a] = (vram[a] & 0x0F) | (c << 4)
    for t, (name, fn, night) in enumerate(TEXTURES):
        alb, emit = fn(p[:, :UT], lat[:, :UT], lon[:, :UT])
        if emit is None:
            emit = np.zeros_like(alb)
        lv = [np.clip(alb * b + emit, 0, 1) for b in BRIGHT]
        pal = make_palette(lv, night, bits, 100 + t)
        allowed = [0] + list(range(2, 16)) + [1]
        idxs = [dither(x, pal, allowed) for x in lv]
        x0, y0 = SLOTS[t]
        for li, ix in enumerate(idxs):
            ix = np.concatenate([ix[:, -1:], ix, ix[:, :1]], 1)     # columns -1 .. 160
            rows = range(-1, HT) if li == 0 else range(HT)
            for v in rows:
                y = y0 + li * TSTRIDE + v
                for u in range(-1, UT + 1):
                    put(x0 + u, y, int(ix[max(v, 0), u + 1]))
        pals.append(pal)
        previews.append((name, idxs, pal))
    return bytes(vram), pals, previews


# ------------------------------------------------------------------ HUD
FONT = {
    "A": "01110 10001 10001 11111 10001 10001 10001", "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110", "D": "11100 10010 10001 10001 10001 10010 11100",
    "E": "11111 10000 10000 11110 10000 10000 11111", "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111", "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110", "K": "10001 10010 10100 11000 10100 10010 10001",
    "L": "10000 10000 10000 10000 10000 10000 11111", "M": "10001 11011 10101 10101 10001 10001 10001",
    "N": "10001 11001 10101 10011 10001 10001 10001", "O": "01110 10001 10001 10001 10001 10001 01110",
    "R": "11110 10001 10001 11110 10100 10010 10001", "S": "01111 10000 10000 01110 00001 00001 11110",
    "T": "11111 00100 00100 00100 00100 00100 00100", "U": "10001 10001 10001 10001 10001 10001 01110",
    "V": "10001 10001 10001 10001 10001 01010 00100", "W": "10001 10001 10001 10101 10101 10101 01010",
    "3": "11110 00001 00001 01110 00001 00001 11110", " ": "00000 00000 00000 00000 00000 00000 00000",
}


def text_bitmap(s, colour, width):
    """Rows of G6 pixels: each font pixel is 2 pixels wide, 6-pixel cells."""
    rows = [[0] * width for _ in range(7)]
    x = 0
    for ch in s:
        g = FONT[ch].split()
        for r in range(7):
            for c in range(5):
                if g[r][c] == "1":
                    for k in (0, 1):
                        if x + 2 * c + k < width:
                            rows[r][x + 2 * c + k] = colour
        x += 12
    assert x - 2 <= width, f"HUD text too wide: {s}"
    return rows


def build_hud():
    """VRAM rows 214..247 of page 0: the six names (two columns of three, 8
    rows apart) and the credit. Returns the bytes and the source positions."""
    rows = 34
    img = [[0] * 512 for _ in range(rows)]
    src = []
    for t, (name, _, _) in enumerate(TEXTURES):
        sx, sy = (t // 3) * 256, (t % 3) * 8
        bm = text_bitmap(name, 15, NAME_BOX[2])
        for r in range(7):
            img[sy + r][sx:sx + NAME_BOX[2]] = bm[r]
        src.append((sx, HUD_Y + sy))
    bm = text_bitmap("GEO3D", 14, CREDIT_BOX[2])
    for r in range(7):
        img[24 + r][0:CREDIT_BOX[2]] = bm[r]
    credit = (0, HUD_Y + 24)
    out = bytearray()
    for r in img:
        for x in range(0, 512, 2):
            out.append((r[x] << 4) | r[x + 1])
    return bytes(out), src, credit


def build_stars(n=76, seed=7077):
    rng = np.random.default_rng(seed)
    stars = []

    def inbox(x, y, b):
        return b[0] - 4 <= x < b[0] + b[2] + 4 and b[1] - 3 <= y < b[1] + b[3] + 3
    while len(stars) < n:
        x, y = int(rng.integers(0, 512)), int(rng.integers(0, 212))
        if inbox(x, y, NAME_BOX) or inbox(x, y, CREDIT_BOX):
            continue
        if any(abs(x - a) < 6 and abs(y - b) < 3 for a, b, _ in stars):
            continue
        stars.append((x, y, 15 if rng.random() < 0.35 else 14))
    return sorted(stars, key=lambda s: (s[1], s[0]))


# ------------------------------------------------------------------ output
def palette_bytes(pal, bits):
    if bits == 5:
        return bytes(int(round(c * 31)) for col in pal for c in col)
    out = bytearray()
    for r, g, b in pal:
        r3, g3, b3 = (int(round(c * 7)) for c in (r, g, b))
        out += bytes([(r3 << 4) | b3, g3])
    return bytes(out)


def contact_sheet(previews, path):
    """The six textures (rows) and their five shaded copies (columns, light
    level 0..4), with the texture palettes, each texel 3 pixels wide and 6
    lines high (SCREEN 7 texels are half as wide as they are high)."""
    from PIL import ImageDraw
    sx, sy = 3, 6
    cw, ch = (UT + 1) * sx, HT * sy
    pad, top, left = 10, 26, 10
    sheet = Image.new("RGB", (left + LEVELS * (cw + pad), top + len(previews) * (ch + 22 + pad)), (16, 16, 20))
    d = ImageDraw.Draw(sheet)
    for li in range(LEVELS):
        d.text((left + li * (cw + pad), 8), f"light level {li}" + (" (night)" if li == 0 else ""),
               fill=(200, 200, 210))
    for t, (name, idxs, pal) in enumerate(previews):
        flat = [int(round(c * 255)) for col in pal for c in col]
        y = top + t * (ch + 22 + pad)
        d.text((left, y), name, fill=(235, 235, 240))
        for li, ix in enumerate(idxs):
            im = Image.fromarray(np.concatenate([ix, ix[:, :1]], 1).astype(np.uint8), "P")
            im.putpalette(flat + [0] * (768 - len(flat)))
            im = im.convert("RGB").resize((cw, ch), Image.NEAREST)
            sheet.paste(im, (left + li * (cw + pad), y + 16))
    sheet.save(path)


def main():
    bits = 3 if "--pal9" in sys.argv else 5
    pace = int(sys.argv[sys.argv.index("--pace") + 1]) if "--pace" in sys.argv else 2
    os.makedirs(os.path.join(OUT, "inc"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "img"), exist_ok=True)
    verts, faces, uvs, vband = sphere_model()
    mats = [rotation(k) for k in range(NROT)]
    light = light_words()
    # light level range (the engine's formula) over every rotation, and over
    # every squash the Z80 applies (the stretched rows make LM longer)
    lvmax, lv_hist = 0, [0] * 7
    for m in mats + [squash_rows(m, kx, ky) for m in mats[::4] for kx in range(4) for ky in range(4)]:
        lm = [max(-131071, min(131071, sum(m[3 * i + j] * light[i] for i in range(3)) >> 14)) for j in range(3)]
        for ids, n, base in faces:
            sh = sum(lm[j] * n[j] for j in range(3)) >> 14
            lv = 0 if sh <= 0 else min(6, (sh * 7) >> 14)
            lvmax = max(lvmax, lv)
            lv_hist[lv] += 1
    assert lvmax <= LEVELS - 1, f"light level {lvmax} > {LEVELS - 1}"
    xl, xr, yt, yb = extents(verts, mats)
    # the box to clear around the centre, per squash (kx, ky): the extents
    # over every rotation, plus one pixel
    boxx, boxy = [], []
    for kx in range(4):
        for ky in range(4):
            e = extents(verts, [squash_rows(m, kx, ky) for m in mats[::2]])
            boxx.append(max(e[0], e[1]) + 1)
            boxy.append(max(e[2], e[3]) + 1)
    sxl, sxr, syt, syb = max(boxx) - 1, max(boxx) - 1, max(boxy) - 1, max(boxy) - 1
    # bounce range of the centre: the sphere's own extents (the Z80 stops the
    # centre right at a limit: the contact frame touches the edge)
    # speed per frame: 120 pixels and 60 lines per second at 30 or 60 fps;
    # the squash lasts three steps of SQ_U pixels (lines) from the contact,
    # about 0.1 s either way
    VX, VY = 2 * pace, pace
    SQ_UX, SQ_UY = 4, 2
    x_min, x_max = xl, W - 1 - xr
    y_min, y_max = yt, H - 1 - yb
    # squash: the centre moves towards the wall by R * (1 - k)
    rx, ry = max(xl, xr), max(yt, yb)
    sq_dx = [0, round(rx / 16), round(rx / 8), round(rx / 4)]
    sq_dy = [0, round(ry / 16), round(ry / 8), round(ry / 4)]
    hx, hy = max(boxx), max(boxy)
    tex, pals, previews = build_textures(vband, bits)
    hud, hud_src, credit = build_hud()
    stars = build_stars()

    # ---- bank 1 (8000h-BFFFh): tables
    b1 = bytearray()
    lab = {}

    def put(name, data):
        lab[name] = 0x8000 + len(b1)
        b1.extend(data)

    def words(ws):
        return b"".join(int(w & 0xFFFF).to_bytes(2, "little") for w in ws)
    put("t_rot", b"".join(words(m) for m in mats))
    vb = b"".join(words(v) for v in verts)
    put("t_verts", vb)
    fb = bytearray()
    for ids, n, base in faces:
        fb += bytes(ids) + words(n) + bytes([base])
    put("t_faces", fb)
    put("t_uvs", b"".join(bytes(uv) for uv in uvs))
    put("t_pals", b"".join(palette_bytes(p, bits) for p in pals))
    # per texture: TEXX(2) TEXY(2) window WSX(2) WSY(2) WEX(2) WEY(2), HUD source x(2) y(2)
    tp = bytearray()
    for t, (x0, y0) in enumerate(SLOTS):
        texy = y0
        tp += words([x0, texy]) + words([x0 - 1, y0 - 1, x0 + UT, y0 + LEVELS * TSTRIDE - 1])
        tp += words(hud_src[t])
    put("t_texp", tp)
    sb = bytearray()
    for x, y, c in stars:
        sb += words([x]) + bytes([y, c])
    sb += bytes([0xFF, 0xFF, 0xFF, 0xFF])
    put("t_stars", sb)
    # T_STARY[y] -> the first star at a line >= y (y = 0..212)
    first = []
    for y in range(213):
        i = next((k for k, s_ in enumerate(stars) if s_[1] >= y), len(stars))
        first.append(lab["t_stars"] + 4 * i)
    put("t_stary", words(first))
    put("t_boxx", bytes(boxx))
    put("t_boxy", bytes(boxy))
    assert len(b1) <= 0x4000, len(b1)
    with open(os.path.join(OUT, "bank1.bin"), "wb") as f:
        f.write(b1 + bytes(0x4000 - len(b1)))
    with open(os.path.join(OUT, "tex.bin"), "wb") as f:
        f.write(tex[256:])                       # rows 512-1023
    hud = hud + tex[:256]                        # row 511 (guard row) after the HUD
    with open(os.path.join(OUT, "hud.bin"), "wb") as f:
        f.write(hud)
    cfg_words = [0] * 9 + [0, 0, TZ, FOCAL, 256, 106, ZNEAR, W, H]
    eq = {
        "NVERT": len(verts), "NFACE": len(faces), "NROT": NROT, "NTEX": len(TEXTURES),
        "PALBYTES": len(palette_bytes(pals[0], bits)), "PAL9": int(bits == 3),
        "TSTRIDE": TSTRIDE, "X_MIN": x_min, "X_MAX": x_max, "Y_MIN": y_min, "Y_MAX": y_max,
        "VX0": VX, "VY0": VY, "SQ_UX": SQ_UX, "SQ_UY": SQ_UY, "HALF_X": hx, "HALF_Y": hy,
        "SQDX1": sq_dx[1], "SQDX2": sq_dx[2], "SQDX3": sq_dx[3],
        "SQDY1": sq_dy[1], "SQDY2": sq_dy[2], "SQDY3": sq_dy[3],
        "NAME_X": NAME_BOX[0], "NAME_Y": NAME_BOX[1], "NAME_W": NAME_BOX[2], "HUD_H": NAME_BOX[3],
        "CRED_X": CREDIT_BOX[0], "CRED_Y": CREDIT_BOX[1], "CRED_W": CREDIT_BOX[2],
        "CRED_SX": credit[0], "CRED_SY": credit[1], "HUD_Y": HUD_Y, "HUD_ROWS": len(hud) // 256 - 1, "GUARD_OFS": len(hud) - 256,
        "LIGHTX": light[0] & 0xFFFF, "LIGHTY": light[1] & 0xFFFF, "LIGHTZ": light[2] & 0xFFFF,
    }
    eq.update({k.upper(): v for k, v in lab.items()})
    with open(os.path.join(OUT, "inc", "sphere7_equ.asm"), "w") as f:
        f.write("; generated by gen_assets.py, do not edit\n")
        for k, v in eq.items():
            f.write(f"{k}: equ {v}\n")
    with open(os.path.join(OUT, "inc", "sphere7_cfg.asm"), "w") as f:
        f.write("; generated by gen_assets.py, do not edit\n")
        f.write("; geo3d registers 00h-23h: M00..M22, TX, TY, TZ, F, CX, CY, ZNEAR, W, H\n")
        f.write("cfg_words:\n")
        for w in cfg_words:
            f.write(f"        dw {w}\n")
    info = dict(eq, cfg_words=cfg_words, slots=SLOTS, names=[t[0] for t in TEXTURES],
                extents=[xl, xr, yt, yb], squash_extents=[sxl, sxr, syt, syb],
                level_hist=lv_hist, stars=stars, pals=[p.tolist() for p in pals], bits=bits,
                light=light, focal=FOCAL, tz=TZ)
    with open(os.path.join(OUT, "assets.json"), "w") as f:
        json.dump(info, f, indent=1)
    contact_sheet(previews, os.path.join(OUT, "img", "textures.png"))
    print(f"sphere: {len(verts)} vertices, {len(faces)} faces; extents L{xl} R{xr} T{yt} B{yb} "
          f"(squashed L{sxl} R{sxr} T{syt} B{syb}); levels {lv_hist}")
    print(f"bounce: x {x_min}..{x_max}, y {y_min}..{y_max}; clear half-size {hx} x {hy}; "
          f"bank 1: {len(b1)} bytes; palette {bits * 3}-bit")


if __name__ == "__main__":
    main()
