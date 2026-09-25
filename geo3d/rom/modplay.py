#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""ProTracker MOD on the MoonSound's wave part (OPL4, YMF278B), for the demo ROM.

The ROM holds the MOD itself and geo3d_modplay.asm plays it the way a MOD
player does: the Z80 reads the pattern data row by row, runs ProTracker's
effects tick by tick at the song's own speed and BPM (the OPL4's timer 2),
and plays the MOD's own 8-bit samples, which it uploads to the MoonSound's
sample RAM. Nothing is converted to FM or PSG, and there is no pre-rendered
stream: this file only decides which part of the MOD goes in the ROM, makes
the OPL4 lookup tables, and models the Z80 player exactly so the tests can
check it.

  trim    the part of the MOD the ROM plays (the demo ROM: the whole song,
          looping; the test ROM: its first seconds): the positions reached
          (song length cut there), the patterns up to the highest they use,
          the samples they name (the others: length 0, no data). The file's
          own bytes otherwise. The ProTracker model below must play it
          exactly as it plays the whole file (a looping song: over 3 passes,
          so the second and third start with what the first left in the
          channels), or the build stops.
  tables  period -> OPL4 pitch (4096 entries), BPM -> timer 2 period, volume
          played (1/4 units) and fade -> OPL4 level, ProTracker's period
          tables, the vibrato / tremolo waveforms.
  Rt      what the Z80 does with a MOD, byte for byte: the MOD header as it
          parses it, the sample RAM it fills (tone headers it computes, the
          samples as they are in the file), the 9xx start offsets that need
          a tone of their own (found here by scanning the patterns; the ROM
          holds the list, after the tables: tt_bytes), and every wave
          register write of every tick.

The ProTracker model (Player) follows ProTracker 2 as OpenMPT plays an M.K.
file in its ProTracker mode, the reference the test measures against:
periods in 1/4 units from ProTracker's finetune tables, the tempo (Fxx >=
20h) from the tick after the one that reads it, the sample properties of an
instrument number loaded on tick 0 even with a note delay, the 9xx offset
that stacks on notes without an instrument number, the out-of-range note
delay whose pitch shows on the next row; and ProTracker 2's rules (pt2-clone)
where a review found them to matter, checked against OpenMPT: the last
EEx of a row counts; a jump or a break after a row delay skips its target
row; a Bxx clears the row of a Dxx before it; past the song's end the
song goes on at position 0, on the row a Dxx gave (its restart: it loops);
EDx and E9x without a note play again on each EEx repeat; 9xx without a
note moves the stacked offset; E5x without a note sets the finetune; E9x
with a lone instrument number retriggers that sample (at the finetune the
row started with); a lone empty sample sets its volume. Four more cases
follow ProTracker 2 where OpenMPT (the audio test's reference) plays them
otherwise, and the build takes them: an arpeggio on a period that a slide
left between two notes plays that period on its base step (OpenMPT the
note's); an arpeggio's 0 / x / y steps start again on each EEx repeat of
its row (OpenMPT goes on counting); ECx with x at or past the speed never
cuts on a row with EEx (OpenMPT counts the ticks across the repeats); an
Fxx tempo on a row with EEx takes effect from its second tick, the first
repeat at speed 1 (OpenMPT from the next row, so its later notes come
earlier or later by up to a tick's difference). Effects: 0
arpeggio, 1/2 porta, 3 tone porta, 4 vibrato, 5 porta + slide, 6 vibrato
+ slide, 7 tremolo, 9 offset, A slide, B jump, C volume, D break, E1/E2
fine porta, E4 vibrato waveform, E5 finetune, E6 loop, E7 tremolo
waveform, E9 retrigger, EA/EB fine slides, EC cut, ED delay, EE row delay,
F speed / tempo. Not played: E0/E8 (ignored), E3x glissando and EFx (the
build stops if a song uses them), 8 panning (the Amiga pans hard: channels
0 and 3 left, 1 and 2 right). The ROM player does not follow a sample's
play position (the OPL4 plays the sample), so it has no instrument swap
(an instrument number without a note while a sample loops, heard at the
loop end in ProTracker) and does not start the sample of a lone instrument
number on a channel whose one-shot ran out (ProTracker plays its loop);
Player(track=False) models that, and the build stops if the song needs
it within what the ROM plays. It also stops for what ProTracker and OpenMPT
play differently: an E6x loop on a row with EEx, or with Bxx / Dxx; a
sample looped from 0 on a loop shorter than itself (ProTracker plays it
whole first).

Vibrato and tremolo as OpenMPT plays them in a MOD (ProcessVibrato,
ProcessTremolo): 4xy / 7xy keep x (speed) and y (depth) unless 0, 6xy is
vibrato with its memory plus the volume slide xy; a position 0..63 moves on
by the speed on every tick but a row's first (and its EEx repeats), which
play the plain period and volume; the waveform (E4x / E7x, x & 3: sine,
ramp down, square, random: OpenMPT's 64-entry tables, MOD_SINE and
MOD_RANDOM) at the position times the depth adds depth * w / 16 to the
period in 1/4 units (the ramp upside down, as FastTracker 2), or depth * w
/ 8 to the volume in 1/4 units (0..256; not at volume 0, and only while a
sample plays), both truncated towards 0. The tremolo's ramp is ProTracker's
(it turns over with the vibrato position's half). A note (a trigger:
retrigger and note delay too, tone portamento only when nothing sounds)
sets both positions to 0 unless E4x / E7x has bit 2 (x + 4); E4x / E7x on a
note's row takes effect after it. ProTracker and this model play B-3 of
finetune 0 at period 113 where OpenMPT keeps it at its Amiga limit (453 in
1/4 units, 3.8 cents higher).

OPL4 mapping (geo3d_modplay.asm):
  pitch   the chip plays 44100 * (1024 + FN) / 1024 * 2^(OCT - 1) samples
          per second; OCT and FN are the nearest to PAULA / period
          (register 38h = OCT << 4 | FN >> 7, 20h = FN << 1 | 1 (tone bit 8)).
  level   register 50h = TL << 1 | 1 (level direct): TL = the TL whose gain
          (0.375 dB steps, linear within each 6 dB) is nearest to the volume
          played / 256 (4 * volume, or the tremolo's), plus the fade's
          attenuation.
  key     68h = A0h | pan on, 20h | pan off (bit 5: LFO off); pan 9 is left
          only, 7 right only.
  timing  the OPL4's FM timer 2 (323.13 us steps) set to the tick length,
          2.5 / BPM s, in whole steps with a 16-bit fraction the player
          dithers (acc += frac, one more step on the carry): the ticks stay
          on ProTracker's time for good.
  notes   MOD channel c plays on OPL4 channels c, c + 8, c + 16 in turn. The
          Z80 works one tick ahead: right after tick i starts it writes tick
          i's key ons (all channels), key offs, pitch and level changes, then
          works out tick i + 1 and prepares the channel of each note that
          starts there (pitch, level, and the tone number, which loads the
          tone header, 0.3 ms): a note's tick only keys it on.
  tones   headers of tones 384-511 at 200000h (register 2 = 10h): tone 384 +
          s - 1 is sample s from its start, the next ones the 9xx start
          offsets the scan found (Rt.scan, at build time: MP_T_TT). A looped sample loops from its loop start
          to its loop end; a one-shot ends on 2 zero bytes after its data.

The MOD itself is an input: music you do not own stays out of the
repository, and so does everything made from it (keep it in out/).
"""
import argparse
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import music  # noqa: E402  (the MOD file parser: music._mod_parse)

PAULA = 3546895                      # PAL Paula clock (OpenMPT's for MOD): PAULA / period Hz
OPL4_CLOCK = 33868800
OPL4_RATE = OPL4_CLOCK / 768         # 44100 Hz: the wave part's output rate
T2_UNIT = 72 * 38 * 4 / OPL4_CLOCK   # 323.13 us: one step of FM timer 2
SRAM_BASE = 0x200000                 # sample RAM, after the 2 MB wave ROM
BLOCK = 128 * 1024                   # sample RAM is detected in 128 KB blocks
TONE0 = 384                          # user tones 384-511 (headers in RAM)
NTONES = 128
SMP_BASE = SRAM_BASE + 12 * NTONES   # the samples follow the 128 headers
BANK = 16384

PT_TABLE = [856, 808, 762, 720, 678, 640, 604, 570, 538, 508, 480, 453,       # finetune 0,
            428, 404, 381, 360, 339, 320, 302, 285, 269, 254, 240, 226,       # C-1 .. B-3
            214, 202, 190, 180, 170, 160, 151, 143, 135, 127, 120, 113]
PT_OCTAVES = ([2 * p for p in (1712, 1616, 1524, 1440, 1356, 1280, 1208, 1140, 1076, 1016, 960, 906)]
              + [1712, 1616, 1524, 1440, 1356, 1280, 1208, 1140, 1076, 1016, 960, 907]
              + PT_TABLE + [107, 101, 95, 90, 85, 80, 75, 71, 67, 63, 60, 56,
                            53, 50, 47, 45, 42, 40, 37, 35, 33, 31, 30, 28])
TUNED = [                            # ProTracker's octave 0 per finetune 0..15 (OpenMPT's table)
    [1712, 1616, 1524, 1440, 1356, 1280, 1208, 1140, 1076, 1016, 960, 907],
    [1700, 1604, 1514, 1430, 1348, 1274, 1202, 1134, 1070, 1010, 954, 900],
    [1688, 1592, 1504, 1418, 1340, 1264, 1194, 1126, 1064, 1004, 948, 894],
    [1676, 1582, 1492, 1408, 1330, 1256, 1184, 1118, 1056, 996, 940, 888],
    [1664, 1570, 1482, 1398, 1320, 1246, 1176, 1110, 1048, 990, 934, 882],
    [1652, 1558, 1472, 1388, 1310, 1238, 1168, 1102, 1040, 982, 926, 874],
    [1640, 1548, 1460, 1378, 1302, 1228, 1160, 1094, 1032, 974, 920, 868],
    [1628, 1536, 1450, 1368, 1292, 1220, 1150, 1086, 1026, 968, 914, 862],
    [1814, 1712, 1616, 1524, 1440, 1356, 1280, 1208, 1140, 1076, 1016, 960],
    [1800, 1700, 1604, 1514, 1430, 1350, 1272, 1202, 1134, 1070, 1010, 954],
    [1788, 1688, 1592, 1504, 1418, 1340, 1264, 1194, 1126, 1064, 1004, 948],
    [1774, 1676, 1582, 1492, 1408, 1330, 1256, 1184, 1118, 1056, 996, 940],
    [1762, 1664, 1570, 1482, 1398, 1320, 1246, 1176, 1110, 1048, 988, 934],
    [1750, 1652, 1558, 1472, 1388, 1310, 1238, 1168, 1102, 1040, 982, 926],
    [1736, 1640, 1548, 1460, 1378, 1302, 1228, 1160, 1094, 1032, 974, 920],
    [1724, 1628, 1536, 1450, 1368, 1292, 1220, 1150, 1086, 1026, 968, 914]]
NOTE_C1 = 48                         # note number of C-1 (period 856) in OpenMPT's 0-based scale
QMIN, QMAX = 113 * 4, 856 * 4        # Amiga limits of the period slides (1/4 periods)
AMIGA_PAN = [9, 7, 7, 9]             # LRRL, hard as on the Amiga: 9 = left only, 7 = right only
KEY_ON, KEY_OFF = 0xA0, 0x20         # register 68h, | pan (bit 5 = LFO reset: the LFO stays off)
SLOTS = 3                            # OPL4 channels per MOD channel: c, c + 8, c + 16
PER_64K = 0xFFFF                     # the Z80's 16-bit stand-in for the arpeggio's period 65536


# ----------------------------------------------------------------- periods
def period_of_note(n, ft):
    """OpenMPT's GetPeriodFromNote for a ProTracker MOD, in 1/4 periods: n is
    the 0-based note (NOTE_C1 = C-1), ft the finetune nibble 0..15."""
    if ft or n < 24 or n >= 24 + len(PT_OCTAVES):
        return (TUNED[ft][n % 12] << 5) >> (n // 12)
    return PT_OCTAVES[n - 24] << 2


def note_of_period(q, ft):
    """OpenMPT's GetNoteFromPeriod: the lowest note whose period is <= q."""
    lo, count = 0, 120
    while count > 0:
        step = count // 2
        mid = lo + step
        p = period_of_note(mid, ft)
        if p > q or not p:
            lo = mid + 1
            count -= step + 1
        else:
            count = step
    return lo


def pattern_note(per):
    """The note of a pattern period: its place in the finetune 0 table (the
    first entry it is not below, as ProTracker looks it up)."""
    for i, p in enumerate(PT_TABLE):
        if per >= p:
            return NOTE_C1 + i
    return NOTE_C1 + len(PT_TABLE) - 1


# -------------------------------------------------------- vibrato, tremolo
MOD_SINE = [0, 12, 25, 37, 49, 60, 71, 81, 90, 98, 106, 112, 117, 122, 125, 126]    # OpenMPT's
MOD_SINE = MOD_SINE + [127] + MOD_SINE[:0:-1]                                     # ModSinusTable
MOD_SINE = MOD_SINE + [-v for v in MOD_SINE]
MOD_RANDOM = [98, -127, -43, 88, 102, 41, -65, -94, 125, 20, -71, -86, -70, -32, -16, -96,     # and its
              17, 72, 107, -5, 116, -69, -62, -40, 10, -61, 65, 109, -18, -38, -13, -76,   # ModRandomTable
              -23, 88, 21, -94, 8, 106, 21, -112, 6, 109, 20, -88, -30, 9, -127, 118,
              42, -34, 89, -4, -51, -72, 21, -29, 112, 123, 84, -101, -92, 98, -54, -95]


def wave(kind, pos):
    """The waveform of E4x / E7x (kind & 3: sine, ramp down, square, random) at
    position pos (0..63), -127..127, as OpenMPT has them (GetVibratoDelta)."""
    k = kind & 3
    if k == 0:
        return MOD_SINE[pos]
    if k == 1:
        return (0 if pos < 32 else 255) - 4 * pos
    if k == 2:
        return 127 if pos < 32 else -127
    return MOD_RANDOM[pos]


def cdiv(a, b):
    """a / b, truncated towards 0 (C)."""
    q = abs(a) // b
    return q if a >= 0 else -q


def vib_delta(kind, pos, depth):
    """What vibrato adds to the period (1/4 units) at this position: OpenMPT's
    ProcessVibrato for a MOD, depth y (4xy) * 4 >> 6 of the waveform, the ramp
    upside down (as FastTracker 2), so period + delta goes down in pitch first."""
    w = wave(kind, pos)
    if kind & 3 == 1:
        w = -w
    return -cdiv(-w * 4 * depth, 64)


def trem_delta(kind, pos, vpos, depth):
    """What tremolo adds to the volume (1/4 units, 0..256) at this position:
    OpenMPT's ProcessTremolo for a MOD, depth y * 4 >> 5 of the waveform; its
    ramp down is ProTracker's, which looks at the vibrato position vpos."""
    if kind & 3 == 1:
        r = (pos * 4) & 0x7F
        if vpos >= 32:
            r ^= 0x7F
        w = -r if pos >= 32 else r
    else:
        w = wave(kind, pos)
    return cdiv(w * 4 * depth, 32)


# ------------------------------------------------------------------ player
class Chan:
    def __init__(self):
        self.sel = 0             # the instrument: last sample number given
        self.vol = 0             # 0..64
        self.ft = 0              # finetune nibble
        self.per = 0             # period, 1/4 units (0: none yet)
        self.dest = 0            # tone portamento target
        self.pspeed = 0
        self.offset = 0          # 9xx parameter memory
        self.stack = 0           # ProTracker's stacked 9xx offset (bytes)
        self.play = None         # sounding: dict(smp, pos, end, loop)
        self.cur = 0             # the sample it plays (or played last)
        self.note = 0            # the note it plays (a retrigger restarts it)
        self.swap = 0            # sample to swap to at the end of this loop
        self.delay = None        # note delay: (tick, note)
        self.late = None         # out-of-range note delay: the note (heard on the next row)
        self.loop_row = 0
        self.loop_n = 0
        self.rft = 0             # finetune of this row's retriggers (E9x)
        self.gliss = 0           # E3x (glissando: not played, the build refuses it with 3xx / 5xy)
        self.vspd = self.vdep = self.vpos = self.vtype = 0     # vibrato: memory, position 0..63, E4x
        self.tspd = self.tdep = self.tpos = self.ttype = 0     # tremolo: the same, E7x


class Player:
    """ProTracker 2 as OpenMPT plays an M.K. MOD (see the module doc).
    track=False: no play positions (the ROM player's view): a sample sounds
    from its trigger on, and an instrument swap never happens."""

    def __init__(self, smps, order, pats, nch, track=True):
        self.smps, self.order, self.pats, self.nch, self.track = smps, order, pats, nch, track
        self.unplayable = {}                        # what the ROM player does not play: first (order, row)

    def trigger(self, ch, s, note, pos, out, ft=None):
        """(Re)starts sample s at position pos (bytes): the event of this tick
        (its period from the channel's finetune, or ft)."""
        if not 0 < s < len(self.smps):
            return
        smp = self.smps[s]
        ch.cur, ch.note = s, note
        if ch.vtype < 4:                            # a note restarts the waveforms (E4x / E7x
            ch.vpos = 0                             # below 4), an empty sample's too
        if ch.ttype < 4:
            ch.tpos = 0
        if not smp["data"]:
            ch.play = None                          # an empty sample: the channel stops
            out["stop"] = True
            return
        ch.per = period_of_note(note, ch.ft if ft is None else ft)
        loop = smp["loop"]
        end = loop[0] + loop[1] if loop else len(smp["data"])
        pos = min(pos, end - 1)
        ch.play = dict(smp=s, pos=float(pos), end=end, loop=(loop[0], loop[0] + loop[1]) if loop else None)
        ch.swap = 0
        out["trig"] = (s, pos)
        out.pop("stop", None)

    def run(self, seconds, max_ticks=10 ** 6, passes=None):
        """Plays from order 0 for `seconds` (or `passes` whole passes of the
        song). Returns the ticks: dict(t, dur, bpm, speed, pos=(order, row,
        tick), ch=[per channel dict(per (1/4 period played, arpeggio or
        vibrato included), base (the channel's period), vol, v4 (volume
        played, 1/4 units: 4 * vol or the tremolo's), trig=(sample, byte
        offset) or None, swap=(sample, t) or None, stop, sounding)]).
        self.pass_starts: the tick where each pass of the song starts: 0,
        then each time the song gets back to a row it played in this pass
        through a jump, a break or its end (not an E6x loop)."""
        nch = self.nch
        chans = [Chan() for _ in range(nch)]
        speed, bpm = 6, 125
        oi, row, t = 0, 0, 0.0
        ticks = []
        self.pass_starts, seen, moved = [0], set(), False
        while t < seconds and len(ticks) < max_ticks:
            if oi >= len(self.order):
                oi = 0                              # the end of the song: it starts over at
                moved = True                        # position 0, on the row a Dxx gave (ProTracker)
            if moved and (oi, row) in seen:         # the song came back: a new pass
                self.pass_starts.append(len(ticks))
                seen = set()
                if passes is not None and len(self.pass_starts) > passes:
                    break                           # (that pass is not played)
            seen.add((oi, row))
            moved = False
            cells = self.pats[self.order[oi]][row]
            jump = brk = tempo = None
            pdelay = None
            loop_to = None
            for s, per, e, x in cells:              # song-wide effects of the row
                if e == 0xF:
                    if x == 0:
                        raise ValueError(f"F00 (stop) at order {oi} row {row}: not supported")
                    if x < 0x20:
                        speed = x
                    else:
                        tempo = x
                elif e == 0xB:
                    jump = x
                    brk = None                      # ProTracker: Bxx clears a break row before it
                elif e == 0xD:
                    brk = (x >> 4) * 10 + (x & 15)
                    if brk > 63:
                        brk = 0
                elif e == 0xE and x >> 4 == 0xE:
                    pdelay = x & 15                 # (the last EEx of the row)
            pdelay = pdelay or 0
            if any(e == 0xE and x >> 4 == 6 and x & 15 for s, per, e, x in cells):
                # an E6x loop with a row delay, or with a jump or break: ProTracker runs it on
                # each repeat and shares the break row with it (and OpenMPT differs again)
                if pdelay and "E6x+EEx" not in self.unplayable:
                    self.unplayable["E6x+EEx"] = f"E6x with EEx at order {oi} row {row}"
                if (jump is not None or brk is not None) and "E6x+B/D" not in self.unplayable:
                    self.unplayable["E6x+B/D"] = f"E6x with Bxx or Dxx at order {oi} row {row}"
            for c, (s, per, e, x) in enumerate(cells):   # a note of an out-of-range delay shows now
                ch = chans[c]
                if ch.late is not None and not per:
                    ch.per = period_of_note(ch.late, ch.ft)
                ch.late = None
            for k in range(speed * (1 + pdelay)):
                tr = k % speed
                outs = []
                for c, cell in enumerate(cells):
                    out = {}
                    loop_to = self.chan_tick(chans[c], cell, k, tr, speed, row, out, loop_to)
                    outs.append(out)
                dur = 2.5 / bpm
                tick = dict(t=t, dur=dur, bpm=bpm, speed=speed, pos=(oi, row, k), ch=outs)
                for c, ch in enumerate(chans):
                    o = outs[c]
                    o["vol"] = ch.vol
                    o.setdefault("v4", 4 * ch.vol)
                    o.setdefault("per", ch.per)
                    o["base"] = ch.per
                    o["sounding"] = ch.play is not None
                    o.setdefault("trig", None)
                    if self.track:
                        self.advance(ch, o, t, dur)
                    o.setdefault("swap", None)
                ticks.append(tick)
                t += dur
                if tempo is not None and k == 0:
                    bpm = tempo                     # ProTracker: from the next tick on
            if jump is not None or brk is not None:
                if jump is not None:
                    oi = jump
                else:
                    oi += 1
                row = brk if brk is not None else 0
                if pdelay:                          # ProTracker: after a row delay the target
                    row += 1                        # row itself is skipped
                    if row == 64:
                        row, oi = 0, oi + 1
                moved = True
            elif loop_to is not None:
                row = loop_to
            else:
                row += 1
                if row == 64:
                    row, oi = 0, oi + 1
        return ticks

    def chan_tick(self, ch, cell, k, tr, speed, row, out, loop_to):
        s, per, e, x = cell
        hi, lo = x >> 4, x & 15
        porta = e in (3, 5)
        if k == 0:
            delay = e == 0xE and hi == 0xD and lo > 0
            ch.delay = None                         # (a note delay plays on every repeat of EEx)
            old_ft = ch.ft
            if s:
                smp = self.smps[s] if s < len(self.smps) else None
                if smp:
                    ch.vol = smp["vol"]             # (an empty sample's too)
                    ch.ft = smp["ft"] & 15
                ch.stack = 0                        # an instrument number resets the stacked offset
                if ch.play and (porta or not per or delay):
                    ch.swap = s if ch.play["smp"] != s else 0   # ProTracker: swapped in at the loop end
                elif (self.track and ch.play is None and not per and smp and smp["data"] and smp["loop"]
                      and ch.cur and ch.per):
                    # ProTracker: the channel's one-shot ran out, and Paula plays this sample's
                    # loop from here (OpenMPT: the sample, from its start): the ROM player can not
                    # (it does not know that the one-shot ended): the build refuses it
                    self.trigger(ch, s, ch.note, 0, out)
                ch.sel = s
            if e == 9:
                if x:
                    ch.offset = x
                if not per:                         # ProTracker: 9xx without a note moves the
                    ch.stack += ch.offset * 256     # stacked offset all the same
            if e == 0xE and hi == 5:
                ch.ft = lo                          # E5x: with or without a note
            ch.rft = old_ft if s and not per else ch.ft     # a retrigger with a lone instrument
            if e == 0xE and hi == 3:                        # number: its sample, the pitch as it was
                ch.gliss = lo
            if ch.gliss and porta and "E3x" not in self.unplayable:
                self.unplayable["E3x"] = f"E3{ch.gliss:X} (glissando) with {e:X}xx at row {row}"
            if e == 0xE and hi == 0xF and lo and "EFx" not in self.unplayable:
                self.unplayable["EFx"] = f"EF{lo:X} (funk repeat) at row {row}"
            if per:
                note = pattern_note(per)
                if porta:
                    q = period_of_note(note, ch.ft)
                    ch.dest = q
                    if not ch.per:
                        ch.per = q
                    if ch.play is None and ch.sel:  # nothing sounds: the sample starts anyway,
                        old = ch.per                # at the period it had, and slides
                        self.trigger(ch, ch.sel, note, 0 if s else ch.stack, out)
                        ch.per = old or q
                elif delay:
                    if lo < speed:
                        ch.delay = (lo, note)
                    else:
                        ch.late = note              # never played, but its pitch shows next row
                else:
                    if e == 9:
                        pos = ch.stack + ch.offset * 256
                        ch.stack += 2 * ch.offset * 256
                    else:
                        pos = ch.stack
                    self.trigger(ch, ch.sel, note, pos, out)
            if e == 3 and x:
                ch.pspeed = x
            elif e == 4 or e == 7:                  # vibrato / tremolo memory: speed x, depth y
                if e == 4:
                    ch.vspd, ch.vdep = hi or ch.vspd, lo or ch.vdep
                else:
                    ch.tspd, ch.tdep = hi or ch.tspd, lo or ch.tdep
        if tr == 0:                                 # first tick of the row (and of its EEx repeats)
            if e == 0xC:
                ch.vol = min(x, 64)
            elif e == 0xE:
                if hi == 1 and ch.per:
                    ch.per = max(QMIN, ch.per - 4 * lo)
                elif hi == 2 and ch.per:
                    ch.per = min(QMAX, ch.per + 4 * lo)
                elif hi == 0xA:
                    ch.vol = min(64, ch.vol + lo)
                elif hi == 0xB:
                    ch.vol = max(0, ch.vol - lo)
                elif hi == 0xC and lo == 0:
                    ch.vol = 0
                elif hi == 4:
                    ch.vtype = lo & 7               # E4x / E7x: the waveform (+4: a note does not
                elif hi == 7:                       # restart it), after this row's note
                    ch.ttype = lo & 7
                elif hi == 9 and lo and not per:
                    self.retrig(ch, out, s)
                elif hi == 6 and k == 0:
                    if lo == 0:
                        ch.loop_row = row
                    elif ch.loop_n == 0:
                        ch.loop_n, loop_to = lo, ch.loop_row
                    else:
                        ch.loop_n -= 1
                        if ch.loop_n:
                            loop_to = ch.loop_row
        else:                                       # the other ticks
            if e == 0 and x:
                pass                                # arpeggio: below
            elif e == 1 and ch.per:
                ch.per = max(QMIN, ch.per - 4 * x)
            elif e == 2 and ch.per:
                ch.per = min(QMAX, ch.per + 4 * x)
            elif porta and ch.per and ch.dest:
                d = 4 * ch.pspeed
                if ch.per < ch.dest:
                    ch.per = min(ch.dest, ch.per + d)
                elif ch.per > ch.dest:
                    ch.per = max(ch.dest, ch.per - d)
                if ch.per == ch.dest:
                    ch.dest = 0                     # ProTracker: target reached, portamento off
            if e in (5, 6, 0xA):
                ch.vol = min(64, ch.vol + hi) if hi else max(0, ch.vol - lo)
            if e == 0xE:
                if hi == 9 and lo and tr % lo == 0:
                    self.retrig(ch, out, s)
                elif hi == 0xC and tr == lo:
                    ch.vol = 0
                elif hi == 0xD and ch.delay and tr == ch.delay[0]:
                    self.trigger(ch, ch.sel, ch.delay[1], ch.stack, out)
            elif e == 4 or e == 6:                  # vibrato: the period played; the position
                if ch.per:                          # moves on even without one
                    out["per"] = ch.per + vib_delta(ch.vtype, ch.vpos, ch.vdep)
                ch.vpos = (ch.vpos + ch.vspd) & 63
            elif e == 7 and ch.play is not None:    # tremolo, while a sample plays: the volume
                if ch.vol:                          # played (1/4 units), not at volume 0
                    out["v4"] = max(0, min(256, 4 * ch.vol + trem_delta(ch.ttype, ch.tpos, ch.vpos, ch.tdep)))
                ch.tpos = (ch.tpos + ch.tspd) & 63
        if e == 0 and x and ch.per:                 # arpeggio: the played period only
            n = note_of_period(ch.per, ch.ft) + (0, hi, lo)[tr % 3]
            if tr % 3:
                if n == 84:
                    out["per"] = 65536              # ProTracker's wrap-around: period 0
                else:
                    out["per"] = period_of_note(n - 37 if n > 84 else n, ch.ft)
        return loop_to

    def retrig(self, ch, out, s=0):
        """The last note again, from its start: its sample, or the row's lone
        instrument number's (ProTracker plays the sample it loaded), at the
        finetune of the row's start."""
        if ch.cur and ch.note:
            self.trigger(ch, s or ch.cur, ch.note, ch.stack, out, ch.rft)

    def advance(self, ch, o, t, dur):
        """Moves the sounding sample through this tick: a one-shot runs out,
        a loop wraps, and a pending sample swap happens where the loop (or
        the sample) ends."""
        p = ch.play
        if p is None:
            return
        per = o["per"]
        if not per:
            return
        rate = PAULA * 4 / per                      # bytes per second
        left = dur
        while left > 0:
            pos = p["pos"]
            reach = pos + rate * left
            if reach < p["end"]:
                p["pos"] = reach
                return
            tc = (p["end"] - pos) / rate            # this much of the tick until the end
            left -= tc
            if ch.swap:
                s = ch.swap
                ch.swap = 0
                smp = self.smps[s]
                o["swap"] = (s, t + dur - left)
                ch.cur = s
                if smp["loop"] and smp["data"]:
                    ls, ll = smp["loop"]
                    ch.play = p = dict(smp=s, pos=float(ls), end=ls + ll, loop=(ls, ls + ll))
                else:
                    ch.play = None                  # a one-shot swapped in plays nothing
                    return
            elif p["loop"]:
                p["pos"] = float(p["loop"][0])
            else:
                ch.play = None                      # the one-shot has run out
                return


def parse(data):
    if music.mod_channels(data) is None:
        raise ValueError("not a ProTracker MOD")
    return music._mod_parse(data)


def events(ticks, heard=None):
    """What a tick plays, for comparing two players: position, tempo, and
    per channel the period, volume, volume played (tremolo), (re)start, stop
    and sample swap. heard: per tick and channel, whether a sample sounds
    (the reference player's o["sounding"]); where none does, the volume
    played is left out: ProTracker's tremolo stops there, the ROM player's
    (which does not know that a one-shot ran out) goes on, unheard."""
    return [(tk["pos"], tk["bpm"], tk["speed"],
             tuple((o["per"], o["vol"], o["v4"] if heard is None or heard[i][c] else None, o["trig"],
                    bool(o.get("stop")), o["swap"] and o["swap"][0])
                   for c, o in enumerate(tk["ch"]))) for i, tk in enumerate(ticks)]


# -------------------------------------------------------------------- trim
def trim(data, ticks):
    """The part of the MOD that plays in these ticks, as a MOD file: the song
    length cut after the last position reached (the rest of the order table
    0, so the pattern count ends at the highest pattern those positions
    use), the patterns up to that one, the samples named in the rows that
    play (the others: length 0 in their header, no data). Every other byte
    is the file's."""
    nch = music.mod_channels(data)
    npat0 = max(data[952:1080]) + 1
    songlen = max(tk["pos"][0] for tk in ticks) + 1
    order = list(data[952:952 + songlen])
    npat = max(order) + 1
    row = 256 * nch
    named = set()
    for oi, r in {tk["pos"][:2] for tk in ticks}:
        for c in range(nch):
            b = data[1084 + data[952 + oi] * row + (r * nch + c) * 4:][:4]
            s = (b[0] & 0xF0) | (b[2] >> 4)
            if s:
                named.add(s)
    head = bytearray(data[:1084])
    body = bytearray()
    at = 1084 + npat0 * row
    for s in range(1, 32):
        o = 20 + 30 * (s - 1)
        n = 2 * (data[o + 22] << 8 | data[o + 23])
        if s in named:
            body += data[at:at + n]
        else:
            head[o + 22:o + 24] = b"\0\0"
        at += n
    head[950] = songlen
    head[952:1080] = bytes(order) + bytes(128 - songlen)
    return bytes(head + data[1084:1084 + npat * row] + body)


# ---------------------------------------------------------------- registers
def opl4_header(addr, loop, end):
    """12-byte tone header: 8-bit samples at addr, loop point and end
    (exclusive) in samples from the start; the envelope holds full level
    (AR 15, DL 0, D2R 0) and releases in 3.6 ms (RR 15)."""
    e = (0x10000 - end) & 0xFFFF
    return bytes([(addr >> 16) & 0x3F, (addr >> 8) & 0xFF, addr & 0xFF,
                  loop >> 8, loop & 0xFF, e >> 8, e & 0xFF, 0x00, 0xF0, 0x00, 0xFF, 0x00])


def opl4_pitch(q):
    """(OCT, FN) for a period of q quarter units: the sample rate PAULA * 4 / q."""
    r = PAULA * 4 / q / OPL4_RATE
    o = math.floor(math.log2(r)) + 1
    fn = round((r / 2 ** (o - 1) - 1) * 1024)
    if fn >= 1024:
        o, fn = o + 1, 0
    o = max(-7, min(7, o))
    return o, max(0, min(1023, fn))


def pitch_regs(q):
    o, fn = opl4_pitch(q)
    return ((o & 15) << 4) | (fn >> 7), ((fn & 0x7F) << 1) | 1


def pitch_hz(r38, r20):
    """The rate the chip plays at for these register values (calcStep)."""
    o = ((r38 >> 4) ^ 8) - 8
    fn = ((r38 & 7) << 7) | (r20 >> 1)
    return OPL4_RATE * (1024 + fn) / 1024 * 2 ** (o - 1)


def tl_gain(tl):
    """The gain of TL 0..127 (openMSX vol_factor, measured on hardware)."""
    if tl >= 127:
        return 0.0
    e = tl * 4
    return (128 - (e & 63)) / 2 ** (7 + (e >> 6))


TL_GAINS = [tl_gain(t) for t in range(128)]


def level_reg(g):
    """Register 50h value for a gain 0..1 (MOD volume / 64)."""
    if g <= 0:
        return 0xFF
    tl = min(range(127), key=lambda t: abs(TL_GAINS[t] - g))
    if g < TL_GAINS[126] / 2:
        tl = 127
    return (tl << 1) | 1


VOL_TL = [level_reg(v / 256) >> 1 for v in range(257)]          # TL of each volume played, 1/4 units
FADE_TL = [127] + [min(127, round(-20 * math.log10(g / 64) / 0.375)) for g in range(1, 65)]


def level_of(v4, g64):
    """Register 50h for the volume played (v4: 1/4 units, 0..256; tremolo
    moves it in 1/4 steps, as OpenMPT): its TL plus the fade's (g64 = 64: no
    fade)."""
    tl = VOL_TL[v4] + FADE_TL[g64]
    return 0xFF if tl >= 127 else (tl << 1) | 1


def t2_period(bpm):
    """Timer 2 steps per tick at this BPM: (whole, 16-bit fraction)."""
    n = 2.5 / bpm / T2_UNIT
    w = math.floor(n)
    f = round((n - w) * 65536)
    if f == 65536:
        w, f = w + 1, 0
    assert 1 <= w <= 255, bpm
    return w, f


PITCH = [pitch_regs(max(q, 1)) for q in range(4096)]
PITCH_64K = pitch_regs(65536)


def pitch_of(q):
    """The registers the Z80 writes for a period: its 4096-entry table, and
    the arpeggio's 65536 (ProTracker's period 0) apart."""
    if q == 65536 or q == PER_64K:
        return PITCH_64K
    return PITCH[min(q, 4095)]


# The lookup tables geo3d_modplay.asm reads, one block in a ROM bank at
# MP_TAB (page 2); offsets:
TAB_PITCH = 0x0000          # 4096 x (38h, 20h), by 1/4 period
TAB_P64K = 0x2000           # (38h, 20h) of the period 65536
TAB_BPM = 0x2002            # 256 x (whole, fraction lo, hi): timer 2 per tick (BPM >= 32)
TAB_VOLTL = TAB_BPM + 768   # 257: TL of each volume played (1/4 units)
TAB_FADETL = TAB_VOLTL + 257  # 65: the fade's TL, by g64 = 0..64
TAB_PT = TAB_FADETL + 65    # 36 words: ProTracker's periods C-1..B-3
TAB_OCT = TAB_PT + 72       # 84 words: PT_OCTAVES
TAB_TUNED = TAB_OCT + 168   # 16 x 12 words: TUNED
TAB_SINE = TAB_TUNED + 384  # 32: MOD_SINE's first half (the second is its negative)
TAB_RANDOM = TAB_SINE + 32  # 64: MOD_RANDOM (signed bytes)
TAB_LEN = TAB_RANDOM + 64
TAB_TT = TAB_LEN            # then the MOD's 9xx tones (Rt.tt_bytes): sample, start (2) each


def tables():
    out = bytearray()
    for r38, r20 in PITCH:
        out += bytes([r38, r20])
    out += bytes(PITCH_64K)
    for bpm in range(256):
        w, f = t2_period(bpm) if bpm >= 32 else (0, 0)
        out += bytes([w, f & 0xFF, f >> 8])
    out += bytes(VOL_TL) + bytes(FADE_TL)
    for p in PT_TABLE + PT_OCTAVES + [x for row in TUNED for x in row]:
        out += bytes([p & 0xFF, p >> 8])
    out += bytes(MOD_SINE[:32]) + bytes(v & 0xFF for v in MOD_RANDOM)
    assert len(out) == TAB_LEN
    return bytes(out)


def tab_equ():
    return "\n".join(f"{n}: equ 0x{v:04x}" for n, v in (
        ("MP_T_PITCH", TAB_PITCH), ("MP_T_P64K", TAB_P64K), ("MP_T_BPM", TAB_BPM),
        ("MP_T_VOLTL", TAB_VOLTL), ("MP_T_FADETL", TAB_FADETL), ("MP_T_PT", TAB_PT),
        ("MP_T_OCT", TAB_OCT), ("MP_T_TUNED", TAB_TUNED), ("MP_T_SINE", TAB_SINE),
        ("MP_T_RANDOM", TAB_RANDOM), ("MP_T_TT", TAB_TT))) + "\n"


# ------------------------------------------------------------ the Z80's view
def z80_channels(data):
    """The channel count the Z80 accepts from the tag at 1080: M.K., M!K!,
    FLT4, or 1CHN..8CHN; None otherwise."""
    tag = bytes(data[1080:1084])
    if tag in (b"M.K.", b"M!K!", b"FLT4"):
        return 4
    if tag[1:] == b"CHN" and 0x31 <= tag[0] <= 0x38:
        return tag[0] - 0x30
    return None


class Rt:
    """geo3d_modplay.asm with the MOD `data` (the ROM's copy), byte for byte:
    the header as it parses it, the sample RAM it writes, the 9xx tones (the
    scan here; the ROM holds its list: tt_bytes), and (schedule) the wave
    register writes of every tick."""

    def __init__(self, data):
        self.data = data
        nch = z80_channels(data)
        if nch is None:
            raise ValueError(f"tag {bytes(data[1080:1084])!r}: the ROM player takes M.K., M!K!, FLT4, 1CHN..8CHN")
        self.nch = nch
        self.pans = [AMIGA_PAN[c % 4] for c in range(nch)]
        self.songlen = data[950]
        if not 1 <= self.songlen <= 128:
            raise ValueError(f"song length {self.songlen}")
        self.order = list(data[952:1080])
        self.npat = max(self.order) + 1
        self.pat_at = 1084
        at = 1084 + self.npat * 256 * nch
        ram = SMP_BASE
        self.smp = [None]
        for s in range(1, 32):
            o = 20 + 30 * (s - 1)
            lw = data[o + 22] << 8 | data[o + 23]
            if lw > 32767:
                raise ValueError(f"sample {s}: {2 * lw} bytes, a tone holds 65534 + 2")
            n = 2 * lw
            lsw = data[o + 26] << 8 | data[o + 27]
            llw = data[o + 28] << 8 | data[o + 29]
            ls, ll = 2 * lsw, 2 * llw
            looped = llw > 1 and ls < n
            end = ls + min(ll, n - ls) if looped else n
            d = dict(len=n, ls=ls if looped else 0, end=end, looped=looped, vol=min(64, data[o + 25]),
                     ft=data[o + 24] & 15, rom=at, adr=ram if n else 0)
            if n:
                if at + n > len(data):
                    raise ValueError(f"sample {s}: the file ends inside its data")
                ram += n + (0 if looped else 2)
            at += n
            self.smp.append(d)
        self.ram_end = ram
        self.blocks = -(-(ram - SRAM_BASE) // BLOCK)
        self.keys = [(s, 0) for s in range(1, 32)]
        self.scan()
        self.index = {k: i for i, k in enumerate(self.keys)}
        self.missing = set()

    def cell(self, p, r, c):
        b = self.data[self.pat_at + (p * 64 + r) * 4 * self.nch + 4 * c:][:4]
        return (b[0] & 0xF0) | (b[2] >> 4), ((b[0] & 15) << 8) | b[1], b[2] & 15, b[3]

    def scan(self):
        """The 9xx start offsets: the song's rows in order-list order, per
        channel the last instrument, the 9xx memory and ProTracker's stacked
        offset (as the notes would start them, jumps and loops aside)."""
        st = [[0, 0, 0] for _ in range(self.nch)]          # instrument, stack, 9xx memory

        def add(s, pos):
            if not s or not pos or not self.smp[s]["len"]:
                return
            pos = min(pos, self.smp[s]["end"] - 1)
            if (s, pos) not in self.keys and len(self.keys) < NTONES:
                self.keys.append((s, pos))
        for oi in range(self.songlen):
            for r in range(64):
                for c in range(self.nch):
                    s, per, e, x = self.cell(self.order[oi], r, c)
                    ch = st[c]
                    if s:
                        ch[0], ch[1] = s, 0
                    if e == 9 and x:
                        ch[2] = x
                    if e == 9 and not per:          # (9xx without a note: the stack moves)
                        ch[1] = min(0xFFFF, ch[1] + ch[2] * 256)
                    if per:
                        if e in (3, 5):
                            pos = 0 if s else ch[1]
                        elif e == 9:
                            pos = min(0xFFFF, ch[1] + ch[2] * 256)
                            ch[1] = min(0xFFFF, ch[1] + 2 * ch[2] * 256)
                        else:
                            pos = ch[1]
                        add(ch[0], pos)
                    elif e == 0xE and x >> 4 == 9 and x & 15:
                        add(ch[0], ch[1])

    def tt_bytes(self):
        """The 9xx tones (tone 384 + 31 on) for the ROM: sample, start (low,
        high) each, as mod_upload_init copies them to mp_tt."""
        return b"".join(bytes([s, pos & 0xFF, pos >> 8]) for s, pos in self.keys[31:])

    def tone(self, s, pos):
        """The tone index (tone 384 + index) of sample s from byte pos."""
        if pos == 0:
            return s - 1
        i = self.index.get((s, pos))
        if i is None:
            self.missing.add((s, pos))
            return s - 1
        return i

    def image(self):
        """The sample RAM from 200000h as the Z80 fills it: 128 tone headers
        (unused ones 0), then each sample's bytes from the file, a one-shot
        followed by 2 zero bytes."""
        out = bytearray()
        for i in range(NTONES):
            if i >= len(self.keys) or not self.smp[self.keys[i][0]]["len"]:
                out += bytes(12)
                continue
            s, pos = self.keys[i]
            d = self.smp[s]
            if d["looped"]:
                out += opl4_header(d["adr"] + pos, max(0, d["ls"] - pos), d["end"] - pos)
            else:
                out += opl4_header(d["adr"] + pos, d["len"] - pos, d["len"] + 2 - pos)
        for s in range(1, 32):
            d = self.smp[s]
            out += self.data[d["rom"]:d["rom"] + d["len"]]
            if d["len"] and not d["looped"]:
                out += b"\0\0"
        assert len(out) == self.ram_end - SRAM_BASE
        return bytes(out)

    def used_channels(self):
        """The OPL4 channels in use, in the order the Z80 walks them (23 down to 0)."""
        return [o for o in range(23, -1, -1) if o % 8 < self.nch]

    def schedule(self, ticks, fade0, fstep):
        """The Z80's wave register writes for these ticks (Player(track=False)
        of this MOD), ending with the fade: tick i lasts counts[i] timer 2
        steps and starts at the sum of the ones before; its fade g64 = 64 -
        (t - fade0) // fstep from fade0 on, and the first tick with g64 = 0
        is the END (every channel off). Returns (start writes, writes per
        tick (the END's key offs last), the head length of each tick (key
        ons, key offs, pitch and level: what comes before the next tick's
        preparations), counts)."""
        nch, pans = self.nch, self.pans
        used = self.used_channels()
        alloff = [(0x68 + o, KEY_OFF | pans[o % 8]) for o in used]
        start = [(2, 0x10)] + alloff + [(0x50 + o, 0xFF) for o in used]
        cur, keyed, regs = [0] * nch, [False] * nch, [None] * nch
        slot_tone = [None] * 24
        heads, preps, counts = [], [], []
        acc, t, fq, fb = 0x8000, 0, 0, 0
        n_end = None
        for i, tk in enumerate(ticks):
            g64 = 64
            if fade0 is not None and t >= fade0:
                while t - fade0 - fb >= fstep:
                    fb += fstep
                    fq += 1
                g64 = max(0, 64 - fq)
            w, f = t2_period(tk["bpm"])
            acc += f
            counts.append(w + (acc >> 16))
            acc &= 0xFFFF
            if g64 == 0:
                n_end = i
                break
            on, off, upd, prep = [], [], [], []
            for c, o in enumerate(tk["ch"]):
                lvl = level_of(o["v4"], g64)
                if o.get("stop"):
                    if keyed[c]:
                        off.append((0x68 + c + 8 * cur[c], KEY_OFF | pans[c]))
                        keyed[c] = False
                    continue
                if o["trig"]:
                    r38, r20 = pitch_of(o["per"])
                    tn = self.tone(*o["trig"])
                    new = (cur[c] + 1) % SLOTS
                    k = c + 8 * new
                    prep += [(0x38 + k, r38), (0x20 + k, r20), (0x50 + k, lvl)]
                    if slot_tone[k] != tn:
                        prep.append((0x08 + k, (TONE0 + tn) & 0xFF))   # tone 384 + index (bit 8 in 20h)
                        slot_tone[k] = tn
                    on.append((0x68 + k, KEY_ON | pans[c]))
                    if keyed[c]:
                        off.append((0x68 + c + 8 * cur[c], KEY_OFF | pans[c]))
                    cur[c], keyed[c], regs[c] = new, True, (r38, r20, lvl)
                    continue
                if not keyed[c]:
                    continue
                k = c + 8 * cur[c]
                r38, r20 = pitch_of(o["per"]) if o["per"] else regs[c][:2]
                if (r38, r20) != regs[c][:2]:
                    upd += [(0x38 + k, r38), (0x20 + k, r20)]
                if lvl != regs[c][2]:
                    upd.append((0x50 + k, lvl))
                regs[c] = (r38, r20, lvl)
            heads.append(on + off + upd)
            preps.append(prep)
            t += counts[-1]
        if n_end is None:
            if fade0 is not None:
                raise ValueError("the ticks end before the fade does")
            n_end = len(ticks)                      # a song that loops: no END (alloff: never played)
        writes = [heads[i] + (preps[i + 1] if i + 1 < n_end else []) for i in range(n_end)] + [alloff]
        return start + preps[0], writes, [len(h) for h in heads] + [len(alloff)], counts


def end_tick(ticks, fade0, fstep):
    """The index of the END tick (the fade reaches 0) for these ticks' BPMs."""
    acc, t = 0x8000, 0
    for i, tk in enumerate(ticks):
        if t >= fade0 and (t - fade0) // fstep >= 64:
            return i
        w, f = t2_period(tk["bpm"])
        acc += f
        t += w + (acc >> 16)
        acc &= 0xFFFF
    raise ValueError("the ticks end before the fade does")


# ------------------------------------------------------------------- song
class Song:
    """A MOD for the ROM: the trimmed MOD (mod), what the Z80 plays of it
    (ticks, until the END), the sample RAM image it uploads and the writes
    it makes, and the checks that it plays what ProTracker plays."""

    def __init__(self, path, seconds=None, fade=3.0, strict=True, loop=False, passes=3, min_seconds=0):
        """strict: stop (ValueError) where the ROM player would not play what
        ProTracker plays; else note it in self.problems (tests of the player
        against its model). loop: the whole song, over and over (ProTracker's
        restart at its end), no fade: the ROM holds every position the song
        reaches and the model covers `passes` passes of it, and more if they
        take less than min_seconds (seconds and fade are not used); else
        `seconds` of it from its start, the last `fade` seconds fading out,
        then the END."""
        orig = open(path, "rb").read()
        self.path, self.fade, self.loop = path, fade, loop
        self.problems = []
        pl = Player(*parse(orig))
        if loop:
            self.fade0 = self.fstep = None
            ref = pl.run(1e9, max_ticks=10 ** 6, passes=passes)
            if ref and ref[-1]["t"] + ref[-1]["dur"] < min_seconds and len(pl.pass_starts) > passes:
                a, b = pl.pass_starts[-2], pl.pass_starts[-1]
                t = [tk["t"] for tk in ref] + [ref[-1]["t"] + ref[-1]["dur"]]
                passes += math.ceil((min_seconds - t[-1]) / max(1e-3, t[b] - t[a]))
                pl = Player(*parse(orig))
                ref = pl.run(1e9, max_ticks=10 ** 6, passes=passes)
            n = len(ref)
            self.pass_starts = pl.pass_starts
            if len(self.pass_starts) <= passes:
                raise ValueError(f"the song does not come back to its loop within {n} ticks")
            self.seconds = seconds = ref[-1]["t"] + ref[-1]["dur"]
        else:
            self.seconds = seconds
            self.fade0 = round((seconds - fade) / T2_UNIT)
            self.fstep = max(1, round(fade / T2_UNIT / 64))
            ref = pl.run(seconds + 2)
            n = end_tick(ref, self.fade0, self.fstep)
        self.orig_len = len(orig)
        self.mod = trim(orig, ref[:n + 1])
        self.rt = rt = Rt(self.mod)
        smps, order, pats, nch = parse(self.mod)
        pz = Player(smps, order, pats, nch, track=False)
        ticks = pz.run(seconds + 2, max_ticks=n if loop else 10 ** 6)
        for what in pl.unplayable.values():
            self.problem(strict, f"{what}: the ROM player does not play it")
        for s in sorted({o["trig"][0] for tk in ref[:n] for o in tk["ch"] if o["trig"]}):
            smp = smps[s] if s < len(smps) else None
            if smp and smp["loop"] and smp["loop"][0] == 0 and sum(smp["loop"]) < len(smp["data"]):
                self.problem(strict, f"sample {s}: a loop from 0 shorter than the sample (ProTracker plays "
                                     f"the whole sample first, OpenMPT only with a restart byte other than 7Fh)")
        heard = [[o["sounding"] for o in tk["ch"]] for tk in ref[:n]]
        a, b = events(ref[:n], heard), events(ticks[:n], heard)
        bad = next((i for i in range(n) if a[i] != b[i]), None)
        if bad is not None:
            self.problem(strict, f"tick {bad}: the ROM player would play {b[bad]}, ProTracker {a[bad]} "
                                 f"(an instrument swap?)")
        self.nch, self.smps, self.ticks = nch, smps, ticks[:n]
        self.writes_start, self.writes, self.heads, self.counts = rt.schedule(ticks, self.fade0, self.fstep)
        if rt.missing:
            self.problem(strict, f"9xx start offsets the scan did not find: {sorted(rt.missing)}")
        self.image = rt.image()
        self.pans = rt.pans
        self.named = [s for s in range(1, 32) if rt.smp[s]["len"]]

    def pass_seconds(self):
        """A looping song: the length of its loop (the last pass modelled), in s."""
        a, b = self.pass_starts[-2], self.pass_starts[-1]
        t = [tk["t"] for tk in self.ticks] + [self.ticks[-1]["t"] + self.ticks[-1]["dur"]]
        return t[b] - t[a]

    def problem(self, strict, msg):
        if strict:
            raise ValueError(msg)
        self.problems.append(msg)

    def used_channels(self):
        return self.rt.used_channels()

    def times(self):
        """(ideal ProTracker start of each tick, the OPL4 timer's start) in s."""
        t, real = 0, []
        for n in self.counts[:len(self.ticks)]:
            real.append(t * T2_UNIT)
            t += n
        return [tk["t"] for tk in self.ticks], real

    def pack(self, first_bank, start=0x8000, tab=None):
        """The lookup tables and the 9xx tones after them (tab_blob, in one
        bank; unless tab says where they already are: (bank, address)), then
        the MOD, from `start` in bank first_bank.
        Returns (banks: bytearrays, the first one starting at `start`;
        equates for geo3d_modplay.asm); self.packed keeps where they went."""
        assert 0x8000 <= start < 0xC000, hex(start)
        off = start - 0x8000
        buf = bytearray(off)

        def where(x):
            return first_bank + x // BANK, 0x8000 + x % BANK
        if tab is None:
            blob = self.tab_blob()
            if off + len(blob) > BANK:
                buf += bytes(BANK - off)
            tab = where(len(buf))
            buf += blob
        mod_at = len(buf)
        buf += self.mod
        banks = [bytearray(buf[i:i + BANK]) for i in range(0, len(buf), BANK)]
        banks[0] = banks[0][off:]
        self.packed = {"tables": tab, "mod": where(mod_at), "mod_len": len(self.mod)}
        (tb, ta), (mb, ma) = self.packed["tables"], self.packed["mod"]
        equ = [f"; generated by modplay.py from {os.path.basename(self.path)}, do not edit",
               "MOD_SONG:       equ 1\t\t; 1: a MOD for the MoonSound",
               f"MP_TAB_BANK:    equ {tb}\t\t; the lookup tables (modplay.tables)",
               f"MP_TAB_ADDR:    equ 0x{ta:04x}",
               f"MP_MOD_BANK:    equ {mb}\t\t; the MOD ({len(self.mod)} bytes)",
               f"MP_MOD_ADDR:    equ 0x{ma:04x}",
               f"MP_NTT:         equ {len(self.rt.keys) - 31}\t\t; 9xx tones after the tables (MP_T_TT)",
               "MP_NULL:        equ 0\t\t; 1: timer 2 never starts (build_rom.py --null-mod)"]
        if self.loop:
            equ += ["MP_LOOP:        equ 1\t\t; the song loops for ever: no fade, no END",
                    "MP_FADE0:       equ 0", "MP_FSTEP:       equ 1"]
        else:
            equ += ["MP_LOOP:        equ 0",
                    f"MP_FADE0:       equ {self.fade0}\t; the fade: from this many timer 2 steps on,",
                    f"MP_FSTEP:       equ {self.fstep}\t\t; 1/64 of the level less every this many"]
        return banks, "\n".join(equ) + "\n" + tab_equ()

    def tab_blob(self):
        """What goes at MP_TAB_ADDR: the lookup tables, then the 9xx tones."""
        blob = tables() + self.rt.tt_bytes()
        assert len(blob) <= BANK
        return blob

    def summary(self):
        rt = self.rt
        n_notes = sum(1 for tk in self.ticks for o in tk["ch"] if o["trig"])
        bpms = sorted({tk["bpm"] for tk in self.ticks})
        last = self.ticks[-1]
        if self.loop:
            ps = self.pass_starts
            t = [tk["t"] for tk in self.ticks] + [last["t"] + last["dur"]]
            lens = [f"{t[b] - t[a]:.3f} s" for a, b in zip(ps, ps[1:])]
            cnt = [f"{b - a} ticks" for a, b in zip(ps, ps[1:])]
            if len(cnt) > 4:
                cnt, lens = cnt[:2] + ["..."] + cnt[-1:], lens[:2] + ["..."] + lens[-1:]
            head = (f"looping: {len(ps) - 1} passes modelled ({len(self.ticks)} ticks; passes of "
                    f"{', '.join(cnt)}: {', '.join(lens)}; the loop "
                    f"goes back to {self.ticks[ps[1]]['pos'][:2] if ps[1] < len(self.ticks) else 'its start'})")
        else:
            head = f"{len(self.ticks)} ticks until the END ({last['t'] + last['dur']:.3f} s"
        return (f"{head}, positions 0-{rt.songlen - 1}), BPM {bpms}; {n_notes} notes; ROM MOD {len(self.mod)} bytes of "
                f"{self.orig_len} (patterns 0-{rt.npat - 1}, samples {self.named}); {len(rt.keys) - 31} 9xx "
                f"tones {rt.keys[31:]}; sample RAM {len(self.image)} bytes ({rt.blocks} x 128 KB)")


def no_mod_equ():
    return ("; generated by build_rom.py: no MOD, do not edit\n"
            "MOD_SONG:       equ 0\n"
            "MP_TAB_BANK:    equ 0\nMP_TAB_ADDR:    equ 0x8000\nMP_MOD_BANK:    equ 0\nMP_NTT:         equ 0\n"
            "MP_NULL:        equ 0\n"
            "MP_MOD_ADDR:    equ 0x8000\nMP_LOOP:        equ 0\nMP_FADE0:       equ 0\nMP_FSTEP:       equ 1\n"
            + tab_equ())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mod")
    ap.add_argument("--seconds", type=float, default=3420 / music.VBLANK_HZ,
                    help="how long it plays (default: the shortest crawl, 3420 frames)")
    ap.add_argument("--fade", type=float, default=180 / music.VBLANK_HZ, help="fade-out at the end (s)")
    ap.add_argument("--dump", action="store_true", help="the events, tick by tick")
    a = ap.parse_args()
    song = Song(a.mod, a.seconds, a.fade)
    print(song.summary())
    if a.dump:
        for i, tk in enumerate(song.ticks):
            ev = []
            for c, o in enumerate(tk["ch"]):
                if o["trig"]:
                    ev.append(f"c{c}:smp{o['trig'][0]}@{o['trig'][1]} p{o['per'] / 4:g} v{o['vol']}")
                if o.get("stop"):
                    ev.append(f"c{c}:stop")
            print(f"{i:5d} {tk['t']:8.4f} o{tk['pos'][0]:02d} r{tk['pos'][1]:02d} t{tk['pos'][2]} "
                  f"sp{tk['speed']} bpm{tk['bpm']} " + " ".join(ev))


if __name__ == "__main__":
    main()
