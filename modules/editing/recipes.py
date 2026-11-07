"""Composite effects: recipes that stack the bank's primitives on exact frames.

What the top anime edits on TikTok repeat (research of 07/11/2026, the
"Investigacion efectos" report): every composite is a list of Events of the
EDL, so the planner adds them like any other effect and the render does not
change. Times are output frames (k / fps); a recipe never moves a cut.

  blur_cuts   C2, the standard transition: the last frame of a shot blurred
              (radial or along the hit) and overexposed, the first of the next
              one half as much; every few cuts an inverted or posterized frame.
  hit         C1, the full hit: additive flash, freeze with a push-in, color
              split, a shake along the hit, radial speed lines, and a whip to
              the next shot if one follows.
  burst       C3, the posterized impact burst: b/w and red–cyan frames
              alternating, star glints, light rays, a falling glow.
  isolate     C10, color isolation: everything gray but one hue for 12
              frames, the color coming back over 6.
  lyric_words C5, the song's words one by one with the "invert" subtitles
              (the letters show the video in negative), placed away from the
              subject and cleared at the cut.

The texts of a song's lyrics are always Axl's (an .srt written or filled by
them); the code only times and places them.
"""
from __future__ import annotations

import re

from modules.editing.edl import Event

# C2 — blur cut
CUT_RADIAL = 1.0              # zoomblur on the outgoing frame (16 % reach at 1)
CUT_DIR_PX = 26.0             # directional blur on the outgoing frame (px of a 1080 frame)
CUT_EXPOSE = 0.42             # white mix on the outgoing frame (≈ +1.5 EV on mid tones)
CUT_ACCENT_EVERY = 4          # one cut in this many gets an inverted / posterized frame
# C1 — full hit
HIT_FREEZE = 9                # frames held
HIT_PUSH = 0.12               # zoom 1 → 1.12 while frozen
HIT_SHAKE = 0.52              # ≈ 14 px of a 1080 frame
HIT_RGB = 0.6                 # 6 px color split
# C3 — impact burst
BURST_FRAMES = 6              # b/w on even frames, duotone on odd ones
# C10 — color isolation
ISOLATE_HOLD = 12
ISOLATE_BACK = 6


def _f(k: int, fps: int) -> float:
    return k / fps


def blur_cuts(cuts: list[int], fps: int, *, style: str = "radial", angles: dict | None = None,
              accent_every: int = CUT_ACCENT_EVERY, strength: float = 1.0, skip: set | None = None) -> list[Event]:
    """C2 on each cut (`cuts` = first output frame of each new shot).
    style: radial | directional | mix (alternating). `angles` {cut: deg} gives
    the directional blur its axis (the hit's or the pan's); without one it is
    horizontal. `skip`: cuts left clean (a panel entering, a black frame)."""
    out: list[Event] = []
    skip = skip or set()
    for i, c in enumerate(sorted(cuts)):
        if c < 1 or c in skip:
            continue
        kind = style if style != "mix" else ("radial" if i % 2 == 0 else "directional")
        ang = (angles or {}).get(c)
        for k, amount in ((c - 1, 1.0), (c, 0.5)):
            if kind == "radial":
                out.append(Event(_f(k, fps), "zoomblur", intensity=CUT_RADIAL * amount * strength, dur=1 / fps))
            else:
                out.append(Event(_f(k, fps), "blur", dur=1 / fps, blur=CUT_DIR_PX * amount * strength, angle=ang))
            out.append(Event(_f(k, fps), "flash", intensity=min(1.0, CUT_EXPOSE * amount * strength),
                             dur=1 / fps, shape="mix"))
        if accent_every and i % accent_every == accent_every - 1:
            if (i // accent_every) % 2 == 0:
                out.append(Event(_f(c, fps), "invert", dur=1 / fps))
            else:
                out.append(Event(_f(c, fps), "impactframe", dur=1 / fps, style="poster"))
    return out


def hit(k: int, fps: int, *, angle: float | None = None, x: float = 0.5, y: float = 0.5,
        strength: float = 1.0, whip: bool = False, seed: int = 0, room: int | None = None) -> list[Event]:
    """C1 on output frame k (the contact frame). `room`: frames left before the
    shot's cut (the freeze never holds a frame over the next shot). With
    `whip`, the whip at the end of the freeze carries into the next shot."""
    s = strength
    n = max(1, min(HIT_FREEZE, room if room is not None else HIT_FREEZE))
    ev = [Event(_f(k, fps), "flash", intensity=min(1.5, 1.0 * s), dur=2 / fps),
          Event(_f(k, fps), "freeze", dur=n / fps),
          Event(_f(k, fps), "push", level=HIT_PUSH * s * n / HIT_FREEZE, dur=n / fps, x=x, y=y),
          Event(_f(k, fps), "rgb", intensity=HIT_RGB * s, dur=7 / fps),
          Event(_f(k + 1, fps), "shake", intensity=HIT_SHAKE * s, dur=6 / fps, angle=angle),
          Event(_f(k, fps), "speedlines", intensity=0.6 * s, dur=5 / fps, x=x, y=y, fade=0, seed=seed)]
    if whip:
        ev.append(Event(_f(k + n, fps), "whip", intensity=0.8 * s, dur=3 / fps, angle=angle))
    return ev


def burst(k: int, fps: int, *, x: float = 0.5, y: float = 0.5, colors: list | None = None,
          strength: float = 1.0, seed: int = 0) -> list[Event]:
    """C3 from output frame k: frames k, k+2, k+4 b/w ink, k+1, k+3, k+5 a
    red–cyan gradient; star glints over k…k+6, light rays k+4…k+8 and a glow
    that falls over k+8…k+20."""
    ev = []
    for i in range(BURST_FRAMES):
        if i % 2 == 0:
            ev.append(Event(_f(k + i, fps), "impactframe", dur=1 / fps, style="bw"))
        else:
            ev.append(Event(_f(k + i, fps), "duotone", dur=1 / fps, colors=list(colors or [])))
    ev += [Event(_f(k, fps), "glints", intensity=1.2 * strength, dur=7 / fps, x=x, y=y, seed=seed),
           Event(_f(k + 4, fps), "speedlines", intensity=1.2 * strength, dur=5 / fps, x=x, y=y, fade=0,
                 seed=seed + 1),
           Event(_f(k + 8, fps), "glow", intensity=min(1.5, strength), dur=12 / fps, attack=0.0)]
    return ev


def isolate(k: int, fps: int, *, color: str = "#e02020", similarity: float = 0.25, flash: bool = True,
            strength: float = 1.0) -> list[Event]:
    """C10 from output frame k: a flash, then everything gray but `color` for
    ISOLATE_HOLD frames, the color coming back over ISOLATE_BACK, and a glow."""
    n = ISOLATE_HOLD + ISOLATE_BACK
    ev = [Event(_f(k, fps), "isolate", dur=n / fps, colors=[color], similarity=similarity,
                fade=ISOLATE_BACK / fps),
          Event(_f(k, fps), "glow", intensity=0.7 * strength, dur=10 / fps, attack=0.0)]
    if flash:
        ev.append(Event(_f(k, fps), "flash", intensity=0.5 * strength, dur=2 / fps, shape="mix"))
    return ev


# ---------------------------------------------------------------- lyrics (C5)

_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def words_from_srt(path: str, offset: float = 0.0) -> list[dict]:
    """[{word, start, end}] from an .srt with one word per cue (Axl's text),
    shifted by `offset` s. Cues still holding the "♪ 001" placeholders are
    skipped: there is no text to show there yet."""
    with open(path, encoding="utf-8-sig") as fh:
        blocks = re.split(r"\n\s*\n", fh.read().strip())
    out = []
    for b in blocks:
        lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
        m = next((_TS.search(ln) for ln in lines if _TS.search(ln)), None)
        if not m:
            continue
        g = [int(v) for v in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        text = " ".join(ln for ln in lines if not _TS.search(ln) and not ln.isdigit())
        if not text or text.startswith("♪"):
            continue
        out.append({"word": text, "start": round(start + offset, 3), "end": round(end + offset, 3)})
    return out


LYRIC_Y = {"high": 560, "mid": 960, "low": 1390}   # center of the word block, px of 1920


def lyric_words(words: list[dict], shots: list[dict], fps: int) -> list[dict]:
    """Each word gets `y` (away from the subject of its shot) and `clear` (the
    next cut: a phrase never spills into the next shot). `shots` =
    [{"start": first frame, "end": end frame, "focus": (fx, fy)}]: the word
    goes in the lower third, or in the upper one when the subject sits low."""
    out = []
    for w in words:
        f = round(w["start"] * fps)
        shot = next((s for s in shots if s["start"] <= f < s["end"]), None)
        y, clear = LYRIC_Y["mid"], None
        if shot is not None:
            fy = shot.get("focus", (0.5, 0.5))[1]
            y = LYRIC_Y["high"] if fy > 0.6 else LYRIC_Y["low"]     # a centered subject: under it
            clear = shot["end"] / fps
        out.append({**w, "y": y, **({"clear": clear} if clear is not None else {})})
    return out
