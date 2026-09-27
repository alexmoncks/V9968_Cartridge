#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
# Builds the Gowin project of the cartridge with geo3d,
# fpga/V9968_Cartridge_TangNano20K_geo3d, IN PLACE with Gowin EDA (gw_sh):
# open_project + run all, which is what the Gowin IDE's "Run All" does, with
# the project's own settings (impl/*_process_config.json, a copy of HRA!'s:
# Place Option 0). The results land in that project's impl/ (gwsynthesis/,
# pnr/; the bitstream is impl/pnr/tangnano20k_vdp_cartridge.fs), laid out as
# HRA!'s own build in fpga/V9968_Cartridge_TangNano20K/impl, which is never
# touched.
#
# Timing check. Gowin's "Total Negative Slack" summary shows 0 even when
# inter-clock paths fail, so the result is judged from path tables: setup and
# hold for every pair of the clocks the project's SDC defines, recovery and
# removal. Those need report_timing commands in the SDC, which do not belong
# in the project, so a second build runs on a copy outside the repository
# ($WORK/timing_check) whose SDC gets them appended (report commands only,
# the constraints are not changed). The copy must give the same bitstream as
# the in-place build (the "//" header lines aside, e.g. "Created Time"), so
# its tables describe the bitstream in impl/. Any negative slack fails the
# script (exit 2); a different bitstream in the copy exits 3. Both leave the
# in-place build in impl/.
#
# Runs from Git Bash on Windows (where Gowin EDA is installed).
# Needs: Gowin EDA (V1.9.12.03 or later), and Python 3 for the check that
# the geo3d project is current (apply_geo3d_patch.py --check, which stops the
# build if not): python3, python or py -3 on Windows, or else WSL's python3.
#   GOWIN_HOME  install folder  (default C:/Gowin/Gowin_V1.9.12.03_x64)
#   WORK        timing-check copy and gw_sh logs (default: geo3d_gowin_build
#               next to the repository; must be outside it)
#   OUT         reports: summary.txt, timing.html (path tables), utilization,
#               logs (default: $WORK/out)
# With HRA!'s stock project this flow reproduces his impl/ bitstream bit for
# bit.
# Usage: sh geo3d/syn/gowin/build_gowin.sh
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
NAME=tangnano20k_vdp_cartridge
PDIR=fpga/V9968_Cartridge_TangNano20K_geo3d
PROJ=$REPO/$PDIR
WORK=${WORK:-$(cd "$REPO/.." && pwd)/geo3d_gowin_build}
OUT=${OUT:-$WORK/out}

GW_SH="${GOWIN_HOME:-C:/Gowin/Gowin_V1.9.12.03_x64}/IDE/bin/gw_sh"
[ -x "$GW_SH" ] || [ -x "$GW_SH.exe" ] || { echo "gw_sh not found: set GOWIN_HOME"; exit 1; }
[ -f "$PROJ/$NAME.gprj" ] || { echo "$PDIR not found: run python3 geo3d/integration/apply_geo3d_patch.py"; exit 1; }

# ---------------------------------------------------------------- project current?
# apply_geo3d_patch.py --check, run from the repository root with the first
# Python 3 found (the Microsoft Store "python3" alias is not one: it fails the
# version test); WSL's python3 gets the same folder as its working directory
check_project() {
    for py in python3 python "py -3"; do
        if $py -c "import sys; sys.exit(sys.version_info < (3, 6))" >/dev/null 2>&1; then
            (cd "$REPO" && $py geo3d/integration/apply_geo3d_patch.py --check)
            return
        fi
    done
    if command -v wsl.exe >/dev/null 2>&1 && (cd "$REPO" && wsl.exe -e python3 -c "") >/dev/null 2>&1; then
        (cd "$REPO" && wsl.exe -e python3 geo3d/integration/apply_geo3d_patch.py --check)
        return
    fi
    echo "Python 3 not found (python3, python, py -3, or python3 in WSL): cannot check that $PDIR is current"
    return 1
}
echo "== geo3d project: $PDIR"
check_project || { echo "== $PDIR is not current or could not be checked: nothing was built"; exit 1; }

mkdir -p "$WORK"
WORK=$(cd "$WORK" && pwd)
case "$WORK/" in "$REPO"/*) echo "WORK must be outside the repository ($REPO)"; exit 1;; esac
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
rm -f "$OUT/summary.txt" "$OUT/timing.html" "$OUT/geo3d_cartridge.fs"

# gw_sh wants native paths
native() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else echo "$1"; fi; }
# gw_sh <tcl> <log> <project dir>: open_project + run all, as the IDE does
gowin() {
    { echo "open_project $(native "$3/$NAME.gprj")"; echo "run all"; } > "$WORK/$1"
    (cd "$WORK" && "$GW_SH" "$1" > "$2" 2>&1) || true
    if grep -qiE "license verification failed|^ERROR" "$WORK/$2" || [ ! -f "$3/impl/pnr/$NAME.fs" ]; then
        tail -20 "$WORK/$2"
        echo "== build failed, see $WORK/$2"
        exit 1
    fi
}
# the bitstream without its "//" header lines (tool version, checksum, Created Time)
body() { grep -v '^//' "$1" | tr -d '\r'; }

# ---------------------------------------------------------------- in place
echo "== Gowin EDA, in place: synthesis, place & route, bitstream"
rm -rf "$PROJ/impl/gwsynthesis" "$PROJ/impl/pnr"
gowin build.tcl gw_sh.log "$PROJ"
IMPL=$PROJ/impl
# the netlist must come from the project's own files (src/, src/geo3d/)
VG_OUT=$(grep -o '^//file[0-9]* "[^"]*"' "$IMPL/gwsynthesis/$NAME.vg" | sed 's/^[^"]*"\\\{0,1\}//; s/"$//' \
         | grep -vi "/$PDIR/src/" || true)
[ -z "$VG_OUT" ] || { echo "== the netlist was built from files outside $PDIR/src:"; echo "$VG_OUT"; exit 1; }
for f in geo3d_core.v geo3d_engine.v geo3d_bus.v; do
    grep -q "/$PDIR/src/geo3d/$f\"" "$IMPL/gwsynthesis/$NAME.vg" || { echo "== $f is not in the netlist"; exit 1; }
done

# ---------------------------------------------------------------- timing-check copy
C=$WORK/timing_check
echo "== Gowin EDA, timing-check copy (path tables): $C"
rm -rf "$C"
mkdir -p "$C/impl"
cp -r "$PROJ/src" "$C/"
cp "$PROJ/$NAME.gprj" "$C/"
cp "$PROJ/impl/${NAME}_process_config.json" "$C/impl/"
# every clock the project's SDC defines (create_clock / create_generated_clock, not commented out)
CLOCKS=$(tr -d '\r' < "$PROJ/src/$NAME.sdc" \
         | sed -n 's/^[ \t]*create_\(generated_\)\{0,1\}clock[ \t].*-name[ \t][ \t]*\([^ \t]*\).*/\2/p' | sort -u | tr '\n' ' ')
NCLK=$(echo $CLOCKS | wc -w)
TABLES=$((NCLK * NCLK * 2 + 2))
[ "$NCLK" -ge 2 ] || { echo "== no clocks found in $PDIR/src/$NAME.sdc"; exit 1; }
echo "   clocks: $CLOCKS($NCLK: $TABLES path tables)"
{
  printf '\n# --- build_gowin.sh: timing reports (timing-check copy only, not in the project) ---\n'
  for a in $CLOCKS; do for b in $CLOCKS; do for t in setup hold; do
    printf 'report_timing -%s -from_clock [get_clocks {%s}] -to_clock [get_clocks {%s}] -max_paths 50\n' $t $a $b
  done; done; done
  printf 'report_timing -recovery -max_paths 50\nreport_timing -removal -max_paths 50\n'
} >> "$C/src/$NAME.sdc"
gowin timing_check.tcl gw_sh_timing_check.log "$C"

cp "$IMPL/pnr/$NAME.fs"                     "$OUT/geo3d_cartridge.fs"
cp "$IMPL/gwsynthesis/$NAME.log"            "$OUT/syn.log"
cp "$IMPL/gwsynthesis/${NAME}_syn.rpt.html" "$OUT/syn.rpt.html"
cp "$IMPL/pnr/$NAME.log"                    "$OUT/pnr.log"
cp "$IMPL/pnr/$NAME.rpt.html"               "$OUT/utilization.rpt.html"
cp "$IMPL/pnr/$NAME.rpt.txt"                "$OUT/utilization.rpt.txt"
cp "$C/impl/pnr/${NAME}_tr_content.html"    "$OUT/timing.html"
cp "$WORK/gw_sh.log" "$WORK/gw_sh_timing_check.log" "$OUT/"

# ---------------------------------------------------------------- summary
{
echo "== bitstream: $PDIR/impl/pnr/$NAME.fs"
body "$C/impl/pnr/$NAME.fs" > "$WORK/body_copy.tmp"
if body "$IMPL/pnr/$NAME.fs" | cmp -s - "$WORK/body_copy.tmp"; then
    echo "  timing-check copy: same bitstream (header lines aside)"
else
    echo "  timing-check copy: DIFFERENT BITSTREAM"
fi
rm -f "$WORK/body_copy.tmp"
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
    printf "  tables: %d, without any path (clock pairs with no connection): %d\n", n, empty
    printf "  negative-slack paths in all tables: %d\n", bad
}' "$OUT/timing.html"
} | tee "$OUT/summary.txt"

echo "== bitstream: $PDIR/impl/pnr/$NAME.fs (copy: $OUT/geo3d_cartridge.fs)"
grep -q "tables: $TABLES," "$OUT/summary.txt" || { echo "== TIMING NOT CHECKED: expected $TABLES path tables, see $OUT/timing.html"; exit 2; }
grep -q "negative-slack paths in all tables: 0$" "$OUT/summary.txt" || { echo "== TIMING FAILS: see $OUT/timing.html"; exit 2; }
grep -q "same bitstream" "$OUT/summary.txt" || { echo "== the timing-check copy gave another bitstream: its tables do not describe impl/"; exit 3; }
echo "== timing clean (setup, hold, recovery, removal, every clock pair)"
