"""Blueprint Flask del módulo downloader."""
import os
import threading

from flask import Blueprint, jsonify, request, send_file

from core import job_manager
from modules.downloader import processor
from modules.downloader.schema import DownloaderParams

bp = Blueprint("downloader", __name__, url_prefix="/api/downloader")

_MIMETYPES = {".mp3": "audio/mpeg", ".mp4": "video/mp4",
              ".m4a": "audio/mp4", ".webm": "video/webm"}


@bp.post("/process")
def process():
    try:
        params = DownloaderParams.from_form(request.form)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    job_id = job_manager.create_job(params.url, output_path="")
    thread = threading.Thread(
        target=processor.process, args=(job_id, params), daemon=True,
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
        title=job.get("title"),
    )


@bp.get("/download/<job_id>")
def download(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404
    if job["status"] != "done":
        return jsonify(error=f"job no está listo (status: {job['status']})"), 409

    path = job["output_path"]
    ext = os.path.splitext(path)[1].lower()
    title = (job.get("title") or "descarga").replace("/", "-").replace("\\", "-")

    return send_file(
        path,
        mimetype=_MIMETYPES.get(ext, "application/octet-stream"),
        as_attachment=True,
        download_name=f"{title}{ext}",
    )
