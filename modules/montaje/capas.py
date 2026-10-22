"""Cuándo entra y sale cada capa del montaje (puro: no toca FFmpeg).

Una capa entra en un segundo fijo (`desde`) o en una PALABRA de la
transcripción del video base (`palabra`, la n-ésima vez que se dice). Sale
en `hasta`, en `hasta_palabra` o, si no dice, cuando entra la siguiente.

Reglas medidas en los montajes a mano de suiza (18/10) y Lux (19/10):
  - entra ADELANTO_S (0.1 s) antes de su palabra;
  - sin final explícito dura hasta la siguiente capa, con un tope de
    TOPE_S (12 s); si el hueco que deja el tope es de menos de PEGAR_S
    (2.5 s), se estira hasta la siguiente;
  - dos capas pegadas se funden: la nueva entra con su fundido ENCIMA de la
    anterior, que sigue debajo lo que dura ese fundido;
  - fuera de eso, dos capas no se pueden encimar.

Whisper escribe lo que oye: los números con dígitos ("16") aunque el guion
diga "dieciséis", y sin tildes ni mayúsculas fijas. Se comparan las dos
cosas normalizadas (sin tildes, minúsculas, sin puntuación y los números
escritos pasados a dígitos).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

ADELANTO_S = 0.1
TOPE_S = 12.0
PEGAR_S = 2.5

_UNIDADES = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14,
    "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "veintiun": 21, "veintiuno": 21, "veintiuna": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
    "veintinueve": 29, "cien": 100, "mil": 1000,
}
_DECENAS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70,
            "ochenta": 80, "noventa": 90}


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def tokens(texto: str) -> list[str]:
    """Palabras normalizadas: minúsculas, sin tildes ni puntuación, y los
    números escritos con letras pasados a dígitos ("treinta y dos" → "32")."""
    crudas = re.findall(r"[a-z0-9ñ]+", _sin_tildes(texto.lower()))
    out: list[str] = []
    i = 0
    while i < len(crudas):
        w = crudas[i]
        if w in _DECENAS:
            n = _DECENAS[w]
            if i + 2 < len(crudas) and crudas[i + 1] == "y" and crudas[i + 2] in _UNIDADES \
                    and _UNIDADES[crudas[i + 2]] < 10:
                n += _UNIDADES[crudas[i + 2]]
                i += 2
            out.append(str(n))
        elif w in _UNIDADES and w not in ("un", "una"):
            out.append(str(_UNIDADES[w]))
        else:
            out.append(w)
        i += 1
    return out


def palabras_de(segments: list[dict]) -> list[tuple[str, float, float]]:
    """(token, inicio, fin) de toda la transcripción (los segments de
    subtitles / reel_express). Una palabra que se normaliza en varias (o en
    ninguna) reparte su tiempo."""
    out = []
    for seg in segments:
        for w in seg.get("words") or []:
            ts = tokens(str(w.get("word", "")))
            if not ts:
                continue
            a, b = float(w["start"]), float(w["end"])
            paso = (b - a) / len(ts)
            out += [(t, a + k * paso, a + (k + 1) * paso) for k, t in enumerate(ts)]
    return out


def buscar(transcripcion: list[tuple[str, float, float]], frase: str, n: int = 1) -> float | None:
    """Segundo en que empieza la n-ésima aparición de `frase` (None si no está)."""
    buscada = tokens(frase)
    if not buscada:
        return None
    vistas = 0
    texto = [t for t, _, _ in transcripcion]
    for i in range(len(texto) - len(buscada) + 1):
        if texto[i:i + len(buscada)] == buscada:
            vistas += 1
            if vistas == n:
                return transcripcion[i][1]
    return None


@dataclass
class Caja:
    """Dónde va la capa en el cuadro de 1080x1920."""
    ancho: int = 720
    x: str = "centro"            # centro | izquierda | derecha
    abajo: int = 0               # margen inferior en px
    modo: str = "caja"           # caja | franja
    y: int = 1240                # franja: dónde arranca
    alto: int = 680              # franja: alto


@dataclass
class Capa:
    archivo: str
    desde: float
    hasta: float
    caja: Caja = field(default_factory=Caja)
    dur_clip: float | None = None   # None = imagen fija
    fundido_entrada: float = 0.25
    fundido_salida: float = 0.2
    etiqueta: str = ""


def resolver(specs: list[dict], segments: list[dict] | None, dur_video: float,
             caja_defecto: Caja, entrada: float = 0.25, salida: float = 0.2,
             tope: float = TOPE_S, adelanto: float = ADELANTO_S) -> list[Capa]:
    """Las capas con sus tiempos resueltos. `specs`: [{archivo, desde | palabra
    [, n], hasta | hasta_palabra [, hasta_n], caja}] (la caja parcial pisa la de
    por defecto). ValueError legible si una palabra no aparece o si se enciman."""
    transcripcion = palabras_de(segments or [])
    inicios = []
    for k, s in enumerate(specs, 1):
        if s.get("desde") not in (None, ""):
            inicios.append(float(s["desde"]))
        elif s.get("palabra"):
            if not transcripcion:
                raise ValueError(f"La capa {k} entra en una palabra y no hay transcripción.")
            t = buscar(transcripcion, str(s["palabra"]), int(s.get("n") or 1))
            if t is None:
                raise ValueError(f"La capa {k}: no encontré «{s['palabra']}» (aparición {s.get('n') or 1}) "
                                 "en la transcripción. Revisá cómo la escribió Whisper.")
            inicios.append(max(0.0, t - adelanto))
        else:
            raise ValueError(f"La capa {k} no dice cuándo entra: falta 'desde' o 'palabra'.")
    orden = sorted(range(len(specs)), key=lambda i: inicios[i])
    capas: list[Capa] = []
    for pos, i in enumerate(orden):
        s = specs[i]
        desde = inicios[i]
        siguiente = inicios[orden[pos + 1]] if pos + 1 < len(orden) else dur_video
        if s.get("hasta") not in (None, ""):
            hasta = float(s["hasta"])
        elif s.get("hasta_palabra"):
            t = buscar(transcripcion, str(s["hasta_palabra"]), int(s.get("hasta_n") or 1))
            if t is None:
                raise ValueError(f"La capa {i + 1}: no encontré «{s['hasta_palabra']}» para su final.")
            hasta = t
        else:
            hasta = min(siguiente, desde + tope)
            if siguiente - hasta < PEGAR_S:
                hasta = siguiente
        hasta = min(hasta, dur_video)
        if hasta - desde < 0.2:
            raise ValueError(f"La capa {i + 1} dura menos de 0.2 s ({desde:.2f} → {hasta:.2f}).")
        caja = Caja(**{**caja_defecto.__dict__, **(s.get("caja") or {})})
        capas.append(Capa(str(s["archivo"]), round(desde, 3), round(hasta, 3), caja,
                          fundido_entrada=entrada, fundido_salida=salida, etiqueta=str(s.get("id") or "")))
    for a, b in zip(capas, capas[1:]):
        if b.desde < a.hasta - 1e-3:
            raise ValueError(f"Las capas «{a.archivo}» y «{b.archivo}» se enciman "
                             f"({a.desde:.2f}–{a.hasta:.2f} y {b.desde:.2f}–{b.hasta:.2f}).")
        if abs(b.desde - a.hasta) < 1e-3:
            # Pegadas: la anterior sigue debajo mientras la nueva entra fundiéndose.
            a.hasta = round(a.hasta + b.fundido_entrada, 3)
            a.fundido_salida = 0.0
    return capas
