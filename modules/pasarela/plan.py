"""The pasarela's timeline (pure: no FFmpeg, no files): where each slide and
each clip goes, where the voice pauses and how long the video lasts.

Everything that decides WHEN is somebody else's, reused as is:
  - the marked script against Whisper: studio (`parse_script`, `align`,
    `auto_layers`: the anchors, the `palabras` fallback, the unplaced list);
  - the slide rules (in until the next one, 12 s cap, stick under 2.5 s,
    back-to-back cross-fade): montage's `layers.resolve`;
  - the gaps the rest slide fills, for the report: studio's `fill_rest`.

What is the pasarela's own (the approved standard, Tom's draft 10/11/2026):
  - the clip marks `{clip:id}` of the script. By default the VOICE PAUSES:
    it is cut in the middle of the silence between the last word before the
    mark and the next one, the whole clip goes in with BREATH_S on each side
    and everything after it moves later (`Timeline.final`);
  - optional mode "encima": the clip over the voice (no pause);
  - the cover comes in at second 0;
  - the video lasts the voice plus the pauses plus TAIL_S.

Times: "voice time" is a second of the voice file; "final time" a second of
the finished video. `Timeline.final(v)` turns one into the other.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from modules.montage import layers as L
from modules.studio import processor as studio

LEAD_S = L.LEAD_S           # 0.1 s before its word (montage's rule)
MAX_S = L.MAX_S             # 12 s cap
FADE_IN_S = 0.25
FADE_OUT_S = 0.2
BREATH_S = 0.15             # silence on each side of a clip that pauses the voice
TAIL_S = 0.3                # after the voice ends (Tom: 166.10 + 18.80 + 0.30 = 185.20 s)
EDGE_GAP_S = 0.15           # a clip mark before the first word / after the last one

CLIP_MARK = re.compile(r"\{clip:([A-Za-z0-9_.-]+)\}")
MODES = ("pausa", "encima")


@dataclass
class ClipInfo:
    """What the timeline needs to know of a clip."""
    id: str
    dur: float
    full: bool                  # 9:16 → full screen; otherwise inside the band
    mode: str = "pausa"         # pausa | encima
    after: str | None = None    # the manifest's `despues`: fallback when the script has no mark


@dataclass
class Timeline:
    duration: float
    layers: list[dict]          # slides and clips: {id, kind, start, end, fade_in, fade_out}
    clips: list[dict]           # {id, mode, full, start, end, dur, voice_at}
    pauses: list[dict]          # {id, voice_at, at, dur}
    voice_pieces: list[dict]    # {from, to, at}: the voice cut at the pauses
    rest: list[dict]            # where only the rest slide shows: {start, end}
    unplaced: list[dict]        # slides and clips without a place: {id, kind, reason}
    alignment: dict | None
    unknown_marks: list[str] = field(default_factory=list)

    def final(self, v: float, after: bool = False) -> float:
        return to_final(v, [(p["voice_at"], p["dur"]) for p in self.pauses], after)


def to_final(v: float, pauses: list[tuple[float, float]], after: bool = False) -> float:
    """Voice time → final time. A pause at p moves everything after p; with
    `after`, a point exactly at p too (where the voice resumes)."""
    return v + sum(d for p, d in pauses if p < v or (after and p <= v))


def clip_marks_as_slides(script: str) -> str:
    """The script with `{clip:id}` written as a slide mark: studio's parser
    reads both alike (its words are the same, without the mark's text)."""
    return CLIP_MARK.sub(lambda m: f"⟦lamina:{m.group(1)}⟧", script or "")


HOP_S = 0.01               # the voice's level, every 10 ms (processor.voice_levels)
SILENCE_DB = -55            # true silence (a TTS voice has digital silence between sentences)
ONSET_DB = -40              # where Whisper normally starts a word: the breath before it
PAUSE_MIN_S = 0.25          # a silence this long inside a word is a swallowed pause
PAUSE_LATE_S = 0.4          # …if it begins within this of the word's start


def snap_starts(segments: list[dict], levels, hop: float = HOP_S) -> tuple[list[dict], int]:
    """Whisper sometimes stretches the word that follows a pause back over the
    whole silence, and a slide comes in 0.1 s before its word's start. Measured
    on Tom's voice with the script's names as prompt: «Al» 27.34–28.18 when the
    voice comes back at 27.98, «Y» 100.50 for 101.12 (two slides 0.6 s early).
    A word whose first PAUSE_LATE_S holds PAUSE_MIN_S of true silence starts
    where the sound comes back (ONSET_DB). Only that: elsewhere Whisper starts a
    word with the breath before it, as in the approved video, and it is kept.
    `levels`: dBFS per `hop` of the voice (numpy). Returns (segments, moved)."""
    import numpy as np
    lv = np.asarray(levels, dtype=float)
    need, late = int(round(PAUSE_MIN_S / hop)), int(round(PAUSE_LATE_S / hop))
    moved, out = 0, []
    for seg in segments:
        words = []
        for w in seg.get("words") or []:
            s, e = float(w["start"]), float(w["end"])
            a, b = int(round(s / hop)), min(len(lv), int(round(e / hop)))
            k = a
            while k < min(a + late, b) and lv[k] >= SILENCE_DB:
                k += 1
            run = k
            while run < b and lv[run] < SILENCE_DB:
                run += 1
            if k < a + late and run - k >= need:
                onset = run
                while onset < b and lv[onset] < ONSET_DB:
                    onset += 1
                if onset < b - int(round(0.05 / hop)):
                    w = {**w, "start": round(onset * hop, 3)}
                    moved += 1
            words.append(w)
        out.append({**seg, "words": words})
    return out, moved


def gap_point(tr: list[tuple[str, float, float]], i: int) -> float:
    """The middle of the silence before transcript word i (where the voice is
    cut). Before the first word: EDGE_GAP_S before it; past the last: after it."""
    if not tr:
        return 0.0
    if i <= 0:
        return max(0.0, tr[0][1] - EDGE_GAP_S)
    if i >= len(tr):
        return tr[-1][2] + EDGE_GAP_S
    return (tr[i - 1][2] + tr[i][1]) / 2


def build(segments: list[dict], script: str, manifest: list[dict], clips: dict[str, ClipInfo],
          voice_dur: float) -> Timeline:
    """The whole timeline. `manifest`: the deck's (slides; clip entries and the
    rest slide are told apart by `tipo` and `reposo`); `clips`: every clip
    the video can use, by id (from the manifest and the request)."""
    tr = L.transcript_words(segments or [])
    text = clip_marks_as_slides(script)
    slides = [m for m in manifest if m.get("tipo") != "clip" and not m.get("reposo")]
    slide_ids = {str(m["id"]) for m in slides}
    unplaced: list[dict] = []

    # 1. Slides: studio's rules (marked script → anchors; else `palabras`).
    auto = studio.auto_layers(slides, segments, text)
    voice_t = {}
    for capa in auto["capas"]:
        t = L.find(tr, capa["ancla"]["palabra"], int(capa["ancla"].get("n") or 1))
        if t is not None:
            voice_t[capa["lamina"]] = t
    unplaced += [{"id": i, "kind": "lamina", "reason": "sin marca en el guion ni palabras que coincidan"}
                 for i in auto["unplaced"]]

    # 2. Clips: their mark's transcript word, and the silence before it.
    script_words, marks = studio.parse_script(text)
    mapping, _ = studio.align(script_words, [w for w, _, _ in tr]) if tr and script_words else ([], 0)
    word_of = {}
    for mid, g in marks:
        word_of[mid] = mapping[g] if g < len(mapping) else len(tr)
    unknown = [mid for mid, _ in marks if mid not in slide_ids and mid not in clips]
    placed_clips = []
    for cid, info in clips.items():
        if cid in word_of:
            i = word_of[cid]
        elif info.after and info.after in voice_t:
            # No mark: right before the slide that follows `despues`.
            nxt = sorted((t, s) for s, t in voice_t.items() if t > voice_t[info.after])
            i = _index_at(tr, nxt[0][0]) if nxt else len(tr)
        else:
            unplaced.append({"id": cid, "kind": "clip",
                             "reason": "sin {clip:" + cid + "} en el guion ni «despues» en el manifest"})
            continue
        placed_clips.append((gap_point(tr, i), info))
    placed_clips.sort(key=lambda c: c[0])

    pauses = [(p, info.dur + 2 * BREATH_S) for p, info in placed_clips if info.mode == "pausa"]
    duration = round(to_final(voice_dur, pauses) + TAIL_S, 3)

    # 3. Final times. Slides 0.1 s before their word; the first one (the cover) at 0.
    specs = [{"id": s, "file": s, "start": max(0.0, to_final(t, pauses) - LEAD_S)}
             for s, t in voice_t.items()]
    if specs:
        min(specs, key=lambda s: s["start"])["start"] = 0.0
    clip_rows = []
    for p, info in placed_clips:
        start = to_final(p, pauses) + (BREATH_S if info.mode == "pausa" else 0.0)
        row = {"id": info.id, "mode": info.mode, "full": info.full, "voice_at": round(p, 3),
               "start": round(start, 3), "end": round(min(start + info.dur, duration), 3), "dur": info.dur}
        clip_rows.append(row)
        if info.mode == "pausa":
            # A clip that pauses the voice is part of the band's sequence: the slide
            # before it ends where it starts (montage's rule over clips and slides).
            specs.append({"id": info.id, "file": info.id, "start": row["start"], "end": row["end"]})

    resolved = L.resolve(specs, None, duration, L.Box(), FADE_IN_S, FADE_OUT_S, MAX_S, LEAD_S) if specs else []
    clip_ids = {c["id"] for c in clip_rows}
    layers = [{"id": c.label, "kind": "clip" if c.label in clip_ids else "lamina",
               "start": c.start, "end": c.end, "fade_in": c.fade_in, "fade_out": c.fade_out} for c in resolved]
    _clip_edges(layers)
    if layers and abs(layers[-1]["end"] - duration) < 1e-3:
        layers[-1]["fade_out"] = 0.0        # the last slide stays until the video ends

    rest = [{"start": g["start"], "end": g["end"]}
            for g in studio.fill_rest([], layers, "reposo", {}, duration)]
    pause_rows = [{"id": info.id, "voice_at": round(p, 3), "at": round(to_final(p, pauses), 3),
                   "dur": round(info.dur + 2 * BREATH_S, 3)}
                  for p, info in placed_clips if info.mode == "pausa"]
    cuts = [0.0] + [p for p, _ in pauses] + [voice_dur]
    pieces = [{"from": round(a, 3), "to": round(b, 3), "at": round(to_final(a, pauses, after=True), 3)}
              for a, b in zip(cuts, cuts[1:]) if b > a]
    return Timeline(duration, layers, clip_rows, pause_rows, pieces, rest, unplaced,
                    auto.get("alignment"), unknown)


def _clip_edges(layers: list[dict]) -> None:
    """montage's resolver cross-fades two back-to-back layers by keeping the
    first one FADE_IN_S longer. A clip is not a slide: it is cut, never
    stretched (a slide right before a clip ends where the clip starts, with no
    fade out: the clip covers it), and a slide right after a clip just fades in."""
    for a, b in zip(layers, layers[1:]):
        if "clip" in (a["kind"], b["kind"]) and abs(a["end"] - b["start"] - b["fade_in"]) < 1e-3:
            a["end"] = b["start"]
            a["fade_out"] = 0.0


def _index_at(tr: list[tuple[str, float, float]], t: float) -> int:
    for i, (_, a, _) in enumerate(tr):
        if a >= t - 1e-6:
            return i
    return len(tr)
