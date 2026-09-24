#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""MIDI to MSX sound chips, for the demo ROM's music.

Reads a Standard MIDI File (own parser, no dependency) and reduces the
arrangement, tick by tick at 60 Hz (the player advances the music once per
vertical blank), to three targets:

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

The MIDI itself is an input (build_rom.py --music): music you do not own must
stay out of the repository.
"""
import math
import struct

TICK_HZ = 60
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
    tracks, length = read_midi(path)
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
    for tr in tracks:
        print(f"  {tr['name'][:20]:20s} prog {tr['program']:3d} ch {tr['channel']:2d} {tr['family']:8s} median {tr['median']}")
    for k, ops in enc.items():
        print(f"{k}: {sum(len(o) for o in ops)} bytes")
