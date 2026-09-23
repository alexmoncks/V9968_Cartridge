# geo3d: 3D geometry coprocessor prototype for MSX + V9968

![Rotating wireframe: LINE commands produced by the geo3d RTL, driven by the real Z80 demo program](docs/geo3d_demo.gif)

A small vertex transform and perspective projection unit in Verilog, with an
8-bit I/O interface for the Z80, synthesized for the same FPGA used by the
V9968 cartridge (Gowin GW2AR-LV18QN88C8/I7, Tang Nano 20K).

Personal project by Alex Moncks. Not part of the official V9968 by HRA!.

## What it does
- 3x3 matrix in Q2.14 plus translation, 16-bit signed vertices (3 parallel multipliers)
- Perspective projection: SX = CX + X*F/Z, SY = CY - Y*F/Z (16-step sequential divider)
- Near-plane rejection, saturation flags and screen outcodes (Cohen-Sutherland)
- Z80 interface: 2 I/O ports (VDP-style index/data). Streaming mode: OTIR of
  6 bytes per vertex, INIR of 7 bytes per result, no index repositioning

### Register map (byte index, little-endian 16-bit words)
| Index | Register |
|---|---|
| 0x00-0x11 | M00..M22 (Q2.14) |
| 0x12-0x17 | TX, TY, TZ |
| 0x18 / 0x1A / 0x1C | F (focal length), CX, CY |
| 0x1E / 0x20 / 0x22 | ZNEAR, W, H |
| 0x24 / 0x26 / 0x28 | VX, VY, VZ (writing 0x29 starts the computation) |
| 0x30-0x36 (read) | SX, SY, Z, FLAGS |

FLAGS: bit0 NEAR, bit1 OVFX, bit2 OVFY, bit3 BUSY, bit4 SX<0, bit5 SX>=W,
bit6 SY<0, bit7 SY>=H. Port 0 read returns FLAGS without side effects.

## Verification
4,000 random vectors (including stress, near-plane and saturation cases),
compared bit-exact against the Python reference model: 0 errors.
Latency: up to 49 clocks per vertex (measured in the testbench).

## Resource usage (Yosys + nextpnr-himbaechel, GW2AR-18)
| Resource | geo3d | Device |
|---|---|---|
| LUT4 | 761 | 20,736 |
| ALU | 521 | |
| Flip-flops | 709 | 15,552 |
| MULT18X18 | 4 | 48 |
| BSRAM | 0 | 46 |
| PLL | 0 | 2 |

Post-route Fmax (nextpnr): about 143 MHz (targets tested: 42.95 and 85.91 MHz, both PASS).

Note: the V9968 cartridge report comes from Gowin EDA, while these numbers come
from Yosys/nextpnr, which count resources and model timing differently. The
definitive figure needs integration into the cartridge project and a Gowin EDA build.

## Phase 2: geo3d drives the V9968 command engine (branch `geo3d-phase2`)

The Z80 uploads the model once. Every frame it only sends **30 bytes**
(page offset, one precomputed matrix, RUN) and geo3d does the rest:
transform all vertices, clip every edge to the screen, and write R#36..R#46
LINE commands straight into the V9968 command engine whenever CE = 0.

| Per frame, cube + octahedron (14 vertices, 24 edges) | Phase 1 (Z80 drives VDP) | Phase 2 |
|---|---|---|
| Z80 I/O to geo3d | 182 (14 x 13) | 30 |
| Z80 writes to VDP command registers | ~312 (24 LINEs) | 0 |
| Z80 arithmetic (clipping, LINE setup) | yes | none |
| Grows with model size | yes | no (up to 255 vertices / 255 edges) |

### How it works
1. Transform phase: each vertex in vertex RAM goes through geo3d_core.
2. Edge phase, per edge: skip if an endpoint is behind the near plane or saturated;
   cull if trivially outside; otherwise clip to [0,W) x [0,H) with midpoint
   bisection (adders only, no divider) and build the LINE command.
3. The issuer sends the 11 command bytes when the VDP is idle, while the clipper
   already works on the next edge.

### Register window additions
| Index | Register |
|---|---|
| 0x40 / 0x41 | VADDR / EADDR (model RAM write pointers) |
| 0x42 / 0x43 | NVERT / NEDGE (0..255) |
| 0x44 / 0x45 | COLOR (R#44) / LOP (low nibble of R#46) |
| 0x46-0x47 | YPAGE, added to every DY (draw into the back page) |
| 0x48 | write bit0 = RUN, read = status |
| 0x4A-0x4F | SKIPPED, DRAWN, CULLED counters (16-bit, read) |
| 0x50 | vertex stream, 6 bytes per vertex (index does not move) |
| 0x51 | edge stream, 2 bytes per edge (index does not move) |

Status (index port read, no side effects): bit0 RUN busy, bit1 transform phase,
bit2 edge phase, bit3 core busy.

While RUN busy = 1 the Z80 must not write R#32..R#46 nor geo3d configuration.
Clearing the back page (HMMV) and flipping pages (R#2) stay with the Z80.

### Ports on the cartridge
geo3d answers at offsets 5 and 7 of the slot's 8-port block: **8Dh** (index/status)
and **8Fh** (data) with the DIP switch at 88h, or 9Dh / 9Fh at 98h.
Offset 6 (8Eh) is left free on purpose because the MegaRAM uses it.

### Integration into the cartridge (`integration/apply_geo3d_patch.py`)
- `src/v9968/vdp.v`: 4 new ports; the external command-register write is OR-ed
  into `vdp_command`, and CE is exported. With the port idle the VDP is unchanged.
- `src/tangnano20k_vdp_cartridge.v`: instantiates `geo3d_bus`, keeps offsets 5/7
  away from the VDP, muxes read data and ready.
- `tangnano20k_vdp_cartridge.gprj`: adds the three geo3d RTL files.
The script is idempotent and byte-preserving (Shift-JIS comments, CRLF).

### Verification
- 24 random scenes / 75 frames (cubes, spheres, random meshes, far and near
  cameras, 4 screen sizes, page offsets, logical ops): 2,792 LINE commands
  bit-exact against the Python reference model, 0 writes while CE = 1.
- The real Z80 demo (`z80/geo3d_demo.asm`) executed in a Z80 emulator for 130 frames
  (across the loop point); its captured port traffic replayed on the RTL:
  3,120 LINE commands, identical to the model.
  `docs/geo3d_demo.gif` and `docs/demo_frames.png` rasterise those commands with the
  same LINE stepping as `vdp_command.v` (`sim/render_video.py`, which also writes an MP4).
- Phase 1 regression (4,000 vectors) passes on the engine's immediate mode.

### Resources and timing (Yosys / nextpnr, GW2AR-18)
| | Engine alone | Cartridge original | Cartridge + geo3d | Delta |
|---|---|---|---|---|
| LUT | 2,780 (P&R) | 8,750 | 10,805 | +2,055 |
| ALU | 1,024 (P&R) | 892 | 1,729 | +837 |
| FF | 1,727 | 4,941 | 6,653 | +1,712 |
| BSRAM | 4 | | | +4 |
| MULT18X18 | 4 | 1 | 5 | +4 |

Cartridge columns: full project synthesised twice with Yosys, the encrypted
Gowin DVI IP replaced by a black box in both. Engine P&R: Fmax 150 MHz, passes at
85.91 MHz (V9968 clock). Yosys maps less densely than Gowin EDA (8,750 vs 6,009
LUT for the original), so the Gowin figure should be lower; a Gowin EDA build
is still needed for the real CLS utilisation, which may approach 70%.

### Known limits (next steps)
- Edges crossing the near plane are skipped (needs 3D clipping before projection).
- W <= 512, H <= 1024.
- Not yet run on hardware or on openMSX.

### Z80 demo
`z80/geo3d_demo.asm` (MSX-DOS .COM, GNU z80asm): SCREEN 5 on the cartridge VDP,
double buffered, 128 precomputed matrices forming one full turn (frame 128 equals
frame 0, so the spin loops forever without a jump); the Z80 does no arithmetic.
Regenerate the table with a different motion in `z80/gen_tables.py`.

## Layout
- rtl/geo3d_core.v    compute core
- rtl/geo3d_z80if.v   Z80 interface (synthesis top)
- sim/gen_vectors.py  reference model and vector generator
- sim/tb_geo3d.v      phase 1 testbench
- syn/                synthesis and P&R reports, test pin constraints
- rtl/geo3d_engine.v  phase 2 renderer (model RAMs, clipper, command issuer)
- rtl/geo3d_bus.v     adapter to the cartridge's msx_slot bus
- sim/gen_scenes.py   phase 2 reference model and scene generator
- sim/tb_engine.v     phase 2 testbench with a V9968 command-engine model
- z80/                demo program, table generator, Z80-emulator runner
- sim/render_video.py rasterises logged LINE commands into MP4 / GIF
- integration/        patch that wires geo3d into the cartridge project
- run_all.sh          reproduces everything (iverilog, python3, z80asm, pip: yowasp-yosys, yowasp-nextpnr-himbaechel-gowin, z80)

Source comments are in Portuguese; English translation will follow.

## License
MIT. Copyright (c) 2026 Alex Moncks.
