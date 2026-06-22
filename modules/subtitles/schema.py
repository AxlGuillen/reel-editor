"""Parámetros y validación del módulo subtitles.

Transcribe el audio del video con faster-whisper y quema subtítulos estilo
karaoke (palabra activa coloreada) usando libass vía FFmpeg.
"""
from dataclasses import dataclass

VALID_LANGUAGES = {"auto", "es", "en", "pt", "fr", "de", "it", "ja", "ko", "zh"}
VALID_MODELS    = {"tiny", "base", "small", "medium",
                   "large-v2", "large-v3", "large-v3-turbo"}

_INT_PARAMS = {
    "font_size":    (64,   30, 140),
    "position_y":   (1560,  0, 1920),
    "words_per_line":(3,    1,    6),
}


@dataclass
class SubtitlesParams:
    font_size:      int = 64
    position_y:     int = 1560      # px desde arriba en el lienzo 1080×1920
    words_per_line: int = 3
    highlight_color: str = "&H0000FFFB&"  # amarillo #fbff00 (formato ASS BGR)
    language:       str = "es"
    model:          str = "large-v3-turbo"

    @classmethod
    def from_form(cls, form) -> "SubtitlesParams":
        values: dict = {}
        for name, (default, lo, hi) in _INT_PARAMS.items():
            raw = form.get(name)
            value = default if raw in (None, "") else _to_int(name, raw)
            values[name] = _clamp(name, value, lo, hi)

        lang = (form.get("language") or "es").strip().lower()
        if lang not in VALID_LANGUAGES:
            raise ValueError(
                f"Idioma inválido: {lang!r}. Opciones: {sorted(VALID_LANGUAGES)}"
            )
        values["language"] = lang

        model = (form.get("model") or "large-v3-turbo").strip().lower()
        if model not in VALID_MODELS:
            raise ValueError(
                f"Modelo inválido: {model!r}. Opciones: {sorted(VALID_MODELS)}"
            )
        values["model"] = model

        # Color de resalte: el usuario manda hex CSS (#RRGGBB), lo convertimos
        # al formato ASS BGR (&H00BBGGRR&). Si no viene, usamos el default.
        raw_color = (form.get("highlight_color") or "").strip()
        if raw_color:
            values["highlight_color"] = _css_to_ass(raw_color)
        else:
            values["highlight_color"] = "&H0000FFFB&"  # amarillo #fbff00

        return cls(**values)


def _to_int(name: str, raw) -> int:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        raise ValueError(f"{name} debe ser numérico, se recibió: {raw!r}")


def _clamp(name: str, value: int, lo: int, hi: int) -> int:
    if value < lo or value > hi:
        raise ValueError(f"{name} fuera de rango [{lo}, {hi}]: {value}")
    return value


def _css_to_ass(css: str) -> str:
    """Convierte #RRGGBB → &H00BBGGRR& (formato de color ASS, mayúsculas)."""
    css = css.lstrip("#").upper()
    if len(css) != 6:
        return "&H00FFFF00&"
    rr, gg, bb = css[0:2], css[2:4], css[4:6]
    return f"&H00{bb}{gg}{rr}&"
