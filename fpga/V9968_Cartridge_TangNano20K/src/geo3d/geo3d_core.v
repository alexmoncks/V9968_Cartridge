// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Alex Moncks
// ============================================================================
// geo3d_core.v
// Coprocessador de geometria 3D para MSX (acoplável ao V9968)
//
// Função: transforma um vértice (VX,VY,VZ) pela matriz 3x3 + translação e
//         projeta em perspectiva para coordenadas de tela (SX,SY).
//
// Formatos numéricos:
//   M00..M22 : signed 16 bits, Q2.14  (rotação/escala, faixa ±2.0)
//   TX,TY,TZ : signed 16 bits, inteiro (unidades de mundo)
//   VX,VY,VZ : signed 16 bits, inteiro
//   F        : unsigned 16 bits (distância focal em pixels)
//   CX,CY    : signed 16 bits (centro da tela)
//   ZNEAR    : signed 16 bits (plano de recorte próximo, > 0)
//   W,H      : signed 16 bits (largura/altura da tela, para outcodes)
//
// Equações:
//   Xc = sat18( (M00*VX + M01*VY + M02*VZ) >>> 14 ) + TX )   (idem Yc, Zc)
//   SX = sat16( CX + sinal(Xc) * min(|Xc|*F / Zc, 32767) )
//   SY = sat16( CY - sinal(Yc) * min(|Yc|*F / Zc, 32767) )
//
// Latência: ~44 ciclos por vértice (3 MAC + 2 x divisão de 16 passos)
//
// Mapa de registradores (endereço de palavra de 16 bits):
//   0..8  M00 M01 M02 M10 M11 M12 M20 M21 M22
//   9..11 TX TY TZ
//   12 F   13 CX   14 CY   15 ZNEAR   16 W   17 H
//   18 VX  19 VY   20 VZ  (escrita em VZ dispara o cálculo)
//
// FLAGS:
//   bit0 NEAR  (vértice atrás do plano próximo, SX/SY inválidos)
//   bit1 OVFX  (projeção X saturada)
//   bit2 OVFY  (projeção Y saturada)
//   bit3 BUSY
//   bit4 SX<0  bit5 SX>=W  bit6 SY<0  bit7 SY>=H   (outcodes de recorte)
// ============================================================================
module geo3d_core (
    input  wire        clk,
    input  wire        rst,
    input  wire        wr_en,
    input  wire [4:0]  wr_addr,
    input  wire [15:0] wr_data,
    output reg  [15:0] sx,
    output reg  [15:0] sy,
    output reg  [15:0] zout,
    output wire [7:0]  flags,
    output wire        busy,
    output reg         done_pulse
);

    // ---------------------------------------------------------------- registros
    reg signed [15:0] m   [0:8];
    reg signed [15:0] t   [0:2];
    reg signed [15:0] v   [0:2];
    reg        [15:0] f;
    reg signed [15:0] cx, cy, znear, w, h;

    integer i;

    // ---------------------------------------------------------------- estados
    localparam S_IDLE = 4'd0,
               S_MAC  = 4'd1,
               S_XF   = 4'd2,
               S_CHK  = 4'd3,
               S_MUL  = 4'd4,
               S_OVF  = 4'd5,
               S_DIV  = 4'd6,
               S_ACC  = 4'd7,
               S_OUT  = 4'd8;

    reg [3:0]  state;
    reg [1:0]  col;
    reg        axis;          // 0 = X, 1 = Y
    reg        start;

    reg signed [33:0] acc0, acc1, acc2;
    reg signed [17:0] xc, yc, zc;
    reg        [32:0] num;
    reg        [16:0] rem;
    reg        [15:0] low;
    reg        [15:0] q;
    reg        [3:0]  cnt;
    reg               f_near, f_ovfx, f_ovfy;
    reg        [3:0]  outc;

    assign busy  = (state != S_IDLE) | start;
    assign flags = {outc, busy, f_ovfy, f_ovfx, f_near};

    // ------------------------------------------------------------ utilitários
    function signed [17:0] sat18;
        input signed [20:0] x;
        begin
            if (x > 21'sd131071)       sat18 = 18'sd131071;
            else if (x < -21'sd131071) sat18 = -18'sd131071;   // simétrico: |x| cabe em 17 bits
            else                       sat18 = x[17:0];
        end
    endfunction

    function signed [15:0] sat16;
        input signed [17:0] x;
        begin
            if (x > 18'sd32767)        sat16 = 16'sd32767;
            else if (x < -18'sd32768)  sat16 = -16'sd32768;
            else                       sat16 = x[15:0];
        end
    endfunction

    // ------------------------------------------- multiplicadores da etapa MAC
    // Três MACs em paralelo (uma por linha da matriz), percorrendo as colunas.
    wire signed [15:0] vcol = v[col];
    wire signed [31:0] p0 = m[{2'd0, col}]        * vcol;
    wire signed [31:0] p1 = m[4'd3 + {2'd0, col}] * vcol;
    wire signed [31:0] p2 = m[4'd6 + {2'd0, col}] * vcol;

    // ------------------------------------------- multiplicador da projeção
    wire signed [17:0] coord  = axis ? yc : xc;
    wire        [16:0] acoord = coord[17] ? (~coord[16:0] + 17'd1) : coord[16:0];
    wire        [32:0] prod   = acoord * f;

    // ------------------------------------------- divisão (um passo por ciclo)
    wire [17:0] rem2  = {rem, low[15]};
    wire        ge    = (rem2 >= {1'b0, zc[16:0]});
    wire [17:0] diff  = rem2 - {1'b0, zc[16:0]};

    // ------------------------------------------- valores com sinal
    wire signed [16:0] sq     = coord[17] ? -$signed({1'b0, q}) : $signed({1'b0, q});
    wire signed [20:0] xs0    = (acc0 >>> 14) + t[0];
    wire signed [20:0] xs1    = (acc1 >>> 14) + t[1];
    wire signed [20:0] xs2    = (acc2 >>> 14) + t[2];

    always @(posedge clk) begin
        if (rst) begin
            state      <= S_IDLE;
            start      <= 1'b0;
            done_pulse <= 1'b0;
            f_near     <= 1'b0;
            f_ovfx     <= 1'b0;
            f_ovfy     <= 1'b0;
            outc       <= 4'd0;
            sx         <= 16'd0;
            sy         <= 16'd0;
            zout       <= 16'd0;
            for (i = 0; i < 9; i = i + 1) m[i] <= 16'sd0;
            for (i = 0; i < 3; i = i + 1) begin t[i] <= 16'sd0; v[i] <= 16'sd0; end
            f <= 16'd256; cx <= 16'sd128; cy <= 16'sd106;
            znear <= 16'sd16; w <= 16'sd256; h <= 16'sd212;
        end else begin
            done_pulse <= 1'b0;
            start      <= 1'b0;

            // ------------------------------------------- escrita de registros
            if (wr_en) begin
                case (wr_addr)
                    5'd0,5'd1,5'd2,5'd3,5'd4,5'd5,5'd6,5'd7,5'd8:
                        m[wr_addr[3:0]] <= wr_data;
                    5'd9:  t[0]  <= wr_data;
                    5'd10: t[1]  <= wr_data;
                    5'd11: t[2]  <= wr_data;
                    5'd12: f     <= wr_data;
                    5'd13: cx    <= wr_data;
                    5'd14: cy    <= wr_data;
                    5'd15: znear <= wr_data;
                    5'd16: w     <= wr_data;
                    5'd17: h     <= wr_data;
                    5'd18: v[0]  <= wr_data;
                    5'd19: v[1]  <= wr_data;
                    5'd20: begin v[2] <= wr_data; start <= 1'b1; end
                    default: ;
                endcase
            end

            // ------------------------------------------- máquina de estados
            case (state)
                S_IDLE: begin
                    if (start) begin
                        acc0  <= 34'sd0;
                        acc1  <= 34'sd0;
                        acc2  <= 34'sd0;
                        col   <= 2'd0;
                        state <= S_MAC;
                    end
                end

                S_MAC: begin
                    acc0 <= acc0 + p0;
                    acc1 <= acc1 + p1;
                    acc2 <= acc2 + p2;
                    if (col == 2'd2) state <= S_XF;
                    col <= col + 2'd1;
                end

                S_XF: begin
                    xc    <= sat18(xs0);
                    yc    <= sat18(xs1);
                    zc    <= sat18(xs2);
                    state <= S_CHK;
                end

                S_CHK: begin
                    zout   <= sat16(zc);
                    f_ovfx <= 1'b0;
                    f_ovfy <= 1'b0;
                    if ((zc < $signed({{2{znear[15]}}, znear})) || (zc <= 18'sd0)) begin
                        f_near <= 1'b1;
                        sx     <= 16'd0;
                        sy     <= 16'd0;
                        outc   <= 4'd0;
                        state  <= S_IDLE;
                        done_pulse <= 1'b1;
                    end else begin
                        f_near <= 1'b0;
                        axis   <= 1'b0;
                        state  <= S_MUL;
                    end
                end

                S_MUL: begin
                    num   <= prod;
                    state <= S_OVF;
                end

                S_OVF: begin
                    if (num >= {zc[16:0], 15'd0}) begin
                        q <= 16'd32767;
                        if (axis) f_ovfy <= 1'b1; else f_ovfx <= 1'b1;
                        state <= S_ACC;
                    end else begin
                        rem   <= num[32:16];
                        low   <= num[15:0];
                        q     <= 16'd0;
                        cnt   <= 4'd15;
                        state <= S_DIV;
                    end
                end

                S_DIV: begin
                    if (ge) begin
                        rem <= diff[16:0];
                        q   <= {q[14:0], 1'b1};
                    end else begin
                        rem <= rem2[16:0];
                        q   <= {q[14:0], 1'b0};
                    end
                    low <= {low[14:0], 1'b0};
                    cnt <= cnt - 4'd1;
                    if (cnt == 4'd0) state <= S_ACC;
                end

                S_ACC: begin
                    if (!axis) begin
                        sx    <= sat16({{2{cx[15]}}, cx} + {sq[16], sq});
                        axis  <= 1'b1;
                        state <= S_MUL;
                    end else begin
                        sy    <= sat16({{2{cy[15]}}, cy} - {sq[16], sq});
                        state <= S_OUT;
                    end
                end

                S_OUT: begin
                    outc[0] <= $signed(sx) < 0;
                    outc[1] <= $signed(sx) >= w;
                    outc[2] <= $signed(sy) < 0;
                    outc[3] <= $signed(sy) >= h;
                    done_pulse <= 1'b1;
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
