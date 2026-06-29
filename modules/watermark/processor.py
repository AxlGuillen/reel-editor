"""Lógica FFmpeg del módulo watermark.

Compone texto (drawtext, con contorno) y un watermark (overlay) sobre el video,
preservando la pista de audio original. El video se re-encodea (dibujamos
encima); el audio se copia tal cual.

Detalle clave de escaping: un path absoluto de Windows (con el colon del drive)
rompe el parser de filtros. Se usan rutas RELATIVAS a la raíz del proyecto y se
corre FFmpeg con cwd=BASE_DIR.
"""
import os
import uuid

import config
from core import ffmpeg_runner, job_manager
from modules.watermark.schema import WatermarkParams

H = config.OUTPUT_HEIGHT
WM_MARGIN = 40          # px de margen horizontal del watermark (izq/der)


def _esc(path: str) -> str:
    """Ruta relativa a BASE_DIR, con '/', entre comillas (para fontfile/textfile)."""
    rel = os.path.relpath(path, config.BASE_DIR).replace("\\", "/")
    return "'" + rel + "'"


def _text_filters(params: WatermarkParams, font_path: str,
                  primary_files: list[str | None],
                  secondary_files: list[str | None]) -> list[str]:
    """Filtros drawtext del bloque de texto, un drawtext POR RENGLÓN.

    Dibujar cada línea por separado con nuestro propio interlineado evita el
    interlineado interno de FFmpeg (que varía mucho según la fuente, p. ej.
    Poppins es enorme) y hace que el preview en canvas calce exacto con la salida.
    """
    fs = params.text_size
    line_h = round(fs * 1.3)
    gap = round(fs * 0.3)
    border = max(2, round(fs * 0.07))
    font = _esc(font_path)

    p_count = len(primary_files)
    s_count = len(secondary_files)
    both = p_count and s_count
    block_height = p_count * line_h + (gap if both else 0) + s_count * line_h
    block_top = round(params.text_y / 100 * max(0, H - block_height))

    def drawtext(textfile, color, y):
        return (
            f"drawtext=fontfile={font}:textfile={_esc(textfile)}:"
            f"expansion=none:fontsize={fs}:fontcolor={color}:"
            f"borderw={border}:bordercolor=black:"
            f"x=(w-text_w)/2:y={y}"
        )

    filters: list[str] = []
    y = block_top
    for tf in primary_files:
        if tf:  # los renglones vacíos solo avanzan la posición
            filters.append(drawtext(tf, "white", y))
        y += line_h
    if both:
        y += gap
    for tf in secondary_files:
        if tf:
            filters.append(drawtext(tf, "red", y))
        y += line_h
    return filters


def _overlay_xy(params: WatermarkParams) -> tuple[str, str]:
    """Expresiones x:y de overlay según posición horizontal + altura (%)."""
    m = WM_MARGIN
    x = {
        "left": f"{m}",
        "center": "(W-w)/2",
        "right": f"W-w-{m}",
    }[params.watermark_x]
    y = f"(H-h)*{params.watermark_y / 100:.4f}"
    return x, y


def build_command(video_path: str, output_path: str, params: WatermarkParams, *,
                  font_path: str | None = None,
                  primary_files: list[str | None] | None = None,
                  secondary_files: list[str | None] | None = None,
                  watermark_path: str | None = None) -> list[str]:
    """Construye el comando FFmpeg: dibuja texto + watermark, copia el audio."""
    has_text = params.has_text and font_path
    has_wm = params.has_watermark and watermark_path

    cmd = [config.FFMPEG_PATH, "-y", "-i", video_path]
    if has_wm:
        cmd += ["-i", watermark_path]  # input índice 1

    filters: list[str] = []
    vops: list[str] = []
    if has_text:
        vops += _text_filters(params, font_path, primary_files or [], secondary_files or [])

    if vops:
        filters.append(f"[0:v]{','.join(vops)}[vbase]")
        base_label = "[vbase]"
    else:
        base_label = "[0:v]"

    if has_wm:
        ww = round(config.OUTPUT_WIDTH * params.watermark_size / 100)
        ox, oy = _overlay_xy(params)
        filters.append(f"[1:v]scale={ww}:-1[wm]")
        filters.append(f"{base_label}[wm]overlay={ox}:{oy}[vout]")
        vmap = "[vout]"
    else:
        vmap = "[vbase]"

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", vmap,
        "-map", "0:a?",                # preserva el audio original si existe
        *ffmpeg_runner.video_encode_flags(),
        "-c:a", "copy",
        output_path,
    ]
    return cmd


def _write_textfile(text: str) -> str:
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    path = os.path.join(config.UPLOAD_FOLDER, f"txt_{uuid.uuid4().hex[:12]}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _write_lines(text: str) -> list[str | None]:
    """Un textfile por renglón (None para renglones vacíos)."""
    files: list[str | None] = []
    for line in text.split("\n"):
        files.append(_write_textfile(line) if line.strip() else None)
    return files


def process(job_id: str, video_path: str, output_path: str,
            params: WatermarkParams) -> None:
    """Ejecuta el job completo (bloqueante). Actualiza job_manager en cada paso."""
    temp_files: list[str] = []
    try:
        video_dur = ffmpeg_runner.probe_duration(video_path)

        font_path = None
        primary_files: list[str | None] = []
        secondary_files: list[str | None] = []
        if params.has_text:
            font_path = config.resolve_font_path()
            if not font_path:
                raise RuntimeError(
                    "No hay fuente cargada. Dejá tu .ttf u .otf en assets/fonts/."
                )
            if params.primary_text:
                primary_files = _write_lines(params.primary_text)
            if params.secondary_text:
                secondary_files = _write_lines(params.secondary_text)
            temp_files += [f for f in primary_files + secondary_files if f]

        watermark_path = None
        if params.has_watermark:
            watermark_path = os.path.join(config.WATERMARKS_FOLDER, params.watermark)
            if not os.path.isfile(watermark_path):
                raise RuntimeError(f"Watermark no encontrado: {params.watermark}")

        command = build_command(
            video_path, output_path, params,
            font_path=font_path, primary_files=primary_files,
            secondary_files=secondary_files, watermark_path=watermark_path,
        )
        # cwd=BASE_DIR: los filtros drawtext usan rutas relativas (ver _esc).
        ffmpeg_runner.run(command, job_id, total_duration=video_dur, cwd=config.BASE_DIR)
        job_manager.update_job(job_id, status="done", progress=100)
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=str(exc))
    finally:
        for path in temp_files:
            try:
                os.remove(path)
            except OSError:
                pass
