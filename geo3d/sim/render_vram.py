#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Turns the VRAM pages dumped by tb_system.v (the pages HRA!'s vdp_command.v
painted, one per shown frame) into video. SCREEN 5: 2 pixels per byte, even x
in the high nibble.

Usage: render_vram.py frames.hex out.mp4 palette.txt [loops] [fps] [gif|-] [png|-] [title]
"""
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

W, H = 256, 212
PAGE = H * 128


def pages(path):
    raw = bytes(int(l, 16) for l in open(path) if l.strip())
    out = []
    for i in range(0, len(raw) - PAGE + 1, PAGE):
        p = raw[i:i + PAGE]
        pix = bytearray(W * H)
        pix[0::2] = bytes(b >> 4 for b in p)
        pix[1::2] = bytes(b & 15 for b in p)
        out.append(bytes(pix))
    return out


def to_img(pix, pal):
    img = Image.frombytes("P", (W, H), pix)
    flat = []
    for r, g, b in pal:
        flat += [round(r * 255 / 7), round(g * 255 / 7), round(b * 255 / 7)]
    img.putpalette(flat + [0] * (768 - len(flat)))
    return img.convert("RGB")


def font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def main():
    src, out, fpal = sys.argv[1], sys.argv[2], sys.argv[3]
    loops = int(sys.argv[4]) if len(sys.argv) > 4 else 4
    fps = int(sys.argv[5]) if len(sys.argv) > 5 else 30
    gif = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] != "-" else None
    png = sys.argv[7] if len(sys.argv) > 7 and sys.argv[7] != "-" else None
    title = sys.argv[8] if len(sys.argv) > 8 else \
        "geo3d + V9968: faces texturizadas (LRMM) e sombreadas (LINE), pintadas pelo VDP"
    pal = [tuple(int(v) for v in l.split()) for l in open(fpal) if l.strip()]
    pg = pages(src)[1:129]                        # skip the initial blank page; one full turn
    screens = [to_img(p, pal) for p in pg]

    VW, VH, S = 960, 720, 3
    f1, f2 = font(22), font(16)
    ox, oy = (VW - W * S) // 2, 10
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{VW}x{VH}", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-movflags", "+faststart", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for _ in range(loops):
        for k, scr in enumerate(screens):
            canvas = Image.new("RGB", (VW, VH), (8, 8, 10))
            canvas.paste(scr.resize((W * S, H * S), Image.NEAREST), (ox, oy))
            d = ImageDraw.Draw(canvas)
            d.rectangle([ox - 2, oy - 2, ox + W * S + 1, oy + H * S + 1], outline=(60, 60, 70))
            d.text((ox, oy + H * S + 10), title, font=f1, fill=(220, 220, 225))
            d.text((ox, oy + H * S + 40),
                   f"VRAM pintada pelo vdp_command.v original do HRA!, comandada pelo geo3d a partir "
                   f"do programa Z80 real   |   quadro {k:3d}/{len(screens)}",
                   font=f2, fill=(150, 150, 160))
            proc.stdin.write(canvas.tobytes())
    proc.stdin.close()
    proc.wait()

    if gif:
        small = [s.resize((W * 2, H * 2), Image.NEAREST).quantize(colors=16, method=Image.Quantize.MEDIANCUT)
                 for s in screens]
        small[0].save(gif, save_all=True, append_images=small[1:], duration=1000 // 25, loop=0, optimize=True)
    if png:
        picks = [screens[i * len(screens) // 4] for i in range(4)]
        sheet = Image.new("RGB", (W * 2 * 4 + 30, H * 2), (8, 8, 10))
        for i, s in enumerate(picks):
            sheet.paste(s.resize((W * 2, H * 2), Image.NEAREST), (i * (W * 2 + 10), 0))
        sheet.save(png)
    print(f"{len(screens)} quadros, {loops} voltas, {fps} qps -> {out}"
          + (f" + {gif}" if gif else "") + (f" + {png}" if png else ""))


if __name__ == "__main__":
    main()
