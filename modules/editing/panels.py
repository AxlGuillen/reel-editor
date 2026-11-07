"""Manga panels for the `panel` event, drawn frame by frame with PIL.

The manga edit (rules v1.0, edit3.py) fits the panel inside 1040×1700 of a
1080×1920 frame, resizes it ×1.1 with LANCZOS once and then, every frame,
scales that copy down BILINEAR to its animated size and pastes it centered.
This does exactly that and writes each frame as a full-frame RGBA PNG
(transparent outside the panel), so FFmpeg only overlays them: FFmpeg's own
bilinear sits half a pixel off PIL's and, on line art, that alone took the
SSIM against edit 3 from 0.99 to 0.88. (Scaling in FFmpeg also needed a fixed
canvas: overlay cannot take an input whose size changes every frame.)
"""
from __future__ import annotations

import hashlib
import os

from PIL import Image

from modules.editing import effects

PRESCALE = effects.PANEL_PRESCALE


def scale_at(e, k: int, fps: int) -> float:
    """Scale of the panel on its frame k: from_scale → 1 in `enter`, then +drift."""
    t = k / fps
    return e.from_scale + (1 - e.from_scale) * min(1.0, t / e.enter) + e.drift * t / e.dur


def track_at(track: list, t: float) -> tuple[float, float, float]:
    """(x, y, w) of a [[t, x, y, w], …] path at time t (linear, held at the ends)."""
    pts = sorted(track)
    if t <= pts[0][0]:
        return tuple(pts[0][1:])
    for (ta, *a), (tb, *b) in zip(pts, pts[1:]):
        if ta <= t <= tb:
            u = 0.0 if tb == ta else (t - ta) / (tb - ta)
            return tuple(a[i] + (b[i] - a[i]) * u for i in range(3))
    return tuple(pts[-1][1:])


def follow_frames(e, width: int, height: int, fps: int, folder: str) -> str:
    """The `follow` event's frames: its image centered on the tracked point,
    `w` wide, at `opacity`, fading in and out over `fade` s (edit 4's chest
    logos). Full-frame RGBA PNGs; returns the image2 pattern."""
    a, b = effects.frames_of(e.t, e.dur, fps)
    n = b - a + 1
    key = hashlib.sha1(f"{os.path.abspath(e.image)}|{os.path.getmtime(e.image)}|{width}x{height}|{fps}|{a}|{n}|"
                       f"{e.track}|{e.opacity}|{e.fade}".encode()).hexdigest()[:12]
    sub = os.path.join(folder, f"follow_{key}")
    pattern = os.path.join(sub, "f_%04d.png")
    if os.path.isfile(os.path.join(sub, f"f_{n - 1:04d}.png")):
        return pattern
    os.makedirs(sub, exist_ok=True)
    with Image.open(e.image) as im:
        logo = im.convert("RGBA")
    cache: dict[int, Image.Image] = {}
    for k in range(n):
        t = (a + k) / fps
        x, y, w = track_at(e.track, t)
        w = max(2, int(round(w)))
        if w not in cache:
            cache[w] = logo.resize((w, max(1, int(round(logo.height * w / logo.width)))), Image.LANCZOS)
        img = cache[w]
        ease = max(0.0, min(1.0, (t - e.t) / e.fade if e.fade else 1.0, (e.t + e.dur - t) / e.fade if e.fade else 1.0))
        alpha = img.getchannel("A").point(lambda v, s=e.opacity * ease: int(round(v * s)))
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        layer = img.copy()
        layer.putalpha(alpha)
        _place(canvas, layer, int(x - img.width / 2), int(y - img.height / 2))
        canvas.save(os.path.join(sub, f"f_{k:04d}.png"), compress_level=1)
    return pattern


def _place(canvas: Image.Image, layer: Image.Image, x: int, y: int) -> None:
    """alpha_composite that clips at the frame edges (it refuses negative offsets)."""
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(canvas.width, x + layer.width), min(canvas.height, y + layer.height)
    if x1 <= x0 or y1 <= y0:
        return
    canvas.alpha_composite(layer.crop((x0 - x, y0 - y, x1 - x, y1 - y)), (x0, y0))


def frames(e, width: int, height: int, fps: int, folder: str) -> str:
    """Writes the panel's frames (one per output frame of the event) and
    returns the image2 pattern (…_%04d.png, from 0). Cached by content."""
    a, b = effects.frames_of(e.t, e.dur, fps)
    n = b - a + 1
    key = hashlib.sha1(f"{os.path.abspath(e.image)}|{os.path.getmtime(e.image)}|{width}x{height}|{fps}|{n}|"
                       f"{e.dur}|{e.enter}|{e.from_scale}|{e.drift}".encode()).hexdigest()[:12]
    sub = os.path.join(folder, f"panel_{key}")
    pattern = os.path.join(sub, "f_%04d.png")
    if os.path.isfile(os.path.join(sub, f"f_{n - 1:04d}.png")):
        return pattern
    os.makedirs(sub, exist_ok=True)
    with Image.open(e.image) as im:
        im = im.convert("RGB")
        w0, h0 = effects.panel_size(im.size, width, height)
        pre = im.resize((int(w0 * PRESCALE), int(h0 * PRESCALE)), Image.LANCZOS)
    for k in range(n):
        f = scale_at(e, k, fps)
        pw, ph = int(w0 * f), int(h0 * f)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.paste(pre.resize((pw, ph), Image.BILINEAR), ((width - pw) // 2, (height - ph) // 2))
        canvas.save(os.path.join(sub, f"f_{k:04d}.png"), compress_level=1)
    return pattern
