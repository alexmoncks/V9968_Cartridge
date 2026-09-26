#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""g3viewer.py: wraps a model written by glb2g3.py (<name>.bas: the palette
subroutine and the G3DATA lines) into a complete MSX-BASIC viewer for the
geo3d BASIC ROM (G3BASIC.ROM, docs/BASIC_API.md):

  - SCREEN 5 (MSX2 and later; an MSX1 with the 88h cartridge goes straight
    to CALL G3INIT(5)), G3INIT, the model's palette, a "Loading..." line
    while G3DATA reads the model (option 0: the faces are already turned
    outwards), then object 1 = model 16;
  - a half turn to face the viewer (G3SPIN, 30 frames of 6 degrees, drawn
    by the main loop so they cost what its frames cost), timed with JIFFY:
    K = the degrees per frame that make about 120 degrees a second on this
    machine (2 on a turbo R at 60 frames per second, 6 on a Z80 MSX at 20);
  - a loop: a cursor key held (STICK(0), diagonals too) sets a spin of K
    degrees per frame (G3SPIN, only when the keys change: a CALL costs a
    Z80 MSX about 17 ms, so BASIC does not recompute angles every frame).
    The front of the figure follows the key: right turns it to the right,
    up tilts it up (we see it from below), on both axes. G3FRAME draws a
    frame. SPACE shows the front again; ESC ends with G3END and SCREEN 0.
    So does CTRL+STOP: BASIC traps it (ON STOP), or the ROM, when it comes
    during one of its waits, turns it into Device I/O error (error 19, spec
    7.2), which ON ERROR takes to the same end; another error in the loop
    is printed after SCREEN 0.

Turntable: the spec's rotation is R = Ry(ay) * Rx(ax) * Rz(az), so a pitch
(ax) is about the model's own X axis once it has turned about Y, and seen
from the side it would roll the figure in the screen plane. The viewer
therefore stores the figure turned on its back: up along +Z, front along
+Y (glb2g3's X right, Y up, front +Z become (-x, z, y), a rotation, so the
faces keep their winding). Then Rz(az) turns the figure about its own
vertical axis (the heading) and Rx(ax) tilts it about the screen's
horizontal axis, whatever the heading: ax = 90 stands it up, facing the
camera at az = 0; ay stays 0. BASIC keeps the tilt in 0..180 (seen from
below .. seen from above) with one integer add per frame while a tilt key
is held, and stops the tilt spin at the ends (G3ROT(1,T): ax only).

Writes <OUT> (ASCII, CRLF, an 8.3 name such as ALEX.BAS) and, with
--autoexec, AUTOEXEC.BAS next to it (10 RUN "<OUT>"), so a disk boots
straight into the viewer.

Usage: g3viewer.py out/alex/alex.bas -o disk/ALEX.BAS --autoexec
       [--title "Alex in 3D"]
"""
import argparse
import os
import re
import sys

PROGRAM = [
    "10 ' {name}: {title} on the geo3d BASIC ROM (G3BASIC.ROM)",
    "20 ' Cursor keys turn it (hold them; the front follows the key), SPACE: the front, ESC ends.",
    "30 ' Model: {nv} vertices, {nf} faces, lines {first}- (glb2g3.py), stored by g3viewer.py"
    " with its up along +Z and its front along +Y: ax tilts it, az turns it",
    "40 DEFINT A-Z:DIM V(8),H(8):RESTORE 910:FOR I=1 TO 8:READ V(I),H(I):NEXT:E$=CHR$(27)"
    ":ON STOP GOSUB 160:STOP ON",
    "50 IF PEEK(&H2D) THEN SCREEN 5",
    "60 CALL G3INIT(5):GOSUB {gosub}",
    "70 IF PEEK(&H2D) THEN OPEN \"GRP:\" AS #1:SET PAGE 0,0:PRESET (88,100):PRINT #1,\"Loading...\":CLOSE #1",
    "80 RESTORE {restore}:CALL G3DATA(16,0):CALL G3OBJ(1,16)",
    "90 ' a half turn to face us, timed: K = degrees per frame for 120 a second",
    "100 CALL G3ROT(1,90,0,180):CALL G3SPIN(1,0,0,6):J=PEEK(&HFC9E):CALL G3FRAME(1):N=29:O=0:ON ERROR GOTO 160",
    "110 ' a key held = a spin of K degrees per frame; the tilt T (ax) stays in 0-180",
    "120 S=STICK(0):IF S<>O THEN GOSUB 300",
    "130 IF X THEN GOSUB 320",
    "140 IF STRIG(0) THEN GOSUB 210",
    "150 CALL G3FRAME:IF N THEN GOSUB 220",
    "155 IF INKEY$<>E$ THEN 120",
    "160 STOP OFF:CALL G3END:SCREEN 0:IF ERR<>0 AND ERR<>19 THEN PRINT \"Error\";ERR;\"in\";ERL",
    "170 IF INKEY$<>\"\" THEN 170 ELSE END",
    "200 ' the front again, still",
    "210 CALL G3SPIN(1,0,0,0):CALL G3ROT(1,90,0,0):T=90:X=0:O=-1:RETURN",
    "220 N=N-1:IF N THEN RETURN ELSE D=(PEEK(&HFC9E)-J) AND 255:K=(2*D+15)\\30:IF K<1 THEN K=1 ELSE IF K>9 THEN K=9",
    "230 GOTO 210",
    "300 O=S:X=V(S)*K:Z=H(S)*K:CALL G3SPIN(1,X,0,Z):RETURN",
    "320 T=T+X:IF T<0 OR T>180 THEN 340",
    "330 RETURN",
    "340 T=-180*(T>0):X=0:CALL G3SPIN(1,0,0,Z):CALL G3ROT(1,T):RETURN",
    "900 ' STICK 1-8 (up, up-right, ... up-left): tilt (up: the front goes up), turn (right: it goes right)",
    "910 DATA -1,0, -1,1, 0,1, 1,1, 1,0, 1,-1, 0,-1, -1,-1",
]
MAX_LINE = 160                          # characters of a DATA line, as glb2g3.py writes them


def dos_name(path):
    base = os.path.basename(path)
    stem, _, ext = base.partition(".")
    ok = re.fullmatch(r"[A-Z0-9_\-]{1,8}", stem) and re.fullmatch(r"[A-Z0-9_\-]{0,3}", ext)
    if not ok:
        sys.exit(f"g3viewer.py: {base} is not an upper-case 8.3 name")
    return base


def turntable(lines, restore):
    """The model's DATA lines (from line <restore> on) with every vertex
    (x, y, z) stored as (-x, z, y): up +Z, front +Y. Same line numbers from
    <restore>, same step, lines of at most MAX_LINE characters."""
    head = [l for l in lines if int(l.split(" ", 1)[0]) < restore]
    data = [l for l in lines if int(l.split(" ", 1)[0]) >= restore]
    nums = []
    for l in data:
        m = re.fullmatch(r"(\d+) DATA (.*)", l)
        if not m:
            sys.exit(f"g3viewer.py: not a DATA line: {l[:40]}...")
        nums += [int(v) for v in m.group(2).split(",")]
    nv, nf, ne, t = nums[:4]
    if len(nums) != 4 + 3 * nv + 5 * nf + 2 * ne + 8 * nf * t:
        sys.exit("g3viewer.py: the DATA lines do not hold nv, nf, ne, t and the model")
    for i in range(nv):
        x, y, z = nums[4 + 3 * i:7 + 3 * i]
        nums[4 + 3 * i:7 + 3 * i] = [-x, z, y]
    step = int(data[1].split(" ", 1)[0]) - int(data[0].split(" ", 1)[0]) if len(data) > 1 else 10
    out = ["%d DATA %d,%d,%d,%d" % (restore, nv, nf, ne, t)]
    ln, cur = restore + step, []
    for v in nums[4:]:
        if cur and len("%d DATA %s" % (ln, ",".join(cur + [str(v)]))) > MAX_LINE:
            out.append("%d DATA %s" % (ln, ",".join(cur)))
            ln, cur = ln + step, []
        cur.append(str(v))
    if cur:
        out.append("%d DATA %s" % (ln, ",".join(cur)))
    return head + out


def main(argv=None):
    ap = argparse.ArgumentParser(description="MSX-BASIC viewer around a glb2g3.py model")
    ap.add_argument("model", help="<name>.bas written by glb2g3.py")
    ap.add_argument("-o", "--out", required=True, help="the program to write (8.3 name, e.g. ALEX.BAS)")
    ap.add_argument("--autoexec", action="store_true", help="also write AUTOEXEC.BAS next to it")
    ap.add_argument("--title", default=None, help="words for the first REM line")
    args = ap.parse_args(argv)

    text = open(args.model, "rb").read().decode("ascii").replace("\x1a", "")
    lines = [l for l in text.splitlines() if l.strip()]
    head = re.match(r"(\d+) ' (\S+): (\d+) vertices, (\d+) faces .*GOSUB (\d+).*RESTORE (\d+)", lines[0])
    if not head:
        sys.exit("g3viewer.py: the first line is not a glb2g3.py header")
    first, name, nv, nf, gosub, restore = head.groups()
    if int(first) <= 910:
        sys.exit("g3viewer.py: the model lines must start after line 910")
    lines = turntable(lines, int(restore))
    out_name = dos_name(args.out)
    title = args.title or f"the model {name}"
    prog = [l.format(name=out_name, title=title, nv=nv, nf=nf, first=first, gosub=gosub,
                     restore=restore) for l in PROGRAM]
    for l in prog + lines:
        if len(l) > 250:
            sys.exit(f"g3viewer.py: line too long: {l[:40]}...")
        if any(not 32 <= ord(ch) < 127 for ch in l):
            sys.exit(f"g3viewer.py: a control character in: {l[:40]}...")
    body = "\r\n".join(prog + lines) + "\r\n"
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "wb") as fh:
        fh.write(body.encode("ascii") + b"\x1a")
    print(f"g3viewer.py: {args.out}: {len(prog) + len(lines)} lines, {len(body)} bytes")
    if args.autoexec:
        auto = os.path.join(os.path.dirname(os.path.abspath(args.out)), "AUTOEXEC.BAS")
        with open(auto, "wb") as fh:
            fh.write(f'10 RUN "{out_name}"\r\n'.encode("ascii") + b"\x1a")
        print(f"g3viewer.py: {auto}")


if __name__ == "__main__":
    main()
