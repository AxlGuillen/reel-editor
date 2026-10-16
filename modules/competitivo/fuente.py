"""El VOD de Ibai en YouTube: hora de inicio del directo y descarga de tramos.

Usa yt-dlp con la misma configuración que el Downloader (cookies.txt subido,
runtimes de JS, reintentos y clientes alternativos ante el 403 de YouTube).

Formato: los HLS que dejó el directo. A igual resolución tienen más bitrate
que los DASH que YouTube recodifica para el VOD (VP9 HLS ~9.4 Mbps contra AV1
DASH 2.3 Mbps), y bajan tramos sueltos rápido: 35 s en ~9 s. Con DASH, yt-dlp
se colgaba (FFmpeg lee GB antes de llegar al tramo). El corte de HLS cae
exacto al frame sin recodificar (medido contra frames con seek exacto), así
que no hace falta --force-keyframes-at-cuts.
"""
from __future__ import annotations

import glob
import os
from datetime import datetime, timezone

import yt_dlp
from yt_dlp.utils import download_range_func

import config
from modules.downloader import processor as downloader

FORMATO = ("bv*[protocol^=m3u8][height<=1080][fps>30]+ba[protocol^=m3u8]/"
           "bv*[protocol^=m3u8][height<=1080]+ba[protocol^=m3u8]/"
           "bv*[height<=1080]+ba/b")


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


def info_vod(url: str) -> dict:
    """{inicio_utc, duracion, titulo} del VOD. Lanza RuntimeError con un
    mensaje legible si no se puede leer."""
    try:
        with yt_dlp.YoutubeDL(_opts(skip_download=True)) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # noqa: BLE001 - mismo mensaje amigable que el Downloader
        raise RuntimeError(downloader._friendly_error(exc))
    ts = info.get("release_timestamp")
    if not ts:
        raise RuntimeError("YouTube no da la hora de inicio de ese video: ¿es el VOD de un directo?")
    return {
        "inicio_utc": datetime.fromtimestamp(ts, tz=timezone.utc),
        "duracion": float(info.get("duration") or 0),
        "titulo": info.get("title") or "",
    }


def bajar_tramo(url: str, inicio: float, fin: float, destino: str) -> str:
    """Baja [inicio, fin] (segundos del VOD) a `destino`.mp4 y devuelve la ruta."""
    for viejo in glob.glob(destino + ".*"):
        os.remove(viejo)
    opts = _opts(
        format=FORMATO,
        outtmpl=destino + ".%(ext)s",
        download_ranges=download_range_func(None, [(max(0.0, inicio), fin)]),
        force_keyframes_at_cuts=False,
        merge_output_format="mp4",
    )
    try:
        downloader._download_with_retry(opts, url)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(downloader._friendly_error(exc))
    hechos = [p for p in glob.glob(destino + ".*") if not p.endswith(".part")]
    if not hechos:
        raise RuntimeError("yt-dlp terminó pero no dejó el tramo descargado.")
    return max(hechos, key=os.path.getmtime)
