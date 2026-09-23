#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
# Synthesises the whole V9968 cartridge project with geo3d wired in (Yosys,
# synth_gowin). The encrypted Gowin DVI IP is replaced by dvi_stub.v.
# Usage: synth_cartridge.sh <V9968_Cartridge repo root> [work dir]
# yowasp-yosys only sees files under its working directory, so the project is
# copied into the work dir and every path is relative.
set -e
REPO=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${2:-$HERE/work}
rm -rf "$WORK"
mkdir -p "$WORK/geo3d"
cp -r "$REPO/fpga" "$WORK/"
cp -r "$HERE/../../rtl" "$WORK/geo3d/"
cp "$HERE/dvi_stub.v" "$WORK/"
python3 "$HERE/../../integration/apply_geo3d_patch.py" "$WORK"
cd "$WORK"
P=fpga/V9968_Cartridge_TangNano20K
FILES=$(grep -o 'File path="[^"]*\.v"' $P/*.gprj | sed 's/File path="//;s/"//' | grep -v dvi_tx \
        | sed "s#^\.\./\.\./#./#; t; s#^#$P/#" | tr '\n' ' ')
cat > cart.ys <<EOF
read_verilog -D SYNTHESIS dvi_stub.v $FILES
synth_gowin -family gw2a -top tangnano20k_vdp_cartridge -json cart.json
tee -o cart_stat.txt stat
EOF
yowasp-yosys -q -s cart.ys > cart_log.txt 2>&1
cp cart_stat.txt "$HERE/yosys_cartridge_with_geo3d_textures_stat.txt"
echo "cartridge + geo3d: $HERE/yosys_cartridge_with_geo3d_textures_stat.txt"
