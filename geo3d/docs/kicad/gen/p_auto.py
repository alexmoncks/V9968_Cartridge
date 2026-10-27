# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Pages generated straight from the Verilog hierarchy (HRA!'s V9968).

Every module that instantiates others gets a page: its sub-modules are
drawn as blocks (leaf modules) or hierarchical sheets (modules with their
own instances), each pin labelled with the expression it is connected to
in the RTL. The module's own logic (assign/always) is listed as text.
"""
import re

import ksch as K
from common import (MODS, LEGEND, header, res_str, netname, label_ext, port_col, port_col_width)

SRCDIR = 'fpga/V9968_Cartridge_TangNano20K_geo3d/src/v9968/'

ABOUT = {
    'vdp': 'V9968: clone do V9958 de HRA!, com extensões (comandos LRMM e outros, VRAM de 256 KB na SDRAM, saída HDMI '
           'com upscan e filtro bilinear).\nPortas ext_cmd_*: o geo3d escreve nos registradores de comando por aqui '
           '(patch de integração do geo3d).',
    'vdp_timing_control': 'Contadores de varredura (SSG), busca de padrões por modo de tela e sprites.',
    'vdp_timing_control_sprite': 'Sprites: seleção dos planos visíveis, coleta de atributos, composição dos pixels.',
    'vdp_color_palette': 'Paleta de 256 cores em BSRAM, modos YJK/YAE, conversão para RGB de 8 bits.',
    'vdp_upscan': 'Upscan: dois buffers de linha (par/ímpar) dobram as linhas para a saída HDMI.',
    'vdp_video_out': 'Saída de vídeo: buffer duplo, escala horizontal, filtro bilinear por canal, scanlines, sincronismo.',
    'vdp_video_double_buffer': 'Buffer duplo de linha de vídeo (par e ímpar).',
}


def _assigns(body):
    out = []
    for a in re.finditer(r'\bassign\s+(.*?);', body, re.S):
        out.append(re.sub(r'\s+', ' ', a.group(1)).strip())
    return out


def _simple_assigns(body):
    """port = identifier-or-slice assigns, for aliasing a port to a net."""
    m = {}
    for a in _assigns(body):
        mm = re.match(r'^(\w+)\s*=\s*([\w\[\]: ]+)$', a)
        if mm:
            m[mm.group(1)] = mm.group(2).replace(' ', '')
    return m


def auto_page(mname, path, inst_name):
    m = MODS[mname]
    portnames = {p.name: p for p in m.ports}
    S = K.Sheet(f'{mname}.kicad_sch', f'{mname}: estrutura (gerada do RTL)',
                comments=[f'Fonte: {SRCDIR}{mname}.v (HRA!)',
                          'Pagina gerada automaticamente da hierarquia Verilog'])

    def lab(expr):
        if expr is None or expr == '':
            return None
        e = expr.strip()
        if e in portnames:
            return portnames[e].label
        return netname(mname, e)

    # ---------------------------------------------------------------- elements
    used = set()
    elems = []
    for inst in m.instances:
        t = MODS[inst.module]
        conn = {p.label: lab(inst.conns.get(p.name)) for p in t.ports}
        for p in t.ports:
            e = (inst.conns.get(p.name) or '').strip()
            if e in portnames:
                used.add(e)
        ins = [p for p in t.ports if p.dir == 'input']
        outs = [p for p in t.ports if p.dir != 'input']
        lext = label_ext([conn[p.label] for p in ins if conn[p.label]])
        rext = label_ext([conn[p.label] for p in outs if conn[p.label]])
        ipath = path + '/' + inst.name
        if t.instances:
            child = auto_page(t.name, ipath, inst.name)
            lw = max([K.text_w(p.label) for p in ins] + [0])
            rw = max([K.text_w(p.label) for p in outs] + [0])
            w = K.snap_up(max(lw + rw + 7.62, K.text_w(inst.name) + 5.08, 20.32), 2.54)
            h = K.snap_up((max(len(ins), len(outs), 1) + 1) * 2.54, 2.54)
            el = dict(kind='sheet', child=child, w=w, h=h)
        else:
            sym = K.box(t.name, [p.label for p in ins],
                        [(p.label, 'bidirectional' if p.dir == 'inout' else 'output') for p in outs])
            el = dict(kind='box', sym=sym, w=sym.w, h=sym.h)
        el.update(inst=inst, t=t, conn=conn, ins=ins, outs=outs, lext=lext, rext=rext, path=ipath,
                  tw=lext + el['w'] + rext, th=el['h'] + 17.78)
        elems.append(el)

    # ---------------------------------------------------------------- ports column
    alias = _simple_assigns(m.body)
    items = []
    for p in m.ports:
        shape = {'input': 'input', 'output': 'output', 'inout': 'bidirectional'}[p.dir]
        if p.name in used:
            local = p.label
        elif p.name in alias:
            local = netname(mname, alias[p.name])
        else:
            local = p.label
        items.append((p.label, local, shape))
    pw = port_col_width(items) + 10.16

    # ---------------------------------------------------------------- layout
    y0 = 55.88
    best = None
    n = len(elems)
    for ncols in range(1, n + 1):
        target = max(max(e['th'] for e in elems), sum(e['th'] for e in elems) / ncols)
        cols, cur, hcur = [], [], 0
        for e in elems:
            if cur and hcur + e['th'] > target * 1.08 and len(cols) < ncols - 1:
                cols.append(cur)
                cur, hcur = [], 0
            cur.append(e)
            hcur += e['th']
        cols.append(cur)
        W = 20.32 + pw + sum(max(e['lext'] for e in c) + max(e['w'] + e['rext'] for e in c) + 10.16 for c in cols)
        H = y0 + max(max(sum(e['th'] for e in c) for c in cols), len(items) * 2.54 + 10) + 60
        for pn, pwid, phei in K.PAPERS:
            if pwid >= W + 15 and phei >= H + 45:
                score = pwid * phei
                break
        else:
            score = 1e9
        if best is None or score < best[0]:
            best = (score, cols, W, H)
    _, cols, W, H = best

    header(S, f'{mname} ({inst_name}): estrutura gerada do RTL',
           ABOUT.get(mname, '') + ('\n' if ABOUT.get(mname) else '') +
           'Blocos brancos = módulos folha (só as portas); blocos amarelos = módulos com página própria.  '
           'Recursos (síntese): ' + res_str(path))
    ybot = port_col(S, 20.32, y0, items, title='Portas do módulo', pitch=2.54)

    x = 20.32 + pw
    bottom = ybot
    for c in cols:
        le = max(e['lext'] for e in c)
        y = y0
        for e in c:
            bl = x + le
            if e['kind'] == 'box':
                s = e['sym']
                cx, cy = K.snap(bl + s.w / 2), K.snap(y + 7.62 + s.h / 2)
                o = S.add(s, cx, cy, value=f"{e['inst'].name} : {e['t'].name}", value_size=1.5,
                          value_at=(cx - s.w / 2, cy - s.h / 2 - 1.9), value_justify='left bottom')
                top_y = cy - s.h / 2
                left_x = cx - s.w / 2
            else:
                o = S.subsheet(e['child'], K.snap(bl), K.snap(y + 7.62),
                               [(p.label, 'input') for p in e['ins']],
                               [(p.label, 'bidirectional' if p.dir == 'inout' else 'output') for p in e['outs']],
                               w=e['w'], name=e['inst'].name)
                top_y, left_x = o.y, o.x
            for p in e['ins'] + e['outs']:
                nm = e['conn'][p.label]
                if nm:
                    S.net(o, p.label, nm)
                else:
                    S.nc(o[p.label])
            S.text(res_str(e['path']), left_x, top_y + e['h'] + 5.08, size=1.27)
            y += e['th']
        bottom = max(bottom, y)
        x += le + max(e['w'] + e['rext'] for e in c) + 10.16

    # ---------------------------------------------------------------- local logic
    asg = _assigns(m.body)
    nalw = len(re.findall(r'\balways\b', m.body))
    lines = [f'Lógica local de {mname}: {len(asg)} assign, {nalw} always (registradores)']
    for a in asg[:28]:
        lines.append('  assign ' + (a if len(a) <= 120 else a[:117] + '...'))
    if len(asg) > 28:
        lines.append(f'  ... e mais {len(asg) - 28}')
    hbox = K.snap_up(3.81 + 1.7 * len(lines), 1.27)
    S.textbox('\n'.join(lines), 20.32, bottom + 5.08, max(200.0, min(x - 30.48, 330.0)), hbox, size=1.27)
    S.textbox(LEGEND, 20.32, bottom + 10.16 + hbox, 165.1, 26.67)
    return S
