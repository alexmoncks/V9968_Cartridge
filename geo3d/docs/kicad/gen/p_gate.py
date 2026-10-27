# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Gate-level pages: msx_slot (HRA!) and geo3d_bus (geo3d v2.0)."""
import ksch as K
from common import (MODS, LEGEND, AND, OR, XOR, NOT, TRI, MUX, H, header, section, right_of,
                    alias, nets, res_str)

D = K.dff()
DR = K.dff(rst='R')
DS = K.dff(rst='S')
DE = K.dff(en=True)
DEN = K.dff(en=True, en_inv=True)
DER = K.dff(en=True, rst='R')
DENR = K.dff(en=True, en_inv=True, rst='R')
DRH = K.dff(rst='R', rst_high=True)
DERH = K.dff(en=True, rst='R', rst_high=True)


class Cur:
    """Vertical cursor of a column: take(h) returns the row's y and moves on."""

    def __init__(self, y):
        self.y = y

    def take(self, h, before=0.0):
        self.y += before
        y = K.snap(self.y)
        self.y += h
        return y


def low(S, obj, key, name, hier=False):
    drop = 5.08 if key in ('S', 'OE') and obj.sym.name in ('MUX2', 'TRIBUF') else 2.54
    S.net_low(obj, key, name, kind='hier' if hier else 'local', drop=drop)


def join(S, src, dst, name=None, mx=None):
    """Route an output to an input with an optional label right after src."""
    S.route(src, dst, mx=mx)
    if name:
        S.label(name, (src[0] + 2.54, src[1]), 'R')


def ff_after(S, src, sym, en=None, label=None, gap=7.62, **kw):
    """A flip-flop whose D is wired to src; leaves room for an EN label."""
    if en:
        gap = max(gap, K.snap_up(K.text_w(en if isinstance(en, str) else en[0]) + 12.7, 2.54))
    f = right_of(S, src, sym, 'D', gap, label=label, **kw)
    if en:
        S.net(f, 'EN', en) if isinstance(en, str) else nets(S, f, {'EN': en})
    return f


def note(S, s, x, y):
    S.text(s, x, y, size=1.27)


# ====================================================================== msx_slot
def page_msx_slot():
    m = MODS['msx_slot']

    def P(n):
        return m.port(n).label

    RST = H(P('p_slot_reset_n'))
    S = K.Sheet('msx_slot.kicad_sch', 'msx_slot: slot MSX -> barramento local (portas logicas)', paper='A2',
                comments=['Fonte: fpga/V9968_Cartridge_TangNano20K_geo3d/src/msx_slot/msx_slot.v (HRA!)'])
    header(S, 'msx_slot (u_msx_slot): ciclos de I/O do Z80 viram transações no barramento local',
           'Todo o módulo roda em clk = 85,9 MHz (clk85m).  O slot é assíncrono: IORQ/WR/RD passam por 2 flip-flops '
           'antes de qualquer uso.\nDecodifica um bloco de 8 portas (88h-8Fh ou 98h-9Fh, escolhido pela dipsw) e entrega '
           'endereço [2:0], dado e direção ao V9968 e ao geo3d.\nRecursos (síntese): ' + res_str('u_msx_slot'))

    # ---------------------------------------------------------------- column 1
    x0 = 25.4
    c = Cur(48.26)
    section(S, '1. Sincronização dos sinais do slot (2 flip-flops por sinal)', x0, c.take(10.16))
    for sig in ('ioreq_n', 'wr_n', 'rd_n'):
        y = c.take(20.32, before=5.08)
        f1 = S.add(D, 80.01, y, show_value=False)
        S.net(f1, 'D', P('p_slot_' + sig), kind='hier')
        S.net(f1, 'C', 'clk', kind='hier')
        f2 = right_of(S, f1['Q'], D, 'D', 10.16, label='ff_pre_slot_' + sig, show_value=False)
        S.net(f2, 'C', 'clk', kind='hier')
        S.net(f2, 'Q', 'ff_slot_' + sig)
    note(S, 'Latch "SDRAM ainda inicializando" (segura os ciclos até a SDRAM ficar pronta):', x0, c.take(10.16))
    y = c.take(25.4)
    g = S.add(OR(2, inv=(1,)), 62.23, y)
    nets(S, g, {'A': H(P('initial_busy')), 'B': RST})
    f = ff_after(S, g['Y'], D, show_value=False)
    S.net(f, 'C', 'clk', kind='hier')
    S.net(f, 'Q', 'ff_initial_busy')

    section(S, '2. Detecção do ciclo de I/O', x0, c.take(12.7, before=5.08))
    for kind, other in (('wr', 'ff_slot_wr_n'), ('rd', 'ff_slot_rd_n')):
        y = c.take(30.48)
        g = S.add(OR(2, nor=True), 62.23, y)
        nets(S, g, {'A': 'ff_slot_ioreq_n', 'B': other})
        f = ff_after(S, g['Y'], DENR, en='ff_initial_busy', show_value=False)
        S.net(f, 'C', 'clk', kind='hier')
        low(S, f, 'R', P('p_slot_reset_n'), hier=True)
        S.net(f, 'Q', f'ff_iorq_{kind}')
    y = c.take(30.48)
    g = S.add(OR(2), 62.23, y)
    nets(S, g, {'A': 'ff_iorq_wr', 'B': 'ff_iorq_rd'})
    f = ff_after(S, g['Y'], DR, label='w_active', show_value=False)
    S.net(f, 'C', 'clk', kind='hier')
    low(S, f, 'R', P('p_slot_reset_n'), hier=True)
    S.net(f, 'Q', 'ff_active')
    y = c.take(15.24)
    g = S.add(AND(2, inv=(0,)), 62.23, y)
    nets(S, g, {'A': 'ff_active', 'B': 'w_active', 'Y': 'w_start*'})
    note(S, 'w_start* = !ff_active && w_active : primeiro clock de um ciclo IORQ+WR ou IORQ+RD.\n'
            'ff_initial_busy = 1 congela ff_iorq_wr/rd (EN com bolinha = ativo em 0).', x0, c.take(10))

    # ---------------------------------------------------------------- column 2
    x1 = 177.8
    c = Cur(48.26)
    section(S, '3. Latches de endereço e de dado escrito', x1, c.take(10.16))
    y = c.take(25.4, before=7.62)
    f = S.add(DEN, 228.6, y, value='ff_slot_address[7:0]  (8 FF)')
    S.net(f, 'D', P('p_slot_address'), kind='hier')
    S.net(f, 'EN', 'ff_slot_ioreq_n')
    S.net(f, 'C', 'clk', kind='hier')
    S.net(f, 'Q', 'ff_slot_address[7:0]')
    y = c.take(22.86, before=2.54)
    f = S.add(DEN, 228.6, y, value='ff_slot_data[7:0]  (8 FF)')
    S.net(f, 'D', P('p_slot_data'), kind='hier', shape='bidirectional')
    S.net(f, 'EN', 'ff_slot_wr_n')
    S.net(f, 'C', 'clk', kind='hier')
    alias(S, f, 'Q', 'ff_slot_data[7:0]', P('bus_wdata'))
    note(S, 'Captura enquanto IORQ_n (endereço) ou WR_n (dado) está em 0:\n'
            'quando ff_slot_ioreq_n = 0 o endereço e o dado já estão estáveis.', x1, c.take(12.7))

    section(S, '4. Decodificação do bloco de portas (88h ou 98h)', x1, c.take(10.16, before=2.54))
    y = c.take(30.48, before=12.7)
    g = S.add(XOR(xnor=True), 203.2, y)
    nets(S, g, {'A': 'ff_slot_address[4]', 'B': H(P('dipsw')), 'Y': 'a4_ok*'})
    g = S.add(AND(5, inv=(1, 2)), 279.4, y)
    nets(S, g, {'A': 'ff_slot_address[7]', 'B': 'ff_slot_address[6]', 'C': 'ff_slot_address[5]',
                'D': 'a4_ok*', 'E': 'ff_slot_address[3]', 'Y': 'w_hit*'})
    note(S, "RTL: { ff_slot_address[7:3], 3'd0 } == w_io_address,  w_io_address = dipsw ? 8'h98 : 8'h88\n"
            'Em portas: A7 · /A6 · /A5 · (A4 XNOR dipsw) · A3   (bits 2..0 escolhem a porta dentro do bloco)',
         x1, c.take(12.7))

    section(S, '5. Transação no barramento local (valid / ioreq / write / address)', x1, c.take(12.7, before=2.54))
    y = c.take(22.86, before=7.62)
    a1 = S.add(AND(3, inv=(1,)), 200.66, y)
    nets(S, a1, {'A': 'ff_valid', 'B': H(P('bus_ready')), 'C': 'ff_ioreq'})
    y2 = c.take(25.4)
    a2 = S.add(AND(3, inv=(0,)), 200.66, y2)
    nets(S, a2, {'A': 'ff_valid', 'B': 'w_start*', 'C': 'w_hit*'})
    o = S.add(OR(2), 254.0, K.snap((y + y2) / 2))
    join(S, a1['Y'], o['A'], 'keep*', mx=233.68)
    join(S, a2['Y'], o['B'], 'w_load*', mx=236.22)
    f = ff_after(S, o['Y'], DR, show_value=False)
    S.net(f, 'C', 'clk', kind='hier')
    low(S, f, 'R', P('p_slot_reset_n'), hier=True)
    alias(S, f, 'Q', 'ff_valid', P('bus_valid'))
    note(S, 'valid fica em 1 até o destino aceitar (bus_ready) ou se o endereço não era do cartucho.',
         x1, c.take(10.16))
    for which, a_sym, a_nets, i1, ffs, local, port in (
            ('ioreq', AND(2), {'A': 'ff_ioreq', 'B': 'ff_active'}, 'w_hit*', DR, 'ff_ioreq', 'bus_ioreq'),
            ('write', OR(2, inv=(1,)), {'A': 'ff_write', 'B': 'ff_active'}, 'ff_iorq_wr', DS, 'ff_write',
             'bus_write')):
        y = c.take(33.02, before=5.08)
        g = S.add(a_sym, 200.66, y)
        nets(S, g, a_nets)
        m1 = S.add(MUX, 241.3, y - 2.54)
        join(S, g['Y'], m1['I0'], f'hold_{which}*')
        S.net(m1, 'I1', i1)
        low(S, m1, 'S', 'w_start*')
        m2 = right_of(S, m1['Y'], MUX, 'I0', K.snap_up(K.text_w(local) + 10.16, 2.54))
        S.net(m2, 'I1', local)
        low(S, m2, 'S', 'ff_valid')
        f = ff_after(S, m2['Y'], ffs, gap=12.7, show_value=False)
        S.net(f, 'C', 'clk', kind='hier')
        low(S, f, 'R' if ffs is DR else 'S', P('p_slot_reset_n'), hier=True)
        alias(S, f, 'Q', local, P(port))
    y = c.take(25.4, before=5.08)
    f = S.add(DE, 228.6, y, value='ff_bus_address[2:0]  (3 FF)')
    nets(S, f, {'D': 'ff_slot_address[2:0]', 'EN': 'w_load*', 'C': H('clk')})
    alias(S, f, 'Q', 'ff_bus_address[2:0]', P('bus_address'))
    note(S, 'Prioridade do RTL: reset > (valid: mantém) > (w_start*: carrega) > (!ff_active: limpa) > mantém.\n'
            'ff_write volta para 1 (set) no reset e fora de ciclo; ff_ioreq e ff_valid voltam para 0.',
         x1, c.take(10.16))

    # ---------------------------------------------------------------- column 3
    x2 = 381.0
    c = Cur(48.26)
    section(S, '6. Leitura: dado para o Z80 e saídas do slot', x2, c.take(10.16))
    y = c.take(30.48, before=5.08)
    o = S.add(OR(2), 406.4, y)
    nets(S, o, {'A': H(P('bus_rdata_en')), 'B': 'ff_data_out'})
    a = right_of(S, o['Y'], AND(2), 'B', 7.62)
    S.net(a, 'A', 'ff_iorq_rd')
    f = ff_after(S, a['Y'], DR, show_value=False)
    S.net(f, 'C', 'clk', kind='hier')
    low(S, f, 'R', P('p_slot_reset_n'), hier=True)
    alias(S, f, 'Q', 'ff_data_out', P('p_slot_data_dir'))
    note(S, 'ff_data_out: 1 depois que o destino entregou o dado (bus_rdata_en) e enquanto durar o RD.',
         x2, c.take(10.16))
    y = c.take(15.24, before=5.08)
    a = S.add(AND(2), 406.4, y)
    nets(S, a, {'A': RST, 'B': 'ff_ioreq', 'Y': 'rdata_keep*'})
    y = c.take(33.02, before=5.08)
    mx = S.add(MUX, 406.4, y)
    S.net(mx, 'I1', P('bus_rdata'), kind='hier')
    S.net(mx, 'I0', 'ff_rdata[7:0]')
    low(S, mx, 'S', P('bus_rdata_en'), hier=True)
    f = ff_after(S, mx['Y'], DR, value='ff_rdata[7:0]  (8 FF)')
    S.net(f, 'C', 'clk', kind='hier')
    low(S, f, 'R', 'rdata_keep*')
    S.net(f, 'Q', 'ff_rdata[7:0]')
    y = c.take(15.24, before=5.08)
    t = S.add(TRI, 408.94, y)
    S.net(t, 'A', 'ff_rdata[7:0]')
    low(S, t, 'OE', 'ff_data_out')
    S.net(t, 'Y', P('p_slot_data'), kind='hier', shape='bidirectional')
    note(S, "8 buffers tri-state: p_slot_data = ff_data_out ? ff_rdata : 8'hZZ\n"
            '(o sentido do transceptor externo vem de p_slot_data_dir)', x2, c.take(12.7))
    y = c.take(10.16, before=5.08)
    n = S.add(NOT, 408.94, y)
    S.net(n, 'A', P('int_n'), kind='hier')
    S.net(n, 'Y', P('p_slot_int'), kind='hier', shape='output')
    note(S, 'p_slot_int = ~int_n (1 = pede interrupção; o transistor externo puxa INT para 0)', x2, c.take(15.24))
    S.textbox(LEGEND, x2, c.take(55), 165.1, 26.67, size=1.27)
    return S


# ====================================================================== geo3d_bus
def page_geo3d_bus(engine_sheet):
    m = MODS['geo3d_bus']

    def P(n):
        return m.port(n).label

    S = K.Sheet('geo3d_bus.kicad_sch', 'geo3d_bus: adaptador barramento <-> geo3d_engine (portas logicas)',
                paper='A1',
                comments=['Fonte: geo3d/rtl/geo3d_bus.v (geo3d v2.0, MIT)'])
    header(S, 'geo3d_bus (u_geo3d): portas 8Dh/8Fh (ou 9Dh/9Fh) do Z80 -> geo3d_engine -> registradores de comando '
              'do V9968',
           'Dois domínios de clock da mesma PLL: clk = 85,9 MHz (barramento e V9968) e clk_eng = 42,95 MHz (engine, '
           'CLKOUTD de u_pll2).\nAs travessias usam "toggles": um lado inverte um bit, o outro detecta a mudança com '
           'um XOR.\nRecursos do bloco inteiro (com engine e core): ' + res_str('u_geo3d'))

    top, bot = 50.8, 381.0
    xa, xb, xc = 20.32, 281.94, 579.12
    S.rect(xa, top, xb - 5.08, bot, kind='dash', width=0.3)
    S.text('DOMÍNIO clk (85,9 MHz): lado do barramento', xa + 2.54, top + 5.08, size=2.0, thick=0.3)
    S.rect(xb, top, xc - 5.08, bot, kind='dash', width=0.3)
    S.text('DOMÍNIO clk_eng (42,95 MHz): engine', xb + 2.54, top + 5.08, size=2.0, thick=0.3)
    S.rect(xc, top, 820.42, bot, kind='dash', width=0.3)
    S.text('DOMÍNIO clk: comandos de volta para o V9968', xc + 2.54, top + 5.08, size=2.0, thick=0.3)

    # ================================================================ clk side, column A
    x0 = xa + 7.62
    gx = x0 + 40.64
    c = Cur(top + 12.7)
    section(S, '1. Endereço e aceite do acesso', x0, c.take(12.7))
    y = c.take(15.24, before=2.54)
    g = S.add(AND(2), gx, y)
    nets(S, g, {'A': 'bus_address[2]', 'B': 'bus_address[0]'})
    alias(S, g, 'Y', 'hit', P('hit'))
    note(S, "RTL: hit = (bus_address == 3'd5) || (bus_address == 3'd7) = bus_address[2] · bus_address[0]\n"
            '(offset 6 = 8Eh fica livre para a MegaRAM).  bus_address[2:0] vem do msx_slot:', x0, c.take(10.16))
    S.label(P('bus_address'), (x0 + 2.54, c.take(10.16)), 'R', kind='hier')
    y = c.take(22.86, before=7.62)
    g = S.add(AND(4, inv=(3,)), gx, y)
    nets(S, g, {'A': H(P('bus_valid')), 'B': H(P('bus_ioreq')), 'C': 'hit', 'D': 'busy_f', 'Y': 'acc'})
    n = S.add(NOT, gx + 71.12, y)
    S.net(n, 'A', 'busy_f')
    S.net(n, 'Y', P('bus_ready'), kind='hier', shape='output')
    note(S, 'acc = bus_valid & bus_ioreq & hit & ~busy_f : um acesso aceito (1 clock).\n'
            'bus_ready = ~busy_f : o msx_slot espera enquanto o engine não respondeu.', x0, c.take(12.7))

    section(S, '2. Captura do acesso (quando acc = 1)', x0, c.take(12.7, before=5.08))
    y = c.take(27.94, before=5.08)
    g = S.add(AND(3), gx, y)
    nets(S, g, {'A': 'bus_address[2]', 'B': 'bus_address[1]', 'C': 'bus_address[0]'})
    f = ff_after(S, g['Y'], DE, en='acc', label='is_data_port*', value='a_sel')
    nets(S, f, {'C': H('clk'), 'Q': 'a_sel'})
    y = c.take(25.4)
    f = S.add(DE, gx + 30.48, y, value='a_wr')
    nets(S, f, {'D': H(P('bus_write')), 'EN': 'acc', 'C': H('clk'), 'Q': 'a_wr'})
    y = c.take(25.4)
    f = S.add(DE, gx + 30.48, y, value='a_data[7:0]  (8 FF)')
    nets(S, f, {'D': H(P('bus_wdata')), 'EN': 'acc', 'C': H('clk'), 'Q': 'a_data[7:0]'})
    y = c.take(30.48)
    n = S.add(NOT, gx, y)
    S.net(n, 'A', P('bus_write'), kind='hier')
    f = ff_after(S, n['Y'], DER, en='acc', value='is_rd')
    nets(S, f, {'C': H('clk'), 'Q': 'is_rd'})
    low(S, f, 'R', P('reset_n'), hier=True)

    # ================================================================ clk side, column B
    x1 = xa + 132.08
    gx1 = x1 + 30.48
    c = Cur(top + 12.7)
    section(S, '3. Pedido (req_t) e espera pela resposta (ack)', x1, c.take(12.7))
    y = c.take(30.48, before=2.54)
    g = S.add(XOR(), gx1, y)
    nets(S, g, {'A': 'req_t', 'B': 'acc'})
    f = ff_after(S, g['Y'], DR, value='req_t')
    nets(S, f, {'C': H('clk'), 'Q': 'req_t'})
    low(S, f, 'R', P('reset_n'), hier=True)
    note(S, 'req_t inverte a cada acesso aceito (pedido para o engine).', x1, c.take(10.16))
    y = c.take(30.48, before=7.62)
    a = S.add(AND(2, inv=(1,)), gx1, y)
    nets(S, a, {'A': 'busy_f', 'B': 'ack_pend*'})
    o = right_of(S, a['Y'], OR(2), 'B', 7.62)
    S.net(o, 'A', 'acc')
    f = ff_after(S, o['Y'], DR, value='busy_f')
    nets(S, f, {'C': H('clk'), 'Q': 'busy_f'})
    low(S, f, 'R', P('reset_n'), hier=True)
    note(S, 'busy_f = acc | (busy_f & ~ack_pend*): ocupado do aceite até a resposta.', x1, c.take(10.16))
    y = c.take(27.94, before=7.62)
    f = S.add(DR, gx1, y, value='ack_s1')
    nets(S, f, {'D': 'ack_t', 'C': H('clk'), 'Q': 'ack_s1'})
    low(S, f, 'R', P('reset_n'), hier=True)
    g = S.add(XOR(), gx1 + 50.8, y - 5.08)
    nets(S, g, {'A': 'ack_s1', 'B': 'ack_seen', 'Y': 'ack_pend*'})
    y = c.take(15.24)
    a = S.add(AND(2), gx1 + 50.8, y)
    nets(S, a, {'A': 'busy_f', 'B': 'ack_pend*', 'Y': 'done*'})
    y = c.take(30.48, before=5.08)
    f = S.add(DER, gx1, y, value='ack_seen')
    nets(S, f, {'D': 'ack_s1', 'EN': 'done*', 'C': H('clk'), 'Q': 'ack_seen'})
    low(S, f, 'R', P('reset_n'), hier=True)
    y = c.take(30.48, before=5.08)
    a = S.add(AND(2), gx1, y)
    nets(S, a, {'A': 'done*', 'B': 'is_rd'})
    f = ff_after(S, a['Y'], DR, value='bus_rdata_en')
    S.net(f, 'C', 'clk', kind='hier')
    low(S, f, 'R', P('reset_n'), hier=True)
    S.net(f, 'Q', P('bus_rdata_en'), kind='hier', shape='output')
    note(S, 'ack_s1 sincroniza o toggle de resposta do engine (ack_t).\n'
            'done* = a resposta chegou: libera o barramento (busy_f = 0) e,\n'
            'se era leitura, pulsa bus_rdata_en com bus_rdata = eng_dout.', x1, c.take(15.24))

    # ================================================================ clk_eng side
    xe = xb + 7.62
    ge = xe + 30.48
    c = Cur(top + 12.7)
    section(S, '4. Recepção do pedido (clk_eng)', xe, c.take(12.7))
    y = c.take(22.86, before=2.54)
    n = S.add(NOT, ge, y)
    S.net(n, 'A', P('reset_n'), kind='hier')
    f = ff_after(S, n['Y'], D, value='rst_e  (v2.0)')
    nets(S, f, {'C': H('clk_eng'), 'Q': 'rst_e'})
    note(S, 'v2.0: o engine sai do reset por um flip-flop próprio\n(antes reset_n ia direto para centenas de '
            'registradores do engine).', xe, c.take(12.7))
    y = c.take(30.48, before=7.62)
    g = S.add(XOR(), ge, y)
    nets(S, g, {'A': 'req_t', 'B': 'req_seen', 'Y': 'req_new*'})
    f = S.add(DERH, ge + 60.96, y + 5.08, value='req_seen')
    nets(S, f, {'D': 'req_t', 'EN': 'req_new*', 'C': H('clk_eng'), 'Q': 'req_seen'})
    low(S, f, 'R', 'rst_e')
    for nm, inv in (('e_wr', ()), ('e_rd', (1,))):
        y = c.take(27.94)
        a = S.add(AND(2, inv=inv), ge, y)
        nets(S, a, {'A': 'req_new*', 'B': 'a_wr'})
        f = ff_after(S, a['Y'], DRH, value=nm)
        nets(S, f, {'C': H('clk_eng'), 'Q': nm})
        low(S, f, 'R', 'rst_e')
    y = c.take(27.94)
    o = S.add(OR(2), ge, y)
    nets(S, o, {'A': 'e_wr', 'B': 'e_rd'})
    x = right_of(S, o['Y'], XOR(), 'B', 7.62)
    S.net(x, 'A', 'ack_r')
    f = ff_after(S, x['Y'], DRH, value='ack_r')
    S.net(f, 'C', 'clk_eng', kind='hier')
    low(S, f, 'R', 'rst_e')
    S.net(f, 'Q', 'ack_t')
    note(S, 'e_wr / e_rd: um pulso de clk_eng por acesso.  ack_r inverte 1 clock depois\n'
            '(eng_dout já válido) e volta ao domínio clk como ack_t.', xe, c.take(12.7))
    y = c.take(25.4, before=5.08)
    f = S.add(DRH, ge, y, value='ce_s')
    nets(S, f, {'D': H(P('cmd_ce')), 'C': H('clk_eng'), 'Q': 'ce_s'})
    low(S, f, 'R', 'rst_e')
    note(S, 'cmd_ce (S#2 CE do V9968) é um nível: basta amostrar (clocks relacionados).', xe, c.take(10.16))

    section(S, '5. Pedido de escrita de comando (clk_eng)', xe, c.take(12.7, before=5.08))
    y = c.take(30.48, before=2.54)
    g = S.add(XOR(), ge, y)
    nets(S, g, {'A': 'cmd_t', 'B': 'e_cmd_wr'})
    f = ff_after(S, g['Y'], DRH, value='cmd_t')
    nets(S, f, {'C': H('clk_eng'), 'Q': 'cmd_t'})
    low(S, f, 'R', 'rst_e')
    y = c.take(25.4)
    f = S.add(DE, ge + 22.86, y, value='cmd_num_e[5:0]')
    nets(S, f, {'D': 'e_cmd_num[5:0]', 'EN': 'e_cmd_wr', 'C': H('clk_eng'), 'Q': 'cmd_num_e[5:0]'})
    y = c.take(25.4)
    f = S.add(DE, ge + 22.86, y, value='cmd_data_e[7:0]')
    nets(S, f, {'D': 'e_cmd_data[7:0]', 'EN': 'e_cmd_wr', 'C': H('clk_eng'), 'Q': 'cmd_data_e[7:0]'})

    # engine sheet
    e = MODS['geo3d_engine']
    left = [(p.label, 'input') for p in e.ports if p.dir == 'input']
    right = [(p.label, 'output') for p in e.ports if p.dir != 'input']
    ex, ey = xe + 152.4, top + 50.8
    section(S, 'u_engine (geo3d_engine): página própria', ex - 2.54, ey - 10.16)
    ss = S.subsheet(engine_sheet, ex, ey, left, right, name='u_engine')
    nets(S, ss, {'clk': 'clk_eng', 'rst': 'rst_e', 'sel': 'a_sel', 'wr_stb': 'e_wr', 'rd_stb': 'e_rd',
                 'din[7:0]': 'a_data[7:0]', 'cmd_ce': 'ce_s', 'dout[7:0]': 'eng_dout[7:0]',
                 'cmd_wr': 'e_cmd_wr', 'cmd_num[5:0]': 'e_cmd_num[5:0]', 'cmd_data[7:0]': 'e_cmd_data[7:0]',
                 'run_busy': 'run_busy'})
    S.text(res_str('u_geo3d/u_engine'), ex, ey + ss.h + 5.08, size=1.27)
    y = ey + ss.h + 17.78
    S.wire((ex, y), (ex + 30.48, y))
    S.label('eng_dout[7:0]', (ex, y), 'R')
    S.label(P('bus_rdata'), (ex + 30.48, y), 'R', kind='hier', shape='output')
    note(S, 'bus_rdata = eng_dout (sem registrador:\nsó é lido quando bus_rdata_en = 1)', ex, y + 5.08)
    y += 15.24
    S.wire((ex, y), (ex + 30.48, y))
    S.label('run_busy', (ex, y), 'R')
    S.label(P('run_busy'), (ex + 30.48, y), 'R', kind='hier', shape='output')
    note(S, 'run_busy: o engine está executando um RUN (status bit 0)', ex, y + 5.08)

    # ================================================================ back to clk
    xk = xc + 7.62
    gk = xk + 27.94
    c = Cur(top + 12.7)
    section(S, '6. O comando volta ao domínio clk', xk, c.take(12.7))
    y = c.take(30.48, before=2.54)
    g = S.add(XOR(), gk, y)
    nets(S, g, {'A': 'cmd_t', 'B': 'cmd_seen', 'Y': 'cmd_new*'})
    f = S.add(DER, gk + 63.5, y + 5.08, value='cmd_seen')
    nets(S, f, {'D': 'cmd_t', 'EN': 'cmd_new*', 'C': H('clk'), 'Q': 'cmd_seen'})
    low(S, f, 'R', P('reset_n'), hier=True)
    y = c.take(27.94)
    f = S.add(DR, gk + 10.16, y, value='cmd_wr')
    nets(S, f, {'D': 'cmd_new*', 'C': H('clk')})
    low(S, f, 'R', P('reset_n'), hier=True)
    S.net(f, 'Q', P('cmd_wr'), kind='hier', shape='output')
    for nm, src in (('cmd_num', 'cmd_num_e[5:0]'), ('cmd_data', 'cmd_data_e[7:0]')):
        y = c.take(25.4)
        f = S.add(DE, gk + 10.16, y, value=P(nm))
        nets(S, f, {'D': src, 'EN': 'cmd_new*', 'C': H('clk')})
        S.net(f, 'Q', P(nm), kind='hier', shape='output')
    note(S, 'Cada escrita do engine em R#32..R#50 vira UM pulso cmd_wr de clk\n(85,9 MHz) com cmd_num e cmd_data '
            'estáveis. O V9968 trata como escrita\nnos registradores de comando (portas ext_cmd_* do vdp).',
         xk, c.take(15.24))
    S.textbox(LEGEND, xk, c.take(55, before=5.08), 160.02, 26.67, size=1.27)
    return S
