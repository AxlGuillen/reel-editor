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
from core import external, ffmpeg_runner, file_utils, job_manager
from modules.editing import effects, panels, timing, titles
from modules.editing.edl import Edl, Piece, Segment

INTERMEDIATE_CRF = 14
PARALLEL = 3
INTERPOLATE_BELOW = 0.6       # with RIFE, pieces slower than this are interpolated
LIMITER = "alimiter=limit=0.89:level=0:latency=1"
OUTPUT = "format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
WATERMARK_MARGIN = 14



# A shot's geometry (Segment.vf) sees the time of its input, which starts at
# that shot's -ss. A geometry that needs SOURCE time (competitivo's split
# camera ranges) writes `(t+SOURCE_T0)` and each shot puts its -ss there.
SOURCE_T0 = "__SOURCE_T0__"


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
                with_audio: bool, source_fps: float, source_size: tuple[int, int] | None = None) -> list[str]:
    """Command for one shot: geometry + retimed pieces → .mkv intermediate.
    `source_size` is needed for Segment.mblur (it blurs the source frames)."""
    step = 1.0 / (source_fps or 30)
    a = min(p.start for p in seg.pieces)
    b = max(max(p.end, p.start + 2 * step) for p in seg.pieces)
    ss = max(0.0, a - 0.05)
    cmd = [config.FFMPEG_PATH, "-y", "-ss", f"{ss:.3f}", "-t", f"{b - ss + 0.3:.3f}", "-i", seg.source]
    k = len(seg.pieces)
    geo = (seg.vf or vf_cover(width, height)).replace(SOURCE_T0, f"{ss:.4f}")
    # A graph that already names its input ([0:v]…, like competitivo's layout)
    # goes as-is; otherwise the input is prepended. Its last chain must stay
    # open: the retime is appended here.
    if seg.mblur:
        if not source_size:
            raise ValueError("El desenfoque de movimiento necesita el tamaño de la fuente.")
        blur = effects.mblur(seg.mblur, source_size)
        geo = (geo.replace("[0:v]", f"[0:v]{blur},", 1) if geo.startswith("[0:v]") else f"{blur},{geo}")
    head = geo if geo.startswith("[") else f"[0:v]{geo}"
    tint = f",{effects.TINTS[seg.tint]}" if seg.tint else ""
    parts = [f"{head}{tint},setsar=1,format=yuv420p,split={k}" + "".join(f"[p{i}]" for i in range(k))]
    for i, p in enumerate(seg.pieces):
        rel = p.start - ss
        n = _n(p.dur, fps)
        if p.frozen:
            src = f"trim=start={rel:.4f}:duration={2 * step:.4f},setpts=PTS-STARTPTS"
        else:
            zero = f"{rel:.6f}/TB" if seg.exact_phase else "STARTPTS"
            src = (f"trim=start={rel:.4f}:end={rel + (p.end - p.start):.4f},"
                   f"setpts=(PTS-{zero})/{p.speed:.6f}")
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
                intensity: float = 1.0, watermark_crop: tuple | None = None,
                subtitles_ass: str | None = None, panels: dict | None = None) -> list[str]:
    """Pass 2: joins the shots and applies color, accents, titles and music.
    `subtitles_ass`: the "invert" subtitles (edl.subtitle_words), under the titles.
    `panels`: {id(event): frame pattern} of the panel events (editing.panels.frames)."""
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
    panel_events = [e for e in edl.events if e.kind == "panel"]
    panel_inputs = []
    for e in panel_events:
        cmd += ["-framerate", str(edl.fps), "-start_number", "0", "-i", panels[id(e)]]
        panel_inputs.append(idx)
        idx += 1

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
    # the panels go over the frozen frame before the accents: a flash whitens them too
    for j, (e, k) in enumerate(zip(panel_events, panel_inputs)):
        start = effects.panel_start(e, edl.fps)
        # in the sequence's own time base (1/fps) half a frame rounds away: the
        # panel came out a frame early. AVTB (µs) keeps the offset.
        parts.append(f"[{k}:v]format=rgba,settb=AVTB,setpts=PTS-STARTPTS+{start:.6f}/TB[pn{j}]")
        parts.append(effects.panel(current, f"pn{j}", f"pv{j}", e, edl.fps))
        current = f"pv{j}"
    if any(e.kind == "glow" for e in edl.events):
        parts.append(effects.glow_pulse(current, "glowp", edl.events, W, H, intensity, edl.fps))
        current = "glowp"
    accents = [f for f in (effects.flash(edl.events, intensity),
                           effects.flash_mix(edl.events, edl.fps, intensity),
                           effects.strobe(edl.events, edl.fps, intensity),
                           effects.camera(edl.events, W, H, edl.fps, intensity),
                           effects.whip_blur(edl.events, intensity),
                           effects.rgb(edl.events, intensity, edl.fps),
                           effects.invert(edl.events, edl.fps),
                           effects.blackout(edl.events, edl.fps)) if f]
    if edl.vignette:
        accents.append(effects.VIGNETTE)
    from modules.subtitles.processor import ass_filter
    if subtitles_ass:
        accents.append(ass_filter(subtitles_ass))      # a whole chain (difference blend)
    if ass:
        accents.append(ass_filter(ass))
    if edl.video_fade_out > 0:
        accents.append(effects.fade_out(dur, edl.video_fade_out))
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
    if edl.uses_original:
        vol = effects.gain(edl.original_gain) if edl.original_gain else f"volume={edl.original_volume:.3f}"
        parts.append(f"[0:a]{fmt},{vol}[orig]")
        sources.append("[orig]")
    if music_idx is not None:
        fade = min(edl.fade_out, dur / 3)
        vol = effects.gain(edl.music_gain) if edl.music_gain else f"volume={edl.music_volume:.3f}"
        fades = (f"afade=t=in:d={edl.music_fade_in:.4f}," if edl.music_fade_in > 0 else "") + \
                (f"afade=t=out:st={max(0.0, dur - fade):.4f}:d={fade:.4f}," if fade > 0 else "")
        parts.append(f"[{music_idx}:a]{fmt},atrim=0:{dur:.4f},asetpts=PTS-STARTPTS,{fades}{vol}[mus]")
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


def _subtitles(edl: Edl) -> str | None:
    """The .ass of edl.subtitle_words with the subtitles "invert" layout (the
    same generator as Subtítulos / Reel Express), or None."""
    if not edl.subtitle_words:
        return None
    from modules.subtitles import processor as subtitles
    from modules.subtitles.schema import SubtitlesParams
    params = SubtitlesParams(layout="invert", **edl.subtitle_style)
    words = [{"word": w["word"], "start": float(w["start"]), "end": float(w["end"])}
             for w in edl.subtitle_words if str(w.get("word") or "").strip()]
    if not words:
        return None
    return subtitles._write_ass(subtitles._build_invert_ass([words], params, until=edl.subtitle_until,
                                                            windows=edl.subtitle_windows or None))


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
        source, _ = external.interpolate(source, start, end, factor, work, dedupe=seg.dedupe)
    pieces = [Piece(p.start - a, p.end - a, p.dur) for p in seg.pieces]
    return Segment(source, pieces, seg.label, seg.vf, False, seg.tint, seg.exact_phase, seg.mblur)


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
    ass = subs = None
    try:
        def one(i: int) -> str:
            seg = edl.segments[i]
            sub = os.path.join(work, f"pre_{i:03d}")
            ready = _prepare(seg, sub, interpolate, upscale_below)
            if ready is not seg:
                used["rife" if ready.source.endswith("interpolated.mp4") else "esrgan"] += 1
            source_fps = ffmpeg_runner.probe_fps(ready.source) or 30.0
            with_audio = edl.uses_original and ffmpeg_runner.has_audio_stream(ready.source)
            path = os.path.join(work, f"shot_{i:03d}.mkv")
            size = ffmpeg_runner.probe_resolution(ready.source) if ready.mblur else None
            ffmpeg_runner.capture(build_pass1(ready, path, edl.width, edl.height, edl.fps, with_audio, source_fps,
                                              size), timeout=1800, cwd=config.BASE_DIR)
            shutil.rmtree(sub, ignore_errors=True)
            return path

        report(f"Planos: 0/{len(edl.segments)}…", lo)
        # Not a `with` block: on error it would wait for EVERY queued shot (up to
        # 1800 s each) before the job saw the failure. Cancel what hasn't started.
        ex = ThreadPoolExecutor(max_workers=PARALLEL)
        try:
            futures = {ex.submit(one, i): i for i in range(len(edl.segments))}
            for fut in futures:
                intermediates[futures[fut]] = fut.result()
                done += 1
                report(f"Planos: {done}/{len(edl.segments)}…",
                       lo + int((mid - lo) * done / len(edl.segments)))
        except BaseException:
            ex.shutdown(wait=True, cancel_futures=True)
            raise
        ex.shutdown()

        concat_list = os.path.join(work, "list.txt")
        with open(concat_list, "w", encoding="utf-8") as fh:
            for path in intermediates:
                path = path.replace("\\", "/").replace("'", "'\\''")
                fh.write(f"file '{path}'\n")
        ass = titles.write(edl.texts, edl.width, edl.height, text_y, accent)
        subs = _subtitles(edl)
        prepared = {id(e): panels.frames(e, edl.width, edl.height, edl.fps, os.path.join(work, "panels"))
                    for e in edl.events if e.kind == "panel"}
        report("Color, acentos y música…", mid)
        cmd = build_final(concat_list, edl, out, ass, intensity, watermark_crop, subs, prepared)
        if job_id:
            ffmpeg_runner.run(cmd, job_id, total_duration=edl.dur, cwd=config.BASE_DIR,
                              progress_range=(mid, hi))
        else:
            ffmpeg_runner.capture(cmd, timeout=3600, cwd=config.BASE_DIR)
    finally:
        # On Windows the .ass can still be held (Defender, the indexer): a
        # PermissionError here hid the real error and skipped the rmtree, which
        # left GBs of intermediates behind.
        if ass or subs:
            file_utils.cleanup_paths(*(p for p in (ass, subs) if p))
        shutil.rmtree(work, ignore_errors=True)
    return {"dur": round(timing.frame_count(edl) / edl.fps, 3), "shots": len(edl.segments),
            "rife": used["rife"], "esrgan": used["esrgan"]}
