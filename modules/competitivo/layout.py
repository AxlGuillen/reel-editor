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
    gameplay (zoom)            centrado donde el observador pone la acción; nítido
                               al centro y fundido hacia los bordes con el mismo
                               juego desenfocado (con menos zoom, también arriba/abajo)
    marcador oficial           a todo el ancho
    cámara del set de Ibai     al 70 % del ancho sobre su propio fondo difuminado
    watermark                  chico, opaco, parado en la esquina inferior derecha

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
JUEGO_H = H - ARRIBA_H - MARCADOR_H - CAMARA_H_OUT
# Con este zoom o más, el gameplay llena su hueco entero. Con menos, el
# recorte (más campo de visión a lo ancho) no alcanza de alto: la zona limpia
# mide 712 px, arriba está el marcador y abajo la barra. Ahí el juego nítido
# va centrado y lo que sobra lo cubre el mismo juego desenfocado ("foco").
ZOOM_LLENO = JUEGO_H / JUEGO[3]
ZOOM_MIN = W / JUEGO[2]           # el recorte no puede ser más ancho que la zona limpia
FOCO_BORDE = 0.14                 # fracción de cada semieje donde el juego se funde con el fondo


def recorte_juego(zoom: float) -> tuple[int, int, int, int, float, int]:
    """(w, h, x, y, zoom efectivo, alto en la salida) del recorte del gameplay.

    `zoom` es la ampliación respecto de la fuente. El alto del recorte nunca
    pasa de la zona limpia: con zoom < ZOOM_LLENO el juego nítido sale más
    bajo que su hueco (alto en la salida < JUEGO_H)."""
    z = max(zoom, ZOOM_MIN)
    cw = min(_even(W / z), JUEGO[2] // 2 * 2)
    ch = _even(min(JUEGO_H / z, JUEGO[3]))
    x0, y0, jw, jh = JUEGO
    cx = min(max(CENTRO[0] - cw / 2, x0), x0 + jw - cw)
    cy = min(max(CENTRO[1] - ch / 2, y0), y0 + jh - ch)
    return cw, ch, int(cx), int(cy), z, min(_even(ch * W / cw), JUEGO_H)


def _mascara_foco(borde: float = FOCO_BORDE) -> str:
    """Expresión geq (0-255): opaco en el centro y caída suave (smoothstep)
    hacia los bordes, más marcada en las esquinas (superelipse, p=6)."""
    u = "abs(X+0.5-W/2)/(W/2)"
    v = "abs(Y+0.5-H/2)/(H/2)"
    d = f"pow(pow({u},6)+pow({v},6),1/6)"
    return f"st(0,clip((1-{d})/{borde},0,1));255*ld(0)*ld(0)*(3-2*ld(0))"


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
def build_filter_complex(camara_y: int, zoom: float = 1.4, color: str = "vivo",
                         marca: tuple[int, int, int, int] | None = None,
                         marca_alto: int = MARCA_ALTO_DEFECTO, foco: bool = True) -> str:
    """filter_complex del layout: entra [0:v] (1920x1080) y, si hay watermark,
    [1:v] (el PNG); sale [vout] 1080x1920 yuv420p.

    `foco`: el juego nítido se funde con su versión desenfocada hacia los
    bordes y las esquinas. Sin foco y con zoom < ZOOM_LLENO, lo que sobra
    arriba y abajo queda desenfocado pero con corte recto."""
    cw, ch, cx, cy, _, alto = recorte_juego(zoom)
    bx, by, bw, bh = BARRA
    mx, my, mw, mh = MARCADOR
    color_f = COLOR[color]
    partes = [
        "[0:v]split=6[s_barra][s_fondo][s_juego][s_juego_f][s_marcador][s_camara]",
        # Barra con aire y esquinas redondeadas sobre su propio fondo difuminado.
        f"[s_barra]crop={bw}:{bh}:{bx}:{by},scale={BARRA_W}:{BARRA_H}:flags=lanczos,format=yuva420p[barra_c]",
        f"color=c=white:s={BARRA_W}x{BARRA_H}:d=1,format=gray,"
        f"geq=lum='{_rounded_mask_expr(RADIO_BARRA)}'[barra_m]",
        "[barra_c][barra_m]alphamerge[barra]",
        f"[s_fondo]crop={bw}:{bh}:{bx}:{by},scale={W // 4}:{_even(ARRIBA_H / 4)},"
        f"gblur=sigma=6,eq=brightness=-0.32,scale={W}:{ARRIBA_H}:flags=bilinear,format=yuv420p[fondo]",
        f"[fondo][barra]overlay={MARGEN_BARRA}:{MARGEN_BARRA},format=yuv420p,setsar=1[arriba]",
        # Gameplay: nítido al centro sobre el mismo recorte desenfocado, que
        # cubre el hueco entero (lo que sobra arriba/abajo y las esquinas).
        f"[s_juego]crop={cw}:{ch}:{cx}:{cy},scale={W}:{alto}:flags=lanczos"
        + (f",{color_f}" if color_f else "") + ",format=yuva420p[juego_c]",
        f"[s_juego_f]crop={cw}:{ch}:{cx}:{cy},scale={W // 4}:{_even(JUEGO_H / 4)}:"
        f"force_original_aspect_ratio=increase,crop={W // 4}:{_even(JUEGO_H / 4)},"
        f"gblur=sigma=7,eq=brightness=-0.12:saturation=1.1,scale={W}:{JUEGO_H}:flags=bilinear[juego_f]",
    ]
    if foco:
        partes += [
            f"color=c=white:s={W}x{alto}:d=1,format=gray,geq=lum='{_mascara_foco()}'[juego_m]",
            "[juego_c][juego_m]alphamerge[juego_n]",
        ]
    else:
        partes.append("[juego_c]null[juego_n]")
    partes += [
        "[juego_f][juego_n]overlay=0:(H-h)/2,format=yuv420p,setsar=1[juego]",
        # Marcador oficial a todo el ancho.
        f"[s_marcador]crop={mw}:{mh}:{mx}:{my},scale={W}:{MARCADOR_H}:flags=lanczos,"
        "format=yuv420p,setsar=1[marcador]",
        # Cámara del set al 70 % sobre su versión difuminada.
        f"[s_camara]crop={CAMARA_W}:{CAMARA_H}:{CAMARA_X}:{camara_y},split[cam_a][cam_b]",
        f"[cam_a]scale={W // 4}:{_even(CAMARA_H_OUT / 4)},gblur=sigma=5,eq=brightness=-0.25,"
        f"scale={W}:{CAMARA_H_OUT}:flags=bilinear[cam_fondo]",
        f"[cam_b]scale={CAMARA_ANCHO}:{CAMARA_H_OUT}:flags=lanczos[cam_frente]",
        "[cam_fondo][cam_frente]overlay=(W-w)/2:0,format=yuv420p,setsar=1[camara]",
        "[arriba][juego][marcador][camara]vstack=4" + ("[pila]" if marca else ",format=yuv420p[vout]"),
    ]
    if marca:
        pw, ph, px, py = marca
        partes += [
            f"[1:v]crop={pw}:{ph}:{px}:{py},scale=-2:{marca_alto}:flags=lanczos,format=rgba[marca]",
            f"[pila][marca]overlay=W-w-{MARCA_MARGEN}:H-h,format=yuv420p[vout]",
        ]
    return ";".join(partes)


def build_command(src: str, out: str, desde: float, duracion: float, camara_y: int,
                  zoom: float = 1.4, color: str = "vivo", marca_path: str | None = None,
                  marca_alto: int = MARCA_ALTO_DEFECTO, foco: bool = True) -> list[str]:
    """Un solo encode por clip: recorte exacto del tramo + layout + watermark."""
    marca = recorte_marca(marca_path) if marca_path else None
    cmd = [config.FFMPEG_PATH, "-y", "-ss", f"{desde:.3f}", "-t", f"{duracion:.3f}", "-i", src]
    if marca:
        cmd += ["-i", marca_path]
    cmd += [
        "-filter_complex", build_filter_complex(camara_y, zoom, color, marca, marca_alto, foco),
        "-map", "[vout]", "-map", "0:a?",
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
