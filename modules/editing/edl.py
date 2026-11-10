"""Edit decision list (EDL): which span of which file goes at which moment of
the output, at what speed, and which accents and titles it carries.

The planners build it (competitivo/edit.py, anime_edit/plan.py) as pure
functions and render.py executes it. No planner touches FFmpeg; the render
decides nothing: that way each side is tested on its own.

Units: seconds. The times of `Event` and `Text` are OUTPUT times; those of
`Piece` are times in its source file.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

FROZEN_EPS = 1e-3             # a piece with less source than this is a frozen frame
# flash, zoom punch, shake, split RGB; and the camera moves of the "acts" edit:
# push (slow push-in to 1+level over `dur`), hold (zoom held at 1+level: one
# step of a stair zoom), whip (a fast zoom punch with horizontal motion blur:
# the transition between fights) and black (the frame goes black: the cut of
# silence before the drop); panel (a manga panel over the frozen frame: the
# frame darkens to ×dim and the panel enters from_scale→1 in `enter` s and keeps
# growing by `drift` until it ends, fitted in PANEL_BOX of the frame).
# The effects bank (phase 3) adds invert (the negative for `dur`), strobe
# (white every `every` frames for `dur`) and glow (a bloom pulse).
# follow (phase 5): an image that follows a tracked point (motion.subject),
# `track` = [[t, x, y, w], …] in output seconds and px.
# Effects bank 2: speedlines (anime speed lines toward x/y or along `angle`),
# impactframe (the frame as a high-contrast b/w, inverted or posterized
# drawing), spin (the frame turns `degrees` with mirrored borders), zoomblur
# (radial blur from the center), glitch (blocks, vhs or flicker), echo
# (ghost trail of the last frames), grain, satpulse (saturation swell),
# overlay (a light leak / embers file blended over), lens (barrel distortion
# + radial chromatic aberration) and tilt (a 3D perspective tilt).
# Composites (editing/recipes.py) add freeze (the frame at t held for `dur`,
# in output time: what follows is skipped, not delayed), duotone (the frame as
# a two-color gradient map, `colors` = [dark, light]), isolate (everything gray
# but the hue of colors[0], within `similarity`; the color comes back over the
# last `fade` s), glints (4-point star sparkles, new ones every frame, around
# x/y) and blur (a directional blur along `angle`, `blur` px of a 1080 frame).
EVENT_KINDS = ("flash", "zoom", "shake", "rgb", "push", "hold", "whip", "black", "panel",
               "invert", "strobe", "glow", "follow",
               "speedlines", "impactframe", "spin", "zoomblur", "glitch", "echo", "grain", "satpulse",
               "overlay", "lens", "tilt",
               "freeze", "duotone", "isolate", "glints", "blur")
EVENT_STYLES = {"speedlines": ("radial", "parallel"), "impactframe": ("bw", "inv", "poster"),
                "glitch": ("blocks", "vhs", "flicker"), "overlay": ("screen", "add", "normal"),
                "tilt": ("left", "right", "up", "down")}
COMPOSITE_KINDS = ("freeze", "duotone", "isolate", "glints", "blur")
HEX = re.compile(r"#[0-9A-Fa-f]{6}")
SPLIT_LAYOUTS = ("top_bottom", "left_right", "diagonal")
FLASH_SHAPES = ("pulse", "mix")   # pulse: brightness that decays · mix: a flat mix toward white
PANEL_BOX = (1040 / 1080, 1700 / 1920)   # the panel fits in this fraction of the frame (rules v1.0)
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
    # Source time maps to output time exactly (a piece starting at 1127.95 s
    # puts that instant at its output start, even if the first source frame
    # is 0.01 s later). By default the first frame at or after the start goes
    # at 0, which shifts the frame picking of the fps conversion by up to a
    # source frame. The manga edit (edit3.py) used exact time.
    exact_phase: bool = False
    # Directional motion blur over the whole shot, in source frames:
    # {"angle": deg (0 right, 90 up), "amount": px of blur in a 1920-wide frame}.
    mblur: dict | None = None
    # With RIFE: drop the repeated drawings (anime on twos/threes) before
    # interpolating, so the invented frames move between real drawings.
    dedupe: bool = False
    # Split screen: a second source shown at the same time, {"source": path,
    # "start": s (the instant shown at the shot's start), "layout":
    # top_bottom | left_right | diagonal}. Both run at the shot's speed.
    split: dict | None = None

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
    # zoom / hold: it starts moving this long BEFORE t (0 = the default quick attack).
    attack: float = 0.0
    # flash: "pulse" (the default) or "mix" (the frame mixed with white by
    # `intensity` for the whole `dur`: the manga edit's 55 % for 2 frames).
    shape: str = "pulse"
    # shake / whip: the direction of the hit (0° right, 90° up; motion map's
    # impact.angle). None = the old behavior (shake in both axes, whip
    # horizontal). decay: power of the shake's fall (2 = quadratic).
    angle: float | None = None
    decay: float = 2.0
    every: int = 2                # strobe: one white frame every N
    # panel: the image and how it enters (see EVENT_KINDS).
    image: str = ""
    dim: float = 0.62
    enter: float = 0.12
    from_scale: float = 0.93
    drift: float = 0.035
    # follow: the path (output s, px) and how it shows
    track: list = field(default_factory=list)
    opacity: float = 0.93
    fade: float = 0.12
    # effects bank 2 (see EVENT_STYLES): the variant, a shake's blur along its
    # axis (px in a 1920-wide frame), a spin's turn, a lens' radial chromatic
    # aberration (fraction of the frame) and a seed for the random looks.
    style: str = ""
    blur: float = 0.0
    degrees: float = 360.0
    ca: float = 0.0
    seed: int = 0
    # composites: duotone [dark, light] / isolate [kept color], as #RRGGBB
    colors: list = field(default_factory=list)
    similarity: float = 0.25      # isolate: how far from colors[0] still counts (colorhold, 0–1)


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
    fade_out: float = 0.6         # music fade at the end (0 = the song's own ending)
    music_fade_in: float = 0.0    # a few ms so the first sample does not click
    video_fade_out: float = 0.0   # fade to black over the last N s of the picture

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
            if s.split is not None:
                if not os.path.isfile(str(s.split.get("source", ""))):
                    raise ValueError(f"No existe la segunda fuente de la pantalla dividida del plano {i}.")
                if s.split.get("layout", "top_bottom") not in SPLIT_LAYOUTS:
                    raise ValueError(f"Pantalla dividida del plano {i}: layout {s.split.get('layout')!r}. "
                                     f"Opciones: {', '.join(SPLIT_LAYOUTS)}.")
            if s.mblur is not None and not (isinstance(s.mblur, dict) and 0 < float(s.mblur.get("amount", 0)) <= 200):
                raise ValueError(f"El desenfoque de movimiento del plano {i} es {{angle, amount (1–200 px)}}.")
            for p in s.pieces:
                if p.dur <= 0:
                    raise ValueError(f"El plano {i} tiene una pieza sin duración.")
                if p.end < p.start - FROZEN_EPS:
                    raise ValueError(f"El plano {i} tiene una pieza al revés ({p.start} → {p.end}).")
        for e in self.events:
            if e.kind not in EVENT_KINDS:
                raise ValueError(f"Evento desconocido: {e.kind!r}.")
            if e.kind == "flash" and e.shape not in FLASH_SHAPES:
                raise ValueError(f"Forma de destello desconocida: {e.shape!r}.")
            if e.kind in COMPOSITE_KINDS and e.dur <= 0:
                raise ValueError(f"El efecto {e.kind} en {e.t:.2f} s no dura nada.")
            if e.colors and not all(isinstance(c, str) and HEX.fullmatch(c) for c in e.colors):
                raise ValueError(f"Los colores del efecto {e.kind} en {e.t:.2f} s van como #RRGGBB.")
            if e.kind == "duotone" and e.colors and len(e.colors) != 2:
                raise ValueError("Un duotono lleva dos colores: [oscuro, claro].")
            if e.kind == "isolate" and not 0 < e.similarity <= 1:
                raise ValueError("La similitud del aislamiento de color va entre 0 y 1.")
            if e.kind in EVENT_STYLES and e.style and e.style not in EVENT_STYLES[e.kind]:
                raise ValueError(f"Estilo desconocido para {e.kind}: {e.style!r}. "
                                 f"Opciones: {', '.join(EVENT_STYLES[e.kind])}.")
            if e.kind in ("speedlines", "impactframe", "spin", "zoomblur", "glitch", "echo", "grain",
                          "satpulse", "overlay", "lens", "tilt") and e.dur <= 0:
                raise ValueError(f"El evento {e.kind} en {e.t:.2f} s no dura nada.")
            if e.kind == "overlay" and (not e.image or not os.path.isfile(e.image)):
                raise ValueError(f"No existe el archivo del overlay: {e.image or '(vacío)'}")
            if e.kind == "strobe" and not 2 <= e.every <= 8:
                raise ValueError("El estrobo va de un cuadro blanco cada 2 a cada 8 ('every').")
            if e.kind in ("invert", "strobe", "glow") and e.dur <= 0:
                raise ValueError(f"El evento {e.kind} en {e.t:.2f} s no dura nada.")
            if e.kind == "follow":
                if not e.image or not os.path.isfile(e.image):
                    raise ValueError(f"No existe la imagen que sigue al personaje: {e.image or '(vacía)'}")
                if len(e.track) < 1 or any(len(p) != 4 for p in e.track) or e.dur <= 0:
                    raise ValueError(f"El seguimiento en {e.t:.2f} s necesita track [[t, x, y, w], …] y duración.")
            if e.kind == "panel":
                if not e.image or not os.path.isfile(e.image):
                    raise ValueError(f"No existe la imagen del panel: {e.image or '(vacía)'}")
                if e.dur <= 0 or not 0 < e.from_scale <= 1.5 or not 0 <= e.dim <= 1:
                    raise ValueError(f"Panel en {e.t:.2f} s con parámetros fuera de rango.")
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
                        **({"x": round(e.x, 3), "y": round(e.y, 3)} if (e.x, e.y) != (0.5, 0.5) else {}),
                        **({"shape": e.shape, "dur": round(e.dur, 3)} if e.kind == "flash" and e.shape != "pulse"
                           else {}),
                        **({"image": os.path.basename(e.image), "dur": round(e.dur, 3)}
                           if e.kind in ("panel", "follow") else {}),
                        **({"track_points": len(e.track)} if e.kind == "follow" else {}),
                        **({"angle": round(e.angle, 1)} if e.angle is not None else {}),
                        **({"dur": round(e.dur, 3)} if e.kind in ("invert", "strobe", "glow") else {}),
                        **({"every": e.every} if e.kind == "strobe" else {}),
                        **({"style": e.style} if e.style else {}),
                        **({"dur": round(e.dur, 3)} if e.kind in ("speedlines", "impactframe", "spin", "zoomblur",
                                                                    "glitch", "echo", "grain", "satpulse",
                                                                    "overlay", "lens", "tilt") + COMPOSITE_KINDS
                           else {}),
                        **({"colors": list(e.colors)} if e.colors else {})}
                       for e in self.events],
            **({"video_fade_out": self.video_fade_out} if self.video_fade_out else {}),
            "music_gain": [[round(t, 3), round(g, 3)] for t, g in self.music_gain],
            "original_gain": [[round(t, 3), round(g, 3)] for t, g in self.original_gain],
            "subtitle_words": len(self.subtitle_words),
            "texts": [{"start": round(t.start, 3), "end": round(t.end, 3), "text": t.text,
                       "style": t.style, "sub": t.sub} for t in self.texts],
            "segments": [{"label": s.label, "dur": round(s.dur, 3), **({"tint": s.tint} if s.tint else {}),
                          "pieces": [[round(p.start, 3), round(p.end, 3), round(p.dur, 3)]
                                     for p in s.pieces]} for s in self.segments],
        }
