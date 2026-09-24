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
; It starts with a language menu (English, Español, Português: the crawl is
; the only text in the demos): 1/2/3, or cursor up/down and SPACE or RETURN.
; Then the demos play in that language at 30 frames per second, in a loop.
; Space bar: next demo.
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
;   08 FLIP  page                   PACE vblanks since the last flip, R#2 = page
;   09 MARK  count                  loop start
;   0A LOOP                         back to MARK until count runs out
;   0B NEXTBANK                     continue at the start of the next bank
;   0C MENU                         language menu on the picture already
;                                   uploaded; picks the demo table, restarts
;   0D PACE  n                      vblanks per flip from here on (each demo
;                                   starts at 2: 30 fps at 60 Hz)
;   0E MUSIC                        start the music (music_table, the best
;                                   chip found at power on: OPL4/OPL3 FM, SCC
;                                   + PSG, or PSG); it advances one tick per
;                                   vertical blank of the page flips and stops
;                                   at the next demo_init
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
PSG_A:      equ 0xA0            ; PSG register select
PSG_D:      equ 0xA1            ; PSG data
OPL_A0:     equ 0xC4            ; OPL4 FM / OPL3: bank 0 address (read: status)
OPL_D0:     equ 0xC5
OPL_A1:     equ 0xC6            ; bank 1 address
OPL_D1:     equ 0xC7

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
menu_sel:   equ 0xC008          ; 0 English, 1 Español, 2 Português
menu_keys:  equ 0xC009          ; keys down at the last menu scan
demo_base:  equ 0xC00A          ; 2 bytes: demo table of the chosen language
flip_n:     equ 0xC00C          ; vblanks per page flip (PACE)
my_slot:    equ 0xC00D          ; this cartridge's slot (ENASLT format)
scc_slot:   equ 0xC00E          ; slot of a Konami SCC, FFh: none
scc_try:    equ 0xC00F
mus_target: equ 0xC010          ; 0 PSG, 1 SCC + PSG, 2 OPL + PSG
mus_on:     equ 0xC011
mus_bank:   equ 0xC012
mus_ptr:    equ 0xC013          ; 2 bytes
mus_wait:   equ 0xC015          ; ticks to skip
scc_end:    equ 0xC016          ; 2 bytes: end of the SCC writes in scc_buf
opl4:       equ 0xC018          ; 1: the FM part is an OPL4's (MoonSound)
kon_lo:     equ 0xC019          ; key on being written: F-number low
kon_b0:     equ 0xC01A          ; and B0
opl_b0:     equ 0xC020          ; 18 bytes: last B0 value per FM channel
scc_buf:    equ 0xC100          ; 1 KB: this tick's SCC writes (offset, value)

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
        ld (my_slot), a
        ld h, 0x80
        call ENASLT
        di
        xor a
        ld (keyprev), a
        call music_detect
        call psg_silence
        ld hl, menu_entry           ; the menu is a stream too (picture + MENU)
        jr play_demo

restart:
        ld hl, (demo_base)

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
        dw op_vrle, op_flip, op_mark, op_loop, op_nextbank, op_menu, op_pace
        dw op_music

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

op_pace:
        ld a, (hl)
        inc hl
        ld (flip_n), a
        jp interp

op_music:
        push hl
        call music_start
        pop hl
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

; Page flip: the V9968 sets S#0 bit7 (F) at every vertical blank and reading
; S#0 clears it. Flip on the flip_n-th blank since the last flip (2 = 30 fps).
op_flip:
        ld a, (hl)
        inc hl
        push hl
        rrca
        rrca
        rrca
        or 0x1F                     ; R#2 = page * 32 + 1Fh
        ld e, a
        ld a, (flip_n)
        ld b, a
        in a, (VDP_CTRL)            ; S#0 (R#15 = 0)
        rlca
        jr nc, fl_wait
        dec b                       ; one blank already went by (PACE >= 2)
        call music_tick             ; one music tick per blank
fl_wait:
        in a, (VDP_CTRL)
        rlca
        jr nc, fl_wait
        dec b
        jr z, fl_flip
        call music_tick
        jr fl_wait
fl_flip:
        ld a, e
        out (VDP_CTRL), a
        ld a, 0x80 + 2
        out (VDP_CTRL), a           ; flip right at the blank,
        call music_tick             ; then this blank's music tick (FM ticks can be long)
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
; Language menu. The picture is on page 0: each option's text uses its own
; palette entry (5, 6, 7) and so does its arrow (8, 9, 10), so the highlight
; is a palette change. Acts on new key presses only (keys already down when
; the menu appears are ignored), then waits until every key is up, so the
; choosing key does not also skip the first demo.
op_menu:
        xor a
        ld (menu_sel), a            ; English first
        dec a
        ld (menu_keys), a
        call menu_paint
mn_loop:
        call wait_frame
        call read_keys
        ld b, a
        ld a, (menu_keys)
        cpl
        and b                       ; A = keys pressed since the last scan
        ld c, a
        ld a, b
        ld (menu_keys), a
        ld a, c
        and 0x38                    ; 1 / 2 / 3: choose and go
        jr nz, mn_digit
        bit 0, c
        jr nz, mn_up
        bit 1, c
        jr nz, mn_down
        bit 2, c
        jr nz, mn_go
        jr mn_loop
mn_digit:
        ld b, 0                     ; lowest digit pressed: bit 3 = '1'
mn_dg1: bit 3, a
        jr nz, mn_dg2
        rrca
        inc b
        jr mn_dg1
mn_dg2: ld a, b
        ld (menu_sel), a
        call menu_paint
        jr mn_go
mn_up:
        ld a, (menu_sel)
        or a
        jr nz, mn_up1
        ld a, 3
mn_up1: dec a
        jr mn_new
mn_down:
        ld a, (menu_sel)
        inc a
        cp 3
        jr c, mn_new
        xor a
mn_new: ld (menu_sel), a
        call menu_paint
        jr mn_loop
mn_go:
        call wait_frame
        call read_keys
        or a
        jr nz, mn_go                ; until every key is up
        xor a
        ld (keyprev), a
        ld a, (menu_sel)
        add a, a
        ld e, a
        ld d, 0
        ld hl, lang_tables
        add hl, de
        ld e, (hl)
        inc hl
        ld d, (hl)
        ld (demo_base), de
        jp restart

; palette entries 5..7 (texts) and 8..10 (arrows): bright for the selection
menu_paint:
        ld a, 5
        ld b, 16
        call vdp_wreg               ; R#16 = 5, auto-increments per entry
        ld hl, mp_text
        call mp_three
        ld a, 8
        ld b, 16
        call vdp_wreg
        ld hl, mp_arrow
mp_three:                           ; HL = on (2 bytes), off (2 bytes)
        ld a, (menu_sel)
        ld d, a
        ld e, 0
mp_l:   push hl
        ld a, e
        cp d
        jr z, mp_on
        inc hl
        inc hl
mp_on:  ld a, (hl)
        out (VDP_PAL), a
        inc hl
        ld a, (hl)
        out (VDP_PAL), a
        pop hl
        inc e
        ld a, e
        cp 3
        jr c, mp_l
        ret

mp_text:  db 0x71, 0x06, 0x34, 0x03    ; on (7,6,1) yellow, off (3,3,4)
mp_arrow: db 0x71, 0x06, 0x00, 0x00    ; on yellow, off black (hidden)

; one vertical blank: S#0 bit7 (R#15 = 0), cleared by the read
wait_frame:
        in a, (VDP_CTRL)
        rlca
        jr nc, wait_frame
        ret

; A = keys down: bit0 up, bit1 down, bit2 SPACE or RETURN, bit3..5 = 1, 2, 3
read_keys:
        xor a
        call key_row                ; row 0: bit1..3 = keys 1..3
        and 0x0E
        add a, a
        add a, a
        ld c, a
        ld a, 7
        call key_row                ; row 7: bit7 = RETURN
        and 0x80
        jr z, rk_1
        set 2, c
rk_1:   ld a, 8
        call key_row                ; row 8: bit0 SPACE, bit5 up, bit6 down
        ld b, a
        and 0x01
        jr z, rk_2
        set 2, c
rk_2:   ld a, b
        and 0x60
        rlca
        rlca
        rlca                        ; bit5 -> bit0, bit6 -> bit1
        or c
        ret

key_row:                            ; A = row; returns its keys, 1 = down
        ld b, a
        in a, (PPI_C)
        and 0xF0
        or b
        out (PPI_C), a
        in a, (PPI_B)
        cpl
        ret

; ----------------------------------------------------------------------------
; Music (data made by music.py). At power on: an OPL3 / OPL4 FM part at C4h
; answers the timer test -> OPL target; else a Konami SCC in some slot ->
; SCC target; else the PSG. The data of the chosen target sits in ROM banks
; and is read through page 2 (the stream's bank goes back after each tick).
; The SCC also lives in page 2, so its writes of a tick are buffered in RAM
; and applied with the SCC's slot switched in (ENASLT), then ours back.

music_detect:
        xor a
        ld (mus_on), a
        ld (mus_target), a          ; PSG
        ld (opl4), a
        ld hl, scc_buf
        ld (scc_end), hl
        dec a
        ld (scc_slot), a
        call opl_detect
        jr nz, md_scc
        ld a, 2
        ld (mus_target), a
        ; an OPL4 (MoonSound) answers device ID 001 in wave register 2
        ; (ports 7Eh/7Fh), which it only selects with NEW2 = 1 (FM bank 1,
        ; register 05h = 03h; an OPL3 ignores that bit). Its FM part runs at
        ; 49517 Hz instead of the OPL3's 49716 Hz, so key on raises the
        ; F-number by 1/256.
        ld a, 5
        out (OPL_A1), a
        ld a, 3
        out (OPL_D1), a             ; NEW = NEW2 = 1
        ld a, 2
        out (0x7E), a
        ex (sp), hl                 ; let the register select settle
        ex (sp), hl
        in a, (0x7F)
        and 0xE0
        ld b, a
        ld a, 5
        out (OPL_A1), a
        xor a
        out (OPL_D1), a             ; NEW = NEW2 = 0 (the music sets NEW)
        ld a, b
        cp 0x20
        ret nz
        ld a, 1
        ld (opl4), a
        ret
md_scc: call scc_detect
        ld a, (scc_slot)
        inc a
        ret z                       ; no SCC: PSG
        ld a, 1
        ld (mus_target), a
        ret

; Z = an OPL3/OPL4 FM part: timer 1 (80 us) must raise status bits 7 and 6.
; No BUSY polling here: an empty port reads FFh.
opl_detect:
        ld c, 4
        ld a, 0x60
        call od_w                   ; reset both timers
        ld a, 0x80
        call od_w                   ; reset the IRQ flags
        in a, (OPL_A0)
        and 0xE0
        ret nz                      ; flags up without a timer: not an OPL
        ld c, 2
        ld a, 0xFF
        call od_w                   ; timer 1: one step to overflow
        ld c, 4
        ld a, 0x21
        call od_w                   ; start timer 1, timer 2 masked
        ld b, 0
od_1:   djnz od_1                   ; ~1 ms
        in a, (OPL_A0)
        and 0xE0
        ld b, a
        ld a, 0x60
        call od_w
        ld a, 0x80
        call od_w
        ld a, b
        cp 0xC0
        ret
od_w:   push af
        ld a, c
        out (OPL_A0), a
        pop af
        out (OPL_D0), a
        ret

; scans every slot and subslot of page 2 for a Konami SCC: 9800h must be
; read-only with the SCC off (9000h = 0) and writable with it on (3Fh).
; RAM (writable both ways) and ROM (neither) are left as they were.
scc_detect:
        ld b, 0
sd_p:   push bc
        ld a, b
        ld hl, EXPTBL
        add a, l
        ld l, a
        ld a, (hl)
        and 0x80
        or b
        ld c, a                     ; slot with the expanded flag
        jp p, sd_one
        ld d, 0
sd_s:   ld a, d
        add a, a
        add a, a
        or c
        push bc
        push de
        call scc_test
        pop de
        pop bc
        jr z, sd_found
        inc d
        ld a, d
        cp 4
        jr c, sd_s
        jr sd_next
sd_one: ld a, c
        call scc_test
        jr z, sd_found
sd_next:
        pop bc
        inc b
        ld a, b
        cp 4
        jr c, sd_p
        ret
sd_found:
        pop bc
        ret

scc_test:                           ; A = slot; Z = SCC there; page 2 back to us
        ld (scc_try), a
        ld h, 0x80
        call ENASLT
        di
        ld hl, 0x9800
        ld a, (0x9000)
        ld e, a                     ; what was at 9000h (RAM)
        xor a
        ld (0x9000), a              ; SCC off (a Konami mapper switches bank here)
        ld d, (hl)                  ; 9800h as it reads now, after the switch
        ld a, d
        cpl
        ld (hl), a
        cp (hl)
        jr z, st_ram                ; writable with the SCC off: not an SCC
        ld a, 0x3F
        ld (0x9000), a              ; SCC on
        ld a, (hl)
        ld d, a
        cpl
        ld (hl), a
        cp (hl)
        ld (hl), d
        jr nz, st_no
        ld a, (scc_try)
        ld (scc_slot), a
        call slot_back
        xor a
        ret
st_ram: ld (hl), d
        ld a, e
        ld (0x9000), a
st_no:  call slot_back
        or 1
        ret

slot_back:                          ; page 2 = this cartridge again
        ld a, (my_slot)
        ld h, 0x80
        call ENASLT
        di
        ret

; MUSIC opcode: start the chosen target's data (music_table: bank FFh = the
; ROM was built without music)
music_start:
        ld a, (mus_target)
        ld e, a
        add a, a
        add a, e
        ld e, a
        ld d, 0
        ld hl, music_table
        add hl, de
        ld a, (hl)
        cp 0xFF
        ret z
        ld (mus_bank), a
        inc hl
        ld e, (hl)
        inc hl
        ld d, (hl)
        ld (mus_ptr), de
        xor a
        ld (mus_wait), a
        ld a, (mus_target)
        cp 1
        jr nz, ms_on
        ld a, (scc_slot)
        ld h, 0x80
        call ENASLT
        di
        ld a, 0x3F
        ld (0x9000), a              ; SCC registers at 9800h
        call slot_back
ms_on:  ld a, 1
        ld (mus_on), a
        ret

; stop and silence every chip of the target
music_stop:
        xor a
        ld (mus_on), a
        call psg_silence
        ld a, (mus_target)
        cp 1
        jr z, mstop_scc
        cp 2
        ret nz
        ld e, 0                     ; OPL: key off all 18 channels
mstop_o:
        ld a, e
        call opl_chan
        ld a, 0xB0
        add a, b
        ld c, a
        xor a
        call opl_w
        inc e
        ld a, e
        cp 18
        jr c, mstop_o
        ret
mstop_scc:
        ld a, (scc_slot)
        ld h, 0x80
        call ENASLT
        di
        xor a
        ld (0x988F), a              ; all SCC channels off
        jp slot_back

psg_silence:
        ld a, 7
        out (PSG_A), a
        ld a, 0xBF                  ; tones and noise off; bit7 = 1 (port B output)
        out (PSG_D), a
        ld b, 8
psl:    ld a, b
        out (PSG_A), a
        xor a
        out (PSG_D), a
        inc b
        ld a, b
        cp 11
        jr c, psl
        ret

; one tick of music (called once per vertical blank); keeps every register
music_tick:
        push af
        ld a, (mus_on)
        or a
        jp z, mt_off
        push bc
        push de
        push hl
        ld a, (mus_wait)
        or a
        jr z, mt_run
        dec a
        ld (mus_wait), a
        jp mt_ret
mt_run: ld a, (mus_bank)
        call bank2_raw              ; music data in page 2
        ld hl, (mus_ptr)
mt_op:  ld a, (hl)
        inc hl
        cp 0x0E
        jr c, mt_psg
        cp 0x20
        jr c, mt_sreg
        cp 0x24
        jr c, mt_swav
        cp 0x30
        jr z, mt_opl0
        cp 0x31
        jr z, mt_opl1
        cp 0x52
        jp c, mt_kon                ; 40h-51h
        cp 0x72
        jp c, mt_koff               ; 60h-71h
        cp 0xFD
        jp c, mt_wait               ; 80h-FCh
        cp 0xFE
        jp z, mt_nextb
        ; FFh: the end
        ld a, (curbank)
        call bank2_raw
        call music_stop
        jp mt_ret
mt_psg: out (PSG_A), a
        ld a, (hl)
        inc hl
        out (PSG_D), a
        jr mt_op
mt_sreg:
        add a, 0x70                 ; 10h-1Fh -> 80h-8Fh (9880h-988Fh)
        ld c, a
        ld a, (hl)
        inc hl
        call scc_put
        jr mt_op
mt_swav:
        sub 0x20
        rrca
        rrca
        rrca                        ; channel * 32
        ld c, a
        ld b, 32
mt_sw1: ld a, (hl)
        inc hl
        call scc_put
        inc c
        djnz mt_sw1
        jr mt_op
mt_opl0:
        ld d, 0
        jr mt_oplw
mt_opl1:
        ld d, 1
mt_oplw:
        ld c, (hl)
        inc hl
        ld a, (hl)
        inc hl
        call opl_w
        jr mt_op
mt_kon: sub 0x40                    ; A0 = a, carrier TL = t, B0 = b (key on)
        ld e, a
        ld a, (hl)
        inc hl
        ld (kon_lo), a
        ld a, (hl)
        inc hl
        ld (kon_b0), a
        ld a, (opl4)
        or a
        call nz, opl4_tune
        ld a, e
        call opl_chan
        ld a, 0xA0
        add a, b
        ld c, a
        ld a, (kon_lo)
        call opl_w                  ; F-number low
        ld a, (kon_b0)
        push af                     ; B0 value
        push hl
        ld hl, car_off
        ld a, l
        add a, b
        ld l, a
        jr nc, mt_k1
        inc h
mt_k1:  ld a, (hl)
        add a, 0x40
        ld c, a
        pop hl
        ld a, (hl)
        inc hl
        call opl_w                  ; carrier TL
        pop af
        push hl
        ld hl, opl_b0
        ld c, e
        ld b, 0
        add hl, bc
        ld (hl), a
        pop hl
        push af
        ld a, e
        call opl_chan
        ld a, 0xB0
        add a, b
        ld c, a
        pop af
        call opl_w                  ; key on
        jp mt_op
mt_koff:
        sub 0x60
        ld e, a
        push hl
        ld hl, opl_b0
        ld c, a
        ld b, 0
        add hl, bc
        ld a, (hl)
        and 0xDF
        ld (hl), a
        pop hl
        push af
        ld a, e
        call opl_chan
        ld a, 0xB0
        add a, b
        ld c, a
        pop af
        call opl_w
        jp mt_op
mt_nextb:
        ld a, (mus_bank)
        inc a
        ld (mus_bank), a
        call bank2_raw
        ld hl, 0x8000
        jp mt_op
mt_wait:
        sub 0x80
        ld (mus_wait), a            ; op 7Fh + n: this tick and n - 1 more
        ld (mus_ptr), hl
        ld a, (curbank)
        call bank2_raw              ; the stream's bank back
        call scc_flush
mt_ret: pop hl
        pop de
        pop bc
mt_off: pop af
        ret

scc_put:                            ; buffer SCC write: C = offset (98xxh), A = value
        push hl
        ld hl, (scc_end)
        ld (hl), c
        inc hl
        ld (hl), a
        inc hl
        ld (scc_end), hl
        pop hl
        ret

scc_flush:                          ; apply the buffered SCC writes
        ld hl, (scc_end)
        ld de, scc_buf
        or a
        sbc hl, de
        ret z
        ld a, (scc_slot)
        ld h, 0x80
        call ENASLT
        di
        ld hl, scc_buf
        ld d, 0x98
sf_1:   ld e, (hl)
        inc hl
        ld a, (hl)
        inc hl
        ld (de), a
        push hl
        ld bc, (scc_end)
        or a
        sbc hl, bc
        pop hl
        jr c, sf_1
        ld hl, scc_buf
        ld (scc_end), hl
        jp slot_back

opl4_tune:                          ; F-number += F-number >> 8 (the data is for
        ld a, (kon_b0)              ; 49716 Hz; +0.26..0.39% for 49517 Hz)
        and 3
        ld c, a
        ld a, (kon_lo)
        add a, c
        ld (kon_lo), a
        ret nc
        ld a, (kon_b0)
        ld c, a
        and 3
        cp 3
        jr z, ot_max
        inc c                       ; carry into F-number bits 8-9
        ld a, c
        ld (kon_b0), a
        ret
ot_max: ld a, 0xFF                  ; already at the top: stay at 1023
        ld (kon_lo), a
        ret

opl_chan:                           ; A = channel 0..17 -> B = index 0..8, D = bank
        ld d, 0
        cp 9
        jr c, oc_1
        sub 9
        inc d
oc_1:   ld b, a
        ret

opl_w:                              ; D = bank, C = register, A = value
        push af
ow_1:   in a, (OPL_A0)              ; OPL4: wait while BUSY
        rrca
        jr c, ow_1
        ld a, c
        bit 0, d
        jr nz, ow_b1
        out (OPL_A0), a
ow_2:   in a, (OPL_A0)
        rrca
        jr c, ow_2
        pop af
        out (OPL_D0), a
        ret
ow_b1:  out (OPL_A1), a
ow_3:   in a, (OPL_A0)
        rrca
        jr c, ow_3
        pop af
        out (OPL_D1), a
        ret

car_off:                            ; carrier operator of channel index 0..8
        db 0x03, 0x04, 0x05, 0x0B, 0x0C, 0x0D, 0x13, 0x14, 0x15

; ----------------------------------------------------------------------------
setbank2:                           ; A = bank for page 2 (the stream's)
        ld (curbank), a
bank2_raw:                          ; A = bank for page 2, curbank kept (music data)
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
        ld a, 2
        ld (flip_n), a              ; 30 fps unless the stream says PACE
        call wait_ce
        xor a
        out (VDP_PORT4), a          ; bit7 = 0: unlock R#20 / R#21
        call music_stop             ; every demo starts silent
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
