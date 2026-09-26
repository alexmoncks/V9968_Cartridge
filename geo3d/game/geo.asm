; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; geo.asm - geo3d: setup, the draw list, drawing the objects
;
; geo3d (rtl/geo3d_engine.v) projects its vertex RAM with the matrix and
; translation written at index 00h, culls back faces, shades each face
; BASE + light level, sorts them and fills them with LINE commands through
; the V9968 command engine, at Y + YPAGE. Setup once: the whole vertex pool
; (models.asm pool_v, 155 vertices), the camera (F 256, CX 128, CY 106,
; ZNEAR 16, W 256, H), the light. Per object: the model's faces if another
; model's are loaded (FADDR 0, NFACE, the _pf stream: 11 bytes a face) and
; NVERT = its pool base + its vertex count; then the 9 matrix words (from
; attitudes.asm: the Z80 computes no rotation) and TX, TY, TZ, then RUN.
; Screen (x, y) -> TX = (x - 128) * 4, TY = (106 - y) * 4, TZ = 1024: 4
; model units per pixel (the title ship: TZ 512, x 2).
; Nothing may write geo3d or R#32-R#46 while RUN is busy; the next
; object's block is prepared in RAM meanwhile.
;
; Draw order (painter's, by model; fixed so overlaps never flicker):
; draw_order below. The draw list keeps a chain per model.
; ============================================================================

geo_init:
        in a, (GEO_IDX)
        cp 0xFF
        ret z                       ; no geo3d (st_geo stays 0)
        ld a, 1
        ld (st_geo), a
        ld a, 0x40
        out (GEO_IDX), a
        ld hl, geo_init40
        ld bc, 8 * 256 + GEO_DAT
        otir                        ; VADDR, EADDR, NVERT, NEDGE, COLOR, LOP, YPAGE
        ld a, 0x50
        out (GEO_IDX), a
        ld hl, pool_v
        ld de, POOL_NV * 6
        ld c, GEO_DAT
gi_1:   outi
        dec de
        ld a, d
        or e
        jr nz, gi_1
        ld a, 0x18
        out (GEO_IDX), a
        ld hl, geo_cam
        ld bc, 12 * 256 + GEO_DAT
        otir
        ld a, 0x58
        out (GEO_IDX), a
        xor a
        out (GEO_DAT), a            ; FADDR
        out (GEO_DAT), a            ; NFACE
        ld hl, geo_light
        ld bc, 6 * 256 + GEO_DAT
        otir                        ; LX, LY, LZ
        ld a, 0xFF
        ld (cur_model), a
        ret

geo_init40:
        db 0, 0, POOL_NV, 0, 15, 0, 0, 0
geo_cam:
        dw G3_F, G3_CX, G3_CY, G3_ZNEAR, G3_W, 212
geo_light:
        dw G3_LX, G3_LY, G3_LZ

; wait for RUN busy = 0 (a stuck geo3d is switched off after ~0.5 s)
geo_wait:
        ld a, (st_geo)
        or a
        ret z
        push bc
        ld bc, 0
gw_1:   in a, (GEO_IDX)
        rrca
        jr nc, gw_x
        dec bc
        ld a, b
        or c
        jr nz, gw_1
        xor a
        ld (st_geo), a
gw_x:   pop bc
        ret

; ---------------------------------------------------------------- list
dl_reset:
        ld hl, dl_buf
        ld (dl_wp), hl
        xor a
        ld (dl_n), a
        ld (dl_big), a
        ld hl, dl_head
        ld b, 24
        ld a, 0xFF
dr_1:   ld (hl), a
        inc hl
        djnz dr_1
        ret

; A = model, HL -> matrix (18 bytes), DE = x, BC = y (pixels, signed):
; add an object, unless nothing of it can show: x + r < 0, x - r > 255,
; y + r < 0 or y - r > 175 (r its radius, mdl_rad). A centre on the screen
; (x 0..255, y 0..175, the common case) needs no radius test.
dl_add:
        ld (tmp + 15), a
        ld a, d
        or b
        jr nz, da_edge
        ld a, c
        cp 176
        jr c, da_in
da_edge:
        ld a, (tmp + 15)
        push hl                     ; the matrix
        ld l, a
        ld h, 0
        push de
        ld de, mdl_rad
        add hl, de
        pop de
        ld a, (hl)
        ld (tmp + 14), a            ; r
        ld l, a
        ld h, 0
        add hl, de                  ; x + r
        bit 7, h
        jp nz, da_out
        ld a, (tmp + 14)
        add a, a
        push de
        ld e, a
        ld d, 0
        or a
        sbc hl, de                  ; x - r
        pop de
        bit 7, h
        jr nz, da_y
        ld a, h
        or a
        jp nz, da_out               ; x - r > 255
da_y:   ld a, (tmp + 14)
        ld l, a
        ld h, 0
        add hl, bc                  ; y + r
        bit 7, h
        jp nz, da_out
        ld a, (tmp + 14)
        add a, a
        push de
        ld e, a
        ld d, 0
        or a
        sbc hl, de                  ; y - r
        pop de
        bit 7, h
        jr nz, da_ok
        ld a, h
        or a
        jp nz, da_out
        ld a, l
        cp 176
        jp nc, da_out               ; y - r > 175
da_ok:  pop hl
da_in:  ld a, (dl_n)
        cp DL_MAX
        ret nc
        push de
        ex de, hl                   ; DE -> the matrix
        ld hl, (dl_wp)              ; the entry
        ld (hl), 0xFF               ; next
        inc hl
        ld a, (tmp + 15)
        ld (hl), a                  ; model
        inc hl
        ld (hl), e
        inc hl
        ld (hl), d                  ; matrix
        inc hl
        pop de
        ld (hl), e
        inc hl
        ld (hl), d                  ; x
        inc hl
        ld (hl), c
        inc hl
        ld (hl), b                  ; y
        inc hl
        ld (dl_wp), hl
        ; link it at the end of its model's chain
        ld e, a
        ld d, 0
        ld hl, dl_tail
        add hl, de
        ld b, (hl)                  ; the old tail
        ld a, (dl_n)
        ld c, a
        inc a
        ld (dl_n), a
        ld (hl), c                  ; tail = n
        ld a, b
        cp 0xFF
        jr nz, da_link
        ld hl, dl_head
        add hl, de
        ld (hl), c                  ; head = n
        ret
da_link:
        ld l, b
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        ld de, dl_buf
        add hl, de
        ld (hl), c                  ; the old tail's next = n
        ret
da_out: pop hl
        ret
; The ship is drawn last (on top). Its faces are then the ones geo3d holds
; when the next frame starts, so if the ship overlaps nothing in that frame
; it is drawn first instead (draw_order_pf): the order changes only for
; objects that do not overlap it, and its 28 faces (308 bytes, 1.5 ms of
; Z80 time) need no upload in every other frame.
draw_order:
        db 0, 3, 2, 1, 4, 5, 6, 7, 8, 9, 10, 11, 0xFF
draw_order_pf:
        db 11, 0, 3, 2, 1, 4, 5, 6, 7, 8, 9, 10, 0xFF
; per model: its radius in pixels (models.asm MDL_*_R; the title ship is
; drawn twice as big)
mdl_rad:
        db 50, 23, 16, 13, 34, 6, 6, 6, 6, 6, 6, 25
; per model: its radius in pixels + the ship's (25), for the overlap test
; (0 title ship, 1 dart, 2 saucer, 3 rock, 4 gunship, 5-10 debris)
ovl_r:
        db 25 + 50, 25 + 23, 25 + 16, 25 + 13, 25 + 34
        db 25 + 6, 25 + 6, 25 + 6, 25 + 6, 25 + 6, 25 + 6

; Z if the ship (model 11) overlaps no other object of the list
ship_clear:
        ld a, (dl_head + 11)
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        ld de, dl_buf + 4
        add hl, de
        ld e, (hl)
        inc hl
        ld d, (hl)                  ; ship x
        inc hl
        ld c, (hl)
        inc hl
        ld b, (hl)                  ; ship y
        ld (tmp + 12), de
        ld (tmp + 14), bc
        ld a, (dl_n)
        or a
        ret z
        ld b, a
        ld ix, dl_buf
sc_1:   ld a, (ix + 1)
        cp 11
        jr z, sc_n
        ld e, a
        ld d, 0
        ld hl, ovl_r
        add hl, de
        ld c, (hl)                  ; the radii
        ; |x - ship x| < r ?
        ld l, (ix + 4)
        ld h, (ix + 5)
        ld de, (tmp + 12)
        or a
        sbc hl, de
        call abs_hl
        ld a, h
        or a
        jr nz, sc_n
        ld a, l
        cp c
        jr nc, sc_n
        ; |y - ship y| < r ?
        ld l, (ix + 6)
        ld h, (ix + 7)
        ld de, (tmp + 14)
        or a
        sbc hl, de
        call abs_hl
        ld a, h
        or a
        jr nz, sc_n
        ld a, l
        cp c
        jr nc, sc_n
        or 1                        ; they overlap
        ret
sc_n:   ld de, DL_SZ
        add ix, de
        djnz sc_1
        xor a
        ret

; HL = |HL|
abs_hl: bit 7, h
        ret z
        xor a
        sub l
        ld l, a
        sbc a, a
        sub h
        ld h, a
        ret

; Drawing the list: geo_start sets the page and H; geo_pump sends the next
; object whenever geo3d is idle (the sprite work of the frame calls it
; between its steps, so the Z80 works while geo3d draws); geo_finish sends
; the rest and waits for the last RUN (and its last LINE) to end.
geo_start:
        xor a
        ld (nobj), a
        ld hl, 0
        ld (fbytes), hl
        ld hl, draw_order
        ld a, (cur_model)
        cp 11
        jr nz, gst_1
        ld a, (dl_head + 11)
        cp 0xFF
        jr z, gst_1
        push hl
        call ship_clear
        pop hl
        jr nz, gst_1
        ld hl, draw_order_pf
gst_1:  ld (g_ord), hl
        ld a, 0xFF
        ld (g_ent), a
        ld a, (st_geo)
        xor 1
        ld (g_done), a              ; no geo3d: nothing to draw
        ret nz
        call geo_wait
        ld a, 0x46
        out (GEO_IDX), a
        xor a
        out (GEO_DAT), a
        ld a, (buf)
        out (GEO_DAT), a            ; YPAGE = 256 * buf
        ld a, 0x22
        out (GEO_IDX), a
        ld hl, (g3_h)
        ld a, l
        out (GEO_DAT), a
        ld a, h
        out (GEO_DAT), a            ; H
        ret

; send the next object if geo3d is idle (keeps every register but AF)
geo_pump:
        ld a, (g_done)
        or a
        ret nz
        in a, (GEO_IDX)
        rrca
        ret c                       ; RUN busy
        push bc
        push de
        push hl
        push ix
        call geo_next
        pop ix
        pop hl
        pop de
        pop bc
        ret

geo_finish:
        ld a, (g_done)
        or a
        jr nz, gf_x
        call geo_wait
        call geo_next
        jr geo_finish
gf_x:   call geo_wait
        ld a, (nobj)
        ld (st_objs), a
        ld hl, (fbytes)
        ld (st_fbytes), hl
        ret

; the next object of the list: faces if its model is not loaded, matrix,
; translation, RUN (geo3d is idle)
geo_next:
        ld a, (g_ent)
        cp 0xFF
        jr nz, gn_have
        ld hl, (g_ord)              ; the next model of draw_order
        ld a, (hl)
        cp 0xFF
        jr z, gn_done
        inc hl
        ld (g_ord), hl
        ld e, a
        ld d, 0
        ld hl, dl_head
        add hl, de
        ld a, (hl)
        ld (g_ent), a
        jr geo_next
gn_have:
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        ld de, dl_buf
        add hl, de
        push hl
        pop ix
        ld a, (ix + 0)
        ld (g_ent), a               ; the next one of this model
        call geo_prep
        ld a, (ix + 1)
        ld hl, cur_model
        cp (hl)
        call nz, geo_faces
        xor a
        out (GEO_IDX), a
        ld l, (ix + 2)
        ld h, (ix + 3)
        ld c, GEO_DAT
        outi                        ; the matrix, from ROM (18 bytes)
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        ld hl, cfgbuf + 18
        outi                        ; TX, TY, TZ
        outi
        outi
        outi
        outi
        outi
        ld a, 0x48
        out (GEO_IDX), a
        ld a, 0x03
        out (GEO_DAT), a            ; RUN, filled faces
        ld hl, nobj
        inc (hl)
        ret
gn_done:
        ld a, 1
        ld (g_done), a
        ret

; IX -> entry: cfgbuf + 18 = TX, TY, TZ
geo_prep:
        ld l, (ix + 4)
        ld h, (ix + 5)
        ld bc, -128
        add hl, bc
        add hl, hl
        ld a, (dl_big)
        or a
        jr nz, gp_1
        add hl, hl
gp_1:   ld (cfgbuf + 18), hl
        ld c, (ix + 6)
        ld b, (ix + 7)
        ld hl, 106
        or a
        sbc hl, bc
        add hl, hl
        ld a, (dl_big)
        or a
        jr nz, gp_2
        add hl, hl
gp_2:   ld (cfgbuf + 20), hl
        ld hl, 1024
        ld a, (dl_big)
        or a
        jr z, gp_3
        ld hl, 512
gp_3:   ld (cfgbuf + 22), hl
        ret

; A = model: its faces into geo3d's face RAM, NVERT for it
geo_faces:
        ld (cur_model), a
        ld l, a
        ld h, 0
        ld e, l
        ld d, h
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, de                  ; 9 a
        ld de, mdl_index
        add hl, de
        ld a, (hl)                  ; NV
        inc hl
        ld b, (hl)                  ; NF
        inc hl
        add a, (hl)                 ; + PB
        ld c, a
        ld a, 0x42
        out (GEO_IDX), a
        ld a, c
        out (GEO_DAT), a            ; NVERT
        ld a, 0x58
        out (GEO_IDX), a
        xor a
        out (GEO_DAT), a            ; FADDR 0
        ld a, b
        out (GEO_DAT), a            ; NFACE
        ld de, 5
        add hl, de                  ; -> _pf
        ld e, (hl)
        inc hl
        ld d, (hl)
        ex de, hl
        ld a, 0x52
        out (GEO_IDX), a
        push bc
        ld d, b
        ld c, GEO_DAT
gf_1:   outi
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        outi
        dec d
        jr nz, gf_1
        pop bc
        ; bytes uploaded (statistics)
        ld l, b
        ld h, 0
        ld e, l
        ld d, h
        add hl, hl
        add hl, hl
        add hl, de
        add hl, hl
        add hl, de                  ; 11 NF
        ld de, (fbytes)
        add hl, de
        ld (fbytes), hl
        ret

; A = attitude table (ATT_ id), E = entry -> HL -> its matrix (keeps BC)
att_mat:
        push bc
        ld l, a
        ld h, 0
        ld c, l
        ld b, h
        add hl, hl
        add hl, bc                  ; 3 a (att_index: dw table, db count)
        ld bc, att_index
        add hl, bc
        ld a, (hl)
        inc hl
        ld h, (hl)
        ld l, a
        push hl
        ld l, e
        ld h, 0
        add hl, hl                  ; 2 e
        ld c, l
        ld b, h
        add hl, hl
        add hl, hl
        add hl, hl                  ; 16 e
        add hl, bc                  ; 18 e
        pop bc
        add hl, bc
        pop bc
        ret

; A = attitude table -> A = its number of entries (keeps BC, DE)
att_count:
        push de
        ld l, a
        ld h, 0
        ld e, l
        ld d, h
        add hl, hl
        add hl, de
        ld de, att_index + 2
        add hl, de
        ld a, (hl)
        pop de
        ret
