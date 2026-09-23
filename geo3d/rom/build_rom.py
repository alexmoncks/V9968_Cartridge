#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Builds GEO3D.ROM (ASCII16 MegaROM): the player (geo3d_rom.asm) in bank 0 and
one command stream per demo in the following banks.

Demos, in order (each one is the port traffic already checked against the RTL):
  1. crawl    perspective text crawl (showcase)
  2. wire     wireframe cube + octahedron (z80/geo3d_demo.asm, captured)
  3. faces    GEO3D solid blocks, shaded (z80/geo3d_faces_demo.asm, captured)
  4. tex      GEO3D textured, spinning (z80/geo3d_tex_demo.asm, captured)
  5. panzoom  camera pan and zoom (showcase)
  6. flyin    letters fly in over a SCREEN 5 background, then the turn (showcase)

The .COM demos are run in the Z80 emulator (z80/run_demo_z80.py) and their
decoded traffic becomes the stream; the showcase scenes come straight from
showcase/showcase.py. Writes rom/out/GEO3D.ROM and rom/out/streams.json (the
expected traffic per demo, used by run_rom_z80.py).

Usage: build_rom.py [--base 0x88|0x98]
  --base 0x88  (default) real hardware, V9968 cartridge at 88h: GEO3D.ROM
  --base 0x98  emulator profile, V9968 as the machine's VDP and geo3d on
               9Dh/9Fh (openMSX fork, -ext geo3d): GEO3D_98.ROM
  The streams are the same for both; only the player's ports change.
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

import showcase  # noqa: E402
from gen_face_tables import palette as logo_palette  # noqa: E402

BANK = 16384
LOOPS = {"wire": 2, "faces": 2, "tex": 2, "panzoom": 2, "flyin": 2}

OP_END, OP_GEO, OP_GEOD, OP_VREG, OP_VIND, OP_WAITGEO, OP_WAITCE = range(7)
OP_VRLE, OP_FLIP, OP_MARK, OP_LOOP, OP_NEXTBANK = range(7, 12)


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

    def put(self, op):
        assert len(op) < BANK - 1, len(op)
        if len(self.banks[-1]) + len(op) > BANK - 1:
            self.banks[-1].append(OP_NEXTBANK)
            self.banks.append(bytearray())
        self.banks[-1].extend(op)


PROFILES = {0x88: "GEO3D.ROM", 0x98: "GEO3D_98.ROM"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", type=lambda x: int(x, 0), default=0x88, choices=sorted(PROFILES))
    base = ap.parse_args().base
    rom_name = PROFILES[base]
    suffix = "" if base == 0x88 else f"_{base:02x}"
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(HERE, "rom_ports.asm"), "w").write(
        f"; generated by build_rom.py, do not edit\nPORT_BASE:  equ 0x{base:02x}\n")
    logo_pal = logo_palette()[1]
    demos = []

    sh, pal, _, _ = showcase.scene_crawl()
    s, b = from_show(sh)
    crawl_pal = []
    for r, g, bl in pal:
        crawl_pal += [(r << 4) | bl, g]
    demos.append(("crawl", crawl_pal, s + b, [], 1))
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
        expect[name] = expand(setup) + expand(body) * loops
    nb = 1 + len(pk.banks)
    size = 1 << (nb - 1).bit_length() if nb > 1 else 1
    lines = ["; generated by build_rom.py, do not edit", "demo_table:"]
    for (name, bank, addr) in table:
        lines.append(f"\tdb {bank}\n\tdw 0x{addr:04x}, pal_{name}\t; {name}")
    lines.append("\tdb 0xFF")
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
    json.dump({"demos": [d[0] for d in demos], "expect": expect,
               "table": table}, open(os.path.join(OUT, "streams.json"), "w"))
    used = sum(len(bk) for bk in pk.banks)
    print(f"{rom_name} (portas {base:02X}h): {len(rom) // 1024} KB (ASCII16, {size} bancos de 16 KB), "
          f"player {len(bank0)} bytes, fluxos {used // 1024} KB em {len(pk.banks)} bancos")
    for (name, bank, addr) in table:
        print(f"  {name:8s} banco {bank:3d} 0x{addr:04x}")


def expand(items):
    """items -> the traffic a decoder sees (run_demo_z80.py SYS format, no F)."""
    out = []
    for it in items:
        k = it[0]
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
            out += ["C", f"D {it[1]}"]
    return out


if __name__ == "__main__":
    main()
