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

import contextlib
import glob
import os
import threading
import time
from datetime import datetime, timezone

import yt_dlp
from yt_dlp.utils import download_range_func

import config
from core import ffmpeg_runner, scheduler
from modules.downloader import processor as downloader

FORMATO = ("bv*[protocol^=m3u8][height<=1080][fps>30]+ba[protocol^=m3u8]/"
           "bv*[protocol^=m3u8][height<=1080]+ba[protocol^=m3u8]/"
           "bv*[height<=1080]+ba/b")
# Twitch serves video and audio together (1080p60 HLS): no merge needed.
FORMAT_TWITCH = "b[height<=1080][fps>30]/b[height<=1080]/b"
# Twitch drops sections when too many go at once: the downloads share the
# "twitch" slots of core/scheduler.py (config.SLOTS: 4 for the whole server,
# summaries, edits and transcriptions together) and a failed section is tried
# again twice.
TWITCH_RETRIES = 2
RETRY_WAIT_S = 3.0
# A long audio (a whole game: ~60 min) comes down by its HLS segments, many at a
# time, instead of one after the other as yt-dlp's FFmpeg does (concurrent
# fragments of yt-dlp do nothing with download_ranges: measured 16.0 s with 1, 8 or
# 16 for 10 min of audio).
SEGMENT_THREADS = 8
SEGMENT_RETRIES = 3
BY_SEGMENTS_MIN_S = 300         # shorter than this: the yt-dlp way


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


def vod_key(url: str) -> str:
    """The VOD a link is, to key what was measured on it: twitch.tv/videos/<id> →
    "twitch-<id>"; any other link as it is (YouTube's watch?v=<id>)."""
    import re
    m = re.search(r"twitch\.tv/videos/(\d+)", url) or re.search(r"[?&]v=([\w-]{6,})", url)         or re.search(r"youtu\.be/([\w-]{6,})", url)
    if m:
        return ("twitch-" if "twitch" in url else "youtube-") + m.group(1)
    return re.sub(r"[^\w.-]+", "_", url)[-80:]


def is_twitch(url: str) -> bool:
    return "twitch.tv" in url.lower()


def bajar_tramo(url: str, inicio: float, fin: float, destino: str, job_id: str | None = None) -> str:
    """Baja [inicio, fin] (segundos del VOD) a `destino`.mp4 y devuelve la ruta.

    If the section comes without video (an audio-only file, or FFmpeg's
    "Output file does not contain any stream" while merging) it is downloaded
    once more; the second time it raises SpanWithoutVideo. Twitch: at most
    TWITCH_SLOTS at a time and TWITCH_RETRIES more tries if one fails."""
    if not is_twitch(url):
        return _span_with_video(url, inicio, fin, destino)
    with scheduler.slot("twitch", job_id):
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


def _audio_segments(url: str) -> list[tuple[str, float, float]]:
    """[(segment url, start s, duration s)] of the audio-only rendition of a Twitch VOD
    (its HLS playlist: the EXTINF of each segment add up to the VOD's time)."""
    import re
    import urllib.parse
    import urllib.request
    with yt_dlp.YoutubeDL(_opts(skip_download=True)) as ydl:
        info = ydl.extract_info(url, download=False)
    fmt = next((f for f in info.get("formats", []) if str(f.get("format_id", "")).lower().startswith("audio_only")), None)
    if not fmt or not str(fmt.get("protocol", "")).startswith("m3u8"):
        raise RuntimeError("el VOD no trae el audio solo en HLS")
    with urllib.request.urlopen(fmt["url"], timeout=30) as resp:
        text = resp.read().decode("utf-8", "replace")
    out, t, dur = [], 0.0, None
    for line in text.splitlines():
        m = re.match(r"#EXTINF:([\d.]+)", line)
        if m:
            dur = float(m.group(1))
        elif line and not line.startswith("#") and dur is not None:
            out.append((urllib.parse.urljoin(fmt["url"], line), t, dur))
            t += dur
            dur = None
    if not out:
        raise RuntimeError("la lista de segmentos del audio está vacía")
    return out


def _get_segment(seg_url: str, path: str) -> None:
    import urllib.request
    last = None
    for attempt in range(SEGMENT_RETRIES):
        try:
            with urllib.request.urlopen(seg_url, timeout=30) as resp, open(path, "wb") as fh:
                data = resp.read()
                if not data:
                    raise OSError("segmento vacío")
                fh.write(data)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"no se pudo bajar un segmento del audio: {last}")


def _audio_by_segments(url: str, inicio: float, fin: float, destino: str, job_id: str | None) -> str:
    """The audio of [inicio, fin] from the VOD's HLS segments, SEGMENT_THREADS at a
    time, cut by the playlist's own times (exact: it does not depend on the
    segments falling where the range starts)."""
    import shutil
    from concurrent.futures import ThreadPoolExecutor
    with scheduler.slot("twitch", job_id):
        segments = _audio_segments(url)
        chosen = [s for s in segments if s[1] + s[2] > inicio and s[1] < fin]
        if not chosen:
            raise RuntimeError("ese tramo no está en el VOD (¿todavía no se grabó?)")
        parts = destino + ".parts"
        shutil.rmtree(parts, ignore_errors=True)
        os.makedirs(parts)
        try:
            paths = [os.path.join(parts, f"{k:05d}.ts") for k in range(len(chosen))]
            with ThreadPoolExecutor(max_workers=SEGMENT_THREADS) as ex:
                list(ex.map(lambda sp: _get_segment(sp[0][0], sp[1]), zip(chosen, paths)))
            joined = destino + ".all.ts"
            with open(joined, "wb") as out:           # MPEG-TS segments simply follow each other
                for p in paths:
                    with open(p, "rb") as part:
                        shutil.copyfileobj(part, out)
        finally:
            shutil.rmtree(parts, ignore_errors=True)
    end = min(fin, chosen[-1][1] + chosen[-1][2])
    result = destino + ".m4a"
    try:
        ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-y", "-ss", f"{max(0.0, inicio - chosen[0][1]):.3f}",
                               "-t", f"{end - max(inicio, chosen[0][1]):.3f}", "-i", joined, "-vn", "-c:a", "copy",
                               result])
    finally:
        with contextlib.suppress(OSError):
            os.remove(joined)
    return result


def bajar_audio(url: str, inicio: float, fin: float, destino: str, job_id: str | None = None) -> str:
    """Only the audio of [inicio, fin] of the VOD (a game, from the draft to
    the nexus: ~45 min) to `destino`.<ext>; its path. A Twitch audio of 5+ min
    comes down by its segments, many at once; if that fails, the yt-dlp way."""
    for viejo in glob.glob(destino + ".*"):
        os.remove(viejo)
    if is_twitch(url) and fin - inicio >= BY_SEGMENTS_MIN_S:
        try:
            return _audio_by_segments(url, inicio, fin, destino, job_id)
        except Exception as exc:  # noqa: BLE001 - the audio matters more than how it comes
            print(f"[competitivo] audio por segmentos falló ({str(exc)[:80]}): por yt-dlp"
                  .encode("ascii", "replace").decode())
            for viejo in glob.glob(destino + ".*"):
                os.remove(viejo)
    opts = _opts(format=FORMAT_AUDIO, outtmpl=destino + ".%(ext)s",
                 download_ranges=download_range_func(None, [(max(0.0, inicio), fin)]),
                 force_keyframes_at_cuts=False)
    try:
        with scheduler.slot("twitch", job_id) if is_twitch(url) else contextlib.nullcontext():
            downloader._download_with_retry(opts, url)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(downloader._friendly_error(exc))
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
