"""Blueprint Flask del módulo subtitles.

Flujo en dos fases:
  POST /transcribe  → sube el video, transcribe en thread, devuelve job_id.
                       El status del job, al terminar, incluye los segmentos.
  POST /render      → con los segmentos (editados) + estilo, quema el .ass.
                       Recibe el job_id de transcripción para ubicar el video.
"""
import os
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import file_utils, job_manager
from modules.subtitles import processor
from modules.subtitles.schema import SubtitlesParams

bp = Blueprint("subtitles", __name__, url_prefix="/api/subtitles")


@bp.post("/transcribe")
def transcribe_route():
    if "video" not in request.files:
        return jsonify(error="Falta el archivo 'video'."), 400
    video = request.files["video"]
    if not video.filename:
        return jsonify(error="Nombre de video vacío."), 400
    if not config.allowed_file(video.filename):
        return jsonify(
            error=f"Extensión no permitida. Usar: {sorted(config.ALLOWED_EXTENSIONS)}"
        ), 400

    try:
        params = SubtitlesParams.from_form(request.form)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    video_path = file_utils.save_upload(video)
    job_id = job_manager.create_job(video_path, output_path="")

    thread = threading.Thread(
        target=processor.transcribe_job,
        args=(job_id, video_path, params),
        daemon=True,
    )
    thread.start()
    return jsonify(job_id=job_id), 202


@bp.post("/render")
def render_route():
    data = request.get_json(silent=True) or {}

    src_job = job_manager.get_job(data.get("job_id"))
    if not src_job:
        return jsonify(
            error="Sesión de transcripción no encontrada. Volvé a transcribir."
        ), 400
    video_path = src_job.get("input_path")
    if not video_path or not os.path.exists(video_path):
        return jsonify(
            error="El video ya no está disponible. Volvé a subirlo."
        ), 400

    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        return jsonify(error="No hay subtítulos para generar."), 400

    try:
        params = SubtitlesParams.from_form(data)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    job_id = job_manager.create_job(video_path, output_path="")
    output_path = file_utils.output_path_for(job_id)

    thread = threading.Thread(
        target=processor.render_job,
        args=(job_id, video_path, segments, params, output_path),
        daemon=True,
    )
    thread.start()
    return jsonify(job_id=job_id), 202


@bp.get("/status/<job_id>")
def status(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404
    return jsonify(
        status=job["status"],
        progress=job["progress"],
        stage=job.get("stage"),
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
        download_name=f"subtitulado_{job_id}.mp4",
    )
