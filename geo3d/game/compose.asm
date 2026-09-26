; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; compose.asm - the page composition: stars, background, foreground band
;
; Each layer has a scroll offset s (pixels here; RAM keeps 1/16 px). Screen
; column c shows layer column c + s: while s < 0 the layer is still coming
; in from the right. Its edge (columns 0..edge-1 without it) is
; (-s + 1) & ~1: even, because HMMM moves bytes (2 pixels); the layer then
; starts at its column x = s + edge (0 or 1). Columns left of the
; background's edge show the stars, left of the foreground's edge the
; background.
;
; VRAM (tools/common.py VRAM_LAYOUT): background 512 wide in pages 2 (x
; 0-255) and 3 (x 256-511), the copy shifted by one pixel in pages 4 and 5
; (BG1[x] = BG[x + 1], read at odd x), the foreground at Y 1536-1583 (page
; 6).
;
; Commands per frame (queued, vdp.asm):
;   stars      HMMV black, columns 0..eb-1 (the stars are plotted later)
;   background HMMM, 1-2 pieces (the ring wraps at 512), 212 lines, or 196
;              once the foreground covers the bottom 16 lines
;   band       LMMM + TIMP (colour 0 = hole), 48 lines at 164-211, 1-2
;              pieces. The engine design had HMMM for the band's 16 opaque
;              rows (from a shifted copy) and LMMM for the 32 others: 2-4
;              commands. One LMMM costs the VDP ~0.5 ms more on the RTL,
;              hidden behind the game logic, and saves the Z80 ~0.6 ms of
;              queueing per frame, which is on the critical path.
; ============================================================================

STAR_Y:     equ 0               ; added to stars_xy's y

; HL = s in 1/16 px -> HL = s in pixels (arithmetic shift)
px_of:
        sra h
        rr l
        sra h
        rr l
        sra h
        rr l
        sra h
        rr l
        ret

; HL = layer offset in pixels (signed) -> HL = edge (0..256, even),
; (lx) = the layer's x at column `edge`
edge2:
        bit 7, h
        jr nz, e2_a
        ld (lx), hl
        ld hl, 0
        ret
e2_a:   ex de, hl
        ld hl, 0
        or a
        sbc hl, de                  ; -s
        ld a, h
        or a
        jr z, e2_b
        ld hl, 256                  ; s <= -256: not there
        ld (lx), hl
        ret
e2_b:   inc hl
        res 0, l                    ; (-s + 1) & ~1
        push hl
        add hl, de
        ld (lx), hl                 ; s + edge
        pop hl
        ret

compose_begin:
        call q_reset
        ld a, (buf)
        ld (rc_dy + 1), a
        xor a
        ld (rc_dy), a               ; D = 256 * buf
        ; the foreground's edge and x
        ld hl, (s_fg)
        call px_of
        call edge2
        ld (ef), hl
        ld hl, (lx)
        ld (tmp + 8), hl            ; its x
        ; the background's
        ld hl, (s_bg)
        call px_of
        call edge2
        ld (eb), hl
        ; geo3d clips at the band when the foreground is there
        ld hl, 212
        ld de, (ef)
        ld a, d
        or a
        jr nz, cb_h
        ld hl, 164
cb_h:   ld (g3_h), hl
        ; 1. stars: clear columns 0..eb-1
        ld hl, (eb)
        ld a, h
        or l
        jr z, cb_1
        ld (cmdbuf + 8), hl         ; NX
        ld hl, 0
        ld (cmdbuf + 0), hl
        ld (cmdbuf + 2), hl
        ld (cmdbuf + 4), hl         ; DX = 0
        ld (cmdbuf + 12), hl        ; CLR 0, ARG 0
        ld hl, (rc_dy)
        ld (cmdbuf + 6), hl
        ld hl, 212
        ld (cmdbuf + 10), hl
        ld a, HMMV
        ld (cmdbuf + 14), a
        call q_add
cb_1:   ; 2. the background on columns eb..255
        ld hl, (eb)
        ld de, 256
        or a
        sbc hl, de
        jr z, cb_2
        ex de, hl
        ld hl, 0
        or a
        sbc hl, de
        ld (rc_w), hl               ; 256 - eb
        ld hl, (eb)
        ld (rc_dx), hl
        ld hl, 196
        ld de, (ef)
        ld a, d
        or e
        jr z, cb_1a
        ld hl, 212                  ; the band is not all there
cb_1a:  ld (rc_ny), hl
        ld hl, (lx)                 ; 0..511
        ld a, l
        and 1
        ld b, a                     ; k: the shifted copy at odd x
        ld a, l
        and 0xFE
        ld (rc_x0), a
        ld a, h
        and 1
        ld (rc_x0 + 1), a
        ld a, 1
        ld (rc_two), a
        xor a
        ld (rc_sy), a
        ld a, b
        add a, a
        add a, 2
        ld (rc_sy + 1), a           ; Y 512 (k = 0) or 1024 (k = 1)
        ld a, HMMM
        ld (rc_cmd), a
        call ring_copy
cb_2:   ; 3. the band on columns ef..255: LMMM + TIMP from Y 1536, any x
        ld hl, (ef)
        ld de, 256
        or a
        sbc hl, de
        jp z, q_pump                ; not there: start the queue
        ex de, hl
        ld hl, 0
        or a
        sbc hl, de
        ld (rc_w), hl
        ld hl, (ef)
        ld (rc_dx), hl
        xor a
        ld (rc_two), a
        ld (rc_x0 + 1), a
        ld a, (rc_dy + 1)
        ld h, a
        ld l, 164
        ld (rc_dy), hl
        ld hl, 48
        ld (rc_ny), hl
        ld a, (tmp + 8)
        ld (rc_x0), a
        ld hl, 1536
        ld (rc_sy), hl
        ld a, LMMM + TIMP
        ld (rc_cmd), a
        call ring_copy
        jp q_pump

; queue a copy of columns [x0, x0 + w) of a ring (256 wide, or 512 wide in
; two pages of 256 lines when rc_two) to [dx, dx + w) at line rc_dy, rc_ny
; lines, with rc_cmd: one command, or two where the ring wraps
ring_copy:
        ld hl, (rc_x0)
        ld a, (rc_two)
        or a
        jr nz, rc_1
        ld h, 0
rc_1:   ld a, (rc_sy + 1)
        add a, h
        ld (cmdbuf + 3), a
        ld a, (rc_sy)
        ld (cmdbuf + 2), a
        ld a, l
        ld (cmdbuf + 0), a
        xor a
        ld (cmdbuf + 1), a
        ld a, l
        neg
        ld e, a
        ld d, 0
        or a
        jr nz, rc_2
        inc d                       ; 256
rc_2:   ld hl, (rc_w)
        or a
        sbc hl, de
        jr nc, rc_3
        ld de, (rc_w)
rc_3:   ld (rc_len1), de
        ld hl, (rc_dx)
        ld (cmdbuf + 4), hl
        ld hl, (rc_dy)
        ld (cmdbuf + 6), hl
        ld (cmdbuf + 8), de
        ld hl, (rc_ny)
        ld (cmdbuf + 10), hl
        xor a
        ld (cmdbuf + 12), a
        ld (cmdbuf + 13), a
        ld a, (rc_cmd)
        ld (cmdbuf + 14), a
        call q_add
        ld hl, (rc_w)
        ld de, (rc_len1)
        or a
        sbc hl, de
        ret z
        ld (cmdbuf + 8), hl
        ld hl, (rc_dx)
        add hl, de
        ld (cmdbuf + 4), hl
        xor a
        ld (cmdbuf + 0), a
        ld a, (rc_two)
        or a
        jr z, rc_4
        ld a, (rc_x0 + 1)
        xor 1
        ld b, a
        ld a, (rc_sy + 1)
        add a, b
        ld (cmdbuf + 3), a
rc_4:   jp q_add

; ---------------------------------------------------------------- stars
; 3 layers of STAR_N stars (level.asm stars_xy), plotted by the Z80 into
; the black columns 0..eb-1 of the page (one byte = 2 pixels: the star's
; nibble, black beside it). Layer k moves at (1, 2, 4)[k] x v_st / 4.
stars_draw:
        ld hl, (eb)
        ld a, h
        or l
        ret z
        ld a, h
        or a
        ld a, l
        jr z, sd_0
        xor a                       ; 256: no limit
sd_0:   ld (tmp), a
        ld a, (buf)
        rrca
        ld (tmp + 1), a             ; A15 of the page (80h for page 1)
        ld ix, stars_xy
        ld iy, s_st
        xor a
        ld (tmp + 2), a
sd_layer:
        ld l, (iy + 0)
        ld h, (iy + 1)
        ld a, l
        rrca
        rrca
        rrca
        rrca
        and 0x0F
        ld e, a
        ld a, h
        rlca
        rlca
        rlca
        rlca
        and 0xF0
        or e
        ld (tmp + 3), a             ; offset in pixels (mod 256)
        ld a, (tmp + 2)
        ld e, a
        ld d, 0
        ld hl, star_col
        add hl, de
        ld a, (hl)
        ld (tmp + 4), a
        rlca
        rlca
        rlca
        rlca
        ld (tmp + 5), a             ; the colour in the high nibble
        ld b, STAR_N
sd_star:
        ld a, (tmp + 3)
        ld c, a
        ld a, (ix + 0)
        sub c
        ld e, a                     ; x
        ld a, (tmp)
        or a
        jr z, sd_in
        ld a, e
        ld hl, tmp
        cp (hl)
        jr nc, sd_skip
sd_in:  ld a, (ix + 1)
        add a, STAR_Y
        ld d, a                     ; y
        srl a
        ld h, a
        ld a, (tmp + 1)
        or h
        ld h, a                     ; A15..A8 = page, y >> 1
        ld a, d
        rrca
        and 0x80
        ld l, a
        ld a, e
        srl a
        or l
        ld l, a                     ; (y & 1) << 7 | x >> 1
        ld a, (tmp + 5)
        bit 0, e
        jr z, sd_even
        ld a, (tmp + 4)
sd_even:
        ld c, a
        di
        ld a, h
        rlca
        rlca
        and 3
        out (VDP_CTRL), a
        ld a, 0x8E
        out (VDP_CTRL), a
        ld a, l
        out (VDP_CTRL), a
        ld a, h
        and 0x3F
        or 0x40
        out (VDP_CTRL), a
        ld a, c
        out (VDP_DATA), a
        call eiop
sd_skip:
        inc ix
        inc ix
        djnz sd_star
        inc iy
        inc iy
        ld hl, tmp + 2
        inc (hl)
        ld a, (hl)
        cp 3
        jp c, sd_layer
        ret

star_col:
        db 3, 5, 6

; ---------------------------------------------------------------- scroll
; speeds move 1/16 px per tick towards their targets; the layers move by
; them (the background from phase 1 on, the foreground from phase 2 on)
scroll_update:
        ld hl, v_bg
        ld de, t_bg
        ld b, 3
su_1:   ld a, (de)
        cp (hl)
        jr z, su_2
        jr c, su_dn
        inc (hl)
        jr su_2
su_dn:  dec (hl)
su_2:   inc hl
        inc de
        djnz su_1
        ld a, (st_phase)
        or a
        jr z, su_st
        ; background: wraps at 512 px once in
        ld a, (v_bg)
        ld e, a
        ld d, 0
        ld hl, (s_bg)
        add hl, de
        bit 7, h
        jr nz, su_b1
        ld a, h
        cp 0x20                     ; 8192 = 512 px
        jr c, su_b1
        sub 0x20
        ld h, a
su_b1:  ld (s_bg), hl
        ld a, (st_phase)
        cp 2
        jr c, su_st
        ld a, (v_fg)
        ld e, a
        ld d, 0
        ld hl, (s_fg)
        add hl, de
        bit 7, h
        jr nz, su_f1
        ld a, h
        and 0x0F                    ; 4096 = 256 px
        ld h, a
su_f1:  ld (s_fg), hl
su_st:  ; stars: v/4, v/2, v
        ld a, (v_st)
        ld e, a
        ld d, 0
        srl e
        srl e
        ld hl, (s_st)
        add hl, de
        ld a, h
        and 0x0F
        ld h, a
        ld (s_st), hl
        ld a, (v_st)
        ld e, a
        srl e
        ld hl, (s_st + 2)
        add hl, de
        ld a, h
        and 0x0F
        ld h, a
        ld (s_st + 2), hl
        ld a, (v_st)
        ld e, a
        ld hl, (s_st + 4)
        add hl, de
        ld a, h
        and 0x0F
        ld h, a
        ld (s_st + 4), hl
        ret
