"""When each montage layer comes in and goes out (pure: it does not touch FFmpeg).

A layer comes in at a fixed second (`start`) or on a WORD of the base video's
transcript (`word`, the n-th time it is said). It goes out at `end`, at
`end_word` or, if not given, when the next one comes in.

Rules measured on the hand-made montages of suiza (18/10) and Lux (19/10):
  - it comes in LEAD_S (0.1 s) before its word;
  - without an explicit end it lasts until the next layer, capped at MAX_S
    (12 s); if the gap the cap leaves is under STICK_S (2.5 s), it stretches
    to the next one;
  - two back-to-back layers cross-fade: the new one comes in with its fade ON
    TOP of the previous one, which stays underneath for that fade;
  - otherwise, two layers cannot overlap.

Whisper writes what it hears: numbers as digits ("16") even if the script
says "dieciséis", and with no fixed accents or capitals. Both sides are
compared normalized (no accents, lowercase, no punctuation and spelled-out
numbers turned into digits).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

LEAD_S = 0.1
MAX_S = 12.0
STICK_S = 2.5

# Spanish number words: the transcripts are in Spanish.
_UNITS = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14,
    "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "veintiun": 21, "veintiuno": 21, "veintiuna": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
    "veintinueve": 29, "cien": 100, "mil": 1000,
}
_TENS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
         "ochenta": 80, "noventa": 90}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def tokens(text: str) -> list[str]:
    """Normalized words: lowercase, no accents or punctuation, and spelled-out
    numbers turned into digits ("treinta y dos" → "32")."""
    raw = re.findall(r"[a-z0-9ñ]+", _strip_accents(text.lower()))
    out: list[str] = []
    i = 0
    while i < len(raw):
        w = raw[i]
        if w in _TENS:
            n = _TENS[w]
            if i + 2 < len(raw) and raw[i + 1] == "y" and raw[i + 2] in _UNITS \
                    and _UNITS[raw[i + 2]] < 10:
                n += _UNITS[raw[i + 2]]
                i += 2
            out.append(str(n))
        elif w in _UNITS and w not in ("un", "una"):
            out.append(str(_UNITS[w]))
        else:
            out.append(w)
        i += 1
    return out


def transcript_words(segments: list[dict]) -> list[tuple[str, float, float]]:
    """(token, start, end) of the whole transcript (the segments of subtitles /
    reel_express). A word that normalizes into several tokens (or none)
    splits its time."""
    out = []
    for seg in segments:
        for w in seg.get("words") or []:
            ts = tokens(str(w.get("word", "")))
            if not ts:
                continue
            a, b = float(w["start"]), float(w["end"])
            step = (b - a) / len(ts)
            out += [(t, a + k * step, a + (k + 1) * step) for k, t in enumerate(ts)]
    return out


def find(transcript: list[tuple[str, float, float]], phrase: str, n: int = 1) -> float | None:
    """Second where the n-th occurrence of `phrase` starts (None if absent)."""
    wanted = tokens(phrase)
    if not wanted:
        return None
    seen = 0
    words = [t for t, _, _ in transcript]
    for i in range(len(words) - len(wanted) + 1):
        if words[i:i + len(wanted)] == wanted:
            seen += 1
            if seen == n:
                return transcript[i][1]
    return None


@dataclass
class Box:
    """Where the layer goes in the 1080x1920 frame."""
    width: int = 720
    x: str = "center"            # center | left | right
    bottom: int = 0              # bottom margin in px
    mode: str = "box"            # box | band
    y: int = 1240                # band: where it starts
    height: int = 680            # band: height


@dataclass
class Layer:
    file: str
    start: float
    end: float
    box: Box = field(default_factory=Box)
    clip_dur: float | None = None   # None = still image
    fade_in: float = 0.25
    fade_out: float = 0.2
    label: str = ""
    # Forced decoder: FFmpeg's built-in VP9/VP8 decoders drop the alpha of a
    # WebM (yuv420p, alpha 255: the layer covers the video as a rectangle);
    # libvpx reads it (yuva420p). Set by processor.prepare().
    decoder: str | None = None


def resolve(specs: list[dict], segments: list[dict] | None, video_dur: float,
            default_box: Box, fade_in: float = 0.25, fade_out: float = 0.2,
            max_s: float = MAX_S, lead: float = LEAD_S) -> list[Layer]:
    """The layers with their times resolved. `specs`: [{file, start | word
    [, n], end | end_word [, end_n], box}] (a partial box overrides the
    default one). Readable ValueError if a word is missing or layers overlap."""
    transcript = transcript_words(segments or [])
    starts = []
    for k, s in enumerate(specs, 1):
        if s.get("start") not in (None, ""):
            starts.append(float(s["start"]))
        elif s.get("word"):
            if not transcript:
                raise ValueError(f"La capa {k} entra en una palabra y no hay transcripción.")
            t = find(transcript, str(s["word"]), int(s.get("n") or 1))
            if t is None:
                raise ValueError(f"La capa {k}: no encontré «{s['word']}» (aparición {s.get('n') or 1}) "
                                 "en la transcripción. Revisá cómo la escribió Whisper.")
            starts.append(max(0.0, t - lead))
        else:
            raise ValueError(f"La capa {k} no dice cuándo entra: falta 'start' o 'word'.")
    order = sorted(range(len(specs)), key=lambda i: starts[i])
    layers: list[Layer] = []
    for pos, i in enumerate(order):
        s = specs[i]
        start = starts[i]
        nxt = starts[order[pos + 1]] if pos + 1 < len(order) else video_dur
        if s.get("end") not in (None, ""):
            end = float(s["end"])
        elif s.get("end_word"):
            t = find(transcript, str(s["end_word"]), int(s.get("end_n") or 1))
            if t is None:
                raise ValueError(f"La capa {i + 1}: no encontré «{s['end_word']}» para su final.")
            end = t
        else:
            end = min(nxt, start + max_s)
            if nxt - end < STICK_S:
                end = nxt
        end = min(end, video_dur)
        if end - start < 0.2:
            raise ValueError(f"La capa {i + 1} dura menos de 0.2 s ({start:.2f} → {end:.2f}).")
        box = Box(**{**default_box.__dict__, **(s.get("box") or {})})
        layers.append(Layer(str(s["file"]), round(start, 3), round(end, 3), box,
                            fade_in=fade_in, fade_out=fade_out, label=str(s.get("id") or "")))
    for a, b in zip(layers, layers[1:]):
        if b.start < a.end - 1e-3:
            raise ValueError(f"Las capas «{a.file}» y «{b.file}» se enciman "
                             f"({a.start:.2f}–{a.end:.2f} y {b.start:.2f}–{b.end:.2f}).")
        if abs(b.start - a.end) < 1e-3:
            # Back to back: the previous one stays underneath while the new one fades in.
            a.end = round(a.end + b.fade_in, 3)
            a.fade_out = 0.0
    return layers
