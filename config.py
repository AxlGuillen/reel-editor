"""Configuración global de ReelForge."""
import os
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "outputs")

MAX_UPLOAD_SIZE_MB = 2000
MAX_CONTENT_LENGTH = MAX_UPLOAD_SIZE_MB * 1024 * 1024


def _resolve_ffmpeg() -> str:
    """Encuentra el binario de ffmpeg de forma robusta.

    El PATH que ve el proceso de Python puede no incluir la carpeta donde
    vive ffmpeg (p. ej. ~/bin). Buscamos primero en el PATH y, si no está,
    en las ubicaciones típicas. Si no se encuentra, devolvemos "ffmpeg" y el
    error saltará al ejecutar (más claro que fallar al importar).
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidates = [
        os.path.expanduser("~/bin/ffmpeg"),
        "/opt/homebrew/bin/ffmpeg",  # Apple Silicon (Homebrew)
        "/usr/local/bin/ffmpeg",     # Intel Mac (Homebrew)
        "/usr/bin/ffmpeg",
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return "ffmpeg"


# Se puede sobreescribir con la variable de entorno FFMPEG_PATH.
# En Windows suele ser algo como "C:/ffmpeg/bin/ffmpeg.exe".
FFMPEG_PATH = os.environ.get("FFMPEG_PATH") or _resolve_ffmpeg()

ALLOWED_EXTENSIONS = {"mp4", "mov", "mkv", "avi"}

# Lienzo vertical de salida (9:16)
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
