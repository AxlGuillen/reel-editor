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

Layout, de arriba a abajo (pedido de Axl):
    barra de cámaras + stats   con aire arriba y a los costados, esquinas redondeadas
    gameplay (zoom)            centrado donde el observador pone la acción, a su alto
                               natural (con menos zoom es más bajo: ver geometria())
    marcador oficial           a todo el ancho
    cámara del set de Ibai     al 70 % del ancho sobre su propio fondo difuminado
    watermark                  chico, opaco, parado en la esquina inferior derecha

Todo va pegado al gameplay: con menos zoom, la barra baja, el marcador y la
cámara suben, y lo que sobra queda arriba de todo y abajo de todo (los fondos
difuminados de la barra y de la cámara se estiran). Son justo las franjas que
tapa la interfaz de TikTok.

Las repeticiones y transiciones no muestran el marcador: `analizar()` las
detecta y el clip arranca después (o termina antes) para no mezclar vistas.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

import config
from core import ffmpeg_runner
from modules.vertical_convert.processor import _rounded_mask_expr

W, H = 1080, 1920                     # salida
SRC_W, SRC_H = 1920, 1080             # fuente

BARRA = (288, 820, 1345, 260)         # cámara de jugador + tabla + cámara de jugador
MARCADOR = (512, 10, 896, 98)
CAMARA_X, CAMARA_W, CAMARA_H = 1478, 440, 246   # interior del recuadro (sin el borde)
CAMARA_Y_DEFECTO = 540                # partida 1 del día 1; se detecta por tramo
JUEGO = (0, 108, 1475, 712)           # zona sin overlays
CENTRO = (960, 540)                   # el observador centra la acción en la pantalla

MARGEN_BARRA = 36                     # aire de la barra arriba y a los costados
SEP_BARRA = 14                        # separación entre la barra y el gameplay
RADIO_BARRA = 16
CAMARA_ANCHO = 756                    # 70 % del ancho
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
    "vivo": "eq=contrast=1.08:saturation=1.25:gamma=0.98,unsharp=5:5:0.6",
    "cine": "eq=contrast=1.12:saturation=1.1,curves=preset=medium_contrast",
}


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


# --- Geometría ------------------------------------------------------------------
BARRA_W = W - 2 * MARGEN_BARRA
BARRA_H = _even(BARRA_W * BARRA[3] / BARRA[2])
ARRIBA_H = MARGEN_BARRA + BARRA_H + SEP_BARRA
MARCADOR_H = _even(W * MARCADOR[3] / MARCADOR[2])
CAMARA_H_OUT = _even(CAMARA_ANCHO * CAMARA_H / CAMARA_W)
JUEGO_H_MAX = H - ARRIBA_H - MARCADOR_H - CAMARA_H_OUT   # 1136: el gameplay más alto posible
# La zona limpia mide 712 px de alto (arriba el marcador, abajo la barra):
# con menos zoom que ZOOM_LLENO el recorte gana campo a lo ancho pero no de
# alto, y el gameplay sale más bajo que JUEGO_H_MAX.
ZOOM_LLENO = JUEGO_H_MAX / JUEGO[3]
ZOOM_MIN = W / JUEGO[2]           # el recorte no puede ser más ancho que la zona limpia


@dataclass(frozen=True)
class Geometria:
    recorte: tuple[int, int, int, int]   # (w, h, x, y) del gameplay en la fuente
    zoom: float                          # zoom efectivo
    juego_h: int                         # alto del gameplay en la salida
    extra_arriba: int                    # lo que sobra, arriba de la barra
    extra_abajo: int                     # y abajo de la cámara del set


def geometria(zoom: float) -> Geometria:
    """Recorte del gameplay y reparto del alto para un zoom (ampliación
    respecto de la fuente). Siempre suma 1920."""
    z = max(zoom, ZOOM_MIN)
    cw = min(_even(W / z), JUEGO[2] // 2 * 2)
    ch = _even(min(JUEGO_H_MAX / z, JUEGO[3]))
    x0, y0, jw, jh = JUEGO
    cx = min(max(CENTRO[0] - cw / 2, x0), x0 + jw - cw)
    cy = min(max(CENTRO[1] - ch / 2, y0), y0 + jh - ch)
    # Si el alto no lo limita la zona limpia, el recorte ya tiene la proporción
    # del hueco: llena JUEGO_H_MAX (sin los 2 px que dejaría el redondeo).
    juego_h = JUEGO_H_MAX if ch < JUEGO[3] else min(_even(ch * W / cw), JUEGO_H_MAX)
    sobra = JUEGO_H_MAX - juego_h
    arriba = sobra // 4 * 2
    return Geometria((cw, ch, int(cx), int(cy)), z, juego_h, arriba, sobra - arriba)


# --- Análisis del tramo ---------------------------------------------------------
@dataclass
class Analisis:
    camara_y: int                     # borde superior del interior del recuadro de Ibai
    vivo_desde: float                 # tramo en vivo (s, relativos al archivo) que
    vivo_hasta: float                 # contiene la kill: sin repeticiones ni cortes
    no_vivo_s: float                  # segundos fuera de la transmisión en vivo


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


def detectar_camara(path: str, muestras: int = 8) -> int:
    """y del recuadro de la cámara del set (interior), sin depender de colores.

    El recuadro es una caja fija: su borde superior es una arista horizontal
    que cruza TODO el ancho (x 1480..1915) y su borde izquierdo una arista
    vertical en x≈1476 a lo largo de su alto. El gameplay no tiene aristas tan
    largas ni tan alineadas. Se usa la mediana de varios frames para que lo
    que se mueve no cuente."""
    x0, y0, ww, hh = 1440, 380, 480, 460      # región de búsqueda
    dur = 20.0
    fr = _gris(path, f"fps={muestras / dur},crop={ww}:{hh}:{x0}:{y0}", dur=dur, w=ww, h=hh)
    if not len(fr):
        return CAMARA_Y_DEFECTO
    m = np.median(fr, axis=0)
    gy = np.abs(m[2:, :] - m[:-2, :])          # arista horizontal en la fila y (centro y+1)
    gx = np.abs(m[:, 2:] - m[:, :-2])          # arista vertical
    xl = 1476 - x0 - 1                         # columna del borde izquierdo en gx
    mejor, mejor_y = -1.0, CAMARA_Y_DEFECTO
    for yy in range(0, hh - CAMARA_H - 6):
        horiz = gy[yy, 1480 - x0:1915 - x0].mean()
        vert = gx[yy + 4:yy + 4 + CAMARA_H - 8, xl - 2:xl + 3].max(axis=1).mean()
        score = horiz + vert
        if score > mejor:
            mejor, mejor_y = score, yy + 1 + y0
    # La arista está en el borde del recuadro; el interior arranca ~4 px abajo.
    return int(mejor_y) + 4


def analizar(path: str, t_kill: float, inicio: float, fin: float, fps: float = 5) -> Analisis:
    """Mira el tramo bajado: dónde está la cámara de Ibai y qué parte está en vivo.

    En las repeticiones, los "stingers" y el set de Ibai a pantalla completa
    el marcador oficial no está. Se compara el recorte del marcador de cada
    frame contra su aspecto típico en el tramo (la mediana, dominada por los
    frames en vivo): lo que se aleja mucho no es la transmisión en vivo. El
    clip queda en el tramo en vivo que contiene la kill."""
    camara_y = detectar_camara(path)
    mw, mh = MARCADOR[2] // 4, MARCADOR[3] // 4
    fr = _gris(path, f"fps={fps},crop={MARCADOR[2]}:{MARCADOR[3]}:{MARCADOR[0]}:{MARCADOR[1]},"
               f"scale={mw}:{mh}:flags=area", w=mw, h=mh)
    if not len(fr):
        return Analisis(camara_y, inicio, fin, 0.0)
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
        return Analisis(camara_y, inicio, fin, float((~vivo).sum()) / fps)
    a = k
    while a > 0 and vivo[a - 1]:
        a -= 1
    b = k
    while b < len(vivo) - 1 and vivo[b + 1]:
        b += 1
    desde = max(inicio, a / fps + (0.2 if a > 0 else 0))
    hasta = min(fin, (b + 1) / fps - (0.2 if b < len(vivo) - 1 else 0))
    fuera = float((~vivo[int(inicio * fps):int(fin * fps)]).sum()) / fps
    return Analisis(camara_y, desde, hasta, fuera)


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
def build_filter_complex(camara_y: int, zoom: float = 1.3, color: str = "vivo",
                         marca: tuple[int, int, int, int] | None = None,
                         marca_alto: int = MARCA_ALTO_DEFECTO,
                         placa_idx: int | None = None, D: float | None = None,
                         camera: bool = True) -> str:
    """filter_complex del layout: entra [0:v] (1920x1080) y, si hay watermark,
    [1:v] (el PNG); sale [vout] 1080x1920 yuv420p.

    `camera=False` (edit mode): Ibai's set camera is replaced by the blurred,
    darkened gameplay. The geometry is the same.

    `placa_idx`/`D`: intro "encima": la placa (entrada `placa_idx`, imagen en
    loop de largo D) va centrada sobre el gameplay durante [0, D], entra con
    un fade de 0.2 s y sale con otro de 0.3 s. Acá D es lo que dura la PLACA
    (IntroEncima.placa_fin), que puede ser menos que la voz."""
    g = geometria(zoom)
    cw, ch, cx, cy = g.recorte
    arriba_h = ARRIBA_H + g.extra_arriba
    camara_h = CAMARA_H_OUT + g.extra_abajo
    bx, by, bw, bh = BARRA
    mx, my, mw, mh = MARCADOR
    color_f = COLOR[color]
    partes = [
        "[0:v]split=5[s_barra][s_fondo][s_juego][s_marcador][s_camara]",
        # Barra con aire y esquinas redondeadas sobre su propio fondo difuminado.
        f"[s_barra]crop={bw}:{bh}:{bx}:{by},scale={BARRA_W}:{BARRA_H}:flags=lanczos,format=yuva420p[barra_c]",
        f"color=c=white:s={BARRA_W}x{BARRA_H}:d=1,format=gray,"
        f"geq=lum='{_rounded_mask_expr(RADIO_BARRA)}'[barra_m]",
        "[barra_c][barra_m]alphamerge[barra]",
        f"[s_fondo]crop={bw}:{bh}:{bx}:{by},scale={W // 4}:{_even(arriba_h / 4)},"
        f"gblur=sigma=6,eq=brightness=-0.32,scale={W}:{arriba_h}:flags=bilinear,format=yuv420p[fondo]",
        # Lo que sobra va arriba de la barra: la barra baja.
        f"[fondo][barra]overlay={MARGEN_BARRA}:{g.extra_arriba + MARGEN_BARRA},"
        "format=yuv420p,setsar=1[arriba]",
        # Gameplay a su alto natural.
        f"[s_juego]crop={cw}:{ch}:{cx}:{cy},scale={W}:{g.juego_h}:flags=lanczos"
        + (f",{color_f}" if color_f else "") + ",format=yuv420p,setsar=1[juego]",
        # Marcador oficial a todo el ancho.
        f"[s_marcador]crop={mw}:{mh}:{mx}:{my},scale={W}:{MARCADOR_H}:flags=lanczos,"
        "format=yuv420p,setsar=1[marcador]",
    ]
    if camera:
        partes += [
            # Cámara del set al 70 % sobre su versión difuminada.
            f"[s_camara]crop={CAMARA_W}:{CAMARA_H}:{CAMARA_X}:{camara_y},split[cam_a][cam_b]",
            f"[cam_a]scale={W // 4}:{_even(camara_h / 4)},gblur=sigma=5,eq=brightness=-0.25,"
            f"scale={W}:{camara_h}:flags=bilinear[cam_fondo]",
            f"[cam_b]scale={CAMARA_ANCHO}:{CAMARA_H_OUT}:flags=lanczos[cam_frente]",
            # La cámara sube pegada al marcador; lo que sobra queda abajo.
            "[cam_fondo][cam_frente]overlay=(W-w)/2:0,format=yuv420p,setsar=1[camara]",
        ]
    else:
        jx, jy, jw, jh = JUEGO
        partes.append(f"[s_camara]crop={jw}:{jh}:{jx}:{jy},scale={W // 4}:{_even(camara_h / 4)},"
                      f"gblur=sigma=8,eq=brightness=-0.3,scale={W}:{camara_h}:flags=bilinear,"
                      "format=yuv420p,setsar=1[camara]")
    partes.append("[arriba][juego][marcador][camara]vstack=4[pila]")
    pila = "[pila]"
    if placa_idx is not None and D:
        cy = ARRIBA_H + g.extra_arriba + g.juego_h // 2
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
    top = ARRIBA_H + g.extra_arriba
    return round(u, 3), round((top + v * g.juego_h) / H, 3)


def edit_geometry(camara_y: int, zoom: float = 1.3, camera: bool = False) -> str:
    """The layout as the geometry of an edit-mode shot (editing/render.py):
    no color (the edit's look sets it), no watermark (it goes at the end,
    static) and with the last chain open so the render can append the retime."""
    fc = build_filter_complex(camara_y, zoom, "off", None, camera=camera)
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


def build_command(src: str, out: str, desde: float, duracion: float, camara_y: int,
                  zoom: float = 1.3, color: str = "vivo", marca_path: str | None = None,
                  marca_alto: int = MARCA_ALTO_DEFECTO, intro: IntroEncima | None = None,
                  con_audio: bool = True) -> list[str]:
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
                                       placa_idx=placa_idx, D=placa_s) + ";" + ";".join(audio)
        amap = "[aout]"
    cmd += [
        "-filter_complex", filtros or build_filter_complex(camara_y, zoom, color, marca, marca_alto),
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
