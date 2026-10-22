"""Flask blueprint of anime_edit (/api/anime-edit).

Contract (also so Claude can drive it on its own; see CLAUDE.md):
    POST /process              → 202 {job_id}  phase 1. Video: `video` (file), `library_clip`
                                               or `video_url`. Song: `music`, `music_url`
                                               or `music_from_video=1`. [lyrics, language, engine]
    GET  /status/<job_id>      → after phase 1: shots, proposal, hero, sheets, beatmap;
                                 after phase 2: result {folder, variants}
    GET  /sheet/<job_id>/<n>   → JPG with 48 numbered shots (n from 1)
    GET  /thumb/<job_id>/<id>  → thumbnail of a shot
    POST /finish               → 202  job_id + [selection, hero, variants, format, duration_s,
                                 music_start, text, look, lut, intensity, fps, interpolate,
                                 upscale, original_volume, watermark, watermark_height, title]
    GET  /download/<job_id>/<n> → variant n (from 1)
    GET  /presets              → the available variants
"""
import os
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import file_utils, job_manager
from modules.anime_edit import presets, processor
from modules.anime_edit.schema import AnimeEditParams, FinishParams
from modules.library.routes import resolve_clip

bp = Blueprint("anime_edit", __name__, url_prefix="/api/anime-edit")


@bp.post("/process")
def process():
    try:
        params = AnimeEditParams.from_form(request.form)
        library = (request.form.get("library_clip") or "").strip()
        library_path = resolve_clip(library) if library else None
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    video_path = library_path
    if not params.video and not library_path:
        video = request.files.get("video")
        if not video or not video.filename:
            return jsonify(error="Falta el video: subí 'video', elegí uno de la librería o mandá 'video_url'."), 400
        if not config.allowed_file(video.filename):
            return jsonify(error=f"Video: extensión no permitida. Usar: {sorted(config.ALLOWED_EXTENSIONS)}"), 400
        video_path = file_utils.save_upload(video)
    music_path = None
    if not params.music and not params.music_from_video:
        music = request.files.get("music")
        if not music or not music.filename:
            return jsonify(error="Falta la canción: subí 'music', mandá 'music_url' o usá music_from_video=1."), 400
        if not config.allowed_audio_file(music.filename):
            return jsonify(error=f"Música: extensión no permitida. Usar: {sorted(config.ALLOWED_AUDIO_EXTENSIONS)}"), 400
        music_path = file_utils.save_upload(music)
    job_id = job_manager.create_job(video_path or params.video.url, output_path="")
    threading.Thread(target=processor.process, args=(job_id, video_path, music_path, params),
                     kwargs={"keep_video": library_path is not None}, daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.post("/finish")
def finish():
    data = request.get_json(silent=True) or request.form
    job_id = str(data.get("job_id") or "").strip()
    job = job_manager.get_job(job_id)
    session = processor._SESSIONS.get(job_id)
    if not job or not session:
        return jsonify(error="Sesión no encontrada. Volvé a analizar el video."), 404
    if job["status"] == "processing":
        return jsonify(error="Ese trabajo todavía está procesando."), 409
    try:
        fp = FinishParams.from_form(data, {p["id"] for p in session["shots"]})
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_manager.update_job(job_id, status="processing", progress=0, finished=False, error=None,
                           result=None, stage="Preparando el edit…")
    threading.Thread(target=processor.finish, args=(job_id, fp), daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.get("/status/<job_id>")
def status(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404
    return jsonify(status=job["status"], progress=job["progress"], stage=job.get("stage"),
                   error=job["error"], finished=bool(job.get("finished")), shots=job.get("shots"),
                   proposal=job.get("proposal"), hero=job.get("hero"), sheets=job.get("sheets"),
                   beatmap=job.get("beatmap"), video_duration=job.get("video_duration"),
                   result=job.get("result"))


@bp.get("/sheet/<job_id>/<int:n>")
def sheet(job_id, n):
    session = processor._SESSIONS.get(job_id)
    if not session or not 1 <= n <= len(session["sheets"]):
        return jsonify(error="Esa hoja no existe."), 404
    return send_file(session["sheets"][n - 1], mimetype="image/jpeg", max_age=0)


@bp.get("/thumb/<job_id>/<int:shot>")
def thumb(job_id, shot):
    session = processor._SESSIONS.get(job_id)
    path = os.path.join(session["folder"], f"shot_{shot:04d}.jpg") if session else ""
    if not path or not os.path.isfile(path):
        return jsonify(error="Ese plano no existe."), 404
    return send_file(path, mimetype="image/jpeg", max_age=0)


@bp.get("/download/<job_id>/<int:n>")
def download(job_id, n):
    job = job_manager.get_job(job_id)
    res = (job or {}).get("result")
    if not job or job["status"] != "done" or not res:
        return jsonify(error="Todavía no hay edit para ese trabajo."), 409
    if not 1 <= n <= len(res["variants"]):
        return jsonify(error="Esa variante no existe."), 404
    v = res["variants"][n - 1]
    return send_file(v["video"], mimetype="video/mp4", as_attachment=True, download_name=v["name"])


@bp.get("/presets")
def preset_list():
    from modules.anime_edit.plan import FORMATS
    return jsonify(presets=presets.listing(), formats=list(FORMATS))
