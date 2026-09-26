; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; vdp.asm - V9968 access: registers, status, VRAM address, the command
; queue, the init sequence, the tile upload and the collision probe.
;
; Interrupt rule (the ISR reads S#0 at every interrupt and writes R#2, R#5,
; R#15): every two-byte control-port sequence runs with interrupts off and
; R#15 is 0 whenever they are on. `eiop` (RAM) is EI; RET once the ISR is
; installed (NOP; RET during init), so these routines end with jp eiop.
; The data port and the indirect port (R#17 auto-increment) are safe with
; interrupts on: the ISR never touches the VRAM address or R#17.
; ============================================================================

; A = value, B = register
wreg:
        di
        out (VDP_CTRL), a
        ld a, b
        or 0x80
        out (VDP_CTRL), a
        jp eiop

; A = n -> A = S#n; R#15 back to 0
rd_status:
        di
        out (VDP_CTRL), a
        ld a, 0x8F
        out (VDP_CTRL), a
        in a, (VDP_CTRL)
        push af
        xor a
        out (VDP_CTRL), a
        ld a, 0x8F
        out (VDP_CTRL), a
        pop af
        jp eiop

; Z when the command engine is idle (S#2 bit 0 = CE)
ce_busy:
        ld a, 2
        call rd_status
        and 1
        ret

wait_ce:
        call ce_busy
        jr nz, wait_ce
        ret

; C:HL = 18-bit VRAM address (C = A17..A16), set for writing
vaddr_w:
        di
        ld a, c
        add a, a
        add a, a
        ld b, a
        ld a, h
        rlca
        rlca
        and 3
        or b
        out (VDP_CTRL), a           ; R#14 = A17..A14
        ld a, 0x8E
        out (VDP_CTRL), a
        ld a, l
        out (VDP_CTRL), a
        ld a, h
        and 0x3F
        or 0x40
        out (VDP_CTRL), a
        jp eiop

; HL -> 15 bytes: R#32..R#46 (SX, SY, DX, DY, NX, NY, CLR, ARG, CMD)
send_hl:
        di
        ld a, 32
        out (VDP_CTRL), a
        ld a, 0x80 + 17
        out (VDP_CTRL), a
        call eiop
        ld bc, 15 * 256 + VDP_IND
        otir
        ret

; run one command now (HL -> 15 bytes) and wait for its end
vdp_cmd:
        push hl
        call wait_ce
        pop hl
        call send_hl
        jp wait_ce

; ---------------------------------------------------------------- the queue
; The frame's layer commands are queued (q_add), the first one is sent at
; once, and q_pump, called from the game logic's loops, sends the next one
; whenever the engine is idle, so the Z80 works while the VDP copies.
q_reset:
        xor a
        ld (q_n), a
        ld (q_i), a
        ld hl, q_buf
        ld (q_wp), hl
        ld (q_rp), hl
        ret

; cmdbuf -> the queue
q_add:
        ld a, (q_n)
        cp Q_MAX
        ret nc
        inc a
        ld (q_n), a
        ld de, (q_wp)
        ld hl, cmdbuf
        ld bc, 15
        ldir
        ld (q_wp), de
        ret

; send the next queued command if the engine is idle (keeps BC, DE, HL,
; IX, IY)
q_pump:
        push hl
        ld a, (q_i)
        ld hl, q_n
        cp (hl)
        jr z, qp_x                  ; nothing left
        call ce_busy
        jr nz, qp_x
        push bc
        push de
        ld hl, q_i
        inc (hl)
        ld hl, (q_rp)
        ld de, 15
        add hl, de
        ld (q_rp), hl
        sbc hl, de                  ; (no carry from the add)
        call send_hl
        pop de
        pop bc
qp_x:   pop hl
        ret

; send all the queued commands and wait for the last one to end
q_flush:
        call q_pump
        ld a, (q_i)
        ld hl, q_n
        cp (hl)
        jr nz, q_flush
        jp wait_ce

; ---------------------------------------------------------------- init
vdp_init:
        xor a
        out (VDP_P4), a             ; unlock R#20 / R#21 (PORT#4 bit 7 = 0)
        ld hl, init_regs
vi_1:   ld a, (hl)
        cp 0xFF
        jr z, vi_2
        ld b, a
        inc hl
        ld a, (hl)
        inc hl
        call wreg
        jr vi_1
vi_2:   ld a, 51
        ld b, 17
        call wreg
        ld hl, window_regs
        ld bc, 8 * 256 + VDP_IND
        otir                        ; R#51..58: command window = all the VRAM
        xor a
        ld b, 16
        call wreg
        ld b, 32
        xor a
vi_3:   out (VDP_PAL), a            ; black palette while loading
        djnz vi_3
        ld hl, cmd_clr01
        call vdp_cmd                ; pages 0 and 1 (and the sprite tables)
        ld hl, cmd_clr7
        jp vdp_cmd                  ; page 7 (text), transparent

init_regs:
        db 0, 0x06                  ; GRAPHIC4 (SCREEN 5)
        db 1, 0x02                  ; display off while loading, 16x16 sprites
        db 2, 0x1F                  ; page 0
        db 5, 0xEF                  ; sprite attributes 7600h (colours 7400h)
        db 11, 0x00
        db 6, 0x0F                  ; sprite patterns 7800h
        db 7, 0x00                  ; border colour 0
        db 8, 0x08                  ; sprites on, TP = 0
        db 9, 0x80                  ; 212 lines
        db 10, 0x00
        db 15, 0x00
        db 16, 0x00
        db 23, 0x00
        db 25, 0x00                 ; no SP2, no MSK
        db 26, 0x00
        db 27, 0x00
        db 21, 0x00                 ; V9968 mode: LRMM, 256 KB
        db 20, 0x81                 ; high-speed commands, 16 sprites per line
        db 0xFF

window_regs:
        db 0, 0, 0, 0, 0xFF, 0x01, 0xFF, 0x07

cmd_clr01:
        dw 0, 0, 0, 0, 256, 512
        db 0x00, 0x00, HMMV
cmd_clr7:
        dw 0, 0, 0, 1792, 256, 256
        db 0x00, 0x00, HMMV

; ---------------------------------------------------------------- tiles
; The packed tiles (bank 2, the demo ROM's VRLE blocks) go to VRAM with
; page 2 switched to bank 2 for the time of the upload.
load_tiles:
        ld a, 2
        ld (BANK2_SEL), a
        ld hl, TILE_BG_L_0
        call vrle
        ld hl, TILE_BG_R_0
        call vrle
        ld hl, TILE_FG_0
        call vrle
        ld a, 1
        ld (BANK2_SEL), a
        ret

; HL -> db A7..A0, A15..A8, A17..A16 ; dw packed length ; VRLE bytes
; (c < 80h: c + 1 literal bytes follow; c >= 80h: one byte, c - 7Eh times)
vrle:
        ld e, (hl)
        inc hl
        ld d, (hl)
        inc hl
        ld c, (hl)
        inc hl
        push hl
        ex de, hl
        call vaddr_w
        pop hl
        ld e, (hl)
        inc hl
        ld d, (hl)
        inc hl
        ld c, VDP_DATA
vl_loop:
        ld a, d
        or e
        ret z
        ld a, (hl)
        inc hl
        dec de
        cp 0x80
        jr nc, vl_run
        inc a
        ld b, a
        ld a, e
        sub b
        ld e, a
        jr nc, vl_lit
        dec d
vl_lit: otir
        jr vl_loop
vl_run: sub 0x7E
        ld b, a
        ld a, (hl)
        inc hl
        dec de
vl_r1:  out (VDP_DATA), a
        djnz vl_r1
        jr vl_loop

; the background copy shifted by one pixel (HMMM moves 2 pixels at a time;
; odd scroll offsets read it): BG1[x] = BG[x + 1]
make_shifted:
        ld hl, shift_cmds
        ld b, 4
ms_1:   push bc
        push hl
        call vdp_cmd
        pop hl
        ld de, 15
        add hl, de
        pop bc
        djnz ms_1
        ret

shift_cmds:
        dw 1, 512, 0, 1024, 255, 212
        db 0, 0, LMMM
        dw 0, 768, 255, 1024, 1, 212
        db 0, 0, LMMM
        dw 1, 768, 0, 1280, 255, 212
        db 0, 0, LMMM
        dw 0, 512, 255, 1280, 1, 212
        db 0, 0, LMMM

; ---------------------------------------------------------------- probe
; Does the VDP report a colour-0 (invisible) sprite under a visible one?
; The V9968 FPGA does (vdp_sprite_makeup_pixel.v checks only the earlier
; sprite's colour); the openMSX fork follows the V99x8 rule (colour 0 never
; collides with TP = 0). Two pairs of 16x16 blocks, shown for a frame each
; with palette entry 15 black: control = colour 15 over colour 15, test =
; colour 15 over colour 0 (IC = 0). st_probe bit0 / bit1 = collided;
; st_hwcoll = 1 when both did. Interrupts are off, the display is on.
; Plane 0 (the visible one, first on its lines) has IC = 1: the FPGA may
; check the first sprite of a line against the pixel left of it (see
; sprites.py, the guard), and with IC = 0 plane 0 would then collide with
; itself and pass the test whatever the colour-0 rule. The earlier
; sprite's IC does not matter for the later one's collision (FPGA, and the
; test build of openMSX); stock openMSX needs IC = 0 on both, so there the
; control pair fails too (st_hwcoll stays 0, as it should).
coll_probe:
        ld a, 0x42                  ; display on, 16x16 sprites, no interrupt
        ld b, 1
        call wreg
        ld a, 0x0F
        call probe_pair
        jr z, cp_1
        ld hl, st_probe
        set 0, (hl)
cp_1:   xor a
        call probe_pair
        jr z, cp_2
        ld hl, st_probe
        set 1, (hl)
cp_2:   ld a, (st_probe)
        cp 3
        ld a, 0
        jr nz, cp_3
        inc a
cp_3:   ld (st_hwcoll), a
        ; hide the probe sprites again
        ld c, 0
        ld hl, 0x7600
        call vaddr_w
        ld b, 32
cp_4:   ld a, 212
        out (VDP_DATA), a
        xor a
        out (VDP_DATA), a
        out (VDP_DATA), a
        out (VDP_DATA), a
        djnz cp_4
        ld hl, spr_col_blank        ; plane 0 and 1 colours back (guard, bullet)
        ld de, 0x7400
        call col_rows
        ld hl, spr_col_e_bullet
        ld de, 0x7410
        jp col_rows

; A = colour of the second (later) sprite -> NZ if S#0 bit 5 was seen
probe_pair:
        push af
        ld c, 0
        ld hl, 0x7410               ; plane 1 colours
        call vaddr_w
        pop af
        ld b, 16
pp_1:   out (VDP_DATA), a
        djnz pp_1
        ld c, 0
        ld hl, 0x7400               ; plane 0: colour 15, IC = 1
        call vaddr_w
        ld a, 0x2F
        ld b, 16
pp_2:   out (VDP_DATA), a
        djnz pp_2
        ld c, 0
        ld hl, 0x7600
        call vaddr_w
        ld hl, probe_sat
        ld bc, 9 * 256 + VDP_DATA
        otir
        call poll_vblank
        call poll_vblank            ; the tables are in use
        ld a, 5
        call rd_status              ; clears S#3-S#6
        in a, (VDP_CTRL)            ; clears C
        xor a
        ld (tmp), a
        call poll_vblank            ; a whole frame with the sprites
        ld a, (tmp)
        or a
        ret

; wait for the next vertical blank (S#0 bit 7), OR-ing S#0 bit 5 into tmp
poll_vblank:
        in a, (VDP_CTRL)
        ld b, a
        and 0x20
        ld hl, tmp
        or (hl)
        ld (hl), a
        bit 7, b
        jr z, poll_vblank
        ret

probe_sat:
        db 99, 100, SP_H_DART, 0    ; (a 16x16 block)
        db 103, 104, SP_H_DART, 0
        db 216

; HL -> 16 colour bytes, DE = VRAM address (page 0 area)
col_rows:
        push hl
        ex de, hl
        ld c, 0
        call vaddr_w
        pop hl
        ld bc, 16 * 256 + VDP_DATA
        otir
        ret
