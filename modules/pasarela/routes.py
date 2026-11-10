"""Flask blueprint of the pasarela (/api/pasarela). JSON in, JSON out, local
paths (nothing is uploaded): it is run by Claude from the chat.

    POST /process            JSON {gameplay: {archivo} | {insights: {grabacion, antes, despues, eventos}},
                             voz, deck, banda, watermark, subtitulos, salida, [clips, volumen_gameplay,
                             idioma, modelo]} → 202 {job_id}
    GET  /status/<job_id>    → {status, progress, stage, error, duration, report_ready}
    GET  /report/<job_id>    → the report (also after a restart: cache/pasarela/reports/)
    GET  /download/<job_id>  → the mp4 (it is already at `salida`)
    GET  /recordings         → Insights Capture's recordings: champion, KDA, win/loss, multikills,
                             events, whether the file is still there
    POST /highlight          JSON {grabacion, antes, despues, eventos, [salida]} → 202 {job_id}: the
                             16:9 highlight on its own (default folder: HIGHLIGHTS_FOLDER)
"""
from __future__ import annotations

import os
import threading

from flask import Blueprint, jsonify, request, send_file

from core import job_manager
from modules.pasarela import insights, processor
from modules.pasarela.schema import Highlight, PasarelaParams

bp = Blueprint("pasarela", __name__, url_prefix="/api/pasarela")


def _json() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("Mandá un JSON (Content-Type: application/json).")
    return data


@bp.post("/process")
def process():
    try:
        params = PasarelaParams.from_json(_json())
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_id = job_manager.create_job(params.voice, params.output)
    job_manager.update_job(job_id, kind="pasarela", stage="En cola…")
    threading.Thread(target=processor.process, args=(job_id, params), daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.get("/status/<job_id>")
def status(job_id):
    job = job_manager.get_job(job_id)
    if not job:
        report = processor.load_report(job_id)
        if report:          # finished before a restart
            return jsonify(job_id=job_id, status="error" if report.get("error") else "done", progress=100,
                           error=report.get("error"), report_ready=True)
        return jsonify(error="No existe ese job."), 404
    keys = ("job_id", "status", "progress", "stage", "error", "duration", "report_ready", "result")
    return jsonify({k: job.get(k) for k in keys if k in job})


@bp.get("/report/<job_id>")
def report(job_id):
    data = processor.load_report(job_id)
    if data is None:
        job = job_manager.get_job(job_id)
        if job and job["status"] in ("pending", "processing"):
            return jsonify(error="Todavía está procesando.", stage=job.get("stage")), 409
        return jsonify(error="No hay reporte de ese job."), 404
    return jsonify(data)


@bp.get("/download/<job_id>")
def download(job_id):
    job = job_manager.get_job(job_id)
    path = (job or {}).get("output_path")
    if not job or job["status"] != "done" or not path or not os.path.isfile(path):
        return jsonify(error="El video no está listo."), 404
    return send_file(path, mimetype="video/mp4", as_attachment=True, download_name=os.path.basename(path))


@bp.get("/recordings")
def recordings():
    try:
        return jsonify(recordings=insights.recordings())
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@bp.post("/highlight")
def highlight():
    try:
        data = _json()
        h = Highlight.from_json(data)
        out = data.get("salida")
        if out is not None:
            if not isinstance(out, str) or not out.lower().endswith(".mp4"):
                raise ValueError("'salida' tiene que ser la ruta de un .mp4.")
            out = os.path.abspath(os.path.expanduser(out))
            if not os.path.isdir(os.path.dirname(out)):
                raise ValueError(f"No existe la carpeta {os.path.dirname(out)}.")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_id = job_manager.create_job(h.recording, out or "")
    job_manager.update_job(job_id, kind="pasarela_highlight")
    threading.Thread(target=processor.highlight_job,
                     args=(job_id, h.recording, h.before, h.after, h.events, out), daemon=True).start()
    return jsonify(job_id=job_id), 202
