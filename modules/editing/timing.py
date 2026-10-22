"""Pure planning tools: spread a span of the source over an output time with a
speed curve, and snap everything to whole frames.

What makes an edit "hit" is the cut or the accent landing on the beat to the
frame. Planners work in seconds; `quantize()` rounds every piece to the frame
while keeping the CUMULATIVE time exact (each edge lands on the frame closest
to its ideal time), so the error never adds up: a hit planned at second
31.250 at 60 fps lands on frame 1875, not 3 frames later from accumulated
rounding.
"""
from __future__ import annotations

from modules.editing.edl import FROZEN_EPS, Edl, Piece

MIN_SPEED, MAX_SPEED = 0.25, 4.0

# Speed curves: [(fraction of the output, relative speed)]. They are scaled
# together so the source fits exactly in the requested duration.
PROFILES = {
    "constant": [(1.0, 1.0)],
    # Velocity edit: slow right after the hit, fast toward the next one.
    "velocity": [(0.35, 0.35), (0.65, 1.6)],
    # Arrive fast at the hit and brake hard at the end of the span.
    "brake": [(0.7, 1.5), (0.3, 0.4)],
    # Gentle slow motion in the middle of the span.
    "breathe": [(0.25, 1.2), (0.5, 0.5), (0.25, 1.2)],
}


def ramp(start: float, end: float, dur: float, profile: str | list = "constant") -> list[Piece]:
    """Spreads the source [start, end] over `dur` s of output following `profile`.

    The relative speeds are multiplied by one factor k such that the source
    used is exactly end − start. Without source (start == end) it is a freeze
    of `dur`."""
    if dur <= 0:
        return []
    src = end - start
    if src < FROZEN_EPS:
        return [Piece(start, start, dur)]
    curve = PROFILES[profile] if isinstance(profile, str) else profile
    weight = sum(f * v for f, v in curve)
    k = src / (dur * weight)
    pieces, t = [], start
    for f, v in curve:
        d = dur * f
        if d <= 0:
            continue
        s = d * v * k
        pieces.append(Piece(t, t + s, d))
        t += s
    pieces[-1].end = end
    return pieces


def impact(start: float, end: float, dur: float, freeze: float = 0.05,
           slow: float = 0.25, slow_speed: float = 0.4) -> list[Piece]:
    """The hit: freezes `freeze` s at `start`, continues `slow` s in slow
    motion and the rest goes at whatever speed is needed to reach `end`.
    The freeze never exceeds 30 % of the span nor the slow motion 60 % of
    what is left."""
    if dur <= 0:
        return []
    freeze = min(freeze, dur * 0.3)
    slow = min(slow, max(0.0, dur - freeze) * 0.6)
    rest = dur - freeze - slow
    src = max(0.0, end - start)
    src_slow = min(slow * slow_speed, src * 0.6)
    pieces = []
    if freeze > 0:
        pieces.append(Piece(start, start, freeze))
    t = start
    if slow > 0 and src_slow > FROZEN_EPS:
        pieces.append(Piece(t, t + src_slow, slow))
        t += src_slow
    elif slow > 0:
        rest += slow
    if rest > 0:
        pieces.append(Piece(t, end if end > t else t, rest))
    return pieces


def clamp_speeds(pieces: list[Piece], vmin: float = MIN_SPEED, vmax: float = MAX_SPEED) -> list[Piece]:
    """No piece slower than vmin nor faster than vmax: the SOURCE is trimmed
    (not the duration), so the output time — the rhythm — does not change.
    A piece that is too fast uses less source (skips a bit) and one that is
    too slow runs a bit past its end: the planner makes sure that source
    exists."""
    out = []
    for p in pieces:
        if p.frozen:
            out.append(p)
            continue
        v = p.speed
        if v > vmax:
            out.append(Piece(p.start, p.start + p.dur * vmax, p.dur))
        elif v < vmin:
            out.append(Piece(p.start, p.start + p.dur * vmin, p.dur))
        else:
            out.append(p)
    return out


def quantize(edl: Edl) -> None:
    """Each piece edge to the frame closest to its ideal cumulative time.
    Pieces left with 0 frames are dropped (and so are empty segments)."""
    fps = edl.fps
    t = 0.0
    done = 0                                  # frames already assigned
    segments = []
    for seg in edl.segments:
        pieces = []
        for p in seg.pieces:
            t += p.dur
            end_frame = round(t * fps)
            n = end_frame - done
            if n <= 0:
                continue
            done = end_frame
            new_dur = n / fps
            if not p.frozen:
                # the source scales in proportion: the speed does not change
                p = Piece(p.start, p.start + (p.end - p.start) * new_dur / p.dur, new_dur)
            else:
                p = Piece(p.start, p.start, new_dur)
            pieces.append(p)
        if pieces:
            seg.pieces = pieces
            segments.append(seg)
    edl.segments = segments


def frame_count(edl: Edl) -> int:
    return round(edl.dur * edl.fps)
