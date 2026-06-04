"""Parámetros y validación del módulo vertical_convert."""
from dataclasses import dataclass

# nombre -> (default, min, max)
_INT_PARAMS = {
    "blur_intensity": (50, 0, 50),
    "enhance_intensity": (0, 0, 100),
}
_FLOAT_PARAMS = {
    "bg_brightness": (0.5, 0.3, 1.0),
    "main_clip_scale": (1.55, 0.7, 5.0),
}
_VALID_POSITIONS = {"center", "top", "bottom"}


@dataclass
class VerticalConvertParams:
    blur_intensity: int = 50
    bg_brightness: float = 0.5
    main_clip_scale: float = 1.55
    main_clip_position: str = "center"
    enhance_intensity: int = 0

    @classmethod
    def from_form(cls, form) -> "VerticalConvertParams":
        """Construye y valida parámetros desde un form de Flask (request.form).

        Lanza ValueError con un mensaje legible si algún valor es inválido.
        """
        values: dict = {}

        for name, (default, lo, hi) in _INT_PARAMS.items():
            raw = form.get(name)
            value = default if raw in (None, "") else _to_number(name, raw, int)
            values[name] = _clamp(name, value, lo, hi)

        for name, (default, lo, hi) in _FLOAT_PARAMS.items():
            raw = form.get(name)
            value = default if raw in (None, "") else _to_number(name, raw, float)
            values[name] = _clamp(name, value, lo, hi)

        position = (form.get("main_clip_position") or "center").lower()
        if position not in _VALID_POSITIONS:
            raise ValueError(
                f"main_clip_position debe ser uno de {sorted(_VALID_POSITIONS)}"
            )
        values["main_clip_position"] = position

        return cls(**values)


def _to_number(name: str, raw, caster):
    try:
        return caster(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} debe ser numérico, se recibió: {raw!r}")


def _clamp(name: str, value, lo, hi):
    if value < lo or value > hi:
        raise ValueError(f"{name} fuera de rango [{lo}, {hi}]: {value}")
    return value
