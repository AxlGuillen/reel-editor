"""Blueprint Flask del módulo competitivo (/api/competitivo).

Contrato (también para que Claude lo opere solo; ver CLAUDE.md):
    POST /process                 → 202 {job_id}   fase 1: propuesta + anclas
    GET  /status/<job_id>         → estado; al terminar la fase 1: partidas, clips, seleccion
    POST /finish                  → 202 {job_id}   fase 2: render (job_id en el form)
    GET  /download/<job_id>       → resumen.mp4
    GET  /download/<job_id>/<n>   → clip n (1..)
    GET  /frame/<job_id>/<n>      → JPG de control del ancla de la partida n
    GET  /portada/<job_id>        → portada.jpg (después de /finish)
    GET  /resumenes               → resúmenes guardados (carpeta persistente)
    GET  /resumenes/<carpeta>/<archivo> → un archivo de un resumen guardado
"""
import os
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import job_manager
from modules.competitivo import processor
from modules.competitivo.schema import CompetitivoParams, FinishParams

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
    job = job_manager.get_job(job_id)
    sesion = processor._SESIONES.get(job_id)
    if not job or not sesion:
        return jsonify(error="Sesión no encontrada. Volvé a generar la propuesta."), 404
    if job["status"] == "processing":
        return jsonify(error="Ese trabajo todavía está procesando."), 409
    try:
        fp = FinishParams.from_form(request.form, sesion["params"])
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_manager.update_job(job_id, status="processing", progress=0, finished=False,
                           stage="Preparando el render…", error=None, resultado=None)
    threading.Thread(target=processor.finish, args=(job_id, fp), daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.get("/status/<job_id>")
def status(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404
    return jsonify(
        status=job["status"], progress=job["progress"], stage=job.get("stage"),
        error=job["error"], finished=bool(job.get("finished")), modo=job.get("modo"),
        aviso=job.get("aviso"), vod=job.get("vod"), partidas=job.get("partidas"),
        clips=job.get("clips"), seleccion=job.get("seleccion"), resultado=job.get("resultado"),
        portada=job.get("portada"), intro=job.get("intro"),
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
    return send_file(os.path.join(res["carpeta"], res["resumen"]), mimetype="video/mp4",
                     as_attachment=True, download_name=f"{res['nombre']} - resumen.mp4")


@bp.get("/download/<job_id>/<int:n>")
def download_clip(job_id, n):
    res, err = _resultado(job_id)
    if err:
        return err
    if not 1 <= n <= len(res["clips"]):
        return jsonify(error="Ese clip no existe."), 404
    clip = res["clips"][n - 1]
    return send_file(os.path.join(res["carpeta"], clip["archivo"]), mimetype="video/mp4",
                     as_attachment=True, download_name=f"{res['nombre']} - {clip['archivo']}")


@bp.get("/frame/<job_id>/<int:n>")
def frame(job_id, n):
    sesion = processor._SESIONES.get(job_id)
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
    path = os.path.join(res["carpeta"], res.get("portada") or "portada.jpg")
    if not os.path.isfile(path):
        return jsonify(error="Ese resumen no tiene portada."), 404
    return send_file(path, mimetype="image/jpeg", max_age=0,
                     as_attachment=request.args.get("descargar") == "1",
                     download_name=f"{res['nombre']} - portada.jpg")


@bp.get("/resumenes")
def resumenes():
    return jsonify(carpeta=config.COMPETITIVO_FOLDER, resumenes=processor.listar_resumenes())


@bp.get("/resumenes/<carpeta>/<archivo>")
def resumen_archivo(carpeta, archivo):
    base = os.path.realpath(config.COMPETITIVO_FOLDER)
    path = os.path.realpath(os.path.join(base, os.path.basename(carpeta), os.path.basename(archivo)))
    if not path.startswith(base + os.sep) or not os.path.isfile(path):
        return jsonify(error="Archivo no encontrado."), 404
    tipos = {".mp4": "video/mp4", ".json": "application/json", ".jpg": "image/jpeg"}
    return send_file(path, mimetype=tipos.get(os.path.splitext(path)[1].lower(), "application/octet-stream"),
                     as_attachment=request.args.get("descargar") == "1")
