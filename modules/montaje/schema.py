"""Parámetros del montaje: qué capas, de dónde se leen, dónde van y cuándo."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import config
from modules.montaje import capas as C
from modules.subtitles.schema import SubtitlesParams

X_VALIDAS = ("centro", "izquierda", "derecha")
MODOS = ("caja", "franja")
MAX_CAPAS = 40


def _num(form, name, default, lo, hi, cast=float):
    raw = form.get(name)
    if raw in (None, ""):
        return default
    try:
        v = cast(raw)
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' tiene que ser un número.")
    if not lo <= v <= hi:
        raise ValueError(f"'{name}' fuera de rango ({lo}–{hi}).")
    return v


def _bool(form, name, default=False):
    raw = form.get(name)
    if raw in (None, ""):
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "on", "yes", "si", "sí"}


def carpeta_permitida(ruta: str) -> str:
    """Ruta real de una carpeta dentro de CAPAS_FOLDER (ValueError si sale)."""
    base = os.path.realpath(config.CAPAS_FOLDER)
    real = os.path.realpath(os.path.expanduser(ruta))
    try:
        dentro = os.path.commonpath([base, real]) == base
    except ValueError:
        dentro = False
    if not dentro:
        raise ValueError(f"La carpeta de capas tiene que estar dentro de {config.CAPAS_FOLDER}.")
    if not os.path.isdir(real):
        raise ValueError(f"No existe la carpeta de capas {ruta}.")
    return real


def _caja(datos: dict, defecto: C.Caja | None = None) -> C.Caja:
    base = dict((defecto or C.Caja()).__dict__)
    for k, v in (datos or {}).items():
        if k not in base:
            raise ValueError(f"La caja no tiene el campo {k!r} (usá ancho, x, abajo, modo, y, alto).")
        base[k] = v
    try:
        caja = C.Caja(ancho=int(base["ancho"]), x=str(base["x"]), abajo=int(base["abajo"]),
                      modo=str(base["modo"]), y=int(base["y"]), alto=int(base["alto"]))
    except (TypeError, ValueError):
        raise ValueError("Caja inválida: ancho, abajo, y y alto son números.")
    if caja.x not in X_VALIDAS:
        raise ValueError(f"La caja: x tiene que ser {', '.join(X_VALIDAS)}.")
    if caja.modo not in MODOS:
        raise ValueError(f"La caja: modo tiene que ser {', '.join(MODOS)}.")
    if not 40 <= caja.ancho <= config.OUTPUT_WIDTH or not 0 <= caja.abajo <= config.OUTPUT_HEIGHT:
        raise ValueError("La caja se sale del cuadro de 1080x1920.")
    if caja.modo == "franja" and not (0 <= caja.y and caja.alto >= 40 and caja.y + caja.alto <= config.OUTPUT_HEIGHT):
        raise ValueError("La franja se sale del cuadro de 1080x1920.")
    return caja


@dataclass
class MontajeParams:
    specs: list[dict]
    carpeta: str | None = None                       # carpeta de capas (ruta real)
    subidos: dict[str, str] = field(default_factory=dict)   # nombre → archivo subido
    caja: C.Caja = field(default_factory=C.Caja)
    entrada_s: float = 0.25
    salida_s: float = 0.2
    tope_s: float = C.TOPE_S
    adelanto_s: float = C.ADELANTO_S
    subtitulos: bool = False
    sub_params: SubtitlesParams = field(default_factory=SubtitlesParams)

    @classmethod
    def from_form(cls, form, subidos: dict[str, str] | None = None) -> "MontajeParams":
        crudo = form.get("capas")
        try:
            specs = json.loads(crudo) if isinstance(crudo, str) else crudo
        except (TypeError, ValueError):
            raise ValueError("'capas' tiene que ser una lista JSON de capas.")
        if not isinstance(specs, list) or not specs:
            raise ValueError("Faltan las capas: [{archivo | id, desde | palabra, …}].")
        if len(specs) > MAX_CAPAS:
            raise ValueError(f"Máximo {MAX_CAPAS} capas.")
        carpeta = form.get("carpeta_capas")
        carpeta = carpeta_permitida(str(carpeta)) if carpeta else None
        defecto = _caja(form.get("caja") if isinstance(form.get("caja"), dict) else
                        json.loads(form.get("caja") or "{}"))
        manifest = {}
        if carpeta and os.path.isfile(os.path.join(carpeta, "manifest.json")):
            with open(os.path.join(carpeta, "manifest.json"), encoding="utf-8") as fh:
                datos = json.load(fh)
            items = datos if isinstance(datos, list) else datos.get("laminas") or datos.get("flyers") or []
            manifest = {str(m.get("id")): m for m in items if isinstance(m, dict)}
        limpias = []
        for k, s in enumerate(specs, 1):
            if not isinstance(s, dict):
                raise ValueError(f"La capa {k} tiene que ser un objeto.")
            archivo = s.get("archivo")
            if not archivo and s.get("id") is not None:
                m = manifest.get(str(s["id"]))
                if not m:
                    raise ValueError(f"La capa {k}: no está el id {s['id']!r} en el manifest.json de la carpeta.")
                archivo = m.get("archivo")
            if not archivo:
                raise ValueError(f"La capa {k} no dice qué archivo usar ('archivo' o 'id').")
            limpias.append({**s, "archivo": str(archivo), "caja": _caja(s.get("caja"), defecto).__dict__})
        sub = SubtitlesParams.from_form(form)
        return cls(specs=limpias, carpeta=carpeta, subidos=subidos or {}, caja=defecto,
                   entrada_s=_num(form, "entrada_s", 0.25, 0, 2), salida_s=_num(form, "salida_s", 0.2, 0, 2),
                   tope_s=_num(form, "tope_s", C.TOPE_S, 1, 120), adelanto_s=_num(form, "adelanto_s", C.ADELANTO_S, 0, 2),
                   subtitulos=_bool(form, "subtitulos"), sub_params=sub)

    @property
    def necesita_transcripcion(self) -> bool:
        return any(s.get("palabra") or s.get("hasta_palabra") for s in self.specs)

    def ruta(self, archivo: str) -> str:
        """Archivo de una capa: uno subido con ese nombre o uno de la carpeta."""
        nombre = os.path.basename(archivo)
        if nombre in self.subidos:
            return self.subidos[nombre]
        if not self.carpeta:
            raise ValueError(f"No subiste «{nombre}» ni diste 'carpeta_capas'.")
        real = os.path.realpath(os.path.join(self.carpeta, archivo))
        if os.path.commonpath([self.carpeta, real]) != self.carpeta:
            raise ValueError(f"«{archivo}» está fuera de la carpeta de capas.")
        if not os.path.isfile(real) or not config.allowed_capa_file(real):
            raise ValueError(f"No existe la capa «{archivo}» (o no es mov, webm, mp4, mkv ni png).")
        return real

    def capas_resueltas(self, segments: list[dict] | None, dur_video: float) -> list[C.Capa]:
        specs = [{**s, "archivo": self.ruta(s["archivo"])} for s in self.specs]
        return C.resolver(specs, segments, dur_video, self.caja, self.entrada_s, self.salida_s,
                          self.tope_s, self.adelanto_s)
