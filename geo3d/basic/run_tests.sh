#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
#
# Builds out/G3BASIC.ROM and checks it in openMSX (the V9968 fork with geo3d)
# on four machines with their real system ROMs:
#   turbor  Panasonic FS-A1ST with the V9968 as its VDP   98h  BASIC 4.0
#   msx2p   Panasonic FS-A1WSX + V9968 cartridge          88h  BASIC 3.0
#   msx2    Philips NMS 8245 + V9968 cartridge            88h  BASIC 2.1
#   msx1    Gradiente Expert XP-800 + CDX-2 disk + V9968  88h  BASIC 1.0 Br
# Each machine boots three times, one after the other:
#   gkey  G held (nothing installed; the baseline for HIMEM and FRE(0))
#   main  normally: disk/G3SKEL.BAS, LIST, G3SKEL2.BAS (a G3INIT left on),
#         a reset, then G3RESET.BAS
#   clea  with H.CLEA taken by an earlier ROM (faked by tests.tcl at INIT):
#         G3CLEA.BAS
# tests.tcl does the checks. At most two openMSX instances run at once (WSL
# has little RAM). Prints one PASS/FAIL line per check and a summary; exits 1
# on any failure. Usage: run_tests.sh [machine...] (default: all four).
set -u
cd "$(dirname "$0")"
OPENMSX=${OPENMSX:-~/openMSX/derived/x86_64-linux-opt/bin/openmsx}
export OPENMSX_SYSTEM_DATA=${OPENMSX_SYSTEM_DATA:-~/openMSX/share}
export SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy
bash build.sh || exit 1

machine() {  # name -> port (decimal), seconds of boot, openMSX options
  case $1 in
    turbor) echo "152 20 -machine Panasonic_FS-A1ST_V9968 -ext geo3d" ;;
    msx2p)  echo "136 15 -machine Panasonic_FS-A1WSX -ext HRA_V9968 -ext geo3d88" ;;
    msx2)   echo "136 15 -machine Philips_NMS_8245 -ext HRA_V9968 -ext geo3d88" ;;
    msx1)   echo "136 15 -machine Gradiente_Expert_XP-800 -ext Microsol_CDX-2 -ext HRA_V9968 -ext geo3d88" ;;
    *)      return 1 ;;
  esac
}

run1() {  # name mode
  local name=$1 mode=$2 opts
  opts=$(machine "$name") || { echo "FAIL $name: unknown machine"; return; }
  set -- $opts
  local port=$1 boot=$2
  shift 2
  CFG=$name MODE=$mode PORT=$port BOOT=$boot OUT=out/t_${name}_$mode.txt \
  GFILE=out/t_${name}_gkey.val LBL=out/g3basic.lbl \
    timeout 600 "$OPENMSX" "$@" -cart out/G3BASIC.ROM -romtype ASCII8 \
    -diska disk -script tests.tcl > out/t_${name}_$mode.log 2>&1
}

runmachine() {
  rm -f out/t_$1_*.txt out/t_$1_gkey.val
  run1 "$1" gkey
  run1 "$1" main
  run1 "$1" clea
}

names=("$@")
[ ${#names[@]} -eq 0 ] && names=(turbor msx2p msx2 msx1)
i=0
while [ $i -lt ${#names[@]} ]; do
  runmachine "${names[$i]}" &
  if [ $((i + 1)) -lt ${#names[@]} ]; then
    runmachine "${names[$((i + 1))]}" &
  fi
  wait
  i=$((i + 2))
done

npass=0
nfail=0
for name in "${names[@]}"; do
  for mode in gkey main clea; do
    f=out/t_${name}_$mode.txt
    if [ ! -f "$f" ]; then
      echo "FAIL $name: no result from the $mode run (see out/t_${name}_$mode.log)"
      nfail=$((nfail + 1))
      continue
    fi
    grep -E '^(PASS|FAIL|  info)' "$f"
    npass=$((npass + $(grep -c '^PASS' "$f")))
    nfail=$((nfail + $(grep -c '^FAIL' "$f")))
    if ! grep -q '^DONE' "$f"; then
      echo "FAIL $name: the $mode run did not finish"
      nfail=$((nfail + 1))
    fi
  done
done
fail=0
[ "$nfail" -gt 0 ] && fail=1
echo "SUMMARY: $npass checks passed, $nfail failed ($(printf '%s ' "${names[@]}")); $([ $fail = 0 ] && echo OK || echo FAILED)"
exit $fail
