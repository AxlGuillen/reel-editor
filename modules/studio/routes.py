"""Flask blueprint of the studio (/api/studio): the slides of a Presentations
deck over a base video, placed and seen live. It does not render anything:
the UI asks Presentations for the slides (POST /api/render there) and then
runs POST /api/montage/process with the specs this module returns.

    GET  /config                 → {presentations_url}
    POST /open                   video | library_clip | job_id → the session: {studio_id, name,
                                 duration, video_url, fingerprint, segments, montaje, folder}
    GET  /session/<studio_id>    → brings a session back after a restart (before transcribing
                                 or rendering: those modules take it as a job_id)
    GET  /video/<studio_id>      → the base video (with Range, for the <video>)
    GET  /script?desk=&deck=     → {script, marks, prompt}: the deck's marked script
                                 (video-out/laminas/guion-marcado.txt) and Whisper's prompt
    POST /segments   (JSON)      studio_id, segments → {words}; saved next to the montaje
    POST /place      (JSON)      studio_id, montaje, manifest, [script, auto, words]
                                 → {montaje, layers (resolved, with the preview geometry), specs,
                                 unplaced, source, alignment, [words]}
    POST /save       (JSON)      studio_id, montaje, [manifest] → {path}
"""
from __future__ import annotations

import glob
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, jsonify, request, send_file

import config
from core import ffmpeg_runner, file_utils, job_manager
from modules.library.routes import resolve_clip
from modules.studio import processor as P
from modules.studio import schema as S

bp = Blueprint("studio", __name__, url_prefix="/api/studio")

_SESSION_ID = re.compile(r"^[0-9a-f]{12}$")
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


# ------------------------------------------------------------------ sessions

def _folder_for(name: str, fp: str) -> str:
    """The persistent folder of a base video: found by its fingerprint (the
    same video under another name or job), or a new one named after it."""
    found = glob.glob(os.path.join(glob.escape(config.STUDIO_FOLDER), f"*· {fp[:10]}"))
    if found:
        return found[0]
    stem = _UNSAFE.sub("", os.path.splitext(name)[0]).strip(" .")[:60] or "video"
    return os.path.join(config.STUDIO_FOLDER, f"{stem} · {fp[:10]}")


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _session(studio_id) -> dict:
    """The session's job; after a server restart it comes back from
    cache/studio/<id>.json (the debug server reloads on every code change)."""
    studio_id = str(studio_id or "")
    if not _SESSION_ID.match(studio_id):
        raise ValueError("Sesión del estudio inválida.")
    job = job_manager.get_job(studio_id)
    if job and job.get("kind") == "studio":
        return job
    saved = _read_json(os.path.join(config.STUDIO_SESSIONS_FOLDER, f"{studio_id}.json"))
    if not saved or not os.path.isfile(saved.get("video_path") or ""):
        raise ValueError("La sesión del estudio ya no existe: volvé a abrir el video.")
    segments = _read_json(os.path.join(saved["folder"], "transcripcion.json"))
    job_manager.restore_job(studio_id, {"kind": "studio", "input_path": saved["video_path"],
                                        "output_path": saved["video_path"], **saved,
                                        "segments": segments})
    return job_manager.get_job(studio_id)


# -------------------------------------------------------------------- routes

@bp.get("/config")
def config_route():
    return jsonify(presentations_url=config.PRESENTATIONS_URL)


@bp.post("/open")
def open_route():
    """Opens a base video: an uploaded file, a library clip or a finished job."""
    saved_upload = None
    try:
        base: dict = {}
        segments = None
        if request.form.get("job_id"):
            src = job_manager.get_job(request.form["job_id"].strip())
            if not src:
                try:                         # a studio session from before a restart
                    src = _session(request.form["job_id"].strip())
                except ValueError:
                    src = None
            if not src or src["status"] != "done" or not os.path.isfile(src.get("output_path") or ""):
                raise ValueError("Ese job no terminó o ya no tiene su video.")
            path = src["output_path"]
            name = src.get("output_name") or src.get("name") or os.path.basename(path)
            base["job_id"] = request.form["job_id"].strip()
            segments = src.get("segments")
        elif request.form.get("library_clip"):
            path = resolve_clip(request.form["library_clip"])
            name = os.path.basename(path)
            base["library_clip"] = name
        else:
            video = request.files.get("video")
            if not video or not video.filename:
                raise ValueError("Falta el video base: 'video', 'library_clip' o 'job_id'.")
            if not config.allowed_file(video.filename):
                raise ValueError(f"Video: extensión no permitida. Usar: {sorted(config.ALLOWED_EXTENSIONS)}")
            path = saved_upload = file_utils.save_upload(video)
            name = os.path.basename(video.filename)
        duration = ffmpeg_runner.probe_duration(path)
        if not duration:
            raise ValueError("No se pudo leer la duración del video.")
    except ValueError as exc:
        file_utils.cleanup_paths(*([saved_upload] if saved_upload else []))
        return jsonify(error=str(exc)), 400

    fp = P.fingerprint(path)
    folder = _folder_for(name, fp)
    base.update(archivo=name, huella=fp, duracion=round(duration, 3))
    saved_segments = _read_json(os.path.join(folder, "transcripcion.json"))
    if saved_segments:
        segments = saved_segments            # the transcript the saved anchors were made on
    elif segments:
        _write_json(os.path.join(folder, "transcripcion.json"), segments)
    montaje = _read_json(os.path.join(folder, "montaje.json"))
    if montaje:
        montaje["base"] = dict(base)          # the video opened now, not the one it was saved with

    studio_id = job_manager.create_job(path, output_path=path)
    session = {"kind": "studio", "video_path": path, "name": name, "fingerprint": fp, "folder": folder,
               "duration": duration, "base": base}
    job_manager.update_job(studio_id, status="done", progress=100, segments=segments, **session)
    _write_json(os.path.join(config.STUDIO_SESSIONS_FOLDER, f"{studio_id}.json"), session)
    return jsonify(studio_id=studio_id, name=name, duration=duration, fingerprint=fp, base=base,
                   video_url=f"/api/studio/video/{studio_id}", folder=folder,
                   segments=segments, montaje=montaje)


@bp.get("/session/<studio_id>")
def session_route(studio_id):
    """Brings the session back after a restart: /api/subtitles/transcribe and
    /api/montage/process take it as a `job_id`, and they only look in memory."""
    try:
        job = _session(studio_id)
    except ValueError as exc:
        return jsonify(error=str(exc)), 404
    return jsonify(studio_id=job["job_id"], name=job["name"], duration=job["duration"])


@bp.get("/video/<studio_id>")
def video_route(studio_id):
    try:
        job = _session(studio_id)
    except ValueError as exc:
        return jsonify(error=str(exc)), 404
    return send_file(job["video_path"], conditional=True, max_age=0)


@bp.get("/script")
def script_route():
    """The deck's marked script, read from the Presentations server (the
    file is not in the API: it is a static file of the deck's folder)."""
    try:
        desk = S.valid_name(request.args.get("desk"), "Escritorio")
        deck = S.valid_name(request.args.get("deck"), "Deck")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    url = f"{config.PRESENTATIONS_URL}/{urllib.parse.quote(desk)}/{urllib.parse.quote(deck)}" \
          "/video-out/laminas/guion-marcado.txt"
    try:
        with urllib.request.urlopen(url, timeout=6) as res:
            text = res.read(S.MAX_SCRIPT_CHARS * 4).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return jsonify(script=None, marks=[], prompt=None)
        return jsonify(error=f"Presentations respondió {exc.code} al pedir el guion."), 502
    except (urllib.error.URLError, OSError):
        return jsonify(error=f"No contesta Presentations en {config.PRESENTATIONS_URL}: "
                             "corré node tools/servidor-local.mjs en ese repo."), 502
    _, marks = P.parse_script(text)
    return jsonify(script=text, marks=[m for m, _ in marks], prompt=P.whisper_prompt(text))


@bp.post("/segments")
def segments_route():
    data = request.get_json(silent=True) or {}
    try:
        job = _session(data.get("studio_id"))
        segments = S.validate_segments(data.get("segments"))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    job_manager.update_job(job["job_id"], segments=segments)
    _write_json(os.path.join(job["folder"], "transcripcion.json"), segments)
    return jsonify(words=P.word_list(segments))


@bp.post("/place")
def place_route():
    """Resolves the montaje (automatic placement if asked or if it is empty):
    the times come from montage's own resolver."""
    t0 = time.perf_counter()
    data = request.get_json(silent=True) or {}
    try:
        job = _session(data.get("studio_id"))
        manifest = S.validate_manifest(data.get("manifest"))
        ids = [m["id"] for m in manifest]
        montaje = data.get("montaje")
        if not isinstance(montaje, dict):
            raise ValueError("Falta el montaje.")
        montaje = {**montaje, "base": dict(job["base"])}
        segments = job.get("segments")
        auto = None
        if data.get("auto") or not montaje.get("capas"):
            script = data.get("script")
            if script is not None and (not isinstance(script, str) or len(script) > S.MAX_SCRIPT_CHARS):
                raise ValueError("El guion es demasiado largo.")
            if not segments:
                raise ValueError("Primero hace falta la transcripción del video.")
            auto = P.auto_layers(manifest, segments, script)
            montaje["capas"] = auto["capas"]
        montaje = S.validate_montaje(montaje, ids)
        specs = P.to_specs(montaje, manifest)
        needs_words = any("word" in s or "end_word" in s for s in specs)
        if needs_words and not segments:
            raise ValueError("Hay láminas ancladas a palabras y todavía no hay transcripción.")
        duration = float(job["duration"])
        crops = {m["id"]: m["recorte"] for m in manifest if m.get("recorte")}
        layers = P.resolve(specs, segments, duration, crops) if specs else []
        # The deck's rest slide fills the gaps (unless the montaje turns it off):
        # resolved again with it, so the times are the ones the render will use.
        rest = next((m for m in manifest if m["reposo"]), None)
        if rest and montaje.get("reposo", True) and specs:
            rest_box = P.layer_box({}, rest, montaje.get("caja"))
            specs = P.fill_rest(specs, layers, rest["id"], rest_box, duration)
            layers = P.resolve(specs, segments, duration, crops)
            for lay in layers:
                lay["rest"] = lay["id"] == rest["id"]
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    placed = {c["lamina"] for c in montaje["capas"]}
    return jsonify(montaje=montaje, specs=specs, layers=layers,
                   unplaced=[i for i in ids if i not in placed and not (rest and i == rest["id"])],
                   source=(auto or {}).get("source"), alignment=(auto or {}).get("alignment"),
                   words=P.word_list(segments) if data.get("words") else None,
                   ms=round((time.perf_counter() - t0) * 1000, 1))


@bp.post("/save")
def save_route():
    data = request.get_json(silent=True) or {}
    try:
        job = _session(data.get("studio_id"))
        ids = None
        if data.get("manifest") is not None:
            ids = [m["id"] for m in S.validate_manifest(data["manifest"])]
        montaje = data.get("montaje")
        if isinstance(montaje, dict):
            montaje = {**montaje, "base": dict(job["base"])}
        montaje = S.validate_montaje(montaje, ids)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    path = os.path.join(job["folder"], "montaje.json")
    _write_json(path, montaje)
    return jsonify(path=path)
