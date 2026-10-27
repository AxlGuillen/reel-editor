"""Layout vertical del módulo competitivo (1080x1920) a partir del directo de Ibai.

Fuente: el VOD de IbaiExtra en YouTube, 1920x1080 a 60 fps. Muestra la señal
oficial de Riot completa más dos cosas de Ibai: su contador de espectadores
(arriba a la derecha) y la cámara de su set (un recuadro a la derecha, encima
del minimapa). Zonas medidas sobre 169 frames de las tres primeras partidas
del día 1 (px en 1920x1080):

    marcador oficial      512,  10   896x98
    barra inferior        0..1920 x 820..1080: panel+publicidad | cámara de
                          jugador 288..464 | tabla de stats 465..1456 (en las
                          peleas, retratos con vida) | cámara de jugador
                          1456..1633 | minimapa 1633..1920
    cámara del set        x 1475..1920, 445x~250; la y cambia POR PARTIDA
                          (536 / 565 / 544): se detecta en cada tramo
    juego limpio          0, 108, 1475x712 (sin overlays)

Layout v2 (Axl's mockups, 23/10), top to bottom in 1080x1920:
    stats table          the broadcast's table WITHOUT the two player cams
                         (465..1456 x 820..1080) at 1020 wide, rounded corners
    gameplay             the full clean height (712 px) at 1080x950: zoom 1.33
                         (more zoom crops less; less than that cannot fill it)
    scoreboard           its center (520..1400 x 10..108) at 1040 wide, rounded
    Ibai's set camera    at 78 % of the width, rounded
    watermark            small, opaque, standing on the bottom right corner
All of it over the whole frame blurred and darkened (the margins).

Split camera: Riot sometimes splits the screen in two views with a thin dark
diagonal line near the center. `split_ranges()` finds it in the span and,
while it lasts, the gameplay shows the WHOLE clean zone (1475x712) across the
width over its own blurred copy instead of the zoomed crop (that would cut
right through the seam).

Las repeticiones y transiciones no muestran el marcador: `analizar()` las
detecta y el clip arranca después (o termina antes) para no mezclar vistas.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

import config
from core import ffmpeg_runner
from modules.vertical_convert.processor import _rounded_mask_expr
from modules.editing.effects import VIVID_CURVE

W, H = 1080, 1920                     # salida
SRC_W, SRC_H = 1920, 1080             # fuente

BARRA = (465, 820, 991, 260)          # stats table (without the player cams)
MARCADOR = (512, 10, 896, 98)         # the whole scoreboard: what the analysis reads
MARCADOR_VISTA = (520, 10, 880, 98)   # what is shown: its center
CAMARA_X, CAMARA_W, CAMARA_H = 1478, 440, 246   # interior del recuadro (sin el borde)
CAMARA_Y_DEFECTO = 540                # partida 1 del día 1; se detecta por tramo
JUEGO = (0, 108, 1475, 712)           # zona sin overlays
CENTRO = (960, 540)                   # el observador centra la acción en la pantalla

TOP_MARGIN = 40
BARRA_W = 1020
GAP_BARRA = 14                        # table → gameplay
JUEGO_H = 950                         # gameplay height in the output
GAP_MARCADOR = 12                     # gameplay → scoreboard
MARCADOR_W = 1040
GAP_CAMARA = 14                       # scoreboard → camera
CAMARA_ANCHO = 842                    # 78 % of the width
RADIO_BARRA, RADIO_MARCADOR, RADIO_CAMARA = 16, 14, 22
# Blurred backgrounds, dimmed by a factor (eq=brightness subtracts, and on a
# dark game it left them black).
BG_DIM, SPLIT_BG_DIM = 0.55, 0.6
MARCA_ALTO_DEFECTO = 190              # watermark: alto en px
MARCA_MARGEN = 14                     # distancia del watermark al borde derecho

# Salida etiquetada bt709/tv explícitamente (el VOD de YouTube HD lo es). El
# resumen se une con concat -c copy y hereda la cabecera del primer archivo:
# la intro y los clips tienen que llevar exactamente las mismas etiquetas.
SALIDA = "format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"

# Intro "encima" (la placa sobre el gameplay del clip 1, ver intro.py).
DUCK_VOL = 0.17           # el audio del juego mientras habla la voz (default de intro_volumen_juego)
DUCK_RAMPA = 0.4          # y vuelve al 100 % con esta rampa en t = D
PLACA_FADE_IN, PLACA_FADE_OUT = 0.2, 0.3


@dataclass
class IntroEncima:
    placa: str            # placa.png (rgba, con el margen de la sombra)
    voz: str              # audio de la voz
    D: float              # duración de la intro: voz + cola
    sting: str | None = None
    sting_vol: float = 0.35
    # Filtros de la voz y del sting (intro.cadena_voz / cadena_sting): la voz
    # normalizada y sin su silencio final; el sting con fade. Vacío = sin tocar.
    voz_filtro: str = ""
    sting_filtro: str = ""
    volumen_juego: float = DUCK_VOL    # 0–1: el juego mientras habla la voz
    # Hasta cuándo se ve la placa (None = D). Se va antes de la primera kill;
    # la voz y el juego bajado siguen hasta D.
    placa_fin: float | None = None


# Limitador de la mezcla: ≈ −1 dBTP, sin subir el nivel (level=0: el default lo
# subía +0.45 dB) y compensando su latencia (si no, corría el audio 5 ms).
LIMITADOR = "alimiter=limit=0.89:level=0:latency=1"


def ducking(D: float, vol: float = DUCK_VOL) -> str:
    """volume con eval=frame: `vol` hasta D y rampa lineal al 100 %, la
    misma idea que el ducking de la música en hook_reel."""
    return (f"volume='{vol:g}+{1 - vol:g}*min(max((t-{D:.3f})/{DUCK_RAMPA},0),1)'"
            ":eval=frame")


COLOR = {
    "off": "",
    # The same curve as the edits' "vivid": more contrast without darkening the game.
    "vivo": f"{VIVID_CURVE},eq=saturation=1.25,unsharp=5:5:0.6",
    "cine": "eq=contrast=1.12:saturation=1.1,curves=preset=medium_contrast",
}


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


# --- Geometría ------------------------------------------------------------------
BARRA_H = _even(BARRA_W * BARRA[3] / BARRA[2])                       # 268
MARCADOR_H = _even(MARCADOR_W * MARCADOR_VISTA[3] / MARCADOR_VISTA[2])  # 116
CAMARA_H_OUT = _even(CAMARA_ANCHO * CAMARA_H / CAMARA_W)               # 470
BARRA_Y = TOP_MARGIN
ARRIBA_H = BARRA_Y + BARRA_H + GAP_BARRA          # where the gameplay starts (322)
MARCADOR_Y = ARRIBA_H + JUEGO_H + GAP_MARCADOR
CAMARA_Y_OUT = MARCADOR_Y + MARCADOR_H + GAP_CAMARA
assert CAMARA_Y_OUT + CAMARA_H_OUT <= H
# The clean zone is 712 px tall: below this zoom the crop cannot fill 950.
ZOOM_LLENO = JUEGO_H / JUEGO[3]
ZOOM_MIN = ZOOM_LLENO
# Split view: the whole clean zone across the width, centered in the gameplay box.
SPLIT_H = _even(W * JUEGO[3] / JUEGO[2])           # 522


@dataclass(frozen=True)
class Geometria:
    recorte: tuple[int, int, int, int]   # (w, h, x, y) del gameplay en la fuente
    zoom: float                          # zoom efectivo
    juego_h: int                         # alto del gameplay en la salida
    extra_arriba: int = 0                # (layout v1; always 0 now)
    extra_abajo: int = 0


def geometria(zoom: float) -> Geometria:
    """Gameplay crop for a zoom (magnification over the source). The box is
    always 1080xJUEGO_H; a zoom under ZOOM_LLENO is taken as ZOOM_LLENO."""
    z = max(zoom, ZOOM_MIN)
    ch = min(_even(JUEGO_H / z), JUEGO[3])
    cw = min(_even(ch * W / JUEGO_H), JUEGO[2] // 2 * 2)
    x0, y0, jw, jh = JUEGO
    cx = min(max(CENTRO[0] - cw / 2, x0), x0 + jw - cw)
    cy = min(max(CENTRO[1] - ch / 2, y0), y0 + jh - ch)
    return Geometria((cw, ch, int(cx), int(cy)), z, JUEGO_H)


# --- Análisis del tramo ---------------------------------------------------------
@dataclass
class Analisis:
    camara_y: int                     # borde superior del interior del recuadro de Ibai
    vivo_desde: float                 # tramo en vivo (s, relativos al archivo) que
    vivo_hasta: float                 # contiene la kill: sin repeticiones ni cortes
    no_vivo_s: float                  # segundos fuera de la transmisión en vivo
    camara: tuple | None = None       # (x, y, w, h) interior of the box (detect_camera_box)


def _gris(path: str, vf: str, ss: float | None = None, dur: float | None = None,
          w: int = 0, h: int = 0) -> np.ndarray:
    cmd = [config.FFMPEG_PATH, "-v", "error"]
    if ss is not None:
        cmd += ["-ss", f"{ss:.3f}"]
    cmd += ["-i", path]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-an", "-vf", f"{vf},format=gray", "-f", "rawvideo", "-"]
    raw = ffmpeg_runner.capture(cmd)
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32)

# --- One decode per span ------------------------------------------------------------
# The analysis used to open the same 1080p60 span many times (the camera box,
# the scoreboard for the live part, the kill counter twice, the split line,
# the clock, the action of each kill) and every pass decoded all its frames to
# keep a small crop. A Scan decodes it ONCE with one output per crop (each its
# own `fps`, so every function gets exactly the frames it asked for before).
CAM_REGION = (1400, 380, 520, 500)     # where detect_camera_box looks (x, y, w, h)
CAM_SPAN_S = 20.0                      # ... in the first seconds of the span
BRANCH_FPS = {"marc_a": 5, "marc_k": 10, "kill_izq": 10, "kill_der": 10, "strip": 4, "cam": 8 / CAM_SPAN_S,
              "track": 10, "clock": 4, "amb_marc": 2, "amb_rgb": 2}
SCAN_CLIP = ("marc_a", "marc_k", "kill_izq", "kill_der", "strip", "cam", "track")   # what /finish reads
SCAN_EDIT = SCAN_CLIP + ("amb_marc", "amb_rgb")                                   # + the ambient shots
SCAN_ANCHOR = ("marc_k", "kill_izq", "kill_der", "clock")                          # what phase 1 reads


def _branch(name: str, fps: float) -> tuple[str, int, int, int]:
    """(filters, width, height, channels) of one output of a Scan."""
    mx, my, mw, mh = MARCADOR
    jx, jy, jw, jh = JUEGO
    if name in ("marc_a", "marc_k", "amb_marc"):
        return f"fps={fps},crop={mw}:{mh}:{mx}:{my},scale={mw // 4}:{mh // 4}:flags=area", mw // 4, mh // 4, 1
    if name in ("kill_izq", "kill_der"):
        kx, ky, kw, kh = KILLS_LADO[name[5:]]
        return f"fps={fps},crop={kw}:{kh}:{kx}:{ky}", kw, kh, 1
    if name == "strip":
        x0, x1 = SPLIT_X
        return (f"fps={fps},crop={x1 - x0}:{jh}:{x0}:{jy},scale={x1 - x0}:{jh // 4}:flags=neighbor",
                x1 - x0, jh // 4, 1)
    if name == "cam":
        cx, cy, cw, ch = CAM_REGION
        return f"trim=end={CAM_SPAN_S},fps={fps},crop={cw}:{ch}:{cx}:{cy}", cw, ch, 1
    if name == "track":
        h = _even(jh * TRACK_W / jw)
        return f"fps={fps},crop={jw}:{jh}:{jx}:{jy},scale={TRACK_W}:{h}", TRACK_W, h, 1
    if name == "clock":
        from modules.competitivo import clock
        x, y, w, h = clock.CLOCK
        return f"fps={fps},crop={w}:{h}:{x}:{y}", w, h, 1
    if name == "amb_rgb":
        return f"fps={fps},crop={jw}:{jh}:{jx}:{jy},scale=160:78", 160, 78, 3
    raise KeyError(name)


class Scan:
    """The crops of a span the analysis needs, from ONE decode of the file.

    Scan(path, want) decodes now; get(name) gives that output as float32
    frames (gray, or RGB for "amb_rgb"). What was not asked for at the start
    is decoded the first time it is read, so a function given a Scan that
    lacks something still works. `ss` / `dur`: only that part of the file;
    `fps`: another rate for some outputs. The file is decoded on the GPU
    (NVDEC) when it can be (`hw`), which leaves the CPU free for the render
    of the previous clip; if that run fails it is done again on the CPU."""

    def __init__(self, path: str, want: tuple = SCAN_CLIP, ss: float = 0.0, dur: float | None = None,
                 fps: dict | None = None, threads: int = 6, hw: bool = True):
        self.path, self.ss, self.dur, self.threads, self.hw = path, ss, dur, threads, hw
        self.fps = {**BRANCH_FPS, **(fps or {})}
        self._raw: dict[str, np.ndarray] = {}
        self._decode(want)

    def compatible(self, ss: float, dur: float | None, fps: dict | None) -> bool:
        return (self.ss == ss and self.dur == dur
                and all(abs(self.fps[k] - v) < 1e-9 for k, v in (fps or {}).items()))

    def has(self, name: str) -> bool:
        return name in self._raw

    def _decode(self, names) -> None:
        import shutil
        import tempfile
        names = [n for n in dict.fromkeys(names) if n not in self._raw]
        if not names:
            return
        tmp = tempfile.mkdtemp(prefix="scan-")
        try:
            specs = {n: _branch(n, self.fps[n]) for n in names}
            if len(names) > 1:
                graph = [f"[0:v]split={len(names)}" + "".join(f"[s{i}]" for i in range(len(names)))]
                src = [f"[s{i}]" for i in range(len(names))]
            else:
                graph, src = [], ["[0:v]"]
            outs = []
            for i, n in enumerate(names):
                chain, w, h, ch = specs[n]
                graph.append(f"{src[i]}{chain},format={'rgb24' if ch == 3 else 'gray'}[o{i}]")
                outs += ["-map", f"[o{i}]", "-f", "rawvideo", os.path.join(tmp, f"{n}.raw")]
            gpu = self.hw and ffmpeg_runner.cuda_decode_supported(ffmpeg_runner.probe_video_stream(self.path))
            for on_gpu in ([True, False] if gpu else [False]):
                cmd = [config.FFMPEG_PATH, "-v", "error"]
                cmd += ffmpeg_runner.CUDA_DECODE_FLAGS if on_gpu else ["-threads", str(self.threads)]
                if self.ss:
                    cmd += ["-ss", f"{self.ss:.3f}"]
                if self.dur is not None:
                    cmd += ["-t", f"{self.dur:.3f}"]
                g = list(graph)
                if on_gpu:        # the frames come down as nv12 once, then every crop
                    g = ["[0:v]hwdownload,format=nv12,format=yuv420p[dl]"] + [x.replace("[0:v]", "[dl]", 1) for x in g]
                try:
                    ffmpeg_runner.capture(cmd + ["-i", self.path, "-an", "-filter_complex", ";".join(g)] + outs)
                    break
                except RuntimeError:
                    if not on_gpu:
                        raise
            for n in names:
                _, w, h, ch = specs[n]
                data = np.fromfile(os.path.join(tmp, f"{n}.raw"), np.uint8)
                size = w * h * ch
                data = data[: data.size // size * size]
                self._raw[n] = data.reshape(-1, h, w, 3) if ch == 3 else data.reshape(-1, h, w)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def raw(self, name: str) -> np.ndarray:
        """The frames as they came (uint8): for big outputs that are read in slices."""
        self._decode([name])
        return self._raw[name]

    def get(self, name: str) -> np.ndarray:
        return self.raw(name).astype(np.float32)


def _scan_for(path: str, scan: "Scan | None", names: tuple, ss: float = 0.0, dur: float | None = None,
              fps: dict | None = None) -> Scan:
    """The scan the caller gave if it fits this call (same part of the file, same
    rates); otherwise a new one with just what this call needs."""
    if scan is not None and scan.compatible(ss, dur, fps):
        return scan
    return Scan(path, names, ss, dur, fps)


CAMARA_BOX_DEFECTO = (CAMARA_X, CAMARA_Y_DEFECTO, CAMARA_W, CAMARA_H)


def detect_camera_box(path: str, muestras: int = 8, scan: "Scan | None" = None) -> tuple[int, int, int, int]:
    """Interior (x, y, w, h) of the box with Ibai's set camera, without
    depending on colors. It changes with the source and the day: day 1 on
    YouTube 440x246 from x 1478; on 23/10 ~450x253 from x ~1468, at y ~541 on
    Twitch and ~406 on YouTube. Always 16:9 and flush with the right edge.

    The left border is the column with the strongest vertical edge on the
    median of several frames; that fixes the width and so the height (16:9).
    The top: the gameplay above the box changes all the time and the set
    behind Ibai does not, so it is where the frame-to-frame variation drops.
    If the gameplay is still too (nothing to compare), the row where the top
    and bottom borders and the left one along the height add up the most."""
    x0, y0, ww, hh = CAM_REGION                # search region
    fr = _scan_for(path, scan, ("cam",), fps={"cam": muestras / CAM_SPAN_S}).get("cam")
    if not len(fr):
        return CAMARA_BOX_DEFECTO
    m = np.median(fr, axis=0)
    gx = np.abs(m[:, 2:] - m[:, :-2])          # vertical edge at column c + 1
    sub = gx[:, 1440 - x0 - 1:1500 - x0 - 1]
    c = int(np.argmax(sub.mean(axis=0)))
    x = 1440 + c + 4                            # the edge is the border: the interior is inside
    w = 1918 - x
    h = _even(w * 9 / 16)
    span = h + 6                                # border to border
    top = None
    if len(fr) >= 6:
        var = np.median(fr.std(axis=0)[:, x - x0 + 12:1900 - x0], axis=1)
        moving = float(np.median(var[:40]))
        if moving >= 6:
            quiet = np.convolve(var < 0.3 * moving, np.ones(20) / 20, mode="valid") > 0.9
            first = int(np.argmax(quiet)) if quiet.any() else -1
            if first > 0 and first + span < hh:
                top = first + 1                 # the window starts ~2 px early; then the border (~4 px)
    if top is None:
        gy = np.abs(m[2:, :] - m[:-2, :])      # horizontal edge at row r + 1
        edge = sub[:, max(0, c - 1):c + 2].max(axis=1)
        horiz = gy[:, x - x0:x - x0 + 300].mean(axis=1)
        best = -1.0
        for t in range(0, len(horiz) - span):
            score = horiz[t] + horiz[t + span] + 2 * edge[t + 2:t + span].mean()
            if score > best:
                best, top = score, t
    if top is None:
        return CAMARA_BOX_DEFECTO
    y = y0 + top + 1 + 4
    # Same aspect as the output (CAMARA_W:CAMARA_H), trimming the excess.
    nw = min(w, _even(h * CAMARA_W / CAMARA_H))
    return x + (w - nw) // 2, y, nw // 2 * 2, h


def detectar_camara(path: str, muestras: int = 8) -> int:
    """y del interior del recuadro de la cámara del set (ver detect_camera_box)."""
    return detect_camera_box(path, muestras)[1]


def analizar(path: str, t_kill: float, inicio: float, fin: float, fps: float = 5,
             scan: "Scan | None" = None) -> Analisis:
    """Mira el tramo bajado: dónde está la cámara de Ibai y qué parte está en vivo.

    En las repeticiones, los "stingers" y el set de Ibai a pantalla completa
    el marcador oficial no está. Se compara el recorte del marcador de cada
    frame contra su aspecto típico en el tramo (la mediana, dominada por los
    frames en vivo): lo que se aleja mucho no es la transmisión en vivo. El
    clip queda en el tramo en vivo que contiene la kill."""
    camara = detect_camera_box(path, scan=scan)
    camara_y = camara[1]
    fr = _scan_for(path, scan, ("marc_a",), fps={"marc_a": fps}).get("marc_a")
    if not len(fr):
        return Analisis(camara_y, inicio, fin, 0.0, camara)
    ref = np.median(fr, axis=0)
    d = np.abs(fr - ref).mean(axis=(1, 2))
    umbral = max(18.0, 3 * float(np.median(d)))
    vivo = d < umbral
    # Huecos de un solo frame no cortan (ruido); un frame suelto "en vivo" en
    # medio de una repetición tampoco cuenta.
    for i in range(1, len(vivo) - 1):
        if vivo[i - 1] == vivo[i + 1] != vivo[i]:
            vivo[i] = vivo[i - 1]
    k = min(max(int(round(t_kill * fps)), 0), len(vivo) - 1)
    if not vivo[k]:
        # La kill cayó en algo que no es el vivo (raro): no se recorta nada.
        return Analisis(camara_y, inicio, fin, float((~vivo).sum()) / fps, camara)
    a = k
    while a > 0 and vivo[a - 1]:
        a -= 1
    b = k
    while b < len(vivo) - 1 and vivo[b + 1]:
        b += 1
    desde = max(inicio, a / fps + (0.2 if a > 0 else 0))
    hasta = min(fin, (b + 1) / fps - (0.2 if b < len(vivo) - 1 else 0))
    fuera = float((~vivo[int(inicio * fps):int(fin * fps)]).sum()) / fps
    return Analisis(camara_y, desde, hasta, fuera, camara)


# --- Split camera ---------------------------------------------------------------
SPLIT_X = (860, 1140)         # where Riot's divider crosses the clean zone (source px)
SPLIT_DARK = 14               # the line is almost black (measured 0–3)
SPLIT_CONTRAST = 15           # and darker than both sides by at least this
# Share of the rows it has to cross. With the split: 0.73–1.00 (it loses rows
# where it passes a health bar or a banner; TL-CFO 23/10 went down to 0.73 and
# 0.85 cut it into pieces); without it, at most 0.51 (31 spans of 23/10).
SPLIT_ROWS = 0.65
SPLIT_SLOPE = 0.35            # |dx/dy| at most (measured −0.19: a slight diagonal)
SPLIT_MIN_S = 0.75            # seen this long in a row (the transition flickers)
SPLIT_GAP_S = 0.5             # a gap this short inside a split does not end it


def _divider_rows(fr: np.ndarray) -> list[list[float]]:
    """Per row of a gray frame of the search strip: the centers of thin dark
    runs (3–16 px) with brighter pixels on both sides."""
    out = []
    for row in fr:
        dark = row <= SPLIT_DARK
        centers = []
        i, n = 0, len(row)
        while i < n:
            if not dark[i]:
                i += 1
                continue
            j = i
            while j < n and dark[j]:
                j += 1
            if 3 <= j - i <= 16 and i >= 6 and j + 6 <= n:
                floor = row[i:j].mean()
                if row[i - 6] - floor >= SPLIT_CONTRAST and row[j + 5] - floor >= SPLIT_CONTRAST:
                    centers.append((i + j - 1) / 2)
            i = j
        out.append(centers)
    return out


def has_divider(fr: np.ndarray, step: int = 1) -> bool:
    """Whether a gray frame of the search strip (the clean zone's full height,
    x SPLIT_X; one row every `step` px) has Riot's split-camera line: thin,
    dark, straight and across (almost) every row, with a slight slope at most."""
    rows = _divider_rows(fr)
    ys = [y for y, cs in enumerate(rows) for _ in cs]
    xs = [c for cs in rows for c in cs]
    if len(set(ys)) < SPLIT_ROWS * len(rows):
        return False
    ys, xs = np.array(ys), np.array(xs, float)
    need = SPLIT_ROWS * len(rows)
    for slope in np.arange(-SPLIT_SLOPE, SPLIT_SLOPE + 1e-9, 0.01):
        # Where each candidate would cross the top row: on the line, all the same
        # (within ±4 px: the antialiasing moves the centers a pixel).
        bins = np.round((xs - slope * step * ys) / 3).astype(int)
        bins -= bins.min()
        window = np.convolve(np.bincount(bins), np.ones(3), mode="same")
        k = int(np.argmax(window))
        if window[k] >= need and len(set(ys[np.abs(bins - k) <= 1])) >= need:
            return True
    return False


def split_ranges(path: str, fps: float = 4, ss: float = 0.0, dur: float | None = None,
                 scan: "Scan | None" = None) -> list[tuple[float, float]]:
    """[(a, b)] seconds of the file with Riot's split camera (see has_divider),
    seen for SPLIT_MIN_S in a row. Rows every 4 px: enough for a full-height line."""
    step = 4
    fr = _scan_for(path, scan, ("strip",), ss, dur, {"strip": fps}).get("strip")
    hits = [has_divider(f, step) for f in fr]
    runs: list[list[int]] = []                       # [first, last + 1] sample of each run
    for i, hit in enumerate(hits):
        if not hit:
            continue
        if runs and i - runs[-1][1] <= SPLIT_GAP_S * fps:
            runs[-1][1] = i + 1                      # a short gap: the same split
        else:
            runs.append([i, i + 1])
    return [(round(max(ss, ss + a / fps - 0.5 / fps), 2), round(ss + b / fps - 0.5 / fps, 2))
            for a, b in runs if (b - a) / fps >= SPLIT_MIN_S]


# --- Ambient shots ----------------------------------------------------------------
AMBIENT_GREEN = 0.02          # the Rift: 5–43 % saturated green; real cameras: 0–0.1 %
AMBIENT_MIN_S = 1.5


def _green_share(rgb: np.ndarray) -> np.ndarray:
    """Share of saturated green-teal pixels (the map) of each RGB frame."""
    a = rgb / 255.0
    mx, mn = a.max(axis=-1), a.min(axis=-1)
    d = np.maximum(mx - mn, 1e-6)
    s = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
    green = (h >= 70) & (h <= 170) & (s > 0.25) & (mx > 0.12)
    return green.mean(axis=(1, 2))


def ambient_ranges(path: str, fps: float = 2, scan: "Scan | None" = None) -> list[tuple[float, float]]:
    """[(a, b)] seconds of the span with "ambient" shots: no scoreboard (not the
    live game) and no game image either (no replay): the stage, the players,
    the crowd. A real camera has no saturated green; the Rift always does
    (measured in IG-GEN 23/10: 0–0.1 % against 5–43 %). At least AMBIENT_MIN_S."""
    sc = _scan_for(path, scan, ("amb_marc", "amb_rgb"), fps={"amb_marc": fps, "amb_rgb": fps})
    m, rgb = sc.get("amb_marc"), sc.get("amb_rgb")
    n = min(len(m), len(rgb))
    if n < 3:
        return []
    rgb = rgb[:n]
    d = np.abs(m[:n] - np.median(m[:n], axis=0)).mean(axis=(1, 2))
    live = d < max(18.0, 3 * float(np.median(d)))
    ambient = ~live & (_green_share(rgb) < AMBIENT_GREEN)
    out, start = [], None
    for i, hit in enumerate(list(ambient) + [False]):
        if hit and start is None:
            start = i
        elif not hit and start is not None:
            # Half a sample inside each border: the cut falls somewhere between samples.
            a, b = (start + 0.5) / fps, (i - 0.5) / fps
            if b - a >= AMBIENT_MIN_S:
                out.append((round(a, 2), round(b, 2)))
            start = None
    return out


def ambient_geometry(camara: int | tuple, camera: bool = True) -> str:
    """Geometry of an ambient shot in an edit (like edit_geometry): the clean
    zone at full width in the middle of the gameplay box, over the whole frame
    blurred, and Ibai's camera where it always is. Without the table and the
    scoreboard (in these shots they are not there)."""
    kx, ky, kw, kh = camara if isinstance(camara, tuple) else (CAMARA_X, camara, CAMARA_W, CAMARA_H)
    jx, jy, jw, jh = JUEGO
    n = 3 if camera else 2
    partes = [
        f"[0:v]split={n}[a_fondo][a_juego]" + ("[a_camara]" if camera else ""),
        f"[a_fondo]scale=-2:{H // 4},crop={W // 4}:{H // 4},gblur=sigma=10,lutyuv=y=val*{BG_DIM},"
        f"scale={W}:{H}:flags=bilinear,format=yuv420p,setsar=1[a_bg]",
        f"[a_juego]crop={jw}:{jh}:{jx}:{jy},scale={W}:{SPLIT_H}:flags=lanczos,setsar=1[a_view]",
        f"[a_bg][a_view]overlay=0:{ARRIBA_H + (JUEGO_H - SPLIT_H) // 2}[a_p]",
    ]
    if camera:
        partes += [
            *_rounded(f"[a_camara]crop={kw}:{kh}:{kx}:{ky},scale={CAMARA_ANCHO}:{CAMARA_H_OUT}:flags=lanczos,",
                      CAMARA_ANCHO, CAMARA_H_OUT, RADIO_CAMARA, "a_cam"),
            f"[a_p][a_cam]overlay={(W - CAMARA_ANCHO) // 2}:{CAMARA_Y_OUT},format=yuv420p,setsar=1",
        ]
    else:
        partes.append("[a_p]format=yuv420p,setsar=1")
    return ";".join(partes)


# Dígitos de kills del marcador, a cada lado del logo central. El equipo azul
# siempre va a la izquierda y el rojo a la derecha (en la G2 del día 1 C9 fue
# azul y pasó a la izquierda). El oro, que cambia todo el tiempo, queda afuera.
KILLS_LADO = {"izq": (885, 22, 60, 36), "der": (975, 22, 60, 36)}


def cambios_kills(path: str, ss: float = 0.0, dur: float | None = None,
                  fps: float = 10, scan: "Scan | None" = None) -> list[tuple[float, str]]:
    """(segundo, lado) en que sube el contador de kills del marcador oficial,
    solo durante la transmisión en vivo. Segundos relativos al archivo.

    Es lo que sincroniza el video con el feed sin pedir nada: el feed dice a
    qué hora UTC subió el contador (±0.2 s) y de qué lado; acá se ve en qué
    segundo del video subió. Cada cambio se mide como escalón: el promedio
    de los 0.5 s posteriores contra el de los 0.5 s anteriores. Así se ven
    también los cambios chicos (8→9 comparte casi todos los trazos) y no
    cuenta el ruido de compresión. Las repeticiones (sin marcador) no cuentan."""
    sc = _scan_for(path, scan, ("marc_k", "kill_izq", "kill_der"), ss, dur,
                   {"marc_k": fps, "kill_izq": fps, "kill_der": fps})
    m = sc.get("marc_k")
    if len(m) < 12:
        return []
    d_marc = np.abs(m - np.median(m, axis=0)).mean(axis=(1, 2))
    vivo = d_marc < max(18.0, 3 * float(np.median(d_marc)))
    v = max(1, int(round(fps * 0.5)))                         # ventana de cada lado del escalón
    out: list[tuple[float, str]] = []
    for lado in KILLS_LADO:
        k = sc.get(f"kill_{lado}")
        n = min(len(k), len(vivo))
        esc = np.zeros(n)
        for i in range(v + 1, n - v):
            antes, despues = slice(i - v - 1, i - 1), slice(i + 1, i + v + 1)
            if vivo[antes].all() and vivo[despues].all():
                esc[i] = np.abs(k[despues].mean(axis=0) - k[antes].mean(axis=0)).mean()
        base = float(np.median(esc[esc > 0])) if (esc > 0).any() else 0.0
        umbral = max(2.5, 5 * base)
        ultimo = -1e9
        for i in range(1, n - 1):
            # máximo local del escalón por encima del umbral, uno por cambio
            if esc[i] > umbral and esc[i] >= esc[i - 1] and esc[i] >= esc[i + 1]:
                t = ss + i / fps
                if t - ultimo >= 1.0:
                    out.append((round(t, 2), lado))
                    ultimo = t
    return sorted(out)


def estimar_desfase(vistos: list[tuple[float, str]], esperados: list[tuple[float, str]],
                    rango: float = 25.0, tol: float = 0.8) -> tuple[float, int]:
    """Corrimiento (s) que mejor alinea las kills esperadas (del feed) con las
    vistas en el video, y cuántas coinciden (mismo lado, a menos de `tol`).

    Se prueba cada corrimiento posible: el que suma más coincidencias gana, y
    se afina con la mediana de las diferencias. (0.0, 0) si no hay datos."""
    if not vistos or not esperados:
        return 0.0, 0
    candidatos = {round(tv - te, 2) for tv, lv in vistos for te, le in esperados
                  if lv == le and abs(tv - te) <= rango}
    mejor = (0, 0.0, 0.0)                                       # (coincidencias, -error, delta)
    for d in candidatos:
        difs = []
        for te, le in esperados:
            cerca = [tv - te - d for tv, lv in vistos if lv == le and abs(tv - te - d) <= tol]
            if cerca:
                difs.append(min(cerca, key=abs))
        if difs:
            clave = (len(difs), -sum(abs(x) for x in difs), d)
            mejor = max(mejor, clave)
    n, _, d = mejor
    if not n:
        return 0.0, 0
    difs = [min((tv - te - d for tv, lv in vistos if lv == le and abs(tv - te - d) <= tol), key=abs)
            for te, le in esperados
            if any(lv == le and abs(tv - te - d) <= tol for tv, lv in vistos)]
    return round(d + float(np.median(difs)), 2), n


# --- Watermark ------------------------------------------------------------------
def recorte_marca(png: str) -> tuple[int, int, int, int] | None:
    """Caja (w, h, x, y) del contenido visible del PNG: sin el margen
    transparente, así el watermark queda "parado" sobre el borde de abajo."""
    probe = ffmpeg_runner.probe_resolution(png)
    if not probe:
        return None
    pw, ph = probe
    raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-i", png, "-vf", "format=rgba",
                                 "-frames:v", "1", "-f", "rawvideo", "-"])
    a = np.frombuffer(raw, np.uint8).reshape(ph, pw, 4)[:, :, 3]
    ys, xs = np.nonzero(a > 16)
    if not len(xs):
        return None
    x1, x2, y1, y2 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    return x2 - x1, y2 - y1, x1, y1


# --- Filtro -----------------------------------------------------------------------
def _track_expr(track: list[tuple[float, float]], t0: str | float) -> str:
    """x(t) of the gameplay crop from keyframes [(t, x)] (s of the source
    file): linear between them, held before the first and after the last."""
    t = f"(t+{t0})" if t0 else "t"
    pts = sorted(track)
    terms = [f"{pts[0][1]:.1f}*lt({t},{pts[0][0]:.3f})", f"{pts[-1][1]:.1f}*gte({t},{pts[-1][0]:.3f})"]
    for (a, xa), (b, xb) in zip(pts, pts[1:]):
        if b - a > 1e-3:
            terms.append(f"if(gte({t},{a:.3f})*lt({t},{b:.3f}),{xa:.1f}+{xb - xa:.1f}*({t}-{a:.3f})/{b - a:.3f},0)")
    return ffmpeg_runner.expr_sum(terms)


def _ranges_expr(ranges: list[tuple[float, float]], t0: str | float) -> str:
    """enable= expression that is 1 inside `ranges` (s of the source file).
    The filter's t starts at the input's -ss: `t0` is that offset (a number,
    or editing.render.SOURCE_T0 for an edit shot, replaced per shot)."""
    t = f"(t+{t0})" if t0 else "t"
    return ffmpeg_runner.expr_sum([f"between({t},{a:.3f},{b:.3f})" for a, b in ranges])


def _rounded(label_in: str, w: int, h: int, r: int, label_out: str) -> list[str]:
    return [f"{label_in}format=yuva420p[{label_out}_c]",
            f"color=c=white:s={w}x{h}:d=1,format=gray,geq=lum='{_rounded_mask_expr(r)}'[{label_out}_m]",
            f"[{label_out}_c][{label_out}_m]alphamerge[{label_out}]"]


def build_filter_complex(camara_y: int | tuple, zoom: float = 1.3, color: str = "vivo",
                         marca: tuple[int, int, int, int] | None = None,
                         marca_alto: int = MARCA_ALTO_DEFECTO,
                         placa_idx: int | None = None, D: float | None = None,
                         camera: bool = True, split: list[tuple[float, float]] | None = None,
                         t0: str | float = 0, crop_track: list[tuple[float, float]] | None = None) -> str:
    """filter_complex del layout: entra [0:v] (1920x1080) y, si hay watermark,
    [1:v] (el PNG); sale [vout] 1080x1920 yuv420p.

    `camara_y`: the camera box (x, y, w, h) or, the old way, its y.
    `camera=False`: no set camera (the background shows there).
    `split`: [(a, b)] seconds of the SOURCE FILE with Riot's split camera (or
    "full" view: the whole zone); `t0` is where the input starts in that file
    (see _ranges_expr). `crop_track`: [(t, x)] the gameplay crop's x over time
    (following the action, camera.py); None = still, centered.

    `placa_idx`/`D`: intro "encima": la placa (entrada `placa_idx`, imagen en
    loop de largo D) va centrada sobre el gameplay durante [0, D], entra con
    un fade de 0.2 s y sale con otro de 0.3 s. Acá D es lo que dura la PLACA
    (IntroEncima.placa_fin), que puede ser menos que la voz."""
    g = geometria(zoom)
    cw, ch, cx, cy = g.recorte
    kx, ky, kw, kh = camara_y if isinstance(camara_y, tuple) else (CAMARA_X, camara_y, CAMARA_W, CAMARA_H)
    bx, by, bw, bh = BARRA
    mx, my, mw, mh = MARCADOR_VISTA
    jx, jy, jw, jh = JUEGO
    color_f = COLOR[color]
    n = 4 + bool(camera)
    partes = [
        f"[0:v]split={n}[s_fondo][s_barra][s_juego][s_marcador]" + ("[s_camara]" if camera else ""),
        # The whole frame, blurred and darkened: the margins.
        f"[s_fondo]scale=-2:{H // 4},crop={W // 4}:{H // 4},gblur=sigma=10,lutyuv=y=val*{BG_DIM},"
        f"scale={W}:{H}:flags=bilinear,format=yuv420p,setsar=1[fondo]",
        *_rounded(f"[s_barra]crop={bw}:{bh}:{bx}:{by},scale={BARRA_W}:{BARRA_H}:flags=lanczos,",
                  BARRA_W, BARRA_H, RADIO_BARRA, "barra"),
        *_rounded(f"[s_marcador]crop={mw}:{mh}:{mx}:{my},scale={MARCADOR_W}:{MARCADOR_H}:flags=lanczos,",
                  MARCADOR_W, MARCADOR_H, RADIO_MARCADOR, "marcador"),
    ]
    crop_x = f"'{_track_expr(crop_track, t0)}'" if crop_track else str(cx)
    zoomed = f"crop={cw}:{ch}:{crop_x}:{cy},scale={W}:{JUEGO_H}:flags=lanczos,setsar=1"
    if split:
        partes += [
            "[s_juego]split=3[j_zoom][j_full][j_bg]",
            f"[j_zoom]{zoomed}[j_z]",
            f"[j_bg]crop={jw}:{jh}:{jx}:{jy},scale=-2:{JUEGO_H // 4},crop={W // 4}:{JUEGO_H // 4},"
            f"gblur=sigma=8,lutyuv=y=val*{SPLIT_BG_DIM},scale={W}:{JUEGO_H}:flags=bilinear,setsar=1[j_b]",
            f"[j_full]crop={jw}:{jh}:{jx}:{jy},scale={W}:{SPLIT_H}:flags=lanczos,setsar=1[j_f]",
            f"[j_b][j_f]overlay=0:{(JUEGO_H - SPLIT_H) // 2}[j_s]",
            f"[j_z][j_s]overlay=0:0:enable='{_ranges_expr(split, t0)}'"
            + (f",{color_f}" if color_f else "") + ",format=yuv420p[juego]",
        ]
    else:
        partes.append(f"[s_juego]{zoomed}" + (f",{color_f}" if color_f else "") + ",format=yuv420p[juego]")
    partes += [
        f"[fondo][barra]overlay={(W - BARRA_W) // 2}:{BARRA_Y}[p1]",
        f"[p1][juego]overlay=0:{ARRIBA_H}[p2]",
        f"[p2][marcador]overlay={(W - MARCADOR_W) // 2}:{MARCADOR_Y}[p3]",
    ]
    if camera:
        partes += [
            *_rounded(f"[s_camara]crop={kw}:{kh}:{kx}:{ky},"
                      f"scale={CAMARA_ANCHO}:{CAMARA_H_OUT}:flags=lanczos,",
                      CAMARA_ANCHO, CAMARA_H_OUT, RADIO_CAMARA, "camara"),
            f"[p3][camara]overlay={(W - CAMARA_ANCHO) // 2}:{CAMARA_Y_OUT},format=yuv420p,setsar=1[pila]",
        ]
    else:
        partes.append("[p3]format=yuv420p,setsar=1[pila]")
    pila = "[pila]"
    if placa_idx is not None and D:
        cy = ARRIBA_H + JUEGO_H // 2
        partes += [
            f"[{placa_idx}:v]format=rgba,fade=t=in:st=0:d={PLACA_FADE_IN}:alpha=1,"
            f"fade=t=out:st={max(0.0, D - PLACA_FADE_OUT):.3f}:d={PLACA_FADE_OUT}:alpha=1[placa]",
            f"[pila][placa]overlay=x=(W-w)/2:y={cy}-h/2:enable='between(t,0,{D:.3f})'"
            ":eof_action=pass[pila_intro]",
        ]
        pila = "[pila_intro]"
    if marca:
        pw, ph, px, py = marca
        partes += [
            f"[1:v]crop={pw}:{ph}:{px}:{py},scale=-2:{marca_alto}:flags=lanczos,format=rgba[marca]",
            f"{pila}[marca]overlay=W-w-{MARCA_MARGEN}:H-h,{SALIDA}[vout]",
        ]
    else:
        partes.append(f"{pila}{SALIDA}[vout]")
    return ";".join(partes)


FOCUS_W = 160                 # action_focus analyzes the gameplay at this width
FOCUS_MIN_MOTION = 12         # below this (0–255) nothing clearly moves: the center
FOCUS_MAX_SPREAD = 0.3        # motion all over the crop (the observer's camera pans): the center


def action_focus(path: str, t: float, zoom: float = 1.3, span: float = 0.6,
                 fps: int = 10) -> tuple[float, float] | None:
    """Where the action is around second `t` of the span: the centroid of the
    motion (frame differences) inside the gameplay crop, mapped to the OUTPUT
    frame (x, y as fractions of 1080x1920), or None if there is no clear
    focus (nothing moves, or everything moves because the camera pans)."""
    g = geometria(zoom)
    cw, ch, cx, cy = g.recorte
    h = _even(ch * FOCUS_W / cw)
    raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-ss", f"{max(0.0, t - span / 2):.3f}",
                                 "-t", f"{span:.3f}", "-i", path, "-an", "-vf",
                                 f"fps={fps},crop={cw}:{ch}:{cx}:{cy},scale={FOCUS_W}:{h},format=gray",
                                 "-f", "rawvideo", "-"])
    frames = np.frombuffer(raw, np.uint8)
    if frames.size < 3 * FOCUS_W * h:
        return None
    frames = frames[: frames.size // (FOCUS_W * h) * FOCUS_W * h].reshape(-1, h, FOCUS_W).astype(np.float32)
    diff = np.abs(np.diff(frames, axis=0)).mean(axis=0)
    if diff.max() < FOCUS_MIN_MOTION:
        return None
    mask = diff >= np.percentile(diff, 95)
    ys, xs = np.nonzero(mask)
    w = diff[mask]
    u = float((xs * w).sum() / w.sum()) / FOCUS_W
    v = float((ys * w).sum() / w.sum()) / h
    spread = float(np.sqrt((((xs / FOCUS_W - u) ** 2 + (ys / h - v) ** 2) * w).sum() / w.sum()))
    if spread > FOCUS_MAX_SPREAD:
        return None
    return round(u, 3), round((ARRIBA_H + v * g.juego_h) / H, 3)


# --- Following the action ---------------------------------------------------------
TRACK_W = 240                 # the clean zone analyzed at this width
TRACK_FPS = 10
LOOKBACK_S = 1.5              # the action is looked for in this long before the kill
TRACK_SURE_SPREAD = 0.16      # more spread than this (fraction of the zone): not sure where it is
TRACK_MIN_MOTION = 6.0        # less residual motion than this (0–255): nothing to follow


def action_track(path: str, t: float, lookback: float = LOOKBACK_S, scan: "Scan | None" = None) -> dict | None:
    """Where the action is in the `lookback` s before second t of the span,
    over the WHOLE clean zone: {u, v} (fractions of it), spread and motion,
    and sure. The observer's camera pans with the fight, so first each frame
    is aligned with the previous one (phase correlation) and only what moves
    after that counts: the champions, the spells. None without frames."""
    import cv2
    jx, jy, jw, jh = JUEGO
    h = _even(jh * TRACK_W / jw)
    if scan is not None and scan.has("track") and scan.compatible(0.0, None, {"track": TRACK_FPS}):
        # The window out of the frames the Scan already decoded.
        i0 = int(round(max(0.0, t - lookback) * TRACK_FPS))
        fr = scan.raw("track")[i0:i0 + int(round((lookback + 0.1) * TRACK_FPS))]
        n = len(fr)
        if n < 3:
            return None
        fr = fr.astype(np.float32)
    else:
        raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-ss", f"{max(0.0, t - lookback):.3f}",
                                     "-t", f"{lookback + 0.1:.3f}", "-i", path, "-an", "-vf",
                                     f"fps={TRACK_FPS},crop={jw}:{jh}:{jx}:{jy},scale={TRACK_W}:{h},format=gray",
                                     "-f", "rawvideo", "-"])
        fr = np.frombuffer(raw, np.uint8)
        n = fr.size // (TRACK_W * h)
        if n < 3:
            return None
        fr = fr[: n * TRACK_W * h].reshape(n, h, TRACK_W).astype(np.float32)
    window = cv2.createHanningWindow((TRACK_W, h), cv2.CV_32F)
    acc = np.zeros((h, TRACK_W), np.float32)
    for a, b in zip(fr, fr[1:]):
        (dx, dy), _ = cv2.phaseCorrelate(a, b, window)
        m = np.float32([[1, 0, -dx], [0, 1, -dy]])
        aligned = cv2.warpAffine(b, m, (TRACK_W, h), borderMode=cv2.BORDER_REPLICATE)
        d = np.abs(aligned - a)
        # The borders the shift uncovered are not motion.
        mx, my = int(abs(dx)) + 2, int(abs(dy)) + 2
        d[:my, :] = d[-my:, :] = 0
        d[:, :mx] = d[:, -mx:] = 0
        acc += d
    acc /= n - 1
    acc = cv2.GaussianBlur(acc, (0, 0), 3)
    # Riot's popups (DRAGON SOUL, the sponsor's "moment of the game") animate
    # at the left and right edges: they weigh less than the middle.
    edge = np.clip(np.minimum(np.arange(TRACK_W), TRACK_W - 1 - np.arange(TRACK_W)) / (0.12 * TRACK_W), 0.25, 1)
    acc *= edge[None, :]
    motion = float(acc.max())
    if motion <= 1e-6:
        return {"u": 0.5, "v": 0.5, "spread": 1.0, "motion": 0.0, "sure": False}   # nothing moves
    hot = acc >= np.percentile(acc, 97)
    ys, xs = np.nonzero(hot)
    w = acc[hot]
    u = float((xs * w).sum() / w.sum()) / TRACK_W
    v = float((ys * w).sum() / w.sum()) / h
    spread = float(np.sqrt((((xs / TRACK_W - u) ** 2 + (ys / h - v) ** 2) * w).sum() / w.sum()))
    sure = motion >= TRACK_MIN_MOTION and spread <= TRACK_SURE_SPREAD
    return {"u": round(u, 3), "v": round(v, 3), "spread": round(spread, 3), "motion": round(motion, 1),
            "sure": bool(sure)}


def crop_x_for(u: float, zoom: float) -> int:
    """x of the gameplay crop (source px) that centers the point u of the zone."""
    cw = geometria(zoom).recorte[0]
    jx, _, jw, _ = JUEGO
    return int(min(max(jx + u * jw - cw / 2, jx), jx + jw - cw))


def edit_geometry(camara_y: int | tuple, zoom: float = 1.3, camera: bool = True,
                  split: list[tuple[float, float]] | None = None,
                  crop_track: list[tuple[float, float]] | None = None) -> str:
    """The layout as the geometry of an edit-mode shot (editing/render.py):
    no color (the edit's look sets it), no watermark (it goes at the end,
    static) and with the last chain open so the render can append the retime.
    `split`: seconds of the span file (the render puts in each shot's -ss)."""
    from modules.editing.render import SOURCE_T0
    fc = build_filter_complex(camara_y, zoom, "off", None, camera=camera, split=split, t0=SOURCE_T0,
                              crop_track=crop_track)
    return fc[: -len("[vout]")] if fc.endswith("[vout]") else fc


def build_concat(lista: str, out: str) -> list[str]:
    """Une el resumen: el video en copy (sin recodificar), el audio retemporizado
    y recodificado UNA vez. Con -c copy el audio de cada archivo traía su
    relleno AAC (priming/padding) y se iba atrasando en cada unión (medido:
    +39, +73, +86 ms); aresample=async lo vuelve a poner en los timestamps
    del video (medido: < 1 ms). El limitador evita que los picos del VOD
    pasen 0 dBFS."""
    return [config.FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0", "-i", lista,
            "-map", "0:v", "-map", "0:a?", "-c:v", "copy",
            "-af", f"aresample=async=1:min_hard_comp=0.001:first_pts=0,{LIMITADOR}",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", out]


@dataclass
class CardOverlay:
    """One card of a freeze: its frames (an image sequence with alpha) and where its canvas goes."""
    pattern: str                      # "dir/f%04d.png", 60 fps
    x: int                            # top-left of the canvas in the 1080x1920 output
    y: int
    scale: float = 1.0                # the canvas is shown at this size


@dataclass
class Freeze:
    """The clip stops (or slows) at second `t` of the output to show the cards, then goes on.

    `t` counts from the start of the clip as rendered (the -ss `desde`). The frame is
    held `dur` s with the sound off ("freeze"); "slow" plays the same stretch of the
    game at `rate` instead (so the picture and the sound go on, slowly)."""
    t: float
    dur: float
    mode: str = "freeze"              # "freeze" | "slow"
    rate: float = 0.3                 # "slow": the speed
    cards: list[CardOverlay] = field(default_factory=list)


def output_point(sx: float, sy: float, zoom: float, t: float, split: list[tuple[float, float]] | None = None,
                 crop_track: list[tuple[float, float]] | None = None) -> tuple[float, float, bool]:
    """Where a point of the source frame (px of the 1920x1080) falls in the 1080x1920 output at
    second `t` of the span file: (x, y, visible). The same geometry as the graph: the zoomed
    crop (its x following `crop_track`) or, inside `split`, the whole clean zone."""
    jx, jy, jw, jh = JUEGO
    if split and any(a <= t <= b for a, b in split):
        x = (sx - jx) * W / jw
        y = ARRIBA_H + (JUEGO_H - SPLIT_H) // 2 + (sy - jy) * SPLIT_H / jh
        top, bottom = ARRIBA_H + (JUEGO_H - SPLIT_H) // 2, ARRIBA_H + (JUEGO_H - SPLIT_H) // 2 + SPLIT_H
    else:
        cw, ch, cx, cy = geometria(zoom).recorte
        if crop_track:
            pts = sorted(crop_track)
            if t <= pts[0][0]:
                cx = pts[0][1]
            elif t >= pts[-1][0]:
                cx = pts[-1][1]
            else:
                for (a, xa), (b, xb) in zip(pts, pts[1:]):
                    if a <= t < b and b - a > 1e-3:
                        cx = xa + (xb - xa) * (t - a) / (b - a)
                        break
        x = (sx - cx) * W / cw
        y = ARRIBA_H + (sy - cy) * JUEGO_H / ch
        top, bottom = ARRIBA_H, ARRIBA_H + JUEGO_H
    return x, y, bool(0 <= x <= W and top <= y <= bottom)


def _with_freeze(filtros: str, audio: str | None, fz: Freeze, first_input: int) -> tuple[str, str | None]:
    """The graph with the freeze after the layout (so the layout's own time expressions do not
    move) and the cards over the held frame; the audio goes through the same cut.
    `audio`: the label of the sound ("[0:a]" / "[aout]") or None when the clip has none.
    Returns the graph and the label of the new sound."""
    assert filtros.count("[vout]") == 1
    one = 1 / 60
    t, d = fz.t, fz.dur
    partes = [filtros.replace("[vout]", "[vpre]")]
    if fz.mode == "slow":
        src = d * fz.rate                                     # the stretch of game shown slowly
        partes += [
            "[vpre]split=3[fa][fb][fc]",
            f"[fa]trim=end={t:.4f},setpts=PTS-STARTPTS[va]",
            f"[fb]trim=start={t:.4f}:end={t + src:.4f},setpts=(PTS-STARTPTS)/{fz.rate}[vb]",
            f"[fc]trim=start={t + src:.4f},setpts=PTS-STARTPTS[vc]",
        ]
    else:
        src = one
        partes += [
            "[vpre]split=3[fa][fb][fc]",
            f"[fa]trim=end={t:.4f},setpts=PTS-STARTPTS[va]",
            f"[fb]trim=start={t:.4f}:end={t + one:.4f},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={d - one:.4f}[vb]",
            f"[fc]trim=start={t + one:.4f},setpts=PTS-STARTPTS[vc]",
        ]
    partes.append("[va][vb][vc]concat=n=3:v=1:a=0[vfz]")
    cur = "[vfz]"
    for k, card in enumerate(fz.cards):
        i = first_input + k
        resize = f"scale=iw*{card.scale:.3f}:ih*{card.scale:.3f}:flags=lanczos," if abs(card.scale - 1) > 0.005 else ""
        partes.append(f"[{i}:v]format=rgba,{resize}setpts=PTS-STARTPTS+{t:.4f}/TB[card{k}]")
        partes.append(f"{cur}[card{k}]overlay={card.x}:{card.y}:eof_action=pass:repeatlast=0[vcard{k}]")
        cur = f"[vcard{k}]"
    partes.append(f"{cur}null[vout]")
    new_audio = None
    if audio:
        fmt = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
        fade = 0.08
        if fz.mode == "slow":
            tempo = ffmpeg_runner.atempo_chain(fz.rate)
            partes += [
                f"{audio}{fmt},asplit=3[aa][ab][ac]",
                f"[aa]atrim=end={t:.4f},asetpts=PTS-STARTPTS[a1]",
                f"[ab]atrim=start={t:.4f}:end={t + src:.4f},asetpts=PTS-STARTPTS,{tempo}[a2]",
                f"[ac]atrim=start={t + src:.4f},asetpts=PTS-STARTPTS[a3]",
            ]
        else:
            partes += [
                f"{audio}{fmt},asplit=2[aa][ac]",
                f"[aa]atrim=end={t:.4f},asetpts=PTS-STARTPTS,afade=t=out:st={max(0.0, t - fade):.4f}:d={fade}[a1]",
                f"anullsrc=r=48000:cl=stereo,atrim=duration={d:.4f},asetpts=PTS-STARTPTS[a2]",
                f"[ac]atrim=start={t:.4f},asetpts=PTS-STARTPTS,afade=t=in:st=0:d={fade}[a3]",
            ]
        partes.append("[a1][a2][a3]concat=n=3:v=0:a=1[afz]")
        new_audio = "[afz]"
    return ";".join(partes), new_audio


def build_command(src: str, out: str, desde: float, duracion: float, camara_y: int | tuple,
                  zoom: float = 1.3, color: str = "vivo", marca_path: str | None = None,
                  marca_alto: int = MARCA_ALTO_DEFECTO, intro: IntroEncima | None = None,
                  con_audio: bool = True, split: list[tuple[float, float]] | None = None,
                  crop_track: list[tuple[float, float]] | None = None,
                  freeze: Freeze | None = None) -> list[str]:
    """Un solo encode por clip: recorte exacto del tramo + layout + watermark.

    `intro`: la intro "encima" del clip 1 (placa + voz + ducking del juego).
    `con_audio`: si el tramo trae audio (sin él, con intro suena solo la voz).
    `freeze`: the clip stops at a second to show the player cards (see Freeze); the
    clip lasts `freeze.dur` more. Then `con_audio` must say whether the span has sound.

    Medido (27/10, 3 tramos): el grafo de filtros es el 95 % del render (12.9 s de 13.6 s
    para 39 s de video; el decode 1.2 s, el encode casi nada), el "vivo" 5 s de ellos
    (curves 3.1, unsharp 1.9). Decodificar en NVDEC (misma imagen, bit a bit) no lo
    acelera (2.79x contra 2.83x) y los presets p5–p7 de NVENC suben el VMAF 0.02–0.03
    con el tope de 16 Mbps: no se usan."""
    marca = recorte_marca(marca_path) if marca_path else None
    cmd = [config.FFMPEG_PATH, "-y", "-ss", f"{desde:.3f}", "-t", f"{duracion:.3f}", "-i", src]
    idx = 1
    if marca:
        cmd += ["-i", marca_path]
        idx += 1
    filtros = None
    amap = "0:a?"
    if intro:
        D = intro.D
        placa_s = min(D, intro.placa_fin) if intro.placa_fin else D
        placa_idx, voz_idx = idx, idx + 1
        cmd += ["-loop", "1", "-framerate", "60", "-t", f"{placa_s:.3f}", "-i", intro.placa, "-i", intro.voz]
        idx += 2
        fmt = "aresample=48000,aformat=channel_layouts=stereo"
        audio, fuentes = [], []
        if con_audio:
            audio.append(f"[0:a]{fmt},{ducking(D, intro.volumen_juego)}[juego]")
            fuentes.append("[juego]")
        audio.append(f"[{voz_idx}:a]{intro.voz_filtro or fmt}[voz]")
        fuentes.append("[voz]")
        if intro.sting:
            cmd += ["-i", intro.sting]
            sting_f = intro.sting_filtro or f"{fmt},volume={intro.sting_vol},atrim=0:{D:.3f}"
            audio.append(f"[{idx}:a]{sting_f}[sting]")
            fuentes.append("[sting]")
            idx += 1
        largo = "first" if con_audio else "longest"
        audio.append(f"{''.join(fuentes)}amix=inputs={len(fuentes)}:normalize=0:duration={largo},"
                     f"{LIMITADOR},apad,atrim=0:{duracion:.3f}[aout]")
        filtros = build_filter_complex(camara_y, zoom, color, marca, marca_alto,
                                       placa_idx=placa_idx, D=placa_s, split=split,
                                       t0=desde, crop_track=crop_track) + ";" + ";".join(audio)
        amap = "[aout]"
    filtros = filtros or build_filter_complex(camara_y, zoom, color, marca, marca_alto,
                                              split=split, t0=desde, crop_track=crop_track)
    if freeze:
        for card in freeze.cards:
            cmd += ["-framerate", "60", "-i", card.pattern]
        sound = "[aout]" if amap == "[aout]" else ("[0:a]" if con_audio else None)
        filtros, new_sound = _with_freeze(filtros, sound, freeze, idx)
        if new_sound:
            amap = new_sound
    cmd += [
        "-filter_complex", filtros,
        "-map", "[vout]", "-map", amap,
        "-r", "60",
        *ffmpeg_runner.video_encode_flags(),
        # Tope de bitrate: con cq 18 una imagen ampliada y con nitidez extra
        # salía a ~34 Mbps (76 MB por 17 s). A 16 Mbps: SSIM 0.991 contra eso.
        "-maxrate", "16M", "-bufsize", "32M",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        out,
    ]
    return cmd
