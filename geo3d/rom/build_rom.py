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

The streams are compressed (g3lz.py): each one is cut into blocks of at most
8 KB of whole ops (the player's decode buffer), NEXTBLOCK closing each block
but the last, and each block is compressed on its own. A loop body starts a
new block (MARK closes the block before it), so the player restarts the loop
by decoding that block again. What a demo does up to its first page flip
(RAW_SETUP: the uploads behind the black screen and the first frame; the
menu's picture) and the setup frames of the loop demos (RAW_SETUP_FRAMES)
stay raw: the player reads them straight from ROM, as fast as before the
compression. Raw ops are stored once (Rom): the demos upload some of the
same textures and tables (tex, panzoom and flyin 21 KB of textures, faces
to flyin 3 KB of geo3d tables, the three crawls 4 KB of uploads and
tables: 59 KB in all), and a run of raw ops a stream placed earlier
already has in ROM is run from there by a CALL; a VRLE that meets a bank's
end goes on in the next bank as VMORE, so raw blocks leave no gaps. Each
costs the player a few hundred T-states (0.2 ms), so the raw setups keep
their speed: every black screen and page flip falls on the same vertical
blank as with the uncompressed ROM (openMSX). The music data follows,
uncompressed. The parse of each block is cached in rom/out/g3lz/.

A MOD goes in as a MOD (modplay.py): the whole song (its header, every
pattern it reaches and the samples those positions name, the file's own
bytes), which the MoonSound plays from the first crawl on, over and over
through every demo, and the lookup tables of the MOD player in
geo3d_modplay.asm, after the converted music (rom_mod.asm has where). A
MoonSound with the sample RAM for its samples plays the MOD; else the
conversion plays in the crawl. While the MOD plays, the player's uploads
are faster (op_vrle_f), which pays for the MOD's work: the frames whose
length an upload sets (the black screens, tex's texture frame) end no
later than in the uncompressed ROM and the other flips stay on their
blanks, if the MOD's work fits in the time each frame has to spare. The
build measures that work (modcost.py: the MOD player in the Z80 emulator,
MSX M1 waits counted, tick by tick) and takes the MOD only if its
heaviest stretch fits (FRAME_SLACK); else only the conversion plays.

Usage: build_rom.py [--base 0x88|0x98] [--music FILE.mid|FILE.mod]
  --base 0x88  (default) real hardware, V9968 cartridge at 88h: GEO3D.ROM
  --base 0x98  emulator profile, V9968 as the machine's VDP and geo3d on
               9Dh/9Fh (openMSX fork, -ext geo3d): GEO3D_98.ROM
  The streams are the same for both; only the player's ports change.
  --music      MIDI or ProTracker MOD (told apart by content) played from the
               start of the crawl (music.py converts it for PSG, SCC + PSG
               and OPL4/OPL3 FM + PSG; the player uses the best chip it
               finds; a MOD plays as a MOD on a MoonSound's wave part, the
               whole song, looping). Keep music you do not own out of the
               repository; without --music the ROM is silent.
  --mod-passes the looping MOD's passes its model covers (the checks, and
               the ticks run_rom_z80.py compares): at least this many
               (default 3) and at least MOD_SECONDS (600 s)
  --mod-unchecked  take a MOD whose work does not fit (a test ROM only)
  --null-mod [2]   a measure for modcost.py --measure-frames (not a ROM to
               use): the MOD player's hooks without any tick (2: and nothing
               decoded ahead)
"""
import argparse
import hashlib
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

import g3lz  # noqa: E402
import modcost  # noqa: E402
import modplay  # noqa: E402
import music  # noqa: E402
import showcase  # noqa: E402
from gen_face_tables import palette as logo_palette  # noqa: E402

BANK = 16384
ROM_BANKS = 32                               # the cartridge: 512 KB
BLOCK = 8192                                 # decoded block: the player's dz_buf
AHEAD = 259                                  # the player decodes this far past each op (DZ_AHEAD)
# The setup frames of the loop demos (first FLIP to MARK) upload tables and
# textures within a frame; decoding them there would make that frame late
# (faces 2 -> 4 vblanks, tex 28 -> 38), so they stay raw: about 20 KB more ROM.
RAW_SETUP_FRAMES = True
# Everything up to a stream's first FLIP (the uploads behind the black
# screen before a demo's first frame, and that frame; the menu's picture)
# stays raw too: decoded, the black screens would last 0.3-0.5 s longer
# (crawl 0.75 -> 1.23 s, panzoom 0.42 -> 0.67 s, flyin 0.65 -> 1.02 s).
RAW_SETUP = True
LOOPS = {"wire": 2, "faces": 2, "tex": 2, "panzoom": 2, "flyin": 2}
# The frames whose length an upload sets, not PACE (besides each demo's frame
# 0, the black screen behind its setup): tex's frame 1, its textures. An
# EAGER starts each of them and each demo's frame 0: while the MOD plays,
# its polls there work out its next tick at once, so its notes keep their
# time (the flip's wait, where they would, is far off).
UPLOAD_FRAMES = {"tex": [1]}
# The first frames of a block decoded between two page flips (a block that
# starts after a stream's first FLIP) are parsed for speed (g3lz fast_len):
# the block before it is decoded to its end by then, so these frames decode
# their ops on demand. flyin's frames after a new block took 16-20 ms of
# decoding each (short matches: ~110 T a byte); with the MOD's work on top
# (a row with notes: up to ~9 ms) that was too close to a late flip. Parsed
# for speed over 3 frames they take 4-9 ms; the ROM grows by 3.5 KB.
FAST_FRAMES = 3
MOD_PASSES = 3                               # the MOD's passes the model covers (checks, harness), at least,
MOD_SECONDS = 600                            # and at least this long (run_rom_z80.py --cycles 3: ~420 s)

OP_END, OP_GEO, OP_GEOD, OP_VREG, OP_VIND, OP_WAITGEO, OP_WAITCE = range(7)
OP_VRLE, OP_FLIP, OP_MARK, OP_LOOP, OP_NEXTBLOCK, OP_MENU, OP_PACE, OP_MUSIC = range(7, 15)
OP_CALL, OP_VMORE, OP_EAGER = 15, 16, 17
MUS_NEXTBANK = 0xFE                          # music data: continue in the next bank
# Raw ops a CALL may run from another place in ROM (no page flip, no control
# flow: the player comes back at the end of the ops called)
CALL_KINDS = {OP_GEO, OP_GEOD, OP_VREG, OP_VIND, OP_WAITGEO, OP_WAITCE, OP_VRLE, OP_PACE}
MIN_CALL = 64                                # bytes a CALL must save (each costs 6, ~0.1 ms)
MIN_PART = 64                                # a VRLE is cut at a bank's end only if this much fits

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


def with_eager(ops, frames):
    """ops with EAGER at the start of each frame in frames (0: the stream's
    first op; k: right after its k-th FLIP)"""
    out, frame = [], 0
    for op in ops:
        if frame in frames and (frame == 0 and not out or out and out[-1][0] == OP_FLIP):
            out.append(bytes([OP_EAGER]))
        out.append(op)
        if op[0] == OP_FLIP:
            frame += 1
    return out


def op_len(s, q):
    """length of the op at s[q]"""
    k = s[q]
    if k in (OP_GEO, OP_VIND):
        return 3 + s[q + 2]
    if k == OP_GEOD:
        return 2 + s[q + 1]
    if k == OP_VREG:
        return 3
    if k == OP_VRLE:
        return 6 + (s[q + 4] | s[q + 5] << 8)
    if k == OP_VMORE:
        return 3 + (s[q + 1] | s[q + 2] << 8)
    if k == OP_CALL:
        return 6
    if k in (OP_FLIP, OP_MARK, OP_PACE):
        return 2
    return 1


def rle_cut(packed, room):
    """the most bytes of whole RLE codes at the start of `packed` that fit
    in `room` (a VRLE cut there goes on as VMORE)"""
    q = 0
    while q < len(packed):
        n = 2 + packed[q] if packed[q] < 0x80 else 2
        if q + n > room:
            break
        q += n
    return q


class Blocks:
    """One stream's ops in blocks of at most BLOCK decoded bytes, each but the
    last closed by NEXTBLOCK.
    hot[i]: block i starts between two page flips (after the stream's first
    FLIP, or at a loop restart), so the player decodes its ops up to its
    first FLIP on demand, within a frame.
    raw[i]: block i is stored as it is and the player reads it from ROM:
    everything up to the stream's first FLIP, that one included (RAW_SETUP;
    the second frame starts a block), and the setup frames of a loop demo,
    from the stream's first FLIP to MARK (RAW_SETUP_FRAMES)."""
    def __init__(self):
        self.blocks, self.hot, self.raw = [bytearray()], [False], [False]
        self.first_flip = None                   # the block of the stream's first FLIP

    def end_setup(self):
        """the stream has no FLIP (the menu): all of it is raw (RAW_SETUP)"""
        if RAW_SETUP and self.first_flip is None:
            for i in range(len(self.blocks)):
                self.raw[i] = True

    def put(self, op):
        assert len(op) < BLOCK, len(op)
        if len(self.blocks[-1]) + len(op) > BLOCK - 1:     # NEXTBLOCK must fit too
            self.cut()
        self.blocks[-1] += op
        if op[0] == OP_FLIP and self.first_flip is None:
            self.first_flip = len(self.blocks) - 1
            if RAW_SETUP:                        # up to here raw; the second frame
                for i in range(len(self.blocks)):     # starts a block
                    self.raw[i] = True
                self.cut(hot=True)

    def cut(self, hot=False):
        self.blocks[-1].append(OP_NEXTBLOCK)
        self.blocks.append(bytearray())
        self.hot.append(hot or self.first_flip is not None)
        self.raw.append(False)

    def mark(self, loops):
        """MARK, the last op of its block: LOOP decodes the body's first block
        again"""
        self.put(bytes([OP_MARK, loops]))
        if RAW_SETUP and self.first_flip is None:   # the setup: raw, and so will be
            for i in range(len(self.blocks)):       # the loop up to its first FLIP
                self.raw[i] = True
        if RAW_SETUP_FRAMES and self.first_flip is not None:
            for i in range(self.first_flip, len(self.blocks)):
                self.raw[i] = True
        self.cut(hot=True)


def split_ops(s):
    """whole ops -> the list of them"""
    out, q = [], 0
    while q < len(s):
        n = op_len(s, q)
        out.append(bytes(s[q:q + n]))
        q += n
    assert q == len(s)
    return out


def player_view(rom, bank, addr):
    """A stream as the player reads it from the ROM image: block after block
    (G3LZ or raw, bank escapes followed, each CALL's ops read where it
    points) up to the one that does not end with NEXTBLOCK. -> its ops
    without the NEXTBLOCKs, each VRLE that a bank's end cut joined again with
    its VMOREs. Checks that a G3LZ block fits dz_buf and has no CALL or
    VMORE, that MARK is the last op of its block, that a CALL runs whole raw
    ops of one bank (no NEXTBLOCK, page flip or control flow among them),
    that a VMORE follows its VRLE's part (no port write between them: they
    send what the VRLE did) and that each part is whole RLE codes."""
    pos, out = bank * BANK + addr - 0x8000, []
    while True:
        pos = g3lz.skip_bank(rom, pos, BANK)
        if rom[pos] == 0 and rom[pos + 1] == g3lz.ESC_RAW:
            q, ops = pos + 2, []
            while True:
                op = bytes(rom[q:q + op_len(rom, q)])
                q += len(op)
                if op[0] == OP_CALL:
                    b, s, e = op[1], op[2] | op[3] << 8, op[4] | op[5] << 8
                    assert 0x8000 <= s < e <= 0xC000, "CALL"
                    called = split_ops(rom[b * BANK + s - 0x8000:b * BANK + e - 0x8000])
                    assert all(c[0] in CALL_KINDS or (i == 0 and c[0] == OP_VMORE)
                               for i, c in enumerate(called)), "CALL"
                    ops += called
                else:
                    ops.append(op)
                if op[0] in (OP_NEXTBLOCK, OP_END, OP_MENU):
                    break
            assert pos // BANK == (q - 1) // BANK, "a raw block across banks"
            pos = q
        else:
            blk, pos = g3lz.decompress(rom, pos, BANK)
            assert len(blk) <= BLOCK
            ops = split_ops(blk)
            assert not any(op[0] in (OP_CALL, OP_VMORE) for op in ops), "CALL or VMORE in a G3LZ block"
        marks = [i for i, op in enumerate(ops) if op[0] == OP_MARK]
        assert not marks or (marks == [len(ops) - 2] and ops[-1][0] == OP_NEXTBLOCK), "MARK"
        if ops[-1][0] != OP_NEXTBLOCK:
            return join_vmore(out + ops)
        out += ops[:-1]


def join_vmore(ops):
    """VRLE, VMORE... -> the one VRLE they send"""
    out = []
    for op in ops:
        if op[0] in (OP_VRLE, OP_VMORE):
            data = op[6:] if op[0] == OP_VRLE else op[3:]
            assert rle_cut(data, len(data)) == len(data), "an RLE code cut in two"
        if op[0] == OP_VMORE:
            assert out and out[-1][0] == OP_VRLE, "VMORE without its VRLE"
            data = out[-1][6:] + op[3:]
            out[-1] = out[-1][:4] + bytes([len(data) & 0xFF, len(data) >> 8]) + data
        else:
            out.append(op)
    return out


def fast_len(blk, frames=None):
    """the bytes of a hot block decoded on demand: its ops up to its first
    FAST_FRAMES FLIPs, plus the player's lookahead (g3lz parses them for
    speed)"""
    frames = FAST_FRAMES if frames is None else frames
    q = 0
    while q < len(blk):
        k = blk[q]
        q += op_len(blk, q)
        if k == OP_FLIP:
            frames -= 1
            if frames == 0:
                break
    return q + AHEAD


G3LZ_KEY = open(g3lz.__file__, "rb").read()


def g3lz_block(blk, fast):
    """G3LZ bytes of one block; the parse is slow, so it is cached in out/g3lz/
    (the key covers the compressor's source)"""
    fn = os.path.join(OUT, "g3lz", hashlib.sha1(G3LZ_KEY + b"%d:" % fast + blk).hexdigest())
    if os.path.exists(fn):
        return open(fn, "rb").read()
    data = g3lz.pack(blk, fast)
    assert g3lz.decompress(data)[0] == blk
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    open(fn + ".tmp", "wb").write(data)
    os.replace(fn + ".tmp", fn)
    return data


class Rom:
    """ROM banks from bank `first` on: the compressed streams (a token or a
    raw op never crosses a bank: the escape 00 01 goes on at 8000h of the
    next one), then plain data (music) with an end-of-bank opcode.

    Raw blocks fill the banks: a VRLE that does not fit before a bank's end
    is cut there (whole RLE codes) and goes on as VMORE in the next bank; an
    op already in ROM, in a run of raw ops a stream placed earlier (another
    demo's upload of the same texture or table, or this stream's), is not
    placed again: a CALL runs that run where it is (one CALL per piece of it
    that has no NEXTBLOCK or bank end inside). Both cost the player a few
    hundred T-states where a raw op costs thousands: the raw setups keep the
    speed they had uncompressed."""
    RESERVE = 3                                  # NEXTBLOCK and 00 01 always fit after an op

    def __init__(self, first):
        self.banks, self.first = [bytearray()], first
        self.mat = []            # raw ops placed a CALL may run: [op, [(bank, first, end)], follows the previous]
        self.index = {}          # op -> its indices in mat
        self.chain = False       # the next op placed follows mat[-1] in its stream
        self.open = False        # inside a raw piece (00 02 written)
        self.stats = dict(calls=0, called=0, vmore=0)

    def here(self):
        return self.first + len(self.banks) - 1, 0x8000 + len(self.banks[-1])

    def room(self):
        return BANK - len(self.banks[-1])

    def next_bank(self):
        self.banks[-1] += bytes([0, g3lz.ESC_BANK])
        self.banks.append(bytearray())

    def token(self, t):
        if self.room() < len(t) + 2:                 # 2 bytes always stay free for 00 01
            self.next_bank()
        self.banks[-1] += t

    def _put(self, op):
        """one op into the raw block being placed -> where its bytes went,
        [(bank, first, end)] (two or more pieces for a VRLE cut by a bank's
        end: the VRLE's part, then VMORE)"""
        cut = op[0] in (OP_VRLE, OP_VMORE)
        segs = []
        while True:
            if not self.open:                        # 00 02, where the op (or its cut) fits
                need = len(op)
                if cut and len(op) > 6:              # the header, and enough RLE codes for a cut
                    hdr = 6 if op[0] == OP_VRLE else 3
                    code = 2 + op[hdr] if op[hdr] < 0x80 else 2
                    need = min(need, hdr + max(MIN_PART, code))
                assert need <= BANK - 2 - 2 - self.RESERVE, f"op of {len(op)} bytes"
                if self.room() - 2 - self.RESERVE < need:
                    self.next_bank()
                self.banks[-1] += bytes([0, g3lz.ESC_RAW])
                self.open = True
            room = self.room() - self.RESERVE
            if len(op) <= room:
                bank, at = self.here()
                self.banks[-1] += op
                return segs + [(bank, at, at + len(op))]
            hdr = 6 if op[0] == OP_VRLE else 3
            k = rle_cut(op[hdr:], room - hdr) if cut and room - hdr >= MIN_PART else 0
            if k:
                part = op[:hdr - 2] + bytes([k & 0xFF, k >> 8]) + op[hdr:hdr + k]
                rest = op[hdr + k:]
                bank, at = self.here()
                self.banks[-1] += part
                segs.append((bank, at, at + len(part)))
                op = bytes([OP_VMORE, len(rest) & 0xFF, len(rest) >> 8]) + rest
                self.stats["vmore"] += 1
            self.banks[-1].append(OP_NEXTBLOCK)      # on in the next bank
            self.next_bank()
            self.open = False

    def _match(self, ops, i):
        """the longest run of placed raw ops equal to ops[i:] -> (index in
        mat, ops, bytes)"""
        best = None
        for k in self.index.get(ops[i], ()):
            n, size = 1, len(ops[i])
            while (i + n < len(ops) and k + n < len(self.mat) and self.mat[k + n][2]
                   and self.mat[k + n][0] == ops[i + n]):
                size += len(ops[i + n])
                n += 1
            if best is None or size > best[2]:
                best = (k, n, size)
        return best

    def raw(self, blk, last=False):
        """a raw block (it ends with NEXTBLOCK, or it is the stream's last):
        its ops (CALLs for the runs already in ROM, see the class), then its
        NEXTBLOCK (none after the last block's last op, END or MENU)"""
        ops = split_ops(blk)
        if not last:
            assert ops[-1][0] == OP_NEXTBLOCK
            ops = ops[:-1]
        i = 0
        while i < len(ops):
            m = self._match(ops, i) if ops[i][0] in CALL_KINDS else None
            if m:
                k, n, size = m
                segs = []                            # the run's pieces in ROM, merged
                for op, sg, _ in self.mat[k:k + n]:
                    for b, s, e in sg:
                        if segs and segs[-1][0] == b and segs[-1][2] == s:
                            segs[-1] = (b, segs[-1][1], e)
                        else:
                            segs.append((b, s, e))
                if size - 6 * len(segs) >= MIN_CALL:
                    for b, s, e in segs:
                        self._put(bytes([OP_CALL, b, s & 0xFF, s >> 8, e & 0xFF, e >> 8]))
                    self.stats["calls"] += len(segs)
                    self.stats["called"] += size
                    self.chain = False
                    i += n
                    continue
            segs = self._put(ops[i])
            if ops[i][0] in CALL_KINDS:
                self.index.setdefault(ops[i], []).append(len(self.mat))
                self.mat.append((ops[i], segs, self.chain))
                self.chain = True
            else:
                self.chain = False
            i += 1
        if not last:
            assert self.open and self.room() >= self.RESERVE
            self.banks[-1].append(OP_NEXTBLOCK)
        self.open = False

    def stream(self, bl):
        """-> (bank, address) of the stream's first block"""
        items = []
        for blk, hot, raw in zip(bl.blocks, bl.hot, bl.raw):
            if raw and items and items[-1][0] and split_ops(items[-1][1])[-2][0] != OP_MARK:
                # raw after raw: one raw block (a raw block needs no dz_buf; a
                # NEXTBLOCK less to run, and a CALL can run ops of both)
                items[-1] = (True, items[-1][1][:-1] + bytes(blk))
            elif raw:
                items.append((True, bytes(blk)))
            else:
                items.append((False, g3lz.split(g3lz_block(bytes(blk), fast_len(blk) if hot else 0))))
        raw, it = items[0]
        if self.room() < (2 + 6 + MIN_PART + self.RESERVE if raw else len(it[0]) + 2):
            self.banks.append(bytearray())           # the demo table points at a block
        at = self.here()
        self.chain = False
        for i, (raw, it) in enumerate(items):
            if raw:
                self.raw(it, i == len(items) - 1)
            else:
                self.chain = False
                for t in it:
                    self.token(t)
        return at

    def put(self, op, nextbank):
        assert len(op) < BANK - 1, len(op)
        if len(self.banks[-1]) + len(op) > BANK - 1:
            self.banks[-1].append(nextbank)
            self.banks.append(bytearray())
        self.banks[-1] += op


PROFILES = {0x88: "GEO3D.ROM", 0x98: "GEO3D_98.ROM"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", type=lambda x: int(x, 0), default=0x88, choices=sorted(PROFILES))
    ap.add_argument("--music", help="MIDI or MOD file for the crawl")
    ap.add_argument("--null-mod", type=int, nargs="?", const=1, default=0, choices=(1, 2),
                    help="a measure (modcost.py --measure-frames): the MOD player's hooks, uploads and polls as "
                         "with the MOD, but timer 2 never starts, so it never plays a tick (2: and nothing "
                         "decoded ahead); not a ROM to use")
    ap.add_argument("--mod-passes", type=int, default=MOD_PASSES,
                    help="passes of a looping MOD the model covers (the checks and run_rom_z80.py), at least; "
                         f"and at least {MOD_SECONDS} s")
    ap.add_argument("--mod-unchecked", action="store_true",
                    help="take a MOD whose work does not fit (modcost.py): a test ROM, whose black screens or "
                         "frames may be late, not one to use")
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

    # a MOD also as a MOD, for a MoonSound (modplay.Song): the whole song,
    # looping; the MOD player's lookup tables (one bank's worth) first in bank 1
    nticks = min(crawl_blanks.values())
    song = None
    if args.music and music.mod_channels(open(args.music, "rb").read()) is not None:
        try:
            song = modplay.Song(args.music, loop=True, passes=args.mod_passes, min_seconds=MOD_SECONDS)
        except ValueError as e:
            print(f"MOD: the MoonSound's MOD player can not play it ({e}): only the conversion")
    if song:
        # its work must fit in the time the demos' frames have to spare
        cost = modcost.measure(song, OUT)
        fits, report, _ = modcost.fit(song, cost)
        print(f"MOD: work per tick (MSX clock cycles, modcost.py): {modcost.summary(cost)}")
        for line in report:
            print(f"MOD:   {line}")
        if not fits and not args.mod_unchecked:
            print("MOD: its work does not fit in the demos' frames (a black screen, a demo's start or a frame "
                  "would be later than without it): only the conversion")
            song = None
        elif not fits:
            print("MOD: --mod-unchecked: the MOD goes in all the same (a test ROM, not one to use)")
    pk = Rom(1)
    if song:
        pk.banks[-1] += song.tab_blob()
    table, expect, blocks = [], {}, {}
    # the menu first: its picture on page 0, then MENU (it never returns)
    page, _ = menu_page()
    menu_setup = [("XB", 0, bytearray(page))]
    bl = Blocks()
    for op in encode(menu_setup):
        bl.put(op)
    bl.put(bytes([OP_MENU]))
    bl.end_setup()
    menu_at = pk.stream(bl)
    blocks["menu"] = (menu_at, bl)
    expect["menu"] = expand(menu_setup)
    for name, pal, setup, body, loops in demos:
        bl = Blocks()
        for op in with_eager(encode(setup), [0] + UPLOAD_FRAMES.get(name, [])):
            bl.put(op)
        if body:
            bl.mark(loops)
            for op in encode(body):
                bl.put(op)
            bl.put(bytes([OP_LOOP]))
        bl.put(bytes([OP_END]))
        bl.end_setup()
        bank, addr = pk.stream(bl)
        table.append((name, bank, addr))
        blocks[name] = ((bank, addr), bl)
        expect[name] = expand(list(setup) + list(body) * loops)   # PACE carries over, like the player
    size_all = sum(len(b) for _, bl in blocks.values() for b in bl.blocks)
    size_raw = sum(len(b) for _, bl in blocks.values() for b, r in zip(bl.blocks, bl.raw) if r)
    nblk = sum(len(bl.blocks) for _, bl in blocks.values())
    nraw = sum(sum(bl.raw) for _, bl in blocks.values())
    tab_len = len(song.tab_blob()) if song else 0     # (the MOD player's tables come first in bank 1)
    packed = sum(map(len, pk.banks)) - tab_len
    print(f"fluxos: {size_all} bytes em {nblk} blocos ({nraw} crus: {size_raw} bytes) -> {packed} na ROM "
          f"(G3LZ, {size_all / packed:.2f}x), bancos 1 a {len(pk.banks)}"
          + (f", depois das tabelas do player do MOD ({tab_len} bytes)" if tab_len else ""))
    print(f"  crus: {pk.stats['called']} bytes de ops já na ROM rodados por {pk.stats['calls']} CALLs, "
          f"{pk.stats['vmore']} VRLEs cortados no fim de um banco (VMORE)")
    streams_end = pk.here()
    # music: one stream per target, as long as the shortest crawl (one tick
    # per vertical blank), fading out at its end
    music_at, music_ticks = {}, None
    if args.music:
        enc, ticks, _, _ = music.convert(args.music, nticks)
        for target in ("psg", "scc", "opl"):
            music_at[target] = pk.here()
            for op in enc[target]:
                pk.put(op, MUS_NEXTBANK)
        music_ticks = {k: [[list(o) for o in ops] for ops in v] for k, v in ticks.items()}
        print("música: " + ", ".join(f"{t} {sum(len(o) for o in enc[t])} bytes" for t in enc)
              + f"; {nticks} ticks")
    music_end = pk.here()
    # a MOD also as a MOD, for a MoonSound (modplay.Song.pack): the player's
    # lookup tables, then the part of the MOD the crawl plays, as long as the
    # conversion, with the same fade
    mod_json = None
    if song:
        bank, addr = pk.here()
        if addr >= 0xC000:
            pk.banks.append(bytearray())
            bank, addr = pk.here()
        mbanks, mod_equ = song.pack(bank, addr, tab=(1, 0x8000))
        if args.null_mod:
            mod_equ = mod_equ.replace("MP_NULL:        equ 0", f"MP_NULL:        equ {args.null_mod}")
            print(f"MOD: --null-mod {args.null_mod}: timer 2 never starts (a measure, not a ROM to use)")
        pk.banks[-1] += mbanks[0]
        pk.banks.extend(bytearray(b) for b in mbanks[1:])
        mod_json = {"writes_start": song.writes_start, "writes": song.writes, "heads": song.heads,
                    "counts": song.counts, "ideal": song.times()[0], "t2_unit": modplay.T2_UNIT,
                    "seconds": song.seconds, "image_len": len(song.image),
                    "image_sha1": hashlib.sha1(song.image).hexdigest(), "blocks": song.rt.blocks,
                    "used": song.used_channels(), "pans": song.pans, "loop": song.loop,
                    "pass_starts": song.pass_starts if song.loop else None}
        print(f"MOD: {song.summary()}")
    else:
        mod_equ = modplay.no_mod_equ()
    open(os.path.join(HERE, "rom_mod.asm"), "w").write(mod_equ)
    nb = 1 + len(pk.banks)
    assert nb <= ROM_BANKS, f"{nb} bancos: a ROM tem {ROM_BANKS}"
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
    # every stream decoded from the ROM image, block after block, as the
    # player reads it (bank escapes included)
    for name, ((bank, addr), bl) in blocks.items():
        want = [op for op in split_ops(b"".join(bl.blocks)) if op[0] != OP_NEXTBLOCK]
        assert player_view(rom, bank, addr) == want, name
    if song:
        mod_check(rom, song)
    open(os.path.join(OUT, rom_name), "wb").write(rom)
    json.dump({"demos": [d[0] for d in demos], "expect": expect, "table": table,
               "langs": [lang for lang, _ in LANGS], "tables": tables,
               "music": music_ticks, "mod": mod_json, "upload_frames": UPLOAD_FRAMES},
              open(os.path.join(OUT, "streams.json"), "w"))
    used = sum(len(bk) for bk in pk.banks)
    free = (ROM_BANKS - nb) * BANK + pk.room()
    print(f"{rom_name} (portas {base:02X}h): {len(rom) // 1024} KB (ASCII16, {size} bancos de 16 KB), "
          f"player {len(bank0)} bytes, fluxos e música {used // 1024} KB nos bancos 1 a {nb - 1}")
    print(f"  livre na ROM de {ROM_BANKS * BANK // 1024} KB: {free} bytes ({free // 1024} KB): "
          f"banco {nb - 1} de 0x{pk.here()[1]:04x}, bancos {nb} a {ROM_BANKS - 1}; "
          f"banco 0: {BANK - len(bank0)} bytes")
    print(f"  fluxos até banco {streams_end[0]} 0x{streams_end[1]:04x}")
    if args.music:
        print(f"  música convertida até banco {music_end[0]} 0x{music_end[1]:04x}")
    if song:
        (tb, ta), (mb, ma) = song.packed["tables"], song.packed["mod"]
        print(f"  MOD: tabelas do player {modplay.TAB_LEN} bytes no banco {tb} 0x{ta:04x}, o MOD "
              f"{song.packed['mod_len']} bytes do banco {mb} 0x{ma:04x} (precisa de {song.rt.blocks} x 128 KB "
              f"de sample RAM: {len(song.image)} bytes)")
    print(f"  {'menu':8s} banco {menu_at[0]:3d} 0x{menu_at[1]:04x}")
    for (name, bank, addr) in table:
        print(f"  {name:8s} banco {bank:3d} 0x{addr:04x}")


def rom_bytes(rom, bank, addr, n):
    """n bytes of page 2 data from (bank, addr) on, going on at 8000h of the
    next bank after BFFFh (how geo3d_modplay.asm reads the MOD)"""
    out = bytearray()
    while len(out) < n:
        k = min(n - len(out), 0xC000 - addr)
        out += rom[bank * BANK + addr - 0x8000:][:k]
        bank, addr = bank + 1, 0x8000
    return bytes(out)


def mod_check(rom, song):
    """The MOD's data as geo3d_modplay.asm reads it from the ROM image: the
    lookup tables (in one bank) and the MOD (on through the banks)."""
    (tb, ta), (mb, ma) = song.packed["tables"], song.packed["mod"]
    tab = song.tab_blob()
    assert (ta - 0x8000) + len(tab) <= BANK and rom_bytes(rom, tb, ta, len(tab)) == tab, "MOD player tables"
    assert rom_bytes(rom, mb, ma, len(song.mod)) == song.mod, "MOD"


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
