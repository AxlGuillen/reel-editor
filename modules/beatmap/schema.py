"""Parameters of the beatmap module (song analysis)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from modules.beatmap.analysis import ENGINES
from modules.downloader.schema import DownloaderParams
from modules.subtitles.schema import VALID_LANGUAGES, VALID_MODELS


def to_bool(form, name: str, default: bool = False) -> bool:
    raw = form.get(name)
    if raw in (None, ""):
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "on", "yes", "si", "sí"}


def music_from_form(form, prefix: str = "music") -> DownloaderParams | None:
    """The music link (`<prefix>_url`), or None if it arrives as a file.
    Shared by beatmap, anime_edit and competitivo's edit mode."""
    url = str(form.get(f"{prefix}_url") or "").strip()
    if not url:
        return None
    if not re.match(r"^https?://\S+$", url):
        raise ValueError(f"'{prefix}_url' tiene que ser un link http(s).")
    return DownloaderParams.from_form({"url": url, "format": "audio", "quality": "320",
                                       "cookies_browser": form.get("cookies_browser") or ""})


@dataclass
class LyricsParams:
    """Whether to extract the lyrics and how (shared by the edit modules)."""
    lyrics: bool = False
    separate_vocals: bool = True
    language: str = "auto"
    model: str = "large-v3-turbo"

    @classmethod
    def from_form(cls, form, lyrics_default: bool = False) -> "LyricsParams":
        language = str(form.get("language") or "auto").strip().lower()
        if language not in VALID_LANGUAGES:
            raise ValueError(f"Idioma inválido: {language!r}. Opciones: {sorted(VALID_LANGUAGES)}")
        model = str(form.get("model") or "large-v3-turbo").strip().lower()
        if model not in VALID_MODELS:
            raise ValueError(f"Modelo inválido: {model!r}. Opciones: {sorted(VALID_MODELS)}")
        return cls(lyrics=to_bool(form, "lyrics", lyrics_default),
                   separate_vocals=to_bool(form, "separate_vocals", True),
                   language=language, model=model)


@dataclass
class BeatmapParams:
    engine: str = "auto"
    preview: bool = True
    music: DownloaderParams | None = None      # None → the song arrives as a file
    lyrics: LyricsParams = field(default_factory=LyricsParams)

    @classmethod
    def from_form(cls, form) -> "BeatmapParams":
        engine = str(form.get("engine") or "auto").strip().lower()
        if engine not in ENGINES:
            raise ValueError(f"Motor inválido: {engine!r}. Opciones: {', '.join(ENGINES)}.")
        return cls(engine=engine, preview=to_bool(form, "preview", True), music=music_from_form(form),
                   lyrics=LyricsParams.from_form(form))
