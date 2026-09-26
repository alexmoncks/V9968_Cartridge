#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Shared definitions of the side-scrolling shooter's asset pipeline: paths,
the SCREEN 5 palette, the VRAM layout, the geo3d camera, the RLE packer and
the z80asm emit helpers. Every generator in this folder imports this module,
so a change here (a palette entry, a VRAM address) reaches all the data.

Units and axes (all models and screen mappings):
  - Screen: SCREEN 5, 256 x 212, x right, y down. The foreground band (the
    256 x 48 tile) covers lines 164-211; the play area is above it.
  - geo3d camera: F = 256, CX = 128, CY = 106, ZNEAR = 16, W = 256,
    H = 164 (faces are clipped at the top of the foreground band), identity
    camera (the Z80 writes each object's own matrix and translation).
  - Objects fly at depth Z0 = 1024, where 4 model units are 1 pixel:
    TX = (sx - 128) * 4, TY = (106 - sy) * 4, TZ = 1024.
  - Model axes: +X forward on screen (right), +Y up, +Z into the screen
    (away from the viewer). The models are written in pixels and scaled by 4.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.dirname(HERE)
GEO3D = os.path.dirname(GAME)
IMG = os.path.join(GEO3D, "demos", "img")
BG_PNG = os.path.join(IMG, "tile_background_512x212.png")
FG_PNG = os.path.join(IMG, "tile_foreground_256x48.png")
OUT = os.path.join(GAME, "out")
INC = os.path.join(OUT, "inc")          # generated z80asm include files
BIN = os.path.join(OUT, "bin")          # the same data as raw binaries
PREVIEW = os.environ.get("G3GAME_PREVIEW",
                         "/mnt/c/Projects/mmsoft/openmsx-geo3d/game_preview")

# ------------------------------------------------------------------ palette
# MSX RGB levels 0-7. Entries 0-6 and 7, 12 are the tile colours, exactly as
# Alex drew them; 0-6 double as the blue shading ramp (BASE 0) and 8-14 are
# the warm ramp (BASE 8) with the tile orange as its level 4.
PALETTE = [
    (0, 0, 0),   # 0  black: space; blue ramp L0; transparent (TIMP, sprites)
    (0, 0, 2),   # 1  navy (tile)          blue ramp L1
    (1, 1, 3),   # 2  (tile)               blue ramp L2
    (2, 2, 4),   # 3  (tile)               blue ramp L3
    (3, 3, 5),   # 4  (tile)               blue ramp L4
    (5, 5, 6),   # 5  (tile)               blue ramp L5
    (7, 7, 7),   # 6  white (tile)         blue ramp L6
    (1, 5, 5),   # 7  cyan (tile): canopy, player bolts, HUD
    (2, 0, 0),   # 8  warm ramp L0
    (4, 1, 0),   # 9  warm ramp L1
    (5, 2, 0),   # 10 warm ramp L2
    (6, 3, 0),   # 11 warm ramp L3
    (7, 4, 1),   # 12 orange (tile)        warm ramp L4
    (7, 6, 2),   # 13 warm ramp L5: engine glow
    (7, 7, 5),   # 14 warm ramp L6
    (7, 2, 6),   # 15 magenta (flat): enemy bullets, enemy eyes
]
TILE_COLOURS = {0, 1, 2, 3, 4, 5, 6, 7, 12}
BLACK, NAVY, WHITE, CYAN, ORANGE, AMBER, MAGENTA = 0, 1, 6, 7, 12, 13, 15
RAMP_BLUE, RAMP_WARM = 0, 8             # face BASE of the two 7-tone ramps
RAMPS = (RAMP_BLUE, RAMP_WARM)
TRANSPARENT = 0                         # fg source: LMMM with TIMP skips it


def palette_bytes(pal=PALETTE):
    """V9938 palette port format: 0RRR0BBB, 00000GGG per entry."""
    out = []
    for r, g, b in pal:
        out += [(r << 4) | b, g]
    return bytes(out)


def rgb8(level):
    return round(level * 255 / 7)


def palette_rgb(pal=PALETTE):
    return [tuple(rgb8(c) for c in p) for p in pal]


# ------------------------------------------------------------------ screen
SCR_W, SCR_H = 256, 212
BAND_Y = 164                            # first line of the foreground band
BAND_H = 48
HUD_H = 16                              # sprite HUD over the top dark band
PLAY_Y0, PLAY_Y1 = 24, 156              # object centres stay in this range
PLAY_X0, PLAY_X1 = 16, 240

# ------------------------------------------------------------------ geo3d
F, CX, CY, ZNEAR, G3W, G3H = 256, 128, 106, 16, 256, BAND_Y
Z0 = 1024                               # object depth: 4 units = 1 pixel
UNIT = 4


def screen_to_t(sx, sy, z=Z0):
    """geo3d translation of an object centred at screen (sx, sy)."""
    return [(sx - CX) * z // F, (CY - sy) * z // F, z]


def geo_cfg(m, t, w=G3W, h=G3H):
    """The 18 geo3d configuration words (gen_scenes.render_faces cfg)."""
    return list(m) + list(t) + [F, CX, CY, ZNEAR, w, h]


# ------------------------------------------------------------------ VRAM
# SCREEN 5 with the V9968 256 KB (R#21 = 0): 2048 lines of 128 bytes.
# HMMM moves whole bytes (2 pixels), so the game keeps a copy of the
# background shifted left by one pixel (BG1[x] = BG[x + 1]); the VDP builds
# it with LMMM after the upload. The foreground is laid with LMMM + TIMP
# (any x), so it needs no shifted copy.
VRAM_LAYOUT = {
    "DISP0_Y": 0,        # page 0, lines 0-211: display buffer 0
    "DISP1_Y": 256,      # page 1, lines 256-467: display buffer 1
    "BG_L_Y": 512,       # page 2: background columns 0-255 (lines 512-723)
    "BG_R_Y": 768,       # page 3: background columns 256-511 (lines 768-979)
    "BG1_L_Y": 1024,     # page 4: background shifted by 1 px, columns 0-255
    "BG1_R_Y": 1280,     # page 5: background shifted by 1 px, columns 256-511
    "FG_Y": 1536,        # page 6: foreground tile (lines 1536-1583)
    "TEXT_Y": 1792,      # page 7: text images (messages, drawn at init)
    "SPR_COL_B": 0x7000,  # sprite colour table B (SAT_B - 512)
    "SPR_SAT_B": 0x7200,  # sprite attribute table B
    "SPR_COL_A": 0x7400,  # sprite colour table A (SAT_A - 512)
    "SPR_SAT_A": 0x7600,  # sprite attribute table A
    "SPR_PAT": 0x7800,   # sprite pattern generator (2 KB, 64 patterns 16x16)
}


def vram_addr(y, x=0):
    return y * 128 + (x >> 1)


# ------------------------------------------------------------------ packing
def rle(data):
    """The demo ROM's VRLE packing (rom/build_rom.py rle, decoded by
    op_vrle / vr_loop in rom/geo3d_rom.asm): control byte c < 80h: c + 1
    literal bytes follow; c >= 80h: one byte follows, repeated c - 7Eh
    times (the packer emits runs of 3..129)."""
    out, i, n = bytearray(), 0, len(data)
    lit = bytearray()

    def flush():
        while lit:
            chunk = lit[:128]
            out.append(len(chunk) - 1)
            out.extend(chunk)
            del lit[:128]
    while i < n:
        j = i
        while j < n and data[j] == data[i] and j - i < 129:
            j += 1
        if j - i >= 3:
            flush()
            out += bytes([0x7E + (j - i), data[i]])
            i = j
        else:
            lit.append(data[i])
            i += 1
    flush()
    return bytes(out)


def unrle(packed):
    out, i = bytearray(), 0
    while i < len(packed):
        c = packed[i]
        i += 1
        if c < 0x80:
            out += packed[i:i + c + 1]
            i += c + 1
        else:
            out += bytes([packed[i]]) * (c - 0x7E)
            i += 1
    return bytes(out)


# ------------------------------------------------------------------ asm
HEADER = ("; SPDX-License-Identifier: MIT\n"
          "; Copyright (c) 2026 Alex Moncks\n"
          "; generated by geo3d/game/tools/{tool}, do not edit\n")


def asm_bytes(data, per_line=16, indent="        "):
    lines = []
    for k in range(0, len(data), per_line):
        lines.append(indent + "db " + ",".join(f"0x{b:02x}" for b in data[k:k + per_line]))
    return lines


def asm_words(words, per_line=8, indent="        "):
    lines = []
    for k in range(0, len(words), per_line):
        lines.append(indent + "dw " + ",".join(str(w) for w in words[k:k + per_line]))
    return lines


def write_asm(name, tool, lines):
    os.makedirs(INC, exist_ok=True)
    path = os.path.join(INC, name)
    with open(path, "w", newline="\n") as f:
        f.write(HEADER.format(tool=tool))
        f.write("\n".join(lines) + "\n")
    return path


def write_bin(name, data):
    os.makedirs(BIN, exist_ok=True)
    path = os.path.join(BIN, name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def s16(x):
    return x & 0xFFFF


def le16(x):
    x &= 0xFFFF
    return bytes([x & 0xFF, x >> 8])
