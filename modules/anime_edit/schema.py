"""anime_edit parameters (phase 1: footage and song; phase 2: the edit)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import config
from modules.anime_edit.plan import FORMATS
from modules.anime_edit.presets import PRESETS
from modules.beatmap.analysis import ENGINES
from modules.beatmap.schema import LyricsParams, music_from_form, to_bool
from modules.downloader.schema import DownloaderParams
from modules.editing import effects

TEXT_MODES = ("auto", "lyrics", "off")


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


def _list(form, name: str):
    raw = form.get(name)
    if raw in (None, ""):
        return None
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        value = [x for x in re.split(r"[\s,;]+", str(raw)) if x]
    return value if isinstance(value, list) else [value]


@dataclass
class AnimeEditParams:
    """Phase 1: the video (file, library or link), the song and whether to extract the lyrics."""
    video: DownloaderParams | None = None       # video link; None → file or library
    music: DownloaderParams | None = None       # song link; None → file
    music_from_video: bool = False              # the song is the video's own audio
    engine: str = "auto"
    lyrics: LyricsParams = field(default_factory=LyricsParams)

    @classmethod
    def from_form(cls, form) -> "AnimeEditParams":
        url = str(form.get("video_url") or "").strip()
        video = None
        if url:
            if not re.match(r"^https?://\S+$", url):
                raise ValueError("'video_url' tiene que ser un link http(s).")
            video = DownloaderParams.from_form({"url": url, "format": "video", "quality": "1080",
                                                "cookies_browser": form.get("cookies_browser") or ""})
        engine = str(form.get("engine") or "auto").strip().lower()
        if engine not in ENGINES:
            raise ValueError(f"Motor inválido: {engine!r}. Opciones: {', '.join(ENGINES)}.")
        return cls(video=video, music=music_from_form(form),
                   music_from_video=to_bool(form, "music_from_video", False), engine=engine,
                   lyrics=LyricsParams.from_form(form, lyrics_default=True))


@dataclass
class FinishParams:
    """Phase 2: which shots, in what order, and how each variant looks."""
    selection: list[int] | None = None          # ids in order; None = the proposal
    hero: int | None = None
    variants: list[str] = field(default_factory=lambda: ["impact"])
    format: str = "vertical"
    duration_s: float = 30
    music_start: float | None = None
    text: str = "auto"
    look: str | None = None
    lut: str = ""
    intensity: float = 1.0
    fps: int = 30
    interpolate: bool = True
    upscale: bool = False
    original_volume: int = 0
    watermark: str = ""
    watermark_height: int = 150
    title: str = ""

    @classmethod
    def from_form(cls, form, valid_ids: set[int]) -> "FinishParams":
        sel = _list(form, "selection")
        if sel is not None:
            try:
                sel = [int(x) for x in sel]
            except (TypeError, ValueError):
                raise ValueError("'selection' es una lista de números de plano, p. ej. [3, 7, 12].")
            bad = [x for x in sel if x not in valid_ids]
            if bad:
                raise ValueError(f"No existe el plano {bad[0]}.")
            if not sel:
                raise ValueError("Elegí al menos un plano.")
        hero = form.get("hero")
        if hero not in (None, ""):
            try:
                hero = int(hero)
            except (TypeError, ValueError):
                raise ValueError("'hero' es el número de un plano.")
            if hero not in valid_ids:
                raise ValueError(f"No existe el plano {hero}.")
        else:
            hero = None
        variants = _list(form, "variants") or ["impact"]
        bad_variants = [v for v in variants if v not in PRESETS]
        if bad_variants:
            raise ValueError(f"Variante desconocida: {bad_variants[0]!r}. Opciones: {', '.join(PRESETS)}.")
        if len(variants) > 6:
            raise ValueError("Máximo 6 variantes por vez.")
        fmt = str(form.get("format") or "vertical").strip().lower()
        if fmt not in FORMATS:
            raise ValueError(f"Formato inválido: {fmt!r}. Opciones: {', '.join(FORMATS)}.")
        text = str(form.get("text") or "auto").strip().lower()
        if text not in TEXT_MODES:
            raise ValueError(f"'text' tiene que ser uno de: {', '.join(TEXT_MODES)}.")
        look = str(form.get("look") or "").strip().lower() or None
        if look and look not in effects.LOOKS:
            raise ValueError(f"Look desconocido: {look!r}. Opciones: {', '.join(effects.LOOKS)}.")
        lut = os.path.basename(str(form.get("lut") or "").strip())
        if lut and lut not in effects.luts():
            raise ValueError(f"No existe el LUT «{lut}» en assets/luts.")
        start = form.get("music_start")
        if start in (None, "", "auto"):
            start = None
        else:
            start = _num(form, "music_start", None, 0, 3600)
        fps = _num(form, "fps", 30, 24, 60, int)
        if fps not in (24, 30, 60):
            raise ValueError("'fps' tiene que ser 24, 30 o 60.")
        watermark = os.path.basename(str(form.get("watermark") or "").strip())
        if watermark and not os.path.isfile(os.path.join(config.WATERMARKS_FOLDER, watermark)):
            raise ValueError(f"No existe el watermark «{watermark}» en assets/watermarks.")
        title = " ".join(str(form.get("title") or "").split())[:80]
        return cls(selection=sel, hero=hero, variants=list(dict.fromkeys(variants)), format=fmt,
                   duration_s=_num(form, "duration_s", 30, 5, 180), music_start=start, text=text,
                   look=look, lut=lut, intensity=_num(form, "intensity", 1.0, 0, 1.5),
                   fps=fps, interpolate=to_bool(form, "interpolate", True), upscale=to_bool(form, "upscale", False),
                   original_volume=_num(form, "original_volume", 0, 0, 100, int),
                   watermark=watermark, watermark_height=_num(form, "watermark_height", 150, 60, 400, int),
                   title=title)
