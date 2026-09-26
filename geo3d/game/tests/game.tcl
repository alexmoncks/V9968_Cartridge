# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
#
# game.tcl - openMSX test driver for the shooter ROM (run by run_tests.sh).
# Logs to $OUT (one line per event, emulated time in seconds):
#   F n t objs fbytes enem mode s_st0 s_st1 s_st2 v_st t_st phase
#                  page flip n (the ISR's write of st_flips)
#   M n from to file     the first page of a new mode, captured
#   K v t          frame progress marker (st_fmark: 30 start, 31 layers done,
#                  32 geo3d and text done, 33 flip asked), with FMARK=1
#   S t k=v ...    status every second
#   C n tick mode file   capture of the page on show at flip n
#   E name PASS|FAIL detail   scenario checks
#   T k ...        cross scenario trials
#   END t          the end (LIMIT emulated seconds, or the scenario's end)
# Env: PORT (152 or 136), OUT, CAPDIR, LIMIT, SCEN (smoke, play, collide,
# attract, rounds, cross, demokey), CAP ("a:b:n,..." flips to capture),
# FMARK, LABELS (a Tcl file with the RAM addresses: set A(name) addr).
set renderer none
set throttle off
set mute on

source $::env(LABELS)
set ::P $::env(PORT)
set ::scen $::env(SCEN)
set ::capdir $::env(CAPDIR)
set ::log [open $::env(OUT) w]
fconfigure $::log -buffering line
if {$::P == 0x98} {
  set ::vdp VDP
  set ::vram "physical VRAM"
} else {
  set ::vdp V9968
  set ::vram "physical V9968 VRAM"
}
set ::caps {}
if {[info exists ::env(CAP)] && $::env(CAP) ne ""} {
  foreach r [split $::env(CAP) ,] {
    lassign [split $r :] a b n
    lappend ::caps [list $a $b $n]
  }
}

proc t {} { format %.6f [machine_info time] }
proc rd {name} { debug read memory $::A($name) }
# rd16 name [offset]: the word at the label (plus offset)
proc rd16 {name {o 0}} {
  set a [expr {$::A($name) + $o}]
  expr {[debug read memory $a] | ([debug read memory [expr {$a + 1}]] << 8)}
}
proc rds16 {name} {
  set v [rd16 $name]
  if {$v >= 32768} { incr v -65536 }
  return $v
}
proc wr {name v} { debug write memory $::A($name) [expr {$v & 255}] }
proc bcd3 {name} {
  set a $::A($name)
  format %02X%02X%02X [debug read memory [expr {$a + 2}]] [debug read memory [expr {$a + 1}]] [debug read memory $a]
}
proc finish {} {
  puts $::log "END [t]"
  close $::log
  exit
}
after time $::env(LIMIT) {
  status
  finish
}

# ---------------------------------------------------------------- status
proc status {} {
  set s "S [t]"
  foreach n {st_mode st_phase st_round st_lives st_enem st_objs st_hwcoll st_probe st_geo} {
    append s " [string range $n 3 end]=[rd $n]"
  }
  foreach n {st_tick st_vbl st_flips st_late st_kills st_hits st_deaths st_cev st_nexev st_events st_fbytes st_work st_maxwork} {
    append s " [string range $n 3 end]=[rd16 $n]"
  }
  append s " score=[bcd3 st_score] hi=[bcd3 st_hi]"
  append s " cx=[rds16 st_cx] cy=[rds16 st_cy]"
  puts $::log $s
}
# PSG volumes (R#8-10) and mixer (R#7) every 50 ms: the sound effects play
proc psg_loop {} {
  set v "P [t]"
  foreach r {7 8 9 10} { append v " [debug read {PSG regs} $r]" }
  puts $::log $v
  after time 0.05 psg_loop
}
after time 1 psg_loop
proc status_loop {} {
  status
  after time 1 status_loop
}
after time 1 status_loop

# ---------------------------------------------------------------- captures
# the page on show (R#2), its sprite tables (R#5), patterns, palette
proc capture {tag} {
  set r2 [debug read "$::vdp regs" 2]
  set r5 [debug read "$::vdp regs" 5]
  set page [expr {($r2 >> 5) & 3}]
  set sat [expr {(($r5 & 0xF8) << 7) + 0x200}]
  set name [format "%s/%s.bin" $::capdir $tag]
  set f [open $name wb]
  fconfigure $f -translation binary
  puts -nonewline $f [debug read_block $::vram [expr {$page * 32768}] 27136]
  puts -nonewline $f [debug read_block $::vram [expr {$sat - 512}] 512]
  puts -nonewline $f [debug read_block $::vram $sat 128]
  puts -nonewline $f [debug read_block $::vram 0x7800 2048]
  puts -nonewline $f [debug read_block "$::vdp palette" 0 32]
  close $f
  return $name
}

set ::lastmode -1
proc on_flip {} {
  set n [rd16 st_flips]
  set m [rd st_mode]
  puts $::log "F $n [t] [rd st_objs] [rd16 st_fbytes] [rd st_enem] $m [rd16 s_st] [rd16 s_st 2] [rd16 s_st 4] [rd v_st] [rd t_st] [rd st_phase]"
  # the first page of a new mode (title, game, demo, game over)
  if {$m != $::lastmode && $::lastmode >= 1 && $m >= 1} {
    set name [capture [format "m%05d" $n]]
    puts $::log "M $n $::lastmode $m $name"
  }
  set ::lastmode $m
  foreach c $::caps {
    lassign $c a b k
    if {$n >= $a && $n < $b && (($n - $a) % $k) == 0} {
      set name [capture [format "f%05d" $n]]
      puts $::log "C $n [rd16 st_tick] [rd st_mode] $name [rd st_phase]"
    }
  }
}
debug set_watchpoint write_mem [expr {$::A(st_flips) + 1}] {} on_flip
# WATCH="name,...": log every write to these RAM variables (W name value pc t)
if {[info exists ::env(WATCH)] && $::env(WATCH) ne ""} {
  foreach n [split $::env(WATCH) ,] {
    debug set_watchpoint write_mem $::A($n) {} "puts \$::log \"W $n \$::wp_last_value \[format %04X \[reg pc\]\] \[t\]\""
  }
}
if {[info exists ::env(FMARK)] && $::env(FMARK) == 1} {
  debug set_watchpoint write_mem $::A(st_fmark) {} { puts $::log "K $::wp_last_value [t]" }
}

# PROFILE="t0 t1": sample the program counter every 97 us of emulated time
# between t0 and t1 (Z pc t lines; analyze.py profile maps them to routines)
proc prof_loop {t1} {
  puts $::log "Z [format %04X [reg pc]] [t]"
  if {[machine_info time] < $t1} { after time 0.000097 [list prof_loop $t1] }
}
if {[info exists ::env(PROFILE)] && $::env(PROFILE) ne ""} {
  lassign $::env(PROFILE) p0 p1
  after time $p0 [list prof_loop $p1]
}

# ---------------------------------------------------------------- input
# keyboard row 8: bit0 SPACE, 4 left, 5 up, 6 down, 7 right
proc press {mask secs} {
  keymatrixdown 8 $mask
  after time $secs "keymatrixup 8 $mask"
}
proc hold {mask} { keymatrixdown 8 $mask }
proc release {mask} { keymatrixup 8 $mask }

# wait until `cond` (a Tcl expression) holds, polling every 50 ms of
# emulated time, then run `body`; give up after `limit` seconds
proc wait_for {cond limit body {what ""}} {
  set ::wf_end [expr {[machine_info time] + $limit}]
  wait_poll $cond $body $what
}
proc wait_poll {cond body what} {
  if {[uplevel #0 [list expr $cond]]} {
    uplevel #0 $body
  } elseif {[machine_info time] > $::wf_end} {
    puts $::log "E wait FAIL timeout: $what ($cond)"
    status
    finish
  } else {
    after time 0.05 [list wait_poll $cond $body $what]
  }
}
proc booted {} { expr {[rd16 st_magic] == 0x3347} }

# ---------------------------------------------------------------- scenarios
proc scen_smoke {} {
  wait_for {[booted]} 10 {
    puts $::log "E boot PASS mode=[rd st_mode] hwcoll=[rd st_hwcoll] probe=[rd st_probe] geo=[rd st_geo] t=[t]"
    capture boot
    binary scan [debug read_block memory $::A(txt_rect) 48] cu* tr
    puts $::log "E txt_rect $tr"
    binary scan [debug read_block memory $::A(tx_buf) 24] cu* tb
    puts $::log "E tx_buf [rd tx_n] $tb"
  } "boot"
}

# the title, SPACE, then the intro and a scripted flight: fire held, moving
proc scen_play {} {
  wait_for {[booted] && [rd st_mode] == 1 && [rd16 st_tick] > 45} 20 {
    puts $::log "E title PASS t=[t]"
    capture title
    press 0x01 0.1
    wait_for {[rd st_mode] == 2} 2 {
      puts $::log "E start PASS t=[t]"
      after time 10.5 play_moves
      after time [expr {$::env(LIMIT) - [machine_info time] - 1.5}] play_esc
    } "game start"
  } "title"
}
# ESC (keyboard row 7, bit 2): back to the title
proc play_esc {} {
  set ::moves {0x00 5}
  keymatrixup 8 0xF1
  keymatrixdown 7 0x04
  after time 0.1 { keymatrixup 7 0x04 }
  wait_for {[rd st_mode] == 1} 1 { puts $::log "E esc_to_title PASS t=[t]" } "ESC"
}
set ::moves {0x21 0.8 0x01 0.5 0x41 1.2 0x01 0.4 0x81 0.6 0x21 0.7 0x11 0.5 0x41 0.9 0x01 0.6}
proc play_moves {} {
  set ::mi 0
  play_step
}
proc play_step {} {
  keymatrixup 8 0xF1
  set m [lindex $::moves [expr {$::mi % [llength $::moves]}]]
  set d [lindex $::moves [expr {($::mi + 1) % [llength $::moves]}]]
  incr ::mi 2
  keymatrixdown 8 $m
  after time $d play_step
}

# forced collisions: a rock in front of the ship (bolts hit and kill it),
# an enemy bullet into the ship (a life lost), a dart onto the ship (ram)
proc scen_collide {} {
  wait_for {[booted] && [rd st_mode] == 1} 20 {
    press 0x01 0.1
    wait_for {[rd st_phase] == 3 && [rd pl_vuln] == 1} 30 {
      puts $::log "E play PASS phase 3 at t=[t] tick=[rd16 st_tick]"
      coll_1
    } "phase 3"
  } "title"
}
proc ship_px {} { expr {[rds16 pl_x] >> 4} }
proc ship_py {} { expr {[rds16 pl_y] >> 4} }
proc coll_1 {} {
  wr st_dbg 5
  after time 0.1 coll_1b
}
proc coll_1b {} {
  set ::k0 [rd16 st_kills]
  set ::h0 [rd16 st_hits]
  set ::e0 [rd16 st_events]
  set ::c0 [rd16 st_cev]
  set ::lv0 [rd st_lives]
  set x [expr {([ship_px] + 72) / 2}]
  wr st_dbgx $x
  wr st_dbgy [ship_py]
  wr st_dbga 2
  wr st_dbg 1
  hold 0x01
  puts $::log "E rock spawned at x=[expr {$x * 2}] y=[ship_py] score=[bcd3 st_score] t=[t]"
  wait_for {[rd16 st_kills] > $::k0 && [rd16 st_hits] >= $::h0 + 4} 5 {
    release 0x01
    puts $::log "E shot_hits_enemy PASS hits=[expr {[rd16 st_hits] - $::h0}] kills=[expr {[rd16 st_kills] - $::k0}] events=[expr {[rd16 st_events] - $::e0}] cflag_ticks=[expr {[rd16 st_cev] - $::c0}] t=[t]"
    after time 0.1 {
      if {[rd st_lives] == $::lv0 + 1} { set r PASS } else { set r FAIL }
      puts $::log "E extra_life $r score=[bcd3 st_score] lives=$::lv0->[rd st_lives]"
    }
    capture coll1
    after time 0.3 coll_2
  } "rock killed by bolts"
}
proc coll_2 {} {
  set ::l0 [rd st_lives]
  set ::d0 [rd16 st_deaths]
  set ::c0 [rd16 st_cev]
  wr st_dbg 3
  after time 0.1 {
    wr st_dbgx [expr {([ship_px] + 40) / 2}]
    wr st_dbgy [ship_py]
    wr st_dbg 2
    puts $::log "E bullet fired at the ship t=[t]"
    wait_for {[rd16 st_deaths] > $::d0} 3 {
      puts $::log "E bullet_hits_ship PASS lives=$::l0->[rd st_lives] cflag_ticks=[expr {[rd16 st_cev] - $::c0}] t=[t]"
      capture coll2
      wait_for {[rd pl_dead] == 0 && [rd pl_in] == 0} 6 { coll_3 } "respawn"
    } "bullet hits the ship"
  }
}
proc coll_3 {} {
  set ::d0 [rd16 st_deaths]
  wr st_dbg 3
  after time 0.1 {
    wr st_dbgx [expr {([ship_px] + 20) / 2}]
    wr st_dbgy [ship_py]
    wr st_dbga 0
    wr st_dbg 1
    wait_for {[rd16 st_deaths] > $::d0} 3 {
      puts $::log "E ram PASS deaths=[rd16 st_deaths] lives=[rd st_lives] t=[t]"
      wait_for {[rd pl_dead] == 0 && [rd pl_in] == 0} 6 { coll_5 } "respawn"
    } "ram"
  }
}
# the score limits: the next extra life at 970000 with the score at 999950
# (one life, then no more), and a kill past 999999 (the score stops there)
proc wr3 {name b0 b1 b2} {
  set a $::A($name)
  debug write memory $a $b0
  debug write memory [expr {$a + 1}] $b1
  debug write memory [expr {$a + 2}] $b2
}
proc coll_5 {} {
  wr3 st_score 0x50 0x99 0x99
  wr3 next_extra 0x00 0x00 0x97
  set ::lv0 [rd st_lives]
  after time 0.5 {
    set ne [debug read memory [expr {$::A(next_extra) + 2}]]
    set lv [rd st_lives]
    if {$ne == 0xFF && $lv == $::lv0 + 1} { set r PASS } else { set r FAIL }
    puts $::log "E extra_life_limit $r next_extra=[bcd3 next_extra] lives=$::lv0->$lv"
    set ::k0 [rd16 st_kills]
    wr st_dbgx [expr {([ship_px] + 72) / 2}]
    wr st_dbgy [ship_py]
    wr st_dbga 2
    wr st_dbg 1
    hold 0x01
    wait_for {[rd16 st_kills] > $::k0} 6 {
      release 0x01
      if {[bcd3 st_score] eq "999999" && [rd st_lives] == $::lv0 + 1} { set r PASS } else { set r FAIL }
      puts $::log "E score_limit $r score=[bcd3 st_score] lives=[rd st_lives]"
      after time 0.3 coll_4
    } "rock past 999999"
  }
}
# the last life: game over (with a WARNING text on show when the ship blows
# up: GAME OVER alone, the level script stops), then the title
proc coll_4 {} {
  set ::d0 [rd16 st_deaths]
  wr st_dbga 1
  wr st_dbg 4
  after time 0.1 { wr st_dbg 3 }
  after time 0.2 {
    wr st_dbgx [expr {([ship_px] + 40) / 2}]
    wr st_dbgy [ship_py]
    wr txt_msg 5
    wr txt_t 200
    wr st_dbg 2
    wait_for {[rd st_mode] == 4} 5 {
      puts $::log "E game_over PASS lives=[rd st_lives] deaths=[rd16 st_deaths] t=[t]"
      set ::pc0 [rd16 sc_pc]
      set ::sw0 [rd sc_wait]
      after time 1.0 {
        if {[rd st_mode] == 4 && [rd txt_t] == 0 && [rd16 sc_pc] == $::pc0} { set r PASS } else { set r FAIL }
        puts $::log "E over_clean $r txt_t=[rd txt_t] sc_pc=[format %04X $::pc0]->[format %04X [rd16 sc_pc]]"
      }
      capture gameover
      wait_for {[rd st_mode] == 1} 9 {
        puts $::log "E back_to_title PASS score=[bcd3 st_score] hi=[bcd3 st_hi] t=[t]"
        status
        finish
      } "title after the game over"
    } "game over"
  }
}

# soak: a game flown by the autopilot with the ship unhittable (debug
# commands 6 and 7), through whole rounds (the gunship, ROUND 2, faster
# waves) for LIMIT seconds; the tile pages are dumped at the end
proc scen_rounds {} {
  wait_for {[booted] && [rd st_mode] == 1} 20 {
    press 0x01 0.1
    wait_for {[rd st_mode] == 2} 2 {
      wr st_dbg 6
      after time 0.1 { wr st_dbg 7 }
      puts $::log "E soak started t=[t]"
      rounds_watch
    } "game start"
  } "title"
}
proc rounds_watch {} {
  if {[rd st_round] >= 2 && ![info exists ::r2]} {
    set ::r2 1
    puts $::log "E rounds PASS round 3 reached at t=[t] kills=[rd16 st_kills] score=[bcd3 st_score]"
  }
  after time 1 rounds_watch
}

# crossings: (1) enemy bullets through the still ship, steep and fast
# (every one must kill it; the hardware path tests the page that showed
# the overlap, not the positions 2 ticks later), (2) bolts against one
# enemy at a time, off the bolt line by up to the hitbox's half height, and
# fast darts (god mode, fire held; every one must be hit). The level script
# is frozen (no other sprites).
set ::btrials {
  {0 96 -30 0} {0 96 -33 0} {0 64 -31 0} {0 -96 30 0} {0 -96 33 0}
  {-96 0 0 42} {-64 64 -40 40} {-64 -64 40 40}
}
# {type dy vx}: dy = the enemy's centre from the bolt line, vx 1/16 px per tick (0: path LINE)
set ::etrials {
  {0 0 0} {0 -6 0} {0 6 0} {0 -8 0} {0 8 0} {1 0 0} {1 -7 0} {1 7 0} {2 -8 0} {2 8 0}
  {0 0 -88} {0 5 -120} {0 -5 -120} {3 0 0} {3 -10 0} {3 11 0}
}
proc freeze {} { wr sc_wait 200; after time 0.5 freeze }
proc scen_cross {} {
  set ::ti 0
  set ::serial 100
  set ::bdied 0
  set ::ehit 0
  wait_for {[booted] && [rd st_mode] == 1} 20 {
    press 0x01 0.1
    wait_for {[rd st_mode] == 2} 3 {
      wait_for {[rd st_phase] == 3 && [rd pl_vuln] == 1} 30 {
        freeze
        wr st_dbga 9
        wr st_dbg 4
        after time 0.3 cross_b
      } "phase 3"
    } "game start"
  } "title"
}
proc cross_b {} {
  if {$::ti >= [llength $::btrials]} {
    set n [llength $::btrials]
    puts $::log "E cross_bullets [expr {$::bdied == $n ? "PASS" : "FAIL"}] $::bdied of $n killed the ship hwcoll=[rd st_hwcoll]"
    set ::ti 0
    wr st_dbg 6
    after time 0.2 { hold 0x01; cross_e }
    return
  }
  wait_for {[rd pl_dead] == 0 && [rd pl_in] == 0} 8 {
    wr st_dbga 9
    wr st_dbg 4
    after time 0.1 { wr st_dbg 3 }
    after time 0.3 cross_bplace
  } "respawn"
}
proc cross_bplace {} {
  lassign [lindex $::btrials $::ti] vx vy dy0 dx0
  set x [expr {([ship_px] + $dx0) * 16}]
  set y [expr {([ship_py] + $dy0) * 16}]
  set b $::A(bullets)
  foreach {o v} [list 1 $x 2 [expr {$x >> 8}] 3 $y 4 [expr {$y >> 8}] 5 $vx 6 $vy 10 [incr ::serial]] {
    debug write memory [expr {$b + $o}] [expr {$v & 255}]
  }
  set ::d0 [rd16 st_deaths]
  set ::c0 [rd16 st_cev]
  debug write memory $b 1
  after time 1.5 {
    set died [expr {[rd16 st_deaths] > $::d0}]
    incr ::bdied $died
    puts $::log "T $::ti bullet [lindex $::btrials $::ti] died=$died flagged_ticks=[expr {[rd16 st_cev] - $::c0}]"
    incr ::ti
    after time 0.2 cross_b
  }
}
proc cross_e {} {
  if {$::ti >= [llength $::etrials]} {
    release 0x01
    set n [llength $::etrials]
    puts $::log "E cross_enemies [expr {$::ehit == $n ? "PASS" : "FAIL"}] $::ehit of $n hit hwcoll=[rd st_hwcoll]"
    status
    finish
    return
  }
  wait_for {[rd pl_dead] == 0 && [rd pl_in] == 0} 8 {
    lassign [lindex $::etrials $::ti] type dy vx
    set ::h0 [rd16 st_hits]
    wr st_dbgx 125
    wr st_dbgy [expr {[ship_py] + 1 + $dy}]
    wr st_dbga $type
    wr st_dbg 1
    if {$vx != 0} { after time 0.05 [list cross_vx $vx] }
    after time 5.0 cross_eend
  } "respawn"
}
proc cross_vx {vx} {
  for {set k 0} {$k < 11} {incr k} {
    set o [expr {$::A(enem) + 48 * $k}]
    if {[debug read memory $o] != 255} {
      debug write memory [expr {$o + 5}] [expr {$vx & 255}]
      debug write memory [expr {$o + 6}] [expr {($vx >> 8) & 255}]
    }
  }
}
proc cross_eend {} {
  set h [expr {[rd16 st_hits] - $::h0}]
  if {$h > 0} { incr ::ehit }
  puts $::log "T $::ti enemy [lindex $::etrials $::ti] hits=$h"
  incr ::ti
  for {set k 0} {$k < 11} {incr k} { debug write memory [expr {$::A(enem) + 48 * $k}] 255 }
  after time 0.5 cross_e
}

# the attract demo: SPACE starts a game, then ESC goes back to the title
proc scen_demokey {} {
  wait_for {[booted] && [rd st_mode] == 3 && [rd st_phase] == 3} 60 {
    press 0x01 0.1
    wait_for {[rd st_mode] == 2} 1 {
      puts $::log "E demo_start PASS t=[t]"
      after time 2 {
        keymatrixdown 7 0x04
        after time 0.1 { keymatrixup 7 0x04 }
        wait_for {[rd st_mode] == 1} 1 {
          puts $::log "E esc_to_title PASS t=[t]"
          after time 1 finish
        } "ESC"
      }
    } "SPACE in the demo starts a game"
  } "the demo"
}

# no input: title, attract demo, title, ... for LIMIT seconds; at the end
# the tile pages are dumped for the comparison with the source tiles
proc scen_attract {} {
  wait_for {[booted]} 20 { puts $::log "E boot PASS t=[t]" } "boot"
}
proc dump_pages {} {
  set f [open $::capdir/vram_2_7.bin wb]
  fconfigure $f -translation binary
  puts -nonewline $f [debug read_block $::vram 65536 [expr {6 * 32768}]]
  close $f
}

switch $::scen {
  smoke   { scen_smoke }
  play    { scen_play }
  collide { scen_collide }
  cross   { scen_cross }
  demokey { scen_demokey }
  attract {
    scen_attract
    after time [expr {$::env(LIMIT) - 0.5}] dump_pages
  }
  rounds {
    scen_rounds
    after time [expr {$::env(LIMIT) - 0.5}] dump_pages
  }
}
