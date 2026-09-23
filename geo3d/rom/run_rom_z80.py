#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Runs GEO3D.ROM in a Z80 emulator (pip package z80) as an MSX would: the ROM
in page 1 with ASCII16 banks switched into page 2, RSLREG / ENASLT answered
like the BIOS, RAM in page 3, the V9968 status registers and the geo3d status
port simulated. Every OUT is recorded and decoded like vdp_cpu_interface.v
(the same decoder as z80/run_demo_z80.py), then compared, demo by demo, with
the traffic the streams were built from (rom/out/streams.json), which is the
traffic checked against the RTL.

Usage: run_rom_z80.py [space_at_flip]
  space_at_flip: also test the space bar, pressed at that page flip of the
  first demo (the player must move to the second demo).
"""
import json
import os
import sys

import z80

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
BANK = 16384

rom = open(os.path.join(OUT, "GEO3D.ROM"), "rb").read()
labels = {}
for line in open(os.path.join(OUT, "labels.txt")):
    p = line.replace(":", " ").split()
    if len(p) >= 3 and p[1] == "equ":
        labels[p[0]] = int(p[2].replace("$", ""), 16)
exp = json.load(open(os.path.join(OUT, "streams.json")))
SPACE_AT = int(sys.argv[1]) if len(sys.argv) > 1 else None

m = z80.Z80Machine()
m.set_memory_block(0x4000, rom[0:BANK])                # page 1: bank 0
m.set_memory_block(0x0024, b"\xC9")                     # ENASLT
m.set_memory_block(0x0138, b"\xC9")                     # RSLREG
m.set_memory_block(0xFCC1, bytes([0x00, 0x00, 0x00, 0x00, 0x00]))
m.sp = 0xF37D
m.pc = 0x4010                                           # INIT from the header
assert rom[0:2] == b"AB" and rom[2] | rom[3] << 8 == 0x4010

st = {"r15": 0, "pend": None, "s0": 0, "geo": 0, "flips": 0, "inits": 0,
      "page2": None, "space": False, "idx": None}
events = []           # ("G", sel, b) / ("RUN",) / ("P", port, b) / ("INIT",)


def on_out(port, v):
    p = port & 0xFF
    if p == 0x8D:
        events.append(("G", 0, v))
        st["idx"] = v
    elif p == 0x8F:
        events.append(("G", 1, v))
        if st["idx"] == 0x48 and v & 1:
            events.append(("RUN",))
            st["geo"] = 2
        if st["idx"] is not None and st["idx"] >> 4 == 4:
            st["idx"] = 0x40 | ((st["idx"] + 1) & 0xF)
    elif p in (0x88, 0x89, 0x8A, 0x8B):
        events.append(("P", p, v))
        if p == 0x89:
            if st["pend"] is None:
                st["pend"] = v
            else:
                if v & 0x80:
                    r = v & 0x3F
                    if r == 15:
                        st["r15"] = st["pend"] & 0x0F
                    if r == 2:
                        st["flips"] += 1
                st["pend"] = None
    elif p == 0x8C:
        events.append(("INIT",))
        st["inits"] += 1


def on_in(port):
    p = port & 0xFF
    if p == 0x8D:
        if st["geo"]:
            st["geo"] -= 1
            return 0x01
        return 0x00
    if p == 0x89:
        if st["r15"] == 0:                    # S#0: F set on every other read
            st["s0"] ^= 1
            return 0x80 if st["s0"] else 0x00
        if st["r15"] == 2:
            return 0x00                       # CE = 0
        return 0x00
    if p == 0xA9:                             # keyboard row 8, bit0 = space
        press = SPACE_AT is not None and st["inits"] == 1 and st["flips"] >= SPACE_AT + 1
        return 0xFE if press else 0xFF
    if p == 0xAA:
        return 0x00
    return 0xFF


m.set_output_callback(on_out)
m.set_input_callback(on_in)
for addr in (0x0024, 0x0138, labels["setbank2"]):
    m.set_breakpoint(addr)

ndemos = len(exp["demos"])
target_inits = (2 if SPACE_AT is not None else ndemos + 1)
while True:
    m.ticks_to_stop = 200000
    m.run()
    if m.halted:
        break
    if m.pc == 0x0024:                        # ENASLT: page 2 = our slot
        assert m.h & 0xC0 == 0x80
        m.step_over_breakpoint()
    elif m.pc == 0x0138:                      # RSLREG: slot 1 in pages 1 and 2
        m.a = 0b00010100
        m.step_over_breakpoint()
    elif m.pc == labels["setbank2"]:          # ASCII16 bank switch for page 2
        b = m.a
        m.set_memory_block(0x8000, rom[b * BANK:(b + 1) * BANK])
        st["page2"] = b
        m.step_over_breakpoint()
    if st["inits"] >= target_inits:
        break

# ---------------------------------------------------------------- decode
demos, cur = [], None
pend, r14, ptr, pinc, addr = None, 0, 0, True, 0


def reg_write(r, v):
    global r14, ptr, pinc
    if r == 14:
        r14 = v & 0x0F
    elif r == 17:
        ptr, pinc = v & 0x3F, not (v & 0x80)
    elif r == 2:
        cur.append("C")
        cur.append(f"D {((v >> 5) & 3) * 256}")
    elif 32 <= r <= 58:
        cur.append(f"V {r} {v:02x}")
        if r == 46:
            cur.append("C")


for e in events:
    if e[0] == "INIT":
        cur = []
        demos.append(cur)
    elif cur is None:
        continue
    elif e[0] == "G":
        cur.append(f"W {e[1]} {e[2]:02x}")
    elif e[0] == "RUN":
        cur.append("R")
    else:
        p, v = e[1], e[2]
        if p == 0x89:
            if pend is None:
                pend = v
            else:
                if v & 0x80:
                    reg_write(v & 0x3F, pend)
                else:
                    addr = (r14 << 14) | ((v & 0x3F) << 8) | pend
                pend = None
        elif p == 0x8B:
            reg_write(ptr, v)
            if pinc:
                ptr = (ptr + 1) & 0x3F
        elif p == 0x88:
            cur.append(f"X {addr:05x} {v:02x}")
            addr = (addr + 1) & 0x3FFFF

# every demo starts with demo_init: R#2 = page 0 (C, D 0), the full LRMM window
# (V 51..58) and the HMMV that clears pages 0 and 1 (V 36..46, C)
PREFIX = (["C", "D 0"] + [f"V {51 + i} {v:02x}" for i, v in enumerate([0, 0, 0, 0, 0xFF, 1, 0xFF, 7])]
          + [f"V {36 + i} {v:02x}" for i, v in enumerate([0, 0, 0, 0, 0, 1, 0, 2, 0, 0, 0xC0])] + ["C"])

ok = True
check = exp["demos"][:len(demos) - 1] if SPACE_AT is None else exp["demos"][:1]
for i, name in enumerate(check):
    got = demos[i]
    if got[:len(PREFIX)] != PREFIX:
        print(f"{name}: prefixo de inicialização inesperado: {got[:6]}")
        ok = False
        continue
    got = got[len(PREFIX):]
    want = exp["expect"][name]
    if SPACE_AT is not None:
        # interrupted: must be an exact prefix of the demo, ending at a flip
        n = len(got)
        good = got == want[:n] and got[-1].startswith("D") and n < len(want)
        flips = sum(1 for g in got if g.startswith("D"))
        print(f"{name}: barra de espaço na troca {SPACE_AT}: parou após {flips} trocas de página, "
              f"tráfego = prefixo exato do fluxo: {good}; passou ao demo 2: {len(demos) == 2}")
        ok = ok and good and len(demos) == 2 and flips == SPACE_AT
        continue
    same = got == want
    nx = sum(1 for g in want if g[0] == "X")
    nw = sum(1 for g in want if g[0] == "W")
    nd = sum(1 for g in want if g[0] == "D")
    print(f"{name:8s}: {'idêntico' if same else 'DIFERENTE'} ao tráfego verificado  "
          f"({nw} escritas geo3d, {nx} bytes de VRAM, {nd} quadros)")
    if not same:
        k = next((j for j in range(min(len(got), len(want))) if got[j] != want[j]), min(len(got), len(want)))
        print(f"   primeira diferença na posição {k}: ROM={got[k:k + 3]} esperado={want[k:k + 3]}")
        ok = False
if SPACE_AT is None:
    print(f"sequência: {len(demos) - 1} demos e o recomeço do primeiro "
          f"({'ok' if len(demos) == ndemos + 1 else 'FALHOU'})")
    ok = ok and len(demos) == ndemos + 1
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
