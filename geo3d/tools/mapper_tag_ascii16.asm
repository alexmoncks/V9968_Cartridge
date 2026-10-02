; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; mapper_tag_ascii16.asm - the mapper tag of every geo3d ASCII16 MegaROM
; (rom/geo3d_rom.asm, game/src/kernel.asm, game/shooter.asm), included in
; bank 0 right after the ROM type signature at 4010h. The header's INIT
; jumps past it: none of it ever runs.
;
; The ROM type signature itself, 8 bytes at 4010h (file offset 0010h, right
; after the 16-byte "AB" header), is written by each source:
;         db "ROM_AS16"
; the ASCII16 code of MSXgl's "ROM type signature" 1.0 (2024-02-18), the
; same place as the ASCII16X, ROM_NEO8 and ROM_NE16 that openMSX reads.
;
; Then, here:
; - the mapper in words, for a person looking at a hex dump (0 at the end;
;   no "2" in it, see below);
; - 40 LD (nnnn),A to ASCII16 bank registers, for the launchers and
;   emulators that guess the mapper by counting them (openMSX's
;   guessRomType, and the launchers that copy it; tools/mapper_guess.py):
;   8 x 6000h and 8 x 7000h (the two registers) and 24 x 77FFh (the top of
;   the 7000h-77FFh register range, which only ASCII16 is scored for).
;   Bytes that happen to read as 32h nn 50h/90h/B0h in compressed data then
;   no longer make a ROM look like a Konami SCC one.
; ============================================================================
        db "ASCII16 MegaROM, mapper ASCII16 (16 KB banks, registers 6000h and 7000h). geo3d", 0
        ld (0x6000), a
        ld (0x7000), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x6000), a
        ld (0x7000), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x6000), a
        ld (0x7000), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x6000), a
        ld (0x7000), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x6000), a
        ld (0x7000), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x6000), a
        ld (0x7000), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x6000), a
        ld (0x7000), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x6000), a
        ld (0x7000), a
        ld (0x77FF), a
        ld (0x77FF), a
        ld (0x77FF), a
        ret
