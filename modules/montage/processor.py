"""Montage: animated layers (flyers, slides from presentations) over a finished
vertical video, in ONE encode, with the subtitles on top if requested.

Each layer:
  -itsoffset start -i layer     the clip starts at its second
  scale / format=yuva420p       to its box, with its transparency (ProRes 4444,
                                VP9 with alpha or PNG); an opaque mp4 shows as
                                a rectangle (the "black flash" of suiza)
  tpad=stop_mode=clone          freezes on its last frame until `end`
                                (no separate PNG needed anymore)
  fade alpha in/out             fade in and fade out
  overlay enable=between        only inside its interval, eof_action=pass

"band" mode (the Lux one): the layer centered at the band's height, over a
full-width copy of itself, blurred and darkened, that hides what is behind.
The base video's audio is copied as-is.
"""
from __future__ import annotations

import os

import config
from core import ffmpeg_runner, file_utils, job_manager
from modules.montage.layers import Layer
from modules.montage.schema import MontageParams
from modules.subtitles import processor as subtitles

W, H = config.OUTPUT_WIDTH, config.OUTPUT_HEIGHT
_IMAGE = (".png", ".jpg", ".jpeg", ".webp")


def _x(box, w_expr: str = "w") -> str:
    return {"left": "0", "right": f"W-{w_expr}"}.get(box.x, f"(W-{w_expr})/2")


def build_command(video: str, layers: list[Layer], out: str, ass: str | None = None) -> list[str]:
    """A single encode: the base video + each layer at its time + subtitles."""
    cmd = [config.FFMPEG_PATH, "-y", "-i", video]
    for c in layers:
        dur = c.end - c.start
        if c.file.lower().endswith(_IMAGE):
            cmd += ["-loop", "1", "-framerate", "30", "-t", f"{dur + 0.1:.3f}"]
        cmd += ["-itsoffset", f"{c.start:.3f}", "-i", c.file]
    parts = []
    current = "0:v"
    for k, c in enumerate(layers, 1):
        hold = max(0.0, (c.end - c.start) - (c.clip_dur or 0)) + 0.2
        fades = [f"fade=t=in:st={c.start:.3f}:d={c.fade_in:.3f}:alpha=1"] if c.fade_in else []
        if c.fade_out:
            fades.append(f"fade=t=out:st={c.end - c.fade_out:.3f}:d={c.fade_out:.3f}:alpha=1")
        tail = ",".join(["format=yuva420p", f"tpad=stop_mode=clone:stop_duration={hold:.3f}", *fades])
        enable = f"enable='between(t,{c.start:.3f},{c.end:.3f})':eof_action=pass"
        box = c.box
        if box.mode == "band":
            parts += [
                f"[{k}:v]split[c{k}a][c{k}b]",
                f"[c{k}a]scale={W}:{box.height}:force_original_aspect_ratio=increase,crop={W}:{box.height},"
                f"gblur=sigma=22,eq=brightness=-0.3,{tail}[f{k}]",
                f"[c{k}b]scale=-2:{box.height}:flags=lanczos,{tail}[l{k}]",
                f"[{current}][f{k}]overlay=0:{box.y}:{enable}[b{k}]",
                f"[b{k}][l{k}]overlay={_x(box)}:{box.y}:{enable}[v{k}]",
            ]
        else:
            parts += [
                f"[{k}:v]scale={box.width}:-2:flags=lanczos,{tail}[l{k}]",
                f"[{current}][l{k}]overlay={_x(box)}:H-h-{box.bottom}:{enable}[v{k}]",
            ]
        current = f"v{k}"
    final = [subtitles.ass_filter(ass)] if ass else []
    parts.append(f"[{current}]" + ",".join(final + ["format=yuv420p"]) + "[vout]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "0:a?",
            *ffmpeg_runner.video_encode_flags(), "-c:a", "copy", "-movflags", "+faststart", out]
    return cmd


def prepare(params: MontageParams, video_dur: float, segments: list[dict] | None) -> list[Layer]:
    """Layers with their times and each clip's duration."""
    layers = params.resolved_layers(segments, video_dur)
    for c in layers:
        if not c.file.lower().endswith(_IMAGE):
            c.clip_dur = ffmpeg_runner.probe_duration(c.file)
    return layers


def process(job_id: str, video_path: str, params: MontageParams, segments: list[dict] | None = None,
            cleanup: list[str] | None = None) -> None:
    ass = None
    try:
        job_manager.update_job(job_id, status="processing", progress=2, stage="Leyendo el video…")
        dur = ffmpeg_runner.probe_duration(video_path)
        if not dur:
            raise RuntimeError("No se pudo leer la duración del video.")
        if (params.needs_transcript or params.subtitles) and not segments:
            job_manager.update_job(job_id, progress=5, stage="Transcribiendo para ubicar las palabras…")
            segments = subtitles.transcribe(video_path, params.sub_params)
        layers = prepare(params, dur, segments)
        if params.subtitles:
            if not segments:
                raise RuntimeError("Para quemar subtítulos hacen falta los segments (o transcribir).")
            ass = subtitles.prepare_ass(segments, params.sub_params)
        out = file_utils.output_path_for(job_id)
        job_manager.update_job(job_id, progress=30, stage=f"Montando {len(layers)} capa(s)…",
                               layers=[{"file": os.path.basename(c.file), "start": c.start,
                                        "end": c.end, "id": c.label} for c in layers])
        ffmpeg_runner.run(build_command(video_path, layers, out, ass), job_id, total_duration=dur,
                          cwd=config.BASE_DIR, progress_range=(30, 100))
        job_manager.update_job(job_id, status="done", progress=100, stage=None, output_path=out)
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
    finally:
        if ass and os.path.exists(ass):
            os.remove(ass)
        file_utils.cleanup_paths(*(cleanup or []))
