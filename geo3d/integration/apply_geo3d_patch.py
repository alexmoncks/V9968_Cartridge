#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Wires geo3d into the V9968 cartridge project (fpga/V9968_Cartridge_TangNano20K).

Changes (idempotent, fails loudly if the upstream text moved):
  1. src/v9968/vdp.v: adds an external command-register write port
     (ext_cmd_wr/num/data) OR-ed into vdp_command, and exposes CE (ext_cmd_ce).
     Nothing else in the VDP changes; with ext_cmd_wr = 0 it is bit-identical.
  2. src/tangnano20k_vdp_cartridge.v: instantiates geo3d_bus on offsets 5 and 7
     of the slot's port block, keeps those offsets away from the VDP, and muxes
     read data / ready.
  3. tangnano20k_vdp_cartridge.gprj: adds the geo3d RTL files.

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


def write(p: Path, s: str, crlf: bool):
    if crlf:
        s = s.replace("\n", "\r\n")
    p.write_bytes(s.encode("latin-1"))


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


def patch_top(p: Path):
    s, crlf = read(p)
    if "geo3d_bus" in s:
        print(f"  {p.name}: already patched")
        return
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
    s = s[:i] + blk + s[j:]
    write(p, s, crlf)
    print(f"  {p.name}: patched")


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
    patch_vdp(proj / "src" / "v9968" / "vdp.v")
    patch_top(proj / "src" / "tangnano20k_vdp_cartridge.v")
    patch_gprj(proj / "tangnano20k_vdp_cartridge.gprj")


if __name__ == "__main__":
    main()
