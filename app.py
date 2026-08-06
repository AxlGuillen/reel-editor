"""Entry point Flask de ReelForge. Registra blueprints de cada módulo."""
import importlib.metadata
import subprocess
import sys
import traceback

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

import config
from core import file_utils
from modules.vertical_convert.routes import bp as vertical_convert_bp
from modules.sound_drop.routes import bp as sound_drop_bp
from modules.insert.routes import bp as insert_bp
from modules.downloader.routes import bp as downloader_bp
from modules.audio_merge.routes import bp as audio_merge_bp
from modules.reel_express.routes import bp as reel_express_bp
from modules.hook_reel.routes import bp as hook_reel_bp
from modules.assets.routes import bp as assets_bp
from modules.watermark.routes import bp as watermark_bp
from modules.subtitles.routes import bp as subtitles_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH
    # Herramienta local en desarrollo: no cachear estáticos, así el navegador
    # siempre toma el JS/CSS recién editado (evita ver versiones viejas).
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    file_utils.ensure_dirs()

    # Registro de módulos
    app.register_blueprint(vertical_convert_bp)
    app.register_blueprint(sound_drop_bp)
    app.register_blueprint(insert_bp)
    app.register_blueprint(downloader_bp)
    app.register_blueprint(audio_merge_bp)
    app.register_blueprint(reel_express_bp)
    app.register_blueprint(hook_reel_bp)
    app.register_blueprint(assets_bp)
    app.register_blueprint(watermark_bp)
    app.register_blueprint(subtitles_bp)

    @app.route("/")
    def index():
        return render_template("index.html")

    def _js_runtime_info():
        """YouTube necesita un runtime de JS; si falta, la descarga degrada."""
        from modules.downloader.processor import available_js_runtimes
        encontrados = available_js_runtimes()
        return ", ".join(encontrados) if encontrados else "ninguno (YouTube fallará)"

    @app.get("/api/info")
    def info():
        """Devuelve versiones de dependencias clave del sistema."""
        def pkg(name):
            try:
                return importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                return "no instalado"

        def bin_version(path):
            try:
                out = subprocess.run(
                    [path, "-version"], capture_output=True, text=True, timeout=5
                )
                line = (out.stdout or out.stderr or "").splitlines()[0]
                # "ffmpeg version 7.1 ..." → "7.1"
                parts = line.split("version")
                return parts[1].strip().split()[0] if len(parts) > 1 else line
            except Exception:
                return "no encontrado"

        return jsonify(
            python=sys.version.split()[0],
            flask=pkg("flask"),
            yt_dlp=pkg("yt-dlp"),
            faster_whisper=pkg("faster-whisper"),
            ffmpeg=bin_version(config.FFMPEG_PATH),
            ffprobe=bin_version(config.FFPROBE_PATH),
            js_runtime=_js_runtime_info(),
        )

    @app.post("/api/cleanup")
    def cleanup():
        """Vacía las carpetas temporales (uploads, outputs, downloads)."""
        stats = file_utils.clear_temp_dirs()
        return jsonify(stats)

    # --- Manejadores de error: la API SIEMPRE responde JSON, nunca HTML ---
    # Sin esto, un 500/413 devuelve la página HTML de error de Flask y el
    # frontend falla al parsear ("Unexpected token '<'"), ocultando la causa.

    def _is_api() -> bool:
        return request.path.startswith("/api/")

    @app.errorhandler(RequestEntityTooLarge)
    def _too_large(exc):
        limit = config.MAX_UPLOAD_SIZE_MB
        # En reel_express lo único que se sube es el video (el audio es un link).
        que = "El video" if "/reel-express" in request.path else "El archivo"
        msg = (f"{que} que subiste supera el límite de {limit} MB. "
               f"Probá con un clip más corto (recortá la parte que vas a usar).")
        if _is_api():
            return jsonify(error=msg), 413
        return msg, 413

    @app.errorhandler(HTTPException)
    def _http_error(exc):
        if _is_api():
            return jsonify(error=exc.description or exc.name), exc.code
        return exc

    @app.errorhandler(Exception)
    def _unhandled(exc):
        # Log completo en la consola del server; mensaje claro al cliente.
        traceback.print_exc()
        if _is_api():
            return jsonify(error=f"Error interno del servidor: {exc}"), 500
        raise exc

    return app


app = create_app()


if __name__ == "__main__":
    # Herramienta local: no exponer en red pública.
    app.run(host="127.0.0.1", port=5001, debug=True)
