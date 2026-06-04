"""Lógica FFmpeg del módulo vertical_convert.

Construye el filtro "split blur background": el clip llena el lienzo 9:16 con
blur de fondo, y el clip original se superpone centrado en su aspect ratio.
"""
import config
from core import ffmpeg_runner, job_manager
from modules.vertical_convert.schema import VerticalConvertParams

W = config.OUTPUT_WIDTH
H = config.OUTPUT_HEIGHT


def _even(value: float) -> int:
    """Redondea a entero par (requerido por libx264)."""
    return max(2, int(round(value / 2)) * 2)


def build_filter_complex(params: VerticalConvertParams) -> str:
    """Arma el filter_complex a partir de los parámetros validados."""
    sigma = params.blur_intensity
    # bg_brightness es un multiplicador (0.3-1.0); eq.brightness es aditivo.
    brightness = round(params.bg_brightness - 1.0, 3)

    # Capa principal: escalada por ancho del lienzo y zoom configurable.
    fg_width = _even(W * params.main_clip_scale)

    # Posición vertical del clip principal.
    if params.main_clip_position == "top":
        overlay_y = "0"
    elif params.main_clip_position == "bottom":
        overlay_y = "H-h"
    else:
        overlay_y = "(H-h)/2"

    bg = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},"
        f"gblur=sigma={sigma},"
        f"eq=brightness={brightness}[bg]"
    )
    fg = f"[0:v]scale={fg_width}:-2[fg]"
    overlay = f"[bg][fg]overlay=(W-w)/2:{overlay_y}[out]"

    return f"{bg};{fg};{overlay}"


def build_command(input_path: str, output_path: str,
                  params: VerticalConvertParams) -> list[str]:
    """Construye el comando FFmpeg completo."""
    return [
        config.FFMPEG_PATH,
        "-y",
        "-i", input_path,
        "-filter_complex", build_filter_complex(params),
        "-map", "[out]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        "-c:a", "aac",
        output_path,
    ]


def process(job_id: str, input_path: str, output_path: str,
            params: VerticalConvertParams) -> None:
    """Ejecuta el job completo (bloqueante). Actualiza job_manager en cada paso."""
    try:
        duration = ffmpeg_runner.probe_duration(input_path)
        command = build_command(input_path, output_path, params)
        ffmpeg_runner.run(command, job_id, total_duration=duration)
        job_manager.update_job(job_id, status="done", progress=100)
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=str(exc))
