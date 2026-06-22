"""Configuración global de ReelForge."""
import os
import re
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "outputs")
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, "downloads")

# Assets persistentes (versionados, NO temporales): librería de watermarks y
# la fuente para los textos. El botón "Limpiar archivos" no los toca.
ASSETS_FOLDER = os.path.join(BASE_DIR, "assets")
WATERMARKS_FOLDER = os.path.join(ASSETS_FOLDER, "watermarks")
FONTS_FOLDER = os.path.join(ASSETS_FOLDER, "fonts")

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


def _resolve_ffprobe() -> str:
    """Deriva el path de ffprobe del de ffmpeg.

    No basta con FFMPEG_PATH.replace("ffmpeg", "ffprobe"): el path suele
    contener "ffmpeg" también en el nombre de la carpeta (p. ej.
    "ffmpeg-8.1.1-full_build"), y reemplazar todo arma una ruta inexistente.
    Sólo tocamos el nombre del archivo, de forma case-insensitive.
    """
    found = shutil.which("ffprobe")
    if found:
        return found
    folder, name = os.path.split(FFMPEG_PATH)
    probe_name = re.sub(r"ffmpeg", "ffprobe", name, flags=re.IGNORECASE)
    candidate = os.path.join(folder, probe_name)
    if os.path.isfile(candidate):
        return candidate
    return "ffprobe"


FFPROBE_PATH = os.environ.get("FFPROBE_PATH") or _resolve_ffprobe()

ALLOWED_EXTENSIONS = {"mp4", "mov", "mkv", "avi"}
# Fuentes de audio para sound_drop: archivos de audio o videos (se extrae su pista).
ALLOWED_AUDIO_EXTENSIONS = {"mp3", "wav", "m4a", "aac", "ogg"} | ALLOWED_EXTENSIONS
# Watermarks: PNG con transparencia. Fuentes: solo las que soporta freetype.
ALLOWED_WATERMARK_EXTENSIONS = {"png"}
ALLOWED_FONT_EXTENSIONS = {"ttf", "otf"}

# Lienzo vertical de salida (9:16)
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920


def _has_allowed_ext(filename: str, allowed: set[str]) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed


def allowed_file(filename: str) -> bool:
    return _has_allowed_ext(filename, ALLOWED_EXTENSIONS)


def allowed_audio_file(filename: str) -> bool:
    return _has_allowed_ext(filename, ALLOWED_AUDIO_EXTENSIONS)


def allowed_watermark_file(filename: str) -> bool:
    return _has_allowed_ext(filename, ALLOWED_WATERMARK_EXTENSIONS)


def allowed_font_file(filename: str) -> bool:
    return _has_allowed_ext(filename, ALLOWED_FONT_EXTENSIONS)


def resolve_font_path() -> str | None:
    """Devuelve el path de la fuente fija del proyecto (el primer .ttf/.otf en
    assets/fonts/), o None si no hay ninguna."""
    if not os.path.isdir(FONTS_FOLDER):
        return None
    for name in sorted(os.listdir(FONTS_FOLDER)):
        if allowed_font_file(name):
            return os.path.join(FONTS_FOLDER, name)
    return None
