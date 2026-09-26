#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Gameplay data of the shooter: enemy types, movement paths, the level
script (intro + one round that loops faster), and a reference simulator.

Timing: one game tick = 2 vertical blanks (30 ticks/s at 60 Hz), one page
flip per tick. Positions are 1/16 pixel (12.4), speeds 1/16 pixel per tick,
attitudes are indices into precomputed matrix tables (models.py
att_tables, out/inc/attitudes.asm): the Z80 computes no rotation.

------------------------------------------------------------------ script
Bytecode, one opcode byte then its arguments:
  01 n                 WAIT n ticks
  02 t p x y c g d     SPAWN c enemies of type t on path p at x*2, y,
                       g ticks apart, member k at y + k*d (d signed)
  03 n                 CLEAR: wait until no enemy is alive, at most n*4 ticks
  04 p                 PHASE p: 0 stars only, 1 the background enters from the
                       right (its scroll starts at -256), 2 the foreground
                       enters (the same), 3 play
  05 b f s             SPEED background, foreground, stars (1/16 px per tick);
                       the current speeds move 1/16 per tick to these
  06 m n               TEXT message m (sprites.MESSAGES index) for n ticks
  07                   LOOPSTART (the round starts here; ROUND n shown)
  08                   LOOP: round + 1, back to LOOPSTART
  09                   PLAYERIN: the player flies in from the left (48 ticks)
  0A s                 SFX s (sfx.EFFECTS index)
Round r (0 based, capped at 8) scales at spawn: speeds * (8 + r) / 8, fire
periods * 8 / (8 + r); types fire only from their first round on.

------------------------------------------------------------------ tables
etype_table, 16 bytes per type (index = type id):
  0 model (models.asm MDL_ order: 1 dart 2 saucer 3 rock 4 gunship)
  1 hit points   2-3 score, BCD word (0x0150 = 150 points)
  4 hitbox sprites (1-2)   5-6 their patterns (SP_ numbers)
  7 fire kind (0 none, 1 aimed, 2 spread of 3, 3 burst of 3 aimed)
  8 fire period (ticks)   9 first round that fires
  10 attitude table (ATT_ id)   11 attitude mode (0 bank by vy, 1 +1 per
  tick, 2 +1 every 2 ticks; models.py att_tables)   12 debris shards
  13 bullet spawn x offset (signed pixels)   14-15 0
etype_box, 4 bytes per type: its hitbox around the centre, x0, x1, y0, y1
  (signed pixels, inclusive; the union of its hitbox sprites' boxes)
path_table, 8 bytes per path: kind, vx, vy (signed, 1/16 px/tick), p0-p4
  0 LINE    straight
  1 SINE    y = y0 + p0 * sin(t * p1 + p2)       (p1, p2: 1/256 turn)
  2 DIVE    straight until x < p0*2, then steer vy towards the player
            (at most p1, change p2 per tick)
  3 ARC     straight for p0 ticks, then turn the velocity p2 (signed,
            1/256 turn) per tick for p1 ticks, then straight
  4 HOVER   in at vx until x <= p0*2, then track the player's y (at most
            p1), fire, and after p2*4 ticks leave at vx = p3 (signed)
  5 BOUNCE  straight, vy flips at the play area's top and bottom
"""
import math

import common as C
import sprites as S

TICK_HZ = 30
SUB = 16                       # 1/16 pixel

# ------------------------------------------------------------------ rules
RULES = [
    ("LIVES", 3, "lives at the start"),
    ("EXTRA_FIRST", 20000, "extra life at this score (points) ..."),
    ("EXTRA_EVERY", 50000, "... and every this many points after it"),
    ("PLAYER_SPEED", 32, "1/16 px per tick, both axes (2 px)"),
    ("PLAYER_XMAX", 200, "the player stays in x 16..200, y 24..156"),
    ("BOLT_SPEED", 128, "1/16 px per tick (8 px)"),
    ("BOLT_DX", 24, "bolt spawn: player x + 24, y + 1"),
    ("BOLT_COOLDOWN", 5, "ticks between bolts while fire is held"),
    ("BULLET_SPEED", 40, "enemy bullets, 1/16 px per tick, x (8 + round) / 8"),
    ("PLAYERIN_TICKS", 48, "fly-in from x = -40 at 2 px per tick"),
    ("RESPAWN_TICKS", 60, "after a death, before the fly-in"),
    ("INVUL_TICKS", 90, "no player sprites (blinking) after the fly-in"),
    ("TITLE_TICKS", 600, "title screen time before the demo"),
    ("DEMO_TICKS", 1800, "the demo returns to the title after this (or a death)"),
    ("DEMO_SEED", 0x1D2B, "xorshift16 seed of every game and of the demo"),
    ("GAMEOVER_TICKS", 180, "GAME OVER shown, then the title"),
]
R = {k: v for k, v, _ in RULES}

# ------------------------------------------------------------------ types
MODEL_ID = {"player": 0, "dart": 1, "saucer": 2, "rock": 3, "gunship": 4}
TYPES = [
    # name      model      hp  score boxes                   fire per 1st att mode debris shot_dx
    ("dart",    "dart",     1,  100, ["H_DART"],              1,  75, 1, "dart", 0, 4, -17),
    ("saucer",  "saucer",   3,  300, ["H_SAUCER"],            2,  70, 0, "saucer", 1, 5, -14),
    ("rock",    "rock",     4,  150, ["H_ROCK"],              0,   0, 0, "rock", 2, 5, 0),
    ("gunship", "gunship", 24, 2000, ["H_GUN_T", "H_GUN_B"],  3,  40, 0, "gunship", 0, 10, -34),
]
ATT_ORDER = ["player", "dart", "saucer", "rock", "gunship", "debris", "title"]      # models.ATT_ORDER
TYPE_ID = {t[0]: i for i, t in enumerate(TYPES)}

# ------------------------------------------------------------------ paths
LINE, SINE, DIVE, ARC, HOVER, BOUNCE = range(6)
PATHS = [
    # name        kind    vx   vy  p0   p1   p2  p3  p4
    ("sine_a",    SINE,   -24,  0, 28,   3,    0, 0, 0),
    ("sine_b",    SINE,   -24,  0, 28,   3,  128, 0, 0),
    ("line",      LINE,   -20,  0,  0,   0,    0, 0, 0),
    ("line_fast", LINE,   -44,  0,  0,   0,    0, 0, 0),
    ("drift_up",  LINE,   -16, -3,  0,   0,    0, 0, 0),
    ("drift_dn",  LINE,   -16,  3,  0,   0,    0, 0, 0),
    ("arc_dn",    ARC,    -32,  0, 40,  64, -2 & 0xFF, 0, 0),
    ("arc_up",    ARC,    -32,  0, 40,  64,    2, 0, 0),
    ("dive",      DIVE,   -28,  0, 90,  28,    2, 0, 0),
    ("hover",     HOVER,  -16,  0, 92,  14,  150, -40 & 0xFF, 0),
    ("bounce",    BOUNCE, -18, 14,  0,   0,    0, 0, 0),
]
PATH_ID = {p[0]: i for i, p in enumerate(PATHS)}

# ------------------------------------------------------------------ script
WAIT, SPAWN, CLEAR, PHASE, SPEED, TEXT, LOOPSTART, LOOP, PLAYERIN, SFX = range(1, 11)
MSG = {label: i for i, (label, _) in enumerate(S.MESSAGES)}
SFX_ID = {"shot": 0, "eshot": 1, "hit": 2, "explode": 3, "bigboom": 4, "death": 5,
          "extra": 6, "start": 7, "warn": 8}
RIGHT = 140                    # spawn x / 2: 280, off the right edge
STARS_V = 32                   # stars on the title and in the intro: 0.5, 1, 2 px per tick,
                               # whole pixels every tick or every other (R_STARS_V)


def spawn(t, p, y, count=1, gap=0, dy=0, x=RIGHT):
    return (SPAWN, TYPE_ID[t], PATH_ID[p], x, y, count, gap, dy & 0xFF)


SCRIPT = [
    # ---- intro (once per game)
    (PHASE, 0), (SPEED, 0, 0, STARS_V), (SFX, SFX_ID["start"]), (PLAYERIN,),
    (TEXT, MSG["MSG_READY"], 75), (WAIT, 75),
    (PHASE, 1), (SPEED, 56, 0, STARS_V), (WAIT, 80),      # background in: 3.5 px/tick
    (SPEED, 16, 0, 16), (WAIT, 30),                        # settles at 1 px/tick
    (PHASE, 2), (SPEED, 16, 56, 16), (WAIT, 80),           # foreground in
    (SPEED, 16, 32, 16), (WAIT, 20), (PHASE, 3),           # parallax 1 : 2
    # ---- the round
    (LOOPSTART,),
    (TEXT, MSG["MSG_ROUND"], 60), (WAIT, 50),
    spawn("dart", "sine_a", 60, 5, 10), (WAIT, 110),
    spawn("dart", "sine_b", 112, 5, 10), (WAIT, 110),
    spawn("rock", "drift_dn", 40), (WAIT, 25), spawn("rock", "drift_up", 140), (WAIT, 25),
    spawn("rock", "line", 92), (WAIT, 90),
    spawn("saucer", "arc_dn", 28, 3, 22), (WAIT, 110),
    spawn("saucer", "arc_up", 146, 3, 22), (WAIT, 120),
    spawn("dart", "dive", 40, 4, 12), (WAIT, 45), spawn("dart", "dive", 136, 4, 12), (WAIT, 105),
    spawn("rock", "bounce", 60, 2, 40, 50), (WAIT, 20),
    spawn("dart", "line_fast", 96, 6, 7), (WAIT, 140),
    spawn("saucer", "sine_a", 50, 2, 30), spawn("dart", "sine_b", 120, 4, 10), (WAIT, 150),
    (TEXT, MSG["MSG_WARN"], 90), (SFX, SFX_ID["warn"]), (WAIT, 90),
    spawn("gunship", "hover", 96, x=158), (CLEAR, 225), (WAIT, 60),
    (LOOP,),
]


def script_bytes():
    out = bytearray()
    for op in SCRIPT:
        out += bytes(v & 0xFF for v in op)
    return bytes(out)


def bcd(n):
    return int(str(n), 16)


def etype_bytes():
    out = bytearray()
    for (name, model, hp, score, boxes, fire, per, first, att, mode, deb, sdx) in TYPES:
        pats = [S.ORDER.index(b) * 4 for b in boxes] + [0]
        out += bytes([MODEL_ID[model], hp]) + C.le16(bcd(score)) + bytes(
            [len(boxes), pats[0], pats[1], fire, per, first, ATT_ORDER.index(att), mode, deb,
             sdx & 0xFF, 0, 0])
    return bytes(out)


def box_bytes():
    """Per type: the union of its hitbox sprites' boxes (x0, x1, y0, y1)."""
    out = bytearray()
    for t in TYPES:
        bs = [S.box(b) for b in t[4]]
        x0, y0 = min(b[0] for b in bs), min(b[1] for b in bs)
        x1, y1 = max(b[2] for b in bs), max(b[3] for b in bs)
        out += bytes(v & 0xFF for v in (x0, x1, y0, y1))
    return bytes(out)


def path_bytes():
    return b"".join(bytes(v & 0xFF for v in p[1:]) for p in PATHS)


# ------------------------------------------------------------------ export
def export(verbose=True):
    lines = ["; Level data of the shooter; formats in geo3d/game/tools/level.py",
             f"TICK_HZ: equ {TICK_HZ}	; one tick = 2 vertical blanks, one page flip"]
    for k, v, note in RULES:
        lines.append(f"R_{k}: equ {v}	; {note}")
    lines.append(f"R_STARS_V: equ {STARS_V}\t; stars speed on the title and in the intro")
    for i, t in enumerate(TYPES):
        lines.append(f"ET_{t[0].upper()}: equ {i}")
    for i, p in enumerate(PATHS):
        lines.append(f"PATH_{p[0].upper()}: equ {i}")
    lines.append("etype_table:\t\t; 16 bytes per type")
    eb = etype_bytes()
    lines += C.asm_bytes(eb)
    lines.append("etype_box:\t\t; 4 bytes per type: hitbox x0, x1, y0, y1 around the centre")
    lines += C.asm_bytes(box_bytes(), 4)
    lines.append("path_table:\t\t; 8 bytes per path")
    pb = path_bytes()
    lines += C.asm_bytes(pb, 8)
    sb = script_bytes()
    loop = 0
    for op in SCRIPT:
        if op[0] == LOOPSTART:
            break
        loop += len(op)
    lines.append(f"SCRIPT_LOOP: equ {loop}\t; offset of LOOPSTART in level_script")
    lines.append("level_script:")
    lines += C.asm_bytes(sb)
    lines.append("; 32-direction unit vectors for aimed shots: dx, dy (signed, 1/16 px")
    lines.append("; at speed 1 px/tick); direction 0 = +X, clockwise on screen (y down)")
    lines.append("dir32:")
    lines += C.asm_bytes(bytes(v & 0xFF for k in range(32) for v in dir_vec(k)))
    lines.append(f"; starfield: 3 layers x STAR_N (x, y); colours {STAR_COLOURS}, speeds {STAR_SPEED} x SPEED/4")
    lines.append(f"STAR_N: equ {STAR_N}")
    lines.append("stars_xy:")
    lines += C.asm_bytes(bytes(v for layer in stars() for p in layer for v in p))
    lines.append("; sine, 256 steps per turn, signed 8-bit (x 127)")
    lines.append("sin256:")
    lines += C.asm_bytes(bytes(v & 0xFF for v in SIN8))
    path = C.write_asm("level.asm", "level.py", lines)
    if verbose:
        print(f"  level: {len(TYPES)} enemy types ({len(eb)} bytes), {len(PATHS)} paths "
              f"({len(pb)} bytes), script {len(sb)} bytes (loop at {loop})")
    return path, dict(types=len(eb), paths=len(pb), script=len(sb))


# ------------------------------------------------------------------ stars
STAR_N = 24
STAR_COLOURS = (3, 5, 6)       # slow, middle, fast layer
STAR_SPEED = (1, 2, 4)         # x the SPEED stars value / 4


def stars():
    """3 layers of STAR_N stars (x 0-255, y 4-207), drawn where the
    background has not come in yet; each layer wraps at 256."""
    rng = Rng(0x5EED)
    return [[(rng.byte(), 4 + rng.byte() % 204) for _ in range(STAR_N)] for _ in range(3)]


# ------------------------------------------------------------------ math
SIN8 = [int(round(127 * math.sin(2 * math.pi * i / 256))) for i in range(256)]


def dir_vec(k):
    a = 2 * math.pi * k / 32
    return int(round(16 * math.cos(a))), int(round(16 * math.sin(a)))


def aim(dx, dy):
    """Direction 0-31 from a vector (screen: y down)."""
    return int(round(math.atan2(dy, dx) * 32 / (2 * math.pi))) % 32


class Rng:
    """xorshift16 (7, 9, 8): the game's generator, seeded per game."""

    def __init__(self, seed=0x1D2B):
        self.s = seed or 1

    def next(self):
        s = self.s
        s ^= (s << 7) & 0xFFFF
        s ^= s >> 9
        s ^= (s << 8) & 0xFFFF
        self.s = s
        return s

    def byte(self):
        return self.next() & 0xFF


# ------------------------------------------------------------------ simulator
class Obj:
    __slots__ = ("kind", "t", "x", "y", "vx", "vy", "age", "hp", "ax", "ay", "az", "fire",
                 "path", "y0", "p", "alive", "life", "model", "bank", "burst", "att")

    def __init__(self, **kw):
        self.alive = True
        self.age = 0
        for k, v in kw.items():
            setattr(self, k, v)


class Game:
    """Reference model of the gameplay rules (not bit exact with a Z80
    implementation, but the same rules): used for the previews and to check
    the sprite budget and the collision design."""

    def __init__(self, seed=0x1D2B, autopilot=True, quirk=False, guard=True):
        self.rng = Rng(seed)
        self.quirk, self.guard = quirk, guard
        self.pc, self.wait, self.round = 0, 0, 0
        self.phase = 0
        self.sbg = self.sfg = -256 * SUB          # layer scrolls, 1/16 px
        self.ssr = [0, 0, 0]                      # star layers
        self.v_bg = self.v_fg = self.v_st = 0
        self.t_bg = self.t_fg = self.t_st = 0
        self.player = Obj(kind="player", x=-40 * SUB, y=96 * SUB, vx=0, vy=0, att=8, ax=0,
                          ay=0, az=0, fire=0, hp=1, life=0)
        self.player_in = 0
        self.lives, self.score, self.invul, self.dead = R["LIVES"], 0, 0, 0
        self.enemies, self.bolts, self.bullets, self.debris, self.flashes = [], [], [], [], []
        self.pending = []                          # formation members still to spawn
        self.text, self.text_t = None, 0
        self.tick = 0
        self.autopilot = autopilot
        self.stats = dict(events=0, false=0, kills=0, hits=0, rams=0, max_enemies=0,
                          max_line=0, max_bullets=0, max_hitbox_planes=0, faces_max=0)
        self.sfx = []

    # ---------------------------------------------------------- script
    def step_script(self):
        if self.wait > 0:
            self.wait -= 1
            return
        while True:
            op = SCRIPT[self.pc]
            self.pc += 1
            k = op[0]
            if k == WAIT:
                self.wait = op[1] - 1
                return
            if k == SPAWN:
                _, t, p, x, y, count, gap, dy = op
                dy = dy - 256 if dy > 127 else dy
                for m in range(count):
                    self.pending.append([m * gap, t, p, x * 2, y + m * dy])
            elif k == CLEAR:
                if any(e.alive for e in self.enemies) or self.pending:
                    self.clear_left = getattr(self, "clear_left", op[1] * 4)
                    self.clear_left -= 1
                    if self.clear_left > 0:
                        self.pc -= 1
                        return
                self.__dict__.pop("clear_left", None)
            elif k == PHASE:
                self.phase = op[1]
                if op[1] == 1:
                    self.sbg = -256 * SUB
                if op[1] == 2:
                    self.sfg = -256 * SUB
            elif k == SPEED:
                self.t_bg, self.t_fg, self.t_st = op[1], op[2], op[3]
            elif k == TEXT:
                label = S.MESSAGES[op[1]][1]
                if label == "ROUND":
                    label = f"ROUND {self.round + 1}"
                self.text, self.text_t = label, op[2]
            elif k == LOOPSTART:
                self.loop_pc = self.pc - 1
            elif k == LOOP:
                self.round += 1
                self.pc = self.loop_pc
            elif k == PLAYERIN:
                self.player_in = R["PLAYERIN_TICKS"]
            elif k == SFX:
                self.sfx.append(op[1])

    def scale(self, v):
        r = min(self.round, 8)
        return v * (8 + r) // 8

    def spawn_enemy(self, t, p, x, y):
        name, model, hp, score, boxes, fire, per, first, att, mode, deb, sdx = TYPES[t]
        path = PATHS[p]
        e = Obj(kind=name, t=t, x=x * SUB, y=y * SUB, vx=self.scale(path[2]), vy=self.scale(path[3]),
                hp=hp, att=ATT_N[att] // 2 if mode == 0 else self.rng.byte() % ATT_N[att],
                path=p, y0=y * SUB, p=0, model=model,
                fire=per // 2 + (self.rng.byte() & 31) if fire else 0, bank=0, life=0,
                burst=2 if fire == 3 else 0)
        self.enemies.append(e)

    # ---------------------------------------------------------- movement
    def move_enemy(self, e):
        path = PATHS[e.path]
        _, kind, vx0, vy0, p0, p1, p2, p3, p4 = path
        s8 = lambda v: v - 256 if v > 127 else v  # noqa: E731
        pl = self.player
        if kind == SINE:
            e.x += e.vx
            e.y = e.y0 + (p0 * SUB * SIN8[(e.age * p1 + p2) & 255]) // 127
        elif kind == DIVE:
            e.x += e.vx
            if e.x < p0 * 2 * SUB:
                want = max(-p1, min(p1, (pl.y - e.y) // 8))
                e.vy += max(-p2, min(p2, want - e.vy))
            e.y += e.vy
        elif kind == ARC:
            if p0 <= e.age < p0 + p1:
                a = 2 * math.pi * s8(p2) / 256
                c, s = math.cos(a), math.sin(a)
                e.vx, e.vy = int(round(e.vx * c - e.vy * s)), int(round(e.vx * s + e.vy * c))
            e.x += e.vx
            e.y += e.vy
        elif kind == HOVER:
            if e.p == 0:
                e.x += e.vx
                if e.x <= p0 * 2 * SUB:
                    e.p, e.life = 1, p2 * 4
            elif e.p == 1:
                want = max(-p1, min(p1, (pl.y - e.y) // 16))
                e.vy += max(-2, min(2, want - e.vy))
                e.y += e.vy
                e.life -= 1
                if e.life <= 0:
                    e.p, e.vx = 2, self.scale(s8(p3))
            else:
                e.x += e.vx
        elif kind == BOUNCE:
            e.x += e.vx
            e.y += e.vy
            if e.y < C.PLAY_Y0 * SUB or e.y > C.PLAY_Y1 * SUB:
                e.vy = -e.vy
        else:
            e.x += e.vx
            e.y += e.vy
        # attitude
        name, model, hp, score, boxes, fire, per, first, att, mode, deb, sdx = TYPES[e.t]
        n = ATT_N[att]
        if mode == 0:
            want = max(0, min(n - 1, n // 2 - e.vy // 3))
            e.att += (want > e.att) - (want < e.att)
        elif mode == 1 or e.age % 2 == 0:
            e.att = (e.att + 1) % n
        if e.x < -48 * SUB or e.x > 320 * SUB and e.vx > 0 or e.y < -40 * SUB or e.y > 220 * SUB:
            e.alive = False
        # fire
        if fire and self.round >= first and e.x < 250 * SUB and self.dead == 0:
            e.fire -= 1
            if e.fire <= 0:
                self.enemy_fire(e, fire)
                if fire == 3 and e.burst > 0:          # burst: 3 shots 6 ticks apart
                    e.burst -= 1
                    e.fire = 6
                else:
                    e.fire = per * 8 // (8 + min(self.round, 8))
                    e.burst = 2 if fire == 3 else 0

    def enemy_fire(self, e, kind):
        pl = self.player
        d = aim(pl.x - e.x, pl.y - e.y)
        dirs = {1: [d], 2: [(d - 1) % 32, d, (d + 1) % 32], 3: [d]}[kind]
        speed = self.scale(R["BULLET_SPEED"])
        ox = TYPES[e.t][11] * SUB
        for k, dd in enumerate(dirs):
            if len(self.bullets) >= S.PLANES["E_BULLET"][1]:
                break
            vx, vy = dir_vec(dd)
            self.bullets.append(Obj(kind="bullet", x=e.x + ox, y=e.y, vx=vx * speed // 16,
                                    vy=vy * speed // 16, hp=1, life=0))
        self.sfx.append(SFX_ID["eshot"])

    # ---------------------------------------------------------- player
    def pilot(self):
        """Attract mode autopilot: follow the nearest enemy ahead in y,
        keep x near 56, dodge bullets close ahead, fire all the time."""
        pl = self.player
        target_y = 96 * SUB
        ahead = [e for e in self.enemies if e.alive and e.x > pl.x + 16 * SUB and e.x < 260 * SUB]
        if ahead:
            e = min(ahead, key=lambda o: o.x)
            target_y = e.y
        dy = target_y - pl.y
        up = dy < -2 * SUB
        down = dy > 2 * SUB
        for b in self.bullets:
            if 0 < b.x - pl.x < 48 * SUB and abs(b.y - pl.y) < 12 * SUB:
                up, down = (b.y > pl.y), (b.y <= pl.y)
                if pl.y < 40 * SUB:
                    up, down = False, True
                if pl.y > 140 * SUB:
                    up, down = True, False
        left = pl.x > 64 * SUB
        right = pl.x < 48 * SUB
        return dict(up=up, down=down, left=left, right=right, fire=True)

    def move_player(self, keys):
        pl = self.player
        if self.player_in > 0:
            pl.x += 2 * SUB
            self.player_in -= 1
            pl.att += (pl.att < 8) - (pl.att > 8)
            return
        if self.dead:
            return
        sp = R["PLAYER_SPEED"]
        vy = (-sp if keys["up"] else 0) + (sp if keys["down"] else 0)
        vx = (-sp if keys["left"] else 0) + (sp if keys["right"] else 0)
        pl.x = max(C.PLAY_X0 * SUB, min(R["PLAYER_XMAX"] * SUB, pl.x + vx))
        pl.y = max(C.PLAY_Y0 * SUB, min(C.PLAY_Y1 * SUB, pl.y + vy))
        target = 16 if vy < 0 else 0 if vy > 0 else 8       # climbing, diving, level
        pl.att += (target > pl.att) - (target < pl.att)
        pl.fire = max(0, pl.fire - 1)
        if keys["fire"] and pl.fire == 0 and len(self.bolts) < S.PLANES["P_BOLT"][1]:
            self.bolts.append(Obj(kind="bolt", x=pl.x + R["BOLT_DX"] * SUB, y=pl.y + SUB,
                                  vx=R["BOLT_SPEED"], vy=0,
                                  hp=1, life=0))
            pl.fire = R["BOLT_COOLDOWN"]
            self.sfx.append(SFX_ID["shot"])

    # ---------------------------------------------------------- sprites
    def sprite_list(self):
        """32 planes: (pattern name, x, y_attr) or None, as the SAT holds them."""
        planes = [None] * 32

        def put(base, n, items):
            for k, it in enumerate(items[:n]):
                planes[base + k] = it

        def at(name, x, y):
            ax, ay = S.ANCHOR[name]
            return (name, x // SUB - ax, y // SUB - ay - 1)
        pl = self.player
        P = S.PLANES
        put(P["E_BULLET"][0], P["E_BULLET"][1],
            [at("E_BULLET" if (self.tick // 4) % 2 == 0 else "E_BULLET2", b.x, b.y)
             for b in self.bullets])
        vulnerable = self.dead == 0 and self.player_in == 0 and self.invul == 0
        if vulnerable:
            if self.guard:
                planes[P["GUARD"][0]] = at("BLANK", pl.x, pl.y)
            planes[P["P_CORE"][0]] = at("P_CORE", pl.x, pl.y)
            planes[P["P_HIT"][0]] = at("P_HIT", pl.x, pl.y)
        put(P["P_BOLT"][0], P["P_BOLT"][1], [at("P_BOLT", b.x, b.y) for b in self.bolts])
        boxes = []
        for e in self.enemies:
            if e.alive:
                for b in TYPES[e.t][4]:
                    boxes.append(at(b, e.x, e.y))
        self.stats["max_hitbox_planes"] = max(self.stats["max_hitbox_planes"], len(boxes))
        put(P["E_HIT"][0], P["E_HIT"][1], boxes)
        fl = []
        for f in self.flashes:
            name = "FLASH0" if f.age < 3 else "FLASH1" if f.age < 6 else "FLASH2"
            fl.append(at(name, f.x, f.y))
        put(P["FX"][0], P["FX"][1], fl)
        return planes

    # ---------------------------------------------------------- collisions
    def collide(self, planes):
        """The V9968 FPGA rule, line by line (top first), pixel by pixel
        (left first): the first pixel where a sprite with IC=0 has a dot
        under a visible dot of a lower plane. With self.quirk, also the
        suspected sub_phase 3 case (sprites.py): the first sprite of the
        line (IC=0, a dot at x) against any visible dot at x - 1.
        Returns (x, y) or None, and the most sprites on one line."""
        rows = {}
        for p, it in enumerate(planes):
            if it is None:
                continue
            name, x, ya = it
            b = S.bits(name)
            col = S.colour_table(name)
            for r in range(16):
                y = ya + 1 + r
                if 0 <= y < C.SCR_H and any(b[r]):
                    rows.setdefault(y, []).append((p, x, b[r], col[r]))
                elif 0 <= y < C.SCR_H:
                    rows.setdefault(y, []).append((p, x, None, col[r]))
        maxline = max((len(v) for v in rows.values()), default=0)
        for y in sorted(rows):
            spr = sorted(rows[y])[:16]              # S16: 16 sprites per line
            plotted = [False] * 256
            hit = None
            for p, x, bitsrow, col in spr:
                if bitsrow is None:
                    continue
                ic = col & 0x20
                visible = (col & 15) != 0
                for k in range(16):
                    if not bitsrow[k]:
                        continue
                    xx = x + k
                    if not 0 <= xx < 256:
                        continue
                    if plotted[xx] and not ic and (hit is None or xx < hit):
                        hit = xx
                for k in range(16):
                    if bitsrow[k] and visible and 0 <= x + k < 256:
                        plotted[x + k] = True
            if self.quirk and spr[0][2] is not None and not spr[0][3] & 0x20:
                p, x, bitsrow, col = spr[0]
                for k in range(16):
                    xx = x + k
                    if bitsrow[k] and 1 <= xx < 256 and plotted[xx - 1] and (hit is None or xx < hit):
                        hit = xx
                        break
            if hit is not None:
                return (hit, y), maxline
        return None, maxline

    def resolve(self, cx, cy):
        """The resolver: which pair is at the collision pixel (boxes, 1 px
        of tolerance). Returns the event name."""
        def inside(name, o, tol=1):
            x0, y0, x1, y1 = S.box(name)
            ox, oy = o.x // SUB, o.y // SUB
            return ox + x0 - tol <= cx <= ox + x1 + tol and oy + y0 - tol <= cy <= oy + y1 + tol
        pl = self.player
        self.stats["events"] += 1
        for b in self.bolts:
            if inside("P_BOLT", b):
                for e in self.enemies:
                    if e.alive and any(inside(n, e) for n in TYPES[e.t][4]):
                        b.alive = False
                        e.hp -= 1
                        if e.hp <= 0:
                            self.kill(e)
                        else:
                            self.sfx.append(SFX_ID["hit"])
                        self.stats["hits"] += 1
                        return "enemy hit"
        vulnerable = self.dead == 0 and self.player_in == 0 and self.invul == 0
        if vulnerable:
            for b in self.bullets:
                if inside("E_BULLET", b) and (inside("P_CORE", pl) or inside("P_HIT", pl)):
                    b.alive = False
                    self.player_death()
                    return "player hit"
            if inside("P_CORE", pl):
                for e in self.enemies:
                    if e.alive and any(inside(n, e) for n in TYPES[e.t][4]):
                        self.stats["rams"] += 1
                        self.player_death()
                        return "ram"
        self.stats["false"] += 1
        return "ignored"

    def kill(self, e):
        e.alive = False
        name, model, hp, score, boxes, fire, per, first, att, mode, deb, sdx = TYPES[e.t]
        self.score += score
        self.stats["kills"] += 1
        self.explode(e.x, e.y, deb, "w", e.vx // 2)
        self.sfx.append(SFX_ID["bigboom" if name == "gunship" else "explode"])

    def explode(self, x, y, n, colour, vx=0):
        for k in range(n):
            d = self.rng.byte() & 31
            sp = 16 + (self.rng.byte() & 31)
            dx, dy = dir_vec(d)
            self.debris.append(Obj(kind="debris", model=f"debris{self.rng.byte() % 3}{colour}",
                                   x=x, y=y, vx=vx + dx * sp // 16, vy=dy * sp // 16,
                                   att=self.rng.byte() % ATT_N["debris"],
                                   hp=0, life=20 + (self.rng.byte() & 7)))
        if len(self.flashes) < 4:
            self.flashes.append(Obj(kind="flash", x=x, y=y, vx=0, vy=0, hp=0, life=10))

    def player_death(self):
        pl = self.player
        self.explode(pl.x, pl.y, 6, "b")
        self.sfx.append(SFX_ID["death"])
        self.dead = R["RESPAWN_TICKS"]
        self.lives -= 1

    # ---------------------------------------------------------- tick
    def step(self, keys=None):
        self.tick += 1
        self.step_script()
        for sp in ("bg", "fg", "st"):
            v, t = getattr(self, "v_" + sp), getattr(self, "t_" + sp)
            setattr(self, "v_" + sp, v + (1 if t > v else -1 if t < v else 0))
        if self.phase >= 1:
            self.sbg += self.v_bg
        if self.phase >= 2:
            self.sfg += self.v_fg
        for k, f in enumerate((1, 2, 4)):
            self.ssr[k] += self.v_st * f // 4
        # formations
        for m in self.pending:
            m[0] -= 1
        for m in [m for m in self.pending if m[0] < 0]:
            self.spawn_enemy(*m[1:])
        self.pending = [m for m in self.pending if m[0] >= 0]
        # player
        if keys is None:
            keys = self.pilot() if self.autopilot else dict(up=0, down=0, left=0, right=0, fire=0)
        self.move_player(keys)
        if self.dead:
            self.dead -= 1
            if self.dead == 0:
                if self.lives > 0:
                    self.player.x, self.player.y = -40 * SUB, 96 * SUB
                    self.player_in, self.invul = R["PLAYERIN_TICKS"], R["INVUL_TICKS"]
                else:
                    self.game_over = True
        elif self.invul and self.player_in == 0:
            self.invul -= 1
        # objects
        for e in self.enemies:
            e.age += 1
            self.move_enemy(e)
        for o in self.bolts + self.bullets:
            o.x += o.vx
            o.y += o.vy
            if not (-16 * SUB < o.x < 264 * SUB and 8 * SUB < o.y < 170 * SUB):
                o.alive = False
        for d in self.debris:
            d.x += d.vx
            d.y += d.vy
            d.att = (d.att + 1) % ATT_N["debris"]
            d.life -= 1
            if d.life <= 0:
                d.alive = False
        for f in self.flashes:
            f.age += 1
            if f.age >= 10:
                f.alive = False
        # two displayed frames per tick: each can report one collision
        for _ in range(2):
            planes = self.sprite_list()
            hit, maxline = self.collide(planes)
            self.stats["max_line"] = max(self.stats["max_line"], maxline)
            if hit is None:
                break
            ev = self.resolve(hit[0], hit[1])   # the Z80 reads S#3-S#6 (X + 12, Y + 8) and subtracts
            self.last_event = (ev, hit)
            self.clean()
        self.clean()
        self.stats["max_enemies"] = max(self.stats["max_enemies"], len(self.enemies))
        self.stats["max_bullets"] = max(self.stats["max_bullets"], len(self.bullets))
        if self.text_t:
            self.text_t -= 1
            if self.text_t == 0:
                self.text = None

    def clean(self):
        self.enemies = [e for e in self.enemies if e.alive]
        self.bolts = [b for b in self.bolts if b.alive]
        self.bullets = [b for b in self.bullets if b.alive]
        self.debris = [d for d in self.debris if d.alive]
        self.flashes = [f for f in self.flashes if f.alive]


ATT_N = {"player": 17, "dart": 17, "saucer": 16, "rock": 64, "gunship": 17, "debris": 32, "title": 32}


def round_length():
    """Ticks of the intro and of one round (all enemies left alive)."""
    ticks, intro = 0, None
    for op in SCRIPT:
        if op[0] == LOOPSTART:
            intro = ticks
        if op[0] == WAIT:
            ticks += op[1]
        if op[0] == CLEAR:
            ticks += op[1] * 4
    return intro, ticks - intro


if __name__ == "__main__":
    export()
    intro, rnd = round_length()
    print(f"  intro {intro} ticks ({intro / TICK_HZ:.1f} s), round up to {rnd} ticks ({rnd / TICK_HZ:.1f} s)")
