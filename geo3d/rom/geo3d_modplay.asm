; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; geo3d_modplay.asm  -  ProTracker MOD player for the MoonSound's wave part
;
; Plays a MOD file as a MOD player does: the Z80 reads the MOD's pattern data
; from ROM row by row and runs ProTracker's effects tick by tick, at the
; song's own speed and BPM (the OPL4's FM timer 2, polled at 0C4h), not at
; the video frame rate. The sound is the MOD's own 8-bit samples, played by
; the OPL4 wave part (YMF278B, ports 7Eh/7Fh) from the MoonSound's sample
; RAM, where this module uploads them. modplay.py models this module
; exactly (class Rt) and documents the ProTracker behaviour it follows.
; Include this file in page 1 code, after defining:
;
;   MP_RAM          1505 bytes of RAM (page 3) for the player
;   mp_setbank      routine: A = ROM bank, mapped at 8000h-BFFFh; may change
;                   AF only (the geo3d player: bank2_raw)
;   mp_restbank     routine: maps the host's own page 2 bank back; may change
;                   AF only (geo3d: ld a, (curbank) / jp bank2_raw)
;   and "rom_mod.asm" (build_rom.py / modplay.py write it): MOD_SONG, the
;   MOD (MP_MOD_BANK, MP_MOD_ADDR: the file's bytes, on through the next
;   banks, 8000h again after BFFFh), the lookup tables (MP_TAB_BANK,
;   MP_TAB_ADDR: one block in one bank, offsets MP_T_*; after them, at
;   MP_T_TT, the MOD's MP_NTT 9xx start offsets that need a tone of their
;   own: sample, start (2), found by modplay.py's scan of the song at build
;   time), MP_LOOP (1: the
;   song plays for ever, looping at its end as ProTracker does: no fade and
;   no END; the demo ROM) or the fade (MP_FADE0, MP_FSTEP, in timer 2
;   steps; the test ROM), and MP_NULL (1: a measure, build_rom.py
;   --null-mod: mod_start leaves timer 2 off, so the player's polls run
;   and cost what they cost, but no tick ever comes).
;
; The MOD: 31 samples, the tag at 1080 (M.K., M!K!, FLT4: 4 channels, 1CHN
; to 8CHN), song length, order table, patterns, sample data, as the file
; has them. Effects: 0 arpeggio, 1/2 porta, 3 tone porta, 4 vibrato, 5 porta
; + slide, 6 vibrato + slide, 7 tremolo, 9 offset (with ProTracker's
; stacking), A slide, B jump, C volume, D break, E1/E2 fine porta, E4
; vibrato waveform, E5 finetune, E6 loop, E7 tremolo waveform, E9 retrigger,
; EA/EB fine slides, EC cut, ED delay, EE row delay, F speed / tempo; with
; ProTracker 2's rules where they matter: the last EEx of a row counts, a
; jump or break after a row delay skips its target row, a Bxx after a Dxx
; clears its row, the song's end goes on at position 0 on the row a Dxx
; gave, EDx and E9x without a note play on each EEx repeat, 9xx without a
; note moves the stacked offset, E5x without a note sets the finetune, E9x
; with a lone instrument number retriggers that sample, a lone empty
; sample sets its volume; an arpeggio's base step plays the period as it is
; (off the table after a slide), its steps and ECx's tick count start again
; on each EEx repeat, and a tempo takes effect from the row's second tick,
; an EEx repeat's too (four cases OpenMPT plays otherwise: modplay.py).
; Ignored: 8, E0/E8, F00 (E3x and EFx the build
; refuses where they would be heard); the play position of a sample is not
; followed (no instrument swap at a loop end, no sample started by a lone
; instrument number after a one-shot ran out). build_rom.py only puts in
; the ROM a MOD this module plays as ProTracker does.
;
; Entry points. mod_reset and mod_upload_step are safe without a MoonSound;
; mod_detect (or mod_upload_later's first unit) comes before the others,
; which need it to have succeeded. Interrupts stay disabled while the music
; plays (the timer's IRQ line is up from each overflow until mod_poll clears
; the flag; stop before an EI). Times for a 3.58 MHz Z80 with the MSX's M1
; wait.
;   mod_reset       nothing playing, nothing to upload (no port access).
;   mod_detect      A = 128 KB blocks of sample RAM (0: no MoonSound, or a
;                   MOD this module does not take), carry set if fewer than
;                   the MOD's samples need: play the fallback music then.
;                   Reads the MOD's header (samples, order table). Sets NEW2
;                   (FM register 105h = 03h), without which the wave part
;                   ignores every write, and leaves it set; with carry 105h
;                   goes back to 0. The FM fallback's 105h = 01h must not run
;                   after a success. About 20 ms. Changes AF, BC, DE, HL, IY.
;   mod_upload_init / mod_upload_step: the upload in steps, e.g. while a
;                   menu waits for its frames. mod_upload_step does A (1-255)
;                   units at most and returns Z once all is there (called
;                   again then, or after mod_reset, it returns Z at once and
;                   touches no port). A unit is 1-2 ms: 8 of the 128 tone
;                   headers it computes from the MOD's sample table and the
;                   9xx start offsets the build found (MP_T_TT, copied to RAM
;                   by mod_upload_init; 200000h: register
;                   2 = 10h puts the headers of tones 384+ there), then up to
;                   256 bytes of sample data (OTIR, 23 T a byte, 1.5 s for
;                   220 KB), each sample as the file has it (a one-shot then
;                   2 zero bytes). The chip plays nothing while its memory is
;                   written (register 2 bit 0), so it ends before mod_start.
;                   Changes AF, BC, DE, HL, IY.
;   mod_upload      the whole upload at once (mod_upload_init first).
;   mod_upload_later: mod_upload_init with mod_detect as the first unit of
;                   mod_upload_step (20 ms), for a host that detects the
;                   MoonSound's FM part at power on and the rest while a
;                   menu waits; mp_ok (RAM) = 1 then if the MOD will play.
;   mod_start       the song from its first tick: NEW2, register 2 = 10h,
;                   every channel off, tick 0 worked out and its notes'
;                   channels prepared, timer 2 started, tick 0 played. About
;                   6 ms. All registers kept.
;   mod_tpoll       the poll for a busy CPU: when timer 2 has run out it
;                   plays the tick (and whatever of its work the polls did
;                   not get to), else nothing (33 T and the CALL). Changes
;                   AF only.
;   mod_poll        call often; changes AF only. When timer 2 has run out it
;                   plays the tick that starts (key ons of all channels
;                   first, key offs, pitch and level changes: its notes'
;                   channels were prepared before) and sets the timer for
;                   the tick after it; then the polls that follow work out
;                   that next tick a piece at a time: the fade and the row
;                   (read from the MOD when a row starts), then each
;                   channel's effects and the preparation of each channel
;                   whose note starts then (pitch, level, and the tone
;                   number, whose header load (LD, 0.3 ms) a later poll waits
;                   for if another is still running). A poll works out one
;                   channel or reads one row (MP_BUDGET): at most ~7.7k T;
;                   with nothing to do a poll takes 59 T (and the CALL).
;                   Work per tick (test_modplay.py --cost): with the crawl's
;                   MOD 9k T median, 11k T mean (15 % of the CPU at 125
;                   BPM), up to ~40k T for a row with a note on each of 4
;                   channels; vibrato or tremolo add about 2k T per channel
;                   and tick (4 channels of them: 16k T mean).
;                   The flag does not count overflows: the polls must come
;                   at least once per MOD tick (20 ms at 125 BPM, 13.5 ms at
;                   185), and the pieces of a tick must fit before the next
;                   one (a tick that comes first finishes them at once: a
;                   host may call mod_poll where it waits and mod_tpoll
;                   where it is busy, as the demo ROM does).
;                   Notes sound when the poll finds the tick: polls P ms
;                   apart delay notes by up to P ms, with no drift (the
;                   timer keeps the time).
;   mod_stop        every channel off (3.6 ms release), timer stopped. The
;                   song's end (its fade, MP_FADE0 / MP_FSTEP) does the same;
;                   a song with MP_LOOP never ends.
;                   All registers kept.
;   mp_on           (RAM) 1 while the music plays.
;
; Channels: MOD channel c plays on OPL4 channels c, c + 8 and c + 16 in
; turn, so the channel of a note can be prepared while the previous note
; still plays. Wave registers per channel k: 08h+k tone bits 7-0 (loads the
; header; LD, status bit 1, for about 300 us), 20h+k F-number 6-0 and tone
; bit 8, 38h+k octave and F-number 9-7, 50h+k level (TL << 1 | direct),
; 68h+k key on, pan. FM bank 0: 03h timer 2, 04h timer control; status at
; C4h: bit 5 FT2, bit 1 LD, bit 0 BUSY (valid with NEW2 = 1).
; ============================================================================

MP_WAVE_A:  equ 0x7E
MP_WAVE_D:  equ 0x7F
MP_FM_A0:   equ 0xC4                ; read: status
MP_FM_D0:   equ 0xC5
MP_FM_A1:   equ 0xC6
MP_FM_D1:   equ 0xC7

MP_MODLIN:  equ MP_MOD_BANK * 16384 + MP_MOD_ADDR - 0x8000    ; the MOD in the ROM image
MP_TAB:     equ MP_TAB_ADDR

; RAM
mp_on:      equ MP_RAM + 0          ; 1: playing
mp_work:    equ MP_RAM + 1          ; the coming tick, next piece: 0 done, 1 fade and
                                    ; row, 2 the channels, 3 the end of the tick
mp_wc:      equ MP_RAM + 2          ; next channel of piece 2
mp_ldreg:   equ MP_RAM + 3          ; a tone number waiting for LD: its register (0 none)
mp_ldval:   equ MP_RAM + 4          ;   and value
mp_nch:     equ MP_RAM + 5          ; channels
mp_rowsz:   equ MP_RAM + 6          ; bytes per row (4 per channel)
mp_songlen: equ MP_RAM + 7
mp_npat:    equ MP_RAM + 8
mp_ticks:   equ MP_RAM + 9          ; 2: ticks played
mp_speed:   equ MP_RAM + 11
mp_bpm:     equ MP_RAM + 12
mp_k:       equ MP_RAM + 13         ; 2: tick of the row (0 = the row's first)
mp_nk:      equ MP_RAM + 15         ; 2: ticks of the row: speed * (1 + row delay)
mp_tr:      equ MP_RAM + 17         ; k mod speed
mp_pos:     equ MP_RAM + 18         ; order position
mp_row:     equ MP_RAM + 19
mp_newrow:  equ MP_RAM + 20         ; 1: the coming tick starts a row
mp_jf:      equ MP_RAM + 21         ; 1: the row jumps (Bxx)
mp_jump:    equ MP_RAM + 22         ;   to this position
mp_brk:     equ MP_RAM + 23         ; Dxx: this row (FFh none)
mp_loopto:  equ MP_RAM + 24         ; E6x: this row (FFh none)
mp_tempo:   equ MP_RAM + 25         ; Fxx >= 20h of the row (0 none)
mp_pdl:     equ MP_RAM + 26         ; EEx of the row (FFh none)
mp_whole:   equ MP_RAM + 27         ; timer 2 steps per tick at mp_bpm: whole
mp_frac:    equ MP_RAM + 28         ; 2: and 16-bit fraction
mp_acc:     equ MP_RAM + 30         ; 2: fraction accumulator
mp_tc:      equ MP_RAM + 32         ; 3: start of the coming tick (timer 2 steps)
mp_cnt:     equ MP_RAM + 35         ; steps of the last period written
mp_g64:     equ MP_RAM + 36         ; the coming tick's level: 64, or less in the fade
mp_fq:      equ MP_RAM + 37         ; fade steps so far
mp_fb:      equ MP_RAM + 38         ; 2: fq * MP_FSTEP
mp_endf:    equ MP_RAM + 40         ; 1: the coming tick is the end (the fade reached 0)
mp_uph:     equ MP_RAM + 41         ; upload: 0 done, 2 headers, 3 samples, 4 mod_detect first
mp_ucnt:    equ MP_RAM + 42         ; units left in this call
mp_uoff:    equ MP_RAM + 43         ; 3: MOD offset of the next sample byte
mp_uram:    equ MP_RAM + 46         ; 3: sample RAM address of the next byte
mp_us:      equ MP_RAM + 49         ; sample being uploaded
mp_uleft:   equ MP_RAM + 50         ; 2: its bytes to go
mp_uz:      equ MP_RAM + 52         ; zero bytes to go after it
mp_ut:      equ MP_RAM + 53         ; next tone header
                                    ; (54, 55: free)
mp_ntone:   equ MP_RAM + 56         ; tones in use: 31 (one per sample) + the 9xx ones
mp_need:    equ MP_RAM + 57         ; 128 KB blocks of sample RAM the MOD needs
mp_rbank:   equ MP_RAM + 58         ; the ROM reader's bank
mp_soff:    equ MP_RAM + 59         ; 3: MOD offset of the sample data
mp_ram:     equ MP_RAM + 62         ; 3: parse: the next sample's RAM address
mp_hi:      equ MP_RAM + 65         ; channel piece: parameter x >> 4
mp_lo:      equ MP_RAM + 66         ;   x & 15
mp_e:       equ MP_RAM + 67         ;   effect
mp_porta:   equ MP_RAM + 68         ;   1: tone portamento (3, 5)
mp_dly:     equ MP_RAM + 69         ;   1: a note delay (EDx, x > 0)
mp_note:    equ MP_RAM + 70         ;   the row's note
mp_old:     equ MP_RAM + 71         ; 2: the period before a tone portamento's start
mp_ok:      equ MP_RAM + 73         ; 1: mod_detect found a MoonSound for the MOD
                                    ; (74-76: free)
mp_tn:      equ MP_RAM + 78         ; tone of a note (77, 79: free)
mp_nq:      equ MP_RAM + 80         ; 2: note_of_period: the period
mp_nlo:     equ MP_RAM + 82         ;   the search
mp_ncnt:    equ MP_RAM + 83
mp_nstep:   equ MP_RAM + 84
mp_cs:      equ MP_RAM + 85         ; a cell: sample
mp_cp:      equ MP_RAM + 86         ;   2: period
mp_ce:      equ MP_RAM + 88         ;   effect
mp_cx:      equ MP_RAM + 89         ;   parameter
mp_hdr:     equ MP_RAM + 90         ; 12: a tone header
mp_rowb:    equ MP_RAM + 102        ; 32: a row as read
mp_stone:   equ MP_RAM + 134        ; 24: tone loaded per OPL4 channel (FFh none)
                                    ; (158-189: free)
mp_order:   equ MP_RAM + 190        ; 128: the order table
mp_si:      equ MP_RAM + 318        ; 31 x 12: the samples (SI_*)
mp_tt:      equ MP_RAM + 690        ; 97 x 3: the 9xx tones 31..127: sample, start (2)
mp_ch:      equ MP_RAM + 981        ; 8 x CH_SIZE: the channels (CH_*)
mp_heavy:   equ MP_RAM + 1421       ; 1: this poll did enough (a row, a note)
mp_lkon:    equ MP_RAM + 1422       ; the coming tick's writes: key ons (count, then
mp_lkoff:   equ MP_RAM + 1439       ;   register / value pairs), key offs,
mp_lupd:    equ MP_RAM + 1456       ;   pitch and level changes (49 bytes)
MP_RAM_END: equ MP_RAM + 1505       ; (mp_heavy = mp_ch + 8 * CH_SIZE)

; a sample (mp_si)
SI_LEN:     equ 0                   ; 2: bytes
SI_LS:      equ 2                   ; 2: loop start (0 for a one-shot)
SI_END:     equ 4                   ; 2: loop end, or the length
SI_LOOP:    equ 6                   ; 1: looped
SI_VOL:     equ 7
SI_FT:      equ 8                   ; finetune nibble
SI_ADR:     equ 9                   ; 3: sample RAM address
SI_SIZE:    equ 12

; a channel (mp_ch, IX)
CH_SEL:     equ 0                   ; the instrument: last sample number given
CH_VOL:     equ 1                   ; 0..64
CH_FT:      equ 2
CH_PER:     equ 3                   ; 2: period, 1/4 units (0: none yet)
CH_DEST:    equ 5                   ; 2: tone portamento target (0: none)
CH_PSPD:    equ 7
CH_OFFS:    equ 8                   ; 9xx memory
CH_STACK:   equ 9                   ; 2: ProTracker's stacked 9xx offset (bytes)
CH_PLAY:    equ 11                  ; 1: a sample sounds
CH_CUR:     equ 12                  ; the sample it plays (or played last)
CH_NOTE:    equ 13                  ; and its note (a retrigger restarts it)
CH_DLYT:    equ 14                  ; note delay: tick (0 none)
CH_DLYN:    equ 15                  ;   and note
CH_LATE:    equ 16                  ; out-of-range note delay: note (0 none)
CH_LROW:    equ 17                  ; E6x: loop row
CH_LN:      equ 18                  ;   and count
CH_S:       equ 19                  ; the row's cell: sample
CH_CPER:    equ 20                  ;   2: period
CH_E:       equ 22                  ;   effect
CH_X:       equ 23                  ;   parameter
CH_TRIG:    equ 24                  ; this tick: 1 = a sample (re)starts
CH_TS:      equ 25                  ;   this one
CH_TPOS:    equ 26                  ;   2: from this byte
CH_STOP:    equ 28                  ; 1: an empty sample stopped it
CH_ARP:     equ 29                  ; 1: the arpeggio's period plays
CH_APER:    equ 30                  ;   2: this one (FFFFh: 65536)
CH_SLOT:    equ 32                  ; OPL4 channel playing: c + 8 * slot
CH_KEYED:   equ 33
CH_R38:     equ 34                  ; its registers 38h, 20h, 50h
CH_R20:     equ 35
CH_LVL:     equ 36
CH_LV:      equ 37                  ; and what they were worked out from: volume
CH_LG:      equ 38                  ;   (FFh: the tremolo's), fade level,
CH_LPP:     equ 39                  ;   2: period played
CH_IDX:     equ 41                  ; the channel's number
CH_PAN:     equ 42                  ; its pan (Amiga: L R R L)
CH_TRM:     equ 43                  ; this tick: 1 = the tremolo's volume plays
CH_V4:      equ 44                  ;   2: this one (1/4 units, 0..256)
CH_VSPD:    equ 46                  ; vibrato (4xy, 6xy): speed x, depth y (memory),
CH_VDEP:    equ 47                  ;   position 0..63, waveform (E4x: 0 sine, 1 ramp
CH_VPOS:    equ 48                  ;   down, 2 square, 3 random; + 4: a note does
CH_VTYP:    equ 49                  ;   not restart it)
CH_MSPD:    equ 50                  ; tremolo (7xy): the same, E7x
CH_MDEP:    equ 51
CH_MPOS:    equ 52
CH_MTYP:    equ 53
CH_RFT:     equ 54                  ; the finetune of this row's retriggers (E9x)
CH_SIZE:    equ 55

NOTE_C1:    equ 48
QMIN:       equ 113 * 4
QMAX:       equ 856 * 4

; ----------------------------------------------------------------------------
mod_reset:
        xor a
        ld (mp_on), a
        ld (mp_work), a
        ld (mp_uph), a
        ld (mp_ldreg), a
        ld (mp_ok), a
        ret

mod_detect:
        call mod_reset
        ld a, 3
        call mp_new2                ; NEW = NEW2 = 1: the wave ports answer
        ld a, 2
        out (MP_WAVE_A), a
        ex (sp), hl                 ; let the register select settle
        ex (sp), hl
        in a, (MP_WAVE_D)
        and 0xE0
        cp 0x20                     ; device ID 001 in register 2
        jr nz, md_none
        call mp_parse               ; the MOD's header
        jr c, md_none               ; not one this module plays
        ld c, 2
        ld a, 0x11
        call mp_ww                  ; headers at 200000h, memory access on
        call mp_ramsize
        push af
        ld c, 2
        ld a, 0x10
        call mp_ww                  ; memory access off
        pop af
        ld hl, mp_need
        cp (hl)
        jr c, md_few
        ld hl, mp_ok
        ld (hl), 1
        ret                         ; enough sample RAM (no carry)
md_few: ld b, a
        jr md_fail
md_none:
        ld b, 0
md_fail:
        xor a
        call mp_new2                ; NEW = NEW2 = 0, as before
        ld a, b
        scf
        ret

; A = contiguous 128 KB blocks of sample RAM at 200000h (0..16). Writes k,
; not k at the start of block k from 15 down to 0, then reads them from 0
; up: a mirror shows a lower block's k, a hole reads FFh.
mp_ramsize:
        ld e, 15
rs_w:   ld a, e
        add a, a
        add a, 0x20
        ld hl, 0
        call mp_adr
        ld c, 6
        ld a, e
        call mp_ww
        cpl
        call mp_ww
        dec e
        jp p, rs_w
        ld e, 0
rs_r:   ld a, e
        add a, a
        add a, 0x20
        ld hl, 0
        call mp_adr
        ld c, 6
        call mp_wr
        cp e
        jr nz, rs_end
        call mp_wr
        cpl
        cp e
        jr nz, rs_end
        inc e
        ld a, e
        cp 16
        jr c, rs_r
rs_end: ld a, e
        ret

; The MOD's header: the channels (tag), the song length and order table,
; the samples (mp_si: lengths, loops, volumes, finetunes, and the sample RAM
; address each one gets), the blocks of sample RAM they need. Carry: a MOD
; this module does not play.
mp_parse:
        ld e, 0
        ld hl, 1080
        call mp_seek
        call mp_rd                  ; the tag
        ld b, a
        call mp_rd
        ld c, a
        call mp_rd
        ld d, a
        call mp_rd
        ld e, a
        ld a, b                     ; M.K. / M!K!
        cp 'M'
        jr nz, pa_t1
        ld a, d
        cp 'K'
        jr nz, pa_t1
        ld a, c
        cp e
        jr nz, pa_t1
        cp '.'
        jr z, pa_4
        cp '!'
        jr z, pa_4
pa_t1:  ld a, b                     ; FLT4
        cp 'F'
        jr nz, pa_t2
        ld a, c
        cp 'L'
        jr nz, pa_bad
        ld a, d
        cp 'T'
        jr nz, pa_bad
        ld a, e
        cp '4'
        jr z, pa_4
        jr pa_bad
pa_t2:  ld a, c                     ; 1CHN .. 8CHN
        cp 'C'
        jr nz, pa_bad
        ld a, d
        cp 'H'
        jr nz, pa_bad
        ld a, e
        cp 'N'
        jr nz, pa_bad
        ld a, b
        sub '1'
        cp 8
        jr nc, pa_bad
        inc a
        jr pa_n
pa_bad: scf
        ret
pa_4:   ld a, 4
pa_n:   ld (mp_nch), a
        add a, a
        add a, a
        ld (mp_rowsz), a
        ld e, 0                     ; song length, order table
        ld hl, 950
        call mp_seek
        call mp_rd
        ld (mp_songlen), a
        or a
        jr z, pa_bad
        cp 129
        jr nc, pa_bad
        call mp_rd                  ; (the restart byte)
        ld de, mp_order
        ld bc, 128 * 256            ; B = 128, C = the highest pattern
pa_o:   call mp_rd
        ld (de), a
        inc de
        cp c
        jr c, pa_o1
        ld c, a
pa_o1:  djnz pa_o
        ld a, c
        inc a
        ld (mp_npat), a
        ld e, a                     ; the samples follow the patterns:
        ld a, (mp_nch)              ; 1084 + patterns * channels * 256
        call mp_mul8                ; (1084 = 4 * 256 + 60)
        ld a, l
        add a, 1084 / 256
        ld l, a
        ld a, h
        adc a, 0
        ld (mp_soff + 2), a
        ld a, l
        ld (mp_soff + 1), a
        ld a, 1084 & 0xFF
        ld (mp_soff), a
        xor a                       ; RAM: after the 128 headers (200600h)
        ld (mp_ram), a
        ld a, 0x06
        ld (mp_ram + 1), a
        ld a, 0x20
        ld (mp_ram + 2), a
        ld e, 0
        ld hl, 20
        call mp_seek
        ld iy, mp_si
        ld b, 31
pa_s:   push bc
        ld b, 22                    ; the name
pa_s1:  call mp_rd
        djnz pa_s1
        call mp_rd                  ; length, words (big-endian)
        ld d, a
        call mp_rd
        ld e, a
        ld a, d
        cp 0x80
        jp nc, pa_sbad              ; more than 65534 bytes: not for one tone
        sla e
        rl d
        ld (iy + SI_LEN), e
        ld (iy + SI_LEN + 1), d
        call mp_rd                  ; finetune
        and 0x0F
        ld (iy + SI_FT), a
        call mp_rd                  ; volume, at most 64
        cp 65
        jr c, pa_s2
        ld a, 64
pa_s2:  ld (iy + SI_VOL), a
        call mp_rd                  ; loop start (words)
        ld b, a
        call mp_rd
        ld c, a
        call mp_rd                  ; loop length (words)
        ld (mp_old + 1), a
        call mp_rd
        ld (mp_old), a
        push hl
        ; looped: loop length > 1 word and loop start (bytes) < length
        xor a
        ld (iy + SI_LOOP), a
        ld (iy + SI_LS), a
        ld (iy + SI_LS + 1), a
        ld (iy + SI_END), e
        ld (iy + SI_END + 1), d
        ld hl, (mp_old)
        ld a, h
        or a
        jr nz, pa_l1
        ld a, l
        cp 2
        jr c, pa_l9                 ; one word or none: a one-shot
pa_l1:  ld a, b
        cp 0x80
        jr nc, pa_l9                ; a start past 65535 bytes
        sla c
        rl b                        ; BC = loop start (bytes)
        ld h, d
        ld l, e
        or a
        sbc hl, bc                  ; HL = length - start
        jr c, pa_l9
        jr z, pa_l9                 ; the start at or past the end
        ; end = start + min(loop length (bytes), length - start)
        ex de, hl                   ; DE = length - start
        ld hl, (mp_old)
        ld a, h
        cp 0x80
        jr nc, pa_l2                ; a loop past 65535 bytes: to the end
        add hl, hl
        push hl
        or a
        sbc hl, de
        pop hl
        jr c, pa_l3
pa_l2:  ex de, hl                   ; HL = length - start
pa_l3:  add hl, bc
        ld (iy + SI_END), l
        ld (iy + SI_END + 1), h
        ld (iy + SI_LS), c
        ld (iy + SI_LS + 1), b
        ld a, 1
        ld (iy + SI_LOOP), a
pa_l9:  pop hl
        ; its sample RAM address, and the next one's (a one-shot: 2 zero
        ; bytes after it)
        xor a
        ld (iy + SI_ADR), a
        ld (iy + SI_ADR + 1), a
        ld (iy + SI_ADR + 2), a
        ld a, (iy + SI_LEN)
        or (iy + SI_LEN + 1)
        jr z, pa_s9
        push hl
        ld hl, (mp_ram)
        ld a, (mp_ram + 2)
        ld (iy + SI_ADR), l
        ld (iy + SI_ADR + 1), h
        ld (iy + SI_ADR + 2), a
        ld c, (iy + SI_LEN)
        ld b, (iy + SI_LEN + 1)
        add hl, bc
        adc a, 0
        ld c, a
        ld a, (iy + SI_LOOP)
        or a
        ld a, c
        jr nz, pa_s8
        ld bc, 2
        add hl, bc
        adc a, 0
pa_s8:  ld (mp_ram), hl
        ld (mp_ram + 2), a
        pop hl
pa_s9:  ld bc, SI_SIZE
        add iy, bc
        pop bc
        dec b
        jp nz, pa_s
        ; blocks: (end - 200000h + 1FFFFh) >> 17
        ld hl, (mp_ram)
        ld a, (mp_ram + 2)
        sub 0x20
        ld de, 0xFFFF
        add hl, de
        adc a, 1
        srl a
        ld (mp_need), a
        or a                        ; no carry: a MOD this module plays
        ret
pa_sbad:
        pop bc
        scf
        ret

; ----------------------------------------------------------------------------
; The upload (see mod_upload_step above).
mod_upload:
        call mod_upload_init
mu_1:   ld a, 255
        call mod_upload_step
        jr nz, mu_1
        ret

; the upload with mod_detect as its first unit (20 ms), e.g. so that it
; runs while a menu waits and not at power on: mp_ok says whether the MOD
; will play once mod_upload_step returns Z
mod_upload_later:
        ld a, 4
        ld (mp_uph), a
        ret

; the tones: one per sample (0..30), then the 9xx start offsets the build
; found (MP_T_TT, at most 97: mp_tt); the headers first, from 200000h
mod_upload_init:
        ld a, 31 + MP_NTT
        ld (mp_ntone), a
        if MP_NTT
        ld a, MP_TAB_BANK
        call mp_setbank
        ld hl, MP_TAB + MP_T_TT
        ld de, mp_tt
        ld bc, MP_NTT * 3
        ldir
        call mp_restbank
        endif
        ld a, 2
        ld (mp_uph), a
        xor a
        ld (mp_ut), a
        ld hl, 0
        ld (mp_uram), hl
        ld a, 0x20
        ld (mp_uram + 2), a
        ret

; A = units at most; Z: all there (memory access off).
mod_upload_step:
        ld e, a
        ld a, (mp_uph)
        or a
        ret z                       ; nothing (left) to upload: no port access
        cp 4
        jr nz, us_0
        call mod_detect             ; (mod_upload_later) the MoonSound first
        push af
        call mp_restbank
        pop af
        jr nc, us_ok
        xor a                       ; Z: none for the MOD, nothing to upload
        ret
us_ok:  call mod_upload_init
        or 1                        ; NZ: the upload to come
        ret
us_0:   ld a, e
        ld (mp_ucnt), a
us_mem: ld c, 2
        ld a, 0x11
        call mp_ww                  ; memory access on (the chip is silent)
        ld hl, (mp_uram)
        ld a, (mp_uram + 2)
        call mp_adr
        ld c, 6
        call mp_sel                 ; the memory data register, for the OTIRs
us_page:                            ; (the test's breakpoint: the first unit)
        ld a, (mp_uph)
        cp 2
        jr nz, us_s
        call mp_hdr8                ; 8 tone headers
        jr us_n
us_s:   call mp_smp256              ; up to 256 sample bytes
us_n:   ld a, (mp_uph)
        or a
        jr z, us_done
        ld hl, mp_ucnt
        dec (hl)
        jr nz, us_page
        call mp_restbank
        ld c, 2
        ld a, 0x10
        call mp_ww                  ; memory access off
        or 1                        ; NZ: more to come
        ret
us_done:
        call mp_restbank
        ld c, 2
        ld a, 0x10
        call mp_ww                  ; memory access off: the chip plays
        xor a
        ret

; HL += C * 256, at most FFFFh
mp_sat: ld a, h
        add a, c
        ld h, a
        ret nc
        ld hl, 0xFFFF
        ret

; HL = min(HL, end of sample IY - 1). Changes AF, DE.
mp_clamp:
        ld e, (iy + SI_END)
        ld d, (iy + SI_END + 1)
        dec de
        push hl
        or a
        sbc hl, de
        pop hl
        ret c
        ex de, hl
        ret

; C = sample, HL = start (not 0): Z and A = tone index if its 9xx tone is
; there. Changes AF, B, DE.
mp_tfind:
        ld a, (mp_ntone)
        sub 31
        jr z, tf_no
        ld b, a
        ld de, mp_tt
tf_1:   ld a, (de)
        cp c
        jr nz, tf_2
        inc de
        ld a, (de)
        cp l
        jr nz, tf_3
        inc de
        ld a, (de)
        cp h
        jr nz, tf_4
        ld a, (mp_ntone)
        sub b
        cp a                        ; Z
        ret
tf_2:   inc de
tf_3:   inc de
tf_4:   inc de
        djnz tf_1
tf_no:  or 1                        ; NZ
        ret

; A = 9xx tone index - 31 -> HL = its mp_tt entry. Changes AF, DE.
mp_tt3: ld l, a
        ld h, 0
        ld d, h
        ld e, l
        add hl, hl
        add hl, de
        ld de, mp_tt
        add hl, de
        ret

; 8 tone headers (mp_mkhdr) to the sample RAM; at the last one, on to the
; samples.
mp_hdr8:
        ld b, 8
h8_1:   push bc
        ld a, (mp_ut)
        call mp_mkhdr
        ld hl, mp_hdr
        ld bc, 12 * 256 + MP_WAVE_D
        otir
        ld hl, (mp_uram)
        ld de, 12
        add hl, de
        ld (mp_uram), hl
        ld hl, mp_ut
        inc (hl)
        ld a, (hl)
        pop bc
        cp 128
        jr z, h8_end
        djnz h8_1
        ret
h8_end: ld a, 3                     ; the samples next, from 200600h
        ld (mp_uph), a
        xor a
        ld (mp_us), a
        ld (mp_uz), a
        ld (mp_uleft), a
        ld (mp_uleft + 1), a
        ld hl, (mp_soff)
        ld (mp_uoff), hl
        ld a, (mp_soff + 2)
        ld (mp_uoff + 2), a
        ret

; A = tone index -> mp_hdr = its 12-byte header: 8-bit samples from the
; sample's address plus its start, loop start and end (exclusive) from
; there; a one-shot loops on its 2 zero bytes. The envelope holds full
; level (AR 15, DL 0, D2R 0) and releases in 3.6 ms (RR 15). An unused tone,
; or an empty sample's: 12 zeros. Changes AF, BC, DE, HL, IY.
mp_mkhdr:
        ld hl, mp_hdr
        ld b, 12
mh_0:   ld (hl), 0
        inc hl
        djnz mh_0
        ld hl, mp_ntone
        cp (hl)
        ret nc                      ; not in use
        cp 31
        jr nc, mh_t
        inc a                       ; tone s - 1: sample s from its start
        ld hl, 0
        jr mh_1
mh_t:   sub 31                      ; a 9xx tone: sample, start
        call mp_tt3
        ld a, (hl)
        inc hl
        ld e, (hl)
        inc hl
        ld d, (hl)
        ex de, hl
mh_1:   push hl
        call mp_sinfo
        pop de                      ; DE = start
        ld a, (iy + SI_LEN)
        or (iy + SI_LEN + 1)
        ret z
        ld a, (iy + SI_ADR)         ; the address: sample + start
        add a, e
        ld (mp_hdr + 2), a
        ld a, (iy + SI_ADR + 1)
        adc a, d
        ld (mp_hdr + 1), a
        ld a, (iy + SI_ADR + 2)
        adc a, 0
        and 0x3F
        ld (mp_hdr), a
        ld a, (iy + SI_LOOP)
        or a
        jr z, mh_os
        ld l, (iy + SI_LS)          ; loop: max(0, loop start - start)
        ld h, (iy + SI_LS + 1)
        or a
        sbc hl, de
        jr nc, mh_2
        ld hl, 0
mh_2:   ld b, h
        ld c, l
        ld l, (iy + SI_END)         ; end: loop end - start
        ld h, (iy + SI_END + 1)
        jr mh_3
mh_os:  ld l, (iy + SI_LEN)         ; one-shot: loop at length - start,
        ld h, (iy + SI_LEN + 1)     ; end 2 bytes later
        or a
        sbc hl, de
        ld b, h
        ld c, l
        inc hl
        inc hl
        add hl, de
mh_3:   or a
        sbc hl, de                  ; HL = end - start
        ld a, b
        ld (mp_hdr + 3), a
        ld a, c
        ld (mp_hdr + 4), a
        xor a                       ; stored as 10000h - end
        sub l
        ld (mp_hdr + 6), a
        ld a, 0
        sbc a, h
        ld (mp_hdr + 5), a
        ld a, 0xF0
        ld (mp_hdr + 8), a          ; AR 15, D1R 0
        ld a, 0xFF
        ld (mp_hdr + 10), a         ; RC 15, RR 15
        ret

; Up to 256 bytes of the samples: the current one's bytes (never across a
; ROM bank), or its 2 zero bytes, or on to the next sample; after the last
; one the upload is over (mp_uph = 0).
mp_smp256:
        ld hl, (mp_uleft)
        ld a, h
        or l
        jr z, sm_z
        ld hl, (mp_uoff)
        ld a, (mp_uoff + 2)
        ld e, a
        call mp_seek                ; HL = the bytes in page 2
        ex de, hl
        ld hl, 0xC000
        or a
        sbc hl, de                  ; HL = bytes to the bank's end
        ld bc, (mp_uleft)
        call mp_min
        ld bc, 256
        call mp_min                 ; HL = bytes now (1..256)
        push hl
        ex de, hl
        ld b, e                     ; 256: B = 0
        ld c, MP_WAVE_D
        call mp_outn
        pop de
        ld hl, (mp_uleft)
        or a
        sbc hl, de
        ld (mp_uleft), hl
        ld hl, mp_uoff
        call mp_add24
        ld hl, mp_uram
        jp mp_add24
sm_z:   ld a, (mp_uz)
        or a
        jr z, sm_next
        ld b, a                     ; a one-shot's 2 zero bytes
        xor a
sm_z1:  out (MP_WAVE_D), a
        djnz sm_z1
        ld (mp_uz), a
        ld de, 2
        ld hl, mp_uram
        jp mp_add24
sm_next:
        ld a, (mp_us)
        inc a
        ld (mp_us), a
        cp 32
        jr nc, sm_end
        call mp_sinfo
        ld l, (iy + SI_LEN)
        ld h, (iy + SI_LEN + 1)
        ld (mp_uleft), hl
        ld a, h
        or l
        ret z                       ; empty: no bytes
        ld a, (iy + SI_LOOP)
        or a
        ret nz
        ld a, 2                     ; a one-shot: 2 zero bytes after it
        ld (mp_uz), a
        ret
sm_end: xor a
        ld (mp_uph), a
        ret

; B bytes (0: 256) from HL to port C by OUTI, 16 at a time (18 T a byte
; where OTIR takes 23; the sample RAM takes a write every 0.83 us). Changes
; AF, B, HL.
mp_outn:
        ld a, b
        and 0x0F                    ; the odd bytes first
        jr z, on_16
on_1:   outi
        dec a
        jr nz, on_1
        inc b
        dec b
        ret z
on_16:  outi
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
        jr nz, on_16                ; (the last OUTI: Z when B is 0)
        ret

; (HL) += DE, 24 bits. Changes AF, HL.
mp_add24:
        ld a, (hl)
        add a, e
        ld (hl), a
        inc hl
        ld a, (hl)
        adc a, d
        ld (hl), a
        inc hl
        ld a, (hl)
        adc a, 0
        ld (hl), a
        ret

; HL = min(HL, BC). Changes AF.
mp_min: push hl
        or a
        sbc hl, bc
        pop hl
        ret c
        ld h, b
        ld l, c
        ret

; ----------------------------------------------------------------------------
mod_start:
        push af
        push bc
        push de
        push hl
        push ix
        push iy
        xor a
        ld (mp_on), a
        ld (mp_work), a
        ld (mp_ldreg), a
        ld a, 3
        call mp_new2                ; NEW2 (the FM fallback must not clear it)
        ld c, 2
        ld a, 0x10
        call mp_ww                  ; tone headers 384+ at 200000h
        call mp_init
        call mp_alloff
        ld d, 24                    ; every channel silent until its note
ms_tl:  dec d
        call mp_used
        jr nc, ms_tl1
        ld a, 0x50
        add a, d
        ld c, a
        ld a, 0xFF
        call mp_ww
ms_tl1: inc d
        dec d
        jr nz, ms_tl
        ld c, 4
        ld a, 0x60
        call mp_fw                  ; timers stopped
        ld a, 0x80
        call mp_fw                  ; flags off
        call mp_period              ; tick 0's length
        ld a, 1
        ld (mp_on), a
        ld (mp_work), a
        call mp_sync                ; tick 0 worked out, its channels prepared
        ld c, 4
        if MP_NULL
        ld a, 0x40                  ; (a measure: timer 2 stays off, no tick is due)
        else
        ld a, 0x42                  ; timer 2 on (unmasked), timer 1 masked
        endif
        call mp_fw
        call mp_out                 ; tick 0
        call mp_period              ; tick 1's length
        ld a, 1
        ld (mp_work), a             ; tick 1 in the polls that follow
        call mp_restbank
        pop iy
        pop ix
        pop hl
        pop de
        pop bc
        pop af
        ret

; the song from its start: speed 6, 125 BPM, position 0, every channel off
mp_init:
        ld hl, mp_speed
        ld (hl), 6
        ld a, 125
        ld (mp_bpm), a
        call mp_t2
        xor a
        ld (mp_pos), a
        ld (mp_row), a
        ld (mp_tr), a
        ld (mp_g64), a
        ld (mp_fq), a
        ld (mp_endf), a
        ld (mp_cnt), a
        ld hl, 0
        ld (mp_k), hl
        ld (mp_ticks), hl
        ld (mp_fb), hl
        ld (mp_tc), hl
        ld (mp_tc + 2), a
        ld (mp_lkon), a             ; no writes waiting
        ld (mp_lkoff), a
        ld (mp_lupd), a
        ld hl, 0x8000
        ld (mp_acc), hl
        inc a
        ld (mp_newrow), a
        ld hl, mp_stone             ; no tone loaded
        ld b, 24
mn_1:   ld (hl), 0xFF
        inc hl
        djnz mn_1
        ld ix, mp_ch
        ld c, 0
mn_c:   push ix
        pop hl
        ld b, CH_SIZE
        xor a
mn_2:   ld (hl), a
        inc hl
        djnz mn_2
        ld (ix + CH_IDX), c
        ld a, c
        and 3
        ld hl, mp_pans
        call mp_addhl
        ld a, (hl)
        ld (ix + CH_PAN), a
        ld de, CH_SIZE
        add ix, de
        inc c
        ld a, (mp_nch)
        cp c
        jr nz, mn_c
        ret

mp_pans:
        db 9, 7, 7, 9               ; Amiga: channels 0 and 3 left, 1 and 2 right

; mp_whole / mp_frac = timer 2 per tick at mp_bpm (the table)
mp_t2:  ld a, MP_TAB_BANK
        call mp_setbank
        ld a, (mp_bpm)
        ld l, a
        ld h, 0
        ld d, h
        ld e, l
        add hl, hl
        add hl, de
        ld de, MP_TAB + MP_T_BPM
        add hl, de
        ld a, (hl)
        ld (mp_whole), a
        inc hl
        ld a, (hl)
        ld (mp_frac), a
        inc hl
        ld a, (hl)
        ld (mp_frac + 1), a
        ret

; ----------------------------------------------------------------------------
mod_poll:
        ld a, (mp_work)
        or a
        jr nz, mp_more              ; the coming tick to work out
        in a, (MP_FM_A0)
        and 0x20                    ; FT2: timer 2 ran out
        ret z
        ld a, (mp_on)
        or a
        ret z                       ; (no MoonSound: the status reads FFh)
        push bc
        push de
        push hl
        push ix
        push iy
mp_tick:
        call mp_sync                ; (if the polls did not get to it all)
        ld c, 4
        ld a, 0x80
        call mp_fw                  ; flags off (IRQ line down)
        ld hl, (mp_ticks)
        inc hl
        ld (mp_ticks), hl
        ld a, (mp_endf)
        or a
        jr nz, mp_d_end
        call mp_out                 ; the tick that starts
        call mp_period              ; the timer for the one after it
        ld a, 1
        ld (mp_work), a             ; which the next polls work out
        jr mp_d2
mp_d_end:
        call mp_end                 ; the fade is over
mp_d2:  call mp_restbank
        pop iy
        pop ix
        pop hl
        pop de
        pop bc
        ret

; the poll for a busy CPU: a tick that is due, and the work of it the polls
; did not get to; nothing else (mod_poll, where the CPU would wait anyway,
; works out the next tick). With no tick due, 33 T (and the CALL). Changes
; AF only.
mod_tpoll:
        in a, (MP_FM_A0)
        and 0x20
        ret z
        ld a, (mp_on)
        or a
        ret z
        push bc
        push de
        push hl
        push ix
        push iy
        jr mp_tick
mp_more:
        in a, (MP_FM_A0)
        and 0x20
        jr z, mp_m1
        ld a, (mp_on)               ; the next tick is already due: finish
        or a                        ; and play it
        ret z
        push bc
        push de
        push hl
        push ix
        push iy
        jr mp_tick
mp_m1:  ld a, (mp_ldreg)            ; a tone number waiting for its LD: while
        or a                        ; LD is up there is nothing to do yet (a
        jr z, mp_m2                 ; poll that comes often returns at once)
        in a, (MP_FM_A0)
        and 2
        ret nz
mp_m2:  push bc
        push de
        push hl
        push ix
        push iy
        call mp_step
        jr mp_d2

mod_stop:
        push af
        ld a, (mp_on)
        or a
        jr z, mst_1                 ; not playing (or no MoonSound): nothing to do
        push bc
        push de
        push hl
        call mp_end
        pop hl
        pop de
        pop bc
mst_1:  pop af
        ret

mp_end:
        xor a
        ld (mp_on), a
        ld (mp_work), a
        ld (mp_ldreg), a
        call mp_alloff
        ld c, 4
        ld a, 0x60
        call mp_fw                  ; timers stopped
        ld a, 0x80
        jp mp_fw                    ; flags off

; key off on every channel in use, 23 down to 0 (release: 3.6 ms)
mp_alloff:
        call mp_ld                  ; (no header still loading)
        ld d, 24
ma_1:   dec d
        call mp_used
        jr nc, ma_2
        ld a, d
        and 3
        ld hl, mp_pans
        call mp_addhl
        ld a, 0x68
        add a, d
        ld c, a
        ld a, (hl)
        or 0x20                     ; key off, LFO off
        call mp_ww
ma_2:   inc d
        dec d
        jr nz, ma_1
        ret

mp_used:                            ; carry: OPL4 channel D is in use (D & 7 < channels)
        ld a, (mp_nch)
        ld c, a
        ld a, d
        and 7
        cp c
        ret

; the rest of the coming tick's work, now (LD waited for)
mp_sync:
        ld a, (mp_ldreg)
        or a
        jr z, sy_1
        call mp_ld
        call mp_ldput
        jr mp_sync
sy_1:   ld a, (mp_work)
        or a
        ret z
        call mp_step
        jr mp_sync

; the coming tick's work, as far as one poll goes: pieces until they did
; MP_BUDGET units of work (mp_heavy: a row read 2, a channel 1, its note or
; arpeggio 1 more), a tone number waits for LD, or the tick is worked out.
; With 1, a poll works out one channel or reads one row (at most ~7.7k T).
MP_BUDGET:  equ 1
mp_step:
        ld a, (mp_ldreg)
        or a
        jr z, st_0
        in a, (MP_FM_A0)            ; a tone number waits: LD still up?
        and 2
        ret nz
        jp mp_ldput                 ; (then the next poll)
st_0:   xor a
        ld (mp_heavy), a
st_1:   ld a, (mp_work)
        dec a
        jr nz, st_2
        call mp_crow                ; 1: the fade, the row
        jr st_4
st_2:   dec a
        jr nz, st_3
        call mp_cchan               ; 2: a channel
        jr st_4
st_3:   jp mp_cfin                  ; 3: the end of the tick
st_4:   ld a, (mp_heavy)
        cp MP_BUDGET
        ret nc
        ld a, (mp_ldreg)
        or a
        ret nz
        ld a, (mp_work)
        or a
        jr nz, st_1
        ret

; the waiting tone number, now
mp_ldput:
        ld a, (mp_ldreg)
        ld c, a
        ld a, (mp_ldval)
        call mp_ww
        xor a
        ld (mp_ldreg), a
        ret

; timer 2 = whole + carry of acc += frac: the period of the tick after the
; one that starts
mp_period:
        ld hl, (mp_acc)
        ld de, (mp_frac)
        add hl, de
        ld (mp_acc), hl
        ld a, (mp_whole)
        adc a, 0
        ld (mp_cnt), a
        neg                         ; register 03h = 256 - steps
        ld c, 3
        jp mp_fw

; the tick that starts: key ons (all channels first), key offs, pitch and
; level changes
mp_out:
        call mp_ld                  ; no tone header still loading
        ld hl, mp_lkon
        call mp_flush
        ld hl, mp_lkoff
        call mp_flush
        ld hl, mp_lupd

; HL = a list (count, then register / value pairs): its writes, then it is
; empty. Changes AF, BC, HL.
mp_flush:
        ld b, (hl)
        ld (hl), 0
        inc hl
        inc b
        dec b
        ret z
fl_1:   ld c, (hl)
        inc hl
        ld a, (hl)
        inc hl
        call mp_ww
        djnz fl_1
        ret

; HL = a list: one more write, register C = A. Changes AF, DE, HL.
mp_push:
        ld e, a
        ld a, (hl)
        inc (hl)
        inc hl
        add a, a
        call mp_addhl
        ld (hl), c
        inc hl
        ld (hl), e
        ret

; ----------------------------------------------------------------------------
; Piece 1: the coming tick's fade level (its start mp_tc against MP_FADE0:
; g64 = 64 - (t - MP_FADE0) / MP_FSTEP; 0: the end), and the row when one
; starts: its cells from the MOD, the row's speed, tempo, jump, break and
; row delay, a late note delay's pitch.
mp_crow:
        if MP_LOOP
        ld a, 64                    ; a song that loops for ever: no fade
        ld (mp_g64), a
        else
        ld hl, (mp_tc)
        ld a, (mp_tc + 2)
        ld de, MP_FADE0 & 0xFFFF
        or a
        sbc hl, de
        sbc a, MP_FADE0 >> 16
        ld c, 64
        jr c, cr_g
        ld c, 0
        or a
        jr nz, cr_g                 ; far past
cr_f:   push hl                     ; while d - fb >= FSTEP: fb += FSTEP, fq += 1
        ld de, (mp_fb)
        or a
        sbc hl, de
        ld de, MP_FSTEP
        sbc hl, de
        pop hl
        jr c, cr_f1
        ld de, (mp_fb)
        push hl
        ld hl, MP_FSTEP
        add hl, de
        ld (mp_fb), hl
        pop hl
        ld a, (mp_fq)
        inc a
        ld (mp_fq), a
        cp 64
        jr c, cr_f
cr_f1:  ld a, (mp_fq)
        cp 64
        ld c, 0
        jr nc, cr_g
        neg
        add a, 64
        ld c, a
cr_g:   ld a, c
        ld (mp_g64), a
        or a
        jr nz, cr_1
        inc a
        ld (mp_endf), a             ; the end
        xor a
        ld (mp_work), a
        ret
        endif
cr_1:   ld a, 2
        ld (mp_work), a
        xor a
        ld (mp_wc), a
        ld a, (mp_newrow)
        or a
        ret z
        ld a, 2
        ld (mp_heavy), a            ; (a row: 2 units of this poll's work)
        xor a
        ld (mp_newrow), a
        ld hl, 0
        ld (mp_k), hl
        ld (mp_tr), a
        ld (mp_jf), a
        ld (mp_tempo), a
        dec a
        ld (mp_brk), a
        ld (mp_loopto), a
        ld (mp_pdl), a
        ld a, (mp_songlen)          ; past the song's end: position 0, on the
        ld c, a                     ; row a Dxx gave (ProTracker)
        ld a, (mp_pos)
        cp c
        jr c, cr_2
        xor a
        ld (mp_pos), a
cr_2:   ld hl, mp_order
        call mp_addhl
        ld a, (mp_row)
        ld c, a
        ld a, (hl)
        call mp_rowrd               ; the row's cells
        ld hl, mp_rowb
        ld ix, mp_ch
        ld a, (mp_nch)
        ld b, a
cr_c:   push bc
        push hl
        call mp_cell
        ld a, (mp_cs)
        ld (ix + CH_S), a
        ld hl, (mp_cp)
        ld (ix + CH_CPER), l
        ld (ix + CH_CPER + 1), h
        ld a, (mp_ce)
        ld (ix + CH_E), a
        ld a, (mp_cx)
        ld (ix + CH_X), a
        ld c, a
        ld a, (mp_ce)
        cp 0x0F
        jr nz, cr_c1
        ld a, c                     ; Fxx: speed, or tempo (x >= 20h); F00: nothing
        or a
        jr z, cr_cn
        cp 0x20
        jr nc, cr_tp
        ld (mp_speed), a
        jr cr_cn
cr_tp:  ld (mp_tempo), a
        jr cr_cn
cr_c1:  cp 0x0B
        jr nz, cr_c2
        ld a, c                     ; Bxx: jump (a Dxx before it: its row
        ld (mp_jump), a             ; no more, ProTracker)
        ld a, 1
        ld (mp_jf), a
        ld a, 0xFF
        ld (mp_brk), a
        jr cr_cn
cr_c2:  cp 0x0D
        jr nz, cr_c3
        ld a, c                     ; Dxx: break (decimal; past 63: 0)
        and 0xF0
        rrca
        ld b, a                     ; 8 * tens
        rrca
        rrca                        ; 2 * tens
        add a, b
        ld b, a
        ld a, c
        and 0x0F
        add a, b
        cp 64
        jr c, cr_bk
        xor a
cr_bk:  ld (mp_brk), a
        jr cr_cn
cr_c3:  cp 0x0E
        jr nz, cr_cn
        ld a, c                     ; EEx: row delay (the last one, ProTracker)
        and 0xF0
        cp 0xE0
        jr nz, cr_cn
        ld a, c
        and 0x0F
        ld (mp_pdl), a
cr_cn:  pop hl
        ld bc, 4
        add hl, bc
        ld bc, CH_SIZE
        add ix, bc
        pop bc
        dec b
        jp nz, cr_c
        ld a, (mp_pdl)              ; ticks of the row: speed * (1 + delay)
        inc a
        jr nz, cr_3
        inc a
cr_3:   ld e, a
        ld a, (mp_speed)
        call mp_mul8
        ld (mp_nk), hl
        ld a, MP_TAB_BANK           ; a late note delay's note: its pitch now,
        call mp_setbank             ; unless the row has a note there
        ld ix, mp_ch
        ld a, (mp_nch)
        ld b, a
cr_l:   ld a, (ix + CH_LATE)
        or a
        jr z, cr_l2
        ld c, a
        ld a, (ix + CH_CPER)
        or (ix + CH_CPER + 1)
        jr nz, cr_l1
        push bc
        ld a, c
        ld b, (ix + CH_FT)
        call mp_pon
        pop bc
        ld (ix + CH_PER), l
        ld (ix + CH_PER + 1), h
cr_l1:  xor a
        ld (ix + CH_LATE), a
cr_l2:  ld de, CH_SIZE
        add ix, de
        djnz cr_l
        ret

; Piece 2: one channel's tick (mp_ctick), then what it means for the OPL4
; (mp_cmap).
mp_cchan:
        ld hl, mp_heavy
        inc (hl)                    ; (a channel: 1 unit)
        ld a, MP_TAB_BANK
        call mp_setbank
        ld a, (mp_wc)
        call mp_chix
        call mp_ctick
        call mp_cmap
        ld hl, mp_wc
        inc (hl)
        ld a, (mp_nch)
        cp (hl)
        ret nz
        ld a, 3
        ld (mp_work), a
        ret

; Piece 3: the tempo from the tick after the row's first, the timer period
; that goes with it, the next tick of the row or the next row (jump, break,
; loop), the next tick's start.
mp_cfin:
        ld hl, (mp_k)
        ld a, h
        or l
        jr nz, cf_1
        ld a, (mp_tempo)
        or a
        jr z, cf_1
        ld (mp_bpm), a
cf_1:   call mp_t2
        ld hl, (mp_k)
        inc hl
        ld (mp_k), hl
        ld a, (mp_speed)
        ld c, a
        ld a, (mp_tr)
        inc a
        cp c
        jr c, cf_2
        xor a
cf_2:   ld (mp_tr), a
        ld de, (mp_nk)
        or a
        sbc hl, de
        jr c, cf_9                  ; more ticks in this row
        ld a, 1
        ld (mp_newrow), a
        ld a, (mp_jf)
        or a
        jr nz, cf_j
        ld a, (mp_brk)
        inc a
        jr nz, cf_j
        ld a, (mp_loopto)
        cp 0xFF
        jr z, cf_n
        ld (mp_row), a              ; E6x: back to the loop row
        jr cf_9
cf_n:   ld a, (mp_row)              ; the next row
        inc a
        cp 64
        jr c, cf_n1
        ld hl, mp_pos
        inc (hl)
        xor a
cf_n1:  ld (mp_row), a
        jr cf_9
cf_j:   ld a, (mp_jf)               ; Bxx: that position, else the next;
        or a                        ; Dxx: that row, else 0
        ld a, (mp_jump)
        jr nz, cf_j1
        ld a, (mp_pos)
        inc a
cf_j1:  ld (mp_pos), a
        ld a, (mp_brk)
        cp 0xFF
        jr nz, cf_j2
        xor a
cf_j2:  ld (mp_row), a
        ld a, (mp_pdl)              ; after a row delay (EEx, x > 0) ProTracker
        inc a                       ; skips the target row itself
        jr z, cf_9
        dec a
        jr z, cf_9
        ld hl, mp_row
        inc (hl)
        ld a, (hl)
        cp 64
        jr c, cf_9
        ld (hl), 0
        ld hl, mp_pos
        inc (hl)
cf_9:   ld a, (mp_cnt)              ; the next tick starts this much later
        ld e, a
        ld d, 0
        ld hl, mp_tc
        call mp_add24
        xor a
        ld (mp_work), a
        ret

; ----------------------------------------------------------------------------
; One channel's tick, as ProTracker plays it (modplay.Player.chan_tick). IX
; = the channel, page 2 = the tables. Sets CH_TRIG / CH_TS / CH_TPOS (a
; sample (re)starts), CH_STOP, CH_ARP / CH_APER (the period played: the
; arpeggio's or the vibrato's), CH_TRM / CH_V4 (the tremolo's volume).
mp_ctick:
        xor a
        ld (ix + CH_TRIG), a
        ld (ix + CH_STOP), a
        ld (ix + CH_ARP), a
        ld (ix + CH_TRM), a
        ld a, (ix + CH_E)           ; no effect: nothing to do after the row's
        or (ix + CH_X)              ; first tick, nor on it without a sample or
        jr nz, ct_go                ; a note
        ld hl, (mp_k)
        ld a, h
        or l
        ret nz
        ld a, (ix + CH_S)
        or (ix + CH_CPER)
        or (ix + CH_CPER + 1)
        ret z
ct_go:  ld a, (ix + CH_X)
        ld c, a
        and 0x0F
        ld (mp_lo), a
        ld a, c
        rrca
        rrca
        rrca
        rrca
        and 0x0F
        ld (mp_hi), a
        ld a, (ix + CH_E)
        ld (mp_e), a
        ld b, 0
        cp 3
        jr z, ct_p1
        cp 5
        jr nz, ct_p0
ct_p1:  inc b
ct_p0:  ld a, b
        ld (mp_porta), a
        ld hl, (mp_k)
        ld a, h
        or l
        jp nz, ct_tr
        ; ---- the row's first tick: its instrument and note
        xor a
        ld (ix + CH_DLYT), a        ; (a note delay: on each repeat of EEx)
        ld a, (ix + CH_FT)
        ld (ix + CH_RFT), a         ; (the finetune before the instrument number)
        ld b, 0                     ; a note delay: EDx, x > 0
        ld a, (mp_e)
        cp 0x0E
        jr nz, ct_d0
        ld a, (mp_hi)
        cp 0x0D
        jr nz, ct_d0
        ld a, (mp_lo)
        or a
        jr z, ct_d0
        inc b
ct_d0:  ld a, b
        ld (mp_dly), a
        ld a, (ix + CH_S)
        or a
        jr z, ct_nos
        ld (ix + CH_SEL), a         ; an instrument number: its volume (an empty
        call mp_sinfo               ; sample's too) and finetune; the stacked
        ld a, (iy + SI_VOL)         ; offset back to 0
        ld (ix + CH_VOL), a
        ld a, (iy + SI_FT)
        ld (ix + CH_FT), a
        xor a
        ld (ix + CH_STACK), a
        ld (ix + CH_STACK + 1), a
ct_nos: ld a, (mp_e)
        cp 9
        jr nz, ct_no9
        ld a, (ix + CH_X)
        or a
        jr z, ct_n9m
        ld (ix + CH_OFFS), a        ; 9xx memory
ct_n9m: ld a, (ix + CH_CPER)        ; 9xx without a note: the stacked offset
        or (ix + CH_CPER + 1)       ; moves all the same (ProTracker)
        jr nz, ct_no9
        ld l, (ix + CH_STACK)
        ld h, (ix + CH_STACK + 1)
        ld c, (ix + CH_OFFS)
        call mp_sat
        ld (ix + CH_STACK), l
        ld (ix + CH_STACK + 1), h
ct_no9: ld a, (mp_e)                ; E5x, with or without a note: the finetune
        cp 0x0E
        jr nz, ct_n5
        ld a, (mp_hi)
        cp 5
        jr nz, ct_n5
        ld a, (mp_lo)
        ld (ix + CH_FT), a
ct_n5:  ld a, (ix + CH_CPER)        ; a retrigger's finetune: the row's start's
        or (ix + CH_CPER + 1)       ; with a lone instrument number, else this
        jr nz, ct_rf
        ld a, (ix + CH_S)
        or a
        jr nz, ct_rf1
ct_rf:  ld a, (ix + CH_FT)
        ld (ix + CH_RFT), a
ct_rf1: ld l, (ix + CH_CPER)
        ld h, (ix + CH_CPER + 1)
        ld a, h
        or l
        jp z, ct_k0e
        ld a, (mp_heavy)
        inc a                       ; (a note: 1 unit more)
        ld (mp_heavy), a
        call mp_pnote               ; the note
        ld (mp_note), a
        ld a, (mp_porta)
        or a
        jr z, ct_n2
        ld a, (mp_note)             ; tone portamento: the note is the target
        ld b, (ix + CH_FT)
        call mp_pon
        ld (ix + CH_DEST), l
        ld (ix + CH_DEST + 1), h
        ld a, (ix + CH_PER)
        or (ix + CH_PER + 1)
        jr nz, ct_n1a
        ld (ix + CH_PER), l
        ld (ix + CH_PER + 1), h
ct_n1a: ld a, (ix + CH_PLAY)
        or a
        jp nz, ct_k0e
        ld a, (ix + CH_SEL)
        or a
        jp z, ct_k0e
        push hl                     ; nothing sounds: the sample starts anyway,
        ld l, (ix + CH_PER)         ; at the period it had, and slides
        ld h, (ix + CH_PER + 1)
        ld (mp_old), hl
        ld hl, 0
        ld a, (ix + CH_S)
        or a
        jr nz, ct_n1b
        ld l, (ix + CH_STACK)
        ld h, (ix + CH_STACK + 1)
ct_n1b: ld a, (mp_note)
        ld c, a
        ld a, (ix + CH_SEL)
        call mp_trig
        pop de
        ld hl, (mp_old)
        ld a, h
        or l
        jr nz, ct_n1c
        ex de, hl
ct_n1c: ld (ix + CH_PER), l
        ld (ix + CH_PER + 1), h
        jr ct_k0e
ct_n2:  ld a, (mp_dly)
        or a
        jr z, ct_n3
        ld a, (mp_speed)            ; note delay: at tick x, or (x >= speed)
        ld b, a                     ; only its pitch, on the next row
        ld a, (mp_lo)
        cp b
        jr nc, ct_n2a
        ld (ix + CH_DLYT), a
        ld a, (mp_note)
        ld (ix + CH_DLYN), a
        jr ct_k0e
ct_n2a: ld a, (mp_note)
        ld (ix + CH_LATE), a
        jr ct_k0e
ct_n3:  ld l, (ix + CH_STACK)       ; the note starts: from the stacked
        ld h, (ix + CH_STACK + 1)   ; offset, plus 9xx (which stacks twice)
        ld a, (mp_e)
        cp 9
        jr nz, ct_n3a
        ld c, (ix + CH_OFFS)
        call mp_sat
        push hl
        ld l, (ix + CH_STACK)
        ld h, (ix + CH_STACK + 1)
        call mp_sat
        call mp_sat
        ld (ix + CH_STACK), l
        ld (ix + CH_STACK + 1), h
        pop hl
ct_n3a: ld a, (mp_note)
        ld c, a
        ld a, (ix + CH_SEL)
        call mp_trig
ct_k0e: ld a, (mp_e)
        cp 3
        jr nz, ct_k4
        ld a, (ix + CH_X)
        or a
        jr z, ct_tr
        ld (ix + CH_PSPD), a
        jr ct_tr
ct_k4:  ld c, CH_VSPD               ; 4xy / 7xy: speed x, depth y (0: as before)
        cp 4
        jr z, ct_k4a
        cp 7
        jr nz, ct_tr
        ld c, CH_MSPD
ct_k4a: push ix
        pop hl
        ld a, c
        call mp_addhl
        ld a, (mp_hi)
        or a
        jr z, ct_k4b
        ld (hl), a
ct_k4b: inc hl
        ld a, (mp_lo)
        or a
        jr z, ct_tr
        ld (hl), a
        ; ---- the row's first tick (and its EEx repeats), or another
ct_tr:  ld a, (mp_tr)
        or a
        jp nz, ct_ot
        ld a, (mp_e)
        cp 0x0C
        jr nz, ct_t1
        ld a, (ix + CH_X)           ; Cxx
        cp 65
        jr c, ct_t0
        ld a, 64
ct_t0:  ld (ix + CH_VOL), a
        jp ct_arp
ct_t1:  cp 0x0E
        jp nz, ct_arp
        ld a, (mp_hi)
        cp 1
        jr z, ct_e1
        cp 2
        jr z, ct_e2
        cp 0x0A
        jr z, ct_ea
        cp 0x0B
        jr z, ct_eb
        cp 0x0C
        jr z, ct_ec
        cp 9
        jr z, ct_e9
        cp 6
        jr z, ct_e6
        ld c, CH_VTYP
        cp 4
        jr z, ct_e4
        cp 7
        jp nz, ct_arp
        ld c, CH_MTYP
ct_e4:  push ix                     ; E4x / E7x: the waveform, x & 7 (after the
        pop hl                      ; row's note, which restarted the old one)
        ld a, c
        call mp_addhl
        ld a, (mp_lo)
        and 7
        ld (hl), a
        ret
ct_e1:  ld a, (mp_lo)               ; E1x: fine porta up
        call mp_x4
        call mp_perdn
        jp ct_arp
ct_e2:  ld a, (mp_lo)               ; E2x: fine porta down
        call mp_x4
        call mp_perup
        jp ct_arp
ct_ea:  ld a, (mp_lo)               ; EAx: fine volume up
        call mp_volup
        jp ct_arp
ct_eb:  ld a, (mp_lo)               ; EBx: fine volume down
        call mp_voldn
        jp ct_arp
ct_ec:  ld a, (mp_lo)               ; EC0: cut now
        or a
        jp nz, ct_arp
        ld (ix + CH_VOL), a
        jp ct_arp
ct_e9:  ld a, (mp_lo)               ; E9x without a note, on the row's first
        or a                        ; tick and on each EEx repeat's: a retrigger
        jp z, ct_arp
        ld a, (ix + CH_CPER)
        or (ix + CH_CPER + 1)
        jp nz, ct_arp
        call mp_retrig
        jp ct_arp
ct_e6:  ld hl, (mp_k)               ; E6x: pattern loop
        ld a, h
        or l
        jp nz, ct_arp
        ld a, (mp_lo)
        or a
        jr nz, ct_e6a
        ld a, (mp_row)              ; E60: the loop starts here
        ld (ix + CH_LROW), a
        jp ct_arp
ct_e6a: ld b, a
        ld a, (ix + CH_LN)
        or a
        jr nz, ct_e6b
        ld (ix + CH_LN), b          ; the first time: x more times
        ld a, (ix + CH_LROW)
        ld (mp_loopto), a
        jp ct_arp
ct_e6b: dec a
        ld (ix + CH_LN), a
        jp z, ct_arp
        ld a, (ix + CH_LROW)
        ld (mp_loopto), a
        jp ct_arp
        ; ---- the other ticks
ct_ot:  ld a, (mp_e)
        or a
        jr z, ct_vs                 ; 0xy: the arpeggio, below
        cp 1
        jr nz, ct_o2
        ld a, (ix + CH_X)           ; 1xx: porta up
        call mp_x4
        call mp_perdn
        jr ct_vs
ct_o2:  cp 2
        jr nz, ct_o3
        ld a, (ix + CH_X)           ; 2xx: porta down
        call mp_x4
        call mp_perup
        jr ct_vs
ct_o3:  ld a, (mp_porta)
        or a
        jr z, ct_vs
        ld l, (ix + CH_PER)         ; 3xx / 5xy: towards the target
        ld h, (ix + CH_PER + 1)
        ld a, h
        or l
        jr z, ct_vs
        ld e, (ix + CH_DEST)
        ld d, (ix + CH_DEST + 1)
        ld a, d
        or e
        jr z, ct_vs
        push de
        ld a, (ix + CH_PSPD)
        call mp_x4                  ; DE = 4 * speed
        ld b, d
        ld c, e
        pop de                      ; DE = target, HL = period
        or a
        sbc hl, de
        jr z, ct_o3z
        jr nc, ct_o3d
        add hl, de                  ; below: up by BC, at most the target
        add hl, bc
        call mp_mindst
        jr ct_o3s
ct_o3d: add hl, de                  ; above: down by BC, at least the target
        or a
        sbc hl, bc
        jr c, ct_o3t
        push hl
        or a
        sbc hl, de
        pop hl
        jr nc, ct_o3s
ct_o3t: ld h, d
        ld l, e
        jr ct_o3s
ct_o3z: add hl, de
ct_o3s: ld (ix + CH_PER), l
        ld (ix + CH_PER + 1), h
        or a
        sbc hl, de
        jr nz, ct_vs
        ld (ix + CH_DEST), l        ; reached: the portamento is over
        ld (ix + CH_DEST + 1), h
ct_vs:  ld a, (mp_e)                ; 5xy / 6xy / Axy: volume slide
        cp 5
        jr z, ct_vs1
        cp 6
        jr z, ct_vs1
        cp 0x0A
        jr nz, ct_oe
ct_vs1: ld a, (mp_hi)
        or a
        jr z, ct_vs2
        call mp_volup
        jr ct_oe
ct_vs2: ld a, (mp_lo)
        call mp_voldn
ct_oe:  ld a, (mp_e)
        cp 0x0E
        jr z, ct_oe0
        bit 2, a                    ; 4xy, 6xy: vibrato, 7xy: tremolo
        jr z, ct_arp
        cp 8
        jr nc, ct_arp
        cp 5
        jr z, ct_arp
        cp 7
        jp z, mp_trem
        jp mp_vib
ct_oe0: ld a, (mp_hi)
        cp 9
        jr nz, ct_oe2
        ld a, (mp_lo)               ; E9x: every x ticks
        or a
        jr z, ct_arp
        ld b, a
        ld a, (mp_tr)
ct_oe1: sub b
        jr z, ct_oe1r
        jr nc, ct_oe1
        jr ct_arp
ct_oe1r:
        call mp_retrig
        jr ct_arp
ct_oe2: cp 0x0C
        jr nz, ct_oe3
        ld a, (mp_lo)               ; ECx: cut at tick x
        ld b, a
        ld a, (mp_tr)
        cp b
        jr nz, ct_arp
        xor a
        ld (ix + CH_VOL), a
        jr ct_arp
ct_oe3: cp 0x0D
        jr nz, ct_arp
        ld a, (ix + CH_DLYT)        ; EDx: the delayed note, at tick x
        or a
        jr z, ct_arp
        ld b, a
        ld a, (mp_tr)
        cp b
        jr nz, ct_arp
        ld c, (ix + CH_DLYN)
        ld l, (ix + CH_STACK)
        ld h, (ix + CH_STACK + 1)
        ld a, (ix + CH_SEL)
        call mp_trig
        ; ---- 0xy: the arpeggio, on the period played only
ct_arp: ld a, (mp_e)
        or a
        ret nz
        ld a, (ix + CH_X)
        or a
        ret z
        ld l, (ix + CH_PER)
        ld h, (ix + CH_PER + 1)
        ld a, h
        or l
        ret z
        ld a, (mp_tr)               ; tick mod 3: 0 the note, 1 + x, 2 + y
ct_a1:  sub 3
        jr nc, ct_a1
        add a, 3
        ret z
        dec a
        ld a, (mp_hi)
        jr z, ct_a2
        ld a, (mp_lo)
ct_a2:  push af
        ld a, (mp_heavy)
        inc a                       ; (the note search: 1 unit more)
        ld (mp_heavy), a
        ld b, (ix + CH_FT)
        call mp_nop                 ; A = the period's note
        pop bc
        add a, b
        cp 84
        jr nz, ct_a3
        ld hl, 0xFFFF               ; ProTracker's wrap: the period 65536
        jr ct_a5
ct_a3:  jr c, ct_a4
        sub 37
ct_a4:  ld b, (ix + CH_FT)
        call mp_pon
ct_a5:  ld (ix + CH_APER), l
        ld (ix + CH_APER + 1), h
        ld a, 1
        ld (ix + CH_ARP), a
        ret

; 4xy / 6xy after the row's first tick (modplay.vib_delta): the period played
; is the period plus |wave| * depth / 16 with the wave's sign (a period of 0
; stays silent); the position moves on by the speed either way.
mp_vib: ld l, (ix + CH_PER)
        ld h, (ix + CH_PER + 1)
        ld a, h
        or l
        jr z, vb_adv
        push hl
        ld a, (ix + CH_VPOS)
        ld b, (ix + CH_VTYP)
        call mp_wave                ; E = |wave|, C = FFh if negative
        ld a, (ix + CH_VDEP)
        call mp_mul4                ; HL = |wave| * depth (at most 1905)
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        ld e, h                     ; E = that / 16
        ld d, 0
        pop hl
        inc c
        jr z, vb_neg
        add hl, de
        jr vb_st
vb_neg: sbc hl, de                  ; (no carry)
vb_st:  ld (ix + CH_APER), l
        ld (ix + CH_APER + 1), h
        ld (ix + CH_ARP), 1
vb_adv: ld a, (ix + CH_VPOS)
        add a, (ix + CH_VSPD)
        and 63
        ld (ix + CH_VPOS), a
        ret

; 7xy after the row's first tick, while a sample plays (modplay.trem_delta):
; the volume played is 4 * volume + |wave| * depth / 8 with the wave's sign,
; within 0..256, and not at volume 0; the position moves on by the speed.
mp_trem:
        ld a, (ix + CH_PLAY)
        or a
        ret z
        ld a, (ix + CH_VOL)
        or a
        jr z, tm_adv
        ld a, (ix + CH_MPOS)
        ld b, (ix + CH_MTYP)
        call mp_wave
        ld a, (ix + CH_MDEP)
        call mp_mul4
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        ld e, h                     ; E = |wave| * depth / 8 (at most 238)
        ld d, 0
        ld l, (ix + CH_VOL)
        ld h, d
        add hl, hl
        add hl, hl
        inc c
        jr z, tm_neg
        add hl, de
        ld de, 257
        push hl
        sbc hl, de                  ; (no carry)
        pop hl
        jr c, tm_st
        ld hl, 256
        jr tm_st
tm_neg: sbc hl, de
        jr nc, tm_st
        ld hl, 0
tm_st:  ld (ix + CH_V4), l
        ld (ix + CH_V4 + 1), h
        ld (ix + CH_TRM), 1
        ld (ix + CH_LV), 0xFF       ; (the level is worked out again)
tm_adv: ld a, (ix + CH_MPOS)
        add a, (ix + CH_MSPD)
        and 63
        ld (ix + CH_MPOS), a
        ret

; A = position 0..63, B = waveform (E4x / E7x) -> E = |wave| (0..127), C =
; FFh if the wave is negative (the second half, or the random table's sign),
; else 0 (modplay.wave; the ramp as vibrato plays it, upside down, and turned
; over by the vibrato position's half: ProTracker's tremolo reads that one).
; Page 2 = the tables. Changes AF, C, DE, HL.
mp_wave:
        ld e, a
        ld a, b
        and 3
        jr z, wv_sin
        dec a
        jr z, wv_ramp
        dec a
        jr z, wv_sq
        ld a, e                     ; 3: the random table (signed)
        ld hl, MP_TAB + MP_T_RANDOM
        call mp_addhl
        ld a, (hl)
        ld c, 0
        or a
        jp p, wv_e
        neg
        dec c
wv_e:   ld e, a
        ret
wv_sq:  ld a, e                     ; 2: square, 127
        ld e, 127
        jr wv_hf
wv_ramp:                            ; 1: ramp, 4 * (position & 31), turned over
        ld a, e                     ; in the vibrato position's second half
        add a, a
        add a, a
        and 0x7C
        bit 5, (ix + CH_VPOS)
        jr z, wv_r1
        xor 0x7F
wv_r1:  ld d, e
        ld e, a
        ld a, d
        jr wv_hf
wv_sin: ld a, e                     ; 0: sine, its first half from the table
        and 31
        ld hl, MP_TAB + MP_T_SINE
        call mp_addhl
        ld a, e
        ld e, (hl)
wv_hf:  and 32                      ; the second half: negative
        ld c, 0
        ret z
        dec c
        ret

; HL = min(HL, DE) (the tone portamento's target). Changes AF.
mp_mindst:
        push hl
        or a
        sbc hl, de
        pop hl
        ret c
        ex de, hl
        push hl
        pop de
        ret

; A = sample (0: nothing), C = note, HL = start (bytes): the sample starts
; (modplay.Player.trigger); an empty one stops the channel. Either way the
; vibrato and tremolo restart (unless their waveform says not to).
mp_trig:
        ld b, (ix + CH_FT)
mp_trigb:                           ; (B = the finetune)
        or a
        ret z
        ld (ix + CH_CUR), a
        ld (ix + CH_NOTE), c
        bit 2, (ix + CH_VTYP)
        jr nz, tg_0
        ld (ix + CH_VPOS), 0
tg_0:   bit 2, (ix + CH_MTYP)
        jr nz, tg_0a
        ld (ix + CH_MPOS), 0
tg_0a:  push hl
        call mp_sinfo
        pop hl
        ld a, (iy + SI_LEN)
        or (iy + SI_LEN + 1)
        jr nz, tg_1
        ld (ix + CH_PLAY), a
        ld (ix + CH_TRIG), a
        inc a
        ld (ix + CH_STOP), a
        ret
tg_1:   push hl
        ld a, c
        call mp_pon
        ld (ix + CH_PER), l
        ld (ix + CH_PER + 1), h
        pop hl
        call mp_clamp               ; at most the sample's end - 1
        ld (ix + CH_TPOS), l
        ld (ix + CH_TPOS + 1), h
        ld a, (ix + CH_CUR)
        ld (ix + CH_TS), a
        ld a, 1
        ld (ix + CH_PLAY), a
        ld (ix + CH_TRIG), a
        xor a
        ld (ix + CH_STOP), a
        ret

; the last note again, from its start (plus the stacked offset): the row's
; instrument number's sample (ProTracker plays the one it loaded), else the
; one it played, at the finetune of the row's start (CH_RFT)
mp_retrig:
        ld a, (ix + CH_CUR)
        or a
        ret z
        ld c, (ix + CH_NOTE)
        inc c
        dec c
        ret z
        ld a, (ix + CH_S)
        or a
        jr nz, rt_1
        ld a, (ix + CH_CUR)
rt_1:   ld l, (ix + CH_STACK)
        ld h, (ix + CH_STACK + 1)
        ld b, (ix + CH_RFT)
        jp mp_trigb

; DE = 4 * A. Changes AF.
mp_x4:  ld e, a
        ld d, 0
        sla e
        rl d
        sla e
        rl d
        ret

; period -= DE, at least QMIN (with a period)
mp_perdn:
        ld l, (ix + CH_PER)
        ld h, (ix + CH_PER + 1)
        ld a, h
        or l
        ret z
        or a
        sbc hl, de
        jr c, pd_min
        push hl
        ld de, QMIN
        sbc hl, de
        pop hl
        jr nc, pd_st
pd_min: ld hl, QMIN
pd_st:  ld (ix + CH_PER), l
        ld (ix + CH_PER + 1), h
        ret

; period += DE, at most QMAX (with a period)
mp_perup:
        ld l, (ix + CH_PER)
        ld h, (ix + CH_PER + 1)
        ld a, h
        or l
        ret z
        add hl, de
        push hl
        ld de, QMAX + 1
        or a
        sbc hl, de
        pop hl
        jr c, pd_st
        ld hl, QMAX
        jr pd_st

mp_volup:                           ; volume += A, at most 64
        add a, (ix + CH_VOL)
        cp 65
        jr c, vu_1
        ld a, 64
vu_1:   ld (ix + CH_VOL), a
        ret

mp_voldn:                           ; volume -= A, at least 0
        ld b, a
        ld a, (ix + CH_VOL)
        sub b
        jr nc, vu_1
        xor a
        jr vu_1

; ----------------------------------------------------------------------------
; What a channel's tick means for the OPL4 (modplay.Rt.schedule). A note
; starts on the next of its 3 OPL4 channels, prepared now (pitch, level,
; tone number); the coming tick keys it on and the old one off (mp_lkon,
; mp_lkoff). Otherwise a stop keys it off, and a new pitch or level waits
; for the tick (mp_lupd); with the volume, fade level and period of the
; last time there is nothing to work out.
mp_cmap:
        ld a, (ix + CH_TRIG)
        or a
        jp nz, cm_note
        ld a, (ix + CH_KEYED)
        or a
        ret z                       ; nothing sounds
        ld a, (ix + CH_STOP)
        or a
        jr z, cm_1
        xor a                       ; an empty sample: off
        ld (ix + CH_KEYED), a
        call mp_och
        ld hl, mp_lkoff
        jp mp_pushoff
cm_1:   call mp_pplay               ; HL = the period played
        ld a, l
        cp (ix + CH_LPP)
        jr nz, cm_2
        ld a, h
        cp (ix + CH_LPP + 1)
        jr z, cm_4                  ; the same period: the same pitch
cm_2:   ld (ix + CH_LPP), l
        ld (ix + CH_LPP + 1), h
        ld a, h
        or l
        jr z, cm_4                  ; no period: the pitch stays
        call mp_pitch
        ld a, d
        cp (ix + CH_R38)
        jr nz, cm_3
        ld a, e
        cp (ix + CH_R20)
        jr z, cm_4
cm_3:   ld (ix + CH_R38), d
        ld (ix + CH_R20), e
        call mp_och
        ld b, a
        add a, 0x38
        ld c, a
        ld a, d
        ld hl, mp_lupd
        call mp_push                ; octave, F-number 9-7 first
        ld a, b
        add a, 0x20
        ld c, a
        ld a, (ix + CH_R20)
        ld hl, mp_lupd
        call mp_push                ; F-number 6-0, tone bit 8
cm_4:   ld a, (ix + CH_VOL)         ; the same volume and fade level: the
        cp (ix + CH_LV)             ; same level (a tremolo tick leaves FFh)
        jr nz, cm_5
        ld a, (mp_g64)
        cp (ix + CH_LG)
        ret z
cm_5:   call mp_lvlnow
        cp (ix + CH_LVL)
        ret z
        ld (ix + CH_LVL), a
        ld b, a
        call mp_och
        add a, 0x50
        ld c, a
        ld a, b
        ld hl, mp_lupd
        jp mp_push
cm_note:
        ld a, (mp_heavy)
        inc a                       ; (a note: 1 unit more)
        ld (mp_heavy), a
        call mp_lvlnow              ; its level, pitch and tone (and the period
        ld (ix + CH_LVL), a         ; they are worked out from)
        call mp_pplay
        ld (ix + CH_LPP), l
        ld (ix + CH_LPP + 1), h
        call mp_pitch
        ld (ix + CH_R38), d
        ld (ix + CH_R20), e
        ld c, (ix + CH_TS)
        ld l, (ix + CH_TPOS)
        ld h, (ix + CH_TPOS + 1)
        ld a, h
        or l
        jr z, cn_1                  ; from its start: tone s - 1
        call mp_tfind               ; a 9xx start: its tone (or, not found,
        jr z, cn_2                  ; the start's)
cn_1:   ld a, c
        dec a
cn_2:   ld (mp_tn), a
        ld a, (ix + CH_KEYED)       ; the OPL4 channel that plays: off
        or a
        jr z, cn_3
        call mp_och
        ld hl, mp_lkoff
        call mp_pushoff
cn_3:   ld a, (ix + CH_SLOT)        ; the next one: on
        inc a
        cp 3
        jr c, cn_4
        xor a
cn_4:   ld (ix + CH_SLOT), a
        ld a, 1
        ld (ix + CH_KEYED), a
        call mp_och
        ld d, a                     ; D = c + 8 * slot
        ld hl, mp_lkon
        call mp_pushon
        call mp_ld                  ; prepared now (after any header load)
        ld a, d
        add a, 0x38
        ld c, a
        ld a, (ix + CH_R38)
        call mp_ww
        ld a, d
        add a, 0x20
        ld c, a
        ld a, (ix + CH_R20)
        call mp_ww
        ld a, d
        add a, 0x50
        ld c, a
        ld a, (ix + CH_LVL)
        call mp_ww
        ld hl, mp_stone             ; its tone, unless already loaded there
        ld a, d
        call mp_addhl
        ld a, (mp_tn)
        cp (hl)
        ret z
        ld (hl), a
        or 0x80                     ; tone 384 + the index: bits 7-0 (bit 8 is
        ld (mp_ldval), a            ; in register 20h)
        ld a, d
        add a, 0x08
        ld (mp_ldreg), a
        in a, (MP_FM_A0)            ; after any header still loading (else a
        and 2                       ; later poll writes it)
        ret nz
        jp mp_ldput

; HL = a list, A = OPL4 channel: key it on / off (68h + A = pan | A0h / 20h:
; the LFO off). Changes AF, C, E, HL.
mp_pushon:
        add a, 0x68
        ld c, a
        ld a, (ix + CH_PAN)
        or 0xA0
        jp mp_push
mp_pushoff:
        add a, 0x68
        ld c, a
        ld a, (ix + CH_PAN)
        or 0x20
        jp mp_push

; HL = the period played: the arpeggio's or the vibrato's, else the channel's
mp_pplay:
        ld l, (ix + CH_PER)
        ld h, (ix + CH_PER + 1)
        ld a, (ix + CH_ARP)
        or a
        ret z
        ld l, (ix + CH_APER)
        ld h, (ix + CH_APER + 1)
        ret

; A = the channel's OPL4 channel now: c + 8 * slot
mp_och: ld a, (ix + CH_SLOT)
        add a, a
        add a, a
        add a, a
        add a, (ix + CH_IDX)
        ret

; ----------------------------------------------------------------------------
; Tables and arithmetic (page 2 = the table bank for mp_pon, mp_pnote,
; mp_nop, mp_pitch, mp_level)

; A = note, B = finetune -> HL = its period (1/4 units): without finetune,
; notes 24..107 from ProTracker's octave table (MP_T_OCT), else the tuned
; octave 0 (MP_T_TUNED) shifted (modplay.period_of_note). Keeps BC.
mp_pon: ld e, a
        ld a, b
        or a
        jr nz, po_t
        ld a, e
        sub 24
        jr c, po_t
        cp 84
        jr nc, po_t
        add a, a
        ld l, a
        ld h, 0
        ld de, MP_TAB + MP_T_OCT
        add hl, de
        ld a, (hl)
        inc hl
        ld h, (hl)
        ld l, a
        add hl, hl
        add hl, hl
        ret
po_t:   ld a, e                     ; (TUNED[ft][n % 12] << 5) >> (n / 12)
        ld d, 0
po_12:  cp 12
        jr c, po_12e
        sub 12
        inc d
        jr po_12
po_12e: ld e, a
        ld a, b
        add a, a
        add a, a
        ld l, a
        add a, a
        add a, l
        add a, e
        ld l, a
        ld h, 0
        add hl, hl
        push de
        ld de, MP_TAB + MP_T_TUNED
        add hl, de
        ld a, (hl)
        inc hl
        ld h, (hl)
        ld l, a
        pop de
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        add hl, hl
        ld a, d
        or a
        ret z
po_sh:  srl h
        rr l
        dec a
        jr nz, po_sh
        ret

; HL = a pattern period -> A = its note: the first entry of ProTracker's
; table (C-1 .. B-3) it is not below. Keeps HL. Changes BC, DE.
mp_pnote:
        ld bc, 36 * 256             ; binary search: B = high end, C = low end
pn_1:   ld a, c
        cp b
        jr nc, pn_e
        add a, b
        srl a                       ; the middle
        push hl
        push af
        add a, a
        ld e, a
        ld d, 0
        ld hl, MP_TAB + MP_T_PT
        add hl, de
        ld e, (hl)
        inc hl
        ld d, (hl)
        pop af
        pop hl
        push hl
        or a
        sbc hl, de
        pop hl
        jr c, pn_lo                 ; below its entry: further on
        ld b, a
        jr pn_1
pn_lo:  inc a
        ld c, a
        jr pn_1
pn_e:   ld a, c
        cp 36
        jr c, pn_2
        dec a                       ; below them all: the last (B-3)
pn_2:   add a, NOTE_C1
        ret

; HL = period, B = finetune -> A = the lowest note whose period is <= it
; (binary search over notes 0..119). Keeps B.
mp_nop: ld (mp_nq), hl
        xor a
        ld (mp_nlo), a
        ld a, 120
        ld (mp_ncnt), a
no_1:   ld a, (mp_ncnt)
        or a
        jr z, no_e
        srl a
        ld (mp_nstep), a
        ld hl, mp_nlo
        add a, (hl)
        ld c, a                     ; C = mid
        call mp_pon
        ld a, h
        or l
        jr z, no_hi
        ex de, hl
        ld hl, (mp_nq)
        or a
        sbc hl, de
        jr c, no_hi                 ; its period > the period
        ld a, (mp_nstep)
        ld (mp_ncnt), a
        jr no_1
no_hi:  ld a, c
        inc a
        ld (mp_nlo), a
        ld a, (mp_nstep)
        inc a
        ld e, a
        ld a, (mp_ncnt)
        sub e
        ld (mp_ncnt), a
        jr no_1
no_e:   ld a, (mp_nlo)
        ret

; HL = period (FFFFh: 65536) -> D = register 38h, E = 20h (the table)
mp_pitch:
        ld a, h
        cp 0x10
        jr c, pt_t
        inc hl
        ld a, h
        or l
        ld hl, MP_TAB + MP_T_P64K
        jr z, pt_r
        ld hl, 4095
pt_t:   add hl, hl
        ld de, MP_TAB + MP_T_PITCH
        add hl, de
pt_r:   ld d, (hl)
        inc hl
        ld e, (hl)
        ret

; A = register 50h for the volume played now (the tremolo's, else 4 * the
; volume), and what it is worked out from (CH_LV: the volume, FFh for the
; tremolo's; CH_LG: the fade level). Changes DE, HL.
mp_lvlnow:
        ld a, (mp_g64)
        ld (ix + CH_LG), a
        ld a, (ix + CH_TRM)
        or a
        jr nz, ln_t
        ld a, (ix + CH_VOL)
        ld (ix + CH_LV), a
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        jr mp_level
ln_t:   ld (ix + CH_LV), 0xFF
        ld l, (ix + CH_V4)
        ld h, (ix + CH_V4 + 1)

; HL = volume played 0..256 (1/4 units) -> A = register 50h: its TL plus the
; fade's. Changes DE, HL.
mp_level:
        ld de, MP_TAB + MP_T_VOLTL
        add hl, de
        ld e, (hl)
        ld a, (mp_g64)
        ld hl, MP_TAB + MP_T_FADETL
        call mp_addhl
        ld a, (hl)
        add a, e
        cp 127
        jr c, lv_1
        ld a, 0xFF                  ; silent
        ret
lv_1:   add a, a
        inc a                       ; TL << 1 | level direct
        ret

; HL += A. Changes AF.
mp_addhl:
        add a, l
        ld l, a
        ret nc
        inc h
        ret

; A = sample 1..31 -> IY = its mp_si entry. Changes AF, DE, HL.
mp_sinfo:
        dec a
        ld l, a
        ld h, 0
        add hl, hl
        add hl, hl
        ld d, h
        ld e, l
        add hl, hl
        add hl, de
        ld de, mp_si
        add hl, de
        push hl
        pop iy
        ret

; A = channel -> IX = its mp_ch entry. Changes AF, DE.
mp_chix:
        ld ix, mp_ch
        or a
        ret z
        ld de, CH_SIZE
cx_1:   add ix, de
        dec a
        jr nz, cx_1
        ret

; HL = E * A (A = 0..15). Changes AF, D.
mp_mul4:
        ld hl, 0
        ld d, h
        rlca
        rlca
        rlca
        rlca
        rla
        jr nc, m4_1
        add hl, de
m4_1:   add hl, hl
        rla
        jr nc, m4_2
        add hl, de
m4_2:   add hl, hl
        rla
        jr nc, m4_3
        add hl, de
m4_3:   add hl, hl
        rla
        ret nc
        add hl, de
        ret

; HL = E * A. Changes AF, B, D.
mp_mul8:
        ld hl, 0
        ld d, 0
        ld b, 8
m8_1:   add hl, hl
        rla
        jr nc, m8_2
        add hl, de
m8_2:   djnz m8_1
        ret

; HL = a cell (4 bytes) -> mp_cs sample, mp_cp period, mp_ce effect,
; mp_cx parameter. Changes AF, HL.
mp_cell:
        ld a, (hl)
        and 0x0F
        ld (mp_cp + 1), a
        ld a, (hl)
        and 0xF0
        ld (mp_cs), a
        inc hl
        ld a, (hl)
        ld (mp_cp), a
        inc hl
        ld a, (hl)
        and 0x0F
        ld (mp_ce), a
        ld a, (hl)
        rrca
        rrca
        rrca
        rrca
        and 0x0F
        push hl
        ld hl, mp_cs
        or (hl)
        ld (hl), a
        pop hl
        inc hl
        ld a, (hl)
        ld (mp_cx), a
        ret

; A = pattern, C = row -> mp_rowb = its cells, from the MOD (offset 1084 +
; (pattern * 64 + row) * channels * 4). Changes AF, BC, DE, HL.
mp_rowrd:
        call mp_rowseek
        ld de, mp_rowb
        ld a, (mp_rowsz)
        ld b, a
rr_1:   call mp_rd
        ld (de), a
        inc de
        djnz rr_1
        ret

; A = pattern, C = row -> page 2 = the bank of its cells, HL = their
; address (mp_rbank). Changes AF, BC, DE.
mp_rowseek:
        push bc
        ld e, a
        ld a, (mp_nch)
        call mp_mul8                ; HL = pattern * channels
        pop bc
        push hl
        ld a, (mp_rowsz)
        ld e, c
        call mp_mul8                ; HL = row * channels * 4 (< 2048)
        ld bc, 1084
        add hl, bc
        pop bc                      ; + pattern * channels * 256
        ld a, h
        add a, c
        ld h, a
        ld a, b
        adc a, 0
        ld e, a                     ; E:HL = the row's offset
        jp mp_seek

; E:HL = offset in the MOD -> page 2 = its bank, HL = its address there.
; Changes AF, BC, E.
mp_seek:
        ld bc, MP_MODLIN & 0xFFFF
        add hl, bc
        ld a, e
        adc a, MP_MODLIN >> 16
        ld e, a
        ld a, h
        rlca
        rlca
        and 3
        ld c, a
        ld a, e
        add a, a
        add a, a
        or c
        ld (mp_rbank), a
        call mp_setbank
        ld a, h
        and 0x3F
        or 0x80
        ld h, a
        ret

; A = the byte at HL (page 2), HL to the next one (the next bank's 8000h
; after BFFFh).
mp_rd:  ld a, (hl)
        inc hl
        bit 6, h
        ret z
        push af
        ld a, (mp_rbank)
        inc a
        ld (mp_rbank), a
        call mp_setbank
        ld h, 0x80
        pop af
        ret

; ----------------------------------------------------------------------------
; Ports

mp_ld:                              ; until no tone header loads (LD = 0)
        ld b, 0                     ; at most 2.6 ms
ml_1:   in a, (MP_FM_A0)
        and 2
        ret z
        djnz ml_1
        ret

mp_adr:                             ; sample memory address A:H:L (bits 21-0)
        ld c, 3
        call mp_ww
        ld c, 4
        ld a, h
        call mp_ww
        ld c, 5                     ; register 5 last: it latches the address
        ld a, l
; Register accesses wait for BUSY (status bit 0) before the select and before
; the data: never seen at 3.58 MHz (an OUT takes longer than the chip's
; 2.6 us), but a faster CPU needs it. BUSY is valid with NEW2 = 1 only, and
; an empty port reads FFh (BUSY for ever): only after mod_detect found the
; OPL4. Keep A, B, DE, HL.
mp_ww:                              ; wave register C = A
        push af
mw_1:   in a, (MP_FM_A0)
        rrca
        jr c, mw_1
        ld a, c
        out (MP_WAVE_A), a
mw_2:   in a, (MP_FM_A0)
        rrca
        jr c, mw_2
        pop af
        out (MP_WAVE_D), a
        ret

mp_wr:                              ; A = wave register C
        call mp_sel
mr_2:   in a, (MP_FM_A0)
        rrca
        jr c, mr_2
        in a, (MP_WAVE_D)
        ret

mp_sel:                             ; select wave register C
mr_1:   in a, (MP_FM_A0)
        rrca
        jr c, mr_1
        ld a, c
        out (MP_WAVE_A), a
        ret

mp_fw:                              ; FM bank 0 register C = A
        push af
mf_1:   in a, (MP_FM_A0)
        rrca
        jr c, mf_1
        ld a, c
        out (MP_FM_A0), a
mf_2:   in a, (MP_FM_A0)
        rrca
        jr c, mf_2
        pop af
        out (MP_FM_D0), a
        ret

; FM bank 1 register 5 (NEW, NEW2) = A. BUSY is not valid until NEW2 = 1, so
; fixed pauses let the chip take the select and the data (10 us at 3.58
; MHz, about 1.7 us on the R800: the FM part needs 1.65 us).
mp_new2:
        push af
        ld a, 5
        out (MP_FM_A1), a
        ex (sp), hl
        ex (sp), hl
        pop af
        out (MP_FM_D1), a
        ex (sp), hl
        ex (sp), hl
        ret
