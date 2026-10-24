"""The edit's storyboard: a thumbnail per kill before the long render.

Each thumbnail is the clean zone of the span at the kill's frame with what the
camera will do: the crop window (green when the action is clear, yellow when
it was corrected by hand) and the point it aims at, or FULL (the whole zone).
Its caption: number, id, scoreboard clock, recipe and output second. All of
them go on one contact sheet. They are corrected with kill_overrides on
/edit/finish ({"kill": "1-10#2", "mode": "full"} or "focus": [u, v], the
point as fractions of the thumbnail).
"""
from __future__ import annotations

import os

import numpy as np

import config
from core import ffmpeg_runner
from modules.competitivo import layout

THUMB_W, THUMB_H = 480, 232
COLS = 4
GREEN, YELLOW, RED, WHITE = (60, 220, 60), (0, 210, 255), (60, 60, 230), (255, 255, 255)


def _track_x(track, t: float, default: float) -> float:
    if not track:
        return default
    pts = sorted(track)
    if t <= pts[0][0]:
        return pts[0][1]
    for (a, xa), (b, xb) in zip(pts, pts[1:]):
        if a <= t < b:
            return xa + (xb - xa) * (t - a) / (b - a)
    return pts[-1][1]


def _frame(span: str, t: float) -> np.ndarray:
    jx, jy, jw, jh = layout.JUEGO
    raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", span,
                                 "-frames:v", "1", "-vf", f"crop={jw}:{jh}:{jx}:{jy},scale={THUMB_W}:{THUMB_H}",
                                 "-f", "rawvideo", "-pix_fmt", "bgr24", "-"])
    if len(raw) != THUMB_W * THUMB_H * 3:
        return np.zeros((THUMB_H, THUMB_W, 3), np.uint8)
    return np.frombuffer(raw, np.uint8).reshape(THUMB_H, THUMB_W, 3).copy()


def thumb(shot: dict, zoom: float) -> np.ndarray:
    import cv2
    img = _frame(shot["span"], shot["src_t"])
    jx, _, jw, _ = layout.JUEGO
    sh = shot.get("shot") or {}
    mode = shot.get("mode", "zoom")
    if mode == "full":
        cv2.rectangle(img, (1, 1), (THUMB_W - 2, THUMB_H - 2), RED, 2)
        cv2.putText(img, "FULL", (THUMB_W - 70, THUMB_H - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.7, RED, 2)
    else:
        cw = layout.geometria(zoom).recorte[0]
        x = _track_x(shot.get("track"), shot["src_t"], layout.geometria(zoom).recorte[2])
        x0, x1 = int((x - jx) / jw * THUMB_W), int((x - jx + cw) / jw * THUMB_W)
        color = YELLOW if sh.get("by") == "override" else GREEN
        cv2.rectangle(img, (x0, 1), (x1, THUMB_H - 2), color, 2)
        if sh.get("sure") or sh.get("by") == "override":
            cv2.circle(img, (int(sh.get("u", 0.5) * THUMB_W), int(sh.get("v", 0.5) * THUMB_H)), 6, color, -1)
    label = f"#{shot['n']} {shot.get('id', '')}  {shot.get('clock', '')}  {shot.get('recipe', '')}  t={shot['t']:.1f}s"
    cv2.rectangle(img, (0, 0), (THUMB_W, 20), (0, 0, 0), -1)
    cv2.putText(img, label, (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.45, WHITE, 1)
    return img


def build(job_id: str, shots: list[dict], folder: str, zoom: float) -> dict:
    """Thumbnails and the contact sheet in `folder`; what goes in the status."""
    import cv2
    os.makedirs(folder, exist_ok=True)
    tiles, out = [], []
    for sh in shots:
        img = thumb(sh, zoom)
        name = f"{sh['n']:02d}.jpg"
        cv2.imwrite(os.path.join(folder, name), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        tiles.append(img)
        info = sh.get("shot") or {}
        out.append({"n": sh["n"], "id": sh.get("id"), "clip": sh.get("clip"), "clock": sh.get("clock"),
                    "t": sh["t"], "recipe": sh.get("recipe"), "mode": sh.get("mode"), "by": info.get("by"),
                    "sure": info.get("sure"), "focus": [info.get("u"), info.get("v")],
                    "thumb": f"/api/competitivo/storyboard/{job_id}/{sh['n']}"})
    if tiles:
        while len(tiles) % COLS:
            tiles.append(np.zeros_like(tiles[0]))
        sheet = np.vstack([np.hstack(tiles[i:i + COLS]) for i in range(0, len(tiles), COLS)])
        cv2.imwrite(os.path.join(folder, "storyboard.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return {"sheet": f"/api/competitivo/storyboard/{job_id}", "folder": folder, "shots": out,
            "how": 'kill_overrides en /edit/finish: [{"kill": "1-10#2", "mode": "full"}, '
                   '{"clock": "21:41", "focus": [0.62, 0.40]}] (focus = fracción de la miniatura)'}
