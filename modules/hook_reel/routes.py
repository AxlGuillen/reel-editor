"""Blueprint Flask del módulo hook_reel ("Reel Frase").

Entradas: video 1 (avatar + voz) y video 2 (clip de cierre) como archivos, y la
música como un link (se descarga con yt-dlp). Flujo de dos fases cuando hay
subtítulos (igual que reel_express):
  POST /process → convierte a vertical lo marcado y transcribe la voz del video 1;
                  devuelve job_id y, al terminar, los segmentos para editar.
  POST /finish  → con los segmentos editados, quema subs y ensambla el reel final.
Sin subtítulos, /process ya produce el reel final en un solo paso.
"""
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import file_utils, job_manager
from modules.hook_reel import processor
from modules.hook_reel.schema import HookReelParams
from modules.subtitles.schema import SubtitlesParams

bp = Blueprint("hook_reel", __name__, url_prefix="/api/hook-reel")


@bp.post("/process")
def process():
    # --- Validación de los 3 archivos ---
    video1 = request.files.get("video1")
    if not video1 or not video1.filename:
        return jsonify(error="Falta el video 1 (avatar + audio)."), 400
    if not config.allowed_file(video1.filename):
        return jsonify(error=f"Video 1: extensión no permitida. Usar: {sorted(config.ALLOWED_EXTENSIONS)}"), 400

    video2 = request.files.get("video2")
    if not video2 or not video2.filename:
        return jsonify(error="Falta el video 2 (el clip de cierre)."), 400
    if not config.allowed_file(video2.filename):
        return jsonify(error=f"Video 2: extensión no permitida. Usar: {sorted(config.ALLOWED_EXTENSIONS)}"), 400

    # La música es un link (se descarga); from_form valida la URL y la calidad.
    try:
        params = HookReelParams.from_form(request.form)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    video1_path = file_utils.save_upload(video1)
    video2_path = file_utils.save_upload(video2)

    job_id = job_manager.create_job(video1_path, output_path="")
    job_manager.update_job(job_id, has_subs=params.has_subtitles)

    thread = threading.Thread(
        target=processor.process,
        args=(job_id,),
        kwargs=dict(video1_path=video1_path, video2_path=video2_path,
                    params=params),
        daemon=True,
    )
    thread.start()
    return jsonify(job_id=job_id), 202


@bp.post("/finish")
def finish():
    """Fase 2: con los subtítulos editados, quema sobre el seg.1 y ensambla."""
    data = request.get_json(silent=True) or {}

    src = job_manager.get_job(data.get("job_id"))
    if not src:
        return jsonify(error="Sesión no encontrada. Volvé a generar el reel."), 400
    ctx = src.get("hook_ctx")
    if not ctx:
        return jsonify(error="El reel base ya no está disponible. Volvé a generar."), 400

    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        return jsonify(error="No hay subtítulos para generar."), 400

    try:
        sub_params = SubtitlesParams.from_form(data)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    job_id = job_manager.create_job(ctx["video1_ready"], output_path="")
    output_path = file_utils.output_path_for(job_id)

    thread = threading.Thread(
        target=processor.finish,
        args=(job_id, ctx, segments, sub_params, output_path),
        daemon=True,
    )
    thread.start()
    return jsonify(job_id=job_id), 202


def _aggregate_progress(job: dict) -> tuple[int, str]:
    """Combina el progreso de los sub-jobs en un % global + label de etapa.

    Bandas: con subs, prep 0-30 y transcripción 30-100. Sin subs, prep 0-40 y el
    ensamblado 40-100 (lo reporta el propio job vía progress_range).
    """
    phase = job.get("phase")
    has_subs = job.get("has_subs")

    if phase == "prep":
        hi = 30 if has_subs else 40
        progresos = []
        for key in ("sub_d", "sub_v1", "sub_v2"):
            if job.get(key):
                progresos.append((job_manager.get_job(job[key]) or {}).get("progress", 0))
        base = sum(progresos) / len(progresos) if progresos else 100
        return int(base / 100 * hi), "Descargando música y preparando clips…"
    if phase == "transcribe":
        t = (job_manager.get_job(job.get("sub_t")) or {}).get("progress", 0)
        return int(30 + (t / 100) * 70), "Transcribiendo la voz…"
    if phase == "assemble":
        return job.get("progress", 40), "Ensamblando reel…"

    return job.get("progress", 0), job.get("stage") or "Procesando…"


@bp.get("/status/<job_id>")
def status(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404

    if job["status"] == "processing":
        progress, stage = _aggregate_progress(job)
    else:
        progress, stage = job["progress"], None

    return jsonify(
        status=job["status"],
        progress=progress,
        stage=stage,
        error=job["error"],
        segments=job.get("segments"),
    )


@bp.get("/download/<job_id>")
def download(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404
    if job["status"] != "done":
        return jsonify(error=f"job no está listo (status: {job['status']})"), 409
    return send_file(
        job["output_path"],
        mimetype="video/mp4",
        as_attachment=True,
        download_name=f"reel_frase_{job_id}.mp4",
    )
