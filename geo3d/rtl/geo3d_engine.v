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
//   0x48 CTRL    write bit0=1: RUN, bit1: 0 = wireframe (edges), 1 = filled
//                faces.  read: status (see below)
//   0x4A-0x4B SKIPPED (read)  0x4C-0x4D DRAWN (read)  0x4E-0x4F CULLED (read)
//   0x50 VDATA   vertex stream: 6 bytes (VX,VY,VZ little-endian) per vertex,
//                stored at VADDR, VADDR auto-increments. Index does not move.
//   0x51 EDATA   edge stream: 2 bytes (index A, index B) per edge, stored at
//                EADDR, EADDR auto-increments. Index does not move.
//   0x52 FDATA   face stream: 11 bytes per face (I0, I1, I2, I3 vertex
//                indices, NX, NY, NZ model normal in Q2.14 little-endian,
//                BASE colour), stored at FADDR, FADDR auto-increments.
//   0x58 FADDR   face RAM write pointer
//   0x59 NFACE   number of faces (0..255)
//   0x5A-0x5F LX, LY, LZ  light direction (towards the light) in camera
//                space, Q2.14 words
//   Indices 0x40-0x4F and 0x58-0x5F auto-increment (wrap inside the block).
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
// Filled-face mode (CTRL bit1 = 1), per RUN after the transform phase:
//   a. LM = transpose(R) * L, the light in model space (9 multiplies)
//   b. per face: skip if a vertex is behind the near plane or saturated;
//      back-face cull by the sign of the projected area of I0, I1, I2
//      (visible when area > 0; wind faces counter-clockwise seen from
//      outside, with y up); shade level = min(6, 7 * max(0, LM . N)), colour =
//      BASE + level; depth key = sum of the 4 camera-space Z
//   c. painter's order: farthest key first, ties by lower face index
//   d. each face (convex quad; triangles repeat a vertex) is filled row by
//      row: the span between the leftmost and rightmost edge crossing,
//      clipped to the screen, is sent as a horizontal LINE command
//   Counters in this mode: SKIPPED = near faces, CULLED = back faces,
//   DRAWN = faces drawn.
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
    reg signed [16:0] scr_wm1, scr_hm1;       // W-1, H-1 (registered for timing)

    // ------------------------------------------------------------ model RAMs
    reg [47:0] vmem [0:255];      // VZ, VY, VX
    reg [15:0] emem [0:255];      // B, A
    reg [55:0] pmem [0:255];      // Z, FLAGS, SY, SX

    reg        vwe;  reg [7:0] vwa;  reg [47:0] vwd;
    reg [7:0]  vra;  reg [47:0] vrd;
    reg        ewe;  reg [7:0] ewa;  reg [15:0] ewd;
    reg [7:0]  era;  reg [15:0] erd;
    reg        pwe;  reg [7:0] pwa;  reg [55:0] pwd;
    reg [7:0]  pra;  reg [55:0] prd;

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

    reg [87:0] fmem [0:255];      // BASE, NZ, NY, NX, I3, I2, I1, I0
    reg [33:0] smem [0:255];      // visible faces: KEY(18), FACE(8), COLOUR(8)
    reg        fwe;  reg [7:0] fwa;  reg [87:0] fwd;
    reg [7:0]  fra;  reg [87:0] frd;
    reg        swe;  reg [7:0] swa;  reg [33:0] swd;
    reg [7:0]  sra;  reg [33:0] srd;

    always @(posedge clk) begin
        if (fwe) fmem[fwa] <= fwd;
        frd <= fmem[fra];
    end
    always @(posedge clk) begin
        if (swe) smem[swa] <= swd;
        srd <= smem[sra];
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
    reg         ctrl_face;
    reg  [7:0]  faddr_w, nface;
    reg  [79:0] fbuf;
    reg  [3:0]  fbc;
    reg signed [15:0] lreg [0:2];     // light LX, LY, LZ (Q2.14)
    reg signed [15:0] msh  [0:8];     // shadow of M00..M22 (Q2.14)
    integer     q;
    reg  [15:0] cnt_skip, cnt_draw, cnt_cull;

    // phase flags (driven by the engine FSM below)
    reg         ph_run, ph_xf, ph_edge;
    assign run_busy = ph_run;

    wire [7:0] status = {c_flags[7:4], c_busy, ph_edge, ph_xf, ph_run};

    function [7:0] next_idx;
        input [7:0] i;
        begin
            if (i[7:4] == 4'h4)      next_idx = {4'h4, i[3:0] + 4'd1};
            else if (i[7:3] == 5'b01011) next_idx = {5'b01011, i[2:0] + 3'd1};
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
            8'h58: rdata = faddr_w;
            8'h59: rdata = nface;
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
            scr_wm1 <= 17'sd255; scr_hm1 <= 17'sd211;
            ctrl_face <= 1'b0; faddr_w <= 8'd0; nface <= 8'd0; fbc <= 4'd0; fwe <= 1'b0;
            lreg[0] <= 16'sd0; lreg[1] <= 16'sd0; lreg[2] <= -16'sd16384;
            for (q = 0; q < 9; q = q + 1) msh[q] <= 16'sd0;
        end else begin
            z_core_wr <= 1'b0;
            vwe       <= 1'b0;
            ewe       <= 1'b0;
            fwe       <= 1'b0;
            run_req   <= 1'b0;

            if (wr_stb) begin
                if (!sel) begin
                    widx <= din; rptr <= din; vbc <= 3'd0; ebc <= 1'b0; fbc <= 4'd0;
                end else begin
                    if (widx < 8'h2A) begin
                        if (!widx[0]) lo_latch <= din;
                        else begin
                            z_core_wr   <= 1'b1;
                            z_core_addr <= widx[5:1];
                            z_core_data <= {din, lo_latch};
                            if (widx[5:1] == 5'd16) begin
                                scr_w   <= {din, lo_latch};
                                scr_wm1 <= $signed({din[7], din, lo_latch}) - 17'sd1;
                            end
                            if (widx[5:1] == 5'd17) begin
                                scr_h   <= {din, lo_latch};
                                scr_hm1 <= $signed({din[7], din, lo_latch}) - 17'sd1;
                            end
                            if (widx[5:1] == 5'd20) rptr  <= 8'h30;
                            if (widx[5:1] < 5'd9)   msh[widx[4:1]] <= {din, lo_latch};
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
                            8'h48: begin run_req <= din[0]; ctrl_face <= din[1]; end
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
                            8'h52: begin
                                if (fbc == 4'd10) begin
                                    fwe <= 1'b1; fwa <= faddr_w; fwd <= {din, fbuf};
                                    faddr_w <= faddr_w + 8'd1;
                                    fbc <= 4'd0;
                                end else begin
                                    fbuf <= {din, fbuf[79:8]};
                                    fbc  <= fbc + 4'd1;
                                end
                            end
                            8'h58: faddr_w <= din;
                            8'h59: nface   <= din;
                            8'h5A, 8'h5C, 8'h5E: lo_latch <= din;
                            8'h5B: lreg[0] <= {din, lo_latch};
                            8'h5D: lreg[1] <= {din, lo_latch};
                            8'h5F: lreg[2] <= {din, lo_latch};
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
    localparam R_IDLE  = 6'd0,
               T_ADDR  = 6'd1,  T_W1 = 6'd2,  T_W2 = 6'd3,
               T_WY    = 6'd4,  T_WZ = 6'd5,  T_WAIT = 6'd6,
               E_START = 6'd7,  E_ADDR = 6'd8, E_W1 = 6'd9, E_W2 = 6'd10,
               E_PA1   = 6'd11, E_PA2 = 6'd12, E_PB1 = 6'd13, E_PB2 = 6'd14,
               E_CLASS = 6'd15, E_SRCH = 6'd16, E_E1 = 6'd17, E_BIS = 6'd18,
               E_BISE  = 6'd19, E_E2 = 6'd20, E_BUILD = 6'd21, E_NEXT = 6'd22,
               R_DRAIN = 6'd23,
               // filled-face mode
               L_OP    = 6'd24, L_ACC = 6'd25,
               F_START = 6'd26, F_ADDR = 6'd27, F_W1 = 6'd28, F_W2 = 6'd29,
               V_ADDR  = 6'd30, V_W1 = 6'd31, V_W2 = 6'd32,
               F_CHK   = 6'd33, F_A1 = 6'd34, F_A2 = 6'd35,
               F_S1    = 6'd36, F_S2 = 6'd37, F_S3 = 6'd38, F_NEXT = 6'd39,
               D_START = 6'd40, D_SRD = 6'd41, D_SW1 = 6'd42, D_SW2 = 6'd43,
               D_SEL   = 6'd44, D_FW1 = 6'd45, D_FW2 = 6'd46, D_YR = 6'd47,
               D_ROW   = 6'd48, D_EDGE = 6'd49, D_EMUL = 6'd50, D_DIV = 6'd51,
               D_DIVE  = 6'd52, D_ENEXT = 6'd53, D_SPAN = 6'd54, D_YNEXT = 6'd55,
               D_NEXTR = 6'd56, D_YR0 = 6'd57, F_S4 = 6'd58,
               D_SPAN0 = 6'd59, D_DIVE2 = 6'd60, D_EDGE0 = 6'd61, F_A3 = 6'd62;

    reg [5:0]  st;
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

    // ---------------------------------------------------- face-mode datapath
    reg signed [17:0] ma, mb;                 // shared multiplier operands
    wire signed [35:0] mp = ma * mb;

    reg        fmode;                         // latched CTRL bit1 for this RUN
    reg [1:0]  li, lj;
    reg signed [35:0] lacc;
    reg signed [17:0] lm [0:2];               // light in model space

    reg [7:0]  fi, nvis, rank, si;
    reg [87:0] fcur;
    reg [1:0]  vk;
    reg        vret;                          // 0 = classify, 1 = draw
    reg signed [15:0] vx [0:3];
    reg signed [15:0] vy [0:3];
    reg signed [15:0] vz [0:3];
    reg [2:0]  vfl [0:3];
    reg signed [36:0] a1;
    reg signed [37:0] sacc;

    reg        first, bvalid;
    reg signed [17:0] lkey, bkey;
    reg [7:0]  lidx, bidx, bcol, dcol;

    reg [10:0] yrow, ymax;
    reg [1:0]  ek;
    reg signed [19:0] xl, xr;
    reg signed [17:0] den;
    reg        dsgn;
    reg [17:0] drem;
    reg [17:0] dlow;
    reg [17:0] dq;
    reg [4:0]  dcnt;

    function signed [17:0] sat18s;
        input signed [35:0] x;
        begin
            if (x > 36'sd131071)       sat18s = 18'sd131071;
            else if (x < -36'sd131071) sat18s = -18'sd131071;
            else                       sat18s = x[17:0];
        end
    endfunction

    // matrix element M[3*li + lj] (row li, column lj)
    wire [3:0] midx = {2'd0, li} + {2'd0, li} + {2'd0, li} + {2'd0, lj};

    // shade level from the accumulated dot product
    wire signed [37:0] snext  = sacc + {{2{mp[35]}}, mp};
    wire signed [23:0] shade  = sacc[37:14];       // registered in F_S3
    wire signed [26:0] shade7 = {{3{shade[23]}}, shade} * 27'sd7;
    wire [2:0]  slevel = (shade <= 24'sd0) ? 3'd0 :
                         (shade7[26:14] >= 13'sd6) ? 3'd6 : shade7[16:14];

    // area terms from the projected vertices 0, 1, 2
    wire signed [16:0] adx1 = {vx[1][15], vx[1]} - {vx[0][15], vx[0]};
    wire signed [16:0] ady1 = {vy[1][15], vy[1]} - {vy[0][15], vy[0]};
    wire signed [16:0] adx2 = {vx[2][15], vx[2]} - {vx[0][15], vx[0]};
    wire signed [16:0] ady2 = {vy[2][15], vy[2]} - {vy[0][15], vy[0]};
    wire signed [36:0] area = a1 - {{1{mp[35]}}, mp};
    reg  signed [36:0] area_r;
    wire signed [17:0] zkey = {{2{vz[0][15]}}, vz[0]} + {{2{vz[1][15]}}, vz[1]}
                            + {{2{vz[2][15]}}, vz[2]} + {{2{vz[3][15]}}, vz[3]};

    // sort: candidate entry from the visible list
    wire signed [17:0] ekey = srd[33:16];
    wire [7:0]  eidx = srd[15:8];
    wire        equal_ok  = first || (ekey < lkey) || (ekey == lkey && eidx > lidx);
    wire        ebetter   = !bvalid || (ekey > bkey) || (ekey == bkey && eidx < bidx);

    // fill: current edge (ek -> ek+1) and y range
    wire [1:0]  ek1 = ek + 2'd1;
    reg  signed [16:0] eya, eyb, exa, exb;    // current edge, loaded in D_EDGE0
    wire signed [16:0] ycur = $signed({6'd0, yrow});
    wire signed [16:0] ylo = (eya < eyb) ? eya : eyb;
    wire signed [16:0] yhi = (eya < eyb) ? eyb : eya;
    wire signed [16:0] fdy  = ycur - eya;       // 17-bit differences, then
    wire signed [16:0] fdx  = exb - exa;        // sign-extended with their
    wire signed [16:0] fden = eyb - eya;        // own MSB
    wire signed [19:0] exa20 = {{3{exa[16]}}, exa};
    wire signed [19:0] exb20 = {{3{exb[16]}}, exb};

    wire signed [16:0] vymin01 = (vy[0] < vy[1]) ? {vy[0][15], vy[0]} : {vy[1][15], vy[1]};
    wire signed [16:0] vymin23 = (vy[2] < vy[3]) ? {vy[2][15], vy[2]} : {vy[3][15], vy[3]};
    wire signed [16:0] vymax01 = (vy[0] > vy[1]) ? {vy[0][15], vy[0]} : {vy[1][15], vy[1]};
    wire signed [16:0] vymax23 = (vy[2] > vy[3]) ? {vy[2][15], vy[2]} : {vy[3][15], vy[3]};
    wire signed [16:0] fymin = (vymin01 < vymin23) ? vymin01 : vymin23;
    wire signed [16:0] fymax = (vymax01 > vymax23) ? vymax01 : vymax23;
    wire signed [16:0] hm1   = scr_hm1;
    wire signed [16:0] wm1   = scr_wm1;
    reg  signed [16:0] fymin_r, fymax_r;
    wire signed [16:0] cymin = (fymin_r < 17'sd0) ? 17'sd0 : fymin_r;
    wire signed [16:0] cymax = (fymax_r > hm1) ? hm1 : fymax_r;

    // division step
    wire [18:0] drem2 = {drem, dlow[17]};
    wire        dge   = (drem2 >= {1'b0, (den[17] ? -den : den)});
    wire [18:0] ddiff = drem2 - {1'b0, (den[17] ? -den : den)};
    wire signed [19:0] dfq = dsgn ? ((drem != 18'd0) ? -$signed({2'b00, dq}) - 20'sd1
                                                    : -$signed({2'b00, dq}))
                                  : $signed({2'b00, dq});
    wire signed [19:0] xcross = exa20 + dfq;

    // span
    wire signed [19:0] wm1_20 = {{3{wm1[16]}}, wm1};
    wire signed [19:0] scl = (xl < 20'sd0) ? 20'sd0 : xl;
    wire signed [19:0] scr = (xr > wm1_20) ? wm1_20 : xr;
    reg  signed [19:0] scl_r, scr_r, xc_r;    // registered span / crossing
    reg                span_ok;
    wire signed [19:0] snx = scr_r - scl_r;
    wire [10:0] sdy = yrow + ypage;

    integer k;

    always @(posedge clk) begin
        if (rst) begin
            st <= R_IDLE; ph_run <= 1'b0; ph_xf <= 1'b0; ph_edge <= 1'b0;
            e_core_wr <= 1'b0; pwe <= 1'b0; swe <= 1'b0; fmode <= 1'b0;
            hold_valid <= 1'b0; isend <= 1'b0; guard <= 3'd0;
            cmd_wr <= 1'b0; cmd_num <= 6'd0; cmd_data <= 8'd0;
            cnt_skip <= 16'd0; cnt_draw <= 16'd0; cnt_cull <= 16'd0;
        end else begin
            e_core_wr <= 1'b0;
            pwe       <= 1'b0;
            swe       <= 1'b0;
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
                        fmode    <= ctrl_face;
                        cnt_skip <= 16'd0; cnt_draw <= 16'd0; cnt_cull <= 16'd0;
                        vi <= 8'd0;
                        if (nvert == 8'd0) st <= ctrl_face ? L_OP : E_START;
                        else begin ph_xf <= 1'b1; st <= T_ADDR; end
                        li <= 2'd0; lj <= 2'd0; lacc <= 36'sd0;
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
                        pwd <= {c_z, c_flags, c_sy, c_sx};
                        vi  <= vi + 8'd1;
                        if (vi + 8'd1 == nvert) st <= fmode ? L_OP : E_START;
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

                // ============================== filled-face mode
                // a. LM[j] = sum_i M[i][j] * L[i]  (transpose(R) * L)
                L_OP: begin
                    ph_xf <= 1'b0;
                    ma <= {{2{msh[midx][15]}}, msh[midx]};
                    mb <= {{2{lreg[li][15]}}, lreg[li]};
                    st <= L_ACC;
                end
                L_ACC: begin
                    if (li == 2'd2) begin
                        lm[lj] <= sat18s((lacc + mp) >>> 14);
                        lacc   <= 36'sd0;
                        li     <= 2'd0;
                        lj     <= lj + 2'd1;
                        st     <= (lj == 2'd2) ? F_START : L_OP;
                    end else begin
                        lacc <= lacc + mp;
                        li   <= li + 2'd1;
                        st   <= L_OP;
                    end
                end

                // b. classify every face
                F_START: begin
                    ph_edge <= 1'b1;
                    fi <= 8'd0; nvis <= 8'd0;
                    st <= (nface == 8'd0) ? R_DRAIN : F_ADDR;
                end
                F_ADDR: begin fra <= fi; st <= F_W1; end
                F_W1:   st <= F_W2;
                F_W2: begin
                    fcur <= frd; vk <= 2'd0; vret <= 1'b0;
                    st <= V_ADDR;
                end
                // fetch the 4 projected vertices of fcur
                V_ADDR: begin
                    case (vk)
                        2'd0: pra <= fcur[7:0];
                        2'd1: pra <= fcur[15:8];
                        2'd2: pra <= fcur[23:16];
                        default: pra <= fcur[31:24];
                    endcase
                    st <= V_W1;
                end
                V_W1:   st <= V_W2;
                V_W2: begin
                    vx[vk]  <= prd[15:0];
                    vy[vk]  <= prd[31:16];
                    vfl[vk] <= prd[34:32];
                    vz[vk]  <= prd[55:40];
                    vk <= vk + 2'd1;
                    if (vk == 2'd3) st <= vret ? D_YR0 : F_CHK;
                    else            st <= V_ADDR;
                end
                F_CHK: begin
                    if (vfl[0] != 3'd0 || vfl[1] != 3'd0 || vfl[2] != 3'd0 || vfl[3] != 3'd0) begin
                        cnt_skip <= cnt_skip + 16'd1;
                        st <= F_NEXT;
                    end else begin
                        ma <= {adx1[16], adx1}; mb <= {ady2[16], ady2};
                        st <= F_A1;
                    end
                end
                F_A1: begin
                    a1 <= {{1{mp[35]}}, mp};
                    ma <= {ady1[16], ady1}; mb <= {adx2[16], adx2};
                    st <= F_A2;
                end
                F_A2: begin
                    area_r <= area;
                    st <= F_A3;
                end
                F_A3: begin
                    if (area_r <= 37'sd0) begin
                        cnt_cull <= cnt_cull + 16'd1;
                        st <= F_NEXT;
                    end else begin
                        sacc <= 38'sd0;
                        ma <= lm[0]; mb <= {{2{fcur[47]}}, fcur[47:32]};
                        st <= F_S1;
                    end
                end
                F_S1: begin
                    sacc <= snext;
                    ma <= lm[1]; mb <= {{2{fcur[63]}}, fcur[63:48]};
                    st <= F_S2;
                end
                F_S2: begin
                    sacc <= snext;
                    ma <= lm[2]; mb <= {{2{fcur[79]}}, fcur[79:64]};
                    st <= F_S3;
                end
                F_S3: begin
                    sacc <= snext;
                    st   <= F_S4;
                end
                F_S4: begin
                    swe <= 1'b1; swa <= nvis;
                    swd <= {zkey, fi, fcur[87:80] + {5'd0, slevel}};
                    nvis <= nvis + 8'd1;
                    st <= F_NEXT;
                end
                F_NEXT: begin
                    fi <= fi + 8'd1;
                    if (fi + 8'd1 == nface) st <= D_START;
                    else                    st <= F_ADDR;
                end

                // c. painter's order: repeated selection of the next farthest face
                D_START: begin
                    rank <= 8'd0; first <= 1'b1;
                    st <= (nvis == 8'd0) ? R_DRAIN : D_SRD;
                    si <= 8'd0; bvalid <= 1'b0;
                end
                D_SRD:  begin sra <= si; st <= D_SW1; end
                D_SW1:  st <= D_SW2;
                D_SW2: begin
                    if (equal_ok && ebetter) begin
                        bvalid <= 1'b1; bkey <= ekey; bidx <= eidx; bcol <= srd[7:0];
                    end
                    si <= si + 8'd1;
                    if (si + 8'd1 == nvis) st <= D_SEL;
                    else                   st <= D_SRD;
                end
                D_SEL: begin
                    first <= 1'b0; lkey <= bkey; lidx <= bidx; dcol <= bcol;
                    fra <= bidx;
                    st <= D_FW1;
                end
                D_FW1:  st <= D_FW2;
                D_FW2: begin
                    fcur <= frd; vk <= 2'd0; vret <= 1'b1;
                    st <= V_ADDR;
                end

                // d. fill the face row by row
                D_YR0: begin
                    fymin_r <= fymin; fymax_r <= fymax;
                    st <= D_YR;
                end
                D_YR: begin
                    if (cymin > cymax) st <= D_NEXTR;
                    else begin
                        yrow <= cymin[10:0]; ymax <= cymax[10:0];
                        st <= D_ROW;
                    end
                end
                D_ROW: begin
                    xl <= 20'sh7FFFF; xr <= -20'sh80000; ek <= 2'd0;
                    st <= D_EDGE0;
                end
                D_EDGE0: begin
                    eya <= {vy[ek][15],  vy[ek]};
                    eyb <= {vy[ek1][15], vy[ek1]};
                    exa <= {vx[ek][15],  vx[ek]};
                    exb <= {vx[ek1][15], vx[ek1]};
                    st  <= D_EDGE;
                end
                D_EDGE: begin
                    if (eya == eyb) begin
                        if (ycur == eya) begin
                            if (exa20 < xl && exa20 <= exb20) xl <= exa20;
                            else if (exb20 < xl)              xl <= exb20;
                            if (exa20 > xr && exa20 >= exb20) xr <= exa20;
                            else if (exb20 > xr)              xr <= exb20;
                        end
                        st <= D_ENEXT;
                    end else if (ycur >= ylo && ycur <= yhi) begin
                        ma  <= {fdy[16],  fdy};
                        mb  <= {fdx[16],  fdx};
                        den <= {fden[16], fden};
                        st  <= D_EMUL;
                    end else begin
                        st <= D_ENEXT;
                    end
                end
                D_EMUL: begin
                    dsgn <= mp[35] ^ den[17];
                    drem <= (mp[35] ? -mp : mp) >>> 18;
                    dlow <= (mp[35] ? -mp : mp);
                    dq   <= 18'd0;
                    dcnt <= 5'd17;
                    st   <= D_DIV;
                end
                D_DIV: begin
                    if (dge) begin drem <= ddiff[17:0]; dq <= {dq[16:0], 1'b1}; end
                    else     begin drem <= drem2[17:0]; dq <= {dq[16:0], 1'b0}; end
                    dlow <= {dlow[16:0], 1'b0};
                    dcnt <= dcnt - 5'd1;
                    if (dcnt == 5'd0) st <= D_DIVE;
                end
                D_DIVE: begin
                    xc_r <= xcross;
                    st <= D_DIVE2;
                end
                D_DIVE2: begin
                    if (xc_r < xl) xl <= xc_r;
                    if (xc_r > xr) xr <= xc_r;
                    st <= D_ENEXT;
                end
                D_ENEXT: begin
                    ek <= ek + 2'd1;
                    st <= (ek == 2'd3) ? D_SPAN0 : D_EDGE0;
                end
                D_SPAN0: begin
                    scl_r   <= scl;
                    scr_r   <= scr;
                    span_ok <= (xl <= xr) && (scl <= scr);
                    st <= D_SPAN;
                end
                D_SPAN: begin
                    if (!span_ok) st <= D_YNEXT;
                    else if (!hold_valid) begin
                        hb[0]  <= scl_r[7:0];
                        hb[1]  <= {7'd0, scl_r[8]};
                        hb[2]  <= sdy[7:0];
                        hb[3]  <= {5'd0, sdy[10:8]};
                        hb[4]  <= snx[7:0];
                        hb[5]  <= {5'd0, snx[10:8]};
                        hb[6]  <= 8'd0;
                        hb[7]  <= 8'd0;
                        hb[8]  <= dcol;
                        hb[9]  <= 8'd0;
                        hb[10] <= {4'h7, lop};
                        hold_valid <= 1'b1;
                        st <= D_YNEXT;
                    end
                end
                D_YNEXT: begin
                    if (yrow == ymax) st <= D_NEXTR;
                    else begin yrow <= yrow + 11'd1; st <= D_ROW; end
                end
                D_NEXTR: begin
                    cnt_draw <= cnt_draw + 16'd1;
                    rank <= rank + 8'd1;
                    if (rank + 8'd1 == nvis) st <= R_DRAIN;
                    else begin si <= 8'd0; bvalid <= 1'b0; st <= D_SRD; end
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
