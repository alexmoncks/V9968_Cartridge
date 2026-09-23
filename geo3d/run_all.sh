#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
# Reproduces every check in this directory.
# Needs: iverilog, python3, z80asm (GNU), pip: yowasp-yosys yowasp-nextpnr-himbaechel-gowin z80
set -e
cd "$(dirname "$0")"

echo "== phase 1: geo3d_core, 4000 random vertices"
cd sim
python3 gen_vectors.py 500 vectors.hex
iverilog -g2005 -o tb.vvp tb_geo3d.v ../rtl/geo3d_z80if.v ../rtl/geo3d_core.v
vvp -n tb.vvp | grep -E "Resultado|PASS|FAIL"
iverilog -g2005 -o p1.vvp tb_phase1_on_engine.v ../rtl/geo3d_engine.v ../rtl/geo3d_core.v
vvp -n p1.vvp | grep -E "Resultado|PASS|FAIL"

echo "== phase 2: geo3d_engine, random scenes (clipping, culling, near plane)"
python3 gen_scenes.py 24
iverilog -g2005 -o tbe.vvp tb_engine.v ../rtl/geo3d_engine.v ../rtl/geo3d_core.v
vvp -n tbe.vvp | head -1
diff scenes_expect.txt scenes_got.txt && echo "PASS: engine matches the reference model"
iverilog -g2005 -o tbb.vvp tb_bus.v ../rtl/geo3d_bus.v ../rtl/geo3d_engine.v ../rtl/geo3d_core.v
vvp -n tbb.vvp +stim=scenes_stim.txt +got=bus_scenes_got.txt | head -1
diff scenes_expect.txt bus_scenes_got.txt && echo "PASS: dual-clock bus bridge (85.9 / 42.95 MHz)"

echo "== filled faces: random scenes (culling, painter's order, shading, spans)"
python3 gen_face_scenes.py 16
vvp -n tbe.vvp +stim=face_stim.txt +got=face_got.txt | head -1
diff face_expect.txt face_got.txt && echo "PASS: filled faces match the reference model"

echo "== phase 2: the real Z80 demo program, run in a Z80 emulator"
cd ../z80
python3 gen_tables.py
z80asm -o GEO3D.COM geo3d_demo.asm
python3 run_demo_z80.py 130
cd ../sim
python3 check_demo.py demo_stim.txt demo_expect.txt
vvp -n tbe.vvp +stim=demo_stim.txt +got=demo_got.txt | head -1
diff demo_expect.txt demo_got.txt && echo "PASS: Z80 demo traffic matches the reference model"

echo "== GEO3D filled-face demo (Z80 emulator -> RTL -> model)"
cd ../z80
python3 gen_face_tables.py
z80asm -o GEO3DF.COM geo3d_faces_demo.asm
python3 run_demo_z80.py 130 GEO3DF.COM ../sim/faces_demo_stim.txt > /dev/null
cd ../sim
python3 check_demo.py faces_demo_stim.txt faces_demo_expect.txt
vvp -n tbe.vvp +stim=faces_demo_stim.txt +got=faces_demo_got.txt | head -1
diff faces_demo_expect.txt faces_demo_got.txt && echo "PASS: GEO3D demo matches the reference model"

if command -v ffmpeg >/dev/null 2>&1; then
  python3 render_video.py demo_got.txt geo3d_demo.mp4 4 30
  python3 render_video.py faces_demo_got.txt geo3d_faces.mp4 4 30 - ../z80/face_palette.txt \
    "GEO3D em blocos sólidos: faces sombreadas, pintadas pelo VDP linha a linha"
fi

echo "== synthesis and P&R (GW2AR-18)"
cd ../syn
yowasp-yosys -q -p "read_verilog ../rtl/geo3d_core.v ../rtl/geo3d_engine.v ../rtl/geo3d_bus.v geo3d_pnr_top.v; synth_gowin -family gw2a -top geo3d_pnr_top -json top.json"
# engine on clk_eng (42.95 MHz); the bus side (85.9 MHz) is a few registers
yowasp-nextpnr-himbaechel-gowin --json top.json --write top_pnr.json \
  --device GW2AR-LV18QN88C8/I7 --vopt family=GW2A-18C --vopt cst=pnr_top.cst \
  --freq 42.95 > nextpnr_engine_dualclock.log 2>&1
grep "Max frequency" nextpnr_engine_dualclock.log | tail -2
