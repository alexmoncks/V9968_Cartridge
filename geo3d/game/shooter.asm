; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; shooter.asm - VECTOR RAID, a side-scrolling shooter for the V9968 + geo3d
;
; MSX MegaROM, ASCII16 mapper, 64 KB:
;   bank 0  page 1 (4000h-7FFFh), fixed: the code (about 10.5 KB)
;   bank 1  page 2 (8000h-BFFFh) while the game runs: palette, models,
;           attitudes, sprites, level, sound effects (about 9.7 KB)
;   bank 2  page 2 during init only: the packed tiles (11.9 KB)
;   bank 3  empty
; (build.sh prints the free space of each bank)
; Ports come from out/ports.asm (build.sh): PORT_BASE 98h (openMSX, the
; V9968 is the machine's VDP) or 88h (the V9968 cartridge next to the
; internal VDP). geo3d sits at PORT_BASE + 5 (index/status) and + 7 (data).
;
; Every frame (30 per second, one page flip every 2 vertical blanks) the
; hidden page is composed from scratch by the V9968 command engine: stars,
; the background window (HMMM, from a copy shifted by one pixel at odd
; offsets), the foreground band (LMMM + TIMP: colour 0 is a hole). The
; command queue (vdp.asm) is fed while the game logic runs. Then geo3d
; draws the ship, the enemies and the debris as filled, shaded 3D models
; (fed between the steps that build the sprites), the text overlays go on
; top (LMMM + TIMP from page 7), the sprites go to the hidden attribute
; table, and the interrupt handler flips page and sprite table together in
; the vertical blank.
; Collisions: invisible hitbox sprites under the visible bolts, bullets
; and the ship's core; the handler notes which tick's page raised S#0 bit
; 5 (or bit 6, a sprite dropped from a full line), and that tick's
; snapshot of the positions gets the box test (game.asm, collide). A probe
; at power on tells whether the VDP reports colour-0 hitboxes (the V9968
; does, the openMSX fork does not yet): without it the box test runs on
; every tick's own positions.
; See README.txt and geo3d/game/tools for the data.
; ============================================================================

        include "out/ports.asm"      ; PORT_BASE (build.sh)

VDP_DATA:   equ PORT_BASE
VDP_CTRL:   equ PORT_BASE + 1
VDP_PAL:    equ PORT_BASE + 2
VDP_IND:    equ PORT_BASE + 3
VDP_P4:     equ PORT_BASE + 4
GEO_IDX:    equ PORT_BASE + 5
GEO_DAT:    equ PORT_BASE + 7

PPI_B:      equ 0xA9
PPI_C:      equ 0xAA
PSG_A:      equ 0xA0
PSG_W:      equ 0xA1
PSG_R:      equ 0xA2

ENASLT:     equ 0x0024
RSLREG:     equ 0x0138
EXPTBL:     equ 0xFCC1
RG1SAV:     equ 0xF3E0
H_KEYI:     equ 0xFD9A
BANK1_SEL:  equ 0x6000          ; ASCII16: bank of 4000h-7FFFh
BANK2_SEL:  equ 0x7000          ; ASCII16: bank of 8000h-BFFFh

HMMV:       equ 0xC0
HMMM:       equ 0xD0
LMMM:       equ 0x90
TIMP:       equ 0x08

MODE_TITLE: equ 1
MODE_GAME:  equ 2
MODE_DEMO:  equ 3
MODE_OVER:  equ 4

; ============================================================================
; bank 0
; ============================================================================
        org 0x4000
        db "AB"
        dw init
        dw 0, 0, 0
        ds 6, 0

init:
        di
        ld sp, 0xF300
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
        ld h, 0x80
        call ENASLT
        di
        xor a
        ld (BANK1_SEL), a
        ld a, 1
        ld (BANK2_SEL), a
        ; clear RAM C000h-E7FFh
        ld hl, 0xC000
        ld de, 0xC001
        ld bc, 0x27FF
        ld (hl), 0
        ldir
        ld hl, 0xC900               ; NOP, RET: interrupts stay off during init
        ld (eiop), hl
        call psg_init
        if PORT_BASE = 0x88
        ; the internal VDP: no interrupts (its BIOS keyboard scan is not needed)
        ld a, (RG1SAV)
        and 0xDF
        out (0x99), a
        ld a, 0x81
        out (0x99), a
        in a, (0x99)
        endif
        call vdp_init
        call load_tiles
        call make_shifted
        call spr_init
        call text_init
        call geo_init
        call coll_probe
        ; the real palette
        xor a
        ld b, 16
        call wreg
        ld hl, palette
        ld bc, 32 * 256 + VDP_PAL
        otir
        ; the interrupt handler (H.KEYI: every interrupt, before the BIOS)
        ld a, 0xC3
        ld (H_KEYI), a
        ld hl, isr
        ld (H_KEYI + 1), hl
        ld hl, 0xC9FB               ; EI, RET
        ld (eiop), hl
        ld a, 'G'
        ld (st_magic), a
        ld a, '3'
        ld (st_magic + 1), a
        ld hl, 0x0000
        ld (st_hi), hl
        ld a, 0
        ld (st_hi + 2), a
        ld hl, R_DEMO_SEED
        ld (st_seed), hl
        ld a, 0x01
        ld (st_hi + 2), a           ; hi-score 010000
        call game_init
        call title_init
        ld a, 1
        ld (buf), a                 ; page 0 is shown, draw page 1 first
        ld a, 0x62                  ; display on, IE0, 16x16 sprites
        ld b, 1
        call wreg                   ; (interrupts on from here: eiop)

; ----------------------------------------------------------------------------
; the frame loop: one game tick and one page per frame. st_fmark marks the
; steps (tests/game.tcl times its writes with FMARK=1): 30 frame start,
; 3A layers queued, 3B script, 3C scroll and spawns, 40 controls, 41 ship
; and enemies, 42 shots and effects, 43 collisions, 44 draw list, 45 texts
; listed, 31 layers done, 34 stars done, 46 sprites built, 35 sprite tables
; sent, 36 geo3d done, 37/38 text overlays queued/done, 32 VDP idle,
; 33 flip asked.
main_loop:
        call frame_begin
        call logic
        call frame_end
        jr main_loop

; wait for the ISR to show the page drawn last, then start composing the
; other one
frame_begin:
fb_w:   ld a, (flip_req)
        or a
        jr nz, fb_w
        ld a, 0x30
        ld (st_fmark), a
        ld hl, (vcount)
        ld (frame_v0), hl
        call compose_begin
        ld a, 0x3A
        ld (st_fmark), a
        ret

; finish the page: layers done, stars, geo3d objects, text, sprites; ask
; the ISR to flip it in the next vertical blank that is 2 after the last flip
frame_end:
        call q_flush
        ld a, 0x31
        ld (st_fmark), a
        call stars_draw
        ld a, 0x34
        ld (st_fmark), a
        call geo_start
        call geo_pump
        call spr_build              ; (these two call geo_pump between steps)
        ld a, 0x46
        ld (st_fmark), a
        call spr_upload
        ld a, 0x35
        ld (st_fmark), a
        call geo_finish
        ld a, 0x36
        ld (st_fmark), a
        call text_draw
        call wait_ce
        ld a, 0x32
        ld (st_fmark), a
        ld a, (buf)
        rrca
        rrca
        rrca
        or 0x1F
        ld (flip_r2), a
        ld a, (buf)
        or a
        ld a, 0xEF                  ; page 0: attributes at 7600h
        jr z, fe_1
        ld a, 0xE7                  ; page 1: attributes at 7200h
fe_1:   ld (flip_r5), a
        ld a, (st_tick)             ; the page's tick (collide: its snapshot)
        and 3
        ld e, a
        ld d, 0
        ld hl, bit_tab
        add hl, de
        ld a, (hl)
        ld (flip_bit), a
        ld hl, (vcount)
        ld de, (frame_v0)
        or a
        sbc hl, de
        ld (st_work), hl
        ld de, (st_maxwork)
        ex de, hl
        or a
        sbc hl, de
        jr nc, fe_2
        ld (st_maxwork), de
fe_2:   ld a, 1
        ld (flip_req), a
        ld a, (buf)
        xor 1
        ld (buf), a
        ld hl, (st_tick)
        inc hl
        ld (st_tick), hl
        ld a, 0x33
        ld (st_fmark), a
        ret

; ----------------------------------------------------------------------------
logic:
        call dl_reset
        xor a
        ld (tx_n), a
        ld a, (st_mode)
        cp MODE_TITLE
        jp z, title_tick
        jp game_tick

; 1 << n, n = tick & 3 (the page's bit for the collision snapshots)
bit_tab:
        db 1, 2, 4, 8

        include "vdp.asm"
        include "isr.asm"
        include "compose.asm"
        include "geo.asm"
        include "text.asm"
        include "spr.asm"
        include "game.asm"

bank0_end:
        ds 0x8000 - $, 0xFF

; ============================================================================
; bank 1: data (and code that did not fit bank 0)
; ============================================================================
        org 0x8000
        include "out/inc/palette.asm"
        include "out/inc/models.asm"
        include "out/inc/attitudes.asm"
        include "out/inc/sprites.asm"
        include "out/inc/level.asm"
        include "out/inc/sfx.asm"
bank1_end:
        ds 0xC000 - $, 0xFF

; ============================================================================
; bank 2: the tiles (page 2 during init only)
; ============================================================================
        org 0x8000
tiles_bank:
        include "out/inc/tiles_bg_l.asm"
        include "out/inc/tiles_bg_r.asm"
        include "out/inc/tiles_fg.asm"
bank2_end:
        ds 0xC000 - $, 0xFF

; ============================================================================
; bank 3: empty
; ============================================================================
        org 0x8000
        ds 0x4000, 0xFF

; ============================================================================
; RAM (addresses only: build.sh cuts the image at 64 KB)
; ============================================================================
        include "ram.asm"
