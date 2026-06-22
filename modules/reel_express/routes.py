"""Blueprint Flask del módulo reel_express."""
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import file_utils, job_manager
from modules.reel_express import processor
from modules.reel_express.schema import ReelExpressParams

bp = Blueprint("reel_express", __name__, url_prefix="/api/reel-express")


@bp.post("/process")
def process():
    if "clip" not in request.files:
        return jsonify(error="Falta el archivo 'clip' (video 16:9)"), 400
    clip = request.files["clip"]
    if not clip.filename:
        return jsonify(error="Nombre de clip vacío"), 400
    if not config.allowed_file(clip.filename):
        return jsonify(
            error=f"Clip: extensión no permitida. Usar: {sorted(config.ALLOWED_EXTENSIONS)}"
        ), 400

    try:
        params = ReelExpressParams.from_form(request.form)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    # Fuente de audio por archivo: validar y guardar el upload (narración).
    audio_path = None
    if params.audio_source == "file":
        audio = request.files.get("audio")
        if not audio or not audio.filename:
            return jsonify(error="Falta el archivo de audio (narración)."), 400
        if not config.allowed_audio_file(audio.filename):
            return jsonify(
                error=f"Audio: extensión no permitida. Usar: {sorted(config.ALLOWED_AUDIO_EXTENSIONS)}"
            ), 400
        audio_path = file_utils.save_upload(audio)

    clip_path = file_utils.save_upload(clip)
    job_id = job_manager.create_job(clip_path, output_path="")
    # has_wm define las bandas de progreso (ver _aggregate_progress).
    job_manager.update_job(job_id, has_wm=params.has_watermark)

    thread = threading.Thread(
        target=processor.process,
        args=(job_id, clip_path, params),
        kwargs={"audio_path": audio_path},
        daemon=True,
    )
    thread.start()

    return jsonify(job_id=job_id), 202


def _aggregate_progress(job: dict) -> tuple[int, str]:
    """Combina el progreso de los sub-jobs en un % global + label de etapa.

    Las bandas dependen de si hay paso de watermark:
      sin watermark → prep 0-70, mix 70-100.
      con watermark → prep 0-55, mix 55-80, watermark 80-100.
    En la fase prep, si la fuente de audio es un archivo (sin descarga), el
    progreso es solo el de la conversión a vertical.
    """
    prep_end, mix_end = (55, 80) if job.get("has_wm") else (70, 100)

    phase = job.get("phase")
    if phase == "prep":
        v = (job_manager.get_job(job.get("sub_v")) or {}).get("progress", 0)
        if job.get("sub_d"):
            d = (job_manager.get_job(job.get("sub_d")) or {}).get("progress", 0)
            base = (v + d) / 2
        else:
            base = v
        return int(base / 100 * prep_end), "Preparando video y audio…"
    if phase == "mix":
        m = (job_manager.get_job(job.get("sub_m")) or {}).get("progress", 0)
        return int(prep_end + (m / 100) * (mix_end - prep_end)), "Mezclando…"
    if phase == "watermark":
        w = (job_manager.get_job(job.get("sub_w")) or {}).get("progress", 0)
        return int(mix_end + (w / 100) * (100 - mix_end)), "Aplicando texto y marca…"
    return job.get("progress", 0), "Procesando…"


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
        download_name=f"reel_express_{job_id}.mp4",
    )
