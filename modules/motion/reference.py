"""What a finished reference edit does, measured: where it cuts, where it
flashes, inverts, zooms and shakes, whether those land on beats and on the
anime's own hits, and how dense it gets per song section.

    python -m modules.motion.reference <edit.mp4> [--out folder]

It reads the edit with the motion map (its cuts, flashes, inverted frames,
impacts and the camera: a zoom punch is a run of frames whose camera zoom
changes fast; a shake is a camera that reverses direction several times in
a few frames) and the song with beatmap (beats, downbeats, sections, drop).
The comparison with ReelForge's taxonomy (editing/catalog.json and
anime_edit/blocks.json) goes in the report.
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

from modules.beatmap import processor as beatmap
from modules.motion import processor as motion

ZOOM_RATE = 0.012             # |Δ log zoom| per frame for a zoom punch
SHAKE_PX = 6.0                # px/frame (1920 units) of camera motion in a shake
SHAKE_FLIPS = 3               # direction reversals within SHAKE_WINDOW frames
SHAKE_WINDOW = 8
ON_BEAT = 2                   # frames: "on the beat"
ON_HIT = 2                    # frames: "on a hit"


def camera_events(m: dict) -> list[dict]:
    """Zoom punches and shakes from the camera track of the motion map."""
    fr = m["frames"]
    n = m["frames_count"]
    f0, fps = m["f0"], m["fps"]
    out = []
    z = np.log(np.maximum(np.array(fr["cam_zoom"], float), 1e-3))
    k = 0
    while k < n:
        if abs(z[k]) < ZOOM_RATE:
            k += 1
            continue
        j = k
        while j + 1 < n and abs(z[j + 1]) >= ZOOM_RATE and np.sign(z[j + 1]) == np.sign(z[k]):
            j += 1
        if j - k + 1 >= 2:
            out.append({"type": "zoom", "f": f0 + k, "t": round((f0 + k) / fps, 3), "frames": j - k + 1,
                        "dir": "in" if z[k] > 0 else "out"})
        k = j + 1
    dx, dy = np.array(fr["cam_dx"], float), np.array(fr["cam_dy"], float)
    k = 0
    while k < n - SHAKE_WINDOW:
        win = range(k, k + SHAKE_WINDOW)
        flips = 0
        for axis in (dx, dy):
            s = [np.sign(axis[i]) for i in win if abs(axis[i]) >= SHAKE_PX]
            flips = max(flips, sum(1 for a, b in zip(s, s[1:]) if a != b))
        if flips >= SHAKE_FLIPS:
            out.append({"type": "shake", "f": f0 + k, "t": round((f0 + k) / fps, 3), "frames": SHAKE_WINDOW})
            k += SHAKE_WINDOW
        else:
            k += 1
    return out


def inverted_runs(m: dict) -> list[dict]:
    fr, f0, fps = m["frames"], m["f0"], m["fps"]
    out, k, n = [], 0, m["frames_count"]
    while k < n:
        if not fr["inverted"][k]:
            k += 1
            continue
        j = k
        while j + 1 < n and fr["inverted"][j + 1]:
            j += 1
        out.append({"type": "invert", "f": f0 + k, "t": round((f0 + k) / fps, 3), "frames": j - k + 1})
        k = j + 1
    return out


ZOOM_CUT = 0.04               # |log zoom| at a "cut" that makes it suspect
SAME_COLORS = 0.3             # HSV histogram distance (Bhattacharyya) below which it is the same shot


def _histograms(video: str, m: dict) -> list[np.ndarray]:
    import cv2
    from core import ffmpeg_runner
    raw = ffmpeg_runner.capture(motion.decode_command(video, m["f0"], m["frames_count"], m["fps"], 160, 90),
                                timeout=900)
    out = []
    for fr in np.frombuffer(raw, np.uint8).reshape(-1, 90, 160, 3):
        h = cv2.calcHist([cv2.cvtColor(fr, cv2.COLOR_BGR2HSV)], [0, 1], None, [18, 8], [0, 180, 0, 256])
        out.append(cv2.normalize(h, h).flatten())
    return out


def zoom_not_cut(video: str, m: dict) -> set[int]:
    """Frames the motion map called cuts that are zoom punches: the camera
    zooms hard there and the colors stay (a stylized edit zooms a lot; the
    detector was tuned on anime footage, where that is rare)."""
    import cv2
    cuts = [e["f"] for e in m["events"] if e["type"] == "cut"]
    if not cuts:
        return set()
    hist = _histograms(video, m)
    zoom = m["frames"]["cam_zoom"]
    out = set()
    for c in cuts:
        k = c - m["f0"]
        if not 1 <= k < len(hist):
            continue
        z = max(abs(math.log(max(zoom[j], 1e-3))) for j in range(max(0, k - 1), min(len(zoom), k + 2)))
        if z >= ZOOM_CUT and cv2.compareHist(hist[k - 1], hist[k], cv2.HISTCMP_BHATTACHARYYA) < SAME_COLORS:
            out.add(c)
    return out


def grid_alignment(times: list[float], fps: float, bpm_range=(60, 200)) -> dict:
    """Is the edit cut on a grid at all? The constant grid (any tempo, any
    phase) that puts the most cuts within ±ON_BEAT frames of a beat, against
    what random times would score on that same grid (window / period)."""
    t = np.asarray(times, float)
    if len(t) < 4:
        return {}
    tol = ON_BEAT / fps
    best = (0.0, 0.0, 0.0)
    for bpm in np.arange(bpm_range[0], bpm_range[1], 0.05):
        p = 60 / bpm
        for ph in np.arange(0, p, 0.005):
            frac = float((np.abs(((t - ph + p / 2) % p) - p / 2) <= tol).mean())
            if frac > best[0]:
                best = (frac, float(bpm), float(ph))
    frac, bpm, ph = best
    return {"best_bpm": round(bpm, 2), "phase": round(ph, 3), "on_grid": round(frac, 3),
            "chance": round(min(1.0, 2 * tol / (60 / bpm)), 3)}


def _near(t: float, times: list[float], tol: float) -> bool:
    i = np.searchsorted(times, t)
    return any(0 <= j < len(times) and abs(times[j] - t) <= tol for j in (i - 1, i))


def analyze(video: str) -> dict:
    m = motion.get(video)
    bm = beatmap.get(video, "librosa")
    fps = m["fps"]
    beats, downs = sorted(bm["beats"]), sorted(bm["downbeats"])
    hits = sorted(e["t"] for e in m["events"] if e["type"] == "impact")
    fake = zoom_not_cut(video, m)
    effects = ([e for e in m["events"] if e["type"] == "flash"]
               + [e for e in m["events"] if e["type"] == "cut" and e["f"] not in fake]
               + [{"type": "zoom", "f": f, "t": round(f / fps, 3), "frames": 1, "dir": "cut-like"} for f in sorted(fake)]
               + inverted_runs(m) + camera_events(m))
    effects.sort(key=lambda e: e["t"])
    tol_b, tol_h = ON_BEAT / fps, ON_HIT / fps
    for e in effects:
        e["on_beat"] = _near(e["t"], beats, tol_b)
        e["on_downbeat"] = _near(e["t"], downs, tol_b)
        e["on_hit"] = _near(e["t"], hits, tol_h)
        e["section"] = next((s["type"] for s in bm["sections"] if s["start"] <= e["t"] < s["end"]), "?")
        if bm.get("drop") is not None and e["t"] >= bm["drop"] and e["section"] == "chorus":
            e["section"] = "drop" if e["t"] < next((s["end"] for s in bm["sections"]
                                                     if s["start"] <= bm["drop"] < s["end"]), 1e9) else "chorus"
    types = sorted({e["type"] for e in effects})
    by_type = {}
    for t in types:
        ev = [e for e in effects if e["type"] == t]
        by_type[t] = {"count": len(ev), "on_beat": sum(e["on_beat"] for e in ev),
                      "on_downbeat": sum(e["on_downbeat"] for e in ev), "on_hit": sum(e["on_hit"] for e in ev)}
    sections = []
    for s in bm["sections"]:
        dur = s["end"] - s["start"]
        ev = [e for e in effects if s["start"] <= e["t"] < s["end"]]
        sections.append({"type": s["type"], "start": round(s["start"], 2), "end": round(s["end"], 2),
                         "cuts_per_s": round(sum(e["type"] == "cut" for e in ev) / max(dur, 1e-6), 2),
                         "effects_per_s": round(sum(e["type"] != "cut" for e in ev) / max(dur, 1e-6), 2),
                         "by_type": {t: sum(e["type"] == t for e in ev) for t in types}})
    real = sorted(e["t"] for e in effects if e["type"] == "cut")
    edges = [m["start"]] + real + [m["end"]]
    shots = [b - a for a, b in zip(edges, edges[1:]) if b > a]
    return {"zoom_not_cut": len(fake), "cut_grid": grid_alignment(real, fps),
"video": os.path.basename(video), "duration": round(m["end"] - m["start"], 2), "fps": round(fps, 3),
            "bpm": bm["bpm"], "drop": bm.get("drop"), "beats": len(beats), "hits": len(hits),
            "shot_median_s": round(float(np.median(shots)), 3) if shots else None,
            "by_type": by_type, "sections": sections, "events": effects, "motion_summary": motion.summary(m)}


NAMES = {"cut": "cortes", "flash": "destellos", "invert": "invertidos", "zoom": "golpes de zoom", "shake": "sacudidas"}


def report_md(r: dict, ours: dict | None = None) -> str:
    """The report in Spanish, for Axl."""
    lines = [f"# Referencia: {r['video']}", "",
             f"{r['duration']} s a {r['fps']} fps · canción a {r['bpm']} BPM · drop en "
             f"{r['drop']:.2f} s · plano mediano {r['shot_median_s']} s · {r['hits']} golpes del anime "
             "detectados por el mapa." if r.get("drop") is not None else "", "",
             "## Dónde pone cada efecto", "",
             "| Efecto | Cuántos | En beat (±2 cuadros) | En el 1 del compás | En un golpe del anime |", "|---|---|---|---|---|"]
    for t, s in r["by_type"].items():
        c = max(1, s["count"])
        lines.append(f"| {NAMES.get(t, t)} | {s['count']} | {s['on_beat']} ({100 * s['on_beat'] // c} %) | "
                     f"{s['on_downbeat']} ({100 * s['on_downbeat'] // c} %) | {s['on_hit']} ({100 * s['on_hit'] // c} %) |")
    g = r.get("cut_grid") or {}
    if g:
        lines += ["", f"**¿Corta en una rejilla?** La mejor rejilla constante ({g['best_bpm']} BPM) deja "
                      f"{100 * g['on_grid']:.0f} % de los cortes a ±2 cuadros de un beat; al azar serían "
                      f"{100 * g['chance']:.0f} %. Los «golpes de zoom» incluyen {r.get('zoom_not_cut', 0)} "
                      "que el mapa había tomado por cortes (zoom fuerte con los mismos colores)."]
    lines += ["", "## Densidad por sección (la curva)", "",
              "| Sección | Tramo | Cortes/s | Efectos/s | Detalle |", "|---|---|---|---|---|"]
    for s in r["sections"]:
        detail = ", ".join(f"{NAMES.get(k, k)} {v}" for k, v in s["by_type"].items() if v and k != "cut")
        lines.append(f"| {s['type']} | {s['start']:.1f}–{s['end']:.1f} s | {s['cuts_per_s']} | {s['effects_per_s']} | "
                     f"{detail or '—'} |")
    if ours:
        lines += ["", "## Contra la taxonomía de ReelForge (edit 3 con motion=on)", ""] + ours.get("lines", [])
    return "\n".join(lines) + "\n"


def main(video: str, out: str | None) -> dict:
    r = analyze(video)
    folder = out or os.path.dirname(os.path.abspath(video))
    os.makedirs(folder, exist_ok=True)
    base = os.path.splitext(os.path.basename(video))[0]
    with open(os.path.join(folder, f"{base}-referencia.json"), "w", encoding="utf-8") as fh:
        json.dump(r, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(folder, f"{base}-referencia.md"), "w", encoding="utf-8") as fh:
        fh.write(report_md(r))
    print(report_md(r))
    return r


if __name__ == "__main__":
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    args = [a for a in sys.argv[1:] if not a.startswith("--") and a != out]
    main(args[0], out)
