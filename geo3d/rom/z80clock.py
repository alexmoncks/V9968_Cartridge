#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""MSX time for the pip z80 emulator (Z80Machine): its T-states plus the
MSX's wait state on every M1 cycle (each opcode fetch; two for a prefixed
instruction), which the emulator does not count. R counts the M1 cycles
(7 bits), so the machine runs in slices short enough that R cannot wrap
between two readings (SLICE T-states: at most ~106 M1 cycles), and the
T-states come from its tick counter (frame_tick, exact where ticks_to_stop
drops what an instruction runs past the limit). Used by run_rom_z80.py and
by the MOD player's cost measure (modcost.py), so that their times are the
ones openMSX shows for a 3.58 MHz MSX.
"""

SLICE = 400                 # T-states per run: < 128 M1 cycles (4 T each at least), plus one instruction
FRAME = 100000              # the emulator's frame_tick wraps here
BREAKPOINT_HIT = 1 << 0


class MsxClock:
    def __init__(self, m, m1_wait=True):
        self.m = m
        self.m1_wait = m1_wait
        self.ft = m.frame_tick
        self.r = m.r & 0x7F
        self.t = 0          # T-states so far
        self.m1 = 0         # M1 cycles so far

    def now(self):
        """The time so far in MSX clock cycles (3.58 MHz): T-states + M1
        waits. Exact at any point, e.g. inside an I/O callback."""
        m = self.m
        ft = m.frame_tick
        self.t += (ft - self.ft) % FRAME
        self.ft = ft
        r = m.r & 0x7F
        self.m1 += (r - self.r) & 0x7F
        self.r = r
        return self.t + self.m1 if self.m1_wait else self.t

    def run(self, n):
        """Runs about n T-states (in slices); True if it stopped at a
        breakpoint (or HALT) first."""
        m = self.m
        end = self.t + n
        while True:
            m.ticks_to_stop = SLICE
            ev = m.run()
            self.now()
            if ev & BREAKPOINT_HIT or m.halted:
                return True
            if self.t >= end:
                return False

    def step_over(self):
        """One instruction past a breakpoint."""
        self.m.ticks_to_stop = SLICE
        self.m.step_over_breakpoint()
        self.now()
