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


def build_filter_complex(params: VerticalConvertParams,
                         src: str = "[0:v]", out: str = "[out]") -> str:
    """Arma el filter_complex a partir de los parámetros validados.

    `src`/`out` permiten encadenar esta cadena dentro de un filter_complex
    mayor (lo usa reel_express para fusionar vertical + watermark en una sola
    pasada). OJO: `src` se consume dos veces (fondo y clip principal), así que
    debe ser un stream de entrada (p. ej. "[0:v]"), no un label intermedio.
    """
    sigma = params.blur_intensity
    # bg_brightness es un multiplicador (0.3-1.0); colorchannelmixer lo aplica
    # de forma multiplicativa igual que CSS brightness(), evitando el clipping
    # negro que produce eq=brightness (que es aditivo).
    bm = round(params.bg_brightness, 3)

    # Capa principal: escalada por ancho del lienzo y zoom configurable.
    fg_width = _even(W * params.main_clip_scale)

    # Posición vertical del clip principal.
    if params.main_clip_position == "top":
        overlay_y = "0"
    elif params.main_clip_position == "bottom":
        overlay_y = "H-h"
    else:
        overlay_y = "(H-h)/2"

    if sigma >= 8:
        # gblur es el filtro más caro de la cadena y su costo escala con los
        # píxeles. Con blur fuerte, blurear a 1/4 de resolución (sigma/4) y
        # reescalar da un resultado indistinguible (SSIM ~0.994 medido) por una
        # fracción del costo. Con blur suave no se usa: el reescalado se vería.
        w4, h4 = _even(W / 4), _even(H / 4)
        bg = (
            f"{src}scale={w4}:{h4}:force_original_aspect_ratio=increase,"
            f"crop={w4}:{h4},"
            f"gblur=sigma={sigma / 4:.2f},"
            f"scale={W}:{H}:flags=bilinear,"
            f"colorchannelmixer=rr={bm}:gg={bm}:bb={bm}[bg]"
        )
    else:
        bg = (
            f"{src}scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},"
            f"gblur=sigma={sigma},"
            f"colorchannelmixer=rr={bm}:gg={bm}:bb={bm}[bg]"
        )

    parts = [bg]

    if params.enhance_intensity > 0:
        i = params.enhance_intensity / 100
        # Curva asimétrica: sombras casi intactas, medios y brillos levantados.
        shadow    = round(0.25 - i * 0.02, 4)   # toca poco las sombras
        midtone   = round(0.50 + i * 0.04, 4)   # levanta los medios
        highlight = round(0.75 + i * 0.10, 4)   # levanta bien los brillos
        brightness = round(i * 0.04, 3)          # boost general de exposición
        sat       = round(1.0 + i * 0.5, 3)
        sharp     = round(i * 0.5, 3)
        parts.append(f"{src}scale={fg_width}:-2[fg_raw]")
        parts.append(
            f"[fg_raw]curves=all='0/0 0.25/{shadow} 0.5/{midtone} 0.75/{highlight} 1/1',"
            f"eq=brightness={brightness}:saturation={sat},"
            f"unsharp=lx=3:ly=3:la={sharp}:cx=3:cy=3:ca=0[fg]"
        )
    else:
        parts.append(f"{src}scale={fg_width}:-2[fg]")

    parts.append(f"[bg][fg]overlay=(W-w)/2:{overlay_y}{out}")
    return ";".join(parts)


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
        *ffmpeg_runner.video_encode_flags(),
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
