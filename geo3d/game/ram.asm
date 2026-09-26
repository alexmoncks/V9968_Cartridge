; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; ram.asm - RAM map of the shooter (page 3, C000h-DFFFh; stack below F300h)
;
; Assembled last in shooter.asm at org C000h: `ds` only reserves addresses
; (build.sh cuts the output at the end of the ROM, so nothing here reaches
; the ROM image). The status block at C000h has fixed addresses: the test
; harness (tests/game.tcl) reads it; keep it first and keep its order.
; ============================================================================

        org 0xC000
ram_start:
; ---- status block (read by tests/game.tcl, fixed addresses) ----------------
st_magic:   ds 2        ; "G3" once init is done
st_mode:    ds 1        ; MODE_* below
st_tick:    ds 2        ; frames (game ticks) since init
st_vbl:     ds 2        ; V9968 vertical blanks seen by the ISR
st_flips:   ds 2        ; page flips done by the ISR
st_late:    ds 2        ; flips more than 2 blanks after the previous one
st_hwcoll:  ds 1        ; 1: hardware transparent-sprite collisions (probe)
st_cev:     ds 2        ; ticks that saw a flagged page (S#0 bit 5 or 6)
st_cx:      ds 2        ; last collision point (S#3/S#4 - 12)
st_cy:      ds 2        ;   and (S#5/S#6 - 8)
st_kills:   ds 2        ; enemies destroyed
st_hits:    ds 2        ; bolts that hit an enemy
st_deaths:  ds 2        ; player deaths
st_lives:   ds 1
st_score:   ds 3        ; BCD, low byte first
st_round:   ds 1        ; 0 based
st_phase:   ds 1        ; 0 stars, 1 bg enters, 2 fg enters, 3 play
st_fmark:   ds 1        ; frame progress marker (the harness times its writes)
st_probe:   ds 1        ; bit0: control pair collided, bit1: colour-0 hitbox collided
st_enem:    ds 1        ; enemies alive
st_objs:    ds 1        ; geo3d objects drawn in the last frame
st_fbytes:  ds 2        ; face bytes uploaded in the last frame
st_hi:      ds 3        ; hi-score, BCD
st_geo:     ds 1        ; 1: geo3d answered at init
st_dbg:     ds 1        ; debug command from the harness (0: none), see game.asm
st_dbgx:    ds 1        ;   its arguments
st_dbgy:    ds 1
st_dbga:    ds 1
st_nexev:   ds 2        ; box tests run (one per tested tick snapshot)
st_events:  ds 2        ; narrow-phase hits found (bolt, bullet, ram)
st_seed:    ds 2        ; the random generator
st_blank:   ds 2        ; blanks between the last two flips
st_work:    ds 2        ; blanks from frame start to flip request (last frame)
st_maxwork: ds 2        ; the most blanks a frame's work took
st_imark:   ds 1        ; ISR progress marker (50h in, 51h out)
st_pad0:    ds 7
; ---- ISR / frame ----------------------------------------------------------
eiop:       ds 2        ; EI (or NOP), RET: interrupts back on after a DI sequence
vcount:     ds 2        ; blanks
lastflip:   ds 2        ; vcount at the last flip
flip_req:   ds 1        ; 1: the page `flip_r2` is ready, the ISR flips it
flip_r2:    ds 1        ; R#2 value
flip_r5:    ds 1        ; R#5 value (the sprite attribute table of that page)
flip_bit:   ds 1        ; 1 << (tick & 3) of that page
shown_bit:  ds 1        ; the same for the page on show (the ISR copies it at the flip)
i_cmask:    ds 1        ; pages (shown_bit) that raised S#0 bit 5 or 6 since the logic looked
i_cx:       ds 2        ; the last collision point (S#3-S#6)
i_cy:       ds 2
in_cur:     ds 1        ; keys now: bit0 up 1 down 2 left 3 right 4 fire 5 esc
in_edge:    ds 1        ; keys pressed since the logic last looked
in_prev:    ds 1
psg_r7:     ds 1        ; PSG mixer shadow
sfx_ch:     ds 12       ; per channel: next frame (2, 0 idle), priority, request (id + 1)
buf:        ds 1        ; page being drawn (0/1)
frame_v0:   ds 2        ; vcount when the frame started
; ---- VDP command queue ----------------------------------------------------
Q_MAX:      equ 10
q_n:        ds 1        ; commands queued
q_i:        ds 1        ; next to issue
q_wp:       ds 2        ; where the next one is written
q_rp:       ds 2        ; the next one to send
q_buf:      ds Q_MAX * 15
cmdbuf:     ds 15
; ---- composition ----------------------------------------------------------
s_bg:       ds 2        ; background scroll, 1/16 px (signed, < 0 while it enters)
s_fg:       ds 2        ; foreground scroll
s_st:       ds 6        ; star layers' scroll (1/16 px, wraps at 4096)
v_bg:       ds 1        ; speeds, 1/16 px per tick
v_fg:       ds 1
v_st:       ds 1
t_bg:       ds 1        ; target speeds (SPEED)
t_fg:       ds 1
t_st:       ds 1
eb:         ds 2        ; background edge: columns 0..eb-1 show stars
ef:         ds 2        ; foreground edge
lx:         ds 2        ; the layer's x at its edge
rc_x0:      ds 2        ; ring copy parameters
rc_dx:      ds 2
rc_w:       ds 2
rc_sy:      ds 2
rc_dy:      ds 2
rc_ny:      ds 2
rc_two:     ds 1
rc_cmd:     ds 1
rc_len1:    ds 2
g3_h:       ds 2        ; geo3d H of this frame (212, or 164 with the band)
; ---- geo3d draw list --------------------------------------------------------
DL_MAX:     equ 40
DL_SZ:      equ 8       ; next, model, matrix (2), x (2), y (2)
dl_n:       ds 1
dl_wp:      ds 2        ; the next free entry
dl_head:    ds 12       ; per model: first entry (FFh: none)
dl_tail:    ds 12       ; per model: last entry
dl_buf:     ds DL_MAX * DL_SZ
cur_model:  ds 1        ; model whose faces are in geo3d's face RAM (FFh: none)
cfgbuf:     ds 24       ; matrix + TX, TY, TZ of the next object
dl_big:     ds 1        ; 1: draw at TZ = 512 (the title ship)
fbytes:     ds 2
nobj:       ds 1
g_ord:      ds 2        ; geo_pump: next model in draw_order
g_ent:      ds 1        ;   next entry of the current model (FFh: next model)
g_done:     ds 1        ;   1: every object sent
; ---- text overlays ----------------------------------------------------------
TX_MAX:     equ 6
tx_n:       ds 1
tx_buf:     ds TX_MAX * 4   ; message, x, y, char offset (digits)
txt_rect:   ds 16 * 4       ; per message: SY (2), NX, NY in page 7
txt_y:      ds 2            ; next free line in page 7 while rendering
; ---- sprites ----------------------------------------------------------------
sat:        ds 128          ; 32 planes x (Y, X, pattern, 0)
fx_col:     ds 8            ; per page, per FX plane: the flash kind (0-2) in its colour table
fx_want:    ds 4            ; the kinds this frame needs
hud_dirty:  ds 2            ; per page: the HUD patterns need redrawing
hud_mode:   ds 1            ; 0 score + lives, 1 hi-score (title)
hudtxt:     ds 8            ; the 8 glyph codes of the HUD
; ---- game -------------------------------------------------------------------
gmode_t:    ds 2            ; ticks in this mode
demo:       ds 1            ; 1: attract demo (autopilot)
keys:       ds 1            ; this tick's controls (bit0 up 1 down 2 left 3 right 4 fire)
sc_pc:      ds 2            ; script: next op
sc_wait:    ds 1
sc_loop:    ds 2
sc_clear:   ds 1            ; CLEAR: ticks left / 4 ... counted in ticks (2 bytes)
sc_clr2:    ds 2
sc_inclear: ds 1
txt_msg:    ds 1            ; TEXT: message shown (FFh none)
txt_t:      ds 1            ;   ticks left
next_extra: ds 3            ; BCD score of the next extra life
; player
pl_x:       ds 2            ; 1/16 px
pl_y:       ds 2
pl_att:     ds 1            ; 0-16 (8 level)
pl_fire:    ds 1            ; cooldown
pl_in:      ds 1            ; fly-in ticks left
pl_dead:    ds 1            ; respawn ticks left (0: alive)
pl_invul:   ds 1            ; invulnerable ticks left
pl_vuln:    ds 1            ; 1: can be hit this tick
pl_blink:   ds 1
; pending formation members
PD_MAX:     equ 16
PD_SZ:      equ 6           ; delay, type, path, x/2, y, used
pend:       ds PD_MAX * PD_SZ
pend_n:     ds 1            ; entries waiting
; enemies
EN_MAX:     equ 11
EN_SZ:      equ 48
; field offsets (the type's data is copied in at spawn: E_MODEL..E_SDX)
E_TYPE:     equ 0           ; FFh: free slot
E_X:        equ 1           ; 2, 1/16 px
E_Y:        equ 3           ; 2
E_VX:       equ 5           ; 2
E_VY:       equ 7           ; 2
E_Y0:       equ 9           ; 2
E_AGE:      equ 11          ; 2
E_HP:       equ 13
E_ATT:      equ 14
E_FIRE:     equ 15
E_BURST:    equ 16
E_PATH:     equ 17
E_P:        equ 18          ; hover phase
E_LIFE:     equ 19          ; 2
E_FLASH:    equ 21          ; hit flash ticks
E_PX:       equ 22          ; 2: x in pixels (this tick)
E_PY:       equ 24          ; 1: y in pixels (0-255, clamped)
E_ON:       equ 25          ; 1: on screen (collidable)
; (26-29 free: the box is in the tick's collision snapshot)
E_GEN:      equ 30          ; generation (spawn_enemy)
E_MODEL:    equ 31          ; geo3d model
E_ATAB:     equ 32          ; 2: its attitude table
E_ATN:      equ 34          ;    number of attitudes
E_AMODE:    equ 35          ;    attitude mode (0 bank, 1 turn, 2 turn slowly)
E_FKIND:    equ 36          ; fire kind (0 none, 1 aimed, 2 spread, 3 burst)
E_FPER:     equ 37          ; fire period
E_FIRST:    equ 38          ; first round that fires
E_NBOX:     equ 39          ; hitbox sprites (1-2)
E_PAT0:     equ 40          ;   their patterns
E_PAT1:     equ 41
E_PPTR:     equ 42          ; 2: its path's entry in path_table
E_SDX:      equ 44          ; bullet x offset (pixels, signed)
E_PYW:      equ 46          ; 2: y in pixels (this tick, signed)
; (E_GEN: the slot's spawn number, 1-255: a collision snapshot names an
; enemy by slot and generation)
enem:       ds EN_MAX * EN_SZ
en_gen:     ds 1            ; the last generation given
; bolts (player) and enemy bullets: contiguous (clear_objects, snapshots);
; serial: 1-255 at the shot, kept when it flies off, 0 once it hit
BO_MAX:     equ 4
BO_SZ:      equ 4           ; active, x (px), y (px), serial
bolts:      ds BO_MAX * BO_SZ
BU_MAX:     equ 6
BU_SZ:      equ 11          ; active, x (2, 1/16), y (2, 1/16), vx, vy, x px (2), y px, serial
bullets:    ds BU_MAX * BU_SZ
obj_serial: ds 1            ; the last serial given
; debris (geo3d shards)
DB_MAX:     equ 8
DB_SZ:      equ 10          ; model (FFh free), x (2, 1/16), y (2), vx, vy, att, life, -
debris:     ds DB_MAX * DB_SZ
db_next:    ds 1
; flashes (sprites)
FL_MAX:     equ 4
FL_SZ:      equ 4           ; active, x (px), y (px), age
flashes:    ds FL_MAX * FL_SZ
; collision snapshots (game.asm collide): slots 0-3 hold the last 4 ticks
; (the hardware path, slot tick & 3), slot 4 this tick (the software path)
SN_BB:      equ 0           ; bolts and bullets, a copy of the RAM arrays
SN_PL:      equ BO_MAX * BO_SZ + BU_MAX * BU_SZ     ; ship: vuln (its sprites on), x px, y px
SN_EN:      equ SN_PL + 3   ; enemies on the screen: n, then n entries
SE_SZ:      equ 7           ; entry: slot address (2), generation, box x0, x1, y0, y1
SN_SZ:      equ SN_EN + 1 + EN_MAX * SE_SZ
snaps:      ds 5 * SN_SZ
sn_bb:      ds 2            ; sn_test: the bolts and bullets of the tested tick
sn_p:       ds 2            ;   the shot in them
sn_q:       ds 2            ;   the same slot in RAM
sn_mask:    ds 1            ; collide: the flagged pages
sn_done:    ds 1            ;   the slots tested since they were saved
en_np:      ds 2            ; this tick's enemy list: its count byte
en_wp:      ds 2            ;   the next entry
demo_end:   ds 1            ; demo: ticks left after the ship blew up (0: none)
god:        ds 1            ; 1: the ship cannot be hit (debug command 6)
pl_gone:    ds 1            ; 1: no ship (game over)
ex_vx:      ds 1            ; explode: the shards. base x speed
ex_col:     ds 1            ;   their model base (5 warm, 8 blue)
ex_n:       ds 1
sh_kind:    ds 1            ; enemy_shoot: fire kind
sh_speed:   ds 1
bvel_tab:   ds 64           ; enemy bullet vx, vy per direction (this round)
div3_tab:   ds 256          ; floor(v / 3) for v = -128..127 (index v & 255)
anchors:    ds 64           ; per sprite pattern: anchor x, y (index pattern / 2)
; scratch
tmp:        ds 16
aim_dx:     ds 2
aim_dy:     ds 2
; text canvas (init only): 256 x 18 pixels
canvas:     ds 128 * 18
ram_end:
