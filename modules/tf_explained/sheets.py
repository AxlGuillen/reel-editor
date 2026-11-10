"""Contact sheets to read a fight frame by frame before writing a line of the
script. Every claim of the voice is checked on them (and on the dossier).

  crop_sheets   2×2 sheets of one crop of the broadcast (Peyz's slot in the
                team bar, the minimap, a fight zone), one tile every `step` s
                of clock, with the clock on top
  fight_sheets  2×4 sheets: the frame, its minimap enlarged, and the HP of the
                10 from the dossier (blue/red; gray = dead)

What to look for (measured on Worlds 2026): the team bar shows R (the circle
on top), Cleanse (teal) and Flash (yellow) and goes 1–1.5 s behind the video;
the crowd or a player cam covers the minimap at times."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import config
from core import ffmpeg_runner
from modules.tf_explained.layout import MINIMAP


def _font(size: int):
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def frame(video: str, t: float, size: tuple[int, int] | None = None) -> Image.Image:
    """The frame of `video` at t s (RGB)."""
    w, h = size or ffmpeg_runner.probe_resolution(video) or (1920, 1080)
    raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", video,
                                 "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    return Image.fromarray(np.frombuffer(raw, np.uint8)[:w * h * 3].reshape(h, w, 3))


def _clock_label(c: float) -> str:
    m, s = divmod(c, 60)
    return f"{int(m)}:{s:05.2f}"


def crop_sheets(video: str, c0: float, a: float, b: float, step: float, out_prefix: str,
                crop: tuple[int, int, int, int] | None = None) -> list[str]:
    """`video` starts at clock c0 (source t = clock − c0). Tiles from clock a
    to b every `step` s, 960 wide; returns the sheet paths (out_prefix-NN.jpg)."""
    size = ffmpeg_runner.probe_resolution(video) or (1920, 1080)
    x, y, w, h = crop or (0, 0, *size)
    font = _font(22)
    tiles, c = [], a
    while c <= b + 1e-6:
        im = frame(video, c - c0, size).crop((x, y, x + w, y + h))
        im = im.resize((960, int(h * 960 / w)))
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, 118, 30), fill=(0, 0, 0))
        d.text((6, 3), _clock_label(c), fill=(255, 230, 80), font=font)
        tiles.append(im)
        c += step
    out = []
    th = tiles[0].height if tiles else 0
    for k in range(0, len(tiles), 4):
        sheet = Image.new("RGB", (1930, 2 * th + 10), (255, 255, 255))
        for j, t in enumerate(tiles[k:k + 4]):
            sheet.paste(t, ((j % 2) * 970, (j // 2) * (th + 10)))
        path = f"{out_prefix}-{k // 4 + 1:02d}.jpg"
        sheet.save(path, quality=85)
        out.append(path)
    return out


def fight_sheets(video: str, dossier: dict, c0: float, a: float, b: float, step: float, out_prefix: str) -> list[str]:
    """Each tile: the frame (640×360), its minimap (360×350) and the HP of
    the 10 at that clock from the dossier."""
    from modules.tf_explained.dossier import hp_at
    who = dossier["who"]
    order = sorted(who, key=lambda p: (who[p]["side"], int(p)))
    font, small = _font(15), _font(12)
    mx, my, mw, mh = MINIMAP
    size = ffmpeg_runner.probe_resolution(video) or (1920, 1080)
    tiles, c = [], a
    while c <= b + 1e-6:
        im = frame(video, c - c0, size)
        tile = Image.new("RGB", (1000, 400), (20, 20, 20))
        tile.paste(im.resize((640, 360)), (0, 30))
        tile.paste(im.crop((mx, my, mx + mw, my + mh)).resize((360, 350)), (640, 30))
        d = ImageDraw.Draw(tile)
        d.text((6, 6), _clock_label(c), fill=(255, 230, 80), font=font)
        hp = hp_at(dossier, c)
        x = 90
        for p in order:
            col = (120, 180, 255) if who[p]["side"] == "blue" else (255, 120, 120)
            d.text((x, 8), f"{str(who[p]['champion'])[:6]} {hp[p]}", fill=col if hp[p] else (110, 110, 110),
                   font=small)
            x += 90
        tiles.append(tile)
        c += step
    out = []
    for k in range(0, len(tiles), 8):
        sheet = Image.new("RGB", (2010, 4 * 410), (255, 255, 255))
        for j, t in enumerate(tiles[k:k + 8]):
            sheet.paste(t, ((j % 2) * 1010, (j // 2) * 410))
        path = f"{out_prefix}-{k // 8 + 1:02d}.jpg"
        sheet.save(path, quality=85)
        out.append(path)
    return out
