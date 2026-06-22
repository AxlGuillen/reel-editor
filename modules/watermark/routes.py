"""Blueprint Flask del módulo watermark."""
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import file_utils, job_manager
from modules.watermark import processor
from modules.watermark.schema import WatermarkParams

bp = Blueprint("watermark", __name__, url_prefix="/api/watermark")


@bp.post("/process")
def process():
    if "video" not in request.files:
        return jsonify(error="Falta el archivo 'video'"), 400
    video = request.files["video"]
    if not video.filename:
        return jsonify(error="Nombre de video vacío"), 400
    if not config.allowed_file(video.filename):
        return jsonify(
            error=f"Video: extensión no permitida. Usar: {sorted(config.ALLOWED_EXTENSIONS)}"
        ), 400

    try:
        params = WatermarkParams.from_form(request.form)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    video_path = file_utils.save_upload(video)
    job_id = job_manager.create_job(video_path, output_path="")
    output_path = file_utils.output_path_for(job_id)
    job_manager.update_job(job_id, output_path=output_path)

    thread = threading.Thread(
        target=processor.process,
        args=(job_id, video_path, output_path, params),
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
        download_name=f"watermark_{job_id}.mp4",
    )
