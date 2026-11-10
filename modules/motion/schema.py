"""Parameters of the motion module (the motion map of a video span)."""
from __future__ import annotations

import os
from dataclasses import dataclass

import config
from modules.beatmap.schema import to_bool

WIDTH_RANGE = (160, 1280)


def _seconds(form, name: str) -> float | None:
    raw = str(form.get(name) or "").strip()
    if not raw:
        return None
    try:
        if ":" in raw:                       # m:ss(.d) or h:mm:ss
            value = 0.0
            for part in raw.split(":"):
                value = value * 60 + float(part)
        else:
            value = float(raw)
    except ValueError:
        raise ValueError(f"'{name}' tiene que ser segundos o m:ss (llegó {raw!r}).")
    if value < 0:
        raise ValueError(f"'{name}' no puede ser negativo.")
    return value


@dataclass
class MotionParams:
    start: float | None = None
    end: float | None = None
    width: int = 480
    preview: bool = False
    path: str = ""                           # a local file (Claude operating the app on this machine)

    @classmethod
    def from_form(cls, form) -> "MotionParams":
        start, end = _seconds(form, "start"), _seconds(form, "end")
        if start is not None and end is not None and end <= start:
            raise ValueError("El fin del tramo tiene que ser mayor que el inicio.")
        try:
            width = int(form.get("width") or 480)
        except ValueError:
            raise ValueError("'width' tiene que ser un número entero de píxeles.")
        if not WIDTH_RANGE[0] <= width <= WIDTH_RANGE[1]:
            raise ValueError(f"'width' fuera de rango: {WIDTH_RANGE[0]}–{WIDTH_RANGE[1]} px.")
        path = str(form.get("path") or "").strip().strip('"')
        if path:
            if not os.path.isfile(path):
                raise ValueError(f"No existe el archivo: {path}")
            if not config.allowed_file(path):
                raise ValueError(f"Video: extensión no permitida. Usar: {sorted(config.ALLOWED_EXTENSIONS)}")
        return cls(start=start, end=end, width=width, preview=to_bool(form, "preview", False), path=path)
