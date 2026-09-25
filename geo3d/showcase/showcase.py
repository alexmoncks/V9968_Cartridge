#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Showcase videos for geo3d + V9968, built from the same pieces the tests use.

Each scene is a list of the port and VDP operations a Z80 program would do
(geo3d register writes, the Z80's own VDP commands, VRAM uploads, page flips).
From that one list:
  - the Python system model (engine model sim/gen_scenes.py + VDP model
    sim/hra/check_lrmm.py, both bit-exact against the RTL) paints VRAM and the
    shown pages become the video;
  - <scene>_engine_stim.txt / _expect.txt run on the geo3d RTL (sim/tb_engine.v);
  - <scene>_sys_sample.txt runs sampled frames end to end on geo3d_bus + HRA!'s
    unmodified vdp_command.v (sim/tb_system.v), to be compared with
    <scene>_sample_expect.hex (check_sample.py).

Scenes:
  panzoom  textured GEO3D, the camera zooms (focal length) and pans (translation)
  crawl    perspective text crawl: one tilted plane cut into strips, textured by
           LRMM, while TEXY scrolls through the text kept in VRAM; one row
           per frame, paced at 0.7 of 30 fps (PACE); crawl_en and crawl_es
           are the same scene in English and Spanish, crawl is Portuguese
  flyin    the letters arrive one by one over an SCREEN 5 background, then the
           logo turns; the Z80 only rewrites the moving letter's vertices

Usage: showcase.py <scene> [loops] [sample_frames]
"""
import math
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for sub in ("sim", "sim/hra", "z80"):
    sys.path.insert(0, os.path.join(ROOT, sub))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from check_lrmm import Vram, model_line, model_lrmm  # noqa: E402
from gen_face_tables import LIGHT as LOGO_LIGHT, model as logo_model, palette as logo_palette  # noqa: E402
from gen_scenes import replay, s16  # noqa: E402
from gen_tex_tables import texture_copies, uv_mapping  # noqa: E402

W, H = 256, 212
PAGE = H * 128


def q14(x):
    return max(-32768, min(32767, round(x * 16384)))


def rot(ax, ay, az=0.0):
    """R = Rz * Rx * Ry (y up, z forward), as Q2.14 words."""
    cx, sx = math.cos(ax), math.sin(ax)
    cy, sy = math.cos(ay), math.sin(ay)
    cz, sz = math.cos(az), math.sin(az)
    ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    r = mul(rz, mul(rx, ry))
    return [q14(r[i][j]) for i in range(3) for j in range(3)]


def smooth(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


def lerp(a, b, u):
    return a + (b - a) * u


# ------------------------------------------------------------------ stimulus
class Show:
    """Operations in program order. Each entry: (kind, frame, text)."""

    def __init__(self, name):
        self.name = name
        self.ops = []
        self.frame = None
        self.nframes = 0
        self.blocks = []

    def _op(self, kind, text):
        self.ops.append((kind, self.frame, text))

    def w(self, sel, b):
        self._op("W", f"W {sel} {b & 0xFF:02x}")

    def idx(self, i, data):
        self.w(0, i)
        for b in data:
            self.w(1, b)

    def words(self, i, ws):
        self.idx(i, [x for w in ws for x in (s16(w) & 0xFF, s16(w) >> 8)])

    def vertices(self, start, verts):
        self.idx(0x40, [start])
        self.w(0, 0x50)
        for v in verts:
            for c in v:
                self.w(1, s16(c) & 0xFF)
                self.w(1, s16(c) >> 8)

    def vdp(self, reg, val):
        self._op("V", f"V {reg} {val & 0xFF:02x}")

    def vram(self, addr, data):
        """A whole VRAM block (zeros included: on hardware VRAM is not blank)."""
        self.blocks.append((addr, bytes(data)))
        self._op("XB", f"XB {len(self.blocks) - 1}")

    def hmmv(self, dx, dy, nx, ny, clr):
        for r, v in zip(range(36, 46), (dx, dx >> 8, dy, dy >> 8, nx, nx >> 8, ny, ny >> 8, clr, 0)):
            self.vdp(r, v)
        self.vdp(46, 0xC0)
        self._op("C", "C")

    def hmmm(self, sx, sy, dx, dy, nx, ny):
        vals = (sx, sx >> 8, sy, sy >> 8, dx, dx >> 8, dy, dy >> 8, nx, nx >> 8, ny, ny >> 8, 0, 0)
        for r, v in zip(range(32, 46), vals):
            self.vdp(r, v)
        self.vdp(46, 0xD0)
        self._op("C", "C")

    def window(self, wsx, wsy, wex, wey):
        for r, v in zip(range(51, 59), (wsx, wsx >> 8, wsy, wsy >> 8, wex, wex >> 8, wey, wey >> 8)):
            self.vdp(r, v)

    def begin(self, k):
        self.frame = k

    def pace(self, blanks):
        """Vertical blanks per page flip from here on (the player's PACE; 2 =
        30 frames per second at 60 Hz, the default of every demo)."""
        self._op("P", f"P {blanks}")

    def music(self):
        """Start the ROM's music here (the player's MUSIC; no VDP/geo3d traffic)."""
        self._op("M", "M")

    def run_and_show(self, ctrl, ypage):
        self.w(0, 0x48)
        self._op("RUN", f"W 1 {ctrl:02x}")
        self._op("R", "R")
        self._op("F", f"F {self.frame}")
        self._op("C", "C")                        # last command finished
        self._op("D", f"D {ypage}")               # page flip: this page is shown
        self.nframes += 1
        self.frame = None


# ------------------------------------------------------------- system model
def simulate(show):
    """Returns (pages, stats): the shown pages (PAGE bytes each) and, per frame,
    (lrmm, line, geo3d bytes, VDP register writes)."""
    eng = []
    for kind, fr, text in show.ops:
        if kind in ("W", "RUN", "R", "F"):
            eng.append(text)
    log = replay(eng, {"draw": 0, "skip": 0, "cull": 0})
    groups, cur = {}, []
    for line in log:
        p = line.split()
        if p[0] in ("L", "M"):
            cur.append((p[0], [int(x, 16) for x in p[1:]]))
        elif p[0] == "F":
            groups[int(p[1])] = cur
            cur = []
    vr = Vram(b"")
    regs = [0] * 64
    win = None
    pages, stats = [], []
    nw = {}
    nv = {}
    for kind, fr, text in show.ops:
        if fr is not None:
            if kind in ("W", "RUN"):
                nw[fr] = nw.get(fr, 0) + 1
            elif kind == "V":
                nv[fr] = nv.get(fr, 0) + 1
    frame = None
    for kind, fr, text in show.ops:
        p = text.split()
        if kind == "XB":
            addr, data = show.blocks[int(p[1])]
            vr.b[addr:addr + len(data)] = data
        elif kind == "V":
            r, v = int(p[1]), int(p[2], 16)
            regs[r] = v
            if 51 <= r <= 58:
                win = (regs[51] | regs[52] << 8, regs[55] | regs[56] << 8,
                       regs[53] | regs[54] << 8, regs[57] | regs[58] << 8)
            if r == 46:
                z80_command(vr, regs)
        elif kind == "F":
            frame = int(p[1])
        elif kind == "D":
            y0 = int(p[1])
            pages.append(bytes(vr.b[y0 * 128:(y0 + H) * 128]))
            cmds = groups.get(frame, [])
            stats.append((sum(1 for c in cmds if c[0] == "M"), sum(1 for c in cmds if c[0] == "L"),
                          nw.get(frame, 0), nv.get(frame, 0)))
        elif kind == "R":
            for kd, b in groups.get(fr, []):
                apply_cmd(vr, kd, b, win)
    return pages, stats, groups


def w16(b, i):
    return b[i] | (b[i + 1] << 8)


def apply_cmd(vr, kind, b, win):
    if kind == "M":
        c = dict(sx=w16(b, 0), sy=w16(b, 2), dx=w16(b, 4) & 0x1FF, dy=w16(b, 6) & 0x7FF,
                 nx=w16(b, 8) & 0x3FF, clr=b[12], dix=(b[13] >> 2) & 1,
                 vx=w16(b, 14), vy=w16(b, 16), lop=b[18] & 15)
        if win:
            c["win"] = win
        model_lrmm(vr, c)
    else:
        arg = b[9]
        model_line(vr, dict(dx=w16(b, 0) & 0x1FF, dy=w16(b, 2) & 0x7FF, nx=w16(b, 4) & 0x3FF,
                            ny=w16(b, 6) & 0x3FF, clr=b[8], maj=arg & 1, dix=(arg >> 2) & 1,
                            diy=(arg >> 3) & 1, lop=b[10] & 15))


def z80_command(vr, r):
    """The Z80's own byte commands in SCREEN 5 (DIX = DIY = 0)."""
    cmd = r[46] >> 4
    dx, dy = r[36] | (r[37] & 1) << 8, r[38] | (r[39] & 7) << 8
    nx, ny = r[40] | (r[41] & 3) << 8, r[42] | (r[43] & 3) << 8
    if cmd == 0xC:                                  # HMMV
        for y in range(ny):
            a = ((dy + y) & 0x7FF) * 128 + (dx >> 1)
            vr.b[a:a + (nx >> 1)] = bytes([r[44]]) * (nx >> 1)
    elif cmd == 0xD:                                # HMMM
        sx, sy = r[32] | (r[33] & 1) << 8, r[34] | (r[35] & 7) << 8
        for y in range(ny):
            s = ((sy + y) & 0x7FF) * 128 + (sx >> 1)
            d = ((dy + y) & 0x7FF) * 128 + (dx >> 1)
            vr.b[d:d + (nx >> 1)] = vr.b[s:s + (nx >> 1)]
    else:
        raise ValueError(f"comando do Z80 não modelado: {r[46]:02x}")


# ------------------------------------------------------------------ outputs
def write_rtl_files(show, pages, sample):
    """Engine stimulus / expected log for tb_engine.v, and a sampled system
    stimulus for tb_system.v (all geo3d writes kept, RUN and drawing only on
    the sampled frames) with the model's pages for those frames."""
    eng = [t for k, f, t in show.ops if k in ("W", "RUN", "R", "F")]
    log = replay(eng, {"draw": 0, "skip": 0, "cull": 0})
    base = os.path.join(HERE, "out", show.name)
    open(base + "_engine_stim.txt", "w").write("\n".join(eng) + "\n")
    open(base + "_engine_expect.txt", "w").write("\n".join(log) + "\n")
    keep = []
    for kind, fr, text in show.ops:
        if kind == "XB":                          # tb_system.v VRAM starts at zero
            addr, data = show.blocks[int(text.split()[1])]
            keep += [f"X {addr + i:05x} {b:02x}" for i, b in enumerate(data) if b]
        elif (fr is None or fr in sample or kind == "W") and kind not in ("P", "M"):   # no port traffic
            keep.append(text)
    open(base + "_sys_sample.txt", "w").write("\n".join(keep) + "\n")
    order = sorted(sample)
    with open(base + "_sample_expect.hex", "w") as f:
        for k in order:
            f.write("".join(f"{b:02x}\n" for b in pages[k]))
    open(base + "_sample_frames.txt", "w").write(" ".join(map(str, order)) + "\n")


def font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def page_image(page, pal):
    pix = bytearray(W * H)
    pix[0::2] = bytes(b >> 4 for b in page)
    pix[1::2] = bytes(b & 15 for b in page)
    img = Image.frombytes("P", (W, H), bytes(pix))
    flat = []
    for r, g, b in pal:
        flat += [round(r * 255 / 7), round(g * 255 / 7), round(b * 255 / 7)]
    img.putpalette(flat + [0] * (768 - len(flat)))
    return img.convert("RGB")


def render(pages, stats, pal, out, title, loops=1, fps=30, extra=None, blanks=None):
    """blanks: vertical blanks each page stays on screen (the player's PACE);
    the video then runs at 60 fps with each page repeated that many times."""
    VW, VH, S = 960, 720, 3
    if blanks:
        fps = 60
    f1, f2 = font(22), font(16)
    ox, oy = (VW - W * S) // 2, 10
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{VW}x{VH}", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-movflags", "+faststart", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    screens = [page_image(p, pal).resize((W * S, H * S), Image.NEAREST) for p in pages]
    for _ in range(loops):
        for k, scr in enumerate(screens):
            canvas = Image.new("RGB", (VW, VH), (8, 8, 10))
            canvas.paste(scr, (ox, oy))
            d = ImageDraw.Draw(canvas)
            d.rectangle([ox - 2, oy - 2, ox + W * S + 1, oy + H * S + 1], outline=(60, 60, 70))
            d.text((ox, oy + H * S + 10), title, font=f1, fill=(220, 220, 225))
            m, l, nw, nv = stats[k]
            info = (f"quadro {k:4d}/{len(pages)}   |   VDP: {m} LRMM + {l} LINE   |   "
                    f"Z80: {nw} bytes ao geo3d + {nv} registros do VDP")
            if extra:
                info += "   |   " + extra(k)
            d.text((ox, oy + H * S + 40), info, font=f2, fill=(150, 150, 160))
            for _ in range(blanks[k] if blanks else 1):
                proc.stdin.write(canvas.tobytes())
    proc.stdin.close()
    proc.wait()


# ------------------------------------------------------------------- scenes
def logo_setup(sh, f, lop, texy=512, tstride=32):
    verts, faces = logo_model()
    faces, uvs = uv_mapping(verts, faces)
    ln = math.sqrt(sum(c * c for c in LOGO_LIGHT))
    light = [round(c / ln * 16384) for c in LOGO_LIGHT]
    sh.vram(texy * 128, texture_copies())
    sh.words(0x18, [f, W // 2, H // 2, 16, W, H])
    sh.idx(0x40, [0, 0, len(verts), 0, 15, lop, 0, 0])
    sh.idx(0x58, [0, len(faces)] + [x for c in light for x in (s16(c) & 0xFF, s16(c) >> 8)])
    sh.idx(0x60, [0, 0, texy & 0xFF, texy >> 8, tstride, 0])
    sh.vertices(0, verts)
    sh.w(0, 0x52)
    for ids, n, base in faces:
        for b in ids + [x for c in n for x in (s16(c) & 0xFF, s16(c) >> 8)] + [base]:
            sh.w(1, b)
    sh.w(0, 0x53)
    for uv in uvs:
        for b in uv:
            sh.w(1, b)
    return verts, faces


def scene_panzoom():
    """One 360-frame cycle: zoom into G, pan along the word to D, zoom out, a
    full turn. Loops seamlessly (frame 360 = frame 0)."""
    sh = Show("panzoom")
    logo_setup(sh, 220, 0)
    TZ, FW, FZ, XG = 820, 220, 1000, 273
    N = 360
    cams = []
    for k in range(N):
        if k < 50:
            u = smooth(k / 50)
            f, tx, yaw, ax = lerp(FW, FZ, u), lerp(0, XG, u), lerp(0, -0.32, u), lerp(0.20, 0.08, u)
        elif k < 160:
            u = smooth((k - 50) / 110)
            f, tx, yaw, ax = FZ, lerp(XG, -XG, u), lerp(-0.32, 0.32, u), 0.08
        elif k < 205:
            u = smooth((k - 160) / 45)
            f, tx, yaw, ax = lerp(FZ, FW, u), lerp(-XG, 0, u), lerp(0.32, 0, u), lerp(0.08, 0.20, u)
        else:
            u = (k - 205) / (N - 205)
            f = FW + 70 * math.sin(math.pi * u) ** 2
            tx, yaw, ax = 0, 2 * math.pi * smooth(u), 0.20 + 0.28 * math.sin(2 * math.pi * u)
        cams.append((round(f), round(tx), yaw, ax))
    for k, (f, tx, yaw, ax) in enumerate(cams):
        ypage = 256 if k % 2 == 0 else 0
        sh.begin(k)
        sh.hmmv(0, ypage, 256, H, 0x00)
        sh.words(0x00, rot(ax, yaw) + [tx, 0, TZ])
        sh.words(0x18, [f])
        sh.idx(0x46, [ypage & 0xFF, ypage >> 8])
        sh.run_and_show(0x07, ypage)
    _, pal = None, logo_palette()[0]
    return sh, pal, ("geo3d + V9968: pan e zoom, mudando só a matriz, a translação e a distância "
                     "focal a cada quadro"), (lambda k: f"F = {cams[k][0]}, TX = {cams[k][1]}")


# ---- crawl
CRAWL_TITLE = ["GEO3D"]
# language -> (subtitle, paragraphs); the only text in the demos
CRAWL = {
    "pt": ("Um coprocessador 3D para o MSX", [
        "Tempos de renascimento. Cartuchos com FPGA dão ao MSX2 um novo VDP, o V9968, "
        "criado por HRA!, com comandos rápidos e 256 KB de VRAM.",
        "Mas o Z80 segue sozinho diante da matemática. Matrizes, perspectiva e divisões "
        "consomem cada ciclo, quadro após quadro.",
        "No mesmo FPGA nasce o geo3d. Ele gira e projeta os vértices, ordena as faces, "
        "calcula a luz e entrega ao VDP os comandos LINE e LRMM, linha a linha.",
        "O Z80 envia poucos bytes por quadro. O VDP busca as texturas na própria VRAM "
        "e pinta cada pixel.",
        "Este letreiro, aliás, também é uma textura: um plano inclinado, cortado em "
        "faixas, que o geo3d desenha enquanto a VRAM rola...",
    ]),
    "en": ("A 3D coprocessor for the MSX", [
        "A time of rebirth. FPGA cartridges give the MSX2 a new VDP, the V9968, "
        "created by HRA!, with fast commands and 256 KB of VRAM.",
        "But the Z80 still faces the mathematics alone. Matrices, perspective and "
        "divisions consume every cycle, frame after frame.",
        "In the same FPGA, geo3d is born. It rotates and projects vertices, sorts faces, "
        "computes lighting and hands the VDP its LINE and LRMM commands, line by line.",
        "The Z80 sends only a few bytes a frame. The VDP fetches textures from its own "
        "VRAM and paints each pixel.",
        "This crawl, incidentally, is also a texture: a tilted plane, cut into strips, "
        "that geo3d draws while the VRAM scrolls...",
    ]),
    "es": ("Un coprocesador 3D para el MSX", [
        "Tiempos de renacimiento. Cartuchos con FPGA dan al MSX2 un nuevo VDP, el V9968, "
        "creado por HRA!, con comandos rápidos y 256 KB de VRAM.",
        "Pero el Z80 sigue solo ante las matemáticas. Matrices, perspectiva y divisiones "
        "consumen cada ciclo, cuadro tras cuadro.",
        "En la misma FPGA nace geo3d. Gira y proyecta los vértices, ordena las caras, "
        "calcula la luz y entrega al VDP los comandos LINE y LRMM, línea a línea.",
        "El Z80 envía pocos bytes por cuadro. El VDP lee las texturas de su propia VRAM "
        "y pinta cada píxel.",
        "Este texto, por cierto, también es una textura: un plano inclinado, cortado en "
        "franjas, que geo3d dibuja mientras la VRAM se desplaza...",
    ]),
}
CRAWL_SPEED = (7, 10)  # of the full-speed crawl (1 texture row per 2 vertical blanks): 30% slower
TXT0 = 768             # first text row in VRAM (page 3)
COLW = 240             # text column, texels
INK = 4


def crawl_texture(lang="pt"):
    """Renders the crawl into a 1-bit column, returns rows of VRAM bytes."""
    sub, paragraphs = CRAWL[lang]
    ft = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", 13)
    fbig = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
    fmid = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", 14)
    LH = 16
    lines = []                                    # (kind, content)
    for t in CRAWL_TITLE:
        lines.append(("big", t))
    lines.append(("gap", 8))
    lines.append(("mid", sub))
    lines.append(("gap", 22))
    probe = ImageDraw.Draw(Image.new("1", (1, 1)))
    wsp = probe.textlength(" ", font=ft)
    for par in paragraphs:
        words = par.split()
        cur = []
        for wd in words:
            test = cur + [wd]
            if probe.textlength(" ".join(test), font=ft) <= COLW - 4 or not cur:
                cur = test
            else:
                lines.append(("just", cur))
                cur = [wd]
        lines.append(("left", cur))
        lines.append(("gap", LH))
    hgt = 0
    for kind, c in lines:
        hgt += c if kind == "gap" else {"big": 44, "mid": 22}.get(kind, LH)
    img = Image.new("1", (COLW, hgt), 0)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    y = 0
    for kind, c in lines:
        if kind == "gap":
            y += c
            continue
        if kind in ("big", "mid"):
            fnt = fbig if kind == "big" else fmid
            tw = d.textlength(c, font=fnt)
            ink = d.textbbox((0, 0), c, font=fnt)
            assert ink[2] - ink[0] <= COLW, f"crawl title/subtitle wider than the column: {c!r}"
            d.text(((COLW - tw) / 2, y), c, font=fnt, fill=1)
            y += 44 if kind == "big" else 22
            continue
        widths = [d.textlength(wd, font=ft) for wd in c]
        x = 2.0
        gap = wsp
        if kind == "just" and len(c) > 1:
            gap = (COLW - 4 - sum(widths)) / (len(c) - 1)
            if gap > 2.6 * wsp:                     # too loose: keep it ragged
                gap = wsp
        for wd, wdt in zip(c, widths):
            d.text((round(x), y), wd, font=ft, fill=1)
            x += wdt + gap
        y += LH
    rows = []
    px = img.load()
    for yy in range(hgt):
        row = bytearray(128)
        for xx in range(COLW):
            if px[xx, yy]:
                row[xx >> 1] |= (INK << 4) if not (xx & 1) else INK
        rows.append(bytes(row))
    return rows


def starfield(seed, n):
    rng = random.Random(seed)
    page = bytearray(PAGE)
    for _ in range(n):
        x, y = rng.randrange(W), rng.randrange(H)
        c = rng.choice([1, 1, 1, 2, 2, 3])
        a = y * 128 + (x >> 1)
        page[a] = (page[a] & 0x0F) | (c << 4) if not (x & 1) else (page[a] & 0xF0) | c
    return bytes(page)


def crawl_paces(nframes):
    """Vertical blanks before each page flip, so the crawl (one texture row per
    frame) runs at CRAWL_SPEED of the 1 row per 2 blanks pace: frame k is
    shown at blank round(k * 2 * den / num). For 0.7: 3,3,3,2,3,3,3 ... (20
    blanks per 7 rows). Slowing down in time keeps every frame exactly one
    frame of the full-speed crawl; moving the plane by fractions of a row
    instead makes the text step backwards, because the engine projects whole
    pixels."""
    num, den = CRAWL_SPEED
    at = [(4 * den * k + num) // (2 * num) for k in range(nframes + 1)]
    return [at[k + 1] - at[k] for k in range(nframes)]


def scene_crawl(lang="pt"):
    """The crawl in one language: one texture row per frame (TEXY + 1), paced
    by crawl_paces()."""
    sh = Show("crawl" if lang == "pt" else f"crawl_{lang}")
    rows = crawl_texture(lang)
    T = len(rows)
    assert TXT0 + T <= 2048, "crawl text past the end of 256 KB VRAM"
    STARS = 512
    sh.vram(STARS * 128, starfield(1985, 150))
    sh.vram(TXT0 * 128, b"".join(rows))
    sh.window(0, TXT0, COLW - 1, TXT0 + T - 1)       # outside the text: CLR = 0, transparent
    # plane: row r from the near edge, r = R - q, q = 0 at the far edge
    TS, LEVELS, STRIP = 240, 5, 16
    R = TS * LEVELS
    SX, Y0, Z0, DY, DZ = 6, -640, 1474, 3, 6
    verts = []
    nb = R // STRIP + 1
    for b in range(nb):
        r = R - b * STRIP
        y, z = Y0 + DY * r, Z0 + DZ * r
        verts += [(-SX * COLW // 2, y, z), (SX * COLW // 2, y, z)]
    faces, uvs = [], []
    for j in range(nb - 1):
        # light level 1..5 = texture bank: the face reads TEXY + level * TS + v.
        # BASE = -level keeps bit7 (textured) and makes the LRMM colour byte
        # BASE + level = 0x00, so texels outside the window are transparent
        # (vdp_command.v tests TIMP on the whole CLR byte).
        lv = (j * STRIP) // TS + 1
        v0 = j * STRIP - (lv - 1) * TS
        tl, tr, bl, br = 2 * j, 2 * j + 1, 2 * (j + 1), 2 * (j + 1) + 1
        n = (lv * 16384 + 6) // 7 + 40
        faces.append(((tl, tr, br, bl), (0, 0, -n), (256 - lv) & 0xFF))
        uvs.append([0, v0, COLW, v0, COLW, v0 + STRIP, 0, v0 + STRIP])
    sh.words(0x18, [256, W // 2, H // 2, 16, W, H])
    sh.idx(0x40, [0, 0, len(verts), 0, 15, 8, 0, 0])          # LOP = TIMP
    sh.idx(0x58, [0, len(faces), 0, 0, 0, 0, s16(-16384) & 0xFF, s16(-16384) >> 8])
    sh.idx(0x60, [0, 0, 0, 0, TS, 0])
    sh.vertices(0, verts)
    sh.w(0, 0x52)
    for ids, n, base in faces:
        for bb in list(ids) + [x for c in n for x in (s16(c) & 0xFF, s16(c) >> 8)] + [base]:
            sh.w(1, bb)
    sh.w(0, 0x53)
    for uv in uvs:
        for bb in uv:
            sh.w(1, bb)
    sh.words(0x00, rot(0, 0) + [0, 0, 0])
    LEAD, TAIL = 45, 640
    ts = list(range(-LEAD, T + TAIL))
    paces = crawl_paces(len(ts))
    for k, t in enumerate(ts):
        ypage = 256 if k % 2 == 0 else 0
        sh.begin(k)
        if k == 0:
            sh.music()                               # the music starts with the crawl
        if k == 0 or paces[k] != paces[k - 1]:
            sh.pace(paces[k])
        sh.hmmm(0, STARS, 0, ypage, 256, H)
        texy = (TXT0 + t - R - TS) & 0x1FFF
        sh.idx(0x62, [texy & 0xFF, texy >> 8])
        sh.idx(0x46, [ypage & 0xFF, ypage >> 8])
        sh.run_and_show(0x07, ypage)
    pal = [(0, 0, 0), (2, 2, 3), (4, 4, 5), (7, 7, 7), (7, 6, 1)] + [(0, 0, 0)] * 11
    return sh, pal, ("geo3d + V9968: letreiro em perspectiva, um plano texturizado (LRMM) "
                     "enquanto a VRAM rola"), (lambda k: f"TEXY = {(TXT0 + ts[k] - R - TS) % 8192}")


# ---- fly-in
def msx2_background():
    """An original 16-colour SCREEN 5 scene with the logo palette: dithered
    night sky, stars, a banded sun, two mountain ranges and a grid floor."""
    rng = random.Random(2026)
    bayer = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]
    HZ = 150
    pix = [[0] * W for _ in range(H)]
    ramp = [0, 8, 9, 10]
    for y in range(HZ):
        t = (y / HZ) ** 1.6 * 2.6
        lo = int(t)
        frac = t - lo
        for x in range(W):
            c = ramp[min(3, lo + (1 if frac * 16 > bayer[y & 3][x & 3] else 0))]
            pix[y][x] = c
    for _ in range(90):
        x, y = rng.randrange(W), rng.randrange(HZ - 40)
        pix[y][x] = rng.choice([15, 15, 3, 10, 2])
    for _ in range(6):
        x, y = rng.randrange(8, W - 8), rng.randrange(8, HZ - 60)
        for d in (-1, 0, 1):
            pix[y + d][x] = 15 if d == 0 else 10
            pix[y][x + d] = 15 if d == 0 else 10
    cx, cy, rad = 128, HZ - 4, 74
    for y in range(cy - rad, cy + 1):
        dy = cy - y
        band = (cy - y) < 44 and ((cy - y) % 8) < (1 + (44 - (cy - y)) // 10)
        if band:
            continue
        half = int(math.sqrt(max(0, rad * rad - dy * dy)))
        tt = dy / rad
        for x in range(cx - half, cx + half + 1):
            lvl = 3 + (1 if tt * 16 > bayer[y & 3][x & 3] else 0) + (1 if tt > 0.6 else 0)
            pix[y][x] = min(5, lvl)

    def ridge(base, amp, seed, rough):
        r2 = random.Random(seed)
        pts = [base + r2.uniform(-amp, amp) for _ in range(9)]
        out = []
        for x in range(W):
            p = x / (W - 1) * 8
            i = min(7, int(p))
            u = smooth(p - i)
            out.append(lerp(pts[i], pts[i + 1], u) + r2.uniform(-rough, rough))
        return out
    far = ridge(HZ - 22, 9, 7, 1.0)
    near = ridge(HZ - 9, 5, 11, 1.4)
    for x in range(W):
        top = int(far[x])
        for y in range(max(0, top), HZ):
            pix[y][x] = 9 if y > top + 1 else 10
        top = int(near[x])
        for y in range(max(0, top), HZ):
            pix[y][x] = 8 if y > top else 9
    for y in range(HZ, H):
        for x in range(W):
            pix[y][x] = 0
    k = 1
    while True:
        y = HZ + int(1.6 * k * k)
        if y >= H:
            break
        c = 9 if y < HZ + 12 else 10 if y < HZ + 35 else 11
        for x in range(W):
            pix[y][x] = c
        k += 1
    for i in range(-16, 17):
        xb = 128 + i * 26
        for y in range(HZ + 5, H):
            u = (y - HZ) / (H - HZ)
            x = round(128 + (xb - 128) * u)
            if 0 <= x < W and pix[y][x] == 0:
                pix[y][x] = 9 if y < HZ + 20 else 10
    for x in range(W):
        pix[HZ][x] = 11
    out = bytearray(PAGE)
    for y in range(H):
        for x in range(0, W, 2):
            out[y * 128 + x // 2] = (pix[y][x] << 4) | pix[y][x + 1]
    return bytes(out)


def ease_out_back(u):
    c1 = 1.55
    c3 = c1 + 1
    u = max(0.0, min(1.0, u))
    return 1 + c3 * (u - 1) ** 3 + c1 * (u - 1) ** 2


def ease_in_back(u):
    c1 = 1.55
    c3 = c1 + 1
    u = max(0.0, min(1.0, u))
    return c3 * u ** 3 - c1 * u ** 2


def scene_flyin():
    sh = Show("flyin")
    BG = 768
    sh.vram(BG * 128, msx2_background())
    verts, faces = logo_setup(sh, 280, 0)
    # letters: G 5 blocks, E/O/3/D 4 blocks, 8 vertices per block
    counts = [5, 4, 4, 4, 4]
    ranges, s = [], 0
    for c in counts:
        ranges.append((s, s + 8 * c))
        s += 8 * c
    origins = [(-900, 60, 0), (0, 760, 0), (0, 0, 14000), (0, -760, 0), (900, -60, 0)]
    HIDDEN = (30000, 0, 0)
    N, TZ = 360, 820
    YAW0, AX0 = -0.30, 0.18

    def offset(li, k):
        s_in, d_in = 14 + 16 * li, 30
        s_out, d_out = 276 + 10 * li, 24
        o = origins[li]
        if k < s_in or k >= s_out + d_out:
            return HIDDEN
        if k < s_in + d_in:
            e = ease_out_back((k - s_in + 1) / d_in)
            return tuple(round(c * (1 - e)) for c in o)
        if k >= s_out:
            e = ease_in_back((k - s_out + 1) / d_out)
            return tuple(round(c * e) for c in o)
        return (0, 0, 0)

    # the setup uploaded every letter at rest; start with all of them hidden
    last = [None] * 5
    for k in range(N):
        ypage = 256 if k % 2 == 0 else 0
        sh.begin(k)
        sh.hmmm(0, BG, 0, ypage, 256, H)
        for li, (a, b) in enumerate(ranges):
            off = offset(li, k)
            if off != last[li]:
                sh.vertices(a, [tuple(v[i] + off[i] for i in range(3)) for v in verts[a:b]])
                last[li] = off
        if 132 <= k < 260:
            u = (k - 132) / 128
            yaw, ax = YAW0 + 2 * math.pi * smooth(u), AX0 + 0.22 * math.sin(2 * math.pi * u)
        else:
            yaw, ax = YAW0, AX0
        sh.words(0x00, rot(ax, yaw) + [0, 8, TZ])
        sh.idx(0x46, [ypage & 0xFF, ypage >> 8])
        sh.run_and_show(0x07, ypage)
    pal = logo_palette()[0]
    return sh, pal, ("geo3d + V9968: as letras chegam uma a uma sobre um cenário SCREEN 5, "
                     "depois o logo gira"), None


SCENES = {"panzoom": scene_panzoom, "crawl": scene_crawl, "flyin": scene_flyin,
          "crawl_en": lambda: scene_crawl("en"), "crawl_es": lambda: scene_crawl("es")}


def main():
    name = sys.argv[1]
    loops = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    nsample = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    sh, pal, title, extra = SCENES[name]()
    pages, stats, _ = simulate(sh)
    n = len(pages)
    sample = sorted({round(i * (n - 1) / max(1, nsample - 1)) for i in range(nsample)})
    write_rtl_files(sh, pages, sample)
    out = os.path.join(HERE, "out", f"geo3d_{name}.mp4")
    blanks, pace = [], 2
    for kind, fr, text in sh.ops:
        if kind == "P":
            pace = int(text.split()[1])
        elif kind == "D":
            blanks.append(pace)
    render(pages, stats, pal, out, title, loops, 30, extra, blanks if set(blanks) != {2} else None)
    tot_m = sum(s[0] for s in stats)
    tot_l = sum(s[1] for s in stats)
    print(f"{name}: {n} quadros x {loops} voltas -> {out}; {tot_m} LRMM, {tot_l} LINE; "
          f"amostra RTL: quadros {sample}")


if __name__ == "__main__":
    main()
