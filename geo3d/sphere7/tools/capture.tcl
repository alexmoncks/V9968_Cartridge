# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
#
# capture.tcl - openMSX driver for the SCREEN 7 sphere demo (run by
# tools/run_openmsx.sh). At every page flip (the ROM's write of nflip's high byte, right
# after R#2 and the palette, inside the vertical blank) it appends to
# $OUT/pages.bin the page on show (212 lines x 256 bytes, from the logical
# "VRAM" debuggable) and its 16 palette entries (32 bytes, the fork's 15-bit
# words), and logs one line to $OUT/log.txt:
#   F n t vbl late page tex px py rot kx ky geo ce
# (t emulated seconds; geo = geo3d's status port, ce = S#2 bit 0, both read
# without side effects at the flip: the frame on show must be finished).
# Env: OUT, LABELS (Tcl: set A(name) addr), LIMIT (emulated s), PORT (152 /
# 136), SPACE ("t1 t2 ..." emulated seconds to press SPACE), ESC (same),
# SKIP (flips not captured to pages.bin: every SKIP-th one is), SHOT (a
# screenshot file, needs a real renderer).
set renderer none
set throttle off
set mute on

source $::env(LABELS)
set ::P $::env(PORT)
if {$::P == 152} {
  set ::vdp VDP
  set ::vram VRAM
} else {
  set ::vdp V9968
  set ::vram "V9968 VRAM"
}
set ::log [open $::env(OUT)/log.txt w]
fconfigure $::log -buffering line
set ::pg [open $::env(OUT)/pages.bin wb]
fconfigure $::pg -translation binary
set ::skip 1
if {[info exists ::env(SKIP)] && $::env(SKIP) ne ""} { set ::skip $::env(SKIP) }

proc t {} { format %.6f [machine_info time] }
proc rd {name} { debug read memory $::A($name) }
proc rd16 {name} { expr {[debug read memory $::A($name)] | ([debug read memory [expr {$::A($name) + 1}]] << 8)} }
proc rds16 {name} { set v [rd16 $name]; if {$v >= 32768} { incr v -65536 }; return $v }

proc on_flip {} {
  set n [rd16 nflip]
  set r2 [debug read "$::vdp regs" 2]
  set page [expr {($r2 >> 5) & 3}]
  set geo [debug read ioports [expr {$::P + 5}]]
  set ce [expr {[debug read "$::vdp status regs" 2] & 1}]
  puts $::log "F $n [t] [rd16 vbl] [rd16 late] $page [rd tex] [rd16 px] [rd16 py] [rd rot] [rd kx] [rd ky] $geo $ce"
  if {($n % $::skip) == 0} {
    puts -nonewline $::pg [debug read_block $::vram [expr {$page * 65536}] 54272]
    puts -nonewline $::pg [debug read_block "$::vdp palette" 0 32]
  }
}
debug set_watchpoint write_mem [expr {$::A(nflip) + 1}] {} on_flip

proc press {row mask t} {
  after time $t "keymatrixdown $row $mask"
  after time [expr {$t + 0.12}] "keymatrixup $row $mask"
}
if {[info exists ::env(SPACE)]} { foreach x $::env(SPACE) { press 8 1 $x } }
if {[info exists ::env(ESC)]} { foreach x $::env(ESC) { press 7 4 $x } }

after time $::env(LIMIT) {
  puts $::log "END [t] flips [rd16 nflip] vbl [rd16 vbl] late [rd16 late] bounces [rd16 bounces] fail [rd fail]"
  close $::log
  close $::pg
  exit
}
