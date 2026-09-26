#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
#
# run_all.sh - the shooter's test matrix in openMSX (WSL), one run at a time
#   98h C-BIOS_V9968_JP + geo3d      smoke, collide, play (frame markers),
#                                    cross (steep bullets, bolts off the
#                                    bolt line, fast darts), demokey,
#                                    attract 5 minutes (captures, VRAM dump),
#                                    rounds (4 minutes of autopilot play,
#                                    the ship unhittable: 5 rounds)
#   98h Panasonic_FS-A1ST_V9968      smoke, collide, attract 2 minutes
#   88h FS-A1WSX + HRA_V9968 + geo3d88   smoke, collide, play, cross,
#                                    attract 5 min, rounds
#   OPENMSX_HW=<binary>: also collide, play, cross and rounds with an
#   openMSX whose V9968 follows the FPGA's sprite collision rule (colour-0
#   hitboxes collide): the hardware collision path
# Writes out/test/summary.txt: every check PASS or FAIL. Exit status 1 if
# any failed.
set -e
cd "$(dirname "$0")/.."
PY=${PY:-~/venv/bin/python}
bash build.sh > /dev/null
R=tests/run_tests.sh
run() { bash $R "$@" > /dev/null; }
run cbios smoke 10
run cbios collide 60
run cbios play 45 "" 1
run cbios cross 240
run cbios demokey 60
run cbios attract 300 "0:99999:150"
run cbios rounds 240 "0:99999:100"
run tr smoke 12
run tr collide 60
run tr attract 120 "0:99999:300"
run wsx smoke 12
run wsx collide 60
run wsx play 45 "" 1
run wsx cross 240
run wsx attract 300 "0:99999:150"
run wsx rounds 240 "0:99999:100"
dirs="out/test/cbios_smoke out/test/cbios_collide out/test/cbios_play out/test/cbios_cross
      out/test/cbios_demokey out/test/cbios_attract out/test/cbios_rounds out/test/tr_smoke
      out/test/tr_collide out/test/tr_attract out/test/wsx_smoke out/test/wsx_collide
      out/test/wsx_play out/test/wsx_cross out/test/wsx_attract out/test/wsx_rounds"
if [ -n "$OPENMSX_HW" ]; then
  TAG=hw OPENMSX=$OPENMSX_HW run cbios collide 60
  TAG=hw OPENMSX=$OPENMSX_HW run cbios play 45 "" 1
  TAG=hw OPENMSX=$OPENMSX_HW run cbios cross 240
  TAG=hw OPENMSX=$OPENMSX_HW run cbios rounds 240 "0:99999:100"
  dirs="$dirs out/test/cbios_collide_hw out/test/cbios_play_hw out/test/cbios_cross_hw
        out/test/cbios_rounds_hw"
fi
{
  echo "shooter tests $(date -u +%Y-%m-%dT%H:%MZ)"
  for d in $dirs; do
    case $d in
      *attract*) MAX_LATE=0.01 $PY tests/analyze.py verdict $d ;;
      *)         MAX_LATE=0.05 $PY tests/analyze.py verdict $d ;;
    esac
  done
} > out/test/summary.txt
cat out/test/summary.txt
! grep -q FAIL out/test/summary.txt
