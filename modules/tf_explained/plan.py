"""Clock-based pieces for the EDL: a fight is planned in the scoreboard
clock ("26:50.8") and each piece says how long it lasts in the output, which
is the voice's timing (fast where nothing matters, slow or frozen where the
play is)."""
from __future__ import annotations

from dataclasses import dataclass

from modules.editing.edl import Piece, Segment


def parse_clock(text: str | float) -> float:
    """"26:50.8" → 1610.8 (also plain seconds)."""
    if isinstance(text, (int, float)):
        return float(text)
    parts = str(text).strip().split(":")
    try:
        value = 0.0
        for part in parts:
            value = value * 60 + float(part)
    except ValueError:
        raise ValueError(f"Reloj inválido: {text!r} (m:ss o m:ss.f).")
    return round(value, 3)


@dataclass
class Span:
    """A downloaded span of the VOD: source t = clock − offset."""
    path: str
    offset: float

    def t(self, at: str | float) -> float:
        return round(parse_clock(at) - self.offset, 3)

    def run(self, a: str | float, b: str | float, dur: float) -> Piece:
        """Clock a → b in `dur` output seconds (faster or slower than real time)."""
        return Piece(self.t(a), self.t(b), dur)

    def freeze(self, at: str | float, dur: float) -> Piece:
        return Piece(self.t(at), self.t(at), dur)

    def shot(self, vf: str, *pieces: Piece, label: str = "") -> Segment:
        return Segment(self.path, list(pieces), label=label, vf=vf)
