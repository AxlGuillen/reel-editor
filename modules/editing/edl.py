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
EVENT_KINDS = ("flash", "zoom", "shake", "rgb")
TEXT_STYLES = ("word", "kill", "multikill", "title", "sub")


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

    @property
    def dur(self) -> float:
        return sum(p.dur for p in self.pieces)


@dataclass
class Event:
    """An accent in the output: flash, zoom punch, shake or split RGB."""
    t: float
    kind: str
    intensity: float = 1.0        # 0–1.5 (multiplied by the global intensity)
    dur: float = 0.18


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

    def validate(self) -> None:
        """Readable ValueError if the EDL cannot be rendered."""
        if not self.segments:
            raise ValueError("La edición no tiene ningún plano.")
        for i, s in enumerate(self.segments, 1):
            if not s.pieces:
                raise ValueError(f"El plano {i} no tiene piezas.")
            for p in s.pieces:
                if p.dur <= 0:
                    raise ValueError(f"El plano {i} tiene una pieza sin duración.")
                if p.end < p.start - FROZEN_EPS:
                    raise ValueError(f"El plano {i} tiene una pieza al revés ({p.start} → {p.end}).")
        for e in self.events:
            if e.kind not in EVENT_KINDS:
                raise ValueError(f"Evento desconocido: {e.kind!r}.")
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
            "events": [{"t": round(e.t, 3), "kind": e.kind, "intensity": round(e.intensity, 2)}
                       for e in self.events],
            "texts": [{"start": round(t.start, 3), "end": round(t.end, 3), "text": t.text,
                       "style": t.style, "sub": t.sub} for t in self.texts],
            "segments": [{"label": s.label, "dur": round(s.dur, 3),
                          "pieces": [[round(p.start, 3), round(p.end, 3), round(p.dur, 3)]
                                     for p in s.pieces]} for s in self.segments],
        }
