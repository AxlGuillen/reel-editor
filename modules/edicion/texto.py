"""Letreros de un edit en ASS (los quema libass, igual que los subtítulos).

Estilos:
  palabra    la letra de la canción, una palabra gigante en el centro con
             degradado vertical (la firma del edit de referencia). libass no
             tiene degradados: se dibuja la palabra N veces, cada copia
             recortada (\\clip) a una franja horizontal de su color, y debajo
             una copia sin relleno con el contorno y la sombra.
  kill       NOMBRE del jugador grande y el campeón chico debajo, entra de golpe.
  multikill  "QUADRA KILL" estampado, en degradado de oro.
  titulo     una línea arriba (equipos, mapa).
  sub        una línea abajo, como un subtítulo.

El lienzo es el del video (vertical u horizontal): los tamaños van en
fracción del lado corto, así un edit 16:9 y uno 9:16 se ven proporcionados.
Las fuentes son las de assets/fonts (fonts.py: familia y métricas).
"""
from __future__ import annotations

import os
import uuid

import config
from modules.edicion.edl import Texto
from modules.subtitles import fonts
from modules.subtitles.processor import _seconds_to_ass

BANDAS = 12
DEGRADADO_PALABRA = ("#9CC2FF", "#FFFFFF")      # azul claro arriba → blanco abajo
DEGRADADO_MULTIKILL = ("#FFE27A", "#FF8A1E")
FUENTES = {
    "palabra": "Poppins-Black.ttf",
    "kill": "Anton-Regular.ttf",
    "kill_sub": "Poppins-Bold.ttf",
    "multikill": "Anton-Regular.ttf",
    "titulo": "Poppins-Black.ttf",
    "sub": "Poppins-Bold.ttf",
}
# em de cada estilo en fracción del lado corto del lienzo
TAMANOS = {"palabra": 0.14, "kill": 0.12, "kill_sub": 0.045, "multikill": 0.15, "titulo": 0.06, "sub": 0.05}
ANCHO_UTIL = 0.9


def _ass_color(css: str) -> str:
    css = css.lstrip("#").upper()
    return f"&H00{css[4:6]}{css[2:4]}{css[0:2]}&"


def _mezcla(a: str, b: str, f: float) -> str:
    a, b = a.lstrip("#"), b.lstrip("#")
    canales = [round(int(a[i:i + 2], 16) * (1 - f) + int(b[i:i + 2], 16) * f) for i in (0, 2, 4)]
    return "#" + "".join(f"{c:02X}" for c in canales)


def _esc(s: str) -> str:
    return s.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


class _Lienzo:
    def __init__(self, ancho: int, alto: int):
        self.ancho, self.alto = ancho, alto
        self.u = min(ancho, alto)

    def em(self, estilo: str) -> float:
        return TAMANOS[estilo] * self.u


def _fuente(estilo: str) -> dict:
    return fonts.font_info(FUENTES[estilo])


def _ajuste(info: dict, texto: str, em: float, ancho: int) -> float:
    """Escala (≤ 1) para que el texto entre en el ANCHO_UTIL del lienzo."""
    w = fonts.text_width(info, texto, em)
    lim = ancho * ANCHO_UTIL
    return 1.0 if w <= lim or w <= 0 else lim / w


def _cabecera(ancho: int, alto: int) -> str:
    # YCbCr Matrix: None → libass usa la matriz del propio video (ver subtitles).
    return f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: {ancho}
PlayResY: {alto}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: None

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,Arial,60,&H00FFFFFF&,&H000000FF&,&H00000000&,&H96000000&,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _evento(capa: int, t: Texto, tags: str, texto: str) -> str:
    return (f"Dialogue: {capa},{_seconds_to_ass(t.desde)},{_seconds_to_ass(t.hasta)},Base,,0,0,0,,"
            f"{{{tags}}}{_esc(texto)}")


def _fuente_tags(info: dict, em: float) -> str:
    return f"\\fn{info['family']}\\fs{fonts.ass_font_size(info, round(em))}\\b{1 if info['bold'] else 0}"


def _cy(info: dict, em: float, centro: float) -> float:
    """y del \\an5 (centro de la CAJA de la línea) para que las MAYÚSCULAS
    queden centradas en `centro`. libass centra la caja winAscent+winDescent,
    no la tinta: en Anton las mayúsculas caían ~25 px por debajo."""
    caja = info["size_ratio"] * em
    cap = info["cap"] / info["upm"] * em
    return centro - caja / 2 + info["descent"] * em + cap / 2


def _alto_mayusculas(info: dict, em: float) -> float:
    return info["cap"] / info["upm"] * em


def _bandas(info: dict, em: float, cy: float) -> tuple[float, float]:
    """(arriba, abajo) de la tinta de las mayúsculas con \\an5 en cy."""
    caja = info["size_ratio"] * em
    base = cy + caja / 2 - info["descent"] * em
    cap = info["cap"] / info["upm"] * em
    return base - cap * 1.08, base + em * 0.04


def _degradado(t: Texto, info: dict, em: float, x: float, y: float, escala: float,
               colores: tuple[str, str], entrada: str, ancho: int, contorno: float,
               sombra: float) -> list[str]:
    """La palabra en N franjas de color (capa 1) sobre su contorno (capa 0)."""
    fuente = _fuente_tags(info, em)
    esc = f"\\fscx{escala * 100:.1f}\\fscy{escala * 100:.1f}" if escala < 0.999 else ""
    pos = f"\\an5\\pos({x:.1f},{y:.1f}){fuente}{esc}"
    out = [_evento(0, t, f"{pos}{entrada}\\1a&HFF&\\bord{contorno:.1f}\\3c&H00140A0A&"
                         f"\\shad{sombra:.1f}\\4a&H60&\\4c&H00000000&", t.texto)]
    arriba, abajo = _bandas(info, em * escala, y)
    alto = (abajo - arriba) / BANDAS
    for k in range(BANDAS):
        y0 = 0 if k == 0 else arriba + k * alto
        y1 = 10 ** 5 if k == BANDAS - 1 else arriba + (k + 1) * alto
        c = _mezcla(colores[0], colores[1], k / (BANDAS - 1))
        out.append(_evento(1, t, f"{pos}{entrada}\\bord0\\shad0\\c{_ass_color(c)}"
                                 f"\\clip(0,{y0:.1f},{ancho},{y1:.1f})", t.texto))
    return out


def eventos(textos: list[Texto], ancho: int, alto: int, texto_y: float = 0.5,
            acento: str = "#FFD640") -> list[str]:
    L = _Lienzo(ancho, alto)
    out: list[str] = []
    for t in textos:
        if t.estilo == "palabra":
            info, em = _fuente("palabra"), L.em("palabra")
            texto = t.texto.upper()
            t = Texto(t.desde, t.hasta, texto, t.estilo, t.sub, t.color)
            escala = _ajuste(info, texto, em, ancho)
            colores = (t.color, "#FFFFFF") if t.color else DEGRADADO_PALABRA
            # entra con un golpe chico: 125 % → 100 % en 80 ms, fundido corto
            entrada = (f"\\fad(30,60)\\fscx{escala * 125:.0f}\\fscy{escala * 125:.0f}"
                       f"\\t(0,80,\\fscx{escala * 100:.1f}\\fscy{escala * 100:.1f})")
            out += _degradado(t, info, em, ancho / 2, _cy(info, em * escala, alto * texto_y), escala,
                              colores, entrada, ancho, contorno=em * 0.03, sombra=em * 0.05)
        elif t.estilo == "kill":
            info, em = _fuente("kill"), L.em("kill")
            texto = t.texto.upper()
            escala = _ajuste(info, texto, em, ancho)
            centro = alto * texto_y
            y = _cy(info, em * escala, centro)
            golpe = (f"\\fad(0,120)\\blur6\\fscx{escala * 160:.0f}\\fscy{escala * 160:.0f}"
                     f"\\t(0,110,\\blur0\\fscx{escala * 100:.1f}\\fscy{escala * 100:.1f})")
            borde = _ass_color(t.color or acento)
            out.append(_evento(1, t, f"\\an5\\pos({ancho / 2:.1f},{y:.1f}){_fuente_tags(info, em)}{golpe}"
                                     f"\\c&H00FFFFFF&\\bord{em * 0.06:.1f}\\3c{borde}\\shad{em * 0.05:.1f}"
                                     f"\\4a&H40&", texto))
            if t.sub:
                si, sem = _fuente("kill_sub"), L.em("kill_sub")
                # El campeón debajo: sus mayúsculas arrancan 0.22 em del nombre
                # más abajo que el pie de las del nombre.
                centro_sub = (centro + _alto_mayusculas(info, em * escala) / 2 + em * 0.22
                              + _alto_mayusculas(si, sem) / 2)
                out.append(_evento(1, t, f"\\an5\\pos({ancho / 2:.1f},{_cy(si, sem, centro_sub):.1f}){_fuente_tags(si, sem)}"
                                         f"\\fad(80,120)\\c{borde}\\bord{sem * 0.08:.1f}\\3c&H00000000&",
                                   t.sub.upper()))
        elif t.estilo == "multikill":
            info, em = _fuente("multikill"), L.em("multikill")
            texto = t.texto.upper()
            t = Texto(t.desde, t.hasta, texto, t.estilo, t.sub, t.color)
            escala = _ajuste(info, texto, em, ancho)
            entrada = (f"\\fad(0,150)\\frz-6\\fscx{escala * 190:.0f}\\fscy{escala * 190:.0f}"
                       f"\\t(0,120,\\frz0\\fscx{escala * 100:.1f}\\fscy{escala * 100:.1f})")
            out += _degradado(t, info, em, ancho / 2, _cy(info, em * escala, alto * min(0.85, texto_y + 0.16)), escala,
                              DEGRADADO_MULTIKILL, entrada, ancho, contorno=em * 0.06, sombra=em * 0.06)
        elif t.estilo == "titulo":
            info, em = _fuente("titulo"), L.em("titulo")
            escala = _ajuste(info, t.texto, em, ancho)
            esc = f"\\fscx{escala * 100:.1f}\\fscy{escala * 100:.1f}" if escala < 0.999 else ""
            out.append(_evento(1, t, f"\\an8\\pos({ancho / 2:.1f},{alto * 0.07:.1f}){_fuente_tags(info, em)}{esc}"
                                     f"\\fad(200,200)\\bord{em * 0.08:.1f}\\3c&H00000000&", t.texto))
        else:   # sub
            info, em = _fuente("sub"), L.em("sub")
            escala = _ajuste(info, t.texto, em, ancho)
            esc = f"\\fscx{escala * 100:.1f}\\fscy{escala * 100:.1f}" if escala < 0.999 else ""
            out.append(_evento(1, t, f"\\an2\\pos({ancho / 2:.1f},{alto * 0.86:.1f}){_fuente_tags(info, em)}{esc}"
                                     f"\\bord{em * 0.1:.1f}\\3c&H00000000&", t.texto))
    return out


def build_ass(textos: list[Texto], ancho: int, alto: int, texto_y: float = 0.5,
              acento: str = "#FFD640") -> str:
    return _cabecera(ancho, alto) + "\n".join(eventos(textos, ancho, alto, texto_y, acento)) + "\n"


def escribir(textos: list[Texto], ancho: int, alto: int, texto_y: float = 0.5,
             acento: str = "#FFD640") -> str | None:
    """Escribe el .ass en uploads/ y devuelve su ruta (None si no hay textos)."""
    if not textos:
        return None
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    path = os.path.join(config.UPLOAD_FOLDER, f"edit_{uuid.uuid4().hex[:12]}.ass")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build_ass(textos, ancho, alto, texto_y, acento))
    return path
