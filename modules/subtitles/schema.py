"""Parámetros y validación del módulo subtitles.

Transcribe el audio del video con faster-whisper y quema subtítulos estilo
karaoke (palabra activa coloreada) usando libass vía FFmpeg.
"""
import os
import re
from dataclasses import dataclass

import config
from modules.subtitles import fonts

VALID_LANGUAGES = {"auto", "es", "en", "pt", "fr", "de", "it", "ja", "ko", "zh"}
VALID_MODELS    = {"tiny", "base", "small", "medium",
                   "large-v2", "large-v3", "large-v3-turbo"}

_INT_PARAMS = {
    "font_size":    (64,   30, 140),
    "position_y":   (1560,  0, 1920),
    "words_per_line":(3,    1,    6),
}

# --- Estilo del texto ---
COLOR_MODES = {"single", "rotate"}     # un color de resalte / colores que rotan
OUTLINE_COLORS = {"black", "white"}
OUTLINE_MIN, OUTLINE_MAX, OUTLINE_DEFAULT = 0, 20, 6   # px en el lienzo 1080x1920
# "Colores que rotan": cada palabra que se dice toma el siguiente color de la
# paleta (sólidos y saturados, para que se lean sobre el gameplay). El preview
# la recibe por GET /api/subtitles/styles: esta es la única definición.
ROTATE_PALETTE = ("#FFE600", "#3DFF6E", "#FF4FD8", "#3DE8FF", "#FF9F1C")
# Disposición de cada frase y tamaños de sus palabras (ver processor._group_style).
LAYOUTS = {"line", "stairs", "mixed"}       # en línea / en escalera / variada
SIZE_MODES = {"same", "grow", "random"}     # iguales / crecientes / variados
_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


@dataclass
class SubtitlesParams:
    font_size:      int = 64
    position_y:     int = 1560      # px desde arriba en el lienzo 1080×1920
    words_per_line: int = 3
    highlight_color: str = "&H0000FFFB&"  # amarillo #fbff00 (formato ASS BGR)
    language:       str = "es"
    model:          str = "large-v3-turbo"
    font:           str = config.DEFAULT_FONT_FILE   # archivo en assets/fonts/
    uppercase:      bool = False
    color_mode:     str = "single"    # single | rotate
    outline_color:  str = "black"     # black | white (con blanco, el texto va negro)
    outline_size:   int = OUTLINE_DEFAULT
    layout:         str = "line"      # line | stairs | mixed
    sizes:          str = "same"      # same | grow | random

    @classmethod
    def from_form(cls, form) -> "SubtitlesParams":
        values: dict = {}
        for name, (default, lo, hi) in _INT_PARAMS.items():
            raw = form.get(name)
            value = default if raw in (None, "") else _to_int(name, raw)
            values[name] = _clamp(name, value, lo, hi)

        lang = str(form.get("language") or "es").strip().lower()
        if lang not in VALID_LANGUAGES:
            raise ValueError(
                f"Idioma inválido: {lang!r}. Opciones: {sorted(VALID_LANGUAGES)}"
            )
        values["language"] = lang

        model = str(form.get("model") or "large-v3-turbo").strip().lower()
        if model not in VALID_MODELS:
            raise ValueError(
                f"Modelo inválido: {model!r}. Opciones: {sorted(VALID_MODELS)}"
            )
        values["model"] = model

        # Color de resalte: el usuario manda hex CSS (#RRGGBB), lo convertimos
        # al formato ASS BGR (&H00BBGGRR&). Si no viene, usamos el default.
        raw_color = str(form.get("highlight_color") or "").strip()
        if raw_color:
            values["highlight_color"] = _css_to_ass(raw_color)
        else:
            values["highlight_color"] = "&H0000FFFB&"  # amarillo #fbff00

        # Estilo. Los nombres de campo llevan prefijo sub_ porque reel_express y
        # hook_reel arman todos sus params desde el MISMO form.
        # str(): en los /render y /finish llega un JSON, que puede traer un
        # número o una lista; así sale un 400 legible y no un 500.
        font = os.path.basename(str(form.get("sub_font") or "").strip()) or config.DEFAULT_FONT_FILE
        if not fonts.is_available(font):
            raise ValueError(f"La fuente {font!r} no está en assets/fonts/.")
        values["font"] = font
        values["uppercase"] = _to_bool(form.get("sub_uppercase"))

        mode = str(form.get("sub_color_mode") or "single").strip().lower()
        if mode not in COLOR_MODES:
            raise ValueError(f"Modo de color inválido: {mode!r}. Opciones: {sorted(COLOR_MODES)}")
        values["color_mode"] = mode

        outline = str(form.get("sub_outline_color") or "black").strip().lower()
        if outline not in OUTLINE_COLORS:
            raise ValueError(f"Color de contorno inválido: {outline!r}. Opciones: {sorted(OUTLINE_COLORS)}")
        values["outline_color"] = outline

        raw = form.get("sub_outline_size")
        size = OUTLINE_DEFAULT if raw in (None, "") else _to_int("El grosor del contorno", raw)
        values["outline_size"] = _clamp("El grosor del contorno", size, OUTLINE_MIN, OUTLINE_MAX)

        layout = str(form.get("sub_layout") or "line").strip().lower()
        if layout not in LAYOUTS:
            raise ValueError(f"Disposición inválida: {layout!r}. Opciones: line (en línea), "
                             "stairs (en escalera) o mixed (variada).")
        values["layout"] = layout

        sizes = str(form.get("sub_sizes") or "same").strip().lower()
        if sizes not in SIZE_MODES:
            raise ValueError(f"Tamaño de palabras inválido: {sizes!r}. Opciones: same (iguales), "
                             "grow (crecientes) o random (variados).")
        values["sizes"] = sizes

        return cls(**values)


def _to_int(name: str, raw) -> int:
    try:
        return int(float(raw))
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} debe ser numérico, se recibió: {raw!r}")


def _clamp(name: str, value: int, lo: int, hi: int) -> int:
    if value < lo or value > hi:
        raise ValueError(f"{name} fuera de rango [{lo}, {hi}]: {value}")
    return value


def _to_bool(raw) -> bool:
    """Checkbox de un form ("on"/"true"/"1") o booleano de un JSON."""
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in {"1", "true", "on", "yes", "si", "sí"}


def _css_to_ass(css: str) -> str:
    """Convierte #RRGGBB → &H00BBGGRR& (formato de color ASS, mayúsculas)."""
    if not _HEX_RE.match(css or ""):
        raise ValueError(f"Color inválido: {css!r} (usar #RRGGBB).")
    css = css.lstrip("#").upper()
    rr, gg, bb = css[0:2], css[2:4], css[4:6]
    return f"&H00{bb}{gg}{rr}&"
