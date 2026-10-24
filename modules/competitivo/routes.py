"""Blueprint Flask del módulo competitivo (/api/competitivo).

Contrato (también para que Claude lo opere solo; ver CLAUDE.md):
    POST /process                 → 202 {job_id}   fase 1: propuesta + anclas
    GET  /status/<job_id>         → estado; al terminar la fase 1: partidas, clips, seleccion
    POST /finish                  → 202 {job_id}   fase 2: render (job_id en el form)
    POST /edit                    → 202 {job_id}   modo edit: las peleas al ritmo de una canción
    POST /edit/finish             → 202 {job_id}   edit con review=1: los segmentos de Ibai corregidos
                                                   (job_id de la fase 1 + `music` o `music_url`;
                                                   devuelve un job nuevo, con su /status y /download)
    GET  /download/<job_id>       → resumen.mp4
    GET  /download/<job_id>/<n>   → clip n (1..)
    GET  /frame/<job_id>/<n>      → JPG de control del ancla de la partida n
    GET  /portada/<job_id>        → portada.jpg (después de /finish)
    GET  /resumenes               → resúmenes guardados (carpeta persistente)
    GET  /resumenes/<ruta>        → un archivo guardado (ruta relativa, con subcarpetas)
"""
import json
import os
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import file_utils, job_manager
from modules.competitivo import processor, salida
from modules.competitivo.schema import CompetitivoParams, EditParams, FinishParams

bp = Blueprint("competitivo", __name__, url_prefix="/api/competitivo")


@bp.post("/process")
def process():
    try:
        params = CompetitivoParams.from_form(request.form)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_id = job_manager.create_job(params.vod_url, output_path="")
    job_manager.update_job(job_id, modo=params.modo)
    threading.Thread(target=processor.process, args=(job_id, params), daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.post("/finish")
def finish():
    job_id = (request.form.get("job_id") or "").strip()
    sesion = processor.get_session(job_id)
    job = job_manager.get_job(job_id)
    if not job or not sesion:
        return jsonify(error="Sesión no encontrada. Volvé a generar la propuesta."), 404
    if job["status"] == "processing":
        return jsonify(error="Ese trabajo todavía está procesando."), 409
    try:
        fp = FinishParams.from_form(request.form, sesion["params"])
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_manager.update_job(job_id, status="processing", progress=0, finished=False,
                           stage="Preparando el render…", error=None, resultado=None,
                           portada=None, intro=None)
    threading.Thread(target=processor.finish, args=(job_id, fp), daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.post("/edit")
def edit():
    phase1 = (request.form.get("job_id") or "").strip()
    sesion = processor.get_session(phase1)
    if not sesion:
        return jsonify(error="Sesión no encontrada. Volvé a generar la propuesta (fase 1)."), 404
    # A /finish of the same session is downloading VOD sections too: both at
    # once means 6 yt-dlp in parallel against YouTube or Twitch.
    if (job_manager.get_job(phase1) or {}).get("status") == "processing":
        return jsonify(error="Ese trabajo todavía está procesando (fase 1 o /finish). Esperá a que termine."), 409
    try:
        ep = EditParams.from_form(request.form, sesion["params"])
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if ep.team and ep.team not in processor.team_codes(sesion):
        return jsonify(error=f"'team': {ep.team} no jugó en esta sesión "
                             f"({', '.join(sorted(processor.team_codes(sesion)))}).", ), 400
    music_path = None
    if not ep.music:
        music = request.files.get("music")
        if not music or not music.filename:
            return jsonify(error="Falta la música: subí el archivo 'music' o mandá 'music_url'."), 400
        if not config.allowed_audio_file(music.filename):
            return jsonify(error=f"Música: extensión no permitida. Usar: {sorted(config.ALLOWED_AUDIO_EXTENSIONS)}"), 400
        music_path = file_utils.save_upload(music)
    job_id = job_manager.create_job(sesion["params"].vod_url, output_path="")
    # `modo` is the key the competitivo status already exposes.
    job_manager.update_job(job_id, modo="edit", phase1=phase1)
    threading.Thread(target=processor.edit, args=(job_id, phase1, ep, music_path), daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.post("/edit/finish")
def edit_finish():
    """Resumes an edit paused with review=1: `segments` (JSON) are Ibai's
    segments of its status, with the text fixed (without them, as they are).
    The same call renders again an edit whose render failed (`retryable`)."""
    job_id = (request.form.get("job_id") or "").strip()
    if not job_manager.get_job(job_id):
        return jsonify(error="job_id no encontrado"), 404
    if not processor.edit_waiting(job_id):
        return jsonify(error="Ese edit no está esperando revisión ni se puede reintentar."), 409
    try:
        segments = json.loads(request.form.get("segments") or "null")
        if segments is not None and not isinstance(segments, list) or segments and not all(
                isinstance(x, dict) and isinstance(x.get("text", ""), str)
                and isinstance(x.get("start"), (int, float)) and isinstance(x.get("end"), (int, float))
                and isinstance(x.get("words") or [], list) for x in segments):
            raise ValueError
    except ValueError:
        return jsonify(error="'segments' tiene que ser la lista de segmentos del status "
                             "({start, end, text, [words]}), en JSON."), 400
    threading.Thread(target=processor.edit_finish, args=(job_id, segments), daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.get("/status/<job_id>")
def status(job_id):
    job = job_manager.get_job(job_id)
    if not job and processor.get_session(job_id):       # a phase 1 from before a restart
        job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404
    return jsonify(
        status=job["status"], progress=job["progress"], stage=job.get("stage"),
        error=job["error"], finished=bool(job.get("finished")), modo=job.get("modo"),
        aviso=job.get("aviso"), vod=job.get("vod"), partidas=job.get("partidas"),
        clips=job.get("clips"), seleccion=job.get("seleccion"), resultado=job.get("resultado"),
        portada=job.get("portada"), intro=job.get("intro"), plan=job.get("plan"),
        segments=job.get("segments"), vocabulary=job.get("vocabulary"),
        voice_peaks=job.get("voice_peaks"), split_camera=job.get("split_camera"),
        retryable=bool(job.get("retryable")),
    )


def _resultado(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return None, (jsonify(error="job_id no encontrado"), 404)
    res = job.get("resultado")
    if job["status"] != "done" or not job.get("finished") or not res:
        return None, (jsonify(error="Todavía no hay resumen para ese trabajo."), 409)
    return res, None


@bp.get("/download/<job_id>")
def download(job_id):
    res, err = _resultado(job_id)
    if err:
        return err
    if res.get("download_name"):                  # edit mode
        nombre = res["download_name"]
    else:
        nombre = res["resumen"] if res.get("organizada") else f"{res['nombre']} - resumen.mp4"
    return send_file(res["video"], mimetype="video/mp4", as_attachment=True, download_name=nombre)


@bp.get("/download/<job_id>/<int:n>")
def download_clip(job_id, n):
    res, err = _resultado(job_id)
    if err:
        return err
    if "clips" not in res or "carpeta" not in res:          # an /edit job: one video, no clips
        return jsonify(error="Ese trabajo no tiene clips sueltos."), 404
    if not 1 <= n <= len(res["clips"]):
        return jsonify(error="Ese clip no existe."), 404
    clip = res["clips"][n - 1]
    return send_file(os.path.join(res["carpeta"], clip["archivo"]), mimetype="video/mp4",
                     as_attachment=True, download_name=f"{res['nombre']} - {clip['archivo']}")


@bp.get("/frame/<job_id>/<int:n>")
def frame(job_id, n):
    sesion = processor.get_session(job_id)
    if not sesion or not 1 <= n <= len(sesion["partidas"]):
        return jsonify(error="No hay frame de control para esa partida."), 404
    nombre = sesion["partidas"][n - 1].get("frame")
    path = os.path.join(sesion["carpeta"], nombre) if nombre else None
    if not path or not os.path.isfile(path):
        return jsonify(error="No hay frame de control para esa partida."), 404
    return send_file(path, mimetype="image/jpeg", max_age=0)


@bp.get("/portada/<job_id>")
def portada(job_id):
    res, err = _resultado(job_id)
    if err:
        return err
    if "carpeta" not in res:                                   # an /edit job
        return jsonify(error="Ese trabajo no tiene portada."), 404
    path = os.path.join(res["carpeta"], res.get("portada") or "portada.jpg")
    if not os.path.isfile(path):
        return jsonify(error="Ese resumen no tiene portada."), 404
    return send_file(path, mimetype="image/jpeg", max_age=0,
                     as_attachment=request.args.get("descargar") == "1",
                     download_name=f"{res['nombre']} - portada.jpg")


@bp.get("/resumenes")
def resumenes():
    return jsonify(carpeta=config.COMPETITIVO_FOLDER, resumenes=processor.listar_resumenes())


@bp.get("/resumenes/<path:ruta>")
def resumen_archivo(ruta):
    # La ruta es relativa a COMPETITIVO_FOLDER y no puede salir de ahí.
    path = salida.resolver(ruta)
    if not path:
        return jsonify(error="Archivo no encontrado."), 404
    tipos = {".mp4": "video/mp4", ".json": "application/json", ".jpg": "image/jpeg"}
    return send_file(path, mimetype=tipos.get(os.path.splitext(path)[1].lower(), "application/octet-stream"),
                     as_attachment=request.args.get("descargar") == "1")
