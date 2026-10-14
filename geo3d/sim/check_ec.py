#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""check_ec.py RTL_EC MODEL_EC: the producer's clocks of the RTL (tb_engine.v
+ec=<file>) against those of an emulator's model of the engine (blue_replay).

Both files have a line per command ("C <clock>"), a line when the producer
reaches R_DRAIN ("E <clock>") and the frame markers ("F <n>"). The two count
from different points, so the clocks may differ by a constant: the check is
that the lines are the same ones and the difference is the same on every
line. Prints the difference found; exit 1 when it is not constant.
"""
import sys

a = [l.split() for l in open(sys.argv[1]) if l.strip()]
b = [l.split() for l in open(sys.argv[2]) if l.strip()]
if len(a) != len(b):
    sys.exit("FAIL: %d lines in the RTL's file, %d in the model's" % (len(a), len(b)))
diffs = {}
first = {}
for n, (x, y) in enumerate(zip(a, b)):
    if x[0] != y[0]:
        sys.exit("FAIL: line %d is %s in the RTL's file and %s in the model's" % (n + 1, x[0], y[0]))
    if x[0] == "F":
        if x[1] != y[1]:
            sys.exit("FAIL: line %d: frame %s against %s" % (n + 1, x[1], y[1]))
        continue
    d = int(x[1]) - int(y[1])
    diffs[d] = diffs.get(d, 0) + 1
    first.setdefault(d, n + 1)
cmds = sum(1 for x in a if x[0] == "C")
runs = sum(1 for x in a if x[0] == "E")
if len(diffs) == 1:
    print("PASS: %d commands and %d RUN ends, RTL clock - model clock = %d on every line" % (cmds, runs, list(diffs)[0]))
else:
    print("FAIL: RTL clock - model clock is not constant: " +
          ", ".join("%d on %d lines (first: line %d)" % (d, c, first[d]) for d, c in sorted(diffs.items(), key=lambda kv: -kv[1])[:6]))
    sys.exit(1)
