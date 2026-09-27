rmdir /S /Q work
vlib work
vlog ..\vdp_timing_control_screen_mode.v
vlog ..\vdp_color_palette_ram.v
vlog ..\vdp_color_palette.v
vlog tb.sv
vsim -c -t 1ns -do run.do tb
move transcript log.txt