"""The pasarela's FFmpeg commands (pure builders: processor.py runs them).

Three passes, so the expensive one runs once and the final one is light:

1. BASE (cached by fingerprint): the gameplay to 9:16 with vertical_convert's
   own graph (HUD plate + main_clip_offset). From Insights it is one pass too:
   each kill window is its own seeked input (only the cuts are decoded) and
   the concat feeds the vertical graph.
2. BAND: the slides and the rest slide flattened into ONE 1080x680 track,
   one small FFmpeg per piece (the stretch between two slide entries: the
   rest slide underneath, the previous slide while the new one fades in, the
   new one with its fades), lossless FFV1 with alpha, then joined with the
   concat demuxer. No filter ever holds ~30 alpha WebMs at once.
3. FINAL: base + band + clips + watermark + the voice cut at the pauses, one
   h264_nvenc encode (p7, cq 16, AAC 256k).
"""
from __future__ import annotations

import config
from core import ffmpeg_runner
from modules.montage import processor as montage
from modules.pasarela import insights
from modules.pasarela.schema import BAND_H
from modules.subtitles import processor as subtitles
from modules.vertical_convert import processor as vertical
from modules.vertical_convert.schema import VerticalConvertParams

W, H = config.OUTPUT_WIDTH, config.OUTPUT_HEIGHT
BAND_FPS = 30               # the deck's frame rate (laminas.mjs)
OUT_FPS = 60
WM_HEIGHT = 150
WM_MARGIN = 70              # from the frame's edge on the free side
CLIP_FADE_IN_S = 0.05       # the clip's audio, so it does not click
CLIP_FADE_OUT_S = 0.12
ENCIMA_GAIN = 0.22          # mode "encima": the clip's audio under the voice
LIMIT = 0.891               # alimiter: −1 dBFS
OFFSET_ABAJO = -10          # % (Cumplelolero's band format: gameplay 297–1239, band from 1240)
HUD_GAP_PX = 2              # band arriba: the HUD plate starts this far under the band


# ------------------------------------------------------------------ geometry

def hud_plate_top(params: VerticalConvertParams, src_w: int, src_h: int) -> float:
    """y of the HUD plate's top edge with offset 0 (vertical_convert's math:
    the region cropped, scaled to hud_scale % of the width, plus its border)."""
    crop_w = int(src_w * (params.hud_right - params.hud_left) / 100)
    crop_h = int(src_h * (params.hud_bottom - params.hud_top) / 100)
    plate_w = vertical._even(W * params.hud_scale / 100)
    plate_h = ffmpeg_even(plate_w * crop_h / max(1, crop_w)) + 2 * vertical.HUD_BORDER
    return (H - plate_h) * params.hud_pos_y / 100


def ffmpeg_even(v: float) -> int:
    """scale=w:-2: the other side rounded to the nearest even number."""
    return int(v / 2 + 0.5) * 2


def vertical_params(band: str, src_w: int = 1920, src_h: int = 1080) -> VerticalConvertParams:
    """The base's vertical settings. Band abajo: offset −10 % (the gameplay and
    the HUD plate go up; the gameplay ends at y=1239 and the band starts at
    1240). Band arriba, the mirror: the offset that puts the HUD plate's top
    HUD_GAP_PX under the band (y=680), so the gameplay ends near y=1690 and the
    bottom 230 px stay free for the watermark. For a 1920x1080 source that is
    +13.5 % (+259 px): plate 682–763, gameplay 748–1690."""
    p = VerticalConvertParams(hud=True)
    if band == "abajo":
        p.main_clip_offset = OFFSET_ABAJO
    else:
        px = BAND_H + HUD_GAP_PX - hud_plate_top(p, src_w, src_h)
        p.main_clip_offset = round(px / H * 100, 2)
    return p


def watermark_y(band: str) -> int:
    """Centered, WM_MARGIN from the edge on the side the band leaves free."""
    return WM_MARGIN if band == "abajo" else H - WM_MARGIN - WM_HEIGHT


# ---------------------------------------------------------------- 1. base

def base_command(out: str, params: VerticalConvertParams, file: str | None = None,
                 recording: str | None = None, windows: list[dict] | None = None,
                 gpu: bool = False) -> list[str]:
    """The vertical base: from a file, or from the windows of a recording."""
    cmd = [config.FFMPEG_PATH, "-y"]
    if windows:
        cmd += insights.window_inputs(recording, windows, hwaccel=gpu)
        graph = insights.concat_graph(windows) + ";" + \
            vertical.build_filter_complex(params, src="[hlv]", out="[vout]", head="null")
        audio = "[hla]"
    else:
        cmd += (["-hwaccel", "cuda"] if gpu else []) + ["-i", file]
        graph = vertical.build_filter_complex(params, src="[0:v]", out="[vout]")
        audio = "0:a?"
    return cmd + ["-filter_complex", graph, "-map", "[vout]", "-map", audio,
                  *ffmpeg_runner.video_encode_flags(crf=16), "-c:a", "aac", "-b:a", "192k", out]


def highlight_command(out: str, recording: str, windows: list[dict], gpu: bool = False) -> list[str]:
    """A 16:9 highlight on its own (like Insights' own): the windows joined."""
    return [config.FFMPEG_PATH, "-y", *insights.window_inputs(recording, windows, hwaccel=gpu),
            "-filter_complex", insights.concat_graph(windows), "-map", "[hlv]", "-map", "[hla]",
            *ffmpeg_runner.video_encode_flags(crf=19), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]


# ---------------------------------------------------------------- 2. band

def decoder_args(path: str, stream: dict | None) -> list[str]:
    """How to read a slide: libvpx for a WebM with alpha (FFmpeg's own VP9
    decoder drops the alpha, see montage). An opaque H.264 export (`alfa:
    false`) is read on the CPU on purpose: each band piece is a short FFmpeg
    and opening CUDA costs more than decoding 1080x680 (Tom, 28 pieces: 11.2 s
    with NVDEC, 10.0 s without). NVDEC is used where it pays: the 1080x1920
    base in the base and final passes (60 s of base: 2.3 s against 3.1 s)."""
    codec = (stream or {}).get("codec")
    if codec in montage._ALPHA_DECODERS:
        return ["-c:v", montage._ALPHA_DECODERS[codec]]
    return []


def band_pieces(layers: list[dict], duration: float, rest_dur: float | None) -> list[dict]:
    """The band cut where each slide comes in. Each piece: {start, frames,
    rest_ss, prev: {id, ss, dur} | None, cur: {id, dur, fade_in, fade_out_at,
    fade_out} | None}. Clips are not in the band track (the final pass puts
    them on top), so where one plays the band shows the rest slide."""
    slides = sorted((l for l in layers if l["kind"] == "lamina"), key=lambda l: l["start"])
    cuts = sorted({0.0, *[s["start"] for s in slides if s["start"] < duration]})
    total = round(duration * BAND_FPS)
    pieces = []
    for k, a in enumerate(cuts):
        b = cuts[k + 1] if k + 1 < len(cuts) else duration
        frames = min(total, round(b * BAND_FPS)) - round(a * BAND_FPS)
        if frames <= 0:
            continue
        cur = next((s for s in slides if abs(s["start"] - a) < 1e-6), None)
        prev = next((s for s in reversed(slides) if s["start"] < a - 1e-6), None)
        # The rest slide plays once from second 0 (under the cover) and then stays on its
        # last frame; a still image (no rest slide in the deck) is read from its start.
        rest_ss = min(a, max(0.0, rest_dur - 1.0 / BAND_FPS)) if rest_dur else 0.0
        piece = {"start": a, "frames": frames, "rest_ss": round(rest_ss, 3), "prev": None, "cur": None}
        if prev and prev["end"] > a + 1e-6:
            piece["prev"] = {"id": prev["id"], "ss": round(a - prev["start"], 3),
                             "dur": round(min(prev["end"], b) - a, 3)}
        if cur:
            vis = min(cur["end"], b) - a
            fade_out = cur["fade_out"] if cur["end"] <= b + 1e-6 else 0.0
            piece["cur"] = {"id": cur["id"], "dur": round(vis, 3), "fade_in": cur["fade_in"],
                            "fade_out": fade_out, "fade_out_at": round(vis - fade_out, 3)}
        pieces.append(piece)
    return pieces


# A band piece is stored losslessly. Opaque slides (`alfa: false`): H.264 NVENC
# lossless, yuv420p. With alpha (or without a GPU): FFV1, yuva420p. Measured on
# Tom's longest piece (536 frames): the graph alone 0.25 s, + FFV1 1.05 s,
# + NVENC lossless 0.43 s.
LOSSLESS_NVENC = ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "lossless"]
LOSSLESS_FFV1 = ["-c:v", "ffv1", "-level", "3", "-slices", "16", "-g", "1"]


def piece_command(piece: dict, files: dict[str, str], decoders: dict[str, list[str]],
                  rest: str, out: str, opaque: bool = False) -> list[str]:
    """One piece of the band, lossless (see LOSSLESS_NVENC)."""
    n = piece["frames"]
    d = n / BAND_FPS
    norm = f"setpts=PTS-STARTPTS,fps={BAND_FPS},format=yuva420p"
    cmd = [config.FFMPEG_PATH, "-y", "-v", "error"]
    still = files[rest].lower().endswith((".png", ".jpg", ".jpeg"))
    cmd += [*decoders[rest], *(["-loop", "1"] if still else []), "-ss", f"{piece['rest_ss']:.3f}", "-i", files[rest]]
    parts = [f"[0:v]{norm},tpad=stop_mode=clone:stop_duration={d + 1:.3f},trim=end_frame={n}[base0]"]
    current, k = "base0", 1
    if piece["prev"]:
        p = piece["prev"]
        cmd += [*decoders[p["id"]], "-ss", f"{p['ss']:.3f}", "-i", files[p["id"]]]
        parts.append(f"[{k}:v]{norm},tpad=stop_mode=clone:stop_duration={p['dur'] + 1:.3f},"
                     f"trim=duration={p['dur']:.3f}[p]")
        parts.append(f"[{current}][p]overlay=0:0:eof_action=pass:format=auto[base{k}]")
        current, k = f"base{k}", k + 1
    if piece["cur"]:
        c = piece["cur"]
        cmd += [*decoders[c["id"]], "-i", files[c["id"]]]
        fades = f",fade=t=in:st=0:d={c['fade_in']:.3f}:alpha=1" if c["fade_in"] else ""
        if c["fade_out"]:
            fades += f",fade=t=out:st={c['fade_out_at']:.3f}:d={c['fade_out']:.3f}:alpha=1"
        parts.append(f"[{k}:v]{norm},tpad=stop_mode=clone:stop_duration={c['dur'] + 1:.3f},"
                     f"trim=duration={c['dur']:.3f}{fades}[c]")
        parts.append(f"[{current}][c]overlay=0:0:eof_action=pass:format=auto[base{k}]")
        current = f"base{k}"
    parts.append(f"[{current}]format={'yuv420p' if opaque else 'yuva420p'}[out]")
    return cmd + ["-filter_complex", ";".join(parts), "-map", "[out]", "-frames:v", str(n), "-an",
                  *(LOSSLESS_NVENC if opaque else LOSSLESS_FFV1), out]


# ---------------------------------------------------------------- 3. final

def final_command(*, base: str, base_loop: bool, band_list: str, band_y: int, voice: str,
                  duration: float, voice_pieces: list[dict], clips: list[dict],
                  watermark: str | None, wm_y: int, gameplay_volume: float, ass: str | None,
                  out: str, gpu_decode: bool) -> list[str]:
    """`clips`: [{file, start, end, full, mode, gain, audio}] in final time."""
    T = duration
    cmd = [config.FFMPEG_PATH, "-y"]
    cmd += (["-hwaccel", "cuda"] if gpu_decode else []) + (["-stream_loop", "-1"] if base_loop else []) + \
        ["-i", base]
    cmd += ["-f", "concat", "-safe", "0", "-i", band_list, "-i", voice]
    parts = [f"[0:v]trim=end={T:.3f},setpts=PTS-STARTPTS[v0]",
             "[1:v]setpts=PTS-STARTPTS[band]",
             f"[v0][band]overlay=0:{band_y}:eof_action=repeat[vb]"]
    current = "vb"
    audio = []
    for k, piece in enumerate(voice_pieces):
        ms = int(round(piece["at"] * 1000))
        parts.append(f"[2:a]atrim={piece['from']:.3f}:{piece['to']:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
                     f"aformat=channel_layouts=stereo,adelay={ms}|{ms}[vz{k}]")
        audio.append(f"[vz{k}]")
    n = 3
    for c in clips:
        cmd += ["-i", c["file"]]
        a, b = c["start"], c["end"]
        if c["full"]:
            fit = f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black"
            xy = "0:0"
        else:
            fit = f"scale={W}:{BAND_H}:force_original_aspect_ratio=decrease"
            xy = f"(W-w)/2:{band_y}+({BAND_H}-h)/2"
        parts.append(f"[{n}:v]{fit},setsar=1,trim=duration={b - a:.3f},setpts=PTS-STARTPTS+{a:.3f}/TB[c{n}]")
        parts.append(f"[{current}][c{n}]overlay={xy}:eof_action=pass:enable='between(t,{a:.3f},{b - 0.001:.3f})'"
                     f"[o{n}]")
        current = f"o{n}"
        if c["audio"]:
            d = b - a
            ms = int(round(a * 1000))
            parts.append(f"[{n}:a]atrim=0:{d:.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
                         f"volume={c['gain']:.4f},afade=t=in:d={CLIP_FADE_IN_S},"
                         f"afade=t=out:st={max(0.0, d - CLIP_FADE_OUT_S):.3f}:d={CLIP_FADE_OUT_S},"
                         f"adelay={ms}|{ms}[ca{n}]")
            audio.append(f"[ca{n}]")
        n += 1
    if gameplay_volume > 0:
        parts.append(f"[0:a]atrim=end={T:.3f},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
                     f"volume={gameplay_volume / 100:.3f}[ga]")
        audio.append("[ga]")
    if watermark:
        cmd += ["-loop", "1", "-framerate", str(OUT_FPS), "-i", watermark]
        hide = [f"between(t,{c['start']:.3f},{c['end']:.3f})" for c in clips if c["full"]]
        enable = f":enable='not({ffmpeg_runner.expr_sum(hide)})'" if hide else ""
        parts.append(f"[{n}:v]scale=-2:{WM_HEIGHT}:flags=lanczos,format=rgba[wm]")
        parts.append(f"[{current}][wm]overlay=(W-w)/2:{wm_y}:shortest=1{enable}[ow]")
        current = "ow"
        n += 1
    burn = [subtitles.ass_filter(ass)] if ass else []
    parts.append(f"[{current}]" + ",".join(burn + ["format=yuv420p"]) + "[v]")
    parts.append("".join(audio) + f"amix=inputs={len(audio)}:duration=longest:normalize=0,"
                 f"atrim=end={T:.3f},alimiter=limit={LIMIT}:level=0[a]")
    return cmd + ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]", "-t", f"{T:.3f}",
                  "-r", str(OUT_FPS), *final_video_flags(), "-c:a", "aac", "-b:a", "256k",
                  "-movflags", "+faststart", out]


def final_video_flags() -> list[str]:
    """High quality: NVENC p7 / cq 16 (libx264 crf 16 without a GPU)."""
    if ffmpeg_runner.video_encode_flags()[1] == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p7", "-rc", "vbr", "-cq", "16", "-b:v", "0"]
    return ["-c:v", "libx264", "-crf", "16", "-preset", "medium"]


def loudness_command(path: str, stream: str = "0:a:0") -> list[str]:
    return [config.FFMPEG_PATH, "-nostats", "-i", path, "-map", stream,
            "-af", "ebur128=peak=true", "-f", "null", "-"]


def concat_list(paths: list[str], list_path: str) -> str:
    with open(list_path, "w", encoding="utf-8") as fh:
        for p in paths:
            fh.write("file '" + p.replace("\\", "/").replace("'", "'\\''") + "'\n")
    return list_path
