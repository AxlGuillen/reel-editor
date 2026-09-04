"""Blueprint de la librería de clips: la carpeta local de capturas (Overwolf
Insights Capture por defecto) listada desde el servidor.

No procesa video: lista los clips (más nuevos primero), genera y cachea una
miniatura por clip, sirve el archivo para el preview (con Range, así el
<video> del browser no lo baja entero) y resuelve un nombre de la librería a
su path absoluto para que los módulos lo procesen IN SITU, sin upload.
"""
import hashlib
import os
import subprocess

from flask import Blueprint, jsonify, send_file

import config
from core import ffmpeg_runner

bp = Blueprint("library", __name__, url_prefix="/api/library")

# Cache en memoria de la duración (ffprobe) por (nombre, tamaño, mtime).
_DURATIONS: dict[tuple, float | None] = {}


def resolve_clip(name: str) -> str:
    """Path absoluto de un clip de la librería a partir de su nombre.

    Lanza ValueError (mensaje legible) si el nombre no es un archivo de video
    dentro de la carpeta configurada. `basename` neutraliza cualquier ../.
    """
    safe = os.path.basename(name or "")
    if not safe or not config.allowed_file(safe):
        raise ValueError("El clip elegido de la librería no es un video válido.")
    path = os.path.join(config.CLIPS_LIBRARY_FOLDER, safe)
    if not os.path.isfile(path):
        raise ValueError(
            f"El clip '{safe}' ya no está en la carpeta de capturas.")
    return path


def _clips() -> list[dict]:
    folder = config.CLIPS_LIBRARY_FOLDER
    if not os.path.isdir(folder):
        return []
    items = []
    for entry in os.scandir(folder):
        if not entry.is_file() or not config.allowed_file(entry.name):
            continue
        st = entry.stat()
        key = (entry.name, st.st_size, int(st.st_mtime))
        if key not in _DURATIONS:
            _DURATIONS[key] = ffmpeg_runner.probe_duration(entry.path)
        items.append({
            "name": entry.name,
            # Sin duración = ffprobe no lo pudo leer (p. ej. grabación cortada
            # sin índice moov): se lista como dañado, no elegible.
            "ok": _DURATIONS[key] is not None,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            "duration": _DURATIONS[key],
            "url": f"/api/library/clips/{entry.name}/file",
            "thumb": f"/api/library/clips/{entry.name}/thumb",
        })
    items.sort(key=lambda c: c["mtime"], reverse=True)
    return items


@bp.get("/clips")
def list_clips():
    folder = config.CLIPS_LIBRARY_FOLDER
    return jsonify(folder=folder, available=os.path.isdir(folder), clips=_clips())


@bp.get("/clips/<name>/file")
def serve_clip(name):
    try:
        path = resolve_clip(name)
    except ValueError as exc:
        return jsonify(error=str(exc)), 404
    # conditional=True habilita Range: el <video> del preview pide trozos.
    return send_file(path, mimetype="video/mp4", conditional=True)


@bp.get("/clips/<name>/thumb")
def clip_thumb(name):
    try:
        path = resolve_clip(name)
    except ValueError as exc:
        return jsonify(error=str(exc)), 404
    st = os.stat(path)
    key = (os.path.basename(path), st.st_size, int(st.st_mtime))
    if key not in _DURATIONS:
        _DURATIONS[key] = ffmpeg_runner.probe_duration(path)
    duration = _DURATIONS[key]
    if duration is None:
        return jsonify(error="El archivo está dañado o incompleto."), 422
    # Las partidas completas arrancan en pantalla de carga: para clips largos
    # se toma el frame al 25 % de la duración; para highlights, a los 3 s.
    seek = 3.0 if duration <= 300 else duration * 0.25
    digest = hashlib.sha1(
        f"{path}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8")).hexdigest()
    thumb = os.path.join(config.THUMBS_FOLDER, f"{digest}.jpg")
    if not os.path.isfile(thumb):
        os.makedirs(config.THUMBS_FOLDER, exist_ok=True)
        # Un frame en `seek` (o el primero si falla), 320 px de ancho.
        cmd = [config.FFMPEG_PATH, "-y", "-v", "error", "-ss", f"{seek:.1f}", "-i", path,
               "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "4", thumb]
        subprocess.run(cmd, capture_output=True)
        if not os.path.isfile(thumb):
            cmd[cmd.index("-ss") + 1] = "0"
            subprocess.run(cmd, capture_output=True)
        if not os.path.isfile(thumb):
            return jsonify(error="No se pudo generar la miniatura."), 500
    return send_file(thumb, mimetype="image/jpeg", max_age=3600)
