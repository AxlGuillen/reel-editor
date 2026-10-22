"""Planificador de un edit de anime (puro): planos elegidos + beatmap + preset → EDL.

  1. Ventana de la canción: arranca en un primer tiempo de modo que el drop
     caiga hacia el 35 % del edit (entre 4 y 15 s) y termina en un compás.
  2. Puntos de corte según el preset: cada `paso` beats según la sección (o
     en cada palabra cantada), medio beat en las subidas, y siempre en el drop.
  3. El drop es del plano héroe (el que eligió Claude o el de más puntaje):
     dura un compás, con congelado y cámara lenta (RIFE si está).
  4. Los demás tramos se llenan con los planos en el orden elegido (la
     historia de la pelea); si se acaban, se repiten tomando otra parte.
  5. Acentos y letra según el preset.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from modules.anime_edit.presets import PRESETS, Preset
from modules.beatmap import analisis
from modules.edicion import render, ritmo
from modules.edicion.edl import Edl, Evento, Pieza, Segmento, Texto

FORMATOS = {
    "vertical": (1080, 1920),          # el clip a lo ancho sobre su copia difuminada
    "vertical_lleno": (1080, 1920),    # recorta los lados y llena el cuadro
    "horizontal": (1920, 1080),        # 16:9, como el edit de referencia
}
DROP_FRACCION, DROP_MIN_S, DROP_MAX_S = 0.35, 4.0, 15.0
MIN_CORTE_S = 0.15
BORDE_PLANO_S = 0.04                  # no usar el primer/último cuadro (el corte)
MAX_EVENTOS = 220                     # las expresiones de FFmpeg crecen con cada uno


@dataclass
class Opciones:
    formato: str = "vertical"
    fps: int = 30
    duracion_s: float = 30
    musica_desde: float | None = None
    texto: str = "auto"                # auto (el del preset) | letra | off
    look: str | None = None            # None = el del preset
    heroe: int | None = None           # id del plano héroe
    interpolar: bool = True


def _beat_en(beats: list[float], i: float) -> float:
    """Tiempo del beat (fraccionario) i, interpolando entre beats."""
    k = int(math.floor(i))
    if k >= len(beats) - 1:
        per = beats[-1] - beats[-2] if len(beats) > 1 else 0.5
        return beats[-1] + (i - (len(beats) - 1)) * per
    return beats[k] + (i - k) * (beats[k + 1] - beats[k])


def ventana(bm: dict, dur: float, musica_desde: float | None) -> tuple[float, float]:
    """(desde, dur) de la canción para el edit, de compás a compás."""
    downs = bm["downbeats"] or bm["beats"]
    if musica_desde is not None:
        desde = min(downs, key=lambda d: abs(d - musica_desde))
    elif bm.get("drop") is not None:
        quiero = bm["drop"] - min(DROP_MAX_S, max(DROP_MIN_S, DROP_FRACCION * dur))
        antes = [d for d in downs if d <= quiero + 1e-6]
        desde = antes[-1] if antes else downs[0]
    else:
        desde = downs[0]
    fin = min(desde + dur, bm["duracion"])
    cierres = [d for d in downs if desde + 4 <= d <= fin + 1e-6]
    if cierres:
        fin = cierres[-1]
    if fin - desde < 2:
        raise ValueError("La canción es muy corta para ese edit.")
    return desde, fin - desde


def cortes(bmw: dict, preset: Preset) -> list[float]:
    """Puntos de corte en la ventana (tiempo de la salida), con 0 y el final."""
    dur = bmw["duracion"]
    beats = bmw["beats"]
    out = {0.0, round(dur, 3)}
    if preset.cortes == "palabras" and bmw.get("palabras"):
        compas = 4 * analisis.periodo(bmw)
        ultimo = 0.0
        for w in bmw["palabras"]:
            if w["inicio"] - ultimo >= 0.3:
                out.add(round(w["inicio"], 3))
                ultimo = w["inicio"]
        # Donde no se canta (puentes, intro), cortes en los compases.
        for d in bmw["downbeats"]:
            vecinos = sorted(out)
            hueco = min((abs(d - c) for c in vecinos), default=99)
            if hueco > compas:
                out.add(round(d, 3))
    elif beats:
        i = 0.0
        while True:
            t = _beat_en(beats, i)
            if t >= dur:
                break
            out.add(round(t, 3))
            paso = preset.paso_subida if analisis.en_subida(bmw, t) else \
                preset.paso.get(analisis.seccion_en(bmw, t), 2)
            i += paso
    if bmw.get("drop") is not None:
        out.add(round(bmw["drop"], 3))
    limpios = []
    for c in sorted(out):
        if c > dur:
            continue
        if limpios and c - limpios[-1] < MIN_CORTE_S:
            continue
        limpios.append(c)
    if limpios[-1] < dur - 1e-6:
        limpios.append(round(dur, 3))
    return limpios


def _ventana_plano(plano: dict, src: float, usos: int) -> tuple[float, float]:
    """Tramo de `src` s del plano centrado en su pico; en cada repetición,
    corrido hacia adelante (no se ve dos veces lo mismo)."""
    a, b = plano["desde"] + BORDE_PLANO_S, plano["hasta"] - BORDE_PLANO_S
    largo = max(0.0, b - a)
    src = min(src, largo)
    inicio = plano["pico"] - src * 0.4 + usos * src * 0.6
    if largo > src:
        inicio = a + ((inicio - a) % (largo - src + 1e-9))
    inicio = min(max(inicio, a), b - src)
    return inicio, inicio + src


def _piezas(desde: float, hasta: float, dur: float, perfil: str) -> list[Pieza]:
    """Llena `dur` con el tramo; si es muy corto, lo que falta queda congelado al final."""
    src = hasta - desde
    if src >= dur * ritmo.VEL_MIN * 0.999:
        return ritmo.rampa(desde, hasta, dur, perfil)
    usado = src / ritmo.VEL_MIN
    return ritmo.rampa(desde, hasta, usado, "constante") + [Pieza(hasta, hasta, dur - usado)]


def _limpiar(palabra: str) -> str:
    return re.sub(r"[^\w'¿?¡!áéíóúñüÁÉÍÓÚÑÜ-]", "", palabra)


def planear(fuente: str, planos: list[dict], bm: dict, preset_id: str, op: Opciones) -> tuple[Edl, dict]:
    """EDL de una variante. `planos` en el orden elegido (con sus medidas)."""
    if preset_id not in PRESETS:
        raise ValueError(f"Variante desconocida: {preset_id!r}. Opciones: {', '.join(PRESETS)}.")
    if op.formato not in FORMATOS:
        raise ValueError(f"Formato inválido: {op.formato!r}. Opciones: {', '.join(FORMATOS)}.")
    if not planos:
        raise ValueError("Elegí al menos un plano.")
    preset = PRESETS[preset_id]
    desde, dur = ventana(bm, op.duracion_s, op.musica_desde)
    bmw = analisis.recortar(bm, desde, dur)
    puntos = cortes(bmw, preset)
    per = analisis.periodo(bmw)

    heroe = next((p for p in planos if p["id"] == op.heroe), None) or max(planos, key=lambda p: p["puntaje"])
    resto = [p for p in planos if p is not heroe] or [heroe]
    drop = bmw.get("drop")

    # Tramos [a, b) de la salida; el del drop se alarga hasta un compás.
    tramos = list(zip(puntos, puntos[1:]))
    if drop is not None:
        i = next((k for k, (a, _) in enumerate(tramos) if abs(a - drop) < 1e-3), None)
        if i is not None:
            fin = tramos[i][1]
            j = i
            while j + 1 < len(tramos) and fin - drop < 4 * per - 1e-3:
                j += 1
                fin = tramos[j][1]
            tramos[i:j + 1] = [(drop, fin)]

    ancho, alto = FORMATOS[op.formato]
    vf = {"vertical": render.vf_vertical_blur(ancho, alto),
          "vertical_lleno": render.vf_cubrir(ancho, alto),
          "horizontal": render.vf_cubrir(ancho, alto)}[op.formato]
    segmentos, usos, k = [], {}, 0
    for a, b in tramos:
        d = b - a
        es_heroe = drop is not None and abs(a - drop) < 1e-3
        plano = heroe if es_heroe else resto[k % len(resto)]
        if not es_heroe:
            k += 1
        n = usos.get(plano["id"], 0)
        usos[plano["id"]] = n + 1
        if es_heroe:
            s0, s1 = _ventana_plano(plano, d * 0.8, n)
            piezas = ritmo.impacto(s0, s1, d, congelado=0.1, lento=d * 0.5, vel_lenta=0.35)
        else:
            s0, s1 = _ventana_plano(plano, d * preset.velocidad, n)
            piezas = _piezas(s0, s1, d, preset.perfil)
        piezas = ritmo.acotar(piezas, 0.0, ritmo.VEL_MAX)
        segmentos.append(Segmento(fuente, piezas, f"plano {plano['id']}", vf,
                                  interpolar=op.interpolar and (es_heroe or preset.perfil != "constante")))

    eventos = _acentos(preset, bmw, [a for a, _ in tramos], drop)
    texto = preset.texto if op.texto == "auto" else op.texto
    textos = []
    if texto == "letra":
        palabras = bmw.get("palabras") or []
        for w, sig in zip(palabras, palabras[1:] + [None]):
            fin = min(sig["inicio"] if sig else w["fin"] + 0.3, w["inicio"] + 1.2, dur)
            limpia = _limpiar(w["texto"])
            if limpia and fin - w["inicio"] >= 0.08:
                textos.append(Texto(w["inicio"], fin, limpia, "palabra"))
    edl = Edl(segmentos, ancho=ancho, alto=alto, fps=op.fps, eventos=eventos, textos=textos,
              musica_desde=desde, look=op.look or preset.look, glow=preset.glow)
    info = {"variante": preset_id, "musica_desde": round(desde, 3), "dur": round(dur, 3),
            "cortes": len(tramos), "heroe": heroe["id"] if drop is not None else None,
            "drop": round(drop, 3) if drop is not None else None, "palabras": len(textos)}
    return edl, info


def _acentos(preset: Preset, bmw: dict, inicios: list[float], drop: float | None) -> list[Evento]:
    coro = [s for s in bmw["secciones"] if s["tipo"] == "coro"]

    def en_coro(t):
        return any(s["inicio"] <= t < s["fin"] for s in coro)

    ev: list[Evento] = []
    if drop is not None:
        ev += [Evento(drop, "flash", 1.0, 0.2), Evento(drop, "zoom", 1.0, 0.35)]
    if preset.flash == "compases_coro":
        ev += [Evento(d, "flash", 0.4, 0.12) for d in bmw["downbeats"] if en_coro(d) and d != drop]
    elif preset.flash == "cortes":
        ev += [Evento(t, "flash", 0.45, 0.12) for t in inicios if t > 0 and t != drop]
    if preset.zoom == "cortes_coro":
        ev += [Evento(t, "zoom", 0.5, 0.2) for t in inicios if en_coro(t) and t != drop]
    elif preset.zoom == "beats_coro":
        ev += [Evento(b, "zoom", 0.35, 0.18) for b in bmw["beats"] if en_coro(b) and b != drop]
    if preset.shake == "golpes_coro":
        ev += [Evento(g, "shake", 0.5, 0.2) for g in bmw.get("golpes", []) if en_coro(g)]
    if preset.shake and drop is not None:
        ev.append(Evento(drop, "shake", 1.0, 0.45))
    if preset.rgb == "drop" and drop is not None:
        ev.append(Evento(drop, "rgb", 1.0, 0.3))
    elif preset.rgb == "cortes_coro":
        ev += [Evento(t, "rgb", 0.8, 0.12) for t in inicios if en_coro(t)]
    ev.sort(key=lambda e: e.t)
    if len(ev) > MAX_EVENTOS:
        # Se quedan los más fuertes (el drop siempre gana).
        ev = sorted(sorted(ev, key=lambda e: -e.intensidad)[:MAX_EVENTOS], key=lambda e: e.t)
    return ev
