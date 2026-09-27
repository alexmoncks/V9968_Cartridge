#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Wires geo3d into the V9968 cartridge project (fpga/V9968_Cartridge_TangNano20K).

Changes (idempotent, fails loudly if the upstream text moved):
  1. src/v9968/vdp.v: adds an external command-register write port
     (ext_cmd_wr/num/data) OR-ed into vdp_command, and exposes CE (ext_cmd_ce).
     Nothing else in the VDP changes; with ext_cmd_wr = 0 it is bit-identical.
  2. src/tangnano20k_vdp_cartridge.v: instantiates geo3d_bus on offsets 5 and 7
     of the slot's port block (bus side on clk85m, engine on clk42g), keeps
     those offsets away from the VDP, and muxes read data / ready.
  3. tangnano20k_vdp_cartridge.gprj: adds the geo3d RTL files.
  4. src/gowin_rpll2/gowin_rpll2.v: exposes the rPLL's CLKOUTD output, which
     the IP already configures as CLKOUT / 2 (DYN_SDIV_SEL = 2) but leaves
     unused. The top routes it to a new net, clk42g (42.95 MHz), used only by
     the geo3d engine. Same PLL as clk85m, so the geo3d_bus crossings see two
     related, phase-aligned clocks. HRA!'s clk42m (Gowin_CLKDIV) is left as it
     is and still clocks the HDMI side and the rest of his design: in Gowin's
     timing model the CLKDIV output has no insertion delay while the PLL
     outputs have 3-4 ns, which broke the clk85m <-> clk42m crossings.
     (The .ipc / .mod files of the IP are not touched: regenerating the IP
     in the Gowin GUI drops the clkoutd port and synthesis then fails loudly.)
  5. src/tangnano20k_vdp_cartridge.sdc: declares clk42g as a clock generated
     from clk14m (x3), like clk85m (x6), plus 0.5 ns of clock uncertainty on
     the clk85m <-> clk42g crossings (Gowin gives CLKOUTD exactly CLKOUT's
     insertion delay; the divider's own delay is not modelled). HRA!'s clocks
     and groups unchanged.
The rPLL2 settings, the SDC clocks and the u_pll2 hookup that clk42g relies
on are checked on every run, also on an already patched tree (after merging
HRA!'s upstream).
Timing closes with the project's own settings (impl/*_process_config.json,
Place Option 0), so the Gowin IDE and geo3d/syn/gowin/build_gowin.sh give the
same bitstream.

Usage: python3 apply_geo3d_patch.py <repo_root>
"""
import re
import sys
from pathlib import Path

MARK = "geo3d"


def read(p: Path):
    """Byte-preserving read (upstream files mix Shift-JIS comments and CRLF)."""
    raw = p.read_bytes().decode("latin-1")
    crlf = "\r\n" in raw
    return raw.replace("\r\n", "\n"), crlf


PENDING = []    # (path, bytes): written only after every step and check passed


def write(p: Path, s: str, crlf: bool):
    if crlf:
        s = s.replace("\n", "\r\n")
    PENDING.append((p, s.encode("latin-1")))


def patch_vdp(p: Path):
    s, crlf = read(p)
    if "ext_cmd_wr" in s:
        print(f"  {p.name}: already patched")
        return
    anchor = (re.search(r"\n([ \t]*)//[ \t]*debug pulse", s) or
              re.search(r"\n([ \t]*)input\s+\[\s*1:0\]\s+button,", s))
    if not anchor:
        sys.exit("vdp.v: port anchor 'button' not found")
    ind = anchor.group(1)
    ports = (f"\n{ind}input\t\t\t\text_cmd_wr,\t\t\t//\tgeo3d: external command register write"
             f"\n{ind}input\t\t[5:0]\text_cmd_num,"
             f"\n{ind}input\t\t[7:0]\text_cmd_data,"
             f"\n{ind}output\t\t\t\text_cmd_ce,\t\t\t//\tgeo3d: S#2 CE\n")
    s = s[:anchor.start()] + ports + s[anchor.start():]

    i = s.find("vdp_command u_command")
    if i < 0:
        sys.exit("vdp.v: vdp_command instance not found")
    j = s.find(");", i)
    blk = s[i:j]
    new = blk
    new, n1 = re.subn(r"\(\s*w_register_write\s*\)", "( w_register_write | ext_cmd_wr )", new)
    new, n2 = re.subn(r"\(\s*w_register_num\s*\)", "( ext_cmd_wr ? ext_cmd_num  : w_register_num  )", new)
    new, n3 = re.subn(r"\(\s*w_register_data\s*\)", "( ext_cmd_wr ? ext_cmd_data : w_register_data )", new)
    if (n1, n2, n3) != (1, 1, 1):
        sys.exit(f"vdp.v: command register hookup not found {n1, n2, n3}")
    s = s[:i] + new + s[j:]

    k = s.rfind("endmodule")
    s = s[:k] + "\t//\tgeo3d: expose command-execute status\n\tassign ext_cmd_ce = w_status_command_execute;\n\n" + s[k:]
    write(p, s, crlf)
    print(f"  {p.name}: patched")


def instance(s: str, head: str, what: str):
    """Returns (start, end) of the port list of instance `head` ("module name")."""
    i = s.find(head)
    if i < 0 or s.find(head, i + 1) >= 0:
        sys.exit(f"{what}: instance '{head}' not found exactly once")
    j = s.find(");", i)
    if j < 0:
        sys.exit(f"{what}: end of instance '{head}' not found")
    return i, j


def patch_top(p: Path):
    s, crlf = read(p)
    done = []
    if "geo3d_bus" not in s:
        s = add_geo3d_bus(s)
        done.append("geo3d_bus")

    # engine clock: clk42g (upgrades earlier patches that used clk85m or clk42m)
    i, j = instance(s, "geo3d_bus u_geo3d", "top")
    blk = s[i:j]
    if not re.search(r"\.clk_eng\s*\(", blk):
        blk, n = re.subn(r"(\n([ \t]*)\.clk\s*\(\s*clk85m\s*\),)",
                         r"\1\n\2.clk_eng\t\t\t( clk42g\t\t\t\t\t),", blk, count=1)
        if n != 1:
            sys.exit("top: geo3d_bus clk hookup not found")
        done.append("clk_eng = clk42g")
    elif not re.search(r"\.clk_eng\s*\(\s*clk42g\s*\)", blk):
        blk, n = re.subn(r"(\.clk_eng\s*\(\s*)clk42m(\s*\))", r"\1clk42g\2", blk)
        if n != 1:
            sys.exit("top: geo3d_bus clk_eng is neither clk42m nor clk42g")
        done.append("clk_eng clk42m -> clk42g")
    s = s[:i] + blk + s[j:]

    # the clk42g net: declared next to clk42m
    if not re.search(r"\n[ \t]*wire\s+clk42g\s*;", s):
        m = re.search(r"\n([ \t]*)wire\s+clk42m\s*;[^\n]*", s)
        if not m:
            sys.exit("top: 'wire clk42m;' not found")
        s = (s[:m.end()] + f"\n{m.group(1)}wire\t\t\tclk42g;\t\t\t\t//\t42.95454MHz"
             " (geo3d engine: CLKOUTD of u_pll2 = clk85m / 2)" + s[m.end():])
        done.append("wire clk42g")

    # ... driven by the CLKOUTD output of the PLL that makes clk85m from clk14m
    # (checked on every run: the SDC's clk42g relies on it)
    i, j = instance(s, "Gowin_rPLL2 u_pll2", "top")
    blk = s[i:j]
    if not re.search(r"\.clkout\s*\(\s*clk85m\s*\)", blk):
        sys.exit("top: u_pll2 no longer drives clk85m")
    if not re.search(r"\.clkin\s*\(\s*clk14m\s*\)", blk):
        sys.exit("top: u_pll2 no longer runs from clk14m")
    if not re.search(r"\.clkoutd\s*\(", blk):
        blk, n = re.subn(r"(\n([ \t]*)\.clkin\s*\(\s*clk14m\s*\))",
                         r"\n\2.clkoutd\t\t( clk42g\t\t\t),\t\t//\toutput clkoutd\t42.95454MHz"
                         r" (CLKOUT / 2, geo3d engine only)\1", blk, count=1)
        if n != 1:
            sys.exit("top: u_pll2 .clkin anchor not found")
        done.append("u_pll2 .clkoutd")
    elif not re.search(r"\.clkoutd\s*\(\s*clk42g\s*\)", blk):
        sys.exit("top: u_pll2 .clkoutd is connected to something else")
    s = s[:i] + blk + s[j:]

    if done:
        write(p, s, crlf)
        print(f"  {p.name}: patched ({', '.join(done)})")
    else:
        print(f"  {p.name}: already patched")


def add_geo3d_bus(s: str) -> str:
    old_mux = re.search(r"\tassign w_bus_rdata\s*=.*?\n\tassign w_bus_rdata_en\s*=.*?\n\tassign w_bus_ready\s*=.*?\n", s, re.S)
    if not old_mux:
        sys.exit("top: bus mux not found")
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

    i = s.find("vdp u_v9958")
    if i < 0:
        sys.exit("top: vdp instance not found")
    j = s.find(");", i)
    blk = s[i:j]
    blk, n = re.subn(r"(\.bus_valid\s*)\(\s*w_bus_valid\s*\)", r"\1( w_bus_valid & ~w_geo_hit\t\t)", blk)
    if n != 1:
        sys.exit("top: vdp bus_valid not found")
    blk, n = re.subn(r"(\n([ \t]*)\.force_highspeed)",
                     r"\n\2.ext_cmd_wr\t\t( w_geo_cmd_wr\t\t\t\t),"
                     r"\n\2.ext_cmd_num\t\t( w_geo_cmd_num\t\t\t\t),"
                     r"\n\2.ext_cmd_data\t\t( w_geo_cmd_data\t\t\t),"
                     r"\n\2.ext_cmd_ce\t\t( w_geo_cmd_ce\t\t\t\t),\1", blk)
    if n != 1:
        sys.exit("top: vdp force_highspeed anchor not found")
    return s[:i] + blk + s[j:]


# rPLL2 settings that make CLKOUT = clk14m x 6 (85.9 MHz) and CLKOUTD = CLKOUT / 2
# (42.95 MHz) with both edges aligned: checked on every run, so that a merge of
# HRA!'s upstream that changes the IP cannot leave clk42g mis-described
PLL2_PARAMS = (("FCLKIN", '"14.318"'), ("DYN_IDIV_SEL", '"false"'), ("IDIV_SEL", "0"),
               ("DYN_FBDIV_SEL", '"false"'), ("FBDIV_SEL", "5"), ("CLKFB_SEL", '"internal"'),
               ("CLKOUT_BYPASS", '"false"'), ("CLKOUT_DLY_STEP", "0"),
               ("CLKOUTD_BYPASS", '"false"'), ("CLKOUTD_SRC", '"CLKOUT"'), ("DYN_SDIV_SEL", "2"))


def patch_pll2(p: Path):
    s, crlf = read(p)
    for key, val in PLL2_PARAMS:
        got = re.findall(rf"defparam\s+rpll_inst\.{key}\s*=\s*([^;]*?)\s*;", s)
        if got != [val]:
            sys.exit(f"gowin_rpll2.v: {key} is {' / '.join(got) or 'missing'}, not {val}: "
                     "CLKOUTD would not be clk85m / 2 in phase (clk42g in the SDC)")
    if re.search(r"^output\s+clkoutd\s*;", s, re.M):
        if not re.search(r"\.CLKOUTD\(\s*clkoutd\s*\)", s):
            sys.exit("gowin_rpll2.v: output clkoutd is not the rPLL's CLKOUTD")
        print(f"  {p.name}: already patched")
        return
    s, n1 = re.subn(r"module\s+Gowin_rPLL2\s*\(\s*clkout\s*,\s*lock\s*,\s*clkoutp\s*,\s*clkin\s*\)\s*;",
                    "module Gowin_rPLL2 (clkout, lock, clkoutp, clkoutd, clkin);", s)
    s, n2 = re.subn(r"(\noutput\s+clkoutp\s*;)",
                    r"\1\noutput clkoutd;\t// geo3d: CLKOUT / 2 = 42.95454MHz (DYN_SDIV_SEL = 2)", s)
    s, n3 = re.subn(r"\nwire\s+clkoutd_o\s*;", "", s)
    s, n4 = re.subn(r"\.CLKOUTD\(\s*clkoutd_o\s*\)", ".CLKOUTD(clkoutd)", s)
    if (n1, n2, n3, n4) != (1, 1, 1, 1):
        sys.exit(f"gowin_rpll2.v: CLKOUTD hookup not found {n1, n2, n3, n4}")
    write(p, s, crlf)
    print(f"  {p.name}: patched (clkoutd)")


# Gowin's model gives CLKOUTD exactly CLKOUT's insertion delay (the divider's
# clock-to-out is not characterised). The crossings clk85m <-> clk42g get this
# much clock uncertainty, setup and hold, both ways, as a margin for it.
CLK42G_UNC = "0.5"
SDC_CLK42G = ("create_generated_clock -name clk42g   -source [get_ports {clk14m}] -master_clock clk14m"
              " -multiply_by 3 [get_nets {clk42g}]")
SDC_UNC = [f"set_clock_uncertainty {CLK42G_UNC} -from [get_clocks {{{a}}}] -to [get_clocks {{{b}}}]"
           for a, b in (("clk85m", "clk42g"), ("clk42g", "clk85m"))]


def patch_sdc(p: Path):
    s, crlf = read(p)
    done = []
    # checked on every run: clk42g is declared as clk85m / 2 from the same source
    m85 = re.findall(r"create_generated_clock\s+-name\s+clk85m\s+-source\s+\[get_ports\s+\{clk14m\}\]"
                     r"\s+-master_clock\s+clk14m\s+-multiply_by\s+(\d+)\s", s)
    if m85 != ["6"]:
        sys.exit("sdc: clk85m is no longer generated from clk14m x6 (the rPLL2's CLKOUT)")
    decl = re.findall(r"^[ \t]*create_generated_clock\s+-name\s+clk42g\b[^\n]*", s, re.M)
    if not decl:
        m = re.search(r"\ncreate_generated_clock\s+-name\s+clk42m\b[^\n]*", s)
        if not m:
            sys.exit("sdc: clk42m definition not found")
        add = ("\n# geo3d: engine clock = CLKOUTD of u_pll2 (CLKOUT / 2), same PLL and phase as clk85m,"
               "\n# so the geo3d_bus crossings clk85m <-> clk42g are timed as related clocks"
               "\n" + SDC_CLK42G)
        s = s[:m.end()] + add + s[m.end():]
        done.append("clk42g")
    elif [d.strip() for d in decl] != [SDC_CLK42G]:
        sys.exit(f"sdc: clk42g is declared as '{decl[0].strip()}', not clk14m x3 (clk85m / 2)")
    # the margin for CLKOUTD's unmodelled delay (added to earlier patches too)
    have = [u for u in re.findall(r"^[ \t]*set_clock_uncertainty\b[^\n]*", s, re.M) if "clk42g" in u]
    if not have:
        m = re.search(r"\n" + re.escape(SDC_CLK42G) + r"[^\n]*", s)
        add = ("\n# geo3d: Gowin gives CLKOUTD exactly CLKOUT's insertion delay; the divider's own delay"
               "\n# is not modelled: a margin for it on the clk85m <-> clk42g crossings"
               "\n" + "\n".join(SDC_UNC))
        s = s[:m.end()] + add + s[m.end():]
        done.append(f"clk42g uncertainty {CLK42G_UNC} ns")
    elif [u.strip() for u in have] != SDC_UNC:
        sys.exit(f"sdc: the clk42g clock uncertainty is not {SDC_UNC}")
    if done:
        write(p, s, crlf)
        print(f"  {p.name}: patched ({', '.join(done)})")
    else:
        print(f"  {p.name}: already patched")


def patch_gprj(p: Path):
    s, crlf = read(p)
    if "geo3d_engine.v" in s:
        print(f"  {p.name}: already patched")
        return
    m = re.search(r'(\s*)<File path="src/v9968/vdp.v"[^>]*/>', s)
    if not m:
        sys.exit("gprj: vdp.v entry not found")
    ind = m.group(1)
    add = "".join(f'{ind}<File path="../../geo3d/rtl/{f}" type="file.verilog" enable="1"/>'
                  for f in ("geo3d_core.v", "geo3d_engine.v", "geo3d_bus.v"))
    s = s[:m.end()] + add + s[m.end():]
    write(p, s, crlf)
    print(f"  {p.name}: patched")


def main():
    root = Path(sys.argv[1])
    proj = root / "fpga" / "V9968_Cartridge_TangNano20K"
    try:
        patch_vdp(proj / "src" / "v9968" / "vdp.v")
        patch_top(proj / "src" / "tangnano20k_vdp_cartridge.v")
        patch_gprj(proj / "tangnano20k_vdp_cartridge.gprj")
        patch_pll2(proj / "src" / "gowin_rpll2" / "gowin_rpll2.v")
        patch_sdc(proj / "src" / "tangnano20k_vdp_cartridge.sdc")
    except SystemExit as e:
        if isinstance(e.code, str):
            sys.exit(f"ERROR {e.code}\nnothing was written: the tree is as it was")
        raise
    # every check passed: only now the files change (a failed check above
    # leaves the whole tree as it was)
    for p, b in PENDING:
        p.write_bytes(b)


if __name__ == "__main__":
    main()
