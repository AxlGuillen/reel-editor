"""Blueprint Flask del módulo reel_express."""
import os
import threading

from flask import Blueprint, jsonify, request, send_file

import config
from core import file_utils, job_manager
from modules.reel_express import processor
from modules.reel_express.schema import ReelExpressParams
from modules.subtitles.schema import SubtitlesParams

bp = Blueprint("reel_express", __name__, url_prefix="/api/reel-express")


@bp.post("/process")
def process():
    if "clip" not in request.files:
        return jsonify(error="Falta el archivo 'clip' (video 16:9)"), 400
    clip = request.files["clip"]
    if not clip.filename:
        return jsonify(error="Nombre de clip vacío"), 400
    if not config.allowed_file(clip.filename):
        return jsonify(
            error=f"Clip: extensión no permitida. Usar: {sorted(config.ALLOWED_EXTENSIONS)}"
        ), 400

    try:
        params = ReelExpressParams.from_form(request.form)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    # Fuente de audio por archivo: validar y guardar el upload (narración).
    audio_path = None
    if params.audio_source == "file":
        audio = request.files.get("audio")
        if not audio or not audio.filename:
            return jsonify(error="Falta el archivo de audio (narración)."), 400
        if not config.allowed_audio_file(audio.filename):
            return jsonify(
                error=f"Audio: extensión no permitida. Usar: {sorted(config.ALLOWED_AUDIO_EXTENSIONS)}"
            ), 400
        audio_path = file_utils.save_upload(audio)

    # Fondo dinámico: el clip de la "caída" que se mete entre partes del video.
    dynamic_clip_path = None
    if params.has_dynamic:
        dyn = request.files.get("dynamic_clip")
        if not dyn or not dyn.filename:
            return jsonify(error="Falta el clip de la caída (fondo dinámico)."), 400
        if not config.allowed_file(dyn.filename):
            return jsonify(
                error=f"Caída: extensión no permitida. Usar: {sorted(config.ALLOWED_EXTENSIONS)}"
            ), 400
        dynamic_clip_path = file_utils.save_upload(dyn)

    clip_path = file_utils.save_upload(clip)
    job_id = job_manager.create_job(clip_path, output_path="")
    # has_subs/has_dyn definen las bandas de progreso (ver _aggregate_progress).
    # output_name: nombre elegido por el usuario para la descarga (opcional).
    job_manager.update_job(job_id, has_subs=params.has_subtitles,
                           has_dyn=params.has_dynamic,
                           output_name=request.form.get("output_name"))

    thread = threading.Thread(
        target=processor.process,
        args=(job_id, clip_path, params),
        kwargs={"audio_path": audio_path, "dynamic_clip_path": dynamic_clip_path},
        daemon=True,
    )
    thread.start()

    return jsonify(job_id=job_id), 202


@bp.post("/finish")
def finish():
    """Fase 2: con los subtítulos editados, quema sobre el video base."""
    data = request.get_json(silent=True) or {}

    src = job_manager.get_job(data.get("job_id"))
    if not src:
        return jsonify(error="Sesión no encontrada. Volvé a generar el reel."), 400
    base_path = src.get("base_path")
    if not base_path or not os.path.exists(base_path):
        return jsonify(error="El video base ya no está disponible. Volvé a generar."), 400

    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        return jsonify(error="No hay subtítulos para generar."), 400

    try:
        sub_params = SubtitlesParams.from_form(data)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400

    job_id = job_manager.create_job(base_path, output_path="")
    job_manager.update_job(job_id, output_name=data.get("output_name"))
    output_path = file_utils.output_path_for(job_id)

    thread = threading.Thread(
        target=processor.finish,
        args=(job_id, base_path, segments, sub_params, output_path),
        daemon=True,
    )
    thread.start()
    return jsonify(job_id=job_id), 202


def _bands(job: dict) -> dict:
    """Bandas (lo, hi) de progreso por fase, según haya subtítulos / fondo dinámico.

    El watermark ya no es fase propia: sus filtros van fusionados en la pasada
    de vertical (prep). Con subtítulos, la transcripción se lleva la cola. El
    fondo dinámico (cutaway) es una fase intermedia entre mix y transcribe.
    """
    subs = job.get("has_subs")
    dyn = job.get("has_dyn")
    # Camino rápido de "No revisar": descarga → transcripción → pasada visual
    # (el palo largo, con los subs adentro) → mezcla en copy.
    if job.get("fast_subs"):
        return {"prep": (0, 12), "transcribe": (12, 28),
                "visual": (28, 90), "mix": (90, 100)}
    # Transcripción temprana: corre DENTRO de prep (en paralelo con la pasada
    # visual), así que no hay fase transcribe aparte.
    if subs and job.get("early_subs"):
        if dyn:
            return {"prep": (0, 60), "mix": (60, 75), "dynamic": (75, 100)}
        return {"prep": (0, 80), "mix": (80, 100)}
    if subs and dyn:
        return {"prep": (0, 25), "mix": (25, 38), "dynamic": (38, 55),
                "transcribe": (55, 100)}
    if subs:
        return {"prep": (0, 30), "mix": (30, 45), "transcribe": (45, 100)}
    if dyn:
        return {"prep": (0, 55), "mix": (55, 72), "dynamic": (72, 100)}
    return {"prep": (0, 70), "mix": (70, 100)}


def _aggregate_progress(job: dict) -> tuple[int, str]:
    """Combina el progreso de los sub-jobs en un % global + label de etapa."""
    bands = _bands(job)
    phase = job.get("phase")

    if phase == "prep":
        lo, hi = bands["prep"]
        # Promedia solo los sub-jobs que existan: la preparación visual se salta
        # cuando el clip ya viene 9:16 y no hay texto/marca; sub_t existe solo
        # con transcripción temprana (corre dentro de prep, en paralelo).
        progresos = [
            (job_manager.get_job(job[key]) or {}).get("progress", 0)
            for key in ("sub_v", "sub_d", "sub_t") if job.get(key)
        ]
        base = sum(progresos) / len(progresos) if progresos else 100
        return int(lo + base / 100 * (hi - lo)), "Preparando video y audio…"
    if phase == "visual":
        lo, hi = bands["visual"]
        v = (job_manager.get_job(job.get("sub_v")) or {}).get("progress", 0)
        return int(lo + (v / 100) * (hi - lo)), "Generando reel con subtítulos…"
    if phase == "mix":
        lo, hi = bands["mix"]
        m = (job_manager.get_job(job.get("sub_m")) or {}).get("progress", 0)
        return int(lo + (m / 100) * (hi - lo)), "Mezclando…"
    if phase == "dynamic":
        lo, hi = bands["dynamic"]
        c = (job_manager.get_job(job.get("sub_c")) or {}).get("progress", 0)
        return int(lo + (c / 100) * (hi - lo)), "Metiendo fondo dinámico…"
    if phase == "transcribe":
        lo, hi = bands["transcribe"]
        t = (job_manager.get_job(job.get("sub_t")) or {}).get("progress", 0)
        return int(lo + (t / 100) * (hi - lo)), "Transcribiendo subtítulos…"

    # Sin fase (p. ej. la fase 2 de quemado usa el progreso directo del job).
    return job.get("progress", 0), job.get("stage") or "Procesando…"


@bp.get("/status/<job_id>")
def status(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404

    if job["status"] == "processing":
        progress, stage = _aggregate_progress(job)
    else:
        progress, stage = job["progress"], None

    return jsonify(
        status=job["status"],
        progress=progress,
        stage=stage,
        error=job["error"],
        segments=job.get("segments"),
        # Camino rápido de "No revisar": la fase 1 ya produjo el reel FINAL
        # (subs quemados en la pasada visual); el frontend no debe llamar a
        # /finish, solo mostrar el resultado.
        finished=bool(job.get("fast_done")),
    )


@bp.get("/download/<job_id>")
def download(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        return jsonify(error="job_id no encontrado"), 404
    if job["status"] != "done":
        return jsonify(error=f"job no está listo (status: {job['status']})"), 409

    return send_file(
        job["output_path"],
        mimetype="video/mp4",
        as_attachment=True,
        download_name=file_utils.safe_download_name(
            job.get("output_name"), f"reel_express_{job_id}"),
    )
