"""Variants of an edit: the same footage and the same song, a different rhythm.

They come from the analysis of the reference edit (Levi, 2.2 M views,
22/04/2026): median shot of 0.43 s, not quantized to the grid (cuts follow
the LYRICS and the anime's own hits), a bridge without text before the last
chorus and a heavily saturated golden-teal grade. Each preset decides:

  cuts        "beats" (every `step` beats per section) or "words" (on every
              word of the lyrics, like the reference)
  step        beats per shot in each section type (0.5 = half a beat)
  profile     speed curve inside each shot (editing/timing.PROFILES)
  accents     where flash, zoom, shake and RGB go
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Preset:
    description: str
    cuts: str = "beats"                   # beats | words
    step: dict = field(default_factory=dict)
    buildup_step: float = 0.5             # in the buildups to the drop
    profile: str = "constant"
    speed: float = 1.0
    flash: str | None = None              # chorus_bars | cuts | drop
    zoom: str | None = None               # chorus_cuts | chorus_beats
    shake: str | None = None              # chorus_hits | drop
    rgb: str | None = None                # drop | chorus_cuts
    text: str = "lyrics"                  # lyrics | off
    look: str = "golden"
    glow: float = 0.3


PRESETS: dict[str, Preset] = {
    "impact": Preset(
        "Corte por beat en el coro y cada 2 en el resto, medio beat en la subida, el plano "
        "héroe congelado y en cámara lenta en el drop. Flash en los compases del coro.",
        step={"intro": 2, "verse": 2, "bridge": 4, "chorus": 1, "outro": 2}, buildup_step=0.5,
        flash="chorus_bars", zoom="chorus_cuts", shake="chorus_hits", rgb="drop"),
    "velocity": Preset(
        "Velocity edit: cada plano arranca en cámara lenta justo en el beat y acelera hacia "
        "el siguiente. Flash en cada corte y zoom en cada beat del coro.",
        step={"intro": 4, "verse": 2, "bridge": 4, "chorus": 2, "outro": 4}, buildup_step=1,
        profile="velocity", speed=1.15, flash="cuts", zoom="chorus_beats", shake="drop",
        rgb="chorus_cuts", look="vivid", glow=0.25),
    "cinematic": Preset(
        "Planos largos que respiran (cámara lenta en el medio), sin sacudidas, look de "
        "película y sin letra. Para trailers y tributos.",
        step={"intro": 4, "verse": 4, "bridge": 8, "chorus": 2, "outro": 4}, buildup_step=2,
        profile="breathe", speed=0.9, flash="drop", text="off", look="film", glow=0.15),
    "lyrics": Preset(
        "Como el edit de referencia: corta en cada palabra cantada, con la palabra gigante "
        "en el centro y el héroe en el drop.",
        cuts="words", step={"intro": 2, "verse": 2, "bridge": 4, "chorus": 2, "outro": 2},
        buildup_step=1, flash="drop", zoom="chorus_cuts", shake="chorus_hits", rgb="drop"),
}


def listing() -> list[dict]:
    return [{"id": k, "description": p.description} for k, p in PRESETS.items()]
