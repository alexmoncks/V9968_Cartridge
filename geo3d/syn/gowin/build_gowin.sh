#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
# Builds the cartridge + geo3d bitstream with Gowin EDA (command line, gw_sh)
# on a COPY of the project, outside the repository. The repository is never
# written to: fpga/V9968_Cartridge_TangNano20K/impl/ keeps HRA!'s original
# build (the recovery bitstream).
#
# The copy has the repository layout, because the .gprj refers to the geo3d
# sources as ../../geo3d/rtl:
#   $WORK/fpga/V9968_Cartridge_TangNano20K   src/, .gprj, impl/*process_config.json
#   $WORK/geo3d/rtl
# The copy's SDC gets extra report_timing commands (setup and hold for every
# clock pair, recovery, removal). The constraints themselves are not changed.
# Gowin's "Total Negative Slack Summary" shows 0 even when inter-clock paths
# fail, so the result is judged from these path tables: any negative slack
# makes the build fail (exit 2), with the bitstream still produced.
#
# Needs: Gowin EDA (V1.9.12.03 or later) and a patched fpga tree
# (python3 geo3d/integration/apply_geo3d_patch.py <repo root>).
#   GOWIN_HOME  install folder     (default C:/Gowin/Gowin_V1.9.12.03_x64)
#   WORK        build folder       (default: geo3d_gowin_build next to the repo)
#   OUT         results folder     (default: $WORK/out)
#   GW_OPTIONS  extra "set_option" arguments (default: none, i.e. the
#               project's own impl/*_process_config.json, as in the Gowin IDE)
# The build uses HRA!'s project settings unchanged (Place Option 0), so it
# gives the same bitstream as opening the patched .gprj in the Gowin IDE and
# running "Run All"; with HRA!'s stock design this flow reproduces his impl/
# bitstream bit for bit. Timing closes on every clock pair with these
# settings; GW_OPTIONS (e.g. "-place_option 1") is for experiments only.
# Usage: sh build_gowin.sh
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
NAME=tangnano20k_vdp_cartridge
PDIR=fpga/V9968_Cartridge_TangNano20K
WORK=${WORK:-$(cd "$REPO/.." && pwd)/geo3d_gowin_build}
OUT=${OUT:-$WORK/out}
GW_OPTIONS=${GW_OPTIONS:-}

GW_SH="${GOWIN_HOME:-C:/Gowin/Gowin_V1.9.12.03_x64}/IDE/bin/gw_sh"
[ -x "$GW_SH" ] || [ -x "$GW_SH.exe" ] || { echo "gw_sh not found: set GOWIN_HOME"; exit 1; }

grep -q geo3d_engine.v "$REPO/$PDIR/$NAME.gprj" && grep -q clk42g "$REPO/$PDIR/src/$NAME.v" \
  && grep -q "^set_clock_uncertainty .*clk42g" "$REPO/$PDIR/src/$NAME.sdc" \
  || { echo "fpga tree not patched: run python3 geo3d/integration/apply_geo3d_patch.py $REPO"; exit 1; }

mkdir -p "$WORK"
WORK=$(cd "$WORK" && pwd)
case "$WORK/" in "$REPO"/*) echo "WORK must be outside the repository ($REPO)"; exit 1;; esac

# ---------------------------------------------------------------- fresh copy
echo "== copy: $WORK"
rm -rf "$WORK/fpga" "$WORK/geo3d"
rm -f "$OUT/geo3d_cartridge.fs" "$OUT/summary.txt"
mkdir -p "$WORK/$PDIR/impl" "$WORK/geo3d" "$OUT"
cp -r "$REPO/$PDIR/src" "$WORK/$PDIR/"
cp "$REPO/$PDIR/$NAME.gprj" "$WORK/$PDIR/"
cp "$REPO/$PDIR/impl/${NAME}_process_config.json" "$WORK/$PDIR/impl/"
cp -r "$REPO/geo3d/rtl" "$WORK/geo3d/"

# path tables for every clock pair (report commands only)
CLOCKS="clk85m clk42m clk42g clk215m clk"
{
  printf '\n# --- build_gowin.sh: extra timing reports (copy only, not in the repository) ---\n'
  for a in $CLOCKS; do for b in $CLOCKS; do for t in setup hold; do
    printf 'report_timing -%s -from_clock [get_clocks {%s}] -to_clock [get_clocks {%s}] -max_paths 50\n' $t $a $b
  done; done; done
  printf 'report_timing -recovery -max_paths 50\nreport_timing -removal -max_paths 50\n'
} >> "$WORK/$PDIR/src/$NAME.sdc"

# gw_sh wants native paths
native() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else echo "$1"; fi; }
{
  echo "open_project $(native "$WORK/$PDIR/$NAME.gprj")"
  [ -z "$GW_OPTIONS" ] || echo "set_option $GW_OPTIONS"
  echo "run all"
} > "$WORK/build.tcl"

# ---------------------------------------------------------------- build
echo "== Gowin EDA: synthesis, place & route, bitstream"
cd "$WORK"
"$GW_SH" build.tcl > gw_sh.log 2>&1 || true
IMPL="$WORK/$PDIR/impl"
if grep -qiE "license verification failed|^ERROR" gw_sh.log || [ ! -f "$IMPL/pnr/$NAME.fs" ]; then
    tail -20 gw_sh.log
    echo "== build failed, see $WORK/gw_sh.log"
    exit 1
fi
cp "$IMPL/pnr/$NAME.fs"                     "$OUT/geo3d_cartridge.fs"
cp "$IMPL/gwsynthesis/$NAME.log"            "$OUT/syn.log"
cp "$IMPL/gwsynthesis/${NAME}_syn.rpt.html" "$OUT/syn.rpt.html"
cp "$IMPL/pnr/$NAME.log"                    "$OUT/pnr.log"
cp "$IMPL/pnr/$NAME.rpt.html"               "$OUT/utilization.rpt.html"
cp "$IMPL/pnr/$NAME.rpt.txt"                "$OUT/utilization.rpt.txt"
cp "$IMPL/pnr/${NAME}_tr_content.html"      "$OUT/timing.html"
cp gw_sh.log "$OUT/"

# ---------------------------------------------------------------- summary
{
echo "== resources"
sed -n '/Resource Usage Summary/,/I\/O Bank Usage Summary/p' "$OUT/utilization.rpt.txt" \
  | grep -E "^\s*(Logic|--LUT|Register|CLS|I/O Port|BSRAM|DSP) " || true
echo "== clock resources and global clock signals"
sed -n '/Clock Resource Usage Summary/,/Pinout by Port Name/p' "$OUT/utilization.rpt.txt" | grep "|" | grep -v "^\s*\(Clock Resource\|Signal\)" || true
echo "== Fmax (clock, constraint, achieved)"
sed -n '/Max_Frequency_Report/,/Total_Negative_Slack_Report/p' "$OUT/timing.html" \
  | sed 's/<[^>]*>//g; s/^[ \t]*//; s/\r//' | grep -E "^clk|MHz" | paste - - - | sed 's/^/  /' || true
echo "== path tables: check, from clock -> to clock, paths listed (max 50), negative, worst slack (ns), worst path"
# first occurrence of each report command = its path slack table;
# columns: number, slack, from, to, from clock, to clock, relation, skew, delay
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
    printf "  reports without any path (clock pairs with no connection): %d\n", empty
    printf "  negative-slack paths in all tables: %d\n", bad
}' "$OUT/timing.html"
} | tee "$OUT/summary.txt"

echo "== bitstream: $OUT/geo3d_cartridge.fs"
grep -q "negative-slack paths in all tables: 0$" "$OUT/summary.txt" || { echo "== TIMING FAILS: see $OUT/timing.html"; exit 2; }
echo "== timing clean (setup, hold, recovery, removal, every clock pair)"
