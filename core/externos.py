"""Binarios externos opcionales: RIFE (interpolación) y Real-ESRGAN (escalado).

Los dos son builds ncnn-vulkan: un ejecutable sin Python que corre en la GPU
(NVIDIA, AMD, Intel y Apple) y trabaja sobre carpetas de PNG. Este es el único
lugar que los ejecuta (como ffmpeg_runner con FFmpeg). Si no están instalados
nada se rompe: `rife_disponible()` / `esrgan_disponible()` dan False y los
edits siguen con la cámara lenta de siempre (cuadros repetidos) y el escalado
lanczos.

Instalación (una vez): bajar el zip de la release de cada uno
  https://github.com/nihui/rife-ncnn-vulkan/releases
  https://github.com/xinntao/Real-ESRGAN/releases  (realesrgan-ncnn-vulkan)
y poner la carpeta en el PATH o apuntar REELFORGE_RIFE / REELFORGE_REALESRGAN
al ejecutable.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess

import config
from core import ffmpeg_runner

ESRGAN_MODELO = "realesr-animevideov3"     # el de anime: rápido y sin "pintura al óleo"


def _existe(path: str) -> bool:
    return bool(path) and (os.path.isfile(path) or bool(shutil.which(path)))


def rife_disponible() -> bool:
    return _existe(config.RIFE_PATH)


def esrgan_disponible() -> bool:
    return _existe(config.REALESRGAN_PATH)


def _correr(cmd: list[str], timeout: float = 3600) -> None:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           cwd=os.path.dirname(cmd[0]) or None)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"{os.path.basename(cmd[0])} no pudo correr: {exc}")
    if r.returncode != 0:
        cola = r.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
        raise RuntimeError(f"{os.path.basename(cmd[0])} falló:\n" + "\n".join(cola))


def _extraer(src: str, desde: float, hasta: float, carpeta: str) -> int:
    """Cuadros de [desde, hasta] como PNG numerados; devuelve cuántos."""
    os.makedirs(carpeta, exist_ok=True)
    ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-y", "-ss", f"{desde:.3f}",
                           "-t", f"{max(0.05, hasta - desde):.3f}", "-i", src, "-an",
                           "-fps_mode", "passthrough", os.path.join(carpeta, "%08d.png")], timeout=1800)
    return len(glob.glob(os.path.join(carpeta, "*.png")))


def _armar(carpeta: str, fps: float, out: str) -> str:
    ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-y", "-framerate", f"{fps:.6f}",
                           "-i", os.path.join(carpeta, "%08d.png"), "-c:v", "libx264", "-crf", "12",
                           "-preset", "veryfast", "-pix_fmt", "yuv420p", out], timeout=1800)
    return out


def interpolar(src: str, desde: float, hasta: float, factor: int, trabajo: str) -> tuple[str, float]:
    """[desde, hasta] de `src` con `factor` veces más cuadros (RIFE).

    Devuelve (archivo, fps nuevo). El archivo dura lo mismo que el tramo: al
    ralentizarlo después con setpts, los cuadros inventados son los que hacen
    que la cámara lenta sea fluida. El tramo arranca en 0 (desde → 0)."""
    fps = ffmpeg_runner.probe_fps(src) or 24.0
    entrada, salida = os.path.join(trabajo, "rife_in"), os.path.join(trabajo, "rife_out")
    shutil.rmtree(salida, ignore_errors=True)
    os.makedirs(salida)
    n = _extraer(src, desde, hasta, entrada)
    if n < 2:
        raise RuntimeError("El tramo a interpolar tiene menos de 2 cuadros.")
    _correr([config.RIFE_PATH, "-i", entrada, "-o", salida, "-n", str(n * factor), "-f", "%08d.png"])
    out = _armar(salida, fps * factor, os.path.join(trabajo, "interpolado.mp4"))
    shutil.rmtree(entrada, ignore_errors=True)
    shutil.rmtree(salida, ignore_errors=True)
    return out, fps * factor


def escalar(src: str, desde: float, hasta: float, trabajo: str, escala: int = 2) -> str:
    """[desde, hasta] de `src` escalado ×`escala` con Real-ESRGAN (modelo de
    anime). El tramo arranca en 0."""
    fps = ffmpeg_runner.probe_fps(src) or 24.0
    entrada, salida = os.path.join(trabajo, "esrgan_in"), os.path.join(trabajo, "esrgan_out")
    shutil.rmtree(salida, ignore_errors=True)
    os.makedirs(salida)
    if _extraer(src, desde, hasta, entrada) < 1:
        raise RuntimeError("El tramo a escalar no tiene cuadros.")
    _correr([config.REALESRGAN_PATH, "-i", entrada, "-o", salida, "-n", ESRGAN_MODELO,
             "-s", str(escala), "-f", "png"])
    out = _armar(salida, fps, os.path.join(trabajo, "escalado.mp4"))
    shutil.rmtree(entrada, ignore_errors=True)
    shutil.rmtree(salida, ignore_errors=True)
    return out
