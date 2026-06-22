"""Orquestación del módulo reel_express (pipeline).

Encadena los módulos en el servidor, sin rebotes por el browser:

    clip 16:9 ──vertical_convert──┐
                                  ├──sound_drop──▶ [watermark]──▶ reel final
    audio (link│archivo) ─────────┘

La conversión a vertical y la descarga (si la fuente es un link) son
independientes y corren en paralelo; al terminar, se mezcla y, si hay texto o
marca, se aplica el watermark. No hay lógica de FFmpeg propia: reusa los
`process()` de cada módulo vía sub-jobs internos. El progreso se agrega en la
ruta de status leyendo esos sub-jobs.
"""
import threading

from core import file_utils, job_manager
from modules.reel_express.schema import ReelExpressParams
from modules.vertical_convert import processor as vertical_processor
from modules.downloader import processor as downloader_processor
from modules.sound_drop import processor as sound_drop_processor
from modules.watermark import processor as watermark_processor


def _check_subjob(sub_id: str, etapa: str) -> dict:
    """Lee un sub-job terminado; si falló, propaga el error con nombre de etapa."""
    sub = job_manager.get_job(sub_id)
    if not sub or sub["status"] == "error":
        detalle = (sub or {}).get("error") or "error desconocido"
        raise RuntimeError(f"Falló en {etapa}: {detalle}")
    return sub


def process(job_id: str, clip_path: str, params: ReelExpressParams, *,
            audio_path: str | None = None) -> None:
    """Ejecuta el pipeline completo (bloqueante). Reporta al job del pipeline.

    `audio_path` viene seteado cuando la fuente es un archivo subido; cuando es
    un link queda None y se completa con la descarga.
    """
    intermedios: list[str] = [clip_path]
    if audio_path:
        intermedios.append(audio_path)
    try:
        job_manager.update_job(job_id, status="processing", progress=0)

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
        # Con watermark, la mezcla escribe un intermedio; sin él, el reel final.
        sub_m = job_manager.create_job(vertical_out, output_path="")
        mix_out = (file_utils.output_path_for(sub_m) if params.has_watermark
                   else file_utils.output_path_for(job_id))
        job_manager.update_job(job_id, phase="mix", sub_m=sub_m)

        sound_drop_processor.process(
            sub_m, vertical_out, audio_path, mix_out, params.sound_drop
        )
        _check_subjob(sub_m, "la mezcla de audio")

        final_out = mix_out

        # --- Fase watermark: texto + marca sobre la mezcla (opcional) ---
        if params.has_watermark:
            intermedios.append(mix_out)
            sub_w = job_manager.create_job(mix_out, output_path="")
            final_out = file_utils.output_path_for(job_id)
            job_manager.update_job(job_id, phase="watermark", sub_w=sub_w)
            watermark_processor.process(sub_w, mix_out, final_out, params.watermark)
            _check_subjob(sub_w, "el texto y la marca")

        job_manager.update_job(
            job_id, status="done", progress=100, output_path=final_out
        )
        # Limpieza: borra clip + vertical + audio (+ mezcla si hubo watermark);
        # deja sólo el reel final.
        file_utils.cleanup_paths(*intermedios)
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=str(exc))
