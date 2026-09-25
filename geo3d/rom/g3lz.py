#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""G3LZ: the demo ROM's stream compression (build_rom.py packs with it, the
player in geo3d_rom.asm decodes it: dz_block / dz_need / dz_idle).

A byte-aligned LZ with a repeat offset. Each block (at most 8 KB of whole
stream ops, the player's dz_buf) is compressed on its own, so the player can
start decoding at any block: a new demo, the next block, a loop restart.

Token T = LLL R MMMM, then the literal length byte, the literals, the offset,
the match length byte (each only when needed):
  LLL   0..6 literals; 7: one more byte x, 7 + x literals
  MMMM  0: no match (a literal-only token); 1..14: a match of MMMM + 1 bytes;
        15: one more byte x, a match of 16 + x bytes
  R     1: the match reuses the last offset; 0: a new offset follows:
        one byte b < 80h: offset b + 1; else two bytes b, c:
        ((b & 7Fh) << 8 | c) + 1
The last offset starts at 1 in every block. T = 00h (it would mean: nothing)
is an escape: the next byte is 00h at the end of the block, 01h where the
stream goes on at 8000h of the next ROM bank (a token never crosses a bank),
and 02h, at the start of a block only, for a raw block: its ops follow as
they are, in one bank, and the player interprets them straight from ROM
(build_rom.py knows their length; decompress() does not read them).

The parse minimises the size (a forward DP over the MAX_CAND newest matches
per position, carrying the repeat offset along the best path), except over
the first `fast_len` bytes of a block,
where each token costs TOKEN_COST bytes more: there the player decodes on
demand between two page flips, and long literal runs (one LDIR) decode
faster than many short matches. Literal runs and matches are capped at
MAXLIT / MAXM bytes, so one token decodes at most 256 bytes: that bounds
dz_idle, one token per poll of the page flip's wait.
"""

ESC_END, ESC_BANK, ESC_RAW = 0x00, 0x01, 0x02
MAXLIT, MAXM = 128, 128
TOKEN_COST = 8.0
MAX_CAND = 48              # match candidates tried per position (newest first)


def _lit_ext(n):
    return 0 if n < 7 else 1


def _match_ext(m):
    return 0 if m < 16 else 1


def compress(data, fast_len=0):
    """-> the token list [(literals, match length or 0, offset or None =
    the last offset again)]."""
    n = len(data)
    INF = 1 << 60
    cost = [INF] * (n + 1)
    run = [0] * (n + 1)            # literal run length at i on the best path
    last = [1] * (n + 1)           # last offset at i on the best path
    back = [None] * (n + 1)        # ("L",) or ("M", start, length, offset)
    cost[0] = 0
    heads = {}                     # 2-byte key -> positions

    def mlen(i, j, cap):
        """longest L <= cap with data[j:j + L] == data[i:i + L] (j < i)"""
        if cap <= 0 or data[j] != data[i]:
            return 0
        lo, hi = 0, cap
        while lo < hi:
            mid = (lo + hi + 1) >> 1
            if data[j:j + mid] == data[i:i + mid]:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def match(i, c, tok, m, o, obytes):
        k = c + tok + obytes + _match_ext(m)
        b = back[i + m]
        if k < cost[i + m] or (k == cost[i + m] and obytes and b and b[0] == "L"):
            cost[i + m], run[i + m], last[i + m], back[i + m] = k, 0, o, ("M", i, m, o)

    for i in range(n):
        c = cost[i]
        if c < INF:
            # the token byte, plus the speed penalty near the block start
            tok = 1 + (TOKEN_COST if i < fast_len else 0)
            r = run[i]
            # one more literal (a run at MAXLIT is closed by a literal-only token)
            if r == MAXLIT:
                nc, nr = c + tok + 1, 1
            else:
                nc, nr = c + 1 + _lit_ext(r + 1) - _lit_ext(r), r + 1
            if nc < cost[i + 1]:
                cost[i + 1], run[i + 1], last[i + 1], back[i + 1] = nc, nr, last[i], ("L",)
            cap = min(n - i, MAXM)
            # a match with the last offset
            ro = last[i]
            if ro <= i:
                L = mlen(i, i - ro, cap)
                for m in range(2, L + 1):
                    if m <= 18 or m in (L, 16):
                        match(i, c, tok, m, ro, 0)
            # matches with a new offset
            if i + 1 < n:
                best = 1
                for seen, j in enumerate(reversed(heads.get(data[i] | data[i + 1] << 8, ()))):
                    if seen == MAX_CAND:
                        break
                    o = i - j
                    L = mlen(i, j, cap)
                    if L < 2 or (L <= best and o > 128):
                        continue
                    ob = 1 if o <= 128 else 2
                    for m in range(2 if ob == 1 else 3, L + 1):
                        if m <= 18 or m in (L, 16):
                            match(i, c, tok, m, o, ob)
                    best = max(best, L)
        if i + 1 < n:
            heads.setdefault(data[i] | data[i + 1] << 8, []).append(i)
    # the best path, back to front
    steps = []
    i = n
    while i > 0:
        b = back[i]
        if b[0] == "L":
            steps.append(("L", i - 1))
            i -= 1
        else:
            steps.append(b)
            i = b[1]
    steps.reverse()
    tokens, lits, lastoff = [], bytearray(), 1
    for s in steps:
        if s[0] == "L":
            if len(lits) == MAXLIT:
                tokens.append((bytes(lits), 0, None))
                lits = bytearray()
            lits.append(data[s[1]])
        else:
            _, _, m, o = s
            tokens.append((bytes(lits), m, None if o == lastoff else o))
            lastoff = o
            lits = bytearray()
    if lits:
        tokens.append((bytes(lits), 0, None))
    return tokens


def token_bytes(lits, m, off):
    nl = len(lits)
    assert nl <= MAXLIT and m <= MAXM and (nl or m)
    lf = min(nl, 7)
    mf = 0 if m == 0 else min(m - 1, 15)
    rep = 1 if (m and off is None) else 0
    out = bytearray([lf << 5 | rep << 4 | mf])
    if lf == 7:
        out.append(nl - 7)
    out += lits
    if m:
        if off is not None:
            d = off - 1
            assert 0 <= d < 0x8000, off
            out += bytes([d]) if d < 128 else bytes([0x80 | d >> 8, d & 0xFF])
        if mf == 15:
            out.append(m - 16)
    return bytes(out)


def pack(data, fast_len=0):
    """one block -> its G3LZ bytes, the end escape included"""
    return b"".join(token_bytes(*t) for t in compress(data, fast_len)) + bytes([0, ESC_END])


def split(buf):
    """a packed block (no bank escapes) -> its tokens as byte strings, the
    end escape last (the ROM packer keeps each one inside a bank)"""
    out, pos = [], 0
    while True:
        start, t = pos, buf[pos]
        pos += 1
        if t == 0:
            assert buf[pos] == ESC_END and pos + 1 == len(buf)
            out.append(buf[start:])
            return out
        nl = t >> 5
        if nl == 7:
            nl = 7 + buf[pos]
            pos += 1
        pos += nl
        if t & 15:
            if not t & 0x10:
                pos += 2 if buf[pos] & 0x80 else 1
            if t & 15 == 15:
                pos += 1
        out.append(buf[start:pos])


def skip_bank(buf, pos, bank):
    """the position of a block's first byte: past a bank escape, if any"""
    if buf[pos] == 0 and buf[pos + 1] == ESC_BANK:
        pos = (pos + 2 + bank - 1) // bank * bank
    return pos


def decompress(buf, pos=0, bank=None):
    """-> (the block, the position after its end escape). A bank escape
    goes on at the next multiple of `bank` bytes (the ROM image), or is an
    error without it."""
    out = bytearray()
    last = 1
    while True:
        t = buf[pos]
        pos += 1
        if t == 0:
            e = buf[pos]
            pos += 1
            if e == ESC_END:
                return bytes(out), pos
            assert e == ESC_BANK and bank, e
            pos = (pos + bank - 1) // bank * bank
            continue
        nl = t >> 5
        if nl == 7:
            nl = 7 + buf[pos]
            pos += 1
        out += buf[pos:pos + nl]
        pos += nl
        mf = t & 15
        if not mf:
            continue
        if not t & 0x10:
            b = buf[pos]
            pos += 1
            if b & 0x80:
                b = (b & 0x7F) << 8 | buf[pos]
                pos += 1
            last = b + 1
        m = mf + 1
        if mf == 15:
            m = 16 + buf[pos]
            pos += 1
        s = len(out) - last
        assert s >= 0, "offset before the block start"
        for k in range(m):
            out.append(out[s + k])
