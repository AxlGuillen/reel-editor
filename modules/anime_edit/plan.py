"""Anime edit planner (pure): chosen shots + beatmap + preset → EDL.

  1. Song window: starts on a downbeat so the drop lands around 35 % of the
     edit (between 4 and 15 s) and ends on a bar.
  2. Cut points per preset: every `step` beats per section (or on every sung
     word), half a beat in the buildups, and always on the drop.
  3. The drop belongs to the hero shot (the one Claude picked or the highest
     score): it lasts a bar, with a freeze and slow motion (RIFE if present).
  4. The other spans are filled with the shots in the chosen order (the story
     of the fight); if they run out, they repeat using another part.
  5. Accents and lyrics per preset.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from modules.anime_edit.presets import PRESETS, Preset
from modules.beatmap import analysis
from modules.editing import render, timing
from modules.editing.edl import Edl, Event, Piece, Segment, Text

FORMATS = {
    "vertical": (1080, 1920),          # the clip full width over its blurred copy
    "vertical_fill": (1080, 1920),     # crops the sides and fills the frame
    "horizontal": (1920, 1080),        # 16:9, like the reference edit
}
DROP_FRACTION, DROP_MIN_S, DROP_MAX_S = 0.35, 4.0, 15.0
MIN_CUT_S = 0.15
SHOT_EDGE_S = 0.04                    # skip the first/last frame (the cut)
MAX_EVENTS = 220                      # FFmpeg expressions grow with each one


@dataclass
class PlanOptions:
    format: str = "vertical"
    fps: int = 30
    duration_s: float = 30
    music_start: float | None = None
    text: str = "auto"                 # auto (the preset's) | lyrics | off
    look: str | None = None            # None = the preset's
    hero: int | None = None            # id of the hero shot
    interpolate: bool = True


def _beat_at(beats: list[float], i: float) -> float:
    """Time of the (fractional) beat i, interpolating between beats."""
    k = int(math.floor(i))
    if k >= len(beats) - 1:
        per = beats[-1] - beats[-2] if len(beats) > 1 else 0.5
        return beats[-1] + (i - (len(beats) - 1)) * per
    return beats[k] + (i - k) * (beats[k + 1] - beats[k])


def song_window(bm: dict, dur: float, music_start: float | None) -> tuple[float, float]:
    """(start, dur) of the song for the edit, bar to bar."""
    downs = bm["downbeats"] or bm["beats"]
    if music_start is not None:
        start = min(downs, key=lambda d: abs(d - music_start))
    elif bm.get("drop") is not None:
        wanted = bm["drop"] - min(DROP_MAX_S, max(DROP_MIN_S, DROP_FRACTION * dur))
        before = [d for d in downs if d <= wanted + 1e-6]
        start = before[-1] if before else downs[0]
    else:
        start = downs[0]
    end = min(start + dur, bm["duration"])
    closings = [d for d in downs if start + 4 <= d <= end + 1e-6]
    if closings:
        end = closings[-1]
    if end - start < 2:
        raise ValueError("La canción es muy corta para ese edit.")
    return start, end - start


def cut_points(bmw: dict, preset: Preset) -> list[float]:
    """Cut points in the window (output time), with 0 and the end."""
    dur = bmw["duration"]
    beats = bmw["beats"]
    out = {0.0, round(dur, 3)}
    if preset.cuts == "words" and bmw.get("words"):
        bar = 4 * analysis.period(bmw)
        last = 0.0
        for w in bmw["words"]:
            if w["start"] - last >= 0.3:
                out.add(round(w["start"], 3))
                last = w["start"]
        # Where nothing is sung (bridges, intro), cuts on the bars.
        for d in bmw["downbeats"]:
            gap = min((abs(d - c) for c in sorted(out)), default=99)
            if gap > bar:
                out.add(round(d, 3))
    elif beats:
        i = 0.0
        while True:
            t = _beat_at(beats, i)
            if t >= dur:
                break
            out.add(round(t, 3))
            step = preset.buildup_step if analysis.in_buildup(bmw, t) else \
                preset.step.get(analysis.section_at(bmw, t), 2)
            i += step
    if bmw.get("drop") is not None:
        out.add(round(bmw["drop"], 3))
    clean = []
    for c in sorted(out):
        if c > dur:
            continue
        if clean and c - clean[-1] < MIN_CUT_S:
            continue
        clean.append(c)
    if clean[-1] < dur - 1e-6:
        clean.append(round(dur, 3))
    return clean


def _shot_span(shot: dict, src: float, uses: int) -> tuple[float, float]:
    """Span of `src` s of the shot centered on its peak; on each repetition,
    shifted forward (the same thing is not seen twice)."""
    a, b = shot["start"] + SHOT_EDGE_S, shot["end"] - SHOT_EDGE_S
    length = max(0.0, b - a)
    src = min(src, length)
    start = shot["peak"] - src * 0.4 + uses * src * 0.6
    if length > src:
        start = a + ((start - a) % (length - src + 1e-9))
    start = min(max(start, a), b - src)
    return start, start + src


def _pieces(start: float, end: float, dur: float, profile: str) -> list[Piece]:
    """Fills `dur` with the span; if it is too short, the rest stays frozen at the end."""
    src = end - start
    if src >= dur * timing.MIN_SPEED * 0.999:
        return timing.ramp(start, end, dur, profile)
    used = src / timing.MIN_SPEED
    return timing.ramp(start, end, used, "constant") + [Piece(end, end, dur - used)]


def _clean_word(word: str) -> str:
    return re.sub(r"[^\w'¿?¡!áéíóúñüÁÉÍÓÚÑÜ-]", "", word)


def plan(source: str, shots: list[dict], bm: dict, preset_id: str, op: PlanOptions) -> tuple[Edl, dict]:
    """EDL of one variant. `shots` in the chosen order (with their measures)."""
    if preset_id not in PRESETS:
        raise ValueError(f"Variante desconocida: {preset_id!r}. Opciones: {', '.join(PRESETS)}.")
    if op.format not in FORMATS:
        raise ValueError(f"Formato inválido: {op.format!r}. Opciones: {', '.join(FORMATS)}.")
    if not shots:
        raise ValueError("Elegí al menos un plano.")
    preset = PRESETS[preset_id]
    start, dur = song_window(bm, op.duration_s, op.music_start)
    bmw = analysis.window(bm, start, dur)
    points = cut_points(bmw, preset)
    per = analysis.period(bmw)

    hero = next((p for p in shots if p["id"] == op.hero), None) or max(shots, key=lambda p: p["score"])
    others = [p for p in shots if p is not hero] or [hero]
    drop = bmw.get("drop")

    # Output spans [a, b); the drop one is extended up to a bar.
    spans = list(zip(points, points[1:]))
    if drop is not None:
        i = next((k for k, (a, _) in enumerate(spans) if abs(a - drop) < 1e-3), None)
        if i is not None:
            end = spans[i][1]
            j = i
            while j + 1 < len(spans) and end - drop < 4 * per - 1e-3:
                j += 1
                end = spans[j][1]
            spans[i:j + 1] = [(drop, end)]

    width, height = FORMATS[op.format]
    vf = {"vertical": render.vf_vertical_blur(width, height),
          "vertical_fill": render.vf_cover(width, height),
          "horizontal": render.vf_cover(width, height)}[op.format]
    segments, uses, k = [], {}, 0
    for a, b in spans:
        d = b - a
        is_hero = drop is not None and abs(a - drop) < 1e-3
        shot = hero if is_hero else others[k % len(others)]
        if not is_hero:
            k += 1
        n = uses.get(shot["id"], 0)
        uses[shot["id"]] = n + 1
        if is_hero:
            s0, s1 = _shot_span(shot, d * 0.8, n)
            pieces = timing.impact(s0, s1, d, freeze=0.1, slow=d * 0.5, slow_speed=0.35)
        else:
            s0, s1 = _shot_span(shot, d * preset.speed, n)
            pieces = _pieces(s0, s1, d, preset.profile)
        pieces = timing.clamp_speeds(pieces, 0.0, timing.MAX_SPEED)
        segments.append(Segment(source, pieces, f"plano {shot['id']}", vf,
                                interpolate=op.interpolate and (is_hero or preset.profile != "constant")))

    events = _accents(preset, bmw, [a for a, _ in spans], drop)
    text = preset.text if op.text == "auto" else op.text
    texts = []
    if text == "lyrics":
        words = bmw.get("words") or []
        for w, nxt in zip(words, words[1:] + [None]):
            end = min(nxt["start"] if nxt else w["end"] + 0.3, w["start"] + 1.2, dur)
            word = _clean_word(w["text"])
            if word and end - w["start"] >= 0.08:
                texts.append(Text(w["start"], end, word, "word"))
    edl = Edl(segments, width=width, height=height, fps=op.fps, events=events, texts=texts,
              music_start=start, look=op.look or preset.look, glow=preset.glow)
    info = {"variant": preset_id, "music_start": round(start, 3), "dur": round(dur, 3),
            "cuts": len(spans), "hero": hero["id"] if drop is not None else None,
            "drop": round(drop, 3) if drop is not None else None, "words": len(texts)}
    return edl, info


def _accents(preset: Preset, bmw: dict, starts: list[float], drop: float | None) -> list[Event]:
    choruses = [s for s in bmw["sections"] if s["type"] == "chorus"]

    def in_chorus(t):
        return any(s["start"] <= t < s["end"] for s in choruses)

    ev: list[Event] = []
    if drop is not None:
        ev += [Event(drop, "flash", 1.0, 0.2), Event(drop, "zoom", 1.0, 0.35)]
    if preset.flash == "chorus_bars":
        ev += [Event(d, "flash", 0.4, 0.12) for d in bmw["downbeats"] if in_chorus(d) and d != drop]
    elif preset.flash == "cuts":
        ev += [Event(t, "flash", 0.45, 0.12) for t in starts if t > 0 and t != drop]
    if preset.zoom == "chorus_cuts":
        ev += [Event(t, "zoom", 0.5, 0.2) for t in starts if in_chorus(t) and t != drop]
    elif preset.zoom == "chorus_beats":
        ev += [Event(b, "zoom", 0.35, 0.18) for b in bmw["beats"] if in_chorus(b) and b != drop]
    if preset.shake == "chorus_hits":
        ev += [Event(g, "shake", 0.5, 0.2) for g in bmw.get("hits", []) if in_chorus(g)]
    if preset.shake and drop is not None:
        ev.append(Event(drop, "shake", 1.0, 0.45))
    if preset.rgb == "drop" and drop is not None:
        ev.append(Event(drop, "rgb", 1.0, 0.3))
    elif preset.rgb == "chorus_cuts":
        ev += [Event(t, "rgb", 0.8, 0.12) for t in starts if in_chorus(t)]
    ev.sort(key=lambda e: e.t)
    if len(ev) > MAX_EVENTS:
        # The strongest stay (the drop always wins).
        ev = sorted(sorted(ev, key=lambda e: -e.intensity)[:MAX_EVENTS], key=lambda e: e.t)
    return ev
