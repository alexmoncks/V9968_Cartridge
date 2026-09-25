#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Tests of the MoonSound MOD player (geo3d_modplay.asm, modelled by modplay.py),
alone or in the demo ROM.

--z80: builds modplay_test.asm with the MOD (modplay.Song.pack) and runs it in
the Z80 emulator (pip z80) with an emulated MoonSound (opl4emu.py), in real
time (the MSX's clock: T-states and M1 waits, z80clock.py): the sample RAM
the upload fills, byte for byte; mod_start's
writes; every wave register write of every tick (between two clears of timer
2's flag) against modplay.py's model of the player; every tone header it
loads a RAM tone whose header points into the samples; the timer 2
periods; no tick lost; the tick and key-on latencies behind the timer. Fast (no
openMSX), so --selftest runs it on MODs made here: one per group of effects
(every effect the player takes, 4, 6 and 8 channels, tempo changes, jumps,
loops, empty samples, vibrato and tremolo with every waveform: the probe
song below, and on 8 channels; ProTracker 2's rules: pt2_rules, and where
OpenMPT plays them otherwise: pt2_vs_openmpt) and random
ones (--fuzz N): the player against its model on songs it was not written
for; then songs that loop (loop_songs: through their end with a Dxx,
through a jump back), built to loop as the demo ROM's MOD does, over two
passes and more. The model plays what
ProTracker plays (modplay.Player, as OpenMPT, which the audio test checks);
in these MODs it is the model without play positions (Player(track=False)),
the player's. --lenient tests a MOD the build would refuse the same way
(where the ROM player does not play what ProTracker plays: an instrument
swap), and says where.

--probe: the probe song (probe_song: vibrato on a 16-byte triangle on
channel 0, tremolo on a 256-byte square on channel 1, every waveform, the
parameter memory, the restarts, row delays, tempo changes) in the test ROM
in openMSX and in OpenMPT, tick by tick: channel 0's pitch (the triangle's
zero crossings are 8 bytes apart, whatever the level) and channel 1's level
(the square's plateaus) against the model's period and volume (OpenMPT) and
against the registers the Z80 wrote (openMSX), and openMSX against OpenMPT.

--cost: the test ROM in openMSX (the MSX's M1 wait included), each mod_poll
call timed: the player's work per tick (the polls between two ticks, T-states
at 3.58 MHz) and its longest poll.

openMSX (default): builds the test ROM, runs it in openMSX with a MoonSound
(extension moonsound_test: 640 KB sample RAM). With --rom: runs the demo ROM
(build_rom.py --music MOD) on C-BIOS_V9968_JP + geo3d with a MoonSound
(--ext: the extensions, default "geo3d moonsound"; for the 88h profile
"HRA_V9968 geo3d88 moonsound"), chooses English at --key-at s, and follows
the crawl's music from mod_start to the MOD's stop, with the demo running.
It checks:

  trace   the sample RAM (tone headers and samples) as the chip sees it
          when the song starts, against modplay.py's; every wave register
          write the Z80 makes (openMSX watchpoints on 7Eh/7Fh) against
          modplay.py's, tick by tick; the timer 2 periods;
          the tick times (the flag clears) against the OPL4 timer and
          against ProTracker's 2.5 / BPM s per tick; the key-on times.
  audio   openMSX's recording (the mix, and each MOD channel: the sum of
          its three OPL4 channels) against OpenMPT playing the original MOD
          file (openmpt123 set up like the OPL4: linear interpolation, no
          Amiga resampler or filter, no volume ramping (but OpenMPT still
          spreads a change between two volumes over the tick), the Amiga's
          hard panning), note by note: onset (looked for around the key
          on's time in the trace: onset_vs_keyon_ms; the trace's own
          key-on times are keyon_vs_protracker_ms), pitch (resampled correlation),
          level, and the loops (the sound late in long notes); the mix frame
          by frame and its lag every 2 s (drift). Also the mix's spectrum
          against OpenMPT as a player plays it by default (Amiga resampler
          and filter): the OPL4 interpolates linearly and has no Amiga
          filter, so it is brighter above 10 kHz.
  --rom   also: the upload (its first unit to its end, and the menu), the
          longest times from one mod_poll or mod_tpoll call to the next while
          the song plays (a call that plays a tick counts in), and the crawl's page
          flips in vertical blanks (against --flips-ref, a d/f time log of
          another run, e.g. of a ROM without the MOD player).

--rom ROM --loop: the demo ROM's looping MOD, recorded for --seconds from
mod_start (default 230 s: a pass of the song and its restart), against
OpenMPT playing the song over and over.

Usage: test_modplay.py MOD [--seconds S] [--fade F] [--gap N] [--work DIR]
                       [--preview DIR] [--tag T] [--no-openmsx]
                       [--rom ROM [--loop] [--labels FILE] [--ext "EXT ..."] [--key-at S]
                        [--flips-ref FILE]]
       test_modplay.py MOD --z80 [--kb KB] [--gap N] [--lenient]
       test_modplay.py MOD --cost [--seconds S] [--fade F]
       test_modplay.py --selftest [--fuzz N] [--seed S]
       test_modplay.py --probe [--no-openmsx]
Everything made from the MOD (ROM, recordings, renders) goes to --work
(default rom/out/modplay_test, git-ignored) and --preview: keep it out of
the repository. WSL / Linux: needs z80asm, the z80 package, numpy; openMSX
(OPENMSX, OPENMSX_SYSTEM_DATA) and openmpt123 for the openMSX tests.
"""
import argparse
import json
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import modplay  # noqa: E402
import opl4emu  # noqa: E402
import z80clock  # noqa: E402

RATE = 44100
BANK = modplay.BANK
OPENMSX = os.environ.get("OPENMSX", os.path.expanduser("~/openMSX/derived/x86_64-linux-opt/bin/openmsx"))
WAVE_DEV = "Sunrise MoonSound wave"


def labels_of(path):
    labels = {}
    for line in open(path):
        p = line.replace(":", " ").split()
        if len(p) >= 3 and p[1] == "equ":
            labels[p[0]] = int(p[2].lstrip("$").replace("0x", ""), 16)
    return labels


# ------------------------------------------------------------------ build
def build(song, work, gap):
    banks, equ = song.pack(1)
    open(os.path.join(work, "rom_mod.asm"), "w").write(equ)
    open(os.path.join(work, "rom_test.asm"), "w").write(
        f"; generated by test_modplay.py\nT_GAP:  equ {gap}\nT_MODE: equ 0\nT_RB:   equ 0\n")
    subprocess.run(["z80asm", "-I", work, "-I", HERE, "-o", "bank0.bin", "--label=labels.txt",
                    os.path.join(HERE, "modplay_test.asm")], cwd=work, check=True)
    bank0 = open(os.path.join(work, "bank0.bin"), "rb").read()
    assert len(bank0) <= BANK, len(bank0)
    rom = bytearray(bank0) + bytes(BANK - len(bank0))
    for b in banks:
        rom += b + bytes(BANK - len(b))
    n = len(rom) // BANK
    rom += bytes(((1 << (n - 1).bit_length()) - n) * BANK)
    path = os.path.join(work, "modplay_test.rom")
    open(path, "wb").write(rom)
    return path, len(bank0), labels_of(os.path.join(work, "labels.txt"))


# -------------------------------------------------------------------- Z80
def run_z80(rom, labels, kb=640, limit_s=None, max_ticks=None):
    """The test ROM in the Z80 emulator, real time (a 3.58 MHz MSX: the
    Z80's T-states and the M1 wait states, z80clock.py), with an emulated
    MoonSound of kb KB. -> (MoonSound, info)."""
    import z80
    m = z80.Z80Machine()
    m.set_memory_block(0x4000, rom[0:BANK])
    m.set_memory_block(0x0024, b"\xC9")                     # ENASLT
    m.set_memory_block(0x0138, b"\xC9")                     # RSLREG
    m.set_memory_block(0xFCC1, bytes(5))
    m.sp = 0xF37D
    m.pc = rom[2] | rom[3] << 8
    ms = opl4emu.MoonSound(kb)
    run = 200000
    clock = z80clock.MsxClock(m)
    now = clock.now

    def on_out(port, v):
        ms.port_out(port & 0xFF, v, now())

    def on_in(port):
        v = ms.port_in(port & 0xFF, now())
        return 0xFF if v is None else v
    m.set_output_callback(on_out)
    m.set_input_callback(on_in)
    stops = {0x0024, 0x0138, labels["mp_setbank"], labels["mod_upload"], labels["mod_start"]}
    for a in stops:
        m.set_breakpoint(a)
    info = {}
    limit = (limit_s or 1e9) * opl4emu.T_HZ
    while True:
        clock.run(run)
        pc = m.pc
        if pc in stops:
            if pc == 0x0138:
                m.a = 0b00010100                            # slot 1 in pages 1 and 2
            elif pc == labels["mp_setbank"]:
                b = m.a
                m.set_memory_block(0x8000, bytes(rom[b * BANK:(b + 1) * BANK]).ljust(BANK, b"\xFF"))
            elif pc == labels["mod_upload"]:
                ms.counting = True
                info["up0"] = now()
            elif pc == labels["mod_start"]:
                ms.counting = False
                info["start"] = now()
            clock.step_over()
        phase = m.memory[0xC000]
        if phase in (3, 0xEE) or m.halted or now() > limit:
            break
        tk = m.memory[labels["mp_ticks"]] | m.memory[labels["mp_ticks"] + 1] << 8
        if tk < info.get("tk16", 0):
            info["wraps"] = info.get("wraps", 0) + 1         # (mp_ticks is 16 bits)
        info["tk16"] = tk
        if max_ticks and tk + 65536 * info.get("wraps", 0) >= max_ticks:
            break
    info.update(phase=phase, blocks=m.memory[0xC001], end=now(),
                ticks=(m.memory[labels["mp_ticks"]] | m.memory[labels["mp_ticks"] + 1] << 8)
                + 65536 * info.get("wraps", 0))
    return ms, info


def check_play(song, ms, t_from=0.0):
    """What the emulated MoonSound got from the player against the model:
    -> (ok, report dict)."""
    seg = opl4emu.segments(ms, t_from)
    if seg is None:
        return False, dict(error="the song never started (timer 2 never on)")
    stopped = seg["t_stop"] < float("inf")
    bad, last_ok = opl4emu.match_ticks(seg["segs"], song.writes, song.heads, stopped)
    init_ok = seg["init"] == song.writes_start
    per = seg["periods"]
    per_ok = per == song.counts[:len(per)] and len(per) >= min(len(seg["segs"]), len(song.counts))
    rep = dict(ticks=len(seg["segs"]), model_ticks=len(song.writes) - 1, stopped=stopped,
               end_reached=stopped and len(seg["segs"]) == len(song.writes),
               start_writes_ok=init_ok, ticks_ok=not bad and last_ok, periods_ok=per_ok,
               periods=len(per), after_stop=len(seg["tail"]), load_clashes=len(ms.load_clash),
               bad_tone_loads=len(ms.bad_loads))
    if bad or not last_ok:
        i = bad[0] if bad else len(seg["segs"]) - 1
        rep["first_bad_tick"] = i
        rep["got"] = seg["segs"][i][:14]
        rep["want"] = (song.writes[i] if i < len(song.writes) else [])[:14]
    if not init_ok:
        rep["start_got"], rep["start_want"] = seg["init"][:10], song.writes_start[:10]
    # timing: timer 2's overflows (the next tick due) against the flag clears
    # (the poll that plays it); a tick is lost if the next overflow comes first
    ov = [t for t in ms.ov2 if seg["t_start"] < t < seg["t_stop"]]
    cl = seg["clears"]
    lat = [c - o for o, c in zip(ov, cl)]
    lost = [i for i in range(min(len(ov) - 1, len(cl))) if cl[i] >= ov[i + 1]]
    marks = [seg["t_start"]] + cl
    kon, kk = [], 0
    for t, r, v in ms.wave:
        if t < seg["t_start"] or t > seg["t_stop"]:
            continue
        while kk + 1 < len(marks) and t >= marks[kk + 1]:
            kk += 1
        if 0x68 <= r < 0x80 and v & 0x80:
            kon.append(t - (seg["t_start"] if kk == 0 else ov[kk - 1] if kk - 1 < len(ov) else marks[kk]))
    ms_ = 1e3 / opl4emu.T_HZ
    rep.update(lost=len(lost), tick_latency_ms_max=round(max(lat) * ms_, 3) if lat else None,
               keyon_latency_ms_max=round(max(kon) * ms_, 3) if kon else None, keyons=len(kon))
    ok = (init_ok and not bad and last_ok and per_ok and not lost and not seg["tail"] and not ms.load_clash
          and not ms.bad_loads)
    return ok, rep


def z80_test(song, work, kb=640, gap=0, quiet=False):
    """Builds the test ROM, runs it in the Z80 emulator, checks the upload
    and the play. -> (ok, report)."""
    rom_path, b0, labels = build(song, work, gap)
    rom = open(rom_path, "rb").read()
    t0 = time.time()
    # (a looping song has no END: the run stops before the model's ticks do)
    ms, info = run_z80(rom, labels, kb, song.seconds + 10, len(song.ticks) - 2 if song.loop else None)
    rep = dict(emulator_s=round(time.time() - t0, 1), phase=info["phase"], blocks_found=info["blocks"],
               module_bytes=labels["mp_new2"] + 20 - labels["mod_reset"], bank0_bytes=b0)
    if song.rt.blocks * 128 > kb:
        ok = info["phase"] == 0xEE
        rep["too_little_ram"] = ok
        return ok, rep
    img = song.image
    rep["image_ok"] = ms.mem_n == len(img) and bytes(ms.ram[:len(img)]) == img
    rep["upload_ms"] = round((ms.mem_t[1] - ms.mem_t[0]) / opl4emu.T_HZ * 1e3, 1) if ms.mem_t else None
    ok, play = check_play(song, ms)
    rep.update(play)
    if song.loop:                           # every pass but part of the last one, and the loops
        rep["passes"] = sum(1 for b in song.pass_starts[1:] if b <= rep["ticks"])
        ok = ok and rep["image_ok"] and info["phase"] == 2 and rep["passes"] >= len(song.pass_starts) - 2
    else:
        ok = ok and rep["image_ok"] and info["phase"] == 3 and rep.get("end_reached")
    return ok, rep


# ---------------------------------------------------------------- self test
PT = modplay.PT_TABLE


def mod_file(nch, samples, order, pats, title=b"geo3d test"):
    """A 31-sample MOD: samples [(data bytes, volume, finetune, loop start,
    loop length (bytes, 0: none))], patterns [64 rows of nch cells (sample,
    period, effect, parameter)]."""
    tag = {4: b"M.K.", 6: b"6CHN", 8: b"8CHN"}.get(nch, b"%dCHN" % nch)
    head = bytearray(title.ljust(20, b"\0")[:20])
    for i in range(31):
        d, vol, ft, ls, ll = samples[i] if i < len(samples) else (b"", 0, 0, 0, 0)
        head += b"smp%d" % (i + 1) + bytes(22 - len(b"smp%d" % (i + 1)))
        head += struct.pack(">HBBHH", len(d) // 2, ft & 15, vol, ls // 2, max(1, ll // 2))
    head += bytes([len(order), 127]) + bytes(order) + bytes(128 - len(order)) + tag
    body = bytearray()
    for p in pats:
        for row in p:
            for s, per, e, x in row:
                body += bytes([(s & 0xF0) | (per >> 8), per & 0xFF, ((s & 15) << 4) | e, x])
    for d, *_ in samples:
        body += d
    return bytes(head + body)


def wave(n, kind, seed=0):
    rnd = random.Random(seed)
    out = bytearray()
    for i in range(n):
        if kind == "saw":
            v = (i * 7) % 200 - 100
        elif kind == "sq":
            v = 90 if (i // 16) % 2 else -90
        else:
            v = rnd.randint(-120, 120)
        out.append(v & 0xFF)
    return bytes(out)


def std_samples():
    """1 looped saw, 2 one-shot noise, 3 empty, 4 looped square (finetune
    -3), 5 one-shot saw (loop start past the end: a one-shot), 6 long loop,
    7 a loop of one word (a one-shot)."""
    return [(wave(4000, "saw"), 64, 0, 1000, 2000),
            (wave(3000, "noise", 1), 50, 0, 0, 0),
            (b"", 40, 0, 0, 0),
            (wave(2048, "sq"), 60, 13, 0, 2048),
            (wave(1500, "saw"), 64, 5, 1600, 100),
            (wave(20000, "noise", 2), 30, 7, 5000, 15000),
            (wave(900, "sq"), 64, 0, 0, 2)]


def empty(nch):
    return [[(0, 0, 0, 0)] * nch for _ in range(64)]


def put(p, row, ch, s=0, note=None, e=0, x=0):
    per = PT[note] if note is not None else 0
    p[row] = list(p[row])
    p[row][ch] = (s, per, e, x)


def cover_songs():
    """(name, channels, samples, order, patterns): every effect the player
    takes, and the song-wide ones."""
    songs = []
    # 1: notes, volume, porta, arpeggio, fine slides, finetune, cut, delay
    p = empty(4)
    put(p, 0, 0, 1, 12, 0xC, 40)
    put(p, 0, 1, 4, 24)
    put(p, 0, 2, 2, 0)
    put(p, 0, 3, 6, 5, 0xA, 0x02)
    put(p, 2, 0, 0, None, 0x1, 3)
    put(p, 3, 1, 0, None, 0x2, 5)
    put(p, 4, 0, 0, None, 0x0, 0x37)
    put(p, 5, 0, 0, None, 0x0, 0x4C)
    put(p, 6, 1, 1, 17, 0x3, 4)
    put(p, 7, 1, 0, None, 0x3, 0)
    put(p, 8, 1, 0, 30, 0x5, 0x20)
    put(p, 9, 1, 0, None, 0x5, 0x03)
    put(p, 10, 0, 0, None, 0xE, 0x13)
    put(p, 11, 0, 0, None, 0xE, 0x2F)
    put(p, 12, 0, 1, 20, 0xE, 0x57)
    put(p, 13, 0, 0, None, 0xE, 0xA3)
    put(p, 14, 0, 0, None, 0xE, 0xB9)
    put(p, 15, 0, 0, None, 0xE, 0xC2)
    put(p, 16, 0, 1, 9, 0xE, 0xD3)
    put(p, 17, 0, 1, 9, 0xE, 0xD9)                  # the delay past the row's end
    put(p, 18, 2, 0, None, 0xE, 0xC0)
    put(p, 19, 2, 5, 33, 0xA, 0x40)
    put(p, 20, 2, 0, None, 0xA, 0x0F)
    put(p, 21, 3, 0, None, 0xC, 80)
    put(p, 22, 3, 3, 10)                            # the empty sample: a stop
    put(p, 23, 3, 7, 11)
    put(p, 24, 0, 1, 35, 0x1, 0xFF)                 # slides to the limits
    put(p, 26, 1, 4, 0, 0x2, 0xFF)
    put(p, 28, 0, 0, None, 0x0, 0xFF)
    put(p, 30, 2, 6, 0, 0x0, 0x9F)
    put(p, 32, 1, 0, 35, 0x3, 0xFF)
    put(p, 34, 0, 2, 2, 0xE, 0x93)                  # a retrigger every 3 ticks
    put(p, 35, 0, 0, None, 0xE, 0x91)
    put(p, 36, 0, 0, None, 0xE, 0x92)
    put(p, 37, 1, 4, 13, 0xE, 0x5F)
    put(p, 38, 1, 0, 13, 0xE, 0x50)
    put(p, 40, 3, 1, 20, 0x3, 8)                    # tone porta on a new channel
    put(p, 42, 2, 0, 20, 0x3, 2)                    # tone porta with nothing sounding
    put(p, 44, 0, 0, 25)                            # a note without instrument
    put(p, 46, 0, 0, None, 0xE, 0x90)
    put(p, 48, 3, 1, 3, 0xC, 0)
    put(p, 50, 3, 0, None, 0xE, 0xA0)
    songs.append(("effects", 4, std_samples(), [0], [p]))
    # 2: 9xx, stacking, retrigger with the stack, E9x on the note row
    p = empty(4)
    put(p, 0, 0, 1, 12, 0x9, 8)
    put(p, 2, 0, 0, 14, 0x9, 0)                     # the memory, stacked
    put(p, 4, 0, 0, 16)                             # the stack alone
    put(p, 6, 0, 0, None, 0xE, 0x92)
    put(p, 8, 0, 1, 18, 0x9, 4)                     # an instrument: the stack from 0
    put(p, 10, 1, 6, 12, 0x9, 0x30)
    put(p, 12, 1, 0, 14, 0x9, 0x60)                 # 9xx past the end: the end
    put(p, 14, 2, 2, 12, 0x9, 2)
    put(p, 16, 2, 0, 12, 0x9, 0xFF)
    put(p, 18, 3, 5, 12, 0x9, 1)
    put(p, 20, 3, 0, 12, 0xE, 0x92)
    songs.append(("offsets", 4, std_samples(), [0], [p]))
    # 3: song-wide: speed, tempo, break (decimal), jump, pattern loop, row delay
    a, b, c = empty(4), empty(4), empty(4)
    put(a, 0, 0, 1, 12, 0xF, 3)
    put(a, 0, 1, 4, 17, 0xF, 0xA0)                  # tempo 160: from the next tick
    put(a, 1, 2, 6, 5)
    put(a, 2, 0, 0, 14, 0xE, 0x60)
    put(a, 3, 1, 0, 19)
    put(a, 4, 0, 0, 16, 0xE, 0x62)                  # back to row 2, twice
    put(a, 5, 0, 0, 12, 0xF, 1)
    put(a, 6, 3, 2, 7, 0xE, 0xE2)                   # the row 3 times
    put(a, 7, 1, 0, 21, 0xF, 31)
    put(a, 8, 0, 0, None, 0xF, 4)
    put(a, 8, 1, 0, None, 0xF, 0x20)                # 32 BPM
    put(a, 9, 2, 0, None, 0xF, 0xFF)                # 255 BPM
    put(a, 10, 3, 0, None, 0xD, 0x32)               # break to row 32 of the next
    put(b, 32, 0, 1, 24, 0xF, 6)
    put(b, 33, 1, 0, None, 0xF, 0x7D)
    put(b, 36, 2, 6, 10, 0xB, 3)                    # jump to position 3
    put(c, 0, 0, 7, 1, 0xE, 0xE1)
    put(c, 0, 1, 0, None, 0xE, 0xE5)                # the last EEx counts (ProTracker)
    put(c, 2, 2, 4, 30, 0xD, 0x99)                  # break past 63: row 0
    songs.append(("song", 4, std_samples(), [0, 1, 2, 2, 1], [a, b, c]))
    # 4, 5: 6 and 8 channels (rows of 24 and 32 bytes)
    for nch in (6, 8):
        p = empty(nch)
        for r in range(0, 64, 2):
            for ch in range(nch):
                if (r // 2 + ch) % 3 == 0:
                    put(p, r, ch, 1 + (r + ch) % 7, (r + 3 * ch) % 36, (0, 0xA, 0xC, 1)[ch % 4],
                        (0, 0x01, 30 + ch, 2)[ch % 4])
        songs.append((f"{nch} channels", nch, std_samples(), [0, 0], [p]))
    # 6: vibrato and tremolo (the probe song), 7: both on 8 channels
    songs.append(("vib / trem", 4) + probe_song())
    p = empty(8)
    for r in range(64):
        for ch in range(8):
            if r % 8 == ch % 4 * 2:
                put(p, r, ch, 1 + (r + ch) % 7, (r + 5 * ch) % 36, (4, 6, 7, 4)[ch % 4],
                    (0x48 + ch, 0x21, 0x8C - ch, 0xF3)[ch % 4])
            elif r % 2:
                put(p, r, ch, 0, None, (4, 6, 7, 0xE)[(r // 2 + ch) % 4],
                    (0, 0x04, 0x30 + ch, 0x40 | (r + ch) % 8)[(r // 2 + ch) % 4])
    put(p, 20, 0, 0, None, 0xE, 0x71)
    put(p, 21, 0, 0, None, 7, 0xF8)
    songs.append(("vib/trem 8ch", 8, std_samples(), [0, 0], [p]))
    songs.append(("pt2 rules", 4, std_samples(), [0, 1, 0], pt2_rules()))
    songs.append(("pt2 vs mpt", 4, std_samples(), [0, 0], pt2_vs_openmpt()))
    return songs


def pt2_rules():
    """The ProTracker 2 rules a review found (checked against OpenMPT): the
    last EEx of a row counts; the delayed note (EDx) and E9x without a note
    play on each EEx repeat; 9xx without a note moves the stacked offset;
    E5x without a note sets the finetune; E9x with a lone instrument number
    retriggers that sample, at the finetune the row started with; a lone
    empty sample sets its volume; Bxx after a Dxx clears its row; a jump or
    a break after a row delay skips the target row."""
    a, b = empty(4), empty(4)
    put(a, 0, 0, 1, 12)
    put(a, 1, 0, 0, None, 0xE, 0xE1)
    put(a, 1, 1, 0, None, 0xE, 0xE2)                # the last EEx: 3 times
    put(a, 2, 0, 1, 14, 0xE, 0xD2)
    put(a, 2, 1, 0, None, 0xE, 0xE1)                # the delayed note on each repeat
    put(a, 3, 0, 0, None, 0xE, 0x93)
    put(a, 3, 1, 0, None, 0xE, 0xE1)                # E9x without a note on each repeat
    put(a, 4, 0, 0, None, 0x9, 0x10)                # 9xx without a note: the stack moves
    put(a, 6, 0, 0, 16)                             # a note without instrument: from there
    put(a, 8, 0, 0, None, 0xE, 0x57)                # E5x without a note
    put(a, 10, 0, 0, 18)                            # a note at that finetune
    put(a, 12, 0, 4, None, 0xE, 0x92)               # a lone instrument (finetune -3) + E92
    put(a, 14, 2, 1, 20)
    put(a, 16, 2, 3, None)                          # a lone empty sample: its volume
    put(a, 17, 2, 0, None, 0xA, 0x01)
    put(a, 18, 3, 1, 12, 0xD, 0x05)
    put(a, 18, 2, 0, None, 0xB, 0x01)               # Bxx after Dxx: row 0 of position 1
    put(b, 0, 0, 2, 12, 0xD, 0x10)
    put(b, 0, 1, 0, None, 0xE, 0xE1)                # a break after a row delay: row 11
    put(b, 11, 0, 1, 24)
    put(b, 12, 1, 0, None, 0xB, 0x02)
    put(b, 12, 2, 0, None, 0xE, 0xE1)               # a jump after a row delay: row 1
    put(b, 13, 0, 6, 30)
    return [a, b]


def pt2_vs_openmpt():
    """The four cases where the model follows ProTracker 2 and OpenMPT plays
    otherwise (modplay.py): an arpeggio on a period a slide left off the
    table, an arpeggio and ECx (x at or past the speed) on rows with EEx, a
    tempo on a speed-1 row with EEx."""
    c = empty(4)
    put(c, 0, 0, 1, 12, 0x1, 0x03)                  # C-2 sliding up: off the table
    put(c, 1, 0, 0, None, 0x0, 0x47)                # an arpeggio on it
    put(c, 2, 3, 0, None, 0xF, 0x04)                # speed 4
    put(c, 3, 1, 2, 17, 0x0, 0x37)
    put(c, 3, 2, 0, None, 0xE, 0xE2)                # an arpeggio on a row with EEx
    put(c, 5, 1, 6, 19, 0xE, 0xC5)
    put(c, 5, 2, 0, None, 0xE, 0xE1)                # EC5 at speed 4 with EEx: no cut
    put(c, 7, 3, 0, None, 0xF, 0x01)                # speed 1
    put(c, 8, 0, 1, 24, 0xF, 0x5A)
    put(c, 8, 2, 0, None, 0xE, 0xE2)                # a tempo on a speed-1 row with EEx
    put(c, 9, 1, 2, 26, 0xF, 0x06)
    put(c, 10, 0, 0, None, 0xF, 0x7D)               # back to speed 6, 125 BPM
    put(c, 11, 1, 4, 12)
    put(c, 20, 0, 0, None, 0xD, 0x00)               # (the next position)
    return [c]


def loop_songs():
    """(name, channels, samples, order, patterns) that loop in other ways:
    through their end (a Dxx on the last position: the next pass starts on
    its row), through a jump back, and the song-wide cover song."""
    songs = []
    a, b = empty(4), empty(4)
    put(a, 0, 0, 1, 12, 0xF, 3)
    put(a, 5, 1, 4, 17)
    put(a, 9, 2, 6, 5, 0xD, 0x00)
    put(b, 0, 0, 2, 14)
    put(b, 1, 3, 0, None, 0xD, 0x05)                # the end: position 0, row 5
    songs.append(("end + Dxx", 4, std_samples(), [0, 1], [a, b]))
    a, b = empty(4), empty(4)
    put(a, 0, 0, 1, 12, 0xF, 4)
    put(a, 3, 1, 4, 20, 0x9, 0x08)
    put(a, 6, 2, 0, None, 0xD, 0x00)
    put(b, 0, 0, 5, 7)
    put(b, 2, 1, 0, None, 0x9, 0x10)
    put(b, 4, 2, 0, None, 0xB, 0x01)                # back to position 1: the loop
    songs.append(("jump back", 4, std_samples(), [0, 1], [a, b]))
    songs.append(next(c for c in cover_songs() if c[0] == "pt2 rules"))
    return songs


TRIANGLE = bytes(v & 0xFF for v in (0, 32, 64, 96, 127, 96, 64, 32, 0, -32, -64, -96, -127, -96, -64, -32))
SQUARE = bytes([127] * 128 + [(-127) & 0xFF] * 128)
NOTES = ["C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-"]


def probe_song():
    """(samples, order, patterns) of a MOD whose channel 0 plays vibrato (4xy,
    6xy, E4x) on a 16-byte triangle, channel 1 tremolo (7xy, E7x) on a 256-byte
    square, channel 2 both on other samples (an empty one, a one-shot that
    runs out), channel 3 the song-wide effects: the pitch of channel 0 and the
    level of channel 1 can be measured tick by tick in a recording (--probe):
    the triangle's zero crossings are 8 bytes apart whatever the level, the
    square's plateaus give the level. Every waveform, the parameter memory,
    the restart on notes (not with E4x / E7x + 4), row delays, speed and tempo
    changes, note delay, retrigger, the tremolo's ramp that follows the
    vibrato position, volume 0, the limits."""
    smps = [(TRIANGLE, 64, 0, 0, 16), (SQUARE, 64, 0, 0, 256), (TRIANGLE, 48, 5, 0, 16),
            (wave(3000, "noise", 3), 50, 0, 0, 0), (b"", 40, 0, 0, 0), (wave(2048, "sq"), 60, 13, 0, 2048)]
    a, b = empty(4), empty(4)

    def p(pat, ch, r, s=0, n=None, e=0, x=0):
        put(pat, r, ch, s, None if n is None else NOTES.index(n[:2]) + 12 * (int(n[2]) - 1), e, x)
    for r, s, n, e, x in [                       # channel 0: vibrato, pattern 0
            (0, 1, "C-2", 4, 0x48), (1, 0, None, 4, 0), (2, 0, None, 4, 0x0C), (3, 0, None, 4, 0x80),
            (4, 1, "C-2", 4, 0), (5, 0, None, 0xE, 0x41), (6, 0, None, 4, 0x46), (7, 0, None, 4, 0),
            (8, 1, "D-2", 4, 0), (9, 0, None, 0xE, 0x42), (10, 0, None, 4, 0x3F), (11, 0, None, 0xE, 0x43),
            (12, 0, None, 4, 0x2F), (13, 1, "C-2", 0xE, 0x44), (14, 0, None, 4, 0x44), (15, 1, "E-2", 4, 0),
            (16, 0, None, 6, 0x02), (17, 0, None, 6, 0x20), (18, 0, None, 6, 0x00), (19, 0, None, 4, 0),
            (20, 1, "C-2", 0xE, 0x40), (21, 0, None, 4, 0x88), (22, 0, None, 0xE, 0x93), (23, 0, None, 4, 0),
            (24, 1, "G-2", 0xE, 0xD2), (25, 0, None, 4, 0), (26, 1, "C-3", 3, 0x08), (27, 0, None, 4, 0),
            (28, 0, None, 1, 4), (29, 0, None, 4, 0x4F), (30, 1, "B-3", 4, 0x4F), (31, 1, "C-1", 4, 0x4F),
            (32, 0, None, 4, 0), (33, 1, "A-2", 0xE, 0x4B), (34, 0, None, 4, 0x6A), (35, 1, "A-2", 4, 0),
            (36, 0, None, 0xE, 0x47), (37, 1, "F-2", 4, 0x19), (38, 3, "F-2", 4, 0x35), (39, 0, None, 4, 0),
            (40, 3, "A#1", 0xE, 0x40), (41, 0, None, 4, 0xF1), (42, 0, None, 0xE, 0x52), (43, 1, "C-2", 4, 0x21),
            (44, 0, None, 4, 0xE3), (45, 0, None, 5, 0x01), (46, 0, None, 4, 0), (47, 0, None, 0xE, 0x10),
            (48, 0, None, 4, 0x39), (49, 1, "C#2", 6, 0x05), (50, 0, None, 6, 0x40), (51, 0, None, 0xE, 0x41),
            (52, 1, "C-2", 4, 0x7C), (53, 0, None, 4, 0), (54, 0, None, 0xE, 0x4A), (55, 0, None, 4, 0xB9),
            (56, 1, "D#2", 0, 0), (57, 0, None, 4, 0x00), (58, 0, None, 0xC, 0), (59, 0, None, 4, 0),
            (60, 0, None, 0xC, 0x30), (61, 0, None, 6, 0xA0), (62, 0, None, 6, 0x0F), (63, 0, None, 4, 0)]:
        p(a, 0, r, s, n, e, x)
    for r, s, n, e, x in [                       # channel 1: tremolo, pattern 0
            (0, 2, "C-1", 7, 0x48), (1, 0, None, 7, 0), (2, 0, None, 0xC, 0x20), (3, 0, None, 7, 0x8F),
            (4, 0, None, 0xE, 0x71), (5, 0, None, 7, 0), (6, 0, None, 0xE, 0x72), (7, 0, None, 7, 0),
            (8, 0, None, 0xE, 0x73), (9, 0, None, 7, 0), (10, 2, "C-1", 7, 0), (11, 0, None, 7, 0x4F),
            (12, 0, None, 0xC, 0), (13, 0, None, 7, 0), (14, 0, None, 0xC, 0x10), (15, 0, None, 7, 0),
            (16, 0, None, 0xA, 0x04), (17, 0, None, 7, 0), (18, 0, None, 0xE, 0x74), (19, 2, "C-1", 7, 0),
            (20, 0, None, 0xE, 0x71), (21, 0, None, 7, 0x1F), (22, 0, None, 4, 0x80), (23, 0, None, 4, 0),
            (24, 0, None, 7, 0x33), (25, 0, None, 7, 0), (26, 0, None, 0xE, 0x70), (27, 2, "C-1", 7, 0xC8),
            (28, 0, None, 7, 0), (29, 0, None, 0xE, 0x92), (30, 0, None, 7, 0), (31, 2, "C-1", 0xE, 0xD3),
            (32, 0, None, 7, 0x2A), (33, 0, None, 0xC, 0x3C), (34, 0, None, 7, 0xFF), (35, 0, None, 0xE, 0xA8),
            (36, 0, None, 7, 0x61), (37, 0, None, 0xE, 0x7B), (38, 0, None, 7, 0), (39, 2, "D-1", 7, 0x44),
            (40, 0, None, 7, 0), (41, 0, None, 0xE, 0x7E), (42, 2, "D-1", 7, 0x0F), (43, 0, None, 7, 0),
            (44, 0, None, 0xE, 0x7D), (45, 2, "C-1", 7, 0x9C), (46, 0, None, 0xE, 0x75), (47, 2, "C-1", 7, 0),
            (48, 0, None, 7, 0), (49, 0, None, 5, 0x03), (50, 0, None, 7, 0x81), (51, 0, None, 0xE, 0xB4),
            (52, 0, None, 7, 0), (53, 2, "C-1", 0xC, 0x08), (54, 0, None, 7, 0x88), (55, 0, None, 7, 0)]:
        p(a, 1, r, s, n, e, x)
    for r, s, n, e, x in [                       # channel 2: an empty sample, a one-shot, a swap
            (0, 4, "C-2", 4, 0x44), (2, 0, None, 4, 0), (4, 5, "C-2", 4, 0), (5, 0, None, 4, 0),
            (6, 4, None, 4, 0), (8, 4, "E-2", 7, 0x6C), (9, 0, None, 7, 0), (12, 6, "G-1", 6, 0x01),
            (13, 0, None, 6, 0x10), (14, 0, "G-1", 0xE, 0xD3), (15, 0, None, 4, 0x2C), (16, 0, None, 7, 0x2C),
            (20, 4, "A-2", 7, 0x6F), (23, 0, None, 7, 0), (40, 0, None, 7, 0), (41, 0, None, 7, 0),
            (44, 4, "C-3", 4, 0x5F)]:
        p(a, 2, r, s, n, e, x)
    for r, e, x in [(19, 0xE, 0xE1), (28, 0xE, 0xE2), (32, 0xF, 3), (44, 0xF, 6), (47, 0xF, 0x96),
                    (53, 0xF, 0x7D)]:            # channel 3: row delays, speed, tempo
        p(a, 3, r, 0, None, e, x)
    for c, s, n in ((0, 1, "C-2"), (1, 2, "C-1")):   # pattern 1: plain notes (the level and
        p(b, c, 0, s, n)                        # pitch references)
    p(b, 3, 8, 0, None, 0xB, 0)
    return smps, [0, 1], [a, b]


def random_song(rnd):
    nch = rnd.choice([4, 4, 4, 6, 8])
    smps = std_samples()
    fx = [(0, lambda: rnd.randint(1, 255)), (1, lambda: rnd.randint(0, 40)), (2, lambda: rnd.randint(0, 40)),
          (3, lambda: rnd.randint(0, 64)), (5, lambda: rnd.choice([0x10, 0x03, 0x40])),
          (9, lambda: rnd.randint(0, 20)), (0xA, lambda: rnd.choice([0x10, 0x02, 0x80, 0x0F])),
          (0xC, lambda: rnd.randint(0, 80)), (0xE, lambda: rnd.choice(
              [0x10 | rnd.randint(0, 15), 0x20 | rnd.randint(0, 15), 0x50 | rnd.randint(0, 15),
               0x90 | rnd.randint(0, 6), 0xA0 | rnd.randint(0, 15), 0xB0 | rnd.randint(0, 15),
               0xC0 | rnd.randint(0, 6), 0xD0 | rnd.randint(0, 8), 0x60 | rnd.randint(0, 2),
               0xE0 | rnd.randint(0, 2), 0x40 | rnd.randint(0, 15), 0x70 | rnd.randint(0, 15)])),
          (4, lambda: rnd.randint(0, 255)), (6, lambda: rnd.choice([0x10, 0x03, 0x40, 0])),
          (7, lambda: rnd.randint(0, 255)), (4, lambda: rnd.choice([0, 0x0F, 0xF0])),
          (0xF, lambda: rnd.choice([rnd.randint(1, 8), rnd.randint(0x20, 0xFF)])),
          (0xD, lambda: rnd.choice([0, 0x10, 0x32, 0x63])), (0xB, lambda: rnd.randint(0, 4))]
    npat = rnd.randint(1, 3)
    pats = []
    for _ in range(npat):
        p = empty(nch)
        for r in range(64):
            for ch in range(nch):
                if rnd.random() < 0.3:
                    s = rnd.choice([0, 0, 1, 2, 3, 4, 5, 6, 7])
                    note = rnd.randint(0, 35) if rnd.random() < 0.7 else None
                    e, x = (0, 0)
                    if rnd.random() < 0.6:
                        e, g = rnd.choice(fx)
                        x = g()
                        if e == 0xB and rnd.random() < 0.8:
                            e, x = 0, 0                 # few jumps
                    put(p, r, ch, s, note, e, x)
        pats.append(p)
    order = [rnd.randrange(npat) for _ in range(rnd.randint(1, 5))]
    return ("random", nch, smps, order, pats)


def selftest(work, fuzz, seed):
    os.makedirs(work, exist_ok=True)
    songs = cover_songs()
    rnd = random.Random(seed)
    songs += [random_song(rnd) for _ in range(fuzz)]
    npass = 0
    for i, (name, nch, smps, order, pats) in enumerate(songs):
        path = os.path.join(work, f"self{i}.mod")
        open(path, "wb").write(mod_file(nch, smps, order, pats))
        try:
            song = modplay.Song(path, 16.0, 1.0, strict=False)
        except ValueError as e:
            print(f"{i:3d} {name:12s}: the model refuses it: {e}")
            continue
        ok, rep = z80_test(song, work)
        npass += ok
        keep = {k: rep[k] for k in ("ticks", "model_ticks", "image_ok", "ticks_ok", "start_writes_ok",
                                    "periods_ok", "lost", "keyons", "tick_latency_ms_max", "emulator_s")
                if k in rep}
        print(f"{i:3d} {name:12s} {nch} ch: {'PASS' if ok else 'FAIL'} {keep}"
              + (f" (model: {song.problems[0][:90]})" if song.problems else ""), flush=True)
        if not ok:
            print("     " + json.dumps({k: rep[k] for k in rep if k not in keep}, default=str)[:1500])
    nloop = 0
    loops = loop_songs()
    for i, (name, nch, smps, order, pats) in enumerate(loops):
        path = os.path.join(work, f"loop{i}.mod")
        open(path, "wb").write(mod_file(nch, smps, order, pats))
        try:
            song = modplay.Song(path, strict=False, loop=True, passes=3)
        except ValueError as e:
            print(f"loop {i} {name:12s}: the model refuses it: {e}")
            continue
        ok, rep = z80_test(song, work)
        nloop += ok
        print(f"loop {i} {name:12s} {nch} ch: {'PASS' if ok else 'FAIL'} passes {rep.get('passes')} of "
              f"{len(song.pass_starts) - 1} (every {song.pass_starts[1]} ticks), ticks {rep.get('ticks')}/"
              f"{rep.get('model_ticks')}, ticks_ok {rep.get('ticks_ok')}, lost {rep.get('lost')}", flush=True)
        if not ok:
            print("     " + json.dumps(rep, default=str)[:1500])
    print(f"self test: {npass}/{len(songs)} PASS; looping: {nloop}/{len(loops)} PASS")
    return npass == len(songs) and nloop == len(loops)


# ---------------------------------------------------------------- openMSX
TCL = r"""
set renderer none
set throttle off
set ::work {%(work)s}
set ::f [open $::work/trace.txt w]
proc io_w {} {
    puts $::f "[machine_info time] $::wp_last_address $::wp_last_value"
}
proc finish {} {
    record stop
    foreach c {%(chans)s} { set "::%(dev)s_ch${c}_record" "" }
    puts $::f "end [machine_info time] ticks [debug read memory %(ticks)d] [debug read memory [expr {%(ticks)d + 1}]]"
    close $::f
    after time 0.1 exit
}
proc phase {} {
    set p $::wp_last_value
    puts $::f "phase $p [machine_info time] blocks [debug read memory 0xC001]"
    if {$p == 2} {
        set fb [open $::work/sram.bin wb]
        fconfigure $fb -translation binary
        puts -nonewline $fb [debug read_block {%(dev)s mem} 0x200000 %(nimg)d]
        close $fb
        debug set_watchpoint write_io {0x7E 0x7F} {} io_w
        debug set_watchpoint write_io {0xC4 0xC5} {} io_w
        record start -audioonly $::work/mix.wav
        foreach c {%(chans)s} { set "::%(dev)s_ch${c}_record" $::work/ch$c.wav }
        puts $::f "rec [machine_info time]"
    }
    if {$p == 3} { after time 0.3 finish }
    if {$p == 238} { after time 0.1 finish }
}
# the phase byte is watched from the cartridge's start on (the BIOS tests that RAM)
set ::armed 0
debug set_bp %(init)d {} {
    if {!$::armed} {
        set ::armed 1
        puts $::f "init [machine_info time]"
        debug set_watchpoint write_mem 0xC000 {} phase
    }
}
after time %(limit)f finish
"""


def run_openmsx(rom, work, song, labels):
    tcl = os.path.join(work, "run.tcl")
    chans = " ".join(str(o + 1) for o in sorted(song.used_channels()))
    open(tcl, "w").write(TCL % dict(work=work, chans=chans, dev=WAVE_DEV, ticks=labels["mp_ticks"],
                                    init=labels["init"], limit=song.seconds + 25, nimg=len(song.image)))
    return openmsx(["-machine", "C-BIOS_V9968_JP", "-ext", "moonsound_test", "-cart", rom,
                    "-romtype", "ASCII16", "-script", tcl], work, 900)


def openmsx(args, work, timeout):
    for f in ("trace.txt", "mix.wav", "sram.bin") + tuple(f"ch{o + 1}.wav" for o in range(24)):
        if os.path.exists(os.path.join(work, f)):
            os.remove(os.path.join(work, f))
    while int(subprocess.run(["pgrep", "-c", "openmsx"], capture_output=True, text=True).stdout or 0) >= 2:
        time.sleep(2)                               # never more than 2 openMSX at once (7 GB of RAM)
    env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
    env.setdefault("OPENMSX_SYSTEM_DATA", os.path.expanduser("~/openMSX/share"))
    t0 = time.time()
    with open(os.path.join(work, "openmsx.log"), "w") as log:
        subprocess.run([OPENMSX] + args, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    return time.time() - t0


ROM_TCL = r"""
set renderer none
set throttle off
set ::work {%(work)s}
set ::f [open $::work/trace.txt w]
set ::on 0
set ::last 0
set ::gaps {}
set ::done 0
proc note {what} {
    if {!$::done} { puts $::f "$what [machine_info time]" }
}
proc io_w {} {
    if {!$::done} { puts $::f "[machine_info time] $::wp_last_address $::wp_last_value" }
}
proc poll {} {
    if {!$::on} return
    set t [machine_info time]
    if {$::last > 0} {
        set g [expr {$t - $::last}]
        if {$g > 0.0004} { lappend ::gaps "$g $t" }
    }
    set ::last $t
}
proc finish {} {
    if {$::done} return
    set ::done 1
    record stop
    foreach c {%(chans)s} { set "::%(dev)s_ch${c}_record" "" }
    foreach g $::gaps { puts $::f "gap $g" }
    puts $::f "end [machine_info time]"
    close $::f
    after time 0.1 exit
}
proc start {} {
    if {$::on} return
    set ::on 1
    set ::last 0
    set fb [open $::work/sram.bin wb]
    fconfigure $fb -translation binary
    puts -nonewline $fb [debug read_block {%(dev)s mem} 0x200000 %(nimg)d]
    close $fb
    debug set_watchpoint write_io {0x7E 0x7F} {} io_w
    debug set_watchpoint write_io {0xC4 0xC5} {} io_w
    record start -audioonly $::work/mix.wav
    foreach c {%(chans)s} { set "::%(dev)s_ch${c}_record" $::work/ch$c.wav }
    puts $::f "rec [machine_info time]"
}
set ::up0 0
debug set_bp %(us_page)d {} { if {!$::up0} { set ::up0 1; note up0 } }
debug set_bp %(us_done)d {} { note up1 }
debug set_bp %(op_menu)d {} { note menu }
debug set_bp %(demo_init)d {} { note d }
debug set_bp %(fl_flip)d {} { note f }
debug set_bp %(mod_start)d {} { start; if {%(span)f > 0} { after time %(span)f finish } }
debug set_bp %(mod_poll)d {} poll
if {%(mod_tpoll)d} { debug set_bp %(mod_tpoll)d {} poll }
debug set_bp %(mp_end)d {} { if {$::on} { after time 0.3 finish } }
after time %(key)f { keymatrixdown 0 0x02 }
after time [expr {%(key)f + 0.2}] { keymatrixup 0 0x02 }
after time %(limit)f finish
"""


def run_openmsx_rom(rom, work, song, labels, ext, key_at, span=0.0):
    """The demo ROM, with the MoonSound: the crawl in English, its music
    recorded from mod_start to the MOD's stop (see ROM_TCL), or for span
    seconds (a MOD that loops: it never stops)."""
    tcl = os.path.join(work, "run.tcl")
    chans = " ".join(str(o + 1) for o in sorted(song.used_channels()))
    names = ("us_page", "us_done", "op_menu", "demo_init", "fl_flip", "mod_start", "mod_poll", "mp_end")
    open(tcl, "w").write(ROM_TCL % dict(work=work, chans=chans, dev=WAVE_DEV, key=key_at, nimg=len(song.image),
                                        span=span, limit=key_at + (span or song.seconds) + 30,
                                        mod_tpoll=labels.get("mod_tpoll", 0), **{k: labels[k] for k in names}))
    return openmsx(["-machine", "C-BIOS_V9968_JP"] + [x for e in ext.split() for x in ("-ext", e)]
                   + ["-cart", rom, "-romtype", "ASCII16", "-script", tcl], work, 1800)


def flip_blanks(events):
    """d/f time events -> per demo, the vertical blanks between its flips."""
    out, last = [], None
    for k, t in events:
        if k == "d":
            out.append([])
            last = None
        elif k == "f" and out:
            if last is not None:
                out[-1].append(round((t - last) * 21477270 / (1368 * 262)))
            last = t
    return out


def parse_trace(path):
    """-> dict(phase times, rec time, wave writes [(t, reg, val)], fm writes [(t, reg, val)];
    with --rom: flips and demo_inits (ev), upload and menu times, poll gaps)."""
    out = dict(phase={}, wave=[], fm=[], rec=None, end=None, ev=[], gaps=[])
    wsel = fsel = None
    for line in open(path):
        p = line.split()
        if p[0] == "init":
            out["init"] = float(p[1])
        elif p[0] == "phase":
            out["phase"][int(p[1])] = float(p[2])
            out["blocks"] = int(p[4])
        elif p[0] == "rec":
            out["rec"] = float(p[1])
        elif p[0] == "end":
            out["end"] = float(p[1])
        elif p[0] in ("d", "f"):
            out["ev"].append((p[0], float(p[1])))
        elif p[0] in ("up0", "up1", "menu"):
            out.setdefault(p[0], float(p[1]))
        elif p[0] == "gap":
            out["gaps"].append((float(p[1]), float(p[2])))
        else:
            t, port, val = float(p[0]), int(p[1]) & 0xFF, int(p[2])
            if port == 0x7E:
                wsel = val
            elif port == 0x7F:
                out["wave"].append((t, wsel, val))
            elif port == 0xC4:
                fsel = val
            elif port == 0xC5:
                out["fm"].append((t, fsel, val))
    return out


def check_trace(song, tr):
    """The writes of each tick against modplay.py; tick and key-on times."""
    fm = tr["fm"]
    start = next(t for t, r, v in fm if r == 4 and v == 0x42)     # timer 2 started: tick 0
    # the END (or mod_stop) stops the timers (60h): writes up to there
    stop = next((t for t, r, v in fm if r == 4 and v == 0x60 and t > start), float("inf"))
    clears = [t for t, r, v in fm if r == 4 and v == 0x80 and start < t < stop]
    entry = max(t for t, r, v in fm if r == 3 and t < start) - 0.05   # (mod_start's writes: the 50 ms before)
    periods = [(t, (256 - v) or 256) for t, r, v in fm if r == 3 and entry < t < stop]
    marks = [start] + clears                        # tick k's writes follow marks[k]
    got = [[] for _ in range(len(marks))]
    init = []
    k = 0
    for t, r, v in tr["wave"]:
        if t > stop:
            continue
        while k + 1 < len(marks) and t >= marks[k + 1]:
            k += 1
        (got[k] if t >= start else init).append((r, v))
    stopped = stop < float("inf")
    bad, last_ok = opl4emu.match_ticks(got, song.writes, song.heads, stopped)
    counts = [c for _, c in periods]
    ideal, real = song.times()
    # the OPL4 timer's tick starts (from the periods written) and ProTracker's
    import numpy as np
    n = min(len(clears) + 1, len(real))
    exp_timer = np.array(real[:n]) + start
    got_ticks = np.array([start] + clears[:n - 1])
    lat = got_ticks[1:] - exp_timer[1:len(got_ticks)]
    pt = np.array(ideal[:len(got_ticks)]) + start
    drift = got_ticks - pt
    # key-on times against ProTracker's time of their tick
    kon = []
    k = 0
    for t, r, v in tr["wave"]:
        if t < start:
            continue
        while k + 1 < len(marks) and t >= marks[k + 1]:
            k += 1
        if 0x68 <= r < 0x68 + 24 and v & 0x80 and k < len(ideal):
            kon.append((k, (r - 0x68) % 8, t - (start + ideal[k])))
    kerr = np.array([e for _, _, e in kon])
    first = (bad[0] if bad else len(got) - 1) if bad or not last_ok else None
    return dict(
        ticks_model=len(song.writes) - 1, ticks_seen=len(marks), ticks_ok=not bad and last_ok,
        first_mismatch=first, stopped_before_end=stopped and len(got) < len(song.writes),
        mismatch_detail=(first, got[first][:12], (song.writes[first] if first < len(song.writes) else [])[:12])
        if first is not None else None,
        start_writes=len(init), start_writes_ok=init[-len(song.writes_start):] == song.writes_start,
        periods_ok=counts[:len(song.counts)] == song.counts[:len(counts)], periods_seen=len(counts),
        timer_latency_us=dict(min=float(lat.min() * 1e6), max=float(lat.max() * 1e6), mean=float(lat.mean() * 1e6)),
        vs_protracker_ms=dict(min=float(drift.min() * 1e3), max=float(drift.max() * 1e3),
                              first_10s=float(np.abs(drift[pt < start + 10]).max() * 1e3),
                              last_10s=float(np.abs(drift[pt > pt[-1] - 10]).max() * 1e3)),
        keyon=len(kon),
        keyon_vs_protracker_ms=dict(min=float(kerr.min() * 1e3), max=float(kerr.max() * 1e3),
                                    mean=float(kerr.mean() * 1e3), p99=float(np.percentile(kerr, 99) * 1e3),
                                    over_4ms=int((kerr > 0.004).sum())),
        start=start, end=tr["end"]), kon


# ---------------------------------------------------------------- OpenMPT
def solo_mod(data, keep):
    """A copy of the MOD with the notes of every channel but `keep` removed
    (their effects stay: speed, tempo and breaks still apply)."""
    d = bytearray(data)
    nch = modplay.music.mod_channels(d)
    npat = max(d[952:1080]) + 1
    for p in range(npat):
        for r in range(64):
            for c in range(nch):
                if c == keep:
                    continue
                o = 1084 + ((p * 64 + r) * nch + c) * 4
                d[o] = 0
                d[o + 1] = 0
                d[o + 2] &= 0x0F
    return bytes(d)


def openmpt(mod_bytes, name, work, seconds, linear=True, repeat=0):
    """openmpt123 render. linear: the measuring reference (linear
    interpolation like the OPL4, no Amiga resampler or filter, no volume
    ramping, stereo separation 200 %: the Amiga's hard panning; OpenMPT's
    100 % puts MOD channels at 75 / 25). Else OpenMPT as a player plays it
    by default."""
    path = os.path.join(work, name + ".mod")
    open(path, "wb").write(mod_bytes)
    opts = (["--stereo", "200", "--filter", "2", "--ramping", "0", "--ctl", "render.resampler.emulate_amiga=0"]
            if linear else [])
    subprocess.run(["openmpt123", "--render", "--force", "--quiet", "--samplerate", str(RATE), "--channels", "2",
                    "--end-time", f"{seconds:.3f}", "--output-type", "wav", "--repeat", str(repeat)] + opts + [path],
                   check=True, stdout=subprocess.DEVNULL)
    os.remove(path)
    return path + ".wav"


def read_wav(path):
    """-> (rate, float array [n, channels])."""
    import numpy as np
    d = open(path, "rb").read()
    assert d[:4] == b"RIFF" and d[8:12] == b"WAVE", path
    i, fmt, data = 12, None, None
    while i + 8 <= len(d):
        tag, ln = d[i:i + 4], struct.unpack("<I", d[i + 4:i + 8])[0]
        body = d[i + 8:i + 8 + ln]
        if tag == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
            if fmt[0] == 0xFFFE:
                fmt = (struct.unpack("<H", body[24:26])[0],) + fmt[1:]
        elif tag == b"data":
            data = body
        i += 8 + ln + (ln & 1)
    tagf, nch, rate, _, _, bits = fmt
    if tagf == 3:
        a = np.frombuffer(data[:len(data) // 4 * 4], dtype="<f4").astype(np.float64)
    elif bits == 16:
        a = np.frombuffer(data[:len(data) // 2 * 2], dtype="<i2").astype(np.float64) / 32768
    else:
        raise ValueError(f"{path}: {bits}-bit format {tagf}")
    return rate, a[:len(a) // nch * nch].reshape(-1, nch)


def mono(a):
    return a.mean(axis=1) if a.ndim == 2 else a


# ------------------------------------------------------------------- audio
def openmpt_tick_times(song):
    """Where OpenMPT's ticks start in its render (whole samples per tick)."""
    out, s = [], 0
    for tk in song.ticks:
        out.append(s / RATE)
        s += int(RATE * 5 // (2 * tk["bpm"]))       # muldiv(rate, 5 * fract, tempo * 2 * fract), truncated
    return out


def notes_of(song, c):
    """(tick, start time ProTracker, next start or end, sample, byte offset, per, vol) of channel c."""
    ns = []
    for i, tk in enumerate(song.ticks):
        o = tk["ch"][c]
        if o["trig"]:
            ns.append([i, tk["t"], None, o["trig"][0], o["trig"][1], o["per"], o["vol"]])
        elif o.get("stop") and ns and ns[-1][2] is None:
            ns[-1][2] = tk["t"]
    end = song.ticks[-1]["t"] + song.ticks[-1]["dur"]
    for j, n in enumerate(ns):
        nxt = ns[j + 1][1] if j + 1 < len(ns) else end
        n[2] = min(n[2], nxt) if n[2] is not None else nxt
    return ns


def best_lag(x, y, maxlag, near0=0.0):
    """Lag (samples) that best aligns x to y: x[i + lag] ~ y[i]. near0: of
    the lags whose match is within near0 of the best, the one nearest 0 (a
    periodic sample matches as well a period away)."""
    import numpy as np
    n = len(y)
    cs = []
    for lag in range(-maxlag, maxlag + 1):
        a = x[maxlag + lag:maxlag + lag + n]
        if len(a) < n:
            continue
        cs.append((np.dot(a, y) / (np.linalg.norm(a) * np.linalg.norm(y) + 1e-12), lag))
    if not cs:
        return 0, -2
    best = max(c for c, _ in cs)
    c, bl = min(((c, lag) for c, lag in cs if c >= best - near0), key=lambda cl: abs(cl[1]))
    return bl, c


def pitch_ratio(x, y, cents=12.0, step=0.25):
    """The pitch of x against y in cents (x(t) best matches y(r t), r =
    2^(cents / 1200)), and the match (normalized correlation)."""
    import numpy as np
    n = len(y)
    t = np.arange(n, dtype=np.float64)
    grid = np.arange(-cents, cents + step / 2, step)
    cs = []
    for cc in grid:
        r = 2 ** (cc / 1200)
        yy = np.interp(t * r, t, y)                 # y played r times faster
        m = int(min(n, n / r)) - 2
        a, b = x[:m], yy[:m]
        cs.append(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
    cs = np.array(cs)
    k = int(cs.argmax())
    if 0 < k < len(cs) - 1:
        den = cs[k - 1] - 2 * cs[k] + cs[k + 1]
        off = 0.5 * (cs[k - 1] - cs[k + 1]) / den if den < 0 else 0
    else:
        off = 0
    return float(grid[k] + off * step), float(cs[k])


def compare_channel(song, c, xm, ym, x_off, y_times, kon_late=None):
    """Note by note: openMSX channel c (xm, aligned so that ProTracker time
    t is sample (t + x_off) * RATE) against OpenMPT's solo render (ym, tick
    times y_times). kon_late {(tick, channel): s}: how late the trace shows
    each key on; the onset is looked for around it (a periodic sample
    matches as well a period away), and onset_vs_keyon_ms is the sound's
    onset against it."""
    import numpy as np
    res = []
    for tick, t0, t1, smp, off, per, vol in notes_of(song, c):
        dur = t1 - t0
        if dur < 0.03 or vol == 0:
            continue
        xs = int(round((t0 + x_off) * RATE))
        ys = int(round(y_times[tick] * RATE))
        w = int(min(dur - 0.005, 0.25) * RATE)
        pad = int(0.004 * RATE)
        kl = (kon_late or {}).get((tick, c), 0.0)
        xs += int(round(kl * RATE))                 # (around the key on's time in the trace)
        if xs - pad < 0 or xs + w + pad > len(xm) or ys + w > len(ym):
            continue
        y = ym[ys:ys + w]
        if np.sqrt(np.mean(y ** 2)) < 1e-4:
            continue
        lag, _ = best_lag(xm[xs - pad:xs + w + pad], ym[ys:ys + int(0.03 * RATE)], pad, near0=0.02)
        lag_kon = lag
        lag += int(round(kl * RATE))
        xs -= int(round(kl * RATE))
        x = xm[xs + lag:xs + lag + w]
        cents, match = pitch_ratio(x, y)
        r38, r20 = modplay.pitch_of(per)
        want = 1200 * math.log2(modplay.pitch_hz(r38, r20) / (modplay.PAULA * 4 / per))
        lv = 20 * math.log10((np.sqrt(np.mean(x ** 2)) + 1e-12) / (np.sqrt(np.mean(y ** 2)) + 1e-12))
        late = None
        if dur > 0.6:                               # the loop: the sound near the end of a long note
            a = int((min(dur, 2.0) - 0.15) * RATE)
            wl = int(0.1 * RATE)
            yl = ym[ys + a:ys + a + wl]
            xl0 = xs + lag + a
            if xl0 + wl + pad < len(xm) and ys + a + wl <= len(ym) and np.sqrt(np.mean(yl ** 2)) > 1e-4:
                lag2, c2 = best_lag(xm[xl0 - pad:xl0 + wl + pad], yl, pad)
                late = dict(match=c2, lag_ms=lag2 / RATE * 1e3,
                            level_db=20 * math.log10((np.sqrt(np.mean(xm[xl0 + lag2:xl0 + lag2 + wl] ** 2)) + 1e-12)
                                                     / (np.sqrt(np.mean(yl ** 2)) + 1e-12)))
        res.append(dict(ch=c, tick=tick, t=t0, smp=smp, off=off, per=per / 4, vol=vol, onset_ms=lag / RATE * 1e3,
                        onset_vs_keyon_ms=lag_kon / RATE * 1e3,
                        cents=cents, cents_regs=want, match=match, level_db=lv, late=late))
    return res


def align(xm, ym, y_t0, x_guess, span=0.05):
    """Offset (s) of xm against ProTracker time, from a window at y_t0."""
    ys = int(y_t0 * RATE)
    y = ym[ys:ys + int(0.5 * RATE)]
    pad = int(span * RATE)
    xs = int((y_t0 + x_guess) * RATE)
    lag, c = best_lag(xm[xs - pad:xs + len(y) + pad], y, pad)
    return x_guess + lag / RATE, c


def frames_db(a, n=2205):
    import numpy as np
    m = len(a) // n
    r = np.sqrt((a[:m * n].reshape(m, n) ** 2).mean(axis=1))
    return 20 * np.log10(r + 1e-9)


def bands_db(x, y, edges=(250, 1000, 4000, 10000, 12000, 16000, 22050)):
    """Each band's share of the total energy, x against y (dB)."""
    import numpy as np
    out = {}
    fx, fy = np.abs(np.fft.rfft(x)) ** 2, np.abs(np.fft.rfft(y)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / RATE)
    for lo, hi in zip(edges, edges[1:]):
        sel = (f >= lo) & (f < hi)
        out[f"{lo / 1000:g}-{hi / 1000:g} kHz"] = round(float(
            10 * np.log10((fx[sel].sum() / fx.sum() + 1e-15) / (fy[sel].sum() / fy.sum() + 1e-15))), 1)
    return out


# ------------------------------------------------------------------- cost
COST_TCL = r"""
set renderer none
set throttle off
set ::f [open "%(work)s/cost.txt" w]
set ::tin 0
set ::on 0
set ::fsel 0
proc pin {} { set ::tin [machine_info time] }
proc pout {} {
    if {!$::on} return
    set d [expr {[machine_info time] - $::tin}]
    if {$d > 0.00002} { puts $::f "p $::tin $d" }
}
proc fmw {} {
    if {($::wp_last_address & 0xFF) == 0xC4} { set ::fsel $::wp_last_value; return }
    if {$::fsel != 4} return
    set v $::wp_last_value
    if {$v == 0x80} { puts $::f "c [machine_info time]" }
    if {$v == 0x42} { set ::on 1; puts $::f "s [machine_info time]" }
    if {$v == 0x60 && $::on} { set ::on 0 }
}
proc done {} { close $::f; exit }
debug set_watchpoint write_io {0xC4 0xC5} {} fmw
debug set_bp %(poll)d {} pin
debug set_bp %(ret)d {} pout
debug set_watchpoint write_mem 0xC000 {} { if {$::wp_last_value == 3 || $::wp_last_value == 238} { after time 0.1 done } }
after time %(limit)f done
"""


def cost_test(song, work):
    """The test ROM in openMSX, every mod_poll call timed (breakpoints on the
    call and its return; the polls that do nothing, under 20 us, left out):
    per tick, the T-states (3.58 MHz, M1 wait included) of the polls between
    its start and the next one's, which work out the next tick; the longest
    poll. -> report."""
    rom, _, labels = build(song, work, 0)
    tcl = os.path.join(work, "cost.tcl")
    open(tcl, "w").write(COST_TCL % dict(work=work, poll=labels["mod_poll"], ret=labels["t_loop"] + 3,
                                         limit=song.seconds + 25))
    openmsx(["-machine", "C-BIOS_V9968_JP", "-ext", "moonsound_test", "-cart", rom, "-romtype", "ASCII16",
             "-script", tcl], work, 1800)
    polls, marks = [], []
    for line in open(os.path.join(work, "cost.txt")):
        p = line.split()
        if p[0] == "p":
            polls.append((float(p[1]), float(p[2])))
        elif p[0] in "sc":
            marks.append(float(p[1]))
    hz = opl4emu.T_HZ
    per_tick, j, longest = [], 0, 0.0
    for a, b in zip(marks, marks[1:]):
        while j < len(polls) and polls[j][0] < a:
            j += 1
        w = 0.0
        while j < len(polls) and polls[j][0] < b:
            w += polls[j][1]
            longest = max(longest, polls[j][1])
            j += 1
        per_tick.append(w * hz)
    wt = sorted(per_tick)
    tick = 2.5 / 125 * hz
    return dict(ticks=len(wt), module_bytes=labels["mp_new2"] + 20 - labels["mod_reset"],
                work_T_per_tick=dict(mean=round(sum(wt) / len(wt)), median=round(wt[len(wt) // 2]),
                                     p99=round(wt[int(len(wt) * 0.99)]), max=round(wt[-1])),
                longest_poll_T=round(longest * hz),
                cpu_at_125_bpm_pct=dict(mean=round(100 * sum(wt) / len(wt) / tick, 1),
                                        max=round(100 * wt[-1] / tick, 1)))


# ------------------------------------------------------------------ probe
def probe_pitch(seg):
    """The rate (bytes per output sample) of the probe's triangle in seg: its
    zero crossings are 8 bytes apart, whatever the level. None: too few."""
    import numpy as np
    s = np.sign(seg)
    i = np.nonzero((s[:-1] != s[1:]) & (s[:-1] != 0))[0]
    if len(i) < 3:
        return None
    tz = i + seg[i] / (seg[i] - seg[i + 1])
    return 8 / np.polyfit(np.arange(len(tz)), tz, 1)[0]


def probe_level(seg, end):
    """The level of the probe's square at index end of seg (its plateaus,
    fitted with a line: OpenMPT ramps a volume change over the tick)."""
    import numpy as np
    v = np.abs(seg)
    d = np.abs(np.diff(v))
    keep = np.nonzero(d < 3 * np.median(d) + 1e-6)[0]
    if len(keep) < 50 or v[keep].max() < 1e-5:
        return 0.0
    return float(max(0.0, np.polyval(np.polyfit(keep, v[keep], 1), end)))


def probe_ticks(song):
    """The ticks of the probe song's first pass (to the jump back to row 0)."""
    return next(i for i, tk in enumerate(song.ticks) if i and tk["pos"] == (0, 0, 0))


def probe_curves(x, bounds, song, n, chan, kind, cut=0.005):
    """Per tick (the first n) of channel chan: the probe's pitch (rate, bytes
    per output sample) or level in x, between bounds[k] and bounds[k + 1]
    (s), cut s off each end; None where the channel is silent."""
    out = []
    for k in range(n):
        o = song.ticks[k]["ch"][chan]
        a, b = int((bounds[k] + cut) * RATE), int((bounds[k + 1] - cut / 5) * RATE)
        if not o["sounding"] or b - a < 200 or b > len(x):
            out.append(None)
            continue
        out.append(probe_pitch(x[a:b]) if kind == "pitch" else probe_level(x[a:b], bounds[k + 1] * RATE - a))
    return out


def probe_test(work, no_openmsx):
    """The probe song (probe_song) in the test ROM in openMSX and in OpenMPT,
    tick by tick: channel 0's pitch (vibrato) and channel 1's level (tremolo)
    against the model (the registers it writes; for OpenMPT the period and
    volume of ProTracker's model), and openMSX against OpenMPT. -> ok."""
    import numpy as np
    path = os.path.join(work, "probe.mod")
    open(path, "wb").write(mod_file(4, *probe_song()))
    ticks0 = modplay.Player(*modplay.parse(open(path, "rb").read())).run(60)
    first = next(i for i, tk in enumerate(ticks0) if i and tk["pos"] == (0, 0, 0))
    pass_s = sum(tk["dur"] for tk in ticks0[:first])
    song = modplay.Song(path, pass_s + 3, 1.0)
    n = probe_ticks(song)
    rom, _, labels = build(song, work, 0)
    if not no_openmsx:
        print(f"openMSX: {run_openmsx(rom, work, song, labels):.0f} s")
    tr = parse_trace(os.path.join(work, "trace.txt"))
    chk, _ = check_trace(song, tr)
    fm = tr["fm"]
    start = chk["start"]
    clears = [t for t, r, v in fm if r == 4 and v == 0x80 and t > start]
    xb = [t - tr["rec"] for t in [start] + clears]              # openMSX: the ticks in its recording
    yb = openmpt_tick_times(song) + [openmpt_tick_times(song)[-1] + song.ticks[-1]["dur"]]
    data = open(path, "rb").read()
    rep = dict(ticks=n, trace_ticks_ok=chk["ticks_ok"], trace_start_writes_ok=chk["start_writes_ok"])
    ok = chk["ticks_ok"] and chk["start_writes_ok"]
    for chan, kind in ((0, "pitch"), (1, "level")):
        x = mono(sum(read_wav(os.path.join(work, f"ch{o + 1}.wav"))[1] for o in (chan, chan + 8, chan + 16)))
        y = mono(read_wav(openmpt(solo_mod(data, chan), f"probe_ch{chan}", work, pass_s + 1))[1])
        mx, my = probe_curves(x, xb, song, n, chan, kind), probe_curves(y, yb, song, n, chan, kind)
        rows = []
        for k in range(n):
            o = song.ticks[k]["ch"][chan]
            if mx[k] is None or my[k] is None:
                continue
            if kind == "pitch":
                per = o["per"]
                want = modplay.pitch_hz(*modplay.pitch_of(per))
                rows.append((k, per, modplay.PAULA * 4 / (my[k] * RATE), 1200 * math.log2(mx[k] * RATE / want),
                             1200 * math.log2(mx[k] / my[k])))
            else:
                rows.append((k, o["v4"], modplay.level_of(o["v4"], 64), my[k], mx[k]))
        if kind == "pitch":
            per_bad = [(k, p, round(q, 2)) for k, p, q, _, _ in rows if abs(q - p) > 0.45]
            # (OpenMPT keeps the period at or above its Amiga limit, 453 = 113.25 for finetune 0,
            # where ProTracker and this player play B-3 at 113: known, 3.8 cents)
            known = [b for b in per_bad if song.ticks[b[0]]["ch"][0]["base"] == 452 and abs(b[2] - b[1] - 1) < 0.05]
            c_regs = np.array([r[3] for r in rows])
            c_mpt = np.array([r[4] for r in rows])
            rep["vibrato_pitch"] = dict(
                ticks=len(rows), distinct_periods=len({r[1] for r in rows}),
                openmpt_period_mismatches=len(per_bad), openmpt_mismatch_ticks=per_bad[:8],
                of_them_b3_amiga_limit=len(known),
                openmsx_cents_vs_registers=dict(max_abs=round(float(np.abs(c_regs).max()), 3),
                                                p95_abs=round(float(np.percentile(np.abs(c_regs), 95)), 3)),
                openmsx_cents_vs_openmpt=dict(min=round(float(c_mpt.min()), 3), max=round(float(c_mpt.max()), 3),
                                              p95_abs=round(float(np.percentile(np.abs(c_mpt), 95)), 3)))
            ok = ok and len(known) == len(per_bad) and np.abs(c_regs).max() < 0.5
        else:
            ref_y = np.median([r[3] for r in rows if r[1] == 256])
            ref_x = np.median([r[4] for r in rows if r[2] == 1])
            lv_bad, d_regs, d_mpt = [], [], []
            for k, v4, reg, ly, lx in rows:
                if abs(ly / ref_y * 256 - v4) > 0.6:      # (OpenMPT's ramp over a tick: within 0.6)
                    lv_bad.append((k, v4, round(ly / ref_y * 256, 2)))
                g = modplay.tl_gain(reg >> 1) if reg != 0xFF else 0.0
                if g > 0.004 and lx > 0:                    # above -48 dB
                    d_regs.append(20 * math.log10(lx / ref_x / g))
                    d_mpt.append(20 * math.log10(lx / ref_x / (ly / ref_y)))
                elif g == 0 and lx > 1e-4 * ref_x:
                    d_regs.append(99.0)
            d_regs, d_mpt = np.array(d_regs), np.array(d_mpt)
            rep["tremolo_level"] = dict(
                ticks=len(rows), distinct_levels=len({r[1] for r in rows}), openmpt_volume_mismatches=len(lv_bad),
                openmpt_mismatch_ticks=lv_bad[:8],
                openmsx_db_vs_registers=dict(max_abs=round(float(np.abs(d_regs).max()), 3),
                                             p95_abs=round(float(np.percentile(np.abs(d_regs), 95)), 3)),
                openmsx_db_vs_openmpt=dict(min=round(float(d_mpt.min()), 3), max=round(float(d_mpt.max()), 3),
                                           p95_abs=round(float(np.percentile(np.abs(d_mpt), 95)), 3)))
            ok = ok and not lv_bad and np.abs(d_regs).max() < 0.1
        json.dump(rows, open(os.path.join(work, f"probe_{kind}.json"), "w"))
    print(json.dumps(rep, indent=1, default=str))
    print("probe: " + ("PASS" if ok else "FAIL"))
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mod", nargs="?")
    ap.add_argument("--seconds", type=float, default=3420 / modplay.music.VBLANK_HZ)
    ap.add_argument("--fade", type=float, default=180 / modplay.music.VBLANK_HZ)
    ap.add_argument("--gap", type=int, default=0, help="busy loop between polls (rounds of 10.7 us)")
    ap.add_argument("--work", default=os.path.join(HERE, "out", "modplay_test"))
    ap.add_argument("--preview", default=None, help="copy the recordings and the reference here")
    ap.add_argument("--tag", default="", help="suffix of the preview file names")
    ap.add_argument("--no-openmsx", action="store_true", help="analyse the last run again")
    ap.add_argument("--rom", help="the demo ROM (build_rom.py --music MOD) instead of the test ROM")
    ap.add_argument("--labels", help="its labels (default: labels.txt / labels_98.txt next to it)")
    ap.add_argument("--ext", default="geo3d moonsound", help="--rom: the openMSX extensions")
    ap.add_argument("--key-at", type=float, default=8.5, help="--rom: when '1' (English) is pressed (s)")
    ap.add_argument("--flips-ref", help="--rom: d/f log of a reference run, to compare the crawl's flips")
    ap.add_argument("--z80", action="store_true", help="the test ROM in the Z80 emulator (no openMSX)")
    ap.add_argument("--kb", type=int, default=640, help="--z80: sample RAM of the emulated MoonSound")
    ap.add_argument("--selftest", action="store_true", help="--z80 on MODs made here (every effect)")
    ap.add_argument("--fuzz", type=int, default=12, help="--selftest: this many random MODs too")
    ap.add_argument("--seed", type=int, default=1985)
    ap.add_argument("--lenient", action="store_true", help="test a MOD the build would refuse (where the ROM "
                    "player does not play what ProTracker plays, e.g. an instrument swap): the model's view")
    ap.add_argument("--probe", action="store_true", help="the vibrato / tremolo probe, tick by tick, in "
                    "openMSX and OpenMPT (no MOD argument)")
    ap.add_argument("--cost", action="store_true", help="the player's CPU time per tick, in openMSX")
    ap.add_argument("--loop", action="store_true", help="--rom: a demo ROM whose MOD loops (build_rom.py): "
                    "the whole song, recorded for --seconds from mod_start (default 230: a pass and more)")
    a = ap.parse_args()
    if a.loop and a.seconds == 3420 / modplay.music.VBLANK_HZ:
        a.seconds = 230.0
    work = os.path.abspath(a.work)
    os.makedirs(work, exist_ok=True)
    if a.selftest:
        sys.exit(0 if selftest(os.path.join(work, "self"), a.fuzz, a.seed) else 1)
    if a.probe:
        os.makedirs(os.path.join(work, "probe"), exist_ok=True)
        sys.exit(0 if probe_test(os.path.join(work, "probe"), a.no_openmsx) else 1)
    if a.loop:                                      # the model covers the recording (and a bit)
        song = modplay.Song(a.mod, strict=not a.lenient, loop=True, passes=1)
        passes = int(a.seconds / song.seconds) + 2
        song = modplay.Song(a.mod, strict=not a.lenient, loop=True, passes=passes)
        a.fade = 0.0
    else:
        song = modplay.Song(a.mod, a.seconds, a.fade, strict=not a.lenient)
    print(song.summary())
    for p in song.problems:
        print("not as ProTracker: " + p[:400])
    if a.cost:
        print(json.dumps(cost_test(song, work), indent=1))
        sys.exit(0)
    if a.z80:
        ok, rep = z80_test(song, work, a.kb, a.gap)
        print(json.dumps(rep, indent=1, default=str))
        print("Z80: " + ("PASS" if ok else "FAIL"))
        sys.exit(0 if ok else 1)
    import numpy as np
    if a.rom:
        rom = os.path.abspath(a.rom)
        lab = a.labels or os.path.join(os.path.dirname(rom), "labels_98.txt" if "_98" in os.path.basename(rom)
                                       else "labels.txt")
        labels = labels_of(lab)
        print(f"demo ROM {rom}, labels {lab}")
    else:
        rom, b0, labels = build(song, work, a.gap)
        print(f"test ROM {rom}: bank 0 {b0} bytes (module {labels['mp_new2'] + 20 - labels['mod_reset']} bytes)")
    if not a.no_openmsx:
        wall = (run_openmsx_rom(rom, work, song, labels, a.ext, a.key_at, a.seconds if a.loop else 0.0) if a.rom
                else run_openmsx(rom, work, song, labels))
        print(f"openMSX: {wall:.0f} s")
    tr = parse_trace(os.path.join(work, "trace.txt"))
    ph = tr["phase"]
    report = dict(mod=os.path.basename(a.mod), seconds=a.seconds, fade=a.fade, gap=a.gap,
                  summary=song.summary(), sram_blocks_found=tr.get("blocks"),
                  upload_s=(tr.get("up1", 0) - tr.get("up0", 0)) if a.rom else ph.get(2, 0) - ph.get(1, 0),
                  rom_mod_bytes=len(song.mod), sample_ram_bytes=len(song.image))
    chk, kon = check_trace(song, tr)
    report["trace"] = chk
    sram = os.path.join(work, "sram.bin")          # the sample RAM when the song starts
    report["sample_ram_ok"] = os.path.exists(sram) and open(sram, "rb").read() == song.image
    if a.rom:
        # the upload against the menu; the polls; the crawl's flips
        gaps = sorted(tr["gaps"], reverse=True)
        fl = flip_blanks(tr["ev"])
        crawl = fl[1] if len(fl) > 1 else []
        report["rom"] = dict(
            menu_s=tr.get("menu"), upload_first_unit_s=tr.get("up0"), upload_done_s=tr.get("up1"),
            upload_after_menu_s=(tr["up1"] - tr["menu"]) if "up1" in tr and "menu" in tr else None,
            mod_start_s=chk["start"], key_at_s=a.key_at,
            poll_gaps_ms=[(round(g * 1e3, 3), round(t - chk["start"], 3)) for g, t in gaps[:5]],
            poll_gaps_over_1ms=sum(1 for g, _ in gaps if g > 0.001),
            crawl_flips=len(crawl), crawl_blanks=sum(crawl))
        if a.flips_ref:
            ref = flip_blanks([(p[0], float(p[1])) for p in (l.split() for l in open(a.flips_ref))
                               if p and p[0] in ("d", "f")])
            rc = ref[1] if len(ref) > 1 else []
            m = min(len(crawl), len(rc))
            report["rom"]["crawl_flips_vs_ref"] = dict(
                compared=m, identical=crawl[:m] == rc[:m],
                diffs=[(j, crawl[j], rc[j]) for j in range(m) if crawl[j] != rc[j]][:5])
    print(json.dumps(report, indent=1, default=str)[:3000])

    # ---- audio: OpenMPT solo renders of the original MOD against openMSX's channels
    data = open(a.mod, "rb").read()
    span = a.seconds + 0.5
    yt = openmpt_tick_times(song)
    ideal, _ = song.times()
    start = chk["start"]
    x0 = start - tr["rec"]                          # ProTracker time 0 in the recordings (first guess)
    notes = []
    offs = []
    rpt = 1 + int(span / song.pass_seconds()) if a.loop else 0
    for c in range(song.nch):
        ref = openmpt(solo_mod(data, c), f"ref_ch{c + 1}", work, span, repeat=rpt)
        _, ym = read_wav(ref)
        xm = sum(read_wav(os.path.join(work, f"ch{o + 1}.wav"))[1] for o in (c, c + 8, c + 16))
        ym, xm = mono(ym), mono(xm)
        first = next((n for n in notes_of(song, c) if n[2] - n[1] > 0.3 and n[6]), None)
        off, cc = align(xm, ym, yt[first[0]], x0 + ideal[first[0]] - yt[first[0]]) if first else (x0, 0)
        off -= ideal[first[0]] - yt[first[0]] if first else 0
        offs.append((off, cc))
        notes += compare_channel(song, c, xm, ym, off, yt, {(k, ch): late for k, ch, late in kon})
    on = np.array([n["onset_ms"] for n in notes])
    fade_at = a.seconds - a.fade if not a.loop else a.seconds + 1
    clean = [n for n in notes if n["match"] > 0.9 and n["t"] + 0.3 < fade_at]   # the fade changes levels
    report["audio"] = dict(
        channel_offsets_ms=[round(o * 1e3, 3) for o, _ in offs],
        notes_compared=len(notes), notes_matched=int((np.array([n["match"] for n in notes]) > 0.9).sum()),
        notes_before_fade=len(clean))
    if clean:                                       # (none: the sound is not the MOD's)
        ce = np.array([n["cents"] - n["cents_regs"] for n in clean])
        cabs = np.array([n["cents"] for n in clean])
        lv = np.array([n["level_db"] for n in clean])
        med = float(np.median(lv))
        lates = [n["late"] for n in clean if n["late"] and n["t"] + 2.1 < fade_at]
        report["audio"].update(
            onset_vs_keyon_ms=dict(min=float(min(n["onset_vs_keyon_ms"] for n in notes)),
                                   max=float(max(n["onset_vs_keyon_ms"] for n in notes))),
            onset_ms=dict(min=float(on.min()), max=float(on.max()), mean=float(on.mean()),
                          p99=float(np.percentile(on, 99)),
                          abs_max_first_10s=float(np.abs([n["onset_ms"] for n in notes if n["t"] < 10]).max()),
                          abs_max_last_10s=float(np.abs([n["onset_ms"] for n in notes
                                                         if n["t"] > a.seconds - 10]).max())),
            cents_openmsx_vs_openmpt=dict(min=float(cabs.min()), max=float(cabs.max()),
                                          abs_p95=float(np.percentile(np.abs(cabs), 95))),
            cents_minus_register_prediction=dict(min=float(ce.min()), max=float(ce.max()),
                                                 abs_p95=float(np.percentile(np.abs(ce), 95))),
            level_db=dict(median=med, dev_min=float(lv.min() - med), dev_max=float(lv.max() - med),
                          abs_dev_p95=float(np.percentile(np.abs(lv - med), 95))),
            loops=dict(n=len(lates), match_min=float(min(lt["match"] for lt in lates)) if lates else None,
                       level_dev_max=float(max(abs(lt["level_db"] - med) for lt in lates)) if lates else None,
                       lag_ms_max=float(max(abs(lt["lag_ms"]) for lt in lates)) if lates else None))
    worst = sorted(notes, key=lambda n: n["match"])[:3]
    report["audio"]["worst_matches"] = [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in n.items()
                                         if k != "late"} for n in worst]

    # ---- the mix: openMSX against OpenMPT (linear) and the plain OpenMPT render
    ref_mix = openmpt(data, "ref_mix_linear", work, span, repeat=rpt)
    ref_def = openmpt(data, "ref_mix_default", work, span, linear=False, repeat=rpt)
    _, ym = read_wav(ref_mix)
    _, yd = read_wav(ref_def)
    _, xm = read_wav(os.path.join(work, "mix.wav"))
    moff = float(np.median([o for o, _ in offs]))
    xs = int(round(moff * RATE))
    n = min(len(ym), len(xm) - xs, int((a.seconds - a.fade) * RATE))
    if xs >= 0 and n > 0:
        blk = []
        for side in (0, 1):
            fx, fy = frames_db(xm[xs:xs + n, side]), frames_db(ym[:n, side])
            loud = fy > fy.max() - 40
            gain = np.median(fx[loud] - fy[loud])
            dev = np.abs(fx[loud] - fy[loud] - gain)
            blk.append(dict(gain_db=float(gain), frame_dev_db_p95=float(np.percentile(dev, 95))))
        lags = []
        for s0 in np.arange(1.0, a.seconds - a.fade - 1, 2.0):
            i0 = int(s0 * RATE)
            y = ym[i0:i0 + int(0.25 * RATE)].sum(axis=1)
            if np.sqrt(np.mean(y ** 2)) < 1e-3:
                continue
            lag, cc = best_lag(xm[xs + i0 - 441:xs + i0 + len(y) + 441].sum(axis=1), y, 441)
            lags.append((float(s0), lag / RATE * 1e3, float(cc)))
        lg = np.array([lag for _, lag, _ in lags])
        report["mix"] = dict(sides=blk, lag_ms_every_2s=dict(n=len(lg), min=float(lg.min()), max=float(lg.max()),
                                                             first=float(lg[0]), last=float(lg[-1])))
        report["mix_lags"] = [(s, round(lag, 3), round(cc, 3)) for s, lag, cc in lags]
        if not a.loop:
            tail = xm[xs + int(a.seconds * RATE) + int(0.05 * RATE):]
            report["mix"]["after_end_peak"] = float(np.abs(tail).max()) if len(tail) else None
            fade = xm[xs + int((a.seconds - a.fade) * RATE):xs + int(a.seconds * RATE)]
            report["mix"]["fade_db_per_s"] = [round(float(v), 1) for v in frames_db(fade.mean(axis=1), RATE // 2)]
        m2 = min(n, len(yd))
        x_, l_, d_ = xm[xs:xs + m2].mean(axis=1), ym[:m2].mean(axis=1), yd[:m2].mean(axis=1)
        report["spectrum"] = dict(vs_openmpt_linear=bands_db(x_, l_), vs_openmpt_default=bands_db(x_, d_),
                                  openmpt_linear_vs_default=bands_db(l_, d_))
    json.dump(dict(report, notes=notes), open(os.path.join(work, "report.json"), "w"), indent=1, default=str)
    print(json.dumps({k: report[k] for k in ("audio", "mix", "spectrum") if k in report}, indent=1, default=str))
    if a.preview:
        os.makedirs(a.preview, exist_ok=True)
        sfx = a.tag
        shutil.copy(os.path.join(work, "mix.wav"),
                    os.path.join(a.preview, f"crawl_music_MOD_MoonSound-wave{sfx}.wav" if a.rom
                                 else f"modplay_openmsx_moonsound_wave_first57s{sfx}.wav"))
        span_tag = "fullsong" if a.loop else "first57s"
        shutil.copy(ref_mix, os.path.join(a.preview, f"modplay_openmpt_reference_amiga-pan_linear_{span_tag}.wav"))
        shutil.copy(ref_def, os.path.join(a.preview, f"modplay_openmpt_reference_default_{span_tag}.wav"))
        print("preview:", a.preview)


if __name__ == "__main__":
    main()
