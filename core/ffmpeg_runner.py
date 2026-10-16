"""Wrapper central para ejecutar FFmpeg.

Este es el único lugar donde se ejecutan comandos de FFmpeg. Ningún módulo
debe llamar a subprocess directamente.
"""
import functools
import json
import re
import subprocess

import config
from core import job_manager

# Captura "time=HH:MM:SS.xx" y "Duration: HH:MM:SS.xx" del stderr de FFmpeg
_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")

# FFmpeg y ffprobe escriben UTF-8 (rutas, títulos y tags ID3 incluidos). Sin
# encoding explícito, Python decodifica con la codificación del sistema (cp1252
# en Windows) y una "Á" (bytes C3 81: el 0x81 no existe en cp1252) tiraba
# UnicodeDecodeError: el job fallaba con FFmpeg huérfano, y en los probes la
# duración salía None en silencio (sin speed match). errors="replace": un byte
# raro en el log no puede tumbar un render.
_TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}


def probe_duration(input_path: str) -> float | None:
    """Retorna la duración del video en segundos usando ffprobe, o None."""
    cmd = [
        config.FFPROBE_PATH,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        input_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30, **_TEXT)
        data = json.loads(result.stdout or "{}")
        return float(data.get("format", {}).get("duration"))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        # ffprobe ausente o salida inesperada: la duración es opcional, sólo
        # se usa para calcular el porcentaje de progreso.
        return None


def has_audio_stream(input_path: str) -> bool:
    """Indica si el archivo tiene al menos una pista de audio."""
    cmd = [
        config.FFPROBE_PATH,
        "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-print_format", "json",
        input_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30, **_TEXT)
        data = json.loads(result.stdout or "{}")
        return bool(data.get("streams"))
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        # Ante la duda asumimos que sí hay audio; si no, FFmpeg lo reportará.
        return True


def probe_resolution(input_path: str) -> tuple[int, int] | None:
    """Retorna (width, height) del primer stream de video, o None si falla."""
    cmd = [
        config.FFPROBE_PATH,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-print_format", "json",
        input_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30, **_TEXT)
        streams = json.loads(result.stdout or "{}").get("streams", [])
        if not streams:
            return None
        return int(streams[0]["width"]), int(streams[0]["height"])
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
        return None


def probe_fps(input_path: str) -> float | None:
    """Retorna los fps del primer stream de video (r_frame_rate), o None."""
    cmd = [
        config.FFPROBE_PATH,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-print_format", "json",
        input_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30, **_TEXT)
        streams = json.loads(result.stdout or "{}").get("streams", [])
        num, den = streams[0]["r_frame_rate"].split("/")
        den = float(den)
        return float(num) / den if den else None
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, IndexError, ZeroDivisionError):
        return None


@functools.lru_cache(maxsize=1)
def _nvenc_available() -> bool:
    """Verifica si h264_nvenc está disponible (se ejecuta una sola vez al arrancar)."""
    # OJO 1: NVENC tiene tamaño mínimo de frame; con 64x64 el probe fallaba
    # ("Frame Dimension less than the minimum supported value") y la GPU
    # quedaba deshabilitada en silencio. 256x256 es seguro.
    # OJO 2: el print va FUERA del try y en ASCII puro: un UnicodeEncodeError
    # en consolas cp1252 de Windows también apagaba la GPU en silencio.
    try:
        r = subprocess.run(
            [config.FFMPEG_PATH, "-f", "lavfi", "-i", "color=c=black:s=256x256:r=1",
             "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, timeout=10,
        )
        available = r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        available = False
    tag = "GPU NVENC" if available else "CPU libx264 (NVENC no disponible)"
    print(f"[ffmpeg] encoder: {tag}")
    return available


# Códecs que NVDEC decodifica en las GPUs con NVENC (RTX y afines).
_CUDA_DECODE_CODECS = {"h264", "hevc", "av1", "vp9"}

# Decodificación en la GPU con los frames quedándose en la VRAM. El grafo tiene
# que bajarlos con hwdownload,format=nv12 (ver cuda_decode_supported).
CUDA_DECODE_FLAGS = ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]


def _rate(text: str | None) -> float | None:
    """"60/1" → 60.0 (None si falta o es 0/0)."""
    try:
        num, den = (text or "").split("/")
        return float(num) / float(den) if float(den) else None
    except ValueError:
        return None


def probe_video_stream(input_path: str) -> dict | None:
    """Códec, pix_fmt y frame rates del primer stream de video, o None.

    `fps` es el nominal (r_frame_rate, el que usa fps=source_fps) y `avg_fps`
    el promedio real: si difieren, el clip es de frame rate variable.
    """
    cmd = [
        config.FFPROBE_PATH,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,pix_fmt,r_frame_rate,avg_frame_rate",
        "-print_format", "json",
        input_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30, **_TEXT)
        stream = json.loads(result.stdout or "{}").get("streams", [])[0]
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, IndexError):
        return None
    return {
        "codec": stream.get("codec_name"),
        "pix_fmt": stream.get("pix_fmt"),
        "fps": _rate(stream.get("r_frame_rate")),
        "avg_fps": _rate(stream.get("avg_frame_rate")),
    }


def cuda_decode_supported(stream: dict | None) -> bool:
    """¿Conviene decodificar en la GPU (NVDEC) el clip de `stream`
    (lo que devuelve probe_video_stream)?

    Solo con NVIDIA (la misma GPU de NVENC) y video 8 bits 4:2:0 de un códec
    que NVDEC soporta: es el caso en que hwdownload entrega nv12. 10 bits,
    4:4:4 o yuvj quedan en la CPU. El que lo usa igual tiene que reintentar
    sin GPU si FFmpeg falla: con -hwaccel_output_format cuda no hay fallback
    automático a software.
    """
    return bool(stream and _nvenc_available()
                and stream["codec"] in _CUDA_DECODE_CODECS
                and stream["pix_fmt"] == "yuv420p")


def video_encode_flags(crf: int = 18) -> list[str]:
    """Flags de encodeo de video: h264_nvenc (GPU) si está disponible, libx264 si no.

    Uso: cmd += video_encode_flags()  o  [*video_encode_flags(), ...]
    """
    if _nvenc_available():
        return ["-c:v", "h264_nvenc", "-preset", "p4",
                "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
    return ["-c:v", "libx264", "-crf", str(crf), "-preset", "veryfast"]


def encoder_info() -> str:
    """Descripción legible del encoder activo (la muestra el módulo Info)."""
    return "GPU NVENC (h264_nvenc)" if _nvenc_available() else "CPU (libx264)"


def atempo_chain(tempo: float) -> str:
    """Cadena atempo válida para cualquier factor > 0.

    atempo acepta [0.5, 100] por instancia; para factores menores se encadenan
    varios (0.4 → atempo=0.5,atempo=0.8). Devuelve el fragmento de filtro listo
    para intercalar en una cadena de audio.
    """
    parts: list[float] = []
    while tempo < 0.5:
        parts.append(0.5)
        tempo /= 0.5
    parts.append(min(tempo, 100.0))
    return ",".join(f"atempo={t:.6f}" for t in parts)


def capture(command: list[str], timeout: float = 300, cwd: str | None = None) -> bytes:
    """Ejecuta FFmpeg y devuelve su stdout (p. ej. frames en `-f rawvideo -`
    para analizarlos con numpy). Lanza RuntimeError si FFmpeg falla.
    `cwd`: como en run(), para filtros con rutas relativas (drawtext)."""
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise RuntimeError("FFmpeg tardó demasiado en leer los frames.")
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
        raise RuntimeError(f"FFmpeg falló (code {result.returncode}):\n" + "\n".join(tail))
    return result.stdout


def _hms_to_seconds(match) -> float:
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _parse_time_seconds(line: str) -> float | None:
    m = _TIME_RE.search(line)
    return _hms_to_seconds(m) if m else None


def _parse_duration_seconds(line: str) -> float | None:
    m = _DURATION_RE.search(line)
    return _hms_to_seconds(m) if m else None


def run(command: list[str], job_id: str, total_duration: float | None = None,
        cwd: str | None = None,
        progress_range: tuple[int, int] = (0, 100)) -> None:
    """Ejecuta FFmpeg como subprocess.

    - Parsea stderr para extraer progreso (time=) y lo reporta a job_manager.
    - Lanza RuntimeError si FFmpeg retorna un código de error.
    - `cwd`: directorio de trabajo. Algunos filtros (drawtext) necesitan rutas
      relativas, que se resuelven contra este directorio.
    - `progress_range`: banda (lo, hi) dentro de la cual se reporta el progreso
      de este comando. Útil cuando FFmpeg es una fase de un pipeline mayor
      (p. ej. subtítulos: transcripción ocupa 0-52, quemado 52-100).
    """
    lo, hi = progress_range
    job_manager.update_job(job_id, status="processing", progress=lo)

    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        bufsize=1,
        cwd=cwd,
        **_TEXT,
    )

    stderr_tail: list[str] = []
    try:
        for line in process.stderr:
            stderr_tail.append(line)
            if len(stderr_tail) > 40:
                stderr_tail.pop(0)

            # Fallback: si no nos pasaron duración (p.ej. ffprobe ausente), la
            # tomamos de la línea "Duration:" que FFmpeg imprime al inicio.
            if not total_duration:
                total_duration = _parse_duration_seconds(line) or total_duration

            if total_duration and total_duration > 0:
                current = _parse_time_seconds(line)
                if current is not None:
                    frac = current / total_duration
                    pct = int(lo + frac * (hi - lo))
                    job_manager.set_progress(job_id, min(pct, hi - 1))

        process.wait()
    finally:
        # Si algo cortó la lectura (una excepción acá adentro), FFmpeg seguía
        # encodeando huérfano, con el archivo de salida abierto.
        if process.poll() is None:
            process.kill()
            process.wait()

    if process.returncode != 0:
        detail = "".join(stderr_tail).strip()
        raise RuntimeError(f"FFmpeg falló (code {process.returncode}):\n{detail}")

    job_manager.set_progress(job_id, hi)
