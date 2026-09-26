#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
#
# run_tests.sh - runs the shooter ROM in openMSX (WSL, the V9968 + geo3d fork)
# Usage: run_tests.sh <machine> <scenario> [limit_s] [captures] [fmark]
#   machine   cbios  98h: C-BIOS_V9968_JP -ext geo3d
#             tr     98h: Panasonic_FS-A1ST_V9968 -ext geo3d (turbo R BIOS)
#             wsx    88h: Panasonic_FS-A1WSX -ext HRA_V9968 -ext geo3d88
#   scenario  smoke, play, collide, attract (tests/game.tcl)
#   captures  flips to capture, "a:b:n,..." (every n-th flip in [a, b))
# Writes out/test/<machine>_<scenario>/ (log.txt, capture .bin files).
# At most one openMSX of its own (WSL has 7 GB; other sessions may run one).
# OPENMSX=... picks another binary (for example a patched build).
set -e
cd "$(dirname "$0")/.."
OPENMSX=${OPENMSX:-~/openMSX/derived/x86_64-linux-opt/bin/openmsx}
export OPENMSX_SYSTEM_DATA=${OPENMSX_SYSTEM_DATA:-~/openMSX/share}
export SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy
m=$1; scen=$2; limit=${3:-60}; caps=${4:-}; fmark=${5:-0}
case $m in
  cbios) args="-machine C-BIOS_V9968_JP -ext geo3d"; port=152; p=98 ;;
  tr)    args="-machine Panasonic_FS-A1ST_V9968 -ext geo3d"; port=152; p=98 ;;
  wsx)   args="-machine Panasonic_FS-A1WSX -ext HRA_V9968 -ext geo3d88"; port=136; p=88 ;;
  *) echo "machine: cbios, tr or wsx"; exit 2 ;;
esac
rom=out/GEO3D_SHOOTER_$p.ROM
[ -f "$rom" ] || { echo "build first (build.sh)"; exit 2; }
dir=out/test/${m}_${scen}${TAG:+_$TAG}
rm -rf "$dir"
mkdir -p "$dir"
# RAM addresses for the Tcl script, from the assembler's label list
awk -F'[:\t$ ]+' '/^[a-z_0-9]+:\tequ \$/ { printf "set ::A(%s) 0x%s\n", $1, $NF }' \
  out/labels_$p.txt > "$dir/labels.tcl"
while [ "$(pgrep -c -x openmsx || true)" -ge 2 ]; do sleep 5; done
OUT=$dir/log.txt CAPDIR=$dir LIMIT=$limit SCEN=$scen PORT=$port CAP=$caps FMARK=$fmark \
  LABELS=$dir/labels.tcl timeout 590 $OPENMSX $args -cart $rom -romtype ASCII16 \
  -script tests/game.tcl > "$dir/openmsx.txt" 2>&1 || true
grep -E "^E |^END" "$dir/log.txt" | head -40
tail -2 "$dir/log.txt" | grep "^S" || true
