"""Hit–beat fit: where a source frame lands in the output, and how to retime a
take so its hit lands on a beat (pure).

Mapping. With Segment.exact_phase the pass-1 retime puts source time s of a
piece at output time (s − start) / speed, and FFmpeg's fps filter shows a
source frame from the output frame round((s − start) / speed · fps) on. So the
output frame of a source instant inside a piece is
    F_piece + round((s − start) / speed · fps).
Pieces here always last a whole number of frames, so F_piece is exact.

Fit. timing.hit_ramp spreads [start, hit] over [0, hit_out] and [hit, end]
over the rest; the hit sits on the boundary, a whole frame. Speeds are kept in
[V_MIN, V_MAX]: the half AFTER the hit is clamped by trimming its end
(timing.clamp_speeds); the half INTO the hit by moving its start, so the hit
never moves.
"""
from __future__ import annotations

import math

from modules.editing import timing
from modules.editing.edl import Piece

V_MIN, V_MAX = 0.4, 2.5


def out_frame(pieces: list[Piece], f0: int, fps: int, s: float, src_fps: float | None = None) -> int | None:
    """Output frame where source instant `s` shows up (None if no piece covers it).
    `src_fps`: the source's frame rate. FFmpeg's fps filter counts from the
    piece's FIRST source frame (the first at or after its start), so a piece
    starting between two source frames shifts by round(offset · fps / speed);
    measured against a real render (tests/anime_edit/test_blocks.py)."""
    f = f0
    for k, p in enumerate(pieces):
        n = round(p.dur * fps)
        last = k == len(pieces) - 1
        # a boundary belongs to the piece that STARTS there (the hit of a ramp
        # is the first frame of its second half)
        if not p.frozen and p.start - 1e-6 <= s and (s < p.end - 1e-6 or (last and s <= p.end + 1e-6)):
            r0 = 0
            if src_fps:
                first = math.ceil(p.start * src_fps - 1e-6) / src_fps
                r0 = round((first - p.start) / p.speed * fps)
            return f + min(n - 1, max(0, round((s - p.start) / p.speed * fps) - r0))
        f += n
    return None


def fit_hit(start: float, n: int, speed: float, hit: float, at: int, fps: int,
            vmin: float = V_MIN, vmax: float = V_MAX) -> list[Piece]:
    """Pieces for a take of `n` frames from source `start` at `speed` whose
    source instant `hit` lands on frame `at` of the take (1 ≤ at ≤ n − 1)."""
    if not 1 <= at <= n - 1:
        raise ValueError("El golpe tiene que caer dentro de la toma.")
    dur, hit_out = n / fps, at / fps
    end = max(hit + 1e-3, start + dur * speed)
    first, second = timing.hit_ramp(min(start, hit), hit, end, dur, hit_out)
    # into the hit: clamped by moving its START; after it: by its END (what
    # timing.clamp_speeds does, also for a half left with almost no source)
    v1 = min(vmax, max(vmin, (hit - min(start, hit)) / hit_out))
    v2 = min(vmax, max(vmin, (end - hit) / second.dur))
    return [Piece(hit - v1 * hit_out, hit, hit_out), Piece(hit, hit + v2 * second.dur, second.dur)]


def nearest_beat(beat_frames: list[int], f0: int, f1: int, natural: int) -> int | None:
    """The beat frame inside the take (not its first or last frame) nearest to
    where the hit falls at constant speed; None if no beat is inside."""
    inside = [b for b in beat_frames if f0 < b < f1 - 1]
    return min(inside, key=lambda b: abs(b - natural)) if inside else None
