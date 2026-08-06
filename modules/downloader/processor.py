"""Lógica de descarga del módulo downloader (yt-dlp).

Descarga un asset desde un link (YouTube, TikTok, Instagram, etc.) como mp4
(video) o mp3 (audio), con la calidad elegida. Usa el FFmpeg ya configurado
para juntar streams / extraer audio, y reporta el progreso al job_manager.

YouTube exige un runtime de JavaScript para descifrar las URLs de los formatos;
sin él, yt-dlp avisa que la extracción está deprecada y faltan formatos. Solo
habilita "deno" por defecto, así que acá se habilitan también node/quickjs/bun
(basta con que uno esté instalado).
"""
import glob
import os
import shutil

import yt_dlp

import config
from core import file_utils, job_manager
from modules.downloader.schema import DownloaderParams

# Mapea las altruras de video a un selector de formato de yt-dlp.
_VIDEO_HEIGHT = {"1080": 1080, "720": 720, "480": 480}

# Runtimes de JS que yt-dlp sabe usar, por orden de preferencia suyo.
_JS_RUNTIMES = ("deno", "node", "quickjs", "bun")


def available_js_runtimes() -> list[str]:
    """Runtimes de JS instalados en el sistema (los que yt-dlp podría usar)."""
    return [r for r in _JS_RUNTIMES if shutil.which(r)]


def _format_selector(params: DownloaderParams) -> str:
    if params.format == "video":
        if params.quality == "max":
            return "bv*+ba/b"
        h = _VIDEO_HEIGHT[params.quality]
        return f"bv*[height<={h}]+ba/b[height<={h}]"
    # audio: el bestaudio; el postproc lo convierte a mp3
    return "ba/b"


def _make_progress_hook(job_id: str):
    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes")
            if total and done:
                # Reservamos el tramo final (95-100) para el postprocesado.
                pct = int((done / total) * 95)
                job_manager.set_progress(job_id, min(pct, 95))
        elif d.get("status") == "finished":
            job_manager.set_progress(job_id, 97)
    return hook


def _build_opts(job_id: str, params: DownloaderParams) -> dict:
    outtmpl = os.path.join(config.DOWNLOAD_FOLDER, f"{job_id}.%(ext)s")
    opts: dict = {
        "outtmpl": outtmpl,
        "format": _format_selector(params),
        "noplaylist": True,           # solo ese video, no la playlist
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [_make_progress_hook(job_id)],
        # Habilita todos los runtimes de JS conocidos: yt-dlp usa el de mayor
        # prioridad que encuentre instalado (por defecto solo probaría deno).
        "js_runtimes": {r: {} for r in _JS_RUNTIMES},
    }

    # Cookies del navegador: es lo único que destraba el "Sign in to confirm
    # you're not a bot" de YouTube. Opcional y explícito: las cookies se usan
    # solo para autenticar con el sitio, no salen de esta máquina.
    if params.cookies_browser:
        opts["cookiesfrombrowser"] = (params.cookies_browser,)

    # Que yt-dlp use nuestro FFmpeg/ffprobe (puede estar fuera del PATH).
    ffmpeg_dir = os.path.dirname(config.FFMPEG_PATH)
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir

    if params.format == "video":
        opts["merge_output_format"] = "mp4"
    else:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": params.quality,
        }]
    return opts


def _friendly_error(exc: Exception) -> str:
    msg = str(exc).lower()
    # El bot-check de YouTube se reporta como "sign in", pero no es contenido
    # privado: el video puede ser público y aun así pedir credenciales.
    if "not a bot" in msg or "confirm you" in msg:
        return ("YouTube está pidiendo verificar que no sos un bot (le pasa a "
                "videos públicos también). Activá «Usar cookies del navegador» "
                "en los ajustes y volvé a intentar.")
    if "429" in msg or "too many requests" in msg:
        return ("YouTube limitó las descargas desde esta conexión por hacer "
                "muchas seguidas. Esperá unos minutos y reintentá.")
    if "private" in msg or "login" in msg or "sign in" in msg or "cookies" in msg:
        return ("No se pudo descargar: el contenido es privado o requiere login. "
                "Probá con un link público, o activá «Usar cookies del navegador».")
    if "unavailable" in msg or "not available" in msg or "removed" in msg:
        return "El video no está disponible o fue eliminado."
    if "unsupported url" in msg or "no video" in msg:
        return "Ese link no es compatible o no contiene un video descargable."
    return f"No se pudo descargar: {exc}"


def process(job_id: str, params: DownloaderParams) -> None:
    """Ejecuta la descarga (bloqueante). Actualiza job_manager en cada paso."""
    try:
        file_utils.ensure_dirs()
        job_manager.update_job(job_id, status="processing", progress=0)

        opts = _build_opts(job_id, params)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(params.url, download=True)

        # El nombre final depende del formato/merge; lo ubicamos por job_id.
        matches = [p for p in glob.glob(os.path.join(config.DOWNLOAD_FOLDER, f"{job_id}.*"))
                   if not p.endswith(".part")]
        if not matches:
            raise RuntimeError("La descarga terminó pero no se encontró el archivo.")
        output_path = max(matches, key=os.path.getmtime)

        title = (info or {}).get("title") or "descarga"
        job_manager.update_job(
            job_id, status="done", progress=100,
            output_path=output_path, title=title,
        )
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=_friendly_error(exc))
