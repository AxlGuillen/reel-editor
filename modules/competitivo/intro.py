"""Intro con voz y portada del módulo competitivo.

La VOZ no la genera ReelForge: llega hecha, como link de un mp3 (la genera
Claude con Fish Audio por su MCP). Acá solo se baja. Lo visual sale de una
plantilla FIJA (siempre igual, solo cambian los datos: es lo que la hace
reconocible):

    portada vertical 1080x1920   portada.jpg de la serie y el segmento "aparte"
    placa horizontal             la intro "encima" del clip 1 (misma plantilla)

Nada de esto puede tumbar un render: sin fondo → degradado oscuro; sin logo (o
si falla la descarga) → el código del equipo dentro de un círculo.

Texto: drawtext con `textfile` y rutas RELATIVAS a BASE_DIR (cwd=BASE_DIR), como
el módulo watermark: el ':' del drive de Windows rompe el parser de filtros.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date

import config
from core import ffmpeg_runner
from modules.subtitles import fonts

CARPETA = os.path.join(config.ASSETS_FOLDER, "competitivo")
FONDO = os.path.join(CARPETA, "fondo_portada.png")      # lo copia Axl (opcional)
STING = os.path.join(CARPETA, "sting.mp3")              # opcional
LOGOS_CACHE = os.path.join(config.BASE_DIR, "cache", "competitivo", "logos")

TITULO = os.path.join(config.FONTS_FOLDER, "Anton-Regular.ttf")
TEXTO = os.path.join(config.FONTS_FOLDER, "Poppins-Bold.ttf")
ACENTO = "0xD9F24A"                  # el lima del diseño de la app (--accent)
ACENTO_RGB = (217, 242, 74)
CIRCULO_RGB = (27, 34, 51)           # relleno del logo de respaldo
DEGRADADO = ("0x090C16", "0x26103A") # fondo de respaldo, de arriba a abajo
MESES = ("ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC")

MAX_LOGO_BYTES = 10 * 1024 * 1024


@dataclass
class Encuentro:
    """Datos de la portada. `azul`/`rojo` son los nombres a mostrar (o los
    códigos del feed si no llegaron nombres); los logos son rutas locales."""
    mapa: int
    codigo_azul: str
    codigo_rojo: str
    azul: str = ""
    rojo: str = ""
    fase: str = ""
    logo_azul: str | None = None
    logo_rojo: str | None = None
    serie_marcador: str = ""
    fecha: date | None = None
    marca: str | None = None          # PNG del watermark para la "marca 4xl" (sutil)

    def __post_init__(self):
        self.azul = self.azul or self.codigo_azul
        self.rojo = self.rojo or self.codigo_rojo


# --- Descargas --------------------------------------------------------------------
def descargar(url: str, destino: str, timeout: float = 20, reintentos: int = 2,
              max_bytes: int | None = None) -> str:
    """Baja `url` a `destino` (urllib). Lanza RuntimeError tras los reintentos."""
    ultimo: Exception | None = None
    for intento in range(reintentos + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ReelForge"})
            with urllib.request.urlopen(req, timeout=timeout) as r, open(destino + ".part", "wb") as fh:
                copiado = 0
                while True:
                    bloque = r.read(64 * 1024)
                    if not bloque:
                        break
                    copiado += len(bloque)
                    if max_bytes and copiado > max_bytes:
                        raise RuntimeError(f"El archivo de {url} es demasiado grande.")
                    fh.write(bloque)
            os.replace(destino + ".part", destino)
            return destino
        except RuntimeError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            ultimo = exc
            if intento < reintentos:
                time.sleep(1.5 * (intento + 1))
        finally:
            if os.path.exists(destino + ".part"):
                os.remove(destino + ".part")
    raise RuntimeError(f"No se pudo descargar {url}: {ultimo}")


def logo(url: str) -> str | None:
    """PNG local del logo (cache por hash de la URL), o None si no hay o falla:
    la portada pone el código del equipo en su lugar."""
    if not url:
        return None
    os.makedirs(LOGOS_CACHE, exist_ok=True)
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    ext = ext if ext in (".png", ".jpg", ".jpeg", ".webp") else ".png"
    path = os.path.join(LOGOS_CACHE, hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ext)
    try:
        if not os.path.isfile(path):
            descargar(url, path, max_bytes=MAX_LOGO_BYTES)
        if not ffmpeg_runner.probe_resolution(path):
            raise RuntimeError("no es una imagen")
        return path
    except RuntimeError as exc:
        if os.path.isfile(path):
            os.remove(path)
        print(f"[competitivo] logo {url}: {exc}".encode("ascii", "replace").decode())
        return None


# --- Texto ------------------------------------------------------------------------
def ajustar_texto(texto: str, fuente: str, tam: int, ancho_max: float,
                  min_tam: int) -> tuple[list[str], int]:
    """Renglones y tamaño para que `texto` entre en `ancho_max` px: primero
    achica hasta `min_tam`; si no alcanza, lo parte en dos renglones
    ("Karmine / Corp") y vuelve a achicar si hace falta."""
    info = fonts.font_info(os.path.basename(fuente))

    def ancho(t: str, s: int) -> float:
        return fonts.text_width(info, t, s)

    s = tam
    while s > min_tam and ancho(texto, s) > ancho_max:
        s -= 2
    if ancho(texto, s) <= ancho_max:
        return [texto], s
    palabras = texto.split()
    if len(palabras) > 1:
        cortes = [(" ".join(palabras[:i]), " ".join(palabras[i:])) for i in range(1, len(palabras))]
        a, b = min(cortes, key=lambda c: max(ancho(c[0], tam), ancho(c[1], tam)))
        s = tam
        while s > 12 and max(ancho(a, s), ancho(b, s)) > ancho_max:
            s -= 2
        return [a, b], s
    return [texto], max(12, int(s * ancho_max / max(ancho(texto, s), 1)))


def _rel(path: str) -> str:
    """Ruta relativa a BASE_DIR, con '/', entre comillas (fontfile/textfile)."""
    return "'" + os.path.relpath(path, config.BASE_DIR).replace("\\", "/") + "'"


class _Textos:
    """Escribe cada texto en un textfile (sin escaping) dentro de `carpeta`."""

    def __init__(self, carpeta: str):
        self.carpeta, self.n = carpeta, 0

    def archivo(self, texto: str) -> str:
        self.n += 1
        path = os.path.join(self.carpeta, f"portada_txt_{self.n}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(texto)
        return _rel(path)

    def drawtext(self, texto: str, fuente: str, tam: int, x: str, y: str, color: str = "white",
                 borde: int = 0, sombra: bool = False) -> str:
        f = (f"drawtext=fontfile={_rel(fuente)}:textfile={self.archivo(texto)}:expansion=none:"
             f"fontsize={tam}:fontcolor={color}:x={x}:y={y}")
        if borde:
            f += f":borderw={borde}:bordercolor=black"
        if sombra:
            f += ":shadowx=0:shadowy=4:shadowcolor=black@0.55"
        return f


def _fecha(f: date | None) -> str:
    return f"{f.day} {MESES[f.month - 1]}" if f else ""


def _logo_respaldo(codigo: str, lado: int, t: _Textos, salida: str) -> str:
    """Círculo oscuro con borde lima y el código del equipo en Anton."""
    c = (lado - 1) / 2
    r = lado / 2 - 2
    d = f"hypot(X-{c},Y-{c})"
    anillo = f"gt({d},{r - 7})"
    canal = [f"if({anillo},{a},{b})" for a, b in zip(ACENTO_RGB, CIRCULO_RGB)]
    lineas, tam = ajustar_texto(codigo, TITULO, int(lado * 0.42), lado * 0.72, int(lado * 0.2))
    return (f"color=c=black:s={lado}x{lado}:d=1,format=rgba,"
            f"geq=r='{canal[0]}':g='{canal[1]}':b='{canal[2]}':a='255*clip({r}-{d},0,1)',"
            + t.drawtext(" ".join(lineas), TITULO, tam, "(w-text_w)/2", "(h-text_h)/2") + salida)


# --- Portada vertical ---------------------------------------------------------------
# Plantilla 1080x1920. Arriba y abajo quedan franjas libres (las tapa la UI de TikTok).
V = {
    "W": 1080, "H": 1920,
    "mapa_y": 300, "mapa_tam": 190,
    "fase_y": 545, "fase_tam": 46,
    "logos_y": 900, "logo_lado": 340, "logo_x": (270, 810),
    "vs_tam": 140,
    "nombre_y": 1100, "nombre_tam": 74, "nombre_min": 48, "nombre_ancho": 450,
    "serie_y": 1360, "serie_tam": 46,
    "marca_alto": 70, "marca_x": 70, "marca_y": 1920 - 430,
}


def render_portada(enc: Encuentro, carpeta: str, fondo: str | None = FONDO) -> dict:
    """Dibuja la portada vertical en `carpeta`/portada.png (+ portada.jpg, ~90 de
    calidad). Devuelve {png, jpg, fondo: "imagen"|"degradado", logos: {azul, rojo}}."""
    os.makedirs(carpeta, exist_ok=True)
    t = _Textos(carpeta)
    W, H = V["W"], V["H"]
    cmd = [config.FFMPEG_PATH, "-y", "-v", "error"]
    entradas, partes = 0, []
    usa_fondo = bool(fondo and os.path.isfile(fondo) and ffmpeg_runner.probe_resolution(fondo))
    if usa_fondo:
        cmd += ["-i", fondo]
        # Un velo oscuro leve: el texto se lee sobre cualquier fondo.
        partes.append(f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                      "format=rgba,drawbox=c=black@0.28:t=fill[fondo]")
    else:
        c0, c1 = DEGRADADO
        cmd += ["-f", "lavfi", "-i", f"gradients=s={W}x{H}:c0={c0}:c1={c1}:x0=0:y0=0:x1={W}:y1={H}"
                                      ":nb_colors=2:speed=0:d=1:r=1"]
        partes.append("[0:v]format=rgba[fondo]")
    entradas += 1

    lado = V["logo_lado"]
    fuentes_logo = {}
    for lado_eq, path, codigo in (("azul", enc.logo_azul, enc.codigo_azul),
                                  ("rojo", enc.logo_rojo, enc.codigo_rojo)):
        if path and os.path.isfile(path):
            cmd += ["-i", path]
            partes.append(f"[{entradas}:v]scale={lado}:{lado}:force_original_aspect_ratio=decrease,"
                          f"format=rgba[logo_{lado_eq}]")
            entradas += 1
            fuentes_logo[lado_eq] = "logo"
        else:
            partes.append(_logo_respaldo(codigo, lado, t, f"[logo_{lado_eq}]"))
            fuentes_logo[lado_eq] = "codigo"
    ly = V["logos_y"]
    xa, xr = V["logo_x"]
    partes.append(f"[fondo][logo_azul]overlay=x={xa}-w/2:y={ly}-h/2[p1]")
    partes.append(f"[p1][logo_rojo]overlay=x={xr}-w/2:y={ly}-h/2[p2]")

    textos = [
        t.drawtext(f"MAPA {enc.mapa}", TITULO, V["mapa_tam"], "(w-text_w)/2", str(V["mapa_y"]),
                   borde=10, sombra=True),
        t.drawtext("VS", TITULO, V["vs_tam"], "(w-text_w)/2", f"{ly}-text_h/2", borde=8),
    ]
    if enc.fase:
        textos.append(t.drawtext(enc.fase.upper(), TEXTO, V["fase_tam"], "(w-text_w)/2",
                                 str(V["fase_y"]), color=ACENTO, sombra=True))
    for nombre, x in ((enc.azul, xa), (enc.rojo, xr)):
        lineas, tam = ajustar_texto(nombre, TITULO, V["nombre_tam"], V["nombre_ancho"], V["nombre_min"])
        for i, linea in enumerate(lineas):
            textos.append(t.drawtext(linea, TITULO, tam, f"{x}-text_w/2",
                                     str(V["nombre_y"] + round(i * tam * 1.12)), borde=5, sombra=True))
    if enc.serie_marcador:
        serie = " · ".join(x for x in (f"Serie {enc.serie_marcador}", _fecha(enc.fecha)) if x)
        textos.append(t.drawtext(serie, TEXTO, V["serie_tam"], "(w-text_w)/2", str(V["serie_y"]),
                                 sombra=True))
    partes.append("[p2]" + ",".join(textos) + "[p3]")

    final = "[p3]"
    if enc.marca and os.path.isfile(enc.marca):
        cmd += ["-i", enc.marca]
        # Marca 4xl: chica, translúcida, sin placa. Tiene que pasar desapercibida.
        partes.append(f"[{entradas}:v]scale=-2:{V['marca_alto']},format=rgba,colorchannelmixer=aa=0.4[marca]")
        partes.append(f"[p3][marca]overlay=W-w-{V['marca_x']}:{V['marca_y']}-h[p4]")
        final = "[p4]"
        entradas += 1
    partes.append(f"{final}format=rgb24[out]")

    png = os.path.join(carpeta, "portada.png")
    jpg = os.path.join(carpeta, "portada.jpg")
    ffmpeg_runner.capture(cmd + ["-filter_complex", ";".join(partes), "-map", "[out]",
                                 "-frames:v", "1", png], cwd=config.BASE_DIR)
    ffmpeg_runner.capture([config.FFMPEG_PATH, "-y", "-v", "error", "-i", png,
                           "-q:v", "2", "-frames:v", "1", jpg])
    for i in range(1, t.n + 1):
        try:
            os.remove(os.path.join(carpeta, f"portada_txt_{i}.txt"))
        except OSError:
            pass
    return {"png": png, "jpg": jpg, "fondo": "imagen" if usa_fondo else "degradado",
            "logos": fuentes_logo}


def guardar_portada(info: dict, destino_dir: str) -> str:
    """Copia portada.jpg a la carpeta de la serie y devuelve su nombre."""
    shutil.copyfile(info["jpg"], os.path.join(destino_dir, "portada.jpg"))
    return "portada.jpg"
