// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Alex Moncks
`timescale 1ns/1ps
// Testbench: dirige o geo3d_z80if como um Z80 faria (OUT/IN em portas de 8 bits)
// e compara os resultados com o modelo de referência em Python.
module tb_geo3d;
    localparam NW = 26;
    localparam MAXV = 4096;

    reg        clk = 0;
    reg        rst = 1;
    reg        a0 = 0;
    reg        wr_stb = 0;
    reg        rd_stb = 0;
    reg  [7:0] din = 0;
    wire [7:0] dout;

    always #11.64 clk = ~clk;   // ~42,95 MHz

    geo3d_z80if dut (
        .clk(clk), .rst(rst), .a0(a0),
        .wr_stb(wr_stb), .rd_stb(rd_stb),
        .din(din), .dout(dout)
    );

    reg [15:0] vec [0:MAXV*NW-1];
    integer nvec, i, j, errors, cycles, maxcycles;
    reg [7:0] rb [0:6];
    reg [15:0] got_sx, got_sy, got_z;
    reg [7:0]  got_fl;
    reg [7:0]  st;

    task io_wr(input p, input [7:0] d);
        begin
            @(negedge clk); a0 = p; din = d; wr_stb = 1;
            @(negedge clk); wr_stb = 0;
            repeat (2) @(negedge clk);
        end
    endtask

    task io_rd(input p, output [7:0] d);
        begin
            @(negedge clk); a0 = p; rd_stb = 1;
            @(negedge clk); rd_stb = 0; d = dout;
            repeat (2) @(negedge clk);
        end
    endtask

    task wr16(input [15:0] w);
        begin
            io_wr(1, w[7:0]);
            io_wr(1, w[15:8]);
        end
    endtask

    initial begin
        for (i = 0; i < MAXV*NW; i = i + 1) vec[i] = 16'hxxxx;
        $readmemh("vectors.hex", vec);
        nvec = 0;
        while (nvec < MAXV && vec[nvec*NW] !== 16'hxxxx) nvec = nvec + 1;
        $display("Vetores carregados: %0d", nvec);

        errors = 0; maxcycles = 0;
        repeat (5) @(negedge clk);
        rst = 0;
        repeat (5) @(negedge clk);

        for (i = 0; i < nvec; i = i + 1) begin
            if (vec[i*NW] == 16'd0) begin
                // configuração completa a partir do índice 0
                io_wr(0, 8'h00);
                for (j = 1; j <= 18; j = j + 1) wr16(vec[i*NW + j]);
            end
            // vértice (6 bytes; o índice volta sozinho para 0x24 no fim)
            wr16(vec[i*NW + 19]);
            wr16(vec[i*NW + 20]);
            // último byte: dispara o cálculo; medimos a latência a partir daqui
            io_wr(1, vec[i*NW + 21][7:0]);
            @(negedge clk); a0 = 1; din = vec[i*NW + 21][15:8]; wr_stb = 1;
            @(negedge clk); wr_stb = 0;
            cycles = 1;
            st = 8'h08;
            while (st[3]) begin
                @(negedge clk); a0 = 0; rd_stb = 1;
                @(negedge clk); rd_stb = 0; st = dout;
                cycles = cycles + 2;
            end
            if (cycles > maxcycles) maxcycles = cycles;

            // lê os 7 bytes de resultado (ponteiro já em 0x30)
            for (j = 0; j < 7; j = j + 1) io_rd(1, rb[j]);
            got_sx = {rb[1], rb[0]};
            got_sy = {rb[3], rb[2]};
            got_z  = {rb[5], rb[4]};
            got_fl = rb[6];

            if (got_sx !== vec[i*NW+22] || got_sy !== vec[i*NW+23] ||
                got_z  !== vec[i*NW+24] || got_fl !== vec[i*NW+25][7:0]) begin
                errors = errors + 1;
                if (errors <= 10)
                    $display("ERRO vetor %0d: obtido sx=%h sy=%h z=%h fl=%h | esperado sx=%h sy=%h z=%h fl=%h",
                             i, got_sx, got_sy, got_z, got_fl,
                             vec[i*NW+22], vec[i*NW+23], vec[i*NW+24], vec[i*NW+25][7:0]);
            end
        end

        $display("Resultado: %0d vetores, %0d erros, latência máxima medida = %0d ciclos",
                 nvec, errors, maxcycles);
        if (errors == 0) $display("PASS");
        else             $display("FAIL");
        $finish;
    end
endmodule
