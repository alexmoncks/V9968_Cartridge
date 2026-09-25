; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; modplay_test.asm  -  standalone test ROM for geo3d_modplay.asm
;
; ASCII16 MegaROM: this program in bank 0, what modplay.py packs (the lookup
; tables, then the MOD) from bank 1 on. It detects the MoonSound, uploads
; the samples, starts the song and calls mod_poll until the song's end,
; then halts. test_modplay.py builds it (rom_mod.asm, rom_test.asm) and runs
; it in openMSX or in its Z80 emulator, and checks what it plays.
;
; RAM, read by the test script:
;   t_phase   0 start, 1 uploading, 2 playing, 3 the end, EEh no MoonSound
;   t_blocks  128 KB blocks of sample RAM found
; T_GAP (rom_test.asm): busy loop between polls, rounds of 38 T (10.7 us at
; 3.58 MHz; 0: poll all the time), to see that a sparse poll delays the
; notes a little but does not make the song drift. T_MODE: 0 polls with
; mod_poll (a host that waits), 1 with mod_tpoll (a busy host: each tick's
; whole work when it is due; modcost.py measures both). T_RB: mp_restbank
; takes as long as the demo ROM's (modcost.py).
; ============================================================================

        include "rom_test.asm"      ; T_GAP, T_MODE, T_RB

ENASLT:     equ 0x0024
RSLREG:     equ 0x0138
EXPTBL:     equ 0xFCC1

t_phase:    equ 0xC000
t_blocks:   equ 0xC001
t_slot:     equ 0xC002
MP_RAM:     equ 0xC100

        org 0x4000
        db "AB"
        dw init
        dw 0, 0, 0
        ds 6, 0

init:
        di
        ld sp, 0xF000
        xor a
        ld (t_phase), a
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
        ld (t_slot), a
        ld h, 0x80
        call ENASLT
        di
        call mod_detect
        ld (t_blocks), a
        jr c, t_fail
        ld a, 1
        ld (t_phase), a
        call mod_upload
        ld a, 2
        ld (t_phase), a
        call mod_start
t_loop:
        if T_MODE
        call mod_tpoll
        else
        call mod_poll
        endif
        ld a, (mp_on)
        or a
        jr z, t_end
        ld hl, T_GAP
t_gap:  ld a, h
        or l
        jr z, t_loop
        dec hl                      ; 38 T per round (M1 waits included)
        jr t_gap
t_end:  ld a, 3
        ld (t_phase), a
t_halt: jr t_halt
t_fail: ld a, 0xEE
        ld (t_phase), a
        jr t_halt

mp_setbank:
        ld (0x7000), a              ; ASCII16: bank at 8000h-BFFFh
        ret
mp_restbank:
        if T_RB
        ld a, (t_slot)              ; (modcost.py: as long as the demo ROM's,
        jr t_rb1                    ; which maps its stream's bank back)
t_rb1:  ld (0x7000), a
        endif
        ret                         ; no stream of our own in page 2

        include "rom_mod.asm"
        include "geo3d_modplay.asm"

        ds 0x8000 - $, 0xFF
