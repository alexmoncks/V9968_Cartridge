; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; geo3d_rom.asm  -  GEO3D demo sequence, MSX MegaROM (ASCII16 mapper)
;
; Bank 0 (page 1, 4000h-7FFFh) holds this player. Banks 1..n (page 2,
; 8000h-BFFFh) hold one command stream per demo: the exact port traffic a Z80
; program sends to geo3d and to the V9968 (register writes, VDP commands,
; VRAM uploads, page flips). The geometry is computed by geo3d in the FPGA;
; the Z80 only moves bytes, as in the .COM demos.
;
; Ports: PORT_BASE comes from rom_ports.asm (written by build_rom.py):
;   88h  GEO3D.ROM, real hardware: V9968 cartridge with the geo3d build, DIP
;        switch at 88h (VDP 88h-8Ch, geo3d 8Dh/8Fh). The internal VDP is left
;        alone. Output: the V9968 cartridge HDMI port.
;   98h  GEO3D_98.ROM, emulator profile (openMSX V9968 fork, -ext geo3d): the
;        V9968 is the machine's VDP (98h-9Ch), geo3d on 9Dh/9Fh.
; 30 frames per second. Space bar: next demo. The sequence loops forever.
;
; Stream opcodes (little-endian), ports for the 88h profile:
;   00 END                          next demo
;   01 GEO   idx n data[n]          OUT 8Dh,idx then n bytes to 8Fh
;   02 GEOD  n data[n]              n bytes to 8Fh (index unchanged)
;   03 VREG  reg val                VDP register write
;   04 VIND  reg n data[n]          R#17 = reg (auto-increment), n bytes to 8Bh
;   05 WAITGEO                      until geo3d RUN busy = 0
;   06 WAITCE                       until the VDP command engine is idle
;   07 VRLE  a0 a1 a2 len(2) rle    VRAM upload at a 18-bit address, RLE:
;                                   c < 80h: c+1 literal bytes follow,
;                                   c >= 80h: next byte repeated c-7Eh times
;   08 FLIP  page                   2 vblanks since the last flip, R#2 = page
;   09 MARK  count                  loop start
;   0A LOOP                         back to MARK until count runs out
;   0B NEXTBANK                     continue at the start of the next bank
;
; Assemble: z80asm -o bank0.bin geo3d_rom.asm (build_rom.py does it all)
; ============================================================================

        include "rom_ports.asm"     ; PORT_BASE: 0x88 or 0x98

VDP_DATA:   equ PORT_BASE
VDP_CTRL:   equ PORT_BASE + 1
VDP_PAL:    equ PORT_BASE + 2
VDP_IND:    equ PORT_BASE + 3
VDP_PORT4:  equ PORT_BASE + 4
GEO_IDX:    equ PORT_BASE + 5
GEO_DAT:    equ PORT_BASE + 7

PPI_B:      equ 0xA9            ; keyboard column input
PPI_C:      equ 0xAA            ; keyboard row select (low nibble)

ENASLT:     equ 0x0024
RSLREG:     equ 0x0138
EXPTBL:     equ 0xFCC1

BANK2_SEL:  equ 0x7000          ; ASCII16: bank for 8000h-BFFFh

; RAM (page 3)
curbank:    equ 0xC000
loop_cnt:   equ 0xC001
loop_bank:  equ 0xC002
loop_ptr:   equ 0xC003          ; 2 bytes
cur_demo:   equ 0xC005          ; 2 bytes
keyprev:    equ 0xC007

        org 0x4000
        db "AB"
        dw init
        dw 0, 0, 0
        ds 6, 0

; ----------------------------------------------------------------------------
init:
        di
        ld sp, 0xF000
        ; page 2 -> this cartridge's slot (same slot as page 1)
        call RSLREG
        rrca
        rrca
        and 3
        ld c, a
        ld b, 0
        ld hl, EXPTBL
        add hl, bc
        ld a, (hl)
        and 0x80
        or c
        ld c, a
        inc hl
        inc hl
        inc hl
        inc hl
        ld a, (hl)
        and 0x0C
        or c
        ld h, 0x80
        call ENASLT
        di
        xor a
        ld (keyprev), a

restart:
        ld hl, demo_table

; HL -> demo table entry: bank, stream address (2), palette address (2)
play_demo:
        ld a, (hl)
        cp 0xFF
        jr z, restart
        ld (cur_demo), hl
        push hl
        inc hl
        inc hl
        inc hl
        ld e, (hl)
        inc hl
        ld d, (hl)
        ex de, hl
        call demo_init              ; HL = palette
        pop hl
        ld a, (hl)
        call setbank2
        inc hl
        ld e, (hl)
        inc hl
        ld d, (hl)
        ex de, hl                   ; HL = stream pointer

; ----------------------------------------------------------------------------
interp:
        ld a, (hl)
        inc hl
        add a, a
        ld e, a
        ld d, 0
        push hl
        ld hl, op_table
        add hl, de
        ld e, (hl)
        inc hl
        ld d, (hl)
        pop hl
        push de
        ret                         ; jump to the handler, HL = operands

op_table:
        dw op_end, op_geo, op_geod, op_vreg, op_vind, op_waitgeo, op_waitce
        dw op_vrle, op_flip, op_mark, op_loop, op_nextbank

op_end:
next_demo:
        ld hl, (cur_demo)
        ld de, 5
        add hl, de
        jp play_demo

op_geo:
        ld a, (hl)
        inc hl
        out (GEO_IDX), a
op_geod:
        ld b, (hl)
        inc hl
        ld a, b
        or a
        jp z, interp
        ld c, GEO_DAT
        otir
        jp interp

op_vreg:
        ld b, (hl)
        inc hl
        ld a, (hl)
        inc hl
        out (VDP_CTRL), a
        ld a, b
        or 0x80
        out (VDP_CTRL), a
        jp interp

op_vind:
        ld a, (hl)
        inc hl
        out (VDP_CTRL), a
        ld a, 0x80 + 17
        out (VDP_CTRL), a           ; R#17 = first register, auto-increment
        ld b, (hl)
        inc hl
        ld c, VDP_IND
        otir
        jp interp

op_waitgeo:
        in a, (GEO_IDX)
        rrca
        jr c, op_waitgeo            ; bit0 = RUN busy
        jp interp

op_waitce:
        call wait_ce
        jp interp

op_mark:
        ld a, (hl)
        inc hl
        ld (loop_cnt), a
        ld (loop_ptr), hl
        ld a, (curbank)
        ld (loop_bank), a
        jp interp

op_loop:
        ld a, (loop_cnt)
        dec a
        ld (loop_cnt), a
        jp z, interp
        ld a, (loop_bank)
        call setbank2
        ld hl, (loop_ptr)
        jp interp

op_nextbank:
        ld a, (curbank)
        inc a
        call setbank2
        ld hl, 0x8000
        jp interp

; VRAM upload, RLE. R#14 = A17..A14, then A7..A0 and 40h | A13..A8.
op_vrle:
        ld e, (hl)                  ; A7..A0
        inc hl
        ld d, (hl)                  ; A15..A8
        inc hl
        ld a, (hl)                  ; A17..A16
        inc hl
        add a, a
        add a, a
        ld b, a
        ld a, d
        rlca
        rlca
        and 3
        or b
        out (VDP_CTRL), a
        ld a, 0x80 + 14
        out (VDP_CTRL), a
        ld a, e
        out (VDP_CTRL), a
        ld a, d
        and 0x3F
        or 0x40
        out (VDP_CTRL), a
        ld e, (hl)                  ; DE = packed length
        inc hl
        ld d, (hl)
        inc hl
        ld c, VDP_DATA
vr_loop:
        ld a, d
        or e
        jp z, interp
        ld a, (hl)
        inc hl
        dec de
        cp 0x80
        jr nc, vr_run
        inc a                       ; literal: a + 1 bytes
        ld b, a
        ld a, e
        sub b
        ld e, a
        jr nc, vr_lit
        dec d
vr_lit:
        otir
        jr vr_loop
vr_run:
        sub 0x7E                    ; run: 2..129 copies
        ld b, a
        ld a, (hl)
        inc hl
        dec de
vr_r1:
        out (VDP_DATA), a
        djnz vr_r1
        jr vr_loop

; Page flip at 30 fps: the V9968 sets S#0 bit7 (F) at every vertical blank
; and reading S#0 clears it. Flip on the second blank since the last flip.
op_flip:
        ld a, (hl)
        inc hl
        push hl
        rrca
        rrca
        rrca
        or 0x1F                     ; R#2 = page * 32 + 1Fh
        ld e, a
        in a, (VDP_CTRL)            ; S#0 (R#15 = 0)
        rlca
        jr c, fl_one                ; one blank already went by
fl_w1:  in a, (VDP_CTRL)
        rlca
        jr nc, fl_w1
fl_one:
fl_w2:  in a, (VDP_CTRL)
        rlca
        jr nc, fl_w2
        ld a, e
        out (VDP_CTRL), a
        ld a, 0x80 + 2
        out (VDP_CTRL), a
        ; space bar (row 8, bit 0, active low): next demo on a new press
        in a, (PPI_C)
        and 0xF0
        or 8
        out (PPI_C), a
        in a, (PPI_B)
        cpl
        and 1
        ld b, a
        ld a, (keyprev)
        ld c, a
        ld a, b
        ld (keyprev), a
        pop hl
        or a
        jp z, interp
        ld a, c
        or a
        jp nz, interp
        call wait_ce
        jp next_demo

; ----------------------------------------------------------------------------
setbank2:                           ; A = bank for page 2
        ld (curbank), a
        ld (BANK2_SEL), a
        ret

vdp_wreg:                           ; A = value, B = register
        out (VDP_CTRL), a
        ld a, b
        or 0x80
        out (VDP_CTRL), a
        ret

wait_ce:                            ; S#2 bit0 = CE; leaves R#15 = 0
        ld a, 2
        ld b, 15
        call vdp_wreg
wc1:    in a, (VDP_CTRL)
        rrca
        jr c, wc1
        xor a
        ld b, 15
        jp vdp_wreg

; HL = palette (32 bytes). SCREEN 5 on the V9968, V9968 mode (LRMM,
; 256 KB), high-speed commands, full LRMM window, both pages cleared.
demo_init:
        push hl
        call wait_ce
        xor a
        out (VDP_PORT4), a          ; bit7 = 0: unlock R#20 / R#21
        ld hl, init_regs
di_1:   ld a, (hl)
        cp 0xFF
        jr z, di_2
        ld b, a
        inc hl
        ld a, (hl)
        inc hl
        call vdp_wreg
        jr di_1
di_2:   xor a
        ld b, 21
        call vdp_wreg               ; R#21 = 0: V9968 mode
        ld a, 1
        ld b, 20
        call vdp_wreg               ; R#20 = 1: high-speed commands
        ld a, 51
        ld b, 17
        call vdp_wreg
        ld hl, window_regs
        ld bc, 8 * 256 + VDP_IND
        otir                        ; R#51..58: window 0..511 x 0..2047
        xor a
        ld b, 16
        call vdp_wreg
        pop hl
        ld bc, 32 * 256 + VDP_PAL
        otir
        ld a, 36
        ld b, 17
        call vdp_wreg
        ld hl, clear_cmd
        ld bc, 11 * 256 + VDP_IND
        otir                        ; HMMV pages 0 and 1 to black
        jp wait_ce

init_regs:
        db 0, 0x06                  ; GRAPHIC4 (SCREEN 5)
        db 1, 0x40                  ; display on, no interrupts
        db 2, 0x1F                  ; page 0
        db 7, 0x00                  ; border
        db 8, 0x0A                  ; sprites off
        db 9, 0x80                  ; 212 lines
        db 15, 0x00                 ; status register 0
        db 0xFF

window_regs:
        db 0, 0, 0, 0, 0xFF, 0x01, 0xFF, 0x07

clear_cmd:
        dw 0, 0, 256, 512           ; DX, DY, NX, NY
        db 0x00, 0x00, 0xC0         ; CLR, ARG, HMMV

        include "rom_tables.asm"
