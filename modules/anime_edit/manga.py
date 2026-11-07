"""The `manga` preset: the rules v1.0 edit (Magna vs Dante, edit 3) as a planner.

Pure: takes, panels, the grid and the song → an EDL for editing.render. The
LLM (or the user) chooses the takes, their framing, tone and length in beats,
and which manga panel pairs with which moment; the code computes every time.

  takes   [{"t": 372.05, "beats": 4, "frame": "B", "focus": [0.30, 0.45],
            "tone": "cold", "speed": 1.0}, {"panel": "m09_….png", "beats": 2}, …]
          A take plays the source from `t` (s) for `beats` beats at `speed`;
          `beats: null` on the last take fills to the end of the song. A
          `panel` item freezes the previous take's LAST frame for `beats` beats
          with that panel over it (the take's framing at its final focus).
  panels  [{"panel": "m09_….png", "t": 373.88, "beats": 2}] — the pairs
          panel–scene (the rules' annex), as an alternative to inline panel
          items: each goes right after the take whose span contains `t`, and
          that take is shifted so its last frame is the frame at `t`.
  grid    {"bpm": 130.75, "phase": 0.21}: a constant grid (beat k at
          phase + k·60/bpm, beat 0 a downbeat), for edits planned on one; by
          default the beatmap's tracked beats.

Cuts: the first take starts at the song's start (music_start) and covers the
lead-in up to the first downbeat at or after it; every boundary after that is
the beat `cum` beats later, rounded to the output frame. That is edit3.py's
`round((PH + cum*BEAT)*FPS)`.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from modules.anime_edit import framing
from modules.editing.edl import Edl, Event, Piece, Segment

FLASH_AMOUNT = 0.55           # the edit's flash: 55 % white for 0.07 s
FLASH_DUR = 0.07
VIDEO_FADE = 1.5
MUSIC_FADE_IN = 0.03
PHRASE_GAP = 0.45             # a pause this long between sung words starts a new phrase
MIN_WORD_SRT = 0.15
MAP_MARGIN = 0.5              # s of motion map on each side of a take (the hit–beat fit may reach there)


@dataclass
class MangaOptions:
    music_start: float = 0.0
    music_end: float | None = None            # None = the song's natural end
    fps: int = 30
    width: int = 1080
    height: int = 1920
    grid: dict | None = None                  # {"bpm", "phase"}; None = the beatmap's beats
    flashes: list[float] | None = None        # song s; None = the start of every phrase
    phrases: list[dict] | None = None         # [{a, b, words: [[a, b]…], flash}] song s; None = beatmap words
    panels_folder: str = ""
    panels: list[dict] = field(default_factory=list)
    panel_beats: float = 2.0
    words: list[dict] = field(default_factory=list)   # own texts, invert layout: [{word, start, end}] output s
    word_style: dict = field(default_factory=dict)
    # motion: "off" = the rules v1.0 edit exactly (phase 2); "on" = blocks,
    # effects on the real hits and hit–beat fit (anime_edit/blocks.py).
    motion: str = "off"
    blocks: list[dict] = field(default_factory=list)  # [{"take": i, "block": id}] chosen by Claude
    # composites (editing/recipes.py), off by default (the rules v1.0 edit exactly):
    # blur_cuts = off | radial | directional | mix — C2 on every cut between two takes
    blur_cuts: str = "off"
    blur_accent_every: int = 4
    blur_cuts_from: float = 0.0               # output s: a slow intro keeps its clean cuts
    # [{"kind": "hit" | "burst" | "isolate", "at": source s (the contact frame),
    #   + the recipe's own keys: angle, x, y, whip, color, strength}]; Claude picks
    # the moments in the episode, the code finds their output frame
    composites: list[dict] = field(default_factory=list)
    # C5: an .srt of the song with one word per cue (Axl's text), in song s
    lyrics_srt: str = ""


# ---------------------------------------------------------------- the grid

class Grid:
    """Song time of "beat number x" counted from the first downbeat at or
    after music_start (x can be fractional)."""

    def __init__(self, bm: dict, op: MangaOptions):
        self.start = op.music_start
        if op.grid:
            bpm, phase = float(op.grid["bpm"]), float(op.grid["phase"])
            if bpm <= 0:
                raise ValueError("El BPM de la rejilla tiene que ser mayor que 0.")
            self.beat = 60.0 / bpm
            bar = math.ceil((self.start - phase) / (4 * self.beat) - 1e-9)
            self.first = phase + bar * 4 * self.beat
            self.beats = None
        else:
            downs = [d for d in bm.get("downbeats", []) if d >= self.start - 1e-6]
            if not downs:
                raise ValueError("La canción no tiene compases después del inicio elegido.")
            self.first = downs[0]
            self.beats = [b for b in bm["beats"] if b >= self.first - 1e-6]
            self.beat = (self.beats[-1] - self.beats[0]) / max(1, len(self.beats) - 1)

    def at(self, x: float) -> float:
        if self.beats is None:
            return self.first + x * self.beat
        i = int(math.floor(x))
        if i + 1 < len(self.beats):
            return self.beats[i] + (x - i) * (self.beats[i + 1] - self.beats[i])
        return self.beats[-1] + (x - len(self.beats) + 1) * self.beat   # past the last tracked beat


# ---------------------------------------------------------------- input

def _panel_path(name: str, folder: str) -> str:
    path = name if os.path.isabs(name) else os.path.join(folder, name)
    if not os.path.splitext(path)[1]:
        path += ".png"
    return path


def normalize(takes: list[dict], op: MangaOptions, fps: int) -> list[dict]:
    """Validated items (take / panel), with the panel pairs inserted."""
    items = []
    for i, raw in enumerate(takes, 1):
        if "panel" in raw:
            path = _panel_path(str(raw["panel"]), op.panels_folder)
            if not os.path.isfile(path):
                raise ValueError(f"No existe el panel «{raw['panel']}» ({path}).")
            items.append({"kind": "panel", "image": path, "beats": float(raw.get("beats") or op.panel_beats),
                          "name": os.path.splitext(os.path.basename(path))[0]})
            continue
        try:
            t = float(raw["t"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"La toma {i} necesita 't' (el segundo del video donde arranca).")
        beats = raw.get("beats")
        if beats is not None:
            beats = float(beats)
            if beats <= 0:
                raise ValueError(f"La toma {i} tiene que durar al menos un poco ('beats' > 0).")
        focus = raw.get("focus", [0.5, 0.5])
        if isinstance(focus, (int, float)):
            focus = [focus, focus]
        if len(focus) != 2 or not all(0 <= float(f) <= 1 for f in focus):
            raise ValueError(f"El foco de la toma {i} son dos valores entre 0 y 1.")
        speed = float(raw.get("speed") or 1.0)
        if not 0.1 <= speed <= 4:
            raise ValueError(f"La velocidad de la toma {i} está fuera de rango (0.1–4).")
        items.append({"kind": "take", "t": t, "beats": beats, "frame": framing.LEGACY.get(raw.get("frame", "B"),
                                                                                      raw.get("frame", "B")),
                      "focus": (float(focus[0]), float(focus[1])), "tone": raw.get("tone", "cold"), "speed": speed})
    if items and items[0]["kind"] == "panel":
        raise ValueError("Un panel congela la toma anterior: no puede ir primero.")
    if any(it["beats"] is None for it in items[:-1]):
        raise ValueError("Solo la última toma puede durar «hasta el final» (beats vacío).")
    return items


# ---------------------------------------------------------------- plan

def boundaries(items: list[dict], grid: Grid, op: MangaOptions) -> list[tuple[int, int]]:
    """(first frame, end frame) of each item in the output."""
    fps = op.fps
    total = round((op.music_end - op.music_start) * fps)
    out, cum, prev = [], 0.0, 0
    for it in items:
        if it["beats"] is None:
            end = total
        else:
            cum += it["beats"]
            end = round((grid.at(cum) - op.music_start) * fps)
        if end <= prev:
            raise ValueError("Una toma quedó sin cuadros: revisá los tiempos de la rejilla.")
        if end > total:
            raise ValueError(f"Las tomas no caben en la canción: llegan a {end / fps:.2f} s y la canción "
                             f"dura {total / fps:.2f} s desde el inicio elegido.")
        out.append((prev, end))
        prev = end
    if prev != total:
        raise ValueError(f"Las tomas terminan en {prev / fps:.2f} s pero la canción sigue hasta {total / fps:.2f} s: "
                         "dejá la última con beats vacío o sumá tomas.")
    return out


def attach_panels(items: list[dict], spans: list[tuple[int, int]], pairs: list[dict], op: MangaOptions) -> None:
    """The panel pairs {panel, t}: after the take whose span contains t, the take
    shifted so its last frame is t. Mutates `items` (spans are recomputed)."""
    fps = op.fps
    for pair in sorted(pairs, key=lambda p: -float(p["t"])):
        t = float(pair["t"])
        for i, it in enumerate(items):
            if it["kind"] != "take" or it["beats"] is None:
                continue
            n = spans[i][1] - spans[i][0]
            last = it["t"] + (n - 1) / fps * it["speed"]
            if it["t"] - 1e-6 <= t <= last + 1.0:      # inside the take or just after its end
                it["t"] = t - (n - 1) / fps * it["speed"]
                path = _panel_path(str(pair["panel"]), op.panels_folder)
                if not os.path.isfile(path):
                    raise ValueError(f"No existe el panel «{pair['panel']}» ({path}).")
                items.insert(i + 1, {"kind": "panel", "image": path,
                                     "beats": float(pair.get("beats") or op.panel_beats),
                                     "name": os.path.splitext(os.path.basename(path))[0]})
                break
        else:
            raise ValueError(f"Ninguna toma contiene el segundo {t:.2f} del panel «{pair['panel']}».")


def phrases_from(bm: dict, op: MangaOptions) -> list[dict]:
    """[{a, b, words: [[a, b]…], flash}] in song s: the given ones, or the
    beatmap's words split at pauses of PHRASE_GAP."""
    if op.phrases is not None:
        return [{"a": float(p["a"]), "b": float(p["b"]), "words": [list(map(float, w)) for w in p.get("words", [])],
                 "flash": bool(p.get("flash", p.get("surv", True)))} for p in op.phrases]
    out: list[dict] = []
    for w in bm.get("words") or []:
        a, b = float(w["start"]), float(w["end"])
        if out and a - out[-1]["b"] < PHRASE_GAP:
            out[-1]["b"] = b
            out[-1]["words"].append([a, b])
        else:
            out.append({"a": a, "b": b, "words": [[a, b]], "flash": True})
    return out


def plan(video: str, source_size: tuple[int, int], bm: dict, takes: list[dict],
         op: MangaOptions, map_for=None) -> tuple[Edl, dict]:
    """The EDL of the manga edit and a summary (cuts in frames, panels, flashes).
    With op.motion == "on", `map_for(start, end)` returns the motion map of a
    source span (motion.processor.get): the blocks read it."""
    if op.music_end is None:
        op.music_end = float(bm["duration"])
    if op.music_end <= op.music_start:
        raise ValueError("El final de la canción tiene que ser después del inicio.")
    fps = op.fps
    grid = Grid(bm, op)
    items = normalize(takes, op, fps)
    spans = boundaries(items, grid, op)
    if op.panels:
        attach_panels(items, spans, op.panels, op)
        spans = boundaries(items, grid, op)
    segments, events = [], []
    last = None
    for it, (f0, f1) in zip(items, spans):
        n = f1 - f0
        dur = n / fps
        if it["kind"] == "take":
            sp = it["speed"]
            vf = framing.frame_filter(it["frame"], it["tone"], it["focus"], it["t"], dur * sp, source_size,
                                      op.width, op.height)
            segments.append(Segment(video, [Piece(it["t"], it["t"] + dur * sp, dur)],
                                    label=f"{it['frame']} {it['t']:.2f}", vf=vf, exact_phase=True))
            last = (it["t"] + (n - 1) / fps * sp, it)
        else:
            t_last, take = last
            fb = take["focus"][1]
            vf = framing.frame_filter(take["frame"], take["tone"], (fb, fb), t_last, 1.0, source_size,
                                      op.width, op.height)
            segments.append(Segment(video, [Piece(t_last, t_last, dur)], label=f"panel {it['name']}", vf=vf))
            events.append(Event(t=f0 / fps, kind="panel", dur=dur, image=it["image"]))
    total = spans[-1][1] / fps
    phrases = phrases_from(bm, op)
    flashes = op.flashes if op.flashes is not None else [p["a"] for p in phrases if p["flash"]]
    flash_out = [f - op.music_start for f in flashes if op.music_start < f < op.music_end]
    for t in flash_out:
        events.append(Event(t=t, kind="flash", intensity=FLASH_AMOUNT, dur=FLASH_DUR, shape="mix"))
    edl = Edl(segments=segments, fps=fps, width=op.width, height=op.height, events=events,
              music_start=op.music_start, look="neutral", glow=0.0, vignette=False, fade_out=0.0,
              music_fade_in=MUSIC_FADE_IN, video_fade_out=VIDEO_FADE)
    comp_info = _composites(edl, items, spans, op)
    if op.words:
        edl.subtitle_words = op.words
        edl.subtitle_style = dict(op.word_style)
    motion_info = None
    if op.motion == "on":
        from modules.anime_edit import blocks
        maps = {}
        for i, it in enumerate(items):
            if it["kind"] == "take" and map_for is not None:
                seg = segments[i]
                maps[i] = map_for(max(0.0, seg.pieces[0].start - MAP_MARGIN), seg.pieces[-1].end + MAP_MARGIN)
        beat_frames = sorted({round((grid.at(k) - op.music_start) * fps) for k in range(0, 4096)
                              if grid.at(k) < op.music_end})
        motion_info = blocks.apply(edl, items, spans, maps, beat_frames, bm, op.music_start, fps, op.blocks)
    elif op.motion != "off":
        raise ValueError("'motion' es on u off.")
    info = {"cuts": [f1 for _, f1 in spans[:-1]], "frames": spans[-1][1], "duration": round(total, 3),
            "panels": [{"name": it["name"], "start": round(f0 / fps, 3), "end": round(f1 / fps, 3)}
                       for it, (f0, f1) in zip(items, spans) if it["kind"] == "panel"],
            "flashes": [round(t, 3) for t in flash_out], "grid": "constant" if op.grid else "beatmap",
            "first_downbeat": round(grid.first - op.music_start, 4)}
    if motion_info is not None:
        info["motion"] = motion_info
    if comp_info:
        info["composites"] = comp_info
    return edl, info


# ---------------------------------------------------------------- composites

COMPOSITE_RECIPES = ("hit", "burst", "isolate")


def output_frame(items: list[dict], spans: list[tuple[int, int]], at: float, fps: int) -> int | None:
    """The output frame showing source second `at` (None if no take shows it)."""
    for it, (f0, f1) in zip(items, spans):
        if it["kind"] != "take":
            continue
        k = f0 + round((at - it["t"]) / it["speed"] * fps)
        if f0 <= k < f1:
            return k
    return None


def _composites(edl: Edl, items: list[dict], spans: list[tuple[int, int]], op: MangaOptions) -> dict:
    from modules.editing import recipes
    fps = op.fps
    info: dict = {}
    if op.blur_cuts != "off":
        if op.blur_cuts not in ("radial", "directional", "mix"):
            raise ValueError("'blur_cuts' es off, radial, directional o mix.")
        # only between two takes: a panel enters on a held frame, clean
        cuts = [spans[i][0] for i in range(1, len(items))
                if items[i]["kind"] == "take" and items[i - 1]["kind"] == "take"
                and spans[i][0] >= op.blur_cuts_from * fps - 1e-6]
        edl.events += recipes.blur_cuts(cuts, fps, style=op.blur_cuts, accent_every=op.blur_accent_every)
        info["blur_cuts"] = len(cuts)
    placed = []
    for n, c in enumerate(op.composites):
        kind = c.get("kind")
        if kind not in COMPOSITE_RECIPES:
            raise ValueError(f"Compuesto desconocido: {kind!r}. Opciones: {', '.join(COMPOSITE_RECIPES)}.")
        k = output_frame(items, spans, float(c["at"]), fps)
        if k is None:
            raise ValueError(f"Ninguna toma muestra el segundo {float(c['at']):.2f} del compuesto {kind}.")
        kw = {key: c[key] for key in ("angle", "x", "y", "whip", "strength") if key in c}
        if kind == "hit":
            end = next(f1 for f0, f1 in spans if f0 <= k < f1)
            edl.events += recipes.hit(k, fps, seed=n, room=end - k, **kw)
        elif kind == "burst":
            kw.pop("angle", None), kw.pop("whip", None)
            edl.events += recipes.burst(k, fps, seed=n, colors=c.get("colors"), **kw)
        else:
            edl.events += recipes.isolate(k, fps, color=c.get("color", "#e02020"),
                                          similarity=float(c.get("similarity", 0.25)),
                                          strength=float(c.get("strength", 1.0)))
        placed.append({"kind": kind, "at": float(c["at"]), "frame": k, "t": round(k / fps, 3)})
    if placed:
        info["placed"] = placed
    if op.lyrics_srt:
        words = [w for w in recipes.words_from_srt(op.lyrics_srt, offset=-op.music_start)
                 if 0 <= w["start"] < spans[-1][1] / fps]
        shots = [{"start": f0, "end": f1, "focus": it.get("focus", (0.5, 0.5)) if it["kind"] == "take" else (0.5, 0.5)}
                 for it, (f0, f1) in zip(items, spans)]
        op.words = recipes.lyric_words(words, shots, fps)
        op.word_style = {"inv_words": "1", **op.word_style}
        info["lyric_words"] = len(op.words)
    return info


# ---------------------------------------------------------------- lyric timing

def _ts(x: float) -> str:
    x = max(0.0, x)
    ms = int(round(x * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_files(bm: dict, op: MangaOptions, folder: str, base: str) -> list[str]:
    """Two .srt with TIMES only (the lyrics are Axl's to write): one cue per
    phrase and one per word, relative to the edit's start."""
    phrases = [p for p in phrases_from(bm, op) if p["b"] > op.music_start]
    words = [w for p in phrases_from(bm, op) for w in p["words"] if w[1] > op.music_start]
    paths = []
    for suffix, rows in (("frases", [(p["a"], p["b"]) for p in phrases]),
                         ("palabra-por-palabra", [(a, max(b, a + MIN_WORD_SRT)) for a, b in words])):
        path = os.path.join(folder, f"{base} - tiempos-letra_{suffix}.srt")
        with open(path, "w", encoding="utf-8") as fh:
            for k, (a, b) in enumerate(rows, 1):
                label = f"♪ frase {k:02d}" if suffix == "frases" else f"♪ {k:03d}"
                fh.write(f"{k}\n{_ts(a - op.music_start)} --> {_ts(b - op.music_start)}\n{label}\n\n")
        paths.append(path)
    return paths
