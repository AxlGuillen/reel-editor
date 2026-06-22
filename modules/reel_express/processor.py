"""Orquestación del módulo reel_express (pipeline).

Encadena los módulos en el servidor, sin rebotes por el browser:

    clip 16:9 ──vertical_convert──┐
                                  ├──sound_drop──▶ [watermark]──▶ [subtítulos] ──▶ reel final
    audio (link│archivo) ─────────┘

La conversión a vertical y la descarga (si la fuente es un link) son
independientes y corren en paralelo; al terminar, se mezcla y, si hay texto o
marca, se aplica el watermark. Eso produce el "video base".

Subtítulos (opcional, default ON) parte el flujo en DOS fases, porque el usuario
revisa/edita el texto en el medio:
  - Fase 1 (process): produce el video base y, si hay subs, lo transcribe y
    deja los segmentos en el job (status done + segments). El video base
    sobrevive para la fase 2.
  - Fase 2 (finish): con los segmentos editados, quema los subtítulos sobre el
    base → reel final. Los subs van últimos, así quedan encima de todo.

No hay lógica de FFmpeg propia: reusa los `process()`/helpers de cada módulo.
"""
import threading

from core import file_utils, job_manager
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

        # Tras la mezcla queda algo más por hacer (watermark y/o subs)?
        needs_more_after_mix = params.has_watermark or params.has_subtitles

        # --- Fase prep: vertical, y (si la fuente es link) descarga en paralelo ---
        sub_v = job_manager.create_job(clip_path, output_path="")
        vertical_out = file_utils.output_path_for(sub_v)
        sub_d = None
        if params.audio_source == "url":
            sub_d = job_manager.create_job(params.downloader.url, output_path="")
        job_manager.update_job(job_id, phase="prep", sub_v=sub_v, sub_d=sub_d)

        t_v = threading.Thread(
            target=vertical_processor.process,
            args=(sub_v, clip_path, vertical_out, params.vertical),
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

        _check_subjob(sub_v, "la conversión a vertical")
        if sub_d:
            audio_path = _check_subjob(sub_d, "la descarga del audio")["output_path"]
            intermedios.append(audio_path)
        intermedios.append(vertical_out)

        # --- Fase mix: audio sobre el vertical ---
        sub_m = job_manager.create_job(vertical_out, output_path="")
        mix_out = (file_utils.output_path_for(sub_m) if needs_more_after_mix
                   else file_utils.output_path_for(job_id))
        job_manager.update_job(job_id, phase="mix", sub_m=sub_m)

        sound_drop_processor.process(
            sub_m, vertical_out, audio_path, mix_out, params.sound_drop
        )
        _check_subjob(sub_m, "la mezcla de audio")

        base_out = mix_out  # "video base": vertical + mezcla (+ watermark)

        # --- Fase watermark: texto + marca sobre la mezcla (opcional) ---
        if params.has_watermark:
            intermedios.append(mix_out)
            sub_w = job_manager.create_job(mix_out, output_path="")
            base_out = (file_utils.output_path_for(sub_w) if params.has_subtitles
                        else file_utils.output_path_for(job_id))
            job_manager.update_job(job_id, phase="watermark", sub_w=sub_w)
            watermark_processor.process(sub_w, mix_out, base_out, params.watermark)
            _check_subjob(sub_w, "el texto y la marca")

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
