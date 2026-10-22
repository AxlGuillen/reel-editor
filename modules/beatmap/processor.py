"""Orquestación del módulo beatmap: canción → beatmap.json (+ preview para validar).

`obtener()` es lo que usan los otros módulos (anime_edit, competitivo/edit):
analiza una vez por canción y cachea el resultado en cache/beatmaps/ por la
huella del archivo, así cada variante o cada edit nuevo con la misma canción
no la vuelve a analizar.

El preview es un video negro con la canción, un pitido en cada beat (agudo en
el primero de cada compás) y un destello por beat, más una barra abajo con el
color de la sección (naranja el coro, azul lo demás). Sirve para validar a ojo
y oído que la rejilla cae donde pega la música ANTES de renderizar un edit.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import wave

import numpy as np

import config
from core import ffmpeg_runner, file_utils, job_manager
from modules.beatmap import analisis, letra as letra_mod
from modules.beatmap.schema import BeatmapParams, LetraParams
from modules.downloader import processor as downloader

PREVIEW_W, PREVIEW_H = 540, 960
SR_PREVIEW = 44100


def huella(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for bloque in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()[:20]


def _cache_path(clave: str) -> str:
    os.makedirs(config.BEATMAPS_FOLDER, exist_ok=True)
    return os.path.join(config.BEATMAPS_FOLDER, f"{clave}.json")


def obtener(path: str, motor: str = "auto", letra: LetraParams | None = None,
            job_id: str | None = None) -> dict:
    """beatmap de la canción en `path` (del cache si ya se analizó con lo mismo)."""
    letra = letra or LetraParams()
    huella_audio = huella(path)
    clave = f"{huella_audio}-{motor}"
    if letra.letra:
        clave += f"-letra-{letra.idioma}-{letra.modelo}-{'voz' if letra.separar_voz else 'mezcla'}"
    cache = _cache_path(clave)
    if os.path.isfile(cache):
        with open(cache, encoding="utf-8") as fh:
            return json.load(fh)
    if job_id:
        job_manager.update_job(job_id, stage="Analizando el ritmo…")
    bm = analisis.analizar(analisis.cargar_audio(path), motor=motor, ruta=path)
    bm["fuente"] = os.path.basename(path)
    bm["huella"] = huella_audio
    if letra.letra:
        carpeta = tempfile.mkdtemp(prefix="beatmap-voz-")
        try:
            voz = None
            if letra.separar_voz:
                if job_id:
                    job_manager.update_job(job_id, stage="Separando la voz…")
                voz = letra_mod.separar_voz(path, carpeta)
            if job_id:
                job_manager.update_job(job_id, stage="Transcribiendo la letra…")
            bm["palabras"] = letra_mod.palabras(voz or path, letra.idioma, letra.modelo)
            bm["letra_de"] = "voz separada" if voz else "mezcla"
        finally:
            shutil.rmtree(carpeta, ignore_errors=True)
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump(bm, fh, ensure_ascii=False)
    return bm


def bajar_musica(job_id: str, params) -> str:
    """Baja la música de un link (yt-dlp, como el Downloader) y devuelve la ruta."""
    sub = job_manager.create_job(params.url, output_path="")
    job_manager.update_job(job_id, stage="Bajando la música…")
    downloader.process(sub, params)
    hecho = job_manager.get_job(sub) or {}
    if hecho.get("status") != "done" or not hecho.get("output_path"):
        raise RuntimeError(f"No se pudo bajar la música: {hecho.get('error') or 'error desconocido'}")
    return hecho["output_path"]


def resumen(bm: dict) -> dict:
    """Lo que se muestra del beatmap sin las listas largas."""
    return {
        "bpm": bm["bpm"], "motor": bm["motor"], "duracion": bm["duracion"],
        "beats": len(bm["beats"]), "compases": len(bm["downbeats"]),
        "secciones": bm["secciones"], "drop": bm["drop"], "subidas": bm["subidas"],
        "palabras": len(bm.get("palabras") or []), "letra_de": bm.get("letra_de"),
    }


# --- Preview ------------------------------------------------------------------------
def escribir_pitidos(bm: dict, path: str, sr: int = SR_PREVIEW) -> str:
    """WAV con un pitido corto en cada beat (1 kHz) y uno más agudo en el
    primero de cada compás (1.6 kHz)."""
    dur = bm["duracion"]
    y = np.zeros(int(sr * dur) + sr, np.float32)
    t = np.arange(int(sr * 0.05)) / sr
    env = np.exp(-t * 60)
    agudos = {round(d, 3) for d in bm["downbeats"]}
    for b in bm["beats"]:
        f = 1600 if round(b, 3) in agudos else 1000
        i = int(b * sr)
        y[i:i + len(t)] += (np.sin(2 * np.pi * f * t) * env * 0.5).astype(np.float32)
    pcm = (np.clip(y, -1, 1) * 32000).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def _enable(intervalos: list[tuple[float, float]]) -> str:
    if not intervalos:
        return "0"
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in intervalos)


def build_preview_command(audio: str, pitidos: str, bm: dict, out: str) -> list[str]:
    """Video del preview: destello por beat, cuadro rojo en cada compás, barra
    de sección abajo y el drop en amarillo."""
    dur = bm["duracion"]
    beats = [(b, b + 0.06) for b in bm["beats"]]
    compases = [(d, d + 0.12) for d in bm["downbeats"]]
    coros = [(s["inicio"], s["fin"]) for s in bm["secciones"] if s["tipo"] == "coro"]
    otros = [(s["inicio"], s["fin"]) for s in bm["secciones"] if s["tipo"] != "coro"]
    drop = [(bm["drop"], bm["drop"] + 0.4)] if bm.get("drop") is not None else []
    w, h = PREVIEW_W, PREVIEW_H
    vf = ",".join([
        f"drawbox=x=0:y=0:w={w}:h={h}:color=white@0.35:t=fill:enable='{_enable(beats)}'",
        f"drawbox=x={w // 4}:y={h // 3}:w={w // 2}:h={w // 2}:color=red@0.9:t=fill:enable='{_enable(compases)}'",
        f"drawbox=x=0:y={h - 60}:w={w}:h=60:color=orange:t=fill:enable='{_enable(coros)}'",
        f"drawbox=x=0:y={h - 60}:w={w}:h=60:color=0x2E5F94:t=fill:enable='{_enable(otros)}'",
        f"drawbox=x=0:y=0:w={w}:h={h}:color=yellow@0.8:t=fill:enable='{_enable(drop)}'",
    ])
    return [config.FFMPEG_PATH, "-y",
            "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r=30:d={dur:.3f}",
            "-i", audio, "-i", pitidos,
            "-filter_complex",
            f"[0:v]{vf},format=yuv420p[v];"
            "[1:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.6[m];"
            "[2:a]aresample=48000,aformat=channel_layouts=stereo[b];"
            "[m][b]amix=inputs=2:normalize=0:duration=first[a]",
            "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out]


# --- Job ----------------------------------------------------------------------------
def process(job_id: str, audio_path: str | None, params: BeatmapParams) -> None:
    descargado = None
    pitidos = None
    try:
        job_manager.update_job(job_id, status="processing", progress=2, stage="Preparando…")
        if params.musica:
            audio_path = descargado = bajar_musica(job_id, params.musica)
        job_manager.update_job(job_id, status="processing", progress=20)
        bm = obtener(audio_path, params.motor, params.letra, job_id=job_id)
        json_path = file_utils.output_path_for(job_id, "json")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(bm, fh, ensure_ascii=False, indent=1)
        salida = ""
        if params.preview:
            job_manager.update_job(job_id, progress=70, stage="Armando el preview…")
            pitidos = os.path.join(config.UPLOAD_FOLDER, f"{job_id}_pitidos.wav")
            escribir_pitidos(bm, pitidos)
            salida = file_utils.output_path_for(job_id)
            ffmpeg_runner.run(build_preview_command(audio_path, pitidos, bm, salida), job_id,
                              total_duration=bm["duracion"], progress_range=(70, 99))
        job_manager.update_job(job_id, status="done", progress=100, stage=None,
                               output_path=salida, json_path=json_path,
                               beatmap=bm, resumen=resumen(bm))
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
    finally:
        file_utils.cleanup_paths(pitidos, descargado)
        if audio_path and audio_path.startswith(config.UPLOAD_FOLDER):
            file_utils.cleanup_paths(audio_path)
