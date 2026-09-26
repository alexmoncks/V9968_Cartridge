; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; isr.asm - the interrupt handler (H.KEYI hook), sound effects, input
;
; H.KEYI is called at every interrupt, before the BIOS looks at the VDP;
; the BIOS has saved all the registers. The handler reads the V9968's S#0
; (R#15 is 0 whenever interrupts are on):
;   - bit 5 (C, sprite collision) or bit 6 (a line had more than 16
;     sprites, so a hitbox may not have been shown): the frame that just
;     ended showed the page of tick shown_bit; that bit goes into i_cmask
;     and collide (game.asm) tests that tick's positions. With bit 5 the
;     point is read too (S#3/S#4, S#5/S#6; S#5 last: it clears them), for
;     the statistics only. Only this read clears C and bit 6.
;   - bit 7 (F, vertical blank): count it; flip to the page the main loop
;     finished (R#2 and R#5 together) when it asked and at least 2 blanks
;     passed since the last flip (30 frames per second); step the sound
;     effects; read the keyboard and joystick 1.
; On 98h this read takes the blank from the BIOS (its handler then sees
; F = 0 and skips its keyboard scan), so the game reads the keys itself.
; On 88h the internal VDP's interrupt is turned off at init and the V9968
; drives /INT (R#1 IE0).
; ============================================================================

isr:
        if PORT_BASE == 0x88
        ; the internal VDP (99h) still raises S#0 F at its own blanks, even
        ; with its interrupt off, and the BIOS handler that called us looks
        ; at it next: clear it, or the BIOS scans the keyboard (~1.4 ms) at
        ; every V9968 interrupt
        in a, (0x99)
        endif
        in a, (VDP_CTRL)
        ld b, a
        and 0x60
        jr z, is_1
        ld a, (shown_bit)           ; (the flip below comes after this frame)
        ld hl, i_cmask
        or (hl)
        ld (hl), a
        bit 5, b
        jr z, is_1
        ld a, 3
        call is_stat
        ld l, a
        ld a, 4
        call is_stat
        and 1
        ld h, a
        ld de, -12
        add hl, de
        ld (i_cx), hl
        ld a, 6
        call is_stat
        and 3
        ld h, a
        ld a, 5
        call is_stat
        ld l, a
        ld de, -8
        add hl, de
        ld (i_cy), hl
        xor a
        out (VDP_CTRL), a
        ld a, 0x8F
        out (VDP_CTRL), a
is_1:   bit 7, b
        ret z
        ld hl, (vcount)
        inc hl
        ld (vcount), hl
        ld (st_vbl), hl
        ld a, (flip_req)
        or a
        jr z, is_nf
        ld de, (lastflip)
        or a
        sbc hl, de                  ; blanks since the last flip
        ld a, h
        or a
        jr nz, is_fl
        ld a, l
        cp 2
        jr c, is_nf
is_fl:  ld (st_blank), hl
        ld a, h
        or a
        jr nz, is_late
        ld a, l
        cp 3
        jr c, is_ok
is_late:
        ld hl, (st_late)
        inc hl
        ld (st_late), hl
is_ok:  ld a, (flip_r2)
        out (VDP_CTRL), a
        ld a, 0x82
        out (VDP_CTRL), a
        ld a, (flip_r5)
        out (VDP_CTRL), a
        ld a, 0x85
        out (VDP_CTRL), a
        ld a, (flip_bit)
        ld (shown_bit), a
        ld hl, (vcount)
        ld (lastflip), hl
        ld hl, (st_flips)
        inc hl
        ld (st_flips), hl
        xor a
        ld (flip_req), a
is_nf:  ld a, 0x50
        ld (st_imark), a
        call sfx_tick
        call input_read
        ld a, 0x51
        ld (st_imark), a
        ret

; A = n -> A = S#n (R#15 left at n; interrupts are off here)
is_stat:
        out (VDP_CTRL), a
        ld a, 0x8F
        out (VDP_CTRL), a
        in a, (VDP_CTRL)
        ret

; ---------------------------------------------------------------- input
; in_cur: bit0 up, 1 down, 2 left, 3 right, 4 fire (SPACE, trigger A),
; 5 ESC, 6 trigger B; in_edge collects the presses for the logic
input_read:
        in a, (PPI_C)
        and 0xF0
        or 8
        out (PPI_C), a
        in a, (PPI_B)               ; row 8: 0 SPACE 4 left 5 up 6 down 7 right
        cpl
        ld c, a
        ld b, 0
        bit 5, c
        jr z, ir_1
        set 0, b
ir_1:   bit 6, c
        jr z, ir_2
        set 1, b
ir_2:   bit 4, c
        jr z, ir_3
        set 2, b
ir_3:   bit 7, c
        jr z, ir_4
        set 3, b
ir_4:   bit 0, c
        jr z, ir_5
        set 4, b
ir_5:   in a, (PPI_C)
        and 0xF0
        or 7
        out (PPI_C), a
        in a, (PPI_B)               ; row 7: bit 2 ESC
        and 4
        jr nz, ir_6
        set 5, b
ir_6:   ld a, 15                    ; joystick port 1: PSG R#15 bit 6 = 0
        out (PSG_A), a
        in a, (PSG_R)
        and 0xBF
        out (PSG_W), a
        ld a, 14
        out (PSG_A), a
        in a, (PSG_R)               ; 0 up 1 down 2 left 3 right 4 A 5 B, low = on
        cpl
        and 0x3F
        ld c, a
        and 0x1F
        or b
        bit 5, c
        jr z, ir_7
        or 0x40
ir_7:   ld (in_cur), a
        ld b, a
        ld a, (in_prev)
        cpl
        and b
        ld hl, in_edge
        or (hl)
        ld (hl), a
        ld a, b
        ld (in_prev), a
        ret

; ---------------------------------------------------------------- sound
; sfx.asm format (tools/sfx.py): db channel, priority; frames of dw tone
; (FFFFh off), db noise (80h off), db volume; db FFh x 4 ends. One effect
; per channel; the logic asks with sfx_play, the handler starts it here and
; steps every playing effect once per blank. sfx_ch, 4 bytes per channel:
; next frame (0: idle), priority playing, request (effect id + 1, 0 none).
psg_init:
        ld a, 0xBF
        ld (psg_r7), a
        ld c, 0
pi_1:   ld a, c
        out (PSG_A), a
        cp 7
        ld a, 0xBF
        jr z, pi_2
        xor a
pi_2:   out (PSG_W), a
        inc c
        ld a, c
        cp 14
        jr c, pi_1
        ret

; A = effect id -> DE -> its data (keeps BC, HL)
sfx_addr:
        push hl
        add a, a
        ld e, a
        ld d, 0
        ld hl, sfx_table
        add hl, de
        ld e, (hl)
        inc hl
        ld d, (hl)
        pop hl
        ret

; A = effect id: ask for it (a pending request of higher priority stays)
sfx_play:
        push hl
        push de
        push bc
        push ix
        ld c, a
        call sfx_addr
        ld a, (de)                  ; channel
        add a, a
        add a, a
        ld l, a
        ld h, 0
        inc de
        ld a, (de)
        ld b, a                     ; priority
        push de
        ld de, sfx_ch
        add hl, de
        pop de
        push hl
        pop ix
        ld a, (ix + 3)
        or a
        jr z, sp_set
        dec a
        call sfx_addr
        inc de
        ld a, (de)                  ; the pending one's priority
        cp b
        jr z, sp_set
        jr nc, sp_x
sp_set: ld a, c
        inc a
        ld (ix + 3), a
sp_x:   pop ix
        pop bc
        pop de
        pop hl
        ret

sfx_tick:
        ld ix, sfx_ch
        ld c, 0
st_ch:  ld a, (ix + 3)
        or a
        jr z, st_play
        ld (ix + 3), 0
        dec a
        call sfx_addr
        inc de
        ld a, (de)                  ; priority
        inc de                      ; DE -> first frame
        ld b, a
        ld a, (ix + 0)
        or (ix + 1)
        jr z, st_start              ; the channel is idle
        ld a, (ix + 2)
        cp b
        jr z, st_start
        jr nc, st_play              ; the one playing is more important
st_start:
        ld (ix + 2), b
        ld (ix + 0), e
        ld (ix + 1), d
st_play:
        ld l, (ix + 0)
        ld h, (ix + 1)
        ld a, h
        or l
        jp z, st_next
        ld e, (hl)
        inc hl
        ld d, (hl)                  ; tone
        inc hl
        ld a, (hl)                  ; noise
        inc hl
        cp 0xFF
        jr z, st_end
        ld b, a
        ld a, (hl)                  ; volume
        inc hl
        ld (ix + 0), l
        ld (ix + 1), h
        push af
        ; tone: R#(2c), R#(2c+1), mixer bit c (0 = on)
        ld a, d
        and e
        inc a
        jr z, st_toff
        ld a, c
        add a, a
        out (PSG_A), a
        ld a, e
        out (PSG_W), a
        ld a, c
        add a, a
        inc a
        out (PSG_A), a
        ld a, d
        out (PSG_W), a
        call st_bit
        cpl
        ld hl, psg_r7
        and (hl)
        ld (hl), a
        jr st_n
st_toff:
        call st_bit
        ld hl, psg_r7
        or (hl)
        ld (hl), a
st_n:   ; noise: R#6, mixer bit c + 3
        ld a, b
        cp 0x80
        jr z, st_noff
        ld a, 6
        out (PSG_A), a
        ld a, b
        out (PSG_W), a
        call st_bit
        add a, a
        add a, a
        add a, a
        cpl
        ld hl, psg_r7
        and (hl)
        ld (hl), a
        jr st_mix
st_noff:
        call st_bit
        add a, a
        add a, a
        add a, a
        ld hl, psg_r7
        or (hl)
        ld (hl), a
st_mix: ld a, 7
        out (PSG_A), a
        ld a, (psg_r7)
        and 0x3F
        or 0x80
        out (PSG_W), a
        ld a, c
        add a, 8
        out (PSG_A), a
        pop af
        out (PSG_W), a              ; volume
        jr st_next
st_end: xor a
        ld (ix + 0), a
        ld (ix + 1), a
        ld (ix + 2), a
        ld a, c
        add a, 8
        out (PSG_A), a
        xor a
        out (PSG_W), a
st_next:
        ld de, 4
        add ix, de
        inc c
        ld a, c
        cp 3
        jp c, st_ch
        ret

; A = 1 << c
st_bit:
        push bc
        ld b, c
        inc b
        ld a, 0x80
st_b1:  rlca
        djnz st_b1
        pop bc
        ret
