"""Edit decision list (EDL): which span of which file goes at which moment of
the output, at what speed, and which accents and titles it carries.

The planners build it (competitivo/edit.py, anime_edit/plan.py) as pure
functions and render.py executes it. No planner touches FFmpeg; the render
decides nothing: that way each side is tested on its own.

Units: seconds. The times of `Event` and `Text` are OUTPUT times; those of
`Piece` are times in its source file.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FROZEN_EPS = 1e-3             # a piece with less source than this is a frozen frame
# flash, zoom punch, shake, split RGB; and the camera moves of the "acts" edit:
# push (slow push-in to 1+level over `dur`), hold (zoom held at 1+level: one
# step of a stair zoom), whip (a fast zoom punch with horizontal motion blur:
# the transition between fights) and black (the frame goes black: the cut of
# silence before the drop).
EVENT_KINDS = ("flash", "zoom", "shake", "rgb", "push", "hold", "whip", "black")
TEXT_STYLES = ("word", "kill", "multikill", "title", "sub")
TINT_KINDS = ("", "bw")           # Segment.tint (the filters are in effects.TINTS)


@dataclass
class Piece:
    """A contiguous span of the source at constant speed."""
    start: float                  # s in the source
    end: float                    # == start → frozen frame during `dur`
    dur: float                    # s in the output

    @property
    def frozen(self) -> bool:
        return self.end - self.start < FROZEN_EPS

    @property
    def speed(self) -> float:
        return 0.0 if self.frozen else (self.end - self.start) / self.dur


@dataclass
class Segment:
    """A shot: one file and its pieces, one after another in the output."""
    source: str
    pieces: list[Piece]
    label: str = ""
    # Geometry filter of this shot (layout, crop), set by the planner; empty =
    # the render's default scaling.
    vf: str = ""
    # Needs interpolation (RIFE) because some piece is very slow.
    interpolate: bool = False
    # A look of its own on top of the edit's (effects.TINTS): "" or "bw".
    tint: str = ""

    @property
    def dur(self) -> float:
        return sum(p.dur for p in self.pieces)


@dataclass
class Event:
    """An accent in the output (see EVENT_KINDS)."""
    t: float
    kind: str
    intensity: float = 1.0        # 0–1.5 (multiplied by the global intensity)
    dur: float = 0.18
    level: float = 0.0            # push / hold: extra zoom (0.12 → 1.12×)
    # Where the zoom aims (zoom, push, hold), as a fraction of the frame; the
    # center by default. The camera goes back to the center when it ends.
    x: float = 0.5
    y: float = 0.5


@dataclass
class Text:
    start: float
    end: float
    text: str
    style: str = "word"
    sub: str = ""                 # second line (the champion under the player)
    color: str = ""               # #RRGGBB; empty = the style's color


@dataclass
class Edl:
    segments: list[Segment]
    width: int = 1080
    height: int = 1920
    fps: int = 60
    events: list[Event] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)
    music: str | None = None
    music_start: float = 0.0      # s of the song where the output starts
    music_volume: float = 1.0
    original_volume: float = 0.0  # 0 = music only
    # Volume automation in OUTPUT time: [(t, gain)] keyframes, linear between
    # them and held before the first / after the last. Empty = the fixed
    # volumes above. With them, music_volume / original_volume are ignored.
    music_gain: list = field(default_factory=list)
    original_gain: list = field(default_factory=list)
    # Burned with the subtitles "invert" layout (white, difference blend):
    # [{word, start, end}] in output time and the SubtitlesParams fields
    # (inv_size, inv_y, inv_direction, inv_words, font).
    subtitle_words: list = field(default_factory=list)
    subtitle_style: dict = field(default_factory=dict)
    subtitle_until: float | None = None   # nothing of them on screen after this
    # [(a, b)] output windows where they may be (Ibai's real-time stretches);
    # empty = anywhere. A word stays at most until the end of its window.
    subtitle_windows: list = field(default_factory=list)
    look: str = "neutral"
    lut: str = ""                 # file in assets/luts ("" = no LUT)
    glow: float = 0.0             # 0–1
    vignette: bool = False
    watermark: str | None = None  # watermark PNG
    watermark_height: int = 150
    fade_out: float = 0.6         # music fade at the end

    @property
    def dur(self) -> float:
        return sum(s.dur for s in self.segments)

    @property
    def uses_original(self) -> bool:
        """Whether the sources' own audio is heard at some point."""
        if self.original_gain:
            return any(g > 0 for _, g in self.original_gain)
        return self.original_volume > 0

    def validate(self) -> None:
        """Readable ValueError if the EDL cannot be rendered."""
        if not self.segments:
            raise ValueError("La edición no tiene ningún plano.")
        for i, s in enumerate(self.segments, 1):
            if not s.pieces:
                raise ValueError(f"El plano {i} no tiene piezas.")
            if s.tint not in TINT_KINDS:
                raise ValueError(f"Tono desconocido en el plano {i}: {s.tint!r}.")
            for p in s.pieces:
                if p.dur <= 0:
                    raise ValueError(f"El plano {i} tiene una pieza sin duración.")
                if p.end < p.start - FROZEN_EPS:
                    raise ValueError(f"El plano {i} tiene una pieza al revés ({p.start} → {p.end}).")
        for e in self.events:
            if e.kind not in EVENT_KINDS:
                raise ValueError(f"Evento desconocido: {e.kind!r}.")
        for name, keys in (("música", self.music_gain), ("audio original", self.original_gain)):
            if any(b[0] < a[0] for a, b in zip(keys, keys[1:])):
                raise ValueError(f"La automatización de volumen de la {name} no está en orden.")
            if any(g < 0 for _, g in keys):
                raise ValueError(f"La automatización de volumen de la {name} tiene un volumen negativo.")
        for t in self.texts:
            if t.style not in TEXT_STYLES:
                raise ValueError(f"Estilo de texto desconocido: {t.style!r}.")
            if t.end <= t.start:
                raise ValueError(f"El texto «{t.text}» no dura nada.")

    def to_dict(self) -> dict:
        """Summary for the status and info.json (without repeated long paths)."""
        return {
            "dur": round(self.dur, 3), "shots": len(self.segments), "fps": self.fps,
            "width": self.width, "height": self.height, "music_start": round(self.music_start, 3),
            "events": [{"t": round(e.t, 3), "kind": e.kind, "intensity": round(e.intensity, 2),
                        **({"dur": round(e.dur, 3)} if e.kind in ("push", "hold") else {}),
                        **({"level": round(e.level, 3)} if e.level else {}),
                        **({"x": round(e.x, 3), "y": round(e.y, 3)} if (e.x, e.y) != (0.5, 0.5) else {})}
                       for e in self.events],
            "music_gain": [[round(t, 3), round(g, 3)] for t, g in self.music_gain],
            "original_gain": [[round(t, 3), round(g, 3)] for t, g in self.original_gain],
            "subtitle_words": len(self.subtitle_words),
            "texts": [{"start": round(t.start, 3), "end": round(t.end, 3), "text": t.text,
                       "style": t.style, "sub": t.sub} for t in self.texts],
            "segments": [{"label": s.label, "dur": round(s.dur, 3), **({"tint": s.tint} if s.tint else {}),
                          "pieces": [[round(p.start, 3), round(p.end, 3), round(p.dur, 3)]
                                     for p in s.pieces]} for s in self.segments],
        }
