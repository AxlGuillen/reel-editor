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
