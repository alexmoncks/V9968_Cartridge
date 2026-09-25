#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
# Reproduces every check in this directory.
# Needs: iverilog, python3, z80asm (GNU), pip: yowasp-yosys yowasp-nextpnr-himbaechel-gowin z80
# Optional: V9968_REPO=<clone of hra1129/V9968_Cartridge> enables the checks that
# run HRA!'s original vdp_command.v (LRMM/LINE models, end-to-end system test,
# textured video, whole-cartridge synthesis).
set -e
cd "$(dirname "$0")"
V=${V9968_REPO:+$(cd "$V9968_REPO" && pwd)/fpga/V9968_Cartridge_TangNano20K/src/v9968}

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

echo "== textured faces (LRMM): random scenes"
python3 gen_tex_scenes.py 12
vvp -n tbe.vvp +stim=tex_stim.txt +got=tex_got.txt | head -1
diff tex_expect.txt tex_got.txt && echo "PASS: textured faces match the reference model"

echo "== GEO3D textured demo (Z80 emulator -> RTL -> model)"
cd ../z80
python3 gen_tex_tables.py
z80asm -o GEO3DT.COM geo3d_tex_demo.asm
python3 run_demo_z80.py 130 GEO3DT.COM ../sim/texdemo_stim.txt ../sim/texdemo_sys.txt > /dev/null
cd ../sim
python3 check_demo.py texdemo_stim.txt texdemo_expect.txt
vvp -n tbe.vvp +stim=texdemo_stim.txt +got=texdemo_got.txt | head -1
diff texdemo_expect.txt texdemo_got.txt && echo "PASS: textured demo matches the reference model"

if [ -n "$V" ]; then
  echo "== HRA!'s vdp_command.v (unmodified) as the golden VDP"
  cd hra
  iverilog -g2012 -o tbhra.vvp tb_hra_cmd.v "$V/vdp_command.v" "$V/vdp_command_cache.v"
  python3 check_lrmm.py 900 1
  python3 check_lrmm.py 300 0
  cd ..
  echo "== end to end: Z80 traffic -> geo3d_bus -> vdp_command.v -> VRAM (takes a while)"
  iverilog -g2012 -o tbs.vvp tb_system.v ../rtl/geo3d_bus.v ../rtl/geo3d_engine.v ../rtl/geo3d_core.v \
    "$V/vdp_command.v" "$V/vdp_command_cache.v"
  vvp -n tbs.vvp +stim=texdemo_sys.txt +frames=texdemo_frames.hex | grep Sistema
  python3 check_system.py texdemo_frames.hex texdemo_got.txt ../z80/tex_init.hex
fi

if command -v ffmpeg >/dev/null 2>&1; then
  if [ -f texdemo_frames.hex ]; then
    python3 render_vram.py texdemo_frames.hex geo3d_textures.mp4 ../z80/face_palette.txt 4 30
  fi
  python3 render_video.py demo_got.txt geo3d_demo.mp4 4 30
  python3 render_video.py faces_demo_got.txt geo3d_faces.mp4 4 30 - ../z80/face_palette.txt \
    "GEO3D em blocos sólidos: faces sombreadas, pintadas pelo VDP linha a linha"
fi

echo "== showcase scenes and the demo ROM"
cd ../showcase
for s in panzoom crawl flyin; do
  python3 showcase.py $s 1 6
  (cd ../sim && vvp -n tbe.vvp +stim=../showcase/out/${s}_engine_stim.txt \
     +got=../showcase/out/${s}_engine_got.txt > /dev/null)
  cmp out/${s}_engine_expect.txt out/${s}_engine_got.txt && echo "PASS: $s, geo3d RTL == model"
  if [ -n "$V" ]; then
    (cd ../sim && vvp -n tbs.vvp +stim=../showcase/out/${s}_sys_sample.txt \
       +frames=../showcase/out/${s}_sample_got.hex | grep Sistema)
    python3 check_sample.py $s
  fi
done
cd ../rom
python3 build_rom.py
python3 run_rom_z80.py
python3 run_rom_z80.py 100
cd ../sim

echo "== synthesis and P&R (GW2AR-18)"
cd ../syn
yowasp-yosys -q -p "read_verilog ../rtl/geo3d_core.v ../rtl/geo3d_engine.v ../rtl/geo3d_bus.v geo3d_pnr_top.v; synth_gowin -family gw2a -top geo3d_pnr_top -json top.json"
# engine on clk_eng (42.95 MHz); the bus side (85.9 MHz) is a few registers
yowasp-nextpnr-himbaechel-gowin --json top.json --write top_pnr.json \
  --device GW2AR-LV18QN88C8/I7 --vopt family=GW2A-18C --vopt cst=pnr_top.cst \
  --freq 42.95 > nextpnr_engine_textures.log 2>&1
grep "Max frequency" nextpnr_engine_textures.log | tail -2
if [ -n "$V9968_REPO" ]; then
  sh cartridge/synth_cartridge.sh "$V9968_REPO"
fi
