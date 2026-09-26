#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Frame composer for the previews, built on the bit-exact references:
geo3d's filled faces (sim/gen_scenes.py render_faces, the RTL's model) and
the Z80 rotation (basic/g3ref.py rotmat, the ROM's Q2.14 math). A frame is
a 256 x 212 index image, composed as the game does it every tick: the
background window, the stars, the foreground band (index 0 transparent),
the geo3d objects, then the sprites.
"""
import os
import sys

import numpy as np
from PIL import Image

import common as C

sys.path.insert(0, os.path.join(C.GEO3D, "basic"))
sys.path.insert(0, os.path.join(C.GEO3D, "sim"))
import g3ref  # noqa: E402
from gen_scenes import render_faces  # noqa: E402

LIGHT = g3ref.nrm3([-1, 1, -1])         # towards the light: up, left, front (BASIC default)
PAL = np.array(C.palette_rgb(), np.uint8)


def ang(deg):
    return int(round(deg * 65536 / 360)) & 0xFFFF


def object_cfg(att, pos, z=C.Z0):
    m = g3ref.rotmat(ang(att[0]), ang(att[1]), ang(att[2]))
    return C.geo_cfg(m, C.screen_to_t(pos[0], pos[1], z))


def draw_model(img, mesh, att, pos, w=C.G3W, h=C.G3H, z=C.Z0):
    """Draws `mesh` into `img` (H x 256 index array) with geo3d's spans.
    Returns (skipped, drawn, culled, spans)."""
    cfg = object_cfg(att, pos, z)
    cfg[16], cfg[17] = w, h
    cmds, skip, draw, cull = render_faces(cfg, mesh.units(), mesh.geo_faces(), 0, 0, LIGHT)
    for c in cmds:
        x, y, n = c[0] | (c[1] << 8), c[2] | (c[3] << 8), c[4] | ((c[5] & 7) << 8)
        img[y, x:x + n + 1] = c[8] & 15
    return skip, draw, cull, len(cmds)


def bg_window(bg, scroll, stars_img=None):
    """Screen columns x show background column (x + scroll) mod 512 when
    x + scroll >= 0, else the starfield (scroll < 0 while it enters)."""
    out = np.zeros((C.SCR_H, C.SCR_W), np.uint8) if stars_img is None else stars_img.copy()
    for x in range(C.SCR_W):
        c = x + scroll
        if c >= 0:
            out[:, x] = bg[:, c % 512]
    return out


def fg_band(img, fg, scroll):
    for x in range(C.SCR_W):
        c = x + scroll
        if c >= 0:
            col = fg[:, c % 256]
            m = col != C.TRANSPARENT
            img[C.BAND_Y:C.BAND_Y + C.BAND_H, x][m] = col[m]


def to_rgb(img):
    return PAL[img]


def save(img, path, scale=3):
    im = Image.fromarray(to_rgb(img) if img.ndim == 2 else img)
    if scale != 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path)
    return path


def draw_model_units(img, mesh, angles, pos, w=C.G3W, h=C.G3H, z=C.Z0):
    """As draw_model, with 16-bit angles (65536 per turn), as the game keeps them."""
    m = g3ref.rotmat(angles[0] & 0xFFFF, angles[1] & 0xFFFF, angles[2] & 0xFFFF)
    cfg = C.geo_cfg(m, C.screen_to_t(pos[0], pos[1], z), w, h)
    cmds, skip, draw, cull = render_faces(cfg, mesh.units(), mesh.geo_faces(), 0, 0, LIGHT)
    for c in cmds:
        x, y, n = c[0] | (c[1] << 8), c[2] | (c[3] << 8), c[4] | ((c[5] & 7) << 8)
        img[y, x:x + n + 1] = c[8] & 15
    return draw, len(cmds)


def draw_sprite(img, bits, colours, x, ya):
    """A mode 2 sprite (16 x 16 bits, 16 line colours) at X = x, Y attribute ya;
    colour 0 is transparent (TP = 0)."""
    for r in range(16):
        y = ya + 1 + r
        col = colours[r] & 15
        if not (0 <= y < C.SCR_H) or col == 0:
            continue
        for k in range(16):
            if bits[r][k] and 0 <= x + k < C.SCR_W:
                img[y, x + k] = col


_ATT = {}


def draw_model_att(img, mesh, table, index, pos, w=C.G3W, h=C.G3H, z=C.Z0):
    """As the game draws: the matrix is entry `index` of attitude table `table`."""
    import models
    if not _ATT:
        for name, entries in models.att_tables().items():
            _ATT[name] = [models.att_matrix(a) for a in entries]
    m = _ATT[table][index]
    cfg = C.geo_cfg(m, C.screen_to_t(pos[0], pos[1], z), w, h)
    cmds, skip, draw, cull = render_faces(cfg, mesh.units(), mesh.geo_faces(), 0, 0, LIGHT)
    for c in cmds:
        x, y, n = c[0] | (c[1] << 8), c[2] | (c[3] << 8), c[4] | ((c[5] & 7) << 8)
        img[y, x:x + n + 1] = c[8] & 15
    return draw, len(cmds)
