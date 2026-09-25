// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Alex Moncks
`timescale 1ns/1ps
// Harness around HRA!'s original V9968 command engine (vdp_command.v, with its
// internal cache), used as the golden reference for LINE and LRMM.
//
// VRAM: 256 KB, 32-bit words, byte lanes little-endian (as vdp_command_cache
// expects). SCREEN 5 (GRAPHIC4) layout: byte = y*128 + x/2, even x in the
// high nibble.
//
// Stimulus file (+cmds=...), one operation per line:
//   W <reg> <hex>   write command register R#reg
//   E               wait until CE = 0 (command finished), log cycles
// Output: +dump=... final VRAM (256 KB) as hex bytes, +log=... cycles.
// +v256=1 (default) is the real V9968 mode: R#21 V58 = 0 enables both the
// extended commands and 256 KB addressing.
module tb_hra_cmd;
    reg clk = 0;
    reg reset_n = 0;
    always #5.82 clk = ~clk;                  // 85.9 MHz, the cartridge VDP clock

    wire [17:0] a;
    wire        v, w;
    wire [31:0] wd;
    wire [3:0]  wm;
    reg  [31:0] rd;
    reg         rde;

    reg         rw = 0;
    reg  [5:0]  rn = 0;
    reg  [7:0]  rdat = 0;
    wire        ce;
    reg         hs;
    reg         v256;

    vdp_command dut (
        .reset_n(reset_n), .clk(clk),
        .command_vram_address(a), .command_vram_valid(v), .command_vram_ready(1'b1),
        .command_vram_write(w), .command_vram_wdata(wd), .command_vram_wdata_mask(wm),
        .command_vram_rdata(rd), .command_vram_rdata_en(rde),
        .register_write(rw), .register_num(rn), .register_data(rdat),
        .clear_border_detect(1'b0), .read_color(1'b0),
        .status_command_execute(ce), .status_border_detect(), .status_transfer_ready(),
        .status_color(), .status_border_position(),
        .screen_mode(10'b0000001000),         // GRAPHIC4 (SCREEN 5)
        .vram_interleave(1'b0), .reg_text_back_color(8'd0),
        .reg_command_enable(1'b1), .reg_command_high_speed_mode(hs),
        .reg_ext_command_mode(1'b1), .reg_vram256k_mode(v256),
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

    reg [8*80-1:0] fcmd, fdump, finit, flog;
    integer fi, fo, fl, r, reg_n, val, cyc;
    reg [8*4-1:0] op;
    reg [7:0] bytes [0:131071];

    initial begin
        if (!$value$plusargs("cmds=%s", fcmd))  fcmd  = "hra_cmds.txt";
        if (!$value$plusargs("init=%s", finit)) finit = "hra_init.hex";
        if (!$value$plusargs("dump=%s", fdump)) fdump = "hra_dump.hex";
        if (!$value$plusargs("log=%s",  flog))  flog  = "hra_log.txt";
        if (!$value$plusargs("hs=%d",   hs))    hs    = 1'b1;
        if (!$value$plusargs("v256=%d", v256))  v256  = 1'b1;   // V9968 mode: ECOM and 256 KB go together
        for (i = 0; i < 65536; i = i + 1) mem[i] = 32'd0;
        $readmemh(finit, bytes);
        for (i = 0; i < 131072; i = i + 1)
            if (bytes[i] !== 8'hxx) mem[i >> 2][8 * (i & 3) +: 8] = bytes[i];
        repeat (4) @(negedge clk);
        reset_n = 1;
        repeat (4) @(negedge clk);

        fi = $fopen(fcmd, "r");
        fl = $fopen(flog, "w");
        while (!$feof(fi)) begin
            r = $fscanf(fi, "%s", op);
            if (r == 1) begin
                if (op[7:0] == "W") begin
                    r = $fscanf(fi, "%d %h", reg_n, val);
                    @(negedge clk); rw = 1; rn = reg_n; rdat = val;
                    @(negedge clk); rw = 0;
                end else if (op[7:0] == "E") begin
                    cyc = 0;
                    @(negedge clk);
                    while (!ce && cyc < 8) begin @(negedge clk); cyc = cyc + 1; end
                    while (ce) begin @(negedge clk); cyc = cyc + 1; end
                    $fwrite(fl, "C %0d\n", cyc);
                end
            end
        end
        $fclose(fl);
        fo = $fopen(fdump, "w");
        for (i = 0; i < 262144; i = i + 1)
            $fwrite(fo, "%02x\n", mem[i >> 2][8 * (i & 3) +: 8]);
        $fclose(fo);
        $finish;
    end
endmodule
