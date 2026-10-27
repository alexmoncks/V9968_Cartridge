# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Minimal Verilog reader for the schematic generator.

Reads module headers (ANSI or classic port lists), port directions and
widths, and named-port instances. Enough for HRA!'s V9968 sources and
geo3d; not a general Verilog parser.
"""
import re

KEYWORDS = {
    'module', 'endmodule', 'input', 'output', 'inout', 'wire', 'reg', 'assign',
    'always', 'begin', 'end', 'if', 'else', 'case', 'endcase', 'function',
    'endfunction', 'localparam', 'parameter', 'integer', 'genvar', 'generate',
    'endgenerate', 'for', 'initial', 'signed', 'default',
}


def strip_comments(text):
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    return re.sub(r'//[^\n]*', '', text)


def read_source(path):
    with open(path, 'rb') as fh:
        return strip_comments(fh.read().decode('latin-1'))


def _balanced(text, pos):
    """text[pos] == '(' -> index just past the matching ')'."""
    depth = 0
    for i in range(pos, len(text)):
        c = text[i]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return i + 1
    raise ValueError('unbalanced parentheses')


def _split_top(text, sep=','):
    parts, depth, cur = [], 0, []
    for c in text:
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        if c == sep and depth == 0:
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append(''.join(cur))
    return [p.strip() for p in parts if p.strip()]


class Port:
    def __init__(self, name, direction, width):
        self.name, self.dir, self.width = name, direction, width

    @property
    def label(self):
        return f'{self.name}{self.width}' if self.width else self.name

    def __repr__(self):
        return f'{self.dir} {self.label}'


class Instance:
    def __init__(self, module, name, conns):
        self.module, self.name, self.conns = module, name, conns


class Module:
    def __init__(self, name, ports, instances, body):
        self.name, self.ports, self.instances, self.body = name, ports, instances, body

    def port(self, name):
        for p in self.ports:
            if p.name == name:
                return p
        return None


_DECL = re.compile(r'^(input|output|inout)\b\s*(?:(?:wire|reg|logic)\b)?\s*(signed\b)?\s*(\[[^\]]+\])?\s*(.*)$',
                   re.S)


def _norm_width(w):
    return re.sub(r'\s+', '', w) if w else ''


def parse_modules(text):
    mods = {}
    for m in re.finditer(r'\bmodule\s+(\w+)', text):
        name = m.group(1)
        pos = m.end()
        while text[pos].isspace():
            pos += 1
        if text[pos] == '#':
            pos = text.index('(', pos)
            pos = _balanced(text, pos)
            while text[pos].isspace():
                pos += 1
        hdr_end = _balanced(text, pos)
        header = text[pos + 1:hdr_end - 1]
        end = text.index('endmodule', hdr_end)
        body = text[hdr_end:end]

        ports = []
        cur_dir, cur_w = None, ''
        for item in _split_top(header):
            d = _DECL.match(item)
            if d:
                cur_dir, cur_w = d.group(1), _norm_width(d.group(3))
                pname = d.group(4).strip()
            else:
                pname = item.strip()
            pname = pname.split('=')[0].strip()
            if cur_dir is None:
                ports.append(Port(pname, None, ''))
            else:
                ports.append(Port(pname, cur_dir, cur_w))
        # classic style: directions declared in the body
        if any(p.dir is None for p in ports):
            for d in re.finditer(r'\b(input|output|inout)\b\s*(?:(?:wire|reg)\b)?\s*(signed\b)?\s*(\[[^\]]+\])?\s*'
                                 r'([^;]+);', body):
                for n in _split_top(d.group(4)):
                    n = n.split('=')[0].strip()
                    for p in ports:
                        if p.name == n:
                            p.dir, p.width = d.group(1), _norm_width(d.group(3))

        insts = []
        for im in re.finditer(r'\b([A-Za-z_]\w*)\s*(#\s*\()?', body):
            mod = im.group(1)
            if mod in KEYWORDS:
                continue
            p = im.end()
            if im.group(2):
                p = _balanced(body, im.end() - 1)
            mm = re.match(r'\s*([A-Za-z_]\w*)\s*\(', body[p:])
            if not mm or mm.group(1) in KEYWORDS:
                continue
            open_at = p + mm.end() - 1
            # the instance must start a statement
            before = body[:im.start()].rstrip()
            if before and before[-1] not in ';)':
                if not before.endswith(('end', 'endcase', 'endfunction', 'endgenerate')):
                    continue
            try:
                close = _balanced(body, open_at)
            except ValueError:
                continue
            if not body[close:].lstrip().startswith(';'):
                continue
            conns = {}
            for c in _split_top(body[open_at + 1:close - 1]):
                cm = re.match(r'\.(\w+)\s*\((.*)\)\s*$', c, re.S)
                if cm:
                    conns[cm.group(1)] = re.sub(r'\s+', ' ', cm.group(2)).strip()
            if conns:
                insts.append(Instance(mod, mm.group(1), conns))
        mods[name] = Module(name, ports, insts, body)
    return mods


def load(paths):
    mods = {}
    for p in paths:
        mods.update(parse_modules(read_source(p)))
    return mods


# The design drawn by the schematic: HRA!'s cartridge top and V9968 from the
# Tang Nano 20K project, geo3d v2.0 from geo3d/rtl (v1.0.0 is the copy under
# fpga/, identical except for the RAM depth and the engine's reset flop).
import os
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
SRC = os.path.join(REPO, 'fpga', 'V9968_Cartridge_TangNano20K_geo3d', 'src')


def sources():
    out = [os.path.join(SRC, 'tangnano20k_vdp_cartridge.v')]
    for sub in ('v9968', 'msx_slot', 'sdram', 'ws2812_led', 'debugger'):
        d = os.path.join(SRC, sub)
        out += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith('.v')]
    out.append(os.path.join(SRC, 'dvi_tx', 'dvi_tx.v'))
    for sub, f in (('gowin_rpll', 'gowin_rpll.v'), ('gowin_rpll2', 'gowin_rpll2.v'),
                   ('gowin_clkdiv', 'gowin_clkdiv.v')):
        out.append(os.path.join(SRC, sub, f))
    for f in ('geo3d_bus.v', 'geo3d_engine.v', 'geo3d_core.v'):
        out.append(os.path.join(REPO, 'geo3d', 'rtl', f))
    return out


if __name__ == '__main__':
    import sys
    ms = load(sys.argv[1:] or sources())
    for name, m in sorted(ms.items()):
        ins = sum(1 for p in m.ports if p.dir == 'input')
        outs = len(m.ports) - ins
        print(f'{name:40} ports={len(m.ports):4} in={ins:4} out/io={outs:4} insts=' +
              ','.join(f'{i.name}:{i.module}' for i in m.instances))
