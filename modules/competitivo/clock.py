"""The game clock of the official scoreboard ("10:18"), read from the video.

The feed's times are UTC; what Axl (and everyone) says is the clock of the
scoreboard: "la del 21:41". The fights' titles used to count from the start
of the feed, ~5 min before 0:00. Here the clock is read from the video
(digit templates of the broadcast's font, assets/competitivo/clock_digits.json,
made from real frames with build_templates) and a game is calibrated once:
clock0_utc = when the scoreboard said 0:00. From there any time of the feed
becomes clock time, minus the pauses the feed marks (gameState "paused").
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import numpy as np

import config
from core import ffmpeg_runner

CLOCK = (925, 76, 70, 24)         # x, y, w, h of the clock in the 1920x1080 source
GLYPH = (10, 20)                  # each digit cell normalized to w × h
WHITE = 150                       # the digits are white over a dark scoreboard
MIN_SCORE = 0.75                  # correlation with the best template to accept a digit
TEMPLATES = os.path.join(config.ASSETS_FOLDER, "competitivo", "clock_digits.json")


def _crop(path: str, ts: list[float]) -> list[np.ndarray]:
    x, y, w, h = CLOCK
    out = []
    for t in ts:
        raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
                                     "-frames:v", "1", "-vf", f"crop={w}:{h}:{x}:{y},format=gray",
                                     "-f", "rawvideo", "-"])
        out.append(np.frombuffer(raw, np.uint8).reshape(h, w).astype(np.float32) if len(raw) == w * h else None)
    return out


# The digits are monospaced (a "1" sits centered in the same cell as a "0")
# and touch each other and the colon: they are cut by their fixed cells (x in
# the CLOCK crop), with a small shift search in case the source moves a pixel.
CELLS = (12, 22, 38, 49)
CELL_W = 10
ROWS = (3, 23)
SHIFTS = range(-3, 4)


def glyphs(img: np.ndarray, shift: int = 0) -> list[np.ndarray]:
    """The four digits of the clock (MM:SS): each cell cut to its own ink (so
    a digit looks the same in any cell) and scaled by area to GLYPH (the hole
    of a 0 is one pixel wide: nearest-neighbor lost it). A last row carries
    its width (a "1" is narrow)."""
    import cv2
    out = []
    for x in CELLS:
        a = max(0, x + shift)
        cell = img[ROWS[0]:ROWS[1], a:a + CELL_W]
        if cell.shape[1] < CELL_W:
            return []
        ink = cell > WHITE
        cols, rows = np.nonzero(ink.any(axis=0))[0], np.nonzero(ink.any(axis=1))[0]
        if len(cols) < 2 or len(rows) < 6:
            return []
        g = cell[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1].astype(np.float32) / 255
        n = cv2.resize(g, GLYPH, interpolation=cv2.INTER_AREA if g.shape[1] >= GLYPH[0] else cv2.INTER_LINEAR)
        width = np.full((1, GLYPH[0]), 3 * (cols[-1] - cols[0] + 1) / CELL_W, np.float32)
        out.append(np.vstack([n, width]))
    return out


def _load() -> dict[int, np.ndarray]:
    with open(TEMPLATES, encoding="utf-8") as fh:
        data = json.load(fh)
    return {int(k): np.array(v, np.float32) for k, v in data["digits"].items()}


_templates: dict[int, np.ndarray] | None = None


def read(img: np.ndarray | None) -> int | None:
    """Seconds of the clock in a crop of CLOCK, or None if it does not read
    as MM:SS (a replay, a transition, no scoreboard)."""
    global _templates
    if img is None or (img > WHITE).sum() < 40:
        return None
    if _templates is None:
        _templates = _load()
    best_total, digits = -1.0, None
    for shift in SHIFTS:
        gs = glyphs(img, shift)
        if len(gs) != 4:
            continue
        ds, total = [], 0.0
        for g in gs:
            d, score = _match(g)
            ds.append((d, score))
            total += score
        if total > best_total:
            best_total, digits = total, ds
    if not digits or min(sc for _, sc in digits) < MIN_SCORE:
        return None
    d = [x for x, _ in digits]
    m, s = d[0] * 10 + d[1], d[2] * 10 + d[3]
    return m * 60 + s if s < 60 else None


def _match(g: np.ndarray) -> tuple[int, float]:
    a = g - g.mean()
    best, score = -1, -1.0
    for d, tpl in _templates.items():
        b = tpl - tpl.mean()
        c = float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) or 1))
        if c > score:
            best, score = d, c
    return best, score


def clock_at(path: str, t: float, tries: int = 5) -> float | None:
    """The clock (s, with the fraction) at second t of the file: the reading
    of several frames 0.25 s apart, which agree once the second changes."""
    ts = [t + 0.25 * k for k in range(tries)]
    vals = [(tt, read(img)) for tt, img in zip(ts, _crop(path, ts))]
    vals = [(tt, v) for tt, v in vals if v is not None]
    if len(vals) < 2:
        return None
    # Where the value goes up by one, the second turns (between the two
    # frames): that gives the fraction. Values that jump are a misreading.
    for (t0, v0), (t1, v1) in zip(vals, vals[1:]):
        if v1 == v0 + 1:
            return v1 - ((t0 + t1) / 2 - t)
    values = [v for _, v in vals]
    consensus = max(set(values), key=values.count)
    return consensus + 0.5 if values.count(consensus) >= len(values) - 1 else None


def calibrate(path: str, ts: list[float]) -> float | None:
    """Second of the FILE at which the scoreboard said 0:00: the median over
    several readings (one wrong reading does not move it)."""
    zeros = []
    for t in ts:
        c = clock_at(path, t)
        if c is not None:
            zeros.append(t - c)
    if not zeros:
        return None
    zeros = np.array(zeros)
    med = float(np.median(zeros))
    good = zeros[np.abs(zeros - med) < 1.5]
    return round(float(np.median(good)), 2) if len(good) >= max(1, len(zeros) // 2) else None


# --- feed time ↔ clock ------------------------------------------------------------
def pauses(frames) -> list[tuple[datetime, datetime]]:
    """[(from, to)] UTC of the pauses the feed marks."""
    out, start = [], None
    for f in frames:
        if f.state == "paused" and start is None:
            start = f.ts
        elif f.state != "paused" and start is not None:
            out.append((start, f.ts))
            start = None
    return out


def to_clock(ts: datetime, clock0_utc: datetime, paused: list[tuple[datetime, datetime]]) -> float:
    """Seconds of the scoreboard clock at UTC time ts."""
    s = (ts - clock0_utc).total_seconds()
    for a, b in paused:
        if b <= ts:
            s -= (b - a).total_seconds() if a >= clock0_utc else 0.0
        elif a < ts:
            s -= (ts - a).total_seconds()
    return s


def from_clock(clock_s: float, clock0_utc: datetime, paused: list[tuple[datetime, datetime]]) -> datetime:
    """UTC time at which the scoreboard said clock_s."""
    ts = clock0_utc + timedelta(seconds=clock_s)
    for a, b in sorted(paused):
        if a >= clock0_utc and a < ts:
            ts += b - a
    return ts


def fmt(clock_s: float) -> str:
    s = max(0, int(clock_s))
    return f"{s // 60}:{s % 60:02d}"


def parse(text: str) -> float:
    """ "21:41" → 1301 s. Raises ValueError."""
    m, s = str(text).strip().split(":")
    if not (m.isdigit() and s.isdigit() and int(s) < 60):
        raise ValueError(text)
    return int(m) * 60 + int(s)


# --- the templates (made once from real frames) --------------------------------------
def build_templates(path: str, t0: float, clock0: int, n: int = 100) -> dict:
    """Digit templates from n consecutive seconds of a file where second t0
    shows clock0 (and nothing pauses): the units digit goes through 0–9.
    First it finds when the units digit turns (0.05 s steps) so each frame
    is labeled with the second it really shows."""
    x = CELLS[3]
    steps = [t0 + 0.05 * k for k in range(25)]
    units = [img[ROWS[0]:ROWS[1], x:x + CELL_W] for img in _crop(path, steps)]
    change = max(range(1, len(units)), key=lambda k: np.abs(units[k] - units[k - 1]).mean())
    turn = steps[change]                         # here clock0 becomes clock0 + 1
    ts = [turn + k + 0.5 for k in range(n)]
    acc: dict[int, list[np.ndarray]] = {}
    for k, img in enumerate(_crop(path, ts)):
        if img is None:
            continue
        gs = glyphs(img)
        if len(gs) != 4:
            continue
        m, s_ = divmod(clock0 + 1 + k, 60)
        for g, d in zip(gs, (m // 10, m % 10, s_ // 10, s_ % 10)):
            acc.setdefault(d, []).append(g)
    return {"source": os.path.basename(path), "glyph": GLYPH, "clock_box": CLOCK, "turn": round(turn, 2),
            "digits": {d: np.mean(v, axis=0).round(3).tolist() for d, v in sorted(acc.items())}}
