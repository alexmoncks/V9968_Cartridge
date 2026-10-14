# geo3d v2.0 (development branch)

Status: branch `geo3d-v2.0`, started 2026-10-13 from v1.0.0 (tag
`geo3d-v1.0.0`, the version in HRA!'s official V9968 distribution). Verified
in simulation and built with Gowin EDA for the Tang Nano 20K; **not run on
hardware and not proposed to HRA! yet**. The two emulators have local
branches with it (section 7). The
official firmware is still v1.0.0.

v2.0 does three things:

1. **1023 vertices, 1023 edges and 1023 faces** resident in geo3d (v1.0.0:
   255 of each).
2. **The same pictures in less time**, in the engine itself and through new
   registers that let a RUN work on a part of the model only.
3. **Nothing changes for a v1.0.0 program**: it sends the same bytes and the
   V9968 gets the same commands.

## 1. What a v1.0.0 program sees

Nothing new. Every register and stream of v1.0.0 keeps its index, its format
and its reset value; the new registers reset to values that make v2.0 behave
as v1.0.0 and live at indices v1.0.0 did not decode. What differs:

| | v1.0.0 | v2.0 |
|---|---|---|
| Index 0x49, read | 0xFF | 0x20: the version |
| Indices 0x54-0x57, 0x66-0x6F | ignored (read 0xFF) | the new registers |
| Time a RUN takes | | less (same commands, in the same order) |

A program finds out with `OUT (index), 0x49` / `IN (data)`: 0xFF is v1.0.0,
0x20 is v2.0. (0x11 is the v1.1-dev diagnostic bitstream with the
performance panel, which is not part of v2.0.)

## 2. New registers

All of them reset to 0.

| Index | Register | |
|---|---|---|
| 0x49 | ID (read) | 0x20 |
| 0x54-0x55 | VFIRST | first vertex of the transform phase: a RUN transforms NVERT vertices from VFIRST on |
| 0x56-0x57 | FFIRST | first face of the face phase: NFACE faces from FFIRST on |
| 0x66-0x67 | EFIRST | first edge of the edge phase: NEDGE edges from EFIRST on |
| 0x68 | VADDR_H | bits 9-8 of VADDR (0x40) |
| 0x69 | EADDR_H | bits 9-8 of EADDR (0x41) |
| 0x6A | NVERT_H | bits 9-8 of NVERT (0x42) |
| 0x6B | NEDGE_H | bits 9-8 of NEDGE (0x43) |
| 0x6C | FADDR_H | bits 9-8 of FADDR (0x58) |
| 0x6D | NFACE_H | bits 9-8 of NFACE (0x59) |
| 0x6E | TADDR_H | bits 9-8 of TADDR (0x65) |
| 0x6F | XCTRL | bit 0: extended mode. Read, bits 7-4: the address bits of the model RAMs (10: 1024 entries each) |

- Every one can be read back. The low registers read their low byte, as in
  v1.0.0.
- XCTRL bits 7-4 tell a program how much the build holds: the engine has one
  parameter, `AW`, the address bits of its six RAMs. 10 is v2.0 as described
  here. A build with `AW = 9` has 512 entries of each kind and counts in 9
  bits (entry n and entry n + 512 are the same); see section 4.
- The write index auto-increments inside 0x54-0x57 and inside 0x68-0x6F (and
  0x66-0x67 belong to the block 0x60-0x67, which already did).
- The three "first" registers go around the end of their RAM: with
  FFIRST = 1020 and NFACE = 10 the faces are 1020 to 1023, then 0 to 5.
- A face keeps its number wherever the phase starts: the number breaks depth
  ties (the lower one is painted first) and picks its texture coordinates.
- The projection of a vertex outlives the RUN, as in v1.0.0: a RUN with
  NVERT = 0 paints with what earlier RUNs left.

### Extended mode (XCTRL bit 0 = 1)

| | v1.0.0 mode (bit 0 = 0) | extended mode |
|---|---|---|
| Write pointers after an entry | the low byte adds 1 and wraps at 256; the high bits stay as written | the 10 bits add 1 (1023 -> 0) |
| Edge stream (0x51) | A, B: 2 bytes | A, B (low bytes), H: 3 bytes. H: bits 1-0 = A's high bits, bits 3-2 = B's |
| Face stream (0x52) | I0 I1 I2 I3, NX NY NZ, BASE: 11 bytes | I0 I1 I2 I3 (low bytes), H, NX NY NZ, BASE: 12 bytes. H: bits 1-0 = I0's high bits, 3-2 = I1's, 5-4 = I2's, 7-6 = I3's |
| Vertex stream (0x50), texture coordinates (0x53) | 6 and 8 bytes | the same |

The counts (NVERT, NEDGE, NFACE), the write pointers and the "first"
registers have their 10 bits in both modes: the mode only says how the
pointers count and how wide the indices in the streams are. A model of up to
256 vertices can stay in the v1.0.0 formats and still use VFIRST and FFIRST.

### Z80: a model of more than 255 vertices

```
        ld a, 0x6F : out (GEO_IDX), a
        ld a, 1    : out (GEO_DAT), a       ; XCTRL: extended mode
        ld a, 0x40 : out (GEO_IDX), a
        xor a      : out (GEO_DAT), a       ; VADDR = 0 ...
        ld a, 0x68 : out (GEO_IDX), a
        xor a      : out (GEO_DAT), a       ; ... its high bits too
        ld a, 0x50 : out (GEO_IDX), a       ; the vertex stream: 6 bytes each,
        ...                                 ; as many as there are (OUTI)
        ld a, 0x42 : out (GEO_IDX), a
        ld a, 600 & 255 : out (GEO_DAT), a  ; NVERT = 600
        ld a, 0x6A : out (GEO_IDX), a
        ld a, 600 >> 8  : out (GEO_DAT), a
```

### Z80: one part of a resident scene

v1.0.0 (every RUN goes over the vertices from 0 to the end of the part):

```
        ld a, 0x42 : out (GEO_IDX), a
        ld a, PART_END : out (GEO_DAT), a   ; NVERT
        ...                                 ; matrix, RUN
```

v2.0 (the part's own vertices only):

```
        ld a, 0x54 : out (GEO_IDX), a
        ld a, PART_FIRST : out (GEO_DAT), a ; VFIRST (low byte; the index
        xor a      : out (GEO_DAT), a       ;  goes on to its high bits)
        ld a, 0x42 : out (GEO_IDX), a
        ld a, PART_SIZE : out (GEO_DAT), a  ; NVERT
        ...                                 ; matrix, RUN
```

And the RUN that paints takes its faces with FFIRST and NFACE, so the RUN
that hid the scenery's vertices (VECTOR RAID's engine.md 6.3) is not needed.

## 3. Speed

What changed in the engine (the pictures do not: every check below compares
the commands byte for byte):

| | v1.0.0 | v2.0 |
|---|---|---|
| Painter's order | for each face painted, a scan of all the visible ones: 3 clocks x n x n | a heap: a face goes in when it is classified and the next one comes out in about 4 clocks x log2(n) |
| Divider of the span set-up (edge crossings, texture steps) | 1 bit a clock: 18 clocks | 2 bits a clock: 9 clocks |
| The 4 projected vertices of a face | 12 clocks | 5 clocks |
| A RUN's work | always from vertex 0, face 0, edge 0 | from VFIRST, FFIRST, EFIRST |

| Frame | RTL | clocks | ms | max ms |
|---|---|---|---|---|
| Resident scene, v1 traffic | v1.0.0 | 370,278 | 8.62 | 8.76 |
| Resident scene, v1 traffic | v2.0 | 324,521 | 7.55 | 7.66 |
| Resident scene, v2 traffic | v2.0 | 178,028 | **4.14** | 4.25 |
| Random textured scenes | v1.0.0 | 266,564 | 6.21 | 52.21 |
| Random textured scenes | v2.0 | 242,703 | 5.65 | 49.57 |
| Random face scenes (large faces) | v1.0.0 | 363,570 | 8.46 | 130.35 |
| Random face scenes | v2.0 | 351,069 | 8.17 | 128.30 |
| 1023 faces, all visible | v2.0 | 344,497 | 8.02 | 8.33 |

The numbers are engine clocks (42.95 MHz) with RUN busy = 1 in a frame, in
`sim/tb_engine.v` with a model of the V9968's command engine in high-speed
mode (LINE 7.0 and LRMM 11.6 clocks of 85.9 MHz a pixel, the figures measured
on HRA!'s `vdp_command.v` for the README). They are simulation, not
hardware.

- The resident scene is GEO FIGHTER's frame: 253 vertices, 200 faces, two
  fighters of 11 rigid parts each and the ring. "v1 traffic" is what the
  game sends today; "v2 traffic" uses VFIRST and FFIRST and paints the same
  picture (the two logs are compared).
- 1023 visible faces: the selection of v1.0.0 would take 3 x 1023 x 1023 =
  3.1 million clocks, 73 ms, for the order alone. The whole v2.0 frame,
  painting included, is in the table.
- After v2.0 most of a frame is the V9968 drawing the spans: the time left
  in geo3d is small next to it, so a bigger gain now has to come from the
  V9968's own pixel rate.

What was left out on purpose:

- `geo3d_core.v` (the vertex transform, up to 49 clocks a vertex) is
  untouched: it is a phase 1 file. Running its two divisions side by side
  would bring a vertex to about 27 clocks (an estimate, not built).
- The edges of a face are still walked with one division per edge and row.
  Stepping them incrementally would shorten the span set-up further (an
  estimate: to about a quarter), which only shows on faces a few pixels
  wide (the V9968 is the limit on the others); it is a larger change to
  the fill.

## 4. FPGA (Tang Nano 20K, GW2AR-18)

| | Official (geo3d v1.0.0) | v2.0, 1024 entries | v2.0 with `AW = 9`, 512 entries |
|---|---|---|---|
| Logic (LUT + ALU) | 13,130 (64%) | 13,867 (67%) | 13,779 (67%) |
| Registers | 7,863 (50%) | 8,004 (51%) | 7,980 (51%) |
| CLS | 9,137 (89%) | 9,282 (90%) | 9,259 (90%) |
| BSRAM | 22 of 46 | 31 of 46 | 22 of 46 |
| DSP | 9 of 24 | 9 of 24 | 9 of 24 |
| geo3d engine alone: registers / ALU / LUT / BSRAM | 2,698 / 1,097 / 3,443 / 12 | 2,839 / 1,165 / 4,081 / 21 | |
| Engine clock (42.955 MHz needed) | 52.2 MHz | 54.4 MHz | 56.0 MHz |
| Worst setup slack inside geo3d | +4.11 ns | +4.88 ns | +5.41 ns |
| Worst setup slack on clk85m (the V9968) | +0.071 ns | **+0.382 ns** | +0.005 ns |
| Negative-slack paths, every clock pair, setup / hold / recovery / removal | 0 | 0 | 0 |
| Place and route | HRA!'s settings | Route Option 2, geo3d's RAMs placed | HRA!'s settings |

Built with `syn/gowin/build_v2.sh`: HRA!'s official project
(`fpga/V9968_Cartridge_TangNano20K`, his sources, his constraints) on a
copy outside the repository, with `src/geo3d/` replaced by `geo3d/rtl`. His
folder is not touched. "Official" is the same flow on his files as they
are: it gives his own figures.

The six model RAMs go from 256 to 1024 entries, 79 kbit to 331 kbit, and
from 12 block RAMs to 21. **Those 9 blocks, not the logic, are what the
V9968 feels.** With HRA!'s settings and nothing else, the placer spreads
geo3d's RAMs over the three BSRAM rows of the chip, his sprite divide table
moves from `BSRAM_R46[9]` to the far end of row R28, the sprite logic
follows it away from the screen position counter it reads, and his worst
path fails:

| v2.0, 1024 entries | Worst setup slack on clk85m | Negative paths |
|---|---|---|
| HRA!'s settings, nothing else | -1.288 ns | 12 |
| Route Option 1 | -0.790 ns | 8 |
| Route Option 2 | -0.779 ns | 14 |
| Place Option 1, Route Option 2 | -0.972 ns | 29 |
| geo3d's RAMs placed: row R10 and the left of R28; HRA!'s settings | -0.570 ns | 20 |
| ... the same, Route Option 2 | -0.035 ns | 2 |
| geo3d's RAMs placed: row R10, the left of R28 and of R46; Route Option 2 | +0.061 ns | 0 |
| geo3d's RAMs placed: row R10 and the left of R46; Route Option 2 | **+0.382 ns** | 0 |

(The first four lines are from the RTL before its last change, the read
value of XCTRL bits 7-4; the other four from the final RTL. Place Option 3
and 4 give the result of Place Option 0: this Gowin version does not seem
to have them.) The last line is the build of the
table above: `syn/gowin/geo3d_v2_bsram.cst` gives each of geo3d's 21 RAMs
its place, 15 in row R10 and 6 at the left end of row R46, so that row R28
and the rest of R46 stay with the V9968 as in HRA!'s own build. The file
names geo3d's instances only and is added to the copy's constraints:

```
WORK=<a folder outside the repository> CST=geo3d/syn/gowin/geo3d_v2_bsram.cst \
  OPTIONS="Route_Option=2" sh geo3d/syn/gowin/build_v2.sh v2
```

Two things were stopped before they finished (the machine ran out of
memory with several builds at once) and are still to be run: the adopted
placement with HRA!'s own Route Option 0, and with Route Option 1.

With `AW = 9` nothing of this is needed: the 512-entry RAMs take the same 12
blocks as v1.0.0 and the build closes with HRA!'s settings as they are
(+0.005 ns, and +0.140 ns with Route Option 2). Those two builds were made
before the last change to the RTL (the read value of XCTRL bits 7-4) and a
512-entry engine has only been built, not simulated: the model and the
v2.0 scenes are for 1024 entries.

The margins on clk85m belong to HRA!'s paths and move with every change to
the netlist: the same placement gave +0.029 ns before that last change of
four read bits, and -0.035 ns after it. A release has to be built and
checked again, whatever it contains.

## 5. Verification

`sh run_v2.sh` (iverilog, python3):

1. the v2.0 model (`sim/gen_scenes.py`) gives the logs of the v1.0.0 model
   (the tag's) for v1.0.0's stimuli: random scenes of edges, faces and
   textures, and the traffic of the three Z80 demo programs;
2. the v2.0 RTL gives those logs too, through the engine alone and through
   the dual-clock bus bridge;
3. phase 1 (the immediate mode, 4,000 vectors) on the v2.0 engine;
4. `sim/gen_v2_scenes.py`, what v2.0 adds: models of up to 1023 vertices,
   edges and faces in the extended formats, textured or not, faces as far as
   one another, all the faces visible at once, VFIRST / EFIRST / FFIRST
   (also around the end of the RAMs), a resident scene moved part by part,
   the write pointers in both modes, v1.0.0 streams with 10-bit counts,
   every new register read back: RTL == model;
5. the benchmark above.

Results (2026-10-13): the six v1.0.0 logs are the same in the v2.0 model
and from the v2.0 RTL (2,942 + 52,250 + 27,621 + 3,380 + 133,969 + 133,969
lines); phase 1: 4,000 vectors, 0 errors; the v2.0 scenes: 72 frames,
148,005 Z80 writes, 77,125 commands (62,974 LINE and 14,151 LRMM) and 121
register reads, identical to the model through the engine and through the
bus bridge; 0 writes while CE = 1 in every run.

The v2.0 scenes were also run against ten deliberately broken engines (the
tie rule of the heap, the choice between two children, the write pointers
always 10 bits or always 8, VFIRST, EFIRST or FFIRST ignored, a wrong
quotient bit, the high bits of the face or of the edge indices dropped):
each of the ten gives a different log.

(Checks 1 to 4 were repeated on the final RTL. The benchmark and the ten
broken engines were run on the RTL as it was before changes that do not
touch what they measure: for the benchmark, the read value of XCTRL bits
7-4; for the broken engines, that, the depth parameter `AW` and the unused
flag bits dropped from the projected-vertex RAM.)

## 6. Not done yet

- **Hardware.** Nothing of v2.0 has run on a cartridge.
- **Emulators.** Both have a local `geo3d-v2.0` branch with the device's
  core brought to v2.0 (section 7); no v2.0 program has run in them yet.
- **Tools.** `tools/glb2g3.py`, G3BASIC and the demo programs still stop at
  255 vertices and faces.
- **README.md, run_all.sh.** Not changed on this branch (the README still
  describes v1.0.0). `run_all.sh` runs as before with the v2.0 RTL and
  model.
- **The bench Gowin project** (`fpga/V9968_Cartridge_TangNano20K_geo3d`) is
  still the v1.0.0 one: its generator refuses HRA!'s folder since that
  folder carries geo3d. `build_v2.sh` builds from his official project
  instead.
- **Release.** By the policy in the README a new geo3d version is proposed
  to HRA!, who decides; v2.0 has not been.

## 7. Emulators

geo3d is emulated by a core with no dependency on its emulator: `Geo3DCore`,
a port of the reference model, written for the openMSX fork and ported to
blueMSX+ by Hesoten, who added the engine's time to it. Both have a local
branch `geo3d-v2.0` with that core brought to v2.0:

| Emulator | Branch, from | What changed |
|---|---|---|
| openMSX fork (alexmoncks/openMSX) | `geo3d-v2.0`, from `geo3d` | `src/video/Geo3DCore.*`: the v2.0 registers, streams and RAMs; `tools/geo3d_replay.cc` reads registers too. The geometry takes no emulated time there, so the faster engine changes nothing |
| blueMSX+ (Hesoten/blueMSX-plus, a local clone) | `geo3d-v2.0`, from `experimental/v9968-geo3d` | `Geo3DCore.*` likewise, and its engine clock recounted for v2.0: the heap of visible faces, the divider, the vertices read back to back. `GEO3D_V2.md` there has the details. Not Hesoten's work and not sent to him |

Checked on the cores alone (`tools/geo3d_replay` of the fork;
`sim/blue_replay.cc` here for the blueMSX+ one), both built with g++:

- the command logs of the reference model, byte for byte: the six v1.0.0
  sets (354,131 lines), the v2.0 scenes (77,390 lines, registers read back)
  and the benchmark's three logs (44,185 lines);
- for blueMSX+, the engine clock of every command and of every RUN end
  against the RTL in simulation: `tb_engine.v +ec=<file>` writes the
  producer's clock (the clocks since RUN it does not spend stalled on the
  holding register) and `sim/check_ec.py` compares it with the core's. The
  difference is one constant, -4, on 191,959 commands and 674 RUN ends of
  v1.0.0's random sets, the v2.0 scenes and the v2.0 benchmarks, and the
  same -4 for the v1.0.0 core against the v1.0.0 RTL.

That comparison found one difference that v1.0.0's core has too: a RUN in
face mode with NFACE = 0 ends one engine clock earlier in the RTL (F_START
goes straight to R_DRAIN). The v2.0 branch of blueMSX+ counts it as the RTL
does.

openMSX was built from its branch (Linux): in a running `C-BIOS_V9968_JP`
machine geo3d reads ID 20h and XCTRL A0h, a 10-bit NVERT and VFIRST read
back, and the fork's smoke test (a v1.0.0 RUN) gives its expected values.

Not done: no v2.0 program has run in either emulator (none exists yet); the
Windows build of openMSX has not been made from the branch; blueMSX+ needs
MSVC 2022 to build and has not been built or run with its branch, and its
`Geo3DDevice.c` (one constant changed) was not compiled.
