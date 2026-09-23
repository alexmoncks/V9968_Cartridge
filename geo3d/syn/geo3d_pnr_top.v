// P&R test harness: feeds geo3d_bus from a shift register and folds the outputs,
// so every gate of the engine survives optimisation while using few pins.
module geo3d_pnr_top (input clk, input rst_n, input sin, input ce_in, output sout, output busy);
    reg [15:0] sh;
    always @(posedge clk) sh <= {sh[14:0], sin};
    wire [7:0] rdata; wire rdata_en, hit, ready, cmd_wr; wire [5:0] cmd_num; wire [7:0] cmd_data;
    geo3d_bus u (
        .clk(clk), .reset_n(rst_n),
        .bus_address(sh[2:0]), .bus_ioreq(sh[3]), .bus_write(sh[4]), .bus_valid(sh[5]),
        .bus_wdata(sh[13:6]), .hit(hit), .bus_ready(ready), .bus_rdata(rdata), .bus_rdata_en(rdata_en),
        .cmd_wr(cmd_wr), .cmd_num(cmd_num), .cmd_data(cmd_data), .cmd_ce(ce_in), .run_busy(busy));
    reg r;
    always @(posedge clk) r <= ^{rdata, rdata_en, hit, ready, cmd_wr, cmd_num, cmd_data};
    assign sout = r;
endmodule
