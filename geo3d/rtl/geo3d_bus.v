// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Alex Moncks
// ============================================================================
// geo3d_bus.v
// Adapter between the cartridge's msx_slot bus (bus_valid / bus_ready /
// bus_rdata_en, 8-port block at 88h or 98h) and geo3d_engine.
//
// The slot decodes an 8-port block; the V9968 uses offsets 0-4. geo3d takes:
//   offset PORT_INDEX (default 5): index write / status read   (8Dh or 9Dh)
//   offset PORT_DATA  (default 7): data read / write           (8Fh or 9Fh)
// Offset 6 (8Eh) is left alone on purpose: 8Eh is used by the MegaRAM.
// ============================================================================
module geo3d_bus #(
    parameter [2:0] PORT_INDEX = 3'd5,
    parameter [2:0] PORT_DATA  = 3'd7
)(
    input  wire       clk,
    input  wire       reset_n,
    // from msx_slot
    input  wire [2:0] bus_address,
    input  wire       bus_ioreq,
    input  wire       bus_write,
    input  wire       bus_valid,
    input  wire [7:0] bus_wdata,
    output wire       hit,            // this access belongs to geo3d
    output wire       bus_ready,
    output wire [7:0] bus_rdata,
    output reg        bus_rdata_en,
    // to the V9968 command engine
    output wire       cmd_wr,
    output wire [5:0] cmd_num,
    output wire [7:0] cmd_data,
    input  wire       cmd_ce,
    output wire       run_busy
);
    assign hit       = (bus_address == PORT_INDEX) || (bus_address == PORT_DATA);
    assign bus_ready = 1'b1;

    wire acc    = bus_valid & bus_ioreq & hit;
    wire wr_stb = acc &  bus_write;
    wire rd_stb = acc & ~bus_write;
    wire sel    = (bus_address == PORT_DATA);

    wire [7:0] dout;

    geo3d_engine u_engine (
        .clk(clk), .rst(~reset_n),
        .sel(sel), .wr_stb(wr_stb), .rd_stb(rd_stb), .din(bus_wdata), .dout(dout),
        .cmd_wr(cmd_wr), .cmd_num(cmd_num), .cmd_data(cmd_data), .cmd_ce(cmd_ce),
        .run_busy(run_busy)
    );

    // engine registers dout on rd_stb; data is valid one clock later
    always @(posedge clk) begin
        if (!reset_n) bus_rdata_en <= 1'b0;
        else          bus_rdata_en <= rd_stb;
    end
    assign bus_rdata = dout;
endmodule
