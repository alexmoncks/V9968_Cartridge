# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
#
# openMSX script for the name test ROM (g3names.rom). After BOOT seconds it
# types RUN"G3TEST.BAS" (disk A), logs everything the ROM prints (breakpoint
# on putc at 4010h) and PROCNM at each handler entry (4013h), and when the
# program reaches CALL GDONE it dumps the tokenized program and exits.
# Env: OUT (log file), BOOT (seconds before typing).
set renderer none
set throttle off
set mute on
set ::log [open $::env(OUT) w]
fconfigure $::log -translation binary

proc in_rom {} {
  # page 1 holds g3names.rom (JP CHPUT at 4010h), not the BASIC ROM
  expr {[peek 0x4010] == 0xC3 && [peek16 0x4011] == 0x00A2}
}
proc hexs {bytes} {
  set s ""
  binary scan $bytes cu* l
  foreach b $l { append s [format " %02X" $b] }
  return $s
}
proc on_putc {} {
  set a [reg A]
  if {$a != 13} { puts -nonewline $::log [format %c $a] }
}
proc on_stmt {} {
  set name [debug read_block memory 0xFD89 16]
  set z [string first "\0" $name]
  if {$z >= 0} { set name [string range $name 0 $z] }
  puts -nonewline $::log "PROCNM[hexs $name]  "
  if {[string match "GDONE*" $name]} {
    after time 1 dump_program
  }
}
proc dump_program {} {
  puts $::log "\n--- tokenized program (bytes >= 80h are tokens):"
  set p [peek16 0xF676]
  while {[set link [peek16 $p]] != 0} {
    set line [peek16 [expr {$p + 2}]]
    set q [expr {$p + 4}]
    set txt ""
    while {[set b [peek $q]] != 0} {
      if {$b >= 0x20 && $b < 0x7F} { append txt [format %c $b] } else { append txt [format "{%02X}" $b] }
      incr q
    }
    puts $::log "$line $txt"
    set p $link
  }
  close $::log
  exit
}
debug set_bp 0x4010 {[in_rom]} on_putc
debug set_bp 0x4013 {[in_rom]} on_stmt
# an Enter first: MSX1 disk ROMs without a clock ask for the date at boot
after time $::env(BOOT) { type "\r" }
after time [expr {$::env(BOOT) + 5}] { type "RUN\"G3TEST.BAS\"\r" }
after time 300 {
  puts $::log "\nTIMEOUT at PC=[format %04X [reg PC]] SP=[format %04X [reg SP]]"
  set sp [reg SP]
  set st ""
  for {set i 0} {$i < 24} {incr i} { append st [format " %04X" [peek16 [expr {$sp + 2 * $i}]]] }
  puts $::log "stack:$st\nslots: [get_selected_slot 0] [get_selected_slot 1] [get_selected_slot 2] [get_selected_slot 3]"
  puts $::log [get_screen]
  dump_program
}
