#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
#
# run_openmsx.sh - runs SPHERE7 in openMSX (WSL, the V9968 + geo3d fork) and
# captures every page flip (tools/capture.tcl), then analyses it
# (tools/analyze.py).
# Usage: run_openmsx.sh <cbios|wsx> <seconds> <outdir> [SPACE times] [ESC times]
#   cbios  98h: C-BIOS_V9968_JP -ext geo3d
#   wsx    88h: Panasonic_FS-A1WSX -ext HRA_V9968 -ext geo3d88
# At most one openMSX of its own; waits while two run machine-wide.
set -e
cd "$(dirname "$0")/.."
OPENMSX=${OPENMSX:-~/openMSX/derived/x86_64-linux-opt/bin/openmsx}
export OPENMSX_SYSTEM_DATA=${OPENMSX_SYSTEM_DATA:-~/openMSX/share}
export SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy
m=$1; limit=$2; dir=$3
case $m in
  cbios) args="-machine C-BIOS_V9968_JP -ext geo3d"; port=152; p=98 ;;
  wsx)   args="-machine Panasonic_FS-A1WSX -ext HRA_V9968 -ext geo3d88"; port=136; p=88 ;;
  *) echo "machine: cbios or wsx"; exit 2 ;;
esac
rom=out/SPHERE7_$p.ROM
mkdir -p "$dir"
awk -F'[:\t$ ]+' '/^[a-z_0-9]+:\tequ \$/ { printf "set ::A(%s) 0x%s\n", $1, $NF }' \
  out/labels_$p.txt > "$dir/labels.tcl"
while [ "$(pgrep -c -x openmsx || true)" -ge 2 ]; do sleep 5; done
OUT=$dir LABELS=$dir/labels.tcl LIMIT=$limit PORT=$port SPACE="${4:-}" ESC="${5:-}" SKIP=${SKIP:-1} \
  timeout 590 $OPENMSX $args -cart $rom -romtype ASCII16 -script tools/capture.tcl > "$dir/openmsx.txt" 2>&1 || true
tail -1 "$dir/log.txt"
