; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; text.asm - bitmap text: the messages are drawn once at init into page 7
; (colour 0 = transparent, a navy shadow one pixel down-right), and every
; frame the ones on show are laid over the page with LMMM + TIMP after
; geo3d, so they sit on top of everything but the sprites.
;
; Glyphs: sprites.asm font8 (5 x 7 in an 8 x 8 cell, bits 6..2), 6 pixels
; a character at scale 1, 12 at scale 2 (the title).
; ============================================================================

; per message (sprites.asm MESSAGES order): scale, colour table (16 rows)
txt_style:
        db 2
        dw col_title                ; MSG_TITLE
        db 1
        dw col_white                ; MSG_PUSH
        db 1
        dw col_cyan                 ; MSG_DEMO
        db 1
        dw col_cyan                 ; MSG_READY
        db 1
        dw col_white                ; MSG_ROUND
        db 1
        dw col_warn                 ; MSG_WARN
        db 1
        dw col_orange               ; MSG_GAME
        db 1
        dw col_orange               ; MSG_OVER
        db 1
        dw col_cyan                 ; MSG_HI
        db 1
        dw col_white                ; MSG_1UP
        db 1
        dw col_white                ; MSG_DIGITS
        db 1
        dw col_grey                 ; MSG_CREDIT
TXT_N:      equ 12
MSG_TITLE:  equ 0
MSG_PUSH:   equ 1
MSG_DEMO:   equ 2
MSG_READY:  equ 3
MSG_ROUND:  equ 4
MSG_WARN:   equ 5
MSG_GAME:   equ 6
MSG_OVER:   equ 7
MSG_HI:     equ 8
MSG_1UP:    equ 9
MSG_DIGITS: equ 10
MSG_CREDIT: equ 11

col_title:
        db 14, 14, 14, 13, 13, 13, 12, 12, 12, 12, 11, 11, 10, 10, 9, 9
col_white:
        db 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6
col_cyan:
        db 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7
col_warn:
        db 15, 15, 14, 13, 12, 13, 14, 15, 15, 15, 15, 15, 15, 15, 15, 15
col_orange:
        db 13, 13, 12, 12, 12, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11
col_grey:
        db 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4

; draw every message into page 7, rows from Y 1792 on; txt_rect gets
; (SY, NX, NY) per message
text_init:
        ld hl, 1792
        ld (txt_y), hl
        xor a
ti_1:   push af
        call text_one
        pop af
        inc a
        cp TXT_N
        jr c, ti_1
        ret

; A = message
text_one:
        ld (tmp + 6), a
        ; style
        ld l, a
        ld h, 0
        ld e, l
        ld d, h
        add hl, hl
        add hl, de
        ld de, txt_style
        add hl, de
        ld a, (hl)
        ld (tmp + 7), a             ; scale
        inc hl
        ld e, (hl)
        inc hl
        ld d, (hl)
        ld (tmp + 8), de            ; colours
        ; the glyph codes
        ld a, (tmp + 6)
        add a, a
        ld e, a
        ld d, 0
        ld hl, msg_table
        add hl, de
        ld e, (hl)
        inc hl
        ld d, (hl)
        ld (tmp + 10), de
        ; clear the canvas
        ld hl, canvas
        ld de, canvas + 1
        ld bc, 128 * 18 - 1
        ld (hl), 0
        ldir
        ; shadow (colour 1, one pixel down-right), then the glyphs
        ld a, 1
        ld (tmp + 12), a
        call text_pass
        xor a
        ld (tmp + 12), a
        call text_pass              ; returns B = number of characters
        ; rectangle: NX = n * 6 * scale + 1, NY = 7 * scale + 1
        ld a, (tmp + 7)
        ld c, a
        ld a, b
        add a, a
        add a, b
        add a, a                    ; 6 n
        dec c
        jr z, to_1
        add a, a                    ; 12 n
to_1:   inc a
        ld (tmp + 13), a            ; NX
        ld a, (tmp + 7)
        ld b, a
        add a, a
        add a, a
        add a, a
        sub b                       ; 7 scale
        inc a
        ld (tmp + 14), a            ; NY
        ; txt_rect[m] = SY, NX, NY
        ld a, (tmp + 6)
        add a, a
        add a, a
        ld e, a
        ld d, 0
        ld hl, txt_rect
        add hl, de
        ld de, (txt_y)
        ld (hl), e
        inc hl
        ld (hl), d
        inc hl
        ld a, (tmp + 13)
        ld (hl), a
        inc hl
        ld a, (tmp + 14)
        ld (hl), a
        ; upload NY rows of the canvas (whole 128-byte lines) at Y txt_y
        ld hl, (txt_y)              ; address = y * 128: C = y >> 9, HL = y << 7
        ld a, h
        srl a
        ld c, a
        ld a, h
        rra
        ld a, l
        rra
        ld h, a
        ld a, 0
        rra
        ld l, a
        call vaddr_w
        ld a, (tmp + 14)
        ld b, a
        ld hl, canvas
to_2:   push bc
        ld bc, 128 * 256 + VDP_DATA
        otir
        pop bc
        djnz to_2
        ; next free line (+ 1 blank)
        ld a, (tmp + 14)
        inc a
        ld e, a
        ld d, 0
        ld hl, (txt_y)
        add hl, de
        ld (txt_y), hl
        ret

; one pass over the message's glyphs into the canvas: (tmp + 12) = 1:
; the shadow in colour 1 at +1, +1; 0: the glyphs in their colours.
; Returns B = number of characters.
text_pass:
        ld hl, (tmp + 10)
        ld b, 0
tp_c:   ld a, (hl)
        cp 0xFF
        ret z
        push hl
        push bc
        ; glyph address
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        ld de, font8
        add hl, de
        ex de, hl                   ; DE -> 8 glyph rows
        ; cell x = b * 6 * scale
        ld a, b
        add a, a
        add a, b
        add a, a
        ld c, a
        ld a, (tmp + 7)
        dec a
        ld a, c
        jr z, tp_0
        add a, a
tp_0:   ld (tmp + 0), a             ; x0
        xor a
        ld (tmp + 1), a             ; row
tp_r:   ld a, (de)
        rlca                        ; bit 6 (column 0) -> bit 7
        ld (tmp + 2), a
        ld c, 0                     ; column
tp_col: ld a, (tmp + 2)
        bit 7, a
        call nz, tp_plot
        ld a, (tmp + 2)
        add a, a
        ld (tmp + 2), a
        inc c
        ld a, c
        cp 5
        jr c, tp_col
        inc de
        ld hl, tmp + 1
        inc (hl)
        ld a, (hl)
        cp 7
        jr c, tp_r
        pop bc
        pop hl
        inc hl
        inc b
        jr tp_c

; plot the scaled pixel of column c, row (tmp + 1)
tp_plot:
        push bc
        push de
        ld a, (tmp + 7)
        ld b, a                     ; scale
        ; x = x0 + c * scale (+ scale if shadow), y = row * scale (+ ...)
        ld a, c
        dec b
        jr z, tpp_1
        add a, a
tpp_1:  ld hl, tmp + 0
        add a, (hl)
        ld e, a                     ; x
        ld a, (tmp + 1)
        inc b
        dec b
        jr z, tpp_2
        add a, a
tpp_2:  ld d, a                     ; y
        ld a, (tmp + 12)
        or a
        jr z, tpp_3
        inc e
        inc d
tpp_3:  ld a, (tmp + 7)
        ld c, a                     ; block size
tpp_y:  push de
        ld a, (tmp + 7)
        ld b, a
tpp_x:  call canvas_px
        inc e
        djnz tpp_x
        pop de
        inc d
        dec c
        jr nz, tpp_y
        pop de
        pop bc
        ret

; pixel (E, D) of the canvas: colour 1 for the shadow, else the row's colour
canvas_px:
        push bc
        push hl
        ld a, (tmp + 12)
        or a
        ld a, 1
        jr nz, cp_col
        ld hl, (tmp + 8)
        ld a, d
        cp 16
        jr c, cp_c1
        ld a, 15
cp_c1:  ld c, a
        ld b, 0
        add hl, bc
        ld a, (hl)
cp_col: ld c, a                     ; colour
        ld a, d
        cp 18
        jr nc, cp_x
        ; canvas + y * 128 + x / 2
        ld l, d
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        ld a, e
        srl a
        add a, l
        ld l, a
        ld a, h
        adc a, 0
        ld h, a
        ld a, l
        add a, canvas & 0xFF
        ld l, a
        ld a, h
        adc a, canvas >> 8
        ld h, a
        bit 0, e
        jr nz, cp_odd
        ld a, (hl)
        and 0x0F
        ld b, a
        ld a, c
        rlca
        rlca
        rlca
        rlca
        or b
        ld (hl), a
        jr cp_x
cp_odd: ld a, (hl)
        and 0xF0
        or c
        ld (hl), a
cp_x:   pop hl
        pop bc
        ret

; ---------------------------------------------------------------- overlays
; A = message, D = x, E = y, C = character (FFh: the whole message; else
; that one character of it, 6 pixels wide: the digits)
tx_add:
        push hl
        push af
        ld a, (tx_n)
        cp TX_MAX
        jr nc, ta_x
        ld l, a
        inc a
        ld (tx_n), a
        ld h, 0
        add hl, hl
        add hl, hl
        push de
        ld de, tx_buf
        add hl, de
        pop de
        pop af
        ld (hl), a
        inc hl
        ld (hl), d
        inc hl
        ld (hl), e
        inc hl
        ld (hl), c
        pop hl
        ret
ta_x:   pop af
        pop hl
        ret

; A = message, E = y: centred on the screen
tx_centre:
        push af
        push hl
        add a, a
        add a, a
        ld l, a
        ld h, 0
        push de
        ld de, txt_rect + 2
        add hl, de
        pop de
        ld a, (hl)                  ; NX
        srl a
        neg
        add a, 128
        ld d, a
        pop hl
        pop af
        ld c, 0xFF
        jp tx_add

; the overlays: LMMM + TIMP from page 7 to the page drawn
text_draw:
        ld a, (tx_n)
        or a
        ret z
        call q_reset
        ld a, (tx_n)
        ld b, a
        ld ix, tx_buf
td_1:   push bc
        ld a, (ix + 0)
        add a, a
        add a, a
        ld e, a
        ld d, 0
        ld hl, txt_rect
        add hl, de
        ld e, (hl)
        inc hl
        ld d, (hl)
        inc hl
        ld (cmdbuf + 2), de         ; SY
        ld a, (hl)                  ; NX
        inc hl
        ld c, (hl)                  ; NY
        ld b, a
        xor a
        ld (cmdbuf + 0), a
        ld (cmdbuf + 1), a
        ld a, (ix + 3)
        cp 0xFF
        jr z, td_2
        ld e, a                     ; one character: SX = 6 k, NX = 6 (its
        add a, a                    ; glyph and its shadow)
        add a, e
        add a, a
        ld (cmdbuf + 0), a
        ld b, 6
td_2:   ld a, b
        ld (cmdbuf + 8), a
        xor a
        ld (cmdbuf + 9), a
        ld a, c
        ld (cmdbuf + 10), a
        xor a
        ld (cmdbuf + 11), a
        ld a, (ix + 1)
        ld (cmdbuf + 4), a
        xor a
        ld (cmdbuf + 5), a
        ld a, (ix + 2)
        ld (cmdbuf + 6), a
        ld a, (buf)
        ld (cmdbuf + 7), a
        xor a
        ld (cmdbuf + 12), a
        ld (cmdbuf + 13), a
        ld a, LMMM + TIMP
        ld (cmdbuf + 14), a
        call q_add
        ld de, 4
        add ix, de
        pop bc
        djnz td_1
        ld a, 0x37
        ld (st_fmark), a
        call q_flush
        ld a, 0x38
        ld (st_fmark), a
        ret
