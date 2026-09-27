[Português](cartridge_report.pt.md) | [日本語](cartridge_report.ja.md)

# geo3d on the standard V9968 cartridge: verification report

Date: 2026-09-27. Base: HRA!'s cartridge project (Tang Nano 20K, Gowin GW2AR-18C FPGA), version 86361d8, with geo3d integrated by the script `geo3d/integration/apply_geo3d_patch.py`. Branch `geo3d-phase2`. Nothing from this stage has been committed yet (section 12).

## 1. Summary

| Question | Answer |
|---|---|
| Does the physical board need changes? | **No.** No component, trace, pin, crystal or jumper. |
| Does HRA!'s original project change? | **Yes, only in the FPGA, in 5 files.** Everything is done by script; the original can be recovered. |
| Does it fit in the cartridge's FPGA? | **Yes.** Official Gowin tools: 62% of the logic, 86% of the slices, 48% of the memory, 38% of the DSPs, 6 of 8 primary clocks. |
| Does geo3d have its own clock? | **Yes.** clk42g (42.95 MHz) comes from the same PLL as clk85m. HRA!'s clk42m and the HDMI stay as they were. |
| Does the logic work? | **Yes, in simulation.** HRA!'s whole board was simulated with geo3d and the new clock. VRAM matched the reference in every case. |
| Does timing close? | **Yes, in the Gowin model.** Zero paths with negative slack for setup, hold, recovery and removal, on every clock pair, with the settings of HRA!'s own project. Some slacks are tight (section 6). |
| Does the demo ROM detect geo3d? | **Yes.** Without it, the ROM shows "geo3d not found" in three languages and stays there, instead of a black screen. |
| Can it be flashed to the cartridge now? | **Yes.** The bitstream `geo3d_cartridge_86361d8.fs` and the `FLASH.txt` instructions (English, Portuguese and Japanese) are ready. Flash with the cartridge out of the MSX. |
| Tested on the real cartridge? | **No.** Nothing here replaces a hardware test (sections 10 and 11). |

## 2. What was checked and how

| Item | Method | Result |
|---|---|---|
| Board and slot | Schematics and netlist in `pcb/`, `msx_slot.v` RTL, simulation of Z80 I/O cycles | Works without modification |
| Integration script | Clean 86361d8 plus the script, compared with the repository's `fpga/` tree; a second run; 24 mutation and upgrade tests | Identical tree (apart from Windows CRLF line endings); the second run changes nothing; 24 of 24 tests pass |
| Utilisation and timing | Gowin EDA Standard V1.9.12.03 (Alex's license), full build with HRA!'s constraints and the geo3d ones, with the project's settings (Place Option 0). Setup and hold path tables for each clock pair | Fits; zero violations |
| Open tools | Yosys + nextpnr-himbaechel (oss-cad-suite) | The original routes; with geo3d it does not place (98.6%). Not a valid reference for this chip |
| Integrated logic | iverilog: HRA!'s full top, VDP, slot, SDRAM controller and Micron model, plus geo3d, with clk42g. Also with clk42g delayed by 0.5 ns and 2.0 ns | Passed everything |
| VDP equivalence | Formal proof (Yosys) and side-by-side simulation | Modified VDP identical to the original with geo3d idle |
| Demo ROM | Z80 simulator `geo3d/rom/run_rom_z80.py`, with and without geo3d, 88h and 98h | 256 of 256 runs pass |
| Software without geo3d | openMSX without geo3d: 88h and 98h, MSX1, MSX2 PAL, MSX2+, no cartridge | BASIC ROM and game correct; demo ROM shows "geo3d not found" |

## 3. Board (hardware)

**Conclusion: the standard cartridge supports geo3d with no modification.** The clock fix (section 4) is in the FPGA only.

- **Address:** A0-A7 reach the FPGA through U5 (SN74LVC8T245, DIR tied to GND, input only). A8-A15, /SLTSL, /MERQ and /M1 do not reach it: the cartridge is I/O only, and the ROMs sit in another slot.
- **Control:** /IORQ, /RD, /WR and /RESET through U6 (DIR tied to GND).
- **Data:** U4 has its direction driven by the FPGA (`SLOT_DATA_DIR`, with a 10k pull-down on R2).
- **/BUSDIR:** driven by Q1 (open-drain NMOS), with its gate on the same `SLOT_DATA_DIR`. It stays low while the cartridge drives the bus, for any port.
- **/WAIT:** through Q2, only during startup (until the FPGA is configured and the SDRAM is initialised). geo3d does not use /WAIT.
- **Decoding:** the original `msx_slot.v` already decodes the whole block of 8 ports (88h-8Fh or 98h-9Fh, set by the DIP switch). The VDP uses +0 to +4, and geo3d uses +5 (index and status) and +7 (data). +6 (8Eh) is left out on purpose.
- **Reads:** geo3d reads go out through the same path as the VDP status reads, with the same U4 turnaround and the same /BUSDIR.
- **Simulated response time:** geo3d puts the data on the bus 116 to 140 ns after /RD (139.7 ns in the simulation with clk42g). A Z80 at 3.58 MHz samples about 500 ns later, so there is a wide margin, and about 220 ns are still left at 7.16 MHz. The level shifter delay is not included in this figure.

**Points to check on your own equipment:**
- **MegaRAM:** according to the msx.org port map (not rechecked), 8Eh-8Fh are used by MegaRAM. A MegaRAM that decodes only part of the address may react to geo3d reads at 8Fh.
- **Crystal:** the board's parts list (`pcb/.../parts.txt`) gives 28.636 MHz for U1, but the project's PLLs expect 14.318 MHz. It is worth checking which one is fitted. This applies equally to HRA!'s original bitstream.
- **Power:** the Tang Nano's 5 V pin is connected to the slot's +5 V. **Flash the bitstream with the cartridge out of the MSX.**

## 4. What changes in HRA!'s project

Everything is applied by `apply_geo3d_patch.py`. It is idempotent and fails if HRA!'s text changes. On every run, even on a tree that is already modified (for example after a merge from HRA!), it checks the rPLL2 settings, the `u_pll2` connections (input clk14m, output clk85m) and the SDC clocks and margins. If any check fails, it writes nothing. Applied to a clean 86361d8, it reproduces the repository's `fpga/` tree exactly (checked again on this date).

| HRA! file | Change (lines, against 86361d8) | Effect |
|---|---|---|
| `src/v9968/vdp.v` | +11 / -3: an external write port to the command registers (`ext_cmd_wr/num/data`) added to the CPU one, and the CE output | With geo3d idle, the VDP is **identical** to the original: formal proof (695 of 695 points; 697 of 697 on 86361d8) and a side-by-side simulation of 3.6 million cycles with every pin equal |
| `src/tangnano20k_vdp_cartridge.v` (top) | +44 / -4: instantiates `geo3d_bus` on ports +5 and +7, removes those ports from the VDP, merges the read data, creates the `clk42g` wire, connects `u_pll2 .clkoutd` to it and drives geo3d's `clk_eng` from clk42g | The VDP still answers on +0 to +4, and +6 still goes to the VDP |
| `tangnano20k_vdp_cartridge.gprj` | +3: the geo3d files | None |
| `src/gowin_rpll2/gowin_rpll2.v` | +3 / -3: exposes the `clkoutd` output. It was already configured in the PLL (CLKOUT / 2, `DYN_SDIV_SEL = 2`) and was unused | None for HRA!: CLKOUT and CLKOUTP do not change |
| `src/tangnano20k_vdp_cartridge.sdc` | +7: `create_generated_clock` for clk42g (clk14m x 3, as clk85m is clk14m x 6) and `set_clock_uncertainty 0.5` in both directions between clk85m and clk42g, with comments | Timing analysis only |

**Unchanged:** `msx_slot.v`, the pin file (`.cst`), the HDMI, the SDRAM, HRA!'s clk42m (it still comes from the CLKDIV divider and feeds the HDMI and the rest of his design), the clocks and clock groups in HRA!'s SDC, and the `impl/` folder, which holds HRA!'s original bitstream for recovery.

**Caution:** the `.ipc` and `.mod` files of the rPLL2 IP were not changed. If someone regenerates this IP in Gowin, the `clkoutd` port disappears and synthesis stops with an error. In that case, run the script again.

**geo3d files changed in this stage:**

| File | Change |
|---|---|
| `geo3d/integration/apply_geo3d_patch.py` | The two new steps (rPLL2 and SDC), clk42g in the top and the checks described above |
| `geo3d/syn/gowin/build_gowin.sh` (new, not yet in git) | Gowin build on a copy outside the repository, with the project's settings. It adds the path tables to the copy's SDC only and exits with code 2 if any slack is negative |
| `geo3d/rtl/geo3d_bus.v` | Header comment only (clk42g and the SDC margin). No logic changed |
| `geo3d/rom/geo3d_rom.asm`, `build_rom.py`, `run_rom_z80.py` | geo3d detection in the demo ROM and new simulator modes (section 8) |
| `geo3d/docs/BASIC_API.md` | R#32-R#58 rule (section 9) |

## 5. FPGA utilisation (Gowin, GW2AR-LV18QN88C8/I7)

Builds on the 86361d8 base, Gowin V1.9.12.03, HRA!'s project settings. Summaries in `openmsx-geo3d\fpga\reports\` (delivery folder, outside the repository).

| Resource | HRA! original | With geo3d, clk42m (before) | With geo3d, clk42g (final) | Final difference |
|---|---|---|---|---|
| Logic (LUT + ALU) | 7,023 (34%) | 12,756 (62%) | 12,756 (62%) | +5,733 |
| Registers | 4,219 (27%) | 7,536 (48%) | 7,536 (48%) | +3,317 |
| Slices (CLS) | 5,605 (55%) | 8,896 (86%) | 8,886 (86%) | +3,281 |
| Block memory (BSRAM) | 10 of 46 (22%) | 22 (48%) | 22 (48%) | +12 |
| DSP | 3 of 24 (13%) | 9 (38%) | 9 (38%) | +6 |
| I/O pins | 39 of 66 | 39 of 66 | 39 of 66 | 0 |
| Primary clocks | 4 of 8 | 5 of 8 | 6 of 8 | +2 |
| Long wires | 5 of 8 | 8 of 8 | 8 of 8 | +3 |
| PLLs (rPLL) / dividers (CLKDIV) | 2 of 2 / 1 of 8 | 2 of 2 / 1 of 8 | 2 of 2 / 1 of 8 | 0 |

- geo3d's own clock uses no logic and no PLL: it uses an output the PLL already had. It uses one primary clock network (clk42g runs in quadrants TR, TL and BL).
- Long wires are at 8 of 8: none are left for future changes.

## 6. Timing

**Result: zero paths with negative slack in every table, with the settings of HRA!'s own project (Place Option 0).** This covers setup, hold, recovery and removal on every clock pair. A build done the way the Gowin IDE does it (`open_project` and `run all`, with no options and no extra tables) produces the same bitstream as `build_gowin.sh`; only the "Created Time" line changes.

Worst slack in ns (positive = pass). In the two geo3d columns, clk42m or clk42g is the clock of the geo3d engine.

| Check (target) | HRA! original | geo3d with clk42m (before) | geo3d with clk42g (final) |
|---|---|---|---|
| Fmax clk85m (85.909 MHz) | 86.73 MHz | 86.14 MHz | 86.34 MHz |
| Fmax clk42m (42.955 MHz) | 120.92 MHz | 62.64 MHz | 117.46 MHz |
| Fmax clk42g (42.955 MHz) | n/a | n/a | 64.07 MHz |
| Setup 85 to 85 | +0.111 | +0.031 | +0.058 |
| Setup 85 to 42m (HDMI) | +1.334 | **-1.130 (50 or more failing)** | +0.489 |
| Setup 85 to 42g | n/a | n/a | +2.383 |
| Setup 42m to 85 | n/a | +12.698 | n/a |
| Setup 42g to 85 | n/a | n/a | +7.857 |
| Setup 42m to 42m | +15.010 | +7.316 | +14.767 |
| Setup 42g to 42g | n/a | n/a | +7.673 |
| Hold 85 to 85 | +0.199 | +0.227 | +0.216 |
| Hold 85 to 42m (HDMI) | +3.443 | +3.458 | +3.782 |
| Hold 85 to 42g | n/a | n/a | +0.047 |
| Hold 42m to 85 | n/a | **-2.487 (40 failing)** | n/a |
| Hold 42g to 85 | n/a | n/a | +0.039 |
| Hold 42m to 42m | +0.539 | +0.074 | +0.544 |
| Hold 42g to 42g | n/a | n/a | +0.074 |
| Recovery / removal | +10.046 / +1.047 | +9.476 / +1.200 | +9.476 / +1.185 |
| **Negative paths, all tables** | **0** | **90 or more** | **0** |

- The tables list at most 50 paths. Hence "50 or more".
- In the clk42m column, the "85 to 42m" rows also cover the geo3d engine, and the worst paths are geo3d's, not the HDMI's: the setup path goes from HRA!'s reset `ff_reset3_n2` to the engine, and the 42m to 85 hold path goes from the `cmd_*_e` registers of `geo3d_bus` to `cmd_*`.
- The 52 tables are setup and hold for the 25 pairs among clk85m, clk42m, clk42g, clk215m and clk, plus recovery and removal. In the final build, 38 of them have no paths: all the pairs with clk215m or clk (32), the pairs between clk42m and clk42g (4, since the HDMI and geo3d are not connected) and clk42m to clk85m (2).
- The slacks between clk85m and clk42g already take off the 0.5 ns margin. Without it, the holds on this crossing would be about +0.54 ns.
- **Care when reading the Gowin report:** the "Total Negative Slack" summary shows 0 even when paths between clocks fail. Only the path tables show the violations. `build_gowin.sh` generates these tables and checks all of them.

**Cause of the old violations:** clk42m comes from a CLKDIV divider, which has no delay in the Gowin model, while clk85m, which comes from the PLL, has 2.9 to 4.4 ns. The crossing between the geo3d domains assumes aligned clocks.

**Fix applied:** geo3d got clk42g, from the CLKOUTD output of the same PLL as clk85m (CLKOUT / 2). With this, the geo3d crossings show 0 ns of clock skew in the model (4.36 ns before).

**0.5 ns margin:** Gowin gives CLKOUTD exactly the delay of CLKOUT and does not model the delay of the divider itself. The SDC reserves 0.5 ns (`set_clock_uncertainty`) in both directions between clk85m and clk42g. **This value is assumed, not measured.** To test the logic, the simulation also ran with clk42g delayed by 0.5 ns and 2.0 ns (section 7).

**Intermediate builds** (summaries in `reports\compare\`):

| Build | Placement | Margin | Negative paths |
|---|---|---|---|
| clk42m (old) | Option 0 | no | 90 or more |
| clk42g | Option 0 | no | 1 (HRA!'s HDMI path, 85 to 42m, -0.059 ns) |
| clk42g | Option 1 | no | 0 |
| **clk42g (delivered)** | **Option 0 (HRA!'s default)** | **0.5 ns** | **0** |
| clk42g | Option 1 | 0.5 ns | 0 |

**Tight but positive slacks:**
- Setup 85 to 85: +0.058 ns, on a path in the VDP command engine (+0.111 ns in the original; +0.031 ns in the clk42m build). The slack changes with placement.
- Hold inside the geo3d engine (42g to 42g): +0.074 ns.
- Holds on the 85 and 42g crossing: +0.039 and +0.047 ns, with the margin already applied.
- The HDMI path (85 to 42m) varies with placement: from -0.059 to +0.962 ns in the clk42g builds above. A future RTL change can move it again. `build_gowin.sh` flags this (code 2).

## 7. Integrated RTL simulation

HRA!'s full top (clean 86361d8 plus the final script, the same Verilog as the repository) was simulated in iverilog with the VDP, the slot, the SDRAM controller and the Micron MT48LC2M32B2 model, plus geo3d, at 85.909 MHz. The stubs cover only the PLLs, the divider (which really divides by 2) and the HDMI. The PLL stub generates `clkoutd` as clk85m / 2, in phase. A checker (`clkcheck.v`) confirms that geo3d's `clk_eng` is clk42g, at exactly half the frequency, with 0 misaligned edges. The Z80 side uses real I/O cycles (/IORQ, /RD, /WR, A0-A7, D0-D7) at OTIR speed. VRAM (256 KB) starts filled with a pseudo-random pattern, to catch any lost write.

| Test | Result |
|---|---|
| Registers, status and immediate transforms | 0 errors |
| Wireframe (cube and octahedron, with XOR and with TIMP) | Identical VRAM |
| Shaded solid faces | Identical VRAM |
| Textured faces (LRMM) | Identical VRAM |
| Real texture demo (GEO3DT.COM), all OUTs replayed | 2,842 commands (1,855 LINE, 987 LRMM), 0 different, 3 identical frames |
| DIP switch at 98h | Passed |
| Startup state (V9958 mode) | Identical VRAM |
| Handshake over 39,158 geo3d writes to the VDP | 0 writes with CE = 1, 0 lost, 0 collisions |
| Deliberate conflict: the CPU writes R#32-R#58 while geo3d is busy (33 writes) | 4 collisions in the same cycle and a corrupted frame, as expected. Confirms the rule in section 9 |

- **Comparison with the old clock and with the previous delivery:** the command and read logs, the VRAM dumps and the cycle counts are byte-for-byte identical to the simulation with clk42m and to the previous delivery with clk42g. The startup state test, which had only run with clk42m before, was run again on the final tree for this revision.
- **Delayed clock:** 7 tests with clk42g delayed by 0.5 ns and 2.0 ns relative to clk85m (14 runs). In a simulation without gate delays, this makes every clk85m to clk42g capture take the value launched on the same edge. All 14 passed. VRAM matched the aligned-clock runs, except in the deliberate conflict test, where the collisions change (1 instead of 4), and the corrupted frame with them.
- **Limit:** the simulation does not model real routing delays. Those are left to the timing analysis (section 6) and the real cartridge.

## 8. Our software with HRA!'s original bitstream (no geo3d)

| Program | What happens | Status |
|---|---|---|
| BASIC ROM (G3BASIC.ROM) | Banner "geo3d BASIC 0.2 (none)"; `CALL G3INIT` gives "Device I/O error"; BASIC keeps working | Correct |
| VECTOR RAID game | Does not hang, but runs without the ship and without the enemies, with no warning | Acceptable |
| Demo ROM | Shows the "geo3d not found" screen (English, Spanish and Portuguese) and stays on it. It writes to no geo3d port | **Correct (fixed)** |

**How the demo ROM detects geo3d** (`geo_probe` in `geo3d_rom.asm`, the same method as the BASIC ROM's `detect`, only at the ROM's own base):
1. On the 98h ROM, on an MSX1 (MSXVER = 0), it does nothing: a TMS9918 would take the write to R#15 as R#7.
2. It reads the VDP ID from S#1. ID 0, 2 or 3 means a V99x8 is present, and the message picture can go to it. geo3d requires ID 2 or 3.
3. With R#15 = 2, it reads P+5. FFh (HRA!'s bitstream or an empty port) means no geo3d. Bits 3-2 = 11 means a VDP whose ports repeat at P+4 to P+7. Only then does it read PORT#4.
4. The first write to geo3d is index 40h. Then come 17 reads from P+7, and the 17th must equal the first.

The detection makes 29 port accesses. With geo3d present, the traffic after it is identical to that of the previous ROM.

**Without geo3d:**
- If a V99x8 is present at the base, a SCREEN 5 picture in the menu's style appears. It uses only registers that any V99x8 accepts: no V9968 register, no command, no write to PORT#4.
- The 88h ROM also writes the text on the MSX's own screen through the BIOS (INITXT and CHPUT, plain ASCII). This is the only message when there is no V9968 at 88h. The 98h ROM does this only on an MSX1.
- The ROM stays in a loop with interrupts disabled (`ng_stay`).
- **50/60 Hz:** the 98h ROM keeps the machine's frequency (R#9 = `(RG9SAV & 02h) | 80h`; 82h on a PAL machine). The 88h ROM uses 80h (60 Hz) for the cartridge's HDMI.

**Time limit on the waits:** the waits for geo3d's RUN and for the VDP's CE give up after about 3 s on a Z80 at 3.58 MHz (3.08 to 3.29 s in the simulator). On a turbo R, the cartridge's INIT runs on the Z80; in R800 ROM mode, each turn of 65,536 reads in the wait (about 1.6 s on a Z80) takes about 0.7 s (the reviewer's measurement, not repeated). When the time limit runs out (`hw_timeout`):
1. The music stops.
2. The command engine gets STOP (R#46 = 0).
3. The ROM waits for geo3d's RUN to end, up to 32,768 reads of P+5 (about 0.55 s), so that nothing from the frame in progress is drawn over the message.
4. A second STOP, only if RUN ended.
5. The same message.

**Z80 simulator (`run_rom_z80.py`), new ROMs:**

| Set | Runs | Result |
|---|---|---|
| Without MoonSound (PSG, SCC, OPL, OPL4; 3 languages; key scripts; space bar; 88h and 98h) | 120 | 120 pass. The trace is the 29 detection accesses followed by the reference trace, byte for byte |
| Message (`--absent ff/mirror/v9938/none`, `--stuck geo/ce/cegeo`, `--msxver`, `--pal`, MSX1, MoonSound, 88h and 98h) | 84 | 84 pass. The CPU ends in `ng_stay`, page 0, the palette and the registers match the picture, the BIOS text checks out |
| MoonSound (640, 256, 128 and 0 KB) | 52 | 52 pass. The per-port writes are detection plus reference in 45 of 52; the other 7 differ only at the end, where real time cuts the run |

- The `--stuck cegeo` mode reproduces the case of a stuck CE with geo3d waiting on it: 1,001 status reads and 2 STOPs, all before the picture. The previous ROM fails this mode and `--pal`.

**openMSX (headless, pictures generated from the VRAM, register and palette dumps):**

| Machine and cartridge | ROM | Result |
|---|---|---|
| FS-A1WSX with HRA!'s V9968 without geo3d | 88h | Picture on the V9968 (HDMI) and text on the MSX screen. Only geo3d access: one read of 8Dh (FFh) |
| C-BIOS_V9968_JP without geo3d | 98h | Picture. One read of 9Dh (FFh), no writes |
| FS-A1WSX without cartridge | 88h | BIOS text only; no access to 8Dh-8Fh |
| MSX1 (Expert XP-800) with HRA!'s V9968 | 88h | Picture on the V9968 and text on the MSX screen |
| FS-A1WSX (V9958 at 98h) | 98h | Picture on the V9958 |
| MSX1 | 98h | BIOS text only; no access to 98h-9Fh |
| Philips NMS 8245 (PAL, V9938) | 98h | Picture with R#9 = 82h (50 Hz kept) |
| Philips NMS 8245 with HRA!'s V9968 without geo3d | 88h | Picture on the V9968 with R#9 = 80h |
| With geo3d (88h and 98h) | both | Normal menu and crawl; port access counts and VRAM equal to the previous delivery |

The pictures are in `openmsx-geo3d\geo3d_not_found\` (9 files).

## 9. Rules for geo3d programmers

- While geo3d is drawing (RUN busy), nothing may write to **R#32 to R#58**. LRMM uses R#47 to R#58, and a CPU write in the same cycle as a geo3d write is lost. The deliberate conflict test in section 7 shows this.
- **CE = 0 does not mean the engine is free:** CE drops between one geo3d command and the next. Wait for bit 0 of the geo3d status.
- The LRMM window (R#51 to R#58) must cover the whole VRAM, which is the reset value.

These rules are in section 7.2 of `BASIC_API.md`. The three ROMs already follow them. The only exception is the STOP in the demo ROM's `hw_timeout`, and only in theory: geo3d does not write registers while CE is high, even when it is stuck.

## 10. What only the real cartridge can confirm

- **The real delay between CLKOUT and CLKOUTD on the chip.** The 0.5 ns margin is assumed, not measured.
- **The tight slacks in section 6** (+0.039 to +0.074 ns on the holds, +0.058 ns on setup 85 to 85): the timing analysis is a model.
- **Signal integrity:** LVC8T245 delays, noise, and a slightly later /BUSDIR on machines with a slot buffer, on expanders and on the R800.
- **Real SDRAM** under geo3d command traffic.
- **HDMI on real monitors.**
- **Power draw, heat and stability** over long use.
- **The "geo3d not found" screen on the hardware**, with HRA!'s original bitstream.

## 11. First hardware test plan

Files in the `openmsx-geo3d` delivery folder (outside the repository):

| File | Check |
|---|---|
| `fpga\geo3d_cartridge_86361d8.fs` | SHA-256 `49a3fc51e3118c597bbb31783b8ebec8a421c7457236cb4ce3b2762e7681172f` |
| `fpga\recovery\tangnano20k_vdp_cartridge_HRA_86361d8.fs` | SHA-256 `9957d2b507897370c402973c2a358adad9675c0bb7b45b38f75d1fb6a6b32b73`, equal to the repository's `impl/pnr/tangnano20k_vdp_cartridge.fs` |
| `fpga\FLASH.txt` | Instructions in English, Portuguese and Japanese |
| `GEO3D_88_hardware_real_MOD.ROM` | SHA-1 `79fbbd4f29f2f3ebffa1e9da414b7917d0ca575f` |
| `GEO3D_98.ROM` | SHA-1 `0b8ff70cd6f6ad3dbbcb194ec269987536f42987` |

Both ROMs use the Star Wars music (MOD) and do not go into git. The other ROMs in the folder are old builds, without the detection.

0. **Baseline:** with the DIP switch at 88h, HDMI connected and HRA!'s **original** bitstream flashed, run the BASIC ROM. `CALL G3INIT` should give "Device I/O error". Then run `GEO3D_88_hardware_real_MOD.ROM`: "geo3d not found" should appear on the HDMI and the text on the MSX screen. This proves that the cartridge, the 88h path and the message work.
1. **Flash the geo3d bitstream**, **with the cartridge out of the MSX** and connected to the PC through USB-C. Check the SHA-256 first.
   - Gowin Programmer: device GW2AR-18C, Access Mode = External Flash Mode, Operation = exFlash Erase,Program thru GAO-Bridge, file `geo3d_cartridge_86361d8.fs`.
   - Or: `openFPGALoader -b tangnano20k -f geo3d_cartridge_86361d8.fs`.
2. **First power-on:** unplug the USB cable, DIP switch at 88h, cartridge in the slot with the MSX off, HDMI connected, power on the MSX. It should boot normally. If it freezes without booting, /WAIT is stuck: the FPGA did not configure or the SDRAM did not initialise. Power off, take the cartridge out and flash again.
3. **BASIC ROM first**, because it is the safest: it only reads until it identifies geo3d, its waits have time limits and it accepts CTRL+STOP. The banner should show "(88h)". Then run the example: `10 SCREEN 5:CALL G3INIT`, `20 CALL G3OBJ(1,1)`, `30 CALL G3SPIN(1,1,2,0)`, `40 CALL G3FRAME:GOTO 40`.
4. **Then the game:** the 3D ship should appear on the title screen.
5. **The demo ROM last** (`GEO3D_88_hardware_real_MOD.ROM`): language menu and the demos.
   - "geo3d not found" right at startup: the cartridge answers, but without geo3d. Check that this bitstream was flashed and that the DIP switch is at 88h.
   - The same message in the middle of a demo: geo3d or the command engine stopped answering for about 3 s. Note the demo and the moment: this is a real problem to investigate.
6. **Watch for:** dots or garbage while drawing (SDRAM), hangs after a few minutes, and heat. If possible, test on another MSX model, including a PAL one and a turbo R.
7. **To go back to the original:** the same procedure, with the cartridge out of the MSX, using `recovery\tangnano20k_vdp_cartridge_HRA_86361d8.fs`.

## 12. Open items

1. **First test on the real cartridge** (section 11). It is the only way to confirm the 0.5 ns margin and the tight slacks.
2. **Review and commit** the changes from this stage. Nothing has been committed or pushed. `geo3d/syn/gowin/` and this report are still outside git.
3. **`geo3d/README.md`** (read-only on purpose, needs Alex's approval): lines 123 and 142 still say clk42m, and the integration list does not mention `gowin_rpll2.v` or the SDC.
4. **`geo3d/demos/README.md`, `README.pt.md` and `README.es.md`:** the "Real hardware" paragraph and the `run_rom_z80.py` usage (new options `--absent`, `--stuck`, `--msxver` and `--pal`) still describe the previous state.
5. **Old ROMs in the delivery folder:** `GEO3D_88_hardware_real.ROM`, the `*_crawlonly` files, `GEO3D_98_fmconv` and `GEO3D_98_midi` do not have the detection. Rebuild or delete them.
6. **Gowin Programmer menu names** in `FLASH.txt`: they come from Sipeed's procedure for the Tang Nano 20K and were not checked on this machine.
7. **Disk space:** the experiment folders `C:\Projects\mmsoft\g3x` (561 MB) and `geo3d_review_gw*` are outside the repository and can be deleted.
