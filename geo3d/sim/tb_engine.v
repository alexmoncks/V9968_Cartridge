// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Alex Moncks
`timescale 1ns/1ps
// Testbench for geo3d_engine: replays the Z80 byte stream produced by
// gen_scenes.py and logs every command the engine sends to a behavioural
// model of the V9968 command engine. The log is compared by check_scenes.py.
module tb_engine;
    reg        clk = 0;
    reg        rst = 1;
    reg        sel = 0, wr_stb = 0, rd_stb = 0;
    reg  [7:0] din = 0;
    wire [7:0] dout;
    wire       cmd_wr;
    wire [5:0] cmd_num;
    wire [7:0] cmd_data;
    reg        cmd_ce = 0;
    wire       run_busy;

    always #5.82 clk = ~clk;   // ~85.9 MHz, same as the V9968 core clock

    geo3d_engine dut (
        .clk(clk), .rst(rst),
        .sel(sel), .wr_stb(wr_stb), .rd_stb(rd_stb), .din(din), .dout(dout),
        .cmd_wr(cmd_wr), .cmd_num(cmd_num), .cmd_data(cmd_data), .cmd_ce(cmd_ce),
        .run_busy(run_busy)
    );

    // ------------------------------------------------ V9968 command model
    reg [7:0] vreg [32:46];
    integer   ce_delay, ce_hold, violations, ncmd;
    integer   fo;
    reg       started;

    always @(posedge clk) begin
        if (rst) begin
            cmd_ce <= 0; ce_delay <= 0; ce_hold <= 0; started <= 0;
        end else begin
            if (cmd_wr) begin
                if (cmd_ce || started) violations = violations + 1;
                vreg[cmd_num] <= cmd_data;
                if (cmd_num == 6'd46) begin
                    started  <= 1;
                    ce_delay <= 2;            // ff_start, then ff_command_execute
                    ce_hold  <= 6 + (vreg[40] % 29) + (($random & 7));
                    ncmd = ncmd + 1;
                    $fwrite(fo, "L %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x\n",
                            vreg[36], vreg[37], vreg[38], vreg[39], vreg[40], vreg[41],
                            vreg[42], vreg[43], vreg[44], vreg[45], cmd_data);
                end
            end
            if (started) begin
                if (ce_delay > 1) ce_delay <= ce_delay - 1;
                else if (!cmd_ce) begin cmd_ce <= 1; end
                else if (ce_hold > 0) ce_hold <= ce_hold - 1;
                else begin cmd_ce <= 0; started <= 0; end
            end
        end
    end

    // ------------------------------------------------ Z80 bus tasks
    task io_wr(input s, input [7:0] d);
        begin
            @(negedge clk); sel = s; din = d; wr_stb = 1;
            @(negedge clk); wr_stb = 0;
            repeat (2) @(negedge clk);
        end
    endtask

    task io_rd(input s, output [7:0] d);
        begin
            @(negedge clk); sel = s; rd_stb = 1;
            @(negedge clk); rd_stb = 0; d = dout;
            repeat (2) @(negedge clk);
        end
    endtask

    // ------------------------------------------------ stimulus replay
    integer fi, r, a, b, frames, maxcyc, cyc;
    reg [8*4-1:0] op;
    reg [7:0] st, k0, k1, k2, k3, k4, k5;
    integer t_start;
    reg [8*64-1:0] stim_name, got_name;

    initial begin
        violations = 0; ncmd = 0; frames = 0; maxcyc = 0;
        if (!$value$plusargs("stim=%s", stim_name)) stim_name = "scenes_stim.txt";
        if (!$value$plusargs("got=%s",  got_name))  got_name  = "scenes_got.txt";
        fi = $fopen(stim_name, "r");
        fo = $fopen(got_name, "w");
        repeat (5) @(negedge clk);
        rst = 0;
        repeat (5) @(negedge clk);

        while (!$feof(fi)) begin
            r = $fscanf(fi, "%s", op);
            if (r == 1) begin
                if (op[7:0] == "W") begin
                    r = $fscanf(fi, "%d %h", a, b);
                    io_wr(a[0], b[7:0]);
                end else if (op[7:0] == "R") begin
                    cyc = 0;
                    st = 8'h01;
                    while (st[0]) begin
                        io_rd(0, st);
                        cyc = cyc + 4;
                    end
                    if (cyc > maxcyc) maxcyc = cyc;
                end else if (op[7:0] == "K") begin
                    io_wr(0, 8'h4A);
                    io_rd(1, k0); io_rd(1, k1); io_rd(1, k2);
                    io_rd(1, k3); io_rd(1, k4); io_rd(1, k5);
                    $fwrite(fo, "K %02x%02x %02x%02x %02x%02x\n", k1, k0, k3, k2, k5, k4);
                end else if (op[7:0] == "F") begin
                    r = $fscanf(fi, "%d", a);
                    $fwrite(fo, "F %0d\n", a);
                    frames = frames + 1;
                end
            end
        end
        $fclose(fo);
        $display("Quadros: %0d, comandos LINE emitidos: %0d, violacoes de CE: %0d, maior RUN: %0d ciclos",
                 frames, ncmd, violations, maxcyc);
        $finish;
    end
endmodule
