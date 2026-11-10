"""The motion module: decode a span small, analyze it, cache it, describe it.

`get(path, start, end)` is what other modules use (anime_edit, competitivo):
it returns motion.json from the cache when the same file, span, width and
analysis version were already analyzed. `process()` is the job behind
POST /api/motion/process: the map, the text summary, the event sheet and the
optional preview.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np

import config
from core import ffmpeg_runner, file_utils, job_manager
from modules.motion import analysis

DEFAULT_WIDTH = 480
SUMMARY_TYPES = ("impact", "smear", "pan", "calm")
STRENGTH = ((0.8, "fuerte"), (0.5, "medio"), (0.0, "suave"))
NAMES = {"impact": "golpe", "smear": "barrido", "pan": "paneo", "calm": "calma", "flash": "destello",
         "cut": "corte"}


def span_frames(fps: float, duration: float, start: float | None, end: float | None) -> tuple[int, int]:
    """(first frame, frame count) of [start, end) in file frames."""
    s = max(0.0, start or 0.0)
    e = min(duration, end) if end else duration
    if e <= s:
        raise ValueError("El tramo está vacío: el fin tiene que ser mayor que el inicio.")
    f0 = int(round(s * fps))
    return f0, max(1, int(round(e * fps)) - f0)


def decode_command(path: str, f0: int, count: int, fps: float, width: int, height: int) -> list[str]:
    """Raw BGR frames f0 … f0+count−1, scaled down. An input seek alone is not
    frame-exact (on the Black Clover mkv it skipped the first frame now and
    then), so the seek lands a second early and `select` keeps the frames from
    half a frame before f0, on the file's own timestamps (-copyts)."""
    t0 = max(0.0, (f0 - 0.5) / fps)
    seek = max(0.0, t0 - 1.0)
    return [config.FFMPEG_PATH, "-v", "error", "-nostdin", "-ss", f"{seek:.4f}", "-copyts", "-i", path,
            "-an", "-sn", "-dn", "-vf", f"select='gte(t\\,{t0:.4f})',scale={width}:{height}:flags=area",
            "-fps_mode", "passthrough", "-frames:v", str(count),
            "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]


def _geometry(path: str, width: int) -> tuple[float, float, int, int]:
    stream = ffmpeg_runner.probe_video_stream(path)
    res = ffmpeg_runner.probe_resolution(path)
    duration = ffmpeg_runner.probe_duration(path)
    if not stream or not res or not duration:
        raise ValueError("No se pudo leer el video (ffprobe no encontró la pista de video).")
    fps = stream.get("fps") or stream.get("avg_fps")
    if not fps:
        raise ValueError("No se pudo leer el frame rate del video.")
    w = min(width, res[0])
    h = int(round(res[1] * w / res[0] / 2)) * 2
    return float(fps), float(duration), w, h


def cache_path(fingerprint: str, f0: int, count: int, width: int) -> str:
    os.makedirs(config.MOTION_FOLDER, exist_ok=True)
    return os.path.join(config.MOTION_FOLDER, f"{fingerprint}-{f0}-{count}-w{width}-v{analysis.VERSION}.json")


def get(path: str, start: float | None = None, end: float | None = None, width: int = DEFAULT_WIDTH,
        calm_s: float = analysis.CALM_S, on_progress=None, refresh: bool = False) -> dict:
    """The motion map of [start, end) of `path` (seconds of the file)."""
    fps, duration, w, h = _geometry(path, width)
    f0, count = span_frames(fps, duration, start, end)
    fp = file_utils.quick_fingerprint(path)
    cache = cache_path(fp, f0, count, w)
    if not refresh and os.path.isfile(cache) and calm_s == analysis.CALM_S:
        with open(cache, encoding="utf-8") as fh:
            return json.load(fh)
    size = w * h * 3
    frames = (np.frombuffer(chunk, np.uint8).reshape(h, w, 3)
              for chunk in ffmpeg_runner.stream(decode_command(path, f0, count, fps, w, h), size))
    m = analysis.analyze(frames, fps, f0, w, h, calm_s=calm_s, on_progress=on_progress, total=count)
    if m["frames_count"] == 0:
        raise ValueError("FFmpeg no devolvió ningún cuadro de ese tramo.")
    n = m["frames_count"]
    m["frames"]["t"] = [round((f0 + k) / fps, 4) for k in range(n)]
    m["frames"].pop("_bursts", None)
    for s in m["shots"]:
        s["start"] = round((f0 + s.pop("f_start")) / fps, 3)
        s["end"] = round((f0 + s.pop("f_end")) / fps, 3)
    m.pop("_raw_cuts", None)
    m.update({"source": os.path.basename(path), "fingerprint": fp,
              "start": round(f0 / fps, 3), "end": round((f0 + n) / fps, 3)})
    if calm_s == analysis.CALM_S:
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(m, fh, ensure_ascii=False, separators=(",", ":"))
    return m


def strength(score: float) -> str:
    return next(name for limit, name in STRENGTH if score >= limit)


def describe(e: dict) -> str:
    """«toma 12 · golpe fuerte en 37.42 s (cuadro 898), hacia la izquierda, centro (0.41, 0.38)»."""
    text = f"toma {e['shot'] + 1} · {NAMES[e['type']]}"
    if e["type"] in ("impact", "smear", "flash"):
        text += f" {strength(e['score'])}"
    text += f" en {e['t']:.2f} s (cuadro {e['f']})"
    if e.get("dur"):
        text += f", {e['dur']:.1f} s"
    if e.get("dir"):
        text += f", hacia {'la ' if e['dir'] in ('derecha', 'izquierda') else ''}{e['dir']}"
    if e["type"] in ("impact", "smear"):
        text += f", centro ({e['cx']:.2f}, {e['cy']:.2f})"
    return text


def summary(m: dict, types=SUMMARY_TYPES, min_score: float = 0.0) -> list[str]:
    """One line per important event, for Claude."""
    head = (f"{m['source']} · {m['start']:.2f}–{m['end']:.2f} s · {m['frames_count']} cuadros a "
            f"{m['fps']:.3f} fps · {len(m['shots'])} tomas · dibujos repetidos {m['held_ratio']:.0%}")
    lines = [describe(e) for e in m["events"] if e["type"] in types and e["score"] >= min_score]
    return [head] + lines


def focus_at(m: dict, t: float) -> tuple[float, float]:
    """The action center (0–1) at second `t` of the file (the nearest frame)."""
    fr = m["frames"]
    k = int(round(t * m["fps"])) - m["f0"]
    k = min(max(k, 0), m["frames_count"] - 1)
    return fr["cx"][k], fr["cy"][k]


def hits(m: dict, min_score: float = 0.0) -> list[dict]:
    """The impact events, strongest first."""
    return sorted((e for e in m["events"] if e["type"] == "impact" and e["score"] >= min_score),
                  key=lambda e: -e["score"])


def process(job_id: str, path: str, params, keep_input: bool = False) -> None:
    """Job: map → json + summary + event sheet (+ preview)."""
    from modules.motion import sheet
    try:
        job_manager.update_job(job_id, status="processing", stage="Leyendo el movimiento…")
        m = get(path, params.start, params.end, params.width,
                on_progress=lambda p: job_manager.set_progress(job_id, int(p * 70)))
        folder = os.path.join(config.OUTPUT_FOLDER, f"motion-{job_id}")
        os.makedirs(folder, exist_ok=True)
        json_path = os.path.join(folder, "motion.json")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(m, fh, ensure_ascii=False, separators=(",", ":"))
        lines = summary(m)
        with open(os.path.join(folder, "resumen.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        job_manager.update_job(job_id, progress=75, stage="Armando la hoja de eventos…")
        sheet_path = sheet.event_sheet(path, m, os.path.join(folder, "eventos.jpg"))
        preview = ""
        if params.preview:
            job_manager.update_job(job_id, progress=85, stage="Armando la vista previa…")
            preview = sheet.preview(path, m, os.path.join(folder, "preview.mp4"), job_id)
        counts = {t: sum(1 for e in m["events"] if e["type"] == t) for t in NAMES}
        job_manager.update_job(job_id, status="done", progress=100, stage="Listo", output_path=preview,
                               json_path=json_path, sheet_path=sheet_path, summary=lines,
                               motion={"source": m["source"], "start": m["start"], "end": m["end"],
                                       "fps": m["fps"], "frames": m["frames_count"],
                                       "fps_analysis": m["fps_analysis"], "shots": len(m["shots"]),
                                       "held_ratio": m["held_ratio"], "counts": counts,
                                       "events": [e for e in m["events"] if e["type"] != "cut"][:300]})
    except Exception as exc:  # noqa: BLE001 - the job reports it; the UI shows the message
        job_manager.update_job(job_id, status="error", error=str(exc))
    finally:
        if not keep_input:
            file_utils.cleanup_paths(path)


def angle_vector(angle: float, length: float) -> tuple[float, float]:
    """(dx, dy) in image coordinates for an angle with 0° right, 90° up."""
    return length * math.cos(math.radians(angle)), -length * math.sin(math.radians(angle))
