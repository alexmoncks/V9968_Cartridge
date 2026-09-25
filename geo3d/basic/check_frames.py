#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Checks the dumps of the "cmds" run of run_tests.sh against the Python
reference: out/t_<machine>_cmds_<n>.st (work area + VRAM model area),
.pg (the page a G3FRAME drew) and .geo (every geo3d port write since the
scene began). Writes PASS/FAIL lines to out/t_<machine>_py.txt.

  models   the model area (VRAM page 7) and the directory, byte for byte,
           against g3ref.Model of the same DATA lines (the ROM's G3DATA:
           corner order, orientation fix, normals, UV order, edges made from
           the faces, compaction when a model is defined again)
  camera   the camera matrix and the light in camera space (g3ref.camset,
           and the float ideal within 8/16384)
  frames   the page, pixel for pixel, against three renders:
           1. reference: the scene (objects, angles, camera, models) through
              g3ref (the ROM's integer math) and sim/gen_scenes.py (geo3d),
              rasterised as the VDP draws its LINE commands
           2. replay: the geo3d traffic the ROM sent, run through the same
              geo3d model (the emulator's drawing against the reference);
              its registers per RUN (M, T, light, counts, YPAGE, CTRL) must
              be the reference's
           3. ideal: floating-point rotation and camera (rounded to Q2.14),
              within a small number of pixels
Usage: check_frames.py machine... (in geo3d/basic).
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "sim"))

import g3ref  # noqa: E402
from gen_scenes import render as render_edges, render_faces  # noqa: E402

CMD = os.path.join(HERE, "disk", "G3CMD.BAS")
MEM = os.path.join(HERE, "disk", "G3MEM.BAS")
PERF = os.path.join(HERE, "disk", "G3PERF.BAS")
U = g3ref.deg_units


def model(path, line, o=1, ramps=(1, 8)):
    return g3ref.Model(g3ref.read_data(path, line), o, ramps)


def cmd_models(ramps=(1, 8)):
    return {
        16: model(CMD, 6000, 1, ramps),
        17: model(CMD, 6500, 1, ramps),
        18: model(CMD, 6100, 1, ramps),
        19: model(CMD, 6200, 2, ramps),
        20: model(CMD, 6300, 1, ramps),
        21: model(CMD, 6400, 1, ramps),
        22: model(CMD, 6600, 1, ramps),
    }


def layout(order, models):
    return [(m, models[m]) for m in order]


# ---- expected model areas (layout order), per check point ------------------
def states():
    c = cmd_models()
    first = dict(c)
    first[17] = model(CMD, 6100, 0)
    s = {
        138: [(16, c[16])],
        139: layout([16, 17, 18, 19, 20, 21], first),
        140: layout([16, 18, 19, 20, 21, 17], c),
        169: layout([16, 18, 19, 20, 21, 17, 22], c),
        150: layout([16, 18, 19, 20, 21, 17, 22], cmd_models((2,))),
        151: layout([16, 18, 19, 20, 21, 17, 22], cmd_models((2, 9))),
        152: layout([16, 18, 19, 20, 21, 17, 22], c),
    }
    big = model(MEM, 1000, 0)
    edg = model(MEM, 999, 0)
    s[201] = [(16, big), (17, big), (18, big)]
    s[203] = s[201] + [(19, edg)]
    s[205] = [(17, big), (18, big), (19, edg), (16, edg)]
    s[210] = [(16, model(PERF, 1000, 0))]
    return s


# ---- expected frames ----------------------------------------------------------
def ob(m, pos, ang, style):
    return dict(model=m, pos=pos, ang=ang, style=style)


def frames():
    a45 = U(45)
    ay2 = (a45 + 2 * U(10)) & 0xFFFF
    cube = [U(30), ay2, 0]
    sp = [U(5), U(7), U(3)]
    f = {
        154: dict(objs={1: ob(16, (0, 0, 0), [U(30), a45, 0], 1)}),
        156: dict(objs={1: ob(16, (0, 0, 0), [U(30), a45, 0], 0)}),
        158: dict(objs={1: ob(16, (0, 0, 0), cube, 1)}),
    }
    two = {1: ob(16, (0, 0, 0), cube, 1), 2: ob(17, (120, 0, 50), [0, U(30), 0], 1)}
    cam = (100, 80, -350)
    f[160] = dict(objs=dict(two), cam=cam)
    three = dict(two)
    three[3] = ob(19, (0, -60, 100), [0, 0, 0], 0)
    f[161] = dict(objs=dict(three), cam=cam)
    four = dict(two)
    four[3] = ob(19, (0, 0, 32000), [0, 0, 0], 0)
    four[4] = ob(20, (-100, 40, 0), list(sp), 1)
    f[162] = dict(objs=dict(four), cam=cam)
    five = dict(four)
    five[4] = ob(20, (-100, 40, 0), [(2 * x) & 0xFFFF for x in sp], 1)
    five[5] = ob(21, (0, 0, -200), [0, 0, 0], 0)
    f[163] = dict(objs=five, cam=cam)
    f[164] = dict(objs={1: ob(16, (0, 0, 0), cube, 1)})
    # G3RAMP between two frames of a resident model: its faces go to geo3d
    # again, with the normals of the new ramp set (colour 8 flat at 173)
    f[173] = dict(objs={1: ob(16, (0, 0, 0), cube, 1)}, models=cmd_models((1, 9)))
    f[174] = dict(objs={1: ob(16, (0, 0, 0), cube, 1)})
    # corners in the order given (G3DATA option 0): the left square runs
    # clockwise seen from the camera (drawn, colour 15), the right one
    # counter-clockwise (culled); turned 180 degrees, the right square
    # (colour 14) is the one seen from the front, on the left of the screen
    sq = {23: model(CMD, 6700, 0)}
    f[177] = dict(objs={1: ob(23, (0, 0, 0), [0, 0, 0], 1)}, models=sq,
                  present=[(15, 0, 128, 2000)], absent=[14])
    f[178] = dict(objs={1: ob(23, (0, 0, 0), [0, U(180), 0], 1)}, models=sq,
                  present=[(14, 0, 128, 2000)], absent=[15])
    tor = {16: model(PERF, 1000, 0)}
    f[212] = dict(objs={1: ob(16, (0, 0, 0), [U(20), 0, 0], 0)}, models=tor)
    f[213] = dict(objs={1: ob(16, (0, 0, 0), [U(20), 0, 0], 0)}, models=tor)
    f[214] = dict(objs={1: ob(16, (0, 0, 0), [U(20), 0, 0], 1)}, models=tor)
    return f


# ---- geo3d register model (the RTL's register window, as sim/gen_scenes) --------
class Geo:
    def __init__(self):
        self.widx, self.lo = 0, 0
        self.cfg = [0] * 12 + [256, 128, 106, 16, 256, 212]
        self.vaddr = self.eaddr = self.nvert = self.nedge = self.faddr = self.nface = 0
        self.color, self.lop, self.ypage = 15, 0, 0
        self.light = [0, 0, -16384]
        self.vmem = [(0, 0, 0)] * 256
        self.emem = [(0, 0)] * 256
        self.fmem = [(0, 0, 0, 0, 0, 0, 0, 0)] * 256
        self.buf = []
        self.runs = []

    def write(self, sel, b, frame):
        s16 = g3ref.s16
        if sel == 0:
            self.widx, self.buf = b, []
            return
        i = self.widx
        if i < 0x2A:
            if i % 2 == 0:
                self.lo = b
            elif i >> 1 < 18:
                w = (b << 8) | self.lo
                self.cfg[i >> 1] = w if i >> 1 == 12 else s16(w)
        elif i == 0x40:
            self.vaddr = b
        elif i == 0x41:
            self.eaddr = b
        elif i == 0x42:
            self.nvert = b
        elif i == 0x43:
            self.nedge = b
        elif i == 0x44:
            self.color = b
        elif i == 0x45:
            self.lop = b & 15
        elif i == 0x46:
            self.ypage = (self.ypage & 0x700) | b
        elif i == 0x47:
            self.ypage = (self.ypage & 0xFF) | ((b & 7) << 8)
        elif i == 0x48 and b & 1:
            self.run(b, frame)
        elif i == 0x50:
            self.buf.append(b)
            if len(self.buf) == 6:
                v = self.buf
                self.vmem[self.vaddr] = tuple(s16(v[2 * k] | v[2 * k + 1] << 8) for k in range(3))
                self.vaddr, self.buf = (self.vaddr + 1) & 255, []
        elif i == 0x51:
            self.buf.append(b)
            if len(self.buf) == 2:
                self.emem[self.eaddr] = tuple(self.buf)
                self.eaddr, self.buf = (self.eaddr + 1) & 255, []
        elif i == 0x52:
            self.buf.append(b)
            if len(self.buf) == 11:
                f = self.buf
                self.fmem[self.faddr] = (f[0], f[1], f[2], f[3], s16(f[4] | f[5] << 8),
                                         s16(f[6] | f[7] << 8), s16(f[8] | f[9] << 8), f[10])
                self.faddr, self.buf = (self.faddr + 1) & 255, []
        elif i == 0x58:
            self.faddr = b
        elif i == 0x59:
            self.nface = b
        elif i in (0x5A, 0x5C, 0x5E):
            self.lo = b
        elif i in (0x5B, 0x5D, 0x5F):
            self.light[(i - 0x5B) // 2] = s16((b << 8) | self.lo)
        if i >> 4 == 4:
            self.widx = 0x40 | ((i + 1) & 0xF)
        elif i >> 3 == 0x0B:
            self.widx = 0x58 | ((i + 1) & 7)
        elif i >> 3 == 0x0C:
            self.widx = 0x60 | ((i + 1) & 7)
        elif i == 0x29:
            self.widx = 0x24
        elif i < 0x40:
            self.widx = i + 1

    def run(self, ctrl, frame):
        verts = self.vmem[:self.nvert]
        if ctrl & 2:
            cmds = render_faces(self.cfg, verts, self.fmem[:self.nface], self.lop, self.ypage, self.light)[0]
        else:
            cmds = render_edges(self.cfg, verts, self.emem[:self.nedge], self.color, self.lop, self.ypage)[0]
        self.runs.append(dict(frame=frame, ctrl=ctrl, m=self.cfg[:9], t=self.cfg[9:12], light=list(self.light),
                              nvert=self.nvert, nedge=self.nedge, nface=self.nface, color=self.color,
                              ypage=self.ypage, cmds=cmds))


def replay(path):
    g = Geo()
    frame = -1
    page = None
    for line in open(path):
        p = line.split()
        if not p:
            continue
        if p[0] == "P":
            page = int(p[1])
        elif p[0] == "F":
            frame += 1
        else:
            g.write(int(p[1]), int(p[2]), frame)
    return page, [r for r in g.runs if r["frame"] == frame]


# ---- the reference scene --------------------------------------------------------
def scene_runs(spec, models, page, ideal=False):
    """Expected RUNs (dicts like Geo.run's) of a G3FRAME of the scene."""
    cam = spec.get("cam", (0, 0, -300))
    c, cami, lc = g3ref.camset(cam, (0, 0, 0), (-1, 1, -1))
    if ideal:
        import math
        fc = g3ref.float_cam(cam, (0, 0, 0))
        c = [round(16384 * x) for x in fc]
        ln = math.sqrt(3)
        lf = [-1 / ln, 1 / ln, -1 / ln]
        lc = [round(16384 * sum(fc[3 * i + k] * lf[k] for k in range(3))) for i in range(3)]
        cami = 0
    vis = []
    for n in sorted(spec["objs"]):
        o = spec["objs"][n]
        if o["model"] == 0:
            continue
        md = models[o["model"]]
        d = [o["pos"][i] - cam[i] for i in range(3)]
        if any(abs(x) > 32767 for x in d):
            continue
        t = d if cami else g3ref.matvec(c, d)[0]
        if any(abs(x) > 30000 for x in t):
            continue
        key = 32767 if md.o & 2 else t[2]
        vis.append((key, n, o, md, t))
    vis.sort(key=lambda e: (-e[0], e[1]))
    runs = []
    for key, n, o, md, t in vis:
        if ideal:
            fr = g3ref.float_rot(*(a * 360 / 65536 for a in o["ang"]))
            r = [round(16384 * x) for x in fr]
            m = [round(sum(c[3 * i + k] * r[3 * k + j] for k in range(3)) / 16384) for i in range(3) for j in range(3)]
        else:
            r = g3ref.rotmat(*o["ang"])
            m = r if cami else g3ref.matmul(c, r)
        passes, _ = g3ref.render(md, m, t, lc, o["style"], page)
        solid = o["style"] == 1 and md.nf
        col = md.col + 6 if md.col in md.ramps else md.col
        for k, cmds in enumerate(passes):
            nedge = 0 if solid else min(255, len(md.edges) - 255 * k)
            runs.append(dict(ctrl=3 if solid else 1, m=m, t=t, light=lc, nvert=md.nv, nedge=nedge,
                             nface=md.nf, color=col, ypage=page * 256, cmds=cmds, obj=n))
    return runs


# ---- checks ---------------------------------------------------------------------
class Out:
    def __init__(self, cfg):
        self.cfg, self.lines, self.npass, self.nfail = cfg, [], 0, 0

    def check(self, name, ok, detail=""):
        if ok:
            self.npass += 1
            self.lines.append("PASS %s: %s" % (self.cfg, name))
        else:
            self.nfail += 1
            self.lines.append("FAIL %s: %s (%s)" % (self.cfg, name, detail))

    def info(self, s):
        self.lines.append("  info %s: %s" % (self.cfg, s))


def pixdiff(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            n += ((x ^ y) & 0xF0 != 0) + ((x ^ y) & 0x0F != 0)
    return n


def check_state(o, n, data, exp):
    work, area = data[:2048], data[2048:]
    off = 0
    used = set()
    for m, md in exp:
        img = md.vram()
        got = area[off:off + len(img)]
        o.check("st %d: model %d in VRAM at offset %d (%d bytes: %d v, %d f, %d edges%s)"
                % (n, m, off, len(img), md.nv, md.nf, len(md.edges), ", UV" if md.t else ""),
                got == img, first_diff(got, img, off))
        d = work[1152 + 16 * (m - 16):1152 + 16 * (m - 16) + 11]
        flags = 1 | (md.t << 1) | (md.derived << 2) | ((md.o & 2) << 2)
        want = struct.pack("<BBBBHHHB", flags, md.nv, md.nf, md.col, len(md.edges), off, md.size(), md.k)
        o.check("st %d: model %d directory (flags %d, offset %d, size %d)" % (n, m, flags, off, md.size()),
                d == want, "%s instead of %s" % (d.hex(), want.hex()))
        off += md.size()
        used.add(m)
    others = [m for m in range(16, 32) if m not in used and work[1152 + 16 * (m - 16)] & 1]
    o.check("st %d: no other model defined" % n, not others, others)
    mend = struct.unpack("<H", work[922:924])[0]
    o.check("st %d: model area in use: %d bytes" % (n, off), mend == off, mend)


def first_diff(a, b, base):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return "first difference at offset %d: %02X instead of %02X" % (base + i, x, y)
    return "lengths %d, %d" % (len(a), len(b))


def check_camera(o, n, data):
    work = data[:2048]
    cam = struct.unpack("<3h", work[24:30])
    c = list(struct.unpack("<9h", work[896:914]))
    lc = list(struct.unpack("<3h", work[914:920]))
    cami = work[1013]
    ec, ecami, elc = g3ref.camset(cam, (0, 0, 0), (-1, 1, -1))
    o.check("st %d: camera %s: matrix, identity flag, light = the reference" % (n, cam),
            (c, cami, lc) == (ec, ecami, elc), (c, cami, lc, ec, ecami, elc))
    fc = g3ref.float_cam(cam, (0, 0, 0))
    o.check("st %d: camera matrix within 8/16384 of the ideal" % n,
            all(abs(c[i] - 16384 * fc[i]) <= 8 for i in range(9)), [(c[i], round(16384 * fc[i])) for i in range(9)])


def check_frame(o, n, base, spec, models):
    page_bytes = open(base + ".pg", "rb").read()
    pg, got_runs = replay(base + ".geo")
    exp_runs = scene_runs(spec, models, pg)
    ref = g3ref.raster([r["cmds"] for r in exp_runs], pg)
    d = pixdiff(ref, page_bytes)
    drawn = sum((b >> 4 != 0) + (b & 15 != 0) for b in ref)
    o.check("frame %d: page %d = the reference render (%d objects, %d RUNs, %d pixels drawn), pixel for pixel"
            % (n, pg, len(spec["objs"]), len(exp_runs), drawn), d == 0, "%d pixels differ" % d)
    rep = g3ref.raster([r["cmds"] for r in got_runs], pg)
    d2 = pixdiff(rep, page_bytes)
    o.check("frame %d: the ROM's geo3d traffic replayed through the geo3d model = the page" % n, d2 == 0,
            "%d pixels differ" % d2)
    keys = ("ctrl", "m", "t", "light", "nvert", "nface", "ypage")

    def sig(r):
        v = [r[k] for k in keys]
        if not r["ctrl"] & 2:
            v += [r["nedge"], r["color"]]
        return v
    # wireframe passes may come in another order (the one geo3d holds first)
    gs = sorted([sig(r) for r in got_runs], key=repr)
    es = sorted([sig(r) for r in exp_runs], key=repr)
    o.check("frame %d: registers of the %d RUNs (CTRL, M, T, light, NVERT, NFACE, YPAGE, NEDGE, COLOR)"
            % (n, len(exp_runs)), gs == es, "got %s, expected %s" % (gs[:2], es[:2]))
    ideal = g3ref.raster([r["cmds"] for r in scene_runs(spec, models, pg, ideal=True)], pg)
    d3 = pixdiff(ideal, page_bytes)
    lim = 20 + drawn // 50
    o.check("frame %d: within %d pixels of the float render (%d differ)" % (n, lim, d3), d3 <= lim, d3)
    pix = [c for b in page_bytes for c in (b >> 4, b & 15)]
    for col, x0, x1, least in spec.get("present", []):
        k = sum(1 for i, c in enumerate(pix) if c == col and x0 <= i % 256 < x1)
        o.check("frame %d: colour %d drawn in columns %d-%d (%d pixels, at least %d)" % (n, col, x0, x1 - 1, k, least),
                k >= least, k)
    for col in spec.get("absent", []):
        k = pix.count(col)
        o.check("frame %d: colour %d nowhere (its face culled)" % (n, col), k == 0, "%d pixels" % k)


def main():
    names = sys.argv[1:] or ["turbor", "msx2p", "msx2", "msx1"]
    st = states()
    fr = frames()
    cmdm = cmd_models()
    for cfg in names:
        o = Out(cfg)
        pre = os.path.join(HERE, "out", "t_%s_cmds_" % cfg)
        try:
            for n in sorted(st):
                p = pre + "%d.st" % n
                if not os.path.exists(p):
                    o.check("st %d: dump" % n, False, "no " + p)
                    continue
                data = open(p, "rb").read()
                check_state(o, n, data, st[n])
            p = pre + "148.st"
            if os.path.exists(p):
                check_camera(o, 148, open(p, "rb").read())
            else:
                o.check("st 148: dump", False, "no " + p)
            for n in sorted(fr):
                if not os.path.exists(pre + "%d.pg" % n):
                    o.check("frame %d: dump" % n, False, "no " + pre + "%d.pg" % n)
                    continue
                check_frame(o, n, pre + str(n), fr[n], fr[n].get("models", cmdm))
            tp = pre + "times"
            if os.path.exists(tp):
                for line in open(tp):
                    o.info("speed: " + line.strip())
        except Exception as e:  # a crash is a failure, with its reason
            import traceback
            o.check("check_frames.py ran to the end", False, traceback.format_exc().replace("\n", " | "))
        o.lines.append("DONE %s py: %d passed, %d failed" % (cfg, o.npass, o.nfail))
        with open(os.path.join(HERE, "out", "t_%s_py.txt" % cfg), "w") as f:
            f.write("\n".join(o.lines) + "\n")


if __name__ == "__main__":
    main()
