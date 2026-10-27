"""A fixed benchmark of the clip pipeline, to measure before and after a change.

It runs on the spans of the calibration folder (real cuts of the VODs, see
calibration.py), so it needs no network and the same clips are measured every
time. Per span it times what /finish does to each clip:

    analysis  cambios_kills + estimar_desfase + analizar + split_ranges +
              action_track per kill (the "analysis" the layout needs)
    anchors   what phase 1 does with its window: cambios_kills + the clock
              calibration + the control frame
    render    the one encode of the clip (layout.build_command)

and samples the CPU (typeperf) and the GPU (nvidia-smi: 3D, encoder, decoder)
while each part runs. The report goes to CALIBRATION_FOLDER/bench/.

    python -m modules.competitivo.bench                 # every span
    python -m modules.competitivo.bench --only analysis # skip the render
    python -m modules.competitivo.bench --label after-p3
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime

import config
from core import ffmpeg_runner, job_manager
from modules.competitivo import calibration, clock, layout

# Spans of the calibration set: 30–70 s of 1080p60 with fights of different sizes.
SPANS = ["TLAW-CFO_1-1.mp4", "IG-GEN_1-3.mp4", "LOS-T1_1-14.mp4", "DK-G2_1-10.mp4", "ig-gen_ambiente.mp4"]
PARTS = ("analysis", "anchors", "render")


class Sampler:
    """CPU and GPU use every ~1 s while a part runs (Windows: typeperf + nvidia-smi)."""

    def __init__(self):
        self.cpu: list[float] = []
        self.gpu: list[tuple[float, float, float]] = []

    def __enter__(self):
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=5)

    def _loop(self):
        while not self._stop.is_set():
            try:
                # Counter indices (238 = Processor, 6 = % Processor Time): the names are localized.
                out = subprocess.run(["typeperf", r"\238(_Total)\6", "-sc", "1"],
                                     capture_output=True, text=True, timeout=5).stdout.splitlines()
                row = next(x for x in out if x.startswith('"') and "PDH" not in x)
                self.cpu.append(float(row.split(",")[1].strip('"')))
            except Exception:  # noqa: BLE001 - a sample that fails is only a missing sample
                pass
            try:
                q = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,utilization.encoder,utilization.decoder",
                                    "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                   timeout=5).stdout.strip().split(",")
                self.gpu.append(tuple(float(x) for x in q))
            except Exception:  # noqa: BLE001
                pass

    def summary(self) -> dict:
        out = {"cpu_pct": round(statistics.mean(self.cpu), 1) if self.cpu else None}
        for i, name in enumerate(("gpu_3d_pct", "nvenc_pct", "nvdec_pct")):
            out[name] = round(statistics.mean(g[i] for g in self.gpu), 1) if self.gpu else None
        return out


def _duration(path: str) -> float:
    return ffmpeg_runner.probe_duration(path) or 0.0


def analysis(span: str, scan: bool) -> dict:
    """What /finish does to a clip before its render (see processor.finish)."""
    kw = {}
    if scan:
        kw = {"scan": layout.Scan(span, layout.SCAN_CLIP)}
    seen = layout.cambios_kills(span, **kw)
    dur = _duration(span)
    t_kill = seen[0][0] if seen else min(5.0, dur / 2)
    an = layout.analizar(span, t_kill, 0.0, dur, **kw)
    split = layout.split_ranges(span, **kw)
    tracks = [layout.action_track(span, t, **kw) for t, _ in seen if not any(a <= t <= b for a, b in split)]
    return {"kills": len(seen), "split": len(split), "tracks": len(tracks), "camera_y": an.camara_y}


def anchors(span: str, scan: bool) -> dict:
    """What phase 1 does with its window: kill counter, clock calibration, control frame."""
    dur = _duration(span)
    kw = {}
    if scan:
        kw = {"scan": layout.Scan(span, layout.SCAN_ANCHOR)}
    seen = layout.cambios_kills(span, **kw)
    zero = clock.calibrate(span, [3.0 * k for k in range(1, int(dur / 3))][:24], **kw)
    out = os.path.join(config.OUTPUT_FOLDER, "bench_frame.jpg")
    os.makedirs(config.OUTPUT_FOLDER, exist_ok=True)
    ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-y", "-ss", f"{dur / 2:.2f}", "-i", span,
                           "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "3", out])
    return {"kills": len(seen), "clock_zero": zero}


def render(span: str, scan: bool) -> dict:
    dur = _duration(span)
    an = layout.analizar(span, min(5.0, dur / 2), 0.0, dur)
    out = os.path.join(config.OUTPUT_FOLDER, f"bench_render_{threading.get_ident()}.mp4")
    os.makedirs(config.OUTPUT_FOLDER, exist_ok=True)
    cmd = layout.build_command(span, out, an.vivo_desde, an.vivo_hasta - an.vivo_desde, an.camara or an.camara_y,
                               zoom=1.4)
    job = job_manager.create_job("bench", output_path="")
    ffmpeg_runner.run(cmd, job, total_duration=an.vivo_hasta - an.vivo_desde)
    size = os.path.getsize(out)
    os.remove(out)
    return {"video_s": round(an.vivo_hasta - an.vivo_desde, 2), "mbps": round(size * 8 / (an.vivo_hasta - an.vivo_desde) / 1e6, 2)}


def run(parts: tuple = PARTS, label: str = "", scan: bool = False, spans: list[str] | None = None) -> dict:
    media = os.path.join(calibration.folder(), "media")
    todo = [s for s in (spans or SPANS) if os.path.isfile(os.path.join(media, s))]
    if not todo:
        raise RuntimeError(f"No hay tramos de calibración en {media}.")
    fns = {"analysis": analysis, "anchors": anchors, "render": render}
    results, totals = [], {p: {"s": 0.0, "video_s": 0.0} for p in parts}
    samples: dict[str, Sampler] = {p: Sampler() for p in parts}
    for name in todo:
        span = os.path.join(media, name)
        row = {"span": name, "video_s": round(_duration(span), 1)}
        for p in parts:
            # One sampler per part, but it only counts while that part runs.
            with samples[p]:
                t0 = time.monotonic()
                info = fns[p](span, scan)
                s = time.monotonic() - t0
            row[p] = {"s": round(s, 2), "x_realtime": round(row["video_s"] / s, 2) if s else None, **info}
            totals[p]["s"] += s
            totals[p]["video_s"] += row["video_s"]
        results.append(row)
    summary = {p: {"s": round(t["s"], 1), "video_s": round(t["video_s"], 1),
                   "x_realtime": round(t["video_s"] / t["s"], 2), **samples[p].summary()}
               for p, t in totals.items()}
    report = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "label": label,
              "scan": scan, "encoder": ffmpeg_runner.encoder_info(), "summary": summary, "spans": results}
    folder = os.path.join(calibration.folder(), "bench")
    os.makedirs(folder, exist_ok=True)
    name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{label or 'bench'}.json"
    with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    return report


def concurrency(n: int = 4, capacities: tuple = (1, 2, 3, 4)) -> dict:
    """n renders started at the same time with each NVENC capacity of the scheduler:
    the wall time and the seconds per clip ALL together (what P1 of the plan measures)."""
    from core import scheduler
    media = os.path.join(calibration.folder(), "media")
    spans = [os.path.join(media, s) for s in (SPANS * n)[:n] if os.path.isfile(os.path.join(media, s))]
    out = []
    for cap in capacities:
        scheduler.get("nvenc").set_capacity(cap)
        per_clip = [0.0] * len(spans)

        def one(i, span):
            t0 = time.monotonic()
            render(span, False)
            per_clip[i] = time.monotonic() - t0
        threads = [threading.Thread(target=one, args=(i, sp)) for i, sp in enumerate(spans)]
        with Sampler() as sampler:
            t0 = time.monotonic()
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            wall = time.monotonic() - t0
        out.append({"nvenc_slots": cap, "clips": len(spans), "wall_s": round(wall, 1),
                    "s_per_clip_together": round(wall / len(spans), 1),
                    "first_done_s": round(min(per_clip), 1), "last_done_s": round(max(per_clip), 1),
                    **sampler.summary()})
    scheduler.get("nvenc").set_capacity(scheduler.capacity_of("nvenc"))
    return {"at": datetime.now().astimezone().isoformat(timespec="seconds"), "runs": out}


def main(argv: list[str]) -> int:
    if "--concurrency" in argv:
        n = int(argv[argv.index("--concurrency") + 1])
        caps = tuple(int(x) for x in argv[argv.index("--slots") + 1].split(",")) if "--slots" in argv else (1, 2, 3, 4)
        report = concurrency(n, caps)
        folder = os.path.join(calibration.folder(), "bench")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_concurrency.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
        for r in report["runs"]:
            print(f"  nvenc {r['nvenc_slots']} slots: {r['clips']} clips in {r['wall_s']} s = {r['s_per_clip_together']} s per clip"
                  f" (first {r['first_done_s']} s, last {r['last_done_s']} s) | cpu {r['cpu_pct']} %"
                  f" nvenc {r['nvenc_pct']} %")
        return 0
    label = argv[argv.index("--label") + 1] if "--label" in argv else ""
    only = argv[argv.index("--only") + 1].split(",") if "--only" in argv else None
    report = run(tuple(only) if only else PARTS, label, scan="--scan" in argv)
    print(f"{report['label'] or 'bench'}  ({report['encoder']}, scan={report['scan']})")
    for p, s in report["summary"].items():
        print(f"  {p:<9} {s['s']:>7.1f} s for {s['video_s']:.0f} s of video = {s['x_realtime']}x"
              f" | cpu {s['cpu_pct']} %  gpu {s['gpu_3d_pct']} %  nvenc {s['nvenc_pct']} %  nvdec {s['nvdec_pct']} %")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
