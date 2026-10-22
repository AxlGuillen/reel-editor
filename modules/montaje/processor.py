"""Montaje: capas animadas (flyers, láminas de presentations) sobre un video
vertical ya terminado, en UN encode, con los subtítulos encima si se piden.

Cada capa:
  -itsoffset desde -i capa      el clip empieza en su segundo
  scale / format=yuva420p       a su caja, con su transparencia (ProRes 4444,
                                VP9 con alfa o PNG); un mp4 opaco se ve como
                                rectángulo (el "destello negro" de suiza)
  tpad=stop_mode=clone          se congela en su último cuadro hasta `hasta`
                                (ya no hace falta el PNG aparte)
  fade alpha in/out             fundidos de entrada y salida
  overlay enable=between        solo en su intervalo, eof_action=pass

Modo "franja" (el de Lux): la capa centrada a la altura de la franja, sobre
una copia suya a lo ancho, difuminada y oscurecida, que tapa lo de atrás.
El audio del video base se copia tal cual.
"""
from __future__ import annotations

import os

import config
from core import ffmpeg_runner, file_utils, job_manager
from modules.montaje.capas import Capa
from modules.montaje.schema import MontajeParams
from modules.subtitles import processor as subtitles

W, H = config.OUTPUT_WIDTH, config.OUTPUT_HEIGHT
_IMAGEN = (".png", ".jpg", ".jpeg", ".webp")


def _x(caja, w_expr: str = "w") -> str:
    return {"izquierda": "0", "derecha": f"W-{w_expr}"}.get(caja.x, f"(W-{w_expr})/2")


def build_command(video: str, capas: list[Capa], out: str, ass: str | None = None) -> list[str]:
    """Un solo encode: el video base + cada capa en su tiempo + subtítulos."""
    cmd = [config.FFMPEG_PATH, "-y", "-i", video]
    for c in capas:
        dur = c.hasta - c.desde
        if c.archivo.lower().endswith(_IMAGEN):
            cmd += ["-loop", "1", "-framerate", "30", "-t", f"{dur + 0.1:.3f}"]
        cmd += ["-itsoffset", f"{c.desde:.3f}", "-i", c.archivo]
    partes = []
    actual = "0:v"
    for k, c in enumerate(capas, 1):
        resto = max(0.0, (c.hasta - c.desde) - (c.dur_clip or 0)) + 0.2
        fundidos = [f"fade=t=in:st={c.desde:.3f}:d={c.fundido_entrada:.3f}:alpha=1"] if c.fundido_entrada else []
        if c.fundido_salida:
            fundidos.append(f"fade=t=out:st={c.hasta - c.fundido_salida:.3f}:d={c.fundido_salida:.3f}:alpha=1")
        cola = ",".join(["format=yuva420p", f"tpad=stop_mode=clone:stop_duration={resto:.3f}", *fundidos])
        enable = f"enable='between(t,{c.desde:.3f},{c.hasta:.3f})':eof_action=pass"
        caja = c.caja
        if caja.modo == "franja":
            partes += [
                f"[{k}:v]split[c{k}a][c{k}b]",
                f"[c{k}a]scale={W}:{caja.alto}:force_original_aspect_ratio=increase,crop={W}:{caja.alto},"
                f"gblur=sigma=22,eq=brightness=-0.3,{cola}[f{k}]",
                f"[c{k}b]scale=-2:{caja.alto}:flags=lanczos,{cola}[l{k}]",
                f"[{actual}][f{k}]overlay=0:{caja.y}:{enable}[b{k}]",
                f"[b{k}][l{k}]overlay={_x(caja)}:{caja.y}:{enable}[v{k}]",
            ]
        else:
            partes += [
                f"[{k}:v]scale={caja.ancho}:-2:flags=lanczos,{cola}[l{k}]",
                f"[{actual}][l{k}]overlay={_x(caja)}:H-h-{caja.abajo}:{enable}[v{k}]",
            ]
        actual = f"v{k}"
    final = [subtitles.ass_filter(ass)] if ass else []
    partes.append(f"[{actual}]" + ",".join(final + ["format=yuv420p"]) + "[vout]")
    cmd += ["-filter_complex", ";".join(partes), "-map", "[vout]", "-map", "0:a?",
            *ffmpeg_runner.video_encode_flags(), "-c:a", "copy", "-movflags", "+faststart", out]
    return cmd


def preparar(params: MontajeParams, dur_video: float, segments: list[dict] | None) -> list[Capa]:
    """Capas con sus tiempos y la duración de cada clip."""
    capas = params.capas_resueltas(segments, dur_video)
    for c in capas:
        if not c.archivo.lower().endswith(_IMAGEN):
            c.dur_clip = ffmpeg_runner.probe_duration(c.archivo)
    return capas


def process(job_id: str, video_path: str, params: MontajeParams, segments: list[dict] | None = None,
            borrar: list[str] | None = None) -> None:
    ass = None
    try:
        job_manager.update_job(job_id, status="processing", progress=2, stage="Leyendo el video…")
        dur = ffmpeg_runner.probe_duration(video_path)
        if not dur:
            raise RuntimeError("No se pudo leer la duración del video.")
        if params.necesita_transcripcion and not segments:
            job_manager.update_job(job_id, progress=5, stage="Transcribiendo para ubicar las palabras…")
            segments = subtitles.transcribe(video_path, params.sub_params)
        capas = preparar(params, dur, segments)
        if params.subtitulos:
            if not segments:
                raise RuntimeError("Para quemar subtítulos hacen falta los segments (o transcribir).")
            ass = subtitles.prepare_ass(segments, params.sub_params)
        out = file_utils.output_path_for(job_id)
        job_manager.update_job(job_id, progress=30, stage=f"Montando {len(capas)} capa(s)…",
                               capas=[{"archivo": os.path.basename(c.archivo), "desde": c.desde,
                                       "hasta": c.hasta, "id": c.etiqueta} for c in capas])
        ffmpeg_runner.run(build_command(video_path, capas, out, ass), job_id, total_duration=dur,
                          cwd=config.BASE_DIR, progress_range=(30, 100))
        job_manager.update_job(job_id, status="done", progress=100, stage=None, output_path=out)
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
    finally:
        if ass and os.path.exists(ass):
            os.remove(ass)
        file_utils.cleanup_paths(*(borrar or []))
