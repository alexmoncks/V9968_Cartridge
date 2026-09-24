#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Runs GEO3D.ROM in a Z80 emulator (pip package z80) as an MSX would: the ROM
in page 1 with ASCII16 banks switched into page 2, RSLREG / ENASLT answered
like the BIOS, RAM in page 3, the V9968 status registers, the geo3d status
port and the keyboard matrix simulated. Every OUT is recorded and decoded like
vdp_cpu_interface.v (the same decoder as z80/run_demo_z80.py), then compared,
demo by demo, with the traffic the streams were built from
(rom/out/streams.json), which is the traffic checked against the RTL.

The ROM starts with the language menu. The keyboard is scripted in frames
(vertical blanks the player waits for, counted from the menu on): SPACE is
already down when the menu appears and must be ignored, then the language is
chosen with --keys:
  digit  its number key (1, 2, 3)
  down   cursor down from English, then SPACE (RETURN for Español)
  up     cursor up from English (wraps around), then SPACE
Cursor keys are held 3 frames (a cursor that moved every frame while held
would come back to where it was), and the choosing key is still held when the
menu can already leave (the player must wait until it is up, or SPACE would
also skip the first demo). Checked: the menu uploads exactly its picture; its
highlight (palette entries 5..10) follows the keys; the demos that follow are
the chosen language's table, each page flip after exactly its PACE of vertical
blanks; the sequence then restarts with that same table (the player's
cur_demo, read from RAM). With a ROM built with --music, the PSG target's
music (there is no OPL or SCC here) is checked tick by tick against the
converter: tick k's PSG writes at the (k+1)-th vertical blank of the crawl,
then only the player's mute after the last tick.

Usage: run_rom_z80.py [--base 0x88|0x98] [--lang en|es|pt] [--keys digit|down|up]
                      [space_at_flip]
  --base: port profile, 0x88 (default, GEO3D.ROM) or 0x98 (GEO3D_98.ROM, the
  emulator profile); build it first with build_rom.py --base.
  space_at_flip: also test the space bar, pressed at that page flip of the
  first demo (the player must move to the second demo).
"""
import argparse
import json
import os
import sys

import z80

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
BANK = 16384

ap = argparse.ArgumentParser()
ap.add_argument("--base", type=lambda x: int(x, 0), default=0x88, choices=(0x88, 0x98))
ap.add_argument("--lang", default="en", choices=("en", "es", "pt"))
ap.add_argument("--keys", default="digit", choices=("digit", "down", "up"))
ap.add_argument("--chip", default="psg", choices=("psg", "scc", "opl"),
                help="music target: after power-on detection (which finds nothing here) the "
                     "harness sets the player's target and emulates that chip")
ap.add_argument("--opl4", action="store_true", help="with --chip opl: an OPL4 (F-number correction)")
ap.add_argument("space_at", nargs="?", type=int)
opt = ap.parse_args()
BASE, SPACE_AT = opt.base, opt.space_at
SUFFIX = "" if BASE == 0x88 else f"_{BASE:02x}"
P_DATA, P_CTRL, P_PAL, P_IND, P_PORT4 = (BASE + i for i in range(5))
P_GIDX, P_GDAT = BASE + 5, BASE + 7

rom = open(os.path.join(OUT, "GEO3D.ROM" if BASE == 0x88 else f"GEO3D_{BASE:02X}.ROM"), "rb").read()
labels = {}
for line in open(os.path.join(OUT, f"labels{SUFFIX}.txt")):
    p = line.replace(":", " ").split()
    if len(p) >= 3 and p[1] == "equ":
        labels[p[0]] = int(p[2].replace("$", ""), 16)
exp = json.load(open(os.path.join(OUT, "streams.json")))
LANG_IDX = exp["langs"].index(opt.lang)
TABLE = exp["tables"][opt.lang]

# keyboard matrix: key -> (row, bit)
KEYS = {"1": (0, 1), "2": (0, 2), "3": (0, 3), "ret": (7, 7),
        "space": (8, 0), "up": (8, 5), "down": (8, 6)}
# menu script: (key, first frame down, first frame up)
script = [("space", 0, 3)]                  # held when the menu appears: ignored
sels = [0]                                  # expected highlight after each repaint
if opt.keys == "digit":
    script.append((str(LANG_IDX + 1), 5, 11))
    sels.append(LANG_IDX)
else:
    step, n = ("down", LANG_IDX) if opt.keys == "down" else ("up", (3 - LANG_IDX) % 3)
    for i in range(n):
        script.append((step, 5 + 8 * i, 8 + 8 * i))
        sels.append((sels[-1] + (1 if step == "down" else 2)) % 3)
    script.append(("ret" if opt.lang == "es" else "space", 5 + 8 * n, 11 + 8 * n))
MENU_LIMIT = 400                            # frames: a stuck menu is a failure
MENU_STEPS = 20000                          # emulator runs: a menu that stops reading S#0

m = z80.Z80Machine()
m.set_memory_block(0x4000, rom[0:BANK])                # page 1: bank 0
m.set_memory_block(0x0024, b"\xC9")                     # ENASLT
m.set_memory_block(0x0138, b"\xC9")                     # RSLREG
m.set_memory_block(0xFCC1, bytes([0x00, 0x00, 0x00, 0x00, 0x00]))
m.sp = 0xF37D
m.pc = 0x4010                                           # INIT from the header
assert rom[0:2] == b"AB" and rom[2] | rom[3] << 8 == 0x4010

st = {"r15": 0, "pend": None, "s0": 0, "geo": 0, "flips": 0, "inits": 0,
      "page2": None, "idx": None, "row": 0, "tick": 0, "blanks": 0, "fdemo": 0, "psg_reg": 0,
      "forced": False, "opl_reg": [0, 0], "in_scc": False, "scc_mem": bytearray(256),
      "scc_9000": 0xFF, "page2_save": None}
SCC_SLOT = 0x02                             # where the emulated SCC sits (primary slot 2)
TARGET = {"psg": 0, "scc": 1, "opl": 2}[opt.chip]
events = []           # ("G", sel, b) / ("RUN",) / ("P", port, b, blanks) / ("INIT",) /
                      # ("S", psg register, value, vertical blanks since the demo began)


def keys_down():
    down = set()
    if st["inits"] >= 1:                                # the script runs on after the menu
        down = {k for k, a, b in script if a <= st["tick"] < b}
    if st["inits"] == 2 and SPACE_AT is not None and st["flips"] >= SPACE_AT + 1:
        down.add("space")
    return down


def on_out(port, v):
    p = port & 0xFF
    if p == P_GIDX:
        events.append(("G", 0, v))
        st["idx"] = v
    elif p == P_GDAT:
        events.append(("G", 1, v))
        if st["idx"] == 0x48 and v & 1:
            events.append(("RUN",))
            st["geo"] = 2
        if st["idx"] is not None and st["idx"] >> 4 == 4:
            st["idx"] = 0x40 | ((st["idx"] + 1) & 0xF)
    elif p in (P_DATA, P_CTRL, P_PAL, P_IND):
        events.append(("P", p, v, st["blanks"]))
        if p == P_CTRL:
            if st["pend"] is None:
                st["pend"] = v
            else:
                if v & 0x80:
                    r = v & 0x3F
                    if r == 15:
                        st["r15"] = st["pend"] & 0x0F
                    if r == 2:
                        st["flips"] += 1
                        st["blanks"] = 0          # blanks are counted from flip to flip
                        events.append(("FLIP", st["fdemo"]))
                st["pend"] = None
    elif p == 0xA0:                           # PSG register select
        st["psg_reg"] = v & 0x0F
    elif p == 0xA1:                           # PSG data
        events.append(("S", st["psg_reg"], v, st["fdemo"]))
    elif p in (0xC4, 0xC6):                   # OPL address, bank 0 / 1
        st["opl_reg"][(p >> 1) & 1] = v
    elif p in (0xC5, 0xC7):                   # OPL data
        bank = (p >> 1) & 1
        events.append(("O", bank, st["opl_reg"][bank], v, st["fdemo"]))
    elif p == P_PORT4:
        events.append(("INIT",))
        st["inits"] += 1
        st["flips"] = 0
        st["fdemo"] = 0
        if st["inits"] == 2:
            st["chosen"] = st["tick"]
    elif p == 0xAA:                           # PPI port C: keyboard row
        st["row"] = v & 0x0F


def on_in(port):
    p = port & 0xFF
    if p == P_GIDX:
        if st["geo"]:
            st["geo"] -= 1
            return 0x01
        return 0x00
    if p == P_CTRL:
        if st["r15"] == 0:                    # S#0: F set on every other read
            st["s0"] ^= 1
            if st["s0"]:
                st["blanks"] += 1
                st["fdemo"] += 1              # the player runs one music tick per blank it sees
                if st["inits"] >= 1:
                    st["tick"] += 1           # frames since the menu appeared
            return 0x80 if st["s0"] else 0x00
        if st["r15"] == 2:
            return 0x00                       # CE = 0
        return 0x00
    if p == 0xA9:                             # keyboard columns, 0 = down
        v = 0xFF
        for k in keys_down():
            row, bit = KEYS[k]
            if row == st["row"]:
                v &= ~(1 << bit)
        return v & 0xFF
    if p == 0xAA:
        return 0x00
    if p == 0xC4 and st["forced"] and opt.chip == "opl":
        return 0x00                           # OPL status: not BUSY
    return 0xFF


m.set_output_callback(on_out)
m.set_input_callback(on_in)
for addr in (0x0024, 0x0138, labels["bank2_raw"], labels["psg_silence"]):
    m.set_breakpoint(addr)


def enaslt_page2(slot):
    """ENASLT for page 2. With --chip scc, switching to SCC_SLOT maps an SCC
    register image (9000h bank register, 9800h-98FFh) into page 2; switching
    back records the SCC bytes that changed, then restores our ROM page."""
    if not (st["forced"] and opt.chip == "scc"):
        return
    if slot == SCC_SLOT and not st["in_scc"]:
        st["page2_save"] = bytes(m.memory[0x8000:0xC000])
        img = bytearray(b"\xFF" * 0x4000)
        img[0x1000] = st["scc_9000"]
        img[0x1800:0x1900] = st["scc_mem"]
        m.set_memory_block(0x8000, bytes(img))
        st["in_scc"] = True
    elif slot != SCC_SLOT and st["in_scc"]:
        now = bytes(m.memory[0x9800:0x9900])
        st["scc_9000"] = m.memory[0x9000]
        changed = tuple((i, now[i]) for i in range(256) if now[i] != st["scc_mem"][i])
        events.append(("C", changed, st["scc_9000"], st["fdemo"]))
        st["scc_mem"] = bytearray(now)
        m.set_memory_block(0x8000, st["page2_save"])
        st["in_scc"] = False

ndemos = len(TABLE)
target_inits = 1 + (2 if SPACE_AT is not None else ndemos + 1)
stuck = False
menu_steps = 0
while True:
    m.ticks_to_stop = 200000
    m.run()
    if st["inits"] == 1:
        menu_steps += 1
    if m.halted:
        break
    if m.pc == 0x0024:                        # ENASLT (always page 2)
        assert m.h & 0xC0 == 0x80
        enaslt_page2(m.a)
        m.step_over_breakpoint()
    elif m.pc == labels["psg_silence"]:
        if not st["forced"]:                  # right after power-on detection
            assert m.memory[labels["mus_target"]] == 0, "detection found a chip in the harness"
            if TARGET:
                m.set_memory_block(labels["mus_target"], bytes([TARGET]))
                m.set_memory_block(labels["scc_slot"], bytes([SCC_SLOT if TARGET == 1 else 0xFF]))
                m.set_memory_block(labels["opl4"], bytes([1 if opt.opl4 else 0]))
            st["forced"] = True
        m.step_over_breakpoint()
    elif m.pc == 0x0138:                      # RSLREG: slot 1 in pages 1 and 2
        m.a = 0b00010100
        m.step_over_breakpoint()
    elif m.pc == labels["bank2_raw"]:          # ASCII16 bank switch for page 2 (streams and music)
        b = m.a
        m.set_memory_block(0x8000, rom[b * BANK:(b + 1) * BANK])
        st["page2"] = b
        m.step_over_breakpoint()
    if st["inits"] >= target_inits:
        break
    if st["inits"] == 1 and (st["tick"] > MENU_LIMIT or menu_steps > MENU_STEPS):
        stuck = True
        break
# the player's current table entry when the run stopped (the restart, or the
# demo after SPACE)
cur_demo = m.memory[labels["cur_demo"]] | m.memory[labels["cur_demo"] + 1] << 8

# ---------------------------------------------------------------- decode
demos, cur = [], None
pend, r14, ptr, pinc, addr = None, 0, 0, True, 0
pal, pal_idx, pal_first = {}, 0, None       # palette as written (R#16, port PAL)
menu_paints = []                            # entries 5..10 each time entry 10 is written


def reg_write(r, v, blanks):
    global r14, ptr, pinc, pal_idx, pal_first
    if r == 14:
        r14 = v & 0x0F
    elif r == 16:
        pal_idx, pal_first = v & 0x0F, None
    elif r == 17:
        ptr, pinc = v & 0x3F, not (v & 0x80)
    elif r == 2:
        cur.append("C")
        page = ((v >> 5) & 3) * 256
        # the first R#2 of a demo is demo_init's; the flips carry their blanks
        cur.append(f"D {page} /{blanks}" if any(g.startswith("D") for g in cur) else f"D {page}")
    elif 32 <= r <= 58:
        cur.append(f"V {r} {v:02x}")
        if r == 46:
            cur.append("C")


demo_snd = []                               # per demo: (blank, kind, data) of sound chip writes
late_flips = []                             # (demo, blank): a sound write of that blank before its flip
for e in events:
    if e[0] == "INIT":
        cur = []
        demos.append(cur)
        demo_snd.append([])
    elif cur is None:
        continue
    elif e[0] == "FLIP":
        # the flip must come right at the blank, before that blank's music tick
        if e[1] > 0 and any(f == e[1] for f, _, _ in demo_snd[-1]):
            late_flips.append((len(demos) - 1, e[1]))
    elif e[0] == "S":
        demo_snd[-1].append((e[3], "psg", (e[1], e[2])))
    elif e[0] == "O":
        demo_snd[-1].append((e[4], "opl", (e[1], e[2], e[3])))
    elif e[0] == "C":
        demo_snd[-1].append((e[3], "scc", (e[1], e[2])))
    elif e[0] == "G":
        cur.append(f"W {e[1]} {e[2]:02x}")
    elif e[0] == "RUN":
        cur.append("R")
    else:
        p, v, blanks = e[1], e[2], e[3]
        if p == P_CTRL:
            if pend is None:
                pend = v
            else:
                if v & 0x80:
                    reg_write(v & 0x3F, pend, blanks)
                else:
                    addr = (r14 << 14) | ((v & 0x3F) << 8) | pend
                pend = None
        elif p == P_IND:
            reg_write(ptr, v, blanks)
            if pinc:
                ptr = (ptr + 1) & 0x3F
        elif p == P_PAL:
            if pal_first is None:
                pal_first = v
            else:
                pal[pal_idx] = (pal_first, v)
                if pal_idx == 10 and len(demos) == 1:
                    menu_paints.append([pal.get(i) for i in range(5, 11)])
                pal_idx, pal_first = (pal_idx + 1) & 15, None
        elif p == P_DATA:
            cur.append(f"X {addr:05x} {v:02x}")
            addr = (addr + 1) & 0x3FFFF

# every demo starts with demo_init: R#2 = page 0 (C, D 0), the full LRMM window
# (V 51..58) and the HMMV that clears pages 0 and 1 (V 36..46, C)
PREFIX = (["C", "D 0"] + [f"V {51 + i} {v:02x}" for i, v in enumerate([0, 0, 0, 0, 0xFF, 1, 0xFF, 7])]
          + [f"V {36 + i} {v:02x}" for i, v in enumerate([0, 0, 0, 0, 0, 1, 0, 2, 0, 0, 0xC0])] + ["C"])

ok = not stuck
keys_txt = ", ".join(f"{k} {a}-{b}" for k, a, b in script)
if stuck:
    print(f"menu: nenhuma escolha em {MENU_LIMIT} quadros (teclas: {keys_txt})")
elif not demos or demos[0][:len(PREFIX)] != PREFIX or demos[0][len(PREFIX):] != exp["expect"]["menu"]:
    print("menu: tráfego diferente da imagem do menu")
    ok = False
else:
    nx = len(exp["expect"]["menu"])
    print(f"menu    : idêntico ({nx} bytes de VRAM); teclas por quadro: {keys_txt}; "
          f"escolha no quadro {st.get('chosen')} do menu")
# highlight: text entries 5..7 and arrows 8..10; demo_init first loads the
# picture's palette (nothing highlighted), then one repaint per selection
ON, TXT_OFF, ARR_OFF = (0x71, 0x06), (0x34, 0x03), (0x00, 0x00)


def paint(sel):
    if sel is None:
        return [TXT_OFF] * 3 + [ARR_OFF] * 3
    return ([ON if i == sel else TXT_OFF for i in range(3)]
            + [ON if i == sel else ARR_OFF for i in range(3)])


if not stuck:
    want_paints = [paint(None)] + [paint(s) for s in sels]
    good = menu_paints == want_paints
    print(f"destaque : seleções {sels} -> paleta {'idêntica' if good else 'DIFERENTE'} "
          f"({len(menu_paints)} pinturas)")
    if not good:
        print(f"   esperado {want_paints}\n   obtido   {menu_paints}")
    ok = ok and good
played = demos[1:]
# music: from the crawl's first blank, tick k of the converter's data at blank
# k + 1, register by register on every chip of the target (PSG writes, SCC
# bytes that change, OPL writes with key on expanded as the player does it:
# A0, carrier TL, B0); after the last tick only the player's mute, once; the
# later demos only mute at their demo_init.
MUTE_PSG = [(7, 0xBF), (8, 0), (9, 0), (10, 0)]
CAR = [0x03, 0x04, 0x05, 0x0B, 0x0C, 0x0D, 0x13, 0x14, 0x15]
MUTE_OPL = [(c // 9, 0xB0 + c % 9, 0) for c in range(18)]


def opl4_tune(lo, b):
    s = lo + (b & 3)
    if s < 256:
        return s, b
    return (0xFF, b) if b & 3 == 3 else (s & 0xFF, b + 1)


def expected_music(ticks):
    b0, state, out = [0] * 18, bytearray(256), []
    for ops in ticks:
        psg, sccw, opl = [], {}, []
        for o in ops:
            op = o[0]
            if op <= 0x0D:
                psg.append((op, o[1]))
            elif 0x10 <= op <= 0x1F:
                sccw[0x80 + op - 0x10] = o[1]
            elif 0x20 <= op <= 0x23:
                for i in range(32):
                    sccw[(op - 0x20) * 32 + i] = o[1 + i]
            elif op in (0x30, 0x31):
                opl.append((op - 0x30, o[1], o[2]))
            elif 0x40 <= op <= 0x51:
                c = op - 0x40
                lo, b = opl4_tune(o[1], o[2]) if opt.opl4 else (o[1], o[2])
                opl += [(c // 9, 0xA0 + c % 9, lo), (c // 9, 0x40 + CAR[c % 9], o[3]), (c // 9, 0xB0 + c % 9, b)]
                b0[c] = b
            elif 0x60 <= op <= 0x71:
                c = op - 0x60
                b0[c] &= 0xDF
                opl.append((c // 9, 0xB0 + c % 9, b0[c]))
        scc = tuple((k, v) for k, v in sorted(sccw.items()) if state[k] != v)
        for k, v in sccw.items():
            state[k] = v
        out.append((psg, scc, opl))
    return out, state


def sound_at(demo_i):
    """Per blank: (psg writes, scc changes, opl writes, scc bank register values)."""
    by = {}
    for f, kind, data in demo_snd[demo_i]:
        e = by.setdefault(f, ([], [], [], []))
        if kind == "psg":
            e[0].append(data)
        elif kind == "opl":
            e[2].append(data)
        else:
            e[1].extend(data[0])
            e[3].append(data[1])
    return by


mus = exp.get("music")
if mus and len(played) >= 1 and not stuck:
    want, scc_state = expected_music(mus[opt.chip])
    got = sound_at(1)
    blanks_in = sum(int(g.split("/")[1]) for g in played[0] if g.startswith("D") and "/" in g)
    nt = len(want)
    heard = min(nt, blanks_in)
    bad = [k for k in range(heard)
           if tuple(got.get(k + 1, ([], [], [], []))[:3]) != (want[k][0], list(want[k][1]), want[k][2])]
    mute = (MUTE_PSG, [(0x8F, 0)] if scc_state[0x8F] else [], MUTE_OPL if opt.chip == "opl" else [])
    tail = {f: v[:3] for f, v in got.items() if f > nt}
    tail_ok = not tail or (list(tail) == [nt + 1] and tuple(tail[nt + 1]) == mute)
    scc_on = all(x == 0x3F for f, v in got.items() if 1 <= f <= heard for x in v[3])
    full = SPACE_AT is not None or blanks_in >= nt
    # later demos: exactly the mute at their demo_init (blank 0), nothing else
    # (the last one is cut at its INIT, before its demo_init mutes)
    later_ok = True
    for i in range(2, len(demos) - 1):
        s = sound_at(i)
        z = s.get(0, ([], [], [], []))
        if set(s) != {0} or z[0] != MUTE_PSG or z[2] != (MUTE_OPL if opt.chip == "opl" else []) \
                or any(c != (0x8F, 0) for c in z[1]):
            later_ok = False
    good = not bad and tail_ok and full and later_ok and scc_on and not late_flips
    if late_flips:
        print(f"   {len(late_flips)} trocas de página depois do tick de música do mesmo retraço, "
              f"a primeira: demo {late_flips[0][0]}, retraço {late_flips[0][1]}")
    print(f"música  : {opt.chip}{' (OPL4)' if opt.opl4 else ''}: {heard} de {nt} ticks conferidos no {TABLE[0]}"
          f"{' (interrompido pela barra de espaço)' if SPACE_AT is not None else ''}: "
          f"{'idêntica' if not bad and tail_ok and full and scc_on else 'DIFERENTE'}; "
          f"silêncio nos demos seguintes: {'ok' if later_ok else 'FALHOU'}")
    if bad:
        k = bad[0]
        g = got.get(k + 1, ([], [], [], []))
        print(f"   primeiro tick diferente: {k}: ROM psg={g[0][:6]} scc={g[1][:6]} opl={g[2][:6]}")
        print(f"                           esperado psg={want[k][0][:6]} scc={list(want[k][1])[:6]} opl={want[k][2][:6]}")
    if not tail_ok:
        print(f"   escritas depois do fim: {dict(list(tail.items())[:2])}")
    if not scc_on:
        print("   o SCC não estava ligado (9000h = 3Fh) em todos os ticks")
    ok = ok and good
check = TABLE[:len(played) - 1] if SPACE_AT is None else TABLE[:1]
for i, name in enumerate(check):
    got = played[i]
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
              f"tráfego = prefixo exato do fluxo: {good}; passou ao demo 2: {len(played) == 2}")
        ok = ok and good and len(played) == 2 and flips == SPACE_AT
        continue
    same = got == want
    nx = sum(1 for g in want if g[0] == "X")
    nw = sum(1 for g in want if g[0] == "W")
    nd = sum(1 for g in want if g[0] == "D")
    nb = sum(int(g.split("/")[1]) for g in want if g[0] == "D")
    print(f"{name:8s}: {'idêntico' if same else 'DIFERENTE'} ao tráfego verificado  "
          f"({nw} escritas geo3d, {nx} bytes de VRAM, {nd} quadros em {nb} vblanks)")
    if not same:
        k = next((j for j in range(min(len(got), len(want))) if got[j] != want[j]), min(len(got), len(want)))
        print(f"   primeira diferença na posição {k}: ROM={got[k:k + 3]} esperado={want[k:k + 3]}")
        ok = False
if not stuck:
    # which table entry the player went on to: the restart (first entry of the
    # chosen table) or, after SPACE, the second one
    table_at = labels[f"demo_table_{opt.lang}"]
    want_at, what = (table_at, "recomeço") if SPACE_AT is None else (table_at + 5, "demo seguinte")
    good_at = cur_demo == want_at
    print(f"{what}: cur_demo = {cur_demo:04x}, esperado {want_at:04x} "
          f"({TABLE[0] if SPACE_AT is None else TABLE[1]}): {'ok' if good_at else 'FALHOU'}")
    ok = ok and good_at
if SPACE_AT is None and not stuck:
    print(f"sequência ({opt.lang}): {len(played) - 1} demos e o recomeço do primeiro "
          f"({'ok' if len(played) == ndemos + 1 else 'FALHOU'})")
    ok = ok and len(played) == ndemos + 1
print(f"portas {BASE:02X}h, {opt.lang}, teclas {opt.keys}: " + ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
