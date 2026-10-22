"""Análisis de una canción: beats, compases, energía, secciones y drop.

Es la base de los edits musicales (anime_edit y el modo edit de competitivo):
ellos deciden QUÉ va en cada momento, y este módulo les dice CUÁNDO pegan los
golpes. Todo sale en segundos desde el inicio del archivo, en un dict que se
guarda tal cual como beatmap.json (el contrato está en CLAUDE.md).

Motores:
  librosa    beats con su tracker (programación dinámica sobre el onset
             envelope) y el primer tiempo de cada compás (downbeat) por la
             fase de 4 donde más pega el bombo (energía bajo 150 Hz).
  beat_this  Beat This! (CPJKU, ISMIR 2024): beats y downbeats con una red.
             Opcional (pip install beat-this); con "auto" se usa si está.

Secciones por energía, sin etiquetas de un modelo: cada compás es "alto" o
"bajo" según su volumen relativo a la canción, y los tramos se nombran por su
lugar (intro, verso, coro, puente, outro). El drop es el arranque del tramo
alto con el salto de energía más grande. Es tosco a propósito: explicable,
determinista y suficiente para decidir cada cuánto cortar.
"""
from __future__ import annotations

import numpy as np

import config
from core import ffmpeg_runner

SR = 22050
HOP = 512                     # energía y bombo
# El tracker de beats trabaja con el período en cuadros enteros del onset
# envelope: con 512 (23 ms) un 120 BPM salía 117.45 (lag de 22 cuadros) y los
# beats se corrían ~40 ms. Con 128 (5.8 ms) sale 120.2 y el error baja a ~16 ms.
HOP_BEAT = 128
VERSION = 1
GOLPE_PERCENTIL = 80          # onsets por encima de este percentil = golpes (shake)
ALTO_UMBRAL = 0.55            # energía relativa del compás (0–1) para ser "alto"
MIN_COMPASES = 2              # un tramo más corto se funde con el anterior
MOTORES = ("auto", "librosa", "beat_this")


def cargar_audio(path: str, sr: int = SR) -> np.ndarray:
    """Audio mono float32 a `sr` Hz, decodificado por FFmpeg (cualquier formato
    que acepte, también la pista de un video)."""
    raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-i", path, "-vn",
                                 "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], timeout=900)
    y = np.frombuffer(raw, np.float32)
    if not len(y):
        raise RuntimeError("El archivo no tiene audio.")
    return y


def beat_this_disponible() -> bool:
    try:
        import beat_this.inference  # noqa: F401
        return True
    except ImportError:
        return False


def _beat_this(path: str) -> tuple[np.ndarray, np.ndarray]:
    """(beats, downbeats) en segundos con Beat This!. La primera vez baja los
    pesos del modelo (~80 MB)."""
    from beat_this.inference import File2Beats
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        device = "cpu"
    beats, downbeats = File2Beats(checkpoint_path="final0", device=device, dbn=False)(path)
    return np.asarray(beats, float), np.asarray(downbeats, float)


def _promedio_por_tramo(valores: np.ndarray, tiempos: np.ndarray, cortes: np.ndarray,
                        fin: float) -> np.ndarray:
    """Promedio de `valores` (muestreados en `tiempos`) en cada [cortes[i], cortes[i+1])."""
    bordes = np.append(cortes, fin)
    out = np.zeros(len(cortes))
    for i in range(len(cortes)):
        m = (tiempos >= bordes[i]) & (tiempos < bordes[i + 1])
        out[i] = float(valores[m].mean()) if m.any() else 0.0
    return out


def _fase_downbeat(beats: np.ndarray, bombo: np.ndarray, sr: int) -> int:
    """De las 4 fases posibles del compás, la que cae donde más pega el bombo
    (`bombo`: onset envelope bajo 150 Hz, en cuadros de HOP)."""
    idx_all = np.clip(np.round(beats * sr / HOP).astype(int), 0, len(bombo) - 1)
    mejor, fase = -1.0, 0
    for p in range(min(4, len(beats))):
        score = float(bombo[idx_all[p::4]].mean())
        if score > mejor:
            mejor, fase = score, p
    return fase


def secciones(downbeats: list[float], energia_compas: list[float], duracion: float) -> list[dict]:
    """Tramos de la canción por energía: [{tipo, inicio, fin, energia}].

    Cada compás es alto o bajo según su energía relativa (percentiles 10–90 de
    la canción, suavizada con la mediana de 3 compases). Los tramos de menos de
    MIN_COMPASES se funden con el anterior. Nombres por lugar: lo bajo antes
    del primer alto es la intro, lo bajo entre altos un puente (o verso si es
    largo) y lo bajo del final el outro; lo alto es coro."""
    n = len(downbeats)
    if not n:
        return [{"tipo": "verso", "inicio": 0.0, "fin": round(duracion, 3), "energia": 0.0}]
    e = np.asarray(energia_compas, float)
    if n >= 3:
        suave = np.array([np.median(e[max(0, i - 1):i + 2]) for i in range(n)])
    else:
        suave = e
    p10, p90 = np.percentile(suave, 10), np.percentile(suave, 90)
    rel = (suave - p10) / (p90 - p10) if p90 > p10 else np.full(n, 0.5)
    alto = rel > ALTO_UMBRAL

    tramos: list[list] = []                      # [alto, i_inicio, i_fin_exclusivo]
    for i, a in enumerate(alto):
        if tramos and tramos[-1][0] == a:
            tramos[-1][2] = i + 1
        else:
            tramos.append([bool(a), i, i + 1])
    fundidos: list[list] = []
    for t in tramos:
        if fundidos and (t[2] - t[1] < MIN_COMPASES or fundidos[-1][0] == t[0]):
            fundidos[-1][2] = t[2]
        else:
            fundidos.append(t)
    if len(fundidos) > 1 and fundidos[0][2] - fundidos[0][1] < MIN_COMPASES:
        fundidos[1][1] = 0
        fundidos.pop(0)

    hubo_alto = False
    out = []
    for k, (a, i0, i1) in enumerate(fundidos):
        inicio = 0.0 if i0 == 0 else downbeats[i0]
        fin = duracion if i1 >= n else downbeats[i1]
        if a:
            tipo = "coro"
            hubo_alto = True
        elif not hubo_alto:
            tipo = "intro"
        elif k == len(fundidos) - 1:
            tipo = "outro"
        else:
            tipo = "puente" if i1 - i0 <= 8 else "verso"
        out.append({"tipo": tipo, "inicio": round(inicio, 3), "fin": round(fin, 3),
                    "energia": round(float(rel[i0:i1].mean()), 3)})
    return out


def drop_y_subidas(downbeats: list[float], energia_compas: list[float],
                   secs: list[dict]) -> tuple[float | None, list[list[float]]]:
    """El drop (arranque del coro con el mayor salto de energía) y las subidas
    (los 2 compases antes de cada coro que viene de algo bajo)."""
    e = np.asarray(energia_compas, float)
    if len(e) < 2:
        return None, []
    idx = {round(d, 3): i for i, d in enumerate(downbeats)}
    mejor, drop = -np.inf, None
    subidas = []
    for k, s in enumerate(secs):
        if s["tipo"] != "coro":
            continue
        i = idx.get(s["inicio"])
        if i is None or i == 0:
            continue
        salto = float(e[i:i + 2].mean() - e[max(0, i - 2):i].mean())
        if salto > mejor:
            mejor, drop = salto, s["inicio"]
        if k > 0 and secs[k - 1]["tipo"] != "coro" and i >= 2:
            subidas.append([round(downbeats[i - 2], 3), s["inicio"]])
    if drop is None:
        # Sin coro que venga de algo bajo: el compás donde más sube la energía.
        d = np.diff(e)
        drop = round(downbeats[int(np.argmax(d)) + 1], 3)
    return drop, subidas


def analizar(y: np.ndarray, sr: int = SR, motor: str = "auto", ruta: str | None = None) -> dict:
    """beatmap de la señal `y`. `ruta` (el archivo) solo la usa Beat This!."""
    import librosa

    if motor not in MOTORES:
        raise ValueError(f"Motor de beats inválido: {motor!r}. Opciones: {', '.join(MOTORES)}.")
    duracion = len(y) / sr
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP_BEAT)
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    bombo = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S[freqs < 150], ref=np.max),
                                         sr=sr, hop_length=HOP)
    usado = "librosa"
    if motor in ("auto", "beat_this") and ruta and beat_this_disponible():
        beats, downbeats = _beat_this(ruta)
        usado = "beat_this"
    elif motor == "beat_this":
        raise RuntimeError("Beat This! no está instalado (pip install beat-this).")
    else:
        _, frames = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=HOP_BEAT, trim=False)
        beats = librosa.frames_to_time(frames, sr=sr, hop_length=HOP_BEAT)
        downbeats = beats[_fase_downbeat(beats, bombo, sr)::4]
    if len(beats) < 4:
        raise RuntimeError("No se encontró un pulso en la canción (¿es música con ritmo?).")
    periodo = float(np.median(np.diff(beats)))

    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0]
    t_rms = librosa.times_like(rms, sr=sr, hop_length=HOP)
    e_beat = _promedio_por_tramo(rms, t_rms, beats, duracion)
    e_compas = _promedio_por_tramo(rms, t_rms, downbeats, duracion)
    maximo = float(e_beat.max()) or 1.0

    onsets = librosa.onset.onset_detect(onset_envelope=onset, sr=sr, hop_length=HOP_BEAT, units="frames")
    fuerza = onset[onsets] if len(onsets) else np.array([])
    corte = np.percentile(fuerza, GOLPE_PERCENTIL) if len(fuerza) else 0
    t_onsets = librosa.frames_to_time(onsets, sr=sr, hop_length=HOP_BEAT)

    db = [round(float(d), 3) for d in downbeats]
    secs = secciones(db, list(e_compas), duracion)
    drop, subidas = drop_y_subidas(db, list(e_compas), secs)
    return {
        "version": VERSION,
        "motor": usado,
        "duracion": round(duracion, 3),
        "bpm": round(60 / periodo, 2),
        "beats": [round(float(b), 3) for b in beats],
        "downbeats": db,
        "onsets": [round(float(t), 3) for t in t_onsets],
        "golpes": [round(float(t), 3) for t, f in zip(t_onsets, fuerza) if f >= corte],
        "energia_por_beat": [round(float(v) / maximo, 3) for v in e_beat],
        "secciones": secs,
        "drop": drop,
        "subidas": subidas,
        "palabras": [],
    }


# --- Utilidades para los que planifican sobre un beatmap --------------------------
def recortar(bm: dict, desde: float, dur: float) -> dict:
    """El beatmap visto desde la ventana [desde, desde + dur] de la canción: los
    tiempos pasan a ser relativos a `desde` y queda solo lo que cae adentro.
    Es lo que usan los planificadores, que trabajan en tiempo de la salida."""
    fin = desde + dur

    def ventana(lista):
        return [round(t - desde, 3) for t in lista if desde - 1e-6 <= t < fin]

    beats = bm["beats"]
    e = bm.get("energia_por_beat") or [0.0] * len(beats)
    energia = [v for t, v in zip(beats, e) if desde - 1e-6 <= t < fin]
    secs = []
    for s in bm.get("secciones", []):
        a, b = max(s["inicio"], desde), min(s["fin"], fin)
        if b > a:
            secs.append({**s, "inicio": round(a - desde, 3), "fin": round(b - desde, 3)})
    drop = bm.get("drop")
    return {
        **bm,
        "duracion": round(dur, 3),
        "beats": ventana(beats),
        "downbeats": ventana(bm["downbeats"]),
        "onsets": ventana(bm.get("onsets", [])),
        "golpes": ventana(bm.get("golpes", [])),
        "energia_por_beat": energia,
        "secciones": secs,
        "drop": round(drop - desde, 3) if drop is not None and desde <= drop < fin else None,
        "subidas": [[round(a - desde, 3), round(b - desde, 3)] for a, b in bm.get("subidas", [])
                    if desde <= a and b <= fin],
        "palabras": [{**w, "inicio": round(w["inicio"] - desde, 3), "fin": round(w["fin"] - desde, 3)}
                     for w in bm.get("palabras", []) if desde <= w["inicio"] < fin],
    }


def seccion_en(bm: dict, t: float) -> str:
    """Tipo de la sección que suena en `t` ("verso" si no cae en ninguna)."""
    for s in bm.get("secciones", []):
        if s["inicio"] <= t < s["fin"]:
            return s["tipo"]
    return "verso"


def en_subida(bm: dict, t: float) -> bool:
    return any(a <= t < b for a, b in bm.get("subidas", []))


def periodo(bm: dict) -> float:
    """Duración típica de un beat (s)."""
    b = bm["beats"]
    return float(np.median(np.diff(b))) if len(b) > 1 else 60 / max(bm.get("bpm") or 120, 1)


def beat_mas_cercano(beats: list[float], t: float) -> int:
    """Índice del beat más cercano a `t`."""
    arr = np.asarray(beats)
    return int(np.argmin(np.abs(arr - t)))
