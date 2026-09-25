#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""The parts of a MoonSound (OPL4, YMF278B) the ROM's MOD player uses, as
openMSX emulates them, for the Z80 harnesses (run_rom_z80.py --moonsound,
test_modplay.py --z80): the FM part's timers, status (IRQ, FT1, FT2, LD;
BUSY never shows at 3.58 MHz) and NEW2; the wave part's registers, sample
memory (the wave ROM reads 0, the RAM from 200000h, FFh past it) and tone
header loads (LD for 295 us). Times are the Z80's T-states; every write is
logged with its T-state.
"""
from array import array

T_HZ = 3579545                              # the Z80's clock
OPL4_CLOCK = 33868800
T1_UNIT = 72 * 38 / OPL4_CLOCK * T_HZ       # T-states per step of FM timer 1 (80.8 us)
T2_UNIT = 4 * T1_UNIT                       # and of timer 2 (323.1 us)
LOAD_T = 10000 / OPL4_CLOCK * T_HZ          # a tone header load (LD), as openMSX has it


class MoonSound:
    def __init__(self, kb):
        self.ram = bytearray(kb * 1024)
        self.fm_latch, self.fm = [0, 0], [bytearray(256), bytearray(256)]
        self.status = self.mask = self.status2 = 0
        self.new2_seen = False
        self.timers = [dict(preset=0, run=False, next=0.0, unit=T1_UNIT, flag=0x40),
                       dict(preset=0, run=False, next=0.0, unit=T2_UNIT, flag=0x20)]
        self.wlatch, self.wregs, self.adr, self.load_until = 0, bytearray(256), 0, 0.0
        self.wave = []          # (T, register, value): every wave register write
        self.fmw = []           # (T, bank, register, value): every FM register write
        self.ov2 = []           # T of every timer 2 overflow
        self.reads = array("d")  # T of every status read
        self.counting = False   # count the memory writes (the upload, after the RAM test)
        self.mem_n, self.mem_t = 0, None    # the counted memory writes: count, (first, last T)
        self.loads = []         # (T, register): tone number writes (header loads)
        self.load_clash = []    # (T, register): a tone number written while LD was up
        self.bad_loads = []     # (T, register, tone): a wave ROM tone (< 384), or a RAM tone
                                # whose header (at 200000h) does not point into the samples

    def advance(self, t):
        for tm in self.timers:
            while tm["run"] and tm["next"] <= t:
                self.status |= tm["flag"]
                if self.status & self.mask:
                    self.status |= 0x80
                if tm["flag"] == 0x20:
                    self.ov2.append(tm["next"])
                tm["next"] += (256 - tm["preset"]) * tm["unit"]      # reloaded with the preset of now

    def read_status(self, t):
        self.advance(t)
        self.reads.append(t)
        v = self.status | self.status2 | (2 if t < self.load_until else 0)
        self.status2 = 0
        return v

    def fm_select(self, bank, v):
        self.fm_latch[bank] = v

    def fm_write(self, bank, v, t):
        self.advance(t)
        r = self.fm_latch[bank]
        self.fm[bank][r] = v
        self.fmw.append((t, bank, r, v))
        if bank == 0 and r in (2, 3):
            self.timers[r - 2]["preset"] = v
        elif bank == 0 and r == 4:
            if v & 0x80:                                 # flags off
                self.status &= ~0x60
                if not self.status & self.mask:
                    self.status &= 0x7F
            else:
                self.mask = ~v & 0x60
                self.status &= self.mask
                if self.status:
                    self.status |= 0x80
                for i, tm in enumerate(self.timers):
                    on = bool(v & (1 << i))
                    if on and not tm["run"]:
                        tm["next"] = t + (256 - tm["preset"]) * tm["unit"]
                    tm["run"] = on
        elif bank == 1 and r == 5 and v & 2 and not self.new2_seen:
            self.status2, self.new2_seen = 2, True      # the first NEW2: one status read shows bit 1

    def fm_read(self, bank):
        return self.fm[bank][self.fm_latch[bank]]

    def wave_out(self, p, v, t):
        if not self.fm[1][5] & 2:
            return                                      # NEW2 = 0: the wave part ignores writes
        if p == 0x7E:
            self.wlatch = v
            return
        r = self.wlatch
        self.wave.append((t, r, v))
        if 0x08 <= r <= 0x1F:
            if t < self.load_until:
                self.load_clash.append((t, r))
            self.loads.append((t, r))
            self.load_until = t + LOAD_T
            tone = (self.wregs[0x20 + r - 8] & 1) << 8 | v
            good = False
            if tone >= 384 and (self.wregs[2] >> 2) & 7 == 4:     # headers at 200000h
                h = self.ram[(tone - 384) * 12:(tone - 383) * 12]
                if len(h) == 12 and h[8] == 0xF0:
                    start = (h[0] & 0x3F) << 16 | h[1] << 8 | h[2]
                    good = 0x200000 + 12 * 128 <= start < 0x200000 + len(self.ram)
            if not good:
                self.bad_loads.append((t, r, tone))
        if r == 3:
            v &= 0x3F
        elif r == 5:
            self.adr = (self.wregs[3] << 16) | (self.wregs[4] << 8) | v
        elif r == 6 and self.wregs[2] & 1:
            a = (self.adr & 0x3FFFFF) - 0x200000
            if 0 <= a < len(self.ram):
                self.ram[a] = v
            if self.counting:
                self.mem_n += 1
                self.mem_t = (self.mem_t[0] if self.mem_t else t, t)
            self.adr += 1
        self.wregs[r] = v

    def wave_in(self, p):
        if p == 0x7E:
            return 0xFF
        r = self.wlatch
        if r == 2:
            return (self.wregs[2] & 0x1F) | 0x20        # device ID 001
        if r == 6:
            if not self.wregs[2] & 1:
                return 0xFF
            a = self.adr & 0x3FFFFF
            self.adr += 1
            if a < 0x200000:
                return 0x00                             # the wave ROM (a dummy one)
            a -= 0x200000
            return self.ram[a] if a < len(self.ram) else 0xFF
        return self.wregs[r]

    def port_in(self, p, t):
        """IN from an OPL4 port (C4h-C7h, 7Eh/7Fh), None for any other."""
        if p in (0xC4, 0xC6):
            return self.read_status(t)
        if p in (0xC5, 0xC7):
            return self.fm_read((p >> 1) & 1)
        if p in (0x7E, 0x7F):
            return self.wave_in(p)
        return None

    def port_out(self, p, v, t):
        """OUT to an OPL4 port; False for any other."""
        if p in (0xC4, 0xC6):
            self.fm_select((p >> 1) & 1, v)
        elif p in (0xC5, 0xC7):
            self.fm_write((p >> 1) & 1, v, t)
        elif p in (0x7E, 0x7F):
            self.wave_out(p, v, t)
        else:
            return False
        return True


def segments(ms, t_from=0.0):
    """The MOD player's writes from the mod_start at or after t_from: (start
    writes, per tick the wave writes between two clears of timer 2's flag,
    the timer 2 periods written, T of the start, T of the stop or inf, the
    wave writes after the stop, the flag clears)."""
    fmw = ms.fmw
    starts = [t for t, b, r, v in fmw if (b, r, v) == (0, 4, 0x42) and t >= t_from]
    if not starts:
        return None
    t_start = starts[0]
    t_entry = max(t for t, b, r, v in fmw if (b, r, v) == (1, 5, 3) and t < t_start)
    stops = [t for t, b, r, v in fmw if (b, r, v) == (0, 4, 0x60) and t > t_start]
    t_stop = stops[0] if stops else float("inf")
    clears = [t for t, b, r, v in fmw if (b, r, v) == (0, 4, 0x80) and t_start < t < t_stop]
    periods = [(256 - v) or 256 for t, b, r, v in fmw if (b, r) == (0, 3) and t_entry < t < t_stop]
    marks = [t_start] + clears
    init, segs, tail = [], [[] for _ in marks], []
    k = 0
    for t, r, v in ms.wave:
        if t < t_entry:
            continue
        if t < t_start:
            init.append((r, v))
            continue
        if t > t_stop:
            tail.append((t, r, v))
            continue
        while k + 1 < len(marks) and t >= marks[k + 1]:
            k += 1
        segs[k].append((r, v))
    return dict(init=init, segs=segs, periods=periods, t_entry=t_entry, t_start=t_start, t_stop=t_stop,
                tail=tail, clears=clears)


def match_ticks(segs, writes, heads, stopped):
    """segs (as played) against the model's writes per tick (the last one the
    END's key offs) and the head lengths: -> (bad tick indexes, the last
    segment's check). A stop (mod_stop) before the END ends the last tick
    with every channel off, after its head and any part of the next tick's
    preparations."""
    n, alloff = len(writes) - 1, writes[-1]
    last = len(segs) - 1
    bad = [i for i in range(last) if i >= n or segs[i] != writes[i]]
    if not stopped:                         # the run ended first
        g = segs[last]
        last_ok = last < n and g == writes[last][:len(g)]
    elif last == n:                         # the END
        last_ok = segs[last] == alloff
    else:                                   # mod_stop
        g = segs[last]
        k = len(g) - len(alloff)
        last_ok = (last < n and k >= heads[last] and g[k:] == alloff and g[:k] == writes[last][:k])
    return bad, last_ok
