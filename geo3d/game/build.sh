#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
#
# build.sh - builds the shooter ROMs (WSL: z80asm 1.8, ~/venv/bin/python)
#   out/GEO3D_SHOOTER_98.ROM  V9968 at 98h (the openMSX V9968 fork, -ext geo3d)
#   out/GEO3D_SHOOTER_88.ROM  V9968 cartridge at 88h (real hardware)
# 64 KB ASCII16 MegaROMs. The data comes from tools/gen_assets.py (run when
# out/inc is missing or older than the tools, or with --assets). Also writes
# out/labels_98.txt (z80asm label list, read by the tests) and prints the
# free space of the banks and the RAM use.
set -e
cd "$(dirname "$0")"
PY=${PY:-~/venv/bin/python}
mkdir -p out
if [ "$1" = --assets ] || [ ! -f out/inc/level.asm ] || \
   [ -n "$(find tools -name '*.py' -newer out/inc/level.asm)" ] || \
   [ ../demos/img/tile_background_512x212.png -nt out/inc/level.asm ] || \
   [ ../demos/img/tile_foreground_256x48.png -nt out/inc/level.asm ]; then
  (cd tools && $PY gen_assets.py --check)
fi
# z80asm 1.8 assembles "ld a, R_x" / "ld a, I_x" as "ld a, r" / "ld a, i"
if grep -n -iE "^[^;]*ld +a, *[ri]_" *.asm; then
  echo "ld a, R_... / I_... is read as the R / I register: use another name"; exit 1
fi
for p in 98 88; do
  echo "PORT_BASE: equ 0x$p" > out/ports.asm
  if ! z80asm -o out/shooter_$p.bin -L shooter.asm 2> out/labels_$p.txt; then
    cat out/labels_$p.txt | grep -v "^[A-Za-z_0-9]*:	equ" | head -40
    exit 1
  fi
  head -c 65536 out/shooter_$p.bin > out/GEO3D_SHOOTER_$p.ROM
  size=$(stat -c %s out/GEO3D_SHOOTER_$p.ROM)
  [ "$size" = 65536 ] || { echo "ROM size $size, expected 65536"; exit 1; }
done
lab() { grep "^$1:" out/labels_98.txt | head -1 | sed 's/.*\$//'; }
printf 'bank 0 code: %d bytes free, bank 1 data: %d bytes free, bank 2 tiles: %d bytes free\n' \
  $((0x8000 - 0x$(lab bank0_end))) $((0xC000 - 0x$(lab bank1_end))) $((0xC000 - 0x$(lab bank2_end)))
printf 'RAM C000h-%sh (%d bytes); the init clears C000h-E7FFh\n' "$(lab ram_end)" $((0x$(lab ram_end) - 0xC000))
[ $((0x$(lab ram_end))) -le $((0xE800)) ] || { echo "RAM map too big"; exit 1; }
ls -l out/GEO3D_SHOOTER_98.ROM out/GEO3D_SHOOTER_88.ROM
