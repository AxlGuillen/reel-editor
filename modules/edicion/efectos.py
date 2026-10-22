"""Filtros de FFmpeg para los acentos y el color de un edit (puros: arman texto).

Medido en el FFmpeg del sistema (1080x1920, 60 fps):
  flash   eq=brightness con eval=frame: se evalúa por CUADRO, no por píxel
          (la mitad de caro que hue=b, y blend/geq por píxel no rinden).
  zoom +  zoompan con d=1: un cuadro de salida por cuadro de entrada; z, x e y
  shake   son expresiones en el tiempo del cuadro (in_time). El golpe de zoom y
          la sacudida van en el MISMO zoompan: la sacudida necesita margen, así
          que mientras sacude se suma un zoom chico que lo da.
  rgb     rgbashift con enable: las capas roja y azul corridas en los golpes.

Cada acento es un pulso: sube en `ataque` s y cae al cuadrado en `dur` s.
"""
from __future__ import annotations

import os

import config
from modules.edicion.edl import Evento

ATAQUE_ZOOM = 0.04
FLASH_MAX = 0.75              # eq brightness (−1..1): 0.75 ≈ casi blanco
ZOOM_MAX = 0.12               # golpe de zoom a intensidad 1 (12 %)
SHAKE_MAX = 0.025             # sacudida a intensidad 1: 2.5 % del ancho
SHAKE_HZ = 13.0
RGB_PX = 10

# Looks: cadenas de FFmpeg. "dorado" es el del edit de referencia (Levi):
# sombras hacia el teal, luces hacia el ámbar y saturación alta.
LOOKS = {
    "neutro": ("Sin cambios", ""),
    "vivo": ("Más contraste y saturación", "eq=contrast=1.08:saturation=1.25:gamma=0.98"),
    "dorado": ("Sombras teal, luces ámbar, muy saturado (anime edit)",
               "colorbalance=rs=-0.06:gs=0.01:bs=0.06:rm=0.04:gm=0.02:bm=-0.04:rh=0.08:gh=0.03:bh=-0.08,"
               "eq=contrast=1.08:saturation=1.38:gamma=0.97"),
    "cine": ("Contraste de película, un poco desaturado",
             "eq=contrast=1.12:saturation=0.92,curves=preset=medium_contrast"),
    "frio": ("Azul frío y contraste", "colorbalance=rs=-0.05:bs=0.07:rh=-0.03:bh=0.05,eq=contrast=1.1:saturation=1.1"),
    "noche": ("Oscuro con sombras azules",
              "colorbalance=rs=-0.04:bs=0.08,eq=brightness=-0.05:contrast=1.15:saturation=1.05:gamma=0.92"),
    "fuego": ("Rojo y naranja intensos", "colorbalance=rs=0.08:gs=0.02:bs=-0.08:rh=0.06:bh=-0.06,eq=saturation=1.3:contrast=1.1"),
}


def looks() -> list[dict]:
    return [{"id": k, "descripcion": v[0]} for k, v in LOOKS.items()]


def luts() -> list[str]:
    if not os.path.isdir(config.LUTS_FOLDER):
        return []
    return sorted(f for f in os.listdir(config.LUTS_FOLDER) if f.lower().endswith(".cube"))


def lut_filtro(nombre: str) -> str:
    """lut3d con la ruta RELATIVA a BASE_DIR (FFmpeg corre con cwd=BASE_DIR):
    el «C:» de una ruta absoluta en Windows rompe el parser de filtros."""
    path = os.path.join(config.LUTS_FOLDER, os.path.basename(nombre))
    rel = os.path.relpath(path, config.BASE_DIR).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f"lut3d=file='{rel}':interp=tetrahedral"


def color(look: str, lut: str = "") -> str:
    """Cadena de color: el LUT (si hay) y encima el look."""
    if look not in LOOKS:
        raise ValueError(f"Look desconocido: {look!r}. Opciones: {', '.join(LOOKS)}.")
    partes = []
    if lut:
        partes.append(lut_filtro(lut))
    if LOOKS[look][1]:
        partes.append(LOOKS[look][1])
    return ",".join(partes)


def _f(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") if x else "0"


def _pulso(t0: float, dur: float, ataque: float = 0.0, var: str = "t") -> str:
    """0 → 1 en `ataque` s antes de t0, y 1 → 0 al cuadrado en `dur` s después."""
    caida = f"if(between({var},{_f(t0)},{_f(t0 + dur)}),pow(1-({var}-{_f(t0)})/{_f(dur)},2),0)"
    if ataque <= 0:
        return caida
    sube = f"if(between({var},{_f(t0 - ataque)},{_f(t0)}),({var}-{_f(t0 - ataque)})/{_f(ataque)},0)"
    return f"({sube}+{caida})"


def _suma(terminos: list[str]) -> str:
    return "+".join(terminos) if terminos else "0"


def flash(eventos: list[Evento], intensidad: float = 1.0) -> str:
    """eq que blanquea en cada flash (vacío si no hay ninguno)."""
    fl = [e for e in eventos if e.tipo == "flash"]
    if not fl:
        return ""
    expr = _suma([f"{_f(FLASH_MAX * e.intensidad * intensidad)}*{_pulso(e.t, e.dur)}" for e in fl])
    return f"eq=brightness='min({_f(FLASH_MAX)},{expr})':eval=frame"


def camara(eventos: list[Evento], ancho: int, alto: int, fps: int, intensidad: float = 1.0) -> str:
    """zoompan con los golpes de zoom y las sacudidas (vacío si no hay)."""
    zooms = [e for e in eventos if e.tipo == "zoom"]
    shakes = [e for e in eventos if e.tipo == "shake"]
    if not zooms and not shakes:
        return ""
    v = "in_time"
    z = [f"{_f(ZOOM_MAX * e.intensidad * intensidad)}*{_pulso(e.t, e.dur, ATAQUE_ZOOM, v)}" for e in zooms]
    # Margen para sacudir: el doble de la amplitud, mientras dura la sacudida.
    z += [f"{_f(2 * SHAKE_MAX * e.intensidad * intensidad)}*{_pulso(e.t, e.dur, 0, v)}" for e in shakes]
    sx = [f"{_f(SHAKE_MAX * e.intensidad * intensidad)}*iw*{_pulso(e.t, e.dur, 0, v)}"
          f"*sin({_f(2 * 3.14159 * SHAKE_HZ)}*{v}+{_f(i * 1.7)})" for i, e in enumerate(shakes)]
    sy = [f"{_f(SHAKE_MAX * e.intensidad * intensidad)}*ih*{_pulso(e.t, e.dur, 0, v)}"
          f"*cos({_f(2 * 3.14159 * SHAKE_HZ * 1.3)}*{v}+{_f(i * 2.3)})" for i, e in enumerate(shakes)]
    zexpr = f"1+{_suma(z)}"
    x = f"clip(iw/2-iw/zoom/2+{_suma(sx)},0,iw-iw/zoom)"
    y = f"clip(ih/2-ih/zoom/2+{_suma(sy)},0,ih-ih/zoom)"
    return f"zoompan=z='{zexpr}':x='{x}':y='{y}':d=1:s={ancho}x{alto}:fps={fps}"


def rgb(eventos: list[Evento], intensidad: float = 1.0) -> str:
    """rgbashift encendido durante los eventos "rgb" (vacío si no hay)."""
    ev = [e for e in eventos if e.tipo == "rgb"]
    if not ev:
        return ""
    px = max(2, int(round(RGB_PX * intensidad * max(e.intensidad for e in ev))))
    enable = "+".join(f"between(t,{_f(e.t)},{_f(e.t + e.dur)})" for e in ev)
    return f"rgbashift=rh=-{px}:bh={px}:enable='{enable}'"


def glow(entrada: str, salida: str, cantidad: float, ancho: int, alto: int) -> str:
    """Brillo difuso (bloom barato): la imagen desenfocada en 1/4, sumada en
    modo screen. `entrada`/`salida` son etiquetas del filter_complex."""
    return (f"[{entrada}]split[gl_a][gl_b];"
            f"[gl_b]scale={ancho // 4}:{alto // 4},gblur=sigma=6,scale={ancho}:{alto}[gl_c];"
            f"[gl_a][gl_c]blend=all_mode=screen:all_opacity={_f(min(1.0, cantidad) * 0.45)}[{salida}]")
