# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
#
# tests_cmds.tcl - check points of the "cmds" run (sourced by tests.tcl):
# disk/G3CMD.BAS (G3OBJ, G3POS, G3ROT, G3SPIN, G3STYLE, G3CAM, G3RAMP,
# G3PAL, G3DATA, G3FRAME), G3MEM.BAS (the model area full, compaction) and
# G3PERF.BAS (a 255-face torus: two edge passes, speed).
#
# Checked here: errors, the object records, the camera and palette values,
# the page flips, the geo3d traffic of a frame. Dumped for check_frames.py
# (the Python reference of the ROM's math and of geo3d's drawing):
#   out/t_<cfg>_cmds_<n>.st   work area (2048 bytes) + model area (32 KB)
#   out/t_<cfg>_cmds_<n>.pg   lines 0-211 of the page the last G3FRAME drew
#   out/t_<cfg>_cmds_<n>.geo  every geo3d port write since the scene began
#                             ("W sel byte"; "F" where a G3FRAME starts to
#                             draw)
#   out/t_<cfg>_cmds_times    frame rates
set ::dbase [file rootname $::env(OUT)]
set ::graw {}
set ::glog 0
debug set_watchpoint write_io [expr {$::P + 5}] {} { if {$::glog} { lappend ::graw "W 0 $::wp_last_value" } }
debug set_watchpoint write_io [expr {$::P + 7}] {} { if {$::glog} { lappend ::graw "W 1 $::wp_last_value" } }
debug set_bp [L sortobj] {[ours]} { if {$::glog} { lappend ::graw "F" } }
set ::tg3data 0
debug set_bp [L g3data] {[ours]} { set ::tg3data [machine_info time] }
# page flips: the time of each (98h: flip98, from the H.TIMI hook or at once;
# 88h: the R#2 write of flip88)
set ::flips {}
set ::fliplog 0
debug set_bp [L [expr {$::P == 0x98 ? "flip98" : "f8_3"}]] {[ours]} {
  if {$::fliplog} { lappend ::flips [machine_info time] }
}
# pace v: the flips come at least v frames apart. A flip is timed where the
# ROM decides it (the hook, or flip88 seeing F), which interrupts and DI
# windows move by a few ms: the gaps are rounded to whole 60 Hz frames
# (16.688 ms, the V9968's NTSC frame in both profiles).
proc flipint {tag v} {
  set l $::flips
  set mn 1000
  set sum 0
  for {set i 1} {$i < [llength $l]} {incr i} {
    set fr [expr {int(round(([lindex $l $i] - [lindex $l [expr {$i - 1}]]) / 0.016688))}]
    incr sum $fr
    if {$fr < $mn} { set mn $fr }
  }
  set n [expr {[llength $l] - 1}]
  if {$n < 1} {
    check "$tag: page flips" 0 "[llength $l] flips seen"
    return
  }
  check "$tag: [llength $l] flips, each at least $v frames after the last (fewest $mn, mean [format %.2f [expr {double($sum) / $n}]])" \
    [expr {$mn >= $v}] "too early"
  set ::flips {}
}
# lines 212-255 of pages 0 and 1 (BASIC's tables on page 0 in 98h)
proc keep212 {} {
  return "[debug read_block $::vram 27136 5632][debug read_block $::vram [expr {32768 + 27136}] 5632]"
}

proc wbin {name data} {
  set f [open "$::dbase\_$name" wb]
  puts -nonewline $f $data
  close $f
}
proc dump_st {n} {
  wbin $n.st "[debug read_block memory [blk] 2048][debug read_block $::vram 229376 32768]"
}
proc wpeek {a} { expr {[peek $a] | ([peek [expr {$a + 1}]] << 8)} }
proc wpeeks {a} { set v [wpeek $a]; expr {$v >= 32768 ? $v - 65536 : $v} }
proc drawpage {} { peek [expr {[blk] + 15}] }
proc dump_pg {n} {
  set pg [drawpage]
  wbin $n.pg [debug read_block $::vram [expr {$pg * 32768}] 27136]
  set f [open "$::dbase\_$n.geo" w]
  puts $f "P $pg"
  foreach op $::graw { puts $f $op }
  close $f
}
# geo3d writes since the last check point, as (index, value) (::gstep)
proc gvals {idx} {
  set l {}
  foreach w $::gstep {
    lassign $w i v
    if {$i == $idx} { lappend l $v }
  }
  return $l
}
proc obj {n} { expr {[blk] + 128 + 48 * ($n - 1)} }
proc mdir {m} { expr {[blk] + 1152 + 16 * ($m - 16)} }
# degrees (integers and halves) to 65536 units per turn, as dacang rounds
proc units {deg} {
  set neg [expr {$deg < 0}]
  set a [expr {abs($deg)}]
  set r [expr {int($a) % 360}]
  set g [expr {int(round(($a - int($a)) * 65536))}]
  set u [expr {($r * 65536 + $g + 180) / 360}]
  if {$u >= 65536} { set u 0 }
  expr {$neg ? (65536 - $u) & 0xFFFF : $u}
}
proc objchk {tag n flags model style pos ang spin} {
  set o [obj $n]
  set got "[peek $o] [peek [expr {$o + 1}]] [peek [expr {$o + 2}]]"
  eq "$tag: object $n flags, model, style" $got "$flags $model $style"
  set p {}
  for {set i 0} {$i < 3} {incr i} { lappend p [wpeeks [expr {$o + 4 + 2 * $i}]] }
  eq "$tag: object $n position" $p $pos
  set a {}
  for {set i 0} {$i < 3} {incr i} { lappend a [wpeek [expr {$o + 10 + 2 * $i}]] }
  set e {}
  foreach x $ang { lappend e [units $x] }
  eq "$tag: object $n angles ($ang degrees)" $a $e
  set s {}
  for {set i 0} {$i < 3} {incr i} { lappend s [wpeek [expr {$o + 16 + 2 * $i}]] }
  set e {}
  foreach x $spin { lappend e [units $x] }
  eq "$tag: object $n spin ($spin degrees)" $s $e
}
proc errs {tag d exp} { eq "$tag: ERR values" $d $exp }
# a ramp as G3RAMP writes it: tone k = round(x (k + 1) / 7)
proc ramp {r g b} {
  set l {}
  for {set k 1} {$k <= 7} {incr k} {
    lappend l [expr {(((($r * $k + 3) / 7) << 4) | (($b * $k + 3) / 7))}] [expr {($g * $k + 3) / 7}]
  }
  return $l
}
proc palchk {tag first bytes} {
  set got [lrange [pal_now] $first [expr {$first + [llength $bytes] / 2 - 1}]]
  eq "$tag: palette $first..[expr {$first + [llength $bytes] / 2 - 1}]" $got [pal15 $bytes]
  if {$::P == 0x98} {
    binary scan [debug read_block $::vram [expr {0x7680 + 2 * $first}] [llength $bytes]] cu* t
    eq "$tag: BASIC's palette table (7680h)" [hexl $t] [hexl $bytes]
  }
}
proc r2 {} { h2 [vreg 2] }
proc flipchk {tag pg} {
  set v [format %02X [expr {$pg * 32 + 31}]]
  eq "$tag: R#2 = $v (page $pg shown)" [r2] $v
  if {$::P == 0x98} {
    eq "$tag: RG2SAV, DPPAGE, ACPAGE" "[h2 [peek 0xF3E1]] [peek 0xFAF5] [peek 0xFAF6]" "$v $pg $pg"
  }
  eq "$tag: no flip pending (W_FLIP 0)" [peek [expr {[blk] + 76}]] 0
}
set ::tperf 0

proc cmds_cp {n d} {
  switch -- $n {
    133 { errs "before G3INIT: G3OBJ POS ROT SPIN STYLE CAM RAMP DATA PAL FRAME" $d {5 5 5 5 5 5 5 5 5 5} }
    134 { err "G3INIT" $d 0 }
    135 { errs "G3OBJ(0|17|1,16 undefined|1,1 built-in|1,32|1|1,0,40000|1,0,\"A\"), G3POS/ROT/SPIN/STYLE of no object" $d {5 5 5 5 5 2 6 13 5 5 5 5} }
    136 {
      errs "G3OBJ(1,0); G3POS(1,1,2); G3ROT(1,\"X\"); G3STYLE(1,2|4|0,9|1); G3CAM(0,0|40000,0,0); G3RAMP(0|10|1,8|1,1,1); G3DATA(15|32|16,4|none); G3FRAME(256|1,2); G3ROT()X" \
        $d {0 2 13 5 5 5 2 2 6 5 5 5 2 5 5 5 2 5 2 2}
    }
    137 {
      errs "G3DATA: Out of DATA; Syntax error (ERL); vertex 8 of 8; nv 0; colour 16; 40000; t 2; UV 256; no faces nor edges" \
        $d {4 2 90 20 5 5 5 6 5 5 5}
      eq "G3DATA errors: model 16 undefined, model area empty" "[peek [mdir 16]] [wpeek [expr {[blk] + 922}]]" "0 0"
    }
    138 {
      errs "G3DATA(16), then READ X: the value after the model" $d {0 77}
      eq "G3DATA(16): directory: defined + edges from the faces, 8 v, 6 f, colour 1, 12 edges, offset 0, 256 bytes (138 used)" \
        [mem [mdir 16] 11] [hexl {5 8 6 1 12 0 0 0 0 1 0}]
      dump_st $n
    }
    139 { errs "G3DATA 17-21" $d {0 0 0 0 0}; dump_st $n }
    140 { errs "G3DATA(17) again (the models after it move down)" $d {0}; dump_st $n }
    169 {
      errs "G3DATA(22): coordinates up to 32768 (normals from them >> 3)" $d {0}
      eq "G3DATA(22): coordinate shift 3" [peek [expr {[mdir 22] + 10}]] 3
      dump_st $n
    }
    141 { objchk "G3OBJ(1,16,10,-20,30)" 1 1 16 1 {10 -20 30} {0 0 0} {0 0 0} }
    142 { objchk "G3POS(1,-5,6,-7,30,45,-60)" 1 1 16 1 {-5 6 -7} {30 45 -60} {0 0 0} }
    143 { objchk "G3ROT(1,22.5,-90,720.5)" 1 1 16 1 {-5 6 -7} {22.5 -90 720.5} {0 0 0} }
    144 { objchk "G3ROT(1,,10): empty positions kept" 1 1 16 1 {-5 6 -7} {22.5 10 720.5} {0 0 0} }
    145 { objchk "G3SPIN(1,1,-2,.5)" 1 1 16 1 {-5 6 -7} {22.5 10 720.5} {1 -2 0.5} }
    146 { objchk "G3STYLE(1,0)" 1 1 16 0 {-5 6 -7} {22.5 10 720.5} {1 -2 0.5} }
    147 {
      objchk "G3OBJ(2,19): a model without faces is wireframe" 2 1 19 0 {0 0 0} {0 0 0} {0 0 0}
      objchk "G3OBJ(3,0): pivot" 3 1 0 0 {0 0 0} {0 0 0} {0 0 0}
    }
    148 {
      eq "G3CAM(100,50,-400): camera stored" "[wpeeks [expr {[blk] + 24}]] [wpeeks [expr {[blk] + 26}]] [wpeeks [expr {[blk] + 28}]]" "100 50 -400"
      eq "G3CAM(100,50,-400): not the identity" [peek [expr {[blk] + 1013}]] 0
      dump_st $n
    }
    149 {
      set l {}
      for {set i 0} {$i < 9} {incr i} { lappend l [wpeeks [expr {[blk] + 896 + 2 * $i}]] }
      eq "G3CAM(0,0,-300): identity matrix" $l {16384 0 0 0 16384 0 0 0 16384}
      eq "G3CAM(0,0,-300): identity flag" [peek [expr {[blk] + 1013}]] 1
      eq "G3CAM(0,0,-300): light (-1,1,-1) in camera space" "[wpeeks [expr {[blk] + 914}]] [wpeeks [expr {[blk] + 916}]] [wpeeks [expr {[blk] + 918}]]" "-9459 9459 -9459"
    }
    150 {
      palchk "G3RAMP(2,7,5,1)" 2 [ramp 7 5 1]
      eq "G3RAMP(2,...): ramp starts: 2 (1 and 8 overlapped it)" [format %04X [wpeek [expr {[blk] + 920}]]] 0004
      dump_st $n
    }
    151 {
      palchk "G3RAMP(9,1,5,7)" 9 [ramp 1 5 7]
      eq "G3RAMP(9,...): ramp starts 2, 9" [format %04X [wpeek [expr {[blk] + 920}]]] 0204
      dump_st $n
    }
    152 {
      palchk "G3RAMP(1,2,3,7), G3RAMP(8,7,4,0): the default ramps again" 1 [lrange $::pal_g3 2 29]
      eq "ramp starts 1, 8" [format %04X [wpeek [expr {[blk] + 920}]]] 0102
      dump_st $n
    }
    172 { errs "G3PAL(16,0,0,0|1,8,0,0|1,1,1|-1,0,0,0)" $d {5 5 2 5} }
    170 {
      palchk "G3PAL(15,5,4,2)" 15 {0x52 0x04}
      eq "G3PAL: ramp starts still 1, 8" [format %04X [wpeek [expr {[blk] + 920}]]] 0102
    }
    171 {
      palchk "G3PAL(15,7,7,7)" 15 {0x77 0x07}
      palchk "G3PAL(0,0,0,0)" 0 {0x00 0x00}
    }
    153 { set ::graw {}; set ::glog 1; set ::k212 [keep212] }
    154 {
      err "G3FRAME (cube, solid)" $d 0
      eq "G3FRAME: RUN with faces (48h = 03h), once" [gvals 0x48] 3
      eq "G3FRAME: YPAGE = 256 (page 1 drawn)" [gvals 0x47] 1
      eq "G3FRAME: geo3d idle at the return" [expr {[geostat] & 0x0F}] 0
      if {$::P == 0x88} { flipchk "88h G3FRAME: flipped before the return" 1 }
      dump_pg $n
    }
    155 { flipchk "G3FRAME, a few blanks later" 1 }
    156 {
      err "G3FRAME (cube, wireframe)" $d 0
      eq "wireframe: RUN with edges (48h = 01h), NEDGE 12" "[gvals 0x48] [gvals 0x43]" "1 12"
      dump_pg $n
    }
    157 { flipchk "wireframe frame shown" 0 }
    158 {
      eq "two G3FRAMEs of a resident model: no vertex, face or edge upload" \
        "[llength [gvals 0x50]] [llength [gvals 0x51]] [llength [gvals 0x52]]" "0 0 0"
      eq "two G3FRAMEs: two RUNs" [gvals 0x48] {3 3}
      dump_pg $n
    }
    159 {
      set o [obj 1]
      eq "G3SPIN(1,0,10,0), two frames: angles (30, 45 + 2 x 10, 0), flags 5 (rotation cached)"         "[peek $o] [wpeek [expr {$o + 10}]] [wpeek [expr {$o + 12}]] [wpeek [expr {$o + 14}]]"         "5 [units 30] [expr {[units 45] + 2 * [units 10]}] 0"
    }
    173 {
      eq "G3RAMP(9,7,0,0) (colour 8 no ramp start now), G3FRAME: no vertex upload, the cube's 6 faces sent again (66 bytes)"         "[llength [gvals 0x50]] [llength [gvals 0x52]]" "0 66"
      dump_pg $n
    }
    174 {
      eq "the default ramps again, G3FRAME: the faces sent again (66 bytes)" [llength [gvals 0x52]] 66
      dump_pg $n
    }
    175 {
      errs "98h: G3FRAME(60), SCREEN 0, G3DATA in text mode" $d {5}
      eq "98h: the flip pending at SCREEN 0 is dropped (W_FLIP 0)" [peek [expr {[blk] + 76}]] 0
    }
    176 { flipchk "98h: SCREEN 5 again, 70 blanks later: no late flip" 0 }
    177 { err "G3DATA(23,0): two squares, corners in the order given" $d 0; dump_pg $n }
    178 { dump_pg $n }
    160 { err "G3FRAME: two objects, a camera" $d 0; eq "two objects: two RUNs" [gvals 0x48] {3 3}; dump_pg $n }
    161 { dump_pg $n }
    162 { dump_pg $n }
    163 { dump_pg $n }
    164 {
      err "G3FRAME(0)" $d 0
      flipchk "G3FRAME(0): shown at once" [drawpage]
      eq "G3FRAME(0): pace 0 kept" [peek [expr {[blk] + 13}]] 0
      dump_pg $n
    }
    165 {
      eq "G3FRAME(3): pace 3 kept" [peek [expr {[blk] + 13}]] 3
      set k [keep212]
      check "12 G3FRAMEs: lines 212-255 of pages 0 and 1 untouched" [string equal $k $::k212] [vdiff 27136 $k $::k212]
    }
    166 { eq "98h: SET PAGE 1,1, G3FRAME: drawn on page 0 (YPAGE 0)" "[gvals 0x47] [drawpage]" "0 0" }
    167 { errs "98h: G3FRAME in SCREEN 0" $d {5} }
    168 {
      err "G3END" $d 0
      set ::glog 0
      check "the cmds program ends normally" [string match "*CMDS OK*" [get_screen]] "not on screen"
    }
    200 { err "G3MEM.BAS: G3INIT" $d 0 }
    201 { errs "three models of 8448 bytes" $d {0 0 0}; dump_st $n }
    202 {
      errs "a fourth: Out of memory while its edges are made" $d {7}
      eq "Out of memory: model 19 undefined, 25344 bytes used" "[peek [mdir 19]] [wpeek [expr {[blk] + 922}]]" "0 25344"
      set z [string repeat "\0" 8192]
      set v [debug read_block $::vram [expr {[drawpage] * 32768}] 8192]
      check "Out of memory: the edge bitmap (lines 0-63 of page [drawpage]) cleared" [string equal $v $z] [vdiff 0 $v $z]
    }
    203 { errs "a 2 KB model (255 vertices, 255 edges)" $d {0}; eq "27392 bytes used" [wpeek [expr {[blk] + 922}]] 27392; dump_st $n }
    204 {
      errs "a header that does not fit: Out of memory" $d {7}
      eq "model 20 undefined, 27392 bytes used" "[peek [mdir 20]] [wpeek [expr {[blk] + 922}]]" "0 27392"
    }
    205 { errs "model 16 redefined: 18944 bytes move down" $d {0}; eq "20992 bytes used" [wpeek [expr {[blk] + 922}]] 20992; dump_st $n }
    206 { err "G3MEM.BAS: G3END" $d 0 }
    210 {
      set dt [expr {[machine_info time] - $::tg3data}]
      set cpu [expr {$::cfg eq "turbor" ? "R800" : "Z80 3.58 MHz"}]
      say "  info $::cfg: G3DATA of the 255-vertex, 255-face torus: [format %.2f $dt] s ($cpu)"
      set f [open "$::dbase\_times" w]
      puts $f "g3data_seconds [format %.2f $dt] $cpu"
      close $f
      dump_st $n
    }
    211 { set ::graw {}; set ::glog 1 }
    212 {
      eq "torus wireframe: 510 edges, two RUNs (NEDGE 255, 255)" "[gvals 0x48] [gvals 0x43]" "1 1 255 255"
      eq "torus wireframe: both passes uploaded" [expr {[llength [gvals 0x51]] == 1020}] 1
      dump_pg $n
    }
    213 {
      eq "torus wireframe again: one pass uploaded (the one geo3d held is drawn first)" [expr {[llength [gvals 0x51]] == 510}] 1
      set first [lsearch -exact [lmap w $::gstep {expr {[lindex $w 0] == 0x51}}] 1]
      set run [lsearch -exact [lmap w $::gstep {expr {[lindex $w 0] == 0x48}}] 1]
      check "torus wireframe again: the first RUN comes before any edge upload" [expr {$run >= 0 && ($first < 0 || $run < $first)}] "first RUN at $run, first edge byte at $first"
      dump_pg $n
    }
    214 { eq "torus solid: one RUN with faces" [gvals 0x48] 3; set ::glog 0; dump_pg $n }
    215 { set ::tperf [machine_info time]; set ::flips {}; set ::fliplog 1 }
    219 { set ::tperf [machine_info time]; set ::flips {} }
    220 { set ::flips {}; set ::fliplog 1 }
    221 { flipint "an empty scene, G3FRAME(2)" 2 }
    222 { flipint "an empty scene, G3FRAME(3)" 3 }
    223 { flipint "an empty scene, G3FRAME(1)" 1; set ::fliplog 0 }
    216 - 217 - 218 {
      set t [machine_info time]
      set fps [expr {60.0 / ($t - $::tperf)}]
      set ::tperf $t
      set what [dict get {216 "G3ROT + G3FRAME(1)" 217 "STICK, G3ROT + G3FRAME (the pace 1 kept)" 218 "turbo R in Z80 mode, G3ROT + G3FRAME(1)"} $n]
      set cpu [expr {$::cfg eq "turbor" && $n != 218 ? "R800" : "Z80 3.58 MHz"}]
      say "  info $::cfg: torus (255 faces, solid, spinning), $what: [format %.1f $fps] frames per second ($cpu)"
      set f [open "$::dbase\_times" a]
      puts $f "fps_$n [format %.2f $fps] $cpu"
      close $f
      if {$cpu ne "R800" && $n != 217} {
        check "$what on a $cpu: 10 or more frames per second ([format %.1f $fps])" [expr {$fps >= 10.0}] "too slow"
      }
      flipint "torus, $what" 1
    }
    default { return 0 }
  }
  return 1
}
