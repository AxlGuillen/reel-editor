"""Blueprint Flask de anime_edit (/api/anime-edit).

Contrato (también para que Claude lo opere solo; ver CLAUDE.md):
    POST /process              → 202 {job_id}  fase 1. Video: `video` (archivo), `library_clip`
                                               o `video_url`. Canción: `musica`, `musica_url`
                                               o `musica_del_video=1`. [letra, idioma, motor]
    GET  /status/<job_id>      → al terminar la fase 1: planos, propuesta, heroe, hojas, beatmap;
                                 al terminar la fase 2: resultado {carpeta, variantes}
    GET  /hoja/<job_id>/<n>    → JPG con 48 planos numerados (n desde 1)
    GET  /thumb/<job_id>/<id>  → miniatura de un plano
    POST /finish               → 202  job_id + [seleccion, heroe, variantes, formato, duracion_s,
                                 musica_desde, texto, look, lut, intensidad, fps, interpolar,
                                 escalar, volumen_original, watermark, marca_alto, titulo]
    GET  /download/<job_id>/<n> → la variante n (desde 1)
    GET  /presets              → las variantes disponibles
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
    musica_path = None
    if not params.musica and not params.musica_del_video:
        musica = request.files.get("musica")
        if not musica or not musica.filename:
            return jsonify(error="Falta la canción: subí 'musica', mandá 'musica_url' o usá musica_del_video=1."), 400
        if not config.allowed_audio_file(musica.filename):
            return jsonify(error=f"Música: extensión no permitida. Usar: {sorted(config.ALLOWED_AUDIO_EXTENSIONS)}"), 400
        musica_path = file_utils.save_upload(musica)
    job_id = job_manager.create_job(video_path or params.video.url, output_path="")
    threading.Thread(target=processor.process, args=(job_id, video_path, musica_path, params),
                     kwargs={"conservar_video": library_path is not None}, daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.post("/finish")
def finish():
    datos = request.get_json(silent=True) or request.form
    job_id = str(datos.get("job_id") or "").strip()
    job = job_manager.get_job(job_id)
    sesion = processor._SESIONES.get(job_id)
    if not job or not sesion:
        return jsonify(error="Sesión no encontrada. Volvé a analizar el video."), 404
    if job["status"] == "processing":
        return jsonify(error="Ese trabajo todavía está procesando."), 409
    try:
        fp = FinishParams.from_form(datos, {p["id"] for p in sesion["planos"]})
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_manager.update_job(job_id, status="processing", progress=0, finished=False, error=None,
                           resultado=None, stage="Preparando el edit…")
    threading.Thread(target=processor.finish, args=(job_id, fp), daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.get("/status/<job_id>")
def status(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404
    return jsonify(status=job["status"], progress=job["progress"], stage=job.get("stage"),
                   error=job["error"], finished=bool(job.get("finished")), planos=job.get("planos"),
                   propuesta=job.get("propuesta"), heroe=job.get("heroe"), hojas=job.get("hojas"),
                   beatmap=job.get("beatmap"), video_dur=job.get("video_dur"),
                   resultado=job.get("resultado"))


@bp.get("/hoja/<job_id>/<int:n>")
def hoja(job_id, n):
    sesion = processor._SESIONES.get(job_id)
    if not sesion or not 1 <= n <= len(sesion["hojas"]):
        return jsonify(error="Esa hoja no existe."), 404
    return send_file(sesion["hojas"][n - 1], mimetype="image/jpeg", max_age=0)


@bp.get("/thumb/<job_id>/<int:plano>")
def thumb(job_id, plano):
    sesion = processor._SESIONES.get(job_id)
    path = os.path.join(sesion["carpeta"], f"plano_{plano:04d}.jpg") if sesion else ""
    if not path or not os.path.isfile(path):
        return jsonify(error="Ese plano no existe."), 404
    return send_file(path, mimetype="image/jpeg", max_age=0)


@bp.get("/download/<job_id>/<int:n>")
def download(job_id, n):
    job = job_manager.get_job(job_id)
    res = (job or {}).get("resultado")
    if not job or job["status"] != "done" or not res:
        return jsonify(error="Todavía no hay edit para ese trabajo."), 409
    if not 1 <= n <= len(res["variantes"]):
        return jsonify(error="Esa variante no existe."), 404
    v = res["variantes"][n - 1]
    return send_file(v["video"], mimetype="video/mp4", as_attachment=True, download_name=v["nombre"])


@bp.get("/presets")
def lista_presets():
    from modules.anime_edit.plan import FORMATOS
    return jsonify(presets=presets.lista(), formatos=list(FORMATOS))
