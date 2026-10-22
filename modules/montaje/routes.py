"""Blueprint Flask del montaje (/api/montaje).

    POST /process            → 202 {job_id}. Video base: `video` (archivo), `library_clip` o
                               `job_id` (de un Reel Express o un job terminado). Capas: `capas`
                               (JSON) con sus archivos subidos (`capas_archivos`, varios) o leídos
                               de `carpeta_capas` (dentro de CAPAS_FOLDER; acepta su manifest.json
                               por id). [caja, entrada_s, salida_s, tope_s, adelanto_s,
                               segments, subtitulos + estilo sub_*]
    GET  /status/<job_id>    → {status, progress, stage, error, capas}
    GET  /download/<job_id>  → el video montado
"""
import json
import os
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import file_utils, job_manager
from modules.library.routes import resolve_clip
from modules.montaje import processor
from modules.montaje.schema import MontajeParams

bp = Blueprint("montaje", __name__, url_prefix="/api/montaje")


def _segments(base_job: dict | None):
    """Los segments: los que llegan en el form, o los del job base (el
    status de reel_express/subtitles los trae)."""
    crudo = request.form.get("segments")
    if crudo:
        try:
            datos = json.loads(crudo)
        except ValueError:
            raise ValueError("'segments' tiene que ser JSON (los de la transcripción).")
        if not isinstance(datos, list):
            raise ValueError("'segments' tiene que ser una lista.")
        return datos
    return (base_job or {}).get("segments")


@bp.post("/process")
def process():
    subidos, guardados = {}, []
    for f in request.files.getlist("capas_archivos"):
        if f and f.filename:
            if not config.allowed_capa_file(f.filename):
                return jsonify(error=f"Capa «{f.filename}»: usá mov, webm, mp4, mkv o png."), 400
            path = file_utils.save_upload(f)
            guardados.append(path)
            subidos[os.path.basename(f.filename)] = path
    try:
        params = MontajeParams.from_form(request.form, subidos)
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
            guardados.append(video_path)
        segments = _segments(base_job)
        if params.subtitulos and not segments and not params.necesita_transcripcion:
            raise ValueError("Para quemar subtítulos mandá 'segments' (o un job_id que los tenga).")
    except ValueError as exc:
        file_utils.cleanup_paths(*guardados)
        return jsonify(error=str(exc)), 400
    job_id = job_manager.create_job(video_path, output_path="")
    job_manager.update_job(job_id, output_name=request.form.get("output_name"))
    threading.Thread(target=processor.process, args=(job_id, video_path, params, segments),
                     kwargs={"borrar": guardados}, daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.get("/status/<job_id>")
def status(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404
    return jsonify(status=job["status"], progress=job["progress"], stage=job.get("stage"),
                   error=job["error"], capas=job.get("capas"))


@bp.get("/download/<job_id>")
def download(job_id):
    job = job_manager.get_job(job_id)
    if not job or job["status"] != "done" or not os.path.isfile(job.get("output_path") or ""):
        return jsonify(error="El montaje no terminó."), 404
    nombre = file_utils.safe_download_name(job.get("output_name"), f"montaje-{job_id}")
    return send_file(job["output_path"], mimetype="video/mp4", as_attachment=True, download_name=nombre)
