; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; mapper_tag_ascii8.asm - the mapper tag of the geo3d ASCII8 MegaROM
; (basic/g3basic.asm), included in bank 0 after the fixed entry points.
; Nothing jumps to it: none of it ever runs.
;
; The ROM type signature itself, 8 bytes at 4010h (file offset 0010h, right
; after the 16-byte "AB" header), is written by the source:
;         defb "ROM_ASC8"
; the ASCII8 code of MSXgl's "ROM type signature" 1.0 (2024-02-18), the
; same place as the ASCII16X, ROM_NEO8 and ROM_NE16 that openMSX reads.
;
; Then, here:
; - the mapper in words, for a person looking at a hex dump (0 at the end;
;   no "2" in it, see below);
; - 40 LD (nnnn),A to ASCII8 bank registers, for the launchers and
;   emulators that guess the mapper by counting them (openMSX's
;   guessRomType, and the launchers that copy it; tools/mapper_guess.py):
;   8 each to 6000h, 6800h and 7000h and 16 to 7800h. 6800h and 7800h are
;   only scored for ASCII8, 6000h and 7000h also for ASCII16 (which wins a
;   tie in openMSX) and for Konami / Konami SCC.
; ============================================================================
        defb "ASCII8 MegaROM, mapper ASCII8 (8 KB banks, registers 6000h, 6800h, 7000h, 7800h). geo3d BASIC", 0
        ld (0x6000), a
        ld (0x6800), a
        ld (0x7000), a
        ld (0x7800), a
        ld (0x7800), a
        ld (0x6000), a
        ld (0x6800), a
        ld (0x7000), a
        ld (0x7800), a
        ld (0x7800), a
        ld (0x6000), a
        ld (0x6800), a
        ld (0x7000), a
        ld (0x7800), a
        ld (0x7800), a
        ld (0x6000), a
        ld (0x6800), a
        ld (0x7000), a
        ld (0x7800), a
        ld (0x7800), a
        ld (0x6000), a
        ld (0x6800), a
        ld (0x7000), a
        ld (0x7800), a
        ld (0x7800), a
        ld (0x6000), a
        ld (0x6800), a
        ld (0x7000), a
        ld (0x7800), a
        ld (0x7800), a
        ld (0x6000), a
        ld (0x6800), a
        ld (0x7000), a
        ld (0x7800), a
        ld (0x7800), a
        ld (0x6000), a
        ld (0x6800), a
        ld (0x7000), a
        ld (0x7800), a
        ld (0x7800), a
        ret
