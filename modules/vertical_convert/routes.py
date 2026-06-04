"""Blueprint Flask del módulo vertical_convert."""
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import file_utils, job_manager
from modules.vertical_convert import processor
from modules.vertical_convert.schema import VerticalConvertParams

bp = Blueprint("vertical_convert", __name__,
               url_prefix="/api/vertical-convert")


@bp.post("/process")
def process():
    if "video" not in request.files:
        return jsonify(error="Falta el archivo 'video'"), 400

    file = request.files["video"]
    if not file.filename:
        return jsonify(error="Nombre de archivo vacío"), 400
    if not config.allowed_file(file.filename):
        return jsonify(
            error=f"Extensión no permitida. Usar: {sorted(config.ALLOWED_EXTENSIONS)}"
        ), 400

    try:
        params = VerticalConvertParams.from_form(request.form)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    input_path = file_utils.save_upload(file)
    job_id = job_manager.create_job(input_path, output_path="")
    output_path = file_utils.output_path_for(job_id)
    job_manager.update_job(job_id, output_path=output_path)

    # El procesamiento corre en un thread para devolver 202 de inmediato y
    # permitir el polling de status. FFmpeg sigue siendo bloqueante por job.
    thread = threading.Thread(
        target=processor.process,
        args=(job_id, input_path, output_path, params),
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
        download_name=f"reelforge_{job_id}.mp4",
    )
