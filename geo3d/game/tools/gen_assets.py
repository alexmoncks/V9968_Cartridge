#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Builds all the data of the side-scrolling shooter (geo3d/game) and,
with --preview, the preview images for Alex.

Usage (WSL, the repository's Python venv):
  ~/venv/bin/python geo3d/game/tools/gen_assets.py [--preview] [--check]

Writes geo3d/game/out/ (git-ignored):
  inc/palette.asm   palette, colour and ramp names, VRAM layout, geo3d camera
  inc/tiles_bg_l.asm, tiles_bg_r.asm, tiles_fg.asm   the tiles (VRLE blocks)
  inc/models.asm    geo3d models: vertex and face streams, the vertex pool
  inc/attitudes.asm precomputed rotation matrices (the Z80 computes none)
  inc/sprites.asm   sprite patterns, colour tables, plane map, hitboxes, font
  inc/level.asm     enemy types, paths, level script, stars, sine, directions
  inc/sfx.asm       PSG sound effects
  inc/game_data_test.asm   includes all of the above (z80asm check)
  bin/*             the tiles as raw and packed binaries, the palette
--preview also writes common.PREVIEW (palette, tiles, models, sprites,
mockup, frame_*.png, intro.gif, sfx/*.wav). --check assembles
game_data_test.asm with z80asm to make sure every include is valid.

Inputs: Alex's tiles geo3d/demos/img/tile_background_512x212.png and
tile_foreground_256x48.png (common.BG_PNG / FG_PNG); everything else is
defined in this folder (models.py, sprites.py, level.py, sfx.py).
"""
import argparse
import os
import subprocess

import common as C
import gen_tiles
import level
import models
import sfx
import sprites


def palette_asm():
    L = C.VRAM_LAYOUT
    lx, ly, lz = __import__("render").LIGHT
    lines = ["; Palette (V9938 port format: 0RRR0BBB, 00000GGG), 16 entries",
             "palette:"]
    lines += C.asm_bytes(C.palette_bytes())
    for i, rgb in enumerate(C.PALETTE):
        lines.append(f";   {i:2d} = RGB {rgb}" + ("  tile" if i in C.TILE_COLOURS else ""))
    lines += [f"COL_BLACK: equ {C.BLACK}", f"COL_NAVY: equ {C.NAVY}", f"COL_WHITE: equ {C.WHITE}",
              f"COL_CYAN: equ {C.CYAN}", f"COL_ORANGE: equ {C.ORANGE}", f"COL_AMBER: equ {C.AMBER}",
              f"COL_MAGENTA: equ {C.MAGENTA}",
              f"RAMP_BLUE: equ {C.RAMP_BLUE}\t; face BASE: 7 tones, entries 0-6",
              f"RAMP_WARM: equ {C.RAMP_WARM}\t; face BASE: 7 tones, entries 8-14",
              "; screen",
              f"BAND_Y: equ {C.BAND_Y}\t\t; foreground band: lines 164-211",
              f"BAND_H: equ {C.BAND_H}",
              f"PLAY_X0: equ {C.PLAY_X0}", f"PLAY_X1: equ {C.PLAY_X1}",
              f"PLAY_Y0: equ {C.PLAY_Y0}", f"PLAY_Y1: equ {C.PLAY_Y1}",
              "; VRAM layout (SCREEN 5, 256 KB: y = line 0-2047, 128 bytes per line)"]
    for k, v in L.items():
        lines.append(f"VR_{k}: equ {v}")
    lines += ["; geo3d camera: objects at TZ = G3_Z0, TX = (x - 128) * 4, TY = (106 - y) * 4",
              f"G3_F: equ {C.F}", f"G3_CX: equ {C.CX}", f"G3_CY: equ {C.CY}", f"G3_ZNEAR: equ {C.ZNEAR}",
              f"G3_W: equ {C.G3W}", f"G3_H: equ {C.G3H}\t\t; faces clipped at the band",
              f"G3_Z0: equ {C.Z0}",
              "; light towards (-1, 1, -1): up, left, front; Q2.14, camera = world",
              f"G3_LX: equ {lx & 0xFFFF}", f"G3_LY: equ {ly & 0xFFFF}", f"G3_LZ: equ {lz & 0xFFFF}",
              "; attitudes (65536 per turn)",
              f"ANG_BANK0: equ {models.ang(models.BANK0)}\t; player rest roll (top to the viewer)",
              f"ANG_BANKSTEP: equ {models.ang(models.BANK_STEP)}\t; + climbing, - diving",
              f"ANG_SAUCER_TILT: equ {models.ang(models.SAUCER_TILT)}"]
    return C.write_asm("palette.asm", "gen_assets.py", lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    print("tiles")
    t = gen_tiles.build()
    print("models")
    ms = models.all_models()
    _, mstats = models.export(models.game_models(ms))
    _, att_bytes = models.export_attitudes()
    assert {k: len(v) for k, v in models.att_tables().items()} == level.ATT_N
    assert models.ATT_ORDER == level.ATT_ORDER
    print("sprites")
    _, ssizes = sprites.export()
    print("level")
    _, lsizes = level.export()
    intro, rnd = level.round_length()
    print(f"  intro {intro} ticks ({intro / level.TICK_HZ:.1f} s), round up to {rnd} ticks "
          f"({rnd / level.TICK_HZ:.1f} s)")
    print("sfx")
    _, sfx_bytes = sfx.export()
    palette_asm()
    inc = ["palette.asm", "tiles_bg_l.asm", "tiles_bg_r.asm", "tiles_fg.asm", "models.asm", "attitudes.asm",
           "sprites.asm", "level.asm", "sfx.asm"]
    C.write_asm("game_data_test.asm", "gen_assets.py",
                ["; assembles every generated include (z80asm check), not a program",
                 "        org 0x4000"] + [f'        include "{n}"' for n in inc])
    tiles_total = sum(v["packed"] for v in t.values())
    model_total = sum(s[5] + 2 * s[6] for s in mstats)
    print("\nsizes (bytes): tiles packed %d (raw %d), models %d (local + pool faces), "
          "attitudes %d, sprites %d, level %d, sfx %d" % (
              tiles_total, sum(v["raw"] for v in t.values()), model_total, att_bytes,
              sum(ssizes.values()), sum(lsizes.values()), sfx_bytes))
    if a.check:
        r = subprocess.run(["z80asm", "-o", os.path.join(C.OUT, "game_data_test.bin"),
                            "game_data_test.asm"], cwd=C.INC, capture_output=True, text=True)
        size = os.path.getsize(os.path.join(C.OUT, "game_data_test.bin")) if r.returncode == 0 else 0
        print("z80asm:", "ok, %d bytes" % size if r.returncode == 0 else r.stderr)
    if a.preview:
        import preview
        preview.main(["all"])


if __name__ == "__main__":
    main()
