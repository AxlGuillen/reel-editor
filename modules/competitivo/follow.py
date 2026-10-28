"""The crop follows ONE player: the gameplay stays centered on his champion.

The broadcast draws a health bar over every champion (bars.py), so a bar says where
a champion is. The name above it cannot always tell the player (three of the five
names of a side are within 1 px of each other), so the player is identified ONCE,
like the cards do (the health the API reports at that second), and then followed
from bar to bar: the observer's camera pan between two frames is measured (phase
correlation) and the bar that lands nearest to where his was, shifted by it, is him.
Where he is not found the crop stays where it was expected; if he is lost for long
or never identified, the answer is None and the caller keeps the action-following camera.
"""
from __future__ import annotations

import cv2
import numpy as np

from modules.competitivo import bars, card_plan, layout

STEP_S = 0.25              # a frame is looked at this often
CHUNK_S = 6.0              # frames are read in chunks of this long
SEED_LEAD_S = 2.0          # the player is identified this long before the first kill (where the cards go)
GATE_PX = 110              # a bar this far from where he should be (px of the zone) is somebody else
LOST_S = 2.0               # lost for longer than this: the crop stops following him
SMOOTH = 0.35              # share of the new position in the smoothed one (1 = no smoothing)
PAN_W = 368                # the frames are compared at this width to measure the pan
REACQUIRE = 0.85           # a bar whose level square is this alike his is him wherever it is
LVL_WEIGHT = 2.0           # how much a level square unlike his counts against a distance of GATE_PX


def level_patch(frame: np.ndarray, bar: "bars.Bar") -> np.ndarray:
    """The level square at the left of a bar, as a 24x24 gray patch. Its digits are what tells two players
    of the same name width apart: his level hardly changes in a fight, theirs differ."""
    x0 = max(0, bar.x - bars.LEVEL_W + 2)
    crop = frame[max(0, bar.y - 4): bar.y + bar.h + 6, x0: max(0, bar.x - 3)]
    if crop.size == 0:
        return np.zeros((24, 24), np.float32)
    return cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (24, 24), interpolation=cv2.INTER_AREA).astype(np.float32)


def seed_of(span: str, t: float, entry: dict, roster: list[dict], partida: dict, utc_at, api=None,
            frames=None) -> tuple[float, tuple[float, float] | None, dict, np.ndarray | None]:
    """(t, (x, y) of his bar in the zone, info, his level patch) at the frame near second `t` that shows
    the most bars of his side. None when the bars cannot tell which one is him (the `info` says why)."""
    got = (frames or card_plan.grab_frames)(span, max(0.0, t - 1.5), t + 1.5)
    scored = []
    for ft, fr in got:
        found = bars.detect(fr)
        scored.append((len(found) + 5 * any(b.side == entry["side"] for b in found), ft, found, fr))
    if not scored:
        return t, None, {"seed": "no_frames"}, None
    _, ft, found, frame = max(scored, key=lambda s: (s[0], s[1]))
    state = (api or card_plan.api_players)(partida["game_id"], utc_at(ft))
    mine = state.get(entry["pid"]) or {}
    frac = (mine["hp"] / mine["maxhp"]) if mine.get("hp") is not None and mine.get("maxhp") else None
    rivals = [(s["hp"] / s["maxhp"]) for pid, s in state.items()
              if pid != entry["pid"] and (pid <= 5) == (entry["side"] == "blue") and s.get("hp") and s.get("maxhp")]
    label = lambda r: bars.predicted_width(f"{r['team']} {r['player']}")  # noqa: E731
    wanted = {entry["player"]: {"side": entry["side"], "fraction": frac, "rivals": rivals, "name_w": label(entry),
                                "others_w": [label(r) for r in roster
                                             if r["side"] == entry["side"] and r["pid"] != entry["pid"]]}}
    res = bars.identify(found, wanted)[entry["player"]]
    if res["bar"] is None:
        return ft, None, {"seed": res["status"], "seed_t": round(ft, 2), "candidates": res["candidates"]}, None
    x, y = res["bar"].top_center[0], res["bar"].y
    return (ft, (float(x), float(y)), {"seed": "ok", "seed_t": round(ft, 2), "bar": res["bar"].n},
            level_patch(frame, res["bar"]))


def _pan(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """How much the picture moved from frame a to frame b (px of the zone), by phase correlation."""
    (dx, dy), resp = cv2.phaseCorrelate(a, b)
    k = card_plan.ZONE_W / PAN_W
    return (dx * k, dy * k) if resp > 0.05 else (0.0, 0.0)


def _read(span: str, t0: float, t1: float, side: str, frames=None):
    """[(t, the side's bars, small gray frame, their level patches)] of the span, chunk by chunk (a frame is 3 MB)."""
    out = []
    t = max(0.0, t0)
    while t < t1:
        for ft, fr in (frames or card_plan.grab_frames)(span, t, min(t1, t + CHUNK_S), STEP_S):
            small = cv2.resize(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (PAN_W, int(fr.shape[0] * PAN_W / fr.shape[1])))
            mine = [b for b in bars.detect(fr) if b.side == side]
            out.append((ft, mine, small.astype(np.float32), [level_patch(fr, b) for b in mine]))
        t += CHUNK_S
    return out


HP_WEIGHT = 2.0            # how much a difference of fullness (0-1) counts against a distance of GATE_PX


def track_player(span: str, t0: float, t1: float, entry: dict, seed_t: float, seed_xy: tuple[float, float],
                 zoom: float, roster: list[dict] | None = None, hp=None, lvl: np.ndarray | None = None,
                 frames=None) -> tuple[list[tuple[float, float]] | None, dict]:
    """([(t, crop x)], info): the crop following the player's bar over [t0, t1] of the span, from the
    position he was identified at (`seed_xy` at `seed_t`). `hp(t)`: his health (0-1) at second t of the
    span, if known: between two bars within reach the one whose fullness is nearer wins. A bar whose
    name is clearly another player's width is not a candidate."""
    seq = _read(span, t0, t1, entry["side"], frames)
    mine_w = bars.predicted_width(f"{entry['team']} {entry['player']}")
    others_w = [bars.predicted_width(f"{r['team']} {r['player']}") for r in (roster or [])
                if r["side"] == entry["side"] and r["pid"] != entry["pid"]]

    def ok_label(b) -> bool:
        return b.label_w is None or bars._fits(b.label_w, mine_w) or not any(bars._fits(b.label_w, w) for w in others_w)

    def cost(b, patch, guess, t) -> float:
        c = float(np.hypot(b.top_center[0] - guess[0], b.y - guess[1])) / GATE_PX
        f = hp(t) if hp else None
        if f is not None:
            c += HP_WEIGHT * abs(b.fraction - f)
        if lvl is not None:
            c += LVL_WEIGHT * (1.0 - max(0.0, float(cv2.matchTemplate(patch, lvl, cv2.TM_CCOEFF_NORMED)[0, 0])))
        return c
    if len(seq) < 3:
        return None, {"frames": len(seq)}
    s = min(range(len(seq)), key=lambda i: abs(seq[i][0] - seed_t))
    pos: list[tuple[float, float] | None] = [None] * len(seq)
    pos[s] = seed_xy
    lost_at = {}
    for step in (1, -1):                                       # forward, then backward from the seed
        cur, last_seen = seed_xy, seq[s][0]
        i = s + step
        while 0 <= i < len(seq):
            prev = seq[i - step]
            dx, dy = _pan(prev[2], seq[i][2]) if step == 1 else tuple(-v for v in _pan(seq[i][2], prev[2]))
            guess = (cur[0] + dx, cur[1] + dy)
            near = [(cost(b, patch, guess, seq[i][0]), b) for b, patch in zip(seq[i][1], seq[i][3])
                    if ok_label(b) and np.hypot(b.top_center[0] - guess[0], b.y - guess[1]) <= GATE_PX]
            b = min(near, key=lambda n: n[0])[1] if near else None
            if b is None and lvl is not None:                  # out of reach: his level square, anywhere
                far = [(float(cv2.matchTemplate(patch, lvl, cv2.TM_CCOEFF_NORMED)[0, 0]), bb)
                       for bb, patch in zip(seq[i][1], seq[i][3]) if ok_label(bb)]
                far = [f for f in far if f[0] >= REACQUIRE]
                b = max(far, key=lambda f: f[0])[1] if far else None
            if b is not None:
                cur, last_seen = (float(b.top_center[0]), float(b.y)), seq[i][0]
            else:
                cur = guess                                    # not found: where he should be
            pos[i] = cur if abs(seq[i][0] - last_seen) <= LOST_S else None
            if pos[i] is None:
                lost_at.setdefault(step, []).append(round(seq[i][0], 2))
            i += step
    found = [(seq[i][0], p[0]) for i, p in enumerate(pos) if p is not None]
    info = {"player": entry["player"], "frames": len(seq), "followed": len(found),
            "share": round(len(found) / len(seq), 2), **({"lost_at": lost_at} if lost_at else {})}
    if len(found) < 0.5 * len(seq):
        return None, info
    track, u = [], None
    for ft, x in found:
        u = x if u is None else u + SMOOTH * (x - u)
        track.append((round(ft, 3), float(layout.crop_x_for(u / card_plan.ZONE_W, zoom))))
    if track[0][0] > t0:                                       # before / after: where he was seen
        track.insert(0, (round(max(0.0, t0), 3), track[0][1]))
    if track[-1][0] < t1:
        track.append((round(t1, 3), track[-1][1]))
    return track, info
