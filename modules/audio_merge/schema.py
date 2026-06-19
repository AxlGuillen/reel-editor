"""Parámetros y validación del módulo audio_merge.

Une varios audios en el orden recibido (lo controla el frontend) y luego capa
las pausas: ningún silencio de la narración supera `max_pause` segundos, para
que el resultado fluya. El único parámetro es ese tope de pausa.
"""
from dataclasses import dataclass

MIN_FILES = 2
MAX_FILES = 20
MAX_PAUSE_MIN = 0.1
MAX_PAUSE_MAX = 1.5
DEFAULT_MAX_PAUSE = 0.5


@dataclass
class AudioMergeParams:
    max_pause: float = DEFAULT_MAX_PAUSE  # segundos: tope de cada pausa

    @classmethod
    def from_form(cls, form) -> "AudioMergeParams":
        """Construye y valida parámetros desde un form de Flask (request.form)."""
        raw = form.get("max_pause")
        if raw in (None, ""):
            max_pause = DEFAULT_MAX_PAUSE
        else:
            try:
                max_pause = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"max_pause debe ser numérico, se recibió: {raw!r}")
        if max_pause < MAX_PAUSE_MIN or max_pause > MAX_PAUSE_MAX:
            raise ValueError(
                f"max_pause fuera de rango [{MAX_PAUSE_MIN}, {MAX_PAUSE_MAX}]: {max_pause}"
            )
        return cls(max_pause=max_pause)
