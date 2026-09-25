#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
#
# Builds the geo3d BASIC extension ROM: out/G3BASIC.ROM, a 64 KB ASCII8
# MegaROM (openMSX: -cart out/G3BASIC.ROM -romtype ASCII8), and its label
# file out/g3basic.lbl. Fails loudly on assembler errors, on a wrong size,
# on a bank 0 over 8 KB and on an "AB" header anywhere but at offset 0.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p out
rm -f out/G3BASIC.ROM out/g3basic.lbl
# z80asm 1.8 reads a symbol like E_SN as the register E (and drops "_SN"
# silently): "ld e,E_SN" became "ld e,e". Refuse such names in the code.
if grep -nE '^[^;]*\b[ABCDEHLIRabcdehlir]_[A-Za-z0-9]' g3basic.asm; then
  echo "build.sh: symbols made of a register letter and '_' are misread by z80asm"
  exit 1
fi
if ! z80asm -o out/G3BASIC.ROM --label=out/g3basic.lbl g3basic.asm 2> out/build.err; then
  cat out/build.err
  echo "build.sh: z80asm failed"
  exit 1
fi
if [ -s out/build.err ]; then
  cat out/build.err
  echo "build.sh: z80asm printed messages (treated as errors)"
  exit 1
fi
size=$(stat -c %s out/G3BASIC.ROM)
if [ "$size" != 65536 ]; then
  echo "build.sh: out/G3BASIC.ROM is $size bytes, not 65536"
  exit 1
fi
# bank 0 is the fixed code bank at 4000h-5FFFh
end=$(awk '$1 == "code_end:" { print $3 }' out/g3basic.lbl | sed "s/^[$]//")
if [ -z "$end" ]; then
  echo "build.sh: code_end not found in the label file"
  exit 1
fi
used=$(( 0x$end - 0x4000 ))
if [ "$used" -gt 8192 ]; then
  echo "build.sh: bank 0 holds $used bytes, more than 8 KB"
  exit 1
fi
# "AB" only at the start of bank 0: any bank can show up at 8000h
for bank in 1 2 3 4 5 6 7; do
  if [ "$(od -An -tx1 -j $((bank * 8192)) -N2 out/G3BASIC.ROM | tr -d ' ')" = 4142 ]; then
    echo "build.sh: bank $bank starts with AB"
    exit 1
  fi
done
echo "build.sh: out/G3BASIC.ROM, 65536 bytes, bank 0 uses $used of 8192 bytes"
