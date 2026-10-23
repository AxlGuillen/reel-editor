"""Beatmap module orchestration: song → beatmap.json (+ a preview to check it).

`get()` is what the other modules use (anime_edit, competitivo/edit): it
analyzes each song once and caches the result in cache/beatmaps/ by the file
fingerprint, so every variant or new edit with the same song does not analyze
it again.

The preview is a black video with the song, a beep on every beat (higher on
the first of each bar), a flash per beat and a bar at the bottom with the
section color (orange for the chorus, blue for the rest). It is for checking by
eye and ear that the grid lands where the music hits BEFORE rendering an edit.
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
from modules.beatmap import analysis, lyrics as lyrics_mod
from modules.beatmap.schema import BeatmapParams, LyricsParams
from modules.downloader import processor as downloader

PREVIEW_W, PREVIEW_H = 540, 960
PREVIEW_SR = 44100


def fingerprint(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:20]


def _cache_path(key: str) -> str:
    os.makedirs(config.BEATMAPS_FOLDER, exist_ok=True)
    return os.path.join(config.BEATMAPS_FOLDER, f"{key}.json")


def get(path: str, engine: str = "auto", lyrics: LyricsParams | None = None,
        job_id: str | None = None) -> dict:
    """Beatmap of the song at `path` (from the cache if it was analyzed the same way)."""
    lyrics = lyrics or LyricsParams()
    audio_fp = fingerprint(path)
    key = f"{audio_fp}-{engine}-v{analysis.VERSION}"
    if lyrics.lyrics:
        key += f"-lyrics-{lyrics.language}-{lyrics.model}-{'vocals' if lyrics.separate_vocals else 'mix'}"
    cache = _cache_path(key)
    if os.path.isfile(cache):
        with open(cache, encoding="utf-8") as fh:
            return json.load(fh)
    if job_id:
        job_manager.update_job(job_id, stage="Analizando el ritmo…")
    bm = analysis.analyze(analysis.load_audio(path), engine=engine, path=path)
    bm["source"] = os.path.basename(path)
    bm["fingerprint"] = audio_fp
    if lyrics.lyrics:
        folder = tempfile.mkdtemp(prefix="beatmap-vocals-")
        try:
            vocals = None
            if lyrics.separate_vocals:
                if job_id:
                    job_manager.update_job(job_id, stage="Separando la voz…")
                vocals = lyrics_mod.isolate_vocals(path, folder)
            if job_id:
                job_manager.update_job(job_id, stage="Transcribiendo la letra…")
            bm["words"] = lyrics_mod.words(vocals or path, lyrics.language, lyrics.model)
            bm["lyrics_from"] = "vocals" if vocals else "mix"
        finally:
            shutil.rmtree(folder, ignore_errors=True)
    with open(cache, "w", encoding="utf-8") as fh:
        json.dump(bm, fh, ensure_ascii=False)
    return bm


def download_music(job_id: str, params) -> str:
    """Downloads the music from a link (yt-dlp, like the Downloader) and returns the path."""
    sub = job_manager.create_job(params.url, output_path="")
    job_manager.update_job(job_id, stage="Bajando la música…")
    downloader.process(sub, params)
    done = job_manager.get_job(sub) or {}
    if done.get("status") != "done" or not done.get("output_path"):
        raise RuntimeError(f"No se pudo bajar la música: {done.get('error') or 'error desconocido'}")
    return done["output_path"]


def summary(bm: dict) -> dict:
    """What is shown of the beatmap, without the long lists."""
    return {
        "bpm": bm["bpm"], "engine": bm["engine"], "duration": bm["duration"],
        "beats": len(bm["beats"]), "bars": len(bm["downbeats"]),
        "sections": bm["sections"], "drop": bm["drop"], "buildups": bm["buildups"],
        "words": len(bm.get("words") or []), "lyrics_from": bm.get("lyrics_from"),
    }


# --- Preview ------------------------------------------------------------------------
def write_beeps(bm: dict, path: str, sr: int = PREVIEW_SR) -> str:
    """WAV with a short beep on every beat (1 kHz) and a higher one on the
    first of each bar (1.6 kHz)."""
    dur = bm["duration"]
    y = np.zeros(int(sr * dur) + sr, np.float32)
    t = np.arange(int(sr * 0.05)) / sr
    env = np.exp(-t * 60)
    high = {round(d, 3) for d in bm["downbeats"]}
    for b in bm["beats"]:
        f = 1600 if round(b, 3) in high else 1000
        i = int(b * sr)
        y[i:i + len(t)] += (np.sin(2 * np.pi * f * t) * env * 0.5).astype(np.float32)
    pcm = (np.clip(y, -1, 1) * 32000).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def _enable(spans: list[tuple[float, float]]) -> str:
    return ffmpeg_runner.expr_sum([f"between(t,{a:.3f},{b:.3f})" for a, b in spans])


def build_preview_command(audio: str, beeps: str, bm: dict, out: str) -> list[str]:
    """Preview video: a flash per beat, a red square on each bar, the section
    bar at the bottom and the drop in yellow."""
    dur = bm["duration"]
    beats = [(b, b + 0.06) for b in bm["beats"]]
    bars = [(d, d + 0.12) for d in bm["downbeats"]]
    choruses = [(s["start"], s["end"]) for s in bm["sections"] if s["type"] == "chorus"]
    others = [(s["start"], s["end"]) for s in bm["sections"] if s["type"] != "chorus"]
    drop = [(bm["drop"], bm["drop"] + 0.4)] if bm.get("drop") is not None else []
    w, h = PREVIEW_W, PREVIEW_H
    vf = ",".join([
        f"drawbox=x=0:y=0:w={w}:h={h}:color=white@0.35:t=fill:enable='{_enable(beats)}'",
        f"drawbox=x={w // 4}:y={h // 3}:w={w // 2}:h={w // 2}:color=red@0.9:t=fill:enable='{_enable(bars)}'",
        f"drawbox=x=0:y={h - 60}:w={w}:h=60:color=orange:t=fill:enable='{_enable(choruses)}'",
        f"drawbox=x=0:y={h - 60}:w={w}:h=60:color=0x2E5F94:t=fill:enable='{_enable(others)}'",
        f"drawbox=x=0:y=0:w={w}:h={h}:color=yellow@0.8:t=fill:enable='{_enable(drop)}'",
    ])
    return [config.FFMPEG_PATH, "-y",
            "-f", "lavfi", "-i", f"color=c=black:s={w}x{h}:r=30:d={dur:.3f}",
            "-i", audio, "-i", beeps,
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
    downloaded = None
    beeps = None
    try:
        job_manager.update_job(job_id, status="processing", progress=2, stage="Preparando…")
        if params.music:
            audio_path = downloaded = download_music(job_id, params.music)
        job_manager.update_job(job_id, status="processing", progress=20)
        bm = get(audio_path, params.engine, params.lyrics, job_id=job_id)
        json_path = file_utils.output_path_for(job_id, "json")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(bm, fh, ensure_ascii=False, indent=1)
        output = ""
        if params.preview:
            job_manager.update_job(job_id, progress=70, stage="Armando el preview…")
            beeps = os.path.join(config.UPLOAD_FOLDER, f"{job_id}_beeps.wav")
            write_beeps(bm, beeps)
            output = file_utils.output_path_for(job_id)
            ffmpeg_runner.run(build_preview_command(audio_path, beeps, bm, output), job_id,
                              total_duration=bm["duration"], progress_range=(70, 99))
        job_manager.update_job(job_id, status="done", progress=100, stage=None,
                               output_path=output, json_path=json_path,
                               beatmap=bm, summary=summary(bm))
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
    finally:
        file_utils.cleanup_paths(beeps, downloaded)
        if audio_path and audio_path.startswith(config.UPLOAD_FOLDER):
            file_utils.cleanup_paths(audio_path)
