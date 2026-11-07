"""What the motion map looks like: the event sheet, the pairs sheet and the preview.

All frames come from one decode of the span (motion.processor.decode_command
plus a `select` on the file timestamps), so the frame a tile says is the frame
it shows. Labels are drawn with PIL: drawtext crashes on this machine
(Fontconfig).
"""
from __future__ import annotations

import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import config
from core import ffmpeg_runner
from modules.motion import processor

TILE_W = 400
COLS = 4
MAX_EVENTS = 24
LIME, WHITE, BLACK, RED = (200, 255, 0), (255, 255, 255), (0, 0, 0), (255, 70, 70)
COLORS = {"impact": LIME, "smear": (0, 220, 255), "flash": WHITE, "cut": (255, 160, 0), "pan": (180, 140, 255),
          "calm": (120, 120, 120)}


def _font(size: int):
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def frames_at(path: str, frames: list[int], fps: float, width: int, height: int) -> dict[int, np.ndarray]:
    """RGB frames (width × height) at those file frame numbers, from one decode."""
    wanted = sorted(set(frames))
    if not wanted:
        return {}
    out: dict[int, np.ndarray] = {}
    # one decode per cluster of nearby frames (a span of 24 min is not decoded whole)
    clusters, cur = [], [wanted[0]]
    for f in wanted[1:]:
        if f - cur[-1] > 10 * fps:
            clusters.append(cur)
            cur = [f]
        else:
            cur.append(f)
    clusters.append(cur)
    size = width * height * 3
    for group in clusters:
        f0, count = group[0], group[-1] - group[0] + 1
        cmd = processor.decode_command(path, f0, count, fps, width, height)
        keep = set(group)
        for k, chunk in enumerate(ffmpeg_runner.stream(cmd, size)):
            if f0 + k in keep:
                out[f0 + k] = np.frombuffer(chunk, np.uint8).reshape(height, width, 3)[..., ::-1].copy()
    return out


def _arrow(d: ImageDraw.ImageDraw, x: float, y: float, angle: float, length: float, color, width: int = 4) -> None:
    dx, dy = processor.angle_vector(angle, length)
    x2, y2 = x + dx, y + dy
    d.line((x, y, x2, y2), fill=color, width=width)
    for side in (150, -150):
        a = math.radians(angle + side)
        d.line((x2, y2, x2 + 14 * math.cos(a), y2 - 14 * math.sin(a)), fill=color, width=width)


def _tile(img: np.ndarray, label: str, color, e: dict | None = None) -> Image.Image:
    im = Image.fromarray(img)
    d = ImageDraw.Draw(im)
    w, h = im.size
    if e is not None and e["type"] in ("impact", "smear"):
        cx, cy = e["cx"] * w, e["cy"] * h
        d.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), outline=color, width=3)
        if e.get("angle") is not None:
            _arrow(d, cx, cy, e["angle"], 30 + 50 * e["score"], color)
    font = _font(15)
    box = d.textbbox((0, 0), label, font=font)
    d.rectangle((0, 0, box[2] + 10, box[3] + 8), fill=BLACK)
    d.text((5, 3), label, fill=color, font=font)
    return im


def _grid(tiles: list[Image.Image], cols: int, title: str | None = None) -> Image.Image:
    tw, th = tiles[0].size
    rows = math.ceil(len(tiles) / cols)
    top = 40 if title else 0
    sheet = Image.new("RGB", (cols * (tw + 4) + 4, top + rows * (th + 4) + 4), (14, 11, 16))
    if title:
        ImageDraw.Draw(sheet).text((10, 10), title, fill=LIME, font=_font(20))
    for i, t in enumerate(tiles):
        sheet.paste(t, (4 + (i % cols) * (tw + 4), top + 4 + (i // cols) * (th + 4)))
    return sheet


def event_sheet(path: str, m: dict, out: str, max_events: int = MAX_EVENTS) -> str:
    """The strongest impacts, smears and flashes, in time order, with arrow and center."""
    pool = [e for e in m["events"] if e["type"] in ("impact", "smear", "flash")]
    pool = sorted(sorted(pool, key=lambda e: -e["score"])[:max_events], key=lambda e: e["f"])
    w = TILE_W
    h = int(round(m["height"] * w / m["width"] / 2)) * 2
    if not pool:
        Image.new("RGB", (w * 2, h), (14, 11, 16)).save(out, quality=85)
        return out
    imgs = frames_at(path, [e["f"] for e in pool], m["fps"], w, h)
    tiles = []
    for e in pool:
        if e["f"] not in imgs:
            continue
        label = f"{processor.NAMES[e['type']]} {e['score']:.2f} · {e['t']:.2f} s · c{e['f']}"
        if e.get("dir"):
            label += f" · {e['dir']}"
        tiles.append(_tile(imgs[e["f"]], label, COLORS[e["type"]], e))
    title = f"{m['source']} · {m['start']:.2f}–{m['end']:.2f} s · {len(tiles)} eventos"
    _grid(tiles, COLS, title).save(out, quality=85)
    return out


def pairs_sheet(path: str, pairs: list[dict], maps: dict, rows: list[dict], out: str) -> str:
    """One row per pair: the annotated frame ±2, with what the detector found."""
    w, h = 320, 180
    fps = next(iter(maps.values()))["fps"]

    def center_of(p: dict, r: dict) -> int:
        if p["kind"] == "hit":
            return r.get("matched_also") or p["frame"]
        return p["cut"] or p["frame"]

    wanted = [center_of(p, r) + d for p, r in zip(pairs, rows) for d in range(-2, 3)]
    imgs = frames_at(path, wanted, fps, w, h)
    tiles = []
    for p, r in zip(pairs, rows):
        m = maps[p["n"]]
        by_frame: dict[int, list[dict]] = {}
        for e in m["events"]:
            by_frame.setdefault(e["f"], []).append(e)
        center = center_of(p, r)
        truth = {p["frame"], p.get("cut"), *p.get("also", [])}
        for d in range(-2, 3):
            f = center + d
            evs = [e for e in by_frame.get(f, []) if e["type"] in ("impact", "cut", "flash", "smear")]
            label = f"par {p['n']} {'OK' if r['ok'] else 'NO'} · c{f}" if d == -2 else f"c{f}"
            if f in truth:
                label += " ← verdad"
            if evs:
                label += " · " + ", ".join(processor.NAMES[e["type"]] for e in evs)
            color = (RED if not r["ok"] else LIME) if evs or d == -2 else WHITE
            img = imgs.get(f, np.zeros((h, w, 3), np.uint8))
            tiles.append(_tile(img, label, color, next((e for e in evs if e["type"] == "impact"), None)))
    ok = sum(r["ok"] for r in rows)
    _grid(tiles, 5, f"Las 10 parejas · {ok}/{len(rows)} · golpes a ±2 cuadros, poses sin golpe y con su corte").save(
        out, quality=85)
    return out


def strip(m: dict, width: int, height: int = 180) -> Image.Image:
    """The character force curve with the events marked, as wide as the video."""
    fr = m["frames"]
    n = m["frames_count"]
    force = np.array(fr["force"], float)
    top = max(float(np.percentile(force, 99)), 1.0)
    im = Image.new("RGB", (width, height), (14, 11, 16))
    d = ImageDraw.Draw(im)
    pts = [(k * (width - 1) / max(1, n - 1), height - 8 - min(1.0, force[k] / top) * (height - 40)) for k in range(n)]
    d.line(pts, fill=LIME, width=2)
    font = _font(13)
    for e in m["events"]:
        x = (e["f"] - m["f0"]) * (width - 1) / max(1, n - 1)
        color = COLORS.get(e["type"], WHITE)
        if e["type"] == "cut":
            d.line((x, 0, x, height), fill=color, width=1)
        elif e["type"] in ("impact", "smear", "flash"):
            d.line((x, 22, x, height), fill=color, width=2)
            d.text((x + 2, 4), processor.NAMES[e["type"]][0].upper(), fill=color, font=font)
        elif e.get("frames"):
            x2 = x + e["frames"] * (width - 1) / max(1, n - 1)
            d.rectangle((x, height - 6, x2, height - 1), fill=color)
    return im


def preview(path: str, m: dict, out: str, job_id: str) -> str:
    """The span with the curve under it and a playhead moving over the curve."""
    w = 960
    h = int(round(m["height"] * w / m["width"] / 2)) * 2
    strip_h = 180
    png = os.path.splitext(out)[0] + "-curva.png"
    strip(m, w, strip_h).save(png)
    dur = m["end"] - m["start"]
    # a plain seek: a frame off does not matter to the eye here, and it keeps
    # audio and video together (the analysis itself is frame-exact)
    cmd = [config.FFMPEG_PATH, "-y", "-v", "error", "-ss", f"{m['start']:.4f}", "-t", f"{dur:.4f}", "-i", path,
           "-loop", "1", "-t", f"{dur:.4f}", "-i", png,
           "-filter_complex",
           f"[0:v]scale={w}:{h},setsar=1,format=yuv420p[v];"
           f"[1:v]scale={w}:{strip_h},setsar=1,format=yuv420p[s];[v][s]vstack[st];"
           f"color=white:s=2x{strip_h}:d={dur:.4f}[ph];"
           f"[st][ph]overlay=x='t/{dur:.4f}*{w - 2}':y={h}:eof_action=pass[out]",
           "-map", "[out]", "-map", "0:a?", "-t", f"{dur:.4f}",
           *ffmpeg_runner.video_encode_flags(), "-c:a", "aac", "-b:a", "128k", out]
    ffmpeg_runner.run(cmd, job_id, total_duration=dur, progress_range=(85, 99))
    os.remove(png)
    return out
