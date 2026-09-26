#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Preview images of the shooter's assets, for Alex (common.PREVIEW,
C:\\Projects\\mmsoft\\openmsx-geo3d\\game_preview by default):

  palette.png     the 16 entries, the two ramps
  tiles.png       the tiles decoded back from the SCREEN 5 bytes, and the
                  foreground laid over the background as the game does it
  models.png      every model, rendered by the geo3d reference (bit exact
                  with the RTL) in its attitudes, over the background
  sprites.png     the sprite patterns (hitboxes shown with an outline)
  mockup.png      a game frame: background, band, objects, sprites, HUD
  intro.gif       the intro and the first waves, from level.py's simulator
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import common as C
import gen_tiles
import level as L
import models as M
import render as R
import sprites as S


def font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
              "C:/Windows/Fonts/arial.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def tiles():
    t = gen_tiles.build(verbose=False)
    bg = np.concatenate([t["bg_l"]["img"], t["bg_r"]["img"]], axis=1)
    return bg, t["fg"]["img"]


def palette_png(path):
    sw, sh = 96, 64
    im = Image.new("RGB", (8 * sw, 2 * sh + 40), (24, 24, 24))
    d = ImageDraw.Draw(im)
    f = font(13)
    for i, rgb in enumerate(C.palette_rgb()):
        x, y = (i % 8) * sw, (i // 8) * sh
        d.rectangle([x + 2, y + 2, x + sw - 3, y + sh - 3], fill=rgb)
        lv = C.PALETTE[i]
        txt = f"{i}  ({lv[0]},{lv[1]},{lv[2]})" + ("  tile" if i in C.TILE_COLOURS else "")
        tc = (0, 0, 0) if sum(rgb) > 380 else (255, 255, 255)
        d.text((x + 6, y + 6), txt, fill=tc, font=f)
    d.text((6, 2 * sh + 4), "blue ramp: BASE 0 (entries 0-6, the tile blues)   "
           "warm ramp: BASE 8 (entries 8-14, tile orange = level 4)", fill=(220, 220, 220), font=f)
    d.text((6, 2 * sh + 21), "flat: 7 cyan (canopy, bolts, HUD), 15 magenta (enemy bullets, eyes); "
           "0 = transparent for TIMP and sprites", fill=(220, 220, 220), font=f)
    im.save(path)


def models_png(path, bg, fg, ms):
    by = {m.name: m for m in ms}
    rows = ["player", "dart", "saucer", "rock", "gunship"]
    cw, ch, scale = 96, 64, 3
    sheet = Image.new("RGB", (4 * cw * scale + 180, len(rows) * ch * scale + ch * scale), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    f = font(15)
    base = R.bg_window(bg, 150)
    R.fg_band(base, fg, 40)
    for r, name in enumerate(rows):
        m = by[name]
        for k, (label, att) in enumerate(M.ATTITUDES[name]):
            img = base.copy()
            skip, draw, cull, spans = R.draw_model(img, m, att, (128, 80))
            crop = img[80 - ch // 2:80 + ch // 2, 128 - cw // 2:128 + cw // 2]
            tile = Image.fromarray(R.to_rgb(crop)).resize((cw * scale, ch * scale), Image.NEAREST)
            sheet.paste(tile, (180 + k * cw * scale, r * ch * scale))
            d.text((180 + k * cw * scale + 6, r * ch * scale + 4),
                   f"{label}  ax,ay,az={att}  {draw} faces", fill=(255, 255, 0), font=f)
        d.text((8, r * ch * scale + 10), f"{name}\n{len(m.v)} v, {len(m.f)} f", fill=(255, 255, 255), font=font(18))
    # debris row
    r = len(rows)
    img = base.copy()
    for k, m in enumerate([mm for mm in ms if mm.name.startswith("debris")]):
        R.draw_model(img, m, (30 * k, 50 + 20 * k, 10 * k), (100 + (k % 3) * 28, 70 + (k // 3) * 22))
    crop = img[80 - ch // 2:80 + ch // 2, 128 - cw // 2:128 + cw // 2]
    tile = Image.fromarray(R.to_rgb(crop)).resize((cw * scale, ch * scale), Image.NEAREST)
    sheet.paste(tile, (180, r * ch * scale))
    d.text((8, r * ch * scale + 10), "debris\n6 x (4 v, 4 f)", fill=(255, 255, 255), font=font(18))
    sheet.save(path)


def tiles_png(path, bg, fg):
    """The tiles decoded from the SCREEN 5 bytes, and the band composition."""
    w = 512
    img = np.zeros((212 * 2 + 48 + 24, w), np.uint8)
    img[:212] = bg
    comp = bg.copy()
    for x in range(w):
        col = fg[:, x % 256]
        m = col != C.TRANSPARENT
        comp[C.BAND_Y:, x][m] = col[m]
    img[212 + 12:212 + 12 + 212] = comp
    img[212 * 2 + 24:, :256] = fg
    im = Image.fromarray(R.to_rgb(img)).resize((w * 2, img.shape[0] * 2), Image.NEAREST)
    d = ImageDraw.Draw(im)
    f = font(16)
    d.text((6, 2 * 212 + 2), "above: background (pages 2+3)   below: foreground over it at lines 164-211 "
           "(LMMM + TIMP), then the foreground alone (page 4)", fill=(255, 255, 0), font=f)
    im.save(path)


def sprites_png(path):
    names = S.ORDER
    cell = 20
    sc = 6
    im = Image.new("RGB", (max(7 * cell * sc, 22 * 44 + 20), 2 * cell * sc + 30 + 180), (40, 40, 40))
    d = ImageDraw.Draw(im)
    f = font(13)
    for i, name in enumerate(names):
        cx, cy = (i % 7) * cell * sc, (i // 7) * (cell * sc + 20) + 10
        b = S.bits(name)
        col = S.colour_table(name)
        for y in range(16):
            for x in range(16):
                if b[y][x]:
                    c = col[y] & 15
                    rgb = C.palette_rgb()[c] if c else (90, 90, 90)
                    d.rectangle([cx + x * sc, cy + y * sc, cx + x * sc + sc - 2, cy + y * sc + sc - 2], fill=rgb)
        ax, ay = S.ANCHOR[name]
        if 0 <= ax < 16:
            d.rectangle([cx + ax * sc, cy + ay * sc, cx + ax * sc + sc - 2, cy + ay * sc + sc - 2],
                        outline=(255, 0, 0))
        d.rectangle([cx, cy, cx + 16 * sc, cy + 16 * sc], outline=(120, 120, 120))
        d.text((cx + 2, cy + 16 * sc + 2), f"{name} IC={S.IC.get(name, 0)}", fill=(255, 255, 255), font=f)
    # font
    y0 = 2 * (cell * sc + 20) + 20
    fb = S.font_bytes()
    for k, ch in enumerate(S.FONT_CHARS):
        gx, gy = 10 + (k % 22) * 44, y0 + (k // 22) * 60
        for r in range(8):
            for x in range(8):
                if fb[8 * k + r] & (0x80 >> x):
                    d.rectangle([gx + x * 5, gy + r * 5, gx + x * 5 + 4, gy + r * 5 + 4], fill=(230, 230, 230))
    d.text((10, y0 + 125), "grey = colour 0 (invisible hitbox, collides only under a visible sprite of "
           "a lower plane); red square = anchor on the object centre", fill=(255, 255, 0), font=f)
    im.save(path)


def text_pixels(img, s, x, y, colour):
    fb = S.font_bytes()
    for k, ch in enumerate(s):
        g = S.FONT_CHARS.index(ch)
        for r in range(8):
            for b in range(8):
                if fb[8 * g + r] & (0x80 >> b):
                    xx, yy = x + 8 * k + b, y + r
                    if 0 <= xx < 256 and 0 <= yy < 212:
                        img[yy, xx] = colour


def game_frame(g, bg, fg, ms):
    """One tick as the game composes it: background window, stars where the
    background has not arrived, foreground (TIMP), geo3d objects, sprites."""
    by = {m.name: m for m in ms}
    img = np.zeros((C.SCR_H, C.SCR_W), np.uint8)
    sb = g.sbg // L.SUB
    e_bg = 256 if g.phase < 1 else max(0, min(256, -sb))
    if e_bg < 256:
        cols = (np.arange(e_bg, 256) + sb) % 512
        img[:, e_bg:] = bg[:, cols]
    for k, layer in enumerate(STARS):
        off = g.ssr[k] // L.SUB
        for sx, sy in layer:
            x = (sx - off) % 256
            if x < e_bg:
                img[sy, x] = L.STAR_COLOURS[k]
    if g.phase >= 2:
        sf = g.sfg // L.SUB
        e_fg = max(0, min(256, -sf))
        for x in range(e_fg, 256):
            col = fg[:, (x + sf) % 256]
            m = col != C.TRANSPARENT
            img[C.BAND_Y:, x][m] = col[m]
    faces = 0
    for d in g.debris:
        faces += R.draw_model_att(img, by[d.model], "debris", d.att, (d.x // L.SUB, d.y // L.SUB))[0]
    for e in g.enemies:
        faces += R.draw_model_att(img, by[e.model], L.TYPES[e.t][8], e.att, (e.x // L.SUB, e.y // L.SUB))[0]
    pl = g.player
    if not g.dead and (g.invul == 0 or g.tick % 2 == 0):
        faces += R.draw_model_att(img, by["player"], "player", pl.att, (pl.x // L.SUB, pl.y // L.SUB))[0]
    g.stats["faces_max"] = max(g.stats["faces_max"], faces)
    planes = g.sprite_list()
    for it in reversed(planes):
        if it:
            name, x, ya = it
            R.draw_sprite(img, S.bits(name), S.colour_table(name), x, ya)
    # HUD (planes 28-31) and messages (planes 24-27)
    text_pixels(img, f"{g.score:06d}", 8, 4, C.WHITE)
    text_pixels(img, f"@{min(g.lives, 9)}", 232, 4, C.CYAN)
    if g.text:
        text_pixels(img, g.text, 128 - 4 * len(g.text), 76, C.WHITE)
    return img


STARS = L.stars()


def gameplay(out, bg, fg, ms, ticks=1300, gif_from=0, gif_to=720, stills=()):
    g = L.Game(seed=0x1D2B, autopilot=True)
    frames = []
    pal = []
    for rgb in C.palette_rgb():
        pal += list(rgb)
    pal += [0] * (768 - len(pal))
    saved = []
    for t in range(ticks):
        g.step()
        if t in stills or (gif_from <= t < gif_to and t % 2 == 0):
            img = game_frame(g, bg, fg, ms)
            if gif_from <= t < gif_to and t % 2 == 0:
                p = Image.fromarray(img, "P")
                p.putpalette(pal)
                frames.append(p)
            if t in stills:
                saved.append(R.save(img, os.path.join(out, f"frame_{t:04d}.png"), 3))
        if getattr(g, "game_over", False):
            break
    if frames:
        frames[0].save(os.path.join(out, "intro.gif"), save_all=True, append_images=frames[1:],
                       duration=66, loop=0, optimize=False)
    return g, saved


def mockup_png(path, bg, fg, ms):
    """A composed frame with every element at once."""
    g = L.Game(autopilot=False)
    g.phase, g.sbg, g.sfg = 3, 180 * L.SUB, 40 * L.SUB
    g.player.x, g.player.y, g.player.att = 60 * L.SUB, 100 * L.SUB, 16
    g.score, g.lives = 12450, 3
    for t, x, y, att in (("dart", 200, 50, 8), ("dart", 226, 62, 11), ("saucer", 170, 128, 5),
                         ("rock", 120, 40, 9), ("gunship", 214, 110, 8)):
        g.spawn_enemy(L.TYPE_ID[t], L.PATH_ID["line"], x, y)
        g.enemies[-1].att = att
    g.bolts.append(L.Obj(kind="bolt", x=104 * L.SUB, y=101 * L.SUB, vx=0, vy=0, hp=1, life=0))
    g.bolts.append(L.Obj(kind="bolt", x=150 * L.SUB, y=101 * L.SUB, vx=0, vy=0, hp=1, life=0))
    for bx, by_ in ((150, 70), (135, 80), (182, 112)):
        g.bullets.append(L.Obj(kind="bullet", x=bx * L.SUB, y=by_ * L.SUB, vx=0, vy=0, hp=1, life=0))
    g.explode(96 * L.SUB, 44 * L.SUB, 5, "w")
    for d in g.debris:
        for _ in range(5):
            d.x += d.vx
            d.y += d.vy
    g.flashes[0].age = 4
    img = game_frame(g, bg, fg, ms)
    R.save(img, path, 3)


def main(argv=None):
    out = C.PREVIEW
    os.makedirs(out, exist_ok=True)
    bg, fg = tiles()
    ms = M.all_models()
    what = set(argv or ["all"])
    if what & {"all", "palette"}:
        palette_png(os.path.join(out, "palette.png"))
    if what & {"all", "tiles"}:
        tiles_png(os.path.join(out, "tiles.png"), bg, fg)
    if what & {"all", "models"}:
        models_png(os.path.join(out, "models.png"), bg, fg, ms)
    if what & {"all", "sprites"}:
        sprites_png(os.path.join(out, "sprites.png"))
    if what & {"all", "mockup"}:
        mockup_png(os.path.join(out, "mockup.png"), bg, fg, ms)
    if what & {"all", "game"}:
        g, saved = gameplay(out, bg, fg, ms, ticks=1900, gif_from=0, gif_to=760,
                            stills=(40, 120, 200, 270, 330, 480, 660, 900, 1100, 1290, 1560, 1640, 1720))
        print("  simulated", g.tick, "ticks: score", g.score, "lives", g.lives, "round", g.round + 1)
        print("  stats:", g.stats)
    if what & {"all", "sfx"}:
        import sfx
        sfx.export(verbose=False, wav_dir=os.path.join(out, "sfx"))
    print("previews in", out)


if __name__ == "__main__":
    main(sys.argv[1:])
