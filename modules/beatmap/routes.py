"""Blueprint Flask del módulo beatmap (/api/beatmap).

    POST /process            → 202 {job_id}   canción (archivo `audio` o `musica_url`)
    GET  /status/<job_id>    → {status, progress, stage, error, resumen, beatmap}
    GET  /download/<job_id>  → preview.mp4 (pitidos y destellos en los beats)
    GET  /json/<job_id>      → beatmap.json
"""
import os
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import file_utils, job_manager
from modules.beatmap import processor
from modules.beatmap.schema import BeatmapParams

bp = Blueprint("beatmap", __name__, url_prefix="/api/beatmap")


@bp.post("/process")
def process():
    try:
        params = BeatmapParams.from_form(request.form)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    audio_path = None
    if not params.musica:
        audio = request.files.get("audio")
        if not audio or not audio.filename:
            return jsonify(error="Falta la canción: subí el archivo 'audio' o mandá 'musica_url'."), 400
        if not config.allowed_audio_file(audio.filename):
            return jsonify(error=f"Extensión no permitida. Usar: {sorted(config.ALLOWED_AUDIO_EXTENSIONS)}"), 400
        audio_path = file_utils.save_upload(audio)
    job_id = job_manager.create_job(audio_path or params.musica.url, output_path="")
    threading.Thread(target=processor.process, args=(job_id, audio_path, params), daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.get("/status/<job_id>")
def status(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404
    return jsonify(status=job["status"], progress=job["progress"], stage=job.get("stage"),
                   error=job["error"], resumen=job.get("resumen"), beatmap=job.get("beatmap"))


@bp.get("/download/<job_id>")
def download(job_id):
    job = job_manager.get_job(job_id)
    if not job or job["status"] != "done":
        return jsonify(error="El análisis no terminó."), 404
    if not job.get("output_path") or not os.path.isfile(job["output_path"]):
        return jsonify(error="Este análisis no tiene preview."), 404
    return send_file(job["output_path"], mimetype="video/mp4", as_attachment=True,
                     download_name="beatmap-preview.mp4")


@bp.get("/json/<job_id>")
def json_file(job_id):
    job = job_manager.get_job(job_id)
    if not job or job["status"] != "done" or not os.path.isfile(job.get("json_path") or ""):
        return jsonify(error="El análisis no terminó."), 404
    return send_file(job["json_path"], mimetype="application/json", as_attachment=True,
                     download_name="beatmap.json")
