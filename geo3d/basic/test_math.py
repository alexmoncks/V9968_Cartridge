#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Runs the math routines of out/G3BASIC.ROM in a Z80 emulator (pip package
z80) and compares them with g3ref.py, bit for bit, and with the float
ideal within a tolerance: getsin, mul16s, div32, rsqrt, qdiv, nrm3, rotmat,
camset, dacang. Also reports the T-states of the per-frame math.

Memory: bank 0 at 4000h, bank 1 at 6000h (a write to 6800h maps another
bank there, as the ASCII8 mapper does), the work area at C000h, IY =
C000h + XB. Usage: test_math.py [seed]. Exit 1 on a mismatch.
"""
import math
import random
import struct
import sys

import z80

import g3ref

ROM = open("out/G3BASIC.ROM", "rb").read()
LBL = {}
for line in open("out/g3basic.lbl"):
    p = line.split()
    if len(p) >= 3 and p[1] == "equ":
        LBL[p[0].rstrip(":")] = int(p[2].lstrip("$"), 16)
WK = 0xC000
XB = LBL["XB"]
IY = WK + XB
HALT = 0xFFF0

m = z80.Z80Machine()
m.set_memory_block(0x4000, ROM[0:0x2000])
m.set_memory_block(0x6000, ROM[0x2000:0x4000])
m.set_memory_block(HALT, b"\x76")
m.set_breakpoint(HALT)
state = {"bank": 1}


def on_write(addr, v):
    if addr == 0x6800:
        state["bank"] = v & 7
        m.set_memory_block(0x6000, ROM[0x2000 * state["bank"]:0x2000 * (state["bank"] + 1)])


m.set_write_callback(on_write)
m.mark_addrs(0x6800, 1, m.WRITE_MARK)

fails = 0
checks = 0


def call(name, **regs):
    """Runs routine `name` until it returns (to HALT); its T-states."""
    m.sp = 0xF000
    m.memory[0xF000] = HALT & 0xFF
    m.memory[0xF001] = HALT >> 8
    for r, v in regs.items():
        setattr(m, r, v)
    m.ix = regs.get("ix", WK)
    m.iy = IY
    m.pc = LBL[name]
    t0, t = m.frame_tick, 0
    while True:
        ev = m.run()
        if ev & 8:
            t += 100000             # the emulator's frame
        if m.pc == HALT:
            break
        if t > 50000000:
            raise RuntimeError("runaway in " + name)
    assert state["bank"] == 1, "bank 1 not back after " + name
    return t + m.frame_tick - t0


def rw(a):
    return m.memory[a] | m.memory[a + 1] << 8


def rs(a):
    return g3ref.s16(rw(a))


def ww(a, v):
    m.memory[a] = v & 0xFF
    m.memory[a + 1] = (v >> 8) & 0xFF


def y(name):
    return WK + LBL[name]


def check(name, ok, detail=""):
    global fails, checks
    checks += 1
    if not ok:
        fails += 1
        if fails < 40:
            print("FAIL", name, detail)


def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    rng = random.Random(seed)
    tstat = {}

    # getsin
    angs = [0, 1, 2, 3, 16382, 16383, 16384, 16385, 32768, 49152, 65534, 65535] + \
        [rng.randrange(65536) for _ in range(400)]
    for a in angs:
        call("getsin", hl=a)
        got = g3ref.s16(m.hl)
        check("getsin %d" % a, got == g3ref.getsin(a), "%d vs %d" % (got, g3ref.getsin(a)))
        check("getsin ideal %d" % a, abs(got - 16384 * math.sin(a * math.pi / 32768)) <= 3.7, got)

    # mul16s
    for _ in range(300):
        a, b = rng.randrange(-32768, 32768), rng.randrange(-32768, 32768)
        call("mul16s", bc=a & 0xFFFF, de=b & 0xFFFF)
        got = (m.de << 16 | m.hl)
        check("mul16s", got == (a * b) & 0xFFFFFFFF, (a, b, hex(got)))

    # div32
    for _ in range(300):
        bc = rng.randrange(1, 65536)
        de = rng.randrange(0, bc)
        hl = rng.randrange(65536)
        call("div32", bc=bc, de=de, hl=hl)
        n = de << 16 | hl
        check("div32", (m.hl, m.de) == (n // bc, n % bc), (n, bc, m.hl, m.de))

    # rsqrt
    for s in [0, 1, 2, 3, 4, 8, 65535, 65536, 2 ** 28, 3 * 2 ** 30, 3 * 2 ** 26] + \
            [rng.randrange(3 * 2 ** 30) for _ in range(200)] + [rng.randrange(1000) for _ in range(50)]:
        call("rsqrt", de=s >> 16, hl=s & 0xFFFF)
        check("rsqrt %d" % s, m.hl == g3ref.rsqrt(s), (m.hl, g3ref.rsqrt(s)))
        check("rsqrt ideal %d" % s, abs(m.hl - math.sqrt(s)) <= 0.5 + 1e-9, (m.hl, math.sqrt(s)))

    # qdiv
    for _ in range(300):
        b = rng.randrange(1, 32768 * 2 - 1)
        a = rng.randrange(-min(b, 32767), min(b, 32767) + 1)
        call("qdiv", de=a & 0xFFFF, bc=b)
        got = g3ref.s16(m.hl)
        check("qdiv", got == g3ref.qdiv(a, b), (a, b, got, g3ref.qdiv(a, b)))

    # nrm3
    for _ in range(300):
        r = rng.choice([1, 100, 20000, 2 ** 20, 2 ** 29])
        v = [rng.randrange(-r, r + 1) for _ in range(3)]
        if rng.random() < 0.1:
            v[rng.randrange(3)] = 0
        for i in range(3):
            a = y("W_TR") + 4 * i
            for k in range(4):
                m.memory[a + k] = (v[i] >> (8 * k)) & 0xFF
        call("nrm3")
        got = [rs(y("W_NQ") + 2 * i) for i in range(3)]
        exp = g3ref.nrm3(v)
        check("nrm3 %s" % v, got == exp, (got, exp))
        ln = math.sqrt(sum(x * x for x in v))
        if ln:
            check("nrm3 ideal", all(abs(got[i] - 16384 * v[i] / ln) <= 1.6 for i in range(3)), (v, got))

    # rotmat: object at C100h (IX)
    obj = WK + 0x100
    for n in range(200):
        a = [rng.randrange(65536) for _ in range(3)]
        for i in range(3):
            ww(obj + LBL["O_ANG"] + 2 * i, a[i])
        m.memory[obj + LBL["O_FLAGS"]] = 1
        t = call("rotmat", ix=obj)
        tstat.setdefault("rotmat", []).append(t)
        got = [rs(obj + LBL["O_ROT"] + 2 * i) for i in range(9)]
        exp = g3ref.rotmat(*a)
        check("rotmat %s" % a, got == exp, (got, exp))
        fl = g3ref.float_rot(*(x * 360 / 65536 for x in a))
        check("rotmat ideal %s" % a, all(abs(got[i] - 16384 * fl[i]) <= 12 for i in range(9)),
              [(got[i], round(16384 * fl[i])) for i in range(9)])
        check("rotmat flag", m.memory[obj + LBL["O_FLAGS"]] & 4 == 4)

    # camset
    for n in range(150):
        rr = rng.choice([10, 300, 5000, 32767])
        cam = [rng.randrange(-rr, rr + 1) for _ in range(3)]
        look = [rng.randrange(-rr, rr + 1) for _ in range(3)] if n % 3 else [0, 0, 0]
        if n == 0:
            cam, look = [0, 0, -300], [0, 0, 0]
        if n == 1:
            cam, look = [0, 500, 0], [0, 0, 0]
        if n == 2:
            cam, look = [0, -500, 0], [0, 0, 0]
        if n == 3:
            cam, look = [5, 5, 5], [5, 5, 5]
        light = [rng.randrange(-100, 101) for _ in range(3)] if n > 4 else [-1, 1, -1]
        for i in range(3):
            ww(WK + LBL["W_CAM"] + 2 * i, cam[i])
            ww(WK + LBL["W_LOOK"] + 2 * i, look[i])
            ww(WK + LBL["W_LIGHT"] + 2 * i, light[i])
        t = call("camset")
        tstat.setdefault("camset", []).append(t)
        c = [rs(y("W_CMAT") + 2 * i) for i in range(9)]
        cami = m.memory[y("W_CAMI")]
        lc = [rs(y("W_LCAM") + 2 * i) for i in range(3)]
        ec, ecami, elc = g3ref.camset(cam, look, light)
        check("camset %s %s" % (cam, look), (c, cami, lc) == (ec, ecami, elc), ((c, cami, lc), (ec, ecami, elc)))
        fc = g3ref.float_cam(cam, look)
        check("camset ideal %s %s" % (cam, look), all(abs(c[i] - 16384 * fc[i]) <= 8 for i in range(9)),
              [(c[i], round(16384 * fc[i])) for i in range(9)])
        if n == 0:
            check("default camera: identity, light (-9459, 9459, -9459)", (cami, lc) == (1, [-9459, 9459, -9459]), (cami, lc))

    # dacang: DAC at F7F6h, VALTYP F663h
    def bcd(value, nd):
        if value == 0:
            return [0] * 8
        neg = value < 0
        s = "%.*e" % (nd - 1, abs(value))
        mant, ex = s.split("e")
        digits = mant.replace(".", "")
        e = int(ex) + 1
        b = [(0x80 if neg else 0) | (e + 64)]
        digits = digits.ljust(14, "0")
        for k in range(0, 14, 2):
            b.append(int(digits[k]) << 4 | int(digits[k + 1]))
        return b[:8]
    cases = [(2, v) for v in [0, 1, -1, 90, 360, -360, 359, 32767, -32768, 12345, -721]] + \
            [(4, v) for v in [0.5, -0.5, 22.5, 1e10, -3.25, 359.999, 0.001, 1e-5, 720.5]] + \
            [(8, v) for v in [1.0 / 3, 123456789.123, -0.0001, 45.0]]
    cases += [(2, rng.randrange(-32768, 32768)) for _ in range(40)]
    cases += [(rng.choice([4, 8]), rng.uniform(-1e6, 1e6)) for _ in range(80)]
    for vt, v in cases:
        if vt == 2:
            dac = [0, 0, v & 0xFF, (v >> 8) & 0xFF, 0, 0, 0, 0]
        else:
            dac = bcd(v, 6 if vt == 4 else 14)
        m.memory[0xF663] = vt
        m.set_memory_block(0xF7F6, bytes(dac))
        call("dacang")
        exp = g3ref.dacang(vt, dac)
        check("dacang %r" % v, m.de == exp, (v, m.de, exp))
        if vt != 2:                     # the value as BASIC holds it
            from decimal import Decimal
            digs = "".join("%02x" % b for b in dac[1:])[:6 if vt == 4 else 14]
            val = Decimal(int(digs)) * Decimal(10) ** ((dac[0] & 0x7F) - 64 - len(digs)) if dac[0] else Decimal(0)
            val = -val if dac[0] & 0x80 else val
            ideal = float((val % 360) * 65536 / 360)
        else:
            ideal = (v % 360) * 65536 / 360
        dd = (m.de - ideal) % 65536
        check("dacang ideal %r" % v, min(dd, 65536 - dd) <= 0.51, (v, m.de, ideal))

    for k, ts in tstat.items():
        print("  info: %s: %d T-states (mean of %d), %.2f ms at 3.58 MHz" % (k, sum(ts) / len(ts), len(ts), sum(ts) / len(ts) / 3579.545))
    print("test_math: %d checks, %d failed" % (checks, fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
