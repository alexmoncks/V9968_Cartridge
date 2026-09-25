#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""MIDI or MOD to MSX sound chips, for the demo ROM's music.

Reads a Standard MIDI File (own parser, no dependency) or a ProTracker MOD
(own player, numpy for the sample analysis) and reduces the arrangement, tick
by tick at 60 Hz (the player advances the music once per vertical blank), to
three targets:

  psg  the MSX PSG (AY-3-8910): melody, bass, harmony + snare on the noise
  scc  Konami SCC (5 wavetable channels) + PSG: melody, 2 harmonies, 2 string
       voices on the SCC; bass, texture, timpani + snare on the PSG
  opl  the FM part of an OPL4 (MoonSound) or an OPL3 at C4h-C7h, 18 channels
       spread over the tonal tracks with one FM patch per family; mallets /
       harp and snare on the PSG. F-numbers are for the OPL3 (49716 Hz); on
       an OPL4 (FM at 49517 Hz) the player raises them by 1/256 at key on.

The tracks are classified by General MIDI program and register, so any
orchestral MIDI works; the melody follows the first family that plays
(trumpets, horns, high strings, high woodwinds), and sticks to its track
through short rests.

Each target becomes a byte stream the player runs (geo3d_rom.asm, music_tick):
  00-0D v                        PSG register op (0..13) = v
  10-1F v                        SCC register 80h + (op - 10h) = v (9880h-988Fh)
  20-23 + 32 bytes               SCC waveform of channel op - 20h (9800h + 32 ch)
  30 r v / 31 r v                OPL register r = v, bank 0 / bank 1
  40-51 a b t                    OPL channel op - 40h: A0 = a, B0 = b (key on),
                                 carrier TL = t
  60-71                          OPL channel op - 60h: key off
  80-FC                          end of this tick, then op - 7Fh ticks in all
  FE                             continue at the start of the next bank
  FF                             end of the music (the player mutes everything)

A MOD becomes the same thing a MIDI file does: one track per sample, its
notes at the true pitch of the sample (the fundamental measured in the sample
data, at the period played) and a General MIDI program (or a drum key) chosen
from the sound of the sample, so the same arranger handles both (read_mod).

The MIDI or MOD itself is an input (build_rom.py --music): music you do not
own must stay out of the repository.
"""
import cmath
import math
import struct

TICK_HZ = 60
VBLANK_HZ = 21477270 / (1368 * 262)   # the player's real tick: an NTSC V99x8 frame, 59.92 Hz
PSG_CLOCK = 1789772.5
SCC_CLOCK = 3579545.0
OPL_RATE = 49716.0                 # OPL3 sample rate (OPL4 FM: 49517, 7 cents off)


# ------------------------------------------------------------------ MIDI file
def _vlq(data, i):
    v = 0
    while True:
        b = data[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i


def read_midi(path):
    """Returns (tracks, length_s). Each track: dict(name, program, channel,
    notes=[(start_s, end_s, pitch, velocity)]), one per (MTrk chunk, MIDI
    channel), so format 0 files and multi-channel tracks split by channel.
    The program is the one in force on that channel (program changes may sit
    in any track). Notes left on are closed by All Notes Off / All Sound Off
    (CC 123 / 120) or at the end of their track. Tempo map applied."""
    data = open(path, "rb").read()
    assert data[:4] == b"MThd", "not a Standard MIDI File"
    hlen, fmt, ntrk, div = struct.unpack(">IHHH", data[4:14])
    assert not div & 0x8000, "SMPTE time division is not supported"
    i = 8 + hlen
    raw = []                                   # per track: [(abs_tick, kind, args)]
    tempos = []
    for _ in range(ntrk):
        assert data[i:i + 4] == b"MTrk"
        (tlen,) = struct.unpack(">I", data[i + 4:i + 8])
        j, end = i + 8, i + 8 + tlen
        i = end
        t, status, ev = 0, 0, []
        while j < end:
            dt, j = _vlq(data, j)
            t += dt
            b = data[j]
            if b == 0xFF:
                kind, j = data[j + 1], j + 2
                ln, j = _vlq(data, j)
                body = data[j:j + ln]
                j += ln
                if kind == 0x51:
                    tempos.append((t, int.from_bytes(body, "big")))
                elif kind == 0x03:
                    ev.append((t, "name", body.decode("latin-1")))
                continue
            if b in (0xF0, 0xF7):
                ln, j = _vlq(data, j + 1)
                j += ln
                continue
            if b & 0x80:
                status, j = b, j + 1
            hi, ch = status & 0xF0, status & 0x0F
            if hi in (0xC0, 0xD0):
                ev.append((t, "prog" if hi == 0xC0 else "x", (ch, data[j])))
                j += 1
            else:
                a, c = data[j], data[j + 1]
                j += 2
                if hi == 0x90 and c > 0:
                    ev.append((t, "on", (ch, a, c)))
                elif hi in (0x80, 0x90):
                    ev.append((t, "off", (ch, a)))
                elif hi == 0xB0 and a in (120, 123):
                    ev.append((t, "alloff", (ch,)))
        ev.append((t, "end", None))
        raw.append(ev)
    tempos.sort()
    progs = sorted((t, a[0], a[1]) for ev in raw for t, kind, a in ev if kind == "prog")

    def program(ch, tick):
        """The program in force on channel ch at tick (else its first one, else 0)."""
        cur = None
        for t, c, p in progs:
            if c != ch:
                continue
            if t <= tick or cur is None:
                cur = p
            if t > tick:
                break
        return cur or 0

    def seconds(tick):
        s, last, tempo = 0.0, 0, 500000
        for at, tp in tempos:
            if at >= tick:
                break
            s += (at - last) * tempo / 1e6 / div
            last, tempo = at, tp
        return s + (tick - last) * tempo / 1e6 / div

    tracks, length = [], 0.0
    for ev in raw:
        name, on, notes = "", {}, {}                         # notes: channel -> list

        def close(key, t):
            s0, v0 = on.pop(key)                         # zero-length notes stay (1 tick later)
            notes.setdefault(key[0], []).append((s0, t, key[1], v0))
        for t, kind, a in ev:
            if kind == "name":
                name = a
            elif kind == "on":
                if (a[0], a[1]) in on:                        # retriggered: close the old one
                    close((a[0], a[1]), t)
                on[(a[0], a[1])] = (t, a[2])
            elif kind == "off" and (a[0], a[1]) in on:
                close((a[0], a[1]), t)
            elif kind == "alloff":
                for key in [k for k in on if k[0] == a[0]]:
                    close(key, t)
            elif kind == "end":
                for key in list(on):
                    close(key, t)
        for ch in sorted(notes):
            ns = sorted(notes[ch])
            length = max(length, seconds(max(n[1] for n in ns)))
            tracks.append(dict(
                name=name if len(notes) == 1 else f"{name} ch{ch + 1}".strip(),
                program=program(ch, ns[0][0]), channel=ch,
                notes=[(seconds(s), seconds(e), p, v) for s, e, p, v in ns]))
    return tracks, length


# ------------------------------------------------------------------ MOD file
AMIGA_CLOCK = 3546894.6            # PAL Paula clock: a sample plays at AMIGA_CLOCK / period Hz
PT_PERIODS = [856, 808, 762, 720, 678, 640, 604, 570, 538, 508, 480, 453,     # C-1..B-3, finetune 0
              428, 404, 381, 360, 339, 320, 302, 285, 269, 254, 240, 226,
              214, 202, 190, 180, 170, 160, 151, 143, 135, 127, 120, 113]
MOD_TAGS = {b"M.K.": 4, b"M!K!": 4, b"FLT4": 4, b"4CHN": 4, b"6CHN": 6, b"8CHN": 8}
NOTE_NAMES = "C C# D D# E F F# G G# A A# B".split()


def mod_channels(data):
    """The channel count of a 31-sample MOD (its tag at 1080), None if it is not one."""
    tag = bytes(data[1080:1084])
    if tag in MOD_TAGS:
        return MOD_TAGS[tag]
    if tag[2:] == b"CH" and tag[:2].isdigit() and 1 <= int(tag[:2]) <= 32:
        return int(tag[:2])
    return None


def _mod_parse(data):
    """-> (samples, order, patterns, channels). samples[1..31]: dict(name,
    data, vol, ft (finetune -8..7), loop ((start, length) in bytes, or None
    for a one-shot)); a pattern cell: (sample, period, effect, parameter)."""
    nch = mod_channels(data)
    npat = max(data[952:1080]) + 1
    at = 1084 + npat * 256 * nch
    smps = [None]
    for i in range(31):
        o = 20 + 30 * i
        ln, ft, vol, ls, ll = struct.unpack(">HBBHH", data[o + 22:o + 30])
        body = data[at:at + 2 * ln]
        at += 2 * ln
        loop = (2 * ls, min(2 * ll, len(body) - 2 * ls)) if ll > 1 and 2 * ls < len(body) else None
        ft &= 15
        smps.append(dict(name=data[o:o + 22].split(b"\0")[0].decode("latin-1").strip(), data=body,
                         vol=min(64, vol), ft=ft - 16 if ft > 7 else ft, loop=loop))

    def cell(p, r, c):
        b = (data[1084 + ((p * 64 + r) * nch + c) * 4:][:4] + bytes(4))[:4]     # a truncated file: silence
        return (b[0] & 0xF0) | (b[2] >> 4), ((b[0] & 15) << 8) | b[1], b[2] & 15, b[3]
    pats = [[[cell(p, r, c) for c in range(nch)] for r in range(64)] for p in range(npat)]
    return smps, list(data[952:952 + data[950]]), pats, nch


def mod_period(per, ft):
    """The period a pattern note plays at: its note (the nearest one of the
    ProTracker table, which some trackers store already finetuned) with the
    sample's finetune (1/8 semitone steps), like ProTracker and OpenMPT."""
    n = round(12 * math.log2(856 / per))
    return (PT_PERIODS[n] if 0 <= n < 36 else 856 * 2 ** (-n / 12)) * 2 ** (-ft / 96)


def _mod_play(smps, order, pats, nch, max_s=1800.0):
    """Plays the song like ProTracker (PAL, tempo on the CIA timer: a tick
    lasts 2.5 / BPM s), tick by tick. Returns (segments, length_s). A segment
    is a stretch of one channel where one sample sounds (volume above 0) at
    one period: dict(ch, smp, start, end, per, arp, trig, pos, vols). trig:
    it starts with a (re)start of the sample, not with a slide that moved the
    period or a volume that came back from 0; arp: the arpeggio (x, y) on the
    period; pos: where in the sample it starts; vols: [(t, volume)] per tick.
    The song ends at the end of the order list, at F00 or at a position jump
    backwards (where it would loop)."""
    speed, bpm, t = 6, 125, 0.0
    oi, row = 0, 0
    chans = [dict(smp=0, sel=0, vol=0, ft=0, per=0.0, now=0.0, target=0.0, pspeed=0, offset=0, on=False,
                  pos=0.0, delayed=None, loop_row=0, loop_n=0, seg=None) for _ in range(nch)]
    segs = []

    def close(cs, at):
        if cs["seg"]:
            cs["seg"]["end"] = at
            segs.append(cs["seg"])
            cs["seg"] = None

    while 0 <= oi < len(order) and t < max_s:
        cells = pats[order[oi]][row]
        jump = brk = loop_to = None
        pdelay = 0
        for _, _, e, x in cells:                        # song-wide effects of the row
            if e == 0xF:
                if x == 0:
                    jump = -1                           # F00: stop
                elif x < 0x20:
                    speed = x
                else:
                    bpm = x
            elif e == 0xB:
                jump = x
            elif e == 0xD:
                brk = (x >> 4) * 10 + (x & 15)
            elif e == 0xE and x >> 4 == 0xE and not pdelay:
                pdelay = x & 15
        dt = 2.5 / bpm
        for k in range(speed * (1 + pdelay)):
            tr = k % speed                              # tick in the row (restarts on its repeats)
            for c, (s, per, e, x) in enumerate(cells):
                cs = chans[c]
                hi, lo = x >> 4, x & 15
                trig = None                             # period to (re)start the sample at
                if k == 0:
                    if s:                               # sample number: its volume and finetune
                        cs["sel"], cs["vol"], cs["ft"] = s, smps[s]["vol"], smps[s]["ft"]
                    if e == 0xE and hi == 5:
                        cs["ft"] = lo - 16 if lo > 7 else lo
                    if per:
                        p = mod_period(per, cs["ft"])
                        if e in (3, 5):
                            cs["target"] = p            # tone portamento: no new note
                        elif e == 0xE and hi == 0xD and lo:
                            cs["delayed"] = p           # note delay
                        else:
                            trig = p
                    if e == 3 and x:
                        cs["pspeed"] = x
                    if e == 9 and x:
                        cs["offset"] = x * 256
                    if e == 0xC:
                        cs["vol"] = min(64, x)
                    if e == 0xE:
                        if hi == 1:
                            cs["per"] = max(113, cs["per"] - lo)
                        elif hi == 2:
                            cs["per"] = min(856, cs["per"] + lo)
                        elif hi == 0xA:
                            cs["vol"] = min(64, cs["vol"] + lo)
                        elif hi == 0xB:
                            cs["vol"] = max(0, cs["vol"] - lo)
                        elif hi == 6:                   # pattern loop
                            if lo == 0:
                                cs["loop_row"] = row
                            elif cs["loop_n"] == 0:
                                cs["loop_n"], loop_to = lo, cs["loop_row"]
                            else:
                                cs["loop_n"] -= 1
                                if cs["loop_n"]:
                                    loop_to = cs["loop_row"]
                else:
                    if e == 0xE and hi == 0xD and k == lo and cs["delayed"]:
                        trig, cs["delayed"] = cs["delayed"], None
                    if tr or pdelay:                    # per-tick effects (and on the repeats' first tick)
                        if e in (0xA, 5, 6):
                            cs["vol"] = min(64, cs["vol"] + hi) if hi else max(0, cs["vol"] - lo)
                        if e == 1:
                            cs["per"] = max(113, cs["per"] - x)
                        elif e == 2:
                            cs["per"] = min(856, cs["per"] + x)
                        elif e in (3, 5) and cs["target"] and cs["per"]:
                            d = cs["target"] - cs["per"]
                            cs["per"] += max(-cs["pspeed"], min(cs["pspeed"], d))
                if e == 0xE and hi == 0xC and tr == lo:
                    cs["vol"] = 0                       # note cut
                if e == 0xE and hi == 9 and lo and tr % lo == 0 and not (tr == 0 and per):
                    trig = cs["per"]                    # retrigger
                if trig:                                # (re)start the sample
                    smp = smps[cs["sel"]]
                    cs["smp"], cs["per"] = cs["sel"], trig
                    pos = cs["offset"] if e == 9 else 0
                    if pos >= len(smp["data"]):         # offset past the end: the loop, or nothing
                        pos = smp["loop"][0] if smp["loop"] else 0
                        cs["on"] = smp["loop"] is not None
                    else:
                        cs["on"] = len(smp["data"]) > 2
                    cs["pos"] = pos
                arp = (hi, lo) if e == 0 and x else None
                cs["now"] = cs["per"] * 2 ** (-(0, hi, lo)[tr % 3] / 12) if arp else cs["per"]
                sounding = cs["on"] and cs["vol"] > 0 and cs["per"] > 0
                sg = cs["seg"]
                if sg and (trig or not sounding or (sg["smp"], sg["per"], sg["arp"]) != (cs["smp"], cs["per"], arp)):
                    close(cs, t)
                if sounding and not cs["seg"]:
                    cs["seg"] = dict(ch=c, smp=cs["smp"], per=cs["per"], arp=arp, trig=bool(trig), start=t,
                                     pos=cs["pos"], vols=[])
                if cs["seg"]:
                    cs["seg"]["vols"].append((t, cs["vol"]))
            for cs in chans:                            # the samples play on: one-shots run out
                if cs["on"] and cs["now"] > 0:
                    smp = smps[cs["smp"]]
                    rate = AMIGA_CLOCK / cs["now"]
                    if smp["loop"] is None and cs["pos"] + rate * dt >= len(smp["data"]):
                        cs["on"] = False
                        close(cs, t + (len(smp["data"]) - cs["pos"]) / rate)
                    cs["pos"] += rate * dt
            t += dt
        if jump == -1:
            break
        if jump is not None or brk is not None:
            if jump is not None:
                if jump <= oi:
                    break                               # jumps back: the song end
                oi = jump
            else:
                oi += 1
            row = brk if brk is not None and brk < 64 else 0
        elif loop_to is not None:
            row = loop_to
        else:
            row += 1
            if row == 64:
                row, oi = 0, oi + 1
    for cs in chans:
        close(cs, t)
    return segs, t


def _cmndf(x, maxlag):
    """YIN's cumulative mean normalized difference of x, lags 0..maxlag-1."""
    import numpy as np
    w = len(x) - maxlag
    n = 1 << (len(x) + w).bit_length()
    r = np.fft.irfft(np.fft.rfft(x, n) * np.conj(np.fft.rfft(x[:w], n)), n)[:maxlag]
    e = np.concatenate([[0.0], np.cumsum(x * x)])
    lag = np.arange(maxlag)
    d = e[w] + e[lag + w] - e[lag] - 2 * r
    out = np.ones(maxlag)
    out[1:] = d[1:] * lag[1:] / np.maximum(np.cumsum(d[1:]), 1e-9)
    return out


def _yin(x, maxlag, thr=0.2):
    """(period in samples, dip): the first local minimum of the CMNDF under
    thr, else its lowest point, refined by a parabola."""
    d = _cmndf(x, maxlag)
    k = next((k for k in range(2, maxlag - 1) if d[k] < thr and d[k] <= d[k - 1] and d[k] <= d[k + 1]), None)
    if k is None:
        k = 2 + int(d[2:maxlag - 1].argmin())
    a, b, c = d[k - 1], d[k], d[k + 1]
    den = a - 2 * b + c
    return k + (0.5 * (a - c) / den if den > 0 else 0.0), float(b)


REF_RATE = AMIGA_CLOCK / 214       # C-3, where the spectral features are measured (Hz)


def mod_sound(s):
    """What a sample sounds like, from its data (as if played at C-3), from
    its loudest point on, a looped sample with its loop repeated after it.
    The pitch: YIN over three windows, of the sample and of its loop. One
    period in every window with a clean dip (a single note): that
    fundamental (the loop's first: it is what sustains), an octave up while
    the loop nearly repeats at half the period (its odd partials hold under
    7.5 % of the energy), refined on its harmonic peaks. Otherwise (an
    ensemble, a chord, a noisy or inharmonic note): the root (the YIN pitch
    class if all windows agree, else the root of the triad that fits the
    pitch classes best) at the lowest partial of that class that is really
    there (35 % of the loudest spectral peak). No spectral peaks or no
    period above 40 Hz: unpitched. Returns dict(cps (fundamental in cycles
    per sample, 0 = unpitched), how ('note', 'chord' or 'noise'), tuned (the
    cps comes from a measured period), bright (spectral centroid over the
    fundamental), centroid (Hz), decay (dB from the loudest point to the end
    of a one-shot), secs (length at C-3), pitch_why (how the pitch was
    found, for mod_table))."""
    import numpy as np
    x = np.frombuffer(s["data"], dtype=np.int8).astype(float)
    if len(x) < 512:
        return None
    env = np.sqrt(np.convolve(x * x, np.ones(256) / 256, "valid"))
    pk = max(0, min(int(env.argmax()), len(x) - 4096))
    raw = x[pk:pk + 16384]
    if s["loop"]:
        ls, ll = s["loop"]
        steady = np.tile(x[ls:ls + ll], 16384 // ll + 1)[:16384]      # what sustains
        whole = np.concatenate([x[min(pk, ls):ls + ll], steady])[:16384]
        raw = x[min(pk, ls):ls + ll][:16384]
        decay = 0.0
    else:
        steady = whole = raw
        decay = 20 * math.log10(env.max() / max(1.0, float(env[-min(len(env), 1024):].mean())))

    def periods(y):                                 # YIN over three windows: (periods, median dip)
        maxlag = min(2048, len(y) // 3)
        win = min(4096, len(y) - maxlag)
        res = [_yin(y[a:a + win + maxlag], maxlag) for a in np.linspace(0, len(y) - win - maxlag, 3).astype(int)]
        return [r[0] for r in res], float(np.median([r[1] for r in res]))
    taus, dip = periods(raw) if len(raw) >= 3072 else periods(whole)
    ltaus, ldip = periods(steady) if s["loop"] else (taus, dip)
    # averaged spectrum
    n = min(8192, 1 << (len(whole).bit_length() - 1))
    spec = np.zeros(n // 2 + 1)
    for a in range(0, len(whole) - n + 1, n // 4):
        spec += np.abs(np.fft.rfft(whole[a:a + n] * np.hanning(n)))
    freq = np.arange(len(spec)) / n                 # cycles per sample
    band = (freq * REF_RATE > 40) & (freq * REF_RATE < 5000)
    pw = spec ** 2
    local = np.array([np.median(spec[max(0, k - 40):k + 41]) for k in range(len(spec))])
    peak = np.zeros(len(spec), bool)
    for k in range(3, len(spec) - 3):
        if spec[k] == spec[k - 3:k + 4].max() and spec[k] > 4 * local[k]:
            peak[k - 2:k + 3] = True
    peaky = float(pw[peak & band].sum() / max(1e-9, pw[band].sum()))
    centroid = float((pw[band] * freq[band]).sum() / max(1e-9, pw[band].sum()))
    chroma = np.zeros(12)
    for k in np.nonzero(band & peak)[0]:
        if freq[k] > 0:
            chroma[round(12 * math.log2(freq[k] * REF_RATE / 261.6256)) % 12] += pw[k]
    chroma /= max(1e-9, chroma.max())

    def amp(f, tol=0.015):                          # spectral peak near f cycles/sample
        m = (freq > f * (1 - tol)) & (freq < f * (1 + tol))
        return float(spec[m].max()) if m.any() else 0.0

    def corr(lag):                                  # correlation of what sustains with itself, lag later
        m = len(steady) - int(lag) - 2
        later = np.interp(np.arange(m) + lag, np.arange(len(steady)), steady)
        return float(np.corrcoef(steady[:m], later)[0, 1])

    def clean(ts, d):                               # one period in every window, a clean dip
        return d < 0.3 and max(ts) / min(ts) < 1.03

    def pclass(t):
        return round(12 * math.log2(REF_RATE / t / 261.6256)) % 12
    top = float(spec[band].max())
    tuned, diag = True, []
    if peaky < 0.35 or min(float(np.median(taus)), float(np.median(ltaus))) * 40 > REF_RATE:
        how, cps, tuned = "noise", 0.0, False       # no peaks, or no period above 40 Hz at C-3
        why = f"{peaky * 100:.0f} % of the energy in peaks, period {np.median(taus):.0f} samples"
    elif clean(ltaus, ldip) or clean(taus, dip):
        how = "note"                                # the loop's period first: what sustains
        loop_first = clean(ltaus, ldip)
        cps = 1 / float(np.median(ltaus if loop_first else taus))
        why = f"{'loop' if loop_first else 'sample'} period {1 / cps:.1f} (dip {ldip if loop_first else dip:.2f})"
        for _ in range(3):
            diag.append(round(corr(0.5 / cps), 2))
            if diag[-1] < 0.85:
                break
            cps *= 2
        why += ", half-period correlation " + " ".join(f"{c:.2f}" for c in diag)
        # finer: from the harmonic peaks of what sustains, the loop played once
        # (repeated, its seams would put every peak on a multiple of 1 / loop
        # length), else the sample as stored
        once = x[s["loop"][0]:sum(s["loop"])] if s["loop"] and s["loop"][1] >= 1024 else raw
        if len(once) >= 1024:
            rs = np.abs(np.fft.rfft(once * np.hanning(len(once)), 1 << 16))
            rtop = float(rs[int(40 / REF_RATE * 65536):].max())
            num = den = 0.0
            for k in range(1, 9):
                j0, j1 = int(k * cps * 0.985 * 65536), int(k * cps * 1.015 * 65536) + 1
                if j1 >= len(rs) - 1:
                    break
                j = j0 + int(rs[j0:j1].argmax())
                if j0 < j < j1 - 1 and rs[j] >= 0.2 * rtop:
                    a, b, c = np.log(rs[j - 1:j + 2] + 1e-9)
                    num += rs[j] * (j + (0.5 * (a - c) / (a - 2 * b + c) if a - 2 * b + c < 0 else 0)) / 65536
                    den += rs[j] * k
            if den:
                cps = num / den
    else:
        how = "chord"
        root = next((1 / float(np.median(ts)) for ts in (taus, ltaus) if len({pclass(t) for t in ts}) == 1), None)
        if root is None:                            # the root of the major or minor triad the chroma fits best
            fit = [max(chroma[r] + chroma[(r + 3) % 12], chroma[r] + chroma[(r + 4) % 12]) + chroma[(r + 7) % 12]
                   for r in range(12)]
            root, tuned = 261.6256 * 2 ** (int(np.argmax(fit)) / 12) / REF_RATE, False
        cps = root / 2 ** math.ceil(math.log2(root * REF_RATE / 40))     # to 40 Hz or below
        while cps < 0.25:
            diag.append(round(amp(cps, 0.045) / top, 2))                 # 3/4 semitone: inharmonic partials
            if diag[-1] >= 0.35:
                break
            cps *= 2
        why = (f"root {NOTE_NAMES[pclass(1 / root)]} "
               f"({'YIN, dip %.2f' % dip if tuned else 'triad of the pitch classes'}), "
               f"its partials from 40 Hz up " + " ".join(f"{a:.2f}" for a in diag))
    return dict(cps=cps, how=how, tuned=tuned, bright=centroid / cps if cps else 0.0, centroid=centroid * REF_RATE,
                decay=decay, secs=len(x) / REF_RATE, pitch_why=why)


def mod_instrument(snd, looped, median, rate):
    """(GM program, drum key or None, why) for a sample: snd from mod_sound,
    median: its median pitch as played, rate: its median playback rate (Hz).
    Unpitched: a looped one is a roll, so a snare; a one-shot: crash cymbal
    if long, else kick, hi-hat or snare by its spectral centroid at that rate.
    A pitched one-shot that dies away (6 dB or more, within 1.5 s): timpani
    below the middle of the staff, else mallets. A nearly pure tone (spectral
    centroid under twice the fundamental): flute. An ensemble or a chord:
    strings, or brass when bright (centroid over 8 times its root). Any other
    single note: brass (trumpet, horn or trombone by register)."""
    secs = snd["secs"] * REF_RATE / rate
    if snd["how"] == "noise":
        c = snd["centroid"] * rate / REF_RATE
        if looped:
            return 0, 38, f"noise, looped (rolls), centroid {c:.0f} Hz: snare"
        if secs >= 1.0:
            return 0, 49, f"noise, {secs:.1f} s: crash cymbal"
        if c < 250:
            return 0, 36, f"noise, centroid {c:.0f} Hz: kick"
        if c >= 6000:
            return 0, 42, f"noise, centroid {c:.0f} Hz: hi-hat"
        return 0, 38, f"noise, centroid {c:.0f} Hz: snare"
    if not looped and snd["decay"] >= 6 and secs <= 1.5:
        if median < 55:
            return 47, None, f"one-shot, dies away {snd['decay']:.0f} dB in {secs:.1f} s, low: timpani"
        return (9 if median >= 72 else 46), None, f"one-shot, dies away {snd['decay']:.0f} dB: mallets"
    if snd["bright"] < 2:
        return 73, None, f"nearly pure (centroid {snd['bright']:.1f} x f0): flute"
    if snd["how"] == "chord":
        if snd["bright"] >= 8:
            return 61, None, f"chord / ensemble, bright ({snd['bright']:.1f} x root): brass section"
        return 48, None, f"chord / ensemble ({snd['bright']:.1f} x root): strings"
    prog = 56 if median >= 58 else 57 if median < 50 else 60
    name = {56: "trumpet", 57: "trombone", 60: "horn"}[prog]
    return prog, None, f"single note, centroid {snd['bright']:.1f} x f0: {name}"


MOD_HEAR_S = 1.0                   # a note's velocity: its loudest point in its first second


def read_mod(path):
    """Returns (tracks, length_s) like read_midi, for a ProTracker MOD: one
    track per sample played, dict(name, program, channel, notes=[(start_s,
    end_s, pitch, velocity)], mod=dict(the analysis, for mod_table)).

    The song is played like ProTracker (_mod_play). A note starts where the
    sample is (re)started, or where it comes back from volume 0; it ends at
    the next note of its channel, a note cut, volume 0, or where a one-shot
    runs out at the rate played (PAL clock / period). The pitch: the sample's
    fundamental (mod_sound) at that rate, on the equal-tempered grid moved by
    the song's own tuning (the mean offset of all its notes, so the chips play
    in tune with the MOD). Slides (1xx, 2xx, 3xx, 5xy) are followed on that
    grid: each semitone reached is a new note, steps shorter than a 60 Hz
    tick are skipped (a fast portamento is one change of note). Arpeggios
    (0xy) keep the base note, plus chord notes for intervals of 3 semitones
    or more (a chord, not an ornament). Velocity: the loudest channel volume
    in the note's first second (the player's dynamics, like a MIDI velocity),
    3 dB per 14 steps, one PSG level. Drums: channel 9, the pitch is the GM
    drum key."""
    data = open(path, "rb").read()
    smps, order, pats, nch = _mod_parse(data)
    segs, length = _mod_play(smps, order, pats, nch)
    segs.sort(key=lambda g: (g["start"], g["ch"]))
    sound = {i: mod_sound(smps[i]) for i in sorted({g["smp"] for g in segs})}

    def pitch(i, per):
        return 69 + 12 * math.log2(AMIGA_CLOCK / per * sound[i]["cps"] / 440)
    tonal = [g for g in segs if sound[g["smp"]] and sound[g["smp"]]["tuned"]]
    z = sum(cmath.exp(2j * math.pi * pitch(g["smp"], g["per"])) * (g["end"] - g["start"]) for g in tonal)
    tune = round(cmath.phase(z) / (2 * math.pi), 2) if tonal else 0.0      # semitones, -0.5..0.5

    def grid(i, per):
        return round(round(pitch(i, per) - tune) + tune, 2)
    played = {i: [] for i in sound}                 # per sample: [start, end, pitch, vols, period, sample]
    cur = [None] * nch
    for g in segs:
        i = g["smp"]
        if not sound[i]:
            continue
        p = grid(i, g["per"]) if sound[i]["cps"] else 0
        n = cur[g["ch"]]
        if (n and n[5] == i and not g["trig"] and abs(n[1] - g["start"]) < 1e-6
                and (p == n[2] or g["end"] - g["start"] < 1 / TICK_HZ)):
            n[1] = g["end"]                         # the same note goes on
            n[3] += g["vols"]
        else:
            n = cur[g["ch"]] = [g["start"], g["end"], p, list(g["vols"]), g["per"], i]
            played[i].append(n)
        if g["arp"] and sound[i]["cps"]:
            for d in {v for v in g["arp"] if v >= 3}:
                played[i].append([g["start"], g["end"], round(p + d, 2), list(g["vols"]), g["per"], i])
    tracks = []
    for i, ns in played.items():
        if not ns:
            continue
        snd, smp = sound[i], smps[i]
        pers = sorted(n[4] for n in ns)
        rate = AMIGA_CLOCK / pers[len(pers) // 2]
        ps = sorted(n[2] for n in ns)
        prog, key, why = mod_instrument(snd, smp["loop"] is not None, ps[len(ps) // 2], rate)
        notes = {}
        for s, e, p, vols, _, _ in ns:
            v = max(v for t, v in vols if t < s + MOD_HEAR_S) / 64
            vel = max(1, min(127, round(127 + 14 / 3 * 20 * math.log10(max(v, 1e-3)))))
            k = (s, key if key is not None else p)
            old = notes.get(k)                      # doubled on two channels: one note
            notes[k] = (s, max(e, old[1]), k[1], max(vel, old[3])) if old else (s, e, k[1], vel)
        c3 = 69 + 12 * math.log2(REF_RATE * 2 ** (smp["ft"] / 96) * snd["cps"] / 440) if snd["cps"] else None
        tracks.append(dict(
            name=f"{i:02d} {smp['name']}".strip(), program=prog, channel=9 if key is not None else 0,
            notes=sorted(notes.values()),
            mod=dict(sample=i, bytes=len(smp["data"]), loop=smp["loop"], ft=smp["ft"], vol=smp["vol"],
                     how=snd["how"], c3=c3, key=key, why=why, tune=tune, sound=snd)))
    return tracks, length


def note_name(p):
    return f"{NOTE_NAMES[round(p) % 12]}{round(p) // 12 - 1}"


def mod_table(tracks):
    """The samples of a MOD as read_mod saw them, for a human to check
    (after classify): per sample a line of numbers, then how its pitch and
    its instrument were chosen."""
    lines = [f"tuning of the song: {tracks[0]['mod']['tune'] * 100:+.0f} cents" if tracks else "",
             " smp  bytes  loop  vol  ft  heard at C-3  sound  family  prog/key  played     median  notes"]
    for tr in sorted(tracks, key=lambda t: t["mod"]["sample"]):
        m = tr["mod"]
        c3 = "-" if m["c3"] is None else f"{note_name(m['c3'])} {(m['c3'] - round(m['c3'])) * 100:+.0f}c"
        ps = [n[2] for n in tr["notes"]]
        rng = f"key {m['key']}" if m["key"] is not None else f"{note_name(min(ps))}-{note_name(max(ps))}"
        lines.append(f"{m['sample']:4d} {m['bytes']:6d}  {'yes' if m['loop'] else 'no':4s} {m['vol']:3d} "
                     f"{m['ft']:+3d}  {c3:12s}  {m['how']:5s}  {tr['family']:7s} "
                     f"{m['key'] if m['key'] is not None else tr['program']:4d}      {rng:10s} "
                     f"{tr['median']:6.1f} {len(tr['notes']):6d}")
        lines.append(f"{'':6s}pitch: {m['sound']['pitch_why']}; {m['why']}")
    return "\n".join(lines)


def read_music(path):
    """read_midi or read_mod, by the file's content (MThd, or a MOD tag at 1080)."""
    head = open(path, "rb").read(1084)
    if head[:4] == b"MThd":
        return read_midi(path)
    if len(head) == 1084 and mod_channels(head):
        return read_mod(path)
    raise ValueError(f"{path}: neither a Standard MIDI File nor a ProTracker MOD")


# ------------------------------------------------------------ classification
def classify(tracks):
    """Adds 'family' and 'median' to each track."""
    for tr in tracks:
        ps = sorted(n[2] for n in tr["notes"])
        tr["median"] = ps[len(ps) // 2]
        p = tr["program"]
        if tr["channel"] == 9:
            fam = "drums"
        elif p == 47:
            fam = "timpani"
        elif 8 <= p <= 15 or p == 46:
            fam = "mallet"                     # glockenspiel, vibraphone, harp
        elif 56 <= p <= 63:
            fam = "brass"
        elif 64 <= p <= 79:
            fam = "reed"
        elif 32 <= p <= 55:
            fam = "strings"
        else:
            fam = "other"
        tr["family"] = fam
    return tracks


def ordered(tracks, fam, key):
    return sorted((t for t in tracks if t["family"] == fam), key=key)


def roles(tracks):
    """Track lists per role, best first. Tracks of no orchestral family
    (piano, organ, guitar, synths...) take the strings' roles."""
    brass = ordered(tracks, "brass", lambda t: -t["median"])
    strings = sorted((t for t in tracks if t["family"] in ("strings", "other")), key=lambda t: -t["median"])
    reed = ordered(tracks, "reed", lambda t: -t["median"])
    low = sorted((t for t in tracks if t["family"] in ("brass", "strings", "reed", "other") and t["median"] < 50),
                 key=lambda t: t["median"])
    lead = ([t for t in brass if t["median"] >= 58] + [t for t in strings if t["median"] >= 70]
            + [t for t in reed if t["median"] >= 65])
    harmony = ([t for t in brass if 48 <= t["median"] < 70 and t not in lead[:1]]
               + [t for t in strings if 55 <= t["median"] < 76] + [t for t in reed if 55 <= t["median"] < 76])
    return dict(
        lead=lead, bass=low, harmony=harmony,
        strings_hi=[t for t in strings if t["median"] >= 62],
        strings_mid=[t for t in strings if 50 <= t["median"] < 76] + [t for t in reed if t["median"] < 70],
        sparkle=ordered(tracks, "mallet", lambda t: -t["median"]) + [t for t in reed if t["median"] >= 75],
        timpani=[t for t in tracks if t["family"] == "timpani"],
        drums=[t for t in tracks if t["family"] == "drums"],
    )


# ----------------------------------------------------------------- note grid
class Grid:
    """Notes of every track on the 60 Hz tick grid. In the strings, very short
    repeated notes of the same pitch (tremolo) merge into one sustained note;
    elsewhere a repeated note ends one tick early, so it is heard (and an FM
    channel gets a key off before the new key on)."""

    def __init__(self, tracks, nticks):
        self.n = nticks
        self.on = {}        # id(track) -> per tick: {pitch: velocity}
        self.onset = {}     # id(track) -> per tick: set of pitches starting
        for tr in tracks:
            if tr["family"] == "drums":
                continue
            merged = []
            last = {}
            tremolo = tr["family"] == "strings"
            for s, e, p, v in tr["notes"]:
                a, b = round(s * TICK_HZ), max(round(s * TICK_HZ) + 1, round(e * TICK_HZ))
                if a >= nticks:
                    continue
                prev = last.get(p)                   # [start, end, in_tremolo, pitch, vel]
                if (tremolo and prev is not None and a - prev[1] <= 1 and b - a <= 5
                        and (prev[2] or prev[1] - prev[0] <= 5)):
                    prev[1] = b                      # tremolo: extend, no new attack
                    prev[2] = True
                    continue
                if prev is not None and prev[1] >= a:
                    prev[1] = max(prev[0] + 1, a - 1)   # repeated note: 1 tick gap
                n = [a, b, False, p, v]
                merged.append(n)
                last[p] = n
            merged = [(a, b, p, v) for a, b, _, p, v in merged]
            on = [dict() for _ in range(nticks)]
            onset = [set() for _ in range(nticks)]
            for a, b, p, v in merged:
                onset[a].add(p)
                for t in range(a, min(b, nticks)):
                    on[t][p] = max(v, on[t].get(p, 0))
            self.on[id(tr)] = on
            self.onset[id(tr)] = onset

    def notes(self, tr, t):
        return self.on[id(tr)][t]

    def attack(self, tr, t, p):
        return p in self.onset[id(tr)][t]


def pick_voice(grid, cands, nticks, choose="high", hold=24, avoid=None):
    """One monophonic voice: per tick (track, pitch, velocity, attack) or None.
    Follows one track: it stays on it through rests of up to `hold` ticks
    (silent), moves to the first candidate that plays after a longer rest, and
    goes back to a higher-priority candidate as soon as that one attacks."""
    out, cur, rest = [], None, 0
    rank = {id(t): i for i, t in enumerate(cands)}
    for t in range(nticks):
        taken = avoid[t] if avoid else set()

        def plays(tr):
            return any(p not in taken for p in grid.notes(tr, t))
        if cur is not None:
            if plays(cur):
                rest = 0
            else:
                rest += 1
                if rest > hold:
                    cur = None
        for tr in cands:
            if cur is not None and rank[id(tr)] >= rank[id(cur)]:
                break
            if plays(tr) and (cur is None or any(grid.attack(tr, t, p) for p in grid.notes(tr, t))):
                cur, rest = tr, 0
                break
        if cur is None or not plays(cur):
            out.append(None)
            continue
        ns = {p: v for p, v in grid.notes(cur, t).items() if p not in taken}
        if not ns:
            out.append(None)
            continue
        p = max(ns) if choose == "high" else min(ns)
        prev = out[-1] if out else None
        att = grid.attack(cur, t, p) or prev is None or prev[1] != p
        out.append((cur, p, ns[p], att))
    return out


def drum_hits(tracks, nticks):
    """Per tick: 'snare' / 'cymbal' / None from the percussion tracks."""
    hits = [None] * nticks
    for tr in tracks:
        if tr["family"] != "drums":
            continue
        for s, e, p, v in tr["notes"]:
            t = round(s * TICK_HZ)
            if t < nticks:
                kind = "snare" if p in (37, 38, 39, 40) else "cymbal" if p in (49, 51, 52, 55, 57, 59) else None
                if kind and hits[t] != "snare":
                    hits[t] = kind
    return hits


# ------------------------------------------------------------- envelopes
# level (0..15) over time: attack ramp, decay to sustain, release
ENV = {
    "brass":   dict(attack=[-2, 0], decay=[(6, -1)], release=[-3, -6, -10]),
    "strings": dict(attack=[-5, -3, -1, 0], decay=[], release=[-2, -4, -7, -10]),
    "reed":    dict(attack=[-2, 0], decay=[], release=[-4, -8]),
    "bass":    dict(attack=[0], decay=[(8, -1)], release=[-4, -9]),
    "pluck":   dict(attack=[0], decay=[(2, -1)] * 14, release=[-3, -6]),
    "timpani": dict(attack=[0], decay=[(3, -1)] * 14, release=[-2, -4, -6]),
}


def envelope_levels(voice, env, base, nticks, fade):
    """voice: pick_voice() output. Returns per tick (pitch, level) or None."""
    e = ENV[env]
    out = []
    age, last_p, last_lvl, rel = 0, None, 0, None
    for t in range(nticks):
        v = voice[t]
        if v is not None:
            tr, p, vel, att = v
            if att:
                age = 0
            peak = base + round((vel - 127) / 14)
            if age < len(e["attack"]):
                lvl = peak + e["attack"][age]
            else:
                lvl, k = peak, age - len(e["attack"])
                for n, d in e["decay"]:
                    if k >= n:
                        lvl += d
                        k -= n
                    else:
                        break
            age += 1
            last_p, last_lvl, rel = p, lvl, 0
        elif last_p is not None and rel is not None and rel < len(e["release"]):
            lvl = last_lvl + e["release"][rel]
            rel += 1
            p = last_p
        else:
            out.append(None)
            continue
        lvl = max(0, min(15, round(lvl * fade[t])))
        out.append((p, lvl) if lvl > 0 else None)
    return out


def fade_curve(nticks, fade_ticks):
    return [1.0 if t < nticks - fade_ticks else max(0.0, (nticks - t) / fade_ticks) for t in range(nticks)]


# ---------------------------------------------------------------- chips
def midi_hz(p):
    return 440.0 * 2 ** ((p - 69) / 12)


def psg_period(p):
    return max(1, min(4095, round(PSG_CLOCK / (16 * midi_hz(p)))))


def scc_period(p):
    return max(0, min(4095, round(SCC_CLOCK / (32 * midi_hz(p))) - 1))


def opl_freq(p):
    f = midi_hz(p)
    for block in range(8):
        fnum = round(f * (1 << (20 - block)) / OPL_RATE)
        if fnum < 1024:
            return fnum, block
    return 1023, 7


def wave(kind):
    """32 signed samples (SCC)."""
    out = []
    for i in range(32):
        x = i / 32
        if kind == "brass":        # bright: saw with a softened edge
            v = sum(math.sin(2 * math.pi * k * x) / k for k in range(1, 9)) * 0.62
        elif kind == "horn":       # rounder brass
            v = (math.sin(2 * math.pi * x) + 0.45 * math.sin(4 * math.pi * x)
                 + 0.25 * math.sin(6 * math.pi * x) + 0.12 * math.sin(8 * math.pi * x)) * 0.62
        elif kind == "strings":    # saw, fewer highs
            v = sum(math.sin(2 * math.pi * k * x) / k ** 1.3 for k in range(1, 7)) * 0.66
        else:                      # flute: nearly sine
            v = math.sin(2 * math.pi * x) + 0.1 * math.sin(4 * math.pi * x)
        out.append(max(-128, min(127, round(v * 110))) & 0xFF)
    return out


class Writer:
    """Per tick register writes -> ops, keeping only changes."""

    def __init__(self, nticks):
        self.ticks = [[] for _ in range(nticks)]
        self.psg = {}
        self.scc = {}

    def psg_w(self, t, r, v):
        if self.psg.get(r) != v:
            self.psg[r] = v
            self.ticks[t].append(bytes([r, v]))

    def scc_w(self, t, r, v):
        if self.scc.get(r) != v:
            self.scc[r] = v
            self.ticks[t].append(bytes([0x10 + r, v]))

    def raw(self, t, b):
        self.ticks[t].append(bytes(b))


PSG_MIX_BASE = 0x80                         # R#7 bit7 = 1 (port B out), bit6 = 0 (port A in)


def psg_part(w, chans, noise, nticks):
    """chans: 3 per-tick lists of (pitch, level) or None (A, B, C). noise:
    per tick noise level on channel C (0 = none) and period."""
    for t in range(nticks):
        mix = 0x3F
        for c in range(3):
            v = chans[c][t] if chans[c] else None
            nl = noise[t][0] if (noise and c == 2 and noise[t]) else 0
            if v is not None:
                per = psg_period(v[0])
                w.psg_w(t, 2 * c, per & 0xFF)
                w.psg_w(t, 2 * c + 1, per >> 8)
                mix &= ~(1 << c)
            lvl = v[1] if v is not None else 0
            if nl:
                w.psg_w(t, 6, noise[t][1])
                mix &= ~(8 << c)
                lvl = max(lvl, nl)
            w.psg_w(t, 8 + c, lvl)
        w.psg_w(t, 7, PSG_MIX_BASE | mix)


def noise_track(hits, nticks, fade):
    out = [None] * nticks
    shape = {"snare": ([14, 12, 10, 7, 4], 9), "cymbal": ([12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2], 2)}
    for t, h in enumerate(hits):
        if h:
            lv, per = shape[h]
            for k, l in enumerate(lv):
                if t + k < nticks and (out[t + k] is None or out[t + k][0] < l):
                    out[t + k] = (max(0, round(l * fade[t + k])), per)
    return out


# ------------------------------------------------------------------ OPL
# 2-operator patches: (modulator, carrier) each (am_vib_egt_ksr_mult, ksl_tl,
# ar_dr, sl_rr, ws), and fb_cnt. Carrier TL is set per note.
PATCH = {
    "brass":   ((0x21, 0x1A, 0x75, 0x13, 0), (0x21, 0x00, 0x86, 0x16, 0), 0x0C),
    "horn":    ((0x21, 0x1F, 0x64, 0x13, 0), (0x21, 0x00, 0x75, 0x16, 0), 0x0A),
    "bassbr":  ((0x21, 0x18, 0x75, 0x14, 0), (0x21, 0x00, 0x86, 0x16, 0), 0x0C),
    "strings": ((0x61, 0x22, 0x52, 0x13, 0), (0x61, 0x00, 0x62, 0x05, 0), 0x0A),
    "lowstr":  ((0x21, 0x1C, 0x63, 0x14, 0), (0x21, 0x00, 0x73, 0x06, 0), 0x0A),
    "flute":   ((0x61, 0x2C, 0x87, 0x14, 0), (0x61, 0x00, 0x86, 0x05, 0), 0x04),
    "reed":    ((0x22, 0x1C, 0x86, 0x14, 0), (0x21, 0x00, 0x86, 0x06, 0), 0x08),
    "timpani": ((0x01, 0x14, 0xF6, 0x55, 0), (0x01, 0x00, 0xF4, 0x55, 0), 0x08),
}
MOD_OFF = [0x00, 0x01, 0x02, 0x08, 0x09, 0x0A, 0x10, 0x11, 0x12]


def opl_patch_for(tr):
    f, m = tr["family"], tr["median"]
    if f == "timpani":
        return "timpani"
    if f == "brass":
        return "bassbr" if m < 50 else "horn" if m < 64 and tr["program"] == 60 else "brass"
    if f == "strings":
        return "lowstr" if m < 55 else "strings"
    if f == "reed":
        return "flute" if tr["program"] in (72, 73, 74, 75, 76, 77, 78, 79) else "reed"
    return "strings"


def opl_budget(tracks, total=18):
    tonal = [t for t in tracks if t["family"] in ("brass", "strings", "reed", "timpani", "other")]
    tonal.sort(key=lambda t: {"brass": 0, "strings": 1, "reed": 2, "timpani": 3}.get(t["family"], 4))
    tonal = tonal[:total]
    budget = {id(t): 1 for t in tonal}
    extra = total - len(tonal)
    brass = sorted((t for t in tonal if t["family"] == "brass" and t["median"] >= 50), key=lambda t: -t["median"])
    while extra > 0 and brass:
        for t in brass:
            if extra and budget[id(t)] < 3:
                budget[id(t)] += 1
                extra -= 1
        if all(budget[id(t)] >= 3 for t in brass):
            break
    return tonal, budget


def opl_part(w, tracks, grid, nticks, fade):
    tonal, budget = opl_budget(tracks)
    chans = []                                  # (track, patch)
    for tr in tonal:
        for _ in range(budget[id(tr)]):
            chans.append(tr)
    assert len(chans) <= 18
    # OPL3 mode, no 4-op, no rhythm; patches
    w.raw(0, [0x31, 0x05, 0x01])
    w.raw(0, [0x31, 0x04, 0x00])
    w.raw(0, [0x30, 0x08, 0x00])
    w.raw(0, [0x30, 0xBD, 0x00])
    car_tl = []
    for ch, tr in enumerate(chans):
        mod, car, fbcnt = PATCH[opl_patch_for(tr)]
        bank, idx = 0x30 + ch // 9, ch % 9
        for off, op in ((MOD_OFF[idx], mod), (MOD_OFF[idx] + 3, car)):
            for base, val in zip((0x20, 0x40, 0x60, 0x80, 0xE0), op):
                w.raw(0, [bank, base + off, val])
        w.raw(0, [bank, 0xC0 + idx, 0x30 | fbcnt])     # both speakers
        car_tl.append(car[1] & 0x3F)
    # notes: per track, its channels take its highest notes
    state = [None] * len(chans)                 # (pitch, start tick)
    base_tl = [0] * len(chans)                  # carrier TL of the sounding note, before the fade
    for t in range(nticks):
        for tr in tonal:
            mine = [c for c, x in enumerate(chans) if x is tr]
            ns = grid.notes(tr, t)
            want = sorted(ns, reverse=tr["median"] >= 50)[:len(mine)]   # bass lines: lowest notes
            # keep channels already on a wanted pitch (unless retriggered)
            keep = {state[c][0]: c for c in mine if state[c] and state[c][0] in want
                    and not grid.attack(tr, t, state[c][0])}
            free = [c for c in mine if c not in keep.values()]
            for c in free:
                if state[c]:
                    w.raw(t, [0x60 + c])        # key off (retriggers too)
                    state[c] = None
            for p in want:
                if p in keep:
                    continue
                c = free.pop(0)
                fnum, block = opl_freq(p)
                vel = ns[p]
                base_tl[c] = min(63, car_tl[c] + round((127 - vel) / 6))
                tl = min(63, base_tl[c] + round((1 - fade[t]) * 40))
                w.raw(t, [0x40 + c, fnum & 0xFF, 0x20 | (block << 2) | (fnum >> 8), tl])
                state[c] = (p, t)
        if fade[t] < 1 and t % 6 == 0:          # fade what keeps sounding, from its own level
            for c, s in enumerate(state):
                if s:
                    tl = min(63, base_tl[c] + round((1 - fade[t]) * 40))
                    bank, idx = 0x30 + c // 9, c % 9
                    w.raw(t, [bank, 0x40 + MOD_OFF[idx] + 3, tl])
    return chans


# --------------------------------------------------------------- targets
def arrange(tracks, nticks, fade_ticks=180):
    """Returns {target: per-tick list of op byte strings}."""
    classify(tracks)
    R = roles(tracks)
    grid = Grid(tracks, nticks)
    fade = fade_curve(nticks, fade_ticks)
    hits = drum_hits(tracks, nticks)
    noise = noise_track(hits, nticks, fade)
    out = {}

    lead = pick_voice(grid, R["lead"], nticks, "high")
    used = [{v[1]} if v else set() for v in lead]
    bass = pick_voice(grid, R["bass"], nticks, "low", avoid=used)
    used2 = [u | ({b[1]} if b else set()) for u, b in zip(used, bass)]
    harm = pick_voice(grid, R["harmony"], nticks, "high", avoid=used2)

    # --- PSG only
    w = Writer(nticks)
    psg_part(w, [envelope_levels(lead, "brass", 15, nticks, fade),
                 envelope_levels(bass, "bass", 13, nticks, fade),
                 envelope_levels(harm, "brass", 11, nticks, fade)], noise, nticks)
    out["psg"] = w.ticks

    # --- SCC + PSG
    w = Writer(nticks)
    for ch, kind in enumerate(["brass", "horn", "horn", "strings"]):
        w.raw(0, [0x20 + ch] + wave(kind))
    used3 = [u | ({h[1]} if h else set()) for u, h in zip(used2, harm)]
    harm2 = pick_voice(grid, [t for t in R["harmony"] if t["family"] == "brass"] or R["harmony"],
                       nticks, "low", avoid=used3)
    used4 = [u | ({h[1]} if h else set()) for u, h in zip(used3, harm2)]
    str_hi = pick_voice(grid, R["strings_hi"], nticks, "high", avoid=used4)
    used5 = [u | ({s[1]} if s else set()) for u, s in zip(used4, str_hi)]
    str_mid = pick_voice(grid, R["strings_mid"], nticks, "high", avoid=used5)
    scc_voices = [envelope_levels(lead, "brass", 15, nticks, fade),
                  envelope_levels(harm, "brass", 12, nticks, fade),
                  envelope_levels(harm2, "brass", 11, nticks, fade),
                  envelope_levels(str_hi, "strings", 11, nticks, fade),
                  envelope_levels(str_mid, "strings", 10, nticks, fade)]
    for t in range(nticks):
        for c, v in enumerate(scc_voices):
            if v[t] is not None:
                per = scc_period(v[t][0])
                w.scc_w(t, 2 * c, per & 0xFF)
                w.scc_w(t, 2 * c + 1, per >> 8)
            w.scc_w(t, 10 + c, v[t][1] if v[t] is not None else 0)
        w.scc_w(t, 15, 0x1F)
    sparkle = pick_voice(grid, R["sparkle"], nticks, "high")
    timp = pick_voice(grid, R["timpani"], nticks, "low")
    psg_part(w, [envelope_levels(bass, "bass", 13, nticks, fade),
                 envelope_levels(sparkle, "pluck", 10, nticks, fade),
                 envelope_levels(timp, "timpani", 12, nticks, fade)], noise, nticks)
    out["scc"] = w.ticks

    # --- OPL + PSG
    w = Writer(nticks)
    opl_part(w, tracks, grid, nticks, fade)
    mallets = [t for t in tracks if t["family"] == "mallet"]
    sp1 = pick_voice(grid, mallets, nticks, "high")
    sp2 = pick_voice(grid, mallets, nticks, "low", avoid=[{v[1]} if v else set() for v in sp1])
    psg_part(w, [envelope_levels(sp1, "pluck", 11, nticks, fade),
                 envelope_levels(sp2, "pluck", 9, nticks, fade),
                 [None] * nticks], noise, nticks)
    out["opl"] = w.ticks
    return out


def encode(ticks):
    """Per-tick ops -> list of atomic byte strings (a wait closes each tick
    that has writes; empty ticks extend the wait). The last one is FF."""
    ops, n = [], len(ticks)
    t = 0
    while t < n:
        ops.extend(ticks[t])
        k = 1
        while t + k < n and not ticks[t + k] and k < 125:
            k += 1
        ops.append(bytes([0x7F + k]))
        t += k
    ops.append(b"\xFF")
    return ops


SCC_BUF = 1024                               # the player's SCC write buffer (2 bytes per write)


def scc_buffer_bytes(ops_of_tick):
    return sum(2 * (32 if o[0] >= 0x20 else 1) for o in ops_of_tick if 0x10 <= o[0] <= 0x23)


def convert(path, nticks, fade_ticks=180):
    tracks, length = read_music(path)
    if tracks and "mod" in tracks[0]:
        # the player ticks at the NTSC frame rate, not at 60 Hz: a MOD note goes
        # to the tick that comes at its real time (0.13 %, 74 ms after 57 s).
        # The MIDI path keeps its 60 Hz grid, so its streams stay as they were.
        k = VBLANK_HZ / TICK_HZ
        for tr in tracks:
            tr["notes"] = [(s * k, e * k, p, v) for s, e, p, v in tr["notes"]]
    ticks = arrange(tracks, nticks, fade_ticks)
    for t, ops in enumerate(ticks["scc"]):
        n = scc_buffer_bytes(ops)
        assert n <= SCC_BUF, f"tick {t}: {n} bytes of SCC writes, over the player's buffer"
    return {k: encode(v) for k, v in ticks.items()}, ticks, tracks, length


def psg_writes(ticks):
    """Per tick, the PSG (register, value) writes, for run_rom_z80.py."""
    return [[(o[0], o[1]) for o in ops if o[0] <= 0x0D] for ops in ticks]


if __name__ == "__main__":
    import sys
    enc, ticks, tracks, length = convert(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3420)
    print(f"{len(tracks)} tracks, {length:.1f} s")
    if tracks and "mod" in tracks[0]:
        print(mod_table(tracks))
    else:
        for tr in tracks:
            print(f"  {tr['name'][:20]:20s} prog {tr['program']:3d} ch {tr['channel']:2d} {tr['family']:8s} "
                  f"median {tr['median']}")
    for k, ops in enc.items():
        print(f"{k}: {sum(len(o) for o in ops)} bytes")
