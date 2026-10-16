"""Parámetros del módulo competitivo (validación de las dos fases)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

import config
from modules.competitivo import engine, kills, layout

COLORES = set(layout.COLOR)
MODOS = {"revisar", "auto"}
_GAME_ID = re.compile(r"^\d{15,20}$")


def _bool(form, name: str, default: bool = False) -> bool:
    raw = form.get(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "on", "yes", "si", "sí"}


def _num(form, name: str, default, lo, hi, cast=int):
    raw = form.get(name)
    if raw in (None, ""):
        return default
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' tiene que ser un número.")
    if not lo <= value <= hi:
        raise ValueError(f"'{name}' fuera de rango ({lo}–{hi}).")
    return value


def _marca(form, name: str = "watermark") -> str:
    """Nombre de un PNG de assets/watermarks ("" = sin watermark)."""
    nombre = os.path.basename((form.get(name) or "").strip())
    if nombre and not os.path.isfile(os.path.join(config.WATERMARKS_FOLDER, nombre)):
        raise ValueError(f"No existe el watermark «{nombre}» en assets/watermarks.")
    return nombre


def _render(form, base: "CompetitivoParams | None" = None) -> dict:
    """Parámetros del render: en /finish pueden cambiarse sin repetir la fase 1."""
    return {
        "zoom": _num(form, "zoom", base.zoom if base else 1.3, 0.8, 2.2, float),
        "color": _color(form, base.color if base else "vivo"),
        "watermark": _marca(form) if form.get("watermark") is not None or not base else base.watermark,
        "marca_alto": _num(form, "marca_alto", base.marca_alto if base else layout.MARCA_ALTO_DEFECTO, 80, 400),
    }


def _color(form, default: str) -> str:
    color = (form.get("color") or default).strip().lower()
    if color not in COLORES:
        raise ValueError(f"'color' tiene que ser uno de {sorted(COLORES)}.")
    return color


@dataclass
class CompetitivoParams:
    """Fase 1 (/process): qué partidas, de qué VOD y con qué reglas."""
    game_ids: list[str]
    vod_url: str
    modo: str = "revisar"            # revisar: pausa para el ancla y los clips; auto: sigue solo
    duracion_max_s: int = 60
    max_por_partida: int = 2
    min_kills: int = 2
    incluir_picks: bool = False
    incluir_primera_sangre: bool = False
    pre_s: float = 8
    post_s: float = 3
    zoom: float = 1.3
    color: str = "vivo"
    watermark: str = ""
    marca_alto: int = layout.MARCA_ALTO_DEFECTO

    @classmethod
    def from_form(cls, form) -> "CompetitivoParams":
        crudo = form.get("game_ids") or ""
        if isinstance(crudo, (list, tuple)):
            ids = [str(x).strip() for x in crudo]
        else:
            ids = [x for x in re.split(r"[\s,;]+", str(crudo)) if x]
        if not ids:
            raise ValueError("Faltan los gameId de la serie (uno por partida, en orden).")
        malos = [x for x in ids if not _GAME_ID.match(x)]
        if malos:
            raise ValueError(f"gameId inválido: {malos[0]}. Son números largos, p. ej. 115660588583171787.")
        if len(set(ids)) != len(ids):
            raise ValueError("Hay gameId repetidos.")
        if len(ids) > 7:
            raise ValueError("Máximo 7 partidas por serie.")
        vod = (form.get("vod_url") or "").strip()
        if not re.match(r"^https?://", vod):
            raise ValueError("Falta 'vod_url': el link del VOD de Ibai en YouTube.")
        modo = (form.get("modo") or "revisar").strip().lower()
        if modo not in MODOS:
            raise ValueError("'modo' tiene que ser 'revisar' o 'auto'.")
        return cls(
            game_ids=ids, vod_url=vod, modo=modo,
            duracion_max_s=_num(form, "duracion_max_s", 60, 20, 180),
            max_por_partida=_num(form, "max_por_partida", 2, 1, 5),
            min_kills=_num(form, "min_kills", 2, 1, 5),
            incluir_picks=_bool(form, "incluir_picks"),
            incluir_primera_sangre=_bool(form, "incluir_primera_sangre"),
            pre_s=_num(form, "pre_s", 8, 0, 20, float),
            post_s=_num(form, "post_s", 3, 0, 15, float),
            **_render(form),
        )

    @property
    def reglas(self) -> kills.Reglas:
        return kills.Reglas(
            min_kills=self.min_kills, incluir_picks=self.incluir_picks,
            incluir_primera_sangre=self.incluir_primera_sangre, pre_s=self.pre_s,
            post_s=self.post_s, duracion_max_s=self.duracion_max_s,
            max_por_partida=self.max_por_partida,
        )


@dataclass
class FinishParams:
    """Fase 2 (/finish): ancla por partida, clips elegidos y retoques del render."""
    anclas: dict[str, float]          # gameId → segundo del VOD de la primera kill
    clips: list[str] | None           # ids de las peleas; None = la selección propuesta
    zoom: float
    color: str
    watermark: str
    marca_alto: int

    @classmethod
    def from_form(cls, form, base: CompetitivoParams) -> "FinishParams":
        anclas = {}
        crudo = form.get("anchors") or form.get("anclas") or "{}"
        try:
            datos = json.loads(crudo) if isinstance(crudo, str) else dict(crudo)
        except (TypeError, ValueError):
            raise ValueError("'anchors' tiene que ser un JSON {gameId: segundo}.")
        for gid, valor in datos.items():
            if gid not in base.game_ids:
                raise ValueError(f"El ancla {gid} no es de esta serie.")
            try:
                anclas[gid] = engine.parse_vod_time(valor)
            except (TypeError, ValueError):
                raise ValueError(f"Ancla inválida para {gid}: usá segundos (4935) o 1:22:15.")
        clips = form.get("clips")
        if clips not in (None, ""):
            try:
                clips = json.loads(clips) if isinstance(clips, str) else list(clips)
            except (TypeError, ValueError):
                raise ValueError("'clips' tiene que ser una lista JSON de ids.")
            if not isinstance(clips, list) or not all(isinstance(c, str) for c in clips):
                raise ValueError("'clips' tiene que ser una lista de ids (texto).")
            if not clips:
                raise ValueError("Elegí al menos un clip.")
        else:
            clips = None
        return cls(anclas=anclas, clips=clips, **_render(form, base))
