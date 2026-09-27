#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
# Synthesises the whole V9968 cartridge project with geo3d wired in (Yosys,
# synth_gowin). The encrypted Gowin DVI IP is replaced by dvi_stub.v.
# Usage: synth_cartridge.sh <V9968_Cartridge repo root> [work dir]
# The repo root only has to hold HRA!'s project (fpga/V9968_Cartridge_TangNano20K):
# a clone of hra1129/V9968_Cartridge will do, as geo3d/run_all.sh uses it.
# The geo3d RTL is this script's own geo3d/rtl.
# yowasp-yosys only sees files under its working directory, so the geo3d
# project is generated into the work dir (the same generator and inputs as
# fpga/V9968_Cartridge_TangNano20K_geo3d: HRA!'s project + geo3d/rtl) and
# every path is relative.
set -e
REPO=$(cd "$1" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${2:-$HERE/work}
P=fpga/V9968_Cartridge_TangNano20K_geo3d
rm -rf "$WORK"
mkdir -p "$WORK"
python3 "$HERE/../../integration/apply_geo3d_patch.py" --rtl "$HERE/../../rtl" \
        --out "$WORK/$P" "$REPO" > /dev/null
cp "$HERE/dvi_stub.v" "$WORK/"
cd "$WORK"
FILES=$(grep -o 'File path="[^"]*\.v"' $P/*.gprj | sed 's/File path="//;s/"//' | grep -v dvi_tx \
        | sed "s#^#$P/#" | tr '\n' ' ')
cat > cart.ys <<EOF
read_verilog -D SYNTHESIS dvi_stub.v $FILES
synth_gowin -family gw2a -top tangnano20k_vdp_cartridge -json cart.json
tee -o cart_stat.txt stat
EOF
yowasp-yosys -q -s cart.ys > cart_log.txt 2>&1
cp cart_stat.txt "$HERE/yosys_cartridge_with_geo3d_textures_stat.txt"
echo "cartridge + geo3d: $HERE/yosys_cartridge_with_geo3d_textures_stat.txt"
