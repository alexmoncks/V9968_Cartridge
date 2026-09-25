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

--moonsound KB emulates a MoonSound (OPL4, opl4emu.py) with KB of sample
RAM, and real time: S#0 shows a vertical blank every 59736 clock cycles (an
NTSC frame) of the MSX's 3.58 MHz clock: the Z80's T-states plus the MSX's
wait state on every M1 cycle (z80clock.py; --no-m1: without it, the Z80
~10-15 % fast, as earlier reports had it), geo3d and the command engine
still answer at once. The chip is not forced: the ROM's own detection runs against the
emulated FM part (the OPL3 timers and status, NEW2 at FM register 105h),
the wave registers (device ID, memory address, memory data, tone loads with
LD for 295 us, as openMSX) and its sample RAM; the MOD player's part of it
runs during the menu, and what it found is read when the menu ends. With a ROM built with a MOD
and enough sample RAM, the MOD player must play the MOD: the sample RAM it
fills (tone headers and samples) exactly modplay.py's model of it, finished
before the song starts; then, from mod_start on, each tick's wave register
writes (between two clears of timer 2's flag) exactly the model's, the
timer 2 periods exactly its dithered ones, every tick played before the
next one is due (none lost), no tone number written while a header loads,
every tone loaded a RAM tone whose header points into the samples, and
nothing after the stop. A MOD that loops (the demo ROM's) never stops: it
plays on through every demo (their PSG only muted at each demo_init), its
passes counted; no frame paced by PACE takes more vertical blanks than the
player counted (the black screens and the upload frames, whose length the
upload sets, are reported: compare them with the reference ROM's).
It reports the tick and key-on latencies behind the timer and the longest
gaps between two status reads (polls) while the song plays, per demo. With
too little sample RAM it must play the FM fallback (checked as --chip opl
--opl4).

--cycles N runs the demo sequence N times (each crawl plays its music from
its start; with a looping MOD: its passes over them).

Usage: run_rom_z80.py [--base 0x88|0x98] [--lang en|es|pt] [--keys digit|down|up]
                      [--chip psg|scc|opl] [--opl4] [--moonsound KB] [--timed] [--cycles N]
                      [--late N] [--out DIR] [--trace FILE] [space_at_flip]
  --base: port profile, 0x88 (default, GEO3D.ROM) or 0x98 (GEO3D_98.ROM, the
  emulator profile); build it first with build_rom.py --base.
  --timed: real time as with --moonsound, without one (for timing reports).
  --late: the menu keys N frames later (with a MoonSound: the samples are
  all up before the choice).
  --out: the build's out directory (default rom/out).
  --trace: every IN and OUT of the run, in order, 3 bytes each ('I' or 'O',
  port, value): two ROMs with the same traces send the same port traffic
  (the harness answers S#0 per read, so the trace does not depend on speed).
  space_at_flip: also test the space bar, pressed at that page flip of the
  first demo (the player must move to the second demo).
"""
import argparse
import hashlib
import json
import os
import sys
from array import array

import z80

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import opl4emu  # noqa: E402
import z80clock  # noqa: E402
BANK = 16384

ap = argparse.ArgumentParser()
ap.add_argument("--base", type=lambda x: int(x, 0), default=0x88, choices=(0x88, 0x98))
ap.add_argument("--lang", default="en", choices=("en", "es", "pt"))
ap.add_argument("--keys", default="digit", choices=("digit", "down", "up"))
ap.add_argument("--chip", default="psg", choices=("psg", "scc", "opl"),
                help="music target: after power-on detection (which finds nothing here) the "
                     "harness sets the player's target and emulates that chip")
ap.add_argument("--opl4", action="store_true", help="with --chip opl: an OPL4 (F-number correction)")
ap.add_argument("--moonsound", type=int, metavar="KB",
                help="a MoonSound with KB of sample RAM (detection not forced; real time)")
ap.add_argument("--timed", action="store_true", help="real time: a vertical blank every 59736 T")
ap.add_argument("--no-m1", action="store_true", help="real time without the MSX's M1 wait (the Z80 a "
                                                     "little fast, as before z80clock.py)")
ap.add_argument("--late", type=int, default=0, help="the menu keys this many frames later")
ap.add_argument("--out", default=os.path.join(HERE, "out"), help="the build's out directory")
ap.add_argument("--trace", help="write every IN and OUT to this file ('I'/'O', port, value)")
ap.add_argument("--cycles", type=int, default=1,
                help="the demo sequence this many times (with a looping MOD: its passes over them)")
ap.add_argument("space_at", nargs="?", type=int)
opt = ap.parse_args()
OUT = opt.out
BASE, SPACE_AT = opt.base, opt.space_at
MS = opt.moonsound is not None
TIMED = opt.timed or MS
SUFFIX = "" if BASE == 0x88 else f"_{BASE:02x}"
P_DATA, P_CTRL, P_PAL, P_IND, P_PORT4 = (BASE + i for i in range(5))
P_GIDX, P_GDAT = BASE + 5, BASE + 7
T_HZ = 3579545                              # the Z80's clock
FRAME_T = 1368 * 262 / 6                    # T-states per NTSC frame (59.92 Hz)
RUN = 200000                                # T-states between two looks at the run's state

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
L = opt.late
if opt.keys == "digit":
    script.append((str(LANG_IDX + 1), L + 5, L + 11))
    sels.append(LANG_IDX)
else:
    step, n = ("down", LANG_IDX) if opt.keys == "down" else ("up", (3 - LANG_IDX) % 3)
    for i in range(n):
        script.append((step, L + 5 + 8 * i, L + 8 + 8 * i))
        sels.append((sels[-1] + (1 if step == "down" else 2)) % 3)
    script.append(("ret" if opt.lang == "es" else "space", L + 5 + 8 * n, L + 11 + 8 * n))
MENU_LIMIT = 400 + L                        # frames: a stuck menu is a failure
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
      "scc_9000": 0xFF, "page2_save": None, "vb": 0, "vb_flip": 0}
SCC_SLOT = 0x02                             # where the emulated SCC sits (primary slot 2)
TARGET = {"psg": 0, "scc": 1, "opl": 2}[opt.chip]
events = []           # ("G", sel, b) / ("RUN",) / ("P", port, b, blanks) / ("INIT",) /
                      # ("S", psg register, value, vertical blanks since the demo began)
trace = []            # --trace: b"O" / b"I", port, value
flip_t = []           # --timed: (demo, flip, T-state, real vertical blanks since the last flip,
                      # blanks the player counted)


clock = z80clock.MsxClock(m, not opt.no_m1)


def now():
    """The MSX's clock cycles since power on: T-states and M1 waits (inside
    an instruction: at its I/O)."""
    return clock.now()


# ------------------------------------------------------------ MoonSound
T2_UNIT = opl4emu.T2_UNIT
ms = opl4emu.MoonSound(opt.moonsound) if MS else None


def keys_down():
    down = set()
    if st["inits"] >= 1:                                # the script runs on after the menu
        down = {k for k, a, b in script if a <= st["tick"] < b}
    if st["inits"] == 2 and SPACE_AT is not None and st["flips"] >= SPACE_AT + 1:
        down.add("space")
    return down


def on_out(port, v):
    p = port & 0xFF
    if opt.trace:
        trace.append(bytes((0x4F, p, v)))
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
                        if TIMED:                 # real vertical blanks since the last R#2
                            vb = int(now() // FRAME_T)
                            flip_t.append((st["inits"], st["flips"], now(), vb - st["vb_flip"],
                                           st["blanks"]))
                            st["vb_flip"] = vb
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
        if MS:
            ms.fm_latch[(p >> 1) & 1] = v
    elif p in (0xC5, 0xC7):                   # OPL data
        bank = (p >> 1) & 1
        events.append(("O", bank, st["opl_reg"][bank], v, st["fdemo"]))
        if MS:
            ms.fm_write(bank, v, now())
    elif p in (0x7E, 0x7F) and MS:            # OPL4 wave part
        ms.wave_out(p, v, now())
    elif p == P_PORT4:
        events.append(("INIT",))
        if TIMED:
            st.setdefault("init_t", []).append((now(), st.get("s0_t", 0)))
        st["inits"] += 1
        st["flips"] = 0
        st["fdemo"] = 0
        if st["inits"] == 2:
            st["chosen"] = st["tick"]
            if MS:                              # the menu is over: what the ROM found
                st["found"] = (m.memory[labels["mus_target"]], m.memory[labels["opl4"]])
    elif p == 0xAA:                           # PPI port C: keyboard row
        st["row"] = v & 0x0F


def on_in(port):
    v = read_port(port & 0xFF)
    if opt.trace:
        trace.append(bytes((0x49, port & 0xFF, v)))
    return v


def read_port(p):
    if p == P_GIDX:
        if st["geo"]:
            st["geo"] -= 1
            return 0x01
        return 0x00
    if p == P_CTRL:
        if st["r15"] == 0:                    # S#0: F set on every other read
            if TIMED:                         # or: F set by every vertical blank since the last read
                st["s0_t"] = now()
                vb = int(st["s0_t"] // FRAME_T)
                st["s0"] = vb > st["vb"]
                st["vb"] = vb
            else:
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
    if MS:
        if p in (0xC4, 0xC6):
            return ms.read_status(now())
        if p in (0xC5, 0xC7):
            return ms.fm[(p >> 1) & 1][ms.fm_latch[(p >> 1) & 1]]
        if p in (0x7E, 0x7F):
            return ms.wave_in(p)
    if p == 0xC4 and st["forced"] and opt.chip == "opl":
        return 0x00                           # OPL status: not BUSY
    return 0xFF


m.set_output_callback(on_out)
m.set_input_callback(on_in)
US_MEM = labels.get("us_mem")                # the MOD upload's memory writes (after its RAM test)
for addr in (0x0024, 0x0138, labels["bank2_raw"], labels["psg_silence"]) + ((US_MEM,) if MS and US_MEM else ()):
    m.set_breakpoint(addr)


def step_over():
    """One instruction past a breakpoint, its T-states counted."""
    clock.step_over()


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
CYCLES = 1 if SPACE_AT is not None else opt.cycles
target_inits = 1 + (2 if SPACE_AT is not None else ndemos * CYCLES + 1)
stuck = False
menu_steps = 0
while True:
    clock.run(RUN)
    if st["inits"] == 1:
        menu_steps += 1
    if m.halted:
        break
    if m.pc == 0x0024:                        # ENASLT (always page 2)
        assert m.h & 0xC0 == 0x80
        enaslt_page2(m.a)
        step_over()
    elif MS and m.pc == US_MEM:
        ms.counting = True
        step_over()
    elif m.pc == labels["psg_silence"]:
        if not st["forced"] and MS:           # right after power-on detection (the chip is not forced)
            st["forced"] = True
        elif not st["forced"]:
            assert m.memory[labels["mus_target"]] == 0, "detection found a chip in the harness"
            if TARGET:
                m.set_memory_block(labels["mus_target"], bytes([TARGET]))
                m.set_memory_block(labels["scc_slot"], bytes([SCC_SLOT if TARGET == 1 else 0xFF]))
                m.set_memory_block(labels["opl4"], bytes([1 if opt.opl4 else 0]))
            st["forced"] = True
        step_over()
    elif m.pc == 0x0138:                      # RSLREG: slot 1 in pages 1 and 2
        m.a = 0b00010100
        step_over()
    elif m.pc == labels["bank2_raw"]:          # ASCII16 bank switch for page 2 (streams and music)
        b = m.a
        m.set_memory_block(0x8000, rom[b * BANK:(b + 1) * BANK])
        st["page2"] = b
        step_over()
    if st["inits"] >= target_inits:
        # with a MoonSound, a few frames more: the demo_init that stops the MOD
        st.setdefault("t_last", now())
        if not MS or now() > st["t_last"] + 3 * FRAME_T:
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
CHIP, OPL4 = opt.chip, opt.opl4             # the target the music is checked for
mod_play = False
if MS and not stuck:
    # the ROM's own detection: the MOD when it has one and the sample RAM for
    # its image, else the OPL4's FM part with the conversion
    modx = exp.get("mod")
    want_mod = bool(modx) and opt.moonsound * 1024 >= modx["blocks"] * 128 * 1024
    found = st.get("found")
    good = found == ((3, 1) if want_mod else (2, 1))
    need = f" (o MOD pede {modx['blocks'] * 128} KB)" if modx else " (ROM sem MOD)"
    print(f"detecção: MoonSound com {opt.moonsound} KB de sample RAM -> "
          f"{'MOD na parte wave' if found and found[0] == 3 else 'FM do OPL4 (conversão)'}{need}: "
          + ("ok" if good else f"FALHOU, alvo {found}"))
    ok = ok and good
    if found and found[0] == 2:
        CHIP, OPL4 = "opl", True
    mod_play = bool(found) and found[0] == 3


def opl4_tune(lo, b):
    s = lo + (b & 3)
    if s < 256:
        return s, b
    return (0xFF, b) if b & 3 == 3 else (s & 0xFF, b + 1)


def expected_music(ticks, state=None):
    """(per tick: psg writes, scc bytes that change (from state: the SCC's
    bytes before, zeros at power on), opl writes; the SCC's bytes after)"""
    b0, state, out = [0] * 18, bytearray(state or bytes(256)), []
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
                lo, b = opl4_tune(o[1], o[2]) if OPL4 else (o[1], o[2])
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
if mus and len(played) >= 1 and not stuck and not mod_play:
    want, scc_state = expected_music(mus[CHIP])
    nt = len(want)
    mute = (MUTE_PSG, [(0x8F, 0)] if scc_state[0x8F] else [], MUTE_OPL if CHIP == "opl" else [])
    # each crawl (the first demo of each time through the sequence) plays the
    # music from its start (the last one only if the run did not cut it)
    crawls = [i for i in range(1, len(demos), len(TABLE)) if i == 1 or i < len(demos) - 1]
    bad, tail_ok, scc_on, full = [], True, True, True
    scc0 = None
    for ci in crawls:
        want, scc_state = expected_music(mus[CHIP], scc0)    # (the SCC keeps its waves)
        scc0 = bytearray(scc_state)
        scc0[0x8F] = 0                                          # (the mute)
        got_i = sound_at(ci)
        blanks_in = sum(int(g.split("/")[1]) for g in played[ci - 1] if g.startswith("D") and "/" in g)
        heard_i = min(nt, blanks_in)
        bad_i = [k for k in range(heard_i)
                 if tuple(got_i.get(k + 1, ([], [], [], []))[:3]) != (want[k][0], list(want[k][1]), want[k][2])]
        tail = {f: v[:3] for f, v in got_i.items() if f > nt}
        tail_ok = tail_ok and (not tail or (list(tail) == [nt + 1] and tuple(tail[nt + 1]) == mute))
        scc_on = scc_on and all(x == 0x3F for f, v in got_i.items() if 1 <= f <= heard_i for x in v[3])
        full = full and (SPACE_AT is not None or blanks_in >= nt)
        if ci == 1:
            got, heard = got_i, heard_i
        if bad_i and not bad:
            bad, got = bad_i, got_i
    # the other demos: exactly the mute at their demo_init (blank 0), nothing
    # else (the last one is cut at its INIT, before its demo_init mutes)
    later_ok = True
    for i in range(2, len(demos) - 1):
        if i in crawls:
            continue
        s = sound_at(i)
        z = s.get(0, ([], [], [], []))
        if set(s) != {0} or z[0] != MUTE_PSG or z[2] != (MUTE_OPL if CHIP == "opl" else []) \
                or any(c != (0x8F, 0) for c in z[1]):
            later_ok = False
    good = not bad and tail_ok and full and later_ok and scc_on and not late_flips
    if late_flips:
        print(f"   {len(late_flips)} trocas de página depois do tick de música do mesmo retraço, "
              f"a primeira: demo {late_flips[0][0]}, retraço {late_flips[0][1]}")
    print(f"música  : {CHIP}{' (OPL4)' if OPL4 else ''}: {heard} de {nt} ticks conferidos no {TABLE[0]}"
          + (f" (e em cada um dos {len(crawls)} crawls)" if len(crawls) > 1 else "") +
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


def t_ms(t):
    return t / T_HZ * 1e3


def mod_check():
    """The MOD on the emulated MoonSound (see the module doc). -> ok"""
    mx = exp["mod"]
    fmw, good = ms.fmw, True
    # the upload: the image in the sample RAM, done before mod_start
    img_ok = (ms.mem_n == mx["image_len"]
              and hashlib.sha1(ms.ram[:mx["image_len"]]).hexdigest() == mx["image_sha1"])
    seg = opl4emu.segments(ms)
    if seg is None:
        print("MOD     : não começou (timer 2 nunca ligado)")
        return False
    t_start, t_entry, t_stop = seg["t_start"], seg["t_entry"], seg["t_stop"]
    up0, up1 = ms.mem_t
    inits = st["init_t"]                    # (T of each demo_init's INIT, of the last S#0 read before it)
    menu_t, crawl_t, last_s0 = inits[0][0], inits[1][0], inits[1][1]
    print(f"MOD     : upload de {ms.mem_n} bytes em {(up1 - up0) / T_HZ:.3f} s, de {(up0 - menu_t) / T_HZ:.2f} s "
          f"a {(up1 - menu_t) / T_HZ:.2f} s depois do menu aparecer; a escolha saiu do menu a "
          f"{(last_s0 - menu_t) / T_HZ:.2f} s, o crawl começou a {(crawl_t - menu_t) / T_HZ:.2f} s "
          f"(esperou o upload {max(0, up1 - last_s0) / T_HZ:.2f} s); sample RAM "
          f"{'idêntica' if img_ok else 'DIFERENTE'} ao modelo, completa antes do mod_start: {up1 < t_entry}")
    good = good and img_ok and up1 < t_entry
    clears, periods, init, segs, tail = seg["clears"], seg["periods"], seg["init"], seg["segs"], seg["tail"]
    marks = [t_start] + clears
    W = [[tuple(x) for x in w] for w in mx["writes"]]
    n = len(W) - 1                          # the ticks, then the END's key offs
    stopped = t_stop < float("inf")
    beyond = ""
    if mx.get("loop") and len(segs) >= n:
        # a run longer than the model (build_rom.py MOD_SECONDS): its ticks up
        # to the model's last but one (the last has no next tick's preparation)
        beyond = f"; a execução passou do modelo ({n} ticks): comparados os {n - 1} primeiros"
        segs, periods = segs[:n - 1], periods[:n - 1]
    bad, last_ok = opl4emu.match_ticks(segs, W, mx["heads"], stopped)
    last = len(segs) - 1
    init_ok = init == [tuple(x) for x in mx["writes_start"]]
    per_ok = periods == mx["counts"][:len(periods)] and len(periods) >= min(len(marks), n) - (1 if beyond else 0)
    fm_other = sorted({(b, r) for t, b, r, v in fmw if t >= t_entry} - {(0, 3), (0, 4), (1, 5)})
    after = [x for x in fmw if x[0] > t_stop and (x[1], x[2], x[3]) != (0, 4, 0x80)]
    how = "e o END" if stopped and last == n else "parado no demo_init" if stopped else "corte do teste"
    print(f"MOD     : {min(len(marks), n)} de {n} ticks tocados ({how}): "
          f"escritas do mod_start {'idênticas' if init_ok else 'DIFERENTES'} ({len(init)}), "
          f"ticks {'idênticos' if not bad and last_ok else 'DIFERENTES'} ao modplay.py, "
          f"períodos do timer 2 {'idênticos' if per_ok else 'DIFERENTES'} ({len(periods)}), "
          f"outras escritas FM: {fm_other or 'nenhuma'}, depois da parada: {len(after) + len(tail)}, "
          f"número de tom durante um carregamento: {len(ms.load_clash)}, tons fora dos samples: "
          f"{len(ms.bad_loads)}{beyond}")
    if bad or not last_ok:
        i = bad[0] if bad else last
        print(f"   primeiro tick diferente: {i}: ROM {segs[i][:8]}\n"
              f"                            esperado {(W[i] if i < len(W) else [])[:8]}")
    if not init_ok:
        print(f"   mod_start: ROM {init[:8]} ...\n              esperado {mx['writes_start'][:8]} ...")
    good = (good and not bad and last_ok and init_ok and per_ok and not fm_other and not after and not tail
            and not ms.load_clash and not ms.bad_loads)
    # timing: timer 2's overflows (tick k + 1 due) against the flag clears
    # (the poll that plays it); a tick is lost if the next overflow comes first
    ov = [t for t in ms.ov2 if t_start < t < t_stop]
    lat = [c - o for o, c in zip(ov, clears)]
    lost = [i for i in range(min(len(ov) - 1, len(clears))) if clears[i] >= ov[i + 1]]
    cnt_ok = len(ov) in (len(clears), len(clears) + 1)
    ideal = mx["ideal"]
    drift = [abs((o - t_start) / T_HZ - ideal[i + 1]) * 1e3 for i, o in enumerate(ov[:len(ideal) - 1])]
    kon, kk = [], 0
    for t, r, v in ms.wave:
        if t < t_start or t > t_stop:
            continue
        while kk + 1 < len(marks) and t >= marks[kk + 1]:
            kk += 1
        if 0x68 <= r < 0x80 and v & 0x80:
            kon.append(t - (t_start if kk == 0 else ov[kk - 1] if kk - 1 < len(ov) else marks[kk]))
    reads = [t for t in ms.reads if t_start <= t <= min(t_stop, now())]
    gaps = sorted(((b - a, a) for a, b in zip(reads, reads[1:])), reverse=True)
    srt = sorted(lat)
    print(f"MOD     : atraso dos ticks atrás do timer: máx {t_ms(srt[-1]):.2f} ms, p99 "
          f"{t_ms(srt[int(len(srt) * 0.99)]):.2f} ms, média {t_ms(sum(lat) / len(lat)):.2f} ms; "
          f"ticks perdidos: {len(lost)}; key on atrás do timer: {len(kon)}, máx {t_ms(max(kon)):.2f} ms, "
          f"média {t_ms(sum(kon) / len(kon)):.2f} ms; timer contra o ProTracker: até {max(drift):.3f} ms")
    print(f"MOD     : maior intervalo entre polls (leituras de status) tocando: "
          + ", ".join(f"{t_ms(g):.2f} ms a {(a - t_start) / T_HZ:.2f} s" for g, a in gaps[:3]))
    good = good and not lost and cnt_ok
    # a MOD that loops never stops: every tick of the run played, on through
    # every demo (its passes: modplay.Song.pass_starts)
    if mx.get("loop"):
        ps = mx["pass_starts"]
        played_n = min(len(marks), n)
        full = sum(1 for a, b in zip(ps, ps[1:]) if b <= played_n)
        loop_ok = not stopped and played_n >= 1
        print(f"MOD     : a música dá voltas: {played_n} ticks tocados, {full} voltas inteiras de "
              f"{ps[1] - ps[0]} ticks ({mx['ideal'][ps[1]] - mx['ideal'][ps[0]]:.2f} s) e "
              f"{played_n - ps[full]} ticks da seguinte; parada: {'nenhuma' if not stopped else 'PAROU'}")
        good = good and loop_ok
        # the longest times between two status reads, per demo
        dem = [t for t, _ in st["init_t"]]
        worst = {}
        for g, a in gaps:
            d = max(i for i, t in enumerate(dem) if t <= a) if a >= dem[0] else 0
            if d not in worst:
                worst[d] = (g, a)
        names = ["menu"] + [TABLE[i % len(TABLE)] for i in range(len(dem))]
        print("MOD     : maior intervalo entre polls por demo: " + ", ".join(
            f"{names[d]}#{d} {t_ms(g):.2f} ms" for d, (g, a) in sorted(worst.items())))
    # the PSG: the mute of each demo_init only; later demos: nothing else (a
    # looping MOD: its timer 2 writes)
    psg = [f for f, kind, data in demo_snd[1] if kind == "psg" and f > 0]
    mod_fm = ((0, 3), (0, 4)) if mx.get("loop") else ()
    later = [(i, f, kind) for i in range(2, len(demos) - 1) for f, kind, data in demo_snd[i]
             if not (f == 0 and (kind == "psg" or (i == 2 and kind == "opl" and data[:2] == (0, 4))))
             and not (kind == "opl" and data[:2] in mod_fm)]
    print(f"MOD     : PSG no crawl além do mudo: {len(psg)} escritas; demos seguintes: "
          f"{('só o MOD' if mx.get('loop') else 'silêncio') if not later else f'{len(later)} escritas'}")
    return good and not psg and not later


if mod_play and len(played) >= 1:
    ok = mod_check() and ok
if TIMED and not stuck:
    # real time: per demo, the black screen before its first flip and the
    # frames whose length an upload sets (build_rom.py UPLOAD_FRAMES), in
    # vertical blanks, and the other frames that took more vertical blanks
    # than the player counted (late: with the MOD, a failure)
    names = ["menu"] + [TABLE[i % len(TABLE)] for i in range(ndemos * CYCLES + 1)]
    upf = exp.get("upload_frames", {})
    n_late = 0
    for d in sorted({f[0] for f in flip_t}):
        fl = [f for f in flip_t if f[0] == d]
        if len(fl) < 2:
            continue
        name = names[d - 1] if d - 1 < len(names) else str(d)
        up = set(upf.get(name, []))
        late = [(f[1], f[3], f[4]) for f in fl[2:] if f[3] != f[4] and f[1] - 1 not in up]
        ups = [f"quadro {f[1] - 1}: {f[3]} retraços" for f in fl[2:] if f[1] - 1 in up]
        n_late += len(late)
        print(f"tempo   : {name:8s} tela preta até a 1a troca {(fl[1][2] - fl[0][2]) / T_HZ:.3f} s "
              f"({fl[1][3]} retraços); {len(fl) - 1} trocas, {len(late)} com mais retraços que o contado"
              + (f": {late[:4]}" if late else "") + (f"; upload: {', '.join(ups)}" if ups else ""))
    if mod_play and n_late:
        print(f"tempo   : {n_late} quadros atrasados com o MOD tocando: FALHOU")
        ok = False
check = [TABLE[i % ndemos] for i in range(len(played) - 1)] if SPACE_AT is None else TABLE[:1]
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
          f"({nw} escritas geo3d, {nx} bytes de VRAM, {nd} quadros em {nb} vblanks)"
          + (f" [volta {i // ndemos + 1}]" if CYCLES > 1 else ""))
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
          f"({'ok' if len(played) == ndemos * CYCLES + 1 else 'FALHOU'})")
    ok = ok and len(played) == ndemos * CYCLES + 1
if opt.trace:
    open(opt.trace, "wb").write(b"".join(trace))
print(f"portas {BASE:02X}h, {opt.lang}, teclas {opt.keys}: " + ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
