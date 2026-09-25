; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; geo3d_tex_demo.asm  -  the word GEO3D as solid blocks with textured caps,
;                         spinning, on the V9968 cartridge, MSX-DOS .COM
;
; Front and back caps are textured: geo3d sends one single-row LRMM per
; scanline and the V9968 copies the texels from VRAM page 2, where the Z80
; stored 7 pre-shaded copies of a 256 x 32 texture (flat shading picks the
; copy). Sides are solid shaded spans (LINE). The Z80 still sends only the
; matrix and RUN per frame.
;
; V9968 setup: PORT#4 bit7 = 0 unlocks R#20/R#21, R#21 = 0 selects V9968
; mode (extended commands such as LRMM, 256 KB addressing), R#20 = 1 turns
; on high-speed commands.
;
; The Z80 does no arithmetic: the model is uploaded once, then every frame
;   1. waits for the VDP command engine (CE = 0)
;   2. clears the back page with HMMV
;   3. points geo3d at the back page (YPAGE)
;   4. sends one precomputed matrix (24 bytes, OTIR) and RUN; the table
;      holds NFRAMES matrices of one full turn, so the spin loops forever
;   5. waits for geo3d to finish, then flips the displayed page (R#2)
;
; Assemble: z80asm -o GEO3DT.COM geo3d_tex_demo.asm
; Ports assume the cartridge DIP switch at 88h. For 98h, add 0x10 to all.
; Press any key to quit.
; ============================================================================

VDP_DATA:   equ 0x88        ; VRAM data
VDP_CTRL:   equ 0x89        ; register / status
VDP_PAL:    equ 0x8A        ; palette
VDP_IND:    equ 0x8B        ; indirect register access (R#17)
GEO_IDX:    equ 0x8D        ; geo3d index write / status read
GEO_DAT:    equ 0x8F        ; geo3d data
VDP_PORT4:  equ 0x8C        ; V9968 PORT#4 (extended-register lock in bit7)

BDOS:       equ 0x0005

        org 0x0100

start:
        di
        call screen5
        call v9968_mode
        call upload_texture
        call upload_model
        xor a
        ld (page), a
        ld (frame), a

main_loop:
        call wait_ce
        ; ---- clear back page (HMMV) -----------------------------------
        ld a, (page)
        xor 1
        ld (back), a
        ld (hmmv_dy+1), a           ; DY high byte = page
        ld hl, hmmv_cmd
        call vdp_cmd11
        call wait_ce

        ; ---- YPAGE = back * 256 ------------------------------------------
        ld a, 0x46
        out (GEO_IDX), a
        xor a
        out (GEO_DAT), a            ; YPAGE low
        ld a, (back)
        out (GEO_DAT), a            ; YPAGE high

        ; ---- matrix for this frame + RUN -----------------------------------
        ld a, (frame)
        ld l, a
        ld h, 0
        add hl, hl                  ; *2
        add hl, hl                  ; *4
        add hl, hl                  ; *8
        ld d, h
        ld e, l
        add hl, hl                  ; *16
        add hl, de                  ; *24
        ld de, matrix_table
        add hl, de
        xor a
        out (GEO_IDX), a            ; index 0x00 = M00
        ld bc, 24 * 256 + GEO_DAT   ; B = 24 bytes, C = port
        otir
        ld a, 0x48
        out (GEO_IDX), a
        ld a, 7
        out (GEO_DAT), a            ; RUN, filled faces, textures

geo_wait:
        in a, (GEO_IDX)
        rrca
        jr c, geo_wait              ; bit0 = RUN busy

        ; ---- show the page we just drew (R#2) ----------------------------------
        call wait_vblank
        ld a, (back)
        ld (page), a
        rrca
        rrca
        rrca                        ; page * 32
        or 0x1F
        ld b, 2
        call vdp_wreg

        ld a, (frame)
        inc a
        and NFRAMES - 1
        ld (frame), a

        ; ---- key pressed? -----------------------------------------------------
        ei
        ld c, 0x0B                  ; console status
        call BDOS
        di
        or a
        jp z, main_loop
        ld c, 0x08                  ; consume the key
        call BDOS
        ret

; ----------------------------------------------------------------------------
; upload_model: static configuration, vertices and edges (once)
; ----------------------------------------------------------------------------
upload_model:
        ld a, 0x18                  ; F, CX, CY, ZNEAR, W, H
        out (GEO_IDX), a
        ld hl, cfg_words
        ld bc, 12 * 256 + GEO_DAT
        otir
        ld a, 0x40                  ; VADDR, EADDR, NVERT, NEDGE, COLOR, LOP
        out (GEO_IDX), a
        ld hl, model_regs
        ld bc, 6 * 256 + GEO_DAT
        otir
        ld a, 0x58                  ; FADDR, NFACE, LX, LY, LZ
        out (GEO_IDX), a
        ld hl, face_regs
        ld bc, 2 * 256 + GEO_DAT
        otir
        ld hl, light_words
        ld b, 6
        otir
        ld a, 0x50                  ; vertex stream
        out (GEO_IDX), a
        ld hl, model_vertices
        ld de, NVERT * 6
        call send_block
        ld a, 0x52                  ; face stream
        out (GEO_IDX), a
        ld hl, model_faces
        ld de, NFACE * 11
        call send_block
        ld a, 0x60                  ; TEXX, TEXY, TSTRIDE, TADDR
        out (GEO_IDX), a
        ld hl, tex_regs
        ld bc, 6 * 256 + GEO_DAT
        otir
        ld a, 0x53                  ; texture-coordinate stream
        out (GEO_IDX), a
        ld hl, model_uv
        ld de, NFACE * 8
        call send_block
        ret

; ----------------------------------------------------------------------------
; V9968 mode and texture upload
; ----------------------------------------------------------------------------
v9968_mode:
        xor a
        out (VDP_PORT4), a          ; bit7 = 0: unlock R#20 / R#21
        ld b, 21
        call vdp_wreg               ; R#21 = 0: V9968 mode (LRMM, 256 KB)
        ld a, 1
        ld b, 20
        call vdp_wreg               ; R#20 = 1: high-speed commands
        ret

upload_texture:                     ; texture copies -> VRAM 0x10000 (y = 512)
        ld a, TEXY * 128 / 16384
        ld b, 14
        call vdp_wreg               ; R#14 = A17..A14
        xor a
        out (VDP_CTRL), a           ; A7..A0
        ld a, 0x40 + ((TEXY * 128 / 256) & 0x3F)
        out (VDP_CTRL), a           ; A13..A8, write mode
        ld hl, texture
        ld de, TEXBYTES
        ld c, VDP_DATA
        jr sb_loop

send_block:                         ; HL = data, DE = byte count (any size)
        ld c, GEO_DAT
sb_loop:
        ld a, d
        or e
        ret z
        outi
        dec de
        jr sb_loop

; ----------------------------------------------------------------------------
; VDP helpers
; ----------------------------------------------------------------------------
vdp_wreg:                           ; A = value, B = register
        out (VDP_CTRL), a
        ld a, b
        or 0x80
        out (VDP_CTRL), a
        ret

vdp_cmd11:                          ; HL = 11 bytes for R#36..R#46
        ld a, 36
        ld b, 17
        call vdp_wreg               ; R#17 = 36, auto-increment
        ld bc, 11 * 256 + VDP_IND
        otir
        ret

read_s2:                            ; A = S#2
        ld a, 2
        ld b, 15
        call vdp_wreg
        in a, (VDP_CTRL)
        push af
        xor a
        ld b, 15
        call vdp_wreg
        pop af
        ret

wait_ce:
        call read_s2
        rrca
        jr c, wait_ce               ; bit0 = CE
        ret

wait_vblank:
        call read_s2
        and 0x40
        jr nz, wait_vblank          ; leave the current blank first
wv1:    call read_s2
        and 0x40
        jr z, wv1                   ; then wait for the next one
        ret

screen5:                            ; GRAPHIC4 256x212, sprites off, page 0
        ld hl, s5_regs
s5_loop:
        ld a, (hl)
        cp 0xFF
        jr z, s5_pal
        ld b, a
        inc hl
        ld a, (hl)
        inc hl
        call vdp_wreg
        jr s5_loop
s5_pal:
        xor a
        ld b, 16
        call vdp_wreg               ; palette pointer = 0
        ld hl, palette
        ld bc, 32 * 256 + VDP_PAL
        otir
        ret

; ----------------------------------------------------------------------------
; data
; ----------------------------------------------------------------------------
s5_regs:
        db 0, 0x06                  ; M4=1, M3=1: GRAPHIC4
        db 1, 0x40                  ; display on
        db 2, 0x1F                  ; page 0
        db 7, 0x00                  ; border colour
        db 8, 0x0A                  ; 64K VRAM, sprites off
        db 9, 0x80                  ; 212 lines
        db 0xFF


cfg_words:
        dw 256, 128, 106, 16, 256, 212   ; F, CX, CY, ZNEAR, W, H

model_regs:
        db 0, 0, NVERT, 0, 0x0F, 0       ; VADDR, EADDR, NVERT, NEDGE, COLOR, LOP=IMP
face_regs:
        db 0, NFACE                      ; FADDR, NFACE
tex_regs:
        db 0, 0, TEXY & 255, TEXY / 256, TSTRIDE, 0   ; TEXX, TEXY, TSTRIDE, TADDR

hmmv_cmd:
        dw 0                        ; DX
hmmv_dy:
        dw 0                        ; DY (high byte patched with the page)
        dw 256, 212                 ; NX, NY
        db 0x00, 0x00, 0xC0         ; CLR, ARG, HMMV

page:   db 0
back:   db 0
frame:  db 0

        include "geo_tex_tables.asm"
