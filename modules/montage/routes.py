"""Flask blueprint of the montage (/api/montage).

    POST /process            → 202 {job_id}. Base video: `video` (file), `library_clip` or
                               `job_id` (of a Reel Express or any finished job). Layers: `layers`
                               (JSON) with their uploaded files (`layer_files`, several) or read
                               from `layers_folder` (inside LAYERS_FOLDER; its manifest.json is
                               accepted by id). [box, fade_in_s, fade_out_s, max_s, lead_s,
                               segments, subtitles + sub_* style]
    GET  /status/<job_id>    → {status, progress, stage, error, layers}
    GET  /download/<job_id>  → the montage video
"""
import json
import os
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import file_utils, job_manager
from modules.library.routes import resolve_clip
from modules.montage import processor
from modules.montage.schema import MontageParams

bp = Blueprint("montage", __name__, url_prefix="/api/montage")


def _segments(base_job: dict | None):
    """The segments: the ones in the form, or the base job's (the
    reel_express/subtitles status carries them)."""
    raw = request.form.get("segments")
    if raw:
        try:
            data = json.loads(raw)
        except ValueError:
            raise ValueError("'segments' tiene que ser JSON (los de la transcripción).")
        if not isinstance(data, list):
            raise ValueError("'segments' tiene que ser una lista.")
        return data
    return (base_job or {}).get("segments")


@bp.post("/process")
def process():
    uploaded, saved = {}, []
    for f in request.files.getlist("layer_files"):
        if f and f.filename:
            if not config.allowed_layer_file(f.filename):
                return jsonify(error=f"Capa «{f.filename}»: usá mov, webm, mp4, mkv o png."), 400
            path = file_utils.save_upload(f)
            saved.append(path)
            uploaded[os.path.basename(f.filename)] = path
    try:
        params = MontageParams.from_form(request.form, uploaded)
        base_job = None
        if request.form.get("job_id"):
            base_job = job_manager.get_job(request.form["job_id"].strip())
            if not base_job or base_job["status"] != "done" or not os.path.isfile(base_job.get("output_path") or ""):
                raise ValueError("Ese job no terminó o ya no tiene su video.")
            video_path = base_job["output_path"]
        elif request.form.get("library_clip"):
            video_path = resolve_clip(request.form["library_clip"])
        else:
            video = request.files.get("video")
            if not video or not video.filename:
                raise ValueError("Falta el video base: 'video', 'library_clip' o 'job_id'.")
            if not config.allowed_file(video.filename):
                raise ValueError(f"Video: extensión no permitida. Usar: {sorted(config.ALLOWED_EXTENSIONS)}")
            video_path = file_utils.save_upload(video)
            saved.append(video_path)
        # Without segments (neither in the form nor in the base job), the video
        # is transcribed when needed: layers on words or subtitles.
        segments = _segments(base_job)
    except ValueError as exc:
        file_utils.cleanup_paths(*saved)
        return jsonify(error=str(exc)), 400
    job_id = job_manager.create_job(video_path, output_path="")
    job_manager.update_job(job_id, output_name=request.form.get("output_name"))
    threading.Thread(target=processor.process, args=(job_id, video_path, params, segments),
                     kwargs={"cleanup": saved}, daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.get("/status/<job_id>")
def status(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404
    return jsonify(status=job["status"], progress=job["progress"], stage=job.get("stage"),
                   error=job["error"], layers=job.get("layers"))


@bp.get("/download/<job_id>")
def download(job_id):
    job = job_manager.get_job(job_id)
    if not job or job["status"] != "done" or not os.path.isfile(job.get("output_path") or ""):
        return jsonify(error="El montaje no terminó."), 404
    name = file_utils.safe_download_name(job.get("output_name"), f"montage-{job_id}")
    return send_file(job["output_path"], mimetype="video/mp4", as_attachment=True, download_name=name)
