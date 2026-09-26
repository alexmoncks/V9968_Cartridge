#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Converts Alex's two tiles to SCREEN 5 VRAM bytes and packs them.

  tile_background_512x212.png  -> two 256 x 212 halves (columns 0-255 and
                                  256-511), 27,136 bytes each, for VRAM
                                  pages 2 and 3 (common.VRAM_LAYOUT)
  tile_foreground_256x48.png   -> 256 x 48, 6,144 bytes, VRAM page 6; its
                                  transparent pixels are index 0, so an LMMM
                                  with TIMP lays it over the background

Every pixel must be one of the palette's tile colours (exact MSX levels):
the conversion fails otherwise, so the ROM shows the PNGs exactly. The
check decodes the packed data back and compares it with the PNGs.

SCREEN 5 byte = two pixels, the even x in the high nibble; 128 bytes per
line. Packing: the demo ROM's VRLE (common.rle). Each image is cut into
blocks of whole lines, each at most BLOCK_MAX packed bytes, so a block
never crosses a 16 KB ASCII16 bank. A block is laid out exactly as the
operands of the demo ROM's VRLE op (rom/geo3d_rom.asm vr_go), so that code
can upload it as it is:

    db A7..A0, A15..A8, A17..A16     ; VRAM address of the block's first line
    dw packed length
    db packed bytes...

Writes out/inc/tiles_bg_l.asm, tiles_bg_r.asm, tiles_fg.asm (one file per
image, each fits one bank) and out/bin/*.bin (raw and packed).
"""
import sys

import numpy as np
from PIL import Image

import common as C

BLOCK_MAX = 16384 - 256          # packed bytes per block (+5 header bytes)


def load(path, want_size, allow_alpha):
    im = Image.open(path).convert("RGBA")
    if im.size != want_size:
        sys.exit(f"{path}: size {im.size}, expected {want_size}")
    a = np.array(im)
    lv = np.rint(a[..., :3].astype(float) * 7 / 255).astype(int)
    back = np.array([[C.rgb8(v) for v in range(8)]])[0][lv]
    if np.any(np.abs(back - a[..., :3]) > 1) and np.any(a[..., 3] > 0):
        opaque = a[..., 3] > 0
        bad = np.argwhere((np.abs(back - a[..., :3]) > 1).any(axis=2) & opaque)
        if len(bad):
            y, x = bad[0]
            sys.exit(f"{path}: pixel ({x},{y}) {tuple(a[y, x, :3])} is not an MSX 3-bit level")
    lut = {rgb: i for i, rgb in enumerate(C.PALETTE)}
    idx = np.zeros(a.shape[:2], np.uint8)
    for y in range(a.shape[0]):
        for x in range(a.shape[1]):
            if a[y, x, 3] == 0:
                if not allow_alpha:
                    sys.exit(f"{path}: transparent pixel at ({x},{y})")
                idx[y, x] = C.TRANSPARENT
                continue
            key = tuple(int(v) for v in lv[y, x])
            if key not in lut or lut[key] not in C.TILE_COLOURS:
                sys.exit(f"{path}: colour {key} at ({x},{y}) is not a tile colour")
            if allow_alpha and lut[key] == C.TRANSPARENT:
                sys.exit(f"{path}: opaque pixel at ({x},{y}) uses the transparent index")
            idx[y, x] = lut[key]
    return idx, a


def screen5(idx):
    h, w = idx.shape
    assert w == 256
    return bytes(((idx[:, 0::2] << 4) | idx[:, 1::2]).astype(np.uint8).reshape(-1))


def unscreen5(data, h):
    b = np.frombuffer(data, np.uint8).reshape(h, 128)
    out = np.zeros((h, 256), np.uint8)
    out[:, 0::2] = b >> 4
    out[:, 1::2] = b & 15
    return out


def blocks(raw, y0, h):
    """Cut `raw` (h lines) into VRLE blocks of whole lines."""
    out, line = [], 0
    while line < h:
        n = h - line
        while True:
            packed = C.rle(raw[line * 128:(line + n) * 128])
            if len(packed) <= BLOCK_MAX:
                break
            n = n * 3 // 4
        out.append((y0 + line, n, packed))
        line += n
    return out


def block_bytes(y, packed):
    a = C.vram_addr(y)
    return bytes([a & 0xFF, (a >> 8) & 0xFF, a >> 16]) + C.le16(len(packed)) + packed


def emit(name, label, what, raw, blks, y0):
    lines = [f"; {what}",
             f"; raw {len(raw)} bytes (SCREEN 5, 128 bytes per line), "
             f"packed {sum(len(p) for _, _, p in blks)} bytes in {len(blks)} VRLE block(s)",
             "; block: db A7..A0, A15..A8, A17..A16 ; dw packed length ; packed bytes",
             "; (the operands of the demo ROM's VRLE op, rom/geo3d_rom.asm vr_go)",
             f"{label}_Y: equ {y0}",
             f"{label}_NBLK: equ {len(blks)}"]
    for k, (y, n, packed) in enumerate(blks):
        lines.append(f"{label}_{k}:\t\t; lines {y}-{y + n - 1}, {len(packed)} packed bytes")
        lines += C.asm_bytes(block_bytes(y, packed))
    return C.write_asm(name, "gen_tiles.py", lines)


def build(verbose=True):
    bg, bg_rgba = load(C.BG_PNG, (512, 212), False)
    fg, fg_rgba = load(C.FG_PNG, (256, 48), True)
    L = C.VRAM_LAYOUT
    out = {}
    for key, img, y0, what in (
            ("bg_l", bg[:, :256], L["BG_L_Y"], "background columns 0-255 (page 2)"),
            ("bg_r", bg[:, 256:], L["BG_R_Y"], "background columns 256-511 (page 3)"),
            ("fg", fg, L["FG_Y"], "foreground tile 256 x 48, index 0 = transparent (page 6)")):
        raw = screen5(img)
        blks = blocks(raw, y0, img.shape[0])
        # round trip: packed -> raw -> pixels
        back = b"".join(C.unrle(p) for _, _, p in blks)
        assert back == raw, key
        assert np.array_equal(unscreen5(back, img.shape[0]), img), key
        C.write_bin(f"tile_{key}.raw", raw)
        C.write_bin(f"tile_{key}.vrle", b"".join(block_bytes(y, p) for y, _, p in blks))
        emit(f"tiles_{key}.asm", f"TILE_{key.upper()}", what, raw, blks, y0)
        out[key] = dict(raw=len(raw), packed=sum(len(p) + 5 for _, _, p in blks),
                        blocks=len(blks), img=img)
    # the pixels decoded from VRAM bytes, through the palette, equal the PNGs
    pal = np.array(C.palette_rgb(), np.uint8)
    full = np.concatenate([out["bg_l"]["img"], out["bg_r"]["img"]], axis=1)
    assert np.array_equal(pal[full], bg_rgba[..., :3]), "background colours differ"
    op = fg_rgba[..., 3] > 0
    assert np.array_equal(pal[out["fg"]["img"]][op], fg_rgba[..., :3][op]), "foreground colours differ"
    assert not np.any(out["fg"]["img"][~op]), "foreground transparency"
    counts = np.bincount(full.reshape(-1), minlength=16)
    if verbose:
        for k in ("bg_l", "bg_r", "fg"):
            o = out[k]
            print(f"  tile {k:5s}: raw {o['raw']:6d} bytes, VRLE {o['packed']:6d} bytes "
                  f"({o['blocks']} block)")
        print("  background colour use:", {i: int(c) for i, c in enumerate(counts) if c})
    C.write_bin("palette.bin", C.palette_bytes())
    return out


if __name__ == "__main__":
    build()
