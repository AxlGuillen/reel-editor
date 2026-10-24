"""El VOD de Ibai (YouTube o Twitch): hora de inicio del directo y descarga de tramos.

Usa yt-dlp con la misma configuración que el Downloader (cookies.txt subido,
runtimes de JS, reintentos y clientes alternativos ante el 403 de YouTube).

Formato: los HLS que dejó el directo. A igual resolución tienen más bitrate
que los DASH que YouTube recodifica para el VOD (VP9 HLS ~9.4 Mbps contra AV1
DASH 2.3 Mbps), y bajan tramos sueltos rápido: 35 s en ~9 s. Con DASH, yt-dlp
se colgaba (FFmpeg lee GB antes de llegar al tramo). El corte de HLS cae
exacto al frame sin recodificar (medido contra frames con seek exacto), así
que no hace falta --force-keyframes-at-cuts.

Twitch: the VOD of the same stream exists (and grows) while it is still live,
and yt-dlp downloads the exact section from it (33 min in 53 s, 1080p60
H.264, the same layout). A YouTube stream that is still live does NOT work:
yt-dlp gets whatever is being broadcast now instead of the requested section.
"""
from __future__ import annotations

import glob
import os
import threading
import time
from datetime import datetime, timezone

import yt_dlp
from yt_dlp.utils import download_range_func

import config
from core import ffmpeg_runner
from modules.downloader import processor as downloader

FORMATO = ("bv*[protocol^=m3u8][height<=1080][fps>30]+ba[protocol^=m3u8]/"
           "bv*[protocol^=m3u8][height<=1080]+ba[protocol^=m3u8]/"
           "bv*[height<=1080]+ba/b")
# Twitch serves video and audio together (1080p60 HLS): no merge needed.
FORMAT_TWITCH = "b[height<=1080][fps>30]/b[height<=1080]/b"
# Twitch drops sections when too many go at once: at most 2 at a time (for
# the whole server, summaries and edits together), and a failed section is
# tried again twice.
TWITCH_SLOTS = threading.BoundedSemaphore(2)
TWITCH_RETRIES = 2
RETRY_WAIT_S = 3.0


def _opts(**extra) -> dict:
    opts = {
        "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
        "js_runtimes": {r: {} for r in ("deno", "node", "quickjs", "bun")},
    }
    if os.path.isfile(config.COOKIES_FILE):
        opts["cookiefile"] = config.COOKIES_FILE
    ffmpeg_dir = os.path.dirname(config.FFMPEG_PATH)
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir
    opts.update(extra)
    return opts


class SpanWithoutVideo(RuntimeError):
    """The downloaded section came without a video stream, twice."""


def platform_of(info: dict) -> str:
    """"youtube", "twitch" or yt-dlp's extractor name for anything else."""
    key = str(info.get("extractor_key") or info.get("extractor") or "").lower()
    for name in ("youtube", "twitch"):
        if key.startswith(name):
            return name
    return key or "otro"


def vod_info(info: dict) -> dict:
    """{inicio_utc, duracion, titulo, platform} from yt-dlp's info. Raises
    RuntimeError with a readable message if it cannot be used."""
    platform = platform_of(info)
    if platform == "youtube" and (info.get("live_status") == "is_live" or info.get("is_live")):
        raise RuntimeError("Ese directo de YouTube sigue en vivo: YouTube entrega lo que se transmite ahora, "
                           "no el tramo pedido. Usa el VOD de Twitch (twitch.tv/videos/…) o espera a que termine.")
    # YouTube: release_timestamp is when the stream started (timestamp is when
    # it was scheduled). Twitch only has timestamp: when the VOD started.
    ts = info.get("release_timestamp") or info.get("timestamp")
    if not ts:
        raise RuntimeError("Ese video no trae la hora de inicio del directo: ¿es el VOD de un directo?")
    return {
        "inicio_utc": datetime.fromtimestamp(ts, tz=timezone.utc),
        "duracion": float(info.get("duration") or 0),
        "titulo": info.get("title") or "",
        "platform": platform,
    }


def info_vod(url: str) -> dict:
    """{inicio_utc, duracion, titulo, platform} del VOD. Lanza RuntimeError con un
    mensaje legible si no se puede leer."""
    try:
        with yt_dlp.YoutubeDL(_opts(skip_download=True)) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 - mismo mensaje amigable que el Downloader
        raise RuntimeError(downloader._friendly_error(exc))
    return vod_info(info)


def is_twitch(url: str) -> bool:
    return "twitch.tv" in url.lower()


def bajar_tramo(url: str, inicio: float, fin: float, destino: str) -> str:
    """Baja [inicio, fin] (segundos del VOD) a `destino`.mp4 y devuelve la ruta.

    If the section comes without video (an audio-only file, or FFmpeg's
    "Output file does not contain any stream" while merging) it is downloaded
    once more; the second time it raises SpanWithoutVideo. Twitch: at most
    TWITCH_SLOTS at a time and TWITCH_RETRIES more tries if one fails."""
    if not is_twitch(url):
        return _span_with_video(url, inicio, fin, destino)
    with TWITCH_SLOTS:
        for attempt in range(TWITCH_RETRIES + 1):
            try:
                return _span_with_video(url, inicio, fin, destino)
            except SpanWithoutVideo:
                raise
            except RuntimeError as exc:
                if attempt == TWITCH_RETRIES:
                    raise
                print(f"[competitivo] Twitch: reintento {attempt + 1} del tramo {inicio:.0f}-{fin:.0f} s"
                      f" ({str(exc)[:80]})".encode("ascii", "replace").decode())
                time.sleep(RETRY_WAIT_S)


def _span_with_video(url: str, inicio: float, fin: float, destino: str) -> str:
    for attempt in (1, 2):
        path = _download_span(url, inicio, fin, destino)
        if path and ffmpeg_runner.probe_video_stream(path):
            return path
        print(f"[competitivo] tramo {inicio:.0f}-{fin:.0f} s sin video (intento {attempt})")
    for viejo in glob.glob(destino + ".*"):
        os.remove(viejo)
    raise SpanWithoutVideo(f"El tramo {inicio:.0f}–{fin:.0f} s del VOD bajó sin video (dos veces).")


def _no_stream(message: str) -> bool:
    return "does not contain any stream" in message.lower()


# Only the audio (a whole game's narration): Twitch's audio_only, YouTube's HLS audio.
FORMAT_AUDIO = "audio_only/ba[protocol^=m3u8]/ba/b"


def bajar_audio(url: str, inicio: float, fin: float, destino: str) -> str:
    """Only the audio of [inicio, fin] of the VOD (a game, from the draft to
    the nexus: ~45 min) to `destino`.<ext>; its path."""
    for viejo in glob.glob(destino + ".*"):
        os.remove(viejo)
    opts = _opts(format=FORMAT_AUDIO, outtmpl=destino + ".%(ext)s",
                 download_ranges=download_range_func(None, [(max(0.0, inicio), fin)]),
                 force_keyframes_at_cuts=False)
    slot = TWITCH_SLOTS if is_twitch(url) else None
    try:
        if slot:
            slot.acquire()
        downloader._download_with_retry(opts, url)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(downloader._friendly_error(exc))
    finally:
        if slot:
            slot.release()
    hechos = [p for p in glob.glob(destino + ".*") if not p.endswith(".part")]
    if not hechos:
        raise RuntimeError("yt-dlp terminó pero no dejó el audio descargado.")
    return max(hechos, key=os.path.getmtime)


def _download_span(url: str, inicio: float, fin: float, destino: str) -> str | None:
    """One download of the section: its path, or None if it came without streams."""
    for viejo in glob.glob(destino + ".*"):
        os.remove(viejo)
    opts = _opts(
        format=FORMAT_TWITCH if is_twitch(url) else FORMATO,
        outtmpl=destino + ".%(ext)s",
        download_ranges=download_range_func(None, [(max(0.0, inicio), fin)]),
        force_keyframes_at_cuts=False,
        merge_output_format="mp4",
    )
    try:
        downloader._download_with_retry(opts, url)
    except Exception as exc:  # noqa: BLE001
        if _no_stream(str(exc)):
            return None
        raise RuntimeError(downloader._friendly_error(exc))
    hechos = [p for p in glob.glob(destino + ".*") if not p.endswith(".part")]
    if not hechos:
        raise RuntimeError("yt-dlp terminó pero no dejó el tramo descargado.")
    return max(hechos, key=os.path.getmtime)
