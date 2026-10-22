"""Parámetros de anime_edit (fase 1: material y canción; fase 2: el edit)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import config
from modules.anime_edit.plan import FORMATOS
from modules.anime_edit.presets import PRESETS
from modules.beatmap.analisis import MOTORES
from modules.beatmap.schema import LetraParams, _bool, musica_de_form
from modules.downloader.schema import DownloaderParams
from modules.edicion import efectos

TEXTOS = ("auto", "letra", "off")


def _num(form, name: str, default, lo, hi, cast=float):
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


def _lista(form, name: str):
    raw = form.get(name)
    if raw in (None, ""):
        return None
    if isinstance(raw, list):
        return raw
    try:
        valor = json.loads(raw)
    except (TypeError, ValueError):
        valor = [x for x in re.split(r"[\s,;]+", str(raw)) if x]
    return valor if isinstance(valor, list) else [valor]


@dataclass
class AnimeEditParams:
    """Fase 1: el video (archivo, librería o link), la canción y si se saca la letra."""
    video: DownloaderParams | None = None       # link del video; None → archivo o librería
    musica: DownloaderParams | None = None      # link de la canción; None → archivo
    musica_del_video: bool = False              # la canción es el audio del propio video
    motor: str = "auto"
    letra: LetraParams = field(default_factory=LetraParams)

    @classmethod
    def from_form(cls, form) -> "AnimeEditParams":
        url = str(form.get("video_url") or "").strip()
        video = None
        if url:
            if not re.match(r"^https?://\S+$", url):
                raise ValueError("'video_url' tiene que ser un link http(s).")
            video = DownloaderParams.from_form({"url": url, "format": "video", "quality": "1080",
                                                "cookies_browser": form.get("cookies_browser") or ""})
        motor = str(form.get("motor") or "auto").strip().lower()
        if motor not in MOTORES:
            raise ValueError(f"Motor inválido: {motor!r}. Opciones: {', '.join(MOTORES)}.")
        return cls(video=video, musica=musica_de_form(form),
                   musica_del_video=_bool(form, "musica_del_video", False), motor=motor,
                   letra=LetraParams.from_form(form, letra_defecto=True))


@dataclass
class FinishParams:
    """Fase 2: qué planos, en qué orden, y cómo se ve cada variante."""
    seleccion: list[int] | None = None          # ids en orden; None = la propuesta
    heroe: int | None = None
    variantes: list[str] = field(default_factory=lambda: ["impacto"])
    formato: str = "vertical"
    duracion_s: float = 30
    musica_desde: float | None = None
    texto: str = "auto"
    look: str | None = None
    lut: str = ""
    intensidad: float = 1.0
    fps: int = 30
    interpolar: bool = True
    escalar: bool = False
    volumen_original: int = 0
    watermark: str = ""
    marca_alto: int = 150
    titulo: str = ""

    @classmethod
    def from_form(cls, form, ids_validos: set[int]) -> "FinishParams":
        sel = _lista(form, "seleccion")
        if sel is not None:
            try:
                sel = [int(x) for x in sel]
            except (TypeError, ValueError):
                raise ValueError("'seleccion' es una lista de números de plano, p. ej. [3, 7, 12].")
            malos = [x for x in sel if x not in ids_validos]
            if malos:
                raise ValueError(f"No existe el plano {malos[0]}.")
            if not sel:
                raise ValueError("Elegí al menos un plano.")
        heroe = form.get("heroe")
        if heroe not in (None, ""):
            try:
                heroe = int(heroe)
            except (TypeError, ValueError):
                raise ValueError("'heroe' es el número de un plano.")
            if heroe not in ids_validos:
                raise ValueError(f"No existe el plano {heroe}.")
        else:
            heroe = None
        variantes = _lista(form, "variantes") or ["impacto"]
        malas = [v for v in variantes if v not in PRESETS]
        if malas:
            raise ValueError(f"Variante desconocida: {malas[0]!r}. Opciones: {', '.join(PRESETS)}.")
        if len(variantes) > 6:
            raise ValueError("Máximo 6 variantes por vez.")
        formato = str(form.get("formato") or "vertical").strip().lower()
        if formato not in FORMATOS:
            raise ValueError(f"Formato inválido: {formato!r}. Opciones: {', '.join(FORMATOS)}.")
        texto = str(form.get("texto") or "auto").strip().lower()
        if texto not in TEXTOS:
            raise ValueError(f"'texto' tiene que ser uno de: {', '.join(TEXTOS)}.")
        look = str(form.get("look") or "").strip().lower() or None
        if look and look not in efectos.LOOKS:
            raise ValueError(f"Look desconocido: {look!r}. Opciones: {', '.join(efectos.LOOKS)}.")
        lut = os.path.basename(str(form.get("lut") or "").strip())
        if lut and lut not in efectos.luts():
            raise ValueError(f"No existe el LUT «{lut}» en assets/luts.")
        desde = form.get("musica_desde")
        if desde in (None, "", "auto"):
            desde = None
        else:
            desde = _num(form, "musica_desde", None, 0, 3600)
        fps = _num(form, "fps", 30, 24, 60, int)
        if fps not in (24, 30, 60):
            raise ValueError("'fps' tiene que ser 24, 30 o 60.")
        marca = os.path.basename(str(form.get("watermark") or "").strip())
        if marca and not os.path.isfile(os.path.join(config.WATERMARKS_FOLDER, marca)):
            raise ValueError(f"No existe el watermark «{marca}» en assets/watermarks.")
        titulo = " ".join(str(form.get("titulo") or "").split())[:80]
        return cls(seleccion=sel, heroe=heroe, variantes=list(dict.fromkeys(variantes)), formato=formato,
                   duracion_s=_num(form, "duracion_s", 30, 5, 180), musica_desde=desde, texto=texto,
                   look=look, lut=lut, intensidad=_num(form, "intensidad", 1.0, 0, 1.5),
                   fps=fps, interpolar=_bool(form, "interpolar", True), escalar=_bool(form, "escalar", False),
                   volumen_original=_num(form, "volumen_original", 0, 0, 100, int),
                   watermark=marca, marca_alto=_num(form, "marca_alto", 150, 60, 400, int), titulo=titulo)
