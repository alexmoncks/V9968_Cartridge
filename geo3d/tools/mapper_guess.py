#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
# ============================================================================
# mapper_guess.py - what a launcher or an emulator guesses for a ROM's mapper
#
# Two checks, the way a loader that has no database entry for the file sees
# it:
#
# 1. The ROM type signature: 8 bytes at file offset 0010h (or 4010h), right
#    after the 16-byte "AB" header. The convention is MSXgl's "ROM type
#    signature" 1.0 (2024-02-18): ROM_AS16 = ASCII16, ROM_ASC8 = ASCII8,
#    ROM_KON4 / ROM_KON5 = Konami / Konami SCC, ROM_NEO8 / ROM_NE16 = NEO,
#    ASCII16X = ASCII16-X. openMSX itself reads ASCII16X, ROM_NEO8 and
#    ROM_NE16 there (RomFactory.cc), the rest by the heuristic below.
#
# 2. The LD (nnnn),A count of openMSX's guessRomType() (src/memory/
#    RomFactory.cc), which several launchers copy: every 32h byte followed
#    by one of these addresses scores one point for each listed mapper:
#        5000h 9000h B000h   Konami SCC
#        4000h 8000h A000h   Konami
#        6800h 7800h         ASCII8
#        6000h               Konami, ASCII8, ASCII16
#        7000h               Konami SCC, ASCII8, ASCII16
#        77FFh               ASCII16
#    ASCII8 then loses one point. The highest score wins; on a tie the type
#    that comes later in openMSX's RomType enum wins (ASCII8 < ASCII16 <
#    Konami < Konami SCC). No point at all = GENERIC_8KB. Files under 64 KB,
#    and 64 KB files without "AB" at offset 0, are plain ROMs and never
#    reach the count.
#
# Usage: mapper_guess.py ROM... [--expect ASCII16|ASCII8] [--margin N]
#   prints the signature, the scores and the guess for each file; with
#   --expect it exits 1 unless the guess is that type, the signature agrees
#   and the type leads the next one by at least --margin points (default 8).
# As a module: report(data, name, expect, need) -> (ok, lines), used by the
# ROM builds (rom/build_rom.py, game/tools/rompack.py, game/build64.sh,
# basic/build.sh) to refuse a ROM that does not read as its mapper.
# ============================================================================
import sys

SIGNATURES = {
    b"ROM_AS16": "ASCII16", b"ROM_ASC8": "ASCII8", b"ROM_KON4": "KONAMI",
    b"ROM_KON5": "KONAMI_SCC", b"ROM_NEO8": "NEO8", b"ROM_NE16": "NEO16",
    b"ASCII16X": "ASCII16X", b"ROM_YAM8": "YAMANOOTO", b"ROM_PSCC": "POPOLON_SCC",
}
# openMSX's RomType enum order for the types the count touches
ORDER = ["ASCII8", "ASCII16", "GENERIC_8KB", "KONAMI", "KONAMI_SCC"]
WEIGHTS = {
    0x5000: ("KONAMI_SCC",), 0x9000: ("KONAMI_SCC",), 0xB000: ("KONAMI_SCC",),
    0x4000: ("KONAMI",), 0x8000: ("KONAMI",), 0xA000: ("KONAMI",),
    0x6800: ("ASCII8",), 0x7800: ("ASCII8",),
    0x6000: ("KONAMI", "ASCII8", "ASCII16"),
    0x7000: ("KONAMI_SCC", "ASCII8", "ASCII16"),
    0x77FF: ("ASCII16",),
}


def signature(data):
    for off in (0x0010, 0x4010):
        if len(data) >= off + 8 and data[off - 16:off - 14] == b"AB":
            tag = bytes(data[off:off + 8])
            if tag in SIGNATURES:
                return off, tag.decode(), SIGNATURES[tag]
    return None, None, None


def scores(data):
    """openMSX's raw counts (before the ASCII8 -1) and the address hits."""
    s = {t: 0 for t in ORDER}
    hits = {}
    for i in range(len(data) - 3):       # xrange(size - 3), as in openMSX
        if data[i] == 0x32:
            a = data[i + 1] | data[i + 2] << 8
            if a in WEIGHTS:
                hits[a] = hits.get(a, 0) + 1
                for t in WEIGHTS[a]:
                    s[t] += 1
    return s, hits


def guess(data):
    """openMSX guessRomType() for a ROM that is not in its software database."""
    size = len(data)
    sig_off = 16
    if size >= sig_off + 8:
        tag = bytes(data[sig_off:sig_off + 8])
        if tag in (b"ASCII16X", b"ROM_NEO8", b"ROM_NE16"):
            return SIGNATURES[tag], None, None
    if size == 0:
        return "NORMAL", None, None
    if size < 0x10000:
        return "MIRRORED (or PAGE2)", None, None
    if size == 0x10000 and data[0:2] != b"AB":
        return "MIRRORED", None, None
    s, hits = scores(data)
    final = dict(s)
    if final["ASCII8"]:
        final["ASCII8"] -= 1
    best = "GENERIC_8KB"
    for t in ORDER:                       # tg && tg >= typeGuess[type]
        if final[t] and final[t] >= final[best]:
            best = t
    return best, final, hits


def margin(final, t):
    others = [v for k, v in final.items() if k != t]
    return final[t] - max(others)


def report(data, name, expect=None, need=8):
    """(ok, lines): what a guesser finds in data; ok is True without expect,
    else whether the guess is expect, the signature agrees and expect leads
    the next type by need points or more."""
    off, tag, sig_type = signature(data)
    g, final, hits = guess(data)
    lines = [f"{name}: {len(data) // 1024} KB",
             "  signature: " + (f"{tag} at {off:04X}h = {sig_type}" if tag else "none")]
    if final is not None:
        lines.append("  openMSX count: " + ", ".join(f"{t} {final[t]}" for t in ORDER if t != "GENERIC_8KB")
                     + "   (ASCII8 after its -1)")
        lines.append("  LD (nnnn),A hits: "
                     + (", ".join(f"{a:04X}h x{n}" for a, n in sorted(hits.items())) or "none"))
    lines.append(f"  openMSX guess (no -romtype, not in the database): {g}")
    ok = True
    if expect:
        ok = g == expect and sig_type == expect
        if final is not None and expect in final:
            m = margin(final, expect)
            lines.append(f"  {expect} leads the next type by {m}")
            ok = ok and m >= need
        lines.append("  " + ("OK" if ok else f"FAIL: expected {expect}, its signature and a lead of {need}"))
    return ok, lines


def main(argv):
    expect, need, files = None, 8, []
    it = iter(argv)
    for a in it:
        if a == "--expect":
            expect = next(it)
        elif a == "--margin":
            need = int(next(it))
        else:
            files.append(a)
    bad = 0
    for f in files:
        ok, lines = report(open(f, "rb").read(), f, expect, need)
        print("\n".join(lines))
        bad += not ok
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
