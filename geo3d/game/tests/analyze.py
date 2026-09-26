#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Reads what tests/game.tcl wrote (out/test/<run>/): renders the captures
to PNG and GIF, measures the frame pacing and the frame work, checks the
tile pages in VRAM and the background seen in the captured frames against
Alex's tiles, the first title page after a game (the starfield, not the
game's layers) and the title's star steps (even cadences).

  analyze.py render <cap.bin> <out.png> [--hitbox]
  analyze.py gif <run dir> <out.gif> [first last step scale]
  analyze.py pacing <run dir>          flip intervals (vertical blanks), the
                                       worst second (late flips in any 30)
  analyze.py work <run dir>            frame markers (needs FMARK=1)
  analyze.py vram <run dir>            vram_2_7.bin against the tiles
  analyze.py frames <run dir>          captured frames: background / band
                                       against the tiles at their best offset
  analyze.py report <run dir>          all of the above that apply (JSON)
"""
import glob
import json
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
GAME = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(GAME, "tools"))
import common as C  # noqa: E402
import gen_tiles  # noqa: E402

VBLANK = 1 / 59.9227          # NTSC field rate of the V99x8 (262 lines)
CAP_SIZE = 27136 + 512 + 128 + 2048 + 32


def pal_of(raw):
    """openMSX's "VDP palette" debuggable: a word per entry, GGGGGRRRRRBBBBB
    (5 bits each)."""
    if len(raw) == 32 and any(raw):
        out = []
        for i in range(16):
            w = raw[2 * i] | (raw[2 * i + 1] << 8)
            f = lambda v: round(v * 255 / 31)  # noqa: E731
            out.append((f((w >> 5) & 31), f((w >> 10) & 31), f(w & 31)))
        return np.array(out, np.uint8)
    return np.array(C.palette_rgb(), np.uint8)


def unpack_page(b):
    bm = np.frombuffer(b[:27136], np.uint8).reshape(212, 128)
    px = np.zeros((212, 256), np.uint8)
    px[:, 0::2] = bm >> 4
    px[:, 1::2] = bm & 15
    return px


def load_cap(path):
    d = open(path, "rb").read()
    assert len(d) >= CAP_SIZE - 32, path
    px = unpack_page(d)
    o = 27136
    col = d[o:o + 512]
    att = d[o + 512:o + 640]
    pat = d[o + 640:o + 2688]
    pal = pal_of(d[o + 2688:o + 2720])
    return px, col, att, pat, pal


def sprites_over(img, col, att, pat, pal, hitbox=False):
    """Sprite mode 2, 16 x 16, 16 per line, TP = 0; planes in order (a lower
    plane in front)."""
    drawn = np.zeros(img.shape[:2], bool)
    per_line = np.zeros(212, int)
    for p in range(32):
        y, x, pn = att[4 * p], att[4 * p + 1], att[4 * p + 2]
        if y == 216:
            break
        for r in range(16):
            line = (y + 1 + r) & 255
            if line >= 212:
                continue
            per_line[line] += 1
            if per_line[line] > 16:
                continue
            c = col[16 * p + r]
            xs = x - (32 if c & 0x80 else 0)
            bits = (pat[(pn & 0xFC) * 8 + r] << 8) | pat[(pn & 0xFC) * 8 + 16 + r]
            for k in range(16):
                if bits & (0x8000 >> k):
                    xx = xs + k
                    if not 0 <= xx < 256 or drawn[line, xx]:
                        continue
                    if c & 15:
                        img[line, xx] = pal[c & 15]
                        drawn[line, xx] = True
                    elif hitbox and (r in (0, 15) or k in (0, 15) or
                                     not (bits & (0x8000 >> max(k - 1, 0))) or
                                     not (bits & (0x8000 >> min(k + 1, 15)))):
                        img[line, xx] = (255, 0, 255)
    return img


def render(path, hitbox=False):
    px, col, att, pat, pal = load_cap(path)
    img = pal[px].copy()
    return sprites_over(img, col, att, pat, pal, hitbox)


def save_png(img, path, scale=2):
    im = Image.fromarray(img)
    if scale != 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    im.save(path)


# ---------------------------------------------------------------- logs
def read_log(run):
    ev = []
    for line in open(os.path.join(run, "log.txt")):
        p = line.split()
        if p:
            ev.append(p)
    return ev


def status_lines(run):
    out = []
    for p in read_log(run):
        if p[0] == "S":
            d = {"t": float(p[1])}
            for kv in p[2:]:
                k, v = kv.split("=")
                try:
                    d[k] = int(v)
                except ValueError:
                    d[k] = v
            out.append(d)
    return out


def pacing(run, t0=None):
    """Intervals between flips in vertical blanks. The first flips of the
    run (init, the first title frame) are skipped: from t0, or from the
    third flip."""
    fl = [(int(p[1]), float(p[2])) for p in read_log(run) if p[0] == "F"]
    # the BIOS writes the same RAM before init: keep the run of counts
    # 1, 2, 3, ...
    good = []
    for n, t in fl:
        if good and n != good[-1][0] + 1:
            if n == 1:
                good = []
            else:
                continue
        if not good and n != 1:
            continue
        good.append((n, t))
    if t0 is not None:
        good = [g for g in good if g[1] >= t0]
    else:
        good = good[2:]
    iv = [round((b[1] - a[1]) / VBLANK, 2) for a, b in zip(good, good[1:])]
    hist = {}
    for v in iv:
        k = int(round(v))
        hist[k] = hist.get(k, 0) + 1
    late = [(good[i + 1][0], good[i + 1][1], iv[i]) for i in range(len(iv)) if iv[i] > 2.5]
    # clustering: the most late flips in any 30 flips (one second), the
    # longest run of late flips in a row
    lf = [1 if v > 2.5 else 0 for v in iv]
    window = max((sum(lf[i:i + 30]) for i in range(max(1, len(lf) - 29))), default=0)
    run_, longest = 0, 0
    for x in lf:
        run_ = run_ + 1 if x else 0
        longest = max(longest, run_)
    return dict(flips=len(good), intervals=len(iv), hist=hist, late=len(late),
                late_list=late[:20], worst_second=window, longest_run=longest,
                mean_ms=round(1000 * (good[-1][1] - good[0][1]) / max(1, len(iv)), 3) if iv else 0)


def work(run):
    """Per frame: start (30h) to flip asked (33h), and the parts."""
    ks = [(int(p[1]), float(p[2])) for p in read_log(run) if p[0] == "K"]
    frames, cur = [], None
    for v, t in ks:
        if v == 0x30:
            cur = {0x30: t}
        elif cur is not None:
            cur[v] = t
            if v == 0x33:
                frames.append(cur)
                cur = None
    if not frames:
        return {}
    tot = [1000 * (f[0x33] - f[0x30]) for f in frames]
    lay = [1000 * (f[0x31] - f[0x30]) for f in frames if 0x31 in f]
    geo = [1000 * (f[0x36] - f[0x34]) for f in frames if 0x36 in f and 0x34 in f]
    return dict(frames=len(frames), work_ms_max=round(max(tot), 2),
                work_ms_mean=round(sum(tot) / len(tot), 2),
                work_ms_p99=round(sorted(tot)[int(0.99 * (len(tot) - 1))], 2),
                logic_and_layers_ms_max=round(max(lay), 2) if lay else None,
                geo3d_ms_max=round(max(geo), 2) if geo else None,
                geo3d_ms_mean=round(sum(geo) / len(geo), 2) if geo else None)


def breakdown(run, t0=0.0):
    """Mean and max time (ms) between consecutive frame markers, per pair."""
    ks = [(int(p[1]), float(p[2])) for p in read_log(run) if p[0] == "K" and float(p[2]) >= t0]
    acc = {}
    for (a, ta), (b, tb) in zip(ks, ks[1:]):
        if a == 0x33:
            continue
        acc.setdefault((a, b), []).append(1000 * (tb - ta))
    out = {}
    for (a, b), v in sorted(acc.items()):
        out[f"{a:02x}->{b:02x}"] = dict(n=len(v), mean=round(sum(v) / len(v), 2), max=round(max(v), 2))
    return out


# ---------------------------------------------------------------- tiles
def tiles():
    t = gen_tiles.build(verbose=False)
    bg = np.concatenate([t["bg_l"]["img"], t["bg_r"]["img"]], axis=1)
    return bg, t["fg"]["img"]


def vram_check(run):
    path = os.path.join(run, "vram_2_7.bin")
    if not os.path.exists(path):
        return None
    d = open(path, "rb").read()
    bg, fg = tiles()

    def lines(y0, n):
        o = (y0 - 512) * 128
        return unpack_page(d[o:o + n * 128] + bytes(27136 - n * 128))[:n]
    res = {}
    res["bg_left"] = bool(np.array_equal(lines(512, 212), bg[:, :256]))
    res["bg_right"] = bool(np.array_equal(lines(768, 212), bg[:, 256:]))
    bg1 = np.roll(bg, -1, axis=1)
    res["bg1_left"] = bool(np.array_equal(lines(1024, 212), bg1[:, :256]))
    res["bg1_right"] = bool(np.array_equal(lines(1280, 212), bg1[:, 256:]))
    res["fg"] = bool(np.array_equal(lines(1536, 48), fg))
    res["ok"] = all(res.values())
    return res


def frame_check(path, bg, fg):
    """The captured frame against the tiles: the best background offset over
    lines 16-163 (away from the HUD), the best band offset over lines
    196-211; the share of pixels that match (objects, text and stars make
    the rest)."""
    px, col, att, pat, pal = load_cap(path)
    out = {}
    rows = slice(16, 164)
    win = px[rows]
    best = (0, -1)
    for s in range(512):
        idx = (np.arange(256) + s) % 512
        m = np.mean(win == bg[rows][:, idx])
        if m > best[0]:
            best = (m, s)
    out["bg_match"], out["bg_offset"] = round(float(best[0]), 4), best[1]
    band = px[196:212]
    bestf = (0, -1)
    for s in range(256):
        idx = (np.arange(256) + s) % 256
        m = np.mean(band == fg[32:48][:, idx])
        if m > bestf[0]:
            bestf = (m, s)
    out["band_match"], out["band_offset"] = round(float(bestf[0]), 4), bestf[1]
    return out


def title_frames(run):
    """The first page of every return to the title (M lines): the title is
    composed on the starfield, so the band area (lines 164-211) shows stars
    and text only, not the foreground band of the game it came from.
    Returns (frames, the most non-black pixels in the band area)."""
    worst, n = 0, 0
    for p in read_log(run):
        if p[0] == "M" and int(p[3]) == 1:
            path = p[4] if os.path.exists(p[4]) else os.path.join(run, os.path.basename(p[4]))
            px = load_cap(path)[0]
            worst = max(worst, int(np.count_nonzero(px[164:212])))
            n += 1
    return n, worst


def star_steps(run):
    """Per-flip steps (pixels) of the 3 star layers on the title once their
    speed is at its target (F lines: s_st0-2, v_st, t_st): each layer must
    step evenly, the same step every flip or two steps alternating (0.5 px
    per tick: 0, 1, 0, 1). Returns (flips looked at, irregular flips, the
    steps seen per layer)."""
    prev, n, bad = None, 0, 0
    seen = [set(), set(), set()]
    hist = [[], [], []]
    for p in read_log(run):
        if p[0] != "F" or len(p) < 13:
            continue
        mode, v, tgt = int(p[6]), int(p[10]), int(p[11])
        cur = [(int(p[7 + k]) >> 4) & 255 for k in range(3)]
        if prev is not None and mode == 1 and v == tgt and prev[1] == 1 and prev[2] == v:
            n += 1
            for k in range(3):
                d = (cur[k] - prev[0][k]) & 255
                seen[k].add(d)
                h = hist[k]
                h.append(d)
                # irregular: a third step value, or the same of two values twice in a row
                if len(set(h[-8:])) > 2 or (len(set(h[-8:])) == 2 and len(h) >= 2 and h[-1] == h[-2]):
                    bad += 1
        if mode != 1:
            hist = [[], [], []]
        prev = (cur, mode, v)
    return n, bad, [sorted(s) for s in seen]


def frames_check(run, only_mode=None):
    bg, fg = tiles()
    res = []
    for p in read_log(run):
        if p[0] == "C":
            n, tick, mode, path = int(p[1]), int(p[2]), int(p[3]), p[4]
            phase = int(p[5]) if len(p) > 5 else -1
            if only_mode is not None and mode not in only_mode:
                continue
            if phase != 3:
                continue                    # stars on show, or a layer coming in
            if not os.path.exists(path):
                path = os.path.join(run, os.path.basename(path))
            r = frame_check(path, bg, fg)
            r.update(flip=n, tick=tick, mode=mode)
            res.append(r)
    return res


# ---------------------------------------------------------------- gif
def gif(run, out, first=0, last=10 ** 9, step=1, scale=2, ms=67):
    caps = []
    for p in read_log(run):
        if p[0] == "C":
            n = int(p[1])
            if first <= n < last and (n - first) % step == 0:
                path = p[4] if os.path.exists(p[4]) else os.path.join(run, os.path.basename(p[4]))
                caps.append(path)
    frames = []
    for c in caps:
        im = Image.fromarray(render(c))
        if scale != 1:
            im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
        frames.append(im.convert("P", palette=Image.ADAPTIVE, colors=64))
    if frames:
        frames[0].save(out, save_all=True, append_images=frames[1:], duration=ms, loop=0,
                       optimize=True)
    return len(frames)


def verdict(run, max_late=0.05):
    """PASS/FAIL lines for one run: its checks, pacing, VRAM, frames."""
    out = []
    ev = read_log(run)
    name = os.path.basename(run.rstrip("/"))
    for p in ev:
        if p[0] == "E" and len(p) > 2 and p[2] in ("PASS", "FAIL"):
            out.append(f"{name}: {p[1]} {p[2]}")
    st = status_lines(run)
    ended = any(p[0] == "END" for p in ev)
    if len(st) >= 3:
        last = st[-1]
        # the game ticks on to the end (no hang): the last status lines advance
        alive = ended and st[-3]["tick"] <= st[-2]["tick"] <= last["tick"] and st[-3]["tick"] < last["tick"]
        out.append(f"{name}: runs to the end {'PASS' if alive else 'FAIL'} "
                   f"(ticks {last['tick']}, flips {last['flips']}, t {last['t']:.0f} s)")
    pc = pacing(run)
    if pc.get("intervals"):
        frac = pc["late"] / pc["intervals"]
        max_window = int(os.environ.get("MAX_WINDOW", "6"))
        ok = frac <= max_late and pc["worst_second"] <= max_window
        out.append(f"{name}: pacing {'PASS' if ok else 'FAIL'} ({pc['intervals']} flips, "
                   f"{pc['late']} late = {100 * frac:.2f} %, limit {100 * max_late:.1f} %, "
                   f"worst second {pc['worst_second']} of 30 late (limit {max_window}), "
                   f"longest run {pc['longest_run']}, hist {pc['hist']})")
    vr = vram_check(run)
    if vr is not None:
        out.append(f"{name}: tile pages in VRAM {'PASS' if vr['ok'] else 'FAIL'} {vr}")
    fr = frames_check(run)
    if fr:
        bmin = min(f["bg_match"] for f in fr)
        fmin = min(f["band_match"] for f in fr)
        ok = bmin >= 0.85 and fmin >= 0.97
        out.append(f"{name}: frames against the tiles {'PASS' if ok else 'FAIL'} ({len(fr)} frames, "
                   f"background match min {bmin:.3f}, band match min {fmin:.3f})")
    ps = [p for p in ev if p[0] == "P"]
    played = any(s.get("mode") in (2, 3, 4) for s in st)
    if ps and played:                       # (the title makes no sound)
        loud = sum(1 for p in ps if any(int(v) & 15 for v in p[3:6]))
        out.append(f"{name}: PSG sound effects {'PASS' if loud else 'FAIL'} ({loud} of {len(ps)} samples)")
    n, worst = title_frames(run)
    if n:
        out.append(f"{name}: title pages after a game {'PASS' if worst < 3000 else 'FAIL'} "
                   f"({n} frames, band area at most {worst} non-black pixels)")
    n, bad, seen = star_steps(run)
    if n >= 20:
        out.append(f"{name}: title star steps {'PASS' if bad == 0 else 'FAIL'} "
                   f"({n} flips, {bad} irregular, steps per layer {seen})")
    return out


def profile(run, labels, top=40):
    """Z lines (sampled PCs) -> share of time per routine (the nearest code
    label below the PC; local labels like xx_1 are folded into the routine
    before them)."""
    import re
    labs = []
    for line in open(labels):
        m = re.match(r"^([A-Za-z_0-9]+):\s+equ \$([0-9a-fA-F]+)", line)
        if m:
            a = int(m.group(2), 16)
            if 0x0000 <= a < 0xC000 and not re.search(r"_\d+[a-z]?$|_n$|_x$", m.group(1)):
                labs.append((a, m.group(1)))
    labs.sort()
    addrs = [a for a, _ in labs]
    import bisect
    cnt = {}
    n = 0
    for p in read_log(run):
        if p[0] == "Z":
            pc = int(p[1], 16)
            n += 1
            if pc < 0x4000:
                key = "(BIOS)"
            elif pc >= 0xC000:
                key = "(RAM)"
            else:
                i = bisect.bisect_right(addrs, pc) - 1
                key = labs[i][1] if i >= 0 else "?"
            cnt[key] = cnt.get(key, 0) + 1
    rows = sorted(cnt.items(), key=lambda kv: -kv[1])[:top]
    return n, [(k, round(100 * v / n, 2)) for k, v in rows]


def main(argv):
    cmd = argv[0]
    if cmd == "profile":
        n, rows = profile(argv[1], argv[2])
        print(f"{n} samples")
        for k, v in rows:
            print(f"  {v:6.2f} %  {k}")
        return
    if cmd == "verdict":
        lim = float(os.environ.get("MAX_LATE", "0.05"))
        for run in argv[1:]:
            for line in verdict(run, lim):
                print(line)
        return
    if cmd == "render":
        save_png(render(argv[1], "--hitbox" in argv), argv[2])
    elif cmd == "gif":
        a = [int(v) for v in argv[3:]]
        n = gif(argv[1], argv[2], *a)
        print(f"{n} frames -> {argv[2]}")
    elif cmd == "pacing":
        print(json.dumps(pacing(argv[1]), indent=1))
    elif cmd == "work":
        print(json.dumps(work(argv[1]), indent=1))
    elif cmd == "breakdown":
        for k, v in breakdown(argv[1], float(argv[2]) if len(argv) > 2 else 0.0).items():
            print(k, v)
    elif cmd == "vram":
        print(json.dumps(vram_check(argv[1]), indent=1))
    elif cmd == "frames":
        for r in frames_check(argv[1]):
            print(r)
    elif cmd == "report":
        run = argv[1]
        rep = dict(pacing=pacing(run), work=work(run), vram=vram_check(run))
        fr = frames_check(run)
        if fr:
            rep["frames"] = dict(n=len(fr), bg_match_min=min(f["bg_match"] for f in fr),
                                 bg_match_mean=round(sum(f["bg_match"] for f in fr) / len(fr), 4),
                                 band_match_min=min(f["band_match"] for f in fr))
        ps = [p for p in read_log(run) if p[0] == "P"]
        if ps:
            loud = [p for p in ps if any(int(v) & 15 for v in p[3:6])]
            rep["psg"] = dict(samples=len(ps), with_sound=len(loud))
        rep["checks"] = [" ".join(p[1:]) for p in read_log(run) if p[0] == "E"]
        st = status_lines(run)
        if st:
            rep["last_status"] = st[-1]
        print(json.dumps(rep, indent=1))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
