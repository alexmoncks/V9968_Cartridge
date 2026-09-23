// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Alex Moncks
// ============================================================================
// geo3d_engine.v  (phase 2)
//
// Wireframe renderer that drives the V9968 command engine directly, so the
// Z80 only uploads the model once and then sends one matrix + RUN per frame.
//
//   Z80 ──(2 I/O ports)──> geo3d_engine ──(R#36..R#46 writes, CE)──> V9968
//
// Per RUN:
//   1. Transform phase: every vertex in vertex RAM goes through geo3d_core
//      (matrix, translation, perspective). Results go to the projected RAM.
//   2. Edge phase: for every edge (pair of vertex indices):
//        - edges touching a vertex behind the near plane or with a saturated
//          projection are skipped (counted in SKIPPED)
//        - edges trivially outside the screen are culled (counted in CULLED)
//        - otherwise the edge is clipped to [0,W) x [0,H) by midpoint
//          bisection (no divider) and emitted as a LINE command
//   3. The command issuer writes R#36..R#46 whenever the VDP is idle (CE=0),
//      while the clipper already prepares the next edge (1-deep pipeline).
//
// Z80 register window (index written on port SEL=0, data on port SEL=1):
//   0x00-0x23  geo3d_core configuration (M00..M22, TX,TY,TZ, F, CX, CY,
//              ZNEAR, W, H) little-endian words, auto-increment
//   0x24-0x29  immediate vertex (phase 1 mode, write of 0x29 computes one
//              vertex; index wraps back to 0x24)
//   0x30-0x36  immediate results SX, SY, Z, FLAGS (read, wraps at 0x36)
//   0x40 VADDR   vertex RAM write pointer
//   0x41 EADDR   edge RAM write pointer
//   0x42 NVERT   number of vertices (0..255)
//   0x43 NEDGE   number of edges (0..255)
//   0x44 COLOR   LINE colour (R#44)
//   0x45 LOP     logical operation (low nibble of R#46)
//   0x46-0x47 YPAGE  added to every DY (page select), 11 bits
//   0x48 CTRL    write bit0=1: RUN.  read: status (see below)
//   0x4A-0x4B SKIPPED (read)  0x4C-0x4D DRAWN (read)  0x4E-0x4F CULLED (read)
//   0x50 VDATA   vertex stream: 6 bytes (VX,VY,VZ little-endian) per vertex,
//                stored at VADDR, VADDR auto-increments. Index does not move.
//   0x51 EDATA   edge stream: 2 bytes (index A, index B) per edge, stored at
//                EADDR, EADDR auto-increments. Index does not move.
//   Indices 0x40-0x4F auto-increment (wrap inside 0x40-0x4F).
//   Writing the index (port SEL=0) resets the VDATA/EDATA byte counters.
//
// Status (port SEL=0 read, no side effects):
//   bit0 RUN busy   bit1 transform phase   bit2 edge phase
//   bit3 core busy  bit7..4 immediate-mode outcodes
//
// Rules for the Z80 while RUN busy = 1:
//   - do not write VDP command registers R#32..R#46
//   - do not write geo3d configuration or model RAM
// Clearing the screen (HMMV) and page flipping stay with the Z80.
//
// Limits: W <= 512, H <= 1024 (VDP coordinate range). Edges crossing the near
// plane are skipped (3D near-plane clipping is future work).
// ============================================================================
module geo3d_engine (
    input  wire        clk,
    input  wire        rst,
    // Z80 side (strobes are one clock wide, already synchronised)
    input  wire        sel,
    input  wire        wr_stb,
    input  wire        rd_stb,
    input  wire [7:0]  din,
    output reg  [7:0]  dout,
    // V9968 command master port
    output reg         cmd_wr,
    output reg  [5:0]  cmd_num,
    output reg  [7:0]  cmd_data,
    input  wire        cmd_ce,
    output wire        run_busy
);

    // ------------------------------------------------------------------ core
    wire [15:0] c_sx, c_sy, c_z;
    wire [7:0]  c_flags;
    wire        c_busy, c_done;

    reg         z_core_wr;
    reg  [4:0]  z_core_addr;
    reg  [15:0] z_core_data;
    reg         e_core_wr;
    reg  [4:0]  e_core_addr;
    reg  [15:0] e_core_data;

    wire        core_wr   = e_core_wr | z_core_wr;
    wire [4:0]  core_addr = e_core_wr ? e_core_addr : z_core_addr;
    wire [15:0] core_data = e_core_wr ? e_core_data : z_core_data;

    geo3d_core u_core (
        .clk(clk), .rst(rst),
        .wr_en(core_wr), .wr_addr(core_addr), .wr_data(core_data),
        .sx(c_sx), .sy(c_sy), .zout(c_z), .flags(c_flags),
        .busy(c_busy), .done_pulse(c_done)
    );

    // Shadow copies of W and H (the clipper needs them)
    reg signed [15:0] scr_w, scr_h;

    // ------------------------------------------------------------ model RAMs
    reg [47:0] vmem [0:255];      // VZ, VY, VX
    reg [15:0] emem [0:255];      // B, A
    reg [39:0] pmem [0:255];      // FLAGS, SY, SX

    reg        vwe;  reg [7:0] vwa;  reg [47:0] vwd;
    reg [7:0]  vra;  reg [47:0] vrd;
    reg        ewe;  reg [7:0] ewa;  reg [15:0] ewd;
    reg [7:0]  era;  reg [15:0] erd;
    reg        pwe;  reg [7:0] pwa;  reg [39:0] pwd;
    reg [7:0]  pra;  reg [39:0] prd;

    always @(posedge clk) begin
        if (vwe) vmem[vwa] <= vwd;
        vrd <= vmem[vra];
    end
    always @(posedge clk) begin
        if (ewe) emem[ewa] <= ewd;
        erd <= emem[era];
    end
    always @(posedge clk) begin
        if (pwe) pmem[pwa] <= pwd;
        prd <= pmem[pra];
    end

    // ------------------------------------------------------ Z80 registers
    reg  [7:0]  widx, rptr, lo_latch;
    reg  [7:0]  vaddr_w, eaddr_w, nvert, nedge, color;
    reg  [3:0]  lop;
    reg  [10:0] ypage;
    reg  [39:0] vbuf;
    reg  [2:0]  vbc;
    reg  [7:0]  ebuf;
    reg         ebc;
    reg         run_req;
    reg  [15:0] cnt_skip, cnt_draw, cnt_cull;

    // phase flags (driven by the engine FSM below)
    reg         ph_run, ph_xf, ph_edge;
    assign run_busy = ph_run;

    wire [7:0] status = {c_flags[7:4], c_busy, ph_edge, ph_xf, ph_run};

    function [7:0] next_idx;
        input [7:0] i;
        begin
            if (i[7:4] == 4'h4)      next_idx = {4'h4, i[3:0] + 4'd1};
            else if (i == 8'h29)     next_idx = 8'h24;
            else if (i < 8'h40)      next_idx = i + 8'd1;
            else                     next_idx = i;
        end
    endfunction

    reg [7:0] rdata;
    always @(*) begin
        case (rptr)
            8'h30: rdata = c_sx[7:0];
            8'h31: rdata = c_sx[15:8];
            8'h32: rdata = c_sy[7:0];
            8'h33: rdata = c_sy[15:8];
            8'h34: rdata = c_z[7:0];
            8'h35: rdata = c_z[15:8];
            8'h36: rdata = c_flags;
            8'h40: rdata = vaddr_w;
            8'h41: rdata = eaddr_w;
            8'h42: rdata = nvert;
            8'h43: rdata = nedge;
            8'h44: rdata = color;
            8'h45: rdata = {4'd0, lop};
            8'h46: rdata = ypage[7:0];
            8'h47: rdata = {5'd0, ypage[10:8]};
            8'h48: rdata = status;
            8'h4A: rdata = cnt_skip[7:0];
            8'h4B: rdata = cnt_skip[15:8];
            8'h4C: rdata = cnt_draw[7:0];
            8'h4D: rdata = cnt_draw[15:8];
            8'h4E: rdata = cnt_cull[7:0];
            8'h4F: rdata = cnt_cull[15:8];
            default: rdata = 8'hFF;
        endcase
    end

    always @(posedge clk) begin
        if (rst) begin
            widx <= 8'h00; rptr <= 8'h30; lo_latch <= 8'h00;
            vaddr_w <= 8'd0; eaddr_w <= 8'd0; nvert <= 8'd0; nedge <= 8'd0;
            color <= 8'd15; lop <= 4'd0; ypage <= 11'd0;
            vbc <= 3'd0; ebc <= 1'b0; run_req <= 1'b0;
            z_core_wr <= 1'b0; vwe <= 1'b0; ewe <= 1'b0;
            dout <= 8'hFF;
            scr_w <= 16'sd256; scr_h <= 16'sd212;
        end else begin
            z_core_wr <= 1'b0;
            vwe       <= 1'b0;
            ewe       <= 1'b0;
            run_req   <= 1'b0;

            if (wr_stb) begin
                if (!sel) begin
                    widx <= din; rptr <= din; vbc <= 3'd0; ebc <= 1'b0;
                end else begin
                    if (widx < 8'h2A) begin
                        if (!widx[0]) lo_latch <= din;
                        else begin
                            z_core_wr   <= 1'b1;
                            z_core_addr <= widx[5:1];
                            z_core_data <= {din, lo_latch};
                            if (widx[5:1] == 5'd16) scr_w <= {din, lo_latch};
                            if (widx[5:1] == 5'd17) scr_h <= {din, lo_latch};
                            if (widx[5:1] == 5'd20) rptr  <= 8'h30;
                        end
                    end else begin
                        case (widx)
                            8'h40: vaddr_w <= din;
                            8'h41: eaddr_w <= din;
                            8'h42: nvert   <= din;
                            8'h43: nedge   <= din;
                            8'h44: color   <= din;
                            8'h45: lop     <= din[3:0];
                            8'h46: ypage[7:0]  <= din;
                            8'h47: ypage[10:8] <= din[2:0];
                            8'h48: run_req <= din[0];
                            8'h50: begin
                                if (vbc == 3'd5) begin
                                    vwe <= 1'b1; vwa <= vaddr_w; vwd <= {din, vbuf};
                                    vaddr_w <= vaddr_w + 8'd1;
                                    vbc <= 3'd0;
                                end else begin
                                    vbuf <= {din, vbuf[39:8]};
                                    vbc  <= vbc + 3'd1;
                                end
                            end
                            8'h51: begin
                                if (ebc) begin
                                    ewe <= 1'b1; ewa <= eaddr_w; ewd <= {din, ebuf};
                                    eaddr_w <= eaddr_w + 8'd1;
                                end else begin
                                    ebuf <= din;
                                end
                                ebc <= ~ebc;
                            end
                            default: ;
                        endcase
                    end
                    widx <= next_idx(widx);
                end
            end

            if (rd_stb) begin
                if (!sel) dout <= status;
                else begin
                    dout <= rdata;
                    if (rptr == 8'h36)          rptr <= 8'h30;
                    else if (rptr[7:4] == 4'h4) rptr <= {4'h4, rptr[3:0] + 4'd1};
                    else if (rptr >= 8'h30 && rptr < 8'h36) rptr <= rptr + 8'd1;
                end
            end
        end
    end

    // --------------------------------------------------------- clip helpers
    function [3:0] ocode;
        input signed [16:0] x;
        input signed [16:0] y;
        input signed [15:0] w;
        input signed [15:0] h;
        begin
            ocode[0] = (x < 17'sd0);
            ocode[1] = (x >= $signed({w[15], w}));
            ocode[2] = (y < 17'sd0);
            ocode[3] = (y >= $signed({h[15], h}));
        end
    endfunction

    function signed [16:0] mid;
        input signed [16:0] a;
        input signed [16:0] b;
        reg   signed [17:0] s;
        begin
            s   = {a[16], a} + {b[16], b};
            mid = s[17:1];               // floor((a+b)/2)
        end
    endfunction

    // --------------------------------------------------------- engine FSM
    localparam R_IDLE  = 5'd0,
               T_ADDR  = 5'd1,  T_W1 = 5'd2,  T_W2 = 5'd3,
               T_WY    = 5'd4,  T_WZ = 5'd5,  T_WAIT = 5'd6,
               E_START = 5'd7,  E_ADDR = 5'd8, E_W1 = 5'd9, E_W2 = 5'd10,
               E_PA1   = 5'd11, E_PA2 = 5'd12, E_PB1 = 5'd13, E_PB2 = 5'd14,
               E_CLASS = 5'd15, E_SRCH = 5'd16, E_E1 = 5'd17, E_BIS = 5'd18,
               E_BISE  = 5'd19, E_E2 = 5'd20, E_BUILD = 5'd21, E_NEXT = 5'd22,
               R_DRAIN = 5'd23;

    reg [4:0]  st;
    reg [7:0]  vi, ei;
    reg [47:0] vtmp;
    reg [7:0]  ib;
    reg signed [16:0] ax_, ay_, bx_, by_;     // endpoints
    reg [2:0]  fa, fb;                        // near/ovf flags
    reg signed [16:0] px, py;                 // inside point
    reg signed [16:0] lox, loy, hix, hiy;     // search / bisection pair
    reg signed [16:0] e1x, e1y, e2x, e2y;
    reg [3:0]  it;
    reg        which;                         // 0 = E1, 1 = E2

    // issuer
    reg        hold_valid;
    reg [7:0]  hb [0:10];
    reg [3:0]  ik;
    reg        isend;
    reg [2:0]  guard;

    wire [3:0] ca = ocode(ax_, ay_, scr_w, scr_h);
    wire [3:0] cb = ocode(bx_, by_, scr_w, scr_h);

    wire signed [16:0] mx = mid(lox, hix);
    wire signed [16:0] my = mid(loy, hiy);
    wire [3:0] cm  = ocode(mx,  my,  scr_w, scr_h);
    wire [3:0] clo = ocode(lox, loy, scr_w, scr_h);
    wire [3:0] chi = ocode(hix, hiy, scr_w, scr_h);

    // LINE command fields from e1/e2
    wire signed [17:0] ldx = {e2x[16], e2x} - {e1x[16], e1x};
    wire signed [17:0] ldy = {e2y[16], e2y} - {e1y[16], e1y};
    wire [17:0] lax = ldx[17] ? -ldx : ldx;
    wire [17:0] lay = ldy[17] ? -ldy : ldy;
    wire        lmaj = (lay > lax);
    wire [10:0] lnx = lmaj ? lay[10:0] : lax[10:0];
    wire [10:0] lny = lmaj ? lax[10:0] : lay[10:0];
    wire [10:0] ldy_full = e1y[10:0] + ypage;
    wire [7:0]  larg = {4'd0, ldy[17], ldx[17], 1'b0, lmaj};

    integer k;

    always @(posedge clk) begin
        if (rst) begin
            st <= R_IDLE; ph_run <= 1'b0; ph_xf <= 1'b0; ph_edge <= 1'b0;
            e_core_wr <= 1'b0; pwe <= 1'b0;
            hold_valid <= 1'b0; isend <= 1'b0; guard <= 3'd0;
            cmd_wr <= 1'b0; cmd_num <= 6'd0; cmd_data <= 8'd0;
            cnt_skip <= 16'd0; cnt_draw <= 16'd0; cnt_cull <= 16'd0;
        end else begin
            e_core_wr <= 1'b0;
            pwe       <= 1'b0;
            cmd_wr    <= 1'b0;
            if (guard != 3'd0) guard <= guard - 3'd1;

            // ------------------------------------------------ issuer
            if (!isend) begin
                if (hold_valid && !cmd_ce && guard == 3'd0) begin
                    isend <= 1'b1;
                    ik    <= 4'd0;
                end
            end else begin
                cmd_wr   <= 1'b1;
                cmd_num  <= 6'd36 + {2'd0, ik};
                cmd_data <= hb[ik];
                if (ik == 4'd10) begin
                    isend      <= 1'b0;
                    hold_valid <= 1'b0;
                    guard      <= 3'd4;
                end else begin
                    ik <= ik + 4'd1;
                end
            end

            // ------------------------------------------------ producer
            case (st)
                R_IDLE: begin
                    if (run_req) begin
                        ph_run   <= 1'b1;
                        cnt_skip <= 16'd0; cnt_draw <= 16'd0; cnt_cull <= 16'd0;
                        vi <= 8'd0;
                        if (nvert == 8'd0) st <= E_START;
                        else begin ph_xf <= 1'b1; st <= T_ADDR; end
                    end
                end

                // ------------------------- transform phase
                T_ADDR: begin vra <= vi; st <= T_W1; end
                T_W1:   st <= T_W2;
                T_W2: begin
                    vtmp <= vrd;
                    e_core_wr <= 1'b1; e_core_addr <= 5'd18; e_core_data <= vrd[15:0];
                    st <= T_WY;
                end
                T_WY: begin
                    e_core_wr <= 1'b1; e_core_addr <= 5'd19; e_core_data <= vtmp[31:16];
                    st <= T_WZ;
                end
                T_WZ: begin
                    e_core_wr <= 1'b1; e_core_addr <= 5'd20; e_core_data <= vtmp[47:32];
                    st <= T_WAIT;
                end
                T_WAIT: begin
                    if (c_done) begin
                        pwe <= 1'b1; pwa <= vi;
                        pwd <= {c_flags, c_sy, c_sx};
                        vi  <= vi + 8'd1;
                        if (vi + 8'd1 == nvert) st <= E_START;
                        else                    st <= T_ADDR;
                    end
                end

                // ------------------------- edge phase
                E_START: begin
                    ph_xf <= 1'b0;
                    ei    <= 8'd0;
                    if (nedge == 8'd0) st <= R_DRAIN;
                    else begin ph_edge <= 1'b1; st <= E_ADDR; end
                end
                E_ADDR: begin era <= ei; st <= E_W1; end
                E_W1:   st <= E_W2;
                E_W2: begin
                    pra <= erd[7:0];
                    ib  <= erd[15:8];
                    st  <= E_PA1;
                end
                E_PA1:  st <= E_PA2;
                E_PA2: begin
                    ax_ <= {prd[15], prd[15:0]};
                    ay_ <= {prd[31], prd[31:16]};
                    fa  <= prd[34:32];
                    pra <= ib;
                    st  <= E_PB1;
                end
                E_PB1:  st <= E_PB2;
                E_PB2: begin
                    bx_ <= {prd[15], prd[15:0]};
                    by_ <= {prd[31], prd[31:16]};
                    fb  <= prd[34:32];
                    st  <= E_CLASS;
                end
                E_CLASS: begin
                    if (fa != 3'd0 || fb != 3'd0) begin
                        cnt_skip <= cnt_skip + 16'd1;
                        st <= E_NEXT;
                    end else if ((ca & cb) != 4'd0) begin
                        cnt_cull <= cnt_cull + 16'd1;
                        st <= E_NEXT;
                    end else if (ca == 4'd0) begin
                        px <= ax_; py <= ay_; st <= E_E1;
                    end else if (cb == 4'd0) begin
                        px <= bx_; py <= by_; st <= E_E1;
                    end else begin
                        lox <= ax_; loy <= ay_; hix <= bx_; hiy <= by_;
                        it  <= 4'd0;
                        st  <= E_SRCH;
                    end
                end
                E_SRCH: begin
                    if (cm == 4'd0) begin
                        px <= mx; py <= my; st <= E_E1;
                    end else if ((clo & cm) != 4'd0) begin
                        lox <= mx; loy <= my;
                        it  <= it + 4'd1;
                        if (it == 4'd15) begin cnt_skip <= cnt_skip + 16'd1; st <= E_NEXT; end
                    end else if ((cm & chi) != 4'd0) begin
                        hix <= mx; hiy <= my;
                        it  <= it + 4'd1;
                        if (it == 4'd15) begin cnt_skip <= cnt_skip + 16'd1; st <= E_NEXT; end
                    end else begin
                        cnt_skip <= cnt_skip + 16'd1;
                        st <= E_NEXT;
                    end
                end
                E_E1: begin
                    if (ca == 4'd0) begin
                        e1x <= ax_; e1y <= ay_; st <= E_E2;
                    end else begin
                        lox <= px; loy <= py; hix <= ax_; hiy <= ay_;   // lo = inside
                        it <= 4'd0; which <= 1'b0; st <= E_BIS;
                    end
                end
                E_E2: begin
                    if (cb == 4'd0) begin
                        e2x <= bx_; e2y <= by_; st <= E_BUILD;
                    end else begin
                        lox <= px; loy <= py; hix <= bx_; hiy <= by_;
                        it <= 4'd0; which <= 1'b1; st <= E_BIS;
                    end
                end
                E_BIS: begin
                    if (cm == 4'd0) begin lox <= mx; loy <= my; end
                    else            begin hix <= mx; hiy <= my; end
                    it <= it + 4'd1;
                    if (it == 4'd15) st <= E_BISE;
                end
                E_BISE: begin
                    if (!which) begin e1x <= lox; e1y <= loy; st <= E_E2; end
                    else        begin e2x <= lox; e2y <= loy; st <= E_BUILD; end
                end
                E_BUILD: begin
                    if (!hold_valid) begin
                        hb[0]  <= e1x[7:0];
                        hb[1]  <= {7'd0, e1x[8]};
                        hb[2]  <= ldy_full[7:0];
                        hb[3]  <= {5'd0, ldy_full[10:8]};
                        hb[4]  <= lnx[7:0];
                        hb[5]  <= {5'd0, lnx[10:8]};
                        hb[6]  <= lny[7:0];
                        hb[7]  <= {5'd0, lny[10:8]};
                        hb[8]  <= color;
                        hb[9]  <= larg;
                        hb[10] <= {4'h7, lop};
                        hold_valid <= 1'b1;
                        cnt_draw   <= cnt_draw + 16'd1;
                        st <= E_NEXT;
                    end
                end
                E_NEXT: begin
                    ei <= ei + 8'd1;
                    if (ei + 8'd1 == nedge) st <= R_DRAIN;
                    else                    st <= E_ADDR;
                end

                R_DRAIN: begin
                    ph_edge <= 1'b0;
                    if (!hold_valid && !isend && guard == 3'd0 && !cmd_ce) begin
                        ph_run <= 1'b0;
                        st <= R_IDLE;
                    end
                end
                default: st <= R_IDLE;
            endcase
        end
    end

endmodule
