"""Flask blueprint of the editing engine (/api/editing): options for the UI and
for Claude (looks, LUTs, speed profiles and optional binaries), the effects
catalog and the effects gallery.

    GET  /options                     → looks, luts, profiles, rife, esrgan…
    GET  /catalog                     → catalog.json (every effect, its params and defaults)
    POST /gallery                     → 202 {job_id}  video (file | library_clip | job_id | path),
                                        start (s or m:ss), [effects (ids, JSON or CSV)]
    GET  /gallery/status/<job_id>     → {status, progress, stage, error, gallery}
    GET  /gallery/sheet/<job_id>      → galeria.jpg (one labeled frame per effect)
    GET  /gallery/clip/<job_id>/<id>  → the clip of one effect (mp4)
"""
import json
import os
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import external, file_utils, job_manager
from modules.beatmap import analysis, lyrics
from modules.editing import effects, gallery, timing
from modules.library.routes import resolve_clip
from modules.motion import subject
from modules.motion.schema import _seconds

bp = Blueprint("editing", __name__, url_prefix="/api/editing")


@bp.get("/options")
def options():
    return jsonify(
        looks=effects.looks(), luts=effects.luts(), profiles=list(timing.PROFILES),
        rife=external.rife_available(), esrgan=external.esrgan_available(),
        beat_this=analysis.beat_this_available(), vocal_separation=lyrics.separation_available(),
        subject_tracking=subject.available(),
    )


@bp.get("/catalog")
def catalog():
    return jsonify(gallery.catalog())


def _gallery_source() -> tuple[str, bool]:
    path = str(request.form.get("path") or "").strip().strip('"')
    if path:
        if not os.path.isfile(path) or not config.allowed_file(path):
            raise ValueError(f"No existe el video o la extensión no está permitida: {path}")
        return path, True
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


@bp.post("/gallery")
def gallery_process():
    try:
        source, keep = _gallery_source()
        start = _seconds(request.form, "start") or 0.0
        raw = str(request.form.get("effects") or "").strip()
        ids = None
        if raw:
            try:
                ids = json.loads(raw)
            except ValueError:
                ids = [x.strip() for x in raw.split(",") if x.strip()]
            known = {e["id"] for e in gallery.catalog()["effects"]}
            bad = [i for i in ids if i not in known]
            if bad:
                raise ValueError(f"Efecto desconocido: {bad[0]!r}. Ver GET /api/editing/catalog.")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_id = job_manager.create_job(source, output_path="")
    threading.Thread(target=gallery.run, args=(job_id, source, start, ids, gallery.folder_for(job_id), keep),
                     daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.get("/gallery/status/<job_id>")
def gallery_status(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404
    return jsonify(status=job["status"], progress=job["progress"], stage=job.get("stage"),
                   error=job["error"], gallery=job.get("gallery"))


@bp.get("/gallery/sheet/<job_id>")
def gallery_sheet(job_id):
    job = job_manager.get_job(job_id)
    if not job or job["status"] != "done" or not os.path.isfile(job.get("output_path") or ""):
        return jsonify(error="La galería no terminó."), 404
    return send_file(job["output_path"], mimetype="image/jpeg", max_age=0)


@bp.get("/gallery/clip/<job_id>/<name>")
def gallery_clip(job_id, name):
    job = job_manager.get_job(job_id)
    g = (job or {}).get("gallery") or {}
    if name not in g.get("clips", []):
        return jsonify(error="Ese efecto no está en la galería."), 404
    path = os.path.join(g["folder"], f"{name}.mp4")
    return send_file(path, mimetype="video/mp4", max_age=0)
