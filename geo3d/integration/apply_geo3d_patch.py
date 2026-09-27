#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Generates the Gowin project of the cartridge with geo3d,
fpga/V9968_Cartridge_TangNano20K_geo3d, from HRA!'s project
fpga/V9968_Cartridge_TangNano20K.

HRA!'s folder is only read: it stays exactly as his upstream (his sources and
his own build in impl/, the recovery bitstream), so merging his updates never
conflicts. The geo3d project next to it has his layout and his file names:

  tangnano20k_vdp_cartridge.gprj      his file list plus src/geo3d/*.v
  src/                                the files his .gprj lists (plus the IP
                                      generator files of his IP cores), 4 patched
  src/geo3d/                          copies of geo3d/rtl, the single source of
                                      truth: edit geo3d/rtl, then run this again
  impl/tangnano20k_vdp_cartridge_process_config.json
                                      his project settings, unchanged
  README.md, geo3d_manifest.txt       what the folder is; what this script
                                      generated there (see "Hand edits")
  impl/gwsynthesis, impl/pnr, impl/temp, *.gprj.user
                                      Gowin outputs (geo3d/syn/gowin/build_gowin.sh
                                      or the IDE's Run All); never touched here

The patch:
  1. src/v9968/vdp.v: adds an external command-register write port
     (ext_cmd_wr/num/data) OR-ed into vdp_command, and exposes CE (ext_cmd_ce).
     Nothing else in the VDP changes; with ext_cmd_wr = 0 it is bit-identical.
  2. src/tangnano20k_vdp_cartridge.v: instantiates geo3d_bus on offsets 5 and 7
     of the slot's port block (bus side on clk85m, engine on clk42g), keeps
     those offsets away from the VDP, and muxes read data / ready.
  3. tangnano20k_vdp_cartridge.gprj: adds src/geo3d/geo3d_core.v,
     geo3d_engine.v and geo3d_bus.v (paths inside the project).
  4. src/gowin_rpll2/gowin_rpll2.v: exposes the rPLL's CLKOUTD output, which
     the IP already configures as CLKOUT / 2 (DYN_SDIV_SEL = 2) but leaves
     unused. The top routes it to a new net, clk42g (42.95 MHz), used only by
     the geo3d engine. Same PLL as clk85m, so the geo3d_bus crossings see two
     related, phase-aligned clocks. HRA!'s clk42m (Gowin_CLKDIV) is left as it
     is and still clocks the HDMI side and the rest of his design: in Gowin's
     timing model the CLKDIV output has no insertion delay while the PLL
     outputs have 3-4 ns, which broke the clk85m <-> clk42m crossings.
     (The .ipc / .mod files of the IP are copied unchanged: regenerating the
     IP in the Gowin GUI drops the clkoutd port and synthesis then fails
     loudly; run this script again.)
  5. src/tangnano20k_vdp_cartridge.sdc: declares clk42g as a clock generated
     from clk14m (x3), like clk85m (x6), plus 0.5 ns of clock uncertainty on
     the clk85m <-> clk42g crossings (Gowin gives CLKOUTD exactly CLKOUT's
     insertion delay; the divider's own delay is not modelled). HRA!'s clocks
     and groups unchanged.
Checked on every run, against HRA!'s text: the rPLL2 settings, the u_pll2
hookup and the SDC clocks that clk42g relies on; the bus read mux, exactly as
upstream has it (a new read source or any other change there is refused, not
overwritten); the declaration and width of every net of his top that the
geo3d hookup uses; and the slot and VDP ports those nets connect to.

Every run rebuilds the .gprj, src/ and the process settings from HRA!'s folder
and geo3d/rtl (files under src/ that are no longer part of the project are
removed), so running it twice gives the same tree. Nothing is written if
HRA!'s text has moved or if his folder already carries geo3d changes.
Files are copied byte for byte (Shift-JIS comments, CRLF are kept).

Hand edits. geo3d_manifest.txt lists what the script generated last time
(SHA-256 of each file's text, LF line endings). A file that differs from both
that and the new output was edited by hand (for example in the Gowin IDE,
which shows the copies in src/geo3d, not geo3d/rtl), and so is a file under
src/ that the script did not generate: the script then refuses and writes
nothing. Move the edit where it belongs (geo3d/rtl, HRA!'s upstream, or the
patch in this script) and run it again, or use --force, which backs those
files up next to the project folder and then overwrites them.

--check writes nothing: it verifies that the geo3d project is exactly what
this script generates now (line endings aside), in particular that src/geo3d
equals geo3d/rtl, and says which differences are hand edits. Exit status 1
if the project is not current.

Timing closes with the project's own settings (Place Option 0), so the Gowin
IDE (Run All on the .gprj) and geo3d/syn/gowin/build_gowin.sh give the same
bitstream.

Runs in any shell with Python 3 (Linux, WSL, macOS, Windows).
Usage: python3 apply_geo3d_patch.py [--check | --force] [<repo root>]
       python3 apply_geo3d_patch.py [--check | --force] --hra <HRA!'s project folder>
                                    --out <geo3d project folder> [--rtl <geo3d/rtl>]
"""
import argparse
import hashlib
import re
import shutil
import sys
import time
from pathlib import Path

NAME = "tangnano20k_vdp_cartridge"
HRA_DIR = "fpga/V9968_Cartridge_TangNano20K"
OUT_DIR = "fpga/V9968_Cartridge_TangNano20K_geo3d"
GEO3D_RTL = ("geo3d_core.v", "geo3d_engine.v", "geo3d_bus.v")
GEO3D_SRC = "src/geo3d"
GPRJ = f"{NAME}.gprj"
PROCESS_CONFIG = f"impl/{NAME}_process_config.json"
MANIFEST = "geo3d_manifest.txt"
# IP Core Generator files kept next to an IP's .v (so the IP opens in the IDE)
IP_FILES = (".ipc", ".mod", "_tmp.v", ".vo")
# files that get the patch (relative to the project folder)
VDP_V = "src/v9968/vdp.v"
TOP_V = f"src/{NAME}.v"
PLL2_V = "src/gowin_rpll2/gowin_rpll2.v"
SDC = f"src/{NAME}.sdc"


class Refuse(Exception):
    """HRA!'s text is not what the patch expects: nothing is written."""


def decode(b: bytes):
    """Byte-preserving text (upstream files mix Shift-JIS comments and CRLF)."""
    raw = b.decode("latin-1")
    return raw.replace("\r\n", "\n"), "\r\n" in raw


def encode(s: str, crlf: bool) -> bytes:
    return (s.replace("\n", "\r\n") if crlf else s).encode("latin-1")


def pristine(s: str, what: str):
    if re.search(r"geo3d|ext_cmd_|clk42g", s):
        raise Refuse(f"HRA!'s {what} already carries geo3d changes: HRA!'s folder must be "
                     f"his upstream content (git restore --source=upstream/main -- {HRA_DIR})")


def instance(s: str, head: str, what: str):
    """Returns (start, end) of the port list of instance `head` ("module name")."""
    i = s.find(head)
    if i < 0 or s.find(head, i + 1) >= 0:
        raise Refuse(f"{what}: instance '{head}' not found exactly once")
    j = s.find(");", i)
    if j < 0:
        raise Refuse(f"{what}: end of instance '{head}' not found")
    return i, j


def uncomment(s: str) -> str:
    """Verilog text without its comments (for the checks on declarations and drivers)."""
    return re.sub(r"//[^\n]*|/\*.*?\*/", " ", s, flags=re.S)


# ------------------------------------------------------------------ vdp.v
def patch_vdp(s: str) -> str:
    pristine(s, "vdp.v")
    anchor = (re.search(r"\n([ \t]*)//[ \t]*debug pulse", s) or
              re.search(r"\n([ \t]*)input\s+\[\s*1:0\]\s+button,", s))
    if not anchor:
        raise Refuse("vdp.v: port anchor 'button' not found")
    ind = anchor.group(1)
    ports = (f"\n{ind}input\t\t\t\text_cmd_wr,\t\t\t//\tgeo3d: external command register write"
             f"\n{ind}input\t\t[5:0]\text_cmd_num,"
             f"\n{ind}input\t\t[7:0]\text_cmd_data,"
             f"\n{ind}output\t\t\t\text_cmd_ce,\t\t\t//\tgeo3d: S#2 CE\n")
    s = s[:anchor.start()] + ports + s[anchor.start():]

    i, j = instance(s, "vdp_command u_command", "vdp.v")
    blk = s[i:j]
    new = blk
    new, n1 = re.subn(r"\(\s*w_register_write\s*\)", "( w_register_write | ext_cmd_wr )", new)
    new, n2 = re.subn(r"\(\s*w_register_num\s*\)", "( ext_cmd_wr ? ext_cmd_num  : w_register_num  )", new)
    new, n3 = re.subn(r"\(\s*w_register_data\s*\)", "( ext_cmd_wr ? ext_cmd_data : w_register_data )", new)
    if (n1, n2, n3) != (1, 1, 1):
        raise Refuse(f"vdp.v: command register hookup not found {n1, n2, n3}")
    s = s[:i] + new + s[j:]

    k = s.rfind("endmodule")
    if k < 0:
        raise Refuse("vdp.v: endmodule not found")
    return s[:k] + "\t//\tgeo3d: expose command-execute status\n\tassign ext_cmd_ce = w_status_command_execute;\n\n" + s[k:]


# ------------------------------------------------------------------ top
# Nets of HRA!'s top that the geo3d hookup uses, with their declared width.
# His top relies on implicit nets in places (Gowin only warns "Undeclared
# symbol"), so a renamed net would leave geo3d silently unconnected: each one
# must be declared exactly once, with this width.
TOP_NETS = (("clk85m", ""), ("reset_n3", ""), ("w_bus_address", "[2:0]"), ("w_bus_ioreq", ""),
            ("w_bus_write", ""), ("w_bus_valid", ""), ("w_bus_wdata", "[7:0]"),
            ("w_bus_ready", ""), ("w_bus_rdata", "[7:0]"), ("w_bus_rdata_en", ""),
            ("w_bus_vdp_ready", ""), ("w_bus_vdp_rdata", "[7:0]"), ("w_bus_vdp_rdata_en", ""))
# ... and what they must be connected to: the slot's bus (geo3d_bus listens to
# it and drives its read side through the mux) and the VDP's (whose reset and
# read side the mux merges with geo3d's)
SLOT_PORTS = (("clk", "clk85m"), ("bus_address", "w_bus_address"), ("bus_ioreq", "w_bus_ioreq"),
              ("bus_write", "w_bus_write"), ("bus_valid", "w_bus_valid"),
              ("bus_ready", "w_bus_ready"), ("bus_wdata", "w_bus_wdata"),
              ("bus_rdata", "w_bus_rdata"), ("bus_rdata_en", "w_bus_rdata_en"))
VDP_PORTS = (("reset_n", "reset_n3"), ("clk", "clk85m"), ("bus_address", "w_bus_address"),
             ("bus_ioreq", "w_bus_ioreq"), ("bus_write", "w_bus_write"),
             ("bus_valid", "w_bus_valid"), ("bus_ready", "w_bus_vdp_ready"),
             ("bus_wdata", "w_bus_wdata"), ("bus_rdata", "w_bus_vdp_rdata"),
             ("bus_rdata_en", "w_bus_vdp_rdata_en"))
# The bus read mux, exactly as upstream 86361d8 has it (only spacing may
# differ): three consecutive lines, the VDP as the only read source. It is
# replaced by the mux with geo3d, so anything else there (a new read source,
# a line in between) would be lost: refused instead.
WS = r"[ \t]*"
BUS_MUX = re.compile(
    rf"^{WS}assign{WS}w_bus_rdata{WS}={WS}\({WS}w_bus_vdp_rdata_en{WS}\){WS}\?{WS}w_bus_vdp_rdata{WS}:{WS}8'hFF{WS};{WS}\n"
    rf"{WS}assign{WS}w_bus_rdata_en{WS}={WS}w_bus_vdp_rdata_en{WS};{WS}\n"
    rf"{WS}assign{WS}w_bus_ready{WS}={WS}w_bus_vdp_ready{WS};{WS}\n", re.M)


def declarations(s: str):
    """{net: [width, ...]} of the wire / reg declarations in `s` (comments removed)."""
    out = {}
    for m in re.finditer(r"\b(?:wire|reg)\b\s*(\[[^\]]*\])?([^;]*);", s):
        width = re.sub(r"\s", "", m.group(1) or "")
        for item in m.group(2).split(","):
            name = re.match(r"\s*([A-Za-z_]\w*)", item)
            if name:
                out.setdefault(name.group(1), []).append(width)
    return out


def check_top(s: str):
    """HRA!'s top, before the patch: the nets, connections and mux geo3d relies on."""
    code = uncomment(s)
    decl = declarations(code)
    for net, width in TOP_NETS:
        if decl.get(net) != [width]:
            got = " / ".join(w or "1 bit" for w in decl.get(net, [])) or "not declared"
            raise Refuse(f"top: net {net} is {got}, not one 'wire {width + ' ' if width else ''}{net};' "
                         "(geo3d_bus is hooked to it)")
    for head, ports in (("msx_slot u_msx_slot", SLOT_PORTS), ("vdp u_v9958", VDP_PORTS)):
        i, j = instance(s, head, "top")
        for port, net in ports:
            if not re.search(rf"\.{port}\s*\(\s*{net}\s*\)", s[i:j]):
                raise Refuse(f"top: {head} .{port} is no longer connected to {net}")
    if len(BUS_MUX.findall(s)) != 1:
        raise Refuse("top: the bus read mux (assign w_bus_rdata / w_bus_rdata_en / w_bus_ready) "
                     "is not exactly upstream's, with the VDP as its only source: merge the "
                     "change into the geo3d mux in this script (add_geo3d_bus)")
    for net in ("w_bus_rdata", "w_bus_rdata_en", "w_bus_ready"):
        if len(re.findall(rf"\bassign\s+{net}\s*=", code)) != 1:
            raise Refuse(f"top: {net} is assigned more than once")


def patch_top(s: str) -> str:
    pristine(s, f"{NAME}.v")
    check_top(s)
    s = add_geo3d_bus(s)

    # the clk42g net: declared next to clk42m
    m = re.search(r"\n([ \t]*)wire\s+clk42m\s*;[^\n]*", s)
    if not m:
        raise Refuse("top: 'wire clk42m;' not found")
    s = (s[:m.end()] + f"\n{m.group(1)}wire\t\t\tclk42g;\t\t\t\t//\t42.95454MHz"
         " (geo3d engine: CLKOUTD of u_pll2 = clk85m / 2)" + s[m.end():])

    # ... driven by the CLKOUTD output of the PLL that makes clk85m from clk14m
    # (the SDC's clk42g relies on it)
    i, j = instance(s, "Gowin_rPLL2 u_pll2", "top")
    blk = s[i:j]
    if not re.search(r"\.clkout\s*\(\s*clk85m\s*\)", blk):
        raise Refuse("top: u_pll2 no longer drives clk85m")
    if not re.search(r"\.clkin\s*\(\s*clk14m\s*\)", blk):
        raise Refuse("top: u_pll2 no longer runs from clk14m")
    if re.search(r"\.clkoutd\s*\(", blk):
        raise Refuse("top: u_pll2 .clkoutd is already connected")
    blk, n = re.subn(r"(\n([ \t]*)\.clkin\s*\(\s*clk14m\s*\))",
                     r"\n\2.clkoutd\t\t( clk42g\t\t\t),\t\t//\toutput clkoutd\t42.95454MHz"
                     r" (CLKOUT / 2, geo3d engine only)\1", blk, count=1)
    if n != 1:
        raise Refuse("top: u_pll2 .clkin anchor not found")
    return s[:i] + blk + s[j:]


def add_geo3d_bus(s: str) -> str:
    old_mux = BUS_MUX.search(s)            # found exactly once (check_top)
    new_mux = """\t// --------------------------------------------------------------------
\t//\tgeo3d coprocessor (ports base+5 / base+7)
\t// --------------------------------------------------------------------
\twire\t\t\tw_geo_hit;
\twire\t\t\tw_geo_ready;
\twire\t[7:0]\tw_geo_rdata;
\twire\t\t\tw_geo_rdata_en;
\twire\t\t\tw_geo_cmd_wr;
\twire\t[5:0]\tw_geo_cmd_num;
\twire\t[7:0]\tw_geo_cmd_data;
\twire\t\t\tw_geo_cmd_ce;
\twire\t\t\tw_geo_run_busy;

\tgeo3d_bus u_geo3d (
\t\t.clk\t\t\t\t( clk85m\t\t\t\t\t),
\t\t.clk_eng\t\t\t( clk42g\t\t\t\t\t),
\t\t.reset_n\t\t\t( reset_n3\t\t\t\t\t),
\t\t.bus_address\t\t( w_bus_address\t\t\t\t),
\t\t.bus_ioreq\t\t\t( w_bus_ioreq\t\t\t\t),
\t\t.bus_write\t\t\t( w_bus_write\t\t\t\t),
\t\t.bus_valid\t\t\t( w_bus_valid\t\t\t\t),
\t\t.bus_wdata\t\t\t( w_bus_wdata\t\t\t\t),
\t\t.hit\t\t\t\t( w_geo_hit\t\t\t\t\t),
\t\t.bus_ready\t\t\t( w_geo_ready\t\t\t\t),
\t\t.bus_rdata\t\t\t( w_geo_rdata\t\t\t\t),
\t\t.bus_rdata_en\t\t( w_geo_rdata_en\t\t\t),
\t\t.cmd_wr\t\t\t\t( w_geo_cmd_wr\t\t\t\t),
\t\t.cmd_num\t\t\t( w_geo_cmd_num\t\t\t\t),
\t\t.cmd_data\t\t\t( w_geo_cmd_data\t\t\t),
\t\t.cmd_ce\t\t\t\t( w_geo_cmd_ce\t\t\t\t),
\t\t.run_busy\t\t\t( w_geo_run_busy\t\t\t)
\t);

\tassign w_bus_rdata\t\t= ( w_geo_rdata_en\t\t) ? w_geo_rdata:
\t\t\t\t\t\t  ( w_bus_vdp_rdata_en\t) ? w_bus_vdp_rdata: 8'hFF;
\tassign w_bus_rdata_en\t= w_bus_vdp_rdata_en | w_geo_rdata_en;
\tassign w_bus_ready\t\t= w_geo_hit ? w_geo_ready: w_bus_vdp_ready;
"""
    s = s[:old_mux.start()] + new_mux + s[old_mux.end():]

    i, j = instance(s, "vdp u_v9958", "top")
    blk = s[i:j]
    blk, n = re.subn(r"(\.bus_valid\s*)\(\s*w_bus_valid\s*\)", r"\1( w_bus_valid & ~w_geo_hit\t\t)", blk)
    if n != 1:
        raise Refuse("top: vdp bus_valid not found")
    blk, n = re.subn(r"(\n([ \t]*)\.force_highspeed)",
                     r"\n\2.ext_cmd_wr\t\t( w_geo_cmd_wr\t\t\t\t),"
                     r"\n\2.ext_cmd_num\t\t( w_geo_cmd_num\t\t\t\t),"
                     r"\n\2.ext_cmd_data\t\t( w_geo_cmd_data\t\t\t),"
                     r"\n\2.ext_cmd_ce\t\t( w_geo_cmd_ce\t\t\t\t),\1", blk)
    if n != 1:
        raise Refuse("top: vdp force_highspeed anchor not found")
    return s[:i] + blk + s[j:]


# ------------------------------------------------------------------ rPLL2
# rPLL2 settings that make CLKOUT = clk14m x 6 (85.9 MHz) and CLKOUTD = CLKOUT / 2
# (42.95 MHz) with both edges aligned: checked on every run, so that an update
# of HRA!'s IP cannot leave clk42g mis-described
PLL2_PARAMS = (("FCLKIN", '"14.318"'), ("DYN_IDIV_SEL", '"false"'), ("IDIV_SEL", "0"),
               ("DYN_FBDIV_SEL", '"false"'), ("FBDIV_SEL", "5"), ("CLKFB_SEL", '"internal"'),
               ("CLKOUT_BYPASS", '"false"'), ("CLKOUT_DLY_STEP", "0"),
               ("CLKOUTD_BYPASS", '"false"'), ("CLKOUTD_SRC", '"CLKOUT"'), ("DYN_SDIV_SEL", "2"))


def patch_pll2(s: str) -> str:
    pristine(s, "gowin_rpll2.v")
    for key, val in PLL2_PARAMS:
        got = re.findall(rf"defparam\s+rpll_inst\.{key}\s*=\s*([^;]*?)\s*;", s)
        if got != [val]:
            raise Refuse(f"gowin_rpll2.v: {key} is {' / '.join(got) or 'missing'}, not {val}: "
                         "CLKOUTD would not be clk85m / 2 in phase (clk42g in the SDC)")
    if re.search(r"^output\s+clkoutd\s*;", s, re.M):
        raise Refuse("gowin_rpll2.v: clkoutd is already a port")
    s, n1 = re.subn(r"module\s+Gowin_rPLL2\s*\(\s*clkout\s*,\s*lock\s*,\s*clkoutp\s*,\s*clkin\s*\)\s*;",
                    "module Gowin_rPLL2 (clkout, lock, clkoutp, clkoutd, clkin);", s)
    s, n2 = re.subn(r"(\noutput\s+clkoutp\s*;)",
                    r"\1\noutput clkoutd;\t// geo3d: CLKOUT / 2 = 42.95454MHz (DYN_SDIV_SEL = 2)", s)
    s, n3 = re.subn(r"\nwire\s+clkoutd_o\s*;", "", s)
    s, n4 = re.subn(r"\.CLKOUTD\(\s*clkoutd_o\s*\)", ".CLKOUTD(clkoutd)", s)
    if (n1, n2, n3, n4) != (1, 1, 1, 1):
        raise Refuse(f"gowin_rpll2.v: CLKOUTD hookup not found {n1, n2, n3, n4}")
    return s


# ------------------------------------------------------------------ SDC
# Gowin's model gives CLKOUTD exactly CLKOUT's insertion delay (the divider's
# clock-to-out is not characterised). The crossings clk85m <-> clk42g get this
# much clock uncertainty, setup and hold, both ways, as a margin for it.
CLK42G_UNC = "0.5"
SDC_CLK42G = ("create_generated_clock -name clk42g   -source [get_ports {clk14m}] -master_clock clk14m"
              " -multiply_by 3 [get_nets {clk42g}]")
SDC_UNC = [f"set_clock_uncertainty {CLK42G_UNC} -from [get_clocks {{{a}}}] -to [get_clocks {{{b}}}]"
           for a, b in (("clk85m", "clk42g"), ("clk42g", "clk85m"))]


def patch_sdc(s: str) -> str:
    pristine(s, f"{NAME}.sdc")
    # clk42g is declared as clk85m / 2 from the same source
    m85 = re.findall(r"create_generated_clock\s+-name\s+clk85m\s+-source\s+\[get_ports\s+\{clk14m\}\]"
                     r"\s+-master_clock\s+clk14m\s+-multiply_by\s+(\d+)\s", s)
    if m85 != ["6"]:
        raise Refuse("sdc: clk85m is no longer generated from clk14m x6 (the rPLL2's CLKOUT)")
    m = re.search(r"\ncreate_generated_clock\s+-name\s+clk42m\b[^\n]*", s)
    if not m:
        raise Refuse("sdc: clk42m definition not found")
    add = ("\n# geo3d: engine clock = CLKOUTD of u_pll2 (CLKOUT / 2), same PLL and phase as clk85m,"
           "\n# so the geo3d_bus crossings clk85m <-> clk42g are timed as related clocks"
           "\n" + SDC_CLK42G)
    s = s[:m.end()] + add + s[m.end():]
    # the margin for CLKOUTD's unmodelled delay
    m = re.search(r"\n" + re.escape(SDC_CLK42G) + r"[^\n]*", s)
    add = ("\n# geo3d: Gowin gives CLKOUTD exactly CLKOUT's insertion delay; the divider's own delay"
           "\n# is not modelled: a margin for it on the clk85m <-> clk42g crossings"
           "\n" + "\n".join(SDC_UNC))
    return s[:m.end()] + add + s[m.end():]


# ------------------------------------------------------------------ .gprj
GPRJ_FILE = re.compile(r'<File path="([^"]*)" type="([^"]*)" enable="([^"]*)"/>')


def gprj_files(s: str):
    """The file paths of a .gprj (relative, inside the project)."""
    paths = [m.group(1) for m in GPRJ_FILE.finditer(s)]
    if not paths:
        raise Refuse(f"{GPRJ}: no <File path=...> entries")
    for p in paths:
        if p.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", p) or ".." in Path(p).parts:
            raise Refuse(f"{GPRJ}: '{p}' is not a path inside the project")
    return paths


def patch_gprj(s: str) -> str:
    pristine(s, GPRJ)
    m = re.search(r'(\s*)<File path="src/v9968/vdp.v"[^>]*/>', s)
    if not m:
        raise Refuse("gprj: vdp.v entry not found")
    ind = m.group(1)
    add = "".join(f'{ind}<File path="{GEO3D_SRC}/{f}" type="file.verilog" enable="1"/>'
                  for f in GEO3D_RTL)
    return s[:m.end()] + add + s[m.end():]


# ------------------------------------------------------------------ README
README = """\
# V9968 cartridge + geo3d (Gowin project)

Generated by `geo3d/integration/apply_geo3d_patch.py` from HRA!'s project
and `geo3d/rtl`. Do not edit the files in this folder by hand: the script
refuses to regenerate over a hand edit (`geo3d_manifest.txt` tells its own
output from hand edits), and with `--force` it backs the edit up next to
this folder and overwrites it.

- `../V9968_Cartridge_TangNano20K/` is HRA!'s project, exactly as upstream,
  with his own build in `impl/` (the recovery bitstream).
- This folder is the same project with geo3d wired in, laid out like his:
  - `tangnano20k_vdp_cartridge.gprj`: his file list plus `src/geo3d/*.v`.
  - `src/`: the files his project lists, and the IP generator files of his
    IPs (`.ipc`, `.mod`, `_tmp.v`, `.vo`); `v9968/vdp.v`,
    `tangnano20k_vdp_cartridge.v`, `gowin_rpll2/gowin_rpll2.v` and
    `tangnano20k_vdp_cartridge.sdc` carry the geo3d patch.
  - `src/geo3d/`: copies of `geo3d/rtl`. Edit the files in `geo3d/rtl`,
    then run the script again.
  - `impl/tangnano20k_vdp_cartridge_process_config.json`: his project
    settings, unchanged.
  - `impl/gwsynthesis/`, `impl/pnr/`: the Gowin build of this project. The
    bitstream is `impl/pnr/tangnano20k_vdp_cartridge.fs`.
  - `geo3d_manifest.txt`: the files the script generated, with the SHA-256
    of their text.
  - `tangnano20k_vdp_cartridge.gprj.user` and `impl/temp/` are written by
    Gowin, as in HRA!'s folder. The first time the Gowin IDE opens the
    project, it adds `impl/temp/rtl_parser.result` and
    `impl/temp/rtl_parser_arg.json` and rewrites the `.gprj.user`.

After a change in HRA!'s project or in `geo3d/rtl`, from the repository root:

    python3 geo3d/integration/apply_geo3d_patch.py           # regenerate this folder
    python3 geo3d/integration/apply_geo3d_patch.py --check   # verify that it is current
    sh geo3d/syn/gowin/build_gowin.sh                         # build it in place, check every timing path

The script needs Python 3 and runs in any shell that has it (Linux, macOS,
WSL, or Windows with Python installed). `build_gowin.sh` needs Gowin EDA, so
on Windows it runs from Git Bash; it runs `--check` first (with WSL's
python3 when Windows has no Python of its own) and stops if this folder is
not current.

Opening `tangnano20k_vdp_cartridge.gprj` in the Gowin IDE and running
"Run All" gives the same bitstream as `build_gowin.sh`.

Licenses: HRA!'s sources, MIT (the repository's `LICENSE`); the files
written by Gowin's IP generator (`src/dvi_tx/`, `src/gowin_*/`), Gowin's
terms, as in HRA!'s folder; geo3d, MIT, Copyright (c) 2026 Alex Moncks.
"""

MANIFEST_HEAD = """\
# Written by geo3d/integration/apply_geo3d_patch.py: the files it generated in
# this folder, with the SHA-256 of their text (LF line endings). The script
# uses it to tell its own output from hand edits. Do not edit.
"""


def text_hash(b: bytes) -> str:
    """SHA-256 of a file's text, line endings aside."""
    return hashlib.sha256(b.replace(b"\r\n", b"\n")).hexdigest()


def same_text(a: bytes, b: bytes) -> bool:
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


# ------------------------------------------------------------------ generator
def generate(hra: Path, rtl: Path):
    """{relative path: bytes} of every file the script owns in the geo3d project."""
    if not (hra / GPRJ).is_file():
        raise Refuse(f"{hra / GPRJ} not found")
    out = {}
    gprj, gprj_crlf = decode((hra / GPRJ).read_bytes())
    pristine(gprj, GPRJ)
    listed = gprj_files(gprj)
    for p in listed:
        if not (hra / p).is_file():
            raise Refuse(f"{GPRJ} lists {p}, which is not in {hra}")
        out[p] = (hra / p).read_bytes()
        base = Path(p)
        if base.suffix == ".v" and (hra / base.with_suffix(".ipc")).is_file():
            for ext in IP_FILES:
                q = (base.parent / (base.stem + ext)).as_posix()
                if (hra / q).is_file():
                    out[q] = (hra / q).read_bytes()
    for p, fn in ((VDP_V, patch_vdp), (TOP_V, patch_top), (PLL2_V, patch_pll2), (SDC, patch_sdc)):
        if p not in out:
            raise Refuse(f"{GPRJ} no longer lists {p}")
        s, crlf = decode(out[p])
        out[p] = encode(fn(s), crlf)
    out[GPRJ] = encode(patch_gprj(gprj), gprj_crlf)
    for f in GEO3D_RTL:
        if not (rtl / f).is_file():
            raise Refuse(f"{rtl / f} not found")
        out[f"{GEO3D_SRC}/{f}"] = (rtl / f).read_bytes()
    if not (hra / PROCESS_CONFIG).is_file():
        raise Refuse(f"{hra / PROCESS_CONFIG} not found")
    out[PROCESS_CONFIG] = (hra / PROCESS_CONFIG).read_bytes()
    out["README.md"] = encode(README, gprj_crlf)        # ASCII; line endings as HRA!'s .gprj
    manifest = MANIFEST_HEAD + "".join(f"{text_hash(b)}  {p}\n" for p, b in sorted(out.items()))
    out[MANIFEST] = encode(manifest, gprj_crlf)
    return out


def on_disk(dst: Path):
    """The files in the places the script owns in the geo3d project: {path: file}."""
    have = {p: dst / p for p in (GPRJ, PROCESS_CONFIG, "README.md", MANIFEST) if (dst / p).is_file()}
    if (dst / "src").is_dir():
        for f in (dst / "src").rglob("*"):
            if f.is_file():
                have[f.relative_to(dst).as_posix()] = f
    return have


def last_generated(dst: Path):
    """{path: text SHA-256} of the script's last output there, from its manifest."""
    f = dst / MANIFEST
    if not f.is_file():
        return None
    out = {}
    for line in f.read_bytes().decode("latin-1").splitlines():
        m = re.fullmatch(r"([0-9a-f]{64})  (\S+)", line.rstrip("\r"))
        if m:
            out[m.group(2)] = m.group(1)
    return out


def survey(files, dst: Path):
    """The files on disk, and (path, state) for each one that is not current:
      missing   not on disk
      stale     the script's earlier output (its inputs changed since)
      edited    differs from both the new and the earlier output: a hand edit
      obsolete  the script's earlier output, no longer part of the project
      extra     under src/, never generated by the script"""
    have = on_disk(dst)
    old = last_generated(dst) or {}
    diffs = []
    for p in sorted(set(files) | set(have)):
        if p not in have:
            diffs.append((p, "missing"))
            continue
        cur = text_hash(have[p].read_bytes())
        if p in files:
            if cur == text_hash(files[p]):
                continue
            diffs.append((p, "stale" if p == MANIFEST or old.get(p) == cur else "edited"))
        else:
            diffs.append((p, "obsolete" if old.get(p) == cur else "extra"))
    return have, diffs


HAND = ("edited", "extra")
REGEN = "python3 geo3d/integration/apply_geo3d_patch.py"


def check(files, dst: Path, rtl: Path, show) -> int:
    _, diffs = survey(files, dst)
    if not diffs:
        print(f"{show(dst)}: current ({len(files)} files; {GEO3D_SRC} = {show(rtl)})")
        return 0
    if len(diffs) > 1:                   # the manifest follows the other files: no news
        diffs = [(p, st) for p, st in diffs if p != MANIFEST]
    no_manifest = not (dst / MANIFEST).is_file()
    print(f"{show(dst)}: NOT CURRENT")
    for p, st in diffs:
        geo = p.startswith(GEO3D_SRC + "/")
        why = {
            "missing": "",
            "stale": (f"{show(rtl / Path(p).name)} changed since the last generation" if geo else
                      "HRA!'s project, geo3d/rtl or this script changed since the last generation"),
            "edited": (f"edited here: {show(rtl / Path(p).name)} is the file to edit, copy the change there"
                       if geo else "edited by hand: HRA!'s files change only through his upstream "
                       "and the patch in this script"),
            "obsolete": "no longer part of the project",
            "extra": "not generated by this script",
        }[st]
        if st == "edited" and no_manifest:
            why = f"differs; without {MANIFEST} a hand edit cannot be told from an older generation"
        print(f"  {st:<9} {p}" + (f"  ({why})" if why else ""))
    if any(st in HAND for _, st in diffs):
        print(f"hand edits: move each one where it belongs, then run {REGEN}; it refuses to "
              "overwrite them (--force backs them up first)")
    else:
        print(f"run {REGEN} to regenerate it")
    return 1


def write(files, dst: Path, show, force: bool):
    have, diffs = survey(files, dst)
    hand = [(p, st) for p, st in diffs if st in HAND]
    if hand and not force:
        print(f"ERROR {show(dst)}: the files below were edited by hand, or not generated by this "
              "script, and would be overwritten or removed:", file=sys.stderr)
        for p, st in hand:
            print(f"  {st:<7} {p}", file=sys.stderr)
        print(f"Move each edit where it belongs ({GEO3D_SRC}/*: geo3d/rtl; the other files: "
              "HRA!'s upstream or the patch in this script) and run the script again, or run "
              "it with --force to back them up and overwrite them.\nnothing was written",
              file=sys.stderr)
        sys.exit(1)
    if hand:
        bak = dst.parent / f"{dst.name}_backup_{time.strftime('%Y%m%d-%H%M%S')}"
        for p, _ in hand:
            (bak / p).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(have[p], bak / p)
        print(f"  backed up {len(hand)} hand-edited file(s) to {show(bak)}")
    written = removed = 0
    for p, b in sorted(files.items(), key=lambda x: x[0] == MANIFEST):   # the manifest last
        f = dst / p
        if p in have and same_text(f.read_bytes(), b):
            continue                         # line endings are git's business (text=auto)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b)
        written += 1
        print(f"  wrote   {p}")
    for p in sorted(set(have) - set(files)):
        have[p].unlink()
        removed += 1
        print(f"  removed {p}")
    if (dst / "src").is_dir():               # folders left empty under src/
        for d in sorted((dst / "src").rglob("*"), key=lambda x: len(x.parts), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
    print(f"{show(dst)}: {len(files)} files, {written} written, {removed} removed")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", help="repository root (default: this script's repository)")
    ap.add_argument("--check", action="store_true", help="verify only, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="back up hand-edited files next to the project folder, then overwrite them")
    ap.add_argument("--hra", help=f"HRA!'s project folder (default: <root>/{HRA_DIR})")
    ap.add_argument("--out", help=f"geo3d project folder (default: <root>/{OUT_DIR})")
    ap.add_argument("--rtl", help="geo3d RTL folder (default: <root>/geo3d/rtl)")
    a = ap.parse_args()
    if a.check and a.force:
        ap.error("--check writes nothing: --force does not apply")
    root = Path(a.root) if a.root else Path(__file__).resolve().parents[2]
    hra = Path(a.hra) if a.hra else root / HRA_DIR
    dst = Path(a.out) if a.out else root / OUT_DIR
    rtl = Path(a.rtl) if a.rtl else root / "geo3d" / "rtl"
    h, d = hra.resolve(), dst.resolve()

    def show(x: Path) -> str:          # paths inside the repository relative to its root
        try:
            return x.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return x.as_posix()

    if h == d or h in d.parents or d in h.parents:
        sys.exit(f"ERROR the geo3d project ({show(dst)}) must be outside HRA!'s folder ({show(hra)})")
    try:
        files = generate(hra, rtl)
    except Refuse as e:
        sys.exit(f"ERROR {e}\nnothing was written")
    if a.check:
        sys.exit(check(files, dst, rtl, show))
    write(files, dst, show, a.force)


if __name__ == "__main__":
    main()
