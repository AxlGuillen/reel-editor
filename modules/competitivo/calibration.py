"""The calibration set: real cases of the VODs with the result each detector
must give, to run before every day of matches and after every change.

The unit tests use synthetic videos and one fixture; the detectors fail on
real broadcasts (a split camera that was not, a webcam box one day wider, a
steal said in two sentences). Each case is a real span (or file) and what was
checked by eye on it. They live in config.CALIBRATION_FOLDER, outside git:

    cases.json            [{id, check, file | files, params, expect, tol, note, known_issue}]
    media/                the spans (cut from the VODs with -c copy)
    reports/last.json     the last report (and report_<date>.json for each run)

Checks: kill_changes (cambios_kills: the base of the sync), split (split_ranges), camera_box (detect_camera_box), clock
(clock_at), ambient (ambient_ranges), voice_peaks (voice.peaks with the
medians of the case's spans), moments (transcript.moments on a saved
transcription) and vod_info (fuente.vod_info on a stored yt-dlp info).

    python -m modules.competitivo.calibration            # every case
    python -m modules.competitivo.calibration split-tl   # some cases
    python -m modules.competitivo.calibration --accept split-tl
        # a deliberate change: what it measures now becomes the expected result

A case with `known_issue` (what is wrong, in words) expects the RIGHT result
and is known to fail: it is reported as "known", does not fail the run, and
is reported as "fixed" the day it passes (then the flag goes away).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

import config
from modules.competitivo import clock, fuente, layout, metrics, transcript, vocab, voice

CASES_FILE = "cases.json"


def folder() -> str:
    return config.CALIBRATION_FOLDER


def _path(name: str) -> str:
    return os.path.join(folder(), name)


def load_cases() -> list[dict]:
    path = _path(CASES_FILE)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("cases") or []


def save_cases(cases: list[dict]) -> None:
    os.makedirs(folder(), exist_ok=True)
    with open(_path(CASES_FILE), "w", encoding="utf-8") as fh:
        json.dump({"cases": cases}, fh, ensure_ascii=False, indent=1)


# --- Measures and comparisons ----------------------------------------------------
def _ranges(rs) -> list[list[float]]:
    return [[round(a, 2), round(b, 2)] for a, b in rs]


def _same_ranges(got, exp, tol) -> bool:
    return len(got) == len(exp) and all(abs(a - x) <= tol and abs(b - y) <= tol
                                        for (a, b), (x, y) in zip(got, exp))


def _m_kill_changes(case):
    return [[round(t, 2), side] for t, side in layout.cambios_kills(_path(case["file"]))]


def _c_kill_changes(got, exp, tol):
    return len(got) == len(exp) and all(s == es and abs(t - et) <= tol for (t, s), (et, es) in zip(got, exp))


def _m_split(case):
    return _ranges(layout.split_ranges(_path(case["file"])))


def _m_ambient(case):
    return _ranges(layout.ambient_ranges(_path(case["file"])))


def _m_camera_box(case):
    return list(layout.detect_camera_box(_path(case["file"])))


def _c_camera_box(got, exp, tol):
    return all(abs(a - b) <= tol for a, b in zip(got, exp))


def _m_clock(case):
    return [[t, (lambda v: round(v, 2) if v is not None else None)(clock.clock_at(_path(case["file"]), t))]
            for t in case["params"]["at"]]


def _c_clock(got, exp, tol):
    return all(g is not None and abs(g - e) <= tol for (_, g), (_, e) in zip(got, exp))


def _m_voice_peaks(case):
    files = case["files"]
    feats = {f: voice.features(_path(f)) for f in files}
    db_med, f0_med = voice.medians(list(feats.values()))
    return {f: [[round(a, 2), round(b, 2)] for a, b, _ in voice.peaks(feats[f], db_med, f0_med)] for f in files}


def _c_voice_peaks(got, exp, tol):
    """Every expected peak (its center) is inside a measured one; no file has twice as many."""
    for f, peaks in exp.items():
        have = got.get(f) or []
        for a, b in peaks:
            mid = (a + b) / 2
            if not any(x - tol <= mid <= y + tol for x, y in have):
                return False
        if len(have) > 2 * max(1, len(peaks)):
            return False
    return True


def _m_moments(case):
    with open(_path(case["file"]), encoding="utf-8") as fh:
        t = json.load(fh)
    fixes = vocab.load()
    segs = [{**s, "text": " ".join(w["word"] for w in vocab.correct(
        [{"word": w["word"], "start": 0, "end": 0} for w in s["words"]], fixes))} for s in t["segments"]]
    voc = t.get("vocabulary") or {}
    lo, hi = case["params"]["from"], case["params"]["to"]
    return [{"clock": m["clock"], "clock_s": m["clock_s"], "kind": m["kind"], "players": m["players"]}
            for m in transcript.moments(segs, [], voc.get("players", []), voc.get("champions", []))
            if lo <= m["clock_s"] <= hi]


def _c_moments(got, exp, tol):
    """The expected kind is there, at its time (± tol) and with its players."""
    for e in exp:
        if not any(m["kind"] == e["kind"] and abs(m["clock_s"] - e["clock_s"]) <= tol
                   and set(e.get("players", [])) <= set(m["players"]) for m in got):
            return False
    return True


def _m_vod_info(case):
    try:
        v = fuente.vod_info(case["params"]["info"])
        return {"platform": v["platform"], "inicio_utc": v["inicio_utc"].isoformat()}
    except RuntimeError as exc:
        return {"error": str(exc)}


def _c_vod_info(got, exp, tol):
    if "error" in exp:
        return exp["error"] in (got.get("error") or "")
    return got == exp


CHECKS = {
    "kill_changes": (_m_kill_changes, _c_kill_changes),
    "split": (_m_split, _same_ranges),
    "ambient": (_m_ambient, _same_ranges),
    "camera_box": (_m_camera_box, _c_camera_box),
    "clock": (_m_clock, _c_clock),
    "voice_peaks": (_m_voice_peaks, _c_voice_peaks),
    "moments": (_m_moments, _c_moments),
    "vod_info": (_m_vod_info, _c_vod_info),
}
DEFAULT_TOL = {"kill_changes": 0.3, "split": 1.0, "ambient": 1.5, "camera_box": 6, "clock": 1.0, "voice_peaks": 1.0, "moments": 3.0}


def _files(case) -> list[str]:
    return case.get("files") or ([case["file"]] if case.get("file") else [])


def measure(case: dict):
    return CHECKS[case["check"]][0](case)


def run_case(case: dict) -> dict:
    out = {"id": case["id"], "check": case["check"], "note": case.get("note", ""), "expected": case.get("expect")}
    missing = [f for f in _files(case) if not os.path.isfile(_path(f))]
    if case["check"] not in CHECKS:
        return {**out, "status": "error", "error": f"No existe el chequeo «{case['check']}»."}
    if missing:
        return {**out, "status": "missing", "error": f"Falta {missing[0]} en la carpeta de calibración."}
    t0 = time.monotonic()
    try:
        got = measure(case)
    except Exception as exc:  # noqa: BLE001 - a broken detector is a failed case, not a broken run
        return {**out, "status": "error", "error": str(exc), "seconds": round(time.monotonic() - t0, 1)}
    tol = case.get("tol", DEFAULT_TOL.get(case["check"], 0))
    ok = CHECKS[case["check"]][1](got, case["expect"], tol)
    if case.get("known_issue"):
        status, out["known_issue"] = ("fixed" if ok else "known"), case["known_issue"]
    else:
        status = "ok" if ok else "fail"
    return {**out, "status": status, "measured": got, "tol": tol, "seconds": round(time.monotonic() - t0, 1)}


def run(ids: list[str] | None = None, progress=None) -> dict:
    """Runs the cases (all, or those ids) and saves the report."""
    cases = [c for c in load_cases() if not ids or c["id"] in ids]
    rec = metrics.Run("calibracion", datetime.now().strftime("%Y%m%d-%H%M%S"), cases=len(cases))
    results = []
    for k, case in enumerate(cases):
        if progress:
            progress(k, len(cases), case["id"])
        results.append(run_case(case))
    count = {s: sum(1 for r in results if r["status"] == s)
             for s in ("ok", "fail", "error", "missing", "known", "fixed")}
    report = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "folder": folder(),
              "cases": len(results), **count, "passed": count["fail"] == count["error"] == 0 and bool(results),
              "seconds": round(sum(r.get("seconds", 0) for r in results), 1), "results": results}
    reports = _path("reports")
    os.makedirs(reports, exist_ok=True)
    for name in (f"report_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json", "last.json"):
        with open(os.path.join(reports, name), "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
    rec.set(ok=count["ok"], failed=count["fail"] + count["error"], missing=count["missing"], known=count["known"],
            failed_ids=[r["id"] for r in results if r["status"] in ("fail", "error")])
    rec.finish("ok" if report["passed"] else "error")
    return report


def last_report() -> dict | None:
    path = os.path.join(_path("reports"), "last.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def accept(ids: list[str]) -> list[str]:
    """What the cases measure now becomes what they expect (a deliberate change)."""
    cases, done = load_cases(), []
    for case in cases:
        if case["id"] in ids:
            case["expect"] = measure(case)
            case["accepted"] = datetime.now().astimezone().isoformat(timespec="seconds")
            done.append(case["id"])
    save_cases(cases)
    return done


def main(argv: list[str]) -> int:
    if argv[:1] == ["--accept"]:
        print("Aceptados:", ", ".join(accept(argv[1:])) or "ninguno")
        return 0
    if not load_cases():
        print(f"No hay casos en {_path(CASES_FILE)}.")
        return 1
    report = run(argv or None, lambda k, n, cid: print(f"[{k + 1}/{n}] {cid}…", flush=True))
    for r in report["results"]:
        mark = {"ok": "OK   ", "fail": "FALLA", "error": "ERROR", "missing": "FALTA", "known": "CONOC",
                "fixed": "ARREG"}[r["status"]]
        print(f"{mark} {r['id']:<22} {r.get('error') or r.get('known_issue') or ''}")
        if r["status"] == "fixed":
            print("      ya da bien: sacale `known_issue` al caso.")
        if r["status"] in ("fail", "known"):
            print(f"      esperado {json.dumps(r['expected'], ensure_ascii=False)[:300]}")
            print(f"      medido   {json.dumps(r['measured'], ensure_ascii=False)[:300]}")
    print(f"{report['ok']}/{report['cases']} bien, {report['fail']} fallas, {report['error']} errores, "
          f"{report['known']} conocidas, {report['missing']} sin archivo ({report['seconds']} s).")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
