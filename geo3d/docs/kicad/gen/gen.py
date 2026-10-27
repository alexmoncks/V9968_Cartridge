# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Generate the KiCad logic drawings of the V9968 cartridge + geo3d v2.0.

Run with KiCad's own Python (no other dependencies):
    "%LOCALAPPDATA%\\Programs\\KiCad\\10.0\\bin\\python.exe" gen.py
Output: ../geo3d_logic.kicad_pro and one .kicad_sch per page, next to this
folder. Export the PDF with export_pdf.ps1.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ksch as K  # noqa: E402
import p_auto  # noqa: E402
import p_gate  # noqa: E402
import p_rtl  # noqa: E402
import p_top  # noqa: E402

OUT = os.path.abspath(os.path.join(HERE, '..'))
DATE = '2026-10-26'
REV = 'geo3d v2.0'
COMPANY = 'geo3d (c) Alex Moncks, MIT  -  V9968 (c) HRA! Takayuki Hara'


def build():
    core = p_rtl.page_core()
    eng = p_rtl.page_engine(core)
    geo = p_gate.page_geo3d_bus(eng)
    msx = p_gate.page_msx_slot()
    clk = p_top.page_clock_reset()
    vdp = p_auto.auto_page('vdp', 'u_v9958', 'u_v9958')
    return p_top.page_top(clk, msx, geo, vdp)


if __name__ == '__main__':
    for f in os.listdir(OUT):
        if f.endswith('.kicad_sch'):
            os.remove(os.path.join(OUT, f))
    root = build()
    order = K.write_project(root, OUT, DATE, REV, COMPANY)
    for sh in order:
        print(f'{sh.page:3} {sh.fname:40} {sh.paper_size()[0]}')
