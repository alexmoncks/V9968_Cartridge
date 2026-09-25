; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; g3names.asm  -  name test ROM for the geo3d MSX-BASIC extension
;
; A 16 KB ROM at 4000h whose CALL handler prints what MSX-BASIC hands it for
; every CALL (or _) statement, before any real command is written:
;   [NAME] xx xx ...  args
; NAME is PROCNM as the CALL dispatcher copied it, the xx are the raw program
; bytes that follow the name (up to 12, stopping at the end of the line), and
; args are the arguments evaluated one by one by FRMEVL: integers in decimal
; with %, singles (!) and doubles (#) as the BCD bytes of DAC, strings in quotes.
; Names that start with G are taken (carry clear); any other name is printed
; with the raw bytes and passed on (carry set) to the next cartridge.
;
; Build: z80asm -o g3names.rom g3names.asm (16384 bytes, ROM type Normal).
; ============================================================================

CHPUT:   equ 0x00A2              ; BIOS: print the character in A
CALBAS:  equ 0x0159              ; BIOS: call IX in the BASIC ROM
ERROR:   equ 0x406F              ; BASIC: error E
CHRGTR:  equ 0x4666              ; BASIC: next char at HL, spaces skipped
FRMEVL:  equ 0x4C64              ; BASIC: evaluate the expression at HL
FRESTR:  equ 0x67D0              ; BASIC: free the temporary string in DAC
VALTYP:  equ 0xF663              ; type of DAC: 2, 3 (string), 4 or 8
DAC:     equ 0xF7F6
PROCNM:  equ 0xFD89              ; name of the CALL, 0-terminated

        org 0x4000
        defb "AB"
        defw 0                  ; INIT
        defw stmt               ; STATEMENT
        defw 0                  ; DEVICE
        defw 0                  ; TEXT
        defs 6

; ---- all output goes through putc (4010h), where a debugger can log it -----
putc:   jp CHPUT

; ---- STATEMENT handler (4013h): HL = program text right after the name ----
stmt:   push hl
        ld a,'['
        call putc
        ld hl,PROCNM
        call puts
        ld a,']'
        call putc
        pop hl
        push hl
        ld b,12
raw:    ld a,(hl)
        or a
        jr z,raw_end
        call puthex
        inc hl
        djnz raw
raw_end:
        pop hl
        ld a,(PROCNM)
        cp 'G'
        jr z,ours
        call crlf
        scf                     ; not ours: HL unchanged, next cartridge
        ret

; RST 08h/10h lead into the BASIC ROM in page 1, where this ROM sits, so
; CHRGTR and everything else in BASIC is called through CALBAS.
ours:   dec hl
        call chrgtr             ; A = first char after the name
        cp '('
        jr nz,done
arg:    call chrgtr             ; past '(' or ','
        ld ix,FRMEVL
        call CALBAS
        push hl
        call putval
        pop hl
        ld a,(hl)
        cp ','
        jr z,arg
        cp ')'
        jr nz,synerr
        call chrgtr             ; past ')'
done:   call crlf
        or a                    ; handled: HL at the end of the statement
        ret

synerr: ld e,2                  ; Syntax error
        ld ix,ERROR
        jp CALBAS

chrgtr: ld ix,CHRGTR
        jp CALBAS

; ---- print the value FRMEVL left in DAC ------------------------------------
putval: ld a,' '
        call putc
        ld a,(VALTYP)
        cp 3
        jr z,pv_str
        cp 2
        jr nz,pv_bcd
        ld hl,(DAC+2)           ; integer: signed decimal and %
        call pdec
        ld a,'%'
        jp putc
; FOUT sits in page 0 but calls into the BASIC ROM in page 1, so CALBAS
; cannot reach it from here: singles (!) and doubles (#) are shown as the
; BCD bytes of DAC (exponent, then 3 or 7 bytes of mantissa).
pv_bcd: ld b,a                  ; 4 or 8 bytes
        ld c,'!'
        cp 4
        jr z,pv_b1
        ld c,'#'
pv_b1:  ld a,c
        call putc
        ld hl,DAC
pv_b2:  ld a,(hl)
        call nibble2
        inc hl
        djnz pv_b2
        ret
pv_str: ld a,'"'
        call putc
        ld hl,(DAC+2)           ; string descriptor: length, address
        ld b,(hl)
        inc hl
        ld e,(hl)
        inc hl
        ld d,(hl)
        ex de,hl
        ld a,b
        or a
        jr z,pv_q
pv_ch:  ld a,(hl)
        call putc
        inc hl
        djnz pv_ch
pv_q:   ld a,'"'
        call putc
        ld ix,FRESTR
        jp CALBAS

; ---- helpers ---------------------------------------------------------------
puts:   ld a,(hl)
        or a
        ret z
        call putc
        inc hl
        jr puts

pdec:   bit 7,h                 ; HL as a signed decimal
        jr z,pd_pos
        ld a,'-'
        call putc
        xor a
        sub l
        ld l,a
        sbc a,a
        sub h
        ld h,a
pd_pos: ld e,0                  ; E bit 0: a digit was printed
        ld bc,-10000
        call pd_dig
        ld bc,-1000
        call pd_dig
        ld bc,-100
        call pd_dig
        ld bc,-10
        call pd_dig
        ld a,l
        add a,'0'
        jp putc
pd_dig: ld a,'0'-1
pd_lp:  inc a
        add hl,bc
        jr c,pd_lp
        sbc hl,bc               ; carry is clear: undo the last step
        cp '0'
        jr nz,pd_out
        bit 0,e
        ret z                   ; no leading zeros
pd_out: ld e,1
        jp putc

puthex: push af
        ld a,' '
        call putc
        pop af
nibble2:
        push af
        rrca
        rrca
        rrca
        rrca
        call nibble
        pop af
nibble: and 0x0F
        add a,0x90
        daa
        adc a,0x40
        daa
        jp putc

crlf:   ld a,13
        call putc
        ld a,10
        jp putc

        defs 0x8000 - $, 0xFF
