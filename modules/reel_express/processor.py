"""Orquestación del módulo reel_express (pipeline).

Encadena los módulos en el servidor, sin rebotes por el browser:

    clip 16:9 ──vertical(+texto/marca, 1 pasada)──┐
                                                  ├──sound_drop──▶ [subtítulos] ──▶ reel final
    audio (link│archivo) ─────────────────────────┘

La conversión a vertical y la descarga (si la fuente es un link) son
independientes y corren en paralelo. El watermark (texto/marca) NO es una
pasada aparte: sus filtros se fusionan en el MISMO filter_complex de la
conversión a vertical (un decode+encode menos y una generación menos de
pérdida). La mezcla de audio produce el "video base".

Subtítulos (opcional, default ON) parte el flujo en DOS fases, porque el usuario
revisa/edita el texto en el medio:
  - Fase 1 (process): produce el video base y, si hay subs, lo transcribe y
    deja los segmentos en el job (status done + segments). El video base
    sobrevive para la fase 2.
  - Fase 2 (finish): con los segmentos editados, quema los subtítulos sobre el
    base → reel final. Los subs van últimos, así quedan encima de todo.

No hay lógica de filtros propia: compone los fragmentos puros de cada módulo
(build_filter_complex / build_filters) y reusa sus process()/helpers.
"""
import threading

import config
from core import ffmpeg_runner, file_utils, job_manager
from modules.reel_express.schema import ReelExpressParams
from modules.vertical_convert import processor as vertical_processor
from modules.downloader import processor as downloader_processor
from modules.sound_drop import processor as sound_drop_processor
from modules.watermark import processor as watermark_processor
from modules.subtitles import processor as subtitles_processor


def _check_subjob(sub_id: str, etapa: str) -> dict:
    """Lee un sub-job terminado; si falló, propaga el error con nombre de etapa."""
    sub = job_manager.get_job(sub_id)
    if not sub or sub["status"] == "error":
        detalle = (sub or {}).get("error") or "error desconocido"
        raise RuntimeError(f"Falló en {etapa}: {detalle}")
    return sub


def _vertical_watermark_job(job_id: str, clip_path: str, output_path: str,
                            params: ReelExpressParams) -> None:
    """Convierte a vertical y, si hay texto/marca, lo compone en la MISMA pasada.

    Fusiona el filter_complex de vertical_convert con el fragmento de watermark
    (un solo decode+encode en vez de dos). Sin watermark, delega en el process()
    normal de vertical_convert. Corre como sub-job (thread de la fase prep).
    """
    if not params.has_watermark:
        vertical_processor.process(job_id, clip_path, output_path, params.vertical)
        return

    temp_files: list[str] = []
    try:
        duration = ffmpeg_runner.probe_duration(clip_path)

        (font_path, primary_files, secondary_files,
         watermark_path, temp_files) = watermark_processor.prepare_assets(params.watermark)

        # vertical: [0:v] → [vc]; watermark: [vc] → [vout]. El PNG es el input 1.
        filters = [vertical_processor.build_filter_complex(params.vertical, out="[vc]")]
        filters += watermark_processor.build_filters(
            params.watermark, font_path=font_path, primary_files=primary_files,
            secondary_files=secondary_files, watermark_path=watermark_path,
            src="[vc]", out="[vout]", wm_index=1,
        )

        cmd = [config.FFMPEG_PATH, "-y", "-i", clip_path]
        if watermark_path:
            cmd += ["-i", watermark_path]
        cmd += [
            "-filter_complex", ";".join(filters),
            "-map", "[vout]",
            "-map", "0:a?",
            *ffmpeg_runner.video_encode_flags(),
            "-c:a", "aac",
            output_path,
        ]
        # cwd=BASE_DIR: los drawtext usan rutas relativas (ver watermark._esc).
        ffmpeg_runner.run(cmd, job_id, total_duration=duration, cwd=config.BASE_DIR)
        job_manager.update_job(job_id, status="done", progress=100)
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=str(exc))
    finally:
        watermark_processor.cleanup_assets(temp_files)


def process(job_id: str, clip_path: str, params: ReelExpressParams, *,
            audio_path: str | None = None) -> None:
    """Fase 1 del pipeline (bloqueante). Reporta al job del pipeline.

    `audio_path` viene seteado cuando la fuente es un archivo subido; cuando es
    un link queda None y se completa con la descarga.

    Si hay subtítulos, termina transcribiendo el video base y dejando los
    segmentos en el job (status done + segments), a la espera de la fase 2.
    Si no, el video base es el reel final.
    """
    intermedios: list[str] = [clip_path]
    if audio_path:
        intermedios.append(audio_path)
    try:
        job_manager.update_job(job_id, status="processing", progress=0)

        # --- Fase prep: vertical (+texto/marca fusionados) y, si la fuente es
        #     un link, la descarga del audio en paralelo ---
        sub_v = job_manager.create_job(clip_path, output_path="")
        vertical_out = file_utils.output_path_for(sub_v)
        sub_d = None
        if params.audio_source == "url":
            sub_d = job_manager.create_job(params.downloader.url, output_path="")
        job_manager.update_job(job_id, phase="prep", sub_v=sub_v, sub_d=sub_d)

        t_v = threading.Thread(
            target=_vertical_watermark_job,
            args=(sub_v, clip_path, vertical_out, params),
            daemon=True,
        )
        t_d = None
        if sub_d:
            t_d = threading.Thread(
                target=downloader_processor.process,
                args=(sub_d, params.downloader),
                daemon=True,
            )
        t_v.start()
        if t_d:
            t_d.start()
        t_v.join()
        if t_d:
            t_d.join()

        _check_subjob(sub_v, "la conversión a vertical (y el texto/marca)")
        if sub_d:
            audio_path = _check_subjob(sub_d, "la descarga del audio")["output_path"]
            intermedios.append(audio_path)
        intermedios.append(vertical_out)

        # --- Fase mix: audio sobre el vertical(+marca). Produce el video base ---
        sub_m = job_manager.create_job(vertical_out, output_path="")
        mix_out = (file_utils.output_path_for(sub_m) if params.has_subtitles
                   else file_utils.output_path_for(job_id))
        job_manager.update_job(job_id, phase="mix", sub_m=sub_m)

        sound_drop_processor.process(
            sub_m, vertical_out, audio_path, mix_out, params.sound_drop
        )
        _check_subjob(sub_m, "la mezcla de audio")

        base_out = mix_out  # "video base": vertical + texto/marca + mezcla

        # --- Fase subtítulos: transcribir el base y pausar para edición ---
        if params.has_subtitles:
            sub_t = job_manager.create_job(base_out, output_path="")
            job_manager.update_job(job_id, phase="transcribe", sub_t=sub_t)
            segments = subtitles_processor.transcribe(
                base_out, params.subtitles, job_id=sub_t
            )
            job_manager.update_job(sub_t, status="done", progress=100)
            # El base debe sobrevivir a la fase 2: NO lo borramos.
            file_utils.cleanup_paths(*intermedios)
            job_manager.update_job(
                job_id, status="done", progress=100, phase=None,
                segments=segments, base_path=base_out,
            )
            return

        # --- Sin subtítulos: el base es el reel final ---
        job_manager.update_job(
            job_id, status="done", progress=100, phase=None, output_path=base_out
        )
        file_utils.cleanup_paths(*intermedios)
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=str(exc))


def finish(job_id: str, base_path: str, segments: list[dict],
           subtitle_params, output_path: str) -> None:
    """Fase 2: quema los subtítulos editados sobre el video base → reel final.

    Reusa el render_job del módulo subtitles (arma el .ass y lo quema) y luego
    borra el video base.
    """
    subtitles_processor.render_job(
        job_id, base_path, segments, subtitle_params, output_path
    )
    file_utils.cleanup_paths(base_path)
