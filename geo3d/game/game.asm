; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; game.asm - the game: title, attract demo, play, game over
;
; The rules are those of tools/level.py (its Game class is the reference
; model; this is not bit-exact with it): one tick per frame (30 per
; second), positions in 1/16 pixel, the level script (level.asm), enemy
; types and paths, the ship's controls, bolts, aimed enemy bullets, geo3d
; debris and sprite flashes for the explosions, BCD score, lives and extra
; lives. Attitudes are indices into attitudes.asm's matrices.
;
; Collisions (collide): a box test of every bolt against every enemy, of
; the enemy bullets against the ship's hitbox and of the ship's core
; against the enemies, on a snapshot of one tick. The boxes are the hitbox
; sprites' (etype_box, sprites.py). On a VDP that reports invisible
; hitboxes (st_hwcoll, the probe at init: the V9968) only the ticks whose
; page raised the sprite collision flag are tested (the ISR tells which);
; elsewhere (the openMSX fork today) every tick is.
; ============================================================================

; z80asm 1.8 notes: "ld a, R_x" assembles as "ld a, r" (the refresh
; register) and drops the rest, so level.asm's R_ rules are written
; "ld a, 0 + R_x" in that form (build.sh checks); and an equ that refers to
; a symbol defined further down gets 0, so there are no such equs here.

; ---------------------------------------------------------------- helpers
; A = type -> HL -> its etype_table entry (16 bytes); keeps BC, DE
etype_ptr:
        push de
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        ld de, etype_table
        add hl, de
        pop de
        ret

; A = path -> HL -> its path_table entry (8 bytes); keeps BC, DE
path_ptr:
        push de
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        ld de, path_table
        add hl, de
        pop de
        ret

; HL = A * E, unsigned (keeps BC; D = 0)
mul8u:
        ld h, a
        ld l, 0
        ld d, l
        add hl, hl
        jr nc, m8u_1
        add hl, de
m8u_1:  add hl, hl
        jr nc, m8u_2
        add hl, de
m8u_2:  add hl, hl
        jr nc, m8u_3
        add hl, de
m8u_3:  add hl, hl
        jr nc, m8u_4
        add hl, de
m8u_4:  add hl, hl
        jr nc, m8u_5
        add hl, de
m8u_5:  add hl, hl
        jr nc, m8u_6
        add hl, de
m8u_6:  add hl, hl
        jr nc, m8u_7
        add hl, de
m8u_7:  add hl, hl
        ret nc
        add hl, de
        ret

; HL = A * E, signed (keeps BC)
mul8s:
        ld d, a
        xor e
        push af                     ; bit 7: the sign of the product
        ld a, d
        bit 7, a
        jr z, m8s_1
        neg
m8s_1:  bit 7, e
        jr z, m8s_2
        push af
        ld a, e
        neg
        ld e, a
        pop af
m8s_2:  call mul8u
        pop af
        bit 7, a
        ret z
neg_hl: xor a
        sub l
        ld l, a
        sbc a, a
        sub h
        ld h, a
        ret

; HL = HL >> n (arithmetic), n = B (keeps DE)
sra_hl:
        sra h
        rr l
        djnz sra_hl
        ret

; HL = 18 * A (keeps BC, DE)
mul18:
        push de
        ld l, a
        ld h, 0
        add hl, hl
        ld d, h
        ld e, l
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, de
        pop de
        ret

; once at power on: tables of the game code
game_init:
        ; floor(v / 3), v = -128..127
        ld hl, div3_tab
        xor a
gi_d3:  push af
        call div3
        ld (hl), a
        inc hl
        pop af
        inc a
        jr nz, gi_d3
        ; sprite anchors (spr_geom: ax, ay, x0, y0, x1, y1 per pattern)
        ld hl, spr_geom
        ld de, anchors
        ld b, SPR_NPAT
gi_an:  ld a, (hl)
        ld (de), a
        inc hl
        inc de
        ld a, (hl)
        ld (de), a
        inc de
        push de
        ld de, 5
        add hl, de
        pop de
        djnz gi_an
        ret

; A = signed 8-bit -> HL sign extended
sext:
        ld l, a
        rlca
        sbc a, a
        ld h, a
        ret

; xorshift16 (7, 9, 8), level.py Rng: A = the new low byte
rand:
        push hl
        push bc
        ld hl, (st_seed)
        ld a, h
        rra
        ld a, l
        rra
        ld b, a                     ; (s << 7) high byte, carry = s bit 0
        ld a, 0
        rra                         ; (s << 7) low byte
        xor l
        ld l, a
        ld a, b
        xor h
        ld h, a                     ; s ^= s << 7
        ld a, h
        srl a
        xor l
        ld l, a                     ; s ^= s >> 9
        ld a, l
        xor h
        ld h, a                     ; s ^= s << 8
        ld (st_seed), hl
        ld a, l
        pop bc
        pop hl
        ret

; A = v (signed, 1/16 px per tick) -> HL = v * (8 + r) / 8, r = min(round, 8)
scale_v:
        push de
        push bc
        ld c, a
        ld a, (st_round)
        cp 8
        jr c, sv_1
        ld a, 8
sv_1:   add a, 8
        ld e, a
        ld a, c
        call mul8s
        ld b, 3
        call sra_hl
        pop bc
        pop de
        ret

; DE = points (BCD word): score += DE
score_add:
        ld hl, st_score
        ld a, (hl)
        add a, e
        daa
        ld (hl), a
        inc hl
        ld a, (hl)
        adc a, d
        daa
        ld (hl), a
        inc hl
        ld a, (hl)
        adc a, 0
        daa
        ld (hl), a
        jp nc, hud_refresh
        ld a, 0x99                  ; the score stops at 999999
        ld (st_score), a
        ld (st_score + 1), a
        ld (st_score + 2), a
        jp hud_refresh

; clear enemies, bolts, bullets, debris, flashes, pending spawns
clear_objects:
        ld hl, enem
        ld b, EN_MAX
co_1:   ld (hl), 0xFF
        ld de, EN_SZ
        add hl, de
        djnz co_1
        ld hl, bolts
        ld de, bolts + 1
        ld bc, BO_MAX * BO_SZ + BU_MAX * BU_SZ - 1
        ld (hl), 0
        ldir
        ld hl, debris
        ld b, DB_MAX
co_2:   ld (hl), 0xFF
        ld de, DB_SZ
        add hl, de
        djnz co_2
        ld hl, flashes
        ld de, flashes + 1
        ld bc, FL_MAX * FL_SZ - 1
        ld (hl), 0
        ldir
        ld hl, pend
        ld de, pend + 1
        ld bc, PD_MAX * PD_SZ - 1
        ld (hl), 0
        ldir
        xor a
        ld (pend_n), a
        ret

; A = the next shot serial, 1-255 (a collision snapshot names a bolt or a
; bullet by slot and serial)
new_serial:
        ld a, (obj_serial)
        inc a
        jr nz, ns_1
        inc a
ns_1:   ld (obj_serial), a
        ret

; ---------------------------------------------------------------- title
title_init:
        ld a, MODE_TITLE
        ld (st_mode), a
        ld hl, 0
        ld (gmode_t), hl
        call clear_objects
        xor a
        ld (st_enem), a
        ld (st_phase), a
        ld (t_bg), a
        ld (t_fg), a
        ld (v_bg), a
        ld (v_fg), a
        ld (pl_vuln), a
        ld (txt_t), a
        ld (in_edge), a
        ld (demo), a
        ld (demo_end), a
        ld a, 0 + R_STARS_V         ; 0.5, 1, 2 px per tick
        ld (t_st), a
        ld hl, -4096
        ld (s_bg), hl
        ld (s_fg), hl
        ld a, 1
        ld (hud_mode), a
        call sn_reset
        jp hud_refresh

title_tick:
        ld hl, (gmode_t)
        inc hl
        ld (gmode_t), hl
        ; start a game (fire), or the demo after TITLE_TICKS
        ld a, (in_edge)
        and 0x10
        jr z, tt_2
        xor a
        call game_start
        jp game_tick
tt_2:   xor a
        ld (in_edge), a
        ld de, R_TITLE_TICKS
        or a
        sbc hl, de
        jr c, tt_3
        ld a, 1
        call game_start
        jp game_tick
tt_3:   call scroll_update
        ; the ship, big, turning; a slow bob
        ld a, 1
        ld (dl_big), a
        ld a, (gmode_t)
        srl a
        and 31
        ld e, a
        ld a, ATT_TITLE
        call att_mat
        push hl
        ld a, (gmode_t)
        add a, a
        ld e, a
        ld d, 0
        ld hl, sin256
        add hl, de
        ld a, (hl)
        sra a
        sra a
        sra a
        sra a                       ; -8..7
        call sext
        ld bc, 100
        add hl, bc
        ld b, h
        ld c, l
        pop hl
        ld de, 128
        xor a
        call dl_add
        ; texts
        ld a, MSG_TITLE
        ld e, 26
        call tx_centre
        ld a, (gmode_t)
        and 16
        jr nz, tt_1
        ld a, MSG_PUSH
        ld e, 164
        call tx_centre
tt_1:   ld a, MSG_CREDIT
        ld e, 196
        call tx_centre
        xor a
        ld (pl_vuln), a
        ret

; ---------------------------------------------------------------- start
; A = 1: the attract demo (autopilot), 0: a game
game_start:
        ld (demo), a
        ld b, a
        xor a
        ld (god), a
        ld a, b
        or a
        ld a, MODE_GAME
        jr z, gs_1
        ld a, MODE_DEMO
gs_1:   ld (st_mode), a
        ld hl, R_DEMO_SEED
        ld (st_seed), hl
        ld a, 0 + R_LIVES
        ld (st_lives), a
        xor a
        ld (st_score), a
        ld (st_score + 1), a
        ld (st_score + 2), a
        ld (st_round), a
        ld (st_phase), a
        ld (next_extra), a
        ld (next_extra + 1), a
        ld (txt_t), a
        ld (in_edge), a
        ld (sc_wait), a
        ld (sc_inclear), a
        ld (t_bg), a
        ld (t_fg), a
        ld (v_bg), a
        ld (v_fg), a
        ld (pl_in), a
        ld (pl_dead), a
        ld (pl_invul), a
        ld (pl_fire), a
        ld (pl_vuln), a
        ld (pl_gone), a
        ld (demo_end), a
        ld (hud_mode), a
        ld a, 2
        ld (next_extra + 2), a      ; 020000
        ld a, 8
        ld (pl_att), a
        ld hl, -40 * 16
        ld (pl_x), hl
        ld hl, 96 * 16
        ld (pl_y), hl
        ld hl, -4096
        ld (s_bg), hl
        ld (s_fg), hl
        ld hl, level_script
        ld (sc_pc), hl
        ld hl, 0
        ld (gmode_t), hl
        call clear_objects
        call sn_reset
        call bvel_init
        jp hud_refresh

; ---------------------------------------------------------------- tick
game_tick:
        ld hl, (gmode_t)
        inc hl
        ld (gmode_t), hl
        ld a, (st_mode)
        cp MODE_DEMO
        jr nz, gt_1
        ; the demo ends at a key (fire starts a game, the others go back to
        ; the title), after DEMO_TICKS, or after the ship blew up
        ld a, (in_edge)
        and 0x10
        jr nz, gt_play
        ld a, (in_edge)
        and 0x2F
        jr nz, gt_title
        ld de, R_DEMO_TICKS
        or a
        sbc hl, de
        jr nc, gt_title
        ld a, (demo_end)
        or a
        jr z, gt_2
        dec a
        ld (demo_end), a
        jr z, gt_title
        jr gt_2
gt_1:   cp MODE_OVER
        jr nz, gt_1a
        ld de, R_GAMEOVER_TICKS
        or a
        sbc hl, de
        jr c, gt_2
        jr gt_title
gt_1a:  ld a, (in_edge)
        and 0x20                    ; ESC: back to the title
        jr z, gt_2
; (the layers of this frame were queued for the mode that ends here:
; compose_begin queues the new mode's, over them)
gt_title:
        call hi_update
        call title_init
        call compose_begin
        jp title_tick
gt_play:
        xor a
        call game_start
        call compose_begin
        jp game_tick
gt_2:   xor a
        ld (in_edge), a
        call debug_cmd
        ld a, (st_mode)
        cp MODE_OVER
        call nz, script_step        ; (no new wave or text under GAME OVER)
        ld a, 0x3B
        ld (st_fmark), a
        call scroll_update
        call pending_step
        ld a, 0x3C
        ld (st_fmark), a
        ; controls
        ld a, (st_mode)
        cp MODE_OVER
        ld a, 0
        jr z, gt_3
        ld a, (demo)
        or a
        ld a, (in_cur)
        jr z, gt_3
        call autopilot
gt_3:   ld (keys), a
        ld a, 0x40
        ld (st_fmark), a
        call q_pump
        call player_update
        call enemies_update
        ld a, 0x41
        ld (st_fmark), a
        call q_pump
        call bolts_update
        call bullets_update
        call q_pump
        call debris_update
        call flashes_update
        ld a, 0x42
        ld (st_fmark), a
        call q_pump
        call collide
        ld a, 0x43
        ld (st_fmark), a
        call q_pump
        call player_timers
        ld a, (txt_t)
        or a
        jr z, gt_4
        dec a
        ld (txt_t), a
gt_4:   call extra_check
        call build_draw
        ld a, 0x44
        ld (st_fmark), a
        call q_pump
        call build_text
        ld a, 0x45
        ld (st_fmark), a
        ld a, (st_hwcoll)
        or a
        ret z
        jp sn_save                  ; (the hardware path: this tick's snapshot)

hi_update:
        ld hl, st_score + 2
        ld de, st_hi + 2
        ld b, 3
hu_1:   ld a, (de)
        cp (hl)
        jr c, hu_new                ; hi < score
        ret nz                      ; hi > score
        dec hl
        dec de
        djnz hu_1
        ret
hu_new: ld hl, st_score
        ld de, st_hi
        ld bc, 3
        ldir
        ret

; ---------------------------------------------------------------- script
; level.py's bytecode (level.asm level_script)
script_step:
        ld a, (sc_wait)
        or a
        jr z, ss_run
        dec a
        ld (sc_wait), a
        ret
ss_run: ld hl, (sc_pc)
ss_op:  ld a, (hl)
        inc hl
        cp 1
        jr nz, ss_2
        ld a, (hl)                  ; WAIT n
        inc hl
        dec a
        ld (sc_wait), a
        ld (sc_pc), hl
        ret
ss_2:   cp 2
        jr nz, ss_3
        call op_spawn               ; SPAWN t p x y c g d
        jr ss_op
ss_3:   cp 3
        jr nz, ss_4
        ; CLEAR n: wait until no enemy is left, at most n * 4 ticks
        call any_enemy
        jr z, ss_3x
        ld a, (sc_inclear)
        or a
        jr nz, ss_3a
        inc a
        ld (sc_inclear), a
        ld a, (hl)
        ld e, a
        ld d, 0
        ex de, hl
        add hl, hl
        add hl, hl
        ld (sc_clr2), hl
        ex de, hl
ss_3a:  push hl
        ld hl, (sc_clr2)
        dec hl
        ld (sc_clr2), hl
        ld a, h
        or l
        pop hl
        jr z, ss_3x
        dec hl                      ; stay on the CLEAR
        ld (sc_pc), hl
        ret
ss_3x:  xor a
        ld (sc_inclear), a
        inc hl
        jr ss_op
ss_4:   cp 4
        jr nz, ss_5
        ld a, (hl)                  ; PHASE p
        inc hl
        ld (st_phase), a
        cp 1
        jr nz, ss_4a
        ld de, -4096
        ld (s_bg), de
ss_4a:  cp 2
        jr nz, ss_op
        ld de, -4096
        ld (s_fg), de
        jr ss_op
ss_5:   cp 5
        jr nz, ss_6
        ld de, t_bg                 ; SPEED b f s
        ld bc, 3
        ldir
        jr ss_op
ss_6:   cp 6
        jr nz, ss_7
        ld a, (hl)                  ; TEXT m n
        inc hl
        ld (txt_msg), a
        ld a, (hl)
        inc hl
        ld (txt_t), a
        jp ss_op
ss_7:   cp 7
        jr nz, ss_8
        dec hl                      ; LOOPSTART
        ld (sc_loop), hl
        inc hl
        jp ss_op
ss_8:   cp 8
        jr nz, ss_9
        ld a, (st_round)            ; LOOP
        inc a
        ld (st_round), a
        call bvel_init
        ld hl, (sc_loop)
        jp ss_op
ss_9:   cp 9
        jr nz, ss_10
        ld a, 0 + R_PLAYERIN_TICKS      ; PLAYERIN
        ld (pl_in), a
        ld de, -40 * 16
        ld (pl_x), de
        ld de, 96 * 16
        ld (pl_y), de
        jp ss_op
ss_10:  cp 10
        jr nz, ss_bad
        ld a, (hl)                  ; SFX s
        inc hl
        call sfx_play
        jp ss_op
ss_bad: ld (sc_pc), hl              ; unknown: stop the script
        ld a, 255
        ld (sc_wait), a
        ret

; NZ if an enemy is alive or a formation member is still to come
any_enemy:
        push hl
        ld hl, enem
        ld de, EN_SZ
        ld b, EN_MAX
ae_1:   ld a, (hl)
        cp 0xFF
        jr nz, ae_y
        add hl, de
        djnz ae_1
        pop hl
        ld a, (pend_n)              ; formation members still to come
        or a
        ret
ae_y:   pop hl
        or 1
        ret

; SPAWN t p x y c g d at HL -> pending members (delay k * g + 1)
op_spawn:
        push hl
        pop ix
        ld de, 7
        add hl, de
        push hl                     ; the next op
        ld c, 0                     ; member
osp_1:  ld a, c
        cp (ix + 4)
        jr nc, osp_x
        call pend_free              ; HL -> a free entry, or Z: none
        jr z, osp_x
        push hl
        ld a, c
        ld e, (ix + 5)
        call mul8u                  ; c * g
        ld a, l
        inc a
        pop hl
        ld (hl), a                  ; delay + 1
        ld a, (pend_n)
        inc a
        ld (pend_n), a
        inc hl
        ld a, (ix + 0)
        ld (hl), a                  ; type
        inc hl
        ld a, (ix + 1)
        ld (hl), a                  ; path
        inc hl
        ld a, (ix + 2)
        ld (hl), a                  ; x / 2
        inc hl
        push hl
        ld a, c
        ld e, (ix + 6)
        call mul8s                  ; c * d (d signed)
        ld a, (ix + 3)
        add a, l
        pop hl
        ld (hl), a                  ; y
        inc c
        jr osp_1
osp_x:  pop hl
        ret

; HL -> a free pending entry (NZ), or Z when full
pend_free:
        ld hl, pend
        ld de, PD_SZ
        ld b, PD_MAX
pf_1:   ld a, (hl)
        or a
        jr z, pf_y
        add hl, de
        djnz pf_1
        xor a
        ret
pf_y:   or 1
        ret

; the members whose delay runs out come in
pending_step:
        ld a, (pend_n)
        or a
        ret z                       ; none waiting
        ld ix, pend
        ld b, PD_MAX
ps_1:   ld a, (ix + 0)
        or a
        jr z, ps_n
        dec a
        ld (ix + 0), a
        jr nz, ps_n
        push bc
        ld hl, pend_n
        dec (hl)
        ld a, (ix + 1)
        ld (tmp + 0), a
        ld a, (ix + 2)
        ld (tmp + 1), a
        ld a, (ix + 3)
        ld (tmp + 2), a
        ld a, (ix + 4)
        ld (tmp + 3), a
        push ix
        call spawn_enemy
        pop ix
        pop bc
ps_n:   ld de, PD_SZ
        add ix, de
        djnz ps_1
        ret

; tmp: type, path, x / 2, y -> a new enemy (none if no slot is free)
spawn_enemy:
        ld iy, enem
        ld b, EN_MAX
se_1:   ld a, (iy + E_TYPE)
        cp 0xFF
        jr z, se_2
        ld de, EN_SZ
        add iy, de
        djnz se_1
        ret
se_2:   ; clear the slot
        push iy
        pop hl
        ld d, h
        ld e, l
        inc de
        ld (hl), 0
        ld bc, EN_SZ - 1
        ldir
        ld a, (en_gen)              ; a new generation (1-255) in this slot
        inc a
        jr nz, se_g
        inc a
se_g:   ld (en_gen), a
        ld (iy + E_GEN), a
        ld a, (tmp + 0)
        ld (iy + E_TYPE), a
        ld a, (tmp + 1)
        ld (iy + E_PATH), a
        ; x = (x / 2) * 32, y = y * 16
        ld a, (tmp + 2)
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        ld (iy + E_X), l
        ld (iy + E_X + 1), h
        ld a, (tmp + 3)
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        ld (iy + E_Y), l
        ld (iy + E_Y + 1), h
        ld (iy + E_Y0), l
        ld (iy + E_Y0 + 1), h
        ; speeds from the path, scaled by the round
        ld a, (tmp + 1)
        call path_ptr
        ld (iy + E_PPTR), l
        ld (iy + E_PPTR + 1), h
        inc hl
        ld a, (hl)
        push hl
        call scale_v
        ld (iy + E_VX), l
        ld (iy + E_VX + 1), h
        pop hl
        inc hl
        ld a, (hl)
        call scale_v
        ld (iy + E_VY), l
        ld (iy + E_VY + 1), h
        ; type data
        ld a, (tmp + 0)
        call etype_ptr
        push hl
        pop ix
        ld a, (ix + 0)
        ld (iy + E_MODEL), a
        ld a, (ix + 1)
        ld (iy + E_HP), a
        ld a, (ix + 4)
        ld (iy + E_NBOX), a
        ld a, (ix + 5)
        ld (iy + E_PAT0), a
        ld a, (ix + 6)
        ld (iy + E_PAT1), a
        ld a, (ix + 7)
        ld (iy + E_FKIND), a
        ld a, (ix + 8)
        ld (iy + E_FPER), a
        ld a, (ix + 9)
        ld (iy + E_FIRST), a
        ld a, (ix + 11)
        ld (iy + E_AMODE), a
        ld a, (ix + 13)
        ld (iy + E_SDX), a
        ld a, (ix + 10)
        ld e, 0
        call att_mat
        ld (iy + E_ATAB), l
        ld (iy + E_ATAB + 1), h
        ; attitude: the middle (bank mode) or random
        ld a, (ix + 10)
        call att_count
        ld (iy + E_ATN), a
        ld c, a
        ld a, (ix + 11)
        or a
        jr nz, se_3
        ld a, c
        srl a
        jr se_4
se_3:   call rand
        ld e, c
        call mul8u                  ; rand * n / 256
        ld a, h
se_4:   ld (iy + E_ATT), a
        ; fire countdown: per / 2 + (rand & 31)
        ld a, (ix + 7)
        or a
        jr z, se_5
        call rand
        and 31
        ld c, a
        ld a, (ix + 8)
        srl a
        add a, c
        ld (iy + E_FIRE), a
        ld a, (ix + 7)
        cp 3
        jr nz, se_5
        ld (iy + E_BURST), 2
se_5:   jp enemy_pos

; ---------------------------------------------------------------- player
player_update:
        ld a, (pl_in)
        or a
        jr z, pu_1
        dec a                       ; flying in: 2 px per tick
        ld (pl_in), a
        ld hl, (pl_x)
        ld de, 32
        add hl, de
        ld (pl_x), hl
        ld c, 8
        jp pu_att
pu_1:   ld a, (pl_dead)
        ld hl, pl_gone
        or (hl)
        ret nz
        ld a, (keys)
        ld c, a
        ; y
        ld de, 0
        bit 0, c
        jr z, pu_2
        ld de, -R_PLAYER_SPEED
pu_2:   bit 1, c
        jr z, pu_3
        ld hl, R_PLAYER_SPEED
        add hl, de
        ex de, hl
pu_3:   push de                     ; vy
        ld hl, (pl_y)
        add hl, de
        ld de, PLAY_Y0 * 16
        call clamp_lo
        ld de, PLAY_Y1 * 16
        call clamp_hi
        ld (pl_y), hl
        ; x
        ld de, 0
        bit 2, c
        jr z, pu_4
        ld de, -R_PLAYER_SPEED
pu_4:   bit 3, c
        jr z, pu_5
        ld hl, R_PLAYER_SPEED
        add hl, de
        ex de, hl
pu_5:   ld hl, (pl_x)
        add hl, de
        ld de, PLAY_X0 * 16
        call clamp_lo
        ld de, R_PLAYER_XMAX * 16
        call clamp_hi
        ld (pl_x), hl
        ; attitude target: 16 climbing, 0 diving, 8 level
        pop de
        ld c, 8
        ld a, d
        or e
        jr z, pu_6
        ld c, 16
        bit 7, d
        jr nz, pu_6
        ld c, 0
pu_6:   call pu_att
        ; fire
        ld a, (pl_fire)
        or a
        jr z, pu_7
        dec a
        ld (pl_fire), a
        ret
pu_7:   ld a, (keys)
        and 0x10
        ret z
        ld ix, bolts
        ld b, BO_MAX
pu_8:   ld a, (ix + 0)
        or a
        jr z, pu_9
        ld de, BO_SZ
        add ix, de
        djnz pu_8
        ret
pu_9:   ld hl, (pl_x)
        call px_of
        ld de, R_BOLT_DX
        add hl, de
        ld a, h
        or a
        ret nz
        ld a, l
        cp 248
        ret nc
        ld (ix + 1), a
        ld hl, (pl_y)
        call px_of
        inc l
        ld (ix + 2), l
        ld (ix + 0), 1
        call new_serial
        ld (ix + 3), a
        ld a, 0 + R_BOLT_COOLDOWN
        ld (pl_fire), a
        ld a, SFX_SHOT
        jp sfx_play

; C = target attitude: pl_att one step towards it
pu_att: ld a, (pl_att)
        cp c
        ret z
        jr c, pa_up
        dec a
        ld (pl_att), a
        ret
pa_up:  inc a
        ld (pl_att), a
        ret

; HL = max(HL, DE), HL = min(HL, DE) (signed)
clamp_lo:
        push hl
        or a
        sbc hl, de
        pop hl
        ret p
        ex de, hl
        ret
clamp_hi:
        push hl
        or a
        sbc hl, de
        pop hl
        ret m
        ret z
        ex de, hl
        ret

; after a death: the respawn wait, then the fly-in (or the game over);
; invulnerability after the fly-in
player_timers:
        ld a, (pl_dead)
        or a
        jr z, pt_2
        dec a
        ld (pl_dead), a
        jr nz, pt_v
        ld a, (st_lives)
        or a
        jr z, pt_over
        ld a, 0 + R_PLAYERIN_TICKS
        ld (pl_in), a
        ld a, 0 + R_INVUL_TICKS
        ld (pl_invul), a
        ld a, 8
        ld (pl_att), a
        ld hl, -40 * 16
        ld (pl_x), hl
        ld hl, 96 * 16
        ld (pl_y), hl
        jr pt_v
pt_over:
        ld a, 1
        ld (pl_gone), a
        ld a, (demo)
        or a
        jr nz, pt_v
        ld a, MODE_OVER
        ld (st_mode), a
        ld hl, 0
        ld (gmode_t), hl
        xor a
        ld (txt_t), a               ; (GAME OVER alone)
        call hi_update
        jr pt_v
pt_2:   ld a, (pl_in)
        or a
        jr nz, pt_v
        ld a, (pl_invul)
        or a
        jr z, pt_v
        dec a
        ld (pl_invul), a
pt_v:   ; can it be hit?
        ld a, (pl_dead)
        ld hl, pl_in
        or (hl)
        ld hl, pl_invul
        or (hl)
        ld hl, pl_gone
        or (hl)
        ld a, 0
        jr nz, pt_v1
        inc a
pt_v1:  ld (pl_vuln), a
        ret

player_death:
        ld hl, (pl_x)
        ld de, (pl_y)
        xor a
        ld (ex_vx), a
        ld a, 8
        ld (ex_col), a
        ld a, 6
        ld (ex_n), a
        call explode
        ld a, SFX_DEATH
        call sfx_play
        ld a, 0 + R_RESPAWN_TICKS
        ld (pl_dead), a
        xor a
        ld (pl_vuln), a
        ld hl, st_lives
        ld a, (hl)
        or a
        jr z, pd_1
        dec (hl)
pd_1:   ld hl, (st_deaths)
        inc hl
        ld (st_deaths), hl
        ld a, (demo)
        or a
        jr z, pd_2
        ld a, 60
        ld (demo_end), a
pd_2:   jp hud_refresh

extra_check:
        ld hl, st_score + 2
        ld de, next_extra + 2
        ld b, 3
ec_1:   ld a, (de)
        cp (hl)
        jr c, ec_yes                ; next < score
        ret nz
        dec hl
        dec de
        djnz ec_1
ec_yes: ; score >= next: an extra life, next += 050000 (past 999999: FFh,
        ; which no score reaches); at most 99 lives
        ld hl, next_extra + 2
        ld a, (hl)
        add a, 5
        daa
        jr nc, ec_2
        ld a, 0xFF
ec_2:   ld (hl), a
        ld hl, st_lives
        ld a, (hl)
        cp 99
        jr nc, ec_3
        inc (hl)
ec_3:   ld a, SFX_EXTRA
        call sfx_play
        jp hud_refresh

; ---------------------------------------------------------------- enemies
; every enemy: move, attitude, fire, position; the ones on the screen go
; into this tick's collision snapshot (slot 4 on the software path, slot
; tick & 3 on the hardware path)
enemies_update:
        ld a, (st_hwcoll)
        or a
        ld a, 4
        jr z, eu_0
        ld a, (st_tick)
        and 3
eu_0:   call sn_addr
        ld de, SN_EN
        add hl, de
        ld (en_np), hl
        ld (hl), 0
        inc hl
        ld (en_wp), hl
        ld iy, enem
        ld b, EN_MAX
        ld c, 0
eu_1:   ld a, (iy + E_TYPE)
        cp 0xFF
        jr z, eu_n
        push bc
        bit 0, b
        call z, q_pump              ; (every other slot)
        call enemy_move
        ld a, (iy + E_TYPE)
        cp 0xFF
        jr z, eu_gone
        call enemy_att
        call enemy_fire_step
        call enemy_pos
        call nz, sn_entry           ; on the screen
eu_off: pop bc
        inc c
        jr eu_n
eu_gone:
        pop bc
eu_n:   ld de, EN_SZ
        add iy, de
        djnz eu_1
        ld a, c
        ld (st_enem), a
        ret

; IY -> enemy: one step along its path
enemy_move:
        ld l, (iy + E_AGE)
        ld h, (iy + E_AGE + 1)
        inc hl
        ld (iy + E_AGE), l
        ld (iy + E_AGE + 1), h
        ld l, (iy + E_PPTR)
        ld h, (iy + E_PPTR + 1)
        push hl
        pop ix                      ; IX -> kind, vx, vy, p0..p4
        ld a, (ix + 0)
        or a
        jp z, em_line
        cp 1
        jr z, em_sine
        cp 2
        jp z, em_dive
        cp 3
        jp z, em_arc
        cp 4
        jp z, em_hover
        ; BOUNCE: vy flips at the play area's top and bottom
        call add_vx
        call add_vy
        ld l, (iy + E_Y)
        ld h, (iy + E_Y + 1)
        ld de, PLAY_Y0 * 16
        or a
        sbc hl, de
        jp m, em_flip
        ld l, (iy + E_Y)
        ld h, (iy + E_Y + 1)
        ld de, PLAY_Y1 * 16 + 1
        or a
        sbc hl, de
        jp m, em_gone
em_flip:
        ld l, (iy + E_VY)
        ld h, (iy + E_VY + 1)
        call neg_hl
        ld (iy + E_VY), l
        ld (iy + E_VY + 1), h
        jp em_gone
em_sine:
        ; x += vx; y = y0 + (p0 * sin(age * p1 + p2)) / 8
        call add_vx
        ld b, (ix + 4)              ; p1 (small): age * p1 by adding
        ld c, (iy + E_AGE)
        ld a, (ix + 5)
es_1:   add a, c
        djnz es_1
        ld e, a
        ld d, 0
        ld hl, sin256
        add hl, de
        ld a, (hl)
        ld e, (ix + 3)
        bit 7, a
        jr nz, es_neg
        call mul8u
        srl h
        rr l
        srl h
        rr l
        srl h
        rr l
        jr es_2
es_neg: neg
        call mul8u
        srl h
        rr l
        srl h
        rr l
        srl h
        rr l
        call neg_hl
es_2:
        ld e, (iy + E_Y0)
        ld d, (iy + E_Y0 + 1)
        add hl, de
        ld (iy + E_Y), l
        ld (iy + E_Y + 1), h
        jp em_gone
em_line:
        call add_vx
        call add_vy
        jp em_gone
em_dive:
        ; straight until x < p0 * 2 px, then steer vy towards the ship
        call add_vx
        ld a, (ix + 3)
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl                  ; p0 * 32
        ex de, hl
        ld l, (iy + E_X)
        ld h, (iy + E_X + 1)
        or a
        sbc hl, de
        jp p, em_dv2
        ; want = clamp((pl_y - y) / 8, -p1, p1)
        ld hl, (pl_y)
        ld e, (iy + E_Y)
        ld d, (iy + E_Y + 1)
        or a
        sbc hl, de
        ld b, 3
        call sra_hl
        ld a, (ix + 4)
        call clamp_pm
        ; vy += clamp(want - vy, -p2, p2)
        ld e, (iy + E_VY)
        ld d, (iy + E_VY + 1)
        or a
        sbc hl, de
        ld a, (ix + 5)
        call clamp_pm
        add hl, de
        ld (iy + E_VY), l
        ld (iy + E_VY + 1), h
em_dv2: call add_vy
        jp em_gone
em_arc:
        ; straight for p0 ticks, then turn the velocity by p2 per tick for
        ; p1 ticks
        ld l, (iy + E_AGE)
        ld h, (iy + E_AGE + 1)
        ld e, (ix + 3)
        ld d, 0
        or a
        sbc hl, de
        jr c, em_ar2                ; age < p0
        ld e, (ix + 4)
        or a
        sbc hl, de
        jr nc, em_ar2               ; age >= p0 + p1
        call rot_v
em_ar2: call add_vx
        call add_vy
        jp em_gone
em_hover:
        ld a, (iy + E_P)
        or a
        jr nz, em_hv1
        ; coming in: until x <= p0 * 2 px
        call add_vx
        ld a, (ix + 3)
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        inc hl
        ex de, hl
        ld l, (iy + E_X)
        ld h, (iy + E_X + 1)
        or a
        sbc hl, de
        jp p, em_gone
        ld (iy + E_P), 1
        ld a, (ix + 5)
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        ld (iy + E_LIFE), l
        ld (iy + E_LIFE + 1), h
        jp em_gone
em_hv1: cp 1
        jr nz, em_hv2
        ; hovering: track the ship's y, then leave
        ld hl, (pl_y)
        ld e, (iy + E_Y)
        ld d, (iy + E_Y + 1)
        or a
        sbc hl, de
        ld b, 4
        call sra_hl
        ld a, (ix + 4)
        call clamp_pm
        ld e, (iy + E_VY)
        ld d, (iy + E_VY + 1)
        or a
        sbc hl, de
        ld a, 2
        call clamp_pm
        add hl, de
        ld (iy + E_VY), l
        ld (iy + E_VY + 1), h
        call add_vy
        ld l, (iy + E_LIFE)
        ld h, (iy + E_LIFE + 1)
        dec hl
        ld (iy + E_LIFE), l
        ld (iy + E_LIFE + 1), h
        ld a, h
        or l
        jr z, em_hv1a
        bit 7, h
        jr z, em_gone
em_hv1a:
        ld (iy + E_P), 2
        ld a, (ix + 6)
        call scale_v
        ld (iy + E_VX), l
        ld (iy + E_VX + 1), h
        jr em_gone
em_hv2: call add_vx
em_gone:
        ; off the field: x < -48 px, or x > 320 px moving right, or
        ; y outside -40..220 px (looked at every other tick)
        bit 0, (iy + E_AGE)
        ret nz
        ld l, (iy + E_X)
        ld h, (iy + E_X + 1)
        ld de, 48 * 16
        add hl, de
        bit 7, h
        jr nz, em_free
        ld l, (iy + E_X)
        ld h, (iy + E_X + 1)
        ld de, -320 * 16
        add hl, de
        bit 7, h
        jr nz, em_g2
        bit 7, (iy + E_VX + 1)
        jr z, em_g2a
em_g2:  ld l, (iy + E_Y)
        ld h, (iy + E_Y + 1)
        ld de, 40 * 16
        add hl, de
        bit 7, h
        jr nz, em_free
        ld de, -260 * 16
        add hl, de
        bit 7, h
        ret nz
        jr em_free
em_g2a: ld a, (iy + E_VX)
        or (iy + E_VX + 1)
        jr z, em_g2
em_free:
        ld (iy + E_TYPE), 0xFF
        ret

; HL = clamp(HL, -A, A)
clamp_pm:
        push de
        ld e, a
        ld d, 0
        bit 7, h
        jr nz, cpm_n
        push hl
        or a
        sbc hl, de
        pop hl
        jr c, cpm_x
        ex de, hl
        jr cpm_x
cpm_n:  push hl
        add hl, de
        pop hl
        jr c, cpm_x                 ; HL + A >= 0: inside
        ld hl, 0
        or a
        sbc hl, de
cpm_x:  pop de
        ret

add_vx: ld l, (iy + E_X)
        ld h, (iy + E_X + 1)
        ld e, (iy + E_VX)
        ld d, (iy + E_VX + 1)
        add hl, de
        ld (iy + E_X), l
        ld (iy + E_X + 1), h
        ret
add_vy: ld l, (iy + E_Y)
        ld h, (iy + E_Y + 1)
        ld e, (iy + E_VY)
        ld d, (iy + E_VY + 1)
        add hl, de
        ld (iy + E_Y), l
        ld (iy + E_Y + 1), h
        ret

; turn (vx, vy) by p2 (IX + 5, 1/256 turn): v' = R v / 127 (vx, vy small)
rot_v:
        ld a, (ix + 5)
        ld e, a
        ld d, 0
        ld hl, sin256
        add hl, de
        ld a, (hl)
        ld (tmp + 0), a             ; s
        ld a, e
        add a, 64
        ld e, a
        ld hl, sin256
        add hl, de
        ld a, (hl)
        ld (tmp + 1), a             ; c
        ; vx' = (vx c - vy s) / 127
        ld a, (tmp + 1)
        ld e, a
        ld a, (iy + E_VX)
        call mul8s
        push hl
        ld a, (tmp + 0)
        ld e, a
        ld a, (iy + E_VY)
        call mul8s
        ex de, hl
        pop hl
        or a
        sbc hl, de
        call div127
        push hl
        ; vy' = (vx s + vy c) / 127
        ld a, (tmp + 0)
        ld e, a
        ld a, (iy + E_VX)
        call mul8s
        push hl
        ld a, (tmp + 1)
        ld e, a
        ld a, (iy + E_VY)
        call mul8s
        pop de
        add hl, de
        call div127
        ld (iy + E_VY), l
        ld (iy + E_VY + 1), h
        pop hl
        ld (iy + E_VX), l
        ld (iy + E_VX + 1), h
        ret

; HL = round(HL / 127) (signed; HL / 128 * 128 / 127, rounded)
div127:
        push de
        ld d, h
        ld e, l
        ld b, 7
        call sra_hl                 ; HL / 128
        add hl, de                  ; HL + HL / 128
        ld de, 64
        add hl, de
        ld b, 7
        call sra_hl
        pop de
        ret

; IY -> enemy: attitude (mode 0: bank by vy; 1: +1 per tick; 2: +1 every
; 2 ticks)
enemy_att:
        ld c, (iy + E_ATN)          ; n
        ld a, (iy + E_AMODE)
        or a
        jr nz, ea_2
        ; want = clamp(n / 2 - vy / 3, 0, n - 1)
        ld e, (iy + E_VY)
        ld d, 0
        ld hl, div3_tab
        add hl, de
        ld b, (hl)
        ld a, c
        srl a
        sub b
        jp p, ea_1a
        xor a
ea_1a:  cp c
        jr c, ea_1b
        ld a, c
        dec a
ea_1b:  ld b, a
        ld a, (iy + E_ATT)
        cp b
        ret z
        jr c, ea_up
        dec (iy + E_ATT)
        ret
ea_up:  inc (iy + E_ATT)
        ret
ea_2:   cp 2
        jr nz, ea_3
        bit 0, (iy + E_AGE)
        ret nz
ea_3:   ld a, (iy + E_ATT)
        inc a
        cp c
        jr c, ea_4
        xor a
ea_4:   ld (iy + E_ATT), a
        ret

; A = signed -> A = floor(A / 3)
div3:
        push hl
        push de
        bit 7, a
        jr nz, d3_n
        ld e, 86
        call mul8u
        ld a, h
        jr d3_x
d3_n:   neg
        add a, 2
        ld e, 86
        call mul8u
        ld a, h
        neg
d3_x:   pop de
        pop hl
        ret

; IY -> enemy: count down its fire timer and shoot
enemy_fire_step:
        ld a, (iy + E_FKIND)
        or a
        ret z
        ld a, (st_round)
        cp (iy + E_FIRST)
        ret c                       ; not from this round yet
        ld a, (pl_dead)
        ld hl, pl_gone
        or (hl)
        ret nz
        ld l, (iy + E_X)
        ld h, (iy + E_X + 1)
        ld de, -250 * 16
        add hl, de
        bit 7, h
        ret z                       ; x >= 250 px: not yet
        dec (iy + E_FIRE)
        jr z, efs_1
        bit 7, (iy + E_FIRE)
        ret z
efs_1:  ld a, (iy + E_FKIND)
        ld (sh_kind), a
        call enemy_shoot
        ld a, (sh_kind)
        cp 3
        jr nz, efs_2
        ld a, (iy + E_BURST)
        or a
        jr z, efs_2
        dec (iy + E_BURST)
        ld (iy + E_FIRE), 6
        ret
efs_2:  ; period * 8 / (8 + min(round, 8))
        ld a, (iy + E_FPER)
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        ld a, (st_round)
        cp 8
        jr c, efs_3
        ld a, 8
efs_3:  add a, 8
        ld c, a
        ld b, 0
        xor a
efs_4:  sbc hl, bc
        jr c, efs_5
        inc a
        jr efs_4
efs_5:  or a
        jr nz, efs_6
        inc a
efs_6:  ld (iy + E_FIRE), a
        ld a, (sh_kind)
        cp 3
        ret nz
        ld (iy + E_BURST), 2
        ret

; IY -> enemy: aimed shot(s) at the ship
enemy_shoot:
        ; direction
        ld hl, (pl_x)
        ld e, (iy + E_X)
        ld d, (iy + E_X + 1)
        or a
        sbc hl, de
        call px_of
        ld (aim_dx), hl
        ld hl, (pl_y)
        ld e, (iy + E_Y)
        ld d, (iy + E_Y + 1)
        or a
        sbc hl, de
        call px_of
        ld (aim_dy), hl
        call aim
        ld (tmp + 4), a             ; d
        ld a, (sh_kind)
        cp 2
        jr nz, esh_1
        ld a, (tmp + 4)
        dec a
        call bullet_new
        ld a, (tmp + 4)
        inc a
        call bullet_new
esh_1:  ld a, (tmp + 4)
        call bullet_new
        ld a, SFX_ESHOT
        jp sfx_play

; A = direction (mod 32): a bullet from the enemy IY (keeps IX)
bullet_new:
        and 31
        push ix
        ld hl, bullets
        ld de, BU_SZ
        ld b, BU_MAX
bn_1:   ld c, a
        ld a, (hl)
        or a
        jr z, bn_2
        ld a, c
        add hl, de
        djnz bn_1
        pop ix
        ret
bn_2:   ld a, c
        push hl
        pop ix                      ; the bullet
        push af
        ld a, (iy + E_SDX)          ; shot x offset (pixels)
        call sext
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        ld e, (iy + E_X)
        ld d, (iy + E_X + 1)
        add hl, de
        ld (ix + 1), l
        ld (ix + 2), h
        ld a, (iy + E_Y)
        ld (ix + 3), a
        ld a, (iy + E_Y + 1)
        ld (ix + 4), a
        pop af
        ; velocity: bvel_tab (dir32 * the round's bullet speed / 16)
        add a, a
        ld e, a
        ld d, 0
        ld hl, bvel_tab
        add hl, de
        ld a, (hl)
        ld (ix + 5), a
        inc hl
        ld a, (hl)
        ld (ix + 6), a
        ld (ix + 0), 1
        call new_serial
        ld (ix + 10), a
        pop ix
        ret

; bvel_tab: the 32 bullet velocities at this round's speed (a round start)
bvel_init:
        ld a, 0 + R_BULLET_SPEED
        call scale_v
        ld a, l
        ld (sh_speed), a
        ld hl, dir32
        ld de, bvel_tab
        ld b, 64
bvi_1:  push bc
        push de
        push hl
        ld a, (sh_speed)
        ld e, a
        ld a, (hl)
        call mul8s
        ld b, 4
        call sra_hl
        ld a, l
        pop hl
        pop de
        ld (de), a
        inc hl
        inc de
        pop bc
        djnz bvi_1
        ret

; aim_dx, aim_dy (pixels) -> A = direction 0-31 (0 = +x, 8 = down)
aim:
        ld hl, (aim_dx)
        ld a, h
        ld (tmp + 5), a             ; sign of dx
        bit 7, h
        call nz, neg_hl
        ex de, hl                   ; DE = |dx|
        ld hl, (aim_dy)
        ld a, h
        ld (tmp + 6), a
        bit 7, h
        call nz, neg_hl             ; HL = |dy|
        ; both below 256
am_1:   ld a, h
        or d
        jr z, am_2
        srl h
        rr l
        srl d
        rr e
        jr am_1
am_2:   ld a, e
        or l
        ret z                       ; on top of it: 0
        ; minor / major -> k (0..4 steps of 11.25 degrees from the major axis)
        ld a, l
        cp e
        jr nc, am_ymaj
        ld b, l                     ; minor = |dy|, major = |dx|
        ld c, e
        call aim_k
        jr am_q                     ; a = k
am_ymaj:
        ld b, e                     ; minor = |dx|, major = |dy|
        ld c, l
        call aim_k
        neg
        add a, 8                    ; a = 8 - k
am_q:   ; quadrant
        ld b, a
        ld a, (tmp + 5)
        bit 7, a
        jr nz, am_left
        ld a, (tmp + 6)
        bit 7, a
        ld a, b
        ret z                       ; dx >= 0, dy >= 0: a
        neg
        and 31
        ret                         ; dx >= 0, dy < 0: 32 - a
am_left:
        ld a, (tmp + 6)
        bit 7, a
        jr nz, am_ll
        ld a, 16                    ; dx < 0, dy >= 0: 16 - a
        sub b
        ret
am_ll:  ld a, 16                    ; dx < 0, dy < 0: 16 + a
        add a, b
        ret

; B = minor, C = major (both < 256, major > 0) -> A = round(atan(B/C) /
; 11.25 degrees): count the thresholds tan(5.6, 16.9, 28.1, 39.4 deg) * 256
; that minor * 256 passes
aim_k:
        ld hl, aim_t
        ld d, 0
        ld a, 4
ak_1:   push af
        push hl
        ld a, (hl)
        ld e, c
        push bc
        call mul8u                  ; major * t
        pop bc
        ld a, b
        cp h                        ; minor vs (major * t) / 256
        jr c, ak_2
        jr nz, ak_3
        ld a, l
        or a
        jr nz, ak_2                 ; equal high bytes: minor * 256 < major * t
ak_3:   pop hl
        inc hl
        pop af
        dec a
        jr nz, ak_1
        ld a, 4
        ret
ak_2:   pop hl
        ld de, aim_t
        or a
        sbc hl, de
        ld a, l
        pop de
        ret

aim_t:  db 25, 78, 137, 210

; IY -> enemy: pixel position and the on-screen flag (NZ: on the screen,
; x and y 0..255, y < 212)
enemy_pos:
        ld l, (iy + E_Y)
        ld h, (iy + E_Y + 1)
        call px_of
        ld (iy + E_PYW), l
        ld (iy + E_PYW + 1), h
        ld c, l
        ld b, h
        ld l, (iy + E_X)
        ld h, (iy + E_X + 1)
        call px_of
        ld (iy + E_PX), l
        ld (iy + E_PX + 1), h
        ld (iy + E_ON), 0
        ld a, h
        or b
        jr nz, ep_off               ; x or y outside 0..255
        ld a, c
        ld (iy + E_PY), a
        cp 212
        jr nc, ep_off
        ld (iy + E_ON), 1
        or 1
        ret
ep_off: xor a
        ret

; IY -> enemy on the screen: its entry in this tick's snapshot (en_wp):
; slot, generation, box (etype_box around E_PX, E_PY, clamped to 0..255)
sn_entry:
        ld hl, (en_wp)
        push iy
        pop de
        ld (hl), e
        inc hl
        ld (hl), d
        inc hl
        ld a, (iy + E_GEN)
        ld (hl), a
        inc hl
        ex de, hl                   ; DE -> the box
        ld a, (iy + E_TYPE)
        add a, a
        add a, a
        add a, etype_box & 0xFF
        ld l, a
        ld a, etype_box >> 8
        adc a, 0
        ld h, a                     ; HL -> the type's x0, x1, y0, y1
        ld c, (iy + E_PX)
        ld a, (hl)
        call add_clamp
        ld (de), a
        inc hl
        inc de
        ld a, (hl)
        call add_clamp
        ld (de), a
        inc hl
        inc de
        ld c, (iy + E_PY)
        ld a, (hl)
        call add_clamp
        ld (de), a
        inc hl
        inc de
        ld a, (hl)
        call add_clamp
        ld (de), a
        inc de
        ld (en_wp), de
        ld hl, (en_np)
        inc (hl)
        ret

; A = C + A (A signed), clamped to 0..255
add_clamp:
        bit 7, a
        jr nz, ac_n
        add a, c
        ret nc
        ld a, 255
        ret
ac_n:   add a, c
        ret c
        xor a
        ret

; ---------------------------------------------------------------- shots
bolts_update:
        ld ix, bolts
        ld b, BO_MAX
bu_1:   ld a, (ix + 0)
        or a
        jr z, bu_n
        ld a, (ix + 1)
        add a, R_BOLT_SPEED / 16
        ld (ix + 1), a
        cp 248
        jr c, bu_n
        ld (ix + 0), 0
bu_n:   ld de, BO_SZ
        add ix, de
        djnz bu_1
        ret

bullets_update:
        ld ix, bullets
        ld b, BU_MAX
bl_1:   ld a, (ix + 0)
        or a
        jr z, bl_n
        ld a, (ix + 5)
        call sext
        ld e, (ix + 1)
        ld d, (ix + 2)
        add hl, de
        ld (ix + 1), l
        ld (ix + 2), h
        ; -16 < x < 264 px
        ld de, 16 * 16
        add hl, de
        bit 7, h
        jr nz, bl_off
        ld de, -280 * 16
        add hl, de
        bit 7, h
        jr z, bl_off
        ld a, (ix + 6)
        call sext
        ld e, (ix + 3)
        ld d, (ix + 4)
        add hl, de
        ld (ix + 3), l
        ld (ix + 4), h
        ; 8 < y < 170 px
        ld de, -9 * 16
        add hl, de
        bit 7, h
        jr nz, bl_off
        ld de, -161 * 16
        add hl, de
        bit 7, h
        jr z, bl_off
        ; pixels for the sprite and the tests
        ld l, (ix + 3)
        ld h, (ix + 4)
        call px_of
        ld (ix + 9), l
        ld l, (ix + 1)
        ld h, (ix + 2)
        call px_of
        ld (ix + 7), l
        ld (ix + 8), h
        jr bl_n
bl_off: ld (ix + 0), 0
bl_n:   ld de, BU_SZ
        add ix, de
        djnz bl_1
        ret

; ---------------------------------------------------------------- effects
; HL, DE = x, y (1/16 px); ex_n shards of models ex_col + 0..2, base x
; speed ex_vx; and a flash
; A = a dir32 component c -> A = c * (2 + s) / 2, s = (tmp + 12) 0-3
; (keeps B, HL)
shard_v:
        ld c, a
        sra a
        ld e, a                     ; c / 2
        ld a, (tmp + 12)
        ld d, a
        ld a, c
        bit 1, d
        jr z, sv_a
        add a, c                    ; 2 c
sv_a:   bit 0, d
        ret z
        add a, e
        ret

explode:
        ld (tmp + 8), hl
        ld (tmp + 10), de
        ; one shard shape per explosion (fewer model changes for geo3d)
        call rand
        ld e, 3
        call mul8u
        ld a, (ex_col)
        add a, h
        ld (tmp + 13), a
        ld a, (ex_n)
        ld b, a
ex_1:   push bc
        ; the slot: round robin (the oldest shard goes)
        ld a, (db_next)
        ld l, a
        inc a
        cp DB_MAX
        jr c, ex_2
        xor a
ex_2:   ld (db_next), a
        ld h, 0
        ld e, l
        ld d, h
        add hl, hl
        add hl, hl
        add hl, de
        add hl, hl                  ; 10 slot
        ld de, debris
        add hl, de
        push hl
        pop ix
        ld a, (tmp + 13)
        ld (ix + 0), a              ; model
        ld hl, (tmp + 8)
        ld (ix + 1), l
        ld (ix + 2), h
        ld hl, (tmp + 10)
        ld (ix + 3), l
        ld (ix + 4), h
        call rand                   ; bits 0-4 direction, 5-6 speed
        ld c, a
        and 31
        add a, a
        ld e, a
        ld d, 0
        ld hl, dir32
        add hl, de
        ld a, c
        rlca
        rlca
        rlca
        and 3
        ld (tmp + 12), a            ; speed: 1, 1.5, 2 or 2.5 px per tick
        ld a, (hl)
        call shard_v
        ld b, a
        ld a, (ex_vx)
        add a, b
        ld (ix + 5), a
        inc hl
        ld a, (hl)
        call shard_v
        ld (ix + 6), a
        call rand                   ; bits 0-4 attitude, 5-7 life
        ld c, a
        and 31
        ld (ix + 7), a
        ld a, c
        rlca
        rlca
        rlca
        and 7
        add a, 20
        ld (ix + 8), a
        pop bc
        dec b
        jp nz, ex_1
        ; a flash (if a plane is free)
        ld ix, flashes
        ld b, FL_MAX
ex_3:   ld a, (ix + 0)
        or a
        jr z, ex_4
        ld de, FL_SZ
        add ix, de
        djnz ex_3
        ret
ex_4:   ld hl, (tmp + 8)
        call px_of
        ld a, h
        or a
        ret nz
        ld (ix + 1), l
        ld hl, (tmp + 10)
        call px_of
        ld a, h
        or a
        ret nz
        ld (ix + 2), l
        ld (ix + 3), 0
        ld (ix + 0), 1
        ret

debris_update:
        ld ix, debris
        ld b, DB_MAX
du_1:   ld a, (ix + 0)
        cp 0xFF
        jr z, du_n
        ld a, (ix + 5)
        call sext
        ld e, (ix + 1)
        ld d, (ix + 2)
        add hl, de
        ld (ix + 1), l
        ld (ix + 2), h
        ld a, (ix + 6)
        call sext
        ld e, (ix + 3)
        ld d, (ix + 4)
        add hl, de
        ld (ix + 3), l
        ld (ix + 4), h
        ld a, (ix + 7)
        inc a
        and 31
        ld (ix + 7), a
        dec (ix + 8)
        jr nz, du_n
        ld (ix + 0), 0xFF
du_n:   ld de, DB_SZ
        add ix, de
        djnz du_1
        ret

flashes_update:
        ld ix, flashes
        ld b, FL_MAX
fu_1:   ld a, (ix + 0)
        or a
        jr z, fu_n
        inc (ix + 3)
        ld a, (ix + 3)
        cp 10
        jr c, fu_n
        ld (ix + 0), 0
fu_n:   ld de, FL_SZ
        add ix, de
        djnz fu_1
        ret

; IY -> enemy: destroyed
enemy_kill:
        ld a, (iy + E_TYPE)
        call etype_ptr
        push hl
        inc hl
        inc hl
        ld e, (hl)
        inc hl
        ld d, (hl)
        call score_add
        ld hl, (st_kills)
        inc hl
        ld (st_kills), hl
        pop hl
        ld de, 12
        add hl, de
        ld a, (hl)                  ; the type's shards: at most 4 (8 for the
        cp 8                        ; gunship), geo3d objects cost the Z80
        jr nc, ek_0
        cp 5
        jr c, ek_0
        ld a, 4
ek_0:   cp DB_MAX + 1
        jr c, ek_0a
        ld a, DB_MAX
ek_0a:  ld (ex_n), a
        ld a, 5
        ld (ex_col), a
        ld a, (iy + E_VX)
        sra a
        ld (ex_vx), a
        ld l, (iy + E_X)
        ld h, (iy + E_X + 1)
        ld e, (iy + E_Y)
        ld d, (iy + E_Y + 1)
        call explode
        ld a, (iy + E_TYPE)
        cp ET_GUNSHIP
        ld a, SFX_EXPLODE
        jr nz, ek_1
        ld a, SFX_BIGBOOM
ek_1:   call sfx_play
        ld (iy + E_TYPE), 0xFF
        ld (iy + E_ON), 0
        ret

; ---------------------------------------------------------------- collide
; The box test runs on a snapshot of one tick (ram.asm snaps): its bolts
; and bullets (the RAM arrays or a copy of them), the ship (vuln: its
; sprites were on, x, y) and the enemies on the screen (slot, generation,
; box; enemies_update writes them). A hit applies to the object only while
; it is still the same one: a bolt or bullet slot with the same serial (a
; shot that flew off still counts), an enemy slot in use with the same
; generation.
;   Software path (st_hwcoll = 0): this tick's snapshot, every tick
;   (slot 4 and the RAM bolts and bullets).
;   Hardware path (st_hwcoll = 1): every tick ends with its snapshot in
;   slot tick & 3 (sn_save). The page of tick K is on show while tick K + 1
;   runs, so the ISR sees its collision flag in tick K + 1 or K + 2 and
;   marks the page's bit (i_cmask); the marked ticks are tested, oldest
;   first, on their own positions (the objects may have moved apart since).
;   S#0 bit 6 marks a page too: a sprite dropped from a full line cannot
;   collide, so the flag alone would miss it.
collide:
        di
        ld a, (i_cmask)
        ld b, a
        xor a
        ld (i_cmask), a
        ld hl, (i_cx)
        ld de, (i_cy)
        call eiop
        ld a, b
        ld (sn_mask), a
        or a
        jr z, cl_0
        ld (st_cx), hl
        ld (st_cy), de
        ld hl, (st_cev)
        inc hl
        ld (st_cev), hl
cl_0:   ld a, (st_hwcoll)
        or a
        jr nz, cl_hw
        ; software: this tick, the shots in RAM
        ld a, 4
        call sn_addr
        push hl
        ld de, SN_PL
        add hl, de
        call sn_ship
        pop ix
        ld hl, bolts
        ld (sn_bb), hl
        jp sn_test
cl_hw:  ld c, 3                     ; ticks 3, 2 and 1 back
cl_h1:  ld a, (st_tick)
        sub c
        and 3
        ld e, a
        ld d, 0
        ld hl, bit_tab
        add hl, de
        ld a, (sn_mask)
        and (hl)
        jr z, cl_h2
        ld a, (sn_done)
        ld b, a
        and (hl)
        jr nz, cl_h2                ; tested already (the page's other frame)
        ld a, b
        or (hl)
        ld (sn_done), a
        push bc
        ld a, e
        call sn_addr
        ld (sn_bb), hl              ; (SN_BB = 0)
        push hl
        pop ix
        call sn_test
        pop bc
cl_h2:  dec c
        jr nz, cl_h1
        ret

; A = snapshot slot 0-4 -> HL -> it (keeps BC)
sn_addr:
        add a, a
        ld e, a
        ld d, 0
        ld hl, sn_tab
        add hl, de
        ld a, (hl)
        inc hl
        ld h, (hl)
        ld l, a
        ret

sn_tab:
        dw snaps, snaps + SN_SZ, snaps + 2 * SN_SZ, snaps + 3 * SN_SZ, snaps + 4 * SN_SZ

; HL -> a snapshot's ship bytes: vuln, x px, y px (now)
sn_ship:
        ld a, (pl_vuln)
        ld (hl), a
        inc hl
        push hl
        ld hl, (pl_x)
        call px_of
        ld a, l
        pop hl
        ld (hl), a
        inc hl
        push hl
        ld hl, (pl_y)
        call px_of
        ld a, l
        pop hl
        ld (hl), a
        ret

; the end of a tick on the hardware path: its bolts, bullets and ship into
; slot tick & 3 (its enemies are there already), as spr_build shows them
sn_save:
        ld a, (st_tick)
        and 3
        ld e, a
        ld d, 0
        ld hl, bit_tab
        add hl, de
        ld a, (hl)
        cpl
        ld hl, sn_done
        and (hl)
        ld (hl), a                  ; not tested yet
        ld a, e
        call sn_addr
        ex de, hl
        ld hl, bolts
        ld bc, SN_PL
        ldir
        ex de, hl                   ; HL -> its ship bytes
        jp sn_ship

; forget the snapshots and the flagged pages (a game starts, the title)
sn_reset:
        di
        xor a
        ld (i_cmask), a
        call eiop
        xor a
        ld (sn_done), a
        ld b, 5
sr_1:   push bc
        ld a, b
        dec a
        call sn_addr
        ld de, SN_PL
        add hl, de
        ld (hl), 0                  ; no ship
        ld de, SN_EN - SN_PL
        add hl, de
        ld (hl), 0                  ; no enemies
        pop bc
        djnz sr_1
        ret

; the box test of the snapshot IX, (sn_bb) -> its bolts and bullets
sn_test:
        ld hl, (st_nexev)
        inc hl
        ld (st_nexev), hl
        ; 1. bolts against the enemies: the bolt's box x - 15..x + 6 (8 px
        ; more behind it, the way it came since the tick before), y - 1..
        ; y + 1
        ld hl, (sn_bb)
        ld (sn_p), hl
        ld hl, bolts
        ld (sn_q), hl
        ld b, BO_MAX
tb_1:   push bc
        ld hl, (sn_p)
        ld a, (hl)
        or a
        jr z, tb_n
        inc hl
        ld a, (hl)                  ; x
        sub 15
        jr nc, tb_2
        xor a
tb_2:   ld d, a                     ; x0
        ld a, (hl)
        add a, 6
        jr nc, tb_3
        ld a, 255
tb_3:   ld e, a                     ; x1
        inc hl
        ld a, (hl)                  ; y
        dec a
        ld c, a                     ; y0
        add a, 2
        ld (tmp + 3), a             ; y1
        inc hl
        ld a, (hl)
        ld (tmp + 4), a             ; its serial
        call sn_enemy
        jr z, tb_n
        ld hl, (sn_q)               ; the same bolt in RAM still?
        inc hl
        inc hl
        inc hl
        ld a, (tmp + 4)
        cp (hl)
        jr nz, tb_n                 ; it hit already, or a new one flies there
        ld (hl), 0                  ; spent
        ld hl, (sn_q)
        ld (hl), 0
        ld hl, (st_hits)
        inc hl
        ld (st_hits), hl
        ld hl, (st_events)
        inc hl
        ld (st_events), hl
        dec (iy + E_HP)
        jr z, tb_kill
        ld (iy + E_FLASH), 4
        ld a, SFX_HIT
        call sfx_play
        jr tb_n
tb_kill:
        push ix
        call enemy_kill
        pop ix
tb_n:   ld de, BO_SZ
        ld hl, (sn_p)
        add hl, de
        ld (sn_p), hl
        ld hl, (sn_q)
        add hl, de
        ld (sn_q), hl
        pop bc
        djnz tb_1
        ; 2. enemy bullets against the ship's hitbox (x - 8..x + 7, y - 3..
        ; y + 2), 3. the ship's core (x..x + 5, y - 2..y + 1) against the
        ; enemies; only if its sprites were on then, and it can be hit now
        ld a, (ix + SN_PL)
        or a
        ret z
        ld a, (pl_vuln)
        or a
        ret z
        ld a, (god)
        or a
        ret nz                      ; (test harness: the ship cannot die)
        ld a, (ix + SN_PL + 1)
        ld (tmp + 8), a             ; ship x
        ld a, (ix + SN_PL + 2)
        ld (tmp + 9), a             ; ship y
        ld hl, (sn_bb)
        ld de, BO_MAX * BO_SZ
        add hl, de
        ld (sn_p), hl
        ld hl, bullets
        ld (sn_q), hl
        ld b, BU_MAX
tu_1:   ld hl, (sn_p)
        ld a, (hl)
        or a
        jr z, tu_n
        ld de, 7
        add hl, de
        ld c, (hl)                  ; x
        inc hl
        ld a, (hl)
        or a
        jr nz, tu_n                 ; x outside 0..255
        inc hl
        ; |sy - by| <= 5: bullet y - 3..y + 2 against the ship's y - 3..y + 2
        ld a, (tmp + 9)
        sub (hl)
        add a, 5
        cp 11
        jr nc, tu_n
        ; |sx - bx| <= 10: bullet x - 3..x + 2 against the ship's x - 8..x + 7
        ld a, (tmp + 8)
        sub c
        add a, 10
        cp 21
        jr nc, tu_n
        inc hl
        ld a, (hl)                  ; its serial
        ld hl, (sn_q)
        ld de, 10
        add hl, de
        cp (hl)
        jr nz, tu_n                 ; not that bullet any more
        ld (hl), 0
        ld hl, (sn_q)
        ld (hl), 0
        ld hl, (st_events)
        inc hl
        ld (st_events), hl
        jp player_death
tu_n:   ld de, BU_SZ
        ld hl, (sn_p)
        add hl, de
        ld (sn_p), hl
        ld hl, (sn_q)
        add hl, de
        ld (sn_q), hl
        djnz tu_1
        ld a, (tmp + 8)
        ld d, a
        add a, 5
        ld e, a
        ld a, (tmp + 9)
        sub 2
        ld c, a
        add a, 3
        ld (tmp + 3), a
        call sn_enemy
        ret z
        ld hl, (st_events)
        inc hl
        ld (st_events), hl
        jp player_death

; the box D = x0, E = x1, C = y0, (tmp + 3) = y1 against the enemies of the
; snapshot IX: NZ and IY -> the first one it overlaps that is still the same
; enemy (its slot in use, the same generation), Z if none (keeps IX)
sn_enemy:
        push ix
        pop hl
        push de
        ld de, SN_EN
        add hl, de
        pop de
        ld a, (hl)
        or a
        ret z
        ld b, a
        inc hl
sne_1:   push hl
        inc hl
        inc hl
        inc hl                      ; x0
        ld a, e
        cp (hl)
        jr c, sne_n                  ; x1 < bx0
        inc hl
        ld a, (hl)
        cp d
        jr c, sne_n                  ; bx1 < x0
        inc hl
        ld a, (tmp + 3)
        cp (hl)
        jr c, sne_n                  ; y1 < by0
        inc hl
        ld a, (hl)
        cp c
        jr c, sne_n                  ; by1 < y0
        pop hl
        push hl
        push de
        ld e, (hl)
        inc hl
        ld d, (hl)
        inc hl
        push de
        pop iy
        pop de
        ld a, (iy + E_TYPE)
        inc a
        jr z, sne_n                  ; the slot is free now
        ld a, (iy + E_GEN)
        cp (hl)
        jr nz, sne_n                 ; another enemy in the slot
        pop hl
        or 1
        ret
sne_n:   pop hl
        ld a, l
        add a, SE_SZ
        ld l, a
        jr nc, sne_2
        inc h
sne_2:   djnz sne_1
        xor a
        ret

; ---------------------------------------------------------------- draw
DL_BUSY:    equ 10          ; no explosion shards from this many objects on

build_draw:
        ; the ship (blinking while invulnerable)
        ld a, (pl_gone)
        or a
        jr nz, bd_e
        ld a, (pl_dead)
        or a
        jr nz, bd_e
        ld a, (pl_invul)
        or a
        jr z, bd_p
        ld a, (st_tick)
        and 2
        jr z, bd_e
bd_p:   ld a, (pl_att)
        ld e, a
        ld a, ATT_PLAYER
        call att_mat
        push hl
        ld hl, (pl_y)
        call px_of
        ld b, h
        ld c, l
        ld hl, (pl_x)
        call px_of
        ex de, hl
        pop hl
        ld a, 11                    ; player_game: the faces it can show in play
        call dl_add
bd_e:   ; enemies (blinking for a few ticks after a hit)
        ld iy, enem
        ld b, EN_MAX
bd_1:   ld a, (iy + E_TYPE)
        cp 0xFF
        jr z, bd_1n
        ld a, (iy + E_FLASH)
        or a
        jr z, bd_1a
        dec (iy + E_FLASH)
        ld a, (st_tick)
        and 1
        jr nz, bd_1n
bd_1a:  push bc
        ld a, (iy + E_ATT)
        call mul18
        ld e, (iy + E_ATAB)
        ld d, (iy + E_ATAB + 1)
        add hl, de
        ld c, (iy + E_PYW)
        ld b, (iy + E_PYW + 1)
        ld e, (iy + E_PX)
        ld d, (iy + E_PX + 1)
        ld a, (iy + E_MODEL)
        call dl_add
        pop bc
bd_1n:  ld de, EN_SZ
        add iy, de
        djnz bd_1
        ; debris: every shard every other frame (even slots on even ticks,
        ; odd slots on odd ticks): they sparkle, and an explosion costs geo3d
        ; half the objects; none once DL_BUSY objects are listed (a busy
        ; frame would miss its flip)
        ld iy, debris
        ld b, DB_MAX
bd_2:   ld a, (iy + 0)
        cp 0xFF
        jr z, bd_2n
        ld a, (dl_n)
        cp DL_BUSY
        ret nc
        ld a, (st_tick)
        xor b
        rrca
        jr c, bd_2n
        push bc
        ld a, (iy + 7)
        call mul18
        ld de, att_debris
        add hl, de
        push hl
        ld l, (iy + 3)
        ld h, (iy + 4)
        call px_of
        ld b, h
        ld c, l
        ld l, (iy + 1)
        ld h, (iy + 2)
        call px_of
        ex de, hl
        pop hl
        ld a, (iy + 0)
        call dl_add
        pop bc
bd_2n:  ld de, DB_SZ
        add iy, de
        djnz bd_2
        ret

; the texts of this tick
build_text:
        ld a, (st_mode)
        cp MODE_OVER
        jr nz, bt_1
        ld a, MSG_GAME
        ld d, 100
        ld e, 90
        ld c, 0xFF
        call tx_add
        ld a, MSG_OVER
        ld d, 131
        ld e, 90
        ld c, 0xFF
        jp tx_add                   ; (GAME OVER alone)
bt_1:   ld a, (st_mode)
        cp MODE_DEMO
        jr nz, bt_2
        ld a, (st_tick)
        and 16
        jr nz, bt_2
        ld a, MSG_DEMO
        ld e, 140
        call tx_centre
bt_2:   ld a, (txt_t)
        or a
        ret z
        ld a, (txt_msg)
        cp MSG_ROUND
        jr z, bt_round
        ld e, 70
        jp tx_centre
bt_round:
        ; "ROUND" and the number (1-99)
        ld a, (st_round)
        inc a
        cp 100
        jr c, bt_r1
        ld a, 99
bt_r1:  ld b, 0
bt_r2:  cp 10
        jr c, bt_r3
        sub 10
        inc b
        jr bt_r2
bt_r3:  ld c, a                     ; units
        ld a, b
        or a
        ld d, 128 - 43 / 2          ; one digit: 31 + 6 + 6 = 43 wide
        jr z, bt_r4
        ld d, 128 - 49 / 2
bt_r4:  push bc
        push de
        ld a, MSG_ROUND
        ld e, 70
        ld c, 0xFF
        call tx_add
        pop de
        pop bc
        ld a, d
        add a, 36
        ld d, a
        ld e, 70
        ld a, b
        or a
        jr z, bt_r5
        push bc
        ld c, b                     ; "0123456789": digit k at k
        ld a, MSG_DIGITS
        call tx_add
        pop bc
        ld a, d
        add a, 6
        ld d, a
bt_r5:  ld a, MSG_DIGITS
        ld e, 70
        jp tx_add

; ---------------------------------------------------------------- demo
; level.py's pilot: follow the nearest enemy ahead in y, keep x near 56,
; dodge bullets close ahead, fire all the time. A = keys.
autopilot:
        ld hl, (pl_x)
        call px_of
        ld a, l
        ld (tmp + 8), a             ; ship x
        ld hl, (pl_y)
        call px_of
        ld a, l
        ld (tmp + 9), a             ; ship y
        ld a, 96
        ld (tmp + 10), a            ; target y
        ld a, 255
        ld (tmp + 11), a            ; nearest x so far
        ld iy, enem
        ld b, EN_MAX
ap_1:   ld a, (iy + E_TYPE)
        cp 0xFF
        jr z, ap_1n
        ld a, (iy + E_ON)
        or a
        jr z, ap_1n
        ld a, (tmp + 8)
        add a, 16
        jr c, ap_1n
        ld c, a
        ld a, (iy + E_PX)
        cp c
        jr c, ap_1n                 ; not ahead
        ld hl, tmp + 11
        cp (hl)
        jr nc, ap_1n
        ld (hl), a
        ld a, (iy + E_PY)
        ld (tmp + 10), a
ap_1n:  ld de, EN_SZ
        add iy, de
        djnz ap_1
        ; up / down towards the target
        ld c, 0
        ld a, (tmp + 9)
        ld hl, tmp + 10
        sub (hl)                    ; y - target
        jr c, ap_dn
        cp 3
        jr c, ap_2
        set 0, c                    ; up
        jr ap_2
ap_dn:  neg
        cp 3
        jr c, ap_2
        set 1, c                    ; down
ap_2:
   ; dodge: a bullet 0..48 px ahead, within 12 px in y
        ld ix, bullets
        ld b, BU_MAX
ap_3:   ld a, (ix + 0)
        or a
        jr z, ap_3n
        ld a, (ix + 8)
        or a
        jr nz, ap_3n
        ld a, (ix + 7)
        ld hl, tmp + 8
        sub (hl)
        jr c, ap_3n
        jr z, ap_3n
        cp 48
        jr nc, ap_3n
        ld l, (ix + 9)
        ld a, (tmp + 9)
        sub l                       ; ship y - bullet y
        add a, 11
        cp 23
        jr nc, ap_3n
        ; bullet below (or level): up, else down
        ld a, (tmp + 9)
        cp l
        ld c, 1                     ; the bullet below: up
        jr c, ap_3a
        ld c, 2                     ; above or level: down
ap_3a:  ld a, (tmp + 9)
        cp 40
        jr nc, ap_3b
        ld c, 2
ap_3b:  cp 141
        jr c, ap_3n
        ld c, 1
ap_3n:  ld de, BU_SZ
        add ix, de
        djnz ap_3
        ; x: towards 48..64
        ld a, (tmp + 8)
        cp 65
        jr c, ap_4
        set 2, c
ap_4:   cp 48
        jr nc, ap_5
        set 3, c
ap_5:   ld a, c
        or 0x10                     ; fire
        ret

; ---------------------------------------------------------------- debug
; st_dbg, written by the test harness (never by the game):
;   1  an enemy of type st_dbga at (st_dbgx * 2, st_dbgy), path LINE
;   2  an enemy bullet at (st_dbgx * 2, st_dbgy) moving left at 3 px/tick
;   3  the ship can be hit now (invulnerability off)
;   4  lives = st_dbga
;   5  score = 019900 (the next kill passes the first extra life)
;   6  the ship cannot be hit until the next game (soak tests)
;   7  the autopilot flies the ship in this game (soak tests)
debug_cmd:
        ld a, (st_dbg)
        or a
        ret z
        ld b, a
        xor a
        ld (st_dbg), a
        ld a, b
        cp 1
        jr nz, dc_2
        ld a, (st_dbga)
        and 3
        ld (tmp + 0), a
        ld a, PATH_LINE
        ld (tmp + 1), a
        ld a, (st_dbgx)
        ld (tmp + 2), a
        ld a, (st_dbgy)
        ld (tmp + 3), a
        jp spawn_enemy
dc_2:   cp 2
        jr nz, dc_3
        ld ix, bullets
        ld b, BU_MAX
dc_2a:  ld a, (ix + 0)
        or a
        jr z, dc_2b
        ld de, BU_SZ
        add ix, de
        djnz dc_2a
        ret
dc_2b:  ld a, (st_dbgx)
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        ld (ix + 1), l
        ld (ix + 2), h
        ld a, (st_dbgy)
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        ld (ix + 3), l
        ld (ix + 4), h
        ld (ix + 5), -48
        ld (ix + 6), 0
        ld (ix + 0), 1
        call new_serial
        ld (ix + 10), a
        ret
dc_3:   cp 3
        jr nz, dc_4
        xor a
        ld (pl_invul), a
        ret
dc_4:   cp 4
        jr nz, dc_5
        ld a, (st_dbga)
        ld (st_lives), a
        jp hud_refresh
dc_5:   cp 5
        jr nz, dc_6
        ld hl, 0x9900               ; score 019900 (BCD, low byte first)
        ld (st_score), hl
        ld a, 0x01
        ld (st_score + 2), a
        jp hud_refresh
dc_6:   cp 6
        jr nz, dc_7
        ld a, 1
        ld (god), a
        ret
dc_7:   cp 7
        ret nz
        ld a, 1
        ld (demo), a                ; the autopilot flies (the mode stays)
        ret
