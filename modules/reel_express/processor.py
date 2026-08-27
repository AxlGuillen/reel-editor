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
from dataclasses import replace

import config
from core import ffmpeg_runner, file_utils, job_manager
from modules.reel_express.schema import ReelExpressParams
from modules.vertical_convert import processor as vertical_processor
from modules.downloader import processor as downloader_processor
from modules.sound_drop import processor as sound_drop_processor
from modules.watermark import processor as watermark_processor
from modules.subtitles import processor as subtitles_processor
from modules.insert import processor as insert_processor


def _check_subjob(sub_id: str, etapa: str) -> dict:
    """Lee un sub-job terminado; si falló, propaga el error con nombre de etapa."""
    sub = job_manager.get_job(sub_id)
    if not sub or sub["status"] == "error":
        detalle = (sub or {}).get("error") or "error desconocido"
        raise RuntimeError(f"Falló en {etapa}: {detalle}")
    return sub


def _prepare_visual_job(job_id: str, clip_path: str, output_path: str,
                        params: ReelExpressParams,
                        speed_factor: float | None = None) -> None:
    """Prepara la imagen: vertical y/o texto+marca, y opcionalmente el retime.

    Todo lo que exija re-encodear el clip se fusiona en UN solo filter_complex
    (un decode+encode, una sola generación de pérdida):
      - conversión a vertical (build_filter_complex de vertical_convert)
      - texto/marca (build_filters de watermark)
      - speed match, si llega `speed_factor`: el MISMO setpts que aplicaría
        sound_drop, pero dentro de esta pasada que ya encodea. El audio del
        clip se retima con atempo para que el resultado sea consistente, y la
        mezcla posterior puede ir en -c:v copy (un encode completo menos).

    Sin speed_factor y con una sola tarea, delega en el process() del módulo.
    Corre como sub-job (thread de la fase prep).
    """
    do_vertical = params.convert_vertical
    do_wm = params.has_watermark

    if speed_factor is None:
        if do_vertical and not do_wm:
            vertical_processor.process(job_id, clip_path, output_path, params.vertical)
            return
        if do_wm and not do_vertical:
            watermark_processor.process(job_id, clip_path, output_path, params.watermark)
            return

    temp_files: list[str] = []
    watermark_path = None
    try:
        duration = ffmpeg_runner.probe_duration(clip_path)

        filters: list[str] = []
        cur = "[0:v]"
        if do_vertical:
            filters.append(
                vertical_processor.build_filter_complex(params.vertical, out="[vc]"))
            cur = "[vc]"
        if do_wm:
            (font_path, primary_files, secondary_files,
             watermark_path, temp_files) = watermark_processor.prepare_assets(params.watermark)
            filters += watermark_processor.build_filters(
                params.watermark, font_path=font_path, primary_files=primary_files,
                secondary_files=secondary_files, watermark_path=watermark_path,
                src=cur, out="[vw]", wm_index=1,
            )
            cur = "[vw]"

        out_duration = duration
        if speed_factor is not None:
            filters.append(f"{cur}setpts={speed_factor:.6f}*PTS[vsp]")
            cur = "[vsp]"
            if duration:
                out_duration = duration * speed_factor

        if speed_factor is not None and ffmpeg_runner.has_audio_stream(clip_path):
            tempo = 1.0 / speed_factor
            filters.append(f"[0:a]{ffmpeg_runner.atempo_chain(tempo)}[asp]")
            audio_maps = ["-map", "[asp]"]
        else:
            audio_maps = ["-map", "0:a?"]

        cmd = [config.FFMPEG_PATH, "-y", "-i", clip_path]
        if watermark_path:
            cmd += ["-i", watermark_path]
        cmd += [
            "-filter_complex", ";".join(filters),
            "-map", cur,
            *audio_maps,
            *ffmpeg_runner.video_encode_flags(),
            "-c:a", "aac",
            output_path,
        ]
        # cwd=BASE_DIR: los drawtext usan rutas relativas (ver watermark._esc).
        ffmpeg_runner.run(cmd, job_id, total_duration=out_duration, cwd=config.BASE_DIR)
        job_manager.update_job(job_id, status="done", progress=100)
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=str(exc))
    finally:
        watermark_processor.cleanup_assets(temp_files)


def process(job_id: str, clip_path: str, params: ReelExpressParams, *,
            audio_path: str | None = None,
            dynamic_clip_path: str | None = None) -> None:
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
    if dynamic_clip_path:
        intermedios.append(dynamic_clip_path)
    try:
        job_manager.update_job(job_id, status="processing", progress=0)

        # --- Fase prep: preparación visual (vertical y/o texto/marca) y, si la
        #     fuente es un link, la descarga del audio en paralelo ---
        # Si el clip ya viene 9:16 y no hay texto/marca, no hay nada que
        # preparar: el clip va directo a la mezcla (un encode menos).
        needs_visual = params.convert_vertical or params.has_watermark

        # --- Fusión del speed match en la pasada visual ---
        # Si el clip ya se va a re-encodear (vertical/marca), el retime va
        # gratis en esa misma pasada y la mezcla baja a -c:v copy: se elimina
        # un decode+encode completo del reel. La duración del audio se conoce
        # sin descargarlo (metadatos de yt-dlp) o con un ffprobe local; si el
        # probe falla, se cae al camino clásico (sound_drop retima).
        speed_factor = None
        mix_params = params.sound_drop
        if needs_visual and params.sound_drop.speed_match:
            clip_dur = ffmpeg_runner.probe_duration(clip_path)
            if params.audio_source == "url":
                audio_dur = downloader_processor.probe_duration(params.downloader)
            else:
                audio_dur = ffmpeg_runner.probe_duration(audio_path)
            if clip_dur and audio_dur:
                speed_factor = audio_dur / clip_dur
                mix_params = replace(params.sound_drop, speed_match=False)

        sub_v = None
        vertical_out = clip_path
        if needs_visual:
            sub_v = job_manager.create_job(clip_path, output_path="")
            vertical_out = file_utils.output_path_for(sub_v)
        sub_d = None
        if params.audio_source == "url":
            sub_d = job_manager.create_job(params.downloader.url, output_path="")
        job_manager.update_job(job_id, phase="prep", sub_v=sub_v, sub_d=sub_d)

        t_v = None
        if sub_v:
            t_v = threading.Thread(
                target=_prepare_visual_job,
                args=(sub_v, clip_path, vertical_out, params, speed_factor),
                daemon=True,
            )
        t_d = None
        if sub_d:
            t_d = threading.Thread(
                target=downloader_processor.process,
                args=(sub_d, params.downloader),
                daemon=True,
            )
        if t_v:
            t_v.start()
        if t_d:
            t_d.start()
        if t_v:
            t_v.join()
        if t_d:
            t_d.join()

        if sub_v:
            _check_subjob(sub_v, "la preparación del video (vertical/texto/marca)")
            intermedios.append(vertical_out)
        if sub_d:
            audio_path = _check_subjob(sub_d, "la descarga del audio")["output_path"]
            intermedios.append(audio_path)

        # --- Fase mix: audio sobre el vertical(+marca). Produce el video base ---
        # Si hay subtítulos o fondo dinámico, el mix es intermedio (viene más).
        needs_more_after_mix = params.has_subtitles or params.has_dynamic
        sub_m = job_manager.create_job(vertical_out, output_path="")
        mix_out = (file_utils.output_path_for(sub_m) if needs_more_after_mix
                   else file_utils.output_path_for(job_id))
        job_manager.update_job(job_id, phase="mix", sub_m=sub_m)

        sound_drop_processor.process(
            sub_m, vertical_out, audio_path, mix_out, mix_params
        )
        _check_subjob(sub_m, "la mezcla de audio")

        base_out = mix_out  # "video base": vertical + texto/marca + mezcla

        # --- Fase fondo dinámico: cutaway. Reemplaza tramos del fondo por la
        #     caída SIN tocar el audio (la narración/mezcla corre por debajo) ---
        if params.has_dynamic and dynamic_clip_path:
            base_dur = ffmpeg_runner.probe_duration(base_out)
            if base_dur is None:
                raise RuntimeError("No pude leer la duración del video base.")
            positions = [f * base_dur for f in params.dynamic_markers]
            sub_c = job_manager.create_job(base_out, output_path="")
            cut_out = (file_utils.output_path_for(sub_c) if params.has_subtitles
                       else file_utils.output_path_for(job_id))
            job_manager.update_job(job_id, phase="dynamic", sub_c=sub_c)
            insert_processor.process_cutaway(
                sub_c, base_out, dynamic_clip_path, cut_out, positions,
                slice_durations=params.dynamic_slice_durations,
            )
            _check_subjob(sub_c, "el fondo dinámico")
            intermedios.append(base_out)   # el mix deja de ser el base
            base_out = cut_out

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
