#!/bin/sh
# Reproduz simulação, síntese e place & route.
# Requer: iverilog, python3, pip install yowasp-yosys yowasp-nextpnr-himbaechel-gowin
set -e
cd sim
python3 gen_vectors.py 500 vectors.hex
iverilog -g2005 -o tb.vvp tb_geo3d.v ../rtl/geo3d_z80if.v ../rtl/geo3d_core.v
vvp -n tb.vvp | grep -E "Resultado|PASS|FAIL"
cd ../syn
yowasp-yosys -q -p "read_verilog ../rtl/geo3d_core.v ../rtl/geo3d_z80if.v; synth_gowin -family gw2a -top geo3d_z80if -json geo3d.json; tee -o yosys_stat.txt stat"
for F in 42.95 85.91; do
  yowasp-nextpnr-himbaechel-gowin --json geo3d.json --write pnr_$F.json \
    --device GW2AR-LV18QN88C8/I7 --vopt family=GW2A-18C --vopt cst=geo3d_test.cst \
    --freq $F > nextpnr_${F}MHz.log 2>&1
  grep "Max frequency" nextpnr_${F}MHz.log | tail -1
done
