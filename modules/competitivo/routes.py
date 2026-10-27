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
from core import file_utils, job_manager, scheduler
from modules.competitivo import calibration, metrics, presets, processor, salida
from modules.competitivo.schema import CompetitivoParams, EditParams, FinishParams

bp = Blueprint("competitivo", __name__, url_prefix="/api/competitivo")


@bp.get("/presets")
def presets_list():
    """The named sets of parameters (assets/competitivo/presets.json)."""
    return jsonify(presets=presets.listing())


@bp.get("/metrics")
def metrics_view():
    """The indicators of the last `days` days (default 7; 0 = everything) and the last jobs."""
    try:
        days = max(0, int(request.args.get("days", 7)))
    except ValueError:
        return jsonify(error="'days' tiene que ser un número de días."), 400
    records = metrics.load(days or None)
    return jsonify(days=days, file=metrics.metrics_file(), summary=metrics.summary(records),
                   discarded=metrics.discarded(), recent=records[-40:][::-1], slots=scheduler.snapshot(),
                   calibration=_calibration_brief(calibration.last_report()))


def _calibration_brief(report):
    if not report:
        return None
    return {k: report.get(k) for k in ("at", "cases", "ok", "fail", "error", "missing", "known", "fixed",
                                       "passed", "seconds")}


@bp.get("/calibration")
def calibration_get():
    """The calibration set: its cases and the last report."""
    return jsonify(folder=calibration.folder(), cases=[
        {k: c.get(k) for k in ("id", "check", "note", "known_issue")} for c in calibration.load_cases()],
        report=calibration.last_report())


@bp.post("/calibration")
def calibration_run():
    """Runs the calibration set in a job ([ids] = some cases, comma-separated) → 202 {job_id}."""
    if not calibration.load_cases():
        return jsonify(error=f"No hay casos de calibración en {calibration.folder()}."), 400
    ids = [x for x in (request.form.get("ids") or "").replace(" ", "").split(",") if x] or None
    job_id = job_manager.create_job("calibration", output_path="")
    job_manager.update_job(job_id, modo="calibration")

    def work():
        try:
            def progress(k, n, cid):
                job_manager.update_job(job_id, status="processing", progress=int(100 * k / n),
                                       stage=f"Caso {k + 1}/{n}: {cid}…")
            report = calibration.run(ids, progress)
            job_manager.update_job(job_id, status="done", progress=100, stage="listo", finished=True,
                                   resultado=report,
                                   aviso=None if report["passed"] else "La calibración tiene casos que fallan.")
        except Exception as exc:  # noqa: BLE001
            job_manager.update_job(job_id, status="error", error=str(exc))

    threading.Thread(target=work, daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.post("/process")
def process():
    try:
        form, preset = presets.apply(request.form, "process")
        params = CompetitivoParams.from_form(form)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_id = job_manager.create_job(params.vod_url, output_path="")
    job_manager.update_job(job_id, modo=params.modo, preset=preset or None)
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
        form, preset = presets.apply(request.form, "finish")
        fp = FinishParams.from_form(form, sesion["params"])
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_manager.update_job(job_id, preset=preset or None)
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
        form, preset = presets.apply(request.form, "edit")
        ep = EditParams.from_form(form, sesion["params"])
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
    job_manager.update_job(job_id, modo="edit", phase1=phase1, preset=preset or None)
    threading.Thread(target=processor.edit, args=(job_id, phase1, ep, music_path), daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.post("/daily")
def daily():
    """The day's summary by itself: game_ids (every game of the day), vod_url,
    carpeta_dia, [titulo, montage_name, picks (JSON {gameId: [ids]}), zoom,
    pre_s, post_s, zoom_mode, watermark] → 202 {job_id}."""
    import re as _re
    try:
        f, preset = presets.apply(request.form, "daily")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    ids = [x for x in _re.split(r"[\s,;]+", f.get("game_ids") or "") if x]
    vod = (f.get("vod_url") or "").strip()
    carpeta = (f.get("carpeta_dia") or "").strip()
    if not ids or not all(_re.fullmatch(r"\d{15,20}", x) for x in ids):
        return jsonify(error="Faltan los gameId del día (números largos, separados por coma)."), 400
    if not _re.match(r"^https?://", vod):
        return jsonify(error="Falta 'vod_url': el VOD del día (YouTube o Twitch)."), 400
    if not carpeta:
        return jsonify(error="Falta 'carpeta_dia', p. ej. «Resumen Suiza Día 2 (24-10)»."), 400
    try:
        picks = json.loads(f.get("picks") or "{}")
        render = {k: float(f[k]) for k in ("zoom", "pre_s", "post_s") if f.get(k)}
        if not isinstance(picks, dict):
            raise ValueError
    except ValueError:
        return jsonify(error="'picks' es un JSON {gameId: [ids]}; zoom, pre_s y post_s son números."), 400
    if f.get("zoom_mode"):
        if f["zoom_mode"] not in ("auto", "zoom", "full"):
            return jsonify(error="'zoom_mode' tiene que ser auto, zoom o full."), 400
        render["zoom_mode"] = f["zoom_mode"]
    if f.get("watermark"):
        render["watermark"] = f["watermark"]
    job_id = job_manager.create_job(vod, output_path="")
    job_manager.update_job(job_id, modo="daily", preset=preset or None)
    threading.Thread(target=processor.daily, daemon=True,
                     args=(job_id, ids, vod, carpeta, (f.get("titulo") or "highlights").strip(), picks, render,
                           (f.get("montage_name") or "").strip())).start()
    return jsonify(job_id=job_id), 202


@bp.post("/transcript")
def transcript():
    """The whole game transcribed (draft → nexus → post-game) on the scoreboard's
    clock, with the index of moments: job_id of a phase 1, [game_id, language,
    draft_lead_s, post_s (900: seconds after the nexus), batched (0: Whisper in
    batches, ~3.6x faster but with ~9 % fewer words), carpeta_dia, partido,
    mapa] → 202 {job_id NEW}."""
    phase1 = (request.form.get("job_id") or "").strip()
    sesion = processor.get_session(phase1)
    if not sesion:
        return jsonify(error="Sesión no encontrada. Volvé a generar la propuesta (fase 1)."), 404
    game_id = (request.form.get("game_id") or "").strip() or None
    if game_id and game_id not in [d["game_id"] for d in sesion["partidas"]]:
        return jsonify(error=f"La partida {game_id} no es de esa sesión."), 400
    try:
        lead = float(request.form.get("draft_lead_s") or processor.DRAFT_LEAD_S)
        mapa = int(request.form.get("mapa") or 0) or None
        post_s = float(request.form.get("post_s") or processor.POST_S)
    except ValueError:
        return jsonify(error="'draft_lead_s', 'post_s' y 'mapa' son números."), 400
    if not 0 <= lead <= 1800:
        return jsonify(error="'draft_lead_s' va de 0 a 1800 s."), 400
    if not 0 <= post_s <= 1800:
        return jsonify(error="'post_s' va de 0 a 1800 s."), 400
    language = (request.form.get("language") or "es").strip().lower()
    job_id = processor.new_transcript_job(phase1, sesion["params"].vod_url, game_id)
    threading.Thread(target=processor.transcript_job, daemon=True,
                     args=(job_id, phase1, game_id, language, lead, (request.form.get("carpeta_dia") or "").strip(),
                           (request.form.get("partido") or "").strip(), mapa, post_s,
                           (request.form.get("batched") or "").strip().lower() in ("1", "true", "on", "yes"))).start()
    return jsonify(job_id=job_id), 202


@bp.post("/edit/finish")
def edit_finish():
    """Resumes an edit paused with review=1 / storyboard=1: `segments` (JSON)
    are Ibai's segments of its status, with the text fixed (without them, as
    they are), and `kill_overrides` (JSON) the camera corrected per kill.
    The same call renders again an edit whose render failed (`retryable`)."""
    job_id = (request.form.get("job_id") or "").strip()
    if not job_manager.get_job(job_id):
        return jsonify(error="job_id no encontrado"), 404
    if not processor.edit_waiting(job_id):
        return jsonify(error="Ese edit no está esperando revisión ni se puede reintentar."), 409
    try:
        from modules.competitivo.schema import kill_overrides_from
        overrides = kill_overrides_from(request.form.get("kill_overrides"))
        processor.check_overrides(job_id, overrides)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
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
    threading.Thread(target=processor.edit_finish, args=(job_id, segments, overrides), daemon=True).start()
    return jsonify(job_id=job_id), 202


@bp.get("/storyboard/<job_id>")
@bp.get("/storyboard/<job_id>/<int:n>")
def storyboard(job_id, n=None):
    """The storyboard of an edit paused with storyboard=1 (the sheet, or one thumbnail)."""
    sb = (job_manager.get_job(job_id) or {}).get("storyboard") or {}
    name = "storyboard.jpg" if n is None else f"{n:02d}.jpg"
    path = os.path.join(sb.get("folder") or "", name)
    if not sb.get("folder") or not os.path.isfile(path):
        return jsonify(error="No hay storyboard para ese edit."), 404
    return send_file(path, mimetype="image/jpeg", max_age=0)


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
        storyboard={k: v for k, v in (job.get("storyboard") or {}).items() if k != "folder"} or None,
        retryable=bool(job.get("retryable")),
        transcript_job_id=job.get("transcript_job_id"), transcript_jobs=job.get("transcript_jobs"),
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
