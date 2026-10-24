"""Where the camera goes at each kill: zoomed on the action or the full view.

For every kill (its frame: when the scoreboard's kill counter goes up) the
action is looked for in the 1.5 s before it (layout.action_track). Then:

    zoom   the gameplay crop slides, smoothly, to center the action before
           the kill, and the zoom punch aims there
    full   the whole clean zone across the width (like the split camera):
           nothing is cut off and there is no zoom

zoom_mode: "zoom" (always zoom; centered if not sure), "full" (the whole span
in the full view) or "auto" (zoom where the action is clear, full where it
is not: better than a zoom in the wrong place). A kill can be corrected by
hand (kill_overrides): its mode, and its focus as (u, v) of the clean zone
(the storyboard's thumbnails).
"""
from __future__ import annotations

from dataclasses import dataclass

from modules.competitivo import layout

MODES = ("auto", "zoom", "full")
SLIDE_S = 0.9                 # the crop takes this long to reach the action
SLIDE_END = 0.6               # and arrives this long before the kill
HOLD_AFTER_S = 1.0            # it stays on it this long after the kill
FULL_BEFORE_S = 1.5           # the full view of an unclear kill
FULL_AFTER_S = 1.2
MIN_SLIDE_S = 0.3


@dataclass
class Shot:
    id: str                   # "1-10#2": clip and kill number
    t: float                  # s in the span (the scoreboard's frame)
    mode: str                 # zoom | full
    u: float = 0.5            # the action in the clean zone (0–1)
    v: float = 0.5
    sure: bool = False
    by: str = "auto"          # auto | override
    aim: tuple = (0.5, 0.5)   # where the zoom punch aims, in the OUTPUT frame
    crop_x: float | None = None

    def as_dict(self) -> dict:
        return {"id": self.id, "t": round(self.t, 3), "mode": self.mode, "u": self.u, "v": self.v,
                "sure": self.sure, "by": self.by, "aim": [round(self.aim[0], 3), round(self.aim[1], 3)]}


def _aim(u: float, v: float, crop_x: float, zoom: float) -> tuple[float, float]:
    """The point (u, v) of the zone in the OUTPUT frame, with the crop at crop_x."""
    g = layout.geometria(zoom)
    cw, ch, _, cy = g.recorte
    jx, jy, jw, jh = layout.JUEGO
    x = min(max((jx + u * jw - crop_x) / cw, 0.0), 1.0)
    y = min(max((jy + v * jh - cy) / ch, 0.0), 1.0)
    return x, (layout.ARRIBA_H + y * layout.JUEGO_H) / layout.H


def plan(kills: list[tuple[str, float]], tracks: dict, zoom: float, mode: str,
         overrides: dict | None = None, span: tuple[float, float] | None = None):
    """(crop_track, full_ranges, shots) of one span.
    kills: [(id, t)] in span seconds; tracks: id → layout.action_track(...);
    overrides: id → {"mode": "zoom"|"full", "focus": [u, v]}."""
    if mode not in MODES:
        raise ValueError(f"zoom_mode inválido: {mode!r}. Opciones: {', '.join(MODES)}.")
    overrides = overrides or {}
    center = layout.geometria(zoom).recorte[2]
    shots: list[Shot] = []
    for kid, t in sorted(kills, key=lambda k: k[1]):
        tr = tracks.get(kid) or {}
        o = overrides.get(kid) or {}
        sh = Shot(kid, t, "zoom", tr.get("u", 0.5), tr.get("v", 0.5), bool(tr.get("sure")))
        if mode == "full":
            sh.mode = "full"
        elif mode == "auto" and not sh.sure:
            sh.mode = "full"
        if not sh.sure and sh.mode == "zoom":
            sh.u = sh.v = 0.5                       # "zoom" without a clear action: the center
        if o.get("focus"):
            sh.u, sh.v = (float(x) for x in o["focus"])
            sh.sure, sh.mode, sh.by = True, "zoom", "override"
        if o.get("mode"):
            sh.mode, sh.by = o["mode"], "override"
        shots.append(sh)
    if mode == "full":
        a, b = span if span else (0.0, 1e6)
        return None, [(a, b)], shots

    # The crop: still at the first zoomed kill's target, then sliding to each next one.
    track: list[tuple[float, float]] = []
    full: list[tuple[float, float]] = []
    x = None
    for sh in shots:
        if sh.mode == "full":
            full.append((max(0.0, sh.t - FULL_BEFORE_S), sh.t + FULL_AFTER_S))
            sh.aim = (0.5, 0.5)
            continue
        target = float(layout.crop_x_for(sh.u, zoom)) if sh.sure else (x if x is not None else float(center))
        start, end = sh.t - SLIDE_END - SLIDE_S, sh.t - SLIDE_END
        if x is None:
            track.append((max(0.0, start), target))
        else:
            last_t = track[-1][0]
            start = max(start, last_t + 1e-3)
            end = max(end, start + MIN_SLIDE_S)
            track += [(start, x), (end, target)]
        track.append((sh.t + HOLD_AFTER_S, target))
        x = target
        sh.crop_x = target
        sh.aim = _aim(sh.u, sh.v, target, zoom)
    merged: list[list[float]] = []
    for a, b in sorted(full):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return (track or None), [(a, b) for a, b in merged], shots


def overrides_from(raw: list | None, kill_clocks: dict[str, float | None]) -> dict:
    """kill_overrides as given ([{kill | clock, mode?, focus?}]) → id → {mode, focus}.
    A clock ("21:41", the scoreboard's) means every kill within 2 s of it.
    Raises ValueError with what did not match."""
    from modules.competitivo import clock as game_clock
    out: dict[str, dict] = {}
    for item in raw or []:
        if item.get("kill"):
            ids = [item["kill"]] if item["kill"] in kill_clocks else []
        else:
            c = game_clock.parse(item.get("clock", ""))
            ids = [k for k, kc in kill_clocks.items() if kc is not None and abs(kc - c) <= 2.0]
        if not ids:
            raise ValueError(f"kill_overrides: no hay ninguna kill en {item.get('kill') or item.get('clock')}.")
        if item.get("mode") not in (None, "zoom", "full"):
            raise ValueError("kill_overrides: 'mode' es zoom o full.")
        for kid in ids:
            out[kid] = {k: item[k] for k in ("mode", "focus") if item.get(k) is not None}
    return out
