#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""PSG sound effects of the shooter, as register tables, and WAV previews.

One effect per PSG channel at a time; an effect replaces the one playing on
its channel when its priority is the same or higher. The player steps
every effect once per vertical blank (60 Hz):

  sfx_<name>:
      db channel (0 A, 1 B, 2 C), priority
      frames: dw tone period (0-4095; 0xFFFF = tone off)
              db noise period (0-31; 0x80 = noise off)
              db volume (0-15)
      db 0xFF, 0xFF, 0xFF, 0xFF   ; end (noise byte 0xFF): the channel goes silent

Per frame the player writes R#(2ch), R#(2ch+1) (tone), R#6 (noise period,
shared by the three channels: the last writer wins), the channel's two bits
of R#7 (tone enable bit ch, noise enable bit ch+3, both active low; keep
R#7 bits 6-7 as the machine needs them, 10xxxxxx on MSX) and R#(8+ch).
PSG clock 1,789,773 Hz: tone f = clock / (16 * period).
"""
import os
import struct

import common as C

CLOCK = 1789773
A, B, CH = 0, 1, 2
OFF_T, OFF_N = 0xFFFF, 0x80


def note(freq):
    return int(round(CLOCK / (16 * freq)))


def fx_shot():
    return A, 1, [(50 + 20 * i, OFF_N, v) for i, v in enumerate((14, 13, 12, 10, 8, 5))]


def fx_eshot():
    return A, 0, [(300 + 40 * i, OFF_N, v) for i, v in enumerate((10, 9, 7, 5, 3))]


def fx_hit():
    return CH, 1, [(200, 3, v) for v in (13, 11, 8, 4)]


def fx_explode():
    n = 22
    return CH, 2, [(OFF_T, min(31, 10 + i), max(0, 15 - (15 * i) // n)) for i in range(n)]


def fx_bigboom():
    n = 50
    return CH, 3, [(1500 + 30 * i if i < 20 else OFF_T, min(31, 16 + i // 3),
                    max(0, 15 - (15 * i * i) // (n * n))) for i in range(n)]


def fx_death():
    n = 60
    return CH, 4, [(200 + 17 * i, min(31, 12 + i // 3), max(0, 15 - (15 * i) // n)) for i in range(n)]


def fx_extra():
    seq = []
    for f in (1046.5, 1318.5, 1568.0, 2093.0):
        seq += [(note(f), OFF_N, 12)] * 5
    return B, 2, seq + [(note(2093.0), OFF_N, v) for v in (10, 8, 6, 4, 2)]


def fx_start():
    seq = []
    for f in (784.0, 1046.5, 1318.5, 1568.0):
        seq += [(note(f), OFF_N, 12)] * 6
    return B, 2, seq + [(note(1568.0), OFF_N, v) for v in (11, 10, 9, 8, 6, 4, 2)]


def fx_warn():
    seq = []
    for k in range(6):
        seq += [(180 if k % 2 == 0 else 140, OFF_N, 12)] * 8
    return B, 2, seq


EFFECTS = [("shot", fx_shot), ("eshot", fx_eshot), ("hit", fx_hit), ("explode", fx_explode),
           ("bigboom", fx_bigboom), ("death", fx_death), ("extra", fx_extra), ("start", fx_start),
           ("warn", fx_warn)]


def effect_bytes(fx):
    ch, prio, frames = fx
    out = bytes([ch, prio])
    for t, n, v in frames:
        out += struct.pack("<HBB", t, n, v)
    return out + b"\xff\xff\xff\xff"


# ------------------------------------------------------------------ WAV
AY_VOL = [0.0] + [10 ** ((v - 15) * 1.5 / 20) for v in range(1, 16)]   # 1.5 dB per step


def render_wav(fx, path, rate=44100):
    ch, prio, frames = fx
    spf = rate // 60
    out = []
    phase, lfsr, nphase, nbit = 0.0, 1, 0.0, 1
    for t, n, v in frames + [(OFF_T, OFF_N, 0)] * 6:
        amp = AY_VOL[v] * 0.6
        tf = CLOCK / (16 * t) if t != OFF_T and t > 0 else 0
        nf = CLOCK / (16 * n) if n != OFF_N and n > 0 else 0
        for _ in range(spf):
            ton = 1
            if tf:
                phase = (phase + tf / rate) % 1.0
                ton = 1 if phase < 0.5 else 0
            non = 1
            if nf:
                nphase += nf / rate
                while nphase >= 1:
                    nphase -= 1
                    nbit = lfsr & 1
                    lfsr = (lfsr >> 1) | ((((lfsr >> 0) ^ (lfsr >> 3)) & 1) << 16)
                non = nbit
            on = (ton if tf else 1) & (non if nf else 1)
            if not tf and not nf:
                on = 0
            out.append(int(32767 * amp * (1 if on else -1) * (1 if (tf or nf) else 0)))
    with open(path, "wb") as f:
        data = struct.pack("<%dh" % len(out), *out)
        f.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " +
                struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" +
                struct.pack("<I", len(data)) + data)


def export(verbose=True, wav_dir=None):
    lines = ["; PSG sound effects; format in geo3d/game/tools/sfx.py"]
    total = 0
    for k, (name, fn) in enumerate(EFFECTS):
        fx = fn()
        b = effect_bytes(fx)
        total += len(b)
        lines.append(f"SFX_{name.upper()}: equ {k}")
        lines.append(f"sfx_{name}:\t\t; channel {'ABC'[fx[0]]}, priority {fx[1]}, "
                     f"{len(fx[2])} frames ({len(fx[2]) / 60:.2f} s)")
        lines += C.asm_bytes(b, 14)
        if wav_dir:
            os.makedirs(wav_dir, exist_ok=True)
            render_wav(fx, os.path.join(wav_dir, f"sfx_{name}.wav"))
    lines.append("sfx_table:")
    lines += ["        dw " + ", ".join(f"sfx_{n}" for n, _ in EFFECTS)]
    path = C.write_asm("sfx.asm", "sfx.py", lines)
    if verbose:
        print(f"  sfx: {len(EFFECTS)} effects, {total} bytes")
    return path, total


if __name__ == "__main__":
    export(wav_dir=os.path.join(C.PREVIEW, "sfx"))
