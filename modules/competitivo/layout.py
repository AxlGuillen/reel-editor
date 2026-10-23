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

from dataclasses import dataclass

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


CAMARA_BOX_DEFECTO = (CAMARA_X, CAMARA_Y_DEFECTO, CAMARA_W, CAMARA_H)


def detect_camera_box(path: str, muestras: int = 8) -> tuple[int, int, int, int]:
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
    x0, y0, ww, hh = 1400, 380, 520, 500       # search region
    dur = 20.0
    fr = _gris(path, f"fps={muestras / dur},crop={ww}:{hh}:{x0}:{y0}", dur=dur, w=ww, h=hh)
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


def analizar(path: str, t_kill: float, inicio: float, fin: float, fps: float = 5) -> Analisis:
    """Mira el tramo bajado: dónde está la cámara de Ibai y qué parte está en vivo.

    En las repeticiones, los "stingers" y el set de Ibai a pantalla completa
    el marcador oficial no está. Se compara el recorte del marcador de cada
    frame contra su aspecto típico en el tramo (la mediana, dominada por los
    frames en vivo): lo que se aleja mucho no es la transmisión en vivo. El
    clip queda en el tramo en vivo que contiene la kill."""
    camara = detect_camera_box(path)
    camara_y = camara[1]
    mw, mh = MARCADOR[2] // 4, MARCADOR[3] // 4
    fr = _gris(path, f"fps={fps},crop={MARCADOR[2]}:{MARCADOR[3]}:{MARCADOR[0]}:{MARCADOR[1]},"
               f"scale={mw}:{mh}:flags=area", w=mw, h=mh)
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
SPLIT_ROWS = 0.85             # share of the rows it has to cross
SPLIT_SLOPE = 0.35            # |dx/dy| at most (measured −0.19: a slight diagonal)
SPLIT_MIN_S = 0.75            # seen this long in a row (the transition flickers)


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


def split_ranges(path: str, fps: float = 4, ss: float = 0.0,
                 dur: float | None = None) -> list[tuple[float, float]]:
    """[(a, b)] seconds of the file with Riot's split camera (see has_divider),
    seen for SPLIT_MIN_S in a row. Rows every 4 px: enough for a full-height line."""
    x0, x1 = SPLIT_X
    jx, jy, jw, jh = JUEGO
    step = 4
    fr = _gris(path, f"fps={fps},crop={x1 - x0}:{jh}:{x0}:{jy},scale={x1 - x0}:{jh // step}:flags=neighbor",
               ss=ss, dur=dur, w=x1 - x0, h=jh // step)
    hits = [has_divider(f, step) for f in fr]
    out, start = [], None
    for i, hit in enumerate(hits + [False]):
        if hit and start is None:
            start = i
        elif not hit and start is not None:
            if (i - start) / fps >= SPLIT_MIN_S:
                out.append((round(ss + start / fps - 0.5 / fps, 2), round(ss + i / fps - 0.5 / fps, 2)))
            start = None
    return out


# Dígitos de kills del marcador, a cada lado del logo central. El equipo azul
# siempre va a la izquierda y el rojo a la derecha (en la G2 del día 1 C9 fue
# azul y pasó a la izquierda). El oro, que cambia todo el tiempo, queda afuera.
KILLS_LADO = {"izq": (885, 22, 60, 36), "der": (975, 22, 60, 36)}


def cambios_kills(path: str, ss: float = 0.0, dur: float | None = None,
                  fps: float = 10) -> list[tuple[float, str]]:
    """(segundo, lado) en que sube el contador de kills del marcador oficial,
    solo durante la transmisión en vivo. Segundos relativos al archivo.

    Es lo que sincroniza el video con el feed sin pedir nada: el feed dice a
    qué hora UTC subió el contador (±0.2 s) y de qué lado; acá se ve en qué
    segundo del video subió. Cada cambio se mide como escalón: el promedio
    de los 0.5 s posteriores contra el de los 0.5 s anteriores. Así se ven
    también los cambios chicos (8→9 comparte casi todos los trazos) y no
    cuenta el ruido de compresión. Las repeticiones (sin marcador) no cuentan."""
    mx, my, mw, mh = MARCADOR
    m = _gris(path, f"fps={fps},crop={mw}:{mh}:{mx}:{my},scale={mw // 4}:{mh // 4}:flags=area",
              ss=ss, dur=dur, w=mw // 4, h=mh // 4)
    if len(m) < 12:
        return []
    d_marc = np.abs(m - np.median(m, axis=0)).mean(axis=(1, 2))
    vivo = d_marc < max(18.0, 3 * float(np.median(d_marc)))
    v = max(1, int(round(fps * 0.5)))                         # ventana de cada lado del escalón
    out: list[tuple[float, str]] = []
    for lado, (kx, ky, kw, kh) in KILLS_LADO.items():
        k = _gris(path, f"fps={fps},crop={kw}:{kh}:{kx}:{ky}", ss=ss, dur=dur, w=kw, h=kh)
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
                         t0: str | float = 0) -> str:
    """filter_complex del layout: entra [0:v] (1920x1080) y, si hay watermark,
    [1:v] (el PNG); sale [vout] 1080x1920 yuv420p.

    `camara_y`: the camera box (x, y, w, h) or, the old way, its y.
    `camera=False`: no set camera (the background shows there).
    `split`: [(a, b)] seconds of the SOURCE FILE with Riot's split camera;
    `t0` is where the input starts in that file (see _ranges_expr).

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
    zoomed = f"crop={cw}:{ch}:{cx}:{cy},scale={W}:{JUEGO_H}:flags=lanczos,setsar=1"
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


def edit_geometry(camara_y: int | tuple, zoom: float = 1.3, camera: bool = True,
                  split: list[tuple[float, float]] | None = None) -> str:
    """The layout as the geometry of an edit-mode shot (editing/render.py):
    no color (the edit's look sets it), no watermark (it goes at the end,
    static) and with the last chain open so the render can append the retime.
    `split`: seconds of the span file (the render puts in each shot's -ss)."""
    from modules.editing.render import SOURCE_T0
    fc = build_filter_complex(camara_y, zoom, "off", None, camera=camera, split=split, t0=SOURCE_T0)
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


def build_command(src: str, out: str, desde: float, duracion: float, camara_y: int | tuple,
                  zoom: float = 1.3, color: str = "vivo", marca_path: str | None = None,
                  marca_alto: int = MARCA_ALTO_DEFECTO, intro: IntroEncima | None = None,
                  con_audio: bool = True, split: list[tuple[float, float]] | None = None) -> list[str]:
    """Un solo encode por clip: recorte exacto del tramo + layout + watermark.

    `intro`: la intro "encima" del clip 1 (placa + voz + ducking del juego).
    `con_audio`: si el tramo trae audio (sin él, con intro suena solo la voz)."""
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
                                       t0=desde) + ";" + ";".join(audio)
        amap = "[aout]"
    cmd += [
        "-filter_complex", filtros or build_filter_complex(camara_y, zoom, color, marca, marca_alto,
                                                           split=split, t0=desde),
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
