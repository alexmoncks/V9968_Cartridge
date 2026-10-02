<!--
Reproduced verbatim, with the author's permission, from his comment on
hra1129/V9968_Cartridge#9 (2026-09-30). Only this header was added.
-->

> **Credit.** These integration notes are by **Albert (Papipapito), MSXimus
> project, September 2026, MIT**. They were posted on 30 September 2026 in
> [hra1129/V9968_Cartridge#9](https://github.com/hra1129/V9968_Cartridge/issues/9#issuecomment-5916327703)
> and are reproduced here as written, with his permission ("you are welcome
> to add them to the repository (MIT, like geo3d)").
>
> Since then: the mapper detection problem he describes under "Things that
> cost time" is addressed by the mapper tag every geo3d ROM now carries
> ("ROM_AS16" / "ROM_ASC8" at offset 0010h); see "ROM format / mapper"
> in [../README.md](../README.md).

# geo3d on the MSXimus Z: integration notes

Notes from integrating Alex Moncks' **geo3d** 3D geometry coprocessor into the **MSXimus Z**, an MSX2+ core on a Xilinx Zynq XC7Z020 (OMDAZZ ZYNQ MINI board, Vivado 2019.2) whose VDP is HRA!'s V9968. Written for anyone adding geo3d to another V9968-based design. Albert (Papipapito), MSXimus project, September 2026. MIT, like geo3d itself.

## The host system

- The V9968 is the machine's own VDP at the standard ports 98h-9Bh (plus 9Ch, its port #4). It runs at 85.909 MHz.
- The Z80 side reaches the VDP through a small glue that turns each I/O access into one transaction on the V9968's `bus_valid` / `bus_ready` bus, with `bus_address` = the low 3 bits of the port.
- VRAM is in DDR3 behind a cache, so VRAM latency is variable. geo3d never touches VRAM itself, so this does not matter to it.

## What was taken

`geo3d_core.v`, `geo3d_engine.v` and `geo3d_bus.v` from `geo3d/rtl/`, commit `c938150`, **unchanged**. `geo3d_z80if.v` (the direct Z80 interface of phase 1) is not used: the design enters through `geo3d_bus`, the adapter to the valid/ready bus.

## Ports

geo3d answers at **9Dh (index / status) and 9Fh (data)**: offsets 5 and 7 of the VDP block, which is what `GEO3D_98.ROM` (`build_rom.py --base 0x98`) and the openMSX `-ext geo3d` expect. 9Eh and the mirrors 8Dh/8Fh are not decoded.

The only change to the address decode is to add those two ports to the VDP's I/O hit, so the glue performs the same transaction it does for the VDP:

```verilog
assign vdp_io_hit = ( /* 98h-9Bh, 9Ch and their mirrors, as before */ )
                 || ( bus_addr[7:2] == 6'b100111 && bus_addr[0] );   // 9Dh, 9Fh
```

## Bus wiring

`geo3d_bus` sits next to the VDP on the same bus. Its `hit` keeps those accesses away from the VDP, and the read data is muxed on `bus_rdata_en`:

```verilog
geo3d_bus u_geo3d (
    .clk(clk_86), .clk_eng(clk_43), .reset_n(rst86_n),
    .bus_address(v68_addr), .bus_ioreq(v68_ioreq), .bus_write(v68_write),
    .bus_valid(v68_valid), .bus_wdata(v68_wdata),
    .hit(geo_hit), .bus_ready(geo_ready), .bus_rdata(geo_rdata), .bus_rdata_en(geo_rdata_en),
    .cmd_wr(geo_cmd_wr), .cmd_num(geo_cmd_num), .cmd_data(geo_cmd_data), .cmd_ce(geo_cmd_ce),
    .run_busy(geo_busy)
);
assign v68_valid_vdp = v68_valid & ~geo_hit;                       // the VDP never sees a geo3d access
assign v68_ready     = geo_hit ? geo_ready : v68_ready_vdp;
assign v68_rdata     = geo_rdata_en ? geo_rdata : v68_rdata_vdp;
assign v68_rdata_en  = geo_rdata_en | v68_rdata_en_vdp;
```

While nobody talks to 9Dh/9Fh the V9968 behaves exactly as before.

## The VDP side

`vdp.v` carries the patch from the geo3d repository, applied by hand to our tree (HRA's core with local changes):

- inputs `ext_cmd_wr`, `ext_cmd_num`, `ext_cmd_data`, OR-ed into the register write that feeds `vdp_command` (R#32-R#46);
- output `ext_cmd_ce`, the CE bit of S#2.

With `ext_cmd_wr = 0` the VDP is identical, so every existing VDP bench simply ties the three inputs to 0.

## Clock

The engine runs at 42.95 MHz, taken from a second output of the **same PLL** that makes the 85.909 MHz VDP clock: divide by 22 instead of 11, so it is exactly half the frequency and in phase. The two clocks are declared related (same clock group), and the toggle crossings inside `geo3d_bus` are timed as synchronous paths with an 11.6 ns budget instead of being cut as asynchronous. Reset is the VDP-domain reset.

## Cost and timing on the XC7Z020

| | Before | With geo3d |
|---|---|---|
| LUTs | 70.6 % | 76.6 % (about +3,200) |
| Block RAM | | +6 tiles |
| DSP48 | | 13 |
| Worst setup slack at 85.909 MHz | +0.68 ns | +0.68 ns |

## Verification before the first bitstream

1. geo3d's own benches on our tree (4,000 vertices, the random scenes, filled and textured faces): bit-exact.
2. `tb_geo3d_int`, an integration bench of ours: the real Z80 glue, `geo3d_bus` and the complete VDP with a VRAM model, driven through ports 9Dh/9Fh with the stimulus of `sim/gen_scenes.py`. The log of LINE commands is identical to the Python model (2,792 commands), with 0 register writes while CE = 1.
3. `tb_geo3d_sys`, added later: the same stack with the whole Z80 side of a real ROM (registers through 99h/9Bh, VRAM through 98h, palette through 9Ah, CE waits, dumps of the displayed page). The stimulus is recorded by running the ROM in a Z80 emulator with the ASCII16 mapper and a stub BIOS, and the expected log comes from `sim/check_demo.py`.

## Results

**On the board.** `GEO3D_98.ROM`: all six demos run. A seventh entry of our own, the word "MSXIMUS" as extruded shaded blocks (232 vertices, 174 faces), was built with the repository's pipeline and runs too. Frames per second, measured with the demos rebuilt to free-run and count frames:

| Demo | V9938 command timing | V9968 high-speed mode |
|---|---|---|
| Wireframe | 17 | about 350 (2.9 ms per frame) |
| Filled faces | 11 | about 200 (5.0 ms) |
| Textured faces (LRMM) | | about 160 (6.2 ms) |

High-speed mode is port 9Ch = 0, R#21 = 0, R#20 = 1.

**Third-party ROMs, in simulation.** The six demo ROMs of kanon-ai's `V9968_Geo3D_SampleDemo` were run through `tb_geo3d_sys`, 24 frames each. For all six the command log is identical to the model and there are 0 writes while CE = 1:

| ROM | geo3d RUNs | Commands |
|---|---|---|
| VECTOR / RUSH | 168 | 58,660 |
| SKYBOUND | 168 | 32,030 |
| ORBITAL | 48 | 29,506 |
| ORBITAL TYPE REAL | 48 | 29,421 |
| NIGHT RAVEN | 217 | 114,680 |
| OBSIDIAN | 216 | 69,343 |

They have not been run on the board yet.

## Things that cost time

- **Mapper detection.** Our launcher guesses the mapper from the ROM contents by counting `LD (nnnn),A` instructions, as openMSX does. `GEO3D_98.ROM` is plain ASCII16, but its compressed streams contain five `32 xx 50/90/B0` by chance against one real write to 7000h, so the launcher chose Konami-SCC and the player overwrote itself. kanon-ai's NIGHT RAVEN is misdetected the same way. A mapper tag in the file name (`[ASCII16]`) fixes it; a signature in the ROM would fix it for everyone.
- **Demos run with interrupts disabled.** A key injected through a debug channel never arrives, so "press a key to exit" cannot be automated. For measurements we rebuilt the demos to exit after a fixed number of frames.
- **Frame pacing through the S1990 counter.** kanon-ai's demos time their frames with ports E6h/E7h of the turbo R system timer. A V9968 + geo3d design for MSX2+ needs that counter for them to pace correctly.
- **VDP upstream changes.** HRA's fixes of 29 September 2026 to `vdp_command.v` (the registered pixel step and the DIY source clip) and to the port #1 latch in `vdp_cpu_interface.v` do not disturb the `ext_cmd` path: four of the ROMs above give identical command logs and identical displayed pages before and after porting them.
