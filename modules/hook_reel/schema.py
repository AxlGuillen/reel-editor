"""Parámetros y validación del módulo hook_reel ("Reel Frase").

Reel de dos segmentos encadenados con una música de fondo continua:

    Segmento 1 (~voz):  fondo (imagen o video) + tu voz diciendo la frase
    Segmento 2 (~resto): otro video acelerado para calzar
    Música:             baja mientras hablás, sube a tope en el corte (ducking)

La duración total la manda la MÚSICA: D1 = duración de la voz, D2 = música - D1
(el clip 2 se acelera/frena para durar D2). La validación música > voz vive en
el processor (necesita las duraciones reales de los archivos).

No define parámetros de imagen propios: reusa VerticalConvertParams (para los
clips marcados 16:9) y SubtitlesParams (subtítulos sobre el segmento 1).
"""
from dataclasses import dataclass

from modules.vertical_convert.schema import VerticalConvertParams
from modules.subtitles.schema import SubtitlesParams

# volumen (%) -> (default, min, max). La música baja durante la frase y sube
# a "full" después; el full llega hasta 150% para poder empujar el beat.
_VOL_PARAMS = {
    "music_low_volume": (25, 0, 100),
    "music_full_volume": (100, 0, 150),
}

# Rampa (segundos) del cambio de volumen en el corte: 0 = golpe seco.
DEFAULT_RAMP = 0.4
RAMP_MIN, RAMP_MAX = 0.0, 2.0

# Posición vertical por defecto de los subtítulos del segmento 1 (px en 1080×1920).
HOOK_SUBTITLE_POSITION_Y = 1601


@dataclass
class HookReelParams:
    vertical: VerticalConvertParams
    music_low_volume: int = 25
    music_full_volume: int = 100
    ramp: float = DEFAULT_RAMP
    seg1_vertical: bool = False     # convertir el fondo (si es video) a 9:16
    seg2_vertical: bool = True      # convertir el clip 2 a 9:16
    add_subtitles: bool = True      # subtítulos karaoke sobre el segmento 1
    subtitles: SubtitlesParams | None = None  # solo si add_subtitles

    @classmethod
    def from_form(cls, form) -> "HookReelParams":
        vertical = VerticalConvertParams.from_form(form)

        values: dict = {}
        for name, (default, lo, hi) in _VOL_PARAMS.items():
            raw = form.get(name)
            value = default if raw in (None, "") else _to_int(name, raw)
            values[name] = _clamp(name, value, lo, hi)

        raw_ramp = form.get("ramp")
        ramp = DEFAULT_RAMP if raw_ramp in (None, "") else _to_float("ramp", raw_ramp)
        values["ramp"] = _clamp("ramp", ramp, RAMP_MIN, RAMP_MAX)

        values["seg1_vertical"] = _to_bool(form.get("seg1_vertical"), False)
        values["seg2_vertical"] = _to_bool(form.get("seg2_vertical"), True)

        add_subtitles = _to_bool(form.get("add_subtitles"), True)
        values["add_subtitles"] = add_subtitles
        subtitles = None
        if add_subtitles:
            subtitles = SubtitlesParams.from_form(form)
            # Default de posición propio del pipeline si el form no la trae.
            if not (form.get("position_y") or "").strip():
                subtitles.position_y = HOOK_SUBTITLE_POSITION_Y
        values["subtitles"] = subtitles

        return cls(vertical=vertical, **values)

    @property
    def music_low_gain(self) -> float:
        return self.music_low_volume / 100

    @property
    def music_full_gain(self) -> float:
        return self.music_full_volume / 100

    @property
    def has_subtitles(self) -> bool:
        return self.add_subtitles and self.subtitles is not None


def _to_int(name: str, raw) -> int:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        raise ValueError(f"{name} debe ser numérico, se recibió: {raw!r}")


def _to_float(name: str, raw) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} debe ser numérico, se recibió: {raw!r}")


def _clamp(name: str, value, lo, hi):
    if value < lo or value > hi:
        raise ValueError(f"{name} fuera de rango [{lo}, {hi}]: {value}")
    return value


def _to_bool(raw, default: bool) -> bool:
    if raw is None:
        return default
    return str(raw).lower() in {"1", "true", "on", "yes"}
