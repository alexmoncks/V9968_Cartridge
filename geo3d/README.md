# geo3d: 3D geometry coprocessor prototype for MSX + V9968

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

## Layout
- rtl/geo3d_core.v    compute core
- rtl/geo3d_z80if.v   Z80 interface (synthesis top)
- sim/gen_vectors.py  reference model and vector generator
- sim/tb_geo3d.v      testbench
- syn/                synthesis and P&R reports, test pin constraints
- run_all.sh          reproduces everything (iverilog, python3, yowasp-yosys, yowasp-nextpnr-himbaechel-gowin)

Source comments are in Portuguese; English translation will follow.

## License
MIT. Copyright (c) 2026 Alex Moncks.
