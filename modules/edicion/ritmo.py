"""Herramientas puras para planificar: repartir un tramo de la fuente en un
tiempo de salida con una curva de velocidad, y cuadrar todo a cuadros enteros.

Lo que hace que un edit "pegue" es que el corte o el golpe caiga en el beat al
cuadro. Los planificadores calculan en segundos; `cuantizar()` redondea cada
pieza al cuadro manteniendo el tiempo ACUMULADO exacto (cada borde cae en el
cuadro más cercano a su tiempo ideal), así el error nunca se suma: un golpe
planeado en el segundo 31.250 a 60 fps cae en el cuadro 1875, no 3 cuadros
después por arrastre de redondeos.
"""
from __future__ import annotations

from modules.edicion.edl import CONGELADO_EPS, Edl, Pieza

VEL_MIN, VEL_MAX = 0.25, 4.0

# Curvas de velocidad: [(fracción de la salida, velocidad relativa)]. Se escalan
# entre todas para que la fuente entre justa en la duración pedida.
PERFILES = {
    "constante": [(1.0, 1.0)],
    # Velocity edit: lento justo después del golpe, rápido hacia el siguiente.
    "velocidad": [(0.35, 0.35), (0.65, 1.6)],
    # Llegar rápido al golpe y frenar en seco al final del tramo.
    "frenada": [(0.7, 1.5), (0.3, 0.4)],
    # Cámara lenta suave en el medio del tramo.
    "respiro": [(0.25, 1.2), (0.5, 0.5), (0.25, 1.2)],
}


def rampa(desde: float, hasta: float, dur: float, perfil: str | list = "constante") -> list[Pieza]:
    """Reparte la fuente [desde, hasta] en `dur` s de salida siguiendo `perfil`.

    Las velocidades relativas se multiplican por un mismo factor k tal que la
    fuente usada sea exactamente hasta − desde. Sin fuente (desde == hasta) es
    un congelado de `dur`."""
    if dur <= 0:
        return []
    src = hasta - desde
    if src < CONGELADO_EPS:
        return [Pieza(desde, desde, dur)]
    curva = PERFILES[perfil] if isinstance(perfil, str) else perfil
    peso = sum(f * v for f, v in curva)
    k = src / (dur * peso)
    piezas, t = [], desde
    for f, v in curva:
        d = dur * f
        if d <= 0:
            continue
        s = d * v * k
        piezas.append(Pieza(t, t + s, d))
        t += s
    piezas[-1].hasta = hasta
    return piezas


def impacto(desde: float, hasta: float, dur: float, congelado: float = 0.05,
            lento: float = 0.25, vel_lenta: float = 0.4) -> list[Pieza]:
    """El golpe: congela `congelado` s en `desde`, sigue `lento` s en cámara
    lenta y el resto va a la velocidad que haga falta para llegar a `hasta`.
    El congelado nunca pasa del 30 % del tramo ni la cámara lenta del 60 % de
    lo que queda."""
    if dur <= 0:
        return []
    congelado = min(congelado, dur * 0.3)
    lento = min(lento, max(0.0, dur - congelado) * 0.6)
    resto = dur - congelado - lento
    src = max(0.0, hasta - desde)
    src_lento = min(lento * vel_lenta, src * 0.6)
    piezas = []
    if congelado > 0:
        piezas.append(Pieza(desde, desde, congelado))
    t = desde
    if lento > 0 and src_lento > CONGELADO_EPS:
        piezas.append(Pieza(t, t + src_lento, lento))
        t += src_lento
    elif lento > 0:
        resto += lento
    if resto > 0:
        piezas.append(Pieza(t, hasta if hasta > t else t, resto))
    return piezas


def acotar(piezas: list[Pieza], vmin: float = VEL_MIN, vmax: float = VEL_MAX) -> list[Pieza]:
    """Ninguna pieza más lenta que vmin ni más rápida que vmax: se recorta la
    FUENTE (no la duración), así el tiempo de salida — el ritmo — no cambia.
    Una pieza demasiado rápida usa menos fuente (salta un poco) y una
    demasiado lenta sigue un poco más allá de su final: el planificador es
    el que se asegura de que esa fuente exista."""
    out = []
    for p in piezas:
        if p.congelado:
            out.append(p)
            continue
        v = p.velocidad
        if v > vmax:
            out.append(Pieza(p.desde, p.desde + p.dur * vmax, p.dur))
        elif v < vmin:
            out.append(Pieza(p.desde, p.desde + p.dur * vmin, p.dur))
        else:
            out.append(p)
    return out


def cuantizar(edl: Edl) -> None:
    """Cada borde de pieza al cuadro más cercano de su tiempo acumulado ideal.
    Las piezas que quedan en 0 cuadros se quitan (y los segmentos vacíos)."""
    fps = edl.fps
    t = 0.0
    hecho = 0                                 # cuadros ya asignados
    segmentos = []
    for seg in edl.segmentos:
        piezas = []
        for p in seg.piezas:
            t += p.dur
            fin = round(t * fps)
            n = fin - hecho
            if n <= 0:
                continue
            hecho = fin
            nueva = n / fps
            if not p.congelado:
                # la fuente se ajusta en proporción: la velocidad no cambia
                p = Pieza(p.desde, p.desde + (p.hasta - p.desde) * nueva / p.dur, nueva)
            else:
                p = Pieza(p.desde, p.desde, nueva)
            piezas.append(p)
        if piezas:
            seg.piezas = piezas
            segmentos.append(seg)
    edl.segmentos = segmentos


def cuadros(edl: Edl) -> int:
    return round(edl.dur * edl.fps)
