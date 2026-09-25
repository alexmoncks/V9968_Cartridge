; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; g3basic.asm  -  geo3d MSX-BASIC extension ROM, skeleton (version 0.1)
;
; CALL G3xxx (or _G3xxx) statements for the geo3d 3D coprocessor next to a
; V9968, as specified in docs/BASIC_API.md. This skeleton has the ROM frame
; (INIT, work area, hooks, CALL dispatch, argument reader) and two commands:
;   G3INIT [(modo [, base])]   V9968 mode, palette, pages 0 and 1 cleared,
;                              geo3d set up for SCREEN 5, scene defaults
;   G3END                      V9958 mode, MSX palette, page 0, BASIC colours
; Profiles (spec section 2), looked for at boot and at every G3INIT, 88h first:
;   88h  V9968 cartridge at 88h-8Ch, geo3d at 8Dh/8Fh. The internal VDP is
;        never touched, so this profile also works on MSX1.
;   98h  the V9968 is the machine's VDP (98h-9Ch), geo3d at 9Dh/9Fh.
;
; ROM: 64 KB ASCII8 MegaROM (openMSX -romtype ASCII8). Bank 0 is the fixed
; code bank at 4000h-5FFFh and holds everything for now. Banks 1-7 are
; reserved (FFh) for data mapped at 6000h-7FFFh later (built-in models, the
; G3TITLE font, texture 0). The mapper powers on with bank 0 in all four
; windows, so INIT first maps bank 1 at 6000h, 8000h and A000h: otherwise
; the BIOS finds our "AB" again at 8000h, runs INIT twice and calls the
; STATEMENT handler twice per CALL. Page 2 is never mapped after that.
;
; RAM (spec 7.6, corrected by the research notes):
;   Work area: WKSIZE bytes of page-3 RAM just under HIMEM. It is reserved
;   at the first CLEARC after all the INITs, by our H.CLEA hook (do_clea):
;   lowering HIMEM in INIT breaks the Disk ROMs (DOS2 turns itself off when
;   HIMEM is not F380h, DOS1 keeps a fixed area at F1C9h), and HIMEM alone
;   is not enough (BASIC's file table, string space and stack are laid out
;   from HIMEM before the ROM search). The work area address is kept in our
;   page-1 SLTWRK entry; G3INIT writes its contents (signature, slot, the
;   page-3 trampoline, scene state), and every command checks it.
;   SLTWRK group of our slot (8 bytes at FD09h + 32*P + 8*S, all ours: the
;   ROM is in pages 1 and 2 of its slot, nothing is in pages 0 and 3):
;     +0 flags   bit 0: our H.TIMI hook is in the chain
;                bit 1: the work area was signed by a G3INIT in this boot and
;                       has stayed reserved since (RAM, and so the old
;                       signature, survives a reset; INIT clears this group)
;                bit 2: installed without a work area (H.CLEA was taken)
;                bit 3: G3INIT set a V9968 to V9968 mode, G3END has not
;                       undone it yet (also when the work area is lost)
;                bit 4: the port of bit 3: 1 = 98h, 0 = 88h
;     +1 old H.TIMI hook, byte 0      +2 work area address (page-1 entry)
;     +4 old H.TIMI hook, bytes 1-4
;   The old hook is kept here and not in the work area, so the chain still
;   works when the work area is gone (after a trip to MSX-DOS, for example).
; Hooks: CALLF format (RST 30h, slot, address, RET) to handlers in this bank,
;   never a JP into RAM: the Kanji driver and the DOS1 kernel chain the hook
;   they find assuming that format (a JP hook crashed CALL KANJI).
;   H.CLEA  installed at INIT (unless G is held). If a ROM earlier in slot
;           order already took it, nothing is chained (TODO: the group has
;           no room for a second old hook): the ROM stays on without a work
;           area, says so in the banner, and G3INIT gives Out of memory.
;   H.TIMI  installed at the first G3INIT, chained to the hook found there
;           (CALLF or JP formats), inert unless a G3INIT is active. G3END
;           leaves it installed, as the spec asks, so that hooks chained
;           after it keep working. A G3INIT puts it back if a program took
;           it out (H.TIMI back to RET or to the hook we chain to).
;
; BASIC calls (spec 7.7): RST 08h, 10h and 28h lead into the BASIC ROM in
; page 1, where this ROM sits, and hang: CHRGTR, FRMEVL, FRESTR and ERROR go
; through CALBAS. Page-0 routines that jump into page 1 (FRCINT, FOUT, ...)
; need the trampoline in the work area (tramp); numbers are converted here
; instead (dacint: all numeric types, rounded, Overflow).
;
; VDP rules (spec 7.2, 7.3): the command handler starts with EI, every wait
; has a loop timeout of about 2 s (Device I/O error, also on the R800) and
; checks CTRL+STOP, which does not cut the wait short: the error comes once
; geo3d or the command engine is idle. A VDP command that never ends is
; stopped (CMD = STOP) at the timeout. 98h: S#0 is never read, S#2 is read
; in DI windows (R#15 = 2, IN, R#15 = 0), every two-byte port 99h write is
; done with interrupts off, and the BIOS copies RG2SAV, RG7SAV, RG20SAV and
; RG21SAV follow our writes (BASIC's SCREEN rewrites R#20/R#21 from RG20SAV/
; RG21SAV). 88h: the V9968 interrupts stay off (IE0 = IE1 = 0, R#20 = 01h,
; R#21 = 00h), R#15 is left at 0.
;
; Fixed addresses (breakpoints in tests.tcl): 4010h "G3BASIC" id, 4020h
; INIT, 4023h STATEMENT, 4026h H.CLEA, 4029h H.TIMI, 402Dh "not ours" exit.
;
; Build: build.sh (z80asm -o out/G3BASIC.ROM g3basic.asm, 65536 bytes).
; Tests: run_tests.sh (openMSX: turbo R 98h; MSX2+, MSX2, MSX1 88h).
; ============================================================================

VER_MAJ: equ 0
VER_MIN: equ 1

; ---- BIOS (page 0) ---------------------------------------------------------
CALSLT:  equ 0x001C              ; call IX in slot IYH
ENASLT:  equ 0x0024              ; page of H = slot A (leaves DI)
MSXVER:  equ 0x002D              ; 0 MSX1, 1 MSX2, 2 MSX2+, 3 turbo R
CHGCLR:  equ 0x0062              ; screen colours from FORCLR/BAKCLR/BDRCLR
CHPUT:   equ 0x00A2              ; print A
BREAKX:  equ 0x00B7              ; carry: CTRL+STOP held
RSLREG:  equ 0x0138              ; A = primary slot register
SNSMAT:  equ 0x0141              ; A = keyboard row A (ends with EI)
CALBAS:  equ 0x0159              ; call IX in the BASIC ROM
EXTROM:  equ 0x015F              ; call IX in the SUB-ROM (MSX2 and later)
GETCPU:  equ 0x0183              ; turbo R: A = 0 Z80, 1-2 R800 (page 0 only)

; ---- SUB-ROM (through EXTROM) ------------------------------------------------
INIPLT:  equ 0x0141              ; MSX palette: VDP and BASIC's VRAM table
SETPLT:  equ 0x014D              ; colour D = A (red*16 + blue), E (green)

; ---- BASIC ROM (page 1: through CALBAS only) --------------------------------
ERROR:   equ 0x406F              ; error E
CHRGTR:  equ 0x4666              ; next character at HL, spaces skipped
FRMEVL:  equ 0x4C64              ; evaluate the expression at HL
FRESTR:  equ 0x67D0              ; free the temporary string in DAC

; ---- system variables ----------------------------------------------------------
RG2SAV:  equ 0xF3E1
RG7SAV:  equ 0xF3E6
BAKCLR:  equ 0xF3EA
BDRCLR:  equ 0xF3EB
VALTYP:  equ 0xF663              ; type of DAC: 2 integer, 3 string, 4, 8
MEMSIZ:  equ 0xF672
STKTOP:  equ 0xF674
FRETOP:  equ 0xF69B
SAVSTK:  equ 0xF6B1
VARTAB:  equ 0xF6C2
DAC:     equ 0xF7F6
MAXFIL:  equ 0xF85F
FILTAB:  equ 0xF860
NULBUF:  equ 0xF862
DPPAGE:  equ 0xFAF5
ACPAGE:  equ 0xFAF6
HIMEM:   equ 0xFC4A
SCRMOD:  equ 0xFCAF
EXPTBL:  equ 0xFCC1
SLTWRK:  equ 0xFD09
PROCNM:  equ 0xFD89              ; CALL name, 0-terminated, upper case
HTIMI:   equ 0xFD9F
HCLEA:   equ 0xFED0
RG20SAV: equ 0xFFF3              ; MSX2 and later; written in 98h only
RG21SAV: equ 0xFFF4

; ---- ASCII8 mapper -----------------------------------------------------------
BANK6:   equ 0x6800              ; bank at 6000h-7FFFh
BANK8:   equ 0x7000              ; bank at 8000h-9FFFh
BANKA:   equ 0x7800              ; bank at A000h-BFFFh
BANK_FF: equ 1                   ; a reserved bank: FFh, no "AB"

; ---- work area (page 3, WKSIZE bytes, address in SLTWRK) --------------------
WKSIZE:   equ 2048
W_SIG:    equ 0                  ; "G3WK" once G3INIT has written the area
W_SLOT:   equ 4                  ; our slot (ENASLT format)
W_FLAGS:  equ 5                  ; bit 0: active (G3INIT done, no G3END yet)
                                 ; bit 1: G3INIT changed the V9968 and BASIC's
                                 ; colours, G3END has not undone it yet
W_PORT:   equ 6                  ; port base: 88h or 98h
W_MODE:   equ 7                  ; screen mode (5)
W_GVER:   equ 8                  ; geo3d byte at index 49h (FFh: version 0)
W_BAKCLR: equ 10                 ; BAKCLR and BDRCLR before G3INIT changed
                                 ; them (valid while W_FLAGS bit 1 is set)
W_BDRCLR: equ 11
W_BG:     equ 12                 ; ---- scene state, in scene_def order ----
W_PACE:   equ 13                 ; blanks per frame
W_SHOW:   equ 14                 ; page shown
W_DRAW:   equ 15                 ; page drawn by geo3d
W_VIEWW:  equ 16                 ; 3D window: width, height, top line
W_VIEWH:  equ 18
W_VIEWY:  equ 20
W_ZOOM:   equ 22                 ; zoom in %
W_CAM:    equ 24                 ; camera x, y, z
W_LOOK:   equ 30                 ; point looked at x, y, z
W_LIGHT:  equ 36                 ; direction of the light x, y, z
W_ARGC:   equ 42                 ; argument reader: bit k = argument k given
W_ARGV:   equ 44                 ; 8 arguments, int16
W_TMP:    equ 60                 ; 16 bytes: VDP command block
W_TRAMP:  equ 80                 ; the trampoline (tr_tpl), 48 bytes at most
W_OBJ:    equ 128                ; NOBJ objects of OBJSZ bytes (0 = empty)
NOBJ:     equ 16
OBJSZ:    equ 48
W_FREE:   equ W_OBJ + NOBJ * OBJSZ    ; 896: model and texture directories,
                                      ; sort list, counters (TODO)

; ---- SLTWRK group ------------------------------------------------------------
G_FLAGS:  equ 0                  ; bits: see the header
G_OLD0:   equ 1                  ; old H.TIMI byte 0
G_BLK:    equ 2                  ; work area address
G_OLD1:   equ 4                  ; old H.TIMI bytes 1-4
GF_TIMI:  equ 0                  ; G_FLAGS bit numbers
GF_SIGN:  equ 1
GF_NOWK:  equ 2
GF_UNDO:  equ 3
GF_98:    equ 4

; ---- BASIC error codes ---------------------------------------------------------
ERRSN:    equ 2                  ; Syntax error
ERRFC:    equ 5                  ; Illegal function call
ERROV:    equ 6                  ; Overflow
ERROM:    equ 7                  ; Out of memory
ERRTM:    equ 13                 ; Type mismatch
ERRIO:    equ 19                 ; Device I/O error
T_ELSE:   equ 0xA1               ; ELSE token

; ============================================================================
; bank 0: 4000h-5FFFh
; ============================================================================
        org 0x4000
        defb "AB"
        defw init
        defw stmt
        defw 0                  ; DEVICE
        defw 0                  ; TEXT
        defs 6, 0
        defb "G3BASIC", 0       ; 4010h: id for the test harness
        defb VER_MAJ, VER_MIN
        defs 0x4020 - $, 0

init:   jp do_init              ; 4020h
stmt:   jp do_stmt              ; 4023h
clea:   jp do_clea              ; 4026h: H.CLEA (CALLF)
timi:   jp do_timi              ; 4029h: H.TIMI (CALLF)
pass:   scf                     ; 402Ch: STATEMENT, not ours:
        ret                     ; carry set, HL unchanged

; ============================================================================
; INIT: runs once at boot, with interrupts off, before BASIC starts.
; ============================================================================
do_init:
        ld a,BANK_FF
        ld (BANK6),a
        ld (BANK8),a            ; no second "AB" at 8000h (see the header)
        ld (BANKA),a
        call getslt
        ld b,a                  ; B = our slot
        call wrkgrp
        push hl
        ld c,8
in_clr: ld (hl),0               ; RAM survives a reset: no work area, no hooks
        inc hl
        dec c
        jr nz,in_clr
        push bc
        ld a,3
        call SNSMAT             ; row 3, bit 4 = G (0: held); ends with EI
        di
        pop bc
        pop hl
        and 0x10
        ret z                   ; G held: nothing installed, nothing reserved
        ld a,(HCLEA)
        cp 0xC9
        jr z,in_hook
        set GF_NOWK,(hl)        ; taken by a ROM earlier in slot order (TODO:
        jr in_find              ; chain it): on, but without a work area
in_hook:
        ld hl,HCLEA             ; H.CLEA = RST 30h, slot, clea, RET
        ld (hl),0xF7
        inc hl
        ld (hl),b
        inc hl
        ld de,clea
        ld (hl),e
        inc hl
        ld (hl),d
        inc hl
        ld (hl),0xC9
        ; banner: the profile found now (G3INIT looks again). BASIC clears
        ; the screen for its own banner right after the ROM search (INITXT
        ; on MSX1, SUB-ROM 0189h on MSX2 and later), so this line is only
        ; seen for a moment during boot.
in_find:
        call findprof           ; C = 88h, 98h, or 0
        ld hl,ban_1
        call puts
        ld a,c
        or a
        ld hl,ban_no
        jr z,in_ban
        call puthex
        ld hl,ban_h
in_ban: call puts
        call grp
        bit GF_NOWK,(hl)
        ld hl,ban_nw
        call nz,puts
in_end: di
        ret

ban_1:  defb "geo3d BASIC ", 0x30 + VER_MAJ, 0x2E, 0x30 + VER_MIN
        defb " (", 0
ban_h:  defb "h)", 13, 10, 0
ban_no: defb "none)", 13, 10, 0
ban_nw: defb "H.CLEA in use: no work area", 13, 10, 0

; ============================================================================
; H.CLEA handler: BASIC CLEARC (62A1h) at boot, NEW, RUN, CLEAR, CLEAR n,m and
; every program line typed. Keeps all registers (BASIC uses HL at 62A4h).
; Never writes the work area: at the first call its space still holds the
; live stack. When a CLEAR leaves no room to put HIMEM back on the work area,
; the area is given up (GF_SIGN cleared): G3END can still undo the V9968
; mode from GF_UNDO, and the next G3INIT starts a clean area.
; ============================================================================
do_clea:
        push af
        push bc
        push de
        push hl
        call getslt
        call wrkgrp
        inc hl
        inc hl                  ; G_BLK
        ld e,(hl)
        inc hl
        ld d,(hl)
        ld a,d
        or e
        jr nz,cl_have
        push hl                 ; first CLEARC after the INITs (and after the
        ld hl,(HIMEM)           ; Disk ROMs took their share): the work area
        ld de,0 - WKSIZE        ; goes right under HIMEM
        add hl,de
        ex de,hl
        pop hl
        ld (hl),d
        dec hl
        ld (hl),e
cl_have:
        ld hl,(HIMEM)
        or a
        sbc hl,de
        jr c,cl_done
        jr z,cl_done            ; HIMEM <= work area: still reserved
        ex de,hl
        call relayout           ; HIMEM = work area (carry: no room, unchanged)
        jr nc,cl_done
        call grp                ; no room: the work area is BASIC's memory
        res GF_SIGN,(hl)        ; now, its contents can no longer be trusted
cl_done:
        pop hl
        pop de
        pop bc
        pop af
        ret

; relayout: HIMEM = HL, then FILTAB, the FCBs, NULBUF, MEMSIZ and STKTOP from
; it, keeping the string space size, as BASIC's 7E6Bh (MAXFILES=) does but
; without touching SP (CLEARC sets FRETOP and SP right after H.CLEA).
; Carry: not enough memory (the 7E6Bh test), nothing changed.
relayout:
        push hl                 ; new HIMEM
        ld a,(MAXFIL)
        ld de,0 - 267           ; per file: 2-byte pointer + 265-byte FCB
rl_1:   add hl,de
        dec a
        jp p,rl_1
        ex de,hl                ; DE = new FILTAB
        ld hl,(MEMSIZ)
        ld bc,(STKTOP)
        or a
        sbc hl,bc               ; HL = string space size
        push hl
        ld bc,140
        add hl,bc
        ld bc,(VARTAB)
        add hl,bc
        or a
        sbc hl,de               ; carry: VARTAB + size + 140 < FILTAB
        pop bc                  ; BC = string space size
        pop hl                  ; HL = new HIMEM
        ccf
        ret c
        ld (HIMEM),hl
        ld (FILTAB),de
        ld l,e
        ld h,d
        dec hl
        dec hl
        ld (MEMSIZ),hl
        ld (FRETOP),hl
        or a
        sbc hl,bc
        ld (STKTOP),hl
        dec hl
        dec hl
        ld (SAVSTK),hl
        ld a,(MAXFIL)
        ld l,a
        inc l
        ld h,0
        add hl,hl
        add hl,de
        ex de,hl                ; DE = FCB 0, HL = FILTAB
        push de
        ld bc,0x0109
rl_2:   ld (hl),e
        inc hl
        ld (hl),d
        inc hl
        ex de,hl
        ld (hl),0               ; FCB mode: closed
        add hl,bc
        ex de,hl
        dec a
        jp p,rl_2
        pop hl
        ld bc,9
        add hl,bc
        ld (NULBUF),hl
        ret

; ============================================================================
; H.TIMI handler (CALLF from FD9Fh): vertical blank only, interrupts off,
; A = S#0 (the BIOS stores it in STATFL afterwards: AF is kept). The BIOS
; interrupt routine saved IX and IY. Inert for now; the 98h page flip of
; G3FRAME goes where the TODO is. Then the hook found at G3INIT runs.
; ============================================================================
do_timi:
        push af
        push bc
        push de
        push hl
        call blkact             ; carry: no active work area (or not ours any
        jr c,ti_chain           ; more, e.g. after MSX-DOS)
        ; TODO: pending page flip (98h): R#2, RG2SAV, DPPAGE, ACPAGE, if
        ; SCRMOD is still the graphic mode.
ti_chain:
        call getslt
        call wrkgrp
        inc hl                  ; G_OLD0
        ld a,(hl)
        inc hl
        inc hl
        inc hl                  ; G_OLD1
        cp 0xF7
        jr z,ti_callf
        cp 0xC3
        jr z,ti_jp
        pop hl                  ; RET (C9h): nothing to chain
        pop de
        pop bc
        pop af
        ret
ti_jp:  ld e,(hl)               ; JP nnnn
        inc hl
        ld d,(hl)
        push de
        pop ix
        pop hl
        pop de
        pop bc
        pop af
        jp (ix)
ti_callf:                       ; RST 30h, slot, nnnn: what CALLF does
        ld a,(hl)
        inc hl
        ld e,(hl)
        inc hl
        ld d,(hl)
        push de
        pop ix
        push af
        pop iy                  ; IYH = slot
        pop hl
        pop de
        pop bc
        pop af
        jp CALSLT

; timi_hook: put our CALLF in H.TIMI (flag GF_TIMI in the SLTWRK group),
; keeping the hook found there for ti_chain. A hook in an unknown format (not
; RET, CALLF or JP) is left alone and ours is not installed (TODO).
; With the flag set, ours is taken for still in the chain unless H.TIMI is
; back to RET or to the very hook we chain to: then a program or a driver's
; uninstall took ours out, and it goes in again. Anything else (ours, or a
; hook chained after ours) is left alone.
timi_hook:
        call getslt
        ld b,a
        call wrkgrp             ; HL = group
        bit GF_TIMI,(hl)
        jr z,th_new
        ld a,(HTIMI)
        cp 0xC9
        jr z,th_lost
        push hl
        call th_same
        pop hl
        ret nz                  ; still in the chain
th_lost:
        res GF_TIMI,(hl)
th_new: ld a,(HTIMI)
        cp 0xF7
        jr nz,th_fmt
        ld a,(HTIMI+1)          ; our own CALLF already there (flag lost):
        cp b                    ; never chain to ourselves
        jr nz,th_ok
        ld de,(HTIMI+2)
        ld a,e
        cp timi & 0xFF
        jr nz,th_ok
        ld a,d
        cp timi >> 8
        jr nz,th_ok
        set GF_TIMI,(hl)
        ret
th_fmt: cp 0xC9
        jr z,th_ok
        cp 0xC3
        ret nz
th_ok:  di
        push hl
        ld a,(HTIMI)
        inc hl
        ld (hl),a               ; G_OLD0
        inc hl
        inc hl
        inc hl
        ex de,hl                ; DE = G_OLD1
        ld hl,HTIMI+1
        ld c,4
th_cp:  ld a,(hl)
        ld (de),a
        inc hl
        inc de
        dec c
        jr nz,th_cp
        ld hl,HTIMI             ; RST 30h, slot, timi, RET
        ld (hl),0xF7
        inc hl
        ld (hl),b
        inc hl
        ld de,timi
        ld (hl),e
        inc hl
        ld (hl),d
        inc hl
        ld (hl),0xC9
        pop hl
        set GF_TIMI,(hl)        ; in the chain
        ei
        ret

; th_same: Z when H.TIMI holds the old hook kept in the group at HL. Keeps B.
th_same:
        inc hl
        ld a,(HTIMI)
        cp (hl)                 ; G_OLD0
        ret nz
        inc hl
        inc hl
        inc hl                  ; G_OLD1
        ld de,HTIMI+1
        ld c,4
ts_1:   ld a,(de)
        cp (hl)
        ret nz
        inc de
        inc hl
        dec c
        jr nz,ts_1
        ret

; ============================================================================
; STATEMENT handler: HL = program text right after the name, name in PROCNM.
; Ours: carry clear, HL at the end of the statement. Not ours: carry set,
; HL unchanged (pass).
; ============================================================================
do_stmt:
        ld a,(PROCNM)
        cp 'G'
        jp nz,pass
        ld a,(PROCNM+1)
        cp '3'
        jp nz,pass
        push hl
        ld hl,cmdtab
st_ent: ld de,PROCNM+2
st_cmp: ld a,(de)
        cp (hl)
        jr nz,st_skip
        inc hl
        inc de
        or a
        jr nz,st_cmp
        ld c,(hl)               ; found: flags, address
        inc hl
        ld e,(hl)
        inc hl
        ld d,(hl)
        call getblk
        ld a,h
        or l
        jr nz,st_inst
        call grp                ; no work area: ours only if INIT stayed on
        bit GF_NOWK,(hl)        ; without one (H.CLEA taken)
st_inst:
        pop hl
        jp z,pass               ; nothing installed (G held at boot): not ours
        ei                      ; an inter-slot call may have left DI
        bit 0,c
        jr z,st_go
        push de
        push hl
        call blkact
        pop hl
        pop de
        jp c,err_fc             ; the command needs an active G3INIT
st_go:  push de
        ret                     ; to the command, HL = text
st_skip:
        ld a,(hl)               ; rest of this name
        inc hl
        or a
        jr nz,st_skip
        inc hl                  ; flags
        inc hl
        inc hl                  ; address
        ld a,(hl)
        or a
        jr nz,st_ent
        pop hl                  ; a G3 name that is not implemented: BASIC
        jp pass                 ; reports Syntax error

; Command table: name without "G3", 0, flags (bit 0: needs an active
; G3INIT, checked by the dispatcher), address. Only implemented commands.
cmdtab: defb "INIT", 0, 0
        defw g3init
        defb "END", 0, 0
        defw g3end
        defb 0

; ============================================================================
; G3INIT [(modo [, base])]  (spec section 4)
;   modo: 5 (7 and 8 are valid in the spec but not implemented yet: Illegal
;         function call, as for any other value). 98h: the BASIC screen must
;         be SCREEN 5. 88h: without modo, SCREEN 7 or 8 in BASIC means 7 or 8
;         (not yet: Illegal function call), anything else means 5.
;   base: &H88 or &H98 forces that profile (Device I/O error if it is not
;         there), otherwise 88h then 98h.
; ============================================================================
g3init:
        push hl
        call blkchk
        pop hl
        jp c,err_om             ; work area not reserved (HIMEM moved up)
        ld b,2
        call getargs            ; syntax and values checked before any change
        push hl                 ; text pointer, returned at the end
        call getblk
        push hl
        pop ix
        bit 0,(ix+W_ARGC)
        jr z,gi_base
        ld a,(ix+W_ARGV+1)
        or a
        jp nz,err_fc
        ld a,(ix+W_ARGV)
        cp 5
        jp nz,err_fc            ; TODO: 7 (SCREEN 7) and 8 (SCREEN 8, EPAL)
gi_base:
        ld c,0                  ; 0: look for a profile
        bit 1,(ix+W_ARGC)
        jr z,gi_find
        ld a,(ix+W_ARGV+3)
        or a
        jp nz,err_fc
        ld a,(ix+W_ARGV+2)
        ld c,a
        cp 0x88
        jr z,gi_find
        cp 0x98
        jp nz,err_fc
gi_find:
        ld a,c
        or a
        jr nz,gi_forced
        call findprof
        jr gi_found
gi_forced:
        call detect
gi_found:
        jp nz,err_io            ; no V9968 with geo3d there
        ld a,c
        cp 0x98
        jr nz,gi_88
        ld a,(SCRMOD)           ; 98h: the BASIC screen is the mode
        cp 5
        jp nz,err_fc            ; text mode (or 7, 8: TODO)
        jr gi_ok
gi_88:  bit 0,(ix+W_ARGC)
        jr nz,gi_ok             ; modo given (5)
        ld a,(SCRMOD)
        cp 7
        jp z,err_fc             ; TODO: SCREEN 7
        cp 8
        jp z,err_fc             ; TODO: SCREEN 8
gi_ok:  ; ---- all checked: from here on the command only changes things
        call sigchk
        jr z,gi_old
        push bc                 ; not signed yet (or lost): a clean work area
        push de
        push ix
        pop hl
        ld bc,WKSIZE
        call zero
        pop de
        pop bc
gi_old: bit 1,(ix+W_FLAGS)
        jr nz,gi_hdr            ; not undone yet: keep the colours saved then
        ld a,(BAKCLR)           ; (a G3INIT that failed half way, for example,
        ld (ix+W_BAKCLR),a      ; has already set them to 0)
        ld a,(BDRCLR)
        ld (ix+W_BDRCLR),a
        set 1,(ix+W_FLAGS)
gi_hdr: ld (ix+W_SIG),'G'
        ld (ix+W_SIG+1),'3'
        ld (ix+W_SIG+2),'W'
        ld (ix+W_SIG+3),'K'
        call grp
        set GF_SIGN,(hl)        ; signed in this boot
        set GF_UNDO,(hl)        ; G3END undoes it, even without the work area
        res GF_98,(hl)
        ld a,c
        cp 0x98
        jr nz,gi_h88
        set GF_98,(hl)
gi_h88: call getslt
        ld (ix+W_SLOT),a
        res 0,(ix+W_FLAGS)      ; inactive until the end
        ld (ix+W_PORT),c
        ld (ix+W_GVER),e
        ld (ix+W_MODE),5
        call tr_copy
        call scene_reset        ; 16 empty objects, camera, light, window...
        ; ---- spec section 4, steps 1 to 5
        call wait_geo           ; 1. geo3d and the VDP command engine idle
        call wait_ce
        call v68_on             ; 2. V9968 mode, high-speed commands, window
        ld a,(ix+W_PORT)
        cp 0x98
        call nz,scr5_88         ;    88h: the V9968 into SCREEN 5
        call border0            ; 3. border 0 and the default palette
        ld hl,pal_g3
        call pal_load
        xor a
        call hmmv               ; 4. clear lines 0-211 of pages 0 and 1,
        call wait_ce
        ld a,1
        call hmmv
        call wait_ce
        call show0              ;    show page 0, draw on page 1
        call geo_cfg            ; 5. geo3d set up for SCREEN 5
        ; TODO: copy texture 0 from its bank; clear the model directory
        ; (16-31) and the texture slots (1-8) (spec steps 5 and 6).
        call timi_hook
        set 0,(ix+W_FLAGS)      ; active
        pop hl
        or a
        ret

; ============================================================================
; G3END (spec section 4). It undoes what G3INIT changed, also after a G3INIT
; that stopped half way with an error. With nothing to undo it does nothing,
; so ON ERROR handlers can always call it. When the work area is gone (a
; CLEAR gave it to BASIC) but GF_UNDO says the V9968 is still in V9968 mode,
; it undoes the V9968 side with a stand-in area on the stack: the port from
; GF_98, and BASIC's colours as they are (the saved ones are lost).
; ============================================================================
g3end:
        call noargs
        push hl
        call blkchk
        jr c,ge_lost            ; not reserved any more
        push hl
        pop ix
        call sigchk
        jr nz,ge_lost           ; not signed in this boot
        bit 1,(ix+W_FLAGS)
        call nz,undo            ; (nothing to undo: nothing done)
        jr ge_done
ge_lost:
        call grp
        bit GF_UNDO,(hl)
        jr z,ge_done            ; nothing to undo
        ld a,0x88
        bit GF_98,(hl)
        jr z,ge_l1
        ld a,0x98
ge_l1:  ld hl,0 - (W_BDRCLR + 1)
        add hl,sp
        ld sp,hl                ; the stand-in: W_FLAGS to W_BDRCLR
        push hl
        pop ix
        ld (ix+W_FLAGS),0
        ld (ix+W_PORT),a
        ld a,(BAKCLR)
        ld (ix+W_BAKCLR),a
        ld a,(BDRCLR)
        ld (ix+W_BDRCLR),a
        call undo
        ld hl,W_BDRCLR + 1
        add hl,sp
        ld sp,hl
ge_done:
        pop hl
        or a
        ret

; undo: G3END's work on the work area (or stand-in) at IX.
undo:   call wait_geo
        call wait_ce
        call v68_off            ; V9958 mode (EPAL off before the palette)
        ld a,(ix+W_PORT)
        cp 0x98
        jr z,ge_98
        ld hl,pal_msx           ; 88h: the V9968 only
        call pal_load
        ld a,0x1F
        ld b,2
        call vreg               ; page 0
        jr ge_off
ge_98:  push ix
        ld ix,INIPLT
        call EXTROM             ; MSX palette, also in BASIC's table
        pop ix
        ld a,(SCRMOD)
        cp 5
        jr c,ge_col             ; text modes: no page to show
        call page0_98
ge_col: ld a,(ix+W_BAKCLR)      ; BASIC colours as before G3INIT
        ld (BAKCLR),a
        ld a,(ix+W_BDRCLR)
        ld (BDRCLR),a
        push ix
        call CHGCLR
        pop ix
ge_off: ld a,(ix+W_FLAGS)       ; inactive, nothing to undo; the H.TIMI
        and 0xFC                ; hook stays, inert
        ld (ix+W_FLAGS),a
        call grp
        res GF_UNDO,(hl)
        ret

; ============================================================================
; V9968 and geo3d set up. IX = work area, (ix+W_PORT) = P.
; ============================================================================

; v68_on: PORT#4 unlocked, R#21 = 0 (V9968 mode: LRMM/LFMC, 256 KB, ID 3),
; R#20 = 1 (high-speed commands), LRMM window over all the VRAM, locked.
v68_on: call p4_open
        xor a
        ld b,21
        call vreg
        ld a,1
        ld b,20
        call vreg
        ld a,51
        ld hl,lrmm_win
        ld b,8
        call vind               ; R#51-58: WSX 0, WSY 0, WEX 511, WEY 2047
        call p4_lock
        ld a,(ix+W_PORT)
        cp 0x98
        ret nz
        ld a,1
        ld (RG20SAV),a          ; BASIC's SCREEN writes R#20/R#21 from these
        xor a
        ld (RG21SAV),a
        ret

; v68_off: R#20 = 0 first (EPAL off), R#21 = 3Bh (boot value: V9958 mode,
; ID 2), PORT#4 locked again. Only R#20 = 00h/01h and R#21 = 00h/3Bh are
; ever written: the other bits differ between the RTL and openMSX.
v68_off:
        call p4_open
        xor a
        ld b,20
        call vreg
        ld a,0x3B
        ld b,21
        call vreg
        call p4_lock
        ld a,(ix+W_PORT)
        cp 0x98
        ret nz
        xor a
        ld (RG20SAV),a
        ld a,0x3B
        ld (RG21SAV),a
        ret

p4_open:
        xor a
        jr p4_w
p4_lock:
        ld a,0x80               ; bit 7: R#20/R#21 locked
p4_w:   push bc
        ld b,a
        ld a,(ix+W_PORT)
        add a,4
        ld c,a
        out (c),b               ; PORT#4
        pop bc
        ret

lrmm_win:
        defb 0x00, 0x00, 0x00, 0x00, 0xFF, 0x01, 0xFF, 0x07

; scr5_88: the cartridge V9968 into SCREEN 5 (GRAPHIC 4), 212 lines, sprites
; and V9968 interrupts off. R#2 is written by show0.
scr5_88:
        ld hl,scr5_tab
s5_1:   ld b,(hl)
        inc b
        ret z
        dec b
        inc hl
        ld a,(hl)
        inc hl
        call vreg
        jr s5_1

scr5_tab:
        defb 0, 0x06            ; GRAPHIC 4, IE1 = 0
        defb 1, 0x40            ; display on, IE0 = 0
        defb 7, 0x00            ; border colour 0
        defb 8, 0x0A            ; VR = 1 (else 14-bit addresses), sprites off
        defb 9, 0x80            ; 212 lines, 60 Hz
        defb 15, 0x00           ; status register 0
        defb 18, 0x00           ; display adjust
        defb 23, 0x00           ; vertical scroll
        defb 25, 0x00           ; V9958 scroll and YJK off
        defb 26, 0x00
        defb 27, 0x00
        defb 0xFF

; border0: R#7 = 0: colour 0 of SCREEN 5 is transparent and shows the border
; (spec 7.5). 98h: also RG7SAV, BAKCLR and BDRCLR, as COLOR ,0,0 would.
border0:
        xor a
        ld b,7
        call vreg
        ld a,(ix+W_PORT)
        cp 0x98
        ret nz
        xor a
        ld (RG7SAV),a
        ld (BAKCLR),a
        ld (BDRCLR),a
        ret

; pal_load: HL = 16 colours (0RRR0BBB, 00000GGG). 98h: SUB-ROM SETPLT, so
; that BASIC's palette table (COLOR=RESTORE) follows. 88h: straight to the
; V9968, whose palette the BIOS never sets.
pal_load:
        ld a,(ix+W_PORT)
        cp 0x98
        jr z,pl_98
        xor a
        ld b,16
        call vreg               ; R#16 = 0
        ld a,(ix+W_PORT)
        add a,2
        ld c,a
        ld b,32
        di
        otir
        ei
        ret
pl_98:  push ix
        ld d,0
pl_1:   ld a,(hl)
        inc hl
        ld e,(hl)
        inc hl
        push hl
        push de
        ld ix,SETPLT
        call EXTROM
        pop de
        pop hl
        inc d
        ld a,d
        cp 16
        jr nz,pl_1
        pop ix
        ret

; Default palette (spec 3.4 and decision 6): 0 black, 1-7 blue ramp, 8-14
; orange ramp, 15 white. Tone k of a base c is round(c * (k+1) / 7): seven
; different tones (the c * (k+2) / 8 of the spec repeats some). Colour 4
; stays blue, so white text on it is readable.
pal_g3: defb 0x00,0x00, 0x01,0x00, 0x12,0x01, 0x13,0x01
        defb 0x14,0x02, 0x15,0x02, 0x26,0x03, 0x27,0x03
        defb 0x10,0x01, 0x20,0x01, 0x30,0x02, 0x40,0x02
        defb 0x50,0x03, 0x60,0x03, 0x70,0x04, 0x77,0x07

; MSX2 default palette (the SUB-ROM INIPLT table), for the 88h V9968.
pal_msx:
        defb 0x00,0x00, 0x00,0x00, 0x11,0x06, 0x33,0x07
        defb 0x17,0x01, 0x27,0x03, 0x51,0x01, 0x27,0x06
        defb 0x71,0x01, 0x73,0x03, 0x61,0x06, 0x64,0x06
        defb 0x11,0x04, 0x65,0x02, 0x55,0x05, 0x77,0x07

; hmmv: A = page. HMMV of lines 0-211 of that page with the background colour
; (DY is 11 bits in V9968 mode). Lines 212-255 of page 0 hold BASIC's sprite
; and palette tables: never cleared (spec 7.4). The caller waits for CE.
hmmv:   push ix
        pop hl
        ld de,W_TMP
        add hl,de
        push hl
        ld (hl),0               ; DX
        inc hl
        ld (hl),0
        inc hl
        ld (hl),0               ; DY = page * 256
        inc hl
        ld (hl),a
        inc hl
        ld (hl),0               ; NX = 256
        inc hl
        ld (hl),1
        inc hl
        ld (hl),212             ; NY
        inc hl
        ld (hl),0
        inc hl
        ld a,(ix+W_BG)
        and 0x0F
        ld b,a
        add a,a
        add a,a
        add a,a
        add a,a
        or b
        ld (hl),a               ; CLR: two pixels of the background colour
        inc hl
        ld (hl),0               ; ARG
        inc hl
        ld (hl),0xC0            ; HMMV
        pop hl
        ld a,36
        ld b,11
        jp vind

; show0: display page 0 (R#2 = page * 32 + 1Fh); geo3d draws on page 1.
show0:  ld (ix+W_SHOW),0
        ld (ix+W_DRAW),1
        ld a,(ix+W_PORT)
        cp 0x98
        jr z,page0_98
        ld a,0x1F
        ld b,2
        jp vreg
page0_98:                       ; 98h: R#2 and BASIC's copies
        ld a,0x1F
        ld b,2
        call vreg
        ld a,0x1F
        ld (RG2SAV),a
        xor a
        ld (DPPAGE),a
        ld (ACPAGE),a
        ret

; geo_cfg: geo3d registers for SCREEN 5 (as the demos do). Each entry: index,
; count, data; FFh ends. Never a 9th byte from 40h: index 48h starts a RUN.
geo_cfg:
        ld a,(ix+W_PORT)
        add a,5
        ld c,a                  ; P+5: index
        ld hl,geo_tab
gc_1:   ld a,(hl)
        cp 0xFF
        ret z
        out (c),a
        inc hl
        ld b,(hl)
        inc hl
        inc c
        inc c                   ; P+7: data
        otir
        dec c
        dec c
        jr gc_1

geo_tab:
        defb 0x18, 12           ; F 256, CX 128, CY 106, ZNEAR 16, W 256, H 212
        defb 0x00,0x01, 0x80,0x00, 0x6A,0x00, 0x10,0x00, 0x00,0x01, 0xD4,0x00
        defb 0x40, 8            ; no model yet, COLOR 15, LOP 0, YPAGE 256
        defb 0x00, 0x00, 0x00, 0x00, 0x0F, 0x00, 0x00, 0x01
        defb 0x58, 8            ; no faces; light (-1,1,-1)/sqrt 3, Q2.14
        defb 0x00, 0x00, 0x0D,0xDB, 0xF3,0x24, 0x0D,0xDB
        defb 0xFF               ; TODO: TEXX, TEXY, TSTRIDE, TADDR (60h)

; scene_reset: objects empty and the scene defaults (spec step 7).
scene_reset:
        push ix
        pop hl
        ld de,W_OBJ
        add hl,de
        ld bc,NOBJ * OBJSZ
        call zero
        push ix
        pop hl
        ld de,W_BG
        add hl,de
        ex de,hl
        ld hl,scene_def
        ld bc,scene_end - scene_def
        ldir
        ret

scene_def:                      ; W_BG to W_LIGHT
        defb 0                  ; background colour 0
        defb 2                  ; 2 blanks per frame
        defb 0, 1               ; show page 0, draw page 1
        defw 256, 212, 0        ; window: the whole screen
        defw 100                ; zoom 100%
        defw 0, 0, 0 - 300      ; camera (0,0,-300)
        defw 0, 0, 0            ; looking at the origin
        defw 0 - 1, 1, 0 - 1    ; light from up, left and front
scene_end:

; ============================================================================
; VDP access. vreg, vind, waits: IX = work area. vstat: C = P+1.
; ============================================================================

; vreg: R#B = A (a two-byte write to P+1, interrupts off). Keeps BC, HL.
vreg:   push bc
        push af
        ld a,(ix+W_PORT)
        inc a
        ld c,a
        pop af
        di
        out (c),a
        ld a,b
        or 0x80
        out (c),a
        ei
        pop bc
        ret

; vind: R#17 = A (auto-increment), then B bytes from HL to P+3.
vind:   push af
        ld a,(ix+W_PORT)
        inc a
        ld c,a
        pop af
        di
        out (c),a
        ld a,17 + 0x80
        out (c),a
        inc c
        inc c
        otir
        ei
        ret

; vstat: A = S#B of the VDP whose P+1 is C. R#15 is 0 again afterwards.
vstat:  di
        out (c),b
        ld a,15 + 0x80
        out (c),a
        in a,(c)
        push af
        xor a
        out (c),a
        ld a,15 + 0x80
        out (c),a
        ei
        pop af
        ret

; Waits (spec 7.2). Each one counts loop turns (about 2 s on a 3.58 MHz Z80)
; and looks at CTRL+STOP every 256 counted turns. CTRL+STOP does not cut a
; wait short, since the engine is still busy then: it is remembered (L = 1)
; and raised as Device I/O error once the engine is idle, so that BASIC gets
; geo3d idle, the command engine free and R#15 = 0. Running out of time also
; ends in Device I/O error. HL is changed. Measured in openMSX: wait_geo
; 2.0-2.1 s (Z80), 1.9 s (R800); wait_ce 2.3-2.4 s (Z80), 1.6 s (R800).

; wait_ce: until the command engine is idle (S#2 bit 0 = CE = 0). A command
; that never ends (an LMMC left without its data, for example) is stopped at
; the timeout, unless geo3d is busy (R#32-46 are geo3d's then). The turbo R
; slows every VDP port access down, so this loop runs on the R800 at about
; Z80 speed and every turn counts there too.
wait_ce:
        ei
        ld hl,0                 ; no CTRL+STOP yet, every turn counts
        ld a,(ix+W_PORT)
        inc a
        ld c,a
        ld de,26000             ; about 2 s
wc_1:   ld b,2
        call vstat
wc_2:   rrca
        jr nc,wt_end
        call tick
        jr nz,wc_1
        ld a,(ix+W_PORT)        ; time up
        add a,5
        ld c,a
        in a,(c)                ; geo3d status
        and 0x0F
        jp nz,err_io
        xor a
        ld b,46
        call vreg               ; CMD = STOP
        dec c
        dec c
        dec c
        dec c                   ; P+1
        ld h,0
wc_3:   ld b,2                  ; a moment for CE to drop
        call vstat
        rrca
        jp nc,err_io
        dec h
        jr nz,wc_3
        jp err_io

; wait_geo: until geo3d is idle (status bits 3-0 = 0: no RUN, core free).
; geo3d cannot be stopped: at the timeout it is still busy. The R800 runs
; this loop about twice as fast as the Z80: there every other turn counts.
wait_geo:
        ei
        call wt_init
        ld a,(ix+W_PORT)
        add a,5
        ld c,a
        ld de,47000             ; about 2 s
wg_1:   in a,(c)
        and 0x0F
        jr z,wt_end
        call tick
        jr nz,wg_1
        jp err_io

; wt_end: the engine is idle; Device I/O error if CTRL+STOP came meanwhile.
wt_end: ld a,l
        or a
        ret z
        jp err_io

; wt_init: L = 0 (no CTRL+STOP yet), H = 2 on the R800 (tick then counts
; every other turn, H bit 0 toggling), else 0. Keeps BC, DE (GETCPU only
; changes AF).
wt_init:
        ld hl,0
        ld a,(MSXVER)
        cp 3
        ret c                   ; not a turbo R
        call GETCPU
        or a
        ret z                   ; Z80
        ld h,2
        ret

; tick: one turn of a wait. DE counts turns down, CTRL+STOP is looked at
; every 256 counted turns (L = 1 once seen). NZ: go on; Z: time is up.
tick:   bit 1,h
        jr z,tk_1
        ld a,h
        xor 1
        ld h,a
        and 1
        ret nz                  ; R800: a turn that does not count
tk_1:   dec de
        ld a,e
        or a
        jr nz,tk_2
        push bc
        push de
        push hl
        call BREAKX
        pop hl
        pop de
        pop bc
        jr nc,tk_2
tk_stop:
        ld l,1                  ; CTRL+STOP: the wait goes on
tk_2:   ld a,d
        or e
        ret

; ============================================================================
; Profile detection (spec 2): reads only until geo3d is identified.
; ============================================================================

; findprof: C = 88h or 98h (Z) where a V9968 with geo3d answers, 88h first;
; C = 0 (NZ) if none. E = geo3d version byte.
findprof:
        ld c,0x88
        call detect
        ret z
        ld c,0x98
        call detect
        ret z
        ld c,0
        ret

; detect: C = port base P. Z: a V9968 (ID 2 or 3) with geo3d at P, E = the
; geo3d byte at index 49h (FFh on the first version). C kept.
;  1. 98h: not on MSX1 (an R#15 write through 99h would hit R#7 there).
;  2. S#1: ID 2 (V9958 mode, also any V9958) or 3 (V9968 mode).
;  3. With R#15 = 2 (one DI window): P+5 is not FFh (empty port) and has not
;     bits 3-2 = 11 (a V99x8 mirrored at 9Ch-9Fh returns S#2 there). Only
;     then P+4 is read (through such a mirror it would be a VRAM read), and
;     PORT#4 must have bits 6-3 = 0.
;  4. First write: index 40h, then 17 reads: LOP 4 bits, YPAGE 11 bits, and
;     the read pointer wraps back to 40h. geo3d may be busy: the index write
;     and the reads are harmless then (geo3d_engine.v), and G3INIT's wait_geo
;     waits for it, with its timeout and CTRL+STOP.
detect: ld a,c
        cp 0x98
        jr nz,dt_1
        ld a,(MSXVER)
        or a
        jp z,dt_no
dt_1:   push bc
        inc c                   ; P+1
        ld b,1
        call vstat              ; S#1 = 0 0 ID4-ID0 FH
        rrca
        and 0x1F
        cp 2
        jr z,dt_2
        cp 3
        jr nz,dt_nop
dt_2:   di
        ld a,2
        out (c),a
        ld a,15 + 0x80
        out (c),a               ; R#15 = 2
        ld a,c
        add a,4
        ld c,a
        in a,(c)                ; P+5
dt_p5:  ld e,0xFF               ; E = PORT#4: FFh (fails) unless read below
        cp 0xFF
        jr z,dt_2b              ; nothing there
        and 0x0C
        cp 0x0C
        jr z,dt_2b              ; S#2 of a mirrored VDP (or a busy geo3d)
        dec c
        in e,(c)                ; P+4 = PORT#4
        inc c
dt_2b:  dec c
        dec c
        dec c
        dec c                   ; P+1
        xor a
        out (c),a
        ld a,15 + 0x80
        out (c),a               ; R#15 = 0
        ei
        ld a,e
        and 0x78
        jr nz,dt_nop
        ld a,c
        add a,4
        ld c,a                  ; P+5
        ld a,0x40
        out (c),a               ; index 40h (VADDR): the first geo3d write
        inc c
        inc c                   ; P+7
        in h,(c)                ; 40h VADDR
        in a,(c)                ; 41h EADDR
        in a,(c)                ; 42h NVERT
        in a,(c)                ; 43h NEDGE
        in a,(c)                ; 44h COLOR
        in a,(c)                ; 45h LOP
        and 0xF0
        jr nz,dt_nop
        in a,(c)                ; 46h YPAGE
        in a,(c)                ; 47h
        and 0xF8
        jr nz,dt_nop
        in a,(c)                ; 48h status (busy or not)
dt_st:  in e,(c)                ; 49h version
        ld b,6
dt_4:   in a,(c)                ; 4Ah-4Fh counters
        djnz dt_4
        in a,(c)                ; 40h again
        cp h
        jr nz,dt_nop
        pop bc
        cp a
        ret
dt_nop: pop bc
dt_no:  or 0xFF
        ret

; ============================================================================
; Work area and slot
; ============================================================================

; getslt: A = slot of page 1 (this ROM), ENASLT format E000SSPP.
getslt: push bc
        push hl
        call RSLREG
        rrca
        rrca
        and 3
        ld c,a
        ld b,0
        ld hl,EXPTBL
        add hl,bc
        or (hl)                 ; bit 7: expanded
        jp p,gs_1
        ld c,a
        inc hl
        inc hl
        inc hl
        inc hl                  ; SLTTBL[P], kept current by ENASLT/CALSLT
        ld a,(hl)
        and 0x0C
        or c
gs_1:   pop hl
        pop bc
        ret

; wrkgrp: HL = SLTWRK group of slot A (FD09h + 32*P + 8*S). Keeps AF, BC, DE.
wrkgrp: push bc
        push af
        ld c,a
        and 3
        rrca
        rrca
        rrca                    ; P * 32
        ld b,a
        ld a,c
        and 0x0C
        add a,a                 ; S * 8
        or b
        ld c,a
        ld b,0
        ld hl,SLTWRK
        add hl,bc
        pop af
        pop bc
        ret

; grp: HL = the SLTWRK group of this ROM. Keeps all but HL.
grp:    push af
        call getslt
        call wrkgrp
        pop af
        ret

; getblk: HL = work area address (0: none). Keeps all but HL.
getblk: push af
        call getslt
        call wrkgrp
        inc hl
        inc hl
        ld a,(hl)
        inc hl
        ld h,(hl)
        ld l,a
        pop af
        ret

; blkchk: HL = work area; carry when there is none or HIMEM is above it
; (not reserved: CLEAR moved HIMEM up and there was no room to move it back).
blkchk: call getblk
        ld a,h
        or l
        scf
        ret z
        push de
        ex de,hl
        ld hl,(HIMEM)
        or a
        sbc hl,de
        ex de,hl
        pop de
        jr z,bk_ok
        ccf
        ret
bk_ok:  or a
        ret

; blkact: IX = work area; carry unless it is reserved, signed and active.
blkact: call blkchk
        ret c
        push hl
        pop ix
        call sigchk
        scf
        ret nz
        bit 0,(ix+W_FLAGS)
        scf
        ret z
        or a
        ret

; sigchk: Z when IX points at the "G3WK" signature and a G3INIT wrote it in
; this boot (GF_SIGN): RAM keeps an old signature across a reset. Keeps BC,
; DE, HL.
sigchk: push hl
        call grp
        bit GF_SIGN,(hl)
        pop hl
        jr nz,sg_1
        or 1                    ; NZ
        ret
sg_1:   ld a,(ix+W_SIG)
        cp 'G'
        ret nz
        ld a,(ix+W_SIG+1)
        cp '3'
        ret nz
        ld a,(ix+W_SIG+2)
        cp 'W'
        ret nz
        ld a,(ix+W_SIG+3)
        cp 'K'
        ret

; zero: BC (>= 1) bytes at HL = 0.
zero:   ld (hl),0
        ld d,h
        ld e,l
        inc de
        dec bc
        ld a,b
        or c
        ret z
        ldir
        ret

; ============================================================================
; Trampoline: page-3 code that calls IX with page 1 switched to the BASIC
; ROM, then page 1 back to this ROM. For page-0 routines that jump into page
; 1 (FRCINT, FOUT, the Math-Pack), which CALBAS cannot reach. AF, BC, DE, HL
; pass in and out; it returns with interrupts enabled. A BASIC error inside
; unwinds normally, with page 1 = BASIC. G3INIT copies it into the work area.
; ============================================================================
tr_tpl: push hl
        push de
        push bc
        push af
        ld a,(EXPTBL)           ; BASIC ROM slot, as CALBAS uses
        ld h,0x40
        call ENASLT             ; leaves DI
        pop af
        pop bc
        pop de
        pop hl
        ei
tr_c1:  call tr_jp              ; relocated
        push hl
        push de
        push bc
        push af
tr_c2:  ld a,(tr_slot)          ; relocated
        ld h,0x40
        call ENASLT             ; page 1 = this ROM again
        pop af
        pop bc
        pop de
        pop hl
        ei
        ret
tr_jp:  jp (ix)
tr_slot:
        defb 0                  ; our slot
tr_end:

; tr_copy: the trampoline into the work area (IX), relocated.
tr_copy:
        push ix
        pop hl
        ld de,W_TRAMP
        add hl,de
        ex de,hl                ; DE = copy
        push de
        ld hl,tr_tpl
        ld bc,tr_end - tr_tpl
        ldir
        pop de
        ld hl,tr_c1 + 1 - tr_tpl
        call tr_rel
        ld hl,tr_c2 + 1 - tr_tpl
        call tr_rel
        ld hl,tr_slot - tr_tpl
        add hl,de
        ld a,(ix+W_SLOT)
        ld (hl),a
        ret
tr_rel: add hl,de               ; the word at DE+HL: template address -> copy
        push hl
        ld a,(hl)
        inc hl
        ld h,(hl)
        ld l,a
        ld bc,0 - tr_tpl
        add hl,bc
        add hl,de
        ld b,h
        ld c,l
        pop hl
        ld (hl),c
        inc hl
        ld (hl),b
        ret

; tramp: call IX in the BASIC ROM (page 1) through the trampoline of the
; work area. Only with a signed work area (sigchk). AF, BC, DE, HL pass.
tramp:  push hl
        push de
        push af
        call getblk
        ld de,W_TRAMP
        add hl,de
        pop af
        pop de
        ex (sp),hl              ; HL back, trampoline address on the stack
        ret

; ============================================================================
; Arguments (spec 7.7)
; ============================================================================

; getargs: reads "[( [a0] [, [a1] ...] )]" at HL (right after the name), up to
; B arguments; any position may be empty. Each argument is a number of any
; type, rounded to a signed 16-bit integer (dacint). Result in the work area:
; W_ARGC bit k = argument k given, W_ARGV + 2k = its value. HL returns at the
; end of the statement; Syntax error for anything else.
getargs:
        push hl
        call getblk
        ld de,W_ARGC
        add hl,de
        ld (hl),0
        pop hl
        ld c,0                  ; C = position
        dec hl
        call chrgtr             ; first character after the name
        cp '('
        jr nz,endstmt
ga_1:   call chrgtr             ; past '(' or ','
        cp ','
        jr z,ga_3
        cp ')'
        jr z,ga_3
        push bc
        call getint             ; DE = value, HL after the expression
        pop bc
        push hl                 ; text
        push de
        call getblk
        ld de,W_ARGV
        add hl,de
        ld a,c
        add a,a
        ld e,a
        ld d,0
        add hl,de
        pop de
        ld (hl),e               ; W_ARGV + 2C = value
        inc hl
        ld (hl),d
        call getblk
        ld de,W_ARGC
        add hl,de
        push bc
        ld b,c
        inc b
        ld a,1
        jr ga_2b
ga_2:   add a,a
ga_2b:  djnz ga_2               ; A = 1 << C
        pop bc
        or (hl)
        ld (hl),a               ; W_ARGC bit C: given
        pop hl                  ; text
ga_3:   ld a,(hl)               ; ',' or ')'
        inc c
        cp ','
        jr nz,ga_4
        ld a,c
        cp b
        jp nc,err_sn            ; too many arguments
        jr ga_1
ga_4:   cp ')'
        jp nz,err_sn
        call chrgtr             ; past ')'
endstmt:
        or a
        ret z                   ; end of line
        cp ':'
        ret z
        cp T_ELSE
        ret z
        jp err_sn

; noargs: for commands without arguments: HL at the end of the statement.
noargs: dec hl
        call chrgtr
        jr endstmt

chrgtr: ld ix,CHRGTR
        jp CALBAS

; getint: DE = the expression at HL as a signed 16-bit integer.
getint: ld ix,FRMEVL
        call CALBAS             ; DAC, VALTYP; HL after the expression
        push hl
        call dacint
        pop hl
        ret

; dacint: DE = DAC rounded half away from zero to -32768..32767. Integers
; (VALTYP 2) as they are; singles and doubles from their BCD (sign and
; excess-64 exponent, then digits: 0.d1d2... x 10^(e-64)); Overflow outside
; the range; Type mismatch for a string (freed first).
dacint: ld a,(VALTYP)
        cp 3
        jr z,di_str
        ld de,(DAC+2)
        cp 2
        ret z
        ld hl,DAC
        ld a,(hl)
        and 0x7F
        sub 0x40                ; A = digits before the point
        jr c,di_0               ; below 0.1, or 0 (exponent byte 0)
        jr z,di_half            ; 0.1 to 0.99...
        cp 6
        jp nc,err_ov            ; 100000 or more
        ld b,a
        cp 5
        jr nz,di_1
        inc hl
        ld a,(hl)
        dec hl
        cp 0x40
        jp nc,err_ov            ; 40000 or more: keeps the sum in 16 bits
di_1:   inc hl                  ; HL = first digit pair
        ld c,0                  ; C bit 0: low nibble next
        ld de,0
di_2:   call digit
        push hl
        ld h,d
        ld l,e
        add hl,hl
        add hl,hl
        add hl,de
        add hl,hl               ; DE * 10
        ld e,a
        ld d,0
        add hl,de
        ex de,hl
        pop hl
        djnz di_2
        call digit              ; the first digit dropped
        cp 5
        jr c,di_sign
        inc de
        jr di_sign
di_half:
        inc hl
        ld de,0
        ld a,(hl)
        cp 0x50                 ; first digit >= 5: 1
        jr c,di_sign
        inc de
di_sign:
        ld a,(DAC)
        or a
        jp p,di_pos
        ld hl,32768             ; negative: up to 32768
        or a
        sbc hl,de
        jp c,err_ov
        ld hl,0
        or a
        sbc hl,de
        ex de,hl
        ret
di_pos: bit 7,d
        jp nz,err_ov
        ret
di_0:   ld de,0
        ret
di_str: ld ix,FRESTR
        call CALBAS
        jp err_tm

; digit: A = the next BCD digit at HL (C bit 0: low nibble); advances.
digit:  ld a,(hl)
        bit 0,c
        jr nz,dg_1
        rrca
        rrca
        rrca
        rrca
        inc c
        and 0x0F
        ret
dg_1:   inc hl
        dec c
        and 0x0F
        ret

; ============================================================================
; Errors (spec 7.7): raised through BASIC's ERROR with geo3d idle, the command
; engine free and R#15 = 0, so ON ERROR GOTO sees a coherent state. The one
; exception is a wait that ran out of time with geo3d still busy.
; ============================================================================
err_sn: ld e,ERRSN
        jr error
err_fc: ld e,ERRFC
        jr error
err_ov: ld e,ERROV
        jr error
err_om: ld e,ERROM
        jr error
err_tm: ld e,ERRTM
        jr error
err_io: ld e,ERRIO
error:  ld ix,ERROR
        jp CALBAS

; ============================================================================
; Text output (banner)
; ============================================================================
puts:   ld a,(hl)
        or a
        ret z
        call CHPUT
        inc hl
        jr puts

puthex: push af
        rrca
        rrca
        rrca
        rrca
        call ph_1
        pop af
ph_1:   and 0x0F
        add a,0x90
        daa
        adc a,0x40
        daa
        jp CHPUT

code_end:
        defs 0x6000 - $, 0xFF   ; bank 0 must stay under 8 KB

; ============================================================================
; banks 1-7: reserved, FFh (no "AB": INIT maps bank 1 at 8000h and A000h)
; ============================================================================
        org 0x6000
        defs 0x2000, 0xFF       ; bank 1
        org 0x6000
        defs 0x2000, 0xFF       ; bank 2
        org 0x6000
        defs 0x2000, 0xFF       ; bank 3
        org 0x6000
        defs 0x2000, 0xFF       ; bank 4
        org 0x6000
        defs 0x2000, 0xFF       ; bank 5
        org 0x6000
        defs 0x2000, 0xFF       ; bank 6
        org 0x6000
        defs 0x2000, 0xFF       ; bank 7
