"""Renders an EDL in two passes.

  Pass 1, one per shot: the span of the source with its geometry (layout,
  vertical crop…) and its retimed pieces (speed, freezes), each piece snapped
  to the exact frame (trim=end_frame). It outputs a .mkv intermediate with
  high-quality H.264 and PCM audio: with AAC every join shifted the audio a
  few ms (the codec padding), the same thing measured in competitivo when
  joining clips.
  Pass 2, a single one: the shots joined + color (LUT and look) + glow + flash
  + camera (zoom and shake) + RGB + vignette + titles (ASS) + watermark, with
  the music on top. The accents are in OUTPUT time, which is why they live
  here and not in each shot.

Shots render in parallel (3 at a time): each one is short and pass 2 is what
dominates the total time.
"""
from __future__ import annotations

import os
import shutil
from concurrent.futures import ThreadPoolExecutor

import config
from core import external, ffmpeg_runner, job_manager
from modules.editing import effects, timing, titles
from modules.editing.edl import Edl, Piece, Segment

INTERMEDIATE_CRF = 14
PARALLEL = 3
INTERPOLATE_BELOW = 0.6       # with RIFE, pieces slower than this are interpolated
LIMITER = "alimiter=limit=0.89:level=0:latency=1"
OUTPUT = "format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
WATERMARK_MARGIN = 14


def vf_cover(width: int, height: int) -> str:
    """The source fills the frame (crops the excess), centered."""
    return (f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height}")


def vf_vertical_blur(width: int, height: int, scale: float = 1.0) -> str:
    """16:9 inside 9:16: the clip full width (×`scale`) over a blurred, darkened
    copy of itself that fills the frame (like vertical_convert)."""
    fw = int(round(width * scale / 2)) * 2
    return (f"split[vb_a][vb_b];"
            f"[vb_a]scale={width // 4}:{height // 4}:force_original_aspect_ratio=increase,"
            f"crop={width // 4}:{height // 4},gblur=sigma=8,eq=brightness=-0.18,"
            f"scale={width}:{height}[vb_bg];"
            f"[vb_b]scale={fw}:-2:flags=lanczos[vb_fg];"
            f"[vb_bg][vb_fg]overlay=(W-w)/2:(H-h)/2")


def _n(dur: float, fps: int) -> int:
    return max(1, round(dur * fps))


def build_pass1(seg: Segment, out: str, width: int, height: int, fps: int,
                with_audio: bool, source_fps: float) -> list[str]:
    """Command for one shot: geometry + retimed pieces → .mkv intermediate."""
    step = 1.0 / (source_fps or 30)
    a = min(p.start for p in seg.pieces)
    b = max(max(p.end, p.start + 2 * step) for p in seg.pieces)
    ss = max(0.0, a - 0.05)
    cmd = [config.FFMPEG_PATH, "-y", "-ss", f"{ss:.3f}", "-t", f"{b - ss + 0.3:.3f}", "-i", seg.source]
    k = len(seg.pieces)
    geo = seg.vf or vf_cover(width, height)
    # A graph that already names its input ([0:v]…, like competitivo's layout)
    # goes as-is; otherwise the input is prepended. Its last chain must stay
    # open: the retime is appended here.
    head = geo if geo.startswith("[") else f"[0:v]{geo}"
    parts = [f"{head},setsar=1,format=yuv420p,split={k}" + "".join(f"[p{i}]" for i in range(k))]
    for i, p in enumerate(seg.pieces):
        rel = p.start - ss
        n = _n(p.dur, fps)
        if p.frozen:
            src = f"trim=start={rel:.4f}:duration={2 * step:.4f},setpts=PTS-STARTPTS"
        else:
            src = (f"trim=start={rel:.4f}:end={rel + (p.end - p.start):.4f},"
                   f"setpts=(PTS-STARTPTS)/{p.speed:.6f}")
        parts.append(f"[p{i}]{src},fps={fps},tpad=stop_mode=clone:stop_duration={p.dur + 1:.3f},"
                     f"trim=end_frame={n},setpts=PTS-STARTPTS[v{i}]")
    parts.append("".join(f"[v{i}]" for i in range(k)) + f"concat=n={k}:v=1:a=0[vout]")
    if with_audio:
        parts.append("[0:a]aresample=48000,aformat=channel_layouts=stereo,asplit="
                     f"{k}" + "".join(f"[q{i}]" for i in range(k)))
        for i, p in enumerate(seg.pieces):
            rel = p.start - ss
            end = _n(p.dur, fps) / fps
            if p.frozen:
                parts.append(f"[q{i}]atrim=start={rel:.4f}:duration=0.01,asetpts=PTS-STARTPTS,volume=0,"
                             f"apad,atrim=end={end:.4f}[a{i}]")
            else:
                parts.append(f"[q{i}]atrim=start={rel:.4f}:end={rel + (p.end - p.start):.4f},"
                             f"asetpts=PTS-STARTPTS,{ffmpeg_runner.atempo_chain(p.speed)},"
                             f"apad,atrim=end={end:.4f}[a{i}]")
        parts.append("".join(f"[a{i}]" for i in range(k)) + f"concat=n={k}:v=0:a=1[aout]")
        amap = "[aout]"
    else:
        total = sum(_n(p.dur, fps) for p in seg.pieces) / fps
        cmd += ["-f", "lavfi", "-t", f"{total:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
        amap = "1:a"
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", amap,
            *ffmpeg_runner.video_encode_flags(INTERMEDIATE_CRF), "-c:a", "pcm_s16le", out]
    return cmd


def build_final(concat_list: str, edl: Edl, out: str, ass: str | None = None,
                intensity: float = 1.0, watermark_crop: tuple | None = None) -> list[str]:
    """Pass 2: joins the shots and applies color, accents, titles and music."""
    dur = timing.frame_count(edl) / edl.fps
    cmd = [config.FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0", "-i", concat_list]
    idx = 1
    music_idx = watermark_idx = None
    if edl.music:
        cmd += ["-ss", f"{edl.music_start:.3f}", "-i", edl.music]
        music_idx, idx = idx, idx + 1
    if edl.watermark:
        cmd += ["-i", edl.watermark]
        watermark_idx, idx = idx, idx + 1

    W, H = edl.width, edl.height
    parts = []
    chain = [c for c in (effects.color(edl.look, edl.lut),) if c]
    current = "0:v"
    if chain:
        parts.append(f"[{current}]{','.join(chain)}[col]")
        current = "col"
    if edl.glow > 0:
        parts.append(effects.glow(current, "glowed", edl.glow, W, H))
        current = "glowed"
    accents = [f for f in (effects.flash(edl.events, intensity),
                           effects.camera(edl.events, W, H, edl.fps, intensity),
                           effects.rgb(edl.events, intensity)) if f]
    if edl.vignette:
        accents.append("vignette=angle=PI/5")
    if ass:
        from modules.subtitles.processor import ass_filter
        accents.append(ass_filter(ass))
    parts.append(f"[{current}]" + (",".join(accents) + "," if accents else "") + "setsar=1[pre]")
    if watermark_idx is not None:
        if watermark_crop:
            pw, ph, px, py = watermark_crop
            crop = f"crop={pw}:{ph}:{px}:{py},"
        else:
            crop = ""
        parts.append(f"[{watermark_idx}:v]{crop}scale=-2:{edl.watermark_height}:flags=lanczos,format=rgba[wm]")
        parts.append(f"[pre][wm]overlay=W-w-{WATERMARK_MARGIN}:H-h,{OUTPUT}[vout]")
    else:
        parts.append(f"[pre]{OUTPUT}[vout]")

    fmt = "aresample=48000,aformat=channel_layouts=stereo"
    sources = []
    if edl.original_volume > 0:
        parts.append(f"[0:a]{fmt},volume={edl.original_volume:.3f}[orig]")
        sources.append("[orig]")
    if music_idx is not None:
        fade = min(edl.fade_out, dur / 3)
        parts.append(f"[{music_idx}:a]{fmt},atrim=0:{dur:.4f},asetpts=PTS-STARTPTS,"
                     f"afade=t=out:st={max(0.0, dur - fade):.4f}:d={fade:.4f},"
                     f"volume={edl.music_volume:.3f}[mus]")
        sources.append("[mus]")
    if not sources:
        parts.append(f"[0:a]{fmt},volume=0[orig]")
        sources.append("[orig]")
    parts.append(f"{''.join(sources)}amix=inputs={len(sources)}:normalize=0:duration=first,"
                 f"{LIMITER},apad,atrim=0:{dur:.4f}[aout]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "[aout]",
            "-r", str(edl.fps), "-t", f"{dur:.4f}",
            *ffmpeg_runner.video_encode_flags(), "-maxrate", "16M", "-bufsize", "32M",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", out]
    return cmd


def _prepare(seg: Segment, work: str, interpolate: bool, upscale_below: int) -> Segment:
    """RIFE / Real-ESRGAN over the shot's span, when they apply and are installed.
    Returns the shot pointing at the processed file (times from 0)."""
    slowest = min((p.speed for p in seg.pieces if not p.frozen), default=1.0)
    use_rife = interpolate and seg.interpolate and slowest < INTERPOLATE_BELOW and external.rife_available()
    res = ffmpeg_runner.probe_resolution(seg.source)
    use_esrgan = bool(upscale_below and res and res[1] < upscale_below and external.esrgan_available())
    if not (use_rife or use_esrgan):
        return seg
    a = min(p.start for p in seg.pieces)
    b = max(p.end for p in seg.pieces) + 0.1
    source, start, end = seg.source, a, b
    os.makedirs(work, exist_ok=True)
    if use_esrgan:
        source = external.upscale(source, start, end, work)
        start, end = 0.0, b - a
    if use_rife:
        factor = 4 if slowest < 0.3 else 2
        source, _ = external.interpolate(source, start, end, factor, work)
    pieces = [Piece(p.start - a, p.end - a, p.dur) for p in seg.pieces]
    return Segment(source, pieces, seg.label, seg.vf, False)


def render(edl: Edl, out: str, job_id: str | None, work: str, *,
           progress: tuple[int, int] = (0, 100), intensity: float = 1.0,
           text_y: float = 0.5, accent: str = "#FFD640", watermark_crop: tuple | None = None,
           interpolate: bool = False, upscale_below: int = 0) -> dict:
    """Renders the EDL to `out`. `work`: folder for the intermediates (deleted
    at the end). Returns a summary (duration, shots, what was used)."""
    edl.validate()
    timing.quantize(edl)
    os.makedirs(work, exist_ok=True)
    lo, hi = progress
    mid = lo + int((hi - lo) * 0.55)

    def report(stage: str, pct: int) -> None:
        if job_id:
            job_manager.update_job(job_id, status="processing", stage=stage, progress=pct)

    used = {"rife": 0, "esrgan": 0}
    intermediates = [None] * len(edl.segments)
    done = 0
    ass = None
    try:
        def one(i: int) -> str:
            seg = edl.segments[i]
            sub = os.path.join(work, f"pre_{i:03d}")
            ready = _prepare(seg, sub, interpolate, upscale_below)
            if ready is not seg:
                used["rife" if ready.source.endswith("interpolated.mp4") else "esrgan"] += 1
            source_fps = ffmpeg_runner.probe_fps(ready.source) or 30.0
            with_audio = edl.original_volume > 0 and ffmpeg_runner.has_audio_stream(ready.source)
            path = os.path.join(work, f"shot_{i:03d}.mkv")
            ffmpeg_runner.capture(build_pass1(ready, path, edl.width, edl.height, edl.fps, with_audio, source_fps),
                                  timeout=1800, cwd=config.BASE_DIR)
            shutil.rmtree(sub, ignore_errors=True)
            return path

        report(f"Planos: 0/{len(edl.segments)}…", lo)
        with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
            futures = {ex.submit(one, i): i for i in range(len(edl.segments))}
            for fut in futures:
                intermediates[futures[fut]] = fut.result()
                done += 1
                report(f"Planos: {done}/{len(edl.segments)}…",
                       lo + int((mid - lo) * done / len(edl.segments)))

        concat_list = os.path.join(work, "list.txt")
        with open(concat_list, "w", encoding="utf-8") as fh:
            for path in intermediates:
                path = path.replace("\\", "/").replace("'", "'\\''")
                fh.write(f"file '{path}'\n")
        ass = titles.write(edl.texts, edl.width, edl.height, text_y, accent)
        report("Color, acentos y música…", mid)
        cmd = build_final(concat_list, edl, out, ass, intensity, watermark_crop)
        if job_id:
            ffmpeg_runner.run(cmd, job_id, total_duration=edl.dur, cwd=config.BASE_DIR,
                              progress_range=(mid, hi))
        else:
            ffmpeg_runner.capture(cmd, timeout=3600, cwd=config.BASE_DIR)
    finally:
        if ass and os.path.exists(ass):
            os.remove(ass)
        shutil.rmtree(work, ignore_errors=True)
    return {"dur": round(timing.frame_count(edl) / edl.fps, 3), "shots": len(edl.segments),
            "rife": used["rife"], "esrgan": used["esrgan"]}
