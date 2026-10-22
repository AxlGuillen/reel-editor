"""Parámetros del módulo beatmap (análisis de una canción)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from modules.beatmap.analisis import MOTORES
from modules.downloader.schema import DownloaderParams
from modules.subtitles.schema import VALID_LANGUAGES, VALID_MODELS


def _bool(form, name: str, default: bool = False) -> bool:
    raw = form.get(name)
    if raw in (None, ""):
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "on", "yes", "si", "sí"}


def musica_de_form(form, prefijo: str = "musica") -> DownloaderParams | None:
    """El link de la música (`<prefijo>_url`), o None si llega como archivo.
    Lo comparten beatmap, anime_edit y el modo edit de competitivo."""
    url = str(form.get(f"{prefijo}_url") or "").strip()
    if not url:
        return None
    if not re.match(r"^https?://\S+$", url):
        raise ValueError(f"'{prefijo}_url' tiene que ser un link http(s).")
    return DownloaderParams.from_form({"url": url, "format": "audio", "quality": "320",
                                       "cookies_browser": form.get("cookies_browser") or ""})


@dataclass
class LetraParams:
    """Si se saca la letra y cómo (los comparten los módulos de edits)."""
    letra: bool = False
    separar_voz: bool = True
    idioma: str = "auto"
    modelo: str = "large-v3-turbo"

    @classmethod
    def from_form(cls, form, letra_defecto: bool = False) -> "LetraParams":
        idioma = str(form.get("idioma") or "auto").strip().lower()
        if idioma not in VALID_LANGUAGES:
            raise ValueError(f"Idioma inválido: {idioma!r}. Opciones: {sorted(VALID_LANGUAGES)}")
        modelo = str(form.get("modelo") or "large-v3-turbo").strip().lower()
        if modelo not in VALID_MODELS:
            raise ValueError(f"Modelo inválido: {modelo!r}. Opciones: {sorted(VALID_MODELS)}")
        return cls(letra=_bool(form, "letra", letra_defecto), separar_voz=_bool(form, "separar_voz", True),
                   idioma=idioma, modelo=modelo)


@dataclass
class BeatmapParams:
    motor: str = "auto"
    preview: bool = True
    musica: DownloaderParams | None = None      # None → la música llega como archivo
    letra: LetraParams = field(default_factory=LetraParams)

    @classmethod
    def from_form(cls, form) -> "BeatmapParams":
        motor = str(form.get("motor") or "auto").strip().lower()
        if motor not in MOTORES:
            raise ValueError(f"Motor inválido: {motor!r}. Opciones: {', '.join(MOTORES)}.")
        return cls(motor=motor, preview=_bool(form, "preview", True), musica=musica_de_form(form),
                   letra=LetraParams.from_form(form))
