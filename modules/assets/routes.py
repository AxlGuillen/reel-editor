"""Blueprint de gestión de assets persistentes: librería de watermarks y la
fuente para los textos.

Los watermarks viven en assets/watermarks/ (versionados). Se suben/borran desde
la app y se sirven para mostrarlos en la galería y dibujarlos en el preview.
La fuente fija vive en assets/fonts/ y se sirve para el @font-face del preview.
"""
import os

from flask import Blueprint, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename

import config
from core import file_utils

bp = Blueprint("assets", __name__)


def _list_watermarks() -> list[dict]:
    folder = config.WATERMARKS_FOLDER
    if not os.path.isdir(folder):
        return []
    items = []
    for name in sorted(os.listdir(folder)):
        if config.allowed_watermark_file(name):
            items.append({"name": name, "url": f"/assets/watermarks/{name}"})
    return items


@bp.get("/api/watermarks")
def list_watermarks():
    return jsonify(watermarks=_list_watermarks())


@bp.post("/api/watermarks")
def upload_watermark():
    if "file" not in request.files:
        return jsonify(error="Falta el archivo 'file'"), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify(error="Nombre de archivo vacío"), 400
    if not config.allowed_watermark_file(file.filename):
        return jsonify(error="El watermark debe ser un PNG (con transparencia)."), 400

    file_utils.ensure_dirs()
    name = secure_filename(file.filename)
    if not name:
        return jsonify(error="Nombre de archivo inválido"), 400
    file.save(os.path.join(config.WATERMARKS_FOLDER, name))
    return jsonify(name=name, url=f"/assets/watermarks/{name}"), 201


@bp.delete("/api/watermarks/<name>")
def delete_watermark(name):
    # basename neutraliza cualquier componente de ruta (../) sin alterar el
    # nombre real: secure_filename rompía nombres con espacios/acentos.
    safe = os.path.basename(name)
    path = os.path.join(config.WATERMARKS_FOLDER, safe)
    if not os.path.isfile(path):
        return jsonify(error="Watermark no encontrado"), 404
    os.remove(path)
    return jsonify(ok=True)


@bp.get("/assets/watermarks/<name>")
def serve_watermark(name):
    # send_from_directory ya protege contra path traversal; pasamos el nombre
    # real (con espacios/acentos) en vez de secure_filename, que lo mangleaba.
    return send_from_directory(config.WATERMARKS_FOLDER, os.path.basename(name))


@bp.get("/api/font")
def font_info():
    """Informa si hay una fuente cargada (para que la UI avise si falta)."""
    path = config.resolve_font_path()
    return jsonify(available=path is not None,
                   name=os.path.basename(path) if path else None)


@bp.get("/assets/font")
def serve_font():
    """Sirve la fuente fija para el @font-face del preview."""
    path = config.resolve_font_path()
    if not path:
        return jsonify(error="No hay fuente en assets/fonts/"), 404
    return send_from_directory(config.FONTS_FOLDER, os.path.basename(path))
