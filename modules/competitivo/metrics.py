"""A record per job of competitivo, and the indicators made from them.

Every phase 1, summary, edit, daily summary and transcription appends one
line to METRICS_FILE (JSON lines, next to the videos: it survives restarts
and "Limpiar archivos"): how long each stage took, what the automatic parts
decided (anchors, clock, sure kills / full view), what was corrected by hand,
retries, NVENC fallbacks and warnings. summary() turns them into the
indicators of the Métricas view: is it getting faster, does the automatic
camera need fewer corrections, does anything fail more often.
"""
from __future__ import annotations

import json
import os
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone

import config
from core import ffmpeg_runner, scheduler

_lock = threading.Lock()
FILE: str | None = None          # another file (the tests: never the user's records)


def metrics_file() -> str:
    return FILE or os.path.join(config.COMPETITIVO_FOLDER, "_metricas", "jobs.jsonl")


_active: dict[int, "Run"] = {}          # the runs going on now (how many jobs share the machine)
ACTIVE_MAX_S = 6 * 3600                 # one that never finished does not count forever


class Run:
    """One job's record: Run(kind, job_id, ...) → mark(stage) → set(...) → finish(status).

    Besides the time per stage it keeps how many other jobs were running when it
    started (`concurrent_at_start`) and the most there were at once while it ran
    (`concurrent_peak`), and, when it finishes, the seconds it waited for each
    resource of core/scheduler.py (`waits`): the cost of sharing the machine."""

    def __init__(self, kind: str, job_id: str, **fields):
        self.kind, self.job_id = kind, job_id
        self.t0 = self.last = time.monotonic()
        self.stages: dict[str, float] = {}
        self.fields = dict(fields)
        self.nvenc0 = ffmpeg_runner.stats["nvenc_fallbacks"]
        self.done = False
        with _lock:
            for key in [k for k, r in _active.items() if self.t0 - r.t0 > ACTIVE_MAX_S]:
                _active.pop(key)
            self.concurrent_at_start = len(_active)
            self.concurrent_peak = len(_active) + 1
            _active[id(self)] = self
            for r in _active.values():
                r.concurrent_peak = max(r.concurrent_peak, len(_active))

    def mark(self, stage: str) -> None:
        now = time.monotonic()
        self.stages[stage] = round(self.stages.get(stage, 0.0) + now - self.last, 1)
        self.last = now

    def set(self, **fields) -> None:
        self.fields.update({k: v for k, v in fields.items() if v is not None})

    def finish(self, status: str, error: str | None = None) -> dict | None:
        if self.done:
            return None
        self.done = True
        with _lock:
            _active.pop(id(self), None)
        waits = scheduler.take_waits(self.job_id)
        record = {"kind": self.kind, "job_id": self.job_id, "at": datetime.now(timezone.utc).isoformat(),
                  "status": status, "seconds": round(time.monotonic() - self.t0, 1), "stages": self.stages,
                  "nvenc_fallbacks": ffmpeg_runner.stats["nvenc_fallbacks"] - self.nvenc0,
                  "concurrent_at_start": self.concurrent_at_start, "concurrent_peak": self.concurrent_peak,
                  **({"waits": waits} if waits else {}), **self.fields}
        if error:
            record["error"] = str(error)[:300]
        write(record)
        return record


def write(record: dict) -> None:
    path = metrics_file()
    try:
        with _lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as exc:                       # the metrics never break a job
        print(f"[metrics] no se pudo escribir: {exc}")


def load(days: int | None = None) -> list[dict]:
    path = metrics_file()
    if not os.path.isfile(path):
        return []
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if since and datetime.fromisoformat(r["at"]) < since:
                continue
            out.append(r)
    return out


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def _pct(num, den):
    return round(100 * num / den, 1) if den else None


def summary(records: list[dict]) -> dict:
    """The indicators of a set of records."""
    by_kind: dict[str, list[dict]] = {}
    for r in records:
        by_kind.setdefault(r["kind"], []).append(r)
    kinds = {}
    for kind, rs in sorted(by_kind.items()):
        ok = [r for r in rs if r["status"] in ("ok", "paused")]
        kinds[kind] = {"jobs": len(rs), "ok": len(ok), "failed": len(rs) - len(ok),
                       "median_s": _median([r["seconds"] for r in ok]),
                       "median_video_s": _median([r.get("video_s") for r in ok])}
    phase1 = by_kind.get("fase1", [])
    anchors = [c for r in phase1 for c in r.get("anchors", [])]
    clocks = [c for r in phase1 for c in r.get("clock_calibrated", [])]
    edits = by_kind.get("edit", []) + by_kind.get("edit_render", [])
    renders = [r for r in edits if r["status"] == "ok"]
    # Only what was rendered: an edit paused for the storyboard is counted by its edit_render.
    shots = [r for r in by_kind.get("resumen", []) + edits if r.get("kills") and r["status"] == "ok"]
    kills = sum(r["kills"] for r in shots)
    days: dict[str, dict[str, int]] = {}
    for r in records:
        d = r["at"][:10]
        days.setdefault(d, {})
        days[d][r["kind"]] = days[d].get(r["kind"], 0) + 1
    return {
        "jobs": len(records),
        "kinds": kinds,
        "anchors_high_pct": _pct(sum(1 for c in anchors if c == "alta"), len(anchors)),
        "anchors": len(anchors),
        "clock_calibrated_pct": _pct(sum(1 for c in clocks if c), len(clocks)),
        "kills": kills,
        "kills_sure_pct": _pct(sum(r.get("kills_sure", 0) for r in shots), kills),
        "kills_full_pct": _pct(sum(r.get("kills_full", 0) for r in shots), kills),
        "overrides_per_edit": round(sum(r.get("overrides", 0) for r in renders) / len(renders), 2) if renders else None,
        "retries": sum(r.get("retries", 0) for r in records),
        "nvenc_fallbacks": sum(r.get("nvenc_fallbacks", 0) for r in records),
        "skipped_spans": sum(r.get("skipped", 0) for r in records),
        "warnings": sum(1 for r in records if r.get("warning")),
        # What it costs to share the machine, and how long a clip takes.
        "queue_wait_s": round(sum(sum((r.get("waits") or {}).values()) for r in records), 1),
        "concurrent_median": _median([r.get("concurrent_peak") for r in records]),
        "s_per_clip": _median([r["seconds"] / r["clips"] for r in by_kind.get("resumen", [])
                               if r["status"] == "ok" and r.get("clips")]),
        "s_per_clip_alone": _median([r["seconds"] / r["clips"] for r in by_kind.get("resumen", [])
                                     if r["status"] == "ok" and r.get("clips") and r.get("concurrent_peak", 1) <= 1]),
        "failed": sum(1 for r in records if r["status"] == "error"),
        "presets": {p: sum(1 for r in records if r.get("preset") == p) for p in {r.get("preset") for r in records} if p},
        "days": dict(sorted(days.items())),
    }


def discarded() -> int:
    """Videos sent to _descartados (a summary rendered again)."""
    n = 0
    for root, dirs, files in os.walk(config.COMPETITIVO_FOLDER):
        if os.path.basename(root) == "_descartados":
            n += sum(1 for f in files if f.endswith(".mp4")) + len(dirs)
            dirs[:] = []
    return n
