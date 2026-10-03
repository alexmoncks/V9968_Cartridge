; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; sphere7.asm - SCREEN 7 sphere demo for geo3d + V9968 (ASCII16 MegaROM)
;
; A textured sphere spins about a tilted axis and bounces off the four edges
; of a 512 x 212 SCREEN 7 (GRAPHIC6) picture, over a starfield. geo3d draws
; it: the Z80 sends one rotation matrix, the centre (CX, CY), the page and
; the texture origin per frame, about 40 bytes, and geo3d transforms the
; 182 vertices, culls, shades and sorts the 200 faces and sends one LRMM per
; row of each face, so the V9968 fetches every texel itself.
;
; VRAM (256 KB, SCREEN 7: 256 bytes per line, 4 pages of 256 lines):
;   page 0, lines   0-211  picture A
;   page 0, lines 214-247  HUD bitmaps (six texture names, the credit)
;   page 1, lines 256-467  picture B
;   page 1, line  511      guard row of the first three textures
;   pages 2-3, 512-1022    six textures, 160 x 51 texels (pole to pole) and
;                          a wrap column, five shaded copies each (light
;                          levels 0..4, 0 the night side, TSTRIDE 51): at
;                          x = 1, 172, 343 and lines 512, 768
; Each texture has a guard column at x - 1 and a guard row at y - 1: geo3d's
; per-pixel step is a floor division, so a span can reach up to one texel
; before u = 0 or v = 0; the LRMM source window (set per texture) covers
; the guards, so every texel is read inside it.
;
; Every frame (PACE vertical blanks: 1 = 60 fps, the default; 2 = 30 fps):
;   1. wait until geo3d has drawn the previous frame (status bit 0), and CE;
;   2. at the vertical blank, show it (R#2), with its texture's palette
;      (16 colours, 15-bit EPAL unless built --pal9) if that changed;
;   3. on the page now hidden: clear the box the sphere took two frames ago
;      (HMMV), copy the HUD back (HMMM), and the stars inside the box (PSET);
;   4. LRMM window for the texture, then geo3d: matrix, CX/CY, YPAGE, TEXX/
;      TEXY, RUN;
;   5. sound, keys, next position: constant speed; at an edge limit the
;      centre stops right on it (the contact frame touches the edge) and the
;      speed turns; a short squash from the contact frame on; a new texture
;      at each side wall; a PSG "boing".
; Keys: SPACE next texture, ESC back to the start.
;
; Rules kept: no write to R#32-R#58 while geo3d's RUN is busy (CE = 0 does
; not mean geo3d is idle: status bit 0 is waited for first); R#15 = 0
; except inside wait_ce and clear_back's star loop (CE polls of S#2; S#0 keeps
; its vertical blank flag until read); PORT#4 bit 7 = 0 unlocks R#20 / R#21; page flips
; and palette changes only right after S#0 shows a vertical blank; no
; interrupts at all (DI from start: the V9968's IE0, IE1 and command-end
; interrupt stay off, as the 88h profile needs).
;
; Ports (out/ports.asm, written by build.sh): PORT_BASE 88h for the V9968
; cartridge with the geo3d build (geo3d at 8Dh/8Fh), 98h for openMSX and
; blueMSX+ (V9968 as the machine's VDP, geo3d at 9Dh/9Fh).
;
; Banks: 0 this code (4000h), 1 tables (8000h, gen_assets.py), 2-9 the
; textures, 10 the HUD bitmaps and the guard row.
; Assemble: build.sh (z80asm 1.8)
; ============================================================================

        include "out/ports.asm"             ; PORT_BASE, PACE, ROTSTEP
        include "out/inc/sphere7_equ.asm"   ; tables and constants

VDP_DATA:   equ PORT_BASE
VDP_CTRL:   equ PORT_BASE + 1
VDP_PAL:    equ PORT_BASE + 2
VDP_IND:    equ PORT_BASE + 3
VDP_PORT4:  equ PORT_BASE + 4
GEO_IDX:    equ PORT_BASE + 5
GEO_DAT:    equ PORT_BASE + 7

PPI_B:      equ 0xA9
PPI_C:      equ 0xAA
PSG_A:      equ 0xA0
PSG_D:      equ 0xA1

ENASLT:     equ 0x0024
MSXVER:     equ 0x002D
INITXT:     equ 0x006C
CHPUT:      equ 0x00A2
RSLREG:     equ 0x0138
EXPTBL:     equ 0xFCC1

BANK2_SEL:  equ 0x7000              ; ASCII16: bank of 8000h-BFFFh
WAIT_TURNS: equ 3                   ; x 65536 polls: about 3 s, then hw_fail
SND_LEN:    equ 24 / PACE              ; frames of the bounce sound (0.4 s)
START_X:    equ X_MIN + 24 * VX0
START_Y:    equ Y_MIN + 14 * VY0

; RAM (page 3)
my_slot:    equ 0xC000
vbl:        equ 0xC001              ; 2: vertical blanks seen (S#0 bit 7)
flip_vbl:   equ 0xC003              ; 2: vbl of the last page flip
late:       equ 0xC005              ; 2: flips later than PACE blanks
nflip:      equ 0xC007              ; 2: page flips
pg:         equ 0xC009              ; page being drawn (0 / 1)
first:      equ 0xC00A              ; 1 until the first frame is drawn
px:         equ 0xC00B              ; 2: centre (no squash), pixels
py:         equ 0xC00D              ; 2
vx:         equ 0xC00F              ; 2: signed steps per frame
vy:         equ 0xC011              ; 2
rot:        equ 0xC013              ; rotation table index
tex:        equ 0xC014              ; texture of the next frame
texpg:      equ 0xC015              ; 2: texture drawn on page 0, page 1
paltex:     equ 0xC017              ; texture of the palette on show
kx:         equ 0xC018              ; squash 0..3 against a side wall
ky:         equ 0xC019              ; and against the floor / ceiling
cx:         equ 0xC01A              ; 2: centre on screen (with the squash)
cy:         equ 0xC01C              ; 2
bounces:    equ 0xC01E              ; 2: contacts with an edge
rects:      equ 0xC020              ; 2 x 8: box per page: x0, nx, y0, ny
keyprev:    equ 0xC030
snd_t:      equ 0xC031
snd_per:    equ 0xC032              ; 2
nstars:     equ 0xC034              ; 2: stars put back (all frames)
fail:       equ 0xC036              ; 1: geo3d timeout, 2: CE timeout
sbx0:       equ 0xC038              ; 2: clear_back's box: x0,
sbx1:       equ 0xC03A              ; 2:   x0 + nx,
sby1:       equ 0xC03C              ;   y0 + ny
sbhy:       equ 0xC03D              ; send_frame: half height of the box
mat:        equ 0xC040              ; 18: the frame's matrix
cmdbuf:     equ 0xC060              ; 16: a command's registers
ram_end:    equ 0xC070

        org 0x4000
        db "AB"
        dw init
        dw 0, 0, 0
        ds 6, 0
        db "ROM_AS16"                   ; 4010h: ROM type signature (MSXgl): ASCII16
        include "../tools/mapper_tag_ascii16.asm"

; ----------------------------------------------------------------------------
init:
        di
        ld sp, 0xF000
        ld hl, 0xC000
        ld de, 0xC001
        ld bc, ram_end - 0xC000
        ld (hl), 0
        ldir
        ; page 2 -> this cartridge's slot (the slot of page 1)
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
        ld a, 1
        call setbank
        call geo_probe
        jp nz, nogeo
        call vdp_init
        call geo_init
        call psg_init
        call state_reset
        ld a, 1
        ld (pg), a                      ; the first frame goes to page 1
        ld (first), a
        ld hl, rects                    ; both pages: clear everything once,
        call rect_full                  ; which also puts every star and
        ld hl, rects + 8                ; the HUD on them
        call rect_full
        xor a
        ld (texpg), a
        ld (texpg + 1), a
        ld (paltex), a
        call pal_write                  ; texture 0's palette (A = 0)
        ld a, 0x40
        ld b, 1
        call vdp_wreg                   ; display on, no interrupts
        ld hl, (vbl)
        ld (flip_vbl), hl

; ----------------------------------------------------------------------------
main_loop:
        call wait_geo                   ; the previous frame is drawn
        call wait_ce
        ld a, (first)
        or a
        jr z, ml_flip
        ld hl, (vbl)                    ; the first frame: the pace starts here
        ld (flip_vbl), hl
        jr ml_1
ml_flip:
        call flip                       ; show it at its vertical blank
ml_1:   xor a
        ld (first), a
        call clear_back                 ; the hidden page: box, HUD, stars
        call send_frame                 ; geo3d draws this frame (RUN)
        call snd_tick
        call keys
        call advance                    ; the next frame's position
        ld a, (pg)
        xor 1
        ld (pg), a
        jr main_loop

; ----------------------------------------------------------------------------
; VDP helpers
setbank:                                ; A = ASCII16 bank of page 2 (8000h-BFFFh)
        ld (BANK2_SEL), a
        ret

vdp_wreg:                               ; A = value, B = register
        out (VDP_CTRL), a
        ld a, b
        or 0x80
        out (VDP_CTRL), a
        ret

; CE (S#2 bit 0) = 0. R#15 = 2 only in here. Keeps HL. Changes AF, BC, DE.
wait_ce:
        ld a, 2
        ld b, 15
        call vdp_wreg
        ld de, 0
        ld c, WAIT_TURNS
wce_1:  in a, (VDP_CTRL)
        rrca
        jr nc, wce_2
        dec de
        ld a, d
        or e
        jr nz, wce_1
        dec c
        jr nz, wce_1
        ld a, 2
        ld (fail), a
        jp hw_fail
wce_2:  xor a
        ld b, 15
        jp vdp_wreg

; one poll of S#0 (R#15 = 0): a vertical blank since the last read -> vbl + 1
vpoll:  in a, (VDP_CTRL)
        rlca
        ret nc
        push hl
        ld hl, (vbl)
        inc hl
        ld (vbl), hl
        pop hl
        ret

; geo3d's RUN over (status bit 0), counting the blanks meanwhile
wait_geo:
        ld de, 0
        ld c, WAIT_TURNS
wg_1:   call vpoll
        in a, (GEO_IDX)
        rrca
        ret nc
        dec de
        ld a, d
        or e
        jr nz, wg_1
        dec c
        jr nz, wg_1
        ld a, 1
        ld (fail), a
        jp hw_fail

; the command in cmdbuf: A = first register (32 or 36), B = count
issue_cmd:
        push bc
        push af
        call wait_ce
        pop af
        out (VDP_CTRL), a
        ld a, 0x80 + 17
        out (VDP_CTRL), a               ; R#17 = first register, auto-increment
        pop bc
        ld hl, cmdbuf
        ld c, VDP_IND
        otir
        ret

; HL = source, DE = byte count, C = port
otir_n: ld a, d
        or a
        jr z, on_2
on_1:   ld b, 0
        otir
        dec d
        jr nz, on_1
on_2:   ld a, e
        or a
        ret z
        ld b, a
        otir
        ret

; A = texture: its palette (R#16 = 0, then PALBYTES bytes)
pal_write:
        ld (paltex), a
        ld hl, T_PALS
        ld de, PALBYTES
        or a
        jr z, pw_2
pw_1:   add hl, de
        dec a
        jr nz, pw_1
pw_2:   xor a
        ld b, 16
        call vdp_wreg
        ld b, PALBYTES
        ld c, VDP_PAL
        otir
        ret

; A = texture -> HL = its 16-byte entry in T_TEXP:
; TEXX, TEXY, window WSX, WSY, WEX, WEY, HUD source x, y (words)
texp_of:
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        ld de, T_TEXP
        add hl, de
        ret

; ----------------------------------------------------------------------------
; Page flip: show page 1 - pg on the PACE-th blank after the last flip, or,
; when that blank has gone by already (a late frame), on the next one.
flip:
        ld hl, (flip_vbl)
        ld de, PACE
        add hl, de
        ex de, hl                       ; DE = the blank to flip on
        ld hl, (vbl)
        or a
        sbc hl, de
        jr c, fl_1
        ld hl, (late)                   ; late: the next blank
        inc hl
        ld (late), hl
        ld de, (vbl)
        inc de
fl_1:   in a, (VDP_CTRL)                ; S#0 (R#15 = 0)
        rlca
        jr nc, fl_1
        ld hl, (vbl)
        inc hl
        ld (vbl), hl
        or a
        sbc hl, de
        jr c, fl_1
        ; the blank: R#2, then the palette of the texture on that page
        ld hl, (vbl)
        ld (flip_vbl), hl
        ld a, (pg)
        xor 1
        ld c, a                         ; C = page to show
        rrca
        rrca
        rrca
        or 0x1F                         ; R#2 = page * 32 + 1Fh
        ld b, 2
        call vdp_wreg
        ld hl, texpg
        ld a, c
        add a, l
        ld l, a
        ld a, (hl)
        ld hl, paltex
        cp (hl)
        call nz, pal_write
        ld hl, (nflip)
        inc hl
        ld (nflip), hl
        ret

; ----------------------------------------------------------------------------
; The hidden page (pg): clear the box of the sphere drawn there two frames
; ago, copy the HUD (name of this frame's texture, credit), put back the
; stars inside the box.
clear_back:
        call rect_ptr                   ; IX = this page's box
        ld a, (ix + 2)
        or (ix + 3)
        jr z, cb_hud                    ; nothing drawn there yet
        ld hl, cmdbuf                   ; HMMV: R#36..R#46
        ld a, (ix + 0)
        ld (hl), a                      ; DX
        inc hl
        ld a, (ix + 1)
        ld (hl), a
        inc hl
        ld a, (ix + 4)
        ld (hl), a                      ; DY
        inc hl
        ld a, (pg)
        ld (hl), a
        inc hl
        ld a, (ix + 2)
        ld (hl), a                      ; NX
        inc hl
        ld a, (ix + 3)
        ld (hl), a
        inc hl
        ld a, (ix + 6)
        ld (hl), a                      ; NY
        inc hl
        ld (hl), 0
        inc hl
        ld (hl), 0                      ; CLR
        inc hl
        ld (hl), 0                      ; ARG
        inc hl
        ld (hl), 0xC0                   ; HMMV
        ld a, 36
        ld b, 11
        call issue_cmd
cb_hud:
        ld a, (tex)
        call texp_of
        ld de, 12
        add hl, de
        ld e, (hl)
        inc hl
        ld d, (hl)                      ; DE = name's source x
        inc hl
        ld a, (hl)                      ; its source y (page 0, < 256)
        ld hl, NAME_X
        ld bc, NAME_W
        call hud_copy
        ld de, CRED_SX
        ld a, 0 + CRED_SY
        ld hl, CRED_X
        ld bc, CRED_W
        ld iy, 0 + CRED_Y
        call hud_copy_y
        ; stars inside the box: from the first one at a line >= y0 (T_STARY)
        ; while their line is in the box; R#15 = 2 for the CE polls
        ld a, (ix + 2)
        or (ix + 3)
        ret z
        ld l, (ix + 0)
        ld h, (ix + 1)
        ld (sbx0), hl
        ld e, (ix + 2)
        ld d, (ix + 3)
        add hl, de
        ld (sbx1), hl                   ; x0 + nx
        ld a, (ix + 4)
        add a, (ix + 6)
        ld (sby1), a                    ; y0 + ny
        ld l, (ix + 4)
        ld h, 0
        add hl, hl
        ld de, T_STARY
        add hl, de
        ld a, (hl)
        inc hl
        ld h, (hl)
        ld l, a                         ; HL -> the first star
        ld a, 2
        ld b, 15
        call vdp_wreg
cb_s1:  ld e, (hl)
        inc hl
        ld d, (hl)                      ; DE = x
        inc hl
        ld a, (sby1)
        ld b, (hl)                      ; B = y (FFh: the end)
        inc hl
        ld c, (hl)                      ; C = colour
        inc hl
        cp b
        jr c, cb_end                    ; y >= y0 + ny (and the end): done
        jr z, cb_end
        push hl
        ld hl, (sbx1)
        scf
        sbc hl, de                      ; x1 - x - 1 >= 0: x < x1
        jr c, cb_s2
        ex de, hl
        ld de, (sbx0)
        sbc hl, de                      ; (no carry here) x - x0 >= 0
        jr c, cb_s2
        add hl, de                      ; HL = x
        ex de, hl
        ld hl, 0
cb_ce:  in a, (VDP_CTRL)                ; S#2: CE (a PSET takes ~0.3 us)
        rrca
        jr nc, cb_ps
        dec hl
        ld a, h
        or l
        jr nz, cb_ce
        ld a, 2
        ld (fail), a
        jp hw_fail
cb_ps:  ld a, 36
        out (VDP_CTRL), a
        ld a, 0x80 + 17
        out (VDP_CTRL), a               ; R#17 = 36
        ld a, e
        out (VDP_IND), a                ; DX
        ld a, d
        out (VDP_IND), a
        ld a, b
        out (VDP_IND), a                ; DY = y + 256 * pg
        ld a, (pg)
        out (VDP_IND), a
        ld a, 44
        out (VDP_CTRL), a
        ld a, 0x80 + 17
        out (VDP_CTRL), a               ; R#17 = 44
        ld a, c
        out (VDP_IND), a                ; CLR
        xor a
        out (VDP_IND), a                ; ARG
        ld a, 0x50
        out (VDP_IND), a                ; PSET
        ld hl, (nstars)
        inc hl
        ld (nstars), hl
cb_s2:  pop hl
        jr cb_s1
cb_end: xor a
        ld b, 15
        jp vdp_wreg                     ; R#15 = 0

; HMMM of a HUD bitmap: DE = source x, A = source y, HL = destination x,
; BC = width; destination y NAME_Y (hud_copy) or IY (hud_copy_y), on page pg
hud_copy:
        ld iy, 0 + NAME_Y
hud_copy_y:
        push hl
        ld hl, cmdbuf
        ld (hl), e                      ; SX
        inc hl
        ld (hl), d
        inc hl
        ld (hl), a                      ; SY
        inc hl
        ld (hl), 0
        inc hl
        pop de
        ld (hl), e                      ; DX
        inc hl
        ld (hl), d
        inc hl
        push iy
        pop de
        ld (hl), e                      ; DY
        inc hl
        ld a, (pg)
        ld (hl), a
        inc hl
        ld (hl), c                      ; NX
        inc hl
        ld (hl), b
        inc hl
        ld (hl), 0 + HUD_H              ; NY
        inc hl
        ld (hl), 0
        inc hl
        ld (hl), 0                      ; CLR
        inc hl
        ld (hl), 0                      ; ARG
        inc hl
        ld (hl), 0xD0                   ; HMMM
        ld a, 32
        ld b, 15
        jp issue_cmd

; IX = rects + 8 * pg
rect_ptr:
        ld a, (pg)
        add a, a
        add a, a
        add a, a
        ld e, a
        ld d, 0
        ld ix, rects
        add ix, de
        ret

; HL -> a box: the whole picture (512 x 212 from 0, 0)
rect_full:
        ld (hl), 0
        inc hl
        ld (hl), 0
        inc hl
        ld (hl), 0
        inc hl
        ld (hl), 2                      ; NX = 512
        inc hl
        ld (hl), 0
        inc hl
        ld (hl), 0
        inc hl
        ld (hl), 212
        inc hl
        ld (hl), 0
        ret

; ----------------------------------------------------------------------------
; This frame to geo3d (idle: wait_geo came first), then RUN.
send_frame:
        ; the matrix: the rotation table entry, squashed
        ld a, (rot)
        ld l, a
        ld h, 0
        add hl, hl                      ; x 18
        ld d, h
        ld e, l
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, de
        ld de, T_ROT
        add hl, de
        ld de, mat
        ld bc, 18
        ldir
        ld a, (kx)
        ld b, a
        ld a, (ky)
        ld c, a
        or b
        jr z, sf_1
        ld ix, mat                      ; row X: squash kx, stretch ky
        call sqst
        ld ix, mat + 2
        call sqst
        ld ix, mat + 4
        call sqst
        ld a, b                         ; row Y: squash ky, stretch kx
        ld b, c
        ld c, a
        ld ix, mat + 6
        call sqst
        ld ix, mat + 8
        call sqst
        ld ix, mat + 10
        call sqst
sf_1:
        ; LRMM window of the texture (R#51-R#58; geo3d is idle)
        ld a, (tex)
        call texp_of
        push hl
        call wait_ce
        ld a, 51
        out (VDP_CTRL), a
        ld a, 0x80 + 17
        out (VDP_CTRL), a
        pop hl
        push hl
        ld de, 4
        add hl, de
        ld bc, 8 * 256 + VDP_IND
        otir
        ; geo3d
        xor a
        out (GEO_IDX), a                ; 00h: M00..M22
        ld hl, mat
        ld bc, 18 * 256 + GEO_DAT
        otir
        ld a, 0x1A
        out (GEO_IDX), a                ; 1Ah: CX, CY
        ld hl, cx
        ld b, 4
        otir
        ld a, 0x46
        out (GEO_IDX), a                ; 46h: YPAGE = pg * 256
        xor a
        out (GEO_DAT), a
        ld a, (pg)
        out (GEO_DAT), a
        ld a, 0x60
        out (GEO_IDX), a                ; 60h: TEXX, TEXY
        pop hl
        ld b, 4
        otir
        ld a, 0x48
        out (GEO_IDX), a
        ld a, 0x07                      ; RUN, faces, textures
        out (GEO_DAT), a
        ; the texture and the box drawn on this page
        ld hl, texpg
        ld a, (pg)
        add a, l
        ld l, a
        ld a, (tex)
        ld (hl), a
        call rect_ptr
        ld a, (kx)                      ; the box's half sizes for this squash
        add a, a
        add a, a
        ld hl, ky
        add a, (hl)
        ld e, a
        ld d, 0
        ld hl, T_BOXX
        add hl, de
        ld c, (hl)                      ; C = half width
        ld hl, T_BOXY
        add hl, de
        ld a, (hl)
        ld (sbhy), a
        ld b, 0
        ld hl, (cx)                     ; x0 = max(0, cx - hx) & ~1
        or a
        sbc hl, bc
        jr nc, sf_2
        ld hl, 0
sf_2:   res 0, l
        ld (ix + 0), l
        ld (ix + 1), h
        push hl
        ld hl, (cx)                     ; x1 = min(512, cx + hx + 2) & ~1
        inc bc
        inc bc
        add hl, bc
        ld de, 512
        push hl
        or a
        sbc hl, de
        pop hl
        jr c, sf_3
        ex de, hl
sf_3:   res 0, l
        pop de
        or a
        sbc hl, de
        ld (ix + 2), l                  ; NX
        ld (ix + 3), h
        ld a, (sbhy)
        ld c, a
        ld b, 0
        ld hl, (cy)                     ; y0 = max(0, cy - hy)
        or a
        sbc hl, bc
        jr nc, sf_4
        ld hl, 0
sf_4:   ld (ix + 4), l
        ld (ix + 5), 0
        push hl
        ld hl, (cy)                     ; y1 = min(212, cy + hy + 1)
        inc bc
        add hl, bc
        ld de, 212
        push hl
        or a
        sbc hl, de
        pop hl
        jr c, sf_5
        ex de, hl
sf_5:   pop de
        or a
        sbc hl, de
        ld (ix + 6), l                  ; NY
        ld (ix + 7), 0
        ret

; the word at IX: squash by B (x - x >> (5 - B)), then stretch by C
; (x + x >> (6 - C)); 0 = unchanged. Keeps BC.
sqst:   ld l, (ix + 0)
        ld h, (ix + 1)
        ld a, b
        or a
        jr z, sq_1
        ld a, 5
        sub b
        call asr_de
        or a
        sbc hl, de
sq_1:   ld a, c
        or a
        jr z, sq_2
        ld a, 6
        sub c
        call asr_de
        add hl, de
sq_2:   ld (ix + 0), l
        ld (ix + 1), h
        ret

asr_de: ld d, h                         ; DE = HL >> A (arithmetic), A >= 1
        ld e, l
ad_1:   sra d
        rr e
        dec a
        jr nz, ad_1
        ret

; ----------------------------------------------------------------------------
; The next frame: rotation, constant-speed motion reflected at the edges,
; squash, texture change at a side wall.
advance:
        ld a, (rot)
        add a, ROTSTEP
        ld (rot), a
        ; x
        ld hl, (px)
        ld de, (vx)
        add hl, de
        ld (px), hl
        ld de, X_MAX
        push hl
        or a
        sbc hl, de
        pop hl
        jr c, av_x1                     ; px < X_MAX
        ld (px), de                     ; at the wall: the contact frame
        ld hl, 0 - VX0
        ld (vx), hl
        jr av_xb
av_x1:  ld de, X_MIN + 1
        or a
        sbc hl, de
        jr nc, av_y                     ; px > X_MIN
        ld hl, X_MIN
        ld (px), hl
        ld hl, VX0
        ld (vx), hl
av_xb:  ld a, (tex)                     ; a side wall: the next texture
        inc a
        cp NTEX
        jr c, av_xt
        xor a
av_xt:  ld (tex), a
        ld hl, 0x0D8                    ; boing, higher
        call snd_start
av_y:   ; y
        ld hl, (py)
        ld de, (vy)
        add hl, de
        ld (py), hl
        ld de, Y_MAX
        push hl
        or a
        sbc hl, de
        pop hl
        jr c, av_y1
        ld (py), de
        ld hl, 0 - VY0
        ld (vy), hl
        jr av_yb
av_y1:  ld de, Y_MIN + 1
        or a
        sbc hl, de
        jr nc, av_sq
        ld hl, Y_MIN
        ld (py), hl
        ld hl, VY0
        ld (vy), hl
av_yb:  ld hl, 0x150                    ; boing, lower
        call snd_start
av_sq:  ; squash: at the contact frame and the two after it (moving away)
        ld hl, (px)
        ld de, X_MIN
        ld bc, X_MAX
        ld a, (vx + 1)                  ; sign of vx
        call squash_k                   ; A = k, HL = offset
        ld (kx), a
        ld de, (px)
        add hl, de
        ld (cx), hl
        ld hl, (py)
        ld de, Y_MIN
        ld bc, Y_MAX
        ld a, (vy + 1)
        call squash_k_y
        ld (ky), a
        ld de, (py)
        add hl, de
        ld (cy), hl
        ret

; HL = position, DE = min, BC = max, A = high byte of the velocity:
; A = squash 0..3, HL = signed offset of the centre towards the wall
squash_k:
        push af
        ld ix, sqd_x
        ld iy, SQ_UX
        jr sk_go
squash_k_y:
        push af
        ld ix, sqd_y
        ld iy, SQ_UY
sk_go:  push hl
        or a
        sbc hl, de                      ; HL = d_low = pos - min
        ex (sp), hl                     ; (sp) = d_low, HL = pos
        push hl
        ld h, b
        ld l, c
        pop de
        or a
        sbc hl, de                      ; HL = d_high = max - pos
        pop de                          ; DE = d_low
        pop af                          ; A = velocity sign byte
        push hl
        or a
        sbc hl, de                      ; d_high - d_low
        pop hl
        jr c, sk_high                   ; nearer the high wall
        ; the low wall: d = d_low, moving away when v > 0, offset -
        bit 7, a
        jr nz, sk_none
        ex de, hl
        call sk_level
        or a
        jr z, sk_none
        call sk_off
        ex de, hl
        ld hl, 0
        or a
        sbc hl, de
        ret
sk_high:
        bit 7, a
        jr z, sk_none                   ; moving towards it
        call sk_level
        or a
        jr z, sk_none
        jp sk_off
sk_none:
        xor a
        ld hl, 0
        ret

; HL = distance, IY = unit u -> A = 3 (d < u), 2 (d < 2u), 1 (d < 3u), else 0
sk_level:
        push iy
        pop de
        ld a, h
        or a
        jr nz, skl_0
        ld a, l
        ld b, 3
skl_1:  cp e
        jr c, skl_k
        sub e
        djnz skl_1
skl_0:  xor a
        ret
skl_k:  ld a, b
        ret

; A = k (1..3) -> HL = the offset from the table at IX; keeps A
sk_off: push af
        ld e, a
        ld d, 0
        add ix, de
        ld l, (ix + 0)
        ld h, 0
        pop af
        ret

sqd_x:  db 0, SQDX1, SQDX2, SQDX3
sqd_y:  db 0, SQDY1, SQDY2, SQDY3

; ----------------------------------------------------------------------------
state_reset:
        ld hl, START_X
        ld (px), hl
        ld (cx), hl
        ld hl, START_Y
        ld (py), hl
        ld (cy), hl
        ld hl, VX0
        ld (vx), hl
        ld hl, VY0
        ld (vy), hl
        xor a
        ld (rot), a
        ld (tex), a
        ld (kx), a
        ld (ky), a
        ret

; ----------------------------------------------------------------------------
; Keys: SPACE (row 8 bit 0) next texture, ESC (row 7 bit 2) start again
keys:   ld a, 7
        call key_row                    ; (changes B)
        and 4
        rrca
        ld c, a
        ld a, 8
        call key_row
        and 1
        or c                            ; bit0 SPACE, bit1 ESC
        ld c, a
        ld a, (keyprev)
        cpl
        and c                           ; new presses
        ld b, a
        ld a, c
        ld (keyprev), a
        bit 0, b
        jr z, ky_1
        ld a, (tex)
        inc a
        cp NTEX
        jr c, ky_0
        xor a
ky_0:   ld (tex), a
ky_1:   bit 1, b
        ret z
        jp state_reset

key_row:                                ; A = row; returns its keys, 1 = down
        ld b, a
        in a, (PPI_C)
        and 0xF0
        or b
        out (PPI_C), a
        in a, (PPI_B)
        cpl
        ret

; ----------------------------------------------------------------------------
; Bounce sound: PSG channel A, the pitch falls and the volume decays
psg_init:
        ld a, 7
        out (PSG_A), a
        ld a, 0xBE                      ; tone A only; port B out, port A in
        out (PSG_D), a
        ld a, 8
        out (PSG_A), a
        xor a
        out (PSG_D), a
        ret

snd_start:                              ; HL = tone period
        ld (snd_per), hl
        ld a, SND_LEN
        ld (snd_t), a
        ld hl, (bounces)
        inc hl
        ld (bounces), hl
        ret

snd_tick:
        ld a, (snd_t)
        or a
        ret z
        dec a
        ld (snd_t), a
        ld b, a
        ld a, 8
        out (PSG_A), a
        ld a, b                         ; volume = frames left (11..0) + 3
        if PACE == 1
        srl a                           ; (60 fps: twice the frames)
        endif
        add a, 3
        cp 4
        jr nc, st_1
        xor a                           ; the last frame: silence
st_1:   out (PSG_D), a
        ld hl, (snd_per)
        ld d, h
        ld e, l
        srl d
        rr e
        srl d
        rr e
        srl d
        rr e
        if PACE == 1
        srl d
        rr e                            ; 60 fps: x 17/16 a frame
        endif
        add hl, de                      ; period x 9/8: the pitch falls
        ld a, h
        and 0x0F
        ld h, a
        ld (snd_per), hl
        xor a
        out (PSG_A), a
        ld a, l
        out (PSG_D), a
        ld a, 1
        out (PSG_A), a
        ld a, h
        out (PSG_D), a
        ret

; ----------------------------------------------------------------------------
; VDP set-up: SCREEN 7 on the V9968 in V9968 mode (256 KB, LRMM), high-speed
; commands, EPAL (unless PAL9), display off; VRAM pages 0-1 cleared, the
; textures and the HUD bitmaps uploaded.
vdp_init:
        call wait_ce
        xor a
        out (VDP_PORT4), a              ; bit 7 = 0: unlock R#20 / R#21
        xor a
        ld b, 21
        call vdp_wreg                   ; R#21 = 0: V9968 mode
        if PAL9
        ld a, 0x81
        else
        ld a, 0x91                      ; S16, EPAL (15-bit palette), HS
        endif
        ld b, 20
        call vdp_wreg
        ld hl, init_regs
vi_1:   ld a, (hl)
        cp 0xFF
        jr z, vi_2
        ld b, a
        inc hl
        ld a, (hl)
        inc hl
        call vdp_wreg
        jr vi_1
vi_2:   ld hl, cmdbuf                   ; HMMV 512 x 512 from 0, 0: pages 0, 1
        ld b, 11
vi_3:   ld (hl), 0
        inc hl
        djnz vi_3
        ld a, 2
        ld (cmdbuf + 5), a              ; NX = 512
        ld (cmdbuf + 7), a              ; NY = 512
        ld a, 0xC0
        ld (cmdbuf + 10), a
        ld a, 36
        ld b, 11
        call issue_cmd
        call wait_ce
        ; textures: banks 2-9 -> VRAM 20000h-3FFFFh
        ld a, 2
vi_t:   push af
        call setbank
        add a, 6                        ; R#14 = A17..A14 = 8 + (bank - 2)
        ld b, 14
        call vdp_wreg
        xor a
        out (VDP_CTRL), a
        ld a, 0x40
        out (VDP_CTRL), a
        ld hl, 0x8000
        ld de, 0x4000
        ld c, VDP_DATA
        call otir_n
        pop af
        inc a
        cp 10
        jr c, vi_t
        ; HUD bitmaps: bank 10 -> line HUD_Y of page 0
        ld a, 10
        call setbank
        ld a, (HUD_Y * 256) >> 14
        ld b, 14
        call vdp_wreg
        xor a
        out (VDP_CTRL), a
        ld a, 0x40 | (((HUD_Y * 256) >> 8) & 0x3F)
        out (VDP_CTRL), a
        ld hl, 0x8000
        ld de, HUD_ROWS * 256
        ld c, VDP_DATA
        call otir_n
        ; the guard row of the first texture slots: line 511 (page 1, below
        ; the 212 lines shown), right after the HUD in bank 10
        ld a, (511 * 256) >> 14
        ld b, 14
        call vdp_wreg
        xor a
        out (VDP_CTRL), a
        ld a, 0x40 | (((511 * 256) >> 8) & 0x3F)
        out (VDP_CTRL), a
        ld hl, 0x8000 + GUARD_OFS
        ld de, 256
        ld c, VDP_DATA
        call otir_n
        xor a
        ld b, 14
        call vdp_wreg
        ld a, 1
        call setbank               ; the tables, for good
        ret

init_regs:
        db 1, 0x00                      ; display off, no interrupts
        db 0, 0x0A                      ; GRAPHIC6 (SCREEN 7)
        db 2, 0x1F                      ; page 0
        db 7, 0x00                      ; black border
        db 8, 0x0A                      ; sprites off
        db 9, 0x80                      ; 212 lines, 60 Hz
        db 15, 0x00
        db 23, 0x00
        db 25, 0x00
        db 26, 0x00
        db 27, 0x00
        db 0xFF

; geo3d set-up: registers, the sphere's vertices, faces and texture
; coordinates (bank 1), the light, TSTRIDE
geo_init:
        xor a
        out (GEO_IDX), a
        ld hl, cfg_words
        ld bc, 36 * 256 + GEO_DAT
        otir
        ld a, 0x40
        out (GEO_IDX), a
        ld hl, geo_40
        ld bc, 8 * 256 + GEO_DAT
        otir
        ld a, 0x50
        out (GEO_IDX), a
        ld hl, T_VERTS
        ld de, NVERT * 6
        ld c, GEO_DAT
        call otir_n
        ld a, 0x58
        out (GEO_IDX), a
        ld hl, geo_58
        ld bc, 8 * 256 + GEO_DAT
        otir
        ld a, 0x52
        out (GEO_IDX), a
        ld hl, T_FACES
        ld de, NFACE * 11
        ld c, GEO_DAT
        call otir_n
        ld a, 0x60
        out (GEO_IDX), a
        ld hl, geo_60
        ld bc, 6 * 256 + GEO_DAT
        otir
        ld a, 0x53
        out (GEO_IDX), a
        ld hl, T_UVS
        ld de, NFACE * 8
        ld c, GEO_DAT
        jp otir_n

geo_40: db 0, 0, NVERT, 0, 15, 0, 0, 0         ; VADDR EADDR NVERT NEDGE COLOR LOP YPAGE
geo_58: db 0, NFACE
        dw LIGHTX, LIGHTY, LIGHTZ                ; FADDR NFACE LX LY LZ
geo_60: dw 0, 0                                  ; TEXX TEXY (every frame)
        db TSTRIDE, 0                            ; TSTRIDE TADDR

; ----------------------------------------------------------------------------
; geo_probe: geo3d at PORT_BASE? As rom/geo3d_rom.asm (reads only until it
; is identified). Z: found.
geo_probe:
        if PORT_BASE == 0x98
        ld a, (MSXVER)
        or a
        jr z, gp_no                     ; an MSX1: a TMS9918 at 98h
        endif
        ld a, 1
        ld b, 15
        call vdp_wreg
        in a, (VDP_CTRL)                ; S#1
        ld e, a
        xor a
        ld b, 15
        call vdp_wreg
        ld a, e
        rrca
        and 0x1F
        cp 2
        jr c, gp_no                     ; V9938 (0), V9948 (1)
        cp 4
        jr nc, gp_no                    ; not a V99x8
        ld a, 2
        ld b, 15
        call vdp_wreg
        in a, (GEO_IDX)
        ld e, 0xFF
        cp 0xFF
        jr z, gp_1
        and 0x0C
        cp 0x0C
        jr z, gp_1                      ; S#2 of a VDP repeated at P+4..P+7
        in a, (VDP_PORT4)
        ld e, a
gp_1:   xor a
        ld b, 15
        call vdp_wreg
        ld a, e
        and 0x78
        jr nz, gp_no
        ld a, 0x40
        out (GEO_IDX), a
        in a, (GEO_DAT)                 ; 40h
        ld d, a
        in a, (GEO_DAT)
        in a, (GEO_DAT)
        in a, (GEO_DAT)
        in a, (GEO_DAT)
        in a, (GEO_DAT)                 ; 45h LOP
        and 0xF0
        jr nz, gp_no
        in a, (GEO_DAT)
        in a, (GEO_DAT)                 ; 47h
        and 0xF8
        jr nz, gp_no
        ld b, 8
gp_2:   in a, (GEO_DAT)
        djnz gp_2
        in a, (GEO_DAT)                 ; 40h again
        cp d
        ret
gp_no:  or 0xFF
        ret

; ----------------------------------------------------------------------------
; No geo3d, or it (or the command engine) stopped answering: a message on
; the MSX's own text screen (BIOS), and stay there.
hw_fail:
        di
        ld sp, 0xF000
        xor a
        ld b, 46
        call vdp_wreg                   ; STOP the command engine
        ld hl, msg_fail
        jr ng_text
nogeo:
        ld hl, msg_nogeo
ng_text:
        push hl
        call INITXT
        di
        pop hl
ng_1:   ld a, (hl)
        or a
        jr z, ng_stay
        push hl
        call CHPUT
        di
        pop hl
        inc hl
        jr ng_1
ng_stay:
        jr ng_stay

msg_nogeo:
        db "SPHERE7: geo3d not found.", 13, 10
        if PORT_BASE == 0x88
        db "It needs the V9968 cartridge with the", 13, 10
        db "geo3d build, DIP switch at 88h.", 13, 10, 0
        else
        db "It needs a V9968 at 98h with geo3d", 13, 10
        db "(openMSX: -ext geo3d).", 13, 10, 0
        endif
msg_fail:
        db "SPHERE7: geo3d or the V9968 command", 13, 10
        db "engine stopped answering.", 13, 10, 0

        include "out/inc/sphere7_cfg.asm"

bank0_end:
        ds 0x8000 - bank0_end, 0xFF
