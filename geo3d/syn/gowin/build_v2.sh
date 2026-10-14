#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
# Builds HRA!'s official cartridge project (fpga/V9968_Cartridge_TangNano20K,
# which carries geo3d v1.0.0 since his 3e81e92) with the geo3d RTL of this
# branch in the place of his src/geo3d/, with Gowin EDA (gw_sh): open_project
# + run all, his own project settings. His folder is only read: the build
# runs on a copy outside the repository, $WORK/<what>.
#
#   sh geo3d/syn/gowin/build_v2.sh [v2 | official]
#     v2        his project, src/geo3d/ replaced by geo3d/rtl (default)
#     official  his project as it is: the reference for the comparison
#
# Timing is judged from path tables, as build_gowin.sh does (Gowin's "Total
# Negative Slack" summary shows 0 even when inter-clock paths fail): the
# copy's SDC gets report_timing commands appended, setup and hold for every
# pair of the clocks it defines, recovery and removal. They are report
# commands only; the constraints are HRA!'s. Any negative slack: exit 2.
#
# Runs from Git Bash on Windows. Needs Gowin EDA (V1.9.12.03 or later).
#   GOWIN_HOME  install folder  (default C:/Gowin/Gowin_V1.9.12.03_x64)
#   WORK        where the copies are built (default: geo3d_gowin_build_v2
#               next to the repository; must be outside it)
#   OPTIONS     place & route settings of the copy that differ from HRA!'s,
#               "Key=value ..." as in his impl/*_process_config.json, e.g.
#               OPTIONS="Route_Option=2". His file is not changed.
#   CST         a file of physical constraints appended to the copy's .cst
#               (geo3d_v2_bsram.cst here: where geo3d's block RAMs go)
#   AW          the engine's AW parameter in the copy (default: the RTL's,
#               10 = 1024 entries in each model RAM; 9 = 512)
#   TAG         suffix of the copy's folder (to keep several builds)
# Results: $WORK/<what>[_<tag>]/impl/pnr/tangnano20k_vdp_cartridge.fs (the
# bitstream) and summary.txt in the same folder.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
NAME=tangnano20k_vdp_cartridge
HRA=$REPO/fpga/V9968_Cartridge_TangNano20K
WHAT=${1:-v2}
WORK=${WORK:-$(cd "$REPO/.." && pwd)/geo3d_gowin_build_v2}

GW_SH="${GOWIN_HOME:-C:/Gowin/Gowin_V1.9.12.03_x64}/IDE/bin/gw_sh"
[ -x "$GW_SH" ] || [ -x "$GW_SH.exe" ] || { echo "gw_sh not found: set GOWIN_HOME"; exit 1; }
[ -f "$HRA/src/geo3d/geo3d_engine.v" ] || { echo "$HRA does not carry geo3d: merge HRA!'s upstream"; exit 1; }
case "$WHAT" in v2|official) ;; *) echo "usage: build_v2.sh [v2 | official]"; exit 1;; esac

mkdir -p "$WORK"
WORK=$(cd "$WORK" && pwd)
case "$WORK/" in "$REPO"/*) echo "WORK must be outside the repository ($REPO)"; exit 1;; esac
C=$WORK/$WHAT${TAG:+_$TAG}
rm -rf "$C"
mkdir -p "$C/impl"
cp -r "$HRA/src" "$C/"
cp "$HRA/$NAME.gprj" "$C/"
cp "$HRA/impl/${NAME}_process_config.json" "$C/impl/"
if [ "$WHAT" = v2 ]; then
    for f in geo3d_bus.v geo3d_core.v geo3d_engine.v; do cp "$REPO/geo3d/rtl/$f" "$C/src/geo3d/$f"; done
    # AW: the engine's parameter (address bits of its RAMs) in this copy
    if [ -n "$AW" ]; then
        grep -q "parameter AW = 10 " "$C/src/geo3d/geo3d_engine.v" || { echo "parameter AW not found in the engine"; exit 1; }
        sed -i "s/parameter AW = 10 /parameter AW = $AW /" "$C/src/geo3d/geo3d_engine.v"
        echo "   engine parameter AW = $AW ($((1 << AW)) entries in each RAM)"
    fi
fi
echo "== $WHAT: $C"
# CST: more physical constraints for the copy (geo3d's own instances)
if [ -n "$CST" ]; then
    [ -f "$CST" ] || { echo "no such file: $CST"; exit 1; }
    { printf '\n'; grep -v '^[ \t]*//' "$CST"; } >> "$C/src/$NAME.cst"
    echo "   constraints added: $CST ($(grep -c INS_LOC "$CST") INS_LOC)"
fi
# OPTIONS: the copy's own place & route settings, e.g. "Route_Option=2"
for o in $OPTIONS; do
    k=${o%%=*}; v=${o#*=}
    grep -q "\"$k\":" "$C/impl/${NAME}_process_config.json" || { echo "no setting $k in the project"; exit 1; }
    sed -i "s/\(\"$k\": *\"\{0,1\}\)[^\",]*/\1$v/" "$C/impl/${NAME}_process_config.json"
    echo "   $(grep "\"$k\":" "$C/impl/${NAME}_process_config.json" | tr -d '\r' | sed 's/^ *//; s/,$//')  (HRA!: $(grep "\"$k\":" "$HRA/impl/${NAME}_process_config.json" | tr -d '\r' | sed 's/^ *[^:]*: *//; s/,$//'))"
done
for f in geo3d_bus.v geo3d_core.v geo3d_engine.v; do
    if cmp -s "$HRA/src/geo3d/$f" "$C/src/geo3d/$f"; then echo "   $f: HRA!'s (v1.0.0)"; else echo "   $f: geo3d/rtl of this branch"; fi
done

# every clock the SDC defines (create_clock / create_generated_clock, not commented out)
CLOCKS=$(tr -d '\r' < "$C/src/$NAME.sdc" \
         | sed -n 's/^[ \t]*create_\(generated_\)\{0,1\}clock[ \t].*-name[ \t][ \t]*\([^ \t]*\).*/\2/p' | sort -u | tr '\n' ' ')
NCLK=$(echo $CLOCKS | wc -w)
TABLES=$((NCLK * NCLK * 2 + 2))
[ "$NCLK" -ge 2 ] || { echo "== no clocks found in the SDC"; exit 1; }
echo "   clocks: $CLOCKS($NCLK: $TABLES path tables)"
{
  printf '\n# --- build_v2.sh: timing reports (this copy only) ---\n'
  for a in $CLOCKS; do for b in $CLOCKS; do for t in setup hold; do
    printf 'report_timing -%s -from_clock [get_clocks {%s}] -to_clock [get_clocks {%s}] -max_paths 50\n' $t $a $b
  done; done; done
  printf 'report_timing -recovery -max_paths 50\nreport_timing -removal -max_paths 50\n'
} >> "$C/src/$NAME.sdc"

native() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else echo "$1"; fi; }
echo "== Gowin EDA: synthesis, place & route, bitstream"
{ echo "open_project $(native "$C/$NAME.gprj")"; echo "run all"; } > "$C/build.tcl"
(cd "$C" && "$GW_SH" build.tcl > gw_sh.log 2>&1) || true
if grep -qiE "license verification failed|^ERROR" "$C/gw_sh.log" || [ ! -f "$C/impl/pnr/$NAME.fs" ]; then
    tail -20 "$C/gw_sh.log"
    echo "== build failed, see $C/gw_sh.log"
    exit 1
fi

{
echo "== $WHAT: bitstream $C/impl/pnr/$NAME.fs"
echo "== resources"
sed -n '/Resource Usage Summary/,/I\/O Bank Usage Summary/p' "$C/impl/pnr/$NAME.rpt.txt" \
  | grep -E "^\s*(Logic|--LUT|Register|CLS|I/O Port|BSRAM|DSP) " || true
echo "== Fmax (clock, constraint, achieved)"
sed -n '/Max_Frequency_Report/,/Total_Negative_Slack_Report/p' "$C/impl/pnr/${NAME}_tr_content.html" \
  | sed 's/<[^>]*>//g; s/^[ \t]*//; s/\r//' | grep -E "^clk|MHz" | paste - - - | sed 's/^/  /' || true
echo "== path tables: check, from clock -> to clock, paths listed (max 50), negative, worst slack (ns), worst path"
awk '
/Report Command:report_timing/ {
    c = $0; sub(/.*Report Command:report_timing /, "", c); sub(/<\/h4>.*/, "", c); sub(/\r$/, "", c)
    if (c in seen) { cur = "" } else { seen[c] = 1; cur = c; order[++n] = c; rows[c] = 0; neg[c] = 0 }
    col = 0; next
}
cur == "" { next }
/<\/table>/ { cur = ""; next }
/<tr/ { col = 0; next }
/<td/ {
    col++; v = $0; gsub(/<[^>]*>/, "", v); gsub(/[ \t\r]/, "", v)
    if (col == 2) { rows[cur]++; if (rows[cur] == 1) worst[cur] = v; if (v + 0 < 0) neg[cur]++ }
    if (col == 3 && rows[cur] == 1) wf[cur] = v
    if (col == 4 && rows[cur] == 1) wt[cur] = v
}
END {
    bad = 0; empty = 0
    for (i = 1; i <= n; i++) {
        c = order[i]
        if (rows[c] == 0) { empty++; continue }
        t = c; sub(/ .*/, "", t); sub(/^-/, "", t)
        f = c; if (sub(/.*-from_clock \[get_clocks \{/, "", f)) sub(/\}.*/, "", f); else f = "all"
        g = c; if (sub(/.*-to_clock \[get_clocks \{/, "", g)) sub(/\}.*/, "", g); else g = "all"
        printf "  %-8s %-7s -> %-7s %4d %4d %8s  %s -> %s\n", t, f, g, rows[c], neg[c], worst[c], wf[c], wt[c]
        bad += neg[c]
    }
    printf "  tables: %d, without any path (clock pairs with no connection): %d\n", n, empty
    printf "  negative-slack paths in all tables: %d\n", bad
}' "$C/impl/pnr/${NAME}_tr_content.html"
} | tee "$C/summary.txt"

grep -q "tables: $TABLES," "$C/summary.txt" || { echo "== TIMING NOT CHECKED: expected $TABLES path tables"; exit 2; }
grep -q "negative-slack paths in all tables: 0$" "$C/summary.txt" || { echo "== TIMING FAILS: see $C/impl/pnr/${NAME}_tr_content.html"; exit 2; }
echo "== timing clean (setup, hold, recovery, removal, every clock pair)"
