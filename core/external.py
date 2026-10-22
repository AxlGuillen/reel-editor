"""Optional external binaries: RIFE (frame interpolation) and Real-ESRGAN (upscaling).

Both are ncnn-vulkan builds: a Python-free executable that runs on the GPU
(NVIDIA, AMD, Intel and Apple) and works on folders of PNGs. This is the only
place that runs them (like ffmpeg_runner with FFmpeg). If they are not
installed nothing breaks: `rife_available()` / `esrgan_available()` return
False and the edits fall back to the usual slow motion (repeated frames) and
lanczos scaling.

Install (once): download the release zip of each one
  https://github.com/nihui/rife-ncnn-vulkan/releases
  https://github.com/xinntao/Real-ESRGAN/releases  (realesrgan-ncnn-vulkan)
and put the folder on the PATH or point REELFORGE_RIFE / REELFORGE_REALESRGAN
at the executable.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess

import config
from core import ffmpeg_runner

ESRGAN_MODEL = "realesr-animevideov3"     # the anime one: fast, no "oil painting" look


def _exists(path: str) -> bool:
    return bool(path) and (os.path.isfile(path) or bool(shutil.which(path)))


def rife_available() -> bool:
    return _exists(config.RIFE_PATH)


def esrgan_available() -> bool:
    return _exists(config.REALESRGAN_PATH)


def _run(cmd: list[str], timeout: float = 3600) -> None:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           cwd=os.path.dirname(cmd[0]) or None)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"{os.path.basename(cmd[0])} no pudo correr: {exc}")
    if r.returncode != 0:
        tail = r.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
        raise RuntimeError(f"{os.path.basename(cmd[0])} falló:\n" + "\n".join(tail))


def _extract(src: str, start: float, end: float, folder: str) -> int:
    """Frames of [start, end] as numbered PNGs; returns how many."""
    os.makedirs(folder, exist_ok=True)
    ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-y", "-ss", f"{start:.3f}",
                           "-t", f"{max(0.05, end - start):.3f}", "-i", src, "-an",
                           "-fps_mode", "passthrough", os.path.join(folder, "%08d.png")], timeout=1800)
    return len(glob.glob(os.path.join(folder, "*.png")))


def _assemble(folder: str, fps: float, out: str) -> str:
    ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-y", "-framerate", f"{fps:.6f}",
                           "-i", os.path.join(folder, "%08d.png"), "-c:v", "libx264", "-crf", "12",
                           "-preset", "veryfast", "-pix_fmt", "yuv420p", out], timeout=1800)
    return out


def interpolate(src: str, start: float, end: float, factor: int, work: str) -> tuple[str, float]:
    """[start, end] of `src` with `factor` times more frames (RIFE).

    Returns (file, new fps). The file lasts as long as the span: when it is
    slowed down later with setpts, the invented frames are what makes the
    slow motion smooth. The span starts at 0 (start → 0)."""
    fps = ffmpeg_runner.probe_fps(src) or 24.0
    frames_in, frames_out = os.path.join(work, "rife_in"), os.path.join(work, "rife_out")
    shutil.rmtree(frames_out, ignore_errors=True)
    os.makedirs(frames_out)
    n = _extract(src, start, end, frames_in)
    if n < 2:
        raise RuntimeError("El tramo a interpolar tiene menos de 2 cuadros.")
    _run([config.RIFE_PATH, "-i", frames_in, "-o", frames_out, "-n", str(n * factor), "-f", "%08d.png"])
    out = _assemble(frames_out, fps * factor, os.path.join(work, "interpolated.mp4"))
    shutil.rmtree(frames_in, ignore_errors=True)
    shutil.rmtree(frames_out, ignore_errors=True)
    return out, fps * factor


def upscale(src: str, start: float, end: float, work: str, scale: int = 2) -> str:
    """[start, end] of `src` upscaled ×`scale` with Real-ESRGAN (anime model).
    The span starts at 0."""
    fps = ffmpeg_runner.probe_fps(src) or 24.0
    frames_in, frames_out = os.path.join(work, "esrgan_in"), os.path.join(work, "esrgan_out")
    shutil.rmtree(frames_out, ignore_errors=True)
    os.makedirs(frames_out)
    if _extract(src, start, end, frames_in) < 1:
        raise RuntimeError("El tramo a escalar no tiene cuadros.")
    _run([config.REALESRGAN_PATH, "-i", frames_in, "-o", frames_out, "-n", ESRGAN_MODEL,
          "-s", str(scale), "-f", "png"])
    out = _assemble(frames_out, fps, os.path.join(work, "upscaled.mp4"))
    shutil.rmtree(frames_in, ignore_errors=True)
    shutil.rmtree(frames_out, ignore_errors=True)
    return out
