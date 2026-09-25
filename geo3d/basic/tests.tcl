# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
#
# tests.tcl - openMSX checks for the geo3d BASIC ROM (out/G3BASIC.ROM), run
# by run_tests.sh, one machine per openMSX instance.
#
# The BASIC programs in disk/ talk to this script through two unused ports:
#   OUT &H2F,v   a data byte for the next check point
#   OUT &H2E,n   check point n: the script looks at the machine and prints
#                PASS/FAIL lines (n = 250: a BASIC error, ERR and ERL)
# Breakpoints on the fixed entry points of the ROM (INIT 4020h, STATEMENT
# 4023h, H.TIMI 4029h, "not ours" exit 402Dh) count what the BIOS and BASIC
# do with it; watchpoints log the writes to the V9968 control port, PORT#4
# and the geo3d ports. Breakpoints on a few labels of the label file fake a
# busy geo3d, a busy command engine or a mirrored VDP where a test needs one.
# Check point 48 (before most G3INITs) undoes what G3INIT sets (V9968
# registers, BASIC's copies, the active flag), so the checks after the call
# see what that very call did.
#
# Runs (MODE):
#   gkey  G held at boot: nothing installed (G3GKEY.BAS); the HIMEM and
#         FRE(0) baseline for the others
#   main  G3SKEL.BAS, LIST, G3SKEL2.BAS (a G3INIT left on), a reset, then
#         G3RESET.BAS
#   clea  H.CLEA taken by an earlier ROM (faked at INIT): on without a work
#         area (G3CLEA.BAS)
# Env: OUT (result file), CFG (turbor, msx2p, msx2, msx1), PORT (152: 98h
# profile, 136: 88h), MODE, BOOT (seconds before typing), GFILE (HIMEM and
# FRE(0) of the gkey run: written by it, read by the others), LBL (label
# file of the ROM).
set renderer none
set throttle off
set mute on

set ::cfg  $::env(CFG)
set ::P    $::env(PORT)
set ::tmode $::env(MODE)
set ::log  [open $::env(OUT) w]
fconfigure $::log -buffering line
set ::npass 0
set ::nfail 0

set ::lbl [dict create]
set f [open $::env(LBL)]
foreach line [split [read $f] \n] {
  if {[regexp {^(\S+):\s+equ \$([0-9a-fA-F]+)} $line -> n v]} { dict set ::lbl $n [expr {"0x$v"}] }
}
close $f
proc L {n} { dict get $::lbl $n }

# ---- output ------------------------------------------------------------------
proc say {s} { puts $::log $s }
proc check {name ok {detail ""}} {
  if {$ok} {
    incr ::npass
    say "PASS $::cfg: $name"
  } else {
    incr ::nfail
    say "FAIL $::cfg: $name ($detail)"
  }
}
proc eq {name got exp} { check $name [expr {$got eq $exp}] "got $got, expected $exp" }
proc finish {} {
  say "DONE $::cfg $::tmode: $::npass passed, $::nfail failed"
  close $::log
  exit
}

# ---- helpers -----------------------------------------------------------------
proc h2 {v} { format %02X [expr {$v & 0xFF}] }
proc h4 {v} { format %04X [expr {$v & 0xFFFF}] }
proc mem {a n} {
  set s ""
  for {set i 0} {$i < $n} {incr i} { append s [format %02X [peek [expr {($a + $i) & 0xFFFF}]]] }
  return $s
}
proc hexl {l} {
  set s ""
  foreach b $l { append s [format %02X [expr {$b & 0xFF}]] }
  return $s
}
proc pokes {a l} {
  foreach b $l {
    poke $a $b
    incr a
  }
}

# the V9968 under test: the machine's VDP (98h) or the cartridge one (88h).
# VRAM is read and written through the physical debuggable: the cartridge
# V9968 starts in a planar mode, where the logical one interleaves addresses
# (SCREEN 5 is linear, so both views agree after G3INIT).
if {$::P == 0x98} {
  set ::vdp VDP
  set ::vram "physical VRAM"
} else {
  set ::vdp V9968
  set ::vram "physical V9968 VRAM"
}
set ::oP [expr {$::P == 0x98 ? 0x88 : 0x98}]
proc vreg {r} { debug read "$::vdp regs" $r }
proc vid {} { expr {([debug read "$::vdp status regs" 1] >> 1) & 0x1F} }
proc vce {} { expr {[debug read "$::vdp status regs" 2] & 1} }
proc geostat {} { debug read ioports [expr {$::P + 5}] }

# palettes as the ROM writes them (0RRR0BBB, 00000GGG) ...
set ::pal_g3 {0x00 0x00 0x01 0x00 0x12 0x01 0x13 0x01 0x14 0x02 0x15 0x02 0x26 0x03 0x27 0x03
              0x10 0x01 0x20 0x01 0x30 0x02 0x40 0x02 0x50 0x03 0x60 0x03 0x70 0x04 0x77 0x07}
set ::pal_msx {0x00 0x00 0x00 0x00 0x11 0x06 0x33 0x07 0x17 0x01 0x27 0x03 0x51 0x01 0x27 0x06
               0x71 0x01 0x73 0x03 0x61 0x06 0x64 0x06 0x11 0x04 0x65 0x02 0x55 0x05 0x77 0x07}
# ... and as the openMSX V9968 keeps them: 15 bits G5 R5 B5, x5 = x3*4 + x3/2
proc c5 {v} { expr {(($v & 7) << 2) | (($v & 7) >> 1)} }
proc pal15 {bytes} {
  set r {}
  foreach {rb g} $bytes {
    lappend r [format %04X [expr {([c5 $g] << 10) | ([c5 [expr {$rb >> 4}]] << 5) | [c5 $rb]}]]
  }
  return $r
}
proc pal_now {} {
  binary scan [debug read_block "$::vdp palette" 0 32] su* l
  set r {}
  foreach v $l { lappend r [format %04X $v] }
  return $r
}

# ---- the ROM: INIT, STATEMENT, hooks -------------------------------------------
proc ours {} {
  expr {[peek 0x4010] == 0x47 && [peek 0x4011] == 0x33 && [peek 0x4012] == 0x42 && [peek 0x4013] == 0x41}
}
set ::inits 0
set ::slot -1
set ::grp 0
set ::banner ""
proc on_init {} {
  incr ::inits
  lassign [get_selected_slot 1] ps ss
  if {$ss eq "X"} {
    set s 0
    set ::slot $ps
  } else {
    set s $ss
    set ::slot [expr {0x80 | ($ss << 2) | $ps}]
  }
  set ::ps $ps
  set ::ss $s
  set ::grp [expr {0xFD09 + 32 * $ps + 8 * $s}]
  if {$::tmode eq "gkey"} { after time 0.3 { keymatrixup 3 0x10 } }
  # clea: a ROM earlier in slot order has taken H.CLEA (a JP to a RET)
  if {$::tmode eq "clea" && $::inits == 1} { pokes 0xFED0 {0xC3 0xD4 0xFE 0xC9 0xC9} }
}
debug set_bp 0x4020 {[ours]} { on_init }
# the banner, as the screen looks at the end of INIT
debug set_bp [L in_end] {[ours]} { set ::banner [get_screen] }
proc blk {} { peek16 [expr {$::grp + 2}] }
# SLTWRK flags: signed in this boot, V9968 mode to undo, port 98h
proc grpflags {} {
  set f [peek $::grp]
  return "[expr {($f >> 1) & 1}] [expr {($f >> 3) & 1}] [expr {($f >> 4) & 1}]"
}

set ::stmts 0
set ::stmts0 0
set ::passes 0
set ::passes0 0
set ::pass_bad 0
set ::stmt_hl 0
debug set_bp 0x4023 {[ours]} { incr ::stmts; set ::stmt_hl [reg HL] }
debug set_bp 0x402D {[ours]} {
  incr ::passes
  if {!([reg F] & 1) || [reg HL] != $::stmt_hl} { incr ::pass_bad }
}
set ::timis 0
debug set_bp 0x4029 {[ours]} { incr ::timis }
set ::oldhits 0

# ---- port logs -------------------------------------------------------------------
set ::data {}
debug set_watchpoint write_io 0x2F {} { lappend ::data $::wp_last_value }
debug set_watchpoint write_io 0x2E {} { checkpoint $::wp_last_value }

set ::gidx 0
set ::gstep {}
debug set_watchpoint write_io [expr {$::P + 5}] {} { set ::gidx $::wp_last_value }
debug set_watchpoint write_io [expr {$::P + 7}] {} { geo_w $::wp_last_value }
proc geo_w {v} {
  lappend ::gstep [list $::gidx $v]
  if {$::gidx < 0x50 || $::gidx > 0x53} { set ::gidx [expr {($::gidx + 1) & 0xFF}] }
}
set ::p4 {}
set ::p4now -1
debug set_watchpoint write_io [expr {$::P + 4}] {} { lappend ::p4 $::wp_last_value; set ::p4now $::wp_last_value }
# reads of PORT#4, of this profile and of the other one
set ::p4r {}
set ::o4r {}
debug set_watchpoint read_io [expr {$::P + 4}] {} { lappend ::p4r 1 }
debug set_watchpoint read_io [expr {$::oP + 4}] {} { lappend ::o4r 1 }
set ::ind {}
debug set_watchpoint write_io [expr {$::P + 3}] {} { lappend ::ind $::wp_last_value }
# register writes (value, 80h + register) on P+1: register, value, the
# PORT#4 value then, and 1 when this ROM wrote it itself (PC in its bank 0)
set ::latch -1
set ::rw {}
debug set_watchpoint write_io [expr {$::P + 1}] {} { ctrl_w $::wp_last_value }
debug set_watchpoint read_io [list $::P [expr {$::P + 1}]] {} { set ::latch -1 }
proc ctrl_w {v} {
  if {$::latch < 0} {
    set ::latch $v
    return
  }
  if {($v & 0xC0) == 0x80} {
    set pc [reg PC]
    lappend ::rw [list [expr {$v & 0x3F}] $::latch $::p4now [expr {[ours] && $pc >= 0x4000 && $pc < 0x6000}] [h4 $pc]]
  }
  set ::latch -1
}
proc r2021 {} {
  set l {}
  foreach w $::rw {
    lassign $w r v p
    if {$r == 20 || $r == 21} { lappend l "R#$r=[h2 $v]/[h2 $p]" }
  }
  return [join $l " "]
}

# ---- check points ------------------------------------------------------------------
proc checkpoint {n} {
  set d $::data
  set ::data {}
  if {$n == 250} {
    lassign [lrange $d end-2 end] err lo hi
    say "  info $::cfg: BASIC error $err in line [expr {$lo + 256 * $hi}]"
    set ::data [lrange $d 0 end-3]
    return
  }
  if {[catch {cp $n $d} msg]} { check "check point $n" 0 "Tcl error: $msg" }
  set ::p4 {}
  set ::p4r {}
  set ::o4r {}
  set ::rw {}
  set ::ind {}
  set ::gstep {}
  set ::stmts0 $::stmts
  set ::passes0 $::passes
}
proc calls {} { expr {$::stmts - $::stmts0} }
proc passes {} { expr {$::passes - $::passes0} }
proc err {name d exp} { eq "$name: ERR $exp" [lindex $d 0] $exp }
proc other {} { expr {$::P == 0x98 ? "88h" : "98h"} }
proc prof {} { expr {$::P == 0x98 ? "98h" : "88h"} }
# noaccess: no register or PORT#4 write since the last check point. own = 1:
# only this ROM's own writes count (BASIC's error handling writes R#46 = 0 on
# the 98h VDP when a CALL raises an error)
proc noaccess {tag {own 0}} {
  set l {}
  foreach w $::rw {
    if {!$own || [lindex $w 3]} { lappend l $w }
  }
  check "$tag: no V9968 access[expr {$own ? " by the ROM" : ""}]" [expr {[llength $l] == 0 && [llength $::p4] == 0}] \
    "registers (r value PORT#4 ours PC): $l, PORT#4: $::p4"
}

proc cp {n d} {
  switch -- $n {
    1 { cp_boot $d }
    10 {}
    11 { err "CALL G3FOO (G3 name that does not exist)" $d 2 }
    12 { err "CALL G3OBJ(1,1) (in the spec, not implemented yet)" $d 2 }
    13 {
      err "CALL XYZ (not a G3 name)" $d 2
      eq "CALL XYZ: our handler called once, passed on with carry and HL unchanged" \
        "[calls] [passes] $::pass_bad" "1 1 0"
    }
    14 { err "CALL G3END before any G3INIT (harmless)" $d 0 }
    15 { err "98h: SCREEN 0, then CALL G3INIT" $d 5 }
    16 {
      err "CALL MEMINI still reaches the SUB-ROM" $d 0
      eq "CALL MEMINI: our handler passed it on" "[passes] $::pass_bad" "1 0"
    }
    20 { err "CALL G3INIT(6)" $d 5 }
    21 { err "CALL G3INIT(7) (SCREEN 7 not implemented yet)" $d 5 }
    22 { err "CALL G3INIT(8) (SCREEN 8 not implemented yet)" $d 5 }
    23 { err "CALL G3INIT(,&H90)" $d 5 }
    24 { err "CALL G3INIT(\"A\")" $d 13 }
    25 { err "CALL G3INIT(1E6)" $d 6 }
    26 { err "CALL G3INIT(-40000!)" $d 6 }
    27 { err "CALL G3INIT(5.5) (rounds to 6)" $d 5 }
    28 {
      err "CALL G3INIT(5,&H[string range [other] 0 1]) (profile not there)" $d 19
      eq "probe of [other]: its PORT#4 ([h2 [expr {$::oP + 4}]]h) not read (nothing at [h2 [expr {$::oP + 5}]]h)" [llength $::o4r] 0
    }
    29 { err "CALL G3INIT(5 (no closing parenthesis)" $d 2 }
    30 { err "CALL G3INIT(5,P,1) (too many arguments)" $d 2 }
    31 { err "CALL G3INIT(5)X (junk after the arguments)" $d 2 }
    32 { err "CALL G3INIT(A\$) (string variable)" $d 13 }
    33 { err "CALL G3INIT(&H88) (modo 136)" $d 5 }
    34 { set ::mirror 1 }
    35 {
      err "probe: a VDP mirrored at P+5 (S#2 there), CALL G3INIT(5,P)" $d 19
      eq "probe: mirror seen, PORT#4 (P+4) not read" "$::mirror [llength $::p4r]" "2 0"
      set ::mirror 0
    }
    36 {
      disturb 0
      set ::bdet 1
      set ::bdet_hits 0
    }
    37 {
      set ::bdet 0
      err "probe: geo3d busy (status 01h at P+5 and at 48h), CALL G3INIT(5,P)" $d 0
      eq "probe: both busy status reads faked" $::bdet_hits 2
      st_on "G3INIT with geo3d busy at the probe"
    }
    40 { cp_before }
    41 { cp_init $d }
    42 { cp_readback $d }
    43 { cp_hooks_run }
    44 { busy_on geo 0 -1 }
    45 {
      busy_off "wait for geo3d: timeout" $d
      wait_time "wait for geo3d"
    }
    46 { busy_on geo 1 300 }
    47 {
      busy_off "wait for geo3d: CTRL+STOP" $d
      stop_went_on "wait for geo3d"
    }
    48 { disturb [lindex $d 0] }
    49 {
      vfill
      disturb 0
    }
    50 {
      err "CALL G3INIT again" $d 0
      st_on "G3INIT again"
      st_vram "G3INIT again" 1
    }
    51 { err "_G3INIT" $d 0; st_on "_G3INIT" }
    52 { err "CALL G3INIT(5,&H[string range [prof] 0 1])" $d 0; st_on "G3INIT(5,P)" }
    53 { err "CALL G3INIT(,&H[string range [prof] 0 1])" $d 0; st_on "G3INIT(,P)" }
    54 { err "CALL G3INIT ( 5 , P ) (spaces)" $d 0; st_on "G3INIT ( 5 , P )" }
    55 { err "CALL G3INIT(X!) with X! = 4.6 (rounds to 5)" $d 0; st_on "G3INIT(X!)" }
    56 { err "CALL G3INIT(X#,X%) (double and integer variables)" $d 0; st_on "G3INIT(X#,X%)" }
    57 { err "CALL G3INIT(5.4999,P+.4) (rounded)" $d 0; st_on "G3INIT(5.4999,P+.4)" }
    58 {
      eq "98h: SCREEN 5 after G3INIT keeps V9968 mode (ID 3, R#21 bit 0 = 0, R#20 = 1)" \
        "[vid] [expr {[vreg 21] & 1}] [vreg 20]" "3 0 1"
    }
    60 { cp_usr $d }
    61 {
      err "trampoline: USR(1234.7) through FRCINT" $d 0
      eq "trampoline: FRCINT(1234.7) = 1234 (04D2h)" [h4 [expr {[lindex $d 1] + 256 * [lindex $d 2]}]] 04D2
      check "trampoline: page 1 = the BASIC ROM inside FRCINT" \
        [expr {[llength $::frc_page1] > 0 && [lsearch $::frc_page1 1] < 0}] "this ROM seen in page 1: $::frc_page1"
      eq "trampoline: page 1 back on this ROM after the call (4010h reads G3B)" [mem [expr {$::usr_f + 2}] 3] 473342
    }
    62 {
      set ::usr_armed 0
      err "trampoline: FRCINT(40000) raises Overflow inside BASIC" $d 6
    }
    63 { busy_on ce 1 300 }
    64 {
      busy_off "wait for the VDP: CTRL+STOP" $d
      stop_went_on "wait for the VDP"
    }
    65 {
      busy_on none 0 -1
      start_lmmc
    }
    66 {
      busy_off "wait for the VDP: an LMMC that never gets its data" $d
      eq "wait for the VDP: the LMMC was running before the call (CE = 1)" $::lmmc_ce 1
      wait_time "wait for the VDP"
      check "wait for the VDP: the command stopped once time was up (R#46 = 00h)" [stop_written] "no R#46 write"
    }
    70 {}
    71 { err "CALL G3END" $d 0; st_off "G3END" 1 }
    72 { err "CALL G3END a second time (harmless)" $d 0; st_off "second G3END" 0 }
    73 { err "CALL G3END(1)" $d 2 }
    74 {
      check "PRINT after G3END and SCREEN 0" [string match "*G3END OK*" [get_screen]] "not on screen"
      if {$::P == 0x98} { eq "98h: SCREEN 0 after G3END keeps V9958 mode (ID 2)" [vid] 2 }
    }
    80 {
      if {$::P == 0x98} {
        err "98h: CALL G3INIT(5) in text mode" $d 5
      } else {
        err "88h: CALL G3INIT(5) with BASIC in text mode, after G3END" $d 0
        st_on "88h G3INIT in text mode"
        eq "88h G3INIT in text mode: default palette" [pal_now] [pal15 $::pal_g3]
      }
    }
    81 { err "98h: SCREEN 5, CALL G3INIT after G3END" $d 0; st_on "98h G3INIT after G3END" }
    82 {
      check "98h: text in SCREEN 0 with G3INIT active (V9968 mode)" \
        [string match "*TEXT IN V9968 MODE*" [get_screen]] "not on screen"
      eq "98h: SCREEN 0 keeps V9968 mode (ID 3)" [vid] 3
    }
    83 { err "88h: SCREEN 5 on the internal VDP, CALL G3INIT" $d 0; st_on "88h G3INIT in SCREEN 5" }
    84 { err "88h: SCREEN 8 on the internal VDP, CALL G3INIT (SCREEN 8 later)" $d 5 }
    85 { err "88h: SCREEN 8 on the internal VDP, CALL G3INIT(5)" $d 0; st_on "88h G3INIT(5) in SCREEN 8" }
    88 {
      set b [blk]
      eq "CLEAR 300,HIMEM+100: HIMEM back at the work area" [h4 [peek16 0xFC4A]] [h4 $b]
      eq "CLEAR 300,HIMEM+100: 300 bytes of string space" [expr {[peek16 0xF672] - [peek16 0xF674]}] 300
    }
    89 { err "CALL G3INIT(5) after CLEAR" $d 0; st_on "G3INIT after CLEAR" }
    90 { err "CALL G3END after CLEAR" $d 0 }
    91 {
      eq "CLEAR n,HIMEM+2048 with no room: HIMEM stays above the work area" [h4 [peek16 0xFC4A]] [h4 [expr {[blk] + 2048}]]
      eq "work area given to BASIC: SLTWRK flags: not signed, V9968 mode still to undo" [lrange [grpflags] 0 1] "0 1"
      set ::bak_before [peek 0xF3EA]
      set ::bdr_before [peek 0xF3EB]
      set ::bak_why "as they were: the saved ones went with the work area"
    }
    92 { err "CALL G3END with the work area given to BASIC" $d 0; st_off "G3END without the work area" 1 1 }
    93 { eq "CLEAR 300,HIMEM-2048: HIMEM back at the work area" [h4 [peek16 0xFC4A]] [h4 [blk]] }
    94 {
      err "CALL G3INIT once the work area is back" $d 0
      st_on "G3INIT once the work area is back"
      st_work "G3INIT once the work area is back"
    }
    99 {
      after time 1 { type "LIST 110\r" }
      after time 3 {
        check "LIST works after the tests" [string match "*110 E=0:CALL G3FOO*" [get_screen]] "not on screen"
        type "RUN\"G3SKEL2.BAS\"\r"
      }
    }
    100 { err "second RUN: CALL G3INIT" $d 0; st_on "second RUN" }
    101 { err "second RUN: CALL G3END" $d 0 }
    102 { check "second RUN: G3INIT and G3END done" [string match "*SECOND RUN OK*" [get_screen]] "not on screen" }
    103 {
      err "second RUN: CALL G3INIT, left on for a reset" $d 0
      set b [blk]
      eq "before the reset: work area signed, active and to undo, colours 4, 7 saved" \
        "[mem $b 4] [h2 [peek [expr {$b + 5}]]] [mem [expr {$b + 10}] 2]" "4733574B 03 0407"
      set ::blk_before $b
      after time 0.5 { reset_run }
    }
    110 { err "G held at boot: CALL G3INIT" $d 2 }
    111 { err "G held at boot: CALL G3END" $d 2 }
    120 {
      set b [blk]
      eq "after the reset: the old work area is still in RAM (signed, flags 03)" \
        "[mem $b 4] [h2 [peek [expr {$b + 5}]]]" "4733574B 03"
      eq "after the reset: same work area, SLTWRK flags cleared by INIT" "[h4 $b] [h2 [peek $::grp]]" "[h4 $::blk_before] 00"
      set ::bak_before [peek 0xF3EA]
      set ::bdr_before [peek 0xF3EB]
      set ::bak_why "as before G3INIT"
      eq "after the reset: COLOR 15,1,2" "$::bak_before $::bdr_before" "1 2"
    }
    121 {
      err "after the reset: CALL G3END with only the old work area" $d 0
      noaccess "after the reset: G3END"
      eq "after the reset: G3END: V9968 ID 2" [vid] 2
      eq "after the reset: G3END: BAKCLR, BDRCLR still 1, 2" "[peek 0xF3EA] [peek 0xF3EB]" "1 2"
    }
    122 {
      err "after the reset: CALL G3INIT" $d 0
      st_on "G3INIT after the reset"
      st_work "G3INIT after the reset"
      eq "G3INIT after the reset: colours saved now (1, 2)" [mem [expr {[blk] + 10}] 2] 0102
      eq "G3INIT after the reset: H.TIMI = CALLF to the ROM again" [mem 0xFD9F 5] "F7[h2 $::slot]2940C9"
    }
    123 { err "after the reset: CALL G3END" $d 0; st_off "G3END after the reset" 1 }
    130 { err "H.CLEA taken: CALL G3INIT" $d 7; noaccess "H.CLEA taken: G3INIT" 1 }
    131 { err "H.CLEA taken: CALL G3END" $d 0; noaccess "H.CLEA taken: G3END" }
    132 { err "H.CLEA taken: CALL G3FOO" $d 2 }
    199 {
      if {$::tmode eq "main"} {
        check "the run after the reset ends normally" [string match "*RESET RUN OK*" [get_screen]] "not on screen"
        eq "work area inactive at the end" [expr {[peek [expr {[blk] + 5}]] & 1}] 0
      }
      finish
    }
    default { check "check point $n" 0 "unexpected" }
  }
}

# ---- boot: INIT, work area, HIMEM, FRE(0) -------------------------------------------
proc gheld {} {
  if {[catch {
    set f [open $::env(GFILE)]
    lassign [gets $f] hg fg
    close $f
  }]} {
    check "values of the G-held boot" 0 "no $::env(GFILE)"
    return {}
  }
  return [list $hg $fg]
}
proc cp_boot {d} {
  lassign $d lo hi
  set fre [expr {$lo + 256 * $hi}]
  set him [peek16 0xFC4A]
  say "  info $::cfg: HIMEM [h4 $him] MEMSIZ [h4 [peek16 0xF672]] STKTOP [h4 [peek16 0xF674]] VARTAB [h4 [peek16 0xF6C2]] ARYTAB [h4 [peek16 0xF6C4]] STREND [h4 [peek16 0xF6C6]] SP [h4 [reg SP]] FRE(0) $fre"
  eq "INIT ran once" $::inits 1
  set a [expr {0xFCC9 + 16 * $::ps + 4 * $::ss}]
  eq "SLTATR: STATEMENT registered for page 1 only (not again at 8000h)" \
    "[h2 [peek [expr {$a + 1}]]] [h2 [peek [expr {$a + 2}]]]" "20 00"
  if {$::tmode eq "gkey"} {
    eq "G held: H.CLEA not hooked" [mem 0xFED0 5] C9C9C9C9C9
    eq "G held: no work area, no hook data in SLTWRK" [mem $::grp 8] 0000000000000000
    set f [open $::env(GFILE) w]
    puts $f "$him $fre"
    close $f
    say "  info $::cfg: G held: HIMEM [h4 $him], FRE(0) $fre"
    after time 1 { type "RUN\"G3GKEY.BAS\"\r" }
    return
  }
  set g [gheld]
  if {$::tmode eq "clea"} {
    eq "H.CLEA taken by an earlier ROM: left as it was" [mem 0xFED0 5] C3D4FEC9C9
    eq "H.CLEA taken: SLTWRK: on without a work area (flags 04), no address" [mem $::grp 8] 0400000000000000
    check "H.CLEA taken: the INIT banner says so" [string match "*H.CLEA in use: no work area*" $::banner] \
      "screen at INIT: [string range $::banner 0 160]"
    if {[llength $g]} {
      lassign $g hg fg
      eq "H.CLEA taken: HIMEM, FRE(0) as in the G-held boot (nothing reserved)" "[h4 $him] $fre" "[h4 $hg] $fg"
    }
    after time 1 { type "RUN\"G3CLEA.BAS\"\r" }
    return
  }
  set b [string first "geo3d BASIC 0.1 (" $::banner]
  if {$b >= 0} {
    say "  info $::cfg: INIT banner: [lindex [split [string range $::banner $b end] \n] 0]"
  }
  set want [expr {$::P == 0x98 ? "98h)" : "88h)"}]
  if {[string first "geo3d BASIC" [get_screen]] >= 0} {
    say "  info $::cfg: the INIT banner is still on the screen at the BASIC prompt"
  } else {
    say "  info $::cfg: the INIT banner is gone at the BASIC prompt (BASIC cleared the screen)"
  }
  check "INIT banner printed with the profile found" [string match "*geo3d BASIC 0.1 ($want*" $::banner] "screen at INIT: [string range $::banner 0 120]"
  eq "H.CLEA = CALLF to the ROM (4026h)" [mem 0xFED0 5] "F7[h2 $::slot]2640C9"
  eq "work area right at HIMEM" [h4 [blk]] [h4 $him]
  eq "SLTWRK flags at boot: 00 (nothing signed, nothing to undo)" [h2 [peek $::grp]] 00
  if {[llength $g]} {
    lassign $g hg fg
    eq "HIMEM [h4 $him]: 2048 below the G-held boot ([h4 $hg])" [expr {$hg - $him}] 2048
    eq "FRE(0) $fre: 2048 below the G-held boot ($fg)" [expr {$fg - $fre}] 2048
  }
  check "FRE(0) still sane" [expr {$fre > 10000 && [peek16 0xF674] < $him}] "FRE(0) $fre, STKTOP [h4 [peek16 0xF674]]"
  after time 1 { type "RUN\"G3SKEL.BAS\"\r" }
}

# ---- the main run, after G3SKEL2.BAS: a reset with a G3INIT left on -----------------
proc reset_run {} {
  reset
  after time $::env(BOOT) { type "\r" }
  after time [expr {$::env(BOOT) + 2}] { type "\r" }
  after time [expr {$::env(BOOT) + 4}] { type "RUN\"G3RESET.BAS\"\r" }
}

# ---- G3INIT -------------------------------------------------------------------------
# vfill: page 0 lines 0-211 and all of page 1 = 5Ah; page 0 lines 212-255
# (BASIC's tables in 98h) are kept for the comparison
proc vfill {} {
  debug write_block $::vram 0 [string repeat Z 27136]
  debug write_block $::vram 32768 [string repeat Z 32768]
  set ::keep [debug read_block $::vram 27136 5632]
}

# disturb: undo what G3INIT sets, so that the next checks see the call itself:
# R#2 = 3Fh, R#7 = 5, R#20 = 0, R#21 = 3Bh (V9958 mode), 98h: RG20SAV and
# RG21SAV to match, the active flag of the work area off (bit 1 stays: the
# colours saved for G3END), no PORT#4 value known. k: 1 = H.TIMI back to the
# hook we chain to (a driver's uninstall), 2 = H.TIMI = RET, 3 = H.TIMI = a
# hook chained after ours (JP to a RET)
set ::tk 0
proc disturb {k} {
  foreach {r v} {2 0x3F 7 5 20 0 21 0x3B} { debug write "$::vdp regs" $r $v }
  if {$::P == 0x98} { pokes 0xFFF3 {0 0x3B} }
  set f [expr {[blk] + 5}]
  poke $f [expr {[peek $f] & 0xFE}]
  set ::p4now -1
  set ::tk $k
  switch -- $k {
    1 {
      poke 0xFD9F [peek [expr {$::grp + 1}]]
      for {set i 0} {$i < 4} {incr i} { poke [expr {0xFDA0 + $i}] [peek [expr {$::grp + 4 + $i}]] }
    }
    2 { poke 0xFD9F 0xC9 }
    3 { pokes 0xFD9F {0xC3 0xD4 0xFE 0xC9 0xC9} }
  }
}
proc timi_chk {tag} {
  if {$::tk == 0} { return }
  set ours "F7[h2 $::slot]2940C9"
  set g [mem $::grp 8]
  set old "[string range $g 2 3][string range $g 8 15]"
  switch -- $::tk {
    1 {
      eq "$tag: H.TIMI taken out (old hook written back), G3INIT puts ours back" "[mem 0xFD9F 5] $old" "$ours $::timi_pre"
    }
    2 {
      eq "$tag: H.TIMI taken out (POKE &HFD9F,&HC9), G3INIT puts ours back, nothing to chain" \
        "[mem 0xFD9F 5] [string range $old 0 1]" "$ours C9"
      # the chain as it was, for the rest of the run
      set l {}
      foreach {x y} [split $::timi_pre ""] { lappend l "0x$x$y" }
      poke [expr {$::grp + 1}] [lindex $l 0]
      pokes [expr {$::grp + 4}] [lrange $l 1 4]
    }
    3 {
      eq "$tag: a hook chained after ours (H.TIMI = JP) is left alone" "[mem 0xFD9F 5] $old" "C3D4FEC9C9 $::timi_pre"
      pokes 0xFD9F [list 0xF7 $::slot 0x29 0x40 0xC9]
    }
  }
  eq "$tag: SLTWRK flag: H.TIMI hooked" [expr {[peek $::grp] & 1}] 1
  set ::tk 0
}

proc cp_before {} {
  vfill
  set ::timi_pre [mem 0xFD9F 5]
  set ::rg2021 [mem 0xFFF3 2]
  set ::bak_before [peek 0xF3EA]
  set ::bdr_before [peek 0xF3EB]
  set ::bak_why "as before G3INIT"
  if {$::P == 0x88} {
    set ::int_regs [debug read_block "VDP regs" 0 [debug size "VDP regs"]]
    set ::int_pal ""
    catch { set ::int_pal [debug read_block "VDP palette" 0 32] }
  }
}

# st_on: the state every successful G3INIT leaves, and what the call wrote
proc st_on {tag} {
  eq "$tag: V9968 ID 3" [vid] 3
  eq "$tag: R#21 bit 0 = 0, R#20 = 01h" "[expr {[vreg 21] & 1}] [h2 [vreg 20]]" "0 01"
  eq "$tag: PORT#4 writes: unlock, lock" [hexl $::p4] 0080
  eq "$tag: R#21, R#20 written with PORT#4 unlocked (value/PORT#4)" [r2021] "R#21=00/00 R#20=01/00"
  eq "$tag: R#2 = 1Fh (page 0 shown), R#7 = 0" "[h2 [vreg 2]] [vreg 7]" "1F 0"
  eq "$tag: geo3d idle" [expr {[geostat] & 0x0F}] 0
  eq "$tag: work area signed and active" "[mem [blk] 4] [expr {[peek [expr {[blk] + 5}]] & 1}]" "4733574B 1"
  eq "$tag: SLTWRK flags: signed in this boot, V9968 mode to undo, port" [grpflags] "1 1 [expr {$::P == 0x98}]"
  if {$::P == 0x98} {
    eq "$tag: RG20SAV, RG21SAV = 01h, 00h" [mem 0xFFF3 2] 0100
  }
  timi_chk $tag
}

proc cp_init {d} {
  set tag "first G3INIT ([prof])"
  err $tag $d 0
  eq "$tag: one STATEMENT call for one CALL" [calls] 1
  st_on $tag
  set wl {}
  foreach w $::rw {
    lassign $w r v p
    if {$r == 17} { lappend wl [h2 $v] }
  }
  eq "$tag: indirect writes start at R#51 (LRMM window) and R#36 (HMMV, twice)" [join $wl " "] "33 24 24"
  eq "$tag: R#51-58 = 0,0,511,2047; HMMV lines 0-211 of page 0, then page 1, colour 0" [hexl $::ind] \
    "00000000FF01FF07000000000001D4000000C0000000010001D4000000C0"
  eq "$tag: default palette in the V9968 (0 black, 1-7 blue, 8-14 orange, 15 white)" [pal_now] [pal15 $::pal_g3]
  # 88h: the first G3INIT sets R#8 VR 0 -> 1, and openMSX then swaps the
  # first 32 KB of VRAM (VDPVRAM::updateVRMode): only the cleared lines can
  # be checked here; lines 212-255 are checked at the next G3INIT (cp 50)
  st_vram $tag [expr {$::P == 0x98}]
  st_geo $tag
  st_work $tag
  if {$::P == 0x98} {
    eq "$tag: RG7SAV, BAKCLR, BDRCLR = 0" "[h2 [peek 0xF3E6]][h2 [peek 0xF3EA]][h2 [peek 0xF3EB]]" 000000
    eq "$tag: RG2SAV = 1Fh, DPPAGE = 0, ACPAGE = 0" "[h2 [peek 0xF3E1]][h2 [peek 0xFAF5]][h2 [peek 0xFAF6]]" 1F0000
    eq "$tag: SCRMOD still 5" [peek 0xFCAF] 5
  } else {
    eq "$tag: V9968 in SCREEN 5 (R#0 06h, R#1 40h: IE0 = IE1 = 0; R#8 0Ah; R#9 80h)" \
      "[h2 [vreg 0]][h2 [vreg 1]][h2 [vreg 8]][h2 [vreg 9]]" 06400A80
    check "$tag: internal VDP registers untouched" \
      [string equal [debug read_block "VDP regs" 0 [debug size "VDP regs"]] $::int_regs] "changed"
    if {$::int_pal ne ""} {
      check "$tag: internal VDP palette untouched" [string equal [debug read_block "VDP palette" 0 32] $::int_pal] "changed"
    }
    eq "$tag: RG20SAV/RG21SAV (internal VDP copies) untouched" [mem 0xFFF3 2] $::rg2021
  }
  # hooks
  eq "$tag: H.TIMI = CALLF to the ROM (4029h)" [mem 0xFD9F 5] "F7[h2 $::slot]2940C9"
  set g [mem $::grp 8]
  eq "$tag: old H.TIMI hook kept in our SLTWRK group" "[string range $g 2 3][string range $g 8 15]" $::timi_pre
  eq "$tag: SLTWRK flag: H.TIMI hooked" [expr {[peek $::grp] & 1}] 1
  set ::watch_old [watch_old]
}

# vdiff: where two VRAM images (strings) first differ, for the FAIL lines
proc vdiff {base a b} {
  set n [string length $a]
  set i 0
  while {$i < $n && [string index $a $i] eq [string index $b $i]} { incr i }
  if {$i >= $n} { return "equal" }
  set c 0
  for {set j $i} {$j < $n} {incr j} {
    if {[string index $a $j] ne [string index $b $j]} { incr c }
  }
  binary scan [string range $a $i [expr {$i + 7}]] cu* x
  binary scan [string range $b $i [expr {$i + 7}]] cu* y
  return "$c bytes differ, first at [h4 [expr {$base + $i}]]: [hexl $x] instead of [hexl $y]"
}

proc st_vram {tag full} {
  set z [string repeat "\0" 27136]
  set v [debug read_block $::vram 0 27136]
  check "$tag: lines 0-211 of page 0 cleared" [string equal $v $z] [vdiff 0 $v $z]
  set v [debug read_block $::vram 32768 27136]
  check "$tag: lines 0-211 of page 1 cleared" [string equal $v $z] [vdiff 32768 $v $z]
  if {!$full} { return }
  set v [debug read_block $::vram 59904 5632]
  set k [string repeat Z 5632]
  check "$tag: lines 212-255 of page 1 untouched" [string equal $v $k] [vdiff 59904 $v $k]
  set now [debug read_block $::vram 27136 5632]
  if {$::P == 0x98} {
    # BASIC's SCREEN 5 palette table (7680h) follows SETPLT
    set o [expr {0x7680 - 27136}]
    binary scan [string range $now $o [expr {$o + 31}]] cu* t
    eq "$tag: BASIC's palette table (7680h) = the default palette" [hexl $t] [hexl $::pal_g3]
    set now [string replace $now $o [expr {$o + 31}] [string range $::keep $o [expr {$o + 31}]]]
  }
  check "$tag: lines 212-255 of page 0 (BASIC's tables) untouched" [string equal $now $::keep] [vdiff 27136 $now $::keep]
}

proc st_geo {tag} {
  set exp [dict create]
  foreach {i v} {0x18 0x00 0x19 0x01 0x1A 0x80 0x1B 0x00 0x1C 0x6A 0x1D 0x00 0x1E 0x10 0x1F 0x00
                 0x20 0x00 0x21 0x01 0x22 0xD4 0x23 0x00
                 0x40 0x00 0x41 0x00 0x42 0x00 0x43 0x00 0x44 0x0F 0x45 0x00 0x46 0x00 0x47 0x01
                 0x58 0x00 0x59 0x00 0x5A 0x0D 0x5B 0xDB 0x5C 0xF3 0x5D 0x24 0x5E 0x0D 0x5F 0xDB} {
    dict set exp [expr {$i}] [expr {$v}]
  }
  set got [dict create]
  foreach w $::gstep {
    lassign $w i v
    dict set got $i $v
  }
  set bad {}
  dict for {i v} $exp {
    if {![dict exists $got $i] || [dict get $got $i] != $v} { lappend bad [h2 $i] }
  }
  dict for {i v} $got {
    if {![dict exists $exp $i]} { lappend bad "extra [h2 $i]" }
  }
  check "$tag: geo3d F 256, CX 128, CY 106, ZNEAR 16, W 256, H 212, YPAGE 256, COLOR 15, light (-1,1,-1)" \
    [expr {[llength $bad] == 0}] "wrong: $bad"
  check "$tag: geo3d not started (no write to 48h)" [expr {![dict exists $got 0x48]}] "RUN written"
}

proc st_work {tag} {
  set b [blk]
  eq "$tag: work area slot, flags (active, to undo), port, mode" "[mem [expr {$b + 4}] 4]" "[h2 $::slot]03[h2 $::P]05"
  eq "$tag: scene defaults (background 0, pace 2, pages 0/1, window 256x212, zoom 100, camera (0,0,-300), origin, light (-1,1,-1))" \
    [mem [expr {$b + 12}] 30] [hexl {0 2 0 1 0 1 0xD4 0 0 0 100 0 0 0 0 0 0xD4 0xFE 0 0 0 0 0 0 0xFF 0xFF 1 0 0xFF 0xFF}]
  check "$tag: 16 objects empty" [string equal [mem [expr {$b + 128}] 768] [string repeat 00 768]] "not zero"
  set t [expr {$b + 80}]
  eq "$tag: trampoline slot byte" [h2 [peek [expr {$t + [L tr_slot] - [L tr_tpl]}]]] [h2 $::slot]
}

proc watch_old {} {
  set h $::timi_pre
  set op [string range $h 0 1]
  if {$op eq "F7"} {
    set s [expr {"0x[string range $h 2 3]"}]
    set a [expr {"0x[string range $h 6 7][string range $h 4 5]"}]
    set ps [expr {$s & 3}]
    if {$s & 0x80} { set want "$ps [expr {($s >> 2) & 3}]" } else { set want "$ps X" }
    debug set_bp $a "\[string equal \[get_selected_slot [expr {$a >> 14}]\] {$want}\]" { incr ::oldhits }
    say "  info $::cfg: old H.TIMI hook: CALLF slot [h2 $s] address [h4 $a]"
    return 1
  }
  if {$op eq "C3"} {
    set a [expr {"0x[string range $h 4 5][string range $h 2 3]"}]
    debug set_bp $a {} { incr ::oldhits }
    say "  info $::cfg: old H.TIMI hook: JP [h4 $a]"
    return 1
  }
  say "  info $::cfg: old H.TIMI hook: $h (nothing to chain)"
  return 0
}

proc cp_readback {d} {
  set tag "first G3INIT ([prof])"
  eq "$tag: geo3d 40h-48h read back by BASIC INP (VADDR..YPAGE, status)" [hexl [lrange $d 0 8]] 000000000F00000100
  eq "$tag: geo3d status from BASIC: idle" [expr {[lindex $d 9] & 0x0F}] 0
  set ::t0 [list $::timis $::oldhits [peek16 0xFC9E]]
}

proc cp_hooks_run {} {
  lassign $::t0 t o j
  set dt [expr {$::timis - $t}]
  set dj [expr {([peek16 0xFC9E] - $j) & 0xFFFF}]
  check "H.TIMI: our handler runs at every blank ($dt calls, JIFFY +$dj)" [expr {$dj >= 55 && abs($dt - $dj) <= 2}] "calls $dt, JIFFY +$dj"
  if {$::watch_old} {
    set dold [expr {$::oldhits - $o}]
    check "H.TIMI: the old hook still runs through ours ($dold calls)" [expr {abs($dold - $dt) <= 2}] "old $dold, ours $dt"
  }
}

# ---- waits ------------------------------------------------------------------------
# A breakpoint right after the status read of wait_geo (wg_1+2) or wait_ce
# (wc_2) makes geo3d or the command engine look busy (::fake geo or ce). With
# ::stopkeys 1, CTRL+STOP goes down at the first busy read and up when the
# error is raised. Once the ROM has seen CTRL+STOP (tk_stop), the fake stays
# busy for ::left more reads (-1: for ever), then the real, idle, value shows:
# the wait must go on until then and only then raise Device I/O error.
set ::fake ""
set ::stopkeys 0
set ::stopseen 0
set ::polls 0
set ::left -1
set ::t0 0
set ::t1 0
set ::ce_err -1
set ::geo_err -1
set ::r15_err -1
proc fake_busy {kind} {
  if {$::fake ne $kind || $::left == 0} { return }
  if {$kind eq "geo"} { reg A 0x01 } else { reg A [expr {[reg A] | 1}] }
  if {$::stopkeys == 1} {
    keymatrixdown 7 0x10
    keymatrixdown 6 0x02
    set ::stopkeys 2
  }
  if {$::stopseen} {
    incr ::polls
    if {$::left > 0} { incr ::left -1 }
  }
}
debug set_bp [expr {[L wg_1] + 2}] {[ours]} { fake_busy geo }
debug set_bp [L wc_2] {[ours]} { fake_busy ce }
debug set_bp [L tk_stop] {[ours]} { set ::stopseen 1 }
debug set_bp [L err_io] {[ours]} {
  set ::t1 [machine_info time]
  set ::ce_err [vce]
  set ::geo_err [expr {[geostat] & 0x0F}]
  set ::r15_err [vreg 15]
  if {$::stopkeys} {
    keymatrixup 7 0x10
    keymatrixup 6 0x02
    set ::stopkeys 0
  }
}
proc busy_on {kind stop left} {
  set ::fake $kind
  set ::stopkeys $stop
  set ::left $left
  set ::stopseen 0
  set ::polls 0
  set ::ce_err -1
  set ::geo_err -1
  set ::r15_err -1
  set ::t0 [machine_info time]
  set ::t1 $::t0
}
proc busy_off {tag d} {
  set ::fake ""
  if {$::stopkeys} {
    keymatrixup 7 0x10
    keymatrixup 6 0x02
    set ::stopkeys 0
  }
  err "$tag, CALL G3INIT" $d 19
  eq "$tag: at the error R#15 = 0, geo3d idle, command engine free (CE = 0)" "$::r15_err $::geo_err $::ce_err" "0 0 0"
}
proc wait_time {tag} {
  set dt [expr {$::t1 - $::t0}]
  check "$tag: timeout after [format %.2f $dt] s (about 2 s)" [expr {$dt >= 1.5 && $dt <= 3.0}] "expected 1.5 to 3.0 s"
}
proc stop_went_on {tag} {
  set dt [expr {$::t1 - $::t0}]
  check "$tag: CTRL+STOP seen, the wait went on until the engine was idle (300 busy reads), error after [format %.2f $dt] s" \
    [expr {$::stopseen && $::polls == 300 && $::left == 0 && $dt < 1.0}] \
    "seen $::stopseen, busy reads after it $::polls, left $::left, [format %.2f $dt] s"
}
# an LMMC (DX 0, DY 0, 256 x 212) that never gets its data: CE stays 1
proc start_lmmc {} {
  set c [expr {$::P + 1}]
  debug write ioports $c 36
  debug write ioports $c [expr {0x80 | 17}]
  foreach v {0 0 0 0 0 1 212 0 0x55 0 0xB0} { debug write ioports [expr {$::P + 3}] $v }
  set ::lmmc_ce [vce]
}
proc stop_written {} {
  foreach w $::rw {
    lassign $w r v p
    if {$r == 46 && $v == 0} { return 1 }
  }
  return 0
}

# ---- probes: a breakpoint after the P+5 read of detect (dt_p5) fakes S#2 of
# a mirrored VDP (::mirror) or a busy geo3d (::bdet, also at the 48h read)
set ::mirror 0
set ::bdet 0
set ::bdet_hits 0
debug set_bp [L dt_p5] {[ours] && [reg C] == $::P + 5} {
  if {$::mirror == 1} {
    reg A 0xAC
    set ::mirror 2
  }
  if {$::bdet} {
    reg A 0x01
    incr ::bdet_hits
  }
}
debug set_bp [L dt_st] {[ours] && [reg C] == $::P + 7} {
  if {$::bdet} {
    reg A 0x01
    incr ::bdet_hits
  }
}

# ---- trampoline: tests.tcl writes a USR routine into M%() ---------------------------
#   ld a,<our slot> / ld h,40h / call ENASLT      (page 1 = this ROM)
#   ld hl,<trampoline> / ld ix,FRCINT / call L1 / ld (F),hl
#   ld a,(4010h) / ld (F+2),a  ... 4011h, 4012h   (what page 1 holds now)
#   ld a,(EXPTBL) / ld h,40h / call ENASLT / ei   (page 1 = BASIC again)
#   ld hl,(F) / ld (DAC+2),hl / ld a,2 / ld (VALTYP),a / ret / L1: jp (hl)
# A breakpoint on FRCINT (2F8Ah) notes whether page 1 held this ROM there.
set ::usr_armed 0
set ::usr_f 0
set ::frc_page1 {}
debug set_bp 0x2F8A {$::usr_armed} { lappend ::frc_page1 [ours] }
proc lo {v} { expr {$v & 255} }
proc hi {v} { expr {($v >> 8) & 255} }
proc cp_usr {d} {
  lassign $d l h
  set a [expr {$l + 256 * $h}]
  set t [expr {[blk] + 80}]
  set l1 [expr {$a + 59}]
  set f [expr {$a + 60}]
  set code [list 0x3E $::slot 0x26 0x40 0xCD 0x24 0x00 \
    0x21 [lo $t] [hi $t] 0xDD 0x21 0x8A 0x2F 0xCD [lo $l1] [hi $l1] 0x22 [lo $f] [hi $f]]
  for {set i 0} {$i < 3} {incr i} {
    set x [expr {$f + 2 + $i}]
    lappend code 0x3A [expr {0x10 + $i}] 0x40 0x32 [lo $x] [hi $x]
  }
  lappend code 0x3A 0xC1 0xFC 0x26 0x40 0xCD 0x24 0x00 0xFB \
    0x2A [lo $f] [hi $f] 0x22 0xF8 0xF7 0x3E 0x02 0x32 0x63 0xF6 0xC9 0xE9
  if {[llength $code] != 60} { check "trampoline: USR routine size" 0 "[llength $code] bytes, not 60" }
  pokes $a $code
  pokes $f {0 0 0 0 0}
  set ::usr_f $f
  set ::frc_page1 {}
  set ::usr_armed 1
}

# ---- G3END ----------------------------------------------------------------------------
# lost: the work area was given to BASIC (its flags are not looked at)
proc st_off {tag first {lost 0}} {
  eq "$tag: V9968 ID 2 (V9958 mode)" [vid] 2
  eq "$tag: R#21 = 3Bh, R#20 = 0" "[h2 [vreg 21]] [h2 [vreg 20]]" "3B 00"
  eq "$tag: PORT#4 locked (last write 80h)" [h2 $::p4now] 80
  eq "$tag: MSX palette in the V9968" [pal_now] [pal15 $::pal_msx]
  eq "$tag: R#2 = 1Fh (page 0)" [h2 [vreg 2]] 1F
  if {!$lost} {
    eq "$tag: work area inactive, nothing left to undo" [expr {[peek [expr {[blk] + 5}]] & 3}] 0
  }
  eq "$tag: SLTWRK flag: nothing left to undo" [lindex [grpflags] 1] 0
  eq "$tag: H.TIMI hook still installed (inert)" [mem 0xFD9F 5] "F7[h2 $::slot]2940C9"
  if {$first} {
    set l {}
    foreach w $::rw {
      lassign $w r v p
      if {$r == 20 || $r == 21} { lappend l "R#$r=[h2 $v]/[h2 $p]" }
    }
    eq "$tag: R#20 then R#21 written with PORT#4 unlocked" [join $l " "] "R#20=00/00 R#21=3B/00"
    eq "$tag: PORT#4 writes: unlock, lock" [hexl $::p4] 0080
  } else {
    noaccess $tag
  }
  if {$::P == 0x98} {
    eq "$tag: RG20SAV, RG21SAV = 00h, 3Bh" [mem 0xFFF3 2] 003B
    eq "$tag: BAKCLR, BDRCLR $::bak_why ($::bak_before, $::bdr_before)" "[peek 0xF3EA] [peek 0xF3EB]" "$::bak_before $::bdr_before"
    eq "$tag: R#7 = BDRCLR (CHGCLR in SCREEN 5)" [vreg 7] [peek 0xF3EB]
    binary scan [debug read_block $::vram 0x7680 32] cu* t
    eq "$tag: BASIC's palette table (7680h) = MSX palette" [hexl $t] [hexl $::pal_msx]
    eq "$tag: RG2SAV = 1Fh, DPPAGE = 0" "[h2 [peek 0xF3E1]][h2 [peek 0xFAF5]]" 1F00
  }
}

# ---- boot and typing --------------------------------------------------------------------
if {$::tmode eq "gkey"} {
  keymatrixdown 3 0x10
  after time [expr {$::env(BOOT) - 3}] { keymatrixup 3 0x10 }
}
# an Enter first: MSX1 disk ROMs without a clock ask for the date at boot
# (twice: with G held, the first one may carry typed g's)
after time $::env(BOOT) { type "\r" }
after time [expr {$::env(BOOT) + 2}] { type "\r" }
after time [expr {$::env(BOOT) + 4}] {
  # CLEAR first: with G held, the g's typed at boot may have made a variable
  type "CLEAR:F=FRE(0):OUT &H2F,F-INT(F/256)*256:OUT &H2F,INT(F/256):OUT &H2E,1\r"
}
after time 450 {
  say "FAIL $::cfg: timeout at PC=[h4 [reg PC]] SP=[h4 [reg SP]] slots [get_selected_slot 0]/[get_selected_slot 1]/[get_selected_slot 2]/[get_selected_slot 3]"
  incr ::nfail
  say [get_screen]
  finish
}
