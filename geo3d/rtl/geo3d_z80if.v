// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Alex Moncks
// ============================================================================
// geo3d_z80if.v
// Interface de I/O de 8 bits (lado Z80) para o geo3d_core.
//
// Duas portas, no estilo do VDP:
//   porta 0 (a0=0) escrita : define o índice de bytes (0x00..0x29)
//   porta 0 (a0=0) leitura : status = FLAGS (bit3 = BUSY), sem efeitos colaterais
//   porta 1 (a0=1) escrita : grava byte no índice atual, índice auto-incrementa
//   porta 1 (a0=1) leitura : lê resultado, ponteiro de leitura auto-incrementa
//
// Palavras de 16 bits em little-endian (byte baixo primeiro). A escrita do
// byte alto confirma a palavra no núcleo.
//
// Mapa de bytes (escrita):
//   0x00-0x11 M00..M22   0x12-0x17 TX,TY,TZ
//   0x18 F  0x1A CX  0x1C CY  0x1E ZNEAR  0x20 W  0x22 H
//   0x24 VX 0x26 VY 0x28 VZ  (escrever 0x29 dispara o cálculo)
//
// Modo streaming: após escrever o byte 0x29, o índice volta sozinho para 0x24.
// O Z80 envia 6 bytes por vértice (OTIR) sem reposicionar o índice.
//
// Mapa de bytes (leitura), ponteiro reinicia em 0x30 a cada novo cálculo:
//   0x30-31 SX   0x32-33 SY   0x34-35 Z (câmera)   0x36 FLAGS
//   Após ler 0x36 o ponteiro volta a 0x30 (INIR de 7 bytes por vértice).
//
// wr_stb / rd_stb: pulsos de 1 ciclo já sincronizados ao clk
// (no cartucho real isso vem do módulo msx_slot).
// ============================================================================
module geo3d_z80if (
    input  wire       clk,
    input  wire       rst,
    input  wire       a0,
    input  wire       wr_stb,
    input  wire       rd_stb,
    input  wire [7:0] din,
    output reg  [7:0] dout
);

    reg  [5:0]  widx;
    reg  [5:0]  rptr;
    reg  [7:0]  lo_latch;

    reg         core_wr;
    reg  [4:0]  core_addr;
    reg  [15:0] core_data;

    wire [15:0] sx, sy, zout;
    wire [7:0]  flags;
    wire        busy, done_pulse;

    geo3d_core u_core (
        .clk        (clk),
        .rst        (rst),
        .wr_en      (core_wr),
        .wr_addr    (core_addr),
        .wr_data    (core_data),
        .sx         (sx),
        .sy         (sy),
        .zout       (zout),
        .flags      (flags),
        .busy       (busy),
        .done_pulse (done_pulse)
    );

    // leitura combinacional (registrada na saída)
    reg [7:0] rdata;
    always @(*) begin
        case (rptr)
            6'h30: rdata = sx[7:0];
            6'h31: rdata = sx[15:8];
            6'h32: rdata = sy[7:0];
            6'h33: rdata = sy[15:8];
            6'h34: rdata = zout[7:0];
            6'h35: rdata = zout[15:8];
            6'h36: rdata = flags;
            default: rdata = 8'hFF;
        endcase
    end

    always @(posedge clk) begin
        if (rst) begin
            widx     <= 6'h00;
            rptr     <= 6'h30;
            lo_latch <= 8'h00;
            core_wr  <= 1'b0;
            dout     <= 8'hFF;
        end else begin
            core_wr <= 1'b0;

            if (wr_stb) begin
                if (!a0) begin
                    widx <= din[5:0];
                    rptr <= din[5:0];
                end else begin
                    if (!widx[0]) begin
                        lo_latch <= din;
                    end else begin
                        core_wr   <= 1'b1;
                        core_addr <= widx[5:1];
                        core_data <= {din, lo_latch};
                    end
                    widx <= (widx == 6'h29) ? 6'h24 : widx + 6'd1;
                end
            end

            if (rd_stb) begin
                if (!a0) begin
                    dout <= flags;
                end else begin
                    dout <= rdata;
                    rptr <= (rptr == 6'h36) ? 6'h30 : rptr + 6'd1;
                end
            end

            // novo cálculo: ponteiro de leitura volta ao início dos resultados
            if (core_wr && core_addr == 5'd20)
                rptr <= 6'h30;
        end
    end

endmodule
