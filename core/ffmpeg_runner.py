"""Wrapper central para ejecutar FFmpeg.

Este es el único lugar donde se ejecutan comandos de FFmpeg. Ningún módulo
debe llamar a subprocess directamente.
"""
import json
import re
import subprocess

import config
from core import job_manager

# Captura "time=HH:MM:SS.xx" y "Duration: HH:MM:SS.xx" del stderr de FFmpeg
_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")


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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        streams = json.loads(result.stdout or "{}").get("streams", [])
        num, den = streams[0]["r_frame_rate"].split("/")
        den = float(den)
        return float(num) / den if den else None
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, IndexError, ZeroDivisionError):
        return None


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
        cwd: str | None = None) -> None:
    """Ejecuta FFmpeg como subprocess.

    - Parsea stderr para extraer progreso (time=) y lo reporta a job_manager.
    - Lanza RuntimeError si FFmpeg retorna un código de error.
    - `cwd`: directorio de trabajo. Algunos filtros (drawtext) necesitan rutas
      relativas, que se resuelven contra este directorio.
    """
    job_manager.update_job(job_id, status="processing", progress=0)

    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        cwd=cwd,
    )

    stderr_tail: list[str] = []
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
                pct = int((current / total_duration) * 100)
                # Reservamos el 100 para cuando el proceso termine con éxito
                job_manager.set_progress(job_id, min(pct, 99))

    process.wait()

    if process.returncode != 0:
        detail = "".join(stderr_tail).strip()
        raise RuntimeError(f"FFmpeg falló (code {process.returncode}):\n{detail}")

    job_manager.set_progress(job_id, 100)
