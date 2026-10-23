"""The shots of the source video: where it cuts, how much it moves and how strong it is.

1. Cuts: PySceneDetect (AdaptiveDetector: compares each frame against the
   average of its neighbors, so a fast pan does not count as a cut). If it is
   not installed, FFmpeg's scdet filter.
2. Shots shorter than MIN_SHOT_S merge into the previous one: in anime the
   "impact frames" (1–3 white, black or inverted frames) look like cuts and
   are not.
3. Measures of each shot on small frames at SAMPLE_FPS: motion (difference
   between frames), brightness, saturation and flashes. From that a 0–100
   score that orders the proposal; the final decision is Claude's (with the
   contact sheet) or the user's.

Anime is animated "on twos" (12 drawings per second over 24 fps): measured at
6 frames per second that is not mistaken for stillness.
"""
from __future__ import annotations

import os
import re

import numpy as np

import config
from core import ffmpeg_runner

MIN_SHOT_S = 0.3
SAMPLE_FPS = 6
SAMPLE_W, SAMPLE_H = 96, 54
FLASH_JUMP = 40              # brightness rise (0–255) between samples that counts as a flash
PER_SHEET = 48               # 8 × 6
THUMB_W = 320
SHOT_BRIGHTNESS = (25, 215)  # outside this the shot is a flash or a fade


def _cuts_scenedetect(path: str) -> list[float] | None:
    try:
        from scenedetect import AdaptiveDetector, detect
    except ImportError:
        return None
    try:
        scenes = detect(path, AdaptiveDetector(adaptive_threshold=2.5, min_scene_len=4))
    except Exception:  # noqa: BLE001 - a file OpenCV can't open or decode: scdet reads it
        return None
    # `seconds` in new versions; get_seconds() in old ones. (`getattr(a, "seconds",
    # None) or …` called the deprecated method whenever a scene began at 0.0.)
    return [float(a.seconds if hasattr(a, "seconds") else a.get_seconds()) for a, _ in scenes][1:]


def _cuts_ffmpeg(path: str) -> list[float]:
    err = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "info", "-i", path, "-an",
                                 "-vf", "scale=320:-2,scdet=threshold=12", "-f", "null", "-"],
                                timeout=1800, stderr=True).decode("utf-8", "replace")
    return [float(m) for m in re.findall(r"lavfi\.scd\.time:\s*([\d.]+)", err)]


def cuts(path: str) -> list[float]:
    """Seconds where the shot changes (without 0)."""
    found = _cuts_scenedetect(path)
    return sorted(found if found is not None else _cuts_ffmpeg(path))


def _samples(path: str) -> np.ndarray:
    """(n, h, w, 3) RGB uint8 at SAMPLE_FPS."""
    raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-i", path, "-an", "-vf",
                                 f"fps={SAMPLE_FPS},scale={SAMPLE_W}:{SAMPLE_H}", "-pix_fmt", "rgb24",
                                 "-f", "rawvideo", "-"], timeout=1800)
    return np.frombuffer(raw, np.uint8).reshape(-1, SAMPLE_H, SAMPLE_W, 3)


def measure(samples: np.ndarray) -> dict[str, np.ndarray]:
    """Per-sample series: motion, brightness and saturation."""
    f = samples.astype(np.float32)
    gray = f.mean(axis=3)
    mx, mn = f.max(axis=3), f.min(axis=3)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0).mean(axis=(1, 2))
    motion = np.zeros(len(gray))
    if len(gray) > 1:
        motion[1:] = np.abs(np.diff(gray, axis=0)).mean(axis=(1, 2))
    return {"motion": motion, "brightness": gray.mean(axis=(1, 2)), "saturation": sat}


def merge_short(edges: list[float], minimum: float = MIN_SHOT_S) -> list[float]:
    """Edges [0, c1, …, end] without shots shorter than `minimum` (they merge
    into the previous one; the first one, into the next)."""
    out = [edges[0]]
    for b in edges[1:-1]:
        if b - out[-1] >= minimum:
            out.append(b)
    if edges[-1] - out[-1] < minimum and len(out) > 1:
        out.pop()
    out.append(edges[-1])
    return out


def _score(motion: float, sat: float, flashes: int, dur: float, extremes: float) -> int:
    dur_score = 1.0 if 0.8 <= dur <= 3.5 else max(0.0, 1 - abs(dur - 2) / 6)
    p = 100 * (0.5 * motion + 0.2 * sat + 0.15 * min(1.0, flashes / 3) + 0.15 * dur_score)
    # A shot that is mostly white or black (impact frames, fades) "moves" a lot
    # on the way in and out, but it is not a moment to show.
    if extremes > 0.3:
        p *= 0.3
    return int(round(p))


def shots_from(edges: list[float], series: dict[str, np.ndarray]) -> list[dict]:
    """One dict per shot with its measures (pure: the tests cover it)."""
    mot, bri, sat = series["motion"], series["brightness"], series["saturation"]
    ref_motion = float(np.percentile(mot, 95)) if len(mot) else 1.0
    out = []
    for i, (a, b) in enumerate(zip(edges, edges[1:]), 1):
        i0, i1 = int(a * SAMPLE_FPS) + 1, max(int(b * SAMPLE_FPS), int(a * SAMPLE_FPS) + 2)
        m = mot[i0:i1]
        s = sat[i0 - 1:i1]
        br = bri[i0 - 1:i1]
        flashes = int((np.diff(br) > FLASH_JUMP).sum()) if len(br) > 1 else 0
        motion = float(m.mean() / ref_motion) if len(m) and ref_motion > 0 else 0.0
        peak = a + (int(np.argmax(m)) + 1) / SAMPLE_FPS if len(m) else (a + b) / 2
        out.append({
            "id": i, "start": round(a, 3), "end": round(b, 3), "dur": round(b - a, 3),
            "peak": round(min(max(peak, a), b), 3),
            "motion": round(min(motion, 2.0), 3),
            "brightness": round(float(br.mean()) if len(br) else 0, 1),
            "saturation": round(float(s.mean()) if len(s) else 0, 3),
            "flashes": flashes,
            "score": _score(min(motion, 1.5) / 1.5, float(s.mean()) if len(s) else 0, flashes, b - a,
                            float(((br < SHOT_BRIGHTNESS[0]) | (br > SHOT_BRIGHTNESS[1])).mean()) if len(br) else 0),
        })
    return out


def detect(path: str) -> list[dict]:
    """Shots of the video with their measures."""
    dur = ffmpeg_runner.probe_duration(path)
    if not dur:
        raise RuntimeError("No se pudo leer la duración del video.")
    edges = merge_short([0.0] + [c for c in cuts(path) if 0 < c < dur] + [dur])
    return shots_from(edges, measure(_samples(path)))


def thumbnails(path: str, shots: list[dict], folder: str) -> None:
    """One JPG per shot, at its moment of most motion (shot_NNNN.jpg)."""
    os.makedirs(folder, exist_ok=True)
    for p in shots:
        ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-y", "-ss", f"{p['peak']:.3f}", "-i", path,
                               "-frames:v", "1", "-vf", f"scale={THUMB_W}:-2", "-q:v", "4",
                               os.path.join(folder, f"shot_{p['id']:04d}.jpg")])


def contact_sheets(folder: str, n_shots: int) -> list[str]:
    """Contact sheets (8 × 6) with each shot's number on top, built from the
    thumbnails: what Claude looks at to pick the moments."""
    font = os.path.relpath(os.path.join(config.FONTS_FOLDER, config.DEFAULT_FONT_FILE),
                           config.BASE_DIR).replace("\\", "/").replace(":", "\\:")
    outputs = []
    for h, first in enumerate(range(1, n_shots + 1, PER_SHEET), 1):
        out = os.path.join(folder, f"sheet_{h:02d}.jpg")
        box = f"scale={THUMB_W}:180:force_original_aspect_ratio=decrease,pad={THUMB_W}:180:(ow-iw)/2:(oh-ih)/2,"
        number = (f"drawtext=fontfile='{font}':text='%{{eif\\:n+{first}\\:d}}':x=8:y=6:fontsize=34:"
                  "fontcolor=yellow:box=1:boxcolor=black@0.7:boxborderw=6,")
        base = [config.FFMPEG_PATH, "-v", "error", "-y", "-start_number", str(first),
                "-i", os.path.join(folder, "shot_%04d.jpg"), "-frames:v", "1", "-vf"]
        try:
            ffmpeg_runner.capture(base + [box + number + "tile=8x6:padding=4:color=black", out],
                                  cwd=config.BASE_DIR)
        except RuntimeError:
            # FFmpeg without freetype (no drawtext): the sheet has no numbers;
            # the order is the same (left to right, row by row).
            ffmpeg_runner.capture(base + [box + "tile=8x6:padding=4:color=black", out], cwd=config.BASE_DIR)
        outputs.append(out)
    return outputs
