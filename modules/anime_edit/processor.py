"""anime_edit orchestration: a video (an anime fight) + a song → several
synced edit variants.

  Phase 1  process()  downloads whatever arrives as a link, analyzes the song
                      (beatmap, with lyrics if requested), detects the video's
                      shots and builds thumbnails and numbered contact sheets.
                      It proposes a selection (the highest scores, in order)
                      and a hero. It stops at stage "esperando_seleccion": the
                      pause where Claude looks at the sheets (or the user at
                      the gallery) and decides which shots go, in what order
                      and which is the hero.
  Phase 2  finish()   one EDL per variant (plan.py) and its render (editing),
                      one after another. The videos end up in EDITS_FOLDER.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

import config
from core import ffmpeg_runner, file_utils, job_manager
from modules.anime_edit import plan, shots as shots_mod
from modules.anime_edit.schema import AnimeEditParams, FinishParams
from modules.beatmap import processor as beatmap
from modules.competitivo import layout, salida
from modules.downloader import processor as downloader
from modules.editing import render

WORK = os.path.join(config.OUTPUT_FOLDER, "anime_edit")
MAX_PROPOSAL = 24

_SESSIONS: dict[str, dict] = {}


def _download_video(job_id: str, params) -> str:
    sub = job_manager.create_job(params.url, output_path="")
    job_manager.update_job(job_id, stage="Bajando el video…")
    downloader.process(sub, params)
    done = job_manager.get_job(sub) or {}
    if done.get("status") != "done" or not done.get("output_path"):
        raise RuntimeError(f"No se pudo bajar el video: {done.get('error') or 'error desconocido'}")
    return done["output_path"]


def proposal(shots: list[dict], n: int = MAX_PROPOSAL) -> tuple[list[int], int | None]:
    """The `n` highest-scoring shots in order of appearance, and the hero (the best)."""
    if not shots:
        return [], None
    best = sorted(shots, key=lambda p: -p["score"])[:n]
    return [p["id"] for p in sorted(best, key=lambda p: p["start"])], best[0]["id"]


def process(job_id: str, video_path: str | None, music_path: str | None,
            params: AnimeEditParams, keep_video: bool = False) -> None:
    temp_files = []
    try:
        folder = os.path.join(WORK, job_id)
        os.makedirs(folder, exist_ok=True)
        job_manager.update_job(job_id, status="processing", progress=2, stage="Preparando…")
        if params.video:
            video_path = _download_video(job_id, params.video)
            temp_files.append(video_path)
        if params.music:
            music_path = beatmap.download_music(job_id, params.music)
            temp_files.append(music_path)
        if params.music_from_video:
            music_path = video_path
        if not ffmpeg_runner.probe_resolution(video_path):
            raise RuntimeError("No se pudo leer el video.")
        job_manager.update_job(job_id, progress=10)
        bm = beatmap.get(music_path, params.engine, params.lyrics, job_id=job_id)
        job_manager.update_job(job_id, progress=40, stage="Buscando los planos del video…")
        shots = shots_mod.detect(video_path)
        job_manager.update_job(job_id, progress=75, stage="Armando las hojas de cuadros…")
        shots_mod.thumbnails(video_path, shots, folder)
        sheets = shots_mod.contact_sheets(folder, len(shots))
        ids, hero = proposal(shots)
        _SESSIONS[job_id] = {"video": video_path, "music": music_path, "bm": bm, "shots": shots,
                             "folder": folder, "sheets": sheets, "params": params,
                             "temp_files": temp_files, "keep_video": keep_video}
        job_manager.update_job(
            job_id, status="done", progress=100, stage="esperando_seleccion", finished=False,
            shots=shots, proposal=ids, hero=hero, sheets=len(sheets),
            beatmap=beatmap.summary(bm), video_duration=ffmpeg_runner.probe_duration(video_path),
        )
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
        file_utils.cleanup_paths(*temp_files)


def _output_folder(fp: FinishParams) -> str:
    name = salida.limpiar(fp.title) or datetime.now().strftime("%Y-%m-%d %H-%M edit")
    folder = os.path.join(config.EDITS_FOLDER, name)
    os.makedirs(folder, exist_ok=True)
    return folder


def finish(job_id: str, fp: FinishParams) -> None:
    session = _SESSIONS.get(job_id)
    try:
        if not session:
            raise RuntimeError("La sesión ya no existe (¿se reinició el servidor?). Volvé a analizar el video.")
        by_id = {p["id"]: p for p in session["shots"]}
        ids = fp.selection or job_manager.get_job(job_id).get("proposal") or list(by_id)
        chosen = [by_id[i] for i in ids]
        folder = _output_folder(fp)
        base = salida.limpiar(fp.title) or "edit"
        watermark = os.path.join(config.WATERMARKS_FOLDER, fp.watermark) if fp.watermark else None
        res = (ffmpeg_runner.probe_resolution(session["video"]) or (0, 0))
        done = []
        n = len(fp.variants)
        for k, variant in enumerate(fp.variants):
            lo, hi = int(100 * k / n), int(100 * (k + 1) / n)
            job_manager.update_job(job_id, status="processing", progress=lo, finished=False,
                                   stage=f"Variante {k + 1}/{n} ({variant}): planeando…")
            op = plan.PlanOptions(format=fp.format, fps=fp.fps, duration_s=fp.duration_s,
                                  music_start=fp.music_start, text=fp.text, look=fp.look,
                                  hero=fp.hero, interpolate=fp.interpolate)
            edl, info = plan.plan(session["video"], chosen, session["bm"], variant, op)
            edl.music = session["music"]
            edl.lut = fp.lut
            edl.original_volume = fp.original_volume / 100
            edl.watermark, edl.watermark_height = watermark, fp.watermark_height
            destination = salida._unico(folder, f"{base} - {variant}.mp4")
            rendered = render.render(edl, destination, job_id, os.path.join(session["folder"], f"render_{k}"),
                                     progress=(lo, hi), intensity=fp.intensity, text_y=0.5,
                                     watermark_crop=layout.recorte_marca(watermark) if watermark else None,
                                     interpolate=fp.interpolate,
                                     upscale_below=1080 if fp.upscale and res[1] < 1080 else 0)
            done.append({**info, "video": destination, "name": os.path.basename(destination),
                         "duration": rendered["dur"], "rife": rendered["rife"], "esrgan": rendered["esrgan"],
                         "edl": edl.to_dict()})
        with open(os.path.join(folder, f"{base}.json"), "w", encoding="utf-8") as fh:
            json.dump({"created": datetime.now(timezone.utc).isoformat(), "selection": ids, "hero": fp.hero,
                       "beatmap": beatmap.summary(session["bm"]), "variants": done}, fh,
                      ensure_ascii=False, indent=2)
        result = {"folder": folder, "variants": [{k: v for k, v in d.items() if k != "edl"} for d in done]}
        job_manager.update_job(job_id, status="done", progress=100, stage="listo", finished=True,
                               result=result, output_path=done[0]["video"] if done else "")
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))


def discard(job_id: str) -> None:
    """Deletes a session's temporary files (thumbnails, downloads)."""
    session = _SESSIONS.pop(job_id, None)
    if not session:
        return
    shutil.rmtree(session["folder"], ignore_errors=True)
    file_utils.cleanup_paths(*session["temp_files"])
