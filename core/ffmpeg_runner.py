"""Wrapper central para ejecutar FFmpeg.

Este es el único lugar donde se ejecutan comandos de FFmpeg. Ningún módulo
debe llamar a subprocess directamente.
"""
import functools
import json
import os
import re
import subprocess
import tempfile
import threading

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

# Windows caps a WHOLE command line at 32,767 characters (Linux caps each
# argument at 128 KB, which is why it never showed up in the cloud). A dense
# edit (one eq/zoompan term per event) or the beat preview of a long, fast song
# builds graphs past that, and CreateProcess failed with WinError 206 ("file
# name too long"). Long graphs go through a file instead: "-/<option> <file>"
# (FFmpeg 7+) reads the option's value from that file. The path is an option
# value, not filter syntax, so an absolute path with the drive colon is fine.
_GRAPH_OPTIONS = {"-filter_complex", "-lavfi", "-vf", "-af", "-filter:v", "-filter:a"}
MAX_INLINE_GRAPH = 8000


def _spill_long_graphs(command: list[str]) -> tuple[list[str], list[str]]:
    """Moves filter graphs longer than MAX_INLINE_GRAPH to temp files.
    Returns the command to run and the files to delete afterwards."""
    out, temps = list(command), []
    for i in range(len(out) - 1):
        if out[i] in _GRAPH_OPTIONS and len(out[i + 1]) > MAX_INLINE_GRAPH:
            fd, path = tempfile.mkstemp(prefix="reelforge-", suffix=".ffgraph")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(out[i + 1])
            out[i], out[i + 1] = "-/" + out[i].lstrip("-"), path
            temps.append(path)
    return out, temps


def _remove(paths: list[str]) -> None:
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass


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


# NVENC has a limit of encode sessions at once (consumer GeForce cards): with
# 7 edits at once, 4 failed with "Invalid argument (-22)". At most NVENC_MAX
# encodes run on the GPU at the same time (the rest wait), and one that
# fails anyway is run again with libx264.
NVENC_MAX = 3
NVENC_SLOTS = threading.BoundedSemaphore(NVENC_MAX)
# Counters for the jobs' metrics (competitivo/metrics.py).
stats = {"nvenc_fallbacks": 0}
_NVENC_ERRORS = ("nvenc", "encodesession", "(-22)", "error while opening encoder")


def uses_nvenc(command: list[str]) -> bool:
    return "h264_nvenc" in command


def nvenc_failed(detail: str) -> bool:
    """Whether an FFmpeg failure is NVENC's (no free session, the driver)."""
    low = detail.lower()
    return any(e in low for e in _NVENC_ERRORS)


def cpu_fallback(command: list[str]) -> list[str]:
    """The same command with libx264 instead of h264_nvenc (video_encode_flags'
    two flag sets: -cq N → -crf N, preset p4 → veryfast, without -rc / -b:v 0)."""
    out, i, crf = [], 0, "18"
    while i < len(command):
        a = command[i]
        nxt = command[i + 1] if i + 1 < len(command) else None
        if a == "-c:v" and nxt == "h264_nvenc":
            out += ["-c:v", "libx264"]
            i += 2
        elif a == "-cq" and nxt is not None:
            crf = nxt
            i += 2
        elif a == "-rc" or (a == "-b:v" and nxt == "0") or (a == "-preset" and str(nxt).startswith("p")):
            i += 2
        else:
            out.append(a)
            i += 1
    k = out.index("libx264") + 1
    return out[:k] + ["-crf", crf, "-preset", "veryfast"] + out[k:]


def encoder_info() -> str:
    """Descripción legible del encoder activo (la muestra el módulo Info)."""
    return "GPU NVENC (h264_nvenc)" if _nvenc_available() else "CPU (libx264)"


EXPR_LEAF = 32


def expr_sum(terms: list[str]) -> str:
    """Sum of FFmpeg expression terms that FFmpeg can parse at any length.

    The expression parser (libavutil/eval.c) caps the depth of the expression
    tree at ~100, and a flat a+b+c… is a chain as deep as its number of terms:
    100+ terms failed with "Error when parsing the expression" on every OS (an
    edit with 100 shakes, the beat preview of any real song, ~400 beats).
    Splitting in halves keeps the depth at EXPR_LEAF + ~2 per level (measured:
    10,000 terms parse)."""
    if not terms:
        return "0"
    if len(terms) <= EXPR_LEAF:
        return "+".join(terms)
    mid = len(terms) // 2
    return f"({expr_sum(terms[:mid])})+({expr_sum(terms[mid:])})"


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


def capture(command: list[str], timeout: float = 300, cwd: str | None = None,
            stderr: bool = False) -> bytes:
    """Ejecuta FFmpeg y devuelve su stdout (p. ej. frames en `-f rawvideo -`
    para analizarlos con numpy). Lanza RuntimeError si FFmpeg falla.
    `cwd`: como en run(), para filtros con rutas relativas (drawtext).
    `stderr`: devuelve el stderr (lo que imprimen loudnorm o silencedetect).
    An NVENC encode waits for a GPU slot and falls back to libx264 (see NVENC_MAX)."""
    if uses_nvenc(command):
        try:
            with NVENC_SLOTS:
                return _capture(command, timeout, cwd, stderr)
        except RuntimeError as exc:
            if not nvenc_failed(str(exc)):
                raise
            print("[ffmpeg] NVENC falló (sin sesión libre?): de nuevo con libx264")
            stats["nvenc_fallbacks"] += 1
            command = cpu_fallback(command)
    return _capture(command, timeout, cwd, stderr)


def _capture(command: list[str], timeout: float, cwd: str | None, stderr: bool) -> bytes:
    command, graphs = _spill_long_graphs(command)
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        raise RuntimeError("FFmpeg tardó demasiado en leer los frames.")
    finally:
        _remove(graphs)
    if result.returncode != 0:
        tail = result.stderr.decode("utf-8", "replace").strip().splitlines()[-12:]
        raise RuntimeError(f"FFmpeg falló (code {result.returncode}):\n" + "\n".join(tail))
    return result.stderr if stderr else result.stdout


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
    if uses_nvenc(command):
        try:
            with NVENC_SLOTS:
                return _run(command, job_id, total_duration, cwd, lo, hi)
        except RuntimeError as exc:
            if not nvenc_failed(str(exc)):
                raise
            print("[ffmpeg] NVENC falló (sin sesión libre?): de nuevo con libx264")
            stats["nvenc_fallbacks"] += 1
            command = cpu_fallback(command)
    _run(command, job_id, total_duration, cwd, lo, hi)


def _run(command: list[str], job_id: str, total_duration: float | None, cwd: str | None,
         lo: int, hi: int) -> None:
    command, graphs = _spill_long_graphs(command)
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=1,
            cwd=cwd,
            **_TEXT,
        )
    except BaseException:
        _remove(graphs)
        raise

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
        # The pipe is a handle: unclosed, it leaked one per job (ResourceWarning).
        process.stderr.close()
        _remove(graphs)

    if process.returncode != 0:
        detail = "".join(stderr_tail).strip()
        raise RuntimeError(f"FFmpeg falló (code {process.returncode}):\n{detail}")

    job_manager.set_progress(job_id, hi)
