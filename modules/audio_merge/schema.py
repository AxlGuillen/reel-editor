"""Parámetros y validación del módulo audio_merge.

Une varios audios en el orden recibido. El orden lo controla el frontend (el
orden en que sube los archivos), así que el único parámetro es el silencio
opcional entre clips.
"""
from dataclasses import dataclass

MIN_FILES = 2
MAX_FILES = 20
GAP_MIN = 0.0
GAP_MAX = 5.0


@dataclass
class AudioMergeParams:
    gap: float = 0.0  # segundos de silencio entre clips

    @classmethod
    def from_form(cls, form) -> "AudioMergeParams":
        """Construye y valida parámetros desde un form de Flask (request.form)."""
        raw = form.get("gap")
        if raw in (None, ""):
            gap = 0.0
        else:
            try:
                gap = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"gap debe ser numérico, se recibió: {raw!r}")
        if gap < GAP_MIN or gap > GAP_MAX:
            raise ValueError(f"gap fuera de rango [{GAP_MIN}, {GAP_MAX}]: {gap}")
        return cls(gap=gap)
