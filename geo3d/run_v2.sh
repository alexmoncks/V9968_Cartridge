#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
# The checks of geo3d v2.0 against v1.0.0 (run_all.sh still reproduces every
# check of the folder with the current RTL and model).
# Needs: iverilog, python3, and v1.0.0's model and engine to compare with:
#   V1=<folder with v1.0.0's gen_scenes.py and geo3d_engine.v>
# or, without V1, git and the tag geo3d-v1.0.0 (they are read from it).
#   GEN_JSON=<GEO FIGHTER's out/gen.json>  the benchmark's resident scene is
#   then that game's (default: two figures of boxes)
#
#   1. the v2.0 model gives the logs of the v1.0.0 model for v1.0.0's
#      stimuli (random scenes of edges, faces and textures; the Z80 demos'
#      traffic too, when run_all.sh has left it in sim/)
#   2. the v2.0 RTL gives those logs too: a v1.0.0 program gets the same
#      commands
#   3. phase 1 (the immediate mode) on the v2.0 engine
#   4. what v2.0 adds (sim/gen_v2_scenes.py): RTL == model, through the
#      engine alone and through the dual-clock bus bridge
#   5. engine clocks per frame, v1.0.0 RTL against v2.0 RTL
set -e
cd "$(dirname "$0")"
HERE=$(pwd)
cd sim
W=v2_work
rm -rf $W
mkdir -p $W/v1

if [ -n "$V1" ]; then
  cp "$V1/gen_scenes.py" "$V1/geo3d_engine.v" $W/v1/
else
  git show geo3d-v1.0.0:geo3d/sim/gen_scenes.py   > $W/v1/gen_scenes.py
  git show geo3d-v1.0.0:geo3d/rtl/geo3d_engine.v  > $W/v1/geo3d_engine.v
fi
# v1.0.0's generators with v1.0.0's model (the other files did not change)
cp gen_vectors.py gen_face_scenes.py gen_tex_scenes.py check_demo.py $W/v1/

echo "== 1. v1.0.0's stimuli: the v2.0 model against the v1.0.0 model"
SETS="scenes face tex"
(cd $W/v1 && python3 gen_scenes.py 24 > /dev/null && python3 gen_face_scenes.py 16 > /dev/null \
          && python3 gen_tex_scenes.py 12 > /dev/null)
for d in demo faces_demo texdemo; do
  if [ -f ${d}_stim.txt ]; then
    cp ${d}_stim.txt $W/v1/
    (cd $W/v1 && python3 check_demo.py ${d}_stim.txt ${d}_expect.txt > /dev/null)
    SETS="$SETS $d"
  else
    echo "   (${d}_stim.txt is not here: run_all.sh writes it from the Z80 emulator)"
  fi
done
for s in $SETS; do
  python3 check_demo.py $W/v1/${s}_stim.txt $W/${s}_v2model.txt > /dev/null
  cmp $W/v1/${s}_expect.txt $W/${s}_v2model.txt
  echo "PASS: $s, the v2.0 model == the v1.0.0 model ($(wc -l < $W/${s}_v2model.txt) lines)"
done

echo "== 2. v1.0.0's stimuli: the v2.0 RTL against the v1.0.0 model"
iverilog -g2005 -o $W/tbe.vvp tb_engine.v ../rtl/geo3d_engine.v ../rtl/geo3d_core.v
iverilog -g2005 -o $W/tbe1.vvp tb_engine.v $W/v1/geo3d_engine.v ../rtl/geo3d_core.v
iverilog -g2005 -o $W/tbb.vvp tb_bus.v ../rtl/geo3d_bus.v ../rtl/geo3d_engine.v ../rtl/geo3d_core.v
for s in $SETS; do
  vvp -n $W/tbe.vvp +stim=$W/v1/${s}_stim.txt +got=$W/${s}_got.txt | head -1
  cmp $W/v1/${s}_expect.txt $W/${s}_got.txt
  echo "PASS: $s, the v2.0 RTL == the v1.0.0 model"
done
vvp -n $W/tbb.vvp +stim=$W/v1/scenes_stim.txt +got=$W/bus_scenes_got.txt | head -1
cmp $W/v1/scenes_expect.txt $W/bus_scenes_got.txt
echo "PASS: scenes through the dual-clock bus bridge"

echo "== 3. phase 1 (immediate mode) on the v2.0 engine"
python3 gen_vectors.py 500 vectors.hex > /dev/null
iverilog -g2005 -o $W/p1.vvp tb_phase1_on_engine.v ../rtl/geo3d_engine.v ../rtl/geo3d_core.v
vvp -n $W/p1.vvp | grep -E "Resultado|PASS|FAIL"

echo "== 4. what v2.0 adds: 1023 entries, extended streams, VFIRST / EFIRST / FFIRST"
(cd $W && python3 ../gen_v2_scenes.py 2 v2)
vvp -n $W/tbe.vvp +stim=$W/v2_stim.txt +got=$W/v2_got.txt | head -1
cmp $W/v2_expect.txt $W/v2_got.txt
echo "PASS: v2.0 scenes, RTL == model"
vvp -n $W/tbb.vvp +stim=$W/v2_stim.txt +got=$W/v2_bus_got.txt | head -1
cmp $W/v2_expect.txt $W/v2_bus_got.txt
echo "PASS: v2.0 scenes through the dual-clock bus bridge"

echo "== 5. engine clocks per frame with RUN busy (V9968 in high-speed mode: +hs=1)"
(cd $W && python3 ../bench_v2.py gen $GEN_JSON)
bench() {  # rtl (tbe1 = v1.0.0, tbe = v2.0), stimulus, name
  vvp -n $W/$1.vvp +hs=1 +stim=$2_stim.txt +got=$W/b_$3_got.txt +time=$W/b_$3_time.txt > /dev/null
  cmp $2_expect.txt $W/b_$3_got.txt
}
bench tbe1 $W/v1/face       face1
bench tbe  $W/v1/face       face2
bench tbe1 $W/v1/tex        tex1
bench tbe  $W/v1/tex        tex2
bench tbe1 $W/bench_res1    res11
bench tbe  $W/bench_res1    res12
bench tbe  $W/bench_res2    res22
bench tbe  $W/bench_big     big2
python3 bench_v2.py report \
  "random faces: v1.0.0=$W/b_face1_time.txt"          "random faces: v2.0=$W/b_face2_time.txt" \
  "random textures: v1.0.0=$W/b_tex1_time.txt"        "random textures: v2.0=$W/b_tex2_time.txt" \
  "resident, v1 traffic: v1.0.0=$W/b_res11_time.txt"  "resident, v1 traffic: v2.0=$W/b_res12_time.txt" \
  "resident, v2 traffic: v2.0=$W/b_res22_time.txt"    "1023 faces: v2.0=$W/b_big2_time.txt"
echo "== all the v2.0 checks passed"
