#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
#
# Builds g3names.rom and runs G3TEST.BAS on real MSX-BASIC machines in
# openMSX (turboR, MSX2+, MSX2, MSX1), one log per machine in out/.
# Needs z80asm, the openMSX V9968 fork (OPENMSX) and the system ROMs.
set -e
cd "$(dirname "$0")"
OPENMSX=${OPENMSX:-~/openMSX/derived/x86_64-linux-opt/bin/openmsx}
export OPENMSX_SYSTEM_DATA=${OPENMSX_SYSTEM_DATA:-~/openMSX/share}
export SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy
mkdir -p out
z80asm -o out/g3names.rom g3names.asm
[ "$(stat -c %s out/g3names.rom)" = 16384 ] || { echo "g3names.rom is not 16 KB"; exit 1; }

run() {  # name boot-seconds machine [extensions...]
  local tag=$1 boot=$2; shift 2
  OUT=out/names_$tag.txt BOOT=$boot timeout 600 "$OPENMSX" "$@" \
    -cart out/g3names.rom -romtype Normal -diska disk -script names.tcl \
    > out/names_$tag.log 2>&1 || true
  echo "== $tag: $(grep -c '^PROCNM' out/names_$tag.txt 2>/dev/null) CALLs logged"
}
run turbor 20 -machine Panasonic_FS-A1ST_V9968
run msx2p  15 -machine Panasonic_FS-A1WSX
run msx2   15 -machine Philips_NMS_8245
run msx1   15 -machine Gradiente_Expert_XP-800 -ext Microsol_CDX-2
