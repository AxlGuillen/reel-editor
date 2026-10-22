"""Orquestación de anime_edit: un video (una pelea de anime) + una canción →
varias variantes de edit sincronizadas.

  Fase 1  process()  baja lo que llegue por link, analiza la canción (beatmap,
                     con la letra si se pide), detecta los planos del video y
                     arma miniaturas y hojas de cuadros numeradas. Propone una
                     selección (los de más puntaje, en orden) y un héroe.
                     Queda en stage "esperando_seleccion": es la pausa en la
                     que Claude mira las hojas (o el usuario la galería) y
                     decide qué planos van, en qué orden y cuál es el héroe.
  Fase 2  finish()   una EDL por variante (plan.py) y su render (edicion), una
                     detrás de otra. Los videos quedan en EDITS_FOLDER.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

import config
from core import ffmpeg_runner, file_utils, job_manager
from modules.anime_edit import plan, planos as planos_mod
from modules.anime_edit.schema import AnimeEditParams, FinishParams
from modules.beatmap import processor as beatmap
from modules.competitivo import layout, salida
from modules.downloader import processor as downloader
from modules.edicion import render

TRABAJO = os.path.join(config.OUTPUT_FOLDER, "anime_edit")
PROPUESTA_MAX = 24

_SESIONES: dict[str, dict] = {}


def _bajar_video(job_id: str, params) -> str:
    sub = job_manager.create_job(params.url, output_path="")
    job_manager.update_job(job_id, stage="Bajando el video…")
    downloader.process(sub, params)
    hecho = job_manager.get_job(sub) or {}
    if hecho.get("status") != "done" or not hecho.get("output_path"):
        raise RuntimeError(f"No se pudo bajar el video: {hecho.get('error') or 'error desconocido'}")
    return hecho["output_path"]


def propuesta(planos: list[dict], n: int = PROPUESTA_MAX) -> tuple[list[int], int | None]:
    """Los `n` planos de más puntaje en orden de aparición, y el héroe (el mejor)."""
    if not planos:
        return [], None
    mejores = sorted(planos, key=lambda p: -p["puntaje"])[:n]
    return [p["id"] for p in sorted(mejores, key=lambda p: p["desde"])], mejores[0]["id"]


def process(job_id: str, video_path: str | None, musica_path: str | None,
            params: AnimeEditParams, conservar_video: bool = False) -> None:
    temporales = []
    try:
        carpeta = os.path.join(TRABAJO, job_id)
        os.makedirs(carpeta, exist_ok=True)
        job_manager.update_job(job_id, status="processing", progress=2, stage="Preparando…")
        if params.video:
            video_path = _bajar_video(job_id, params.video)
            temporales.append(video_path)
        if params.musica:
            musica_path = beatmap.bajar_musica(job_id, params.musica)
            temporales.append(musica_path)
        if params.musica_del_video:
            musica_path = video_path
        if not ffmpeg_runner.probe_resolution(video_path):
            raise RuntimeError("No se pudo leer el video.")
        job_manager.update_job(job_id, progress=10)
        bm = beatmap.obtener(musica_path, params.motor, params.letra, job_id=job_id)
        job_manager.update_job(job_id, progress=40, stage="Buscando los planos del video…")
        planos = planos_mod.detectar(video_path)
        job_manager.update_job(job_id, progress=75, stage="Armando las hojas de cuadros…")
        planos_mod.miniaturas(video_path, planos, carpeta)
        hojas = planos_mod.hojas(carpeta, len(planos))
        ids, heroe = propuesta(planos)
        _SESIONES[job_id] = {"video": video_path, "musica": musica_path, "bm": bm, "planos": planos,
                             "carpeta": carpeta, "hojas": hojas, "params": params,
                             "temporales": temporales, "conservar_video": conservar_video}
        job_manager.update_job(
            job_id, status="done", progress=100, stage="esperando_seleccion", finished=False,
            planos=planos, propuesta=ids, heroe=heroe, hojas=len(hojas),
            beatmap=beatmap.resumen(bm), video_dur=ffmpeg_runner.probe_duration(video_path),
        )
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
        file_utils.cleanup_paths(*temporales)


def _carpeta_salida(fp: FinishParams) -> str:
    nombre = salida.limpiar(fp.titulo) or datetime.now().strftime("%Y-%m-%d %H-%M edit")
    carpeta = os.path.join(config.EDITS_FOLDER, nombre)
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def finish(job_id: str, fp: FinishParams) -> None:
    sesion = _SESIONES.get(job_id)
    try:
        if not sesion:
            raise RuntimeError("La sesión ya no existe (¿se reinició el servidor?). Volvé a analizar el video.")
        por_id = {p["id"]: p for p in sesion["planos"]}
        ids = fp.seleccion or job_manager.get_job(job_id).get("propuesta") or list(por_id)
        elegidos = [por_id[i] for i in ids]
        carpeta = _carpeta_salida(fp)
        base = salida.limpiar(fp.titulo) or "edit"
        marca = os.path.join(config.WATERMARKS_FOLDER, fp.watermark) if fp.watermark else None
        res = (ffmpeg_runner.probe_resolution(sesion["video"]) or (0, 0))
        hechas = []
        n = len(fp.variantes)
        for k, variante in enumerate(fp.variantes):
            lo, hi = int(100 * k / n), int(100 * (k + 1) / n)
            job_manager.update_job(job_id, status="processing", progress=lo, finished=False,
                                   stage=f"Variante {k + 1}/{n} ({variante}): planeando…")
            op = plan.Opciones(formato=fp.formato, fps=fp.fps, duracion_s=fp.duracion_s,
                               musica_desde=fp.musica_desde, texto=fp.texto, look=fp.look,
                               heroe=fp.heroe, interpolar=fp.interpolar)
            edl, info = plan.planear(sesion["video"], elegidos, sesion["bm"], variante, op)
            edl.musica = sesion["musica"]
            edl.lut = fp.lut
            edl.volumen_original = fp.volumen_original / 100
            edl.marca, edl.marca_alto = marca, fp.marca_alto
            destino = salida._unico(carpeta, f"{base} - {variante}.mp4")
            hecho = render.render(edl, destino, job_id, os.path.join(sesion["carpeta"], f"render_{k}"),
                                  progreso=(lo, hi), intensidad=fp.intensidad, texto_y=0.5,
                                  marca_recorte=layout.recorte_marca(marca) if marca else None,
                                  interpolar=fp.interpolar,
                                  escalar_hasta=1080 if fp.escalar and res[1] < 1080 else 0)
            hechas.append({**info, "video": destino, "nombre": os.path.basename(destino),
                           "duracion": hecho["dur"], "rife": hecho["rife"], "esrgan": hecho["esrgan"],
                           "edl": edl.a_dict()})
        with open(os.path.join(carpeta, f"{base}.json"), "w", encoding="utf-8") as fh:
            json.dump({"creado": datetime.now(timezone.utc).isoformat(), "seleccion": ids, "heroe": fp.heroe,
                       "beatmap": beatmap.resumen(sesion["bm"]), "variantes": hechas}, fh,
                      ensure_ascii=False, indent=2)
        resultado = {"carpeta": carpeta, "variantes": [{k: v for k, v in h.items() if k != "edl"} for h in hechas]}
        job_manager.update_job(job_id, status="done", progress=100, stage="listo", finished=True,
                               resultado=resultado, output_path=hechas[0]["video"] if hechas else "")
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))


def descartar(job_id: str) -> None:
    """Borra lo temporal de una sesión (miniaturas, descargas)."""
    sesion = _SESIONES.pop(job_id, None)
    if not sesion:
        return
    shutil.rmtree(sesion["carpeta"], ignore_errors=True)
    file_utils.cleanup_paths(*sesion["temporales"])
