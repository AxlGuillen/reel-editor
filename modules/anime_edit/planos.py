"""Los planos del video fuente: dónde corta, cuánto se mueve y qué tan fuerte es.

1. Cortes: PySceneDetect (AdaptiveDetector: compara cada cuadro contra el
   promedio de sus vecinos, así un paneo rápido no cuenta como corte). Si no
   está instalado, el filtro scdet de FFmpeg.
2. Planos de menos de MIN_PLANO_S se funden con el anterior: en anime los
   "impact frames" (1–3 cuadros en blanco, negro o invertidos) parecen cortes
   y no lo son.
3. Medidas de cada plano sobre cuadros chicos a MUESTRAS_FPS: movimiento
   (diferencia entre cuadros), brillo, saturación y destellos. Con eso un
   puntaje 0–100 que ordena la propuesta; la decisión final la toma Claude
   (con la hoja de cuadros) o el usuario.

El anime se anima "en dos" (12 dibujos por segundo sobre 24 fps): medido a 6
cuadros por segundo eso no se confunde con quietud.
"""
from __future__ import annotations

import os
import re

import numpy as np

import config
from core import ffmpeg_runner

MIN_PLANO_S = 0.3
MUESTRAS_FPS = 6
MUESTRA_W, MUESTRA_H = 96, 54
SALTO_DESTELLO = 40          # subida de brillo (0–255) entre muestras que cuenta como destello
POR_HOJA = 48                # 8 × 6
THUMB_W = 320


def _cortes_scenedetect(path: str) -> list[float] | None:
    try:
        from scenedetect import AdaptiveDetector, detect
    except ImportError:
        return None
    escenas = detect(path, AdaptiveDetector(adaptive_threshold=2.5, min_scene_len=4))
    # `seconds` en las versiones nuevas; get_seconds() en las viejas.
    return [float(getattr(a, "seconds", None) or a.get_seconds()) for a, _ in escenas][1:]


def _cortes_ffmpeg(path: str) -> list[float]:
    err = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "info", "-i", path, "-an",
                                 "-vf", "scale=320:-2,scdet=threshold=12", "-f", "null", "-"],
                                timeout=1800, stderr=True).decode("utf-8", "replace")
    return [float(m) for m in re.findall(r"lavfi\.scd\.time:\s*([\d.]+)", err)]


def cortes(path: str) -> list[float]:
    """Segundos donde cambia de plano (sin el 0)."""
    hechos = _cortes_scenedetect(path)
    return sorted(hechos if hechos is not None else _cortes_ffmpeg(path))


def _muestras(path: str) -> np.ndarray:
    """(n, h, w, 3) RGB uint8 a MUESTRAS_FPS."""
    raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-i", path, "-an", "-vf",
                                 f"fps={MUESTRAS_FPS},scale={MUESTRA_W}:{MUESTRA_H}", "-pix_fmt", "rgb24",
                                 "-f", "rawvideo", "-"], timeout=1800)
    return np.frombuffer(raw, np.uint8).reshape(-1, MUESTRA_H, MUESTRA_W, 3)


def medir(muestras: np.ndarray) -> dict[str, np.ndarray]:
    """Series por muestra: movimiento, brillo y saturación."""
    f = muestras.astype(np.float32)
    gris = f.mean(axis=3)
    mx, mn = f.max(axis=3), f.min(axis=3)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0).mean(axis=(1, 2))
    mov = np.zeros(len(gris))
    if len(gris) > 1:
        mov[1:] = np.abs(np.diff(gris, axis=0)).mean(axis=(1, 2))
    return {"movimiento": mov, "brillo": gris.mean(axis=(1, 2)), "saturacion": sat}


def fundir_cortos(bordes: list[float], minimo: float = MIN_PLANO_S) -> list[float]:
    """Bordes [0, c1, …, fin] sin planos más cortos que `minimo` (se funden
    con el anterior; el primero, con el siguiente)."""
    out = [bordes[0]]
    for b in bordes[1:-1]:
        if b - out[-1] >= minimo:
            out.append(b)
    if bordes[-1] - out[-1] < minimo and len(out) > 1:
        out.pop()
    out.append(bordes[-1])
    return out


BRILLO_PLANO = (25, 215)     # fuera de esto el plano es un destello o un fundido


def _puntaje(mov: float, sat: float, destellos: int, dur: float, extremos: float) -> int:
    dur_score = 1.0 if 0.8 <= dur <= 3.5 else max(0.0, 1 - abs(dur - 2) / 6)
    p = 100 * (0.5 * mov + 0.2 * sat + 0.15 * min(1.0, destellos / 3) + 0.15 * dur_score)
    # Un plano que es sobre todo blanco o negro (impact frames, fundidos) "se
    # mueve" mucho al entrar y salir, pero no es un momento para mostrar.
    if extremos > 0.3:
        p *= 0.3
    return int(round(p))


def planos_de(bordes: list[float], series: dict[str, np.ndarray]) -> list[dict]:
    """Un dict por plano con sus medidas (puro: lo prueban los tests)."""
    mov, bri, sat = series["movimiento"], series["brillo"], series["saturacion"]
    ref_mov = float(np.percentile(mov, 95)) if len(mov) else 1.0
    out = []
    for i, (a, b) in enumerate(zip(bordes, bordes[1:]), 1):
        i0, i1 = int(a * MUESTRAS_FPS) + 1, max(int(b * MUESTRAS_FPS), int(a * MUESTRAS_FPS) + 2)
        m = mov[i0:i1]
        s = sat[i0 - 1:i1]
        br = bri[i0 - 1:i1]
        destellos = int((np.diff(br) > SALTO_DESTELLO).sum()) if len(br) > 1 else 0
        movimiento = float(m.mean() / ref_mov) if len(m) and ref_mov > 0 else 0.0
        pico = a + (int(np.argmax(m)) + 1) / MUESTRAS_FPS if len(m) else (a + b) / 2
        out.append({
            "id": i, "desde": round(a, 3), "hasta": round(b, 3), "dur": round(b - a, 3),
            "pico": round(min(max(pico, a), b), 3),
            "movimiento": round(min(movimiento, 2.0), 3),
            "brillo": round(float(br.mean()) if len(br) else 0, 1),
            "saturacion": round(float(s.mean()) if len(s) else 0, 3),
            "destellos": destellos,
            "puntaje": _puntaje(min(movimiento, 1.5) / 1.5, float(s.mean()) if len(s) else 0, destellos, b - a,
                                float(((br < BRILLO_PLANO[0]) | (br > BRILLO_PLANO[1])).mean()) if len(br) else 0),
        })
    return out


def detectar(path: str) -> list[dict]:
    """Planos del video con sus medidas."""
    dur = ffmpeg_runner.probe_duration(path)
    if not dur:
        raise RuntimeError("No se pudo leer la duración del video.")
    bordes = fundir_cortos([0.0] + [c for c in cortes(path) if 0 < c < dur] + [dur])
    return planos_de(bordes, medir(_muestras(path)))


def miniaturas(path: str, planos: list[dict], carpeta: str) -> None:
    """Un JPG por plano, en su momento de más movimiento (plano_NNNN.jpg)."""
    os.makedirs(carpeta, exist_ok=True)
    for p in planos:
        ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-y", "-ss", f"{p['pico']:.3f}", "-i", path,
                               "-frames:v", "1", "-vf", f"scale={THUMB_W}:-2", "-q:v", "4",
                               os.path.join(carpeta, f"plano_{p['id']:04d}.jpg")])


def hojas(carpeta: str, n_planos: int) -> list[str]:
    """Hojas de cuadros (8 × 6) con el número de cada plano encima, a partir
    de las miniaturas: lo que mira Claude para elegir los momentos."""
    fuente = os.path.relpath(os.path.join(config.FONTS_FOLDER, config.DEFAULT_FONT_FILE),
                             config.BASE_DIR).replace("\\", "/").replace(":", "\\:")
    salidas = []
    for h, inicio in enumerate(range(1, n_planos + 1, POR_HOJA), 1):
        out = os.path.join(carpeta, f"hoja_{h:02d}.jpg")
        ffmpeg_runner.capture([
            config.FFMPEG_PATH, "-v", "error", "-y", "-start_number", str(inicio),
            "-i", os.path.join(carpeta, "plano_%04d.jpg"), "-frames:v", "1", "-vf",
            f"scale={THUMB_W}:180:force_original_aspect_ratio=decrease,pad={THUMB_W}:180:(ow-iw)/2:(oh-ih)/2,"
            f"drawtext=fontfile='{fuente}':text='%{{eif\\:n+{inicio}\\:d}}':x=8:y=6:fontsize=34:"
            "fontcolor=yellow:box=1:boxcolor=black@0.7:boxborderw=6,"
            f"tile=8x6:padding=4:color=black",
            out], cwd=config.BASE_DIR)
        salidas.append(out)
    return salidas
