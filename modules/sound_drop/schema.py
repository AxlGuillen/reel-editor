"""Parámetros y validación del módulo sound_drop.

Toma un video vertical y le superpone un audio externo (de un archivo de audio
o de otro video). Permite mutear/bajar el original, ajustar el volumen del
nuevo, aplicar fade in/out y acelerar el video para igualar la duración del
audio (speed match).
"""
from dataclasses import dataclass

# nombre -> (default, min, max). Volúmenes en porcentaje (100 = sin cambio).
_INT_PARAMS = {
    "original_volume": (0, 0, 100),
    "new_volume": (100, 0, 150),
}
_BOOL_PARAMS = {
    "fade": True,
    "speed_match": True,
}

FADE_DURATION = 0.8  # segundos de fade in/out cuando fade=True


@dataclass
class SoundDropParams:
    original_volume: int = 0
    new_volume: int = 100
    fade: bool = True
    speed_match: bool = True

    @classmethod
    def from_form(cls, form) -> "SoundDropParams":
        """Construye y valida parámetros desde un form de Flask (request.form).

        Lanza ValueError con un mensaje legible si algún valor es inválido.
        """
        values: dict = {}

        for name, (default, lo, hi) in _INT_PARAMS.items():
            raw = form.get(name)
            value = default if raw in (None, "") else _to_int(name, raw)
            values[name] = _clamp(name, value, lo, hi)

        for name, default in _BOOL_PARAMS.items():
            values[name] = _to_bool(form.get(name), default)

        return cls(**values)

    @property
    def original_gain(self) -> float:
        return self.original_volume / 100

    @property
    def new_gain(self) -> float:
        return self.new_volume / 100


def _to_int(name: str, raw):
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        raise ValueError(f"{name} debe ser numérico, se recibió: {raw!r}")


def _to_bool(raw, default: bool) -> bool:
    if raw is None:
        return default
    return str(raw).lower() in {"1", "true", "on", "yes"}


def _clamp(name: str, value, lo, hi):
    if value < lo or value > hi:
        raise ValueError(f"{name} fuera de rango [{lo}, {hi}]: {value}")
    return value
