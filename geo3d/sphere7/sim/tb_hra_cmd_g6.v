// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Alex Moncks
`timescale 1ns/1ps
// GRAPHIC6 (SCREEN 7) version of geo3d/sim/hra/tb_hra_cmd.v: HRA!'s original
// V9968 command engine (vdp_command.v and its cache vdp_command_cache.v,
// unmodified, wired as vdp.v wires them since his 88132d2), used as the
// golden reference for the commands the SCREEN 7 sphere demo uses (LRMM,
// LINE, HMMV, HMMM, PSET) with 512-pixel lines.
//
// VRAM: 256 KB, 32-bit words, byte lanes little-endian. The files (+init,
// +dump) hold the LOGICAL layout of SCREEN 7: byte = y*256 + x/2, even x in
// the high nibble. +ilv=1 runs the engine as vdp.v does in GRAPHIC6/7
// (vram_interleave = 1: physical address = {a17, a0, a16..a1}); the harness
// then stores every byte at its physical address, so the files stay logical
// and the result must not depend on +ilv (only the timing does: the cache
// lines hold other bytes).
//
// Stimulus file (+cmds=...), one operation per line:
//   W <reg> <hex>   write command register R#reg
//   E               wait until CE = 0 (command finished), log cycles
// Output: +dump=... final VRAM (256 KB) as hex bytes, +log=... cycles.
module tb_hra_cmd_g6;
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
    reg         ilv;

    wire [17:0] ca;
    wire        cv, cr, cw, crde, cfs, cfe;
    wire [7:0]  cwd, crd;

    vdp_command dut (
        .reset_n(reset_n), .clk(clk),
        .cache_vram_address(ca), .cache_vram_valid(cv), .cache_vram_ready(cr),
        .cache_vram_write(cw), .cache_vram_wdata(cwd), .cache_vram_rdata(crd),
        .cache_vram_rdata_en(crde), .cache_flush_start(cfs), .cache_flush_end(cfe),
        .register_write(rw), .register_num(rn), .register_data(rdat),
        .clear_border_detect(1'b0), .read_color(1'b0),
        .status_command_execute(ce), .status_border_detect(), .status_transfer_ready(),
        .status_color(), .status_border_position(),
        .screen_mode(10'b0000100000),         // GRAPHIC6 (SCREEN 7): w_mode index 5
        .vram_interleave(ilv), .reg_text_back_color(8'd0),
        .reg_command_enable(1'b1), .reg_command_high_speed_mode(hs),
        .reg_ext_command_mode(1'b1), .reg_vram256k_mode(1'b1),
        .vram_access_mask(), .intr_command_end()
    );

    vdp_command_cache u_cache (
        .reset_n(reset_n), .clk(clk), .start(1'b0),
        .cache_vram_address(ca), .cache_vram_valid(cv), .cache_vram_ready(cr),
        .cache_vram_write(cw), .cache_vram_wdata(cwd), .cache_vram_rdata(crd),
        .cache_vram_rdata_en(crde), .cache_flush_start(cfs), .cache_flush_end(cfe),
        .cpu_vram_address(18'd0), .cpu_vram_valid(1'b0), .cpu_vram_ready(),
        .cpu_vram_write(1'b0), .cpu_vram_wdata(8'd0), .cpu_vram_rdata(), .cpu_vram_rdata_en(),
        .command_vram_address(a), .command_vram_valid(v), .command_vram_ready(1'b1),
        .command_vram_write(w), .command_vram_wdata(wd), .command_vram_wdata_mask(wm),
        .command_vram_rdata(rd), .command_vram_rdata_en(rde)
    );

    reg [31:0] mem [0:65535];
    integer i, p;
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

    // logical byte address -> physical (vdp.v's interleave in GRAPHIC6/7)
    function integer phys;
        input integer la;
        begin
            phys = ilv ? ((la & 32'h20000) | ((la & 1) << 16) | ((la >> 1) & 32'hFFFF)) : la;
        end
    endfunction

    reg [8*80-1:0] fcmd, fdump, finit, flog;
    integer fi, fo, fl, r, reg_n, val, cyc;
    reg [8*4-1:0] op;
    reg [7:0] bytes [0:262143];

    initial begin
        if (!$value$plusargs("cmds=%s", fcmd))  fcmd  = "hra6_cmds.txt";
        if (!$value$plusargs("init=%s", finit)) finit = "hra6_init.hex";
        if (!$value$plusargs("dump=%s", fdump)) fdump = "hra6_dump.hex";
        if (!$value$plusargs("log=%s",  flog))  flog  = "hra6_log.txt";
        if (!$value$plusargs("hs=%d",   hs))    hs    = 1'b1;
        if (!$value$plusargs("ilv=%d",  ilv))   ilv   = 1'b0;
        for (i = 0; i < 65536; i = i + 1) mem[i] = 32'd0;
        $readmemh(finit, bytes);
        for (i = 0; i < 262144; i = i + 1)
            if (bytes[i] !== 8'hxx) begin
                p = phys(i);
                mem[p >> 2][8 * (p & 3) +: 8] = bytes[i];
            end
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
        for (i = 0; i < 262144; i = i + 1) begin
            p = phys(i);
            $fwrite(fo, "%02x\n", mem[p >> 2][8 * (p & 3) +: 8]);
        end
        $fclose(fo);
        $finish;
    end
endmodule
