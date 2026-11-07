"""Flask blueprint of the motion module (/api/motion).

    POST /process            → 202 {job_id}   video (file | library_clip | job_id | path)
                                              [start, end (s or m:ss), width=480, preview]
    GET  /status/<job_id>    → {status, progress, stage, error, summary, motion}
    GET  /json/<job_id>      → motion.json
    GET  /summary/<job_id>   → resumen.txt (one line per event, for Claude)
    GET  /sheet/<job_id>     → eventos.jpg (the strongest events with arrow and center)
    GET  /download/<job_id>  → preview.mp4 (the span with the curve under it)
"""
import os
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import file_utils, job_manager
from modules.library.routes import resolve_clip
from modules.motion import processor
from modules.motion.schema import MotionParams

bp = Blueprint("motion", __name__, url_prefix="/api/motion")


def _source(params: MotionParams) -> tuple[str, bool]:
    """(path, keep): the video to analyze and whether it must survive the job."""
    if params.path:
        return params.path, True
    if request.form.get("job_id"):
        src = job_manager.get_job(request.form["job_id"].strip())
        if not src or src["status"] != "done" or not os.path.isfile(src.get("output_path") or ""):
            raise ValueError("Ese job no terminó o ya no tiene su video.")
        return src["output_path"], True
    if request.form.get("library_clip"):
        return resolve_clip(request.form["library_clip"]), True
    video = request.files.get("video")
    if not video or not video.filename:
        raise ValueError("Falta el video: 'video', 'library_clip', 'job_id' o 'path'.")
    if not config.allowed_file(video.filename):
        raise ValueError(f"Video: extensión no permitida. Usar: {sorted(config.ALLOWED_EXTENSIONS)}")
    return file_utils.save_upload(video), False


@bp.post("/process")
def process():
    try:
        params = MotionParams.from_form(request.form)
        path, keep = _source(params)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_id = job_manager.create_job(path, output_path="")
    threading.Thread(target=processor.process, args=(job_id, path, params, keep), daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.get("/status/<job_id>")
def status(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404
    return jsonify(status=job["status"], progress=job["progress"], stage=job.get("stage"), error=job["error"],
                   summary=job.get("summary"), motion=job.get("motion"),
                   has_preview=bool(job.get("output_path")))


def _file(job_id: str, key: str, mimetype: str, name: str, attachment: bool = True):
    job = job_manager.get_job(job_id)
    if not job or job["status"] != "done":
        return jsonify(error="El análisis no terminó."), 404
    path = job.get(key) or ""
    if not os.path.isfile(path):
        return jsonify(error="Este análisis no tiene ese archivo."), 404
    return send_file(path, mimetype=mimetype, as_attachment=attachment, download_name=name)


@bp.get("/json/<job_id>")
def json_file(job_id):
    return _file(job_id, "json_path", "application/json", "motion.json")


@bp.get("/summary/<job_id>")
def summary_file(job_id):
    job = job_manager.get_job(job_id)
    if not job or job["status"] != "done":
        return jsonify(error="El análisis no terminó."), 404
    return "\n".join(job.get("summary") or []) + "\n", 200, {"Content-Type": "text/plain; charset=utf-8"}


@bp.get("/sheet/<job_id>")
def sheet_file(job_id):
    return _file(job_id, "sheet_path", "image/jpeg", "eventos.jpg", attachment=False)


@bp.get("/download/<job_id>")
def download(job_id):
    return _file(job_id, "output_path", "video/mp4", "motion-preview.mp4")
