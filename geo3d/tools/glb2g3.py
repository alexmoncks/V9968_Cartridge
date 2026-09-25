#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""glb2g3.py: converts a glTF 2.0 model (.glb or .gltf) into a geo3d model
for the MSX-BASIC extension (CALL G3DATA, docs/BASIC_API.md section 5.5).

Pipeline:
  1. read the glTF with an own parser: node tree and transforms, triangle
     primitives (lists, strips, fans), base-colour texture and factor,
     COLOR_0;
  2. weld the vertices by position (UV seams), drop degenerate triangles and
     tiny loose parts;
  3. move to the geo3d frame: X right, Y up, Z into the screen, front +Z.
     glTF is right-handed (the model's right hand at -X), geo3d is
     left-handed, so X is mirrored and the winding reversed. Scale to
     --height units and centre the bounding box on the origin;
  4. average the base colour over every source triangle (10 texture samples
     each, in linear light) and search the SCREEN 5 palette: every pair of
     the large colour clusters as the two 7-tone ramps (G3RAMP, 1..7 and
     8..14), every cluster as colour 15 (G3PAL), colour 0 black; the ramp
     tones can also be used as flat colours. The cheapest palette wins
     (hue-weighted Lab error, penalties for flat colours and for two large
     materials that would look alike). Black is also the background
     (G3INIT), so a face in it vanishes where it meets the background: only
     near-black source colours (--black-max) may become a flat black face,
     and a ramp whose darkest tone would be black is never used;
  5. decimate to at most 255 vertices, then pair adjacent triangles into
     convex quads (maximum weight matching on the dual graph, fold limit
     --fold) until the face count fits 255. Strategies:
       qem  fast pre-reduction (fast_simplification), then an own quadric
            decimator: keeps the mesh manifold (link condition), refuses
            flips and slivers (--min-quality), weighs --focus regions;
       fs   plain fast_simplification to the final size, with a winding
            repair (only clean meshes are kept);
       tri  like qem, triangles only (no quads), about 128 vertices;
       all  builds the three and keeps the best score (--pick overrides);
     every fold limit gives a candidate, and the best preview score wins;
  6. colour every face by an area-weighted vote of the source triangles it
     covers (--colour mean: the mean colour instead);
  7. write BASIC lines (palette subroutine and G3DATA DATA lines), a JSON
     copy, previews rendered with the bit-exact geo3d reference model
     (sim/gen_scenes.py render_faces) next to the source mesh, a turntable
     GIF and a report.

Score of a candidate: silhouette IoU and mean colour error (Lab dE) on 28
views, against the source reduced to 8000 triangles, coloured the same way
and drawn by the same geo3d model; pixels count by the --focus weight of the
surface under them. score = mean IoU - mean dE / 400. The silhouettes are
the visible ones: a pixel in the background colour counts as background, as
it looks on the MSX. The report also compares every view with the source
mesh itself (full colour, the geo3d light model: what the MSX shows of the
source, palette included) and gives the share of the figure drawn in the
background colour.

Face rules (rtl/geo3d_engine.v): a face is a convex quad a,b,c,d; a triangle
repeats c. geo3d culls by the projected area of the first three corners, so
cross(v1 - v0, v2 - v0) must point outwards (the rule of the demo models):
in geo3d's frame (X right, Y up, Z into the screen) a face seen from outside
runs clockwise, as the spec says (docs/BASIC_API.md 3.7). Human figures are
concave: load them with CALL G3DATA(m,0), without the ROM's orientation
correction.

Flat colours: a face whose colour is not a ramp base is drawn flat. The
previews emulate that with a zero normal (light level 0, colour = base).

Requires numpy, pillow, scipy, networkx and fast_simplification
(pip install numpy pillow scipy networkx fast-simplification).

Example (a standing figure whose head is the top 30% of its height):
  glb2g3.py alex-model.glb -o out/alex --name alex --strategy all --focus 0.70:1:3
"""
import argparse
import base64
import heapq
import io
import json
import math
import os
import struct
import sys
import time
import urllib.parse

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.normpath(os.path.join(HERE, "..", "sim"))

MAX_VERTS = 255
MAX_FACES = 255

# SCREEN 5 set-up of G3INIT (basic/g3basic.asm) and the spec defaults
SCR_W, SCR_H = 256, 212
FOCAL, CX, CY, ZNEAR = 256, 128, 106, 16
CAM_DIST = 300                       # camera at (0,0,-300) looking at the origin
LIGHT = (-1.0, 1.0, -1.0)            # towards the light, world = camera space
BACKGROUND = (0, 0, 0)               # colour 0, the frame background of G3INIT
TYPICAL = 5.0 / 7.0                  # ramp tone of a face turned to the camera

SHEET_VIEWS = [(0, 180), (0, 135), (0, 90), (0, 0), (0, 270), (-25, 180), (25, 210), (-35, 150)]
SCORE_VIEWS = sorted(set(SHEET_VIEWS) | {(ax, ay) for ax in (0, -20) for ay in range(0, 360, 30)})


def log(msg):
    print(msg, flush=True)


# ============================================================== glTF reader
COMP_TYPES = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16,
              5125: np.uint32, 5126: np.float32}
N_COMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


class Gltf:
    """Minimal glTF 2.0 reader: .glb, or .gltf with external or data: buffers."""

    def __init__(self, path):
        self.dir = os.path.dirname(os.path.abspath(path))
        with open(path, "rb") as fh:
            data = fh.read()
        self.bin = None
        self.doc = None
        if data[:4] == b"glTF":
            length = struct.unpack_from("<I", data, 8)[0]
            off = 12
            while off + 8 <= length:
                clen, ctype = struct.unpack_from("<II", data, off)
                chunk = data[off + 8:off + 8 + clen]
                if ctype == 0x4E4F534A:
                    self.doc = json.loads(chunk.decode("utf-8"))
                elif ctype == 0x004E4942 and self.bin is None:
                    self.bin = chunk
                off += 8 + clen
            if self.doc is None:
                raise ValueError("GLB without a JSON chunk")
        else:
            self.doc = json.loads(data.decode("utf-8-sig"))
        self._buf, self._img = {}, {}

    def _uri(self, uri):
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        with open(os.path.join(self.dir, urllib.parse.unquote(uri)), "rb") as fh:
            return fh.read()

    def buffer(self, i):
        if i not in self._buf:
            b = self.doc["buffers"][i]
            self._buf[i] = self._uri(b["uri"]) if "uri" in b else self.bin
        return self._buf[i]

    def view(self, i):
        bv = self.doc["bufferViews"][i]
        off = bv.get("byteOffset", 0)
        return self.buffer(bv["buffer"])[off:off + bv["byteLength"]], bv.get("byteStride")

    def accessor(self, i):
        a = self.doc["accessors"][i]
        dt = np.dtype(COMP_TYPES[a["componentType"]]).newbyteorder("<")
        n, count = N_COMP[a["type"]], a["count"]
        if "bufferView" in a:
            raw, stride = self.view(a["bufferView"])
            off = a.get("byteOffset", 0)
            if stride and stride != dt.itemsize * n:
                arr = np.ndarray((count, n), dt, raw, off, (stride, dt.itemsize)).copy()
            else:
                arr = np.frombuffer(raw, dt, count * n, off).reshape(count, n).copy()
        else:
            arr = np.zeros((count, n), dt)
        sp = a.get("sparse")
        if sp:
            ii, vv = sp["indices"], sp["values"]
            raw, _ = self.view(ii["bufferView"])
            idt = np.dtype(COMP_TYPES[ii["componentType"]]).newbyteorder("<")
            idx = np.frombuffer(raw, idt, sp["count"], ii.get("byteOffset", 0)).astype(np.int64)
            raw, _ = self.view(vv["bufferView"])
            arr[idx] = np.frombuffer(raw, dt, sp["count"] * n, vv.get("byteOffset", 0)).reshape(-1, n)
        if a.get("normalized") and dt.kind in "iu":
            return np.maximum(arr.astype(np.float64) / float(np.iinfo(dt).max), -1.0)
        return arr.astype(np.float64) if dt.kind == "f" else arr.astype(np.int64)

    def image(self, i):
        if i not in self._img:
            im = self.doc["images"][i]
            raw = self.view(im["bufferView"])[0] if "bufferView" in im else self._uri(im["uri"])
            self._img[i] = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))
        return self._img[i]


def node_matrix(node):
    if "matrix" in node:
        return np.array(node["matrix"], float).reshape(4, 4).T      # column-major
    t = node.get("translation", [0, 0, 0])
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    s = node.get("scale", [1, 1, 1])
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    m = np.eye(4)
    m[:3, :3] = r * np.array(s, float)[None, :]
    m[:3, 3] = t
    return m


def srgb_to_lin(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(c):
    c = np.clip(np.asarray(c, float), 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def srgb_to_lab(c):
    lin = srgb_to_lin(c)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = lin @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


# 10 points strictly inside a triangle (barycentric, sum 6)
BARY = np.array([(i, j, 6 - i - j) for i in range(1, 5) for j in range(1, 6 - i)], float) / 6.0


def _wrap(coord, size, mode):
    i = np.floor(coord).astype(np.int64)
    if mode == 33071:                                  # CLAMP_TO_EDGE
        return np.clip(i, 0, size - 1)
    if mode == 33648:                                  # MIRRORED_REPEAT
        k = np.mod(i, 2 * size)
        return np.where(k < size, k, 2 * size - 1 - k)
    return np.mod(i, size)                             # REPEAT


def triangle_colours(g, prim, tri):
    """Base colour averaged over every triangle, linear RGB (T, 3)."""
    at = prim["attributes"]
    mat = g.doc["materials"][prim["material"]] if "material" in prim else {}
    pbr = mat.get("pbrMetallicRoughness", {})
    lin = np.ones((len(tri), 3)) * np.array(pbr.get("baseColorFactor", [1, 1, 1, 1])[:3], float)
    ti = pbr.get("baseColorTexture")
    if ti is not None:
        tex = g.doc["textures"][ti["index"]]
        key = f"TEXCOORD_{ti.get('texCoord', 0)}"
        if tex.get("source") is not None and key in at:
            img = g.image(tex["source"])
            smp = g.doc["samplers"][tex["sampler"]] if "sampler" in tex else {}
            uv = g.accessor(at[key])[:, :2]
            uvs = np.einsum("kj,tjc->tkc", BARY, uv[tri])
            h, w = img.shape[:2]
            x = _wrap(uvs[..., 0] * w, w, smp.get("wrapS", 10497))
            y = _wrap(uvs[..., 1] * h, h, smp.get("wrapT", 10497))
            lin = lin * srgb_to_lin(img[y, x].astype(np.float64) / 255.0).mean(axis=1)
    if "COLOR_0" in at:
        lin = lin * g.accessor(at["COLOR_0"])[:, :3][tri].mean(axis=1)
    return lin


def load_gltf(path):
    """Returns world-space positions (N,3), triangles (M,3) and the linear
    base colour of every triangle (M,3)."""
    g = Gltf(path)
    doc = g.doc
    nodes = doc.get("nodes", [])
    if doc.get("scenes"):
        roots = doc["scenes"][doc.get("scene", 0)].get("nodes", [])
    else:
        kids = {c for n in nodes for c in n.get("children", [])}
        roots = [i for i in range(len(nodes)) if i not in kids]
    pos, tris, cols, base = [], [], [], 0
    stack = [(r, np.eye(4)) for r in roots]
    while stack:
        ni, parent = stack.pop()
        node = nodes[ni]
        m = parent @ node_matrix(node)
        stack += [(c, m) for c in node.get("children", [])]
        if "mesh" not in node:
            continue
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            mode = prim.get("mode", 4)
            at = prim["attributes"]
            if mode not in (4, 5, 6) or "POSITION" not in at:
                log(f"  skipping a primitive (mode {mode})")
                continue
            p = g.accessor(at["POSITION"])[:, :3]
            idx = g.accessor(prim["indices"]).ravel() if "indices" in prim else np.arange(len(p))
            if mode == 4:
                tri = idx[:len(idx) // 3 * 3].reshape(-1, 3)
            elif mode == 5:
                tri = np.array([(idx[i], idx[i + 1], idx[i + 2]) if i % 2 == 0 else
                                (idx[i + 1], idx[i], idx[i + 2]) for i in range(len(idx) - 2)])
            else:
                tri = np.array([(idx[0], idx[i], idx[i + 1]) for i in range(1, len(idx) - 1)])
            tri = tri.astype(np.int64).reshape(-1, 3)
            if np.linalg.det(m[:3, :3]) < 0:
                tri = tri[:, ::-1]
            pos.append((np.c_[p, np.ones(len(p))] @ m.T)[:, :3])
            cols.append(triangle_colours(g, prim, tri))
            tris.append(tri + base)
            base += len(p)
    if not tris:
        raise ValueError("no triangle meshes in the file")
    return np.vstack(pos), np.vstack(tris), np.vstack(cols)


# ============================================================== mesh helpers
def tri_normals(P, T):
    n = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
    a = np.linalg.norm(n, axis=1)
    return n / np.maximum(a, 1e-30)[:, None], a / 2


def clean_mesh(V, F, C, min_part):
    diag = np.linalg.norm(V.max(0) - V.min(0))
    q = np.round(V / (diag * 1e-7)).astype(np.int64)
    _, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
    inv = inv.ravel()
    V, F = V[first], inv[F]
    ok = (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])
    F, C = F[ok], C[ok]
    _, keep = np.unique(np.sort(F, axis=1), axis=0, return_index=True)
    keep = np.sort(keep)
    dups = len(F) - len(keep)
    F, C = F[keep], C[keep]
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    rows = np.r_[F[:, 0], F[:, 1], F[:, 2]]
    cols = np.r_[F[:, 1], F[:, 2], F[:, 0]]
    ncomp, lab = connected_components(coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(V), len(V))),
                                      directed=False)
    _, area = tri_normals(V, F)
    comp_area = np.bincount(lab[F[:, 0]], weights=area, minlength=ncomp)
    big = comp_area >= min_part * area.sum()
    fk = big[lab[F[:, 0]]]
    F, C = F[fk], C[fk]
    used = np.unique(F)
    remap = -np.ones(len(V), np.int64)
    remap[used] = np.arange(len(used))
    info = dict(welded=len(V), duplicates=dups, parts=int(ncomp), parts_kept=int(big.sum()),
                faces=len(F))
    return V[used], remap[F], C, info


def signed_volume(P, T):
    return float(np.einsum("ij,ij->i", P[T[:, 0]], np.cross(P[T[:, 1]], P[T[:, 2]])).sum() / 6)


def to_geo3d(V, F, height, yaw):
    """glTF (right-handed, front +Z, right hand at -X) to geo3d (left-handed,
    front +Z, right hand at +X): mirror X, reverse the winding."""
    V = V.copy()
    V[:, 0] = -V[:, 0]
    F = F[:, ::-1].copy()
    if yaw:
        V = V @ rot_matrix(0, yaw, 0).T
    lo, hi = V.min(0), V.max(0)
    s = height / (hi[1] - lo[1])
    V = (V - (lo + hi) / 2) * s
    flipped = False
    if signed_volume(V, F) < 0:                      # inside out: fix
        F = F[:, ::-1].copy()
        flipped = True
    return V, F, flipped


def rot_matrix(ax, ay, az=0.0):
    """R = Ry(ay) * Rx(ax) * Rz(az), degrees, as in BASIC_API.md 3.2: ay 90
    turns the front (+Z) to +X, ax > 0 lifts the front, az > 0 rolls right."""
    a, b, c = (math.radians(t) for t in (ax, ay, az))
    ry = np.array([[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]])
    rx = np.array([[1, 0, 0], [0, math.cos(a), math.sin(a)], [0, -math.sin(a), math.cos(a)]])
    rz = np.array([[math.cos(c), math.sin(c), 0], [-math.sin(c), math.cos(c), 0], [0, 0, 1]])
    return ry @ rx @ rz


# ============================================================== palette
def msx_round(x):
    return int(np.clip(math.floor(x + 0.5), 0, 7))


def ramp_tones(rgb):
    """G3RAMP: tone k (0..6) = colour * (k+1) / 7, rounded."""
    return [tuple(msx_round(c * (k + 1) / 7) for c in rgb) for k in range(7)]


def weighted_kmeans(x, w, k, iters=40, seed=1):
    rng = np.random.default_rng(seed)
    p = w / w.sum()
    cent = [x[rng.choice(len(x), p=p)]]
    for _ in range(1, k):
        d = np.min(((x[:, None, :] - np.array(cent)[None]) ** 2).sum(-1), axis=1)
        q = p * d
        cent.append(x[rng.choice(len(x), p=q / q.sum())])
    cent = np.array(cent)
    for _ in range(iters):
        lab = np.argmin(((x[:, None, :] - cent[None]) ** 2).sum(-1), axis=1)
        for j in range(k):
            m = lab == j
            if m.any():
                cent[j] = np.average(x[m], axis=0, weights=w[m])
    return cent, lab


class Palette:
    """SCREEN 5 palette: 0 black, two ramps (G3RAMP), colour 15 (G3PAL).
    Options for a face: a ramp (shaded) or any palette colour (flat)."""

    def __init__(self, ramps, flat15, gain, flat_penalty, tone_penalty=None, black_max=12.0):
        self.ramps = ramps                          # [(c, (r,g,b))]
        self.black_max = black_max
        self.ramp_bases = {c for c, _ in ramps}
        self.gain = gain
        self.flat_penalty = flat_penalty
        self.tone_penalty = flat_penalty if tone_penalty is None else tone_penalty
        pal = [(0, 0, 0)] + [(0, 0, 0)] * 14 + [flat15]
        for c, rgb in ramps:
            for k, t in enumerate(ramp_tones(rgb)):
                pal[c + k] = t
        self.pal = pal
        self.flat15 = flat15
        opts = []                                   # (colour index, shaded, appearance sRGB 0..1)
        for c, rgb in ramps:
            opts.append((c, True, np.array(rgb, float) / 7 * TYPICAL))
        for i in range(16):
            if i not in self.ramp_bases:            # a ramp base is always shaded
                opts.append((i, False, np.array(pal[i], float) / 7))
        self.options = opts
        self.opt_lab = srgb_to_lab(np.array([o[2] for o in opts]))
        # flats pay a penalty (no shading); ramp tones used as flats pay more,
        # because textures often have baked shadows of the ramp colour
        self.penalty = np.array([0.0 if o[1] else
                                 (self.flat_penalty if o[0] in (0, 15) else self.tone_penalty)
                                 for o in opts])
        # flat options in the background colour: only for near-black sources
        self.bg_opt = np.array([not o[1] and tuple(pal[o[0]]) == BACKGROUND for o in opts])

    def appearance(self, lin):
        """Linear albedo -> sRGB as a camera-facing face would show it: the
        gain, the hue-preserving limit of quant_ramp, then the typical tone."""
        return gained(lin_to_srgb(lin), self.gain) * TYPICAL

    def distances(self, lin):
        lab = srgb_to_lab(self.appearance(lin))
        d = colour_dist(lab[:, None, :], self.opt_lab[None]) + self.penalty[None]
        d[np.ix_(lab[:, 0] > self.black_max, self.bg_opt)] = np.inf
        return d

    def classify(self, lin):
        """Option index for every colour (linear RGB (N,3))."""
        return np.argmin(self.distances(lin), axis=1)

    def cost(self, lin, area):
        """Area-weighted representation error (dE plus penalties)."""
        return float((self.distances(lin).min(axis=1) * area).sum() / area.sum())

    def describe(self):
        out = [f"ramp {c}..{c + 6}: base ({r},{g},{b})" for c, (r, g, b) in self.ramps]
        out.append("colour 15 (flat): ({},{},{})".format(*self.flat15))
        return out


def gained(srgb, gain):
    """sRGB times the gain; a colour that would pass 1 is scaled down as a
    whole, so it keeps its hue (the palette tops out at level 7)."""
    v = np.asarray(srgb, float) * gain
    m = v.max(axis=-1, keepdims=True)
    return np.where(m > 1, v / np.maximum(m, 1e-9), v)


TONE_WEIGHT = np.array([0.25, 0.5, 1, 2, 3, 2, 1.5])  # how often each tone shows (front faces: 4)
HUE_W = 4.0                   # weight of a*, b* against L*: 3-bit dark colours lose hue first
ALL_RGB = [(r, g, b) for r in range(8) for g in range(8) for b in range(8)]
_ramp_lab = None


def colour_dist(lab1, lab2):
    d = lab1 - lab2
    return np.sqrt(d[..., 0] ** 2 + HUE_W * (d[..., 1] ** 2 + d[..., 2] ** 2))


def quant_ramp(srgb, gain):
    """3-bit ramp base whose 7 rounded tones best match the ideal tones
    (hue-weighted Lab, weighted by how often each light level shows), among
    the bases whose darkest tone is not the background colour."""
    global _ramp_lab
    if _ramp_lab is None:
        _ramp_lab = srgb_to_lab(np.array([ramp_tones(b) for b in ALL_RGB], float) / 7)   # (512, 7, 3)
    ideal = gained(np.asarray(srgb, float), gain)
    ideal_lab = srgb_to_lab(np.array([ideal * (k + 1) / 7 for k in range(7)]))
    e = (colour_dist(_ramp_lab, ideal_lab[None]) * TONE_WEIGHT).sum(axis=1)
    # tone 0 (faces turned away from the light) must not be the background:
    # a base with every channel below 4 would draw them black
    e[[ramp_tones(b)[0] == BACKGROUND for b in ALL_RGB]] = np.inf
    return ALL_RGB[int(np.argmin(e))]


def quant_flat(srgb):
    """Nearest 3-bit colour (hue-weighted Lab)."""
    lab = srgb_to_lab(np.array(ALL_RGB, float) / 7)
    return ALL_RGB[int(np.argmin(colour_dist(lab, srgb_to_lab(np.asarray(srgb, float)))))]


def build_palette(lin, area, args):
    srgb = lin_to_srgb(lin)
    lab = srgb_to_lab(srgb)
    cent, lab_id = weighted_kmeans(lab, area, args.clusters)
    clusters = []
    for j in range(len(cent)):
        m = lab_id == j
        if not m.any():
            continue
        mean_lin = np.average(lin[m], axis=0, weights=area[m])
        clusters.append(dict(weight=float(area[m].sum() / area.sum()), srgb=lin_to_srgb(mean_lin),
                             lab=srgb_to_lab(lin_to_srgb(mean_lin))))
    clusters.sort(key=lambda c: -c["weight"])
    # an area-weighted sample keeps the search fast
    rng = np.random.default_rng(2)
    pick = rng.choice(len(lin), size=min(len(lin), 30000), replace=False, p=area / area.sum())
    s_lin, s_area = lin[pick], np.ones(len(pick))
    big = [cl for cl in clusters if cl["weight"] >= 0.04]
    big_lin = srgb_to_lin(np.array([cl["srgb"] for cl in big]))

    def total_cost(pal):
        """Representation error plus a penalty when two large materials end
        up on options that look alike (dE < 20 at the typical tone)."""
        c = pal.cost(s_lin, s_area)
        opt = pal.classify(big_lin)
        for i in range(len(big)):
            for j in range(i + 1, len(big)):
                d = float(colour_dist(pal.opt_lab[opt[i]], pal.opt_lab[opt[j]]))
                c += args.merge_penalty * min(big[i]["weight"], big[j]["weight"]) * max(0.0, 1 - d / 20)
        return c

    flat_cands = [(7, 7, 7)]
    for cl in clusters:
        q = quant_flat(gained(cl["srgb"], args.gain) * TYPICAL)
        if q not in flat_cands and cl["weight"] >= 0.005:
            flat_cands.append(q)
    if args.flat15:
        flat_cands = [tuple(int(t) for t in args.flat15.split(","))]

    def best_with(ramps):
        opts = [Palette(ramps, f, args.gain, args.flat_penalty, args.tone_penalty, args.black_max)
                for f in flat_cands]
        return min(((total_cost(p), p) for p in opts), key=lambda t: t[0])

    if args.ramp:
        ramps = []
        for spec in args.ramp:
            c, rgb = spec.split(":")
            ramps.append((int(c), tuple(int(t) for t in rgb.split(","))))
        return best_with(ramps)[1], clusters, []
    # every pair of the larger, not too dark clusters as the two ramps
    cands = [cl for cl in clusters if cl["lab"][0] >= 22 and cl["weight"] >= 0.02][:5]
    tried = []
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            if np.linalg.norm(cands[i]["lab"] - cands[j]["lab"]) < 15:
                continue
            tried.append(best_with([(1, quant_ramp(cands[i]["srgb"], args.gain)),
                                    (8, quant_ramp(cands[j]["srgb"], args.gain))]))
    if not tried:
        return best_with([(1, quant_ramp(cl["srgb"], args.gain)) for cl in cands[:1]])[1], clusters, []
    tried.sort(key=lambda t: t[0])
    return tried[0][1], clusters, tried


# ============================================================== decimation
class Qem:
    """Quadric edge-collapse decimator (Garland-Heckbert) for small meshes.
    Keeps the mesh manifold (link condition), refuses collapses that flip a
    face, adds constraint planes along open borders and colour borders, and
    weighs the face quadrics by --focus regions."""

    def __init__(self, P, T, face_w, labels=None, seam_w=0.0, flip_cos=0.2, min_quality=0.0):
        self.P = np.array(P, float)
        self.T = np.array(T, np.int64)
        n = len(self.P)
        self.alive_f = np.ones(len(self.T), bool)
        self.alive_v = np.zeros(n, bool)
        self.alive_v[np.unique(self.T)] = True
        self.nv = int(self.alive_v.sum())
        self.vf = [set() for _ in range(n)]
        for fi, t in enumerate(self.T):
            for v in t:
                self.vf[v].add(fi)
        self.flip_cos = flip_cos
        self.min_quality = min_quality
        self.Q = np.zeros((n, 4, 4))
        nrm, area = tri_normals(self.P, self.T)
        d = -np.einsum("ij,ij->i", nrm, self.P[self.T[:, 0]])
        h = np.c_[nrm, d]
        K = np.einsum("fi,fj->fij", h, h) * (area * face_w)[:, None, None]
        for k in range(3):
            np.add.at(self.Q, self.T[:, k], K)
        # borders: open edges, and edges between faces of different colour
        emap = {}
        for fi, (a, b, c) in enumerate(self.T):
            for e in ((a, b), (b, c), (c, a)):
                emap.setdefault((min(e), max(e)), []).append(fi)
        self.border_edges = 0
        for (a, b), fs in emap.items():
            if len(fs) == 1:
                wgt = 50.0
            elif len(fs) == 2 and labels is not None and seam_w > 0 and labels[fs[0]] != labels[fs[1]]:
                wgt = seam_w
            else:
                continue
            self.border_edges += 1
            e = self.P[b] - self.P[a]
            for fi in fs:
                pn = np.cross(e, nrm[fi])
                ln = np.linalg.norm(pn)
                if ln < 1e-20:
                    continue
                pn /= ln
                hh = np.r_[pn, -pn @ self.P[a]]
                kk = np.outer(hh, hh) * (e @ e) * wgt * face_w[fi]
                self.Q[a] += kk
                self.Q[b] += kk
        self.ver = np.zeros(n, np.int64)
        self.heap = []
        for (a, b) in emap:
            self._push(a, b)

    def _nbrs(self, u):
        s = set()
        for fi in self.vf[u]:
            s.update(self.T[fi])
        s.discard(u)
        return s

    def _push(self, u, v):
        Q = self.Q[u] + self.Q[v]
        pu, pv = self.P[u], self.P[v]
        mid = (pu + pv) / 2
        cands = [pu, pv, mid]
        try:
            p = np.linalg.solve(Q[:3, :3], -Q[:3, 3])
            if np.all(np.isfinite(p)) and np.linalg.norm(p - mid) <= 1.5 * np.linalg.norm(pu - pv):
                cands.insert(0, p)
        except np.linalg.LinAlgError:
            pass
        best, bp = None, None
        for p in cands:
            hh = np.r_[p, 1.0]
            c = hh @ Q @ hh
            if best is None or c < best - 1e-12:
                best, bp = c, p
        heapq.heappush(self.heap, (max(best, 0.0), u, v, self.ver[u], self.ver[v], tuple(bp)))

    def _valid(self, u, v, p):
        fu, fv = self.vf[u], self.vf[v]
        shared = fu & fv
        if not shared or len(shared) > 2:
            return False
        opp = set()
        for fi in shared:
            opp.update(self.T[fi])
        opp -= {u, v}
        nu, nv = self._nbrs(u), self._nbrs(v)
        if (nu & nv) != opp:
            return False
        if len((nu | nv) - {u, v}) < 3:
            return False
        for fi in (fu | fv) - shared:
            t = self.T[fi]
            old = self.P[t]
            new = old.copy()
            for k in range(3):
                if t[k] == u or t[k] == v:
                    new[k] = p
            n0 = np.cross(old[1] - old[0], old[2] - old[0])
            n1 = np.cross(new[1] - new[0], new[2] - new[0])
            l0, l1 = np.linalg.norm(n0), np.linalg.norm(n1)
            if l1 < 1e-12 * max(l0, 1e-30) or l0 == 0:
                return False
            if n0 @ n1 < self.flip_cos * l0 * l1:
                return False
            if self.min_quality > 0:
                # shape quality 4*sqrt(3)*area / sum of squared edges (1 = equilateral):
                # slivers make non-convex quads later, so do not create new ones
                q1 = 2 * math.sqrt(3) * l1 / (((new - np.roll(new, 1, axis=0)) ** 2).sum())
                if q1 < self.min_quality:
                    q0 = 2 * math.sqrt(3) * l0 / (((old - np.roll(old, 1, axis=0)) ** 2).sum())
                    if q1 < q0:
                        return False
        return True

    def run(self, stop_verts, snapshots=()):
        """Collapses until stop_verts vertices remain; returns {nv: (P, T)}
        for every vertex count in snapshots reached on the way."""
        snaps = {}
        want = set(snapshots)
        if self.nv in want:
            snaps[self.nv] = self.mesh()
        while self.nv > stop_verts and self.heap:
            cost, u, v, vu, vv, p = heapq.heappop(self.heap)
            if not (self.alive_v[u] and self.alive_v[v]) or self.ver[u] != vu or self.ver[v] != vv:
                continue
            p = np.array(p)
            if not self._valid(u, v, p):
                continue
            shared = self.vf[u] & self.vf[v]
            for fi in shared:
                self.alive_f[fi] = False
                for w in self.T[fi]:
                    self.vf[w].discard(fi)
            for fi in list(self.vf[v]):
                t = self.T[fi]
                t[t == v] = u
                self.vf[u].add(fi)
            self.vf[v] = set()
            self.alive_v[v] = False
            self.P[u] = p
            self.Q[u] += self.Q[v]
            self.ver[u] += 1
            self.ver[v] += 1
            self.nv -= 1
            for w in self._nbrs(u):
                self._push(u, w)
            if self.nv in want:
                snaps[self.nv] = self.mesh()
        return snaps

    def mesh(self):
        used = np.flatnonzero(self.alive_v)
        remap = -np.ones(len(self.P), np.int64)
        remap[used] = np.arange(len(used))
        return self.P[used].copy(), remap[self.T[self.alive_f]]


def repair_winding(P, T):
    """Drops double-sided fins (the same triangle twice), orients every part
    consistently from one seed face and turns inside-out parts around."""
    key = [tuple(sorted(t)) for t in T.tolist()]
    seen = {}
    for k in key:
        seen[k] = seen.get(k, 0) + 1
    T = np.array([t for t, k in zip(T.tolist(), key) if seen[k] == 1], np.int64).reshape(-1, 3)
    edges = {}
    for ti, (a, b, c) in enumerate(T.tolist()):
        for e in ((a, b), (b, c), (c, a)):
            edges.setdefault((min(e), max(e)), []).append(ti)
    T = T.copy()
    part = -np.ones(len(T), np.int64)
    for seed in range(len(T)):
        if part[seed] >= 0:
            continue
        part[seed] = seed
        stack = [seed]
        while stack:
            ti = stack.pop()
            a, b, c = T[ti]
            for x, y in ((a, b), (b, c), (c, a)):
                fs = edges[(min(x, y), max(x, y))]
                if len(fs) != 2:
                    continue
                nb = fs[0] if fs[1] == ti else fs[1]
                if part[nb] >= 0:
                    continue
                t2 = T[nb].tolist()
                if any((t2[i], t2[(i + 1) % 3]) == (x, y) for i in range(3)):
                    T[nb] = T[nb][::-1]                # same direction: flip the neighbour
                part[nb] = seed
                stack.append(nb)
    for sd in np.unique(part):
        m = part == sd
        if signed_volume(P, T[m]) < 0:
            T[m] = T[m][:, ::-1]
    return T


def fs_simplify(P, T, target, repair=False):
    import fast_simplification
    p, t = fast_simplification.simplify(P.astype(np.float64), T.astype(np.int64), target_count=int(target))
    p, t = np.asarray(p, float), np.asarray(t, np.int64)
    ok = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    t = t[ok]
    if repair:
        t = repair_winding(p, t)
    used = np.unique(t)
    remap = -np.ones(len(p), np.int64)
    remap[used] = np.arange(len(used))
    return p[used], remap[t]


def focus_weights(P, T, focus):
    w = np.ones(len(T))
    if not focus:
        return w
    y = P[T].mean(axis=1)[:, 1]
    lo, hi = P[:, 1].min(), P[:, 1].max()
    f = (y - lo) / (hi - lo)
    for spec in focus:
        a, b, k = (float(x) for x in spec.split(":"))
        w[(f >= a) & (f <= b)] *= k
    return w


# ============================================================== source -> faces
def map_source(P, T, src_c, src_n, order=8):
    """For every source triangle (centroid, normal) the index of the nearest
    triangle of (P, T) whose normal agrees."""
    from scipy.spatial import cKDTree
    bary = np.array([(i, j, order - i - j) for i in range(order + 1) for j in range(order + 1 - i)], float) / order
    pts = np.einsum("kj,tjc->tkc", bary, P[T]).reshape(-1, 3)
    owner = np.repeat(np.arange(len(T)), len(bary))
    nrm, _ = tri_normals(P, T)
    tree = cKDTree(pts)
    k = 8
    _, idx = tree.query(src_c, k=k)
    cand = owner[idx]                                             # (S, k)
    agree = np.einsum("skc,sc->sk", nrm[cand], src_n) > 0.0
    first = np.where(agree.any(axis=1), agree.argmax(axis=1), 0)
    return cand[np.arange(len(cand)), first]


# ============================================================== quads
def pair_quads(P, T, max_fold, limit, min_turn=0.05):
    """Pairs adjacent triangles into convex quads (maximum weight matching,
    fold <= max_fold degrees). Returns a list of faces (corner tuples of 4,
    triangles as a,b,c,c) and, for every face, the triangles it covers."""
    import networkx as nx
    nrm, _ = tri_normals(P, T)
    emap = {}
    for ti, (a, b, c) in enumerate(T):
        emap[(a, b)] = ti
        emap[(b, c)] = ti
        emap[(c, a)] = ti
    G = nx.Graph()
    quads = {}
    for (a, b), t1 in emap.items():
        t2 = emap.get((b, a))
        if t2 is None or t1 >= t2:
            continue
        x = [v for v in T[t1] if v != a and v != b]
        y = [v for v in T[t2] if v != a and v != b]
        if len(x) != 1 or len(y) != 1 or x[0] == y[0]:
            continue
        q = (a, y[0], b, x[0])
        fold = math.degrees(math.acos(np.clip(nrm[t1] @ nrm[t2], -1, 1)))
        if fold > max_fold:
            continue
        pts = P[list(q)]
        n = newell(pts)
        ln = np.linalg.norm(n)
        if ln < 1e-12:
            continue
        n /= ln
        ok = True
        for i in range(4):
            e0 = pts[i] - pts[i - 1]
            e1 = pts[(i + 1) % 4] - pts[i]
            s = np.cross(e0, e1) @ n / max(np.linalg.norm(e0) * np.linalg.norm(e1), 1e-30)
            if s < min_turn:
                ok = False
                break
        if not ok:
            continue
        G.add_edge(t1, t2, weight=1000.0 - fold)
        quads[(t1, t2)] = (q, fold)
    match = nx.max_weight_matching(G, maxcardinality=True)
    pairs = []
    for t1, t2 in match:
        key = (min(t1, t2), max(t1, t2))
        pairs.append((quads[key][1], key))
    pairs.sort()                                        # flattest first
    n_faces = len(T) - len(pairs)
    spare = max(0, limit - n_faces)
    if spare:                                           # use the spare budget on the most folded quads
        pairs = pairs[:len(pairs) - spare] if spare < len(pairs) else []
    used = set()
    faces, cover, folds = [], [], []
    for fold, (t1, t2) in pairs:
        q = quads[(t1, t2)][0]
        faces.append(best_rotation(P, q))
        cover.append((t1, t2))
        folds.append(fold)
        used.update((t1, t2))
    for ti, (a, b, c) in enumerate(T):
        if ti not in used:
            faces.append((a, b, c, c))
            cover.append((ti,))
    return faces, cover, folds


def newell(pts):
    n = np.zeros(3)
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        n += np.array([(a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]), (a[0] - b[0]) * (a[1] + b[1])])
    return n


def best_rotation(P, q):
    """Rotation of the quad whose first three corners (the culling triangle
    of geo3d) best represent the whole face."""
    pts = P[list(q)]
    n = newell(pts)
    best, br = None, 0
    for r in range(4):
        a, b, c = pts[r], pts[(r + 1) % 4], pts[(r + 2) % 4]
        s = np.cross(b - a, c - a) @ n
        if best is None or s > best:
            best, br = s, r
    return tuple(q[(br + k) % 4] for k in range(4))


# ============================================================== model
class G3Model:
    def __init__(self, name, strategy, verts, faces, colours, normals, info):
        self.name = name
        self.strategy = strategy
        self.verts = verts            # list of (x, y, z) ints
        self.faces = faces            # list of (a, b, c, d)
        self.colours = colours        # list of palette indices
        self.normals = normals        # list of (nx, ny, nz) Q2.14 (0 for flat faces)
        self.info = info


def build_model(name, strategy, P, T, faces, cover, folds, src, pal, args, extra):
    """Rounds to integers, colours the faces and checks them."""
    faces = [tuple(int(v) for v in f) for f in faces]
    Pi = np.round(P).astype(np.int64)
    src_c, src_n, src_area, src_opt, src_lin = src
    tri_of_src = map_source(P, T, src_c, src_n)
    face_of_tri = np.zeros(len(T), np.int64)
    for fi, cv in enumerate(cover):
        for ti in cv:
            face_of_tri[ti] = fi
    fos = face_of_tri[tri_of_src]
    nf = len(faces)
    votes = np.zeros((nf, len(pal.options)))
    np.add.at(votes, (fos, src_opt), src_area)
    wsum = np.bincount(fos, weights=src_area, minlength=nf)
    mean_lin = np.zeros((nf, 3))
    for k in range(3):
        mean_lin[:, k] = np.bincount(fos, weights=src_area * src_lin[:, k], minlength=nf)
    empty = wsum <= 0
    mean_lin[~empty] /= wsum[~empty, None]
    if args.colour == "mean":
        opt = pal.classify(np.maximum(mean_lin, 1e-6))
    else:
        opt = np.argmax(votes, axis=1)
    # faces that received no source triangle (tiny): copy a neighbour's option
    if empty.any():
        vf = {}
        for fi, f in enumerate(faces):
            for v in set(f):
                vf.setdefault(v, []).append(fi)
        for fi in np.flatnonzero(empty):
            nb = [g for v in set(faces[fi]) for g in vf[v] if not empty[g]]
            if nb:
                opt[fi] = max(set(opt[nb].tolist()), key=opt[nb].tolist().count)
    colours, normals, bad, nonconvex = [], [], 0, 0
    for fi, f in enumerate(faces):
        o = pal.options[opt[fi]]
        colours.append(o[0])
        pts = Pi[list(f)].astype(float)
        n = newell(pts)
        ln = np.linalg.norm(n)
        nf_ = newell(P[list(f)])
        if ln < 1e-9 or n @ nf_ <= 0:
            bad += 1
            n = nf_
            ln = np.linalg.norm(n)
        elif f[2] != f[3]:
            for i in range(4):                          # convex on the integer corners
                e0, e1 = pts[i] - pts[i - 1], pts[(i + 1) % 4] - pts[i]
                if np.cross(e0, e1) @ n <= 0:
                    nonconvex += 1
                    break
        if o[1]:
            normals.append(tuple(int(round(c / ln * 16384)) for c in n))
        else:
            normals.append((0, 0, 0))
    nquad = sum(1 for f in faces if f[2] != f[3])
    info = dict(extra)
    info.update(vertices=len(Pi), faces=nf, quads=nquad, triangles=nf - nquad,
                max_fold=round(max(folds), 1) if folds else 0.0,
                mean_fold=round(float(np.mean(folds)), 1) if folds else 0.0,
                degenerate_after_rounding=bad, nonconvex_quads=nonconvex,
                shaded_faces=sum(1 for c in colours if c in pal.ramp_bases),
                flat_faces=sum(1 for c in colours if c not in pal.ramp_bases),
                extent=[int(Pi[:, k].min()) for k in range(3)] + [int(Pi[:, k].max()) for k in range(3)])
    m = G3Model(name, strategy, [tuple(int(c) for c in p) for p in Pi], faces, colours, normals, info)
    m.mean_rgb = [tuple(int(round(c * 255)) for c in lin_to_srgb(ml)) for ml in mean_lin]
    return m


def closed_mesh_check(T):
    e = {}
    for a, b, c in T:
        for x, y in ((a, b), (b, c), (c, a)):
            e[(x, y)] = e.get((x, y), 0) + 1
    open_e = sum(1 for (x, y) in e if (y, x) not in e)
    dup = sum(1 for v in e.values() if v > 1)
    return open_e, dup


def largest_fit(sizes, fetch, fold, limit, max_verts):
    """Largest mesh (sizes descending) whose quad pairing fits the limits."""
    for s in sizes:
        p, t = fetch(s)
        if len(p) > max_verts or len(t) - len(t) // 2 > limit:
            continue
        if closed_mesh_check(t)[1]:                     # a directed edge used twice: flipped or
            continue                                    # non-manifold faces, holes on screen
        # convexity and fold are tested on the integer corners that go out
        faces, cover, folds = pair_quads(np.round(p), t, fold, limit)
        if len(faces) <= limit:
            return p, t, faces, cover, folds
    return None


def decimate(strategy, P, T, src, pal, args):
    """Candidate models of one strategy: one per quad fold limit (--fold)."""
    t0 = time.time()
    src_c, src_n, src_area, src_opt, src_lin = src
    limit = args.max_faces
    found = []                                          # (fold limit, p, t, faces, cover, folds)
    if strategy == "fs":
        cache = {}

        def fetch(target):
            if target not in cache:
                cache[target] = fs_simplify(P, T, target, repair=True)
            return cache[target]
        for fold in args.fold:
            r = largest_fit(range(2 * args.max_verts + 16, 200, -4), fetch, fold, limit, args.max_verts)
            if r:
                found.append((fold,) + r)
        extra = dict(pre_faces=len(T))
    else:
        pre = args.pre_faces
        p0, t0_ = fs_simplify(P, T, pre) if len(T) > pre else (P, T)
        labels = None
        if args.seam > 0:
            tri = map_source(p0, t0_, src_c, src_n)
            votes = np.zeros((len(t0_), len(pal.options)))
            np.add.at(votes, (tri, src_opt), src_area)
            labels = np.argmax(votes, axis=1)
        fw = focus_weights(p0, t0_, args.focus)
        q = Qem(p0, t0_, fw, labels, args.seam, min_quality=args.min_quality)
        if strategy == "tri":
            snaps = q.run(100, snapshots=range(100, 200))
            for nv in sorted(snaps, reverse=True):
                p, t = snaps[nv]
                if len(t) <= limit:
                    found.append((0, p, t, [tuple(x) + (x[2],) for x in t], [(i,) for i in range(len(t))], []))
                    break
        else:
            snaps = q.run(150, snapshots=range(150, args.max_verts + 1))
            for fold in args.fold:
                r = largest_fit(sorted(snaps, reverse=True), snaps.get, fold, limit, args.max_verts)
                if r:
                    found.append((fold,) + r)
        extra = dict(pre_faces=len(t0_), seam_edges=q.border_edges)
    if not found:
        raise RuntimeError(f"{strategy}: no size fits the limits")
    out = []
    for fold, p, t, faces, cover, folds in found:
        open_e, dup = closed_mesh_check(t)
        ex = dict(extra, fold_limit=fold, triangles_before_pairing=len(t), open_edges=open_e,
                  duplicate_edges=dup, seconds=round(time.time() - t0, 1))
        out.append(build_model(args.name, strategy, p, t, faces, cover, folds, src, pal, args, ex))
    return out


# ============================================================== rendering
def light_q14():
    ln = math.sqrt(sum(c * c for c in LIGHT))
    return [int(round(c / ln * 16384)) for c in LIGHT]


def cfg_for(ax, ay, az=0.0, scale=1):
    r = rot_matrix(ax, ay, az)
    m = [int(np.clip(round(r[i, j] * 16384), -32768, 32767)) for i in range(3) for j in range(3)]
    return m + [0, 0, CAM_DIST * scale] + [FOCAL, CX, CY, ZNEAR, SCR_W, SCR_H]


_render_faces = None


def sim_renderer():
    global _render_faces
    if _render_faces is None:
        try:
            sys.path.insert(0, SIM_DIR)
            from gen_scenes import render_faces
            _render_faces = render_faces
        except ImportError:
            _render_faces = False
    return _render_faces


def render_g3(model, pal, ax, ay, az=0.0):
    """Index image (H, W) and coverage mask, from the geo3d reference model
    (bit exact with the RTL) or, without sim/, an own painter renderer."""
    faces = [tuple(f) + tuple(n) + (c,) for f, n, c in zip(model.faces, model.normals, model.colours)]
    cfg = cfg_for(ax, ay, az, getattr(model, "scale", 1))
    pix = np.zeros((SCR_H, SCR_W), np.uint8)
    cov = np.zeros((SCR_H, SCR_W), bool)
    rf = sim_renderer()
    if rf:
        cmds, _, _, _ = rf(cfg, model.verts, faces, 0, 0, light_q14())
        for c in cmds:
            x, y, n = c[0] | (c[1] << 8), c[2] | (c[3] << 8), c[4] | (c[5] << 8)
            pix[y, x:x + n + 1] = c[8] & 15
            cov[y, x:x + n + 1] = True
        return pix, cov
    # fallback: same rules, PIL polygons
    r = np.array(cfg[:9], float).reshape(3, 3) / 16384
    V = np.array(model.verts, float) @ r.T + np.array([0, 0, cfg[11]])
    sx = CX + V[:, 0] * FOCAL / V[:, 2]
    sy = CY - V[:, 1] * FOCAL / V[:, 2]
    lm = r.T @ (np.array(light_q14(), float) / 16384)
    order = []
    for fi, f in enumerate(model.faces):
        x0, y0, x1, y1, x2, y2 = sx[f[0]], sy[f[0]], sx[f[1]], sy[f[1]], sx[f[2]], sy[f[2]]
        if (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0) <= 0 or min(V[list(f), 2]) < ZNEAR:
            continue
        s = lm @ np.array(model.normals[fi], float) / 16384
        lvl = 0 if s <= 0 else min(6, int(s * 7))
        order.append((-sum(V[list(f), 2]), fi, model.colours[fi] + lvl))
    img = Image.new("L", (SCR_W, SCR_H), 0)
    msk = Image.new("L", (SCR_W, SCR_H), 0)
    d, dm = ImageDraw.Draw(img), ImageDraw.Draw(msk)
    for _, fi, col in sorted(order):
        pts = [(sx[v], sy[v]) for v in model.faces[fi]]
        d.polygon(pts, fill=int(col))
        dm.polygon(pts, fill=255)
    return np.asarray(img).copy(), np.asarray(msk) > 0


def to_rgb(pix, pal):
    lut = np.array([[round(c * 255 / 7) for c in p] for p in pal.pal], np.uint8)
    return lut[pix]


def render_source(P, T, lin, pal, ax, ay, az=0.0):
    """Source mesh with the geo3d light model (level = min(6, 7 L.N), tone
    (level+1)/7), continuous levels and full colour, painter order."""
    r = rot_matrix(ax, ay, az)
    V = P @ r.T + np.array([0, 0, CAM_DIST])
    sx = CX + V[:, 0] * FOCAL / V[:, 2]
    sy = CY - V[:, 1] * FOCAL / V[:, 2]
    a = (sx[T[:, 1]] - sx[T[:, 0]]) * (sy[T[:, 2]] - sy[T[:, 0]]) - \
        (sy[T[:, 1]] - sy[T[:, 0]]) * (sx[T[:, 2]] - sx[T[:, 0]])
    vis = np.flatnonzero(a > 0)
    nrm, _ = tri_normals(V, T)                       # camera space, outwards
    L = np.array(LIGHT) / np.linalg.norm(LIGHT)
    lvl = np.minimum(6, 7 * np.maximum(0, nrm @ L))
    col = gained(lin_to_srgb(lin), pal.gain)
    rgb = np.clip(col * ((lvl + 1) / 7)[:, None] * 255, 0, 255).astype(np.uint8)
    key = V[T, 2].sum(axis=1)
    vis = vis[np.argsort(-key[vis], kind="stable")]
    img = Image.new("RGBA", (SCR_W, SCR_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for ti in vis:
        t = T[ti]
        d.polygon([(sx[t[0]], sy[t[0]]), (sx[t[1]], sy[t[1]]), (sx[t[2]], sy[t[2]])],
                  fill=tuple(int(c) for c in rgb[ti]) + (255,))
    arr = np.asarray(img)
    return arr[..., :3].copy(), arr[..., 3] > 0


def font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
              "C:/Windows/Fonts/arial.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def outline(img2x, model, ax, ay):
    """Draws the outlines of the visible faces over a 2x image."""
    r = np.array(cfg_for(ax, ay)[:9], float).reshape(3, 3) / 16384
    V = np.array(model.verts, float) @ r.T + np.array([0, 0, CAM_DIST])
    sx = (CX + V[:, 0] * FOCAL / V[:, 2]) * 2
    sy = (CY - V[:, 1] * FOCAL / V[:, 2]) * 2
    d = ImageDraw.Draw(img2x)
    for f in model.faces:
        x0, y0, x1, y1, x2, y2 = sx[f[0]], sy[f[0]], sx[f[1]], sy[f[1]], sx[f[2]], sy[f[2]]
        if (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0) <= 0:
            continue
        pts = [(sx[v], sy[v]) for v in (f if f[2] != f[3] else f[:3])]
        d.line(pts + [pts[0]], fill=(255, 255, 255), width=1)
    return img2x


_src_cache = {}


def reference_model(P, T, src, pal, focus, faces=8000, scale=16):
    """The source reduced to about 8000 triangles, coloured like the final
    model and drawn by the same geo3d reference model (coordinates x16, the
    camera too, so the projection is the same): the yardstick of the
    metrics, free of the differences between two rasterisers."""
    src_c, src_n, src_area, src_opt, _ = src
    p, t = fs_simplify(P, T, faces) if len(T) > faces else (P, T)
    tri = map_source(p, t, src_c, src_n)
    votes = np.zeros((len(t), len(pal.options)))
    np.add.at(votes, (tri, src_opt), src_area)
    opt = np.argmax(votes, axis=1)
    nrm, _ = tri_normals(p, t)
    cols, nrms = [], []
    for k in range(len(t)):
        o = pal.options[opt[k]]
        cols.append(o[0])
        nrms.append(tuple(int(round(c * 16384)) for c in nrm[k]) if o[1] else (0, 0, 0))
    m = G3Model("reference", "reference", [tuple(int(c) for c in v) for v in np.round(p * scale)],
                [(int(a), int(b), int(c), int(c)) for a, b, c in t], cols, nrms, {})
    m.scale = scale
    # the same faces with the --focus weight class as a flat colour: a map
    # of how much each screen pixel counts in the metrics
    fw = focus_weights(p, t, focus)
    levels = sorted(set(fw.tolist()))[:15]
    wm = G3Model("weights", "weights", m.verts, m.faces,
                 [1 + levels.index(w) if w in levels else 15 for w in fw.tolist()], [(0, 0, 0)] * len(t), {})
    wm.scale = scale
    wm.levels = np.array([1.0] + levels + [levels[-1]] * (15 - len(levels)))
    m.weights = wm
    return m


def visible(pix, cov, pal):
    """Pixels of the figure that do not show the background colour."""
    return cov & np.array([tuple(c) != BACKGROUND for c in pal.pal])[pix]


def match(a_m, a_rgb, b_m, b_rgb, wmap):
    """Weighted silhouette IoU of the masks and mean Lab dE over their union."""
    u = a_m | b_m
    iou = (wmap * (a_m & b_m)).sum() / max((wmap * u).sum(), 1e-9)
    if not u.any():
        return float(iou), 0.0
    la = srgb_to_lab(a_rgb[u] / 255.0)
    lb = srgb_to_lab(b_rgb[u] / 255.0)
    return float(iou), float((np.sqrt(((la - lb) ** 2).sum(-1)) * wmap[u]).sum() / wmap[u].sum())


def compare_views(model, pal, P, T, lin, views, ref):
    """Per view: source image (full colour, for the eye), geo3d image, and
    two comparisons of the visible figure (a pixel in the background colour
    counts as background, as on the MSX), pixels weighted by --focus:
      iou, de          against the reference model drawn by geo3d (the
                       source reduced to 8000 triangles, same palette and
                       colouring): what the decimation keeps; the score;
      src_iou, src_de  against the source mesh itself (render_source: full
                       colour, the geo3d light model): what the MSX shows
                       of the source, palette included;
      black            share of the figure's pixels in the background colour."""
    out = []
    for ax, ay in views:
        key = (id(P), ax, ay)
        if key not in _src_cache:
            rpix, rcov = render_g3(ref, pal, ax, ay)
            wpix, _ = render_g3(ref.weights, pal, ax, ay)
            _src_cache[key] = render_source(P, T, lin, pal, ax, ay) + (to_rgb(rpix, pal), visible(rpix, rcov, pal),
                                                                        ref.weights.levels[wpix])
        src_rgb, src_m, ref_rgb, ref_v, wmap = _src_cache[key]
        pix, cov = render_g3(model, pal, ax, ay)
        g_rgb = to_rgb(pix, pal)
        vis = visible(pix, cov, pal)
        iou, de = match(ref_v, ref_rgb, vis, g_rgb, wmap)
        src_iou, src_de = match(src_m, src_rgb, vis, g_rgb, wmap)
        black = float((cov & ~vis).sum() / max(cov.sum(), 1))
        out.append(dict(view=(ax, ay), src=src_rgb, ref=ref_rgb, g3=g_rgb, iou=iou, de=de,
                        src_iou=src_iou, src_de=src_de, black=black))
    return out


def sheet_rows(results):
    """The results of the sheet views, in the order of SHEET_VIEWS."""
    by_view = {r["view"]: r for r in results}
    return [by_view[v] for v in SHEET_VIEWS if v in by_view]


def sheet(results, model, title, path, scale=2):
    results = sheet_rows(results)
    W, H = SCR_W * scale, SCR_H * scale
    pad, head = 10, 26
    cols = 3
    img = Image.new("RGB", (cols * (W + pad) + pad, len(results) * (H + head) + 44), (12, 12, 16))
    d = ImageDraw.Draw(img)
    d.text((pad, 10), title, font=font(18), fill=(230, 230, 235))
    f2 = font(14)
    for i, r in enumerate(results):
        y = 44 + i * (H + head)
        ax, ay = r["view"]
        labels = [f"source mesh  (G3ROT ax={ax}, ay={ay})",
                  f"geo3d  IoU {r['iou']:.3f} dE {r['de']:.1f}  source IoU {r['src_iou']:.3f} dE {r['src_de']:.1f}",
                  "visible face outlines"]
        ims = [Image.fromarray(r["src"]).resize((W, H), Image.NEAREST),
               Image.fromarray(r["g3"]).resize((W, H), Image.NEAREST)]
        ims.append(outline(ims[1].copy(), model, ax, ay))
        for k, im in enumerate(ims):
            x = pad + k * (W + pad)
            d.text((x, y + 4), labels[k], font=f2, fill=(180, 180, 190))
            img.paste(im, (x, y + head - 4))
    img.save(path)


def turntable(model, pal, P, T, lin, path, frames=36, pitch=-10, scale=2):
    ims = []
    for k in range(frames):
        ay = 180 + 360 * k / frames
        src, _ = render_source(P, T, lin, pal, pitch, ay)
        pix, _ = render_g3(model, pal, pitch, ay)
        fr = Image.new("RGB", (SCR_W * scale * 2 + 8, SCR_H * scale), (12, 12, 16))
        fr.paste(Image.fromarray(src).resize((SCR_W * scale, SCR_H * scale), Image.NEAREST), (0, 0))
        fr.paste(Image.fromarray(to_rgb(pix, pal)).resize((SCR_W * scale, SCR_H * scale), Image.NEAREST),
                 (SCR_W * scale + 8, 0))
        ims.append(fr.quantize(colors=128, method=Image.Quantize.MEDIANCUT))
    ims[0].save(path, save_all=True, append_images=ims[1:], duration=100, loop=0, optimize=True)


# ============================================================== writers
def basic_lines(model, pal, args):
    """Palette subroutine + G3DATA lines. Returns (lines, restore, gosub)."""
    ln, step, width = args.line, args.step, args.max_line
    out = []

    def add(text):
        nonlocal ln
        s = f"{ln} {text}"
        if len(s) > 250:
            raise ValueError("BASIC line too long")
        out.append(s)
        ln += step

    nv, nf = len(model.verts), len(model.faces)
    add(f"' {model.name}: {nv} vertices, {nf} faces (glb2g3.py). GOSUB {ln + step}: palette. "
        f"RESTORE {ln + 2 * step}:CALL G3DATA(16,0)")
    gosub = ln
    cmds = [f"CALL G3RAMP({c},{r},{g},{b})" for c, (r, g, b) in pal.ramps]
    if pal.flat15 != (7, 7, 7):
        cmds.append("CALL G3PAL(15,{},{},{})".format(*pal.flat15))
    add(":".join(cmds + ["RETURN"]))
    restore = ln

    def data(records):
        cur = []
        for rec in records:
            txt = ",".join(str(v) for v in rec)
            if cur and len(f"{ln} DATA " + ",".join(cur + [txt])) > width:
                add("DATA " + ",".join(cur))
                cur = []
            cur.append(txt)
        if cur:
            add("DATA " + ",".join(cur))

    add(f"DATA {nv},{nf},0,0")
    data(model.verts)
    data([tuple(f) + (c,) for f, c in zip(model.faces, model.colours)])
    return out, restore, gosub


def write_outputs(model, pal, clusters, outdir, args, results):
    os.makedirs(outdir, exist_ok=True)
    name = model.name
    lines, restore, gosub = basic_lines(model, pal, args)
    with open(os.path.join(outdir, f"{name}.bas"), "wb") as fh:
        fh.write(("\r\n".join(lines) + "\r\n").encode("ascii") + b"\x1a")
    doc = dict(name=name, source=os.path.basename(args.input), tool="glb2g3.py", strategy=model.strategy,
               units=dict(height=args.height, axes="X right, Y up, Z into the screen, front +Z",
                          winding="cross(v1-v0, v2-v0) points outwards; load with G3DATA(m,0)"),
               nv=len(model.verts), nf=len(model.faces), ne=0, t=0,
               vertices=[list(v) for v in model.verts],
               faces=[dict(v=list(f), colour=c, shaded=c in pal.ramp_bases, normal_q14=list(n), mean_rgb=list(m))
                      for f, c, n, m in zip(model.faces, model.colours, model.normals, model.mean_rgb)],
               palette=dict(colours=[list(p) for p in pal.pal],
                            ramps=[dict(c=c, rgb=list(rgb)) for c, rgb in pal.ramps],
                            flat15=list(pal.flat15), gain=pal.gain,
                            basic=[l.split(" ", 1)[1] for l in lines[1:2]]),
               basic=dict(first_line=args.line, last_line=args.line + (len(lines) - 1) * args.step,
                          palette_gosub=gosub, restore=restore, lines=len(lines),
                          max_line_chars=max(len(l) for l in lines)),
               stats=model.info,
               views=[dict(ax=r["view"][0], ay=r["view"][1], iou=round(r["iou"], 4), de=round(r["de"], 2),
                           src_iou=round(r["src_iou"], 4), src_de=round(r["src_de"], 2),
                           black=round(r["black"], 4))
                      for r in results],
               colour_clusters=[dict(weight=round(c["weight"], 4), rgb255=[int(round(x * 255)) for x in c["srgb"]])
                                for c in clusters])
    with open(os.path.join(outdir, f"{name}.json"), "w") as fh:
        json.dump(doc, fh, indent=1)
    return lines, restore, gosub


def report_text(model, pal, clusters, results, lines, restore, gosub, args, extra=""):
    i = model.info
    def mean(k):
        return np.mean([r[k] for r in results]) if results else float("nan")

    t = [f"glb2g3 report: {model.name} ({os.path.basename(args.input)}), strategy {model.strategy}",
         "",
         f"vertices {i['vertices']} (max {args.max_verts}), faces {i['faces']} (max {args.max_faces}): "
         f"{i['quads']} quads + {i['triangles']} triangles, from {i['triangles_before_pairing']} triangles",
         f"quad fold: mean {i['mean_fold']} deg, max {i['max_fold']} deg (limit {i['fold_limit']})",
         f"closed mesh check: {i['open_edges']} open edges, {i['duplicate_edges']} duplicated edges; "
         f"on the integer corners: {i['degenerate_after_rounding']} degenerate or flipped faces, "
         f"{i['nonconvex_quads']} non-convex quads",
         f"extent x {i['extent'][0]}..{i['extent'][3]}, y {i['extent'][1]}..{i['extent'][4]}, "
         f"z {i['extent'][2]}..{i['extent'][5]}",
         f"shaded faces {i['shaded_faces']}, flat faces {i['flat_faces']}",
         f"decimation time {i['seconds']} s",
         "",
         "palette (SCREEN 5, levels 0..7):"] + ["  " + s for s in pal.describe()] + [
         "  " + " ".join(f"{k}:{r}{g}{b}" for k, (r, g, b) in enumerate(pal.pal)),
         "colour clusters of the source (area share, --focus weighted; sRGB):"] + [
         "  {:5.1%}  ({:3d},{:3d},{:3d})".format(c["weight"], *[int(round(x * 255)) for x in c["srgb"]])
         for c in clusters] + [
         "",
         f"BASIC: lines {args.line}..{args.line + (len(lines) - 1) * args.step} ({len(lines)} lines, "
         f"longest {max(len(l) for l in lines)} chars)",
         f"  GOSUB {gosub} (palette), RESTORE {restore}:CALL G3DATA(16,0)",
         "",
         "views (G3ROT ax, ay; the model faces the default camera at ay=180). The visible figure (a",
         "pixel in the background colour counts as background, as on the MSX), pixels weighted by the",
         "--focus weight of the surface under them, against:",
         "  ref     the source reduced to 8000 triangles, coloured the same way and drawn by the same",
         "          geo3d model: what the decimation keeps (the score of the candidates);",
         "  source  the source mesh itself in full colour, with the geo3d light model: what the MSX",
         "          shows of the source, palette included;",
         "  black   share of the figure drawn in the background colour:"] + [
         f"  ax={r['view'][0]:4d} ay={r['view'][1]:4d}  ref IoU {r['iou']:.3f} dE {r['de']:4.1f}   "
         f"source IoU {r['src_iou']:.3f} dE {r['src_de']:4.1f}   black {r['black']:5.1%}"
         for r in sheet_rows(results)] + [
         f"  mean of {len(results)} views (12 headings x 2 pitches + the above): ref IoU {mean('iou'):.3f} "
         f"dE {mean('de'):.1f}; source IoU {mean('src_iou'):.3f} dE {mean('src_de'):.1f}; "
         f"black {mean('black'):.1%} (worst {max((r['black'] for r in results), default=0):.1%})"]
    return "\n".join(t) + ("\n" + extra if extra else "") + "\n"


# ============================================================== main
def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="glTF 2.0 (.glb/.gltf) to geo3d model (BASIC DATA for CALL G3DATA)")
    ap.add_argument("input")
    ap.add_argument("-o", "--outdir", default=".")
    ap.add_argument("--name", default=None, help="base name of the output files")
    ap.add_argument("--strategy", default="qem", choices=["qem", "fs", "tri", "all"])
    ap.add_argument("--height", type=float, default=200, help="model height in units (default 200)")
    ap.add_argument("--yaw", type=float, default=0, help="extra turn about Y if the front is not glTF +Z")
    ap.add_argument("--max-verts", type=int, default=MAX_VERTS)
    ap.add_argument("--max-faces", type=int, default=MAX_FACES)
    ap.add_argument("--fold", default="30,45,60,90",
                    help="fold limits of the quads to try, degrees; the best preview score wins")
    ap.add_argument("--pre-faces", type=int, default=8000, help="fast pre-reduction target (qem, tri)")
    ap.add_argument("--focus", action="append", default=[],
                    help="Y0:Y1:W, weight W for the height band Y0..Y1 (0 feet .. 1 top), e.g. 0.86:1:3")
    ap.add_argument("--seam", type=float, default=0.0,
                    help="qem: weight of constraint planes along colour borders (0 = off)")
    ap.add_argument("--min-quality", type=float, default=0.15,
                    help="qem: do not create triangles below this shape quality (0..1, 1 = equilateral)")
    ap.add_argument("--min-part", type=float, default=0.002, help="drop loose parts below this area share")
    ap.add_argument("--clusters", type=int, default=6)
    ap.add_argument("--gain", type=float, default=1.35, help="brightness gain of the ramps (lighting)")
    ap.add_argument("--flat-penalty", type=float, default=8.0, help="dE added to flat colours 0 and 15")
    ap.add_argument("--tone-penalty", type=float, default=16.0, help="dE added to ramp tones used as flats")
    ap.add_argument("--black-max", type=float, default=12.0,
                    help="L* (as a lit face shows it) above which a source colour never becomes a flat face "
                         "in the background colour (black)")
    ap.add_argument("--merge-penalty", type=float, default=60.0,
                    help="palette search: cost of two large materials sharing one colour")
    ap.add_argument("--ramp", action="append", default=[], help="C:R,G,B forces a ramp (C 1..9)")
    ap.add_argument("--flat15", default=None, help="R,G,B forces colour 15")
    ap.add_argument("--colour", default="vote", choices=["vote", "mean"])
    ap.add_argument("--line", type=int, default=10000, help="first BASIC line number")
    ap.add_argument("--step", type=int, default=10)
    ap.add_argument("--max-line", type=int, default=160, help="max characters of a DATA line")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--no-gif", action="store_true")
    ap.add_argument("--pick", default=None, help="with --strategy all: keep this one instead of the best score")
    args = ap.parse_args(argv)
    args.fold = [float(x) for x in args.fold.split(",")]
    if args.name is None:
        args.name = os.path.splitext(os.path.basename(args.input))[0]
    return args


def score(res):
    """Preview score: mean silhouette IoU minus mean colour error / 400."""
    if not res:
        return 0.0
    return float(np.mean([r["iou"] for r in res]) - np.mean([r["de"] for r in res]) / 400)


def summary(m, res):
    i = m.info
    def mean(k):
        return np.mean([r[k] for r in res]) if res else 0

    return (f"{m.strategy:4s} fold<={i['fold_limit']:<3g} {i['vertices']:3d} v {i['faces']:3d} f "
            f"({i['quads']:3d} quads, max fold {i['max_fold']:5.1f})  "
            f"IoU {mean('iou'):.3f}  dE {mean('de'):.1f}  score {score(res):.4f}  "
            f"(source IoU {mean('src_iou'):.3f} dE {mean('src_de'):.1f}, black {mean('black'):.1%})")


def main(argv=None):
    args = parse_args(argv)
    t0 = time.time()
    log(f"reading {args.input}")
    V, F, C = load_gltf(args.input)
    log(f"  {len(V)} vertices, {len(F)} triangles")
    V, F, C, cinfo = clean_mesh(V, F, C, args.min_part)
    log(f"  welded: {cinfo['welded']} positions, {cinfo['duplicates']} duplicate triangles, "
        f"{cinfo['parts_kept']}/{cinfo['parts']} parts kept, {len(F)} triangles")
    P, T, flipped = to_geo3d(V, F, args.height, args.yaw)
    if flipped:
        log("  note: the source was inside out, winding reversed")
    n, area = tri_normals(P, T)
    cen = P[T].mean(axis=1)
    pal, clusters, tried = build_palette(C, area * focus_weights(P, T, args.focus), args)
    pal_lines = [f"  cost {c:6.2f}: " + "; ".join(p_.describe()) for c, p_ in tried[:6]]
    for line in pal_lines:
        log("  palette" + line)
    log("  palette: " + "; ".join(pal.describe()))
    src_opt = pal.classify(C)
    src = (cen, n, area, src_opt, C)
    strategies = ["qem", "fs", "tri"] if args.strategy == "all" else [args.strategy]
    views = SCORE_VIEWS
    ref = reference_model(P, T, src, pal, args.focus) if not args.no_preview else None
    rows, best_of = [], []
    for s in strategies:
        log(f"strategy {s}")
        cands = []
        for m in decimate(s, P, T, src, pal, args):
            res = compare_views(m, pal, P, T, C, views, ref) if not args.no_preview else []
            cands.append((m, res))
            rows.append(summary(m, res))
            log("  " + rows[-1])
        best_of.append(max(cands, key=lambda mr: score(mr[1])))
    extra = f"palette search (best 6 of {len(tried)}):\n" + "\n".join(pal_lines) + "\n"
    extra += "candidates (score = mean IoU - mean dE / 400):\n" + "\n".join("  " + r for r in rows) + "\n"
    if len(best_of) > 1:
        for m, res in best_of:
            sub = os.path.join(args.outdir, "strategies", m.strategy)
            lines, rst, gs = write_outputs(m, pal, clusters, sub, args, res)
            if res:
                sheet(res, m, f"{m.name} - strategy {m.strategy}", os.path.join(sub, f"{m.name}_views.png"))
            with open(os.path.join(sub, "report.txt"), "w") as fh:
                fh.write(report_text(m, pal, clusters, res, lines, rst, gs, args))
        if not args.no_preview:
            compare_sheet(best_of, os.path.join(args.outdir, f"{args.name}_strategies.png"))
    pick = next((d for d in best_of if d[0].strategy == args.pick), None) or max(best_of, key=lambda mr: score(mr[1]))
    extra += f"kept: {pick[0].strategy}, fold limit {pick[0].info['fold_limit']}\n"
    m, res = pick
    lines, restore, gosub = write_outputs(m, pal, clusters, args.outdir, args, res)
    if res:
        sheet(res, m, f"{m.name}: {m.info['vertices']} vertices, {m.info['faces']} faces "
                      f"({m.info['quads']} quads), strategy {m.strategy}",
              os.path.join(args.outdir, f"{m.name}_views.png"))
        if not args.no_gif:
            turntable(m, pal, P, T, C, os.path.join(args.outdir, f"{m.name}_turntable.gif"))
    rep = report_text(m, pal, clusters, res, lines, restore, gosub, args, extra)
    rep += f"total time {time.time() - t0:.0f} s\n"
    with open(os.path.join(args.outdir, "report.txt"), "w") as fh:
        fh.write(rep)
    log(rep)


def compare_sheet(done, path, scale=2):
    W, H = SCR_W * scale, SCR_H * scale
    pad, head = 8, 22
    done = [(m, sheet_rows(res)) for m, res in done]
    res0 = done[0][1]
    img = Image.new("RGB", ((len(done) + 1) * (W + pad) + pad, len(res0) * (H + head) + pad), (12, 12, 16))
    d = ImageDraw.Draw(img)
    f2 = font(14)
    for i in range(len(res0)):
        y = pad + i * (H + head)
        cells = [("source  ax={} ay={}".format(*res0[i]["view"]), res0[i]["src"])]
        for m, res in done:
            cells.append((f"{m.strategy}: {m.info['vertices']}v {m.info['faces']}f  IoU {res[i]['iou']:.3f}"
                          f"  dE {res[i]['de']:.1f}",
                          res[i]["g3"]))
        for k, (lab, im) in enumerate(cells):
            x = pad + k * (W + pad)
            d.text((x, y), lab, font=f2, fill=(180, 180, 190))
            img.paste(Image.fromarray(im).resize((W, H), Image.NEAREST), (x, y + head - 4))
    img.save(path)


if __name__ == "__main__":
    main()
