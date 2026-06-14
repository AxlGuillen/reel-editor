"""Blueprint Flask del módulo audio_merge."""
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import file_utils, job_manager
from modules.audio_merge import processor
from modules.audio_merge.schema import AudioMergeParams, MIN_FILES, MAX_FILES

bp = Blueprint("audio_merge", __name__, url_prefix="/api/audio-merge")


@bp.post("/process")
def process():
    files = request.files.getlist("audios")
    files = [f for f in files if f and f.filename]
    if len(files) < MIN_FILES:
        return jsonify(error=f"Subí al menos {MIN_FILES} audios para unir."), 400
    if len(files) > MAX_FILES:
        return jsonify(error=f"Demasiados audios (máximo {MAX_FILES})."), 400
    for f in files:
        if not config.allowed_audio_file(f.filename):
            return jsonify(
                error=f"'{f.filename}': extensión no permitida. "
                      f"Usar: {sorted(config.ALLOWED_AUDIO_EXTENSIONS)}"
            ), 400

    try:
        params = AudioMergeParams.from_form(request.form)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    # El orden de getlist preserva el orden de subida del frontend.
    input_paths = [file_utils.save_upload(f) for f in files]
    job_id = job_manager.create_job(input_paths[0], output_path="")
    output_path = file_utils.output_path_for(job_id, ext="mp3")
    job_manager.update_job(job_id, output_path=output_path)

    thread = threading.Thread(
        target=processor.process,
        args=(job_id, input_paths, output_path, params),
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
        mimetype="audio/mpeg",
        as_attachment=True,
        download_name=f"audio_unido_{job_id}.mp3",
    )
