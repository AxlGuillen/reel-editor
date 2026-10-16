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
import http.client
import os
import shutil
import tempfile
import time
import unicodedata
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
class _Incompleto(OSError):
    """El servidor cortó antes de mandar todo lo que anunció (Content-Length)."""


def descargar(url: str, destino: str, timeout: float = 20, reintentos: int = 2,
              max_bytes: int | None = None) -> str:
    """Baja `url` a `destino` (urllib). Lanza RuntimeError tras los reintentos.

    - Un 4xx (salvo 408/429) no se reintenta: el link está mal y no va a cambiar.
    - Un cuerpo más corto que su Content-Length es un error (se reintenta): sin
      esto, un logo o una voz cortados pasaban como buenos.
    - Plazo total de 3× `timeout`, para que un servidor que gotea no cuelgue el job.
    - Archivo temporal propio por llamada: dos jobs con la misma URL no se pisan."""
    ultimo: Exception | None = None
    carpeta = os.path.dirname(destino) or "."
    os.makedirs(carpeta, exist_ok=True)
    for intento in range(reintentos + 1):
        fd, parcial = tempfile.mkstemp(dir=carpeta, suffix=".part")
        fh = os.fdopen(fd, "wb")
        try:
            limite = time.monotonic() + 3 * timeout
            req = urllib.request.Request(url, headers={"User-Agent": "ReelForge"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                # Lo anunciado, ANTES de leer (en HTTPResponse, .length va bajando).
                esperado = getattr(r, "length", None) or int(r.headers.get("Content-Length") or 0)
                copiado = 0
                while True:
                    bloque = r.read(64 * 1024)
                    if not bloque:
                        break
                    copiado += len(bloque)
                    if max_bytes and copiado > max_bytes:
                        raise RuntimeError(f"El archivo de {url} es demasiado grande.")
                    if time.monotonic() > limite:
                        raise TimeoutError("la descarga tardó demasiado")
                    fh.write(bloque)
                if esperado and copiado < esperado:
                    raise _Incompleto(f"llegaron {copiado} de {esperado} bytes")
            fh.close()
            os.replace(parcial, destino)
            return destino
        except RuntimeError:
            raise
        except urllib.error.HTTPError as exc:
            ultimo = RuntimeError(f"HTTP {exc.code}: {exc.reason}")
            exc.close()
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                break
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError) as exc:
            ultimo = exc
        finally:
            if not fh.closed:
                fh.close()
            if os.path.exists(parcial):
                try:
                    os.remove(parcial)
                except OSError:
                    pass
        if intento < reintentos:
            time.sleep(1.5 * (intento + 1))
    raise RuntimeError(f"No se pudo descargar {url}: {ultimo}")


def _es_imagen(path: str) -> bool:
    """Imagen de verdad: ffprobe le ve tamaño y FFmpeg decodifica un frame. (Un
    HTML o un SVG guardados como .png dan 0x0 en ffprobe; un PNG cortado no
    decodifica.)"""
    res = ffmpeg_runner.probe_resolution(path)
    if not res or res[0] <= 0 or res[1] <= 0:
        return False
    try:
        ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-xerror", "-i", path,
                               "-frames:v", "1", "-f", "null", "-"], timeout=30)
        return True
    except RuntimeError:
        return False


def logo(url: str) -> str | None:
    """PNG local del logo (cache por hash de la URL), o None si no hay o falla:
    la portada pone el código del equipo en su lugar. Nunca lanza."""
    if not url:
        return None
    os.makedirs(LOGOS_CACHE, exist_ok=True)
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    ext = ext if ext in (".png", ".jpg", ".jpeg", ".webp") else ".png"
    path = os.path.join(LOGOS_CACHE, hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ext)
    try:
        if not os.path.isfile(path):
            descargar(url, path, timeout=8, reintentos=1, max_bytes=MAX_LOGO_BYTES)
        if not _es_imagen(path):
            raise RuntimeError("no es una imagen que FFmpeg pueda leer")
        return path
    except Exception as exc:  # noqa: BLE001 - un logo nunca tumba el job
        if os.path.isfile(path):
            try:
                os.remove(path)          # que no quede envenenado en el cache
            except OSError:
                pass
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


def achicar(texto: str, fuente: str, tam: int, ancho_max: float, min_tam: int) -> int:
    """Tamaño para que `texto` entre en UN renglón de `ancho_max` px (lo usa la fase)."""
    info = fonts.font_info(os.path.basename(fuente))
    s = tam
    while s > min_tam and fonts.text_width(info, texto, s) > ancho_max:
        s -= 2
    return s


def tiene_glifos(texto: str, fuente: str) -> bool:
    """¿La fuente tiene todos los caracteres? (Anton no trae coreano, por ejemplo:
    saldrían cajitas.)"""
    info = fonts.font_info(os.path.basename(fuente))
    return all(ord(c) in info["adv"] for c in texto if not c.isspace())


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


# --- Plantilla --------------------------------------------------------------------
# La misma plantilla en dos tamaños: la portada vertical (portada.jpg y el modo
# "aparte") y la placa horizontal (el modo "encima", sobre el gameplay del clip 1).
# Mismo fondo, mismas fuentes, mismos colores, mismos elementos.
V = {   # portada 1080x1920: arriba y abajo quedan franjas libres (las tapa la UI de TikTok)
    "W": 1080, "H": 1920,
    "mapa_y": 300, "mapa_tam": 190, "mapa_borde": 10,
    "fase_y": 545, "fase_tam": 46, "fase_min": 28, "fase_ancho": 900,
    "logos_y": 900, "logo_lado": 340, "logo_x": (290, 790),   # lejos del riel derecho de TikTok
    "vs_tam": 140, "vs_borde": 8,
    "nombre_y": 1100, "nombre_tam": 74, "nombre_min": 48, "nombre_ancho": 450, "nombre_borde": 5,
    "serie_y": 1360, "serie_tam": 46,
    "marca_alto": 70, "marca_x": 70, "marca_y": 1920 - 430,
}
P = {   # placa horizontal (80 % del ancho del reel)
    "W": 864, "H": 560,
    "mapa_y": 34, "mapa_tam": 108, "mapa_borde": 6,
    "fase_y": 168, "fase_tam": 30, "fase_min": 18, "fase_ancho": 780,
    "logos_y": 318, "logo_lado": 180, "logo_x": (196, 668),
    "vs_tam": 84, "vs_borde": 5,
    "nombre_y": 424, "nombre_tam": 46, "nombre_min": 30, "nombre_ancho": 300, "nombre_borde": 3,
    "serie_y": 500, "serie_tam": 28,
}
PLACA_RADIO = 26           # esquinas, como la placa del HUD de vertical_convert
PLACA_BORDE = 4            # borde blanco
PLACA_SOMBRA = 30          # margen para la sombra (blur)
PLACA_SOMBRA_DY = 8


def _componer(enc: Encuentro, T: dict, t: _Textos, fondo: str | None) -> tuple[list[str], list[str], dict]:
    """Entradas (-i …) y filtros de la tarjeta de la plantilla `T`: termina en
    [tarjeta] (rgba, T["W"]xT["H"]). Siempre arranca en la entrada 0."""
    W, H = T["W"], T["H"]
    entradas, partes = [], []
    usa_fondo = bool(fondo and os.path.isfile(fondo) and ffmpeg_runner.probe_resolution(fondo))
    if usa_fondo:
        entradas += ["-i", fondo]
        # Un velo oscuro leve: el texto se lee sobre cualquier fondo.
        partes.append(f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                      "format=rgba,drawbox=c=black@0.28:t=fill[fondo]")
    else:
        c0, c1 = DEGRADADO
        # De arriba a abajo. Los extremos tienen que caer DENTRO del cuadro: fuera de
        # rango, FFmpeg los elige al azar y la plantilla salía distinta cada vez.
        entradas += ["-f", "lavfi", "-i", f"gradients=s={W}x{H}:c0={c0}:c1={c1}:x0=0:y0=0:x1=0:y1={H - 1}"
                                          ":nb_colors=2:speed=0:seed=0:d=1:r=1"]
        partes.append("[0:v]format=rgba[fondo]")
    n = 1

    lado = T["logo_lado"]
    fuentes_logo = {}
    for lado_eq, path, codigo in (("azul", enc.logo_azul, enc.codigo_azul),
                                  ("rojo", enc.logo_rojo, enc.codigo_rojo)):
        if path and os.path.isfile(path):
            entradas += ["-i", path]
            partes.append(f"[{n}:v]scale={lado}:{lado}:force_original_aspect_ratio=decrease,"
                          f"format=rgba[logo_{lado_eq}]")
            n += 1
            fuentes_logo[lado_eq] = "logo"
        else:
            partes.append(_logo_respaldo(codigo, lado, t, f"[logo_{lado_eq}]"))
            fuentes_logo[lado_eq] = "codigo"
    ly = T["logos_y"]
    xa, xr = T["logo_x"]
    partes.append(f"[fondo][logo_azul]overlay=x={xa}-w/2:y={ly}-h/2[p1]")
    partes.append(f"[p1][logo_rojo]overlay=x={xr}-w/2:y={ly}-h/2[p2]")

    textos = [
        t.drawtext(f"MAPA {enc.mapa}", TITULO, T["mapa_tam"], "(w-text_w)/2", str(T["mapa_y"]),
                   borde=T["mapa_borde"], sombra=True),
        t.drawtext("VS", TITULO, T["vs_tam"], "(w-text_w)/2", f"{ly}-text_h/2", borde=T["vs_borde"]),
    ]
    fase = enc.fase.upper()
    if fase and tiene_glifos(fase, TEXTO):
        tam = achicar(fase, TEXTO, T["fase_tam"], T["fase_ancho"], T["fase_min"])
        textos.append(t.drawtext(fase, TEXTO, tam, "(w-text_w)/2", str(T["fase_y"]),
                                 color=ACENTO, sombra=True))
    dos_renglones = False
    nombres = [(n if tiene_glifos(n, TITULO) else c, x)
               for n, c, x in ((enc.azul, enc.codigo_azul, xa), (enc.rojo, enc.codigo_rojo, xr))]
    for nombre, x in nombres:
        lineas, tam = ajustar_texto(nombre, TITULO, T["nombre_tam"], T["nombre_ancho"], T["nombre_min"])
        dos_renglones = dos_renglones or len(lineas) > 1
        for i, linea in enumerate(lineas):
            textos.append(t.drawtext(linea, TITULO, tam, f"{x}-text_w/2",
                                     str(T["nombre_y"] + round(i * tam * 1.12)),
                                     borde=T["nombre_borde"], sombra=True))
    # En la placa no hay lugar para la serie si un nombre ocupa dos renglones.
    if enc.serie_marcador and not (dos_renglones and T is P):
        serie = " · ".join(x for x in (f"Serie {enc.serie_marcador}", _fecha(enc.fecha)) if x)
        textos.append(t.drawtext(serie, TEXTO, T["serie_tam"], "(w-text_w)/2", str(T["serie_y"]),
                                 sombra=True))
    partes.append("[p2]" + ",".join(textos) + "[tarjeta]")
    return entradas, partes, {"fondo": "imagen" if usa_fondo else "degradado", "logos": fuentes_logo}


def _limpiar(t: _Textos) -> None:
    for i in range(1, t.n + 1):
        try:
            os.remove(os.path.join(t.carpeta, f"portada_txt_{i}.txt"))
        except OSError:
            pass


def render_portada(enc: Encuentro, carpeta: str, fondo: str | None = FONDO) -> dict:
    """Dibuja la portada vertical en `carpeta`/portada.png (+ portada.jpg, ~90 de
    calidad). Devuelve {png, jpg, fondo: "imagen"|"degradado", logos: {azul, rojo}}."""
    os.makedirs(carpeta, exist_ok=True)
    t = _Textos(carpeta)
    entradas, partes, info = _componer(enc, V, t, fondo)
    cmd = [config.FFMPEG_PATH, "-y", "-v", "error", *entradas]
    final = "[tarjeta]"
    caja = None
    if enc.marca and os.path.isfile(enc.marca):
        from modules.competitivo.layout import recorte_marca
        caja = recorte_marca(enc.marca)
    if caja:
        n = entradas.count("-i")
        cmd += ["-i", enc.marca]
        # Marca 4xl: chica, translúcida, sin placa. Tiene que pasar desapercibida.
        # Se recorta su margen transparente antes de escalar (si no, la figura
        # quedaba a la mitad del alto pedido).
        pw, ph, px, py = caja
        partes.append(f"[{n}:v]crop={pw}:{ph}:{px}:{py},scale=-2:{V['marca_alto']},format=rgba,"
                      "colorchannelmixer=aa=0.4[marca]")
        partes.append(f"[tarjeta][marca]overlay=W-w-{V['marca_x']}:{V['marca_y']}-h[con_marca]")
        final = "[con_marca]"
    partes.append(f"{final}format=rgb24[out]")
    png = os.path.join(carpeta, "portada.png")
    jpg = os.path.join(carpeta, "portada.jpg")
    try:
        ffmpeg_runner.capture(cmd + ["-filter_complex", ";".join(partes), "-map", "[out]",
                                     "-frames:v", "1", png], cwd=config.BASE_DIR)
    finally:
        _limpiar(t)
    ffmpeg_runner.capture([config.FFMPEG_PATH, "-y", "-v", "error", "-i", png,
                           "-q:v", "2", "-frames:v", "1", jpg])
    return {"png": png, "jpg": jpg, **info}


def render_placa(enc: Encuentro, carpeta: str, fondo: str | None = FONDO) -> str:
    """La placa del modo "encima" en `carpeta`/placa.png (rgba): la plantilla en
    horizontal con esquinas redondeadas, borde blanco y sombra, como la placa
    del HUD. El PNG incluye el margen de la sombra; se centra tal cual."""
    from modules.vertical_convert.processor import _rounded_mask_expr
    os.makedirs(carpeta, exist_ok=True)
    t = _Textos(carpeta)
    entradas, partes, _ = _componer(enc, P, t, fondo)
    W, H, b, m = P["W"], P["H"], PLACA_BORDE, PLACA_SOMBRA
    bw, bh = W + 2 * b, H + 2 * b
    partes += [
        f"color=c=white:s={W}x{H}:d=1,format=gray,geq=lum='{_rounded_mask_expr(PLACA_RADIO)}'[m_tarjeta]",
        "[tarjeta][m_tarjeta]alphamerge[tarjeta_r]",
        f"color=c=white:s={bw}x{bh}:d=1,format=rgba[borde_c]",
        f"color=c=white:s={bw}x{bh}:d=1,format=gray,geq=lum='{_rounded_mask_expr(PLACA_RADIO + b)}'[m_borde]",
        "[borde_c][m_borde]alphamerge[borde]",
        f"[borde][tarjeta_r]overlay={b}:{b},split[placa_a][placa_b]",
        f"[placa_b]geq=r=0:g=0:b=0:a='0.55*alpha(X,Y)',pad={bw + 2 * m}:{bh + 2 * m}:{m}:{m}:color=black@0,"
        f"gblur=sigma=12[sombra]",
        f"color=c=black@0:s={bw + 2 * m}x{bh + 2 * m}:d=1,format=rgba[lienzo]",
        f"[lienzo][sombra]overlay=0:{PLACA_SOMBRA_DY}[con_sombra]",
        f"[con_sombra][placa_a]overlay={m}:{m},format=rgba[out]",
    ]
    png = os.path.join(carpeta, "placa.png")
    try:
        ffmpeg_runner.capture([config.FFMPEG_PATH, "-y", "-v", "error", *entradas,
                               "-filter_complex", ";".join(partes), "-map", "[out]",
                               "-frames:v", "1", png], cwd=config.BASE_DIR)
    finally:
        _limpiar(t)
    return png


def guardar_portada(info: dict, destino_dir: str) -> str:
    """Copia portada.jpg a la carpeta de la serie y devuelve su nombre."""
    shutil.copyfile(info["jpg"], os.path.join(destino_dir, "portada.jpg"))
    return "portada.jpg"


# --- Voz ----------------------------------------------------------------------------
MAX_VOZ_S = 8.0          # tope de la voz
COLA_S = 0.4             # la intro dura la voz + esta cola
STING_VOL = 0.35         # el sting (si existe) suena debajo de la voz
MAX_VOZ_BYTES = 20 * 1024 * 1024


@dataclass
class Voz:
    path: str
    duracion: float      # de la voz
    D: float             # de la intro: voz + cola


def preparar_voz(url: str, carpeta: str) -> Voz:
    """Baja la voz (una vez) y mide su duración. Error claro si no es audio o
    si dura más de MAX_VOZ_S."""
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    path = os.path.join(carpeta, "intro_voz" + (ext if ext in (".mp3", ".wav", ".m4a", ".ogg", ".opus") else ".mp3"))
    descargar(url, path, timeout=20, reintentos=2, max_bytes=MAX_VOZ_BYTES)
    dur = ffmpeg_runner.probe_duration(path) if ffmpeg_runner.has_audio_stream(path) else None
    if not dur:
        raise RuntimeError("No se pudo leer el audio de la intro: ¿el link es un mp3?")
    if dur > MAX_VOZ_S:
        raise RuntimeError(f"La voz de la intro dura {dur:.1f} s; máximo {MAX_VOZ_S:g}.")
    return Voz(path, round(dur, 3), round(dur + COLA_S, 3))


def modo_para_clip1(D: float, dur_clip: float, t_primera_kill: float) -> tuple[str, str | None]:
    """¿La intro puede ir "encima" del clip 1? Si no, cae a "aparte" (y el motivo).

    - el clip tiene que durar al menos D + 2 s;
    - su primera kill tiene que caer después de que la placa se fue (D + 0.5 s):
      si no, la placa la taparía."""
    if dur_clip < D + 2:
        return "aparte", f"el clip 1 dura {dur_clip:.1f} s y la intro {D:.1f} s"
    if t_primera_kill < D + 0.5:
        return "aparte", f"la primera kill del clip 1 cae a los {t_primera_kill:.1f} s y la placa la taparía"
    return "encima", None


def sting() -> str | None:
    """assets/competitivo/sting.mp3 si existe (opcional, sin parámetro)."""
    return STING if os.path.isfile(STING) else None


# Mismos parámetros de salida que los clips (layout.build_command): el resumen
# se une con concat -c copy y toma la cabecera de video del PRIMER archivo,
# que es la intro. Por eso también va bt709/tv, como los clips: con otra
# matriz, todo el resumen se vería con los colores corridos.
COLOR_SALIDA = ("scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
                "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv,setsar=1")


def salida_video_audio() -> list[str]:
    return [
        "-r", "60",
        *ffmpeg_runner.video_encode_flags(),
        "-maxrate", "16M", "-bufsize", "32M",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
    ]


def _audio_intro(voz_idx: int, sting_idx: int | None, D: float) -> list[str]:
    """Voz (+ sting al 35 %) de largo exacto D → [aout]."""
    voz = f"[{voz_idx}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{D:.3f}"
    if sting_idx is None:
        return [voz + "[aout]"]
    return [
        voz + "[voz]",
        f"[{sting_idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={STING_VOL},"
        f"apad,atrim=0:{D:.3f}[sting]",
        "[voz][sting]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95[aout]",
    ]


def build_intro_aparte(portada_png: str, voz: Voz, out: str) -> list[str]:
    """Segmento intro.mp4 de duración D: la portada con un zoom lento
    (1.00 → 1.06), un fade-in de 0.2 s y la voz encima.

    El zoom se hace sobre la portada al doble de tamaño: zoompan recorta en
    píxeles enteros, y así cada paso es medio píxel de la salida (sin saltos)."""
    D = voz.D
    n = max(1, round(D * 60))
    s = sting()
    cmd = [config.FFMPEG_PATH, "-y", "-loop", "1", "-framerate", "60", "-t", f"{D:.3f}",
           "-i", portada_png, "-i", voz.path]
    if s:
        cmd += ["-i", s]
    video = (f"[0:v]scale=2160:3840:flags=bicubic,"
             f"zoompan=z='1+0.06*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
             f"d=1:s=1080x1920:fps=60,fade=t=in:st=0:d=0.2,{COLOR_SALIDA}[vout]")
    partes = [video, *_audio_intro(1, 2 if s else None, D)]
    return cmd + ["-filter_complex", ";".join(partes), "-map", "[vout]", "-map", "[aout]",
                  "-t", f"{D:.3f}", *salida_video_audio(), out]
