#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Runs GEO3D.COM in a Z80 emulator (pip package z80), with a minimal MSX-DOS
BDOS and a fake VDP status, and records every byte sent to the geo3d ports.

The capture is written as scenes_stim.txt-compatible stimulus
(../sim/demo_stim.txt) so the same RTL testbench and reference model can
check what the real Z80 program makes the hardware draw.
"""
import sys
import z80

FRAMES = int(sys.argv[1]) if len(sys.argv) > 1 else 6
COM = sys.argv[2] if len(sys.argv) > 2 else "GEO3D.COM"
OUT = sys.argv[3] if len(sys.argv) > 3 else "../sim/demo_stim.txt"
SYS = sys.argv[4] if len(sys.argv) > 4 else None     # optional full-system stimulus
GEO_IDX, GEO_DAT = 0x8D, 0x8F
VDP_CTRL, VDP_IND = 0x89, 0x8B

m = z80.Z80Machine()
code = open(COM, "rb").read()
m.set_memory_block(0x0100, code)
m.set_memory_block(0x0005, b"\xC9")   # BDOS entry: RET (handled below)
m.set_memory_block(0x0000, b"\x76")   # warm boot -> HALT
m.sp = 0xF000
m.set_memory_block(0xF000, b"\x00\x00")  # return address 0 -> HALT
m.pc = 0x0100

geo_ops = []          # ("W", sel, byte) / ("RUN",)
vdp_writes = []       # (port, byte)
events = []           # everything, in program order: ("G", sel, b) / ("RUN",) / ("P", port, b)
state = {"s2_reads": 0, "geo_busy": 0, "idx": None, "frames": 0, "keypolls": 0}


def on_out(port, value):
    p = port & 0xFF
    if p == GEO_IDX:
        geo_ops.append(("W", 0, value))
        events.append(("G", 0, value))
        state["idx"] = value
    elif p == GEO_DAT:
        geo_ops.append(("W", 1, value))
        events.append(("G", 1, value))
        if state["idx"] == 0x48 and value & 1:
            geo_ops.append(("RUN",))
            events.append(("RUN",))
            state["geo_busy"] = 3
            state["frames"] += 1
        if state["idx"] is not None and state["idx"] >> 4 == 4:
            state["idx"] = 0x40 | ((state["idx"] + 1) & 0xF)
    elif p in (VDP_CTRL, VDP_IND, 0x8A, 0x88, 0x8C):
        vdp_writes.append((p, value))
        events.append(("P", p, value))


def on_in(port):
    p = port & 0xFF
    if p == GEO_IDX:
        if state["geo_busy"]:
            state["geo_busy"] -= 1
            return 0x01
        return 0x00
    if p == VDP_CTRL:
        state["s2_reads"] += 1
        vr = (state["s2_reads"] // 4) & 1          # toggling vertical blank
        return 0x40 if vr else 0x00                # CE always 0
    return 0xFF


m.set_output_callback(on_out)
m.set_input_callback(on_in)
m.set_breakpoint(0x0005)

steps = 0
while True:
    m.ticks_to_stop = 100000
    m.run()
    steps += 1
    if m.halted or steps > 20000:
        break
    if m.pc == 0x0005:
        fn = m.c
        if fn == 0x0B:          # console status: key after FRAMES frames
            m.a = 0xFF if state["frames"] >= FRAMES else 0x00
        elif fn == 0x08:
            m.a = 0x1B
        m.step_over_breakpoint()

print(f"Z80 parou: halted={m.halted}, quadros={state['frames']}, "
      f"escritas geo3d={sum(1 for o in geo_ops if o[0] == 'W')}, escritas VDP={len(vdp_writes)}")

# ------------------------------------------------ stimulus for the RTL TB
lines, fr = [], 0
for o in geo_ops:
    if o[0] == "W":
        lines.append(f"W {o[1]} {o[2]:02x}")
    else:
        lines += ["R", "K", f"F {fr}"]
        fr += 1
open(OUT, "w").write("\n".join(lines) + "\n")

# ------------------------------------------------ full-system stimulus (tb_system.v)
# Decodes the Z80's VDP port traffic like vdp_cpu_interface.v does:
#   89h pairs: register write (bit7) or VRAM address setup (A13..A0, R#14 = A17..A14)
#   8Bh: indirect register write at R#17 (auto-increment unless bit7 of R#17)
#   88h: VRAM data write, address auto-increments with carry (bitmap modes)
# and emits: W sel b / R / F n (geo3d), V reg val and C (command registers,
# wait CE), X addr b (VRAM write), D y0 (dump the page just shown, R#2).
if SYS:
    out, fr = [], 0
    pend, r14, ptr, pinc, addr = None, 0, 0, True, 0

    def reg_write(r, v):
        global r14, ptr, pinc
        if r == 14:
            r14 = v & 0x0F
        elif r == 17:
            ptr, pinc = v & 0x3F, not (v & 0x80)
        elif r == 2:
            out.append("C")
            out.append(f"D {((v >> 5) & 3) * 256}")
        elif 32 <= r <= 58:
            out.append(f"V {r} {v:02x}")
            if r == 46:
                out.append("C")

    for e in events:
        if e[0] == "G":
            out.append(f"W {e[1]} {e[2]:02x}")
        elif e[0] == "RUN":
            out += ["R", f"F {fr}"]
            fr += 1
        else:
            p, v = e[1], e[2]
            if p == VDP_CTRL:
                if pend is None:
                    pend = v
                else:
                    if v & 0x80:
                        reg_write(v & 0x3F, pend)
                    else:
                        addr = (r14 << 14) | ((v & 0x3F) << 8) | pend
                    pend = None
            elif p == VDP_IND:
                reg_write(ptr, v)
                if pinc:
                    ptr = (ptr + 1) & 0x3F
            elif p == 0x88:
                out.append(f"X {addr:05x} {v:02x}")
                addr = (addr + 1) & 0x3FFFF
    open(SYS, "w").write("\n".join(out) + "\n")
    print(f"estímulo do sistema: {sum(1 for l in out if l[0] == 'X')} bytes de VRAM, "
          f"{sum(1 for l in out if l[0] == 'V')} registros de comando do Z80, "
          f"{sum(1 for l in out if l[0] == 'D')} trocas de página -> {SYS}")

# ------------------------------------------------ VDP side sanity: HMMV blocks
regs, hmmv = {}, []
ptr, pend = None, None
for p, v in vdp_writes:
    if p == VDP_CTRL:
        if pend is None:
            pend = v
        else:
            if v & 0x80:
                regs[v & 0x3F] = pend
                if (v & 0x3F) == 17:
                    ptr = pend
            pend = None
    elif p == VDP_IND:
        regs[ptr] = v
        if ptr == 46:
            hmmv.append((regs[36] | regs[37] << 8, regs[38] | regs[39] << 8,
                         regs[40] | regs[41] << 8, regs[42] | regs[43] << 8, v))
        ptr += 1
print("comandos do Z80 no VDP (DX, DY, NX, NY, CMD):", hmmv[:4], "...")
print("R#2 (página exibida) ao longo dos quadros:",
      [hex(pend_v) for pend_v, nxt in zip([w[1] for w in vdp_writes if w[0] == VDP_CTRL][::2],
                                          [w[1] for w in vdp_writes if w[0] == VDP_CTRL][1::2]) if nxt == 0x82][:8])
