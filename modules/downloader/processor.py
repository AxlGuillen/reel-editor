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
import re
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


# ── cookies.txt subido (formato Netscape) ────────────────────────────────
# En Windows, Chrome/Edge/Brave cifran sus cookies (App-Bound Encryption) y
# yt-dlp no puede leerlas del navegador. La alternativa: el usuario exporta un
# cookies.txt con una extensión y lo sube; acá se guarda y se pasa a yt-dlp.

MAX_COOKIES_SIZE = 2 * 1024 * 1024   # 2 MB: un cookies.txt real pesa unos KB


def cookies_file_status() -> dict:
    """Estado del cookies.txt guardado: {exists, mtime, size}."""
    path = config.COOKIES_FILE
    if not os.path.isfile(path):
        return {"exists": False}
    st = os.stat(path)
    return {"exists": True, "mtime": int(st.st_mtime), "size": st.st_size}


def save_cookies_file(raw: bytes) -> None:
    """Valida, FILTRA y guarda el cookies.txt. Lanza ValueError si no es válido.

    Solo se conservan las cookies de YouTube/Google: son las que resuelven el
    bot-check. Las de otros sitios se descartan a propósito — un export "de
    todos los sitios" trae cookies parciales/viejas de TikTok o Instagram que
    hacen que esos sitios respondan 403 a descargas que sin cookies funcionan.
    """
    if len(raw) > MAX_COOKIES_SIZE:
        raise ValueError("El archivo es demasiado grande para ser un cookies.txt.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("El archivo no es texto. Exportalo en formato Netscape "
                         "(extensión «Get cookies.txt LOCALLY» o similar).")

    filas = [
        line for line in text.splitlines()
        if not line.startswith("#") and len(line.split("\t")) == 7
    ]
    if not filas:
        raise ValueError(
            "Eso no parece un cookies.txt en formato Netscape. Exportalo con "
            "una extensión como «Get cookies.txt LOCALLY» y subí ese archivo."
        )

    utiles = [f for f in filas
              if "youtube" in f.split("\t")[0] or "google" in f.split("\t")[0]]
    if not utiles:
        raise ValueError(
            "El archivo no trae cookies de YouTube. Exportalo estando en "
            "youtube.com con la sesión iniciada."
        )

    os.makedirs(config.COOKIES_FOLDER, exist_ok=True)
    contenido = "# Netscape HTTP Cookie File\n" + "\n".join(utiles) + "\n"
    with open(config.COOKIES_FILE, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(contenido)


def delete_cookies_file() -> bool:
    """Borra el cookies.txt guardado. Devuelve si existía."""
    if os.path.isfile(config.COOKIES_FILE):
        os.remove(config.COOKIES_FILE)
        return True
    return False


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

    # Cookies: es lo único que destraba el "Sign in to confirm you're not a
    # bot" de YouTube. Opcional y explícito: se usan solo para autenticar con
    # el sitio, no salen de esta máquina. "file" = cookies.txt subido (la vía
    # que funciona con Chrome en Windows); un navegador = leerlas de ahí.
    if params.cookies_browser == "file":
        if not os.path.isfile(config.COOKIES_FILE):
            raise ValueError(
                "No hay ningún cookies.txt cargado. Subilo primero con el "
                "botón «Subir cookies.txt» del Downloader."
            )
        opts["cookiefile"] = config.COOKIES_FILE
    elif params.cookies_browser:
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


# Secuencias de color ANSI que yt-dlp mete en sus mensajes de error y que en
# la UI se ven como basura ("?[0;31mERROR:?[0m …").
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Señales de fallo TRANSITORIO: TikTok (y a veces otros) rechazan por rachas
# cortas cuando ven varias peticiones seguidas; el mismo request funciona
# segundos después. Verificado empíricamente: 403 en 3 intentos seguidos y OK
# al siguiente, con opciones idénticas.
_TRANSIENT = ("403", "forbidden", "unexpected response", "timed out", "timeout")
# 25s: TikTok limita por ráfaga; con esperas de 6s el reintento caía dentro
# de la misma ventana de bloqueo y fallaba igual (verificado).
_RETRY_WAIT_S = 25
_MAX_TRIES = 3


def _download_with_retry(opts: dict, url: str):
    """extract_info(download=True) con reintentos ante fallos transitorios."""
    import time
    for intento in range(1, _MAX_TRIES + 1):
        try:
            with yt_dlp.YoutubeDL(dict(opts)) as ydl:
                return ydl.extract_info(url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            msg = str(exc).lower()
            es_transitorio = any(t in msg for t in _TRANSIENT)
            # El bot-check también dice 403 a veces, pero trae su propia frase:
            # no lo reintentamos (necesita cookies, no paciencia).
            if "not a bot" in msg or "confirm you" in msg:
                raise
            if intento < _MAX_TRIES and es_transitorio:
                time.sleep(_RETRY_WAIT_S)
                continue
            raise


def _friendly_error(exc: Exception) -> str:
    # Los ValueError los generamos nosotros con mensajes ya amigables
    # (p. ej. "No hay ningún cookies.txt cargado"): pasan tal cual.
    if isinstance(exc, ValueError):
        return str(exc)
    msg = _ANSI_RE.sub("", str(exc)).lower()
    # Chrome (y derivados) en Windows cifran las cookies con App-Bound
    # Encryption: yt-dlp no puede leerlas del navegador. La salida es el
    # cookies.txt exportado.
    if "could not copy" in msg and "cookie" in msg:
        return ("No se pudieron leer las cookies del navegador: Chrome/Edge las "
                "cifran en Windows y bloquean el acceso. Usá la opción "
                "«Archivo cookies.txt»: exportá las cookies con una extensión "
                "(«Get cookies.txt LOCALLY») y subí el archivo.")
    # El bot-check de YouTube se reporta como "sign in", pero no es contenido
    # privado: el video puede ser público y aun así pedir credenciales.
    if "not a bot" in msg or "confirm you" in msg:
        return ("YouTube está pidiendo verificar que no sos un bot (le pasa a "
                "videos públicos también). Elegí una fuente en «Cookies» — en "
                "Windows lo que funciona es «Archivo cookies.txt» — y reintentá.")
    if "429" in msg or "too many requests" in msg:
        return ("YouTube limitó las descargas desde esta conexión por hacer "
                "muchas seguidas. Esperá unos minutos y reintentá.")
    if "403" in msg or "forbidden" in msg:
        return ("El sitio rechazó la petición (403). Suele ser un bloqueo "
                "temporal por varias descargas seguidas: esperá un minuto y "
                "reintentá.")
    if "private" in msg or "login" in msg or "sign in" in msg or "cookies" in msg:
        return ("No se pudo descargar: el contenido es privado o requiere login. "
                "Probá con un link público, o configurá «Cookies».")
    if "unavailable" in msg or "not available" in msg or "removed" in msg:
        return "El video no está disponible o fue eliminado."
    if "unsupported url" in msg or "no video" in msg:
        return "Ese link no es compatible o no contiene un video descargable."
    return f"No se pudo descargar: {_ANSI_RE.sub('', str(exc))}"


def process(job_id: str, params: DownloaderParams) -> None:
    """Ejecuta la descarga (bloqueante). Actualiza job_manager en cada paso."""
    try:
        file_utils.ensure_dirs()
        job_manager.update_job(job_id, status="processing", progress=0)

        opts = _build_opts(job_id, params)
        info = _download_with_retry(opts, params.url)

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
