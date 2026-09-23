// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Alex Moncks
`timescale 1ns/1ps
// End-to-end testbench: geo3d_bus (engine at 42.95 MHz, bus at 85.9 MHz)
// drives HRA!'s original V9968 command engine (vdp_command.v, unmodified),
// which draws into a 256 KB VRAM model. The stimulus is the captured traffic
// of the real Z80 program (run_demo_z80.py): geo3d port writes, the Z80's own
// VDP commands (page clear), texture upload, page flips.
//
// Ops: W sel b | R | F n | V reg val | C | X addr b | D y0
// Output (+frames=...): for every D, 212 rows x 128 bytes of VRAM starting at
// y0 (the page just shown), one hex byte per line.
// The command engine runs in V9968 mode (extended commands, 256 KB) with
// high-speed commands, as the Z80 program selects (R#21 = 0, R#20 = 1).
module tb_system;
    reg clk = 0, clk_eng = 0, rst = 1;
    always #5.82 clk = ~clk;
    always @(posedge clk) clk_eng <= ~clk_eng;

    // ---------------------------------------------------------------- geo3d
    reg  [2:0] bus_address = 0;
    reg        bus_ioreq = 0, bus_write = 0, bus_valid = 0;
    reg  [7:0] bus_wdata = 0;
    wire       hit, bus_ready, bus_rdata_en, run_busy;
    wire [7:0] bus_rdata;
    wire       g_wr;
    wire [5:0] g_num;
    wire [7:0] g_data;
    wire       ce;

    geo3d_bus u_geo (
        .clk(clk), .clk_eng(clk_eng), .reset_n(~rst),
        .bus_address(bus_address), .bus_ioreq(bus_ioreq), .bus_write(bus_write),
        .bus_valid(bus_valid), .bus_wdata(bus_wdata), .hit(hit), .bus_ready(bus_ready),
        .bus_rdata(bus_rdata), .bus_rdata_en(bus_rdata_en),
        .cmd_wr(g_wr), .cmd_num(g_num), .cmd_data(g_data), .cmd_ce(ce), .run_busy(run_busy)
    );

    // ------------------------------------------- V9968 command engine (HRA!)
    reg        z_wr = 0;              // Z80's own command-register writes
    reg  [5:0] z_num = 0;
    reg  [7:0] z_data = 0;
    wire [17:0] a;
    wire        v, w;
    wire [31:0] wd;
    wire [3:0]  wm;
    reg  [31:0] rd;
    reg         rde;

    // same hookup as the patched vdp.v: external port OR-ed in
    vdp_command u_cmd (
        .reset_n(~rst), .clk(clk),
        .command_vram_address(a), .command_vram_valid(v), .command_vram_ready(1'b1),
        .command_vram_write(w), .command_vram_wdata(wd), .command_vram_wdata_mask(wm),
        .command_vram_rdata(rd), .command_vram_rdata_en(rde),
        .register_write(z_wr | g_wr), .register_num(g_wr ? g_num : z_num),
        .register_data(g_wr ? g_data : z_data),
        .clear_border_detect(1'b0), .read_color(1'b0),
        .status_command_execute(ce), .status_border_detect(), .status_transfer_ready(),
        .status_color(), .status_border_position(),
        .screen_mode(10'b0000001000), .vram_interleave(1'b0), .reg_text_back_color(8'd0),
        .reg_command_enable(1'b1), .reg_command_high_speed_mode(1'b1),
        .reg_ext_command_mode(1'b1), .reg_vram256k_mode(1'b1),
        .vram_access_mask(), .intr_command_end()
    );

    reg [31:0] mem [0:65535];
    integer i;
    always @(posedge clk) begin
        rde <= 1'b0;
        if (v) begin
            if (w) begin
                if (!wm[0]) mem[a[17:2]][ 7: 0] <= wd[ 7: 0];
                if (!wm[1]) mem[a[17:2]][15: 8] <= wd[15: 8];
                if (!wm[2]) mem[a[17:2]][23:16] <= wd[23:16];
                if (!wm[3]) mem[a[17:2]][31:24] <= wd[31:24];
            end else begin
                rd  <= mem[a[17:2]];
                rde <= 1'b1;
            end
        end
    end

    integer z80_vs_geo;               // Z80 command write while geo3d is drawing
    always @(posedge clk) if (z_wr && run_busy) z80_vs_geo = z80_vs_geo + 1;

    // ---------------------------------------------------------------- bus tasks
    task io_wr(input s, input [7:0] d);
        begin
            @(negedge clk);
            bus_address = s ? 3'd7 : 3'd5; bus_write = 1; bus_ioreq = 1;
            bus_wdata = d; bus_valid = 1;
            @(posedge clk); while (!bus_ready) @(posedge clk);
            @(negedge clk); bus_valid = 0; bus_ioreq = 0;
            repeat (3) @(negedge clk);
        end
    endtask

    task io_rd(input s, output [7:0] d);
        begin
            @(negedge clk);
            bus_address = s ? 3'd7 : 3'd5; bus_write = 0; bus_ioreq = 1; bus_valid = 1;
            @(posedge clk); while (!bus_ready) @(posedge clk);
            @(negedge clk); bus_valid = 0;
            @(posedge clk); while (!bus_rdata_en) @(posedge clk);
            d = bus_rdata;
            @(negedge clk); bus_ioreq = 0;
            repeat (3) @(negedge clk);
        end
    endtask

    // ---------------------------------------------------------------- replay
    reg [8*80-1:0] fstim, fframes;
    integer fi, fo, r, p1, p2, frames, dumps, y, xb, t0, maxrun;
    reg [8*4-1:0] op;
    reg [7:0] st;

    initial begin
        if (!$value$plusargs("stim=%s", fstim))     fstim   = "texdemo_sys.txt";
        if (!$value$plusargs("frames=%s", fframes)) fframes = "texdemo_frames.hex";
        for (i = 0; i < 65536; i = i + 1) mem[i] = 32'd0;
        z80_vs_geo = 0; frames = 0; dumps = 0; maxrun = 0;
        repeat (6) @(negedge clk);
        rst = 0;
        repeat (6) @(negedge clk);
        fi = $fopen(fstim, "r");
        fo = $fopen(fframes, "w");
        while (!$feof(fi)) begin
            r = $fscanf(fi, "%s", op);
            if (r == 1) begin
                case (op[7:0])
                    "W": begin r = $fscanf(fi, "%d %h", p1, p2); io_wr(p1[0], p2[7:0]); end
                    "R": begin
                        t0 = $time;
                        st = 8'h01;
                        while (st[0]) io_rd(0, st);
                        if (($time - t0) > maxrun) maxrun = $time - t0;
                    end
                    "F": begin r = $fscanf(fi, "%d", p1); frames = frames + 1; end
                    "V": begin
                        r = $fscanf(fi, "%d %h", p1, p2);
                        @(negedge clk); z_wr = 1; z_num = p1; z_data = p2;
                        @(negedge clk); z_wr = 0;
                    end
                    "C": begin
                        repeat (4) @(negedge clk);
                        while (ce) @(negedge clk);
                    end
                    "X": begin
                        r = $fscanf(fi, "%h %h", p1, p2);
                        mem[p1[17:2]][8 * p1[1:0] +: 8] = p2[7:0];
                    end
                    "D": begin
                        r = $fscanf(fi, "%d", p1);
                        for (y = 0; y < 212; y = y + 1)
                            for (xb = 0; xb < 128; xb = xb + 1)
                                $fwrite(fo, "%02x\n", mem[((p1 + y) * 128 + xb) >> 2][8 * (xb & 3) +: 8]);
                        dumps = dumps + 1;
                    end
                    default: ;
                endcase
            end
        end
        $fclose(fo);
        $display("Sistema: %0d quadros, %0d páginas capturadas, maior RUN = %0d us, escritas do Z80 durante RUN = %0d",
                 frames, dumps, maxrun / 1000, z80_vs_geo);
        $finish;
    end
endmodule
