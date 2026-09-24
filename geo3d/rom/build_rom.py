#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Builds GEO3D.ROM (ASCII16 MegaROM): the player (geo3d_rom.asm) in bank 0 and
one command stream per demo in the following banks.

It starts with a language menu (English, Español, Português), itself a stream:
the menu picture uploaded to page 0, then the MENU opcode. Each language has
its own demo table; they differ only in the crawl, the one demo with text.

Demos, in order (each one is the port traffic already checked against the RTL):
  1. crawl    perspective text crawl (showcase), in the chosen language
  2. wire     wireframe cube + octahedron (z80/geo3d_demo.asm, captured)
  3. faces    GEO3D solid blocks, shaded (z80/geo3d_faces_demo.asm, captured)
  4. tex      GEO3D textured, spinning (z80/geo3d_tex_demo.asm, captured)
  5. panzoom  camera pan and zoom (showcase)
  6. flyin    letters fly in over a SCREEN 5 background, then the turn (showcase)

The .COM demos are run in the Z80 emulator (z80/run_demo_z80.py) and their
decoded traffic becomes the stream; the showcase scenes come straight from
showcase/showcase.py. Writes rom/out/GEO3D.ROM and rom/out/streams.json (the
expected traffic per demo, used by run_rom_z80.py).

Usage: build_rom.py [--base 0x88|0x98] [--music FILE.mid]
  --base 0x88  (default) real hardware, V9968 cartridge at 88h: GEO3D.ROM
  --base 0x98  emulator profile, V9968 as the machine's VDP and geo3d on
               9Dh/9Fh (openMSX fork, -ext geo3d): GEO3D_98.ROM
  The streams are the same for both; only the player's ports change.
  --music      MIDI played from the start of the crawl (music.py converts it
               for PSG, SCC + PSG and OPL4/OPL3 FM + PSG; the player uses the
               best chip it finds). Keep music you do not own out of the
               repository; without --music the ROM is silent.
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
Z80 = os.path.join(ROOT, "z80")
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.join(ROOT, "showcase"))
sys.path.insert(0, Z80)

import music  # noqa: E402
import showcase  # noqa: E402
from gen_face_tables import palette as logo_palette  # noqa: E402

BANK = 16384
LOOPS = {"wire": 2, "faces": 2, "tex": 2, "panzoom": 2, "flyin": 2}

OP_END, OP_GEO, OP_GEOD, OP_VREG, OP_VIND, OP_WAITGEO, OP_WAITCE = range(7)
OP_VRLE, OP_FLIP, OP_MARK, OP_LOOP, OP_NEXTBANK, OP_MENU, OP_PACE, OP_MUSIC = range(7, 15)
MUS_NEXTBANK = 0xFE                          # music data: continue in the next bank

# menu order = player's menu_sel (0, 1, 2) = lang_tables order
LANGS = [("en", "English"), ("es", "Español"), ("pt", "Português")]


# ------------------------------------------------------------------ menu
MENU_PAL = ([(0, 0, 0), (2, 2, 3), (4, 4, 5), (7, 7, 7), (7, 6, 1)]   # crawl colours
            + [(3, 3, 4)] * 3          # 5..7: option texts (the player repaints them)
            + [(0, 0, 0)] * 3          # 8..10: option arrows (idem)
            + [(4, 4, 5)]              # 11: hints
            + [(0, 0, 0)] * 4)


def menu_page():
    """The menu picture, one SCREEN 5 page: starfield, GEO3D, the three
    languages. Each option's text and arrow have their own palette entry, so
    the player highlights the choice by changing the palette only."""
    from PIL import Image, ImageDraw, ImageFont
    dv = "/usr/share/fonts/truetype/dejavu/"
    fbig = ImageFont.truetype(dv + "DejaVuSans-Bold.ttf", 34)
    fopt = ImageFont.truetype(dv + "DejaVuSansCondensed-Bold.ttf", 18)
    fsym = ImageFont.truetype(dv + "DejaVuSans-Bold.ttf", 14)
    fhint = ImageFont.truetype(dv + "DejaVuSansCondensed-Bold.ttf", 11)
    W, H = showcase.W, showcase.H
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"

    boxes = []                                    # text areas, kept free of stars

    def text(xy, s, fnt, colour):
        d.text(xy, s, font=fnt, fill=colour)
        x0, y0, x1, y1 = d.textbbox(xy, s, font=fnt)
        M = 6                                     # a star next to a glyph reads as punctuation
        boxes.append((x0 - M, y0 - M, x1 + M, y1 + M))

    def centred(y, s, fnt, colour):
        tw = d.textlength(s, font=fnt)
        assert tw <= W - 8, s
        text(((W - tw) / 2, y), s, fnt, colour)

    centred(16, "GEO3D", fbig, 4)
    centred(62, "LANGUAGE  ·  IDIOMA", fhint, 11)
    for i, (_, name) in enumerate(LANGS):
        y = 92 + 28 * i
        text((64, y + 3), "▶", fsym, 8 + i)
        text((86, y), f"{i + 1}   {name}", fopt, 5 + i)
    centred(186, "1 2 3     ↑ ↓   SPACE / RETURN", fhint, 11)
    stars = showcase.starfield(1985, 150)
    page = bytearray(showcase.PAGE)
    px = img.load()
    near = [[any(x0 <= x < x1 and y0 <= y < y1 for x0, y0, x1, y1 in boxes) for x in range(W)]
            for y in range(H)]
    for y in range(H):
        for x in range(W):
            c = px[x, y]
            if c == 0 and not near[y][x]:
                b = stars[y * 128 + (x >> 1)]
                c = (b >> 4) if not (x & 1) else (b & 15)
            page[y * 128 + (x >> 1)] |= (c << 4) if not (x & 1) else c
    return bytes(page), img


# ------------------------------------------------------------------ sources
def captured(asm, com, frames=130):
    """Assembles a .COM demo, runs it in the Z80 emulator, returns its decoded
    system traffic (sys ops) split into (setup, body): body = frames 2..129,
    one full turn that loops seamlessly (frame 128 = frame 0)."""
    subprocess.run(["z80asm", "-o", com, asm], cwd=Z80, check=True)
    stim = os.path.join(OUT, com + ".stim")
    sysf = os.path.join(OUT, com + ".sys")
    subprocess.run([sys.executable, "run_demo_z80.py", str(frames), com, stim, sysf],
                   cwd=Z80, check=True, stdout=subprocess.DEVNULL)
    ops = [l.split() for l in open(sysf) if l.strip()]
    items = to_items(ops)
    # split after the D that follows "F 1"
    cut, seen = None, False
    for i, it in enumerate(items):
        if it[0] == "F" and it[1] == 1:
            seen = True
        if seen and it[0] == "D":
            cut = i + 1
            break
    return items[:cut], items[cut:]


def to_items(ops):
    """sys op tokens -> items; consecutive VRAM bytes become one block."""
    items = []
    for p in ops:
        k = p[0]
        if k == "W":
            items.append(("W", int(p[1]), int(p[2], 16)))
        elif k == "V":
            items.append(("V", int(p[1]), int(p[2], 16)))
        elif k == "X":
            a, b = int(p[1], 16), int(p[2], 16)
            if items and items[-1][0] == "XB" and items[-1][1] + len(items[-1][2]) == a:
                items[-1][2].append(b)
            else:
                items.append(("XB", a, bytearray([b])))
        elif k in ("R", "C"):
            items.append((k,))
        elif k == "F":
            items.append(("F", int(p[1])))
        elif k == "D":
            items.append(("D", int(p[1])))
    return items


def from_show(sh):
    """showcase Show -> (setup, body) items; body = all frames."""
    setup, body = [], []
    for kind, fr, text in sh.ops:
        p = text.split()
        if kind == "XB":
            a, data = sh.blocks[int(p[1])]
            it = ("XB", a, bytearray(data))
        elif kind in ("W", "RUN"):
            it = ("W", int(p[1]), int(p[2], 16))
        elif kind == "V":
            it = ("V", int(p[1]), int(p[2], 16))
        elif kind in ("R", "C"):
            it = (kind,)
        elif kind == "F":
            it = ("F", int(p[1]))
        elif kind == "D":
            it = ("D", int(p[1]))
        elif kind == "P":
            it = ("P", int(p[1]))
        elif kind == "M":
            it = ("M",)
        (setup if fr is None else body).append(it)
    return setup, body


# ------------------------------------------------------------------ encoding
def rle(data):
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


def encode(items):
    """items -> list of atomic byte strings (opcodes)."""
    ops = []
    geo = None           # open GEO/GEOD op: [opcode, idx or None, data]
    vset = None          # open VDP group: [first reg, data]

    def close():
        nonlocal geo, vset
        if geo:
            if geo[1] is None:
                ops.append(bytes([OP_GEOD, len(geo[2])]) + bytes(geo[2]))
            else:
                ops.append(bytes([OP_GEO, geo[1], len(geo[2])]) + bytes(geo[2]))
            geo = None
        if vset:
            if len(vset[1]) == 1:
                ops.append(bytes([OP_VREG, vset[0], vset[1][0]]))
            else:
                ops.append(bytes([OP_VIND, vset[0], len(vset[1])]) + bytes(vset[1]))
            vset = None

    for it in items:
        k = it[0]
        if k == "W":
            if vset:
                close()
            if it[1] == 0:
                close()
                geo = [OP_GEO, it[2], bytearray()]
            else:
                if geo is None or len(geo[2]) == 255:
                    close()
                    geo = [OP_GEOD, None, bytearray()]
                geo[2].append(it[2])
            continue
        if k == "V":
            if geo:
                close()
            if vset and vset[0] + len(vset[1]) == it[1] and len(vset[1]) < 255:
                vset[1].append(it[2])
            else:
                close()
                vset = [it[1], bytearray([it[2]])]
            continue
        close()
        if k == "R":
            ops.append(bytes([OP_WAITGEO]))
        elif k == "C":
            ops.append(bytes([OP_WAITCE]))
        elif k == "D":
            ops.append(bytes([OP_FLIP, it[1] // 256]))
        elif k == "P":
            assert 2 <= it[1] <= 255, it
            ops.append(bytes([OP_PACE, it[1]]))
        elif k == "M":
            ops.append(bytes([OP_MUSIC]))
        elif k == "XB":
            a, data = it[1], bytes(it[2])
            pos = 0
            while pos < len(data):
                addr = a + pos
                room = 16384 - (addr & 0x3FFF)      # never cross a 16 KB VRAM boundary
                chunk = data[pos:pos + min(room, 6144)]
                packed = rle(chunk)
                ops.append(bytes([OP_VRLE, addr & 0xFF, (addr >> 8) & 0xFF, addr >> 16,
                                  len(packed) & 0xFF, len(packed) >> 8]) + packed)
                pos += len(chunk)
        # F: nothing to send
    close()
    return ops


class Packer:
    def __init__(self, first_bank):
        self.banks = [bytearray()]
        self.first = first_bank

    def here(self):
        return self.first + len(self.banks) - 1, 0x8000 + len(self.banks[-1])

    def put(self, op, nextbank=OP_NEXTBANK):
        assert len(op) < BANK - 1, len(op)
        if len(self.banks[-1]) + len(op) > BANK - 1:
            self.banks[-1].append(nextbank)
            self.banks.append(bytearray())
        self.banks[-1].extend(op)


PROFILES = {0x88: "GEO3D.ROM", 0x98: "GEO3D_98.ROM"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", type=lambda x: int(x, 0), default=0x88, choices=sorted(PROFILES))
    ap.add_argument("--music", help="MIDI file for the crawl")
    args = ap.parse_args()
    base = args.base
    rom_name = PROFILES[base]
    suffix = "" if base == 0x88 else f"_{base:02x}"
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(HERE, "rom_ports.asm"), "w").write(
        f"; generated by build_rom.py, do not edit\nPORT_BASE:  equ 0x{base:02x}\n")
    logo_pal = logo_palette()[1]
    demos = []

    def pal_bytes(pal):
        out = []
        for r, g, bl in pal:
            out += [(r << 4) | bl, g]
        return out

    crawl_blanks = {}
    for lang, _ in LANGS:
        sh, pal, _, _ = showcase.scene_crawl(lang)
        s, b = from_show(sh)
        demos.append((f"crawl_{lang}", pal_bytes(pal), s + b, [], 1))
        crawl_blanks[lang] = sum(showcase.crawl_paces(sh.nframes))
    s, b = captured("geo3d_demo.asm", "GEO3D.COM")
    demos.append(("wire", logo_pal, s, b, LOOPS["wire"]))
    s, b = captured("geo3d_faces_demo.asm", "GEO3DF.COM")
    demos.append(("faces", logo_pal, s, b, LOOPS["faces"]))
    s, b = captured("geo3d_tex_demo.asm", "GEO3DT.COM")
    demos.append(("tex", logo_pal, s, b, LOOPS["tex"]))
    sh, _, _, _ = showcase.scene_panzoom()
    s, b = from_show(sh)
    demos.append(("panzoom", logo_pal, s, b, LOOPS["panzoom"]))
    sh, _, _, _ = showcase.scene_flyin()
    s, b = from_show(sh)
    demos.append(("flyin", logo_pal, s, b, LOOPS["flyin"]))

    pk = Packer(1)
    table, expect = [], {}
    # the menu first: its picture on page 0, then MENU (it never returns)
    page, _ = menu_page()
    menu_setup = [("XB", 0, bytearray(page))]
    menu_at = pk.here()
    for op in encode(menu_setup):
        pk.put(op)
    pk.put(bytes([OP_MENU]))
    expect["menu"] = expand(menu_setup)
    for name, pal, setup, body, loops in demos:
        bank, addr = pk.here()
        table.append((name, bank, addr))
        for op in encode(setup):
            pk.put(op)
        if body:
            pk.put(bytes([OP_MARK, loops]))
            for op in encode(body):
                pk.put(op)
            pk.put(bytes([OP_LOOP]))
        pk.put(bytes([OP_END]))
        expect[name] = expand(list(setup) + list(body) * loops)   # PACE carries over, like the player
    # music: one stream per target, as long as the shortest crawl (one tick
    # per vertical blank), fading out at its end
    music_at, music_ticks = {}, None
    if args.music:
        nticks = min(crawl_blanks.values())
        enc, ticks, _, _ = music.convert(args.music, nticks)
        for target in ("psg", "scc", "opl"):
            music_at[target] = pk.here()
            for op in enc[target]:
                pk.put(op, MUS_NEXTBANK)
        music_ticks = {k: [[list(o) for o in ops] for ops in v] for k, v in ticks.items()}
        print("música: " + ", ".join(f"{t} {sum(len(o) for o in enc[t])} bytes" for t in enc)
              + f"; {nticks} ticks")
    nb = 1 + len(pk.banks)
    size = 1 << (nb - 1).bit_length() if nb > 1 else 1
    where = {name: (bank, addr) for name, bank, addr in table}
    shared = [d[0] for d in demos if not d[0].startswith("crawl_")]
    tables = {lang: [f"crawl_{lang}"] + shared for lang, _ in LANGS}
    lines = ["; generated by build_rom.py, do not edit",
             "menu_entry:", f"\tdb {menu_at[0]}\n\tdw 0x{menu_at[1]:04x}, pal_menu",
             "lang_tables:", "\tdw " + ", ".join(f"demo_table_{lang}" for lang, _ in LANGS)]
    for lang, _ in LANGS:
        lines.append(f"demo_table_{lang}:")
        for name in tables[lang]:
            bank, addr = where[name]
            lines.append(f"\tdb {bank}\n\tdw 0x{addr:04x}, pal_{name}\t; {name}")
        lines.append("\tdb 0xFF")
    lines.append("music_table:\t\t\t; psg, scc, opl: bank (FFh = no music), address")
    for target in ("psg", "scc", "opl"):
        bank, addr = music_at.get(target, (0xFF, 0))
        lines.append(f"\tdb {bank}\n\tdw 0x{addr:04x}\t; {target}")
    lines.append("pal_menu:\n\tdb " + ",".join(f"0x{x:02x}" for x in pal_bytes(MENU_PAL)))
    for name, pal, *_ in demos:
        lines.append(f"pal_{name}:\n\tdb " + ",".join(f"0x{x:02x}" for x in pal))
    open(os.path.join(HERE, "rom_tables.asm"), "w").write("\n".join(lines) + "\n")
    subprocess.run(["z80asm", "-o", os.path.join(OUT, f"bank0{suffix}.bin"),
                    "--label=" + os.path.join(OUT, f"labels{suffix}.txt"), "geo3d_rom.asm"],
                   cwd=HERE, check=True)
    bank0 = open(os.path.join(OUT, f"bank0{suffix}.bin"), "rb").read()
    assert len(bank0) <= BANK, len(bank0)
    rom = bytearray(bank0) + bytearray(BANK - len(bank0))
    for bnk in pk.banks:
        rom += bnk + bytearray(BANK - len(bnk))
    rom += bytearray(size * BANK - len(rom))
    open(os.path.join(OUT, rom_name), "wb").write(rom)
    json.dump({"demos": [d[0] for d in demos], "expect": expect, "table": table,
               "langs": [lang for lang, _ in LANGS], "tables": tables,
               "music": music_ticks},
              open(os.path.join(OUT, "streams.json"), "w"))
    used = sum(len(bk) for bk in pk.banks)
    print(f"{rom_name} (portas {base:02X}h): {len(rom) // 1024} KB (ASCII16, {size} bancos de 16 KB), "
          f"player {len(bank0)} bytes, fluxos {used // 1024} KB em {len(pk.banks)} bancos")
    print(f"  {'menu':8s} banco {menu_at[0]:3d} 0x{menu_at[1]:04x}")
    for (name, bank, addr) in table:
        print(f"  {name:8s} banco {bank:3d} 0x{addr:04x}")


def expand(items, pace=2):
    """items -> the traffic a decoder sees (run_demo_z80.py SYS format, no F).
    Each page flip is "D <ypage> /<blanks>": the vertical blanks the player
    waits for before it (PACE; every demo starts at 2)."""
    out = []
    for it in items:
        k = it[0]
        if k == "P":
            pace = it[1]
            continue
        if k == "W":
            out.append(f"W {it[1]} {it[2]:02x}")
        elif k == "V":
            out.append(f"V {it[1]} {it[2]:02x}")
            if it[1] == 46:
                out.append("C")
        elif k == "XB":
            out += [f"X {it[1] + i:05x} {b:02x}" for i, b in enumerate(it[2])]
        elif k == "R":
            out.append("R")
        elif k == "D":
            out += ["C", f"D {it[1]} /{pace}"]
    return out


if __name__ == "__main__":
    main()
