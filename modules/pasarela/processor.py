"""Pasarela: a narrated video from end to end, run by Claude through the API.

    gameplay (file │ Insights highlight) ─vertical (cached)─┐
    voice ─Whisper (cached) + marked script → timeline ────┼─ final encode → mp4 + report
    deck slides + rest slide ─flattened band track ────────┘
    clips ({clip:id} in the script) ─ the voice pauses and they play whole

The rules are the approved standard (Tom's draft, 10/11/2026): plan.py. The
FFmpeg passes: render.py. This file runs them, caches what does not change
between two montages of the same gameplay (the vertical base, the
transcript), times each phase and writes the report.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import config
from core import ffmpeg_runner, file_utils, job_manager
from modules.pasarela import insights, plan, render
from modules.pasarela.schema import BAND_H, PasarelaParams, load_manifest, load_script
from modules.studio import processor as studio
from modules.subtitles import processor as subtitles
from modules.subtitles.schema import SubtitlesParams

BASE_DIR = os.path.join(config.PASARELA_CACHE, "base")
STT_DIR = os.path.join(config.PASARELA_CACHE, "stt")
WORK_DIR = os.path.join(config.PASARELA_CACHE, "work")
REPORTS_DIR = os.path.join(config.PASARELA_CACHE, "reports")
BAND_WORKERS = 4
SUB_Y = {"abajo": 1200, "arriba": 1660}     # subtitles over the gameplay, clear of the band
BASE_VERSION = 1                            # bump when the base's graph changes (invalidates the cache)

_LUFS = re.compile(r"^\s+I:\s+(-?[\d.]+|-inf) LUFS", re.M)
_PEAK = re.compile(r"^\s+Peak:\s+(-?[\d.]+|-inf) dBFS", re.M)


class Phases:
    """Seconds per phase, in order (the report's `tiempos`)."""

    def __init__(self):
        self.t0 = time.perf_counter()
        self.items: dict[str, float] = {}

    def run(self, name: str, fn, *args, **kwargs):
        t = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            self.items[name] = round(self.items.get(name, 0) + time.perf_counter() - t, 2)

    def report(self) -> dict:
        return {**self.items, "total": round(time.perf_counter() - self.t0, 2)}


def _key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _stage(job_id: str, progress: int, stage: str) -> None:
    job_manager.update_job(job_id, status="processing", progress=progress, stage=stage)


def loudness(path: str, stream: str = "0:a:0") -> tuple[float | None, float | None]:
    """(integrated LUFS, true peak dBFS) with ebur128."""
    out = ffmpeg_runner.capture(render.loudness_command(path, stream), stderr=True).decode("utf-8", "replace")
    lufs, peak = _LUFS.findall(out), _PEAK.findall(out)

    def num(v):
        return None if not v or v[-1] == "-inf" else float(v[-1])
    return num(lufs), num(peak)


# ----------------------------------------------------------------- clips

def collect_clips(params: PasarelaParams, manifest: list[dict], script: str) -> dict[str, dict]:
    """Every clip the video uses: the manifest's `tipo: clip` entries and the
    `{clip:id}` marks of the script, each with its file (the request's
    `clips` wins over the manifest's) and what FFmpeg says about it."""
    by_id = {str(m["id"]): m for m in manifest if m.get("tipo") == "clip"}
    ids = list(dict.fromkeys([*by_id, *plan.CLIP_MARK.findall(script), *params.clips]))
    out, missing = {}, []
    for cid in ids:
        req = params.clips.get(cid)
        entry = by_id.get(cid, {})
        file = (req.file if req and req.file else None) or \
            (os.path.join(params.slides, entry["archivo"]) if entry.get("archivo") else None)
        if not file or not os.path.isfile(file):
            missing.append(cid)
            continue
        dur = ffmpeg_runner.probe_duration(file)
        size = ffmpeg_runner.probe_resolution(file)
        if not dur or not size:
            raise ValueError(f"No pude leer el clip «{cid}» ({file}).")
        out[cid] = {"id": cid, "file": file, "dur": round(dur, 3), "full": size[1] > size[0],
                    "size": list(size), "mode": req.mode if req else "pausa",
                    "audio": ffmpeg_runner.has_audio_stream(file), "after": entry.get("despues")}
    if missing:
        raise ValueError("Faltan los archivos de los clips " + ", ".join(f"«{c}»" for c in missing) +
                         ": pasalos en 'clips' ({id: ruta}) o ponelos en el manifest del deck.")
    return out


# ------------------------------------------------------------------ base

def gameplay_base(job_id: str, params: PasarelaParams, report: dict) -> str:
    """The vertical base, from the cache when the same gameplay was already
    converted with the same settings (another voice re-uses it)."""
    windows, recording = None, None
    if params.highlight:
        h = params.highlight
        hl = insights.plan_highlight(h.recording, h.before, h.after, h.events)
        windows, recording = hl["windows"], hl["archivo"]
        source = recording
        report["base"] = {"fuente": "insights", "grabacion": hl["summary"]["nombre"], "uuid": hl["uuid"],
                          "campeon": hl["summary"]["campeon"], "kda": hl["summary"]["kda"],
                          "ventanas": windows, "duracion_highlight": hl["duracion"], **h.key}
    else:
        source = params.gameplay_file
        report["base"] = {"fuente": "archivo", "archivo": source}
    size = ffmpeg_runner.probe_resolution(source) or (1920, 1080)
    vp = render.vertical_params(params.band, *size)
    report["base"]["main_clip_offset"] = vp.main_clip_offset
    key = _key(BASE_VERSION, studio.fingerprint(source), windows, vp.__dict__)
    out = os.path.join(BASE_DIR, f"{key}.mp4")
    report["base"]["cache"] = os.path.isfile(out)
    if not report["base"]["cache"]:
        os.makedirs(BASE_DIR, exist_ok=True)
        tmp = out + ".part.mp4"
        total = sum(w["end"] - w["start"] for w in windows) if windows else ffmpeg_runner.probe_duration(source)
        gpu = ffmpeg_runner.cuda_decode_supported(ffmpeg_runner.probe_video_stream(source))
        _stage(job_id, 10, "Pasando el gameplay a vertical (y armando la banda)…")
        try:
            ffmpeg_runner.run(render.base_command(tmp, vp, source, recording, windows, gpu), job_id,
                              total_duration=total, progress_range=(10, 58))
        except RuntimeError:
            if not gpu:
                raise
            ffmpeg_runner.run(render.base_command(tmp, vp, source, recording, windows, False), job_id,
                              total_duration=total, progress_range=(10, 58))
        os.replace(tmp, out)
    report["base"]["duracion"] = round(ffmpeg_runner.probe_duration(out) or 0, 3)
    return out


# ------------------------------------------------------------ transcript

def transcript(job_id: str, params: PasarelaParams, script: str, report: dict) -> list[dict]:
    """Whisper on the voice with the script's names as `initial_prompt`
    (studio's rule: never hotwords), cached by the voice's fingerprint."""
    prompt = studio.whisper_prompt(script) if params.whisper_hint else None
    key = _key(studio.fingerprint(params.voice), prompt, params.model, params.language)
    path = os.path.join(STT_DIR, f"{key}.json")
    report["transcripcion"] = {"cache": os.path.isfile(path), "initial_prompt": prompt, "modelo": params.model}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            segments = json.load(fh)
    else:
        _stage(job_id, 3, "Transcribiendo la voz…")
        segments = subtitles.transcribe(params.voice, SubtitlesParams(language=params.language, model=params.model),
                                        job_id=job_id, initial_prompt=prompt)
        os.makedirs(STT_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(segments, fh, ensure_ascii=False)
    segments, moved = plan.snap_starts(segments, voice_levels(params.voice))
    report["transcripcion"]["palabras_corregidas"] = moved
    return segments


def voice_levels(path: str):
    """dBFS of the voice every plan.HOP_S (mono, 16 kHz)."""
    import numpy as np
    raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-i", path, "-ac", "1", "-ar", "16000",
                                 "-f", "f32le", "-"])
    hop = int(16000 * plan.HOP_S)
    y = np.frombuffer(raw, np.float32)
    frames = y[:len(y) // hop * hop].reshape(-1, hop)
    return 20 * np.log10(np.sqrt((frames * frames).mean(axis=1)) + 1e-9)


# ------------------------------------------------------------------ band

def build_band(params: PasarelaParams, manifest: list[dict], timeline: plan.Timeline,
               work: str) -> tuple[str, dict]:
    """The slides and the rest slide flattened into one 1080x680 track
    (lossless pieces joined by a concat list): (the list's path, its summary)."""
    files = {str(m["id"]): os.path.join(params.slides, m["archivo"]) for m in manifest if m.get("archivo")}
    rest_item = next((m for m in manifest if m.get("reposo")), None)
    if rest_item:
        rest_id = str(rest_item["id"])
    else:
        rest_id = "__negro__"
        files[rest_id] = os.path.join(work, "negro.png")
        ffmpeg_runner.capture([config.FFMPEG_PATH, "-y", "-v", "error", "-f", "lavfi", "-i",
                               f"color=black:s={config.OUTPUT_WIDTH}x{BAND_H}", "-frames:v", "1",
                               files[rest_id]])
    used = {rest_id} | {l["id"] for l in timeline.layers if l["kind"] == "lamina"}
    missing = [i for i in used if not os.path.isfile(files.get(i, ""))]
    if missing:
        raise ValueError("Faltan los archivos de las láminas " + ", ".join(sorted(missing)) +
                         ": exportá las láminas del deck otra vez.")
    streams = {i: ffmpeg_runner.probe_video_stream(files[i]) for i in used}
    decoders = {i: render.decoder_args(files[i], streams[i]) for i in used}
    # Opaque band (every slide `alfa: false`, as Presentations exports a band
    # deck since 10/11): NVENC lossless. With alpha, or if NVENC fails: FFV1.
    by_id = {str(m["id"]): m for m in manifest}
    opaque = all(by_id.get(i, {}).get("alfa") is False or i == "__negro__" for i in used) and         ffmpeg_runner.video_encode_flags()[1] == "h264_nvenc"
    rest_dur = ffmpeg_runner.probe_duration(files[rest_id]) if not files[rest_id].endswith(".png") else None
    pieces = render.band_pieces(timeline.layers, timeline.duration, rest_dur)

    def run_all(opaque: bool) -> list[str]:
        outs = [os.path.join(work, f"band_{k:03d}.{'mp4' if opaque else 'mkv'}") for k in range(len(pieces))]

        def one(k):
            ffmpeg_runner.capture(render.piece_command(pieces[k], files, decoders, rest_id, outs[k], opaque),
                                  timeout=600)
        with ThreadPoolExecutor(BAND_WORKERS) as pool:
            list(pool.map(one, range(len(pieces))))
        return outs

    try:
        outs = run_all(opaque)
    except RuntimeError:
        if not opaque:
            raise
        outs = run_all(False)       # one codec for every piece: the concat list needs it
        opaque = False
    report_band = {"tramos": len(pieces), "codec": "h264 sin pérdida" if opaque else "ffv1"}
    return render.concat_list(outs, os.path.join(work, "band.txt")), report_band


# ----------------------------------------------------------------- main

def process(job_id: str, params: PasarelaParams) -> None:
    phases = Phases()
    report: dict = {"job_id": job_id, "salida": params.output, "banda": params.band, "avisos": []}
    work = os.path.join(WORK_DIR, job_id)
    ass = None
    try:
        os.makedirs(work, exist_ok=True)
        _stage(job_id, 1, "Leyendo el deck…")
        manifest = load_manifest(params.slides)
        script = load_script(params.slides)
        report["laminas"] = params.slides
        sides = {m.get("banda") for m in manifest if m.get("tipo") != "clip"} - {None}
        if sides and sides != {params.band}:
            report["avisos"].append(f"Las láminas son de banda {'/'.join(sorted(sides))} y el video va con la "
                                    f"banda {params.band}: el deck no tiene esa variante (laminas.mjs --banda=arriba).")
        clips = phases.run("clips", collect_clips, params, manifest, script)
        voice_dur = ffmpeg_runner.probe_duration(params.voice)
        if not voice_dur:
            raise ValueError("No pude leer la duración de la voz.")

        segments = phases.run("transcripcion", transcript, job_id, params, script, report)

        _stage(job_id, 8, "Ubicando láminas y clips…")
        infos = {c["id"]: plan.ClipInfo(c["id"], c["dur"], c["full"], c["mode"], c["after"]) for c in clips.values()}
        timeline = phases.run("plan", plan.build, segments, script, manifest, infos, voice_dur)

        voice_lufs, _ = phases.run("loudness", loudness, params.voice)
        for c in clips.values():
            c["lufs"] = phases.run("loudness", loudness, c["file"])[0] if c["audio"] else None
            db = (voice_lufs - c["lufs"]) if (voice_lufs is not None and c["lufs"] is not None) else 0.0
            c["gain_db"] = round(db, 2)
            c["gain"] = 10 ** (db / 20) * (render.ENCIMA_GAIN if c["mode"] == "encima" else 1.0)

        # The band does not need the base: it is built (CPU, and NVENC lossless)
        # while the base converts (NVDEC + the vertical graph + NVENC).
        with ThreadPoolExecutor(1) as side:
            band_job = side.submit(phases.run, "banda", build_band, params, manifest, timeline, work)
            base = phases.run("base", gameplay_base, job_id, params, report)
            _stage(job_id, 59, "Terminando la banda…")
            band_list, report["banda_pista"] = band_job.result()
        if report["base"]["duracion"] + 1e-3 < timeline.duration:
            report["avisos"].append(f"El gameplay dura {report['base']['duracion']:.1f} s y el video "
                                    f"{timeline.duration:.1f} s: el gameplay se repite desde el principio.")

        if params.subtitles is not None:
            ass = phases.run("subtitulos", _subtitles_ass, params, segments, timeline)

        final_clips = [{**clips[r["id"]], "start": r["start"], "end": r["end"]} for r in timeline.clips]
        tmp = params.output[:-4] + ".part.mp4"
        kwargs = dict(base=base, base_loop=report["base"]["duracion"] + 1e-3 < timeline.duration,
                      band_list=band_list, band_y=params.band_y, voice=params.voice, duration=timeline.duration,
                      voice_pieces=timeline.voice_pieces, clips=final_clips, watermark=params.watermark,
                      wm_y=render.watermark_y(params.band), gameplay_volume=params.gameplay_volume, ass=ass,
                      out=tmp)
        _stage(job_id, 62, "Encodeando el video final…")

        def final():
            try:
                ffmpeg_runner.run(render.final_command(**kwargs, gpu_decode=True), job_id,
                                  total_duration=timeline.duration, cwd=config.BASE_DIR, progress_range=(62, 97))
            except RuntimeError as exc:
                report["avisos"].append("La decodificación en la GPU falló; se repitió en la CPU: "
                                        + str(exc).splitlines()[-1][:200])
                ffmpeg_runner.run(render.final_command(**kwargs, gpu_decode=False), job_id,
                                  total_duration=timeline.duration, cwd=config.BASE_DIR, progress_range=(62, 97))
        phases.run("final", final)
        os.replace(tmp, params.output)

        _stage(job_id, 98, "Midiendo el resultado…")
        out_lufs, out_peak = phases.run("medicion", loudness, params.output)
        report.update(_tables(timeline, clips, voice_lufs, out_lufs, out_peak, voice_dur,
                              ffmpeg_runner.probe_duration(params.output)))
        try:
            report["montaje"] = montaje(params, timeline, manifest)
        except ValueError as exc:
            report["avisos"].append(f"El montaje.json no cumple el contrato: {exc}")
        report["tiempos"] = phases.report()
        _save_report(job_id, report)
        job_manager.update_job(job_id, status="done", progress=100, stage=None, output_path=params.output,
                               duration=report["duracion"], report_ready=True)
        shutil.rmtree(work, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001
        report["error"] = str(exc)
        report["tiempos"] = phases.report()
        _save_report(job_id, report)
        job_manager.update_job(job_id, status="error", error=str(exc), stage=None, report_ready=True)
    finally:
        file_utils.cleanup_paths(*([ass] if ass else []))


def _subtitles_ass(params: PasarelaParams, segments: list[dict], timeline: plan.Timeline) -> str:
    """The karaoke over the gameplay, with each word moved to final time."""
    style = {"position_y": SUB_Y[params.band], **params.subtitles}
    sp = SubtitlesParams(**{k: v for k, v in style.items() if k in SubtitlesParams.__dataclass_fields__})
    moved = []
    for seg in segments:
        words = [{**w, "start": timeline.final(w["start"]), "end": timeline.final(w["end"])}
                 for w in seg.get("words") or []]
        if words:
            moved.append({**seg, "start": words[0]["start"], "end": words[-1]["end"], "words": words})
    return subtitles.prepare_ass(moved, sp)


def _tables(tl: plan.Timeline, clips: dict, voice_lufs, out_lufs, out_peak, voice_dur, out_dur) -> dict:
    layers = []
    for k, l in enumerate(tl.layers):
        nxt = tl.layers[k + 1] if k + 1 < len(tl.layers) else None
        stuck = nxt is not None and l["end"] > nxt["start"] + 1e-6
        layers.append({"id": l["id"], "tipo": l["kind"], "inicio": l["start"],
                       "fin": nxt["start"] if stuck else l["end"],
                       **({"debajo_hasta": l["end"]} if stuck else {}),
                       "fundido_entrada": l["fade_in"], "fundido_salida": l["fade_out"]})
    al = tl.alignment or {}
    return {
        "duracion": round(out_dur or tl.duration, 3),
        "duracion_plan": tl.duration,
        "duracion_voz": round(voice_dur, 3),
        "alineacion": {"palabras_guion": al.get("script_words"), "alineadas": al.get("matched"),
                       "palabras_transcripcion": al.get("transcript_words"),
                       "porcentaje": round(100 * al["matched"] / al["script_words"], 1)
                       if al.get("script_words") else None},
        "capas": layers,
        "clips": [{"id": r["id"], "modo": r["mode"], "pantalla": "completa" if r["full"] else "banda",
                   "inicio": r["start"], "fin": r["end"], "duracion": r["dur"], "voz_en": r["voice_at"],
                   "archivo": clips[r["id"]]["file"], "lufs": clips[r["id"]]["lufs"],
                   "ganancia_db": clips[r["id"]]["gain_db"], "ganancia": round(clips[r["id"]]["gain"], 3)}
                  for r in tl.clips],
        "pausas": [{"clip": p["id"], "voz_en": p["voice_at"], "video_en": p["at"], "duracion": p["dur"]}
                   for p in tl.pauses],
        "reposo": tl.rest,
        "sin_colocar": tl.unplaced,
        "marcas_desconocidas": tl.unknown_marks,
        "audio": {"lufs_voz": voice_lufs, "lufs": out_lufs, "pico_dbfs": out_peak},
    }


def montaje(params: PasarelaParams, timeline: plan.Timeline, manifest: list[dict]) -> dict:
    """The slides as they went, in the studio's contract (montaje.json v1,
    docs/contratos/montaje.schema.json): each one by seconds of the final
    video (`desde`/`hasta`), validated with the studio's own validator."""
    from modules.studio import schema as studio_schema
    deck = os.path.basename(os.path.normpath(params.deck))
    desk = os.path.basename(os.path.dirname(os.path.normpath(params.deck)))
    data = {"version": 1,
            "base": {"archivo": os.path.basename(params.output)[:260], "duracion": timeline.duration},
            "deck": {"desk": desk, "deck": deck}, "caja": "banda", "reposo": True,
            "capas": [{"lamina": l["id"], "desde": l["start"], "hasta": l["end"]}
                      for l in timeline.layers if l["kind"] == "lamina"]}
    ids = [str(m["id"]) for m in manifest if m.get("tipo") != "clip"]
    return studio_schema.validate_montaje(data, ids)


def _save_report(job_id: str, report: dict) -> None:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    with open(os.path.join(REPORTS_DIR, f"{job_id}.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)


def load_report(job_id: str) -> dict | None:
    path = os.path.join(REPORTS_DIR, f"{os.path.basename(job_id)}.json")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ------------------------------------------------------------- highlight

def highlight_job(job_id: str, ref: str, before: float, after: float, events: list[str], out: str | None) -> None:
    """A highlight on its own (16:9), like Insights' own."""
    t0 = time.perf_counter()
    try:
        _stage(job_id, 2, "Leyendo Insights Capture…")
        hl = insights.plan_highlight(ref, before, after, events)
        out = out or os.path.join(config.HIGHLIGHTS_FOLDER, insights.default_name(hl, before, after))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        tmp = out[:-4] + ".part.mp4"
        gpu = ffmpeg_runner.cuda_decode_supported(ffmpeg_runner.probe_video_stream(hl["archivo"]))
        _stage(job_id, 5, f"Cortando {len(hl['windows'])} jugadas…")
        try:
            ffmpeg_runner.run(render.highlight_command(tmp, hl["archivo"], hl["windows"], gpu), job_id,
                              total_duration=hl["duracion"], progress_range=(5, 99))
        except RuntimeError:
            if not gpu:
                raise
            ffmpeg_runner.run(render.highlight_command(tmp, hl["archivo"], hl["windows"], False), job_id,
                              total_duration=hl["duracion"], progress_range=(5, 99))
        os.replace(tmp, out)
        result = {"salida": out, "grabacion": hl["summary"], "ventanas": hl["windows"],
                  "duracion": hl["duracion"], "tiempo_s": round(time.perf_counter() - t0, 2)}
        _save_report(job_id, result)
        job_manager.update_job(job_id, status="done", progress=100, stage=None, output_path=out,
                               result=result, report_ready=True)
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc), stage=None)
