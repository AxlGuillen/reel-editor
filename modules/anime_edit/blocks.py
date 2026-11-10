"""The block planner (motion: on): every take of the manga edit gets a block,
the block decides the effects, and the code puts them on the real hits.

  1. Curve: the song section under each take (beatmap: intro / verse /
     chorus / bridge / outro, the buildups before each chorus and the drop)
     gives a target intensity (blocks.json "curve").
  2. Block: the strongest available block with intensity ≤ target whose
     needs the take's motion map meets (e.g. rafaga_golpes needs 3 impacts);
     or the one Claude asked for (`blocks=[{"take": i, "block": id}]`).
  3. Recipe: the block's effects, each one ON the output frame where its hit
     shows up (fit.out_frame), with the hit's direction and the action center.
     `rampa` retimes the take so its strongest hit lands on a beat (fit.fit_hit).
  4. Transitions: on the cuts between takes from intensity 3 up, at least 3
     types and none more than 6 times; the rest are hard cuts.

Pure: the motion maps come in (`maps`, by take index), nothing is read here.
The planner records every hit-locked effect ({kind, frame, hit_frame}) so the
±1-frame criterion is checked on the plan itself.
"""
from __future__ import annotations

import json
import math
import os

from modules.anime_edit import fit
from modules.editing.edl import Edl, Event, Piece

BLOCKS = os.path.join(os.path.dirname(__file__), "blocks.json")
MIN_HIT = 0.45                # impacts below this score are not hits for a block
HIT_GAP = 4                   # frames between two accented hits in a burst


def load() -> dict:
    with open(BLOCKS, encoding="utf-8") as fh:
        data = json.load(fh)
    data["by_id"] = {b["id"]: b for b in data["blocks"]}
    return data


def section_of(song_t: float, bm: dict) -> str:
    """intro / verse / chorus / bridge / outro, or buildup / drop."""
    for a, b in bm.get("buildups") or []:
        if a <= song_t < b:
            return "buildup"
    secs = bm.get("sections") or []
    drop = bm.get("drop")
    for s in secs:
        if s["start"] <= song_t < s["end"]:
            if drop is not None and s["type"] == "chorus" and s["start"] - 0.05 <= drop < s["end"]:
                return "drop"
            return s["type"]
    return "verse"


def _hits(m: dict | None, a: float, b: float) -> list[dict]:
    if not m:
        return []
    return sorted((e for e in m["events"] if e["type"] == "impact" and a <= e["t"] <= b and e["score"] >= MIN_HIT),
                  key=lambda e: e["t"])


def _meets(block: dict, m: dict | None, a: float, b: float) -> bool:
    need = block.get("needs", {}).get("impact", {}).get("min", 0)
    return len(_hits(m, a, b)) >= need


def choose(target: int, label: str, m: dict | None, a: float, b: float, data: dict) -> dict:
    """The strongest usable block with intensity ≤ target."""
    usable = [bl for bl in data["blocks"]
              if bl.get("available", True) and bl["id"] not in ("panel", "texto")
              and bl["intensity"] <= target and (not bl.get("sections") or label in bl["sections"])
              and _meets(bl, m, a, b)]
    # ties: the block made for this section (estrobo in a buildup) wins
    return max(usable, key=lambda bl: (bl["intensity"], bool(bl.get("sections")), bl["id"] != "calma"),
               default=data["by_id"]["calma"])


def _focus(m: dict | None, t: float) -> tuple[float, float]:
    if not m:
        return 0.5, 0.5
    fr = m["frames"]
    k = min(max(int(round(t * m["fps"])) - m["f0"], 0), m["frames_count"] - 1)
    return fr["cx"][k], fr["cy"][k]


def apply(edl: Edl, items: list[dict], spans: list[tuple[int, int]], maps: dict, beat_frames: list[int],
          bm: dict, music_start: float, fps: int, explicit: list[dict] | None = None) -> dict:
    """Blocks, recipes and transitions over the manga EDL (segments ↔ items 1:1)."""
    data = load()
    take_index = [i for i, it in enumerate(items) if it["kind"] == "take"]
    asked = {}
    for req in explicit or []:
        k = int(req["take"])
        if not 0 <= k < len(take_index):
            raise ValueError(f"No existe la toma {k} (hay {len(take_index)}).")
        if req["block"] not in data["by_id"] or not data["by_id"][req["block"]].get("available", True):
            raise ValueError(f"Bloque desconocido o no disponible: {req['block']!r}.")
        asked[take_index[k]] = req["block"]
    plan, locked = [], []
    for i, it in enumerate(items):
        f0, f1 = spans[i]
        seg = edl.segments[i]
        mid = (f0 + f1) / 2 / fps + music_start
        label = section_of(mid, bm)
        target = data["curve"].get(label, 2)
        if it["kind"] == "panel":
            plan.append({"item": i, "block": "panel", "intensity": data["by_id"]["panel"]["intensity"],
                         "section": label, "target": target, "frames": [f0, f1]})
            continue
        m = maps.get(i)
        p0 = seg.pieces[0]
        a, b = p0.start, p0.end
        block = data["by_id"][asked[i]] if i in asked else choose(target, label, m, a, b, data)
        locked += [{**x, "item": i} for x in _recipe(block, edl, seg, it, m, f0, f1, beat_frames, fps)]
        plan.append({"item": i, "block": block["id"], "intensity": block["intensity"], "section": label,
                     "target": target, "frames": [f0, f1], "hits": len(_hits(m, seg.pieces[0].start, seg.pieces[-1].end)),
                     **({"asked": True} if i in asked else {})})
    # a retimed take ends on another frame: its panel pause freezes that one
    for i, it in enumerate(items):
        if it["kind"] == "panel" and i > 0:
            last = edl.segments[i - 1].pieces[-1]
            n_last = round(last.dur * fps)
            t_last = last.start + (n_last - 1) / fps * last.speed
            edl.segments[i].pieces = [Piece(t_last, t_last, p.dur) for p in edl.segments[i].pieces]
    # the hit's frame, recomputed from the take's FINAL pieces (not copied from the event)
    for x in locked:
        src = (maps.get(x["item"]) or {}).get("fps")
        x["hit_frame"] = fit.out_frame(edl.segments[x["item"]].pieces, spans[x["item"]][0], fps, x["hit_t"], src)
    transitions = _transitions(edl, items, spans, plan, maps, fps, data["transitions"])
    return {"blocks": plan, "locked": locked, "transitions": transitions,
            "intensity": _section_intensity(plan, fps)}


def _event_at(edl: Edl, kind: str, frame: int, fps: int, **kw) -> Event:
    e = Event(frame / fps, kind, **kw)
    edl.events.append(e)
    return e


def _recipe(block: dict, edl: Edl, seg, it: dict, m: dict | None, f0: int, f1: int,
            beat_frames: list[int], fps: int) -> list[dict]:
    """The block's effects on this take. Returns the hit-locked ones."""
    n = f1 - f0
    locked = []
    bid = block["id"]
    src_fps = m["fps"] if m else None

    def lock(kind: str, frame: int, hit: dict, **kw) -> None:
        _event_at(edl, kind, frame, fps, **kw)
        locked.append({"kind": kind, "frame": frame, "hit_t": hit["t"]})

    if bid in ("calma", "texto"):
        cx, cy = _focus(m, seg.pieces[0].start + (seg.pieces[0].end - seg.pieces[0].start) / 2)
        _event_at(edl, "push", f0, fps, level=0.05, dur=n / fps, x=cx, y=cy)
    elif bid == "camara_lenta":
        sp = block.get("speed", 0.6)
        p = seg.pieces[0]
        seg.pieces = [Piece(p.start, p.start + n / fps * sp, n / fps)]
        seg.dedupe = True
        seg.interpolate = True
        hits = _hits(m, seg.pieces[0].start, seg.pieces[0].end)
        if hits:
            h = max(hits, key=lambda e: e["score"])
            frame = fit.out_frame(seg.pieces, f0, fps, h["t"], src_fps)
            lock("zoom", frame, h, intensity=0.8, x=h["cx"], y=h["cy"])
    elif bid == "rampa":
        p = seg.pieces[0]
        hits = _hits(m, p.start, p.end)
        if hits:
            h = max(hits, key=lambda e: e["score"])
            natural = fit.out_frame(seg.pieces, f0, fps, h["t"], src_fps)
            beat = fit.nearest_beat(beat_frames, f0, f1, natural)
            if beat is not None:
                seg.pieces = fit.fit_hit(p.start, n, p.speed, h["t"], beat - f0, fps)
                frame = beat
            else:
                frame = natural
            lock("flash", frame, h, intensity=0.7, dur=0.12)
            lock("shake", frame, h, intensity=0.7, dur=0.25, angle=h.get("angle"))
    elif bid == "rafaga_zooms":
        steps = [b for b in beat_frames if f0 <= b < f1] or [f0]
        for k, b in enumerate(steps):
            nxt = steps[k + 1] if k + 1 < len(steps) else f1
            cx, cy = _focus(m, seg.pieces[0].start + (b - f0) / fps * seg.pieces[0].speed)
            _event_at(edl, "hold", b, fps, level=min(0.24, 0.06 * (k + 1)), dur=max(1, nxt - b) / fps, x=cx, y=cy)
    elif bid == "estrobo":
        _event_at(edl, "strobe", f0, fps, intensity=0.6, dur=n / fps, every=2)
    elif bid == "rafaga_golpes":
        p = seg.pieces[0]
        hits = _hits(m, p.start, p.end)
        done: list[int] = []
        strongest = max(hits, key=lambda e: e["score"]) if hits else None
        cycle = ("flash", "shake", "zoom")
        for h in hits:
            frame = fit.out_frame(seg.pieces, f0, fps, h["t"], src_fps)
            if frame is None or any(abs(frame - d) < HIT_GAP for d in done):
                continue
            kind = cycle[len(done) % 3]
            kw = {"flash": {"intensity": 0.6, "dur": 0.1},
                  "shake": {"intensity": 0.8, "dur": 0.22, "angle": h.get("angle")},
                  "zoom": {"intensity": 0.9, "x": h["cx"], "y": h["cy"]}}[kind]
            lock(kind, frame, h, **kw)
            if h is strongest:
                lock("invert", frame, h, dur=1 / fps)
            done.append(frame)
    return locked


def _transitions(edl: Edl, items: list[dict], spans, plan: list[dict], maps: dict, fps: int,
                 rules: dict) -> list[dict]:
    """A transition on the cuts between two takes from intensity `from_intensity`
    up: types in rotation (at least min_types, none over max_per_type)."""
    out = []
    count = {t: 0 for t in rules["types"]}
    kinds = list(rules["types"])
    k = 0
    for i in range(len(items) - 1):
        if items[i]["kind"] != "take" or items[i + 1]["kind"] != "take":
            continue                          # the cut into or out of a panel pause stays dry
        if max(plan[i]["intensity"], plan[i + 1]["intensity"]) < rules["from_intensity"]:
            continue
        cut = spans[i][1]
        for _ in range(len(kinds)):
            kind = kinds[k % len(kinds)]
            k += 1
            if count[kind] < rules["max_per_type"]:
                break
        else:
            continue                           # every type used up: a hard cut
        m = maps.get(i)
        angle = None
        if kind == "whip" and m:
            near = [e for e in m["events"] if e["type"] in ("pan", "impact", "smear") and e.get("angle") is not None]
            end_t = edl.segments[i].pieces[-1].end
            if near:
                angle = min(near, key=lambda e: abs(e["t"] - end_t))["angle"]
        if kind == "whip":
            _event_at(edl, "whip", cut, fps, intensity=0.8, dur=0.2, angle=angle)
        elif kind == "flash":
            _event_at(edl, "flash", cut, fps, intensity=0.6, dur=0.12)
        elif kind == "black":
            _event_at(edl, "black", cut, fps, dur=2 / fps)
        elif kind == "invert":
            _event_at(edl, "invert", cut, fps, dur=1 / fps)
        else:
            _event_at(edl, "zoom", cut, fps, intensity=0.9)
        count[kind] += 1
        out.append({"frame": cut, "kind": kind, **({"angle": round(angle, 1)} if angle is not None else {})})
    return out


def _section_intensity(plan: list[dict], fps: int) -> dict:
    """Mean block intensity per section, weighted by frames."""
    acc: dict[str, list[float]] = {}
    for p in plan:
        f0, f1 = p["frames"]
        acc.setdefault(p["section"], [0.0, 0.0])
        acc[p["section"]][0] += p["intensity"] * (f1 - f0)
        acc[p["section"]][1] += f1 - f0
    return {k: round(v[0] / v[1], 2) for k, v in acc.items() if v[1]}


def check(info: dict, fps: int) -> dict:
    """The phase-4 criterion on a plan: every hit-locked effect within ±1 frame
    of its hit, drop intensity above the buildup, transition variety."""
    worst = max((abs(x["frame"] - x["hit_frame"]) for x in info["locked"]), default=0)
    kinds = [t["kind"] for t in info["transitions"]]
    counts = {k: kinds.count(k) for k in set(kinds)}
    inten = info["intensity"]
    return {"locked": len(info["locked"]), "max_offset_frames": worst,
            "drop": inten.get("drop"), "buildup": inten.get("buildup"),
            "drop_above_buildup": inten.get("drop") is not None and inten.get("buildup") is not None
            and inten["drop"] > inten["buildup"],
            "transitions": counts, "transition_types": len(counts),
            "max_per_type": max(counts.values(), default=0)}
