"""Modo edit: las peleas de una partida montadas al ritmo de una canción.

Lo que el resumen hace con la voz de los casters, el edit lo hace con música,
y con algo que un edit de anime no tiene: los datos. Cada kill ya viene ubicada
al cuadro (feed + contador del marcador, ver layout.cambios_kills), así que no
se busca en el video qué coincide con la música: se FUERZA que cada kill caiga
en un beat estirando o comprimiendo el tramo de antes (rampa de velocidad).

Estructura de cada pelea en beats (todo en enteros, así los cortes caen en el
beat y cada pelea arranca en el primer tiempo de un compás):

    entrada  L beats   (1 compás; 2 si hay mucho antes de la primera kill)
    kills    n_i beats entre kill y kill (lo más cerca posible de la velocidad
             base, entre 0.5× y 2.5×)
    cola     T beats   (≥ 2, lo que falte para cerrar el compás)

La pelea más importante (la de cierre, o la de más puntaje) se ajusta para que
su última kill caiga en un primer tiempo, y la música arranca de modo que esa
kill caiga justo en el DROP de la canción. Todo es puro: recibe los clips ya
bajados y el beatmap, devuelve la EDL.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from modules.beatmap import analisis
from modules.edicion import ritmo
from modules.edicion.edl import Edl, Evento, Pieza, Segmento, Texto

FUSION_S = 0.35              # kills más juntas que esto son un solo golpe
ENTRADA_S = 4.0              # cuánto antes de la primera kill se quiere ver
VEL_GAP_MIN, VEL_GAP_MAX = 0.5, 2.5
COLA_MIN = 2
MULTI = {"triple": "TRIPLE KILL", "quadra": "QUADRA KILL", "penta": "PENTAKILL"}
ESTILOS = ("impacto", "velocidad", "limpio")
TEXTOS = ("kills", "letra", "ambos", "off")


@dataclass
class KillEdit:
    t: float                          # s en el tramo (ya corregido)
    lado: str = ""
    jugador: str = ""
    campeon: str = ""
    cantidad: int = 1


@dataclass
class ClipEdit:
    id: str
    fuente: str                       # el tramo bajado
    desde: float                      # tramo en vivo (s en el archivo)
    hasta: float
    kills: list[KillEdit]
    puntaje: int = 0
    multikill: str | None = None      # "quadra de Caliste"
    cierra: bool = False
    titulo: str = ""
    vf: str = ""                      # geometría (layout vertical) de este tramo


@dataclass
class Opciones:
    estilo: str = "impacto"
    velocidad: float = 1.0            # velocidad base del juego
    duracion_max_s: float = 60
    alinear_drop: bool = True
    musica_desde: float | None = None # s de la canción; None = automático
    texto: str = "kills"
    acentos_musica: bool = True       # golpes de zoom chicos en los compases del coro
    titulo: str = ""
    fps: int = 60
    ancho: int = 1080
    alto: int = 1920


@dataclass
class _Plan:
    clip: ClipEdit
    golpes: list[KillEdit]            # kills fusionadas
    entrada: int                      # L
    gaps: list[int]                   # n_i
    cola: int                         # T
    entrada_src: float = 0.0
    cola_src: float = 0.0

    @property
    def beats(self) -> int:
        return self.entrada + sum(self.gaps) + self.cola

    @property
    def beat_ultima(self) -> int:
        return self.entrada + sum(self.gaps)


@dataclass
class Resultado:
    edl: Edl
    musica_desde: float
    drop_alineado: bool
    usados: list[str]
    descartados: list[str] = field(default_factory=list)
    aviso: str | None = None


def fusionar(kills: list[KillEdit]) -> list[KillEdit]:
    """Kills a menos de FUSION_S son un solo golpe (el primero, con la suma)."""
    out: list[KillEdit] = []
    for k in sorted(kills, key=lambda k: k.t):
        if out and k.t - out[-1].t < FUSION_S:
            out[-1] = KillEdit(out[-1].t, out[-1].lado, out[-1].jugador, out[-1].campeon,
                               out[-1].cantidad + k.cantidad)
        else:
            out.append(KillEdit(k.t, k.lado, k.jugador, k.campeon, k.cantidad))
    return out


def _beats_para(src: float, per: float, base: float) -> int:
    """Beats para mostrar `src` s de juego cerca de la velocidad `base`, sin
    pasar de VEL_GAP_MIN–VEL_GAP_MAX."""
    n = max(1, round(src / (base * per)))
    n = max(n, math.ceil(src / (VEL_GAP_MAX * per)))
    n = min(n, max(1, math.floor(src / (VEL_GAP_MIN * per))))
    return n


def estructura(clip: ClipEdit, per: float, base: float) -> _Plan:
    golpes = fusionar([k for k in clip.kills if clip.desde <= k.t <= clip.hasta])
    if not golpes:
        # Sin kills dentro del vivo (raro): un compás del medio del tramo.
        mitad = (clip.desde + clip.hasta) / 2
        golpes = [KillEdit(mitad)]
    previo = golpes[0].t - clip.desde
    entrada = 8 if previo >= 6 * per * base and previo > ENTRADA_S * 1.6 else 4
    gaps = [_beats_para(b.t - a.t, per, base) for a, b in zip(golpes, golpes[1:])]
    total = entrada + sum(gaps) + COLA_MIN
    cola = COLA_MIN + (-total) % 4
    plan = _Plan(clip, golpes, entrada, gaps, cola)
    plan.entrada_src = max(0.0, min(previo, entrada * per * base))
    plan.cola_src = max(0.0, min(clip.hasta - golpes[-1].t, cola * per * base))
    return plan


def _climax(planes: list[_Plan]) -> int:
    return max(range(len(planes)), key=lambda i: (planes[i].clip.cierra, planes[i].clip.puntaje, i))


def _alinear_climax(plan: _Plan) -> None:
    """La última kill de la pelea clave en un primer tiempo: se estira el
    último hueco (o la entrada, si hay una sola kill) y se recalcula la cola."""
    resto = plan.beat_ultima % 4
    if resto:
        extra = 4 - resto
        if plan.gaps:
            plan.gaps[-1] += extra
        else:
            plan.entrada += extra
    total = plan.beat_ultima + COLA_MIN
    plan.cola = COLA_MIN + (-total) % 4


def _llenar(desde: float, hasta: float, dur: float, armar, congelar_al: str) -> list[Pieza]:
    """Las piezas de `armar(desde, hasta, d)`; si no hay fuente para llenar
    `dur` sin bajar de VEL_MIN, lo que falta va congelado al principio
    (entrada: el plano quieto antes de arrancar) o al final (cola)."""
    src = max(0.0, hasta - desde)
    if src >= dur * ritmo.VEL_MIN * 0.999:
        return armar(desde, hasta, dur)
    usado = src / ritmo.VEL_MIN
    resto = dur - usado
    if congelar_al == "inicio":
        return [Pieza(desde, desde, resto)] + (armar(desde, hasta, usado) if usado > 0 else [])
    return (armar(desde, hasta, usado) if usado > 0 else []) + [Pieza(hasta, hasta, resto)]


def _multikill(texto: str | None) -> str | None:
    if not texto:
        return None
    for clave, rotulo in MULTI.items():
        if texto.lower().startswith(clave):
            return rotulo
    return None


def planear(clips: list[ClipEdit], bm: dict, op: Opciones) -> Resultado:
    """EDL del edit: `clips` en orden cronológico, `bm` el beatmap COMPLETO de
    la canción (los tiempos de la salida salen de sus beats reales)."""
    if op.estilo not in ESTILOS:
        raise ValueError(f"Estilo inválido: {op.estilo!r}. Opciones: {', '.join(ESTILOS)}.")
    if not clips:
        raise ValueError("No hay peleas para el edit.")
    beats = bm["beats"]
    per = analisis.periodo(bm)
    planes = [estructura(c, per, op.velocidad) for c in clips]
    descartados: list[str] = []

    # Presupuesto: afuera las de menos puntaje (nunca la clave) hasta entrar.
    def dur(pl):
        return sum(p.beats for p in pl) * per

    while len(planes) > 1 and dur(planes) > op.duracion_max_s:
        clave = _climax(planes)
        peor = min((i for i in range(len(planes)) if i != clave),
                   key=lambda i: (planes[i].clip.puntaje, -i))
        descartados.append(planes.pop(peor).clip.id)

    clave = _climax(planes)
    _alinear_climax(planes[clave])
    inicio_clip, acum = [], 0
    for p in planes:
        inicio_clip.append(acum)
        acum += p.beats
    total_beats = acum
    beat_clave = inicio_clip[clave] + planes[clave].beat_ultima

    # Dónde arranca la música (m0: índice de beat de la canción).
    aviso = None
    drop_alineado = False
    downs = {round(d, 3) for d in bm["downbeats"]}
    primer_down = next((i for i, b in enumerate(beats) if round(b, 3) in downs), 0)
    if op.musica_desde is not None:
        # El beat más cercano y, si no es primer tiempo, el próximo que lo sea.
        m0 = analisis.beat_mas_cercano(beats, op.musica_desde)
        for _ in range(3):
            if round(beats[m0], 3) in downs or m0 >= len(beats) - 1:
                break
            m0 += 1
    elif op.alinear_drop and bm.get("drop") is not None:
        m0 = analisis.beat_mas_cercano(beats, bm["drop"]) - beat_clave
        if m0 < 0:
            aviso = "La canción tiene poco antes del drop: la pelea clave no cae en el drop."
            m0 = primer_down
        else:
            drop_alineado = True
    else:
        m0 = primer_down
    if m0 + total_beats >= len(beats):
        falta = (m0 + total_beats - len(beats) + 1) * per
        raise ValueError(f"La canción es corta para estas peleas (faltan {falta:.0f} s de música). "
                         "Bajá duracion_max_s o elegí otra canción.")

    def T(r: int) -> float:
        return beats[m0 + r] - beats[m0]

    segmentos: list[Segmento] = []
    eventos: list[Evento] = []
    textos: list[Texto] = []
    kills_salida: list[float] = []
    for n, (plan, r0) in enumerate(zip(planes, inicio_clip)):
        c = plan.clip
        es_clave = n == clave
        piezas: list[Pieza] = []
        g = plan.golpes
        r = r0 + plan.entrada
        piezas += _llenar(g[0].t - plan.entrada_src, g[0].t, T(r) - T(r0),
                          lambda a, b, d: ritmo.rampa(a, b, d, "constante"), "inicio")
        tramos = list(zip(g, g[1:] + [None], plan.gaps + [plan.cola]))
        for k, (a, b, nb) in enumerate(tramos):
            hasta = b.t if b else a.t + plan.cola_src
            d = T(r + nb) - T(r)
            ultima = b is None
            fuerte = es_clave and ultima
            if op.estilo == "impacto":
                def armar(x, y, dd, fuerte=fuerte):
                    return ritmo.impacto(x, y, dd, congelado=0.1 if fuerte else 0.05,
                                         lento=min(dd * 0.6, per * (2 if fuerte else 1)),
                                         vel_lenta=0.3 if fuerte else 0.45)
            else:
                def armar(x, y, dd):
                    return ritmo.rampa(x, y, dd, "velocidad" if op.estilo == "velocidad" else "constante")
            piezas += _llenar(a.t, hasta, d, armar, "fin")
            t_kill = T(r)
            kills_salida.append(t_kill)
            multi = a.cantidad >= 2
            eventos += [Evento(t_kill, "flash", 1.0 if fuerte else 0.75 if multi else 0.55, 0.16),
                        Evento(t_kill, "zoom", 1.0 if fuerte else 0.6, 0.3 if fuerte else 0.22),
                        Evento(t_kill, "shake", 1.0 if fuerte else 0.5, 0.4 if fuerte else 0.25)]
            if fuerte or multi:
                eventos.append(Evento(t_kill, "rgb", 1.0, 0.3))
            if op.texto in ("kills", "ambos") and a.jugador:
                fin = T(r + nb) if b else t_kill + max(per * 2, 1.0)
                textos.append(Texto(t_kill, min(fin, t_kill + 1.6), a.jugador, "kill", sub=a.campeon))
            r += nb
        mk = _multikill(c.multikill)
        if mk and op.texto in ("kills", "ambos"):
            t_ult = kills_salida[-1]
            textos.append(Texto(t_ult + 0.05, t_ult + max(per * 3, 1.4), mk, "multikill"))
        # Solo se acota lo demasiado rápido (usa menos fuente): lo lento ya lo
        # resolvió _llenar con congelados, sin pasarse del tramo.
        piezas = ritmo.acotar(piezas, 0.0, ritmo.VEL_MAX)
        segmentos.append(Segmento(c.fuente, piezas, c.id, c.vf))
        if n > 0:
            eventos.append(Evento(T(r0), "flash", 0.35, 0.12))

    if op.acentos_musica:
        vent = analisis.recortar(bm, beats[m0], T(total_beats))
        for d in vent["downbeats"]:
            if analisis.seccion_en(vent, d) == "coro" and all(abs(d - k) > per for k in kills_salida):
                eventos.append(Evento(d, "zoom", 0.3, 0.2))
    if op.texto in ("letra", "ambos"):
        vent = analisis.recortar(bm, beats[m0], T(total_beats))
        ocupados = [(t.desde, t.hasta) for t in textos]
        palabras = vent.get("palabras") or []
        for w, sig in zip(palabras, palabras[1:] + [None]):
            fin = min(sig["inicio"] if sig else w["fin"] + 0.3, w["inicio"] + 1.2)
            if fin - w["inicio"] < 0.08 or any(a < fin and w["inicio"] < b for a, b in ocupados):
                continue
            texto_w = re.sub(r"[^\w'¿?¡!áéíóúñüÁÉÍÓÚÑÜ-]", "", w["texto"])
            if texto_w:
                textos.append(Texto(w["inicio"], fin, texto_w, "palabra"))
    if op.titulo:
        textos.append(Texto(0.0, min(2.5, T(total_beats)), op.titulo, "titulo"))

    edl = Edl(segmentos, ancho=op.ancho, alto=op.alto, fps=op.fps, eventos=eventos,
              textos=sorted(textos, key=lambda t: t.desde), musica_desde=beats[m0])
    return Resultado(edl, beats[m0], drop_alineado, [p.clip.id for p in planes], descartados, aviso)
