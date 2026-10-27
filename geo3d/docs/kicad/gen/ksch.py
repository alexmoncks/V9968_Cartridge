# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Writer for KiCad schematic files (.kicad_sch, KiCad 8 format 20231120).

Only what the logic drawings need: gate and block symbols embedded in each
sheet, wires, labels, hierarchical sheets, text and graphic boxes. All
coordinates in mm on the 1.27 mm grid; symbol graphics use KiCad's library
convention (y up), sheet items the page convention (y down).
"""
import uuid

PROJECT = 'geo3d_logic'
GRID = 1.27
CHAR_W = 1.05                     # mm per character of 1.27 mm text (estimate)
PAPERS = [('A4', 297, 210), ('A3', 420, 297), ('A2', 594, 420), ('A1', 841, 594), ('A0', 1189, 841)]


def U():
    return str(uuid.uuid4())


def snap(v, g=GRID):
    return round(round(v / g) * g, 4)


def snap_up(v, g=GRID):
    n = int(v / g)
    if n * g < v - 1e-6:
        n += 1
    return round(n * g, 4)


def f(v):
    v = round(v, 4)
    s = ('%.4f' % v).rstrip('0').rstrip('.')
    return '0' if s in ('-0', '', '-') else s


def q(s):
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def text_w(s, size=1.27):
    return len(s) * CHAR_W * size / 1.27


def effects(size=1.27, justify=None, hide=False, thick=None):
    t = f' (thickness {f(thick)})' if thick else ''
    j = f' (justify {justify})' if justify else ''
    h = ' hide' if hide else ''
    return f'(effects (font (size {f(size)} {f(size)}){t}){j}{h})'


def stroke(width=0.254, kind='default'):
    return f'(stroke (width {f(width)}) (type {kind}))'


# ----------------------------------------------------------------- graphics
def g_poly(pts, width=0.254, fill='background'):
    p = ' '.join(f'(xy {f(x)} {f(y)})' for x, y in pts)
    return f'(polyline (pts {p}) {stroke(width)} (fill (type {fill})))'


def g_arc(s, m, e, width=0.254, fill='none'):
    return (f'(arc (start {f(s[0])} {f(s[1])}) (mid {f(m[0])} {f(m[1])}) (end {f(e[0])} {f(e[1])}) '
            f'{stroke(width)} (fill (type {fill})))')


def g_rect(x1, y1, x2, y2, width=0.254, fill='background'):
    return f'(rectangle (start {f(x1)} {f(y1)}) (end {f(x2)} {f(y2)}) {stroke(width)} (fill (type {fill})))'


def g_circle(cx, cy, r, width=0.254, fill='none'):
    return f'(circle (center {f(cx)} {f(cy)}) (radius {f(r)}) {stroke(width)} (fill (type {fill})))'


def g_text(s, x, y, size=1.27, angle=0, justify=None):
    return f'(text {q(s)} (at {f(x)} {f(y)} {angle}) {effects(size, justify)})'


# ----------------------------------------------------------------- symbols
class LPin:
    """A library pin. (x, y) is the connection point; angle points from it
    towards the body (0: body to the right, i.e. a pin on the left side)."""

    def __init__(self, key, etype, x, y, angle, length=2.54, shape='line', name=None):
        self.key, self.etype, self.x, self.y = key, etype, x, y
        self.angle, self.length, self.shape = angle, length, shape
        self.name = key if name is None else name
        self.num = None


class SymDef:
    def __init__(self, name, pins, gfx, show_names=True, name_offset=0.508,
                 top=5.08, bottom=-5.08):
        self.name, self.pins, self.gfx = name, pins, gfx
        self.show_names, self.name_offset = show_names, name_offset
        self.top, self.bottom = top, bottom
        for i, p in enumerate(pins):
            p.num = str(i + 1)
        self.pin = {p.key: p for p in pins}

    def sexpr(self):
        n = self.name
        hide = '' if self.show_names else ' hide'
        out = [f'(symbol {q("geo3d:" + n)} (pin_numbers hide) (pin_names (offset {f(self.name_offset)}){hide})',
               '(exclude_from_sim no) (in_bom yes) (on_board yes)',
               f'(property "Reference" "U" (at 0 {f(self.top + 1.27)} 0) {effects()})',
               f'(property "Value" {q(n)} (at 0 {f(self.bottom - 1.27)} 0) {effects()})',
               f'(property "Footprint" "" (at 0 0 0) {effects(hide=True)})',
               f'(property "Datasheet" "" (at 0 0 0) {effects(hide=True)})',
               f'(property "Description" "" (at 0 0 0) {effects(hide=True)})',
               f'(symbol {q(n + "_0_1")} ' + ' '.join(self.gfx) + ')']
        pins = []
        for p in self.pins:
            pins.append(f'(pin {p.etype} {p.shape} (at {f(p.x)} {f(p.y)} {p.angle}) (length {f(p.length)}) '
                        f'(name {q(p.name)} {effects()}) (number {q(p.num)} {effects()}))')
        out.append(f'(symbol {q(n + "_1_1")} ' + ' '.join(pins) + ')')
        out.append(')')
        return ' '.join(out)


# gate geometry ------------------------------------------------------------
def _in_ys(n):
    """y of n inputs, 5.08 apart (room for labels), centred on 0."""
    if n % 2:
        return [5.08 * (n // 2 - i) for i in range(n)]
    return [5.08 * (n / 2 - i) - 2.54 for i in range(n)]


def _half(n):
    ys = _in_ys(n)
    return max(3.81, max(abs(y) for y in ys) + 1.27)


def gate(kind, n=2, inv=(), inv_out=False, name=None):
    """kind: AND, OR, XOR. inv: indexes of inverted inputs (bubble)."""
    hh = _half(n)
    ys = _in_ys(n)
    gfx = []
    if kind == 'AND':
        gfx.append(g_poly([(0, hh), (-3.81, hh), (-3.81, -hh), (0, -hh)], fill='none'))
        gfx.append(g_arc((0, hh), (hh, 0), (0, -hh)))
        # fill the body: a closed polygon approximating the arc
        tip = hh
        back = lambda y: -3.81
    else:
        bulge = 1.27
        gfx.append(g_arc((-3.81, hh), (-3.81 + bulge, 0), (-3.81, -hh)))
        tip = hh + 1.27
        s = (-1.27, hh)
        m = (-1.27 + (tip + 1.27) * 0.62, hh * 0.72)
        gfx.append(g_poly([(-3.81, hh), s], fill='none'))
        gfx.append(g_poly([(-3.81, -hh), (-1.27, -hh)], fill='none'))
        gfx.append(g_arc(s, m, (tip, 0)))
        gfx.append(g_arc((-1.27, -hh), (m[0], -m[1]), (tip, 0)))
        # x of the back curve at height y (circle through the 3 points)
        r = (hh * hh + bulge * bulge) / (2 * bulge)
        cxc = -3.81 + bulge - r
        back = lambda y, cxc=cxc, r=r: cxc + (r * r - y * y) ** 0.5
        if kind == 'XOR':
            gfx.append(g_arc((-5.08, hh), (-5.08 + bulge, 0), (-5.08, -hh)))
            back = lambda y, cxc=cxc, r=r: cxc - 1.27 + (r * r - y * y) ** 0.5
    pins = []
    for i, y in enumerate(ys):
        x0 = -7.62
        ln = back(y) - x0
        pins.append(LPin(chr(ord('A') + i), 'input', x0, y, 0, length=ln,
                         shape='inverted' if i in inv else 'line', name='~'))
    xo = snap_up(tip + 2.54, 2.54)
    pins.append(LPin('Y', 'output', xo, 0, 180, length=xo - tip,
                     shape='inverted' if inv_out else 'line', name='~'))
    nm = name or f'{kind}{n}' + (''.join(f'_N{i}' for i in inv)) + ('_INV' if inv_out else '')
    return SymDef(nm, pins, gfx, show_names=False, top=hh, bottom=-hh)


def inverter(buf=False):
    gfx = [g_poly([(-2.54, 2.54), (2.54, 0), (-2.54, -2.54), (-2.54, 2.54)])]
    pins = [LPin('A', 'input', -5.08, 0, 0, name='~'),
            LPin('Y', 'output', 5.08, 0, 180, shape='line' if buf else 'inverted', name='~')]
    return SymDef('BUF' if buf else 'NOT', pins, gfx, show_names=False, top=2.54, bottom=-2.54)


def tristate():
    gfx = [g_poly([(-2.54, 2.54), (2.54, 0), (-2.54, -2.54), (-2.54, 2.54)])]
    pins = [LPin('A', 'input', -5.08, 0, 0, name='~'),
            LPin('OE', 'input', 0, -5.08, 90, length=3.81, name='~'),
            LPin('Y', 'tri_state', 5.08, 0, 180, name='~')]
    return SymDef('TRIBUF', pins, gfx, show_names=False, top=2.54, bottom=-5.08)


def mux2():
    gfx = [g_poly([(-2.54, 7.62), (2.54, 5.08), (2.54, -5.08), (-2.54, -7.62), (-2.54, 7.62)]),
           g_text('1', -1.6, 2.54, 1.0), g_text('0', -1.6, -2.54, 1.0)]
    pins = [LPin('I1', 'input', -5.08, 2.54, 0, name='~'),
            LPin('I0', 'input', -5.08, -2.54, 0, name='~'),
            LPin('S', 'input', 0, -8.89, 90, length=2.54, name='~'),
            LPin('Y', 'output', 5.08, 0, 180, name='~')]
    return SymDef('MUX2', pins, gfx, show_names=False, top=7.62, bottom=-8.89)


def dff(en=False, rst=None, en_inv=False, rst_high=False, name=None):
    """D flip-flop, rising edge. en: clock enable (en_inv: active low).
    rst: None, 'R' (synchronous reset) or 'S' (synchronous set), active low
    unless rst_high."""
    gfx = [g_rect(-3.81, 7.62, 3.81, -7.62)]
    pins = [LPin('D', 'input', -6.35, 5.08, 0)]
    if en:
        pins.append(LPin('EN', 'input', -6.35, 0, 0, shape='inverted' if en_inv else 'line'))
    pins.append(LPin('C', 'input', -6.35, -5.08, 0, shape='clock', name=''))
    pins.append(LPin('Q', 'output', 6.35, 5.08, 180))
    if rst:
        pins.append(LPin(rst, 'input', 0, -10.16, 90, shape='line' if rst_high else 'inverted', name=''))
        gfx.append(g_text(rst, 0, -6.1, 1.27))
    nm = name or ('DFF' + ('E' if en else '') + ('N' if en_inv else '') + (rst or '') + ('H' if rst_high else ''))
    return SymDef(nm, pins, gfx, top=7.62, bottom=-10.16 if rst else -7.62)


def box(name, inputs, outputs, title=None, title_size=2.0, min_w=10.16, bottom=(), top=(),
        in_types=None, out_types=None):
    """A block with inputs on the left and outputs on the right.
    inputs/outputs: lists of pin names (or (name, etype, shape) tuples)."""
    def norm(lst, default):
        out = []
        for it in lst:
            if isinstance(it, tuple):
                nm, et, sh = (it + ('line',))[:3]
            else:
                nm, et, sh = it, default, 'line'
            out.append((nm, et, sh))
        return out
    ins, outs = norm(inputs, 'input'), norm(outputs, 'output')
    li = max([text_w(n) for n, _, _ in ins] + [0])
    lo = max([text_w(n) for n, _, _ in outs] + [0])
    tw = text_w(title, title_size) + 2.54 if title else 0
    w = max(min_w, li + lo + 5.08, (tw + 2 * max(li, lo) + 5.08) if title else 0)
    w = snap_up(w, 2.54)
    nrows = max(len(ins), len(outs), 1)
    h = snap_up((nrows + 1) * 2.54, 2.54)
    hh = h / 2
    gfx = [g_rect(-w / 2, hh, w / 2, -hh, fill='none')]
    if title:
        gfx.append(g_text(title, 0, 0, title_size))
    pins = []
    y0 = hh - 2.54
    for i, (nm, et, sh) in enumerate(ins):
        pins.append(LPin(nm, et, -w / 2 - 2.54, y0 - 2.54 * i, 0, shape=sh))
    for i, (nm, et, sh) in enumerate(outs):
        pins.append(LPin(nm, et, w / 2 + 2.54, y0 - 2.54 * i, 180, shape=sh))
    for i, nm in enumerate(bottom):
        x = -w / 2 + 2.54 * (i + 1) if len(bottom) > 1 else 0
        pins.append(LPin(nm, 'input', x, -hh - 2.54, 90))
    for i, nm in enumerate(top):
        x = -w / 2 + 2.54 * (i + 1) if len(top) > 1 else 0
        pins.append(LPin(nm, 'input', x, hh + 2.54, 270))
    s = SymDef(name, pins, gfx, top=hh, bottom=-hh)
    s.w, s.h = w, h
    return s


# ----------------------------------------------------------------- placement
DIR_OF_ANGLE = {0: 'L', 180: 'R', 90: 'D', 270: 'U'}
STEP = {'L': (-1, 0), 'R': (1, 0), 'U': (0, -1), 'D': (0, 1)}


class Placed:
    def __init__(self, sheet, sym, x, y):
        self.sheet, self.sym, self.x, self.y = sheet, sym, x, y
        self.p = {k: (snap(x + p.x, 0.0001), snap(y - p.y, 0.0001)) for k, p in sym.pin.items()}
        self.d = {k: DIR_OF_ANGLE[p.angle] for k, p in sym.pin.items()}

    def __getitem__(self, k):
        return self.p[k]


class SheetSym:
    def __init__(self, child, x, y, w, h, pins):
        self.child, self.x, self.y, self.w, self.h = child, x, y, w, h
        self.uuid = U()
        self.pins = pins            # (name, shape, side, y)
        self.p, self.d = {}, {}
        for name, shape, side, py in pins:
            if side == 'L':
                self.p[name], self.d[name] = (round(x, 4), round(py, 4)), 'L'
            else:
                self.p[name], self.d[name] = (round(x + w, 4), round(py, 4)), 'R'

    def __getitem__(self, k):
        return self.p[k]


class Sheet:
    def __init__(self, fname, title, paper=None, comments=(), sheetname=None):
        self.fname, self.title, self.paper = fname, title, paper
        self.sheetname = sheetname or fname.replace('.kicad_sch', '')
        self.comments = list(comments)
        self.uuid = U()
        self.items, self.syms, self.placed, self.segs, self.subs = [], {}, [], [], []
        self.anchors = set()        # pins, sheet pins
        self.soft = set()           # labels, no-connects: split points only
        self.path = None
        self.page = None
        self.extent = [0, 0]
        self.warnings = []

    # extent tracking (for the paper size)
    def _ext(self, x, y):
        self.extent[0] = max(self.extent[0], x)
        self.extent[1] = max(self.extent[1], y)

    # symbols
    def add(self, sym, x, y, value=None, show_value=None, ref='U', props=(), value_at=None,
            value_size=1.27, value_justify=None):
        x, y = snap(x), snap(y)
        self.syms[sym.name] = sym
        pl = Placed(self, sym, x, y)
        pl.value = value if value is not None else sym.name
        if show_value is None:
            show_value = value is not None or sym.show_names
        pl.show_value, pl.refp = show_value, ref
        pl.props = list(props)
        pl.value_at = value_at
        pl.value_size, pl.value_justify = value_size, value_justify
        self.placed.append(pl)
        for pt in pl.p.values():
            self.anchors.add(pt)
            self._ext(*pt)
        self._ext(x + 10, y - sym.bottom + 5)
        return pl

    # wires
    def wire(self, *pts, width=0):
        pts = [(snap(x, 0.0001), snap(y, 0.0001)) for x, y in pts]
        for a, b in zip(pts, pts[1:]):
            if a != b:
                self.segs.append([a, b, width])
                self._ext(*a)
                self._ext(*b)

    def route(self, a, b, mx=None, my=None, width=0):
        """Orthogonal route a -> b: straight, via x = mx (H-V-H) or y = my
        (V-H-V), else an L that leaves a horizontally."""
        if a[0] == b[0] or a[1] == b[1]:
            self.wire(a, b, width=width)
        elif mx is not None:
            self.wire(a, (mx, a[1]), (mx, b[1]), b, width=width)
        elif my is not None:
            self.wire(a, (a[0], my), (b[0], my), b, width=width)
        else:
            self.wire(a, (b[0], a[1]), b, width=width)

    def _label(self, kind, name, pt, d, shape='input', size=1.27):
        ang = {'R': 0, 'L': 180, 'U': 90, 'D': 270}[d]
        x, y = pt
        if kind == 'hier':
            just = 'left' if d in 'RU' else 'right'
            self.items.append(f'(hierarchical_label {q(name)} (shape {shape}) (at {f(x)} {f(y)} {ang}) '
                              f'(fields_autoplaced yes) {effects(size, just)} (uuid {q(U())}))')
            extra = text_w(name) + 4
        else:
            just = ('left' if d in 'RU' else 'right') + ' bottom'
            self.items.append(f'(label {q(name)} (at {f(x)} {f(y)} {ang}) (fields_autoplaced yes) '
                              f'{effects(size, just)} (uuid {q(U())}))')
            extra = text_w(name) + 1
        self.soft.add((snap(x, 0.0001), snap(y, 0.0001)))
        dx, dy = STEP[d]
        self._ext(x + dx * extra, y + dy * extra)

    def label(self, name, pt, d='R', kind='local', shape='input'):
        self._label(kind, name, pt, d, shape)

    def stub(self, pt, name, d, length=5.08, kind='local', shape='input', width=0):
        dx, dy = STEP[d]
        end = (pt[0] + dx * length, pt[1] + dy * length)
        self.wire(pt, end, width=width)
        self._label(kind, name, end, d, shape)
        return end

    def net(self, obj, key, name, length=5.08, kind='local', shape=None, width=0):
        """Stub + label on a pin of a placed symbol or sheet symbol."""
        d = obj.d[key]
        if shape is None:
            shape = 'output' if d == 'R' else 'input'
        return self.stub(obj.p[key], name, d, length, kind, shape, width)

    def net_low(self, obj, key, name, kind='local', shape='input', drop=2.54, run=3.81):
        """Bottom (or top) pin: a short drop, then the label to the right."""
        p = obj.p[key]
        sgn = 1 if obj.d[key] == 'D' else -1
        mid = (p[0], p[1] + sgn * drop)
        end = (p[0] + run, mid[1])
        self.wire(p, mid, end)
        self._label(kind, name, end, 'R', shape)
        return end

    def nc(self, pt):
        self.items.append(f'(no_connect (at {f(pt[0])} {f(pt[1])}) (uuid {q(U())}))')
        self.soft.add(pt)

    # text and graphics
    def text(self, s, x, y, size=1.27, justify='left top', thick=None, angle=0):
        self.items.append(f'(text {q(s)} (exclude_from_sim no) (at {f(x)} {f(y)} {angle}) '
                          f'{effects(size, justify, thick=thick)} (uuid {q(U())}))')
        lines = s.split('\n')
        self._ext(x + max(text_w(l, size) for l in lines), y + len(lines) * size * 1.6)

    def textbox(self, s, x, y, w, h, size=1.27, border=True, fill=None):
        st = stroke(0.1524 if border else 0, 'solid' if border else 'default')
        fl = f'(fill (type color) (color {fill}))' if fill else '(fill (type none))'
        self.items.append(f'(text_box {q(s)} (exclude_from_sim no) (at {f(x)} {f(y)} 0) (size {f(w)} {f(h)}) '
                          f'{st} {fl} {effects(size, "left top")} (uuid {q(U())}))')
        self._ext(x + w, y + h)

    def gline(self, *pts, width=0.254, kind='solid'):
        """A graphic line (not a wire: it connects nothing)."""
        p = ' '.join(f'(xy {f(x)} {f(y)})' for x, y in pts)
        self.items.append(f'(polyline (pts {p}) {stroke(width, kind)} (uuid {q(U())}))')
        for x, y in pts:
            self._ext(x, y)

    def arrow(self, a, b, width=0.254):
        """A graphic arrow a -> b (horizontal or vertical)."""
        self.gline(a, b, width=width)
        dx = (b[0] > a[0]) - (b[0] < a[0])
        dy = (b[1] > a[1]) - (b[1] < a[1])
        ax, ay = b[0] - dx * 1.5, b[1] - dy * 1.5
        px, py = -dy * 0.75, dx * 0.75
        self.gline((ax + px, ay + py), b, (ax - px, ay - py), width=width)

    def rect(self, x1, y1, x2, y2, kind='dash', width=0.254, fill=None):
        fl = f'(fill (type color) (color {fill}))' if fill else '(fill (type none))'
        self.items.append(f'(rectangle (start {f(x1)} {f(y1)}) (end {f(x2)} {f(y2)}) '
                          f'{stroke(width, kind)} {fl} (uuid {q(U())}))')
        self._ext(max(x1, x2), max(y1, y2))

    # hierarchy
    def subsheet(self, child, x, y, left, right, w=None, name=None):
        """left/right: lists of (pin name, shape). Returns a SheetSym."""
        x, y = snap(x), snap(y)
        lw = max([text_w(n) for n, _ in left] + [0])
        rw = max([text_w(n) for n, _ in right] + [0])
        if w is None:
            w = snap_up(max(lw + rw + 7.62, text_w(name or child.sheetname) + 5.08, 20.32), 2.54)
        n = max(len(left), len(right), 1)
        h = snap_up((n + 1) * 2.54, 2.54)
        pins = []
        for i, (nm, sh) in enumerate(left):
            pins.append((nm, sh, 'L', y + 2.54 * (i + 1)))
        for i, (nm, sh) in enumerate(right):
            pins.append((nm, sh, 'R', y + 2.54 * (i + 1)))
        ss = SheetSym(child, x, y, w, h, pins)
        ss.name = name or child.sheetname
        self.subs.append(ss)
        for pt in ss.p.values():
            self.anchors.add(pt)
        self._ext(x + w, y + h + 3)
        return ss

    # ------------------------------------------------------------- output
    def _split_and_junctions(self):
        pts = set(self.anchors) | set(self.soft)
        for a, b, _ in self.segs:
            pts.add(a)
            pts.add(b)
        out = []
        for a, b, w in self.segs:
            (x1, y1), (x2, y2) = a, b
            inner = []
            for p in pts:
                if p == a or p == b:
                    continue
                if x1 == x2 == p[0] and min(y1, y2) < p[1] < max(y1, y2):
                    inner.append(p)
                elif y1 == y2 == p[1] and min(x1, x2) < p[0] < max(x1, x2):
                    inner.append(p)
            for p in inner:
                if p in self.anchors:
                    self.warnings.append(f'{self.fname}: wire {a}-{b} runs through pin/label at {p}')
            inner.sort(key=lambda p: abs(p[0] - x1) + abs(p[1] - y1))
            chain = [a] + inner + [b]
            for c, d in zip(chain, chain[1:]):
                out.append((c, d, w))
        count = {}
        for a, b, _ in out:
            count[a] = count.get(a, 0) + 1
            count[b] = count.get(b, 0) + 1
        for p in self.anchors:
            if p in count:
                count[p] += 1
        juncs = [p for p, c in count.items() if c >= 3]
        return out, juncs

    def paper_size(self):
        if self.paper:
            for nm, w, h in PAPERS:
                if nm == self.paper:
                    return nm, w, h
        need_w, need_h = self.extent[0] + 15, self.extent[1] + 45
        for nm, w, h in PAPERS:
            if w >= need_w and h >= need_h:
                return nm, w, h
        return PAPERS[-1]

    def render(self, refs, date, rev, company, root_uuid):
        nm, _, _ = self.paper_size()
        lines = ['(kicad_sch (version 20231120) (generator "eeschema") (generator_version "8.0")',
                 f'(uuid {q(self.uuid)})', f'(paper {q(nm)})']
        tb = [f'(title {q(self.title)})', f'(date {q(date)})', f'(rev {q(rev)})', f'(company {q(company)})']
        for i, c in enumerate(self.comments[:9]):
            tb.append(f'(comment {i + 1} {q(c)})')
        lines.append('(title_block ' + ' '.join(tb) + ')')
        lines.append('(lib_symbols')
        for s in sorted(self.syms.values(), key=lambda s: s.name):
            lines.append(s.sexpr())
        lines.append(')')
        segs, juncs = self._split_and_junctions()
        for p in sorted(juncs):
            lines.append(f'(junction (at {f(p[0])} {f(p[1])}) (diameter 0) (color 0 0 0 0) (uuid {q(U())}))')
        for a, b, w in segs:
            lines.append(f'(wire (pts (xy {f(a[0])} {f(a[1])}) (xy {f(b[0])} {f(b[1])})) '
                         f'{stroke(w)} (uuid {q(U())}))')
        lines.extend(self.items)
        for pl in self.placed:
            lines.append(self._render_symbol(pl, refs))
        for ss in self.subs:
            lines.append(self._render_sheet(ss))
        if self.path == '/' + root_uuid:
            lines.append('(sheet_instances (path "/" (page "1")))')
        lines.append(')')
        return '\n'.join(lines) + '\n'

    def _render_symbol(self, pl, refs):
        sym = pl.sym
        n = refs.get(pl.refp, 0) + 1
        refs[pl.refp] = n
        ref = f'{pl.refp}{n}'
        x, y = pl.x, pl.y
        if pl.value_at:
            vx, vy = pl.value_at
        else:
            vx, vy = x, y - sym.top - 1.27
        vj = pl.value_justify
        props = [f'(property "Reference" {q(ref)} (at {f(x)} {f(y - sym.bottom + 1.27)} 0) {effects(hide=True)})',
                 f'(property "Value" {q(pl.value)} (at {f(vx)} {f(vy)} 0) '
                 f'{effects(pl.value_size, vj, hide=not pl.show_value)})',
                 f'(property "Footprint" "" (at {f(x)} {f(y)} 0) {effects(hide=True)})',
                 f'(property "Datasheet" "" (at {f(x)} {f(y)} 0) {effects(hide=True)})',
                 f'(property "Description" "" (at {f(x)} {f(y)} 0) {effects(hide=True)})']
        for i, (k, v, px, py, size, just) in enumerate(pl.props):
            props.append(f'(property {q(k)} {q(v)} (at {f(px)} {f(py)} 0) {effects(size, just)})')
        pins = ' '.join(f'(pin {q(p.num)} (uuid {q(U())}))' for p in sym.pins)
        return (f'(symbol (lib_id {q("geo3d:" + sym.name)}) (at {f(x)} {f(y)} 0) (unit 1) '
                f'(exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (uuid {q(U())}) '
                + ' '.join(props) + ' ' + pins +
                f' (instances (project {q(PROJECT)} (path {q(self.path)} (reference {q(ref)}) (unit 1)))))')

    def _render_sheet(self, ss):
        x, y, w, h = ss.x, ss.y, ss.w, ss.h
        out = [f'(sheet (at {f(x)} {f(y)}) (size {f(w)} {f(h)}) (fields_autoplaced yes) '
               f'(stroke (width 0.1524) (type solid)) (fill (color 255 255 225 1.0000)) (uuid {q(ss.uuid)})',
               f'(property "Sheetname" {q(ss.name)} (at {f(x)} {f(y - 0.7116)} 0) {effects(1.5, "left bottom")})',
               f'(property "Sheetfile" {q(ss.child.fname)} (at {f(x)} {f(y + h + 0.5884)} 0) '
               f'{effects(1.27, "left top")})']
        for name, shape, side, py in ss.pins:
            if side == 'L':
                out.append(f'(pin {q(name)} {shape} (at {f(x)} {f(py)} 180) {effects(1.27, "left")} (uuid {q(U())}))')
            else:
                out.append(f'(pin {q(name)} {shape} (at {f(x + w)} {f(py)} 0) {effects(1.27, "right")} '
                           f'(uuid {q(U())}))')
        out.append(f'(instances (project {q(PROJECT)} (path {q(self.path)} (page {q(str(ss.child.page))}))))')
        out.append(')')
        return ' '.join(out)


def write_project(root, outdir, date, rev, company):
    """Assign paths and page numbers depth-first, then write every sheet."""
    import os
    import json
    order = []

    def walk(sh, path):
        sh.path = path
        order.append(sh)
        sh.page = len(order)
        for ss in sh.subs:
            walk(ss.child, path + '/' + ss.uuid)

    walk(root, '/' + root.uuid)
    refs = {}
    for sh in order:
        with open(os.path.join(outdir, sh.fname), 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(sh.render(refs, date, rev, company, root.uuid))
        for w in sh.warnings:
            print('WARN', w)
    pro = {'meta': {'filename': PROJECT + '.kicad_pro', 'version': 3},
           'schematic': {'drawing': {'default_text_size': 50.0}},
           'sheets': [[sh.uuid, sh.sheetname if sh is not root else 'Root'] for sh in order]}
    with open(os.path.join(outdir, PROJECT + '.kicad_pro'), 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(pro, fh, indent=2)
    return order
