# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""RTL block pages: geo3d_engine and geo3d_core (geo3d v2.0)."""
import ksch as K
from common import (MODS, LEGEND, AND, OR, MUX, header, section, nets, res_str, Blk, column, port_col)

DE = K.dff(en=True)
D = K.dff()


def ram(prefix, width, name, what, bsram):
    w = f'[{width - 1}:0]'
    return Blk(f'RAM_{name}', ['clk', f'{prefix}we', f'{prefix}wa[9:0]', f'{prefix}wd{w}', f'{prefix}ra[9:0]'],
               [f'{prefix}rd{w}'], f'{name}: {what}  (1024 × {width}, ≈{bsram} BSRAM)', title=f'1024×{width}',
               title_size=1.5, nets={'clk': 'clk'})


# ====================================================================== engine
def page_engine(core_sheet):
    S = K.Sheet('geo3d_engine.kicad_sch', 'geo3d_engine: arquitetura RTL (v2.0)', paper='A1',
                comments=['Fonte: geo3d/rtl/geo3d_engine.v (geo3d v2.0, MIT), parametro AW = 10'])
    header(S, 'geo3d_engine (u_engine): renderizador que comanda o V9968 sozinho, a 42,95 MHz (clk_eng)',
           'O Z80 envia o modelo uma vez (vértices, arestas, faces, texturas) e depois só matriz + RUN por quadro.  '
           'O engine transforma os vértices (geo3d_core), recorta arestas ou\npreenche faces e escreve os registradores '
           'de comando R#32..R#50 do V9968 sempre que o VDP está livre (CE = 0).  '
           'Recursos: ' + res_str('u_geo3d/u_engine') + '\n'
           'Nível de desenho: blocos RTL. Os nomes com * agrupam vários registradores (lista na caixa "Grupos").')

    y0 = 50.8
    yp = port_col(S, 20.32, y0, [
        ('clk', 'clk', 'input'), ('rst', 'rst', 'input'), ('sel', 'sel', 'input'), ('wr_stb', 'wr_stb', 'input'),
        ('rd_stb', 'rd_stb', 'input'), ('din[7:0]', 'din[7:0]', 'input'), ('cmd_ce', 'cmd_ce', 'input'),
        ('dout[7:0]', 'dout[7:0]', 'output'), ('cmd_wr', 'cmd_wr', 'output'),
        ('cmd_num[5:0]', 'cmd_num[5:0]', 'output'), ('cmd_data[7:0]', 'cmd_data[7:0]', 'output'),
        ('run_busy', 'ph_run', 'output')], title='Portas do módulo (run_busy = ph_run)')

    z80 = Blk('ENG_Z80WIN',
              ['clk', 'rst', 'sel', 'wr_stb', 'rd_stb', 'din[7:0]', 'status[7:0]', 'c_sx[15:0]', 'c_sy[15:0]',
               'c_z[15:0]', 'c_flags[7:0]', 'cnt_skip[15:0]', 'cnt_draw[15:0]', 'cnt_cull[15:0]'],
              ['dout[7:0]', 'z_core_wr', 'z_core_addr[4:0]', 'z_core_data[15:0]', 'vwe', 'vwa[9:0]', 'vwd[47:0]',
               'ewe', 'ewa[9:0]', 'ewd[19:0]', 'fwe', 'fwa[9:0]', 'fwd[95:0]', 'twe', 'twa[9:0]', 'twd[63:0]',
               'run_req', 'ctrl_face', 'ctrl_tex', 'ext', 'nvert[9:0]', 'nedge[9:0]', 'nface[9:0]', 'vfirst[9:0]',
               'efirst[9:0]', 'ffirst[9:0]', 'color[7:0]', 'lop[3:0]', 'ypage[10:0]', 'scr_w[15:0]', 'scr_h[15:0]',
               'scr_wm1[16:0]', 'scr_hm1[16:0]', 'texx[8:0]', 'texy[12:0]', 'tstride[7:0]', 'lreg[0:2]',
               'msh[0:8]'],
              'Janela de registradores do Z80', title='Z80',
              note='Porta SEL=0: escreve o índice (widx/rptr), lê status.\n'
                   'Porta SEL=1: dado. 0x00-0x29 vão ao core (palavras de 16 bits\n'
                   'via lo_latch); 0x40-0x6F configuram o engine; 0x50-0x53 são\n'
                   'streams (vbuf/ebuf/fbuf/tbuf juntam bytes até completar uma\n'
                   'entrada e escrevem na RAM; o ponteiro avança com ptr_inc).\n'
                   'rdata: mux de leitura (0x30-0x36 resultado do core, 0x49 ID 0x20).')
    r1, b1, _ = column(S, 20.32, yp + 7.62, [z80])

    # ---------------------------------------------------------------- core write mux + core
    x2 = r1 + 7.62
    section(S, 'Escrita no core: engine tem prioridade', x2, y0)
    o = S.add(OR(2), x2 + 38.1, y0 + 15.24)
    nets(S, o, {'A': 'e_core_wr', 'B': 'z_core_wr', 'Y': 'core_wr'})
    m = S.add(MUX, x2 + 38.1, y0 + 40.64)
    nets(S, m, {'I1': 'e_core_addr[4:0]', 'I0': 'z_core_addr[4:0]', 'Y': 'core_addr[4:0]'})
    S.net_low(m, 'S', 'e_core_wr', drop=5.08)
    m = S.add(MUX, x2 + 38.1, y0 + 71.12)
    nets(S, m, {'I1': 'e_core_data[15:0]', 'I0': 'z_core_data[15:0]', 'Y': 'core_data[15:0]'})
    S.net_low(m, 'S', 'e_core_wr', drop=5.08)
    c = MODS['geo3d_core']
    left = [(p.label, 'input') for p in c.ports if p.dir == 'input']
    right = [(p.label, 'output') for p in c.ports if p.dir != 'input']
    sy = y0 + 101.6
    section(S, 'u_core (geo3d_core): página própria', x2, sy - 7.62)
    ss = S.subsheet(core_sheet, x2 + 27.94, sy, left, right, name='u_core')
    nets(S, ss, {'clk': 'clk', 'rst': 'rst', 'wr_en': 'core_wr', 'wr_addr[4:0]': 'core_addr[4:0]',
                 'wr_data[15:0]': 'core_data[15:0]', 'sx[15:0]': 'c_sx[15:0]', 'sy[15:0]': 'c_sy[15:0]',
                 'zout[15:0]': 'c_z[15:0]', 'flags[7:0]': 'c_flags[7:0]', 'busy': 'c_busy',
                 'done_pulse': 'c_done'})
    S.text(res_str('u_geo3d/u_engine/u_core'), x2 + 27.94, sy + ss.h + 5.08, size=1.27)
    st = Blk('ENG_STATUS', ['c_flags[7:4]', 'c_busy', 'ph_edge', 'ph_xf', 'ph_run'], ['status[7:0]'],
             'status = {c_flags[7:4], c_busy, ph_edge, ph_xf, ph_run}', title='{ }')
    r2, b2, _ = column(S, x2, sy + ss.h + 15.24, [st])
    r2 = max(r2, ss.x + ss.w + 30.48)

    # ---------------------------------------------------------------- RAMs
    x3 = r2 + 5.08
    section(S, 'RAMs do modelo (BSRAM)', x3, y0)
    rams = [ram('v', 48, 'vmem', 'vértices VZ,VY,VX', 3), ram('e', 20, 'emem', 'arestas B,A', 2),
            ram('p', 51, 'pmem', 'projetados Z,FLAGS,SY,SX', 3),
            ram('f', 96, 'fmem', 'faces BASE,NZ,NY,NX,I3..I0', 6),
            ram('s', 31, 'smem', 'heap de faces visíveis KEY,FACE,LEVEL', 2),
            ram('t', 64, 'tmem', 'texturas V3,U3..V0,U0', 4)]
    r3, b3, _ = column(S, x3, y0 + 5.08, rams, gap=10.16)
    S.text('v2.0: 1024 entradas por RAM (v1.0.0: 256).\nBSRAM estimadas a 1024×18 bits cada (total ≈20;\n'
           'a síntese conta 21 no geo3d).\nvmem/emem/fmem/tmem: escritas pelo Z80.\npmem/smem: escritas pela FSM.',
           x3, b3, size=1.27)

    # ---------------------------------------------------------------- FSM
    x4 = r3 + 5.08
    fsm = Blk('ENG_FSM',
              ['clk', 'rst', 'run_req', 'ctrl_face', 'ctrl_tex', 'nvert[9:0]', 'nedge[9:0]', 'nface[9:0]',
               'vfirst[9:0]', 'efirst[9:0]', 'ffirst[9:0]', 'color[7:0]', 'lop[3:0]', 'ypage[10:0]', 'msh[0:8]',
               'lreg[0:2]', 'vrd[47:0]', 'erd[19:0]', 'prd[50:0]', 'frd[95:0]', 'srd[30:0]', 'trd[63:0]', 'c_done',
               'c_sx[15:0]', 'c_sy[15:0]', 'c_z[15:0]', 'c_flags[7:0]', 'clip_res*', 'mp[35:0]', 'face_res*',
               'heap_res*', 'div_res*', 'span_res*', 'tex_res*', 'hold_clr*'],
              ['vra[9:0]', 'era[9:0]', 'pra[9:0]', 'pwe', 'pwa[9:0]', 'pwd[50:0]', 'fra[9:0]', 'sra[9:0]', 'swe',
               'swa[9:0]', 'swd[30:0]', 'tra[9:0]', 'e_core_wr', 'e_core_addr[4:0]', 'e_core_data[15:0]', 'ph_run',
               'ph_xf', 'ph_edge', 'cnt_skip[15:0]', 'cnt_draw[15:0]', 'cnt_cull[15:0]', 'hold_valid', 'hlrmm',
               'hb[0:18]', 'clip_regs*', 'ma[17:0]', 'mb[17:0]', 'face_regs*', 'heap_regs*', 'div_regs*',
               'fill_regs*', 'tex_regs*'],
              'FSM produtora: st[6:0], 90 estados (lista abaixo)', title='FSM')
    section(S, 'Controle', x4, y0)
    r4, b4, _ = column(S, x4, y0 + 5.08, [fsm])

    # ---------------------------------------------------------------- datapaths
    x5 = r4 + 5.08
    section(S, 'Datapaths combinacionais (lidos pela FSM)', x5, y0)
    dps = [
        Blk('ENG_CLIP', ['clip_regs*', 'scr_w[15:0]', 'scr_h[15:0]', 'ypage[10:0]'], ['clip_res*'],
            'Clipper: outcodes, ponto médio, campos do LINE', title='clip',
            note='ocode() ×5 (4 comparadores cada), mid() = (a+b)>>>1,\nLINE: |dx|,|dy|, eixo maior, DY+YPAGE, ARG'),
        Blk('ENG_MUL', ['ma[17:0]', 'mb[17:0]'], ['mp[35:0]'], 'Multiplicador compartilhado 18×18 (DSP)', title='×',
            note='luz no modelo, sombra, área, interpolação de bordas'),
        Blk('ENG_FACE', ['face_regs*', 'mp[35:0]'], ['face_res*'], 'Sombreamento, área e chave Z', title='face',
            note='slevel = min(6, 7·max(0, LM·N)), área = cruzado de V1-V0, V2-V0,\nzkey = Z0+Z1+Z2+Z3'),
        Blk('ENG_HEAP', ['srd[30:0]', 'heap_regs*'], ['heap_res*'], 'Heap da ordem do pintor (v2.0)', title='heap',
            note='hbefore(): KEY maior primeiro, empate pelo menor nº de face;\npai = (hole-1)/2, filhos 2h+1 e 2h+2'),
        Blk('ENG_DIV', ['div_regs*'], ['div_res*'], 'Divisor radix-4: 2 bits de quociente por clock', title='÷',
            note='compara o resto com |den|, 2|den|, 3|den|; quociente com piso (v1.0.0: 1 bit)'),
        Blk('ENG_FILL', ['fill_regs*', 'scr_wm1[16:0]', 'scr_hm1[16:0]', 'ypage[10:0]'], ['span_res*'],
            'Preenchimento: arestas e span', title='fill',
            note='faixa de y da face recortada à tela, cruzamento das arestas,\nspan [scl, scr] recortado a [0, W-1]'),
        Blk('ENG_TEX', ['tex_regs*', 'texx[8:0]', 'texy[12:0]', 'tstride[7:0]'], ['tex_res*'],
            'Texturas: origem e passo do LRMM', title='tex',
            note='lsx = TEXX + u, lsy = TEXY + nível·TSTRIDE + v'),
    ]
    r5, b5, _ = column(S, x5, y0 + 5.08, dps, gap=7.62)

    # ---------------------------------------------------------------- issuer
    yi = max(b1, b4, b3) + 10.16
    xi = x3
    section(S, 'Emissor de comandos para o V9968 (registradores + portas)', xi, yi)
    yi += 22.86
    g0 = Blk('ENG_GZ', ['guard[2:0]'], ['guard_zero*'], 'guard == 0', title='=0')
    _, _, (gz,) = column(S, xi, yi - 12.7, [g0])
    a = S.add(AND(3, inv=(1,)), gz['guard_zero*'][0] + 50.8, yi + 7.62)
    nets(S, a, {'A': 'hold_valid', 'B': 'cmd_ce', 'C': 'guard_zero*', 'Y': 'issue_go*'})
    seq = Blk('ENG_SEQ', ['clk', 'rst', 'issue_go*', 'hlrmm'], ['isend', 'ik[4:0]', 'guard[2:0]', 'hold_clr*'],
              'Sequenciador: isend, contador ik, guard', title='seq',
              note='LINE: ik = 0..10 → R#36..R#46\nLRMM: ik = 0..18 → R#32..R#45, R#47..R#50, R#46\n'
                   'no fim: hold_valid = 0, guard = 4 (pausa antes do próximo)')
    num = Blk('ENG_NUM', ['ik[4:0]', 'hlrmm'], ['num_next*[5:0]'],
              'cmd_num = hlrmm ? lrmm_num(ik) : 36 + ik', title='#')
    mux = Blk('ENG_HBMUX', ['hb[0:18]', 'ik[4:0]'], ['data_next*[7:0]'], 'cmd_data = hb[ik] (mux 19:1)',
              title='MUX')
    rs, bs, _ = column(S, a['Y'][0] + 30.48, yi - 12.7, [seq, num, mux], gap=7.62)
    xf = rs + 10.16
    for i, (d, q, v) in enumerate((('isend', 'cmd_wr', 'cmd_wr'), ('num_next*[5:0]', 'cmd_num[5:0]', 'cmd_num[5:0]'),
                                   ('data_next*[7:0]', 'cmd_data[7:0]', 'cmd_data[7:0]'))):
        f = S.add(D, xf + 25.4, yi + 25.4 * i, value=v)
        nets(S, f, {'D': d, 'C': 'clk', 'Q': q})
    S.text('Os 11 (LINE) ou 19 (LRMM) bytes de um comando ficam em hb[] (hold_valid = 1) enquanto a FSM já\n'
           'prepara a próxima aresta/linha: pipeline de 1 nível.  O envio só começa com o VDP parado (cmd_ce = 0).',
           xi, max(bs, yi + 75) + 2.54, size=1.27)

    # ---------------------------------------------------------------- notes
    yn = max(b1, b2) + 7.62
    S.textbox('Grupos de sinais (nomes com *)\n'
              'clip_regs* = ax_,ay_,bx_,by_ (pontas), lox,loy,hix,hiy (par da busca/bisseção),\n'
              '             e1x,e1y,e2x,e2y (aresta recortada), px,py, it, which\n'
              'clip_res*  = ca, cb, cm, clo, chi (outcodes), mx, my (ponto médio),\n'
              '             lnx, lny, ldy_full, larg (campos do LINE)\n'
              'face_regs* = vx,vy,vz,vfl[0:3], a1, sacc, area_r, lacc, lm[0:2], li, lj, fcur\n'
              'face_res*  = slevel, shade, area, zkey, midx (elemento da matriz)\n'
              'heap_regs* = hole, hn, hent, hch ;  heap_res* = hparent, hc1, hc2, hsecond, hbest\n'
              'div_regs*  = den, dsgn, drem, dlow, dq, dcnt, dret ;  div_res* = dqb, drest, dfq\n'
              'fill_regs* = yrow, ymax, ek, eya,eyb,exa,exb, xl, xr, fymin_r, fymax_r, scl_r, scr_r\n'
              'span_res*  = ylo, yhi, fdy, fdx, fden, fymin, fymax, cymin, cymax, scl, scr, snx, sdy\n'
              'tex_regs*  = tcur, eua..evb, ul,ur,vl,vr, du,dv, us,vs, dlev, cx,cu,cv, qres\n'
              'tex_res*   = lsx, lsy, lnxp, lev_off, fdu, fdv\n'
              'hold_clr*  = fim do envio (o emissor zera hold_valid)', 20.32, yn, 170.18, 63.5, size=1.27)
    S.textbox('Fases de um RUN (estados da FSM)\n'
              'R_IDLE: espera run_req (CTRL bit0); copia ctrl_face/ctrl_tex; zera contadores.\n'
              'T_*  transformação: para cada vértice lê vmem, escreve VX,VY,VZ no core (end. 18..20),\n'
              '     espera c_done e grava {Z, FLAGS[2:0], SY, SX} em pmem.  ph_xf = 1.\n'
              'E_*  arestas: lê emem e os dois vértices em pmem; near/ovf → SKIPPED; fora da tela →\n'
              '     CULLED; senão recorta por bisseção (E_SRCH, E_BIS) e monta um LINE em hb[].\n'
              'L_*  faces: luz no espaço do modelo LM = transposta(R)·L (9 multiplicações).\n'
              'F_*, V_*  por face: lê fmem e os 4 vértices; back-face pela área; sombra; chave Z;\n'
              '     H_UP* insere no heap (smem).\n'
              'D_*  desenho: D_POP*/H_DN* tira a face mais distante; linha a linha (D_ROW, D_EDGE,\n'
              '     D_DIV) gera um LINE horizontal ou, com textura, um LRMM (D_T0..D_T5).\n'
              'R_DRAIN: espera o emissor esvaziar e baixa ph_run (fim do RUN).',
              20.32, yn + 66.04, 170.18, 55.88, size=1.27)
    S.textbox(LEGEND, 20.32, yn + 124.46, 170.18, 26.67)
    return S


# ====================================================================== core
def page_core():
    S = K.Sheet('geo3d_core.kicad_sch', 'geo3d_core: datapath da transformacao e projecao', paper='A1',
                comments=['Fonte: geo3d/rtl/geo3d_core.v (MIT)'])
    header(S, 'geo3d_core (u_core): matriz 3×3 + translação + perspectiva, ~44 clocks por vértice',
           'Xc = sat18((M00·VX + M01·VY + M02·VZ) >>> 14) + TX  (idem Yc, Zc);   '
           'SX = sat16(CX + sinal(Xc)·min(|Xc|·F / Zc, 32767));   SY = sat16(CY - sinal(Yc)·min(|Yc|·F / Zc, 32767))\n'
           'Formatos: M em Q2.14, T/V/CX/CY/ZNEAR/W/H inteiros de 16 bits, F sem sinal.  Escrever VZ (endereço 20) '
           'dispara o cálculo.  Recursos: ' + res_str('u_geo3d/u_engine/u_core'))
    y0 = 50.8
    yp = port_col(S, 20.32, y0, [
        ('clk', 'clk', 'input'), ('rst', 'rst', 'input'), ('wr_en', 'wr_en', 'input'),
        ('wr_addr[4:0]', 'wr_addr[4:0]', 'input'), ('wr_data[15:0]', 'wr_data[15:0]', 'input'),
        ('sx[15:0]', 'sx[15:0]', 'output'), ('sy[15:0]', 'sy[15:0]', 'output'), ('zout[15:0]', 'zout[15:0]', 'output'),
        ('flags[7:0]', 'flags[7:0]', 'output'), ('busy', 'busy', 'output'), ('done_pulse', 'done_pulse', 'output')])

    regs = Blk('CORE_REGS', ['clk', 'rst', 'wr_en', 'wr_addr[4:0]', 'wr_data[15:0]'],
               ['m[0:8]', 't[0:2]', 'v[0:2]', 'f[15:0]', 'cx[15:0]', 'cy[15:0]', 'znear[15:0]', 'w[15:0]', 'h[15:0]',
                'start'], 'Banco de registradores (decodificador de wr_addr)', title='regs',
               note='0..8 M00..M22, 9..11 TX..TZ, 12 F, 13 CX, 14 CY, 15 ZNEAR,\n'
                    '16 W, 17 H, 18..20 VX..VZ (VZ dispara start)\n'
                    'reset: F=256, CX=128, CY=106, ZNEAR=16, W=256, H=212')
    fsm = Blk('CORE_FSM', ['clk', 'rst', 'start', 'col[1:0]', 'cnt[3:0]', 'near*', 'ovf*', 'axis'],
              ['state[3:0]', 'st_mac*', 'st_xf*', 'st_chk*', 'st_mul*', 'st_ovf*', 'st_div*', 'st_acc*', 'st_out*',
               'busy', 'done_pulse'], 'Máquina de estados (9 estados)', title='FSM',
              note='busy = (state != S_IDLE) | start')
    r1, b1, _ = column(S, 20.32, yp + 7.62, [regs, fsm])

    # ---------------------------------------------------------------- MAC stage
    x2 = r1 + 5.08
    section(S, '1. MAC: 3 linhas em paralelo, 1 coluna por clock', x2, y0)
    sel = Blk('CORE_COL', ['m[0:8]', 'v[0:2]', 'col[1:0]'], ['m_row0*[15:0]', 'm_row1*[15:0]', 'm_row2*[15:0]',
                                                            'vcol[15:0]'],
              'Seleção da coluna col', title='MUX', note='m_rowK* = m[3K + col], vcol = v[col]')
    blks = [sel]
    for k in range(3):
        blks.append(Blk(f'CORE_MUL{k}', [f'm_row{k}*[15:0]', 'vcol[15:0]'], [f'p{k}[31:0]'],
                        f'Multiplicador 16×16 com sinal (p{k})', title='×'))
    r2, b2, _ = column(S, x2, y0 + 5.08, blks, gap=7.62)
    accs = []
    for k in range(3):
        accs.append(Blk(f'CORE_ACC{k}', ['clk', f'p{k}[31:0]', 'st_mac*', 'start'], [f'acc{k}[33:0]'],
                        f'Acumulador acc{k} (34 bits): zera em S_IDLE+start, soma em S_MAC', title='Σ'))
    r3, b3, _ = column(S, r2 + 10.16, y0 + 5.08, accs, gap=7.62)

    # ---------------------------------------------------------------- transform
    xs = []
    for k, (o, tn) in enumerate((('xc', 't0'), ('yc', 't1'), ('zc', 't2'))):
        xs.append(Blk(f'CORE_XF{k}', ['clk', f'acc{k}[33:0]', f't[{k}]', 'st_xf*'], [f'{o}[17:0]'],
                      f'{o} = sat18((acc{k} >>> 14) + T{"XYZ"[k]})  (reg. em S_XF)', title='sat'))
    near = Blk('CORE_NEAR', ['zc[17:0]', 'znear[15:0]'], ['near*'], 'zc < ZNEAR ou zc ≤ 0 → vértice atrás',
               title='<', note='near*: FLAGS.NEAR = 1, SX = SY = 0, termina (S_CHK)')
    zo = Blk('CORE_ZOUT', ['clk', 'zc[17:0]', 'st_chk*'], ['zout[15:0]'], 'zout = sat16(zc)  (S_CHK)', title='sat')
    r4, b4, _ = column(S, r3 + 10.16, y0 + 5.08, xs + [near, zo], gap=7.62)
    section(S, '2. Translação, saturação, plano próximo', r3 + 10.16, y0)

    # ---------------------------------------------------------------- projection
    yq = max(b2, b4) + 10.16
    section(S, '3. Projeção: |coord|·F / Zc por divisão de 16 passos, primeiro X depois Y (axis)', x2, yq)
    yq += 15.24
    m = S.add(MUX, x2 + 33.02, yq + 2.54)
    nets(S, m, {'I1': 'yc[17:0]', 'I0': 'xc[17:0]', 'Y': 'coord[17:0]'})
    S.net_low(m, 'S', 'axis', drop=5.08)
    proj = [Blk('CORE_ABS', ['coord[17:0]'], ['acoord[16:0]'], 'valor absoluto', title='|x|'),
            Blk('CORE_PMUL', ['acoord[16:0]', 'f[15:0]'], ['prod[32:0]'], 'Multiplicador 17×16', title='×'),
            Blk('CORE_NUM', ['clk', 'prod[32:0]', 'st_mul*'], ['num[32:0]'], 'num (S_MUL)', title='reg'),
            Blk('CORE_OVF', ['num[32:0]', 'zc[17:0]'], ['ovf*'], 'num ≥ zc·2^15 → satura em 32767', title='≥',
                note='ovf*: q = 32767 e FLAGS.OVFX/OVFY = 1 (S_OVF)')]
    rp, bp, _ = column(S, x2 + 50.8, yq - 7.62, proj, gap=7.62)
    div = [Blk('CORE_DCMP', ['rem[16:0]', 'low[15:0]', 'zc[17:0]'], ['ge', 'diff[17:0]', 'rem2[17:0]'],
               'rem2 = {rem, low[15]};  ge = rem2 ≥ zc;  diff = rem2 - zc', title='≥ −'),
           Blk('CORE_DREG', ['clk', 'ge', 'diff[17:0]', 'rem2[17:0]', 'num[32:0]', 'st_ovf*', 'st_div*'],
               ['rem[16:0]', 'low[15:0]', 'q[15:0]', 'cnt[3:0]'], 'Registradores do divisor (restaurador)',
               title='div', note='S_OVF: rem = num[32:16], low = num[15:0], q = 0, cnt = 15\n'
                                 'S_DIV: rem = ge ? diff : rem2;  q = {q, ge};  low <<= 1;  cnt--')]
    rd, bd, _ = column(S, rp + 10.16, yq - 7.62, div, gap=7.62)

    # ---------------------------------------------------------------- output
    xo = max(rd, r4) + 10.16
    section(S, '4. Sinal, centro, saturação e outcodes', xo, y0)
    outs = [Blk('CORE_SQ', ['q[15:0]', 'coord[17:0]'], ['sq[16:0]'], 'sq = coord < 0 ? -q : q', title='±'),
            Blk('CORE_SX', ['clk', 'cx[15:0]', 'sq[16:0]', 'st_acc*', 'axis'], ['sx[15:0]'],
                'sx = sat16(CX + sq)  (S_ACC, axis = 0)', title='+'),
            Blk('CORE_SY', ['clk', 'cy[15:0]', 'sq[16:0]', 'st_acc*', 'axis'], ['sy[15:0]'],
                'sy = sat16(CY - sq)  (S_ACC, axis = 1)', title='−'),
            Blk('CORE_OUTC', ['clk', 'sx[15:0]', 'sy[15:0]', 'w[15:0]', 'h[15:0]', 'st_out*'], ['outc[3:0]'],
                'outcodes: sx<0, sx≥W, sy<0, sy≥H  (S_OUT)', title='<≥'),
            Blk('CORE_FLAGS', ['clk', 'near*', 'ovf*', 'axis', 'st_chk*', 'st_ovf*'],
                ['f_near', 'f_ovfx', 'f_ovfy'], 'bits de flag', title='flags'),
            Blk('CORE_FCAT', ['outc[3:0]', 'busy', 'f_ovfy', 'f_ovfx', 'f_near'], ['flags[7:0]'],
                'flags = {outc, busy, f_ovfy, f_ovfx, f_near}', title='{ }')]
    ro, bo, _ = column(S, xo, y0 + 5.08, outs, gap=7.62)

    # ---------------------------------------------------------------- state diagram
    ys = max(bo, bp, bd) + 10.16
    section(S, 'Sequência de estados (state[3:0])', xo, ys)
    states = [('S_IDLE', 'espera start'), ('S_MAC', '×3 (col 0..2)'), ('S_XF', 'sat18 + T'), ('S_CHK', 'near?'),
              ('S_MUL', '|c|·F'), ('S_OVF', 'satura?'), ('S_DIV', '×16 passos'), ('S_ACC', 'sx ou sy'),
              ('S_OUT', 'outcodes')]
    bx, by, bw, bh = xo, ys + 7.62, 22.86, 10.16
    pos = []
    for i, (nm, what) in enumerate(states):
        cx = bx + (i % 5) * (bw + 10.16)
        cy = by + (i // 5) * (bh + 15.24)
        S.rect(cx, cy, cx + bw, cy + bh, kind='solid', width=0.25, fill='255 255 225 1')
        S.text(nm, cx + 1.27, cy + 4.0, size=1.5, thick=0.25)
        S.text(what, cx + 1.27, cy + 8.0, size=1.1)
        pos.append((cx, cy))
    for i in range(len(states) - 1):
        (ax, ay), (bxx, byy) = pos[i], pos[i + 1]
        if byy == ay:
            S.arrow((ax + bw, ay + bh / 2), (bxx, ay + bh / 2))
        else:
            S.gline((ax + bw / 2, ay + bh), (ax + bw / 2, ay + bh + 5.08), (bxx + bw / 2, ay + bh + 5.08))
            S.arrow((bxx + bw / 2, ay + bh + 5.08), (bxx + bw / 2, byy))
    S.text('S_CHK com near → S_IDLE.  S_OVF com ovf pula para S_ACC.\nS_ACC com axis = 0 volta a S_MUL '
           '(agora Y).  S_OUT → S_IDLE com done_pulse.\nTotal ≈ 44 clocks: 3 MAC + 2 × (MUL, OVF, 16 DIV, ACC) + '
           'XF, CHK, OUT.', bx, by + 2 * (bh + 15.24), size=1.27)
    S.textbox(LEGEND, 20.32, max(b1, ys) + 50.8, 165.1, 26.67)
    return S
