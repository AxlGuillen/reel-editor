"""Archivos sintéticos para los tests de los edits (sin internet ni material
con derechos): una canción de bombo y hi-hat con el tempo conocido, y videos
de planos de colores con los cortes en segundos conocidos."""
import os
import subprocess
import wave

import numpy as np

import config

SR = 22050


def senal(bpm: float = 120, dur: float = 40, alto_desde: float = 16,
          primer_beat: float = 0.25) -> np.ndarray:
    """Bombo en el 1 de cada compás, hi-hat en el resto; antes de `alto_desde`
    todo suena al 30 % (intro) y después a tope (coro: el drop)."""
    y = np.zeros(int(SR * dur), np.float32)
    per = 60 / bpm
    tt = np.arange(int(SR * 0.12)) / SR
    bombo = (np.sin(2 * np.pi * 55 * tt) * np.exp(-tt * 30)).astype(np.float32)
    hat = (np.random.default_rng(0).standard_normal(len(tt)) * np.exp(-tt * 80)).astype(np.float32) * 0.3
    k = 0
    while True:
        t0 = primer_beat + k * per
        i = int(t0 * SR)
        if i + len(tt) >= len(y):
            break
        g = 0.3 if t0 < alto_desde else 1.0
        y[i:i + len(tt)] += (bombo if k % 4 == 0 else hat) * g
        k += 1
    return y


def cancion(path: str, **kw) -> str:
    """WAV mono 16 bits con senal(**kw)."""
    y = senal(**kw)
    pcm = (np.clip(y, -1, 1) * 32000).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


COLORES = ("red", "green", "blue", "yellow", "magenta", "cyan", "white", "orange")


def video_planos(path: str, duraciones: list[float], w: int = 640, h: int = 360,
                 fps: int = 24, con_audio: bool = True) -> str:
    """Video con un plano de color (y un cuadrado que se mueve) por duración:
    los cortes caen en las sumas acumuladas de `duraciones`."""
    entradas, filtros = [], []
    for i, d in enumerate(duraciones):
        color = COLORES[i % len(COLORES)]
        entradas += ["-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:r={fps}:d={d}"]
        # Un cuadrado gris que cruza el plano: hay movimiento para medir.
        filtros.append(f"[{i}:v]drawbox=x='mod(t*{40 + 30 * i},{w - 60})':y={h // 3}:w=60:h=60:"
                       f"color=gray:t=fill,setsar=1[v{i}]")
    n = len(duraciones)
    grafo = ";".join(filtros) + ";" + "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    cmd = [config.FFMPEG_PATH, "-v", "error", "-y", *entradas]
    total = sum(duraciones)
    if con_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={total}"]
    cmd += ["-filter_complex", grafo, "-map", "[v]"]
    if con_audio:
        cmd += ["-map", f"{n}:a", "-c:a", "aac", "-b:a", "96k"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p", path]
    subprocess.run(cmd, check=True)
    return path


def frames_gris(path: str, fps: float, w: int = 32, h: int = 32) -> np.ndarray:
    """Cuadros en gris (n, h, w) a `fps`, para medir brillo y cortes."""
    raw = subprocess.run([config.FFMPEG_PATH, "-v", "error", "-i", path, "-vf",
                          f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32)


def tmpdir(prefijo: str) -> str:
    import tempfile
    return tempfile.mkdtemp(prefix=f"reelforge-{prefijo}-")


def borrar(path: str) -> None:
    import shutil
    if path and os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
