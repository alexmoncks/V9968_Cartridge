#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
#
# build.sh - builds the SCREEN 7 sphere demo (WSL: z80asm 1.8, ~/venv/bin/python)
#   out/SPHERE7_98.ROM  V9968 at 98h, geo3d at 9Dh/9Fh (openMSX fork -ext geo3d,
#                       blueMSX+ V9968-geo3d)
#   out/SPHERE7_88.ROM  the V9968 cartridge with the geo3d build at 88h
# 256 KB ASCII16 MegaROMs: bank 0 the code, 1 the tables, 2-9 the textures,
# 10 the HUD bitmaps. Options: --pal9 (9-bit palette instead of EPAL),
# --pace N (vertical blanks per frame, default 1 = 60 fps; 2 = 30 fps), --assets.
set -e
cd "$(dirname "$0")"
PY=${PY:-~/venv/bin/python}
PACE=1
PAL=
ASSETS=0
while [ $# -gt 0 ]; do
  case $1 in
    --pal9) PAL=--pal9 ;;
    --pace) PACE=$2; shift ;;
    --assets) ASSETS=1 ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
  shift
done
# the spin: one turn in 4.3 s (256 frames at 60 fps, 128 at 30 fps)
ROTSTEP=$(( PACE == 1 ? 1 : 2 ))
mkdir -p out
stamp=out/inc/.assets$PAL.p$PACE
if [ $ASSETS = 1 ] || [ ! -f $stamp ] || [ gen_assets.py -nt $stamp ]; then
  $PY gen_assets.py $PAL --pace $PACE
  rm -f out/inc/.assets*
  touch $stamp
fi
if grep -n -iE "^[^;]*ld +a, *[ri]_" sphere7.asm; then
  echo "ld a, R_... / I_... is read as the R / I register: use another name"; exit 1
fi
for p in 98 88; do
  printf 'PORT_BASE: equ 0x%s\nPACE: equ %d\nROTSTEP: equ %d\n' $p $PACE $ROTSTEP > out/ports.asm
  if ! z80asm -o out/bank0_$p.bin --list=out/sphere7_$p.lst --label=out/labels_$p.txt sphere7.asm 2> out/asm_$p.err || [ -s out/asm_$p.err ]; then
    head -40 out/asm_$p.err
    exit 1
  fi
  $PY - "$p" <<'EOF'
import sys
p = sys.argv[1]
b0 = open(f"out/bank0_{p}.bin", "rb").read()
assert len(b0) == 0x4000, len(b0)
b1 = open("out/bank1.bin", "rb").read()
tex = open("out/tex.bin", "rb").read()
hud = open("out/hud.bin", "rb").read()
assert len(b1) == 0x4000 and len(tex) == 0x20000 and len(hud) <= 0x4000
rom = b0 + b1 + tex + hud + bytes(0x4000 - len(hud))
rom += bytes(0x40000 - len(rom))
open(f"out/SPHERE7_{p}.ROM", "wb").write(rom)
EOF
  if ! $PY ../tools/mapper_guess.py --expect ASCII16 --margin 16 out/SPHERE7_$p.ROM > out/mapper_$p.txt; then
    cat out/mapper_$p.txt; echo "out/SPHERE7_$p.ROM does not read as ASCII16 to a mapper guesser"; exit 1
  fi
done
lab() { grep "^$1:" out/labels_98.txt | head -1 | sed 's/.*\$//'; }
printf 'bank 0: %d bytes free; RAM C000h-%sh\n' $((0x8000 - 0x$(lab bank0_end))) "$(lab ram_end)"
md5sum out/SPHERE7_98.ROM out/SPHERE7_88.ROM
