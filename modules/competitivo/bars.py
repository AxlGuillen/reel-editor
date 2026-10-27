"""The health bars of the broadcast, in ONE frame: where each champion is.

The broadcast draws, over every champion, a bar with its level (a dark square at
the left), the team tag and name ("GEN Canyon") above it and the health: red for
the red side, blue for the blue one, in segments. They sit right over the
champion, so they say where it is. The feed gives no positions, and a card over a
champion only needs them ONCE (at the start of the play, during a freeze): a
single frame, not a track.

detect() finds the bars of a frame of the clean zone (BGR), measures how full each
is and cuts out its name label. identify() ties each wanted player to a bar with
what the API knows at that second (the side of the player, and its health).
Whatever is not clear is reported, not guessed: the preview shows every bar with
its number and the label, and the request can say `bar:N` or an x,y of its own.

At the start of a fight nearly everyone has full health, so health alone cannot tell
the bars apart. What does is the NAME above each bar: its width in pixels follows the
letters ("GEN Canyon" is wider than "GEN Chovy"), measured against a font (Montserrat
ExtraBold: the ratio held within 2 % on four labels of three games). It is only used
where it tells ONE of the side's players from the rest.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

BAR_W = 107                   # width of a full health bar in a 1080p frame
LABEL_K = 0.271               # px of a name label per px of the same text in Montserrat ExtraBold at size 40
LABEL_TOL = 0.065             # a label "is" a name when its width is within this share of the name's
LEVEL_W = 23                  # the level square at its left
MIN_FILL_W = 12              # a narrower fill is a dying champion: not seen
MINION_W = 70                 # minions' bars are shorter than this when full
RED = dict(h_lo=168, h_hi=6, s=110, v=100)
BLUE = dict(h_lo=94, h_hi=108, s=140, v=110)      # the mana bar (S ~110) is left out


@dataclass
class Bar:
    n: int                    # number in this frame (left to right, top to bottom)
    side: str                 # "red" | "blue"
    x: int                    # of the fill (the clean zone's pixels)
    y: int
    w: int
    h: int
    fraction: float           # how full: 0-1
    label: tuple = field(default_factory=tuple)      # x, y, w, h of the name above
    label_w: int | None = None                        # the name's width in px, if it could be measured

    @property
    def top_center(self) -> tuple[int, int]:
        """Where a card goes: over the bar's middle, above the name."""
        return self.x - LEVEL_W + (LEVEL_W + BAR_W) // 2, self.label[1] if self.label else self.y - 16


def _mask(hsv: np.ndarray, side: str) -> np.ndarray:
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    if side == "red":
        c = RED
        return ((h <= c["h_hi"]) | (h >= c["h_lo"])) & (s >= c["s"]) & (v >= c["v"])
    c = BLUE
    return (h >= c["h_lo"]) & (h <= c["h_hi"]) & (s >= c["s"]) & (v >= c["v"])


def label_width(frame: np.ndarray, bar: "Bar") -> int | None:
    """Width of the name written above a bar: the group of white letters nearest the bar's middle (a spell
    effect behind the text can add stray bright columns: only a group of letters, not a lone streak, counts)."""
    x, y, w, h = bar.label
    x0, y0 = max(0, x), max(0, y)
    crop = frame[y0:y0 + h, x0:x0 + w]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    white = (hsv[..., 2] >= 175) & (hsv[..., 1] <= 70)
    cols = white.sum(axis=0) >= 2
    runs, start, last = [], None, None
    for i, on in enumerate(cols):
        if not on:
            continue
        if start is None:
            start = i
        elif i - last > 9:                       # a gap wider than a space: another group
            runs.append((start, last))
            start = i
        last = i
    if start is not None:
        runs.append((start, last))
    runs = [r for r in runs if r[1] - r[0] + 1 >= 24]
    if not runs:
        return None
    centre = (BAR_W + 16) / 2 - (x0 - x)             # the label box is centered on the bar
    best = min(runs, key=lambda r: abs((r[0] + r[1]) / 2 - centre))
    return int(best[1] - best[0] + 1)


def predicted_width(text: str) -> float:
    """What a name's label should measure, from Montserrat ExtraBold's letters."""
    import os

    import config
    from PIL import ImageFont
    font = ImageFont.truetype(os.path.join(config.FONTS_FOLDER, "Montserrat-ExtraBold.ttf"), 40)
    return font.getlength(text) * LABEL_K


def detect(frame: np.ndarray) -> list[Bar]:
    """The champions' health bars of a BGR frame of the clean zone."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    found: list[Bar] = []
    for side in ("red", "blue"):
        m = (_mask(hsv, side).astype(np.uint8)) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((1, 9), np.uint8))       # the ticks between segments
        n, _, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if not (6 <= h <= 16 and MIN_FILL_W <= w <= BAR_W + 6 and area >= 0.55 * w * h):
                continue
            # The level square at its left: dark, with the digit's white strokes in it.
            lx0, lx1 = max(0, x - LEVEL_W + 2), max(0, x - 3)
            if lx1 - lx0 < 12 or y < 0:
                continue
            box = gray[max(0, y - 4): y + h + 6, lx0:lx1]
            if box.size == 0 or float(np.median(box)) > 95 or float((box > 170).mean()) < 0.03:
                continue
            bar = Bar(0, side, int(x), int(y), int(w), int(h), min(1.0, w / BAR_W),
                      (int(x - 8), int(y - 17), BAR_W + 16, 15))
            bar.label_w = label_width(frame, bar)
            found.append(bar)
    # Number them left to right inside rows (the same fight reads the same way twice).
    found.sort(key=lambda b: (round(b.y / 40), b.x))
    for k, b in enumerate(found, 1):
        b.n = k
    return found


def to_dict(bars: list[Bar]) -> list[dict]:
    return [{"n": b.n, "side": b.side, "x": b.x, "y": b.y, "w": b.w, "fraction": round(b.fraction, 2),
             "top_center": list(b.top_center)} for b in bars]


def identify(bars: list[Bar], wanted: dict[str, dict], margin: float = 0.07) -> dict[str, dict]:
    """Ties each wanted player to one bar.

    wanted: name → {"side": "red"|"blue", "fraction": health now (0-1) or None,
    "rivals": [fractions of the OTHERS of that side], "name_w": its label's width,
    "others_w": [the widths of the side's other players' labels]}. A player is
    identified when it is the only bar of its side, or when its bar is the only one
    whose name fits its width and none of the side's other names does, or when its
    bar is the one whose fullness is the nearest to the API's and clearly nearer than
    the next one (margin). Otherwise the answer says why ("ambiguous"/"not seen")
    and the candidates, so it can be fixed with `bar:N` or a point.
    Returns name → {"bar": Bar | None, "status": "ok" | "ambiguous" | "not_seen", "candidates": [n…]}."""
    out: dict[str, dict] = {}
    used: set[int] = set()
    for name, info in wanted.items():
        cands = [b for b in bars if b.side == info["side"] and b.n not in used]
        if not cands:
            out[name] = {"bar": None, "status": "not_seen", "candidates": []}
            continue
        if len(cands) == 1 and len([b for b in bars if b.side == info["side"]]) == 1:
            out[name] = {"bar": cands[0], "status": "ok", "candidates": [cands[0].n]}
            used.add(cands[0].n)
            continue
        frac = info.get("fraction")
        by_name = _by_name(cands, info)
        if by_name is not None and (frac is None or abs(by_name.fraction - frac) <= 0.2):
            out[name] = {"bar": by_name, "status": "ok", "candidates": [b.n for b in cands]}
            used.add(by_name.n)
            continue
        if frac is None:
            out[name] = {"bar": None, "status": "ambiguous", "candidates": [b.n for b in cands]}
            continue
        ranked = sorted(cands, key=lambda b: abs(b.fraction - frac))
        best = ranked[0]
        second = abs(ranked[1].fraction - frac) if len(ranked) > 1 else 1.0
        # Clear only if the nearest is close to the API's AND the others of its side
        # (who would also fit) are not as near.
        near_rivals = [r for r in info.get("rivals", []) if abs(r - frac) < margin]
        if abs(best.fraction - frac) <= 0.12 and second - abs(best.fraction - frac) >= margin and not near_rivals:
            out[name] = {"bar": best, "status": "ok", "candidates": [b.n for b in cands]}
            used.add(best.n)
        else:
            out[name] = {"bar": None, "status": "ambiguous", "candidates": [b.n for b in ranked]}
    return out


def _fits(width: int | None, expected: float | None) -> bool:
    return bool(width and expected and abs(width - expected) <= LABEL_TOL * expected)


def _by_name(cands: list[Bar], info: dict) -> Bar | None:
    """The one candidate whose name has this player's width while no other player of the side has it."""
    if not info.get("name_w"):
        return None
    mine = [b for b in cands if _fits(b.label_w, info["name_w"])]
    if len(mine) != 1:
        return None
    if any(_fits(mine[0].label_w, w) for w in info.get("others_w", [])):
        return None                                  # another player's name is as wide: the width cannot tell
    return mine[0]


def annotate(frame: np.ndarray, bars: list[Bar], marks: dict[int, str] | None = None) -> np.ndarray:
    """The frame with every bar boxed and numbered (the preview of the cards)."""
    img = frame.copy()
    for b in bars:
        color = (60, 60, 255) if b.side == "red" else (255, 160, 40)
        cv2.rectangle(img, (b.x - LEVEL_W, b.y - 3), (b.x - LEVEL_W + LEVEL_W + BAR_W, b.y + b.h + 12), color, 2)
        text = f"{b.n}" + (f" {marks[b.n]}" if marks and b.n in marks else "")
        cv2.putText(img, text, (b.x - LEVEL_W, b.y - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
    return img


def label_crops(frame: np.ndarray, bars: list[Bar], scale: int = 3) -> np.ndarray:
    """A strip with the name label of every bar, enlarged (to read who each one is)."""
    tiles = []
    for b in bars:
        x, y, w, h = b.label
        x0, y0 = max(0, x), max(0, y)
        crop = frame[y0: y0 + h + 4, x0: x0 + w]
        if crop.size == 0:
            continue
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        cv2.putText(crop, str(b.n), (2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
        tiles.append(cv2.copyMakeBorder(crop, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=(40, 40, 40)))
    if not tiles:
        return np.zeros((10, 10, 3), np.uint8)
    width = max(t.shape[1] for t in tiles)
    return np.vstack([cv2.copyMakeBorder(t, 0, 0, 0, width - t.shape[1], cv2.BORDER_CONSTANT) for t in tiles])
