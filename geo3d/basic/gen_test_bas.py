#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Writes the data-heavy test programs of run_tests.sh (CRLF, MSX ASCII):

disk/G3MEM.BAS   the model area filled up: three models of 8448 bytes each
                 (255 vertices, 255 faces with UV, 1020 edges made from the
                 faces), a fourth that runs out of room while its edges are
                 made, a 2 KB model, a header that does not fit at all, and
                 a redefinition that moves 20 KB of models down
disk/G3PERF.BAS  a torus of 255 vertices and 255 quads (faces turned
                 outwards here, G3DATA option 0): wireframe in two passes
                 of edges, solid, and the frame rate of a spinning torus

Usage: gen_test_bas.py (writes both files into disk/).
"""
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
HDR = ["1 ' SPDX-License-Identifier: MIT", "2 ' Copyright (c) 2026 Alex Moncks"]
ERR = "9000 E=ERR:EL=ERL:OUT &H2F,ERR:OUT &H2F,ERL AND 255:OUT &H2F,ERL\\256:OUT &H2E,250:RESUME NEXT"
WAIT = ["9200 T=TIME", "9210 IF TIME-T<5 THEN 9210", "9220 RETURN"]


def data_lines(first, values, per):
    out, ln = [], first
    for k in range(0, len(values), per):
        out.append("%d DATA %s" % (ln, ",".join(str(v) for v in values[k:k + per])))
        ln += 1
    return out, ln


def write(name, lines):
    with open(os.path.join(HERE, "disk", name), "wb") as f:
        f.write(("\r\n".join(lines) + "\r\n").encode("ascii"))


def mem():
    lines = HDR + [
        "10 ' the model area full (Out of memory), and a redefinition that moves 20 KB",
        "20 ON ERROR GOTO 9000:MV=PEEK(&H2D):IF MV=3 THEN P=&H98:SCREEN 5 ELSE P=&H88",
        "30 E=0:CALL G3INIT:OUT &H2F,E:OUT &H2E,200",
        "40 FOR M=16 TO 18:RESTORE 1000:E=0:CALL G3DATA(M,0):OUT &H2F,E:NEXT:OUT &H2E,201",
        "50 RESTORE 1000:E=0:CALL G3DATA(19,0):OUT &H2F,E:OUT &H2E,202",
        "60 RESTORE 999:E=0:CALL G3DATA(19,0):OUT &H2F,E:OUT &H2E,203",
        "70 RESTORE 990:E=0:CALL G3DATA(20):OUT &H2F,E:OUT &H2E,204",
        "80 RESTORE 999:E=0:CALL G3DATA(16,0):OUT &H2F,E:OUT &H2E,205",
        "90 E=0:CALL G3END:OUT &H2F,E:SCREEN 0:OUT &H2E,206",
        "100 RUN \"G3PERF.BAS\"",
        "990 DATA 255,255,255,1",
        "999 DATA 255,0,255,0",
        "1000 DATA 255,255,0,1",
    ]
    z, _ = data_lines(1001, [0] * 765, 60)
    faces = []
    for i in range(255):
        faces += [i, (i + 1) % 255, (i + 3) % 255, (i + 7) % 255, 0]
    f, _ = data_lines(1100, faces, 40)
    u, _ = data_lines(1200, [0] * 2040, 60)
    write("G3MEM.BAS", lines + z + f + u + [ERR])


def torus():
    nu, nv, big, small = 17, 15, 70, 28
    verts, faces = [], []
    for i in range(nu):
        a = 2 * math.pi * i / nu
        for j in range(nv):
            b = 2 * math.pi * j / nv
            verts.append((round((big + small * math.cos(b)) * math.cos(a)),
                          round(small * math.sin(b)),
                          round((big + small * math.cos(b)) * math.sin(a))))
    for i in range(nu):
        for j in range(nv):
            q = [i * nv + j, ((i + 1) % nu) * nv + j,
                 ((i + 1) % nu) * nv + (j + 1) % nv, i * nv + (j + 1) % nv]
            p = [verts[k] for k in q]
            a = [p[2][k] - p[0][k] for k in range(3)]
            b = [p[3][k] - p[1][k] for k in range(3)]
            n = [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
            # outwards: away from the centre of the tube at this face
            ac = 2 * math.pi * (i + 0.5) / nu
            c = (big * math.cos(ac), 0, big * math.sin(ac))
            m = [sum(pp[k] for pp in p) / 4 - c[k] for k in range(3)]
            if sum(n[k] * m[k] for k in range(3)) < 0:
                q = [q[0], q[3], q[2], q[1]]
            faces.append(q + [1 if (i + j) % 2 else 8])
    lines = HDR + [
        "10 ' a torus of 255 vertices and 255 faces: wireframe (two passes), solid, speed",
        "20 ON ERROR GOTO 9000:DEFINT A-Z:MV=PEEK(&H2D):IF MV=3 THEN P=&H98:SCREEN 5 ELSE P=&H88",
        "30 CALL G3INIT:T=TIME:RESTORE 1000:CALL G3DATA(16,0):T=TIME-T:OUT &H2F,T AND 255:OUT &H2F,T\\256:OUT &H2E,210",
        "40 CALL G3OBJ(1,16):CALL G3ROT(1,20,0,0):CALL G3STYLE(1,0):OUT &H2E,211",
        "50 CALL G3FRAME:OUT &H2E,212",
        "60 GOSUB 9200:CALL G3FRAME:OUT &H2E,213",
        "70 GOSUB 9200:CALL G3STYLE(1,1):CALL G3FRAME:OUT &H2E,214",
        "80 GOSUB 9200:AY=0:OUT &H2E,215",
        "90 FOR I=1 TO 60:AY=AY+3:CALL G3ROT(1,20,AY,0):CALL G3FRAME(1):NEXT:OUT &H2E,216",
        "100 FOR I=1 TO 60:S=STICK(0):IF S=3 THEN AY=AY+5 ELSE IF S=7 THEN AY=AY-5",
        "110 AY=AY+2:CALL G3ROT(1,AX,AY,0):CALL G3FRAME:NEXT:OUT &H2E,217",
        "120 IF MV=3 THEN GOSUB 8000:FOR I=1 TO 60:AY=AY+3:CALL G3ROT(1,20,AY,0):CALL G3FRAME(1):NEXT:OUT &H2E,218",
        "130 CALL G3OBJ(1,0):OUT &H2E,220:FOR I=1 TO 30:CALL G3FRAME(2):NEXT:OUT &H2E,221",
        "140 OUT &H2E,220:FOR I=1 TO 30:CALL G3FRAME(3):NEXT:OUT &H2E,222",
        "150 OUT &H2E,220:FOR I=1 TO 30:CALL G3FRAME(1):NEXT:OUT &H2E,223",
        "160 CALL G3END:SCREEN 0:OUT &H2E,199:END",
        "8000 ' turbo R: CHGCPU to the Z80",
        "8010 DIM U(3):A=VARPTR(U(0)):POKE A,&H3E:POKE A+1,&H80:POKE A+2,&HCD:POKE A+3,&H80:POKE A+4,1:POKE A+5,&HC9",
        "8020 DEFUSR=A:A=USR(0):GOSUB 9200:OUT &H2E,219:RETURN",
    ]
    d, _ = data_lines(1000, [len(verts), len(faces), 0, 0], 4)
    v, _ = data_lines(1001, [c for p in verts for c in p], 24)
    f, _ = data_lines(1100, [c for q in faces for c in q], 25)
    write("G3PERF.BAS", lines + d + v + f + [ERR] + WAIT)


if __name__ == "__main__":
    mem()
    torus()
