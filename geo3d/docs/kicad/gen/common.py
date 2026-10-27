# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Shared data and helpers for the logic-drawing pages."""
import os
import re

import ksch as K
import vparse as V

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')

MODS = V.load(V.sources())

LEGEND = ('Convenções\n'
          '• Portas lógicas com bolinha = entrada ou saída invertida.\n'
          '• Flip-flops sensíveis à borda de subida; R/S = reset/set SÍNCRONO\n'
          '  (feito dentro do always @(posedge clk)), ativo em 0 quando há bolinha.\n'
          '• EN = habilitação do clock (o registrador mantém o valor quando EN inativo).\n'
          '• Um registrador [n:0] é desenhado como um flip-flop: são n+1 em paralelo.\n'
          '• Rótulos iguais na mesma folha = mesmo sinal. Rótulos com * são nomes\n'
          '  criados para o desenho (no RTL são expressões sem nome).\n'
          '• Bandeiras (hierarchical labels) = portas do módulo, ligadas à folha-mãe.')


# ------------------------------------------------------------- resources
def load_resources():
    """Per-instance subtree totals from Gowin's hierarchy resource report
    (geo3d v2.0 build v2_locr2)."""
    t = open(os.path.join(DATA, 'v2_locr2_syn_resource.html'), encoding='utf-8', errors='replace').read()
    rows = []
    for r in t.split('</tr>'):
        tds = re.findall(r'<td[^>]*>(.*?)</td>', r, re.S)
        if len(tds) < 8:
            continue
        d = tds[0].count('&nbsp') // 4
        n = re.sub(r'\s*\(.*', '', re.sub(r'<[^>]*>|&nbsp;?|\|--', '', tds[0]).strip(), flags=re.S).strip()
        v = [0 if re.sub(r'<[^>]*>', '', x).strip() in ('-', '') else int(re.sub(r'<[^>]*>', '', x).strip())
             for x in tds[1:8]]
        rows.append((d, n, v))
    res, stack = {}, []
    for d, n, v in rows:
        stack = stack[:d]
        stack.append(n)
        for i in range(1, len(stack) + 1):
            key = '/'.join(stack[1:i]) if i > 1 else ''
            acc = res.setdefault(key, [0] * 7)
            for j in range(7):
                acc[j] += v[j]
    return res


RES = load_resources()
ALIAS = {'u_v9958': 'u_v9968'}


def res_str(path):
    parts = [ALIAS.get(p, p) for p in path.split('/') if p]
    v = RES.get('/'.join(parts))
    if not v:
        return ''
    reg, alu, lut, dsp, bsram, ssram, _ = v
    s = f'REG {reg} · LUT {lut} · ALU {alu}'
    if dsp:
        s += f' · DSP {dsp}'
    if bsram:
        s += f' · BSRAM {bsram}'
    if ssram:
        s += f' · SSRAM {ssram}'
    return s


def pnr_summary():
    t = open(os.path.join(DATA, 'v2_locr2_pnr.rpt.txt'), encoding='utf-8', errors='replace').read()
    out = []
    for key in ('Logic', 'Register', 'CLS', 'I/O Port', 'BSRAM', 'DSP'):
        m = re.search(r'^\s*' + re.escape(key) + r'\s*\|\s*([0-9/]+)\s*\|\s*([0-9]+%)', t, re.M)
        if m:
            out.append((key, m.group(1), m.group(2)))
    return out


# ------------------------------------------------------------- symbols
def AND(n=2, inv=()):
    return K.gate('AND', n, inv)


def OR(n=2, inv=(), nor=False):
    return K.gate('OR', n, inv, inv_out=nor)


def XOR(xnor=False):
    return K.gate('XOR', 2, inv_out=xnor)


NOT = K.inverter()
TRI = K.tristate()
MUX = K.mux2()


# ------------------------------------------------------------- helpers
def header(S, title, lines, x=20.32, y=20.32):
    S.text(title, x, y, size=3.0, thick=0.5)
    if lines:
        S.text(lines, x, y + 8.89, size=1.5)


def right_of(S, pt, sym, key, gap, label=None, **kw):
    """Place sym so that its pin `key` sits `gap` mm to the right of pt, on
    the same row, and wire them (with an optional label on the wire)."""
    p = sym.pin[key]
    if label:
        gap = max(gap, K.snap_up(K.text_w(label) + 5.08, 2.54))
    obj = S.add(sym, pt[0] + gap - p.x, pt[1] + p.y, **kw)
    S.wire(pt, obj[key])
    if label:
        S.label(label, (pt[0] + 2.54, pt[1]), 'R')
    return obj


def wlabel(S, a, b, name, mx=None):
    """Wire a -> b with a label just after a."""
    S.route(a, b, mx=mx)
    S.label(name, (a[0] + 2.54, a[1]), 'R')


def alias(S, obj, key, local, hier, shape='output'):
    """An output pin that is both an internal signal and a module port
    (Verilog: assign port = reg)."""
    a = obj[key]
    d = obj.d[key]
    sgn = 1 if d == 'R' else -1
    end = (a[0] + sgn * K.snap_up(K.text_w(local) + 7.62, 2.54), a[1])
    S.wire(a, end)
    S.label(local, (a[0] + sgn * 2.54, a[1]), d)
    S.label(hier, end, d, kind='hier', shape=shape)
    return end


def nets(S, obj, mapping, length=5.08):
    """mapping: pin key -> net name, or (name, 'hier', shape)."""
    for k, v in mapping.items():
        if v is None:
            continue
        if isinstance(v, tuple):
            name, kind, shape = v
            S.net(obj, k, name, length=length, kind=kind, shape=shape)
        else:
            S.net(obj, k, v, length=length)


def H(name, shape='input'):
    return (name, 'hier', shape)


def label_ext(names, hier=False):
    return 2.54 + 5.08 + max([K.text_w(n) + (4.5 if hier else 1.5) for n in names] + [0])


class Blk:
    """A block to place in a column: a box symbol whose pins are labelled."""

    def __init__(self, name, ins, outs, value, title=None, nets=None, note=None, title_size=2.0):
        self.sym = K.box(name, ins, outs, title=title, title_size=title_size)
        self.value, self.nets, self.note = value, dict(nets or {}), note
        self.ins = [p if isinstance(p, str) else p[0] for p in ins]
        self.outs = [p if isinstance(p, str) else p[0] for p in outs]

    def lab(self, pin):
        return self.nets.get(pin, pin)


def column(S, x, y, blocks, gap=12.7):
    """Stack blocks top to bottom, left edges aligned; returns (right edge, bottom)."""
    le = max(label_ext([b.lab(p) for p in b.ins if b.lab(p)]) for b in blocks)
    right = 0
    placed = []
    for b in blocks:
        s = b.sym
        cx = K.snap(x + le + s.w / 2)
        cy = K.snap(y + 5.08 + s.h / 2)
        o = S.add(s, cx, cy, value=b.value, value_size=1.5,
                  value_at=(cx - s.w / 2, cy - s.h / 2 - 1.9), value_justify='left bottom')
        for p in b.ins + b.outs:
            n = b.lab(p)
            if n:
                S.net(o, p, n)
        y = cy + s.h / 2 + 2.54
        if b.note:
            S.text(b.note, cx - s.w / 2, y + 2.54, size=1.27)
            y += 2.54 + 2.2 * (b.note.count('\n') + 1)
        y += gap
        right = max(right, cx + s.w / 2 + label_ext([b.lab(p) for p in b.outs if b.lab(p)]),
                    cx - s.w / 2 + K.text_w(b.value, 1.5) + 5.08)
        if b.note:
            right = max(right, cx - s.w / 2 + max(K.text_w(l) for l in b.note.split('\n')) + 5.08)
        placed.append(o)
    return right, y, placed


def port_col(S, x, y, items, title='Portas do módulo', pitch=5.08):
    """Module ports: a flag (hierarchical label) wired to a local label.
    items: (port label, local label, shape)."""
    S.text(title, x, y, size=1.5, thick=0.25)
    y = K.snap(y + 5.08)
    xa = K.snap_up(x + max(K.text_w(p) for p, _, _ in items) + 5.08, 2.54)
    for port, local, shape in items:
        S.wire((xa, y), (xa + 7.62, y))
        S.label(port, (xa, y), 'L', kind='hier', shape=shape)
        S.label(local, (xa + 7.62, y), 'R')
        y += pitch
    return y


def port_col_width(items):
    return K.snap_up(max(K.text_w(p) for p, _, _ in items) + 5.08, 2.54) + 7.62 + \
        max(K.text_w(n) for _, n, _ in items) + 2.54


def section(S, title, x, y):
    S.text(title, x, y, size=2.0, thick=0.3)


def sanitize(expr):
    e = expr.replace('{', '(').replace('}', ')').replace(' ', '')
    return e if len(e) <= 60 else e[:57] + '...'


_WIDTHS = {}


def widths(mname):
    """name -> '[msb:lsb]' for the wires, regs and ports of a module."""
    if mname not in _WIDTHS:
        m = MODS[mname]
        w = {p.name: p.width for p in m.ports}
        for d in re.finditer(r'\b(?:wire|reg)\s+(?:signed\s+)?(\[[^\]]+\])\s*([^;=]+)[;=]', m.body):
            for n in d.group(2).split(','):
                n = n.strip()
                if re.match(r'^\w+$', n):
                    w[n] = re.sub(r'\s+', '', d.group(1))
        _WIDTHS[mname] = w
    return _WIDTHS[mname]


def netname(mname, expr):
    """Label text for an instance connection: a bare identifier gets its
    declared width, anything else is shown as written in the RTL."""
    e = expr.strip()
    if re.match(r'^\w+$', e):
        return e + widths(mname).get(e, '')
    return sanitize(e)
