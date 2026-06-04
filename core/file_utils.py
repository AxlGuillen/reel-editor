"""Helpers para manejo de archivos temporales."""
import os
import uuid

from werkzeug.utils import secure_filename

import config


def ensure_dirs() -> None:
    """Crea las carpetas temporales (uploads, outputs, downloads) si no existen."""
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(config.OUTPUT_FOLDER, exist_ok=True)
    os.makedirs(config.DOWNLOAD_FOLDER, exist_ok=True)


def save_upload(file_storage) -> str:
    """Guarda un archivo subido con nombre único y retorna su path absoluto."""
    ensure_dirs()
    original = secure_filename(file_storage.filename) or "input"
    ext = original.rsplit(".", 1)[1].lower() if "." in original else "mp4"
    unique = f"{uuid.uuid4().hex[:12]}.{ext}"
    path = os.path.join(config.UPLOAD_FOLDER, unique)
    file_storage.save(path)
    return path


def output_path_for(job_id: str) -> str:
    """Path de salida (mp4) para un job dado."""
    ensure_dirs()
    return os.path.join(config.OUTPUT_FOLDER, f"{job_id}.mp4")


def cleanup_paths(*paths: str) -> None:
    """Borra archivos temporales sin lanzar si no existen."""
    for path in paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
