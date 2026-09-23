#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Alex Moncks
"""Turns the LINE commands logged by tb_engine.v (demo_got.txt) into video.

Each LINE is rasterised with the same stepping as the V9968 RTL
(vdp_command.v): error accumulator starts at (NX-1)/2, NY is subtracted on
every major-axis step, a minor-axis step happens when it goes negative
(then NX is added back); NX+1 dots are drawn.

Usage: render_video.py demo_got.txt out.mp4 [loops] [fps] [gif] [palette.txt] [title]
palette.txt: 16 lines "r g b" (V9938 levels 0..7). Without it, every
command is drawn white (wireframe demo).
"""
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

W, H = 256, 212


def parse(path):
    frames, cur = [], []
    for line in open(path):
        p = line.split()
        if p[0] == "L":
            cur.append([int(x, 16) for x in p[1:]])
        elif p[0] == "F":
            frames.append(cur)
            cur = []
    return frames


def raster(cmds, pal=None):
    pix = bytearray(W * H)
    for b in cmds:
        x = b[0] | (b[1] << 8)
        y = (b[2] | (b[3] << 8)) & 0xFF            # remove the page offset
        nx = b[4] | (b[5] << 8)
        ny = b[6] | (b[7] << 8)
        arg = b[9]
        maj = arg & 1
        sx = -1 if arg & 4 else 1
        sy = -1 if arg & 8 else 1
        nyb = ((nx - 1) & 0x7FF) >> 1
        for i in range(nx + 1):
            if 0 <= x < W and 0 <= y < H:
                pix[y * W + x] = (b[8] & 15) if pal else 1
            if i == nx:
                break
            nb = nyb - ny
            shift = nb < 0
            nyb = nb + nx if shift else nb
            if maj:
                y += sy
                if shift:
                    x += sx
            else:
                x += sx
                if shift:
                    y += sy
    if pal:
        img = Image.frombytes("P", (W, H), bytes(pix))
        flat = []
        for r, g, bl in pal:
            flat += [round(r * 255 / 7), round(g * 255 / 7), round(bl * 255 / 7)]
        img.putpalette(flat + [0] * (768 - len(flat)))
        return img.convert("RGB")
    return Image.frombytes("L", (W, H), bytes(pix)).point(lambda v: 240 if v else 0)


def font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def main():
    src, out = sys.argv[1], sys.argv[2]
    loops = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    fps = int(sys.argv[4]) if len(sys.argv) > 4 else 30
    gif = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] != "-" else None
    pal = None
    if len(sys.argv) > 6:
        pal = [tuple(int(v) for v in l.split()) for l in open(sys.argv[6]) if l.strip()]
    title = sys.argv[7] if len(sys.argv) > 7 else \
        "geo3d + V9968: o Z80 envia 30 bytes por quadro, o VDP desenha as arestas"
    frames = parse(src)[:128]                     # one full turn
    screens = [raster(c, pal) for c in frames]

    # video frame: 960x720, MSX screen scaled 3x, caption underneath
    VW, VH, S = 960, 720, 3
    f1, f2 = font(22), font(16)
    ox, oy = (VW - W * S) // 2, 10
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{VW}x{VH}", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for lp in range(loops):
        for k, scr in enumerate(screens):
            canvas = Image.new("RGB", (VW, VH), (8, 8, 10))
            canvas.paste(scr.resize((W * S, H * S), Image.NEAREST).convert("RGB"), (ox, oy))
            d = ImageDraw.Draw(canvas)
            d.rectangle([ox - 2, oy - 2, ox + W * S + 1, oy + H * S + 1], outline=(60, 60, 70))
            d.text((ox, oy + H * S + 10),
                   title,
                   font=f1, fill=(220, 220, 225))
            d.text((ox, oy + H * S + 40),
                   f"simulação RTL a partir do programa Z80 real   |   quadro {k:3d}/128   |   "
                   f"{len(frames[k])} comandos LINE",
                   font=f2, fill=(150, 150, 160))
            proc.stdin.write(canvas.tobytes())
    proc.stdin.close()
    proc.wait()

    if gif:
        small = [s.resize((W * 2, H * 2), Image.NEAREST).convert("RGB").quantize(colors=16, method=Image.Quantize.MEDIANCUT) for s in screens]
        small[0].save(gif, save_all=True, append_images=small[1:], duration=1000 // 25,
                      loop=0, optimize=True)
    print(f"{len(screens)} quadros por volta, {loops} voltas, {fps} qps -> {out}" + (f" + {gif}" if gif else ""))


if __name__ == "__main__":
    main()
