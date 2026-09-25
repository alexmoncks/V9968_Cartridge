; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Alex Moncks
; ============================================================================
; g3bank1.asm  -  bank 1 of the geo3d BASIC ROM (6000h-7FFFh), included by
; g3basic.asm: the scene commands, G3DATA, G3FRAME and their math.
;
; Bank 1 is at 6000h whenever a command of ours runs (INIT maps it, and the
; STATEMENT handler maps it again before every command). Only getsin (bank
; 0) maps another bank there (the sine table, bank 2), and it maps bank 1
; back before it returns; the H.TIMI handler only runs bank 0 code.
;
; Registers: IX = work area and IY = work area + XB (the Y_ fields, 896 to
; 1151) after wkptr. Code that points IX elsewhere (an object, the G3DATA
; block) calls the bank 0 VDP routines through x_ce/x_geo, or points IX back
; at the work area first (wkix). Every BASIC call (CALBAS, the trampoline,
; EXTROM) changes IX and IY: they are set again right after it.
;
; Numbers: angles are 16-bit, 65536 units per turn. Matrices and unit
; vectors are Q2.14 (16384 = 1.0). The rotation of an object is
; R = Ry(ay) * Rx(ax) * Rz(az) (spec 3.2), with
;   Ry = [cy 0 sy; 0 1 0; -sy 0 cy]      ay = 90: the front (+Z) turns to +X
;   Rx = [1 0 0; 0 cx sx; 0 -sx cx]      ax > 0: the front goes up
;   Rz = [cz sz 0; -sz cz 0; 0 0 1]      az > 0: the top leans right
; A camera at C looking at the point L has the rows right, up, forward:
;   f = d / |d|, d = L - C;  r = (dz, 0, -dx) / |(dx, dz)|;  u = f x r
; and geo3d gets M = Cam * R and T = Cam * (P - C) for an object at P, and
; the light Cam * (the unit vector of W_LIGHT). Every sum of products is
; rounded once: sum + 8192, >> 14. g3ref.py repeats this integer math bit
; for bit (test_math.py runs these routines in a Z80 emulator against it,
; check_frames.py the openMSX frames).
; Speed (3.58 MHz Z80, T-states without wait states): rotmat 30.5K, camset
; 86K (G3CAM only), a 255-face model spinning: 20 frames per second from a
; DEFINT BASIC loop of G3ROT and G3FRAME (run_tests.sh measures it).
; ============================================================================

        org 0x6000
bank1:  defb 0x00, 0xFF         ; never "AB"

; ============================================================================
; Pointers
; ============================================================================

; wkix: IX = work area. Keeps all but IX.
wkix:   push hl
        call getblk
        push hl
        pop ix
        pop hl
        ret

; ixfy: IX = IY - XB, the work area (IY set). Keeps all but IX.
ixfy:   push de
        push iy
        pop ix
        ld de,0 - XB
        add ix,de
        pop de
        ret

; wkptr: IX = work area, IY = IX + XB, Y_PORT = W_PORT. Keeps BC, DE, HL.
wkptr:  call wkix
        push hl
        push de
        push ix
        pop hl
        ld de,XB
        add hl,de
        push hl
        pop iy
        ld a,(ix+W_PORT)
        ld (iy+Y_PORT),a
        pop de
        pop hl
        ret

; dbptr: IX = the G3DATA block (work area + W_DB). Keeps all but IX.
dbptr:  push hl
        push de
        push iy
        pop hl
        ld de,W_DB - XB
        add hl,de
        push hl
        pop ix
        pop de
        pop hl
        ret

; objadr: HL = object A (1-16). Keeps BC, DE.
objadr: push de
        dec a
        ld l,a
        ld h,0
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        ld d,h
        ld e,l
        add hl,hl
        add hl,de               ; (A - 1) * 48
        ld de,W_OBJ - XB
        add hl,de
        push iy
        pop de
        add hl,de
        pop de
        ret

; mdadr: HL = directory entry of model A (16-31). Keeps BC, DE.
mdadr:  push de
        sub 16
        add a,a
        add a,a
        add a,a
        add a,a
        ld e,a
        ld d,0
        push iy
        pop hl
        add hl,de
        ld de,W_MDIR - XB
        add hl,de
        pop de
        ret

; adr_y: HL = IY + A (A signed). Keeps BC, DE.
adr_y:  push de
        push iy
        pop hl
        ld e,a
        rla
        sbc a,a
        ld d,a
        add hl,de
        pop de
        ret

; ixadr: HL = IX + A (A unsigned). Keeps BC, DE.
ixadr:  push de
        push ix
        pop hl
        ld e,a
        ld d,0
        add hl,de
        pop de
        ret

; ixw: DE = the word at IX + A. Keeps BC, HL.
ixw:    push hl
        call ixadr
        ld e,(hl)
        inc hl
        ld d,(hl)
        pop hl
        ret

; ixws: the word at IX + A = DE. Keeps BC, DE, HL.
ixws:   push hl
        call ixadr
        ld (hl),e
        inc hl
        ld (hl),d
        pop hl
        ret

; x_ce, x_geo: wait_ce, wait_geo with IX pointing anywhere.
x_ce:   push ix
        call wkix
        call wait_ce
        pop ix
        ret
x_geo:  push ix
        call wkix
        call wait_geo
        pop ix
        ret

; ============================================================================
; Arguments (after getargs; IY set)
; ============================================================================

; arg: DE = argument A (0-7); carry set when that position was empty.
; Keeps BC, HL.
arg:    push hl
        push bc
        ld c,a
        push iy
        pop hl
        ld de,W_ARGC - XB
        add hl,de
        ld b,(hl)               ; bits: given
        inc hl
        inc hl                  ; W_ARGV
        add a,a
        ld e,a
        ld d,0
        add hl,de
        ld e,(hl)
        inc hl
        ld d,(hl)
        ld a,b
        inc c
ar_1:   rrca
        dec c
        jr nz,ar_1
        ccf
        pop bc
        pop hl
        ret

; argreq: DE = argument A; Syntax error when it was not given.
argreq: call arg
        jp c,err_sn
        ret

; argz: DE = argument A, or 0 when it was not given.
argz:   call arg
        ret nc
        ld de,0
        ret

; ubyte: A = DE when 0 <= DE <= A, else Illegal function call.
ubyte:  inc d
        dec d
        jp nz,err_fc
        cp e
        jp c,err_fc
        ld a,e
        ret

; argobj: A = argument 0, an object number (1-16), HL = that object.
argobj: xor a
        call argreq
        ld a,NOBJ
        call ubyte
        or a
        jp z,err_fc
        push af
        call objadr
        pop af
        ret

; argobju: argobj for an object that exists (G3OBJ), else Illegal
; function call.
argobju:
        call argobj
        bit 0,(hl)
        jp z,err_fc
        ret

; putargs: for arguments A to A+B-1: the word at HL = that argument when it
; was given (unchanged otherwise), HL += 2.
putargs:
        push bc
        push af
        call arg
        jr c,pa_1
        ld (hl),e
        inc hl
        ld (hl),d
        dec hl
pa_1:   inc hl
        inc hl
        pop af
        inc a
        pop bc
        djnz putargs
        ret

; ============================================================================
; G3OBJ(n, m [, x, y, z])  (spec section 4)
;   n: 1-16. m: 0 (pivot, never drawn) or 16-31 (a model made by G3DATA);
;   the built-in models 1-12 are not in this version: Illegal function call,
;   as for 13-15 and for a model that is not defined. x, y, z: -32768 to
;   32767, 0 when left out. The object starts with angles 0, no spin, size
;   100, the model's colours, solid (wireframe for a model without faces)
;   and visible. No VDP access.
; ============================================================================
g3obj:  ld b,5
        xor a
        call getargs
        push hl
        call wkptr
        ld a,1
        call argreq
        ld a,31
        call ubyte
        ld c,a                  ; m
        ld b,0                  ; style: wireframe
        or a
        jr z,go_1
        cp 16
        jp c,err_fc             ; built-in models: not yet
        call mdadr
        bit 0,(hl)
        jp z,err_fc             ; not defined
        inc hl
        inc hl
        ld a,(hl)               ; MD_NF
        or a
        jr z,go_1
        inc b                   ; faces: solid
go_1:   push bc
        ld a,2
        call argz
        push de
        ld a,3
        call argz
        push de
        ld a,4
        call argz
        push de
        call argobj             ; all checked from here on
        push hl
        ld bc,OBJSZ
        call zero
        pop ix
        pop de
        ld (ix+O_POS+4),e
        ld (ix+O_POS+5),d
        pop de
        ld (ix+O_POS+2),e
        ld (ix+O_POS+3),d
        pop de
        ld (ix+O_POS),e
        ld (ix+O_POS+1),d
        pop bc
        ld (ix+O_MODEL),c
        ld (ix+O_STYLE),b
        ld (ix+O_COLOR),0xFF
        ld (ix+O_SIZE),100
        ld (ix+O_FLAGS),1       ; in use, visible
        pop hl
        or a
        ret

; ============================================================================
; G3POS(n, x, y, z [, ax, ay, az])   position (and angles)
; G3ROT(n, ax, ay, az)              angles in degrees
; G3SPIN(n, dax, day, daz)          degrees added at every G3FRAME
; Angles take fractions and any value (dacang). An angle position left
; empty keeps its value. The object must exist (G3OBJ).
; ============================================================================
g3pos:  ld b,7
        ld a,0x70               ; arguments 4-6 are angles
        call getargs
        push hl
        call wkptr
        ld a,1
        call argreq
        ld a,2
        call argreq
        ld a,3
        call argreq
        call argobju
        push hl
        ld de,O_POS
        add hl,de
        ld a,1
        ld b,3
        call putargs
        pop hl
        push hl
        ld de,O_ANG
        add hl,de
        ld a,4
        ld b,3
        call putargs
        pop hl
        res 2,(hl)              ; the rotation is worked out again
        pop hl
        or a
        ret

g3rot:  ld de,O_ANG
        jr rs_go
g3spin: ld de,O_SPIN
rs_go:  push de
        ld b,4
        ld a,0x0E               ; arguments 1-3 are angles
        call getargs
        pop de
        push hl
        push de
        call wkptr
        call argobju
        pop de
        push hl                 ; the object
        push de                 ; the field
        add hl,de
        ld a,1
        ld b,3
        call putargs
        pop de
        pop hl
        ld a,e
        cp O_ANG
        jr nz,ro_1
        res 2,(hl)              ; new angles: the rotation is worked out again
ro_1:   pop hl
        or a
        ret

; ============================================================================
; G3STYLE(n, s [, t])  s: 0 wireframe, 1 solid shaded. Styles 2 and 3
; (textures) are not in this version: Illegal function call. t: 0-8, kept
; for the texture styles (not used yet).
; ============================================================================
g3style:
        ld b,3
        xor a
        call getargs
        push hl
        call wkptr
        ld a,1
        call argreq
        ld a,3
        call ubyte
        cp 2
        jp nc,err_fc            ; textures: not yet
        ld c,a
        ld a,2
        call arg
        jr c,sy_1
        ld a,8
        call ubyte
sy_1:   push bc
        call argobju
        pop bc
        inc hl
        inc hl                  ; O_STYLE
        ld (hl),c
        pop hl
        or a
        ret

; ============================================================================
; G3CAM(x, y, z)  the camera at (x, y, z), looking at the origin (the
; G3LOOK point). The camera matrix and the light in camera space are worked
; out here, once.
; ============================================================================
g3cam:  ld b,3
        xor a
        call getargs
        push hl
        call wkptr
        xor a
        call argreq
        ld a,1
        call argreq
        ld a,2
        call argreq
        push ix
        pop hl
        ld de,W_CAM
        add hl,de
        xor a
        ld b,3
        call putargs
        call camset
        pop hl
        or a
        ret

; camset: Y_CMAT (right, up, forward rows), Y_CAMI and Y_LCAM from W_CAM,
; W_LOOK and W_LIGHT. IX = work area.
camset: push ix
        pop hl
        ld de,W_LOOK
        add hl,de               ; HL -> look point (the camera is 6 before)
        ld a,Y_N
        ld b,3
cs_1:   push bc
        push af                 ; destination
        ld e,(hl)
        inc hl
        ld d,(hl)               ; DE = look
        inc hl
        push hl                 ; next look word
        ld bc,0 - 8
        add hl,bc
        ld c,(hl)
        inc hl
        ld b,(hl)               ; BC = camera
        ex de,hl
        or a
        sbc hl,bc               ; look - camera, low 16 bits
        call s17                ; A = the sign byte of the true difference
        ld e,a
        pop bc                  ; next look word
        pop af                  ; destination
        push af
        push bc
        push hl
        call adr_y
        pop bc                  ; the difference
        ld (hl),c
        inc hl
        ld (hl),b
        inc hl
        ld (hl),e
        inc hl
        ld (hl),e
        pop hl
        pop af
        add a,4
        pop bc
        djnz cs_1
        call mags
        ld (iy+Y_SGN),c
        call shift3
        jp z,cs_id              ; camera at the point it looks at
        ld a,Y_D
        call sgn16              ; Y_D: dx, dy, dz scaled into 2^14..2^15
        ; dh = |(dx, dz)|, dl = |d|
        ld a,Y_D
        call sqw                ; DE:HL = dx^2
        call sqacc0
        ld a,Y_D+4
        call sqw
        call sqacc
        call sqld
        call rsqrt
        ld (iy+Y_DH),l
        ld (iy+Y_DH+1),h
        ld a,Y_D+2
        call sqw
        call sqacc
        call sqld
        call rsqrt
        ld (iy+Y_DL),l
        ld (iy+Y_DL+1),h
        ld a,(iy+Y_DH)
        or (iy+Y_DH+1)
        jp z,cs_vert            ; straight up or down
        ld hl,camq
        ld b,5
cs_2:   push bc
        ld a,(hl)               ; destination
        inc hl
        push af
        ld a,(hl)               ; numerator
        inc hl
        call fetchw
        push de
        ld a,(hl)               ; denominator
        inc hl
        push hl
        call fetchw
        ld b,d
        ld c,e
        pop hl
        pop de
        push hl
        call qdiv               ; HL = DE * 16384 / BC, rounded
        ex de,hl
        pop hl
        pop af
        push hl
        call adr_y
        ld (hl),e
        inc hl
        ld (hl),d
        pop hl
        pop bc
        djnz cs_2
        ld e,(iy+Y_DH)          ; uy = dh / dl: both up to 56755 (unsigned)
        ld d,(iy+Y_DH+1)
        ld c,(iy+Y_DL)
        ld b,(iy+Y_DL+1)
        call qdivu
        ld (iy+Y_UY),l
        ld (iy+Y_UY+1),h
        ; C: right = (rz, 0, -rx), up = (-fy*rx, uy, -fy*rz), fwd = (fx, fy, fz)
        ld hl,camtab
        call qeval
        ld a,(iy+Y_RZ)
        ld (iy+Y_CMAT),a
        ld a,(iy+Y_RZ+1)
        ld (iy+Y_CMAT+1),a
        xor a
        ld (iy+Y_CMAT+2),a
        ld (iy+Y_CMAT+3),a
        ld a,Y_RX
        call fetchw
        call negde
        ld (iy+Y_CMAT+4),e
        ld (iy+Y_CMAT+5),d
        ld a,(iy+Y_UY)
        ld (iy+Y_CMAT+8),a
        ld a,(iy+Y_UY+1)
        ld (iy+Y_CMAT+9),a
        ld a,Y_FX
        call adr_y
        ex de,hl
        ld a,Y_CMAT+12
        call adr_y
        ex de,hl
        ld bc,6
        ldir                    ; fx, fy, fz
        jr cs_cami
cs_vert:                        ; right (1,0,0), up (0,0,-s), fwd (0,s,0)
        ld hl,cmat_i
        call cs_set
        ld de,16384
        bit 1,(iy+Y_SGN)        ; the sign of dy
        jr z,cs_v1
        call negde
cs_v1:  ld (iy+Y_CMAT+14),e
        ld (iy+Y_CMAT+15),d
        call negde
        ld (iy+Y_CMAT+10),e
        ld (iy+Y_CMAT+11),d
        xor a
        ld (iy+Y_CMAT+8),a
        ld (iy+Y_CMAT+9),a
        ld (iy+Y_CMAT+16),a
        ld (iy+Y_CMAT+17),a
        jr cs_cami
cs_id:  ld hl,cmat_i
        call cs_set
cs_cami:                        ; Y_CAMI: 1 when C is the identity
        ld a,Y_CMAT
        call adr_y
        ld de,cmat_i
        ld b,18
        ld c,1
cs_3:   ld a,(de)
        cp (hl)
        jr z,cs_4
        ld c,0
cs_4:   inc hl
        inc de
        djnz cs_3
        ld (iy+Y_CAMI),c
        ; the light: unit vector (world), then C * L (camera space)
        push ix
        pop hl
        ld de,W_LIGHT
        add hl,de
        ld a,Y_N
        ld b,3
cs_5:   push bc
        push af
        ld e,(hl)
        inc hl
        ld d,(hl)
        inc hl
        push hl
        call adr_y
        ld (hl),e
        inc hl
        ld (hl),d
        inc hl
        ld a,d
        rla
        sbc a,a
        ld (hl),a
        inc hl
        ld (hl),a
        pop hl
        pop af
        add a,4
        pop bc
        djnz cs_5
        call nrm3
        ld hl,cltab
        jp qeval

; cs_set: Y_CMAT = the 18 bytes at HL.
cs_set: push hl
        ld a,Y_CMAT
        call adr_y
        ex de,hl
        pop hl
        ld bc,18
        ldir
        ret

cmat_i: defw 16384, 0, 0, 0, 16384, 0, 0, 0, 16384

; camq: destination, numerator, denominator of the camera ratios (signed
; numerators; dh / dl follows, unsigned)
camq:   defb Y_RX, Y_D, Y_DH            ; dx / dh
        defb Y_RZ, Y_D+4, Y_DH          ; dz / dh
        defb Y_FX, Y_D, Y_DL            ; dx / dl
        defb Y_FY, Y_D+2, Y_DL          ; dy / dl
        defb Y_FZ, Y_D+4, Y_DL          ; dz / dl

camtab: defb Y_CMAT+6, 1,  1, Y_FY, Y_RX        ; up x = -fy * rx
        defb Y_CMAT+10, 1, 1, Y_FY, Y_RZ        ; up z = -fy * rz
        defb 0x7F

cltab:  defb Y_LCAM+0, 3,  0, Y_CMAT+0, Y_NQ+0,  0, Y_CMAT+2, Y_NQ+2,  0, Y_CMAT+4, Y_NQ+4
        defb Y_LCAM+2, 3,  0, Y_CMAT+6, Y_NQ+0,  0, Y_CMAT+8, Y_NQ+2,  0, Y_CMAT+10, Y_NQ+4
        defb Y_LCAM+4, 3,  0, Y_CMAT+12, Y_NQ+0, 0, Y_CMAT+14, Y_NQ+2, 0, Y_CMAT+16, Y_NQ+4
        defb 0x7F

; s17: after HL = a - b (sbc, 16 bits): A = 00h or FFh, the sign of the
; true 17-bit difference (bit 15 of HL, flipped on a signed overflow).
s17:    ld a,h
        jp po,s7_1
        cpl
s7_1:   rla
        sbc a,a
        ret

; sqw: DE:HL = (word at IY + A) squared.
sqw:    call fetchw
        ld b,d
        ld c,e
        jp mul16s

; sqacc0, sqacc: Y_SQ = DE:HL, Y_SQ += DE:HL (32 bits). sqld: DE:HL = Y_SQ.
sqacc0: xor a
        ld (iy+Y_SQ),a
        ld (iy+Y_SQ+1),a
        ld (iy+Y_SQ+2),a
        ld (iy+Y_SQ+3),a
sqacc:  ld a,(iy+Y_SQ)
        add a,l
        ld (iy+Y_SQ),a
        ld a,(iy+Y_SQ+1)
        adc a,h
        ld (iy+Y_SQ+1),a
        ld a,(iy+Y_SQ+2)
        adc a,e
        ld (iy+Y_SQ+2),a
        ld a,(iy+Y_SQ+3)
        adc a,d
        ld (iy+Y_SQ+3),a
        ret
sqld:   ld l,(iy+Y_SQ)
        ld h,(iy+Y_SQ+1)
        ld e,(iy+Y_SQ+2)
        ld d,(iy+Y_SQ+3)
        ret

; negde: DE = -DE.
negde:  xor a
        sub e
        ld e,a
        sbc a,a
        sub d
        ld d,a
        ret

; ============================================================================
; G3RAMP(c, r, g, b)  a ramp of 7 tones in colours c..c+6 (spec 5.3): tone
; k = round(colour * (k + 1) / 7). c: 1-9, r, g, b: 0-7. c becomes a ramp
; start and the starts it overlaps stop being ramps. 98h: through the
; SUB-ROM (BASIC's palette table follows). The faces of the models defined
; so far follow the new ramp set (a flat colour has a zero normal), and the
; next G3FRAME sends them to geo3d again.
; ============================================================================
g3ramp: ld b,4
        xor a
        call getargs
        push hl
        call wkptr
        xor a
        call argreq
        ld a,9
        call ubyte
        or a
        jp z,err_fc
        ld (iy+Y_TT),a          ; c
        ld a,1
        call argreq
        ld a,7
        call ubyte
        ld (iy+Y_TT+1),a        ; r
        ld a,2
        call argreq
        ld a,7
        call ubyte
        ld (iy+Y_TT+2),a        ; g
        ld a,3
        call argreq
        ld a,7
        call ubyte
        ld (iy+Y_TT+3),a        ; b
        ld (iy+Y_TT+4),1        ; k + 1
rp_1:   ld a,(iy+Y_TT+1)
        call rnd7
        add a,a
        add a,a
        add a,a
        add a,a
        push af
        ld a,(iy+Y_TT+3)
        call rnd7
        pop bc
        or b
        push af                 ; red * 16 + blue
        ld a,(iy+Y_TT+2)
        call rnd7
        ld e,a                  ; green
        ld a,(iy+Y_TT)
        add a,(iy+Y_TT+4)
        dec a
        ld d,a                  ; colour c + k
        pop af
        call palone
        call wkptr
        inc (iy+Y_TT+4)
        ld a,(iy+Y_TT+4)
        cp 8
        jr c,rp_1
        ; ramp starts: those in c-6..c+6 go, c comes
        ld c,(iy+Y_TT)
        ld l,(iy+Y_RAMPS)
        ld h,(iy+Y_RAMPS+1)
        ld de,1                 ; bit of colour B
        ld b,0
rp_2:   ld a,b
        sub c
        jr nc,rp_3
        neg
rp_3:   cp 7
        jr nc,rp_4              ; |B - c| > 6: kept
        ld a,e
        cpl
        and l
        ld l,a
        ld a,d
        cpl
        and h
        ld h,a
rp_4:   ld a,b
        cp c
        jr nz,rp_5
        ld a,e
        or l
        ld l,a
        ld a,d
        or h
        ld h,a
rp_5:   sla e
        rl d
        inc b
        ld a,b
        cp 16
        jr c,rp_2
        ld (iy+Y_RAMPS),l
        ld (iy+Y_RAMPS+1),h
        call rescan
        ld (iy+Y_RESF),0        ; the faces geo3d holds have the old normals
        pop hl
        or a
        ret

; ============================================================================
; G3PAL(c, r, g, b)  colour c (0-15) = (r, g, b), 0-7 each (spec 5.3): like
; COLOR=(c,r,g,b), but on the V9968 in both profiles. 98h: through the
; SUB-ROM (BASIC's palette table follows). The ramp starts stay as they are.
; ============================================================================
g3pal:  ld b,4
        xor a
        call getargs
        push hl
        call wkptr
        xor a
        call argreq
        ld a,15
        call ubyte
        ld (iy+Y_TT),a          ; c
        ld a,1
        call argreq
        ld a,7
        call ubyte
        add a,a
        add a,a
        add a,a
        add a,a
        ld (iy+Y_TT+1),a        ; r * 16
        ld a,2
        call argreq
        ld a,7
        call ubyte
        ld (iy+Y_TT+2),a        ; g
        ld a,3
        call argreq
        ld a,7
        call ubyte
        or (iy+Y_TT+1)          ; r * 16 + b
        ld e,(iy+Y_TT+2)
        ld d,(iy+Y_TT)
        call palone
        pop hl
        or a
        ret

; rnd7: A = round(A * (k + 1) / 7), k + 1 = (Y_TT+4).
rnd7:   ld b,(iy+Y_TT+4)
        ld c,a
        xor a
r7_1:   add a,c
        djnz r7_1
        add a,3
        ld c,0xFF
r7_2:   inc c
        sub 7
        jr nc,r7_2
        ld a,c
        ret

; palone: colour D = A (red * 16 + blue), E (green). IX = work area. 98h:
; SETPLT (IX, IY changed). 88h: straight to the V9968.
palone: push de
        push af
        ld a,(ix+W_PORT)
        cp 0x98
        jr z,pa_98
        ld a,d
        ld b,16
        call vreg               ; R#16 = colour
        ld a,(ix+W_PORT)
        add a,2
        ld c,a
        pop af
        pop de
        di
        out (c),a
        out (c),e
        ei
        ret
pa_98:  pop af
        pop de
        ld ix,SETPLT
        jp EXTROM

; isramp: NZ (A = 1) when colour A (0-15) starts a ramp. Keeps BC, DE, HL.
isramp: push hl
        push bc
        ld c,a
        ld l,(iy+Y_RAMPS)
        ld h,(iy+Y_RAMPS+1)
        inc c
ir_1:   dec c
        jr z,ir_2
        srl h
        rr l
        jr ir_1
ir_2:   ld a,l
        and 1
        pop bc
        pop hl
        ret

; ============================================================================
; Math
; ============================================================================

; mul16u: DE:HL = BC * DE, unsigned (shift and add, unrolled).
mul16u: ld hl,0
        add hl,hl               ; bit 15 (HL = 0: no carry out)
        rl e
        rl d
        jr nc,mu_1
        ld h,b
        ld l,c
mu_1:   add hl,hl               ; bits 14 to 0
        rl e
        rl d
        jr nc,mu_2
        add hl,bc
        jr nc,mu_2
        inc de
mu_2:   add hl,hl
        rl e
        rl d
        jr nc,mu_3
        add hl,bc
        jr nc,mu_3
        inc de
mu_3:   add hl,hl
        rl e
        rl d
        jr nc,mu_4
        add hl,bc
        jr nc,mu_4
        inc de
mu_4:   add hl,hl
        rl e
        rl d
        jr nc,mu_5
        add hl,bc
        jr nc,mu_5
        inc de
mu_5:   add hl,hl
        rl e
        rl d
        jr nc,mu_6
        add hl,bc
        jr nc,mu_6
        inc de
mu_6:   add hl,hl
        rl e
        rl d
        jr nc,mu_7
        add hl,bc
        jr nc,mu_7
        inc de
mu_7:   add hl,hl
        rl e
        rl d
        jr nc,mu_8
        add hl,bc
        jr nc,mu_8
        inc de
mu_8:   add hl,hl
        rl e
        rl d
        jr nc,mu_9
        add hl,bc
        jr nc,mu_9
        inc de
mu_9:   add hl,hl
        rl e
        rl d
        jr nc,mu_10
        add hl,bc
        jr nc,mu_10
        inc de
mu_10:  add hl,hl
        rl e
        rl d
        jr nc,mu_11
        add hl,bc
        jr nc,mu_11
        inc de
mu_11:  add hl,hl
        rl e
        rl d
        jr nc,mu_12
        add hl,bc
        jr nc,mu_12
        inc de
mu_12:  add hl,hl
        rl e
        rl d
        jr nc,mu_13
        add hl,bc
        jr nc,mu_13
        inc de
mu_13:  add hl,hl
        rl e
        rl d
        jr nc,mu_14
        add hl,bc
        jr nc,mu_14
        inc de
mu_14:  add hl,hl
        rl e
        rl d
        jr nc,mu_15
        add hl,bc
        jr nc,mu_15
        inc de
mu_15:  add hl,hl
        rl e
        rl d
        ret nc
        add hl,bc
        ret nc
        inc de
        ret

; mul16s: DE:HL = BC * DE, signed. Changes A, BC.
mul16s: ld a,b
        xor d
        push af                 ; bit 7: the sign of the product
        bit 7,b
        jr z,ms_1
        xor a
        sub c
        ld c,a
        sbc a,a
        sub b
        ld b,a
ms_1:   bit 7,d
        call nz,negde
        call mul16u
        pop af
        ret p
; neg32: DE:HL = -DE:HL.
neg32:  xor a
        sub l
        ld l,a
        ld a,0
        sbc a,h
        ld h,a
        ld a,0
        sbc a,e
        ld e,a
        ld a,0
        sbc a,d
        ld d,a
        ret

; div32: HL = DE:HL / BC, DE = remainder, unsigned; DE < BC on entry.
div32:  ld a,16
dv_1:   add hl,hl
        rl e
        rl d
        jr c,dv_2               ; 17 bits: more than BC anyway
        ex de,hl
        or a
        sbc hl,bc
        jr nc,dv_3
        add hl,bc
        ex de,hl
        dec a
        jr nz,dv_1
        ret
dv_2:   ex de,hl
        or a
        sbc hl,bc
dv_3:   ex de,hl
        inc l                   ; quotient bit
        dec a
        jr nz,dv_1
        ret

; rsqrt: HL = round(sqrt(DE:HL)), DE:HL < 3.2e9 (Newton from 65535,
; then rounded: +1 when DE:HL - x^2 > x). Uses Y_SQ.
rsqrt:  ld (iy+Y_SQ),l
        ld (iy+Y_SQ+1),h
        ld (iy+Y_SQ+2),e
        ld (iy+Y_SQ+3),d
        ld a,h
        or l
        or d
        or e
        ret z                   ; sqrt(0) = 0
        ld bc,0xFFFF            ; a start at or above sqrt(s)
        ld a,d
        cp 0x40
        jr nc,rq_1              ; s >= 2^30
        ld bc,0x8000
        or a
        jr nz,rq_1              ; s >= 2^24
        ld bc,0x1000
        or e
        jr nz,rq_1              ; s >= 2^16
        ld bc,0x0100
rq_1:   call sqld
        call div32              ; HL = s / x
        add hl,bc
        rr h
        rr l                    ; y = (x + s / x) / 2
        or a
        sbc hl,bc
        jr nc,rq_2              ; y >= x: x = floor(sqrt(s))
        add hl,bc
        ld b,h
        ld c,l
        jr rq_1
rq_2:   push bc
        ld d,b
        ld e,c
        call mul16u             ; x^2
        ld a,(iy+Y_SQ)
        sub l
        ld l,a
        ld a,(iy+Y_SQ+1)
        sbc a,h
        ld h,a
        ld a,(iy+Y_SQ+2)
        sbc a,e                 ; A:HL = s - x^2 (0 .. 2x)
        pop bc
        or a
        jr nz,rq_up
        sbc hl,bc
        jr z,rq_3
        jr c,rq_3
rq_up:  inc bc
rq_3:   ld h,b
        ld l,c
        ret

; qdiv: HL = round(DE * 16384 / BC), signed DE, |DE| <= BC, BC > 0.
qdiv:   ld a,d
        push af
        bit 7,d
        call nz,negde
        call qdivu
        pop af
        rla
        ret nc
        ex de,hl
        call negde
        ex de,hl
        ret

; qdivu: HL = (DE * 16384 + BC / 2) / BC, 0 <= DE <= BC.
qdivu:  ld a,e
        and 3
        rrca
        rrca
        ld h,a
        ld l,0                  ; low word of DE << 14
        srl d
        rr e
        srl d
        rr e                    ; high word
        push bc
        srl b
        rr c
        add hl,bc
        jr nc,qu_1
        inc de
qu_1:   pop bc
        jp div32

; fetchw: DE = the word at IY + A. Keeps BC, HL.
fetchw: push hl
        call adr_y
        ld e,(hl)
        inc hl
        ld d,(hl)
        pop hl
        ret

; acc_add: Y_ACC += DE:HL.
acc_add:
        ld a,(iy+Y_ACC)
        add a,l
        ld (iy+Y_ACC),a
        ld a,(iy+Y_ACC+1)
        adc a,h
        ld (iy+Y_ACC+1),a
        ld a,(iy+Y_ACC+2)
        adc a,e
        ld (iy+Y_ACC+2),a
        ld a,(iy+Y_ACC+3)
        adc a,d
        ld (iy+Y_ACC+3),a
        ret

; qeval: sums of Q2.14 products from the table at HL. Each entry: the
; destination (IY displacement), the number of terms, then per term: flags
; (bit 0: subtract), the two operands (IY displacements). The destination
; gets (sum + 8192) >> 14; Y_OVF = 1 when that does not fit 16 bits. 7Fh
; ends the table. Keeps IX.
qeval:  push ix
        push hl
        pop ix
qe_0:   ld a,(ix+0)
        cp 0x7F
        jr z,qe_end
        push af                 ; destination
        ld b,(ix+1)
        inc ix
        inc ix
        ld (iy+Y_ACC),0
        ld (iy+Y_ACC+1),0x20    ; 8192: rounding
        ld (iy+Y_ACC+2),0
        ld (iy+Y_ACC+3),0
qe_1:   push bc
        ld a,(ix+1)             ; BC = word at IY + first operand
        ld c,a
        rla
        sbc a,a
        ld b,a
        push iy
        pop hl
        add hl,bc
        ld c,(hl)
        inc hl
        ld b,(hl)
        ld a,(ix+2)             ; DE = word at IY + second operand
        ld e,a
        rla
        sbc a,a
        ld d,a
        push iy
        pop hl
        add hl,de
        ld e,(hl)
        inc hl
        ld d,(hl)
        call mul16s
        bit 0,(ix+0)
        call nz,neg32
        call acc_add
        inc ix
        inc ix
        inc ix
        pop bc
        djnz qe_1
        ld l,(iy+Y_ACC)
        ld h,(iy+Y_ACC+1)
        ld e,(iy+Y_ACC+2)
        ld d,(iy+Y_ACC+3)
        ld a,d
        and 0xE0
        jr z,qe_2
        cp 0xE0
        jr z,qe_2
        ld (iy+Y_OVF),1         ; does not fit 16 bits
qe_2:   add hl,hl
        rl e
        rl d
        add hl,hl
        rl e
        rl d                    ; DE = sum >> 14
        pop af
        call adr_y
        ld (hl),e
        inc hl
        ld (hl),d
        jr qe_0
qe_end: pop ix
        ret

; ---- three 32-bit values at Y_N: magnitudes, scaling, unit vector -----------

; mags: the three signed values at Y_N become magnitudes; C = their signs
; (bit 2: first, bit 1: second, bit 0: third).
mags:   ld a,Y_N
        call adr_y
        ld c,0
        ld b,3
mg_1:   inc hl
        inc hl
        inc hl
        ld a,(hl)
        dec hl
        dec hl
        dec hl
        rla                     ; carry = sign
        rl c
        bit 0,c
        call nz,negm
        inc hl
        inc hl
        inc hl
        inc hl
        djnz mg_1
        ret

; negm: the 32-bit value at HL negated. Keeps BC, HL.
negm:   push hl
        push bc
        ld b,4
        or a
ng_1:   ld a,0
        sbc a,(hl)
        ld (hl),a
        inc hl
        djnz ng_1
        pop bc
        pop hl
        ret

; or3: C:B:D:E = the OR of the three magnitudes (bytes 3, 2, 1, 0).
or3:    ld a,Y_N
        call adr_y
        ld bc,0
        ld de,0
        ld a,3
o3_1:   push af
        ld a,(hl)
        or e
        ld e,a
        inc hl
        ld a,(hl)
        or d
        ld d,a
        inc hl
        ld a,(hl)
        or b
        ld b,a
        inc hl
        ld a,(hl)
        or c
        ld c,a
        inc hl
        pop af
        dec a
        jr nz,o3_1
        ret

; shift3: the magnitudes at Y_N scaled by a power of two until their OR is
; in 2^14..2^15-1 (shifting right drops the low bits). Z: all three are 0.
shift3: call or3
        ld a,c
        or b
        or d
        or e
        ret z
        ld h,0                  ; bits to shift: + right, - left
sh_1:   ld a,c
        or b
        jr nz,sh_2
        bit 7,d
        jr nz,sh_2
        bit 6,d
        jr nz,sh_3
        sla e                   ; below 2^14
        rl d
        dec h
        jr sh_1
sh_2:   srl c                   ; 2^15 or more
        rr b
        rr d
        rr e
        inc h
        jr sh_1
sh_3:   ld a,h
        or a
        jr z,sh_9
        jp m,sh_6
sh_4:   cp 8                    ; right: whole bytes first
        jr c,sh_5
        push af
        ld a,Y_N
        call adr_y
        ld b,3
sh_4a:  inc hl
        ld a,(hl)
        dec hl
        ld (hl),a
        inc hl
        inc hl
        ld a,(hl)
        dec hl
        ld (hl),a
        inc hl
        inc hl
        ld a,(hl)
        dec hl
        ld (hl),a
        inc hl
        ld (hl),0
        inc hl
        djnz sh_4a
        pop af
        sub 8
        jr sh_4
sh_5:   or a
        jr z,sh_9
        push af
        ld a,Y_N+11
        call adr_y
        ld b,3
sh_5a:  srl (hl)
        dec hl
        rr (hl)
        dec hl
        rr (hl)
        dec hl
        rr (hl)
        dec hl
        djnz sh_5a
        pop af
        dec a
        jr sh_5
sh_6:   neg                     ; left (the values are below 2^14 then)
sh_7:   push af
        ld a,Y_N
        call adr_y
        ld b,3
sh_7a:  sla (hl)
        inc hl
        rl (hl)
        inc hl
        rl (hl)
        inc hl
        rl (hl)
        inc hl
        djnz sh_7a
        pop af
        dec a
        jr nz,sh_7
sh_9:   or 1                    ; NZ
        ret

; sgn16: at IY + A: the three magnitudes (low words) with the signs of
; Y_SGN, as words.
sgn16:  ld c,a
        ld d,(iy+Y_SGN)
        ld b,0
sg_2:   ld a,b
        add a,a
        add a,a
        add a,Y_N
        call adr_y
        ld a,(hl)
        inc hl
        ld h,(hl)
        ld l,a                  ; magnitude
        bit 2,d
        jr z,sg_3
        push de
        ex de,hl
        call negde
        ex de,hl
        pop de
sg_3:   push de
        ex de,hl                ; DE = the signed value
        ld a,b
        add a,a
        add a,c
        call adr_y
        ld (hl),e
        inc hl
        ld (hl),d
        pop de
        sla d                   ; the next sign to bit 2
        inc b
        ld a,b
        cp 3
        jr c,sg_2
        ret

; nrm3: Y_NQ = the vector of the three signed 32-bit values at Y_N scaled
; to length 16384 (Q2.14 unit vector, rounded), or zero. Y_N is changed.
nrm3:   call mags
        ld (iy+Y_SGN),c
        call shift3
        jr nz,nm_1
        ld a,Y_NQ
        call adr_y
        ld b,6
nm_0:   ld (hl),0
        inc hl
        djnz nm_0
        ret
nm_1:   ld b,0                  ; s = sum of the squares
nm_2:   push bc
        ld a,b
        add a,a
        add a,a
        add a,Y_N
        call fetchw
        ld b,d
        ld c,e
        call mul16u
        pop bc
        push bc
        ld a,b
        or a
        push af
        call z,sqacc0
        pop af
        call nz,sqacc
        pop bc
        inc b
        ld a,b
        cp 3
        jr c,nm_2
        call sqld
        call rsqrt
        ld b,h
        ld c,l                  ; BC = length
        ld a,0
nm_3:   push af
        add a,a
        add a,a
        add a,Y_N
        call fetchw
        call qdivu              ; HL = magnitude * 16384 / length
        pop af
        push af
        ex de,hl
        ld h,a
        ld a,(iy+Y_SGN)
        inc h
nm_4:   dec h
        jr z,nm_5
        add a,a
        jr nm_4
nm_5:   bit 2,a
        call nz,negde
        pop af
        push af
        add a,a
        add a,Y_NQ
        push bc
        call adr_y
        pop bc
        ld (hl),e
        inc hl
        ld (hl),d
        pop af
        inc a
        cp 3
        jr c,nm_3
        ret

; ============================================================================
; Scene state at G3INIT (spec step 6 and 7): no models, the ramps of the
; default palette (1, 8), the default camera and light, no flip pending, the
; upload loop in RAM. IX = work area.
; ============================================================================
scnx:   call wkptr
        push iy
        pop hl
        ld de,W_CMAT - XB
        add hl,de
        ld bc,W_END - W_CMAT
        call zero
        call wkptr
        ld (iy+Y_RAMPS),0x02    ; colours 1 and 8
        ld (iy+Y_RAMPS+1),0x01
        ld (ix+W_FLIP),0
        ld (ix+W_BLANK),0
        ld hl,(JIFFY)
        ld (ix+W_LASTJ),l
        ld (ix+W_LASTJ+1),h
        call camset
        ; the upload loop: IN A,(P) / OUT (P+7),A / DJNZ / DEC D / JR NZ / RET
        push iy
        pop hl
        ld de,W_UK - XB
        add hl,de
        ld a,(ix+W_PORT)
        ld (hl),0xDB
        inc hl
        ld (hl),a
        inc hl
        ld (hl),0xD3
        inc hl
        add a,7
        ld (hl),a
        inc hl
        ld (hl),0x10
        inc hl
        ld (hl),0xFA
        inc hl
        ld (hl),0x15
        inc hl
        ld (hl),0x20
        inc hl
        ld (hl),0xF7
        inc hl
        ld (hl),0xC9
        ; the DATA number reader, run through the trampoline (page 1 =
        ; BASIC): RST 10h (CHRGTR), CALL FIN, DEC HL, RST 10h, RET
        inc hl                  ; W_RN
        ld de,rn_tpl
        ex de,hl
        ld bc,7
        ldir
        ret

rn_tpl: defb 0xD7, 0xCD, FIN & 0xFF, FIN >> 8, 0x2B, 0xD7, 0xC9

; ============================================================================
; VRAM (the V9968 at Y_PORT)
; ============================================================================

; vaddr: VRAM address A:HL (A = bits 17-16) for reading (carry clear) or
; writing (carry set). Keeps BC, DE, HL.
vaddr:  push bc
        ld b,a
        ld a,0
        rla
        rrca
        rrca                    ; 40h when writing
        push af
        ld a,b
        add a,a
        add a,a
        ld b,a
        ld a,h
        rlca
        rlca
        and 3
        or b
        ld b,a                  ; R#14 = A17-A14
        ld a,(iy+Y_PORT)
        inc a
        ld c,a
        di
        out (c),b
        ld a,14 + 0x80
        out (c),a
        out (c),l
        pop af
        ld b,a
        ld a,h
        and 0x3F
        or b
        out (c),a
        ei
        pop bc
        ret

; vmr, vmw: VRAM address of offset HL of the model area (page 7, 38000h),
; for reading or writing. Keeps BC, DE, HL.
vmr:    or a
        jr vm_1
vmw:    scf
vm_1:   push hl
        push af
        ld a,h
        or 0x80
        ld h,a
        pop af
        ld a,3
        call vaddr
        pop hl
        ret

; vout: B bytes from HL to VRAM; vin: B bytes from VRAM to HL. More than
; 29 T-states between two accesses (V9938 timing).
vout:   ld a,(iy+Y_PORT)
        ld c,a
vo_1:   ld a,(hl)
        out (c),a
        inc hl
        djnz vo_1
        ret
vin:    ld a,(iy+Y_PORT)
        ld c,a
vi_1:   in a,(c)
        ld (hl),a
        inc hl
        djnz vi_1
        ret

; addmul: HL += A * DE (shift and add). Keeps BC, DE.
addmul: or a
        ret z
        push de
am_1:   srl a
        jr nc,am_2
        add hl,de
am_2:   jr z,am_3               ; no bits left (ADD HL keeps Z)
        ex de,hl
        add hl,hl
        ex de,hl
        jr am_1
am_3:   pop de
        ret

; vcmd: the command block at W_TMP to R#A.. (B bytes). The command engine
; must be free.
vcmd:   push ix
        call wkix
        push af
        push iy
        pop hl
        ld de,W_TMP - XB
        add hl,de
        pop af
        call vind
        pop ix
        ret

; tmpadr: HL = W_TMP. Keeps BC, DE.
tmpadr: push de
        push iy
        pop hl
        ld de,W_TMP - XB
        add hl,de
        pop de
        ret

; vclr: HMMV with colour 0 of B lines from the top of page A; waits for it.
vclr:   push bc
        push af
        call x_ce
        call tmpadr
        xor a
        ld (hl),a               ; DX
        inc hl
        ld (hl),a
        inc hl
        ld (hl),a               ; DY = page * 256
        inc hl
        pop af
        ld (hl),a
        inc hl
        ld (hl),0               ; NX = 256
        inc hl
        ld (hl),1
        inc hl
        pop bc
        ld (hl),b               ; NY
        inc hl
        xor a
        ld (hl),a
        inc hl
        ld (hl),a               ; CLR
        inc hl
        ld (hl),a               ; ARG
        inc hl
        ld (hl),0xC0            ; HMMV
        ld a,36
        ld b,11
        call vcmd
        jp x_ce

; ============================================================================
; The model area: VRAM page 7 (38000h-3FFFFh, 32 KB). A model is stored
; from a 128-byte boundary, in geo3d's own format:
;   vertices  6 bytes each: X, Y, Z (little-endian words)
;   faces    11 bytes each: I0-I3, NX, NY, NZ (Q2.14, of the order stored),
;            colour (a colour that is no ramp start gets the normal 0: geo3d
;            then always picks its tone 0, the colour itself)
;   UV        8 bytes per face (G3DATA with t = 1)
;   edges     2 bytes each (the model's own, or made from the faces)
; Models lie one after the other from offset 0 to W_MEND; freeing one moves
; the ones after it down (HMMM of whole lines).
; Directory entry (W_MDIR, 16 bytes per model 16-31):
;   +0 flags: bit 0 defined, bit 1 UV, bit 2 edges made from the faces,
;      bit 3 background model (G3DATA option 2)
;   +1 vertices  +2 faces  +3 wireframe colour (first face, or 15)
;   +4 edges (word)  +6 offset (word)  +8 size (word)  +10 coordinate shift
; ============================================================================
MD_FLAGS: equ 0
MD_NV:    equ 1
MD_NF:    equ 2
MD_COL:   equ 3
MD_NE:    equ 4
MD_OFF:   equ 6
MD_SIZE:  equ 8
MD_K:     equ 10

; resinv: geo3d no longer holds model A. Keeps all but F.
resinv: cp (iy+Y_RESV)
        jr nz,rv_1
        ld (iy+Y_RESV),0
rv_1:   cp (iy+Y_RESF)
        jr nz,rv_2
        ld (iy+Y_RESF),0
rv_2:   cp (iy+Y_RESE)
        ret nz
        ld (iy+Y_RESE),0
        ret

; mfree: model A undefined; its space goes to the models after it.
mfree:  push af
        call mdadr
        bit 0,(hl)
        jr nz,mf_1
        pop af
        ret
mf_1:   pop af
        call resinv
        ld (hl),0               ; not defined
        ld de,MD_OFF
        add hl,de
        ld e,(hl)
        inc hl
        ld d,(hl)               ; DE = offset
        inc hl
        ld c,(hl)
        inc hl
        ld b,(hl)               ; BC = size
        ld (iy+Y_D),e
        ld (iy+Y_D+1),d
        ld (iy+Y_D+2),c
        ld (iy+Y_D+3),b
        ex de,hl
        add hl,bc
        ex de,hl                ; DE = end of the model
        ld l,(iy+Y_MEND)
        ld h,(iy+Y_MEND+1)
        or a
        sbc hl,de               ; bytes after it
        jr z,mf_2
        ; HMMM: lines 1792 + end / 128 ... up to 1792 + offset / 128
        call l128
        push af                 ; lines to move
        ex de,hl
        call l128
        ld l,a
        ld h,0
        ld de,1792
        add hl,de
        ex de,hl                ; DE = SY
        ld l,(iy+Y_D)
        ld h,(iy+Y_D+1)
        call l128
        ld l,a
        ld h,0
        ld bc,1792
        add hl,bc               ; HL = DY
        pop af
        call hmmm
mf_2:   ld a,16                 ; the models after it: offset -= size
mf_3:   push af
        call mdadr
        bit 0,(hl)
        jr z,mf_4
        ld de,MD_OFF
        add hl,de
        ld e,(hl)
        inc hl
        ld d,(hl)               ; its offset
        push hl
        ex de,hl
        ld e,(iy+Y_D)
        ld d,(iy+Y_D+1)
        or a
        sbc hl,de
        jr c,mf_5               ; before the freed one: stays
        add hl,de
        ld e,(iy+Y_D+2)
        ld d,(iy+Y_D+3)
        or a
        sbc hl,de
        ex de,hl
        pop hl
        ld (hl),d
        dec hl
        ld (hl),e
        jr mf_4
mf_5:   pop hl
mf_4:   pop af
        inc a
        cp 32
        jr c,mf_3
        ld l,(iy+Y_MEND)        ; W_MEND -= size
        ld h,(iy+Y_MEND+1)
        ld e,(iy+Y_D+2)
        ld d,(iy+Y_D+3)
        or a
        sbc hl,de
        ld (iy+Y_MEND),l
        ld (iy+Y_MEND+1),h
        ret

; l128: A = HL / 128 (HL a multiple of 128, below 8000h... up to 8000h).
l128:   ld a,l
        rla
        ld a,h
        rla
        ret

; hmmm: HMMM of A whole lines (256 pixels) from line DE to line HL (both
; in V9968 11-bit Y), top to bottom (the lines move up: HL < DE). Waits.
hmmm:   push af
        push hl
        push de
        call x_ce
        call tmpadr
        xor a
        ld (hl),a               ; SX
        inc hl
        ld (hl),a
        inc hl
        pop de
        ld (hl),e               ; SY
        inc hl
        ld (hl),d
        inc hl
        ld (hl),a               ; DX
        inc hl
        ld (hl),a
        inc hl
        pop de
        ld (hl),e               ; DY
        inc hl
        ld (hl),d
        inc hl
        ld (hl),a               ; NX = 256
        inc hl
        ld (hl),1
        inc hl
        pop af
        ld (hl),a               ; NY
        inc hl
        ld (hl),0
        inc hl
        xor a
        ld (hl),a               ; CLR
        inc hl
        ld (hl),a               ; ARG
        inc hl
        ld (hl),0xD0            ; HMMM
        ld a,32
        ld b,15
        call vcmd
        jp x_ce

; ============================================================================
; G3DATA(m [, o])  model m (16-31) from the program's DATA, from the current
; READ position (spec 5.5): nv, nf, ne, t; nv x (x, y, z); nf x (a, b, c, d,
; colour), a triangle repeats c; ne x (a, b); if t = 1, nf x (u0, v0, u1,
; v1, u2, v2, u3, v3). READ goes on right after the model.
;   o (default 1): bit 0: each face is turned to face away from the centre
;   of the model (the mean of its vertices), right for convex shapes; bit
;   1: background model (drawn before the others).
;   Limits: 1-255 vertices, 0-255 faces and edges (a model without faces
;   needs edges), t 0 or 1, vertex numbers below nv, colours 0-15 (SCREEN
;   5), UV 0-255. Coordinates -32768..32767, rounded.
; Numbers are read as BASIC's READ reads them: BASIC's DATA scan (485Bh),
; CHRGTR and FIN (3299h, through the trampoline), so every form READ takes
; (&H, exponents, a sign) works. Errors: Out of DATA; Syntax error in the
; DATA line for an item that is not a number (as READ gives it); Illegal
; function call for a value out of range; Out of memory when the model area
; is full; Overflow outside -32768..32767. 98h: Illegal function call
; unless BASIC is in SCREEN 5 (the VDP commands below need a bitmap mode).
; A model m already defined is freed first; after an error m is undefined.
; Without edges (ne = 0) the wireframe style uses the faces' sides: each
; one once, found with a bitmap (256 x 256 bits, lines 0-63 of the page
; G3FRAME draws on next: G3FRAME clears it anyway; cleared again here).
; ============================================================================
Q_M:    equ 0
Q_O:    equ 1
Q_NV:   equ 2
Q_NF:   equ 3
Q_NE:   equ 4                   ; word
Q_T:    equ 6
Q_OFF:  equ 7                   ; word
Q_FOFF: equ 9                   ; word
Q_UOFF: equ 11                  ; word
Q_EOFF: equ 13                  ; word
Q_I:    equ 15
Q_K:    equ 16
Q_SUM:  equ 17                  ; 3 x 24 bits
Q_MAX:  equ 26                  ; word
Q_CEN:  equ 28                  ; 3 words
Q_REC:  equ 34                  ; 11 bytes
Q_IDX:  equ 45                  ; 4 bytes
Q_PERM: equ 49
Q_COL:  equ 50
Q_DIST: equ 51
Q_TMP:  equ 52                  ; 8 bytes
Q_V:    equ 60                  ; 4 x 3 words
Q_A:    equ 84                  ; 3 words
Q_B:    equ 90                  ; 3 words
Q_UV:   equ 96                  ; 8 bytes
Q_BM:   equ 104                 ; bitmap page
Q_FL:   equ 105                 ; directory flags
Q_J:    equ 106
Q_END:  equ 107

g3data: ld b,2
        xor a
        call getargs
        push hl
        call wkptr
        xor a
        call argreq
        ld a,31
        call ubyte
        cp 16
        jp c,err_fc
        ld c,a
        ld a,1
        call arg
        jr nc,da_1
        ld de,1
da_1:   ld a,3
        call ubyte
        ld b,a
        call scr5               ; 98h: the HMMV and HMMM below need SCREEN 5
        push bc
        call wait_geo
        call wait_ce
        call flip_wait
        call pgsync
        ld a,(ix+W_DRAW)
        pop bc
        call dbptr
        ld (ix+Q_BM),a
        ld (ix+Q_M),c
        ld (ix+Q_O),b
        ld (ix+Q_COL),15
        ld (ix+Q_FL),1
        ld a,b
        and 2
        jr z,da_2
        set 3,(ix+Q_FL)         ; background
da_2:   ; header
        call rdb
        or a
        jp z,err_fc             ; no vertices
        ld (ix+Q_NV),a
        call rdb
        ld (ix+Q_NF),a
        call rdb
        ld (ix+Q_NE),a
        ld (ix+Q_NE+1),0
        or (ix+Q_NF)
        jp z,err_fc             ; no faces and no edges: nothing to draw
        call rdnum
        ld a,1
        call ubyte
        ld (ix+Q_T),a
        or a
        jr z,da_3
        set 1,(ix+Q_FL)
da_3:   ld a,(ix+Q_M)
        call mfree
        ; layout
        ld l,(iy+Y_MEND)
        ld h,(iy+Y_MEND+1)
        ld (ix+Q_OFF),l
        ld (ix+Q_OFF+1),h
        ld a,(ix+Q_NV)
        ld de,6
        call addmul
        ld (ix+Q_FOFF),l
        ld (ix+Q_FOFF+1),h
        ld a,(ix+Q_NF)
        ld de,11
        call addmul
        ld (ix+Q_UOFF),l
        ld (ix+Q_UOFF+1),h
        ld a,(ix+Q_T)
        or a
        jr z,da_4
        ld a,(ix+Q_NF)
        ld de,8
        call addmul
da_4:   ld (ix+Q_EOFF),l
        ld (ix+Q_EOFF+1),h
        ld a,(ix+Q_NE)
        ld de,2
        call addmul
        call chkend
        ; ---- vertices
        xor a
        ld b,Q_CEN - Q_SUM
        ld a,Q_SUM
        call ixadr
da_5:   ld (hl),0
        inc hl
        djnz da_5
        ld (ix+Q_I),0
vx_1:   ld b,0
vx_2:   push bc
        call rdnum
        pop bc
        push bc
        ld a,b
        add a,a
        add a,Q_REC
        call ixws
        ld a,b
        call vacc
        pop bc
        inc b
        ld a,b
        cp 3
        jr c,vx_2
        ld l,(ix+Q_OFF)
        ld h,(ix+Q_OFF+1)
        ld a,(ix+Q_I)
        ld de,6
        call addmul
        call vmw
        ld a,Q_REC
        call ixadr
        ld b,6
        call vout
        inc (ix+Q_I)
        ld a,(ix+Q_I)
        cp (ix+Q_NV)
        jr c,vx_1
        ; shift k: |coordinates| >> k below 8192
        ld (ix+Q_K),0
        ld l,(ix+Q_MAX)
        ld h,(ix+Q_MAX+1)
dk_1:   ld a,h
        cp 0x20
        jr c,dk_2
        srl h
        rr l
        inc (ix+Q_K)
        jr dk_1
dk_2:   ; centre: (sum / nv, towards 0) >> k
        ld b,0
dc_1:   push bc
        ld a,b
        add a,a
        add a,b
        add a,Q_SUM
        call ixadr
        ld e,(hl)
        inc hl
        ld d,(hl)
        inc hl
        ld a,(hl)               ; bits 23-16
        ex de,hl                ; A:HL = sum
        push af                 ; sign
        bit 7,a
        jr z,dc_2
        ld e,a                  ; magnitude
        ld d,0xFF
        call neg32
        ld a,e
dc_2:   ld e,a
        ld d,0
        ld c,(ix+Q_NV)
        ld b,0
        call div32              ; HL = |sum| / nv
        pop af
        rla
        jr nc,dc_3
        ex de,hl
        call negde
        ex de,hl
dc_3:   ld a,(ix+Q_K)
        or a
        jr z,dc_5
dc_4:   sra h
        rr l
        dec a
        jr nz,dc_4
dc_5:   ex de,hl
        pop bc
        push bc
        ld a,b
        add a,a
        add a,Q_CEN
        call ixws
        pop bc
        inc b
        ld a,b
        cp 3
        jr c,dc_1
        ; ---- faces
        ld (ix+Q_I),0
        ld a,(ix+Q_NF)
        or a
        jp z,df_end
df_1:   ld b,0
df_2:   push bc
        call rdnum
        ld a,(ix+Q_NV)
        dec a
        call ubyte
        pop bc
        push af
        ld a,b
        add a,Q_IDX
        call ixadr
        pop af
        ld (hl),a
        inc b
        ld a,b
        cp 4
        jr c,df_2
        call rdnum
        ld a,15
        call ubyte
        ld (ix+Q_REC+10),a
        call fcyc
        ld (ix+Q_DIST),a
        ld a,Q_REC+4
        call ixadr
        ld b,6
df_3:   ld (hl),0
        inc hl
        djnz df_3
        ld a,(ix+Q_DIST)
        cp 3
        jr c,df_w               ; fewer than 3 corners: no normal
        ld a,(ix+Q_REC+10)
        call isramp
        jr nz,df_4
        bit 0,(ix+Q_O)
        jr z,df_w
df_4:   call fnorm              ; Y_NQ, Q_V
        bit 0,(ix+Q_O)
        call nz,forient
        ld a,(ix+Q_REC+10)
        call isramp
        jr z,df_w               ; a flat colour: normal 0
        ld a,Y_NQ
        call adr_y
        ex de,hl
        ld a,Q_REC+4
        call ixadr
        ex de,hl
        ld bc,6
        ldir
df_w:   ld l,(ix+Q_FOFF)
        ld h,(ix+Q_FOFF+1)
        ld a,(ix+Q_I)
        ld de,11
        call addmul
        call vmw
        ld a,Q_REC
        call ixadr
        ld b,11
        call vout
        ld a,(ix+Q_T)
        or a
        jr z,df_5
        ld l,(ix+Q_UOFF)        ; the UV slot keeps the corner order for now
        ld h,(ix+Q_UOFF+1)
        ld a,(ix+Q_I)
        ld de,8
        call addmul
        call vmw
        ld a,Q_PERM
        call ixadr
        ld b,1
        call vout
df_5:   ld a,(ix+Q_I)
        or a
        jr nz,df_6
        ld a,(ix+Q_REC+10)
        ld (ix+Q_COL),a
df_6:   inc (ix+Q_I)
        ld a,(ix+Q_I)
        cp (ix+Q_NF)
        jp c,df_1
df_end: ; ---- edges given
        ld a,(ix+Q_NE)
        or a
        jr z,de_end
        ld (ix+Q_I),0
de_1:   call rdnum
        ld a,(ix+Q_NV)
        dec a
        call ubyte
        ld (ix+Q_REC),a
        call rdnum
        ld a,(ix+Q_NV)
        dec a
        call ubyte
        ld (ix+Q_REC+1),a
        ld l,(ix+Q_EOFF)
        ld h,(ix+Q_EOFF+1)
        ld a,(ix+Q_I)
        ld de,2
        call addmul
        call vmw
        ld a,Q_REC
        call ixadr
        ld b,2
        call vout
        inc (ix+Q_I)
        ld a,(ix+Q_I)
        cp (ix+Q_NE)
        jr c,de_1
de_end: ; ---- UV
        ld a,(ix+Q_T)
        or a
        jp z,du_end
        ld a,(ix+Q_NF)
        or a
        jp z,du_end
        ld (ix+Q_I),0
du_1:   ld l,(ix+Q_UOFF)
        ld h,(ix+Q_UOFF+1)
        ld a,(ix+Q_I)
        ld de,8
        call addmul
        call vmr
        ld a,Q_PERM
        call ixadr
        ld b,1
        call vin
        ld b,0
du_2:   push bc
        call rdnum
        ld a,255
        call ubyte
        pop bc
        push af
        ld a,b
        add a,Q_UV
        call ixadr
        pop af
        ld (hl),a
        inc b
        ld a,b
        cp 8
        jr c,du_2
        ; corner j of the record gets UV corner (perm >> 2j) & 3
        ld c,(ix+Q_PERM)
        ld b,0
du_3:   ld a,c
        and 3
        add a,a
        add a,Q_UV
        call ixadr
        ld e,(hl)
        inc hl
        ld d,(hl)
        ld a,b
        add a,a
        add a,Q_REC
        call ixws
        srl c
        srl c
        inc b
        ld a,b
        cp 4
        jr c,du_3
        ld l,(ix+Q_UOFF)
        ld h,(ix+Q_UOFF+1)
        ld a,(ix+Q_I)
        ld de,8
        call addmul
        call vmw
        ld a,Q_REC
        call ixadr
        ld b,8
        call vout
        inc (ix+Q_I)
        ld a,(ix+Q_I)
        cp (ix+Q_NF)
        jr c,du_1
du_end: ; ---- edges from the faces
        ld a,(ix+Q_NE)
        or a
        jr nz,dd_1
        ld a,(ix+Q_NF)
        or a
        call nz,edges
dd_1:   ; ---- directory
        ld l,(ix+Q_EOFF)
        ld h,(ix+Q_EOFF+1)
        ld e,(ix+Q_NE)
        ld d,(ix+Q_NE+1)
        add hl,de
        add hl,de               ; end
        ld e,(ix+Q_OFF)
        ld d,(ix+Q_OFF+1)
        or a
        sbc hl,de               ; size
        ld de,127
        add hl,de
        ld a,l
        and 0x80
        ld l,a                  ; rounded up to 128
        push hl
        ld a,(ix+Q_M)
        call resinv
        call mdadr
        ld a,(ix+Q_FL)
        ld (hl),a               ; MD_FLAGS
        inc hl
        ld a,(ix+Q_NV)
        ld (hl),a
        inc hl
        ld a,(ix+Q_NF)
        ld (hl),a
        inc hl
        ld a,(ix+Q_COL)
        ld (hl),a
        inc hl
        ld a,(ix+Q_NE)
        ld (hl),a
        inc hl
        ld a,(ix+Q_NE+1)
        ld (hl),a
        inc hl
        ld e,(ix+Q_OFF)
        ld d,(ix+Q_OFF+1)
        ld (hl),e
        inc hl
        ld (hl),d
        inc hl
        pop bc
        ld (hl),c
        inc hl
        ld (hl),b
        inc hl
        ld a,(ix+Q_K)
        ld (hl),a
        ex de,hl
        add hl,bc
        ld (iy+Y_MEND),l
        ld (iy+Y_MEND+1),h
        call wkptr
        pop hl
        or a
        ret

; chkend: Out of memory when HL (an offset in the model area) is past its
; end (8000h).
chkend: push hl
        push de
        ld de,0x8001
        or a
        sbc hl,de
        pop de
        pop hl
        ret c
        jp err_om

; vacc: the vertex coordinate DE (axis A) into the sums and the OR of the
; magnitudes.
vacc:   push de
        ld c,a
        add a,a
        add a,c
        add a,Q_SUM
        call ixadr
        ld a,d
        rla
        sbc a,a
        ld c,a
        ld a,(hl)
        add a,e
        ld (hl),a
        inc hl
        ld a,(hl)
        adc a,d
        ld (hl),a
        inc hl
        ld a,(hl)
        adc a,c
        ld (hl),a
        pop de
        bit 7,d
        call nz,negde           ; -32768 stays 8000h: its magnitude
        ld a,(ix+Q_MAX)
        or e
        ld (ix+Q_MAX),a
        ld a,(ix+Q_MAX+1)
        or d
        ld (ix+Q_MAX+1),a
        ret

; fcyc: Q_IDX (a, b, c, d) -> Q_REC (I0-I3), Q_PERM (2 bits per corner of
; the record: its corner in Q_IDX), A = corners left once each corner equal
; to the next one (cyclically) is dropped. 3: the triangle (p, q, r, r);
; 4, or fewer than 3 (degenerate): the face as given.
fcyc:   ld bc,0                 ; B = corner, C = corners kept
fc_1:   ld a,b
        inc a
        and 3
        add a,Q_IDX
        call ixadr
        ld e,(hl)               ; the next corner's vertex
        ld a,b
        add a,Q_IDX
        call ixadr
        ld a,(hl)
        cp e
        jr z,fc_2               ; the same vertex as the next corner
        ld e,a
        ld a,c
        add a,Q_TMP
        call ixadr
        ld (hl),e
        inc hl
        inc hl
        inc hl
        inc hl
        ld (hl),b
        inc c
fc_2:   inc b
        ld a,b
        cp 4
        jr c,fc_1
        ld a,c
        cp 3
        jr z,fc_3
        ld a,(ix+Q_IDX)
        ld (ix+Q_REC),a
        ld a,(ix+Q_IDX+1)
        ld (ix+Q_REC+1),a
        ld a,(ix+Q_IDX+2)
        ld (ix+Q_REC+2),a
        ld a,(ix+Q_IDX+3)
        ld (ix+Q_REC+3),a
        ld (ix+Q_PERM),0xE4     ; corners 0, 1, 2, 3
        ld a,c
        ret
fc_3:   ld a,(ix+Q_TMP)
        ld (ix+Q_REC),a
        ld a,(ix+Q_TMP+1)
        ld (ix+Q_REC+1),a
        ld a,(ix+Q_TMP+2)
        ld (ix+Q_REC+2),a
        ld (ix+Q_REC+3),a
        ld a,(ix+Q_TMP+6)
        ld b,a
        add a,a
        add a,a
        or b
        add a,a
        add a,a
        or (ix+Q_TMP+5)
        add a,a
        add a,a
        or (ix+Q_TMP+4)
        ld (ix+Q_PERM),a
        ld a,3
        ret

; fnorm: Y_NQ = the unit normal (p2 - p0) x (p3 - p1) of the face in
; Q_REC, from its vertices in VRAM, >> k; Q_V = those vertices (>> k).
fnorm:  ld b,0
fn_1:   push bc
        ld a,b
        add a,Q_REC
        call ixadr
        ld a,(hl)               ; vertex number
        ld l,(ix+Q_OFF)
        ld h,(ix+Q_OFF+1)
        ld de,6
        call addmul
        call vmr
        pop bc
        push bc
        ld a,b
        add a,a
        add a,b
        add a,a
        add a,Q_V
        call ixadr
        push hl
        ld b,6
        call vin
        pop hl
        ld a,(ix+Q_K)
        or a
        jr z,fn_4
        ld b,3
fn_2:   push bc
        ld e,(hl)
        inc hl
        ld d,(hl)
        ld b,(ix+Q_K)
fn_3:   sra d
        rr e
        djnz fn_3
        ld (hl),d
        dec hl
        ld (hl),e
        inc hl
        inc hl
        pop bc
        djnz fn_2
fn_4:   pop bc
        inc b
        ld a,b
        cp 4
        jr c,fn_1
        ld hl,abtab
        ld b,6
        call isub
        call cross
        jp nrm3

; the sides a = v2 - v0 and b = v3 - v1 (destination, x, y: x - y)
abtab:  defb Q_A+0, Q_V+12, Q_V+0
        defb Q_A+2, Q_V+14, Q_V+2
        defb Q_A+4, Q_V+16, Q_V+4
        defb Q_B+0, Q_V+18, Q_V+6
        defb Q_B+2, Q_V+20, Q_V+8
        defb Q_B+4, Q_V+22, Q_V+10

; isub: B entries of the table at HL: (IX + dst) = (IX + x) - (IX + y).
isub:   push bc
        ld c,(hl)
        inc hl
        ld a,(hl)
        inc hl
        call ixw
        push de
        ld a,(hl)
        inc hl
        call ixw
        ex (sp),hl              ; HL = x, the table on the stack
        or a
        sbc hl,de
        ex de,hl
        ld a,c
        call ixws
        pop hl
        pop bc
        djnz isub
        ret

; cross: Y_N = a x b, three 32-bit values.
crtab:  defb Q_A+2, Q_B+4, Q_A+4, Q_B+2 ; ay * bz - az * by
        defb Q_A+4, Q_B+0, Q_A+0, Q_B+4 ; az * bx - ax * bz
        defb Q_A+0, Q_B+2, Q_A+2, Q_B+0 ; ax * by - ay * bx
cross:  ld hl,crtab
        ld (iy+Y_TP),l
        ld (iy+Y_TP+1),h
        ld c,Y_N
        ld b,3
cr_1:   push bc
        call crmul
        ld (iy+Y_ACC),l
        ld (iy+Y_ACC+1),h
        ld (iy+Y_ACC+2),e
        ld (iy+Y_ACC+3),d
        call crmul
        call neg32
        call acc_add
        pop bc
        push bc
        ld a,c
        call adr_y
        ld a,(iy+Y_ACC)
        ld (hl),a
        inc hl
        ld a,(iy+Y_ACC+1)
        ld (hl),a
        inc hl
        ld a,(iy+Y_ACC+2)
        ld (hl),a
        inc hl
        ld a,(iy+Y_ACC+3)
        ld (hl),a
        pop bc
        ld a,c
        add a,4
        ld c,a
        djnz cr_1
        ret
crmul:  ld l,(iy+Y_TP)
        ld h,(iy+Y_TP+1)
        ld a,(hl)
        inc hl
        call ixw
        push de
        ld a,(hl)
        inc hl
        ld (iy+Y_TP),l
        ld (iy+Y_TP+1),h
        call ixw
        pop bc
        jp mul16s

; forient: when the normal Y_NQ points towards the centre (its dot product
; with the face's centroid - centre, all >> k, is negative) the face is
; turned: (0, 3, 2, 1) for 4 corners, (0, 2, 1, 1) for a triangle; the
; normal and the corner order (Q_PERM) follow.
forient:
        xor a
        ld (iy+Y_ACC),a
        ld (iy+Y_ACC+1),a
        ld (iy+Y_ACC+2),a
        ld (iy+Y_ACC+3),a
        ld b,0
fo_1:   push bc
        ld a,b
        add a,a
        add a,Q_V
        call ixw
        ex de,hl
        ld a,b
        add a,a
        add a,Q_V+6
        call ixw
        add hl,de
        ld a,b
        add a,a
        add a,Q_V+12
        call ixw
        add hl,de
        ld a,b
        add a,a
        add a,Q_V+18
        call ixw
        add hl,de
        sra h
        rr l
        sra h
        rr l                    ; centroid
        ld a,b
        add a,a
        add a,Q_CEN
        call ixw
        or a
        sbc hl,de               ; d = centroid - centre
        push hl
        ld a,b
        add a,a
        add a,Y_NQ
        call fetchw
        ld b,d
        ld c,e                  ; BC = normal
        pop de                  ; DE = d
        call mul16s
        call acc_add
        pop bc
        inc b
        ld a,b
        cp 3
        jr c,fo_1
        bit 7,(iy+Y_ACC+3)
        ret z                   ; outwards (or edge-on): as it is
        ; turn it
        ld a,(ix+Q_PERM)
        ld b,4
        push af
        ld a,Q_TMP+4
        call ixadr
        pop af
fo_2:   ld c,a
        and 3
        ld (hl),a
        inc hl
        ld a,c
        rrca
        rrca
        djnz fo_2
        ld a,(ix+Q_DIST)
        cp 3
        jr z,fo_3
        ld a,(ix+Q_REC+1)
        ld b,(ix+Q_REC+3)
        ld (ix+Q_REC+1),b
        ld (ix+Q_REC+3),a
        ld a,(ix+Q_TMP+5)
        ld b,(ix+Q_TMP+7)
        ld (ix+Q_TMP+5),b
        ld (ix+Q_TMP+7),a
        jr fo_4
fo_3:   ld a,(ix+Q_REC+1)
        ld b,(ix+Q_REC+2)
        ld (ix+Q_REC+1),b
        ld (ix+Q_REC+2),a
        ld (ix+Q_REC+3),a
        ld a,(ix+Q_TMP+5)
        ld b,(ix+Q_TMP+6)
        ld (ix+Q_TMP+5),b
        ld (ix+Q_TMP+6),a
        ld (ix+Q_TMP+7),a
fo_4:   ld a,(ix+Q_TMP+7)
        add a,a
        add a,a
        or (ix+Q_TMP+6)
        add a,a
        add a,a
        or (ix+Q_TMP+5)
        add a,a
        add a,a
        or (ix+Q_TMP+4)
        ld (ix+Q_PERM),a
        ld b,3
        ld a,Y_NQ
fo_5:   push af
        call fetchw
        call negde
        pop af
        push af
        call adr_y
        ld (hl),e
        inc hl
        ld (hl),d
        pop af
        add a,2
        djnz fo_5
        ret

; edges: the wireframe edges of the faces (Q_NE of them at Q_EOFF), each
; side once. Bitmap: bit (hi & 7) of VRAM byte page * 8000h + lo * 32 +
; hi / 8, lines 0-63 of page Q_BM.
edges:  ld a,(ix+Q_BM)
        ld b,64
        call vclr
        ld (ix+Q_I),0
eg_1:   ld l,(ix+Q_FOFF)
        ld h,(ix+Q_FOFF+1)
        ld a,(ix+Q_I)
        ld de,11
        call addmul
        call vmr
        ld a,Q_IDX
        call ixadr
        ld b,4
        call vin
        ld (ix+Q_J),0
eg_2:   ld a,(ix+Q_J)
        inc a
        and 3
        add a,Q_IDX
        call ixadr
        ld e,(hl)
        ld a,(ix+Q_J)
        add a,Q_IDX
        call ixadr
        ld d,(hl)
        ld a,d
        cp e
        jr z,eg_4               ; no side
        jr c,eg_3
        ld d,e                  ; D = lo, E = hi
        ld e,a
eg_3:   call bmset
        jr nz,eg_4              ; seen already
        ld l,(ix+Q_EOFF)
        ld h,(ix+Q_EOFF+1)
        push de
        ld e,(ix+Q_NE)
        ld d,(ix+Q_NE+1)
        add hl,de
        add hl,de
        pop de
        push hl
        inc hl
        inc hl
        call chkedg
        pop hl
        call vmw
        ld a,(iy+Y_PORT)
        ld c,a
        out (c),d
        nop
        nop
        nop
        nop
        out (c),e
        inc (ix+Q_NE)
        jr nz,eg_4
        inc (ix+Q_NE+1)
eg_4:   inc (ix+Q_J)
        ld a,(ix+Q_J)
        cp 4
        jr c,eg_2
        inc (ix+Q_I)
        ld a,(ix+Q_I)
        cp (ix+Q_NF)
        jp c,eg_1
        set 2,(ix+Q_FL)
        ld a,(ix+Q_BM)
        ld b,64
        jp vclr

; chkedg: as chkend, clearing the bitmap before Out of memory.
chkedg: push hl
        push de
        ld de,0x8001
        or a
        sbc hl,de
        pop de
        pop hl
        ret c
        ld a,(ix+Q_BM)
        ld b,64
        call vclr
        jp err_om

; bmset: Z when the side D-E (D < E) was not in the bitmap (it is now).
; Keeps DE.
bmset:  push de
        ld l,d
        ld h,0
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl
        add hl,hl               ; lo * 32
        ld a,e
        rrca
        rrca
        rrca
        and 0x1F
        or l
        ld l,a
        ld a,(ix+Q_BM)
        rrca
        or h
        ld h,a                  ; + page * 8000h
        xor a                   ; read
        call vaddr
        ld a,e
        and 7
        ld b,a
        inc b
        ld a,0x80
bs_1:   rlca
        djnz bs_1               ; A = 1 << (hi & 7)
        ld d,a
        ld a,(iy+Y_PORT)
        ld c,a
        in a,(c)
        ld e,a
        and d
        jr nz,bs_2              ; seen
        ld a,e
        or d
        ld e,a
        xor a
        scf
        call vaddr
        out (c),e
        pop de
        xor a                   ; Z
        ret
bs_2:   pop de
        or 1                    ; NZ
        ret

; ---- DATA -------------------------------------------------------------------

; rdb: A = the next DATA number, 0-255 (Illegal function call otherwise).
rdb:    call rdnum
        ld a,255
        jp ubyte

; rdnum: DE = the next DATA number (READ's own scan and FIN), rounded to
; -32768..32767. Keeps IX, IY (on the stack: BASIC changes them).
rdnum:  push ix
        push iy
        ld hl,(DATPTR)
        ld a,(hl)
        cp ','
        jr z,rn_3
rn_1:   ld ix,DATSKP            ; to the end of this statement
        call CALBAS
        or a
        jr nz,rn_2
        inc hl                  ; next line: link, number
        ld a,(hl)
        inc hl
        or (hl)
        jp z,err_od             ; end of the program
        inc hl
        ld e,(hl)
        inc hl
        ld d,(hl)
        ld (DATLIN),de
rn_2:   call chrgtr             ; the statement
        cp T_DATA
        jr nz,rn_1
rn_3:   pop iy                  ; the item: W_RN (CHRGTR, FIN, CHRGTR) in
        push iy                 ; one trip through the trampoline
        push hl
        push iy
        pop hl
        ld de,W_RN - XB
        add hl,de
        push hl
        pop ix
        pop hl
        call tramp              ; DAC; HL, A: the character after the number
        or a
        jr z,rn_4
        cp ':'
        jr z,rn_4
        cp ','
        jr z,rn_4
        ld ix,DATSN             ; not a number: Syntax error in the DATA line
        jp CALBAS
rn_4:   ld (DATPTR),hl
        call dacint
        pop iy
        pop ix
        ret

; ============================================================================
; G3RAMP's rescan: every face of every model gets the normal its colour
; asks for now (a ramp start: the unit normal; otherwise 0).
; ============================================================================
rescan: call dbptr
        ld a,16
rs_1:   push af
        call mdadr
        bit 0,(hl)
        jp z,rs_9
        inc hl
        ld a,(hl)
        ld (ix+Q_NV),a
        inc hl
        ld a,(hl)
        ld (ix+Q_NF),a
        ld de,MD_OFF - MD_NF
        add hl,de
        ld e,(hl)
        inc hl
        ld d,(hl)
        ld (ix+Q_OFF),e
        ld (ix+Q_OFF+1),d
        inc hl
        inc hl
        inc hl
        ld a,(hl)               ; MD_K
        ld (ix+Q_K),a
        ex de,hl
        ld a,(ix+Q_NV)
        ld de,6
        call addmul
        ld (ix+Q_FOFF),l
        ld (ix+Q_FOFF+1),h
        ld a,(ix+Q_NF)
        or a
        jp z,rs_9
        ld (ix+Q_I),0
rs_2:   ld l,(ix+Q_FOFF)
        ld h,(ix+Q_FOFF+1)
        ld a,(ix+Q_I)
        ld de,11
        call addmul
        push hl
        call vmr
        ld a,Q_REC
        call ixadr
        ld b,11
        call vin
        ld a,(ix+Q_REC+4)
        or (ix+Q_REC+5)
        or (ix+Q_REC+6)
        or (ix+Q_REC+7)
        or (ix+Q_REC+8)
        or (ix+Q_REC+9)
        ld c,a                  ; C: normal not 0
        ld a,(ix+Q_REC+10)
        call isramp
        jr z,rs_4
        ld a,c
        or a
        jr nz,rs_8              ; a ramp with its normal
        call fnorm
        ld a,Y_NQ
        call adr_y
        ex de,hl
        ld a,Q_REC+4
        call ixadr
        ex de,hl
        ld bc,6
        ldir
        jr rs_5
rs_4:   ld a,c
        or a
        jr z,rs_8               ; flat with normal 0
        ld a,Q_REC+4
        call ixadr
        ld b,6
rs_6:   ld (hl),0
        inc hl
        djnz rs_6
rs_5:   pop hl
        push hl
        call vmw
        ld a,Q_REC
        call ixadr
        ld b,11
        call vout
rs_8:   pop hl
        inc (ix+Q_I)
        ld a,(ix+Q_I)
        cp (ix+Q_NF)
        jr c,rs_2
rs_9:   pop af
        inc a
        cp 32
        jp c,rs_1
        jp wkix

; ============================================================================
; G3FRAME [(v)]  (spec section 4)
;   1. G3SPIN angles advance.
;   2. The page drawn is cleared (HMMV, lines 0-211), then the visible
;      objects are drawn on it, the farthest first (background models first
;      of all). Per object: its model goes to geo3d unless geo3d holds it
;      already, then M, T, the light and RUN (faces, or edges in passes of
;      255), and the wait for geo3d.
;   3. The page is shown at the first vertical blank at least v blanks
;      after the last flip (v kept for the next frames, 2 at G3INIT; 0: at
;      once). 98h: the H.TIMI hook flips it and G3FRAME returns right away;
;      the next G3FRAME waits for that flip. 88h: G3FRAME waits for the
;      V9968's blank (S#0 F) and flips it.
;   98h: Illegal function call unless BASIC is in SCREEN 5; a SET PAGE of
;   the program is taken over (DPPAGE).
; ============================================================================
g3frame:
        ld b,1
        xor a
        call getargs
        push hl
        call wkptr
        xor a
        call arg
        jr c,fr_1
        ld a,255
        call ubyte
        ld (ix+W_PACE),a
fr_1:   call scr5
        call spins
        call flip_wait
        call pgsync
        call wait_geo
        call wait_ce
        ld a,(ix+W_DRAW)
        call hmmv               ; runs while the objects are worked out
        call sortobj
        ld a,0x18               ; F, CX, CY, ZNEAR, W, H
        ld hl,geo_tab + 2
        ld b,12
        call gwr
        ld (iy+Y_I),0
fr_3:   ld a,(iy+Y_I)
        cp (iy+Y_NVIS)
        jr nc,fr_4
        add a,Y_SORT
        call adr_y
        ld a,(hl)
        call drawobj
        inc (iy+Y_I)
        jr fr_3
fr_4:   call wait_geo
        inc (iy+Y_FCNT)
        jr nz,fr_5
        inc (iy+Y_FCNT+1)
fr_5:   call flip
        pop hl
        or a
        ret

; scr5: 98h: Illegal function call unless BASIC is in SCREEN 5 (the VDP
; commands the ROM uses do not run in the text modes). Keeps BC, DE, HL.
scr5:   ld a,(ix+W_PORT)
        cp 0x98
        ret nz
        ld a,(SCRMOD)
        cp 5
        ret z
        jp err_fc

; spins: G3SPIN of every object.
spins:  ld a,1
sp_1:   push af
        call objadr
        push hl
        pop ix
        bit 0,(ix+O_FLAGS)
        jr z,sp_2
        ld a,(ix+O_SPIN)
        or (ix+O_SPIN+1)
        or (ix+O_SPIN+2)
        or (ix+O_SPIN+3)
        or (ix+O_SPIN+4)
        or (ix+O_SPIN+5)
        jr z,sp_2
        ld l,(ix+O_ANG)
        ld h,(ix+O_ANG+1)
        ld e,(ix+O_SPIN)
        ld d,(ix+O_SPIN+1)
        add hl,de
        ld (ix+O_ANG),l
        ld (ix+O_ANG+1),h
        ld l,(ix+O_ANG+2)
        ld h,(ix+O_ANG+3)
        ld e,(ix+O_SPIN+2)
        ld d,(ix+O_SPIN+3)
        add hl,de
        ld (ix+O_ANG+2),l
        ld (ix+O_ANG+3),h
        ld l,(ix+O_ANG+4)
        ld h,(ix+O_ANG+5)
        ld e,(ix+O_SPIN+4)
        ld d,(ix+O_SPIN+5)
        add hl,de
        ld (ix+O_ANG+4),l
        ld (ix+O_ANG+5),h
        res 2,(ix+O_FLAGS)
sp_2:   pop af
        inc a
        cp NOBJ + 1
        jr c,sp_1
        jp ixfy

; pgsync: W_DRAW, the page to draw on: the one not shown (98h: DPPAGE, so a
; SET PAGE of the program is followed).
pgsync: ld a,(ix+W_PORT)
        cp 0x98
        jr nz,pg_1
        ld a,(DPPAGE)
        ld (ix+W_SHOW),a
pg_1:   ld a,(ix+W_SHOW)
        dec a
        jr z,pg_2               ; page 1 shown: draw on 0
        ld a,1
pg_2:   ld (ix+W_DRAW),a
        ret

; flip_wait: 98h: until the H.TIMI hook has shown the page of the last
; G3FRAME (spec: the next frame only waits when it needs that page). Device
; I/O error when JIFFY stands still for about 2 s (interrupts off) or runs
; more than v + 60 blanks. Outside SCREEN 5 the flip is dropped instead.
flip_wait:
        ei                      ; (a BASIC call through CALBAS left DI)
        ld a,(ix+W_PORT)
        cp 0x98
        ret nz
        bit 7,(ix+W_FLIP)
        ret z
        ld a,(SCRMOD)
        cp 5
        jr z,fw_0
        ld (ix+W_FLIP),0        ; not SCREEN 5: dropped, as the hook drops it
        ret
fw_0:   ld a,(JIFFY)
        ld (iy+Y_J0),a
        ld (iy+Y_J1),a
        ld de,0
fw_1:   bit 7,(ix+W_FLIP)
        ret z
        ld a,(JIFFY)
        cp (iy+Y_J1)
        jr z,fw_3
        ld (iy+Y_J1),a
        ld de,0
        sub (iy+Y_J0)
        ld b,a                  ; blanks waited
        ld a,(ix+W_PACE)
        add a,60
        jr nc,fw_2
        ld a,250
fw_2:   cp b
        jp c,err_io
fw_3:   dec de
        ld a,d
        or e
        jr nz,fw_1
        jp err_io

; flip: shows W_DRAW (step 3).
flip:   ld a,(ix+W_DRAW)
        ld c,a
        ld a,(ix+W_PORT)
        cp 0x98
        jr nz,flip88
        ld a,(ix+W_PACE)
        or a
        jr z,fl_1
        ld a,c
        or 0x80
        ld (ix+W_FLIP),a        ; the H.TIMI hook shows it
        ret
fl_1:   ld a,c
        di
        call flip98
        ei
        ret

; flip88: 88h: the flip at a fresh V9968 blank (S#0 F, read by this ROM
; only; F is read once first, as it may be old), once v blanks went by
; since the last flip (JIFFY, or v fresh blanks).
flip88: ld a,(ix+W_PACE)
        or a
        jr z,f8_3
        ld a,(ix+W_PORT)
        inc a
        ld c,a
        in a,(c)                ; S#0: an old F goes
        ld (iy+Y_FC),0
f8_1:   call waitf
        inc (iy+Y_FC)
        ld a,(iy+Y_FC)
        cp (ix+W_PACE)
        jr nc,f8_3
        ld hl,(JIFFY)           ; v interrupts of the machine's VDP since the
        ld e,(ix+W_LASTJ)       ; last flip: more than v - 1 of its periods
        ld d,(ix+W_LASTJ+1)     ; went by, so this fresh V9968 blank is at
        or a                    ; least the v-th since the flip (a 50 Hz
        sbc hl,de               ; machine only waits longer)
        ld a,h
        or a
        jr nz,f8_3
        ld a,l
        cp (ix+W_PACE)
        jr c,f8_1
f8_3:   ld a,(ix+W_DRAW)
        ld (ix+W_SHOW),a
        rrca
        rrca
        rrca
        or 0x1F
        ld b,2
        call vreg
        ld hl,(JIFFY)
        ld (ix+W_LASTJ),l
        ld (ix+W_LASTJ+1),h
        ld a,(ix+W_PORT)
        inc a
        ld c,a
        in a,(c)                ; F cleared after the flip
        ret

; waitf: until S#0 bit 7 (F) of the V9968 (88h, R#15 = 0), with the usual
; timeout and CTRL+STOP.
waitf:  ei
        call wt_init
        ld a,(ix+W_PORT)
        inc a
        ld c,a
        ld de,47000
wf_1:   in a,(c)
        rlca
        jp c,wt_end
        call tick
        jr nz,wf_1
        jp err_io

; gwr: geo3d index A, then B bytes from HL.
gwr:    push af
        ld a,(iy+Y_PORT)
        add a,5
        ld c,a
        pop af
        out (c),a
        inc c
        inc c
        otir
        ret

; ---- the objects in drawing order ---------------------------------------------

; sortobj: Y_SORT = the objects to draw (in use, visible, not a pivot, a
; defined model, within 30000 of the camera on each camera axis), each with
; T = Cam * (position - camera) in O_T, sorted by key: 32767 for a
; background model, else T.z, the largest first.
sortobj:
        ld (iy+Y_NVIS),0
        ld a,1
so_1:   push af
        call objadr
        push hl
        pop ix
        ld a,(ix+O_FLAGS)
        and 3
        cp 1
        jp nz,so_9              ; empty or hidden
        ld a,(ix+O_MODEL)
        or a
        jp z,so_9               ; pivot
        call mdadr
        bit 0,(hl)
        jp z,so_9               ; the model went (a G3DATA that failed)
        res 3,(ix+O_FLAGS)
        bit 3,(hl)
        jr z,so_2
        set 3,(ix+O_FLAGS)      ; background
so_2:   push iy                 ; d = position - camera (16 bits, else far)
        pop hl
        ld de,W_CAM - XB
        add hl,de
        ld b,0
so_3:   push hl
        ld e,(hl)
        inc hl
        ld d,(hl)
        ld a,b
        add a,a
        add a,O_POS
        push de
        call ixw
        pop hl
        ex de,hl
        or a
        sbc hl,de               ; position - camera
        jp pe,so_8              ; out of range
        ex de,hl
        ld a,b
        add a,a
        add a,Y_D
        push hl
        call adr_y
        ld (hl),e
        inc hl
        ld (hl),d
        pop hl
        pop hl
        inc hl
        inc hl
        inc b
        ld a,b
        cp 3
        jr c,so_3
        ld a,(iy+Y_CAMI)
        or a
        jr z,so_4
        ld a,Y_D
        call adr_y
        ex de,hl
        ld a,Y_TT
        call adr_y
        ex de,hl
        ld bc,6
        ldir
        jr so_5
so_4:   ld (iy+Y_OVF),0
        ld hl,cdtab
        call qeval
        ld a,(iy+Y_OVF)
        or a
        jr nz,so_9
so_5:   ld b,3
        ld a,Y_TT
so_6:   push af
        call fetchw
        ex de,hl
        call rng30
        jr c,so_7
        pop af
        add a,2
        djnz so_6
        ld a,Y_TT
        call adr_y
        push ix
        pop de
        push hl
        ld hl,O_T
        add hl,de
        ex de,hl
        pop hl
        ld bc,6
        ldir
        ld a,(iy+Y_NVIS)
        add a,Y_SORT
        call adr_y
        pop af
        push af
        ld (hl),a
        inc (iy+Y_NVIS)
        jr so_9
so_7:   pop af
        jr so_9
so_8:   pop hl
so_9:   pop af
        inc a
        cp NOBJ + 1
        jp c,so_1
        ; insertion sort, largest key first
        ld c,1                  ; C = i
so_10:  ld a,c
        cp (iy+Y_NVIS)
        jr nc,so_14
        add a,Y_SORT
        call adr_y
        ld a,(hl)               ; x = S[i]
        ld (iy+Y_OM),a
        ld b,c                  ; B = j
so_11:  ld a,b
        or a
        jr z,so_13
        add a,Y_SORT - 1
        call adr_y
        ld a,(hl)               ; S[j-1]
        push bc
        push hl
        call okey
        push hl
        ld a,(iy+Y_OM)
        call okey
        ex de,hl                ; DE = key(x)
        pop hl                  ; HL = key(S[j-1])
        call cmps               ; carry: HL < DE
        pop hl
        pop bc
        jr nc,so_13
        ld a,(hl)
        inc hl
        ld (hl),a               ; S[j] = S[j-1]
        dec b
        jr so_11
so_13:  ld a,b
        add a,Y_SORT
        call adr_y
        ld a,(iy+Y_OM)
        ld (hl),a
        inc c
        jr so_10
so_14:  jp ixfy

; okey: HL = the sorting key of object A. Keeps BC.
okey:   call objadr
        ld a,(hl)
        ld de,O_T + 4
        add hl,de
        ld e,(hl)
        inc hl
        ld d,(hl)
        ex de,hl
        bit 3,a
        ret z
        ld hl,32767
        ret

; cmps: carry when HL < DE, signed. Changes A, HL, DE.
cmps:   ld a,h
        xor 0x80
        ld h,a
        ld a,d
        xor 0x80
        ld d,a
        or a
        sbc hl,de
        ret

; rng30: carry when HL is outside -30000..30000.
rng30:  bit 7,h
        jr z,r3_1
        ld de,30000
        add hl,de
        ld a,h
        rla
        ret
r3_1:   ld de,0 - 30001
        add hl,de
        ret

cdtab:  defb Y_TT+0, 3,  0, Y_CMAT+0, Y_D+0,  0, Y_CMAT+2, Y_D+2,  0, Y_CMAT+4, Y_D+4
        defb Y_TT+2, 3,  0, Y_CMAT+6, Y_D+0,  0, Y_CMAT+8, Y_D+2,  0, Y_CMAT+10, Y_D+4
        defb Y_TT+4, 3,  0, Y_CMAT+12, Y_D+0, 0, Y_CMAT+14, Y_D+2, 0, Y_CMAT+16, Y_D+4
        defb 0x7F

; ---- one object -------------------------------------------------------------

; drawobj: object A to geo3d: rotation (cached while its angles stay), M,
; T, its model unless geo3d holds it, RUN. IX = work area on entry and exit.
drawobj:
        call objadr
        push hl
        pop ix
        bit 2,(ix+O_FLAGS)
        call z,rotmat
        ld a,(iy+Y_CAMI)
        or a
        ld a,Y_MT               ; camera matrix 1: M = R
        jr nz,dw_1
        ld a,Y_TR               ; else R to Y_TR, M = C * R
dw_1:   call adr_y
        ex de,hl
        push ix
        pop hl
        ld bc,O_ROT
        add hl,bc
        ld bc,18
        ldir
        ld a,(iy+Y_CAMI)
        or a
        jr nz,dw_2
        ld hl,cxrtab
        call qeval
dw_2:   ld a,Y_MT + 18          ; T
        call adr_y
        ex de,hl
        push ix
        pop hl
        ld bc,O_T
        add hl,bc
        ld bc,6
        ldir
        ld a,(ix+O_MODEL)
        ld (iy+Y_OM),a
        ld a,(ix+O_STYLE)
        ld (iy+Y_OS),a
        call ixfy
        call wait_geo           ; the object before is done
        ld a,(iy+Y_OM)
        call mdadr
        ld (iy+Y_MDP),l
        ld (iy+Y_MDP+1),h
        ld a,(iy+Y_OS)
        or a
        jr z,dw_w
        inc hl
        inc hl
        ld a,(hl)               ; faces
        or a
        jr z,dw_w
        call upv
        call upf
        ld bc,0x0300            ; RUN, faces; NEDGE 0
        jp regs
dw_w:   ; wireframe: the edges in passes of 255
        call mdp
        ld de,MD_NE
        add hl,de
        ld e,(hl)
        inc hl
        ld d,(hl)               ; DE = edges
        ld a,d
        or e
        ret z
        ex de,hl
        dec hl
        ld c,0
        ld de,0 - 255
dw_3:   inc c
        add hl,de
        jr c,dw_3               ; C = passes: (edges - 1) / 255 + 1
        ld (iy+Y_NP),c
        ld a,(iy+Y_OM)
        cp (iy+Y_RESE)
        ld a,0
        jr nz,dw_4
        ld a,(iy+Y_RESP)
        cp c
        jr c,dw_4
        xor a
dw_4:   ld (iy+Y_PASS),a        ; the pass geo3d holds goes first
        ld (iy+Y_PN),c
dw_5:   call upv
        ld a,(iy+Y_PASS)
        call upe                ; C = edges of this pass
        ld b,1                  ; RUN, edges
        call regs
        dec (iy+Y_PN)
        ret z
        ld a,(iy+Y_PASS)
        inc a
        cp (iy+Y_NP)
        jr c,dw_6
        xor a
dw_6:   ld (iy+Y_PASS),a
        call wait_geo
        jr dw_5

; mdp: HL = the directory entry of the object being drawn.
mdp:    ld l,(iy+Y_MDP)
        ld h,(iy+Y_MDP+1)
        ret

; upv: the vertices of model Y_OM to geo3d, unless it holds them.
upv:    ld a,(iy+Y_OM)
        cp (iy+Y_RESV)
        ret z
        ld (iy+Y_RESV),a
        ld a,0x40               ; VADDR = 0
        call gidx0
        call mdp
        inc hl
        ld a,(hl)               ; vertices
        ld de,MD_OFF - MD_NV
        add hl,de
        ld e,(hl)
        inc hl
        ld d,(hl)
        ex de,hl                ; HL = offset
        ld de,6
        call mulw               ; DE = A * 6
        ld a,0x50
        jp gstream

; upf: the faces of model Y_OM.
upf:    ld a,(iy+Y_OM)
        cp (iy+Y_RESF)
        ret z
        ld (iy+Y_RESF),a
        ld a,0x58               ; FADDR = 0
        call gidx0
        call mdp
        inc hl
        ld a,(hl)               ; vertices
        inc hl
        ld c,(hl)               ; faces
        ld de,MD_OFF - MD_NF
        add hl,de
        ld e,(hl)
        inc hl
        ld d,(hl)
        ex de,hl
        ld de,6
        call addmul             ; HL = offset of the faces
        ld a,c
        ld de,11
        call mulw
        ld a,0x52
        jp gstream

; upe: pass A of the edges of model Y_OM to geo3d (unless held); C = its
; edges (1-255).
upe:    push af
        call mdp
        ld de,MD_NE
        add hl,de
        ld e,(hl)
        inc hl
        ld d,(hl)               ; all edges
        pop af
        push af
        ld bc,0 - 255
        ex de,hl
        or a
        jr z,ue_2
ue_1:   add hl,bc
        dec a
        jr nz,ue_1
ue_2:   ld a,h                  ; HL = edges from this pass on
        or a
        ld a,l
        jr z,ue_3
        ld a,255
ue_3:   cp 255
        jr c,ue_4
        ld a,255
ue_4:   ld c,a                  ; C = edges in this pass
        pop af
        ld b,a
        ld a,(iy+Y_OM)
        cp (iy+Y_RESE)
        jr nz,ue_5
        ld a,b
        cp (iy+Y_RESP)
        ret z
ue_5:   ld a,(iy+Y_OM)
        ld (iy+Y_RESE),a
        ld (iy+Y_RESP),b
        push bc
        ld a,0x41               ; EADDR = 0
        call gidx0
        call mdp
        push hl
        inc hl
        ld a,(hl)               ; vertices
        inc hl
        ld e,(hl)               ; faces
        ld d,(hl)
        push de
        ld de,MD_OFF - MD_NF
        add hl,de
        ld e,(hl)
        inc hl
        ld d,(hl)
        ex de,hl                ; offset
        ld de,6
        call addmul
        pop de
        push de
        ld a,d
        ld de,11
        call addmul
        pop de
        ex (sp),hl              ; HL = entry
        bit 1,(hl)
        pop hl
        jr z,ue_6
        ld a,e
        ld de,8
        call addmul             ; UV
ue_6:   pop bc
        push bc
        ld a,b                  ; pass
        ld de,510
        call addmul
        pop bc
        ld a,c
        ld de,2
        push bc
        call mulw
        ld a,0x51
        call gstream
        pop bc
        ret

; gidx0: geo3d index A = 0.
gidx0:  ld b,a
        ld a,(iy+Y_PORT)
        add a,5
        ld c,a
        out (c),b
        inc c
        inc c
        xor a
        out (c),a
        ret

; mulw: DE = A * DE. Keeps HL, BC.
mulw:   push hl
        ld hl,0
        call addmul
        ex de,hl
        pop hl
        ret

; gstream: DE bytes (1 or more) from model area offset HL to geo3d stream
; index A, through the RAM loop (W_UK: 38 T-states per byte).
gstream:
        push de
        push af
        call vmr
        ld a,(iy+Y_PORT)
        add a,5
        ld c,a
        pop af
        out (c),a
        pop de
        push iy
        pop hl
        push de
        ld de,W_UK - XB
        add hl,de
        pop de
        ld b,e
        ld a,e
        or a
        jr z,gt_1
        inc d
gt_1:   ei
        jp (hl)

; regs: the registers of one RUN (model at Y_MDP): 40h VADDR 0, EADDR 0,
; NVERT, NEDGE C, COLOR, LOP 0, YPAGE; 58h FADDR 0, NFACE, LX, LY, LZ;
; 00h M, T; then 48h = B (RUN).
regs:   push bc
        call tmpadr
        push hl
        ld (hl),0               ; VADDR
        inc hl
        ld (hl),0               ; EADDR
        inc hl
        push hl
        call mdp
        inc hl
        ld a,(hl)
        inc hl
        inc hl
        ld e,(hl)               ; wireframe colour
        pop hl
        ld (hl),a               ; NVERT
        inc hl
        ld (hl),c               ; NEDGE
        inc hl
        ld a,e
        call isramp
        ld a,e
        jr z,rg_1
        add a,6                 ; a ramp: its brightest tone
rg_1:   ld (hl),a               ; COLOR
        inc hl
        ld (hl),0               ; LOP
        inc hl
        ld (hl),0               ; YPAGE
        inc hl
        ld a,(ix+W_DRAW)
        ld (hl),a
        pop hl
        ld a,0x40
        ld b,8
        call gwr
        call tmpadr
        push hl
        ld (hl),0               ; FADDR
        inc hl
        push hl
        call mdp
        inc hl
        inc hl
        ld a,(hl)
        pop hl
        ld (hl),a               ; NFACE
        inc hl
        ex de,hl
        ld a,Y_LCAM
        call adr_y
        ld bc,6
        ldir
        pop hl
        ld a,0x58
        ld b,8
        call gwr
        ld a,Y_MT
        call adr_y
        xor a
        ld b,24
        call gwr
        pop bc
        ld a,(iy+Y_PORT)
        add a,5
        ld c,a
        ld a,0x48
        out (c),a
        inc c
        inc c
        out (c),b               ; RUN
        ret

; ---- rotation of an object ------------------------------------------------------

; rotmat: O_ROT = R = Ry * Rx * Rz of the angles of object IX; cached.
rotmat: ld a,Y_SX
        ld l,(ix+O_ANG)
        ld h,(ix+O_ANG+1)
        call sincos
        ld a,Y_SY
        ld l,(ix+O_ANG+2)
        ld h,(ix+O_ANG+3)
        call sincos
        ld a,Y_SZ
        ld l,(ix+O_ANG+4)
        ld h,(ix+O_ANG+5)
        call sincos
        ld (iy+Y_ONE),0
        ld (iy+Y_ONE+1),0x40
        ld hl,rottab
        call qeval
        push ix
        pop hl
        ld de,O_ROT
        add hl,de
        ex de,hl
        ld a,Y_MT
        call adr_y
        ld bc,18
        ldir
        set 2,(ix+O_FLAGS)
        ret

; sincos: (IY + A) = sin, (IY + A + 2) = cos of angle HL.
sincos: push af
        push hl
        call getsin
        ex de,hl
        pop hl
        pop af
        push af
        push hl
        call adr_y
        ld (hl),e
        inc hl
        ld (hl),d
        pop hl
        ld de,16384
        add hl,de
        call getsin
        ex de,hl
        pop af
        add a,2
        call adr_y
        ld (hl),e
        inc hl
        ld (hl),d
        ret

; R = Ry * Rx * Rz (see the header), T1 = sx * sz, T2 = sx * cz
rottab: defb Y_T1, 1,  0, Y_SX, Y_SZ
        defb Y_T2, 1,  0, Y_SX, Y_CZ
        defb Y_MT+0, 2,  0, Y_CY, Y_CZ,  0, Y_SY, Y_T1          ; cy cz + sy sx sz
        defb Y_MT+2, 2,  0, Y_CY, Y_SZ,  1, Y_SY, Y_T2          ; cy sz - sy sx cz
        defb Y_MT+4, 1,  0, Y_SY, Y_CX                          ; sy cx
        defb Y_MT+6, 1,  1, Y_CX, Y_SZ                          ; -cx sz
        defb Y_MT+8, 1,  0, Y_CX, Y_CZ                          ; cx cz
        defb Y_MT+10, 1, 0, Y_SX, Y_ONE                         ; sx
        defb Y_MT+12, 2, 0, Y_CY, Y_T1,  1, Y_SY, Y_CZ          ; cy sx sz - sy cz
        defb Y_MT+14, 2, 1, Y_SY, Y_SZ,  1, Y_CY, Y_T2          ; -sy sz - cy sx cz
        defb Y_MT+16, 1, 0, Y_CY, Y_CX                          ; cy cx
        defb 0x7F

; M = C * R (C at Y_CMAT, R copied to Y_TR)
cxrtab: defb Y_MT+0,  3, 0, Y_CMAT+0,  Y_TR+0,  0, Y_CMAT+2,  Y_TR+6,  0, Y_CMAT+4,  Y_TR+12
        defb Y_MT+2,  3, 0, Y_CMAT+0,  Y_TR+2,  0, Y_CMAT+2,  Y_TR+8,  0, Y_CMAT+4,  Y_TR+14
        defb Y_MT+4,  3, 0, Y_CMAT+0,  Y_TR+4,  0, Y_CMAT+2,  Y_TR+10, 0, Y_CMAT+4,  Y_TR+16
        defb Y_MT+6,  3, 0, Y_CMAT+6,  Y_TR+0,  0, Y_CMAT+8,  Y_TR+6,  0, Y_CMAT+10, Y_TR+12
        defb Y_MT+8,  3, 0, Y_CMAT+6,  Y_TR+2,  0, Y_CMAT+8,  Y_TR+8,  0, Y_CMAT+10, Y_TR+14
        defb Y_MT+10, 3, 0, Y_CMAT+6,  Y_TR+4,  0, Y_CMAT+8,  Y_TR+10, 0, Y_CMAT+10, Y_TR+16
        defb Y_MT+12, 3, 0, Y_CMAT+12, Y_TR+0,  0, Y_CMAT+14, Y_TR+6,  0, Y_CMAT+16, Y_TR+12
        defb Y_MT+14, 3, 0, Y_CMAT+12, Y_TR+2,  0, Y_CMAT+14, Y_TR+8,  0, Y_CMAT+16, Y_TR+14
        defb Y_MT+16, 3, 0, Y_CMAT+12, Y_TR+4,  0, Y_CMAT+14, Y_TR+10, 0, Y_CMAT+16, Y_TR+16
        defb 0x7F

; ============================================================================
; dacang: DE = the number in DAC as an angle: degrees to 65536 units per
; turn, rounded (half away from 0), any value (whole turns removed from the
; decimal digits: r = integer part mod 360, g = fraction * 65536, then
; (r * 65536 + g + 180) / 360). Type mismatch for a string. Changes IX, IY.
; ============================================================================
dacang: ld a,(VALTYP)
        cp 3
        jp z,di_str
        cp 2
        jr nz,an_1
        ld hl,(DAC+2)           ; integer
        ld a,h
        push af
        bit 7,h
        jr z,an_0
        ex de,hl
        call negde
        ex de,hl                ; |value| (32768 stays 8000h)
an_0:   ld bc,0 - 23040         ; r = |value| mod 360: 360 * 64, 32, ... 1
        call am360
        ld bc,0 - 11520
        call am360
        ld bc,0 - 5760
        call am360
        ld bc,0 - 2880
        call am360
        ld bc,0 - 1440
        call am360
        ld bc,0 - 720
        call am360
        ld bc,0 - 360
        call am360
        add hl,hl               ; (r * 65536 + 180) / 360 from angtab
        ld de,angtab
        add hl,de
        ld e,(hl)
        inc hl
        ld d,(hl)
        pop af
        rla
        ret nc
        jp negde

; am360: HL -= -BC when HL >= -BC.
am360:  add hl,bc
        ret c
        or a
        sbc hl,bc
        ret
an_1:   ld b,6                  ; digits: single 6, double 14
        cp 4
        jr z,an_2
        ld b,14
an_2:   ld a,(DAC)
        push af                 ; sign
        and 0x7F
        jr nz,an_3
        pop af
        ld de,0                 ; zero
        ret
an_3:   sub 0x40
        ld c,a                  ; C = digits before the point (signed)
        ld hl,0                 ; r
        ld d,1                  ; digit number
        or a
        jp m,an_6
        jr z,an_6
an_4:   xor a
        ld e,a
        ld a,d
        cp b
        jr z,an_4a
        jr nc,an_5              ; past the digits: 0
an_4a:  call bcdd
        ld e,a
an_5:   ld a,e
        call r10
        inc d
        ld a,d
        dec a
        cp c
        jr c,an_4
an_6:   ; g = fraction * 65536: digits b down to max(c, 0) + 1
        push hl                 ; r
        ld hl,0
        ld a,c
        bit 7,a
        jr z,an_7
        xor a
an_7:   ld e,a                  ; E = last integer digit number
        ld a,b
an_8:   cp e
        jr z,an_8b
        jr c,an_8b
        push af
        call bcdd
        push de
        ld d,0
        ld e,a                  ; DE:HL = digit * 65536 + g
        or h
        or l
        jr z,an_8a              ; a 0 digit while g = 0: g stays 0
        push bc
        ld bc,10
        call div32
        pop bc
an_8a:  pop de
        pop af
        dec a
        jr an_8
an_8b:  ld a,c                  ; leading zeros after the point
        bit 7,a
        jr z,an_8d
        neg
an_8c:  push af
        ld a,h
        or l
        jr z,an_8e              ; g = 0 stays 0
        ld de,0
        push bc
        ld bc,10
        call div32
        pop bc
an_8e:  pop af
        dec a
        jr nz,an_8c
an_8d:  ex de,hl                ; DE = g
        pop hl                  ; r
an_9:   ex de,hl                ; DE = r, HL = g
        ld bc,180
        add hl,bc
        jr nc,an_10
        inc de
an_10:  ld a,d                  ; r = 360 after the carry: a whole turn
        or a
        jr z,an_11
        ld a,e
        cp 360 - 256
        jr nz,an_11
        ld hl,0
        jr an_12
an_11:  ld bc,360
        call div32
an_12:  ex de,hl
        pop af
        rla
        ret nc
        jp negde

; bcdd: A = digit A (1-14) of the BCD mantissa in DAC. Keeps BC, DE, HL.
bcdd:   push hl
        push bc
        dec a
        ld c,a
        srl a
        ld hl,DAC + 1
        add a,l
        ld l,a
        jr nc,bd_1
        inc h
bd_1:   ld a,(hl)
        bit 0,c
        jr nz,bd_2
        rrca
        rrca
        rrca
        rrca
bd_2:   and 0x0F
        pop bc
        pop hl
        ret

; r10: HL = (HL * 10 + A) mod 360, HL < 360. Keeps BC, DE.
r10:    push de
        ld e,l
        ld d,h
        add hl,hl
        add hl,hl
        add hl,de
        add hl,hl
        ld e,a
        ld d,0
        add hl,de
        ld de,360
r1_1:   or a
        sbc hl,de
        jr nc,r1_1
        add hl,de
        pop de
        ret

bank1_end:
        defs 0x8000 - $, 0xFF
