# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Top sheet (tangnano20k_vdp_cartridge) and the clock/reset sheet."""
import ksch as K
from common import (MODS, LEGEND, AND, OR, MUX, header, section, nets, res_str, pnr_summary, netname, Blk,
                    column)

D = K.dff()


def ports_of(mname):
    m = MODS[mname]
    left = [(p.label, 'input') for p in m.ports if p.dir == 'input']
    right = [(p.label, 'bidirectional' if p.dir == 'inout' else 'output') for p in m.ports if p.dir != 'input']
    return left, right


def sheet_conn(S, ss, mname, conns, override=None):
    m = MODS[mname]
    override = override or {}
    for p in m.ports:
        e = override.get(p.name, conns.get(p.name))
        if e is None or e == '':
            S.nc(ss[p.label])
        else:
            S.net(ss, p.label, e if e.endswith('*') else netname('tangnano20k_vdp_cartridge', e))


# ====================================================================== clock / reset
def page_clock_reset():
    S = K.Sheet('clock_reset.kicad_sch', 'Clocks (PLLs) e sincronizadores de reset', paper='A3',
                comments=['Fonte: fpga/V9968_Cartridge_TangNano20K_geo3d/src/tangnano20k_vdp_cartridge.v (HRA!)',
                          'Folha de desenho: estes elementos estão no próprio módulo de topo'])
    header(S, 'Clocks e reset (parte do módulo de topo tangnano20k_vdp_cartridge)',
           'Tudo deriva de clk14m (14,31818 MHz, pino 80).  O oscilador de 27 MHz da placa (clk, pino 4) não é usado.\n'
           'slot_reset_n (RESET do MSX) passa por 3 flip-flops em cada domínio antes de virar reset síncrono.')

    section(S, '1. PLLs e divisor (primitivas Gowin rPLL / CLKDIV)', 20.32, 45.72)
    b1 = K.box('Gowin_rPLL', ['clkin'], ['clkout', 'lock'], title='rPLL')
    p1 = S.add(b1, 76.2, 66.04, value='u_pll : Gowin_rPLL', value_size=1.5)
    S.net(p1, 'clkin', 'clk14m', kind='hier')
    S.net(p1, 'clkout', 'clk215m')
    S.nc(p1['lock'])
    S.text('×15 → 214,77 MHz (serializador TMDS do HDMI)', 60.96, 76.2, size=1.27)

    b2 = K.box('Gowin_rPLL2', ['clkin'], ['clkout', 'clkoutp', 'clkoutd', 'lock'], title='rPLL')
    p2 = S.add(b2, 76.2, 101.6, value='u_pll2 : Gowin_rPLL2', value_size=1.5)
    S.net(p2, 'clkin', 'clk14m', kind='hier')
    S.net(p2, 'clkout', 'clk85m')
    S.net(p2, 'clkoutp', 'clk85m_n')
    S.net(p2, 'clkoutd', 'clk42g')
    S.net(p2, 'lock', 'pll_lock85')
    S.text('×6 → clk85m 85,91 MHz (VDP, barramento, SDRAM)\n'
           'clkoutp: 85,91 MHz defasado 180° (clock da SDRAM)\n'
           'clkoutd: ÷2 da própria PLL → clk42g 42,95 MHz (engine geo3d)', 60.96, 116.84, size=1.27)

    b3 = K.box('Gowin_CLKDIV', ['hclkin', 'resetn'], ['clkout'], title='÷2')
    p3 = S.add(b3, 76.2, 147.32, value='u_clkdiv : Gowin_CLKDIV', value_size=1.5)
    S.net(p3, 'hclkin', 'clk85m')
    S.net(p3, 'resetn', 'pll_lock85')
    S.net(p3, 'clkout', 'clk42m')
    S.text('clk42m 42,95 MHz (pixel clock do DVI).  O geo3d usa clk42g e não\n'
           'clk42m: o CLKDIV fica 3-4 ns defasado das saídas da PLL no modelo de timing.', 60.96, 157.48, size=1.27)

    # outputs column
    section(S, 'Saídas desta folha', 157.48, 45.72)
    y = 58.42
    for n in ('clk215m', 'clk85m', 'clk85m_n', 'clk42g', 'clk42m', 'reset_n', 'reset_n2', 'reset_n3'):
        S.wire((160.02, y), (190.5, y))
        S.label(n, (160.02, y), 'R')
        S.label(n, (190.5, y), 'R', kind='hier', shape='output')
        y += 5.08

    section(S, '2. Sincronizadores de reset (3 flip-flops, syn_preserve)', 20.32, 185.42)
    y = 203.2
    for clk, out, regs, use in (('clk85m', 'reset_n', 'ff_reset_n0..2', 'msx_slot, SDRAM, LED, debugger'),
                                ('clk42m', 'reset_n2', 'ff_reset2_n0..2', 'DVI (HDMI)'),
                                ('clk85m', 'reset_n3', 'ff_reset3_n0..2', 'geo3d e V9968')):
        f1 = S.add(D, 55.88, y, value=regs.replace('..2', '').replace('n0', 'n0') + '', show_value=False)
        S.net(f1, 'D', 'slot_reset_n', kind='hier')
        S.net(f1, 'C', clk)
        f2 = K.snap(f1['Q'][0] + 12.7 + 6.35)
        p = f1
        for i in range(2):
            n = S.add(D, p['Q'][0] + 12.7 + 6.35, y, show_value=False)
            S.wire(p['Q'], n['D'])
            S.net(n, 'C', clk)
            p = n
        S.net(p, 'Q', out)
        S.text(f'{regs} em {clk} → {out}: {use}', 124.46, y - 1.27, size=1.27)
        y += 25.4
    S.textbox(LEGEND, 228.6, 185.42, 165.1, 26.67)
    return S


# ====================================================================== top
def page_top(s_clk, s_msx, s_geo, s_vdp):
    top = MODS['tangnano20k_vdp_cartridge']
    inst = {i.name: i for i in top.instances}
    S = K.Sheet(K.PROJECT + '.kicad_sch', 'V9968 + geo3d v2.0 no Tang Nano 20K: visão geral', paper='A2',
                comments=['Fonte: fpga/V9968_Cartridge_TangNano20K_geo3d/src/tangnano20k_vdp_cartridge.v (HRA!)',
                          'geo3d v2.0: geo3d/rtl (Alex Moncks). Recursos: build Gowin v2_locr2 (2026-10-13)',
                          'Gerado de RTL por geo3d/docs/kicad/gen/gen.py'])
    header(S, 'Cartucho V9968 + coprocessador geo3d v2.0 no Tang Nano 20K (GW2AR-LV18QN88C8/I7): módulo de topo',
           'Desenho lógico gerado a partir do RTL.  Cada folha hierárquica abre a página do módulo; blocos amarelos '
           'são folhas, blocos brancos são IPs folha (mostrados só pelas portas).\n'
           'Nível de detalhe: msx_slot e geo3d_bus em portas lógicas; geo3d_engine e geo3d_core em blocos RTL; '
           'V9968 por módulos (as ~11 mil LUTs não cabem em portas).')
    y0 = 50.8

    def conn(name, value, pins, note=None):
        """A board connector: pins on the right, each labelled with its net.
        pins: (net, pin text, etype); the pin text goes after a '/'."""
        outs = [(f'{n}/{t}' if t else n, et) for n, t, et in pins]
        return Blk(name, [], outs, value, nets={o[0]: n for o, (n, _, _) in zip(outs, pins)}, note=note)

    # ---------------------------------------------------------------- column 1: FPGA pins
    section(S, 'Pinos do FPGA', 20.32, y0)
    c1 = [conn('CONN_MSX_SLOT', 'Slot do cartucho MSX (via transceptores)', [
        ('slot_reset_n', 'PIN86', 'output'), ('slot_iorq_n', 'PIN18', 'output'), ('slot_rd_n', 'PIN15', 'output'),
        ('slot_wr_n', 'PIN16', 'output'), ('slot_a[7:0]', 'PIN17,49,48,41,42,76,31,30', 'output'),
        ('slot_d[7:0]', 'PIN73,74,75,85,77,27,28,29', 'bidirectional'), ('slot_wait', 'PIN72', 'input'),
        ('slot_intr', 'PIN71', 'input'), ('slot_data_dir', 'PIN19', 'input'), ('oe_n', 'PIN20', 'input')]),
          conn('CONN_CLOCKS', 'Osciladores', [('clk14m', 'PIN80_14.318MHz', 'output'),
                                              ('clk', 'PIN04_27MHz', 'output')],
               note='clk (27 MHz) não é usado'),
          conn('CONN_USER', 'Chaves, botões e LED RGB', [
              ('dipsw[1:0]', 'PIN52,53', 'output'), ('button[1:0]', 'PIN87,88', 'output'),
              ('ws2812_led', 'PIN79', 'input')],
               note='dipsw[0]: bloco de I/O 88h (0) ou 98h (1)\ndipsw[1]: força o modo rápido do VDP'),
          conn('CONN_SDRAM', 'SDRAM 64 Mbit ×32 (dentro do GW2AR-18)', [
              (n, '', 'bidirectional' if n.startswith('IO') else 'input') for n in
              ('O_sdram_clk', 'O_sdram_cke', 'O_sdram_cs_n', 'O_sdram_ras_n', 'O_sdram_cas_n', 'O_sdram_wen_n',
               'IO_sdram_dq[31:0]', 'O_sdram_addr[10:0]', 'O_sdram_ba[1:0]', 'O_sdram_dqm[3:0]')]),
          conn('CONN_HDMI', 'Conector HDMI (sinal DVI)', [
              ('tmds_clk_p', 'PIN33/34', 'input'), ('tmds_clk_n', 'par_dif', 'input'),
              ('tmds_d_p[2:0]', 'PIN39/40,37/38,35/36', 'input'), ('tmds_d_n[2:0]', 'par_dif', 'input')])]
    c1[1].nets['clk/PIN04_27MHz'] = None
    r1, b1, placed = column(S, 20.32, y0 + 2.54, c1, gap=10.16)
    S.nc(placed[1]['clk/PIN04_27MHz'])
    section(S, 'Atribuições diretas no topo', 20.32, b1)
    y = b1 + 10.16
    for a, b in (('w_sdram_init_busy', 'slot_wait'), ("1'b0", 'oe_n')):
        S.wire((22.86, y), (60.96, y))
        S.label(a, (22.86, y), 'R')
        S.label(b, (60.96, y), 'R')
        y += 5.08
    S.text("slot_wait = w_sdram_init_busy (o Z80 espera a SDRAM inicializar)\noe_n = 0 (transceptores sempre "
           "habilitados)\nw_sdram_address[22:18] = 0 (o VDP usa 256 KB)", 20.32, y + 2.54, size=1.27)

    # ---------------------------------------------------------------- column 2: clock/reset, slot, glue
    x2 = r1 + 7.62
    clk_out = ('clk215m', 'clk85m', 'clk85m_n', 'clk42g', 'clk42m', 'reset_n', 'reset_n2', 'reset_n3')
    ss = S.subsheet(s_clk, x2 + 22.86, y0 + 5.08, [('clk14m', 'input'), ('slot_reset_n', 'input')],
                    [(n, 'output') for n in clk_out], name='clock_reset')
    for n in ('clk14m', 'slot_reset_n') + clk_out:
        S.net(ss, n, n)
    S.text('PLLs, divisor e sincronizadores de reset', ss.x, ss.y + ss.h + 5.08, size=1.27)
    L, R = ports_of('msx_slot')
    ss = S.subsheet(s_msx, x2 + 27.94, ss.y + ss.h + 20.32, L, R, name='u_msx_slot')
    sheet_conn(S, ss, 'msx_slot', inst['u_msx_slot'].conns)
    S.text(res_str('u_msx_slot'), ss.x, ss.y + ss.h + 5.08, size=1.27)
    r2 = ss.x + ss.w + 30.48

    gx = x2 + 25.4
    gy = ss.y + ss.h + 22.86
    section(S, 'Cola do barramento local (portas)', x2, gy)
    a = S.add(AND(2, inv=(1,)), gx, gy + 15.24)
    nets(S, a, {'A': 'w_bus_valid', 'B': 'w_geo_hit', 'Y': 'w_bus_valid_vdp*'})
    S.text('o V9968 só vê o acesso se não for do geo3d', x2, gy + 24.13, size=1.27)
    m1 = S.add(MUX, gx, gy + 40.64)
    nets(S, m1, {'I1': 'w_bus_vdp_rdata[7:0]', 'I0': "8'hFF"})
    S.net_low(m1, 'S', 'w_bus_vdp_rdata_en', drop=5.08)
    m2 = S.add(MUX, m1['Y'][0] + 25.4 + 5.08, gy + 43.18)
    S.wire(m1['Y'], (m2['I0'][0] - 7.62, m1['Y'][1]), (m2['I0'][0] - 7.62, m2['I0'][1]), m2['I0'])
    S.net(m2, 'I1', 'w_geo_rdata[7:0]')
    S.net_low(m2, 'S', 'w_geo_rdata_en', drop=5.08)
    S.net(m2, 'Y', 'w_bus_rdata[7:0]')
    o = S.add(OR(2), gx, gy + 68.58)
    nets(S, o, {'A': 'w_bus_vdp_rdata_en', 'B': 'w_geo_rdata_en', 'Y': 'w_bus_rdata_en'})
    m3 = S.add(MUX, gx, gy + 88.9)
    nets(S, m3, {'I1': 'w_geo_ready', 'I0': 'w_bus_vdp_ready', 'Y': 'w_bus_ready'})
    S.net_low(m3, 'S', 'w_geo_hit', drop=5.08)
    S.text('Leitura: o geo3d tem prioridade, depois o VDP, senão FFh.\n'
           'ready vem do geo3d quando hit, senão do VDP.', x2, gy + 106.68, size=1.27)
    r2 = max(r2, m2['Y'][0] + 30.48)

    # ---------------------------------------------------------------- column 3: geo3d and V9968
    x3 = r2 + 7.62
    L, R = ports_of('geo3d_bus')
    ss = S.subsheet(s_geo, x3 + 30.48, y0 + 5.08, L, R, name='u_geo3d')
    sheet_conn(S, ss, 'geo3d_bus', inst['u_geo3d'].conns)
    S.text('geo3d v2.0 (bus + engine + core):\n' + res_str('u_geo3d'), ss.x, ss.y + ss.h + 5.08, size=1.27)
    r3 = ss.x + ss.w + 30.48
    L, R = ports_of('vdp')
    ss = S.subsheet(s_vdp, x3 + 30.48, ss.y + ss.h + 22.86, L, R, name='u_v9958')
    sheet_conn(S, ss, 'vdp', inst['u_v9958'].conns, override={'bus_valid': 'w_bus_valid_vdp*'})
    S.text('V9968 (clone do V9958 com extensões):\n' + res_str('u_v9958') +
           '\n(a instância se chama u_v9958 no RTL e u_v9968 na build v2_locr2)', ss.x, ss.y + ss.h + 5.08,
           size=1.27)
    r3 = max(r3, ss.x + ss.w + 35.56)
    b3 = ss.y + ss.h + 20.32

    rows = pnr_summary()
    txt = 'Ocupação do GW2AR-18 (place & route, build v2_locr2, geo3d v2.0)\n'
    for k, u, pct in rows:
        txt += f'  {k:<10} {u:>12}   {pct:>4}\n'
    txt += ('Por bloco (síntese):\n'
            f'  geo3d v2.0  {res_str("u_geo3d")}\n'
            f'  V9968       {res_str("u_v9958")}\n'
            f'  SDRAM       {res_str("u_sdram")}\n'
            f'  DVI         {res_str("u_dvi")}\n'
            f'  msx_slot    {res_str("u_msx_slot")}\n'
            'O CLS (89%) é o recurso mais apertado.')
    S.textbox(txt, x3, b3, 152.4, 48.26, size=1.27)

    # ---------------------------------------------------------------- column 4: leaf IPs
    x4 = r3 + 5.08
    section(S, 'IPs folha (só as portas)', x4, y0)
    ips = []
    for mname, iname, note in (('ip_sdram', 'u_sdram', 'FREQ = 85 909 080 Hz'), ('DVI_TX_Top', 'u_dvi', None),
                               ('ip_ws2812_led', 'u_led', None), ('ip_debugger', 'u_debugger', None)):
        mm = MODS[mname]
        ins = [p.label for p in mm.ports if p.dir == 'input']
        outs = [(p.label, 'bidirectional' if p.dir == 'inout' else 'output') for p in mm.ports if p.dir != 'input']
        nm = {p.label: netname('tangnano20k_vdp_cartridge', inst[iname].conns[p.name]) for p in mm.ports}
        ips.append(Blk(mname, ins, outs, f'{iname} : {mname}', nets=nm,
                       note=res_str(iname) + (f'\n{note}' if note else '')))
    r4, b4, _ = column(S, x4, y0 + 2.54, ips, gap=7.62)
    S.textbox(LEGEND, x4, b4, 152.4, 26.67)
    S.textbox('Páginas\n'
              '  1  Topo (esta)    2  clock_reset    3  msx_slot (portas)\n'
              '  4  geo3d_bus (portas, travessia de clock)    5  geo3d_engine (RTL)\n'
              '  6  geo3d_core (datapath)    7..13  V9968 (vdp) e submódulos\n'
              'No PDF, clique numa folha hierárquica para abrir a página.', x4, b4 + 30.48, 152.4, 17.78,
              size=1.27)
    return S

