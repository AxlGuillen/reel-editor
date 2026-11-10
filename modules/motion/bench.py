"""Measures the motion map against hand-annotated ground truth.

    python -m modules.motion.bench pairs <episode> [--sheet folder] [--refresh]
    python -m modules.motion.bench speed <video> [start end]

`pairs` uses tests/motion/pairs.json (the 10 panel–scene pairs of Magna vs
Dante): a hit pair passes with an impact within ±2 frames of the annotated
frame; a pose pair passes when no impact falls inside its window and, if the
window ends on a cut, the cut is found within ±2 frames. Each pair is
analyzed in a span of ±SPAN_S seconds around its frame.
"""
from __future__ import annotations

import json
import os
import sys
import time

from modules.motion import processor

PAIRS_FILE = os.path.join(os.path.dirname(__file__), "..", "..", "tests", "motion", "pairs.json")
SPAN_S = 5.0
TOLERANCE = 2


def load_pairs() -> dict:
    with open(PAIRS_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def judge(pair: dict, m: dict) -> dict:
    """Pass/fail of one pair against its motion map."""
    ev = m["events"]
    impacts = [e for e in ev if e["type"] == "impact"]
    cuts = [e for e in ev if e["type"] == "cut"]
    a, b = pair["window"]
    out = {"n": pair["n"], "kind": pair["kind"], "frame": pair["frame"]}
    if pair["kind"] == "hit":
        # a flurry was annotated with its other contacts ("also") before measuring
        targets = [pair["frame"]] + pair.get("also", [])
        pairs_ = [(abs(e["f"] - t), e, t) for e in impacts for t in targets]
        dist, best, target = min(pairs_, key=lambda p: (p[0], p[2] != pair["frame"]), default=(None, None, None))
        out["nearest"] = None if best is None else {"f": best["f"], "score": best["score"], "dir": best.get("dir"),
                                                    "signals": best["signals"]}
        out["delta"] = None if best is None else best["f"] - target
        if target is not None and target != pair["frame"]:
            out["matched_also"] = target
        out["ok"] = best is not None and dist <= TOLERANCE
    else:
        inside = [e["f"] for e in impacts if a - TOLERANCE <= e["f"] <= b + TOLERANCE]
        out["impacts_inside"] = inside
        ok = not inside
        if pair.get("cut"):
            near = min(cuts, key=lambda e: abs(e["f"] - pair["cut"]), default=None)
            out["cut_found"] = None if near is None else near["f"]
            ok = ok and near is not None and abs(near["f"] - pair["cut"]) <= TOLERANCE
        out["ok"] = ok
    return out


def run_pairs(episode: str, refresh: bool = False, sheet_folder: str | None = None) -> dict:
    data = load_pairs()
    fps = data["fps"]
    rows, maps = [], {}
    frames = elapsed = 0.0
    for pair in data["pairs"]:
        t = pair["frame"] / fps
        m = processor.get(episode, t - SPAN_S, t + SPAN_S, refresh=refresh)
        maps[pair["n"]] = m
        frames += m["frames_count"]
        elapsed += m["elapsed_s"]
        r = judge(pair, m)
        rows.append(r)
        print(f"{'OK ' if r['ok'] else 'NO '} par {pair['n']:2} {pair['kind']:4} cuadro {pair['frame']} → "
              + json.dumps({k: v for k, v in r.items() if k not in ("n", 'kind', 'frame', 'ok')}, ensure_ascii=False))
    hits = [r for r in rows if r["kind"] == "hit"]
    report = {"ok": sum(r["ok"] for r in rows), "total": len(rows),
              "hits_ok": sum(r["ok"] for r in hits), "hits": len(hits),
              "fps_analysis": round(frames / max(elapsed, 1e-6), 1), "rows": rows}
    print(f"\n{report['ok']}/{report['total']} parejas · golpes {report['hits_ok']}/{report['hits']} · "
          f"{report['fps_analysis']} cuadros/s de análisis")
    if sheet_folder:
        from modules.motion import sheet
        os.makedirs(sheet_folder, exist_ok=True)
        path = sheet.pairs_sheet(episode, data["pairs"], maps, rows, os.path.join(sheet_folder, "parejas.jpg"))
        print("hoja:", path)
    return report


def run_speed(video: str, start: float | None = None, end: float | None = None) -> None:
    t0 = time.time()
    m = processor.get(video, start, end, refresh=True)
    wall = time.time() - t0
    print(f"{m['frames_count']} cuadros · análisis {m['elapsed_s']} s ({m['fps_analysis']} cuadros/s) · "
          f"total con decodificación {wall:.2f} s ({m['frames_count'] / wall:.1f} cuadros/s)")


if __name__ == "__main__":
    cmd, args = sys.argv[1], [a for a in sys.argv[2:] if not a.startswith("--")]
    if cmd == "pairs":
        folder = sys.argv[sys.argv.index("--sheet") + 1] if "--sheet" in sys.argv else None
        if folder in args:
            args.remove(folder)
        run_pairs(args[0], refresh="--refresh" in sys.argv, sheet_folder=folder)
    elif cmd == "speed":
        run_speed(args[0], *(float(x) for x in args[1:3]))
