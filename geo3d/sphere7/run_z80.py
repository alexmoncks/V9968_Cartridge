#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Runs SPHERE7_xx.ROM in a Z80 emulator (pip package z80) as an MSX would
(ASCII16 banks in page 2, RSLREG / ENASLT, RAM in page 3, MSX time: the
Z80's T-states plus the M1 wait, a vertical blank every 59736 cycles), with
the V9968 status registers, the geo3d probe and the keyboard simulated;
geo3d and the command engine answer "idle" at once. Every OUT is recorded and
decoded as vdp_cpu_interface.v does, and then:

  1. geo3d's traffic -> <out>/engine_stim.txt for sim/tb_engine.v (the RTL),
     and the reference model's command log (sim/gen_scenes.py replay) ->
     engine_expect.txt; --rtl runs tb_engine.v and compares (bit-exact);
  2. the system model: the Z80's own commands (HMMV, HMMM, PSET), the VRAM
     uploads and geo3d's commands applied in program order to the SCREEN 7
     VDP model (vdp6.py, checked against vdp_command.v), and every page
     shown (R#2 at a vertical blank) captured with its palette ->
     frames.npz and PNGs; checks on the way: the page shown is never the one
     being drawn, no command register written while geo3d runs, flips only
     right after a vertical blank;
  3. --sys F1,F2,..: a stimulus for sim/tb_system_g6.v (geo3d_bus + HRA!'s
     vdp_command.v in SCREEN 7) with every frame up to max(F) drawn, and the
     model's pages of the frames F1, F2, .. -> sys_stim.txt / sys_expect.hex.

Usage: run_z80.py [--base 98|88] [--frames N] [--space F,...] [--esc F,...]
                  [--out DIR] [--rtl] [--sys F1,F2,... [--sysrun]] [--png N]
Also printed: the Z80's time per frame (MSX cycles); PROF=1 in the
environment samples the program counter and prints the busiest routines.
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np
import z80
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "sim"))
sys.path.insert(0, os.path.join(ROOT, "rom"))
sys.path.insert(0, HERE)
from gen_scenes import replay  # noqa: E402
from z80clock import MsxClock  # noqa: E402
import vdp6  # noqa: E402

FRAME_CYC = 59736          # MSX cycles per NTSC frame (3.58 MHz)
BANK = 0x4000

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="98")
ap.add_argument("--frames", type=int, default=120, help="frames to draw (RUNs)")
ap.add_argument("--space", default="", help="SPACE pressed while drawing these frames")
ap.add_argument("--esc", default="", help="ESC pressed while drawing these frames")
ap.add_argument("--out", default=None)
ap.add_argument("--rtl", action="store_true")
ap.add_argument("--sys", default="")
ap.add_argument("--sysrun", action="store_true", help="run tb_system_g6.v on the --sys stimulus")
ap.add_argument("--png", type=int, default=0, help="write the first N shown pages as PNG")
opt = ap.parse_args()

out_dir = opt.out or os.path.join(HERE, "out", f"z80_{opt.base}")
os.makedirs(out_dir, exist_ok=True)
P = int(opt.base, 16)
VDP_DATA, VDP_CTRL, VDP_PAL, VDP_IND, PORT4, GEO_IDX, GEO_DAT = (P + k for k in (0, 1, 2, 3, 4, 5, 7))
rom = open(os.path.join(HERE, "out", f"SPHERE7_{opt.base}.ROM"), "rb").read()
labels = {}
for ln in open(os.path.join(HERE, "out", f"labels_{opt.base}.txt")):
    if ":" in ln and "$" in ln:
        labels[ln.split(":")[0]] = int(ln.split("$")[1], 16)
assets = json.load(open(os.path.join(HERE, "out", "assets.json")))
space_at = {int(x) for x in opt.space.split(",") if x}
esc_at = {int(x) for x in opt.esc.split(",") if x}

m = z80.Z80Machine()
m.set_memory_block(0x4000, rom[0:BANK])
m.set_memory_block(0x8000, rom[0:BANK])
m.set_memory_block(0x0024, b"\xC9")             # ENASLT
m.set_memory_block(0x0138, b"\xC9")             # RSLREG
m.set_memory_block(0x006C, b"\xC9")             # INITXT
m.set_memory_block(0x00A2, b"\xC9")             # CHPUT
m.set_memory_block(0x002D, bytes([2]))          # MSXVER: MSX2+
m.set_memory_block(0xFCC1, bytes(5))            # EXPTBL: not expanded
m.pc = 0x4000 + (rom[2] | rom[3] << 8)
m.sp = 0xF380
clk = MsxClock(m)

st = {"r15": 0, "vblank_seen": 0, "runs": 0, "geo_idx": 0, "rptr": 0x30, "ppi_row": 0,
      "text": "", "flips": 0}
events = []                 # (kind, ...) in program order


def now():
    return clk.now()


def keys_down():
    k = st["runs"]
    return {"space": k in space_at, "esc": k in esc_at}


GEO_RESET = {0x40: 0, 0x41: 0, 0x42: 0, 0x43: 0, 0x44: 15, 0x45: 0, 0x46: 0, 0x47: 0, 0x48: 0}


def on_out(port, v):
    p = port & 0xFF
    t = now()
    if p == GEO_IDX:
        st["geo_idx"] = v
        st["rptr"] = v
        events.append(("G", 0, v, t))
    elif p == GEO_DAT:
        events.append(("G", 1, v, t))
        i = st["geo_idx"]
        if i == 0x48 and v & 1:
            st["runs"] += 1
            events.append(("RUN", st["runs"] - 1, t))
        if i < 0x2A or i >> 4 == 4 or i >> 3 in (0x0B, 0x0C):
            if i >> 4 == 4:
                st["geo_idx"] = 0x40 | ((i + 1) & 0xF)
            elif i >> 3 == 0x0B:
                st["geo_idx"] = 0x58 | ((i + 1) & 7)
            elif i >> 3 == 0x0C:
                st["geo_idx"] = 0x60 | ((i + 1) & 7)
            elif i == 0x29:
                st["geo_idx"] = 0x24
            else:
                st["geo_idx"] = i + 1
    elif p in (VDP_DATA, VDP_CTRL, VDP_PAL, VDP_IND, PORT4):
        events.append(("V", p, v, t))
        if p == VDP_CTRL:
            pend = st.get("pend")
            if pend is None:
                st["pend"] = v
            else:
                st["pend"] = None
                if v & 0x80 and (v & 0x3F) == 15:
                    st["r15"] = pend & 15
    elif p == 0xAA:
        st["ppi_row"] = v & 0x0F
    elif p in (0xA0, 0xA1):
        events.append(("S", p, v, t))


def on_in(port):
    p = port & 0xFF
    if p == VDP_CTRL:
        st["pend"] = None
        if st["r15"] == 0:
            fr = now() // FRAME_CYC
            if fr > st["vblank_seen"]:
                st["vblank_seen"] = fr
                events.append(("VB", fr, now()))
                return 0x80
            return 0x00
        if st["r15"] == 1:
            return 0x04                            # ID 2: a V9968 after reset (V9958 mode)
        if st["r15"] == 2:
            return 0x00                            # CE = 0
        return 0x00
    if p == PORT4:
        return 0x80
    if p == GEO_IDX:
        events.append(("GI", now()))
        return 0x00                                # status: idle
    if p == GEO_DAT:
        r = st["rptr"]
        v = GEO_RESET.get(r, 0xFF if r == 0x49 else 0)
        if r >> 4 == 4:
            st["rptr"] = 0x40 | ((r + 1) & 0xF)
        return v
    if p == 0xA9:
        k = keys_down()
        row = st["ppi_row"]
        v = 0xFF
        if row == 8 and k["space"]:
            v &= ~1
        if row == 7 and k["esc"]:
            v &= ~4
        return v & 0xFF
    return 0xFF


m.set_output_callback(on_out)
m.set_input_callback(on_in)
for a in (0x0024, 0x0138, 0x006C, 0x00A2, labels["setbank"]):
    m.set_breakpoint(a)

prof = {}
while st["runs"] <= opt.frames:
    if os.environ.get("PROF") and st["runs"] > 2:
        stop = clk.run(97)
        prof[m.pc] = prof.get(m.pc, 0) + 1
    else:
        stop = clk.run(200000)
    if stop:
        pc = m.pc
        if pc == 0x0138:
            m.a = 0x04                          # page 1 in slot 1 (page 2 the same)
        elif pc == labels["setbank"]:
            b = m.a
            m.set_memory_block(0x8000, rom[b * BANK:(b + 1) * BANK])
        elif pc == 0x006C:
            st["text"] += "<INITXT>"
        elif pc == 0x00A2:
            st["text"] += chr(m.a)
            if len(st["text"]) > 200:
                break
        if m.halted:
            break
        clk.step_over()
    if st["text"] and m.pc == labels.get("ng_stay"):
        break
    if now() > FRAME_CYC * 60 * 120:
        break
if st["text"]:
    print("BIOS text:", st["text"])
    sys.exit(1)

ram = {k: m.memory[labels[k]] | m.memory[labels[k] + 1] << 8 for k in ("vbl", "late", "nflip", "bounces", "nstars")}
print(f"Z80: {st['runs']} RUNs, {ram['nflip']} flips, {ram['vbl']} blanks, late {ram['late']}, "
      f"{ram['bounces']} contacts, {ram['nstars']} stars put back")

# ---------------------------------------------------------------- decode
eng_ops = []
for e in events:
    if e[0] == "G":
        eng_ops.append(f"W {e[1]} {e[2]:02x}")
    elif e[0] == "RUN":
        eng_ops += ["R", f"F {e[1]}"]
log = replay(eng_ops, {"draw": 0, "skip": 0, "cull": 0})
groups, cur = {}, []
for ln in log:
    p_ = ln.split()
    if p_[0] in ("L", "M"):
        cur.append((p_[0], [int(x, 16) for x in p_[1:]]))
    elif p_[0] == "F":
        groups[int(p_[1])] = cur
        cur = []
with open(os.path.join(out_dir, "engine_stim.txt"), "w") as f:
    f.write("\n".join(eng_ops) + "\n")
with open(os.path.join(out_dir, "engine_expect.txt"), "w") as f:
    f.write("\n".join(log) + "\n")
nl = sum(len(g) for g in groups.values())
print(f"engine model: {len(groups)} frames, {nl} commands "
      f"({nl / max(1, len(groups)):.0f} per frame, at most {max(len(g) for g in groups.values())})")

# system model
vr = vdp6.Vram6()
regs = [0] * 64
regs[51:59] = [0, 0, 0, 0, 0xFF, 1, 0xFF, 7]
pend, r14, ptr, pinc, addr = None, 0, 0, True, 0
pal = [[0, 0, 0] for _ in range(256)]
pal_idx, pal_phase, pal_buf = 0, 0, []
epal = False
geo_busy = False
page_drawn = None           # page geo3d draws into in the current frame
shown = []                  # (frame index drawn, page, rgb palette 16, vblank, vram page bytes)
last_vb = (-1, -1)
issues = []
sys_ops = []                # tb_system_g6 stimulus
want_sys = sorted(int(x) for x in opt.sys.split(",") if x)
sys_last = max(want_sys) if want_sys else -1
sys_pages = {}
cur_frame = -1
vb_count = 0
flip_vbs = []
geo_ypage = 0
gidx = 0


def palette_rgb():
    out = []
    for i in range(16):
        r, g, b = pal[i]
        if epal:
            out.append(tuple(round(c * 255 / 31) for c in (r, g, b)))
        else:
            out.append(tuple(round(c * 255 / 7) for c in (r, g, b)))
    return out


def vreg(r, v, t):
    global r14, ptr, pinc, epal, pal_idx, pal_phase
    if r == 14:
        r14 = v & 0x0F
    elif r == 17:
        ptr, pinc = v & 0x3F, not (v & 0x80)
    elif r == 16:
        pal_idx, pal_phase = (v if epal else v & 15), 0
    elif r == 20:
        epal = bool(v & 0x10)
    elif r == 2:
        page = (v >> 5) & 3
        if cur_frame >= 0 and (t - last_vb[1] > 4000 or last_vb[0] < 0):
            issues.append(f"R#2 = {v:02x} not right after a vertical blank ({t - last_vb[1]} cycles after it)")
        if page_drawn is not None and page == page_drawn and geo_busy:
            issues.append(f"page {page} shown while geo3d draws it")
        shown.append([cur_frame, page, None, last_vb[0], vr.page(page * 256)])
        flip_vbs.append(last_vb[0])
        if cur_frame <= sys_last:
            sys_ops.append("C")
            sys_ops.append(f"D {page * 256}")
    elif 32 <= r <= 58:
        if geo_busy:
            issues.append(f"R#{r} written while geo3d runs (frame {cur_frame})")
        regs[r] = v
        if cur_frame <= sys_last or cur_frame < 0:
            sys_ops.append(f"V {r} {v:02x}")
        if r == 46:
            vdp6.apply_regs(vr, regs)
            if cur_frame <= sys_last or cur_frame < 0:
                sys_ops.append("C")


for e in events:
    kind = e[0]
    if kind == "VB":
        last_vb = (e[1], e[2])
        vb_count += 1
        continue
    if kind == "GI":
        geo_busy = False                          # the Z80 read geo3d's status: idle
        continue
    if kind == "G":
        if geo_busy:
            issues.append(f"geo3d written while it runs (frame {cur_frame})")
        if e[1] == 0:
            gidx = e[2]
        elif gidx == 0x46:
            geo_ypage = (geo_ypage & 0x700) | e[2]
        elif gidx == 0x47:
            geo_ypage = (geo_ypage & 0xFF) | (e[2] & 7) << 8
        if e[1] == 1 and (gidx >> 4 == 4 or gidx < 0x2A):
            gidx = 0x40 | ((gidx + 1) & 0xF) if gidx >> 4 == 4 else gidx + 1
        if cur_frame + 1 <= sys_last or cur_frame < 0:
            sys_ops.append(f"W {e[1]} {e[2]:02x}")
        continue
    if kind == "RUN":
        cur_frame = e[1]
        for kd, b in groups.get(cur_frame, []):
            r = list(regs)
            if kd == "M":
                for k, v in zip(list(range(32, 46)) + list(range(47, 51)), b[:18]):
                    r[k] = v
                r[46] = b[18]
            else:
                for k, v in zip(range(36, 46), b[:10]):
                    r[k] = v
                r[46] = b[10]
            for k in list(range(32, 51)):
                regs[k] = r[k]
            vdp6.apply_regs(vr, r)
        page_drawn = geo_ypage >> 8
        geo_busy = True
        if cur_frame <= sys_last:
            sys_ops += ["R", f"F {cur_frame}"]
        if cur_frame in want_sys:
            sys_pages[cur_frame] = None           # captured at its flip below
        continue
    if kind != "V":
        continue
    p_, v, t = e[1], e[2], e[3]
    if p_ == VDP_CTRL:
        if pend is None:
            pend = v
        else:
            if v & 0x80:
                vreg(v & 0x3F, pend, t)
            else:
                addr = (r14 << 14) | ((v & 0x3F) << 8) | pend
            pend = None
    elif p_ == VDP_IND:
        vreg(ptr, v, t)
        if pinc:
            ptr = (ptr + 1) & 0x3F
    elif p_ == VDP_DATA:
        vr.b[addr] = v
        if v and (cur_frame <= sys_last or cur_frame < 0):
            sys_ops.append(f"X {addr:05x} {v:02x}")
        addr = (addr + 1) & 0x3FFFF
    elif p_ == VDP_PAL:
        if epal:
            pal_buf.append(v & 31)
            if len(pal_buf) == 3:
                pal[pal_idx & 255] = list(pal_buf)
                pal_buf = []
                pal_idx += 1
        else:
            pal_buf.append(v)
            if len(pal_buf) == 2:
                pal[pal_idx & 15] = [(pal_buf[0] >> 4) & 7, pal_buf[1] & 7, pal_buf[0] & 7]
                pal_buf = []
                pal_idx = (pal_idx + 1) & 15

# the palette of a shown page: the one in force once the flip's blank is over
# (pal_write follows R#2 at once); a second pass over the events, closed at the
# next geo3d access
pal = [[0, 0, 0] for _ in range(256)]
pal_idx, pal_buf, epal, ptr, pinc, pend = 0, [], False, 0, True, None
si = 0
flip_n = 0
pal_at_flip = []
pending_flip = False
for e in events:
    if e[0] == "V":
        p_, v = e[1], e[2]
        if p_ == VDP_CTRL:
            if pend is None:
                pend = v
            else:
                if v & 0x80:
                    r = v & 0x3F
                    if r == 20:
                        epal = bool(pend & 0x10)
                    elif r == 16:
                        pal_idx, pal_buf = (pend if epal else pend & 15), []
                    elif r == 2:
                        if pending_flip:
                            pal_at_flip.append(palette_rgb())
                        pending_flip = True
                pend = None
        elif p_ == VDP_PAL:
            pal_buf.append(v & (31 if epal else 0xFF))
            if epal and len(pal_buf) == 3:
                pal[pal_idx & 255] = list(pal_buf)
                pal_buf, pal_idx = [], pal_idx + 1
            elif not epal and len(pal_buf) == 2:
                pal[pal_idx & 15] = [(pal_buf[0] >> 4) & 7, pal_buf[1] & 7, pal_buf[0] & 7]
                pal_buf, pal_idx = [], (pal_idx + 1) & 15
    elif e[0] in ("G", "RUN") and pending_flip:
        pal_at_flip.append(palette_rgb())
        pending_flip = False
if pending_flip:
    pal_at_flip.append(palette_rgb())
for s_, pl in zip(shown, pal_at_flip):
    s_[2] = pl

print(f"system model: {len(shown)} pages shown; issues: {len(issues)}")
for i in issues[:10]:
    print("  ", i)


def page_png(page, pl, path, scale_y=2):
    a = np.frombuffer(page, dtype=np.uint8).reshape(212, 256)
    pix = np.empty((212, 512), dtype=np.uint8)
    pix[:, 0::2] = a >> 4
    pix[:, 1::2] = a & 15
    rgb = np.array(pl, dtype=np.uint8)[pix]
    im = Image.fromarray(rgb, "RGB")
    if scale_y > 1:
        im = im.resize((512, 212 * scale_y), Image.NEAREST)
    im.save(path)


np.savez_compressed(os.path.join(out_dir, "frames.npz"),
                    pages=np.array([np.frombuffer(s_[4], dtype=np.uint8) for s_ in shown]),
                    pals=np.array([s_[2] for s_ in shown], dtype=np.uint8),
                    frame=np.array([s_[0] for s_ in shown]), vbl=np.array([s_[3] for s_ in shown]))
for k in range(min(opt.png, len(shown))):
    page_png(shown[k][4], shown[k][2], os.path.join(out_dir, f"shown_{k:04d}.png"))
gaps = np.diff([s_[3] for s_ in shown])
if len(gaps):
    vals, cnt = np.unique(gaps, return_counts=True)
    print("vertical blanks between flips:", dict(zip(vals.tolist(), cnt.tolist())))

# ---------------------------------------------------------------- tb_system_g6 stimulus
if want_sys:
    with open(os.path.join(out_dir, "sys_stim.txt"), "w") as f:
        f.write("\n".join(sys_ops) + "\n")
    with open(os.path.join(out_dir, "sys_expect.hex"), "w") as f:
        n = 0
        for s_ in shown:
            if s_[0] <= sys_last:
                f.write("".join(f"{b:02x}\n" for b in s_[4]))
                n += 1
    print(f"tb_system_g6 stimulus: frames 0..{sys_last}, {n} pages expected")
    if opt.sysrun:
        hra = os.path.join(ROOT, "..", "fpga", "V9968_Cartridge_TangNano20K", "src", "v9968")
        vvp = os.path.join(HERE, "out", "tbsys6.vvp")
        srcs = [os.path.join(HERE, "sim", "tb_system_g6.v")] + [os.path.join(ROOT, "rtl", f) for f in
                ("geo3d_bus.v", "geo3d_engine.v", "geo3d_core.v")] + [os.path.join(hra, f) for f in
                ("vdp_command.v", "vdp_command_cache.v")]
        if not os.path.exists(vvp) or any(os.path.getmtime(s_) > os.path.getmtime(vvp) for s_ in srcs):
            subprocess.run(["iverilog", "-g2012", "-o", vvp] + srcs, check=True)
        r = subprocess.run(["vvp", "-n", vvp, f"+stim={os.path.join(out_dir, 'sys_stim.txt')}",
                            f"+frames={os.path.join(out_dir, 'sys_got.hex')}",
                            f"+times={os.path.join(out_dir, 'sys_times.txt')}"], capture_output=True, text=True)
        print([x for x in r.stdout.splitlines() if x.startswith("System")])
        got = open(os.path.join(out_dir, "sys_got.hex")).read().split()
        exp = open(os.path.join(out_dir, "sys_expect.hex")).read().split()
        pages = len(exp) // (212 * 256)
        badp = [k for k in range(pages) if got[k * 54272:(k + 1) * 54272] != exp[k * 54272:(k + 1) * 54272]]
        print(f"tb_system_g6 (geo3d_bus + vdp_command.v, SCREEN 7): {pages - len(badp)} of {pages} pages "
              f"identical to the model" + (f"; differ: {badp[:10]}" if badp else ""))
        if len(got) != len(exp):
            print(f"  page count differs: RTL {len(got) // 54272}, model {pages}")

# ---------------------------------------------------------------- RTL (tb_engine.v)
if opt.rtl:
    vvp = os.path.join(HERE, "out", "tbe.vvp")
    srcs = [os.path.join(ROOT, "sim", "tb_engine.v"), os.path.join(ROOT, "rtl", "geo3d_engine.v"),
            os.path.join(ROOT, "rtl", "geo3d_core.v")]
    if not os.path.exists(vvp) or any(os.path.getmtime(s_) > os.path.getmtime(vvp) for s_ in srcs):
        subprocess.run(["iverilog", "-g2012", "-o", vvp] + srcs, check=True)
    got = os.path.join(out_dir, "engine_got.txt")
    r = subprocess.run(["vvp", "-n", vvp, f"+stim={os.path.join(out_dir, 'engine_stim.txt')}", f"+got={got}"],
                       capture_output=True, text=True)
    print(" ".join(x for x in r.stdout.splitlines() if x.startswith("Quadros")) or r.stderr[-400:])
    a = [x.strip() for x in open(got)]
    b = [x.strip() for x in open(os.path.join(out_dir, "engine_expect.txt"))]
    bad = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None)
    if bad is None and len(a) == len(b):
        print(f"RTL tb_engine.v: {sum(1 for x in a if x[0] in 'LM')} commands in {sum(1 for x in a if x[0] == 'F')} "
              f"frames, identical to the model")
    else:
        print(f"RTL tb_engine.v DIFFERS at line {bad}: got {a[bad] if bad is not None and bad < len(a) else '-'} "
              f"expected {b[bad] if bad is not None and bad < len(b) else '-'} (lines {len(a)} / {len(b)})")
        sys.exit(1)

# ---------------------------------------------------------------- Z80 time per frame
# from the vertical blank of a flip to the RUN that follows it (clear, HUD,
# stars, window, geo3d registers), and from that RUN to the next status poll
# (sound, keys, next position): the Z80's part of a frame, in MSX cycles
vbt, z1, z2, last_run = None, [], [], None
for e in events:
    if e[0] == "VB":
        vbt = e[2]
    elif e[0] == "RUN":
        if vbt is not None and e[1] > 0:
            z1.append(e[2] - vbt)
        last_run = e[2]
    elif e[0] == "GI" and last_run is not None:
        z2.append(e[1] - last_run)
        last_run = None
if z1:
    ms = 1000 / 3579545
    print(f"Z80 time per frame: flip to RUN {np.mean(z1) * ms:.2f} ms (max {max(z1) * ms:.2f}), "
          f"RUN to the next wait {np.mean(z2) * ms:.2f} ms (max {max(z2) * ms:.2f})")

if prof:
    labs = sorted((a, n) for n, a in labels.items() if 0x4000 <= a < 0x8000 and not n.isupper())
    acc = {}
    for pc, n in prof.items():
        name = "?"
        for a, ln in labs:
            if a <= pc:
                name = ln
            else:
                break
        acc[name] = acc.get(name, 0) + n
    tot = sum(acc.values())
    for name, n in sorted(acc.items(), key=lambda x: -x[1])[:16]:
        print(f"  {name:12s} {100 * n / tot:5.1f} %")
