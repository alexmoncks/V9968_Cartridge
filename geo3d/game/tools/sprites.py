#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Sprites of the shooter (sprite mode 2, 16 x 16, SCREEN 5): patterns, the
per-line colour tables with their IC bits, the plane map, the hitbox
rectangles the collision resolver uses, and the 8 x 8 font of the HUD.

Collision, as the V9968 FPGA detects it (vdp_sprite_makeup_pixel.v, the
ff_sprite_collision block): a sprite pixel collides when a sprite of a
LOWER plane number has already put a visible pixel there (colour != 0, or
TP = 1) and the later sprite has IC = 0 and CC = 0. The later sprite's
own colour does not matter, so an invisible sprite (colour 0) collides
with a visible sprite in front of it, but two invisible sprites never do.
The plane order below is built on that:

  plane   0     guard            colour 0  IC=1  (blank pattern on the player's
                                                  lines, see below)
  planes  1-6   enemy bullets    visible   IC=1  (always the front member)
  plane   7     player core      visible   IC=0  (cockpit light: rams, bullets)
  plane   8     player hitbox    colour 0  IC=0  (hit by enemy bullets; a
                                                  hole under the core)
  planes  9-12  player bolts     visible   IC=1  (front member only)
  planes 13-23  enemy hitboxes   colour 0  IC=0  (hit by bolts, ram the core)
  planes 24-27  flashes, text    visible   IC=1  (never collide)
  planes 28-31  HUD              visible   IC=1  (never collide)

The ship's hitbox comes before the bolts and the enemy hitboxes: a line
shows 16 sprites at most (in plane order), and the ship's lines are the
busiest (guard, core, hitbox, the bolts it fires, the bullets aimed at it).
A colour-0 sprite never makes another one collide, and the bolts have
IC=1, so the order adds no event. The game also takes S#0 bit 6 (a line
had more than 16 sprites) as a reason to run the box test (game.asm).

The guard: in vdp_sprite_makeup_pixel.v the collision check also runs at
w_sub_phase 3, where the first plane of pixel x meets the mixed state of
pixel x - 1 (only sub_phase 1 is excluded). If that reading is right, a
visible IC=0 sprite that is the first sprite on its line collides with its
own left neighbour dot every frame, and that event would hide every other
one below it (one event per frame). The guard is a blank sprite (no dots,
IC=1) at the core's position, so the core is never the first sprite on its
lines. It costs one plane; drop it if the hardware shows no such event.
level.Game.collide(quirk=True) models the suspected behaviour.

Events the hardware reports (S#0 bit 5, then S#3-S#6: X + 12, Y + 8 of
the first colliding pixel, top line first): bolt over enemy hitbox (enemy
hit), enemy bullet over the core or the player hitbox (player hit), core
over an enemy hitbox (ram), and the harmless enemy bullet over an enemy
hitbox (ignored). resolve() in level.py is the reference resolver. The
game does not use the point: a flagged frame gets the box test of every
pair, on the positions of the tick that frame showed (game.asm collide).

Enemy hitboxes are as tall as a sprite allows (16 rows; the gunship has
two stacked): the models are drawn about 16-24 pixels tall, and a bolt is
tested only against the box, so a short box lets bolts fly through the
wings. They are plain rectangles, so the sprite's pixels and the box the
Z80 tests are the same.

Needs 16 sprites per line (R#20 bit 7, S16; same bit in the FPGA and the
openMSX fork) and R#8 bit 5 (TP) = 0. Hidden planes use Y = 212 (D4h),
never 216 (D8h ends the sprite list). A sprite's Y attribute is its first
line minus 1.

Pattern origin: each pattern has an anchor, the pixel that sits on the
object's screen centre (sx, sy): X = sx - ax, Y attribute = sy - ay - 1.
"""
import common as C

# ------------------------------------------------------------------ art
# '#' pattern pixel. Colours per line are given separately.
ART = {
    "BLANK": ["." * 16] * 16,
    "P_BOLT": ["." * 16] * 6 + [
        "..############..",
        ".##############.",
        "..############..",
    ] + ["." * 16] * 7,
    "E_BULLET": ["." * 16] * 5 + [
        "......####......",
        ".....######.....",
        ".....######.....",
        ".....######.....",
        ".....######.....",
        "......####......",
    ] + ["." * 16] * 5,
    "E_BULLET2": ["." * 16] * 6 + [
        "......####......",
        ".....######.....",
        ".....######.....",
        "......####......",
    ] + ["." * 16] * 6,
    "P_CORE": ["." * 16] * 6 + [
        ".........####...",
        "........######..",
        "........######..",
        ".........####...",
    ] + ["." * 16] * 6,
    # player hitbox: x -8..7, y -3..2 around the centre, minus the core
    "P_HIT": ["." * 16] * 5 + [
        "################",
        "################",
        "################",
        "################",
        "################",
        "################",
    ] + ["." * 16] * 5,
    # enemy hitboxes (see ANCHOR for where they sit): dart x -8..7 y -8..7,
    # saucer x -11..4 y -7..7, rock x -8..7 y -8..7, gunship x -20..-5 in
    # two sprites, y -11..4 and 5..10
    "H_DART": ["################"] * 16,
    "H_SAUCER": ["." * 16] + ["################"] * 15,
    "H_ROCK": ["################"] * 16,
    "H_GUN_T": ["################"] * 16,
    "H_GUN_B": ["." * 16] * 5 + ["################"] * 6 + ["." * 16] * 5,
    "FLASH0": ["." * 16] * 4 + [
        "........#.......",
        "........#.......",
        ".....#..#..#....",
        "......#####.....",
        "...#########....",
        "......#####.....",
        ".....#..#..#....",
        "........#.......",
        "........#.......",
    ] + ["." * 16] * 3,
    "FLASH1": [
        "................",
        ".......#........",
        "..#....#....#...",
        "...#...#...#....",
        "....#.###.#.....",
        ".....#####......",
        "...#########....",
        ".#####...#####..",
        "...#########....",
        ".....#####......",
        "....#.###.#.....",
        "...#...#...#....",
        "..#....#....#...",
        ".......#........",
        "................",
        "................",
    ],
    "FLASH2": [
        ".#......#.....#.",
        "................",
        "...#...#...#....",
        "#...#.....#....#",
        "................",
        "..#.....#....#..",
        "................",
        "#..#.........#..",
        "................",
        "..#....#....#..#",
        "................",
        "#...#.....#.....",
        "...#...#...#....",
        "................",
        ".#......#.....#.",
        "................",
    ],
}

# pattern numbers (sprite mode 2, 16 x 16: number = 4 * index)
ORDER = ["BLANK", "P_BOLT", "E_BULLET", "E_BULLET2", "P_CORE", "P_HIT", "H_DART", "H_SAUCER",
         "H_ROCK", "H_GUN_T", "H_GUN_B", "FLASH0", "FLASH1", "FLASH2"]
HUD_FIRST = 48            # patterns 48-63: composed at run time (HUD, messages)

# anchors: pattern pixel on the object's centre
ANCHOR = {
    "BLANK": (8, 8), "P_BOLT": (8, 7), "E_BULLET": (8, 8), "E_BULLET2": (8, 8),
    "P_CORE": (8, 8), "P_HIT": (8, 8),
    "H_DART": (8, 8), "H_SAUCER": (11, 8), "H_ROCK": (8, 8),
    "H_GUN_T": (20, 11), "H_GUN_B": (20, 0),
    "FLASH0": (8, 8), "FLASH1": (8, 8), "FLASH2": (8, 8),
}

# colour per line (index 0-15), IC per pattern
CYAN, WHITE, MAG = C.CYAN, C.WHITE, C.MAGENTA
LINE_COLOURS = {
    "P_BOLT": {6: CYAN, 7: WHITE, 8: CYAN},
    "E_BULLET": {5: MAG, 6: MAG, 7: WHITE, 8: WHITE, 9: MAG, 10: MAG},
    "E_BULLET2": {6: MAG, 7: WHITE, 8: WHITE, 9: MAG},
    "P_CORE": {6: CYAN, 7: WHITE, 8: WHITE, 9: CYAN},
    "FLASH0": {r: WHITE for r in range(16)},
    "FLASH1": {r: (14 if r % 2 else 13) for r in range(16)},
    "FLASH2": {r: C.ORANGE for r in range(16)},
}
IC = {"E_BULLET": 1, "E_BULLET2": 1, "P_BOLT": 1, "FLASH0": 1, "FLASH1": 1, "FLASH2": 1,
      "P_CORE": 0, "P_HIT": 0, "H_DART": 0, "H_SAUCER": 0, "H_ROCK": 0, "H_GUN_T": 0,
      "H_GUN_B": 0, "BLANK": 1, "HUD": 1}

PLANES = {
    "GUARD": (0, 1), "E_BULLET": (1, 6), "P_CORE": (7, 1), "P_HIT": (8, 1), "P_BOLT": (9, 4),
    "E_HIT": (13, 11), "FX": (24, 4), "HUD": (28, 4),
}


def _cut_core(rows):
    """The player hitbox has a hole under the core (they share the anchor)."""
    core = ART["P_CORE"]
    return ["".join("." if core[y][x] == "#" else rows[y][x] for x in range(16)) for y in range(16)]


ART["P_HIT"] = _cut_core(ART["P_HIT"])


def bits(name):
    rows = ART[name]
    assert len(rows) == 16 and all(len(r) == 16 for r in rows), name
    return [[1 if ch == "#" else 0 for ch in r] for r in rows]


def pattern_bytes(name):
    b = bits(name)
    left = [sum(b[y][x] << (7 - x) for x in range(8)) for y in range(16)]
    right = [sum(b[y][8 + x] << (7 - x) for x in range(8)) for y in range(16)]
    return bytes(left + right)


def colour_table(name):
    ic = IC.get(name, 0) << 5
    cols = LINE_COLOURS.get(name, {})
    return bytes([ic | cols.get(y, 0) for y in range(16)])


def box(name):
    """Bounding box of the pattern's pixels relative to the object centre:
    (x0, y0, x1, y1), inclusive."""
    b = bits(name)
    ax, ay = ANCHOR[name]
    xs = [x for y in range(16) for x in range(16) if b[y][x]]
    ys = [y for y in range(16) for x in range(16) if b[y][x]]
    if not xs:
        return (0, 0, -1, -1)
    return (min(xs) - ax, min(ys) - ay, max(xs) - ax, max(ys) - ay)


# ------------------------------------------------------------------ font
FONT_CHARS = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-:.!x@"     # '@' = ship icon
_G = {
    " ": [".....", ".....", ".....", ".....", ".....", ".....", "....."],
    "0": [".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."],
    "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
    "3": ["#####", "...#.", "..#..", "...#.", "....#", "#...#", ".###."],
    "4": ["...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."],
    "5": ["#####", "#....", "####.", "....#", "....#", "#...#", ".###."],
    "6": ["..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."],
    "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."],
    "9": [".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."],
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "C": [".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."],
    "D": ["###..", "#..#.", "#...#", "#...#", "#...#", "#..#.", "###.."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "F": ["#####", "#....", "#....", "####.", "#....", "#....", "#...."],
    "G": [".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".####"],
    "H": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "I": [".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "J": ["..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."],
    "K": ["#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "M": ["#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"],
    "N": ["#...#", "#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "Q": [".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "V": ["#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."],
    "W": ["#...#", "#...#", "#...#", "#.#.#", "#.#.#", "#.#.#", ".#.#."],
    "X": ["#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"],
    "Y": ["#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."],
    "Z": ["#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"],
    "-": [".....", ".....", ".....", "#####", ".....", ".....", "....."],
    ":": [".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."],
    ".": [".....", ".....", ".....", ".....", ".....", ".##..", ".##.."],
    "!": ["..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."],
    "x": [".....", ".....", "#...#", ".#.#.", "..#..", ".#.#.", "#...#"],
}
SHIP_ICON = ["#.......", "###.....", ".#####..", "########", ".#####..", "###.....", "#......."]


def glyph(ch):
    """8 bytes, bit 7 = left pixel; 5 x 7 glyphs sit in columns 1-5."""
    if ch == "@":
        rows = SHIP_ICON + ["........"]
        return bytes(sum((1 << (7 - x)) for x in range(8) if r[x] == "#") for r in rows)
    rows = _G[ch] + ["....."]
    return bytes(sum((1 << (6 - x)) for x in range(5) if r[x] == "#") for r in rows)


def font_bytes():
    return b"".join(glyph(ch) for ch in FONT_CHARS)


def text_codes(s):
    return bytes(FONT_CHARS.index(ch) for ch in s)


MESSAGES = [
    ("MSG_TITLE", "VECTOR RAID"), ("MSG_PUSH", "PUSH SPACE"), ("MSG_DEMO", "DEMO"),
    ("MSG_READY", "READY"), ("MSG_ROUND", "ROUND"), ("MSG_WARN", "WARNING!"),
    ("MSG_GAME", "GAME"), ("MSG_OVER", "OVER"), ("MSG_HI", "HI"), ("MSG_1UP", "1UP"),
    # used by the game code only (bitmap text, geo3d/game/text.asm)
    ("MSG_DIGITS", "0123456789"), ("MSG_CREDIT", "GEO3D - V9968"),
]


# ------------------------------------------------------------------ export
def export(verbose=True):
    L = C.VRAM_LAYOUT
    lines = [
        "; Sprites (mode 2, 16x16) of the shooter; see geo3d/game/tools/sprites.py",
        "; for the collision rule and the plane map.",
        f"SPR_PAT_ADDR: equ 0x{L['SPR_PAT']:04x}\t; R#6 = SPR_PAT_ADDR >> 11",
        f"SPR_SAT_A: equ 0x{L['SPR_SAT_A']:04x}\t; colour table A at SPR_SAT_A - 512",
        f"SPR_SAT_B: equ 0x{L['SPR_SAT_B']:04x}\t; colour table B at SPR_SAT_B - 512",
        "SPR_HIDE_Y: equ 212\t\t; Y of an unused plane (never 216)",
        f"SPR_HUD_PAT: equ {HUD_FIRST * 4}\t; first run-time pattern number (HUD, text)",
        "; plane map: first plane, count",
    ]
    for k, (p0, n) in PLANES.items():
        lines += [f"PL_{k}: equ {p0}", f"PL_{k}_N: equ {n}"]
    lines.append("; pattern numbers (x4 in the attribute table), anchors, hitboxes")
    pats = b""
    for i, name in enumerate(ORDER):
        ax, ay = ANCHOR[name]
        x0, y0, x1, y1 = box(name)
        lines.append(f"SP_{name}: equ {4 * i}\t; anchor ({ax},{ay}) box x {x0}..{x1} y {y0}..{y1}")
        pats += pattern_bytes(name)
    lines.append(f"SPR_NPAT: equ {len(ORDER)}")
    lines.append("spr_patterns:\t\t; SPR_NPAT x 32 bytes, upload to SPR_PAT_ADDR")
    lines += C.asm_bytes(pats)
    lines.append("; colour tables: 16 bytes (one per line: bit5 IC, bits 3-0 colour)")
    cols = b""
    for name in ORDER:
        lines.append(f"spr_col_{name.lower()}:")
        lines += C.asm_bytes(colour_table(name))
        cols += colour_table(name)
    lines.append("; per pattern: anchor x, anchor y, box x0, y0, x1, y1 (signed, pixels")
    lines.append("; around the object centre): what the resolver tests")
    lines.append("spr_geom:")
    geom = b""
    for name in ORDER:
        ax, ay = ANCHOR[name]
        x0, y0, x1, y1 = box(name)
        rec = bytes([ax, ay] + [v & 0xFF for v in (x0, y0, x1, y1)])
        geom += rec
        lines += C.asm_bytes(rec)
    lines.append(f"; font: {len(FONT_CHARS)} glyphs x 8 bytes, codes = index in "
                 f"\"{FONT_CHARS}\" ('@' ship icon)")
    lines.append("FONT_NCHAR: equ %d" % len(FONT_CHARS))
    lines.append("font8:")
    lines += C.asm_bytes(font_bytes())
    lines.append("; messages: glyph codes, 0xFF ends; msg_table in MESSAGES order (level TEXT m)")
    lines.append("msg_table:")
    lines.append("        dw " + ", ".join(label.lower() for label, _ in MESSAGES))
    for label, s in MESSAGES:
        lines.append(f"{label.lower()}:\t\t; \"{s}\"")
        lines += C.asm_bytes(text_codes(s) + b"\xff")
    path = C.write_asm("sprites.asm", "sprites.py", lines)
    sizes = dict(patterns=len(pats), colours=len(cols), geom=len(geom), font=len(font_bytes()))
    if verbose:
        print(f"  sprites: {len(ORDER)} patterns ({len(pats)} bytes), colour tables {len(cols)} bytes, "
              f"font {len(FONT_CHARS)} glyphs ({len(font_bytes())} bytes)")
        for name in ORDER:
            print(f"    {name:9s} box {box(name)} IC={IC.get(name, 0)}")
    return path, sizes


if __name__ == "__main__":
    export()
