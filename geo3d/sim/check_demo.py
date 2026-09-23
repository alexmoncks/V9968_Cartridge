#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Expected log for a captured Z80 stimulus (e.g. demo_stim.txt)."""
import sys
from gen_scenes import replay
ops = [l.strip() for l in open(sys.argv[1]) if l.strip()]
totals = {"draw": 0, "skip": 0, "cull": 0}
log = replay(ops, totals)
open(sys.argv[2], "w").write("\n".join(log) + "\n")
print(f"esperado: {totals['draw']} LINEs, {totals['skip']} skip, {totals['cull']} cull")
