; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; spr.asm - hardware sprites: bullets, bolts, hitboxes, flashes, the HUD
;
; Two attribute tables, one per page: page 0 shows 7600h (colours 7400h,
; R#5 = EFh), page 1 shows 7200h (colours 7000h, R#5 = E7h); the ISR
; switches R#5 with R#2. Patterns at 7800h (R#6 = 0Fh): sprites.asm's 14,
; then the HUD's 4 patterns per page at 48-51 and 52-55 (drawn from the
; font when the score changes), so nothing on show is ever rewritten.
;
; Plane map (tools/sprites.py; the V9968 reports a collision where a sprite
; with IC = 0 has a dot under a visible dot of a lower plane, whatever its
; own colour):
;   0      guard: blank, IC = 1, at the ship (keeps the core from being the
;          first sprite of its lines, see sprites.py)
;   1-6    enemy bullets (visible, IC = 1)
;   7      the ship's core (visible, IC = 0): hit by bullets, rams hitboxes
;   8      the ship's hitbox (colour 0, IC = 0), before the planes that a
;          full line (16 sprites) may drop
;   9-12   the ship's bolts (visible, IC = 1)
;   13-23  enemy hitboxes (colour 0: invisible, IC = 0)
;   24-27  explosion flashes (IC = 1)
;   28-31  HUD (IC = 1)
; The colours are per plane and fixed, but the flashes': their 16 colour
; bytes are rewritten in the hidden table when a plane's flash kind changes.
; Unused planes sit at Y = 212 (below the 212 lines; 216 would end the list).
; ============================================================================

HUD_Y:      equ 2

spr_init:
        ld c, 0
        ld hl, SPR_PAT_ADDR
        call vaddr_w
        ld hl, spr_patterns
        ld de, SPR_NPAT * 32
        ld c, VDP_DATA
si_1:   outi
        dec de
        ld a, d
        or e
        jr nz, si_1
        ld hl, SPR_SAT_A - 512
        call col_table_init
        ld hl, SPR_SAT_B - 512
        call col_table_init
        ld hl, SPR_SAT_A
        call sat_hide
        ld hl, SPR_SAT_B
        jp sat_hide

; HL = colour table address: each plane's 16 colour bytes (plane_cols)
col_table_init:
        ld c, 0
        call vaddr_w
        ld ix, plane_cols
        ld b, 32
ct_1:   ld l, (ix + 0)
        ld h, (ix + 1)
        push bc
        ld bc, 16 * 256 + VDP_DATA
        otir
        pop bc
        inc ix
        inc ix
        djnz ct_1
        ret

; HL = attribute table: 32 planes at Y 212
sat_hide:
        ld c, 0
        call vaddr_w
        ld b, 32
sh_1:   ld a, 212
        out (VDP_DATA), a
        xor a
        out (VDP_DATA), a
        out (VDP_DATA), a
        out (VDP_DATA), a
        djnz sh_1
        ret

plane_cols:
        dw spr_col_blank
        dw spr_col_e_bullet, spr_col_e_bullet, spr_col_e_bullet
        dw spr_col_e_bullet, spr_col_e_bullet, spr_col_e_bullet
        dw spr_col_p_core
        dw spr_col_p_hit
        dw spr_col_p_bolt, spr_col_p_bolt, spr_col_p_bolt, spr_col_p_bolt
        dw spr_col_h_dart, spr_col_h_dart, spr_col_h_dart, spr_col_h_dart
        dw spr_col_h_dart, spr_col_h_dart, spr_col_h_dart, spr_col_h_dart
        dw spr_col_h_dart, spr_col_h_dart, spr_col_h_dart
        dw spr_col_flash0, spr_col_flash0, spr_col_flash0, spr_col_flash0
        dw hud_col_w, hud_col_w, hud_col_w, hud_col_c

hud_col_w:
        db 0x26, 0x26, 0x26, 0x26, 0x26, 0x26, 0x26, 0x26
        db 0x26, 0x26, 0x26, 0x26, 0x26, 0x26, 0x26, 0x26
hud_col_c:
        db 0x27, 0x27, 0x27, 0x27, 0x27, 0x27, 0x27, 0x27
        db 0x27, 0x27, 0x27, 0x27, 0x27, 0x27, 0x27, 0x27

fx_cols:
        dw spr_col_flash0, spr_col_flash1, spr_col_flash2

; ---------------------------------------------------------------- build
; A = pattern number, HL = x, DE = y (pixels, signed), IX -> SAT entry:
; the pattern's anchor on (x, y), hidden when off the screen
spr_at:
        push af
        push bc
        push hl
        rrca
        and 0x7E                    ; pattern / 2: anchors (x, y)
        add a, anchors & 0xFF
        ld l, a
        ld a, anchors >> 8
        adc a, 0
        ld h, a
        ld c, (hl)                  ; anchor x
        inc hl
        ld b, (hl)                  ; anchor y
        pop hl
        ld a, l
        sub c
        ld l, a
        ld a, h
        sbc a, 0
        jr nz, sa_hide              ; X outside 0..255
        ld (ix + 1), l
        ex de, hl
        ld a, l
        sub b
        ld l, a
        ld a, h
        sbc a, 0
        ld h, a
        dec hl                      ; Y = y - ay - 1
        ld a, h
        or a
        jr z, sa_pos
        inc a
        jr nz, sa_hide
        ld a, l
        cp 0xF0                     ; -16..-1: partly on top
        jr c, sa_hide
        jr sa_ok
sa_pos: ld a, l
        cp 212
        jr nc, sa_hide
sa_ok:  ld (ix + 0), l
        pop bc
        pop af
        ld (ix + 2), a
        ret
sa_hide:
        ld (ix + 0), 212
        pop bc
        pop af
        ret

; the sprite list of this tick
spr_build:
        ld hl, sat
        ld de, 4
        ld b, 28                    ; (the HUD's 4 are set below)
        ld a, 212
sb_0:   ld (hl), a
        add hl, de
        djnz sb_0
        ; the ship: guard, core, hitbox (only while it can be hit)
        ld a, (pl_vuln)
        or a
        jr z, sb_bu
        ld hl, (pl_x)
        call px_of
        push hl
        ld hl, (pl_y)
        call px_of
        ex de, hl
        pop hl
        ld ix, sat + 4 * PL_GUARD
        ld a, SP_BLANK
        push hl
        push de
        call spr_at
        pop de
        pop hl
        ld ix, sat + 4 * PL_P_CORE
        ld a, SP_P_CORE
        push hl
        push de
        call spr_at
        pop de
        pop hl
        ld ix, sat + 4 * PL_P_HIT
        ld a, SP_P_HIT
        call spr_at
sb_bu:  ; enemy bullets: slot k -> plane 1 + k
        call geo_pump
        ld ix, sat + 4 * PL_E_BULLET
        ld iy, bullets
        ld b, BU_MAX
sb_1:   ld a, (iy + 0)
        or a
        jr z, sb_1n
        push bc
        ld l, (iy + 7)
        ld h, (iy + 8)
        ld e, (iy + 9)
        ld d, 0
        ld a, (st_tick)
        and 4
        ld a, SP_E_BULLET
        jr z, sb_1a
        ld a, SP_E_BULLET2
sb_1a:  call spr_at
        pop bc
sb_1n:  ld de, 4
        add ix, de
        ld de, BU_SZ
        add iy, de
        djnz sb_1
        ; bolts: slot k -> plane 8 + k
        call geo_pump
        ld ix, sat + 4 * PL_P_BOLT
        ld iy, bolts
        ld b, BO_MAX
sb_2:   ld a, (iy + 0)
        or a
        jr z, sb_2n
        push bc
        ld l, (iy + 1)
        ld h, 0
        ld e, (iy + 2)
        ld d, 0
        ld a, SP_P_BOLT
        call spr_at
        pop bc
sb_2n:  ld de, 4
        add ix, de
        ld de, BO_SZ
        add iy, de
        djnz sb_2
        ; enemy hitboxes: planes 12-22 in slot order (the gunship takes 2)
        call geo_pump
        ld ix, sat + 4 * PL_E_HIT
        ld iy, enem
        ld b, EN_MAX
        ld c, PL_E_HIT_N            ; planes left
sb_3:   ld a, (iy + E_TYPE)
        cp 0xFF
        jr z, sb_3n
        ld a, (iy + E_ON)
        or a
        jr z, sb_3n
        ld a, c
        or a
        jr z, sb_3n
        dec c
        push bc
        ld l, (iy + E_PX)
        ld h, (iy + E_PX + 1)
        ld e, (iy + E_PY)
        ld d, 0
        ld a, (iy + E_PAT0)
        push hl
        push de
        call spr_at
        pop de
        pop hl
        pop bc
        ld a, (iy + E_NBOX)
        cp 2
        jr c, sb_3a
        ld a, c
        or a
        jr z, sb_3a
        dec c
        push bc
        push de
        ld de, 4
        add ix, de
        pop de
        ld a, (iy + E_PAT1)
        call spr_at
        pop bc
sb_3a:  ld de, 4
        add ix, de
sb_3n:  ld de, EN_SZ
        add iy, de
        djnz sb_3
        call geo_pump
        ; flashes: slot k -> plane 24 + k, kind by age (a free plane keeps
        ; the kind its colour table of this page has: nothing to rewrite)
        ld a, (buf)
        add a, a
        add a, a
        ld e, a
        ld d, 0
        ld hl, fx_col
        add hl, de
        ld de, fx_want
        ld bc, 4
        ldir
        ld ix, sat + 4 * PL_FX
        ld iy, flashes
        ld hl, fx_want
        ld b, FL_MAX
sb_4:   ld a, (iy + 0)
        or a
        jr z, sb_4n
        push bc
        push hl
        ld a, (iy + 3)              ; age
        ld c, 0
        cp 3
        jr c, sb_4a
        inc c
        cp 6
        jr c, sb_4a
        inc c
sb_4a:  ld a, c
        ld (hl), a                  ; kind wanted
        add a, a
        add a, a
        add a, SP_FLASH0
        push af
        ld l, (iy + 1)
        ld h, 0
        ld e, (iy + 2)
        ld d, 0
        pop af
        call spr_at
        pop hl
        pop bc
        jr sb_4k
sb_4n:
sb_4k:  inc hl
        ld de, 4
        add ix, de
        ld de, FL_SZ
        add iy, de
        djnz sb_4
        ; HUD: planes 28-31, patterns of this page
        ld a, (buf)
        add a, a
        add a, a
        add a, a
        add a, a
        add a, 192                  ; 192 + 16 buf
        ld c, a
        ld ix, sat + 4 * PL_HUD
        ld hl, hud_pos_game
        ld a, (hud_mode)
        or a
        jr z, sb_5
        ld hl, hud_pos_title
sb_5:   ld b, 4
sb_5a:  ld (ix + 0), HUD_Y
        ld a, (hl)
        ld (ix + 1), a
        ld (ix + 2), c
        inc hl
        ld a, c
        add a, 4
        ld c, a
        ld de, 4
        add ix, de
        djnz sb_5a
        ret

hud_pos_game:
        db 8, 24, 40, 224
hud_pos_title:
        db 104, 120, 136, 88

; ---------------------------------------------------------------- upload
; the hidden page's attribute table, its flash colours, its HUD patterns
spr_upload:
        ld a, (buf)
        or a
        ld hl, SPR_SAT_A
        jr z, sup_0
        ld hl, SPR_SAT_B
sup_0:   push hl
        ld c, 0
        call vaddr_w
        ld hl, sat
        ld bc, 128 * 256 + VDP_DATA
        otir
        pop hl
        call geo_pump
        ; flash colours: table at SAT - 512, plane 24 + k
        ld de, -512 + 16 * PL_FX
        add hl, de                  ; colours of plane 24
        ld a, (buf)
        add a, a
        add a, a
        ld e, a
        ld d, 0
        ld iy, fx_col
        add iy, de
        ld ix, fx_want
        ld b, 4
sup_1:   ld a, (ix + 0)
        cp (iy + 0)
        jr z, sup_1n
        ld (iy + 0), a
        push bc
        push hl
        add a, a
        ld e, a
        ld d, 0
        push hl
        ld hl, fx_cols
        add hl, de
        ld e, (hl)
        inc hl
        ld d, (hl)
        pop hl
        push de
        ld c, 0
        call vaddr_w
        pop hl
        ld bc, 16 * 256 + VDP_DATA
        otir
        pop hl
        pop bc
sup_1n:  ld de, 16
        add hl, de
        inc ix
        inc iy
        djnz sup_1
        call geo_pump
        ; HUD patterns
        ld a, (buf)
        ld e, a
        ld d, 0
        ld hl, hud_dirty
        add hl, de
        ld a, (hl)
        or a
        ret z
        ld (hl), 0
        ld a, (buf)
        rrca                        ; 80h for page 1
        ld l, a
        ld h, 0x7E                  ; 7E00h / 7E80h: patterns 48 / 52
        ld c, 0
        call vaddr_w
        ld ix, hudtxt
        ld b, 4
sup_2:   ld a, (ix + 0)
        call hud_glyph
        ld a, (ix + 1)
        call hud_glyph
        inc ix
        inc ix
        djnz sup_2
        ret

; A = glyph code: its 8 rows and 8 blank rows (one half of a 16x16 pattern)
hud_glyph:
        push bc
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        ld de, font8
        add hl, de
        ld bc, 8 * 256 + VDP_DATA
        otir
        xor a
        ld b, 8
hg_1:   out (VDP_DATA), a
        djnz hg_1
        pop bc
        ret

; the HUD text from the score and lives (or the hi-score on the title)
hud_refresh:
        push hl
        push de
        push bc
        ld a, (hud_mode)
        or a
        ld hl, st_score + 2
        jr z, hr_1
        ld hl, st_hi + 2
hr_1:   ld de, hudtxt
        ld b, 3
hr_2:   ld a, (hl)
        rrca
        rrca
        rrca
        rrca
        and 0x0F
        inc a
        ld (de), a
        inc de
        ld a, (hl)
        and 0x0F
        inc a
        ld (de), a
        inc de
        dec hl
        djnz hr_2
        ld a, (hud_mode)
        or a
        jr nz, hr_t
        ld a, 42                    ; '@': the ship icon
        ld (de), a
        inc de
        ld a, (st_lives)
        cp 9
        jr c, hr_3
        ld a, 9
hr_3:   inc a
        ld (de), a
        jr hr_x
hr_t:   ld a, 18                    ; 'H'
        ld (de), a
        inc de
        ld a, 19                    ; 'I'
        ld (de), a
hr_x:   ld a, 1
        ld (hud_dirty), a
        ld (hud_dirty + 1), a
        pop bc
        pop de
        pop hl
        ret
