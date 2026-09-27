#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
# Tests of apply_geo3d_patch.py, on temporary copies: the repository is only
# read. HRA!'s project is copied from fpga/V9968_Cartridge_TangNano20K (which
# must be his upstream content), then generated from, edited and mutated.
# Covers: idempotence, --check, the hand-edit protection (manifest, --force),
# updates of HRA!'s project and of geo3d/rtl, and the refusals when HRA!'s
# text has moved (nothing may be written then).
# Needs bash, python3 and GNU diff / sed (Linux or WSL).
# Usage: bash geo3d/integration/test_apply_geo3d_patch.sh
R=$(cd "$(dirname "$0")/../.." && pwd)
GEN="python3 $R/geo3d/integration/apply_geo3d_patch.py"
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
NAME=tangnano20k_vdp_cartridge
TOP=src/$NAME.v
pass=0; fail=0
ok() { if [ "$1" = 0 ]; then pass=$((pass + 1)); echo "PASS  $2"; else fail=$((fail + 1)); echo "FAIL  $2"; fi; }

# HRA!'s project: what the generator reads (the .gprj, src/, the process settings)
H=$T/hra/fpga/V9968_Cartridge_TangNano20K
mkdir -p "$H/impl"
cp -r "$R/fpga/V9968_Cartridge_TangNano20K/src" "$H/"
cp "$R/fpga/V9968_Cartridge_TangNano20K/$NAME.gprj" "$H/"
cp "$R/fpga/V9968_Cartridge_TangNano20K/impl/${NAME}_process_config.json" "$H/impl/"
cp -r "$R/geo3d/rtl" "$T/rtl"
g() { $GEN --hra "$H" --rtl "$T/rtl" "$@"; }            # the generator on the copies

echo "== generation"
g --out "$T/A" > /dev/null
g --out "$T/A" > "$T/second.txt"
grep -q ", 0 written, 0 removed" "$T/second.txt"; ok $? "a second run writes nothing"
g --out "$T/B" > /dev/null
diff -r "$T/A" "$T/B" > /dev/null; ok $? "two generations give identical trees (bytes)"
g --check --out "$T/A" > /dev/null; ok $? "--check passes on a fresh generation"
same=0; for f in geo3d_core.v geo3d_engine.v geo3d_bus.v; do
    cmp -s "$T/rtl/$f" "$T/A/src/geo3d/$f" || same=1; done
ok $same "src/geo3d is byte-identical to geo3d/rtl"
$GEN --check --hra "$H" --out "$R/fpga/V9968_Cartridge_TangNano20K_geo3d" --rtl "$R/geo3d/rtl" > /dev/null
ok $? "the repository's geo3d project is what the script generates (--check)"

echo "== --check"
chk() {  # <label> <expected line prefix> <project>: --check fails and reports the line
    out=$(g --check --out "$3"); rc=$?
    echo "$out" | grep -q "^  $2"; r=$?; [ $rc = 1 ] || r=1; ok $r "$1"; }
cp -a "$T/A" "$T/C1"; echo "// hand edit" >> "$T/C1/src/geo3d/geo3d_engine.v"
chk "a hand edit in src/geo3d is reported as edited" "edited    src/geo3d/geo3d_engine.v  (edited here:" "$T/C1"
cp -a "$T/A" "$T/C2"; echo "// hand edit" >> "$T/C2/src/v9968/vdp_command.v"
chk "a hand edit in one of HRA!'s copied sources" "edited    src/v9968/vdp_command.v" "$T/C2"
cp -a "$T/A" "$T/C3"; touch "$T/C3/src/stale.v"
chk "a file under src/ that the script did not generate" "extra     src/stale.v" "$T/C3"
cp -a "$T/A" "$T/C4"; rm "$T/C4/src/geo3d/geo3d_bus.v"
chk "a missing file" "missing   src/geo3d/geo3d_bus.v" "$T/C4"
cp -a "$T/A" "$T/C5"; sed -i 's/Place_Option": "0"/Place_Option": "1"/' "$T/C5/impl/${NAME}_process_config.json"
chk "changed project settings" "edited    impl/${NAME}_process_config.json" "$T/C5"
cp -a "$T/A" "$T/C6"
python3 - "$T/C6" <<'PY'
import sys, pathlib
d = pathlib.Path(sys.argv[1])
for f in list((d / "src/geo3d").glob("*.v")) + [d / "src/v9968/vdp_command.v", d / "README.md"]:
    b = f.read_bytes()
    f.write_bytes(b.replace(b"\r\n", b"\n") if b"\r\n" in b else b.replace(b"\n", b"\r\n"))
PY
g --check --out "$T/C6" > /dev/null; ok $? "line-ending-only differences are accepted"

echo "== updates (no --force needed)"
cp -a "$T/A" "$T/U"; cp -a "$T/rtl" "$T/rtl.bak"
echo "// a change in geo3d/rtl" >> "$T/rtl/geo3d_engine.v"
chk "a change in geo3d/rtl makes the copy stale" "stale     src/geo3d/geo3d_engine.v" "$T/U"
g --out "$T/U" | grep -q "wrote   src/geo3d/geo3d_engine.v"; ok $? "regenerating takes the change in geo3d/rtl"
g --check --out "$T/U" > /dev/null; ok $? "... and the project is current again"
rm -rf "$T/rtl"; mv "$T/rtl.bak" "$T/rtl"
cp -a "$H" "$T/hra.bak"
echo "// an upstream change" >> "$H/src/v9968/vdp_command.v"
sed -i 's#<File path="src/ram/ip_ram.v"[^>]*/>##' "$H/$NAME.gprj"
chk "an update of HRA!'s project makes the copies stale" "stale     src/v9968/vdp_command.v" "$T/U"
out=$(g --out "$T/U"); rc=$?
echo "$out" | grep -q "wrote   src/v9968/vdp_command.v" && echo "$out" | grep -q "removed src/ram/ip_ram.v" && [ $rc = 0 ]
ok $? "regenerating takes his update and removes a file he no longer lists"
[ ! -d "$T/U/src/ram" ]; ok $? "... and the folder left empty"
rm -rf "$H"; mv "$T/hra.bak" "$H"
g --out "$T/U" > /dev/null; diff -r "$T/A" "$T/U" > /dev/null; ok $? "back to the same tree as the first generation"

echo "== hand edits are not overwritten"
refuse_write() {  # <label> <project>: the run fails and the project does not change
    cp -a "$2" "$T/before"
    g --out "$2" > /dev/null 2> "$T/err.txt"; rc=$?
    diff -r "$T/before" "$2" > /dev/null; same=$?; rm -rf "$T/before"
    [ $rc = 1 ] && [ $same = 0 ] && grep -q "nothing was written" "$T/err.txt"; ok $? "$1"; }
refuse_write "a hand edit in src/geo3d: refused, nothing written" "$T/C1"
refuse_write "a hand edit in one of HRA!'s copied sources: refused" "$T/C2"
refuse_write "a file under src/ that the script did not generate: refused" "$T/C3"
mkdir -p "$T/other/src/x"; echo "keep" > "$T/other/src/x/notes.txt"; echo "keep" > "$T/other/README.md"
refuse_write "--out pointing at another folder: refused, nothing written or removed" "$T/other"
cp -a "$T/A" "$T/N"; rm "$T/N/geo3d_manifest.txt"; echo "// x" >> "$T/N/src/v9968/vdp.v"
refuse_write "no manifest and a differing file: refused" "$T/N"
out=$(g --force --out "$T/C1"); rc=$?
bak=$(ls -d "$T"/C1_backup_* 2> /dev/null | head -1)
[ $rc = 0 ] && [ -n "$bak" ] && grep -q "// hand edit" "$bak/src/geo3d/geo3d_engine.v" && diff -r "$T/A" "$T/C1" > /dev/null
ok $? "--force backs the edit up next to the project, then regenerates it"
g --force --out "$T/C3" > /dev/null
bak=$(ls -d "$T"/C3_backup_* 2> /dev/null | head -1)
[ -f "$bak/src/stale.v" ] && [ ! -e "$T/C3/src/stale.v" ]; ok $? "--force backs up and removes a file the script did not generate"

echo "== refusals when HRA!'s text has moved (nothing written)"
refuse() {  # <label> <HRA!'s folder> [out]
    out=${3:-$T/R}; rm -rf "$out"
    msg=$($GEN --hra "$2" --out "$out" --rtl "$T/rtl" 2>&1); rc=$?
    if [ $rc != 0 ] && [ ! -e "$out" ]; then ok 0 "$1: $(echo "$msg" | head -1 | cut -c1-110)"
    else ok 1 "$1 (rc=$rc)"; fi; }
mut() {  # <file in HRA!'s folder> <sed program>: a mutated copy of HRA!'s folder in $T/H2
    rm -rf "$T/H2"; cp -a "$H" "$T/H2"; cp "$T/H2/$1" "$T/orig"
    sed -i "$2" "$T/H2/$1"
    cmp -s "$T/orig" "$T/H2/$1" && echo "  (test error: the mutation did not apply)"; }
rm -rf "$T/H3"; cp -a "$H" "$T/H3"
for f in "$NAME.gprj" "$TOP" src/v9968/vdp.v src/gowin_rpll2/gowin_rpll2.v "src/$NAME.sdc"; do
    cp "$T/A/$f" "$T/H3/$f"; done
refuse "HRA!'s folder already patched" "$T/H3"
mut src/v9968/vdp.v 's/vdp_command u_command/vdp_command u_cmd/';                 refuse "vdp_command instance renamed" "$T/H2"
mut src/v9968/vdp.v 's/( w_register_num\s*)/( w_reg_num )/';                        refuse "command register hookup changed" "$T/H2"
mut $TOP "s/? w_bus_vdp_rdata: 8'hFF;/? w_bus_vdp_rdata: ( w_bus_mem_rdata_en ) ? w_bus_mem_rdata: 8'hFF;/; s/assign w_bus_rdata_en\t= w_bus_vdp_rdata_en;/assign w_bus_rdata_en\t= w_bus_vdp_rdata_en | w_bus_mem_rdata_en;/"
refuse "bus mux: a second read source" "$T/H2"
mut $TOP "s/\tassign w_bus_rdata_en/\twire hra_new_line = 1'b0;\n\tassign w_bus_rdata_en/"
refuse "bus mux: a line inserted between its assigns" "$T/H2"
mut $TOP 's/assign w_bus_ready/assign w_bus_rdy/';                                   refuse "bus mux: an assign renamed" "$T/H2"
mut $TOP '0,/^module/s//\/\/ vdp u_v9958 ( old notes );\nmodule/';                   refuse "the VDP instance name found twice" "$T/H2"
mut $TOP 's/wire\t\t\tw_bus_vdp_ready;/wire\t\t\tw_bus_vdp_rdy;/';                    refuse "a net geo3d uses is no longer declared" "$T/H2"
mut $TOP 's/wire\t\[2:0\]\tw_bus_address;/wire\t[3:0]\tw_bus_address;/';              refuse "a net geo3d uses changed width" "$T/H2"
mut $TOP 's/\.bus_rdata\t\t\t( w_bus_rdata\t/.bus_rdata\t\t\t( w_slot_rdata\t/';       refuse "the slot's read data moved to another net" "$T/H2"
mut $TOP 's/\.reset_n\t\t\t( reset_n3\t/.reset_n\t\t\t( reset_n\t/';                  refuse "the VDP's reset changed" "$T/H2"
mut $TOP 's/\.clkout\t\t\t( clk85m/.clkout\t\t\t( clk85x/';                          refuse "u_pll2 no longer drives clk85m" "$T/H2"
mut $TOP 's/wire\t\t\tclk42m;/wire\t\t\tclk42x;/';                                   refuse "'wire clk42m' moved" "$T/H2"
mut src/gowin_rpll2/gowin_rpll2.v 's/DYN_SDIV_SEL = 2/DYN_SDIV_SEL = 4/';            refuse "rPLL2 divider changed" "$T/H2"
mut src/gowin_rpll2/gowin_rpll2.v 's/\.CLKOUTD(clkoutd_o)/.CLKOUTD()/';              refuse "rPLL2 CLKOUTD hookup changed" "$T/H2"
mut src/$NAME.sdc 's/-multiply_by 6/-multiply_by 5/';                                refuse "SDC clk85m changed" "$T/H2"
mut src/$NAME.sdc 's/-name clk42m /-name clk42n /';                                  refuse "SDC clk42m moved" "$T/H2"
mut $NAME.gprj 's#src/v9968/vdp.v#src/v9968/vdp2.v#';                               refuse ".gprj lists a missing file" "$T/H2"
mut $NAME.gprj 's#src/ram/ip_ram.v#../ram/ip_ram.v#';                                refuse ".gprj path outside the project" "$T/H2"
refuse "output folder inside HRA!'s folder" "$H" "$H/geo3d_out"
cp -a "$T/A" "$T/K"
mut src/v9968/vdp.v 's/vdp_command u_command/vdp_command u_cmd/'
$GEN --hra "$T/H2" --out "$T/K" --rtl "$T/rtl" > /dev/null 2>&1
diff -r "$T/A" "$T/K" > /dev/null; ok $? "a refusal leaves an existing project untouched"

echo "== a clone of HRA!'s repository alone (as geo3d/syn/cartridge/synth_cartridge.sh uses it)"
$GEN --rtl "$T/rtl" --out "$T/S/fpga/V9968_Cartridge_TangNano20K_geo3d" "$T/hra" > /dev/null
diff -r "$T/A" "$T/S/fpga/V9968_Cartridge_TangNano20K_geo3d" > /dev/null; ok $? "<repo root> without geo3d/, with --rtl"

echo "== $pass of $((pass + fail)) tests pass"
[ $fail = 0 ]
