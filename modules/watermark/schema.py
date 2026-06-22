"""Parámetros y validación del módulo watermark.

Compone sobre un video vertical un bloque de texto (principal blanco +
secundario rojo, con contorno) y/o un watermark de la librería de assets. No
toca el audio (eso es sound_drop); preserva la pista original tal cual.
"""
from dataclasses import dataclass

# nombre -> (default, min, max).
_INT_PARAMS = {
    "text_size": (55, 24, 140),       # px de la fuente (lienzo de 1080 de ancho)
    "text_y": (20, 0, 100),           # posición vertical del bloque (% del alto)
    "watermark_size": (50, 10, 70),   # % del ancho del video
    "watermark_y": (100, 0, 100),     # posición vertical del watermark (% del alto)
}
_VALID_WATERMARK_X = {"left", "center", "right"}
MAX_TEXT_LEN = 200


@dataclass
class WatermarkParams:
    primary_text: str = ""
    secondary_text: str = ""
    text_size: int = 55
    text_y: int = 20                  # % del alto: 0 = arriba, 100 = abajo
    watermark: str = ""               # nombre del archivo en assets/watermarks/
    watermark_x: str = "center"
    watermark_size: int = 50
    watermark_y: int = 100            # % del alto: 0 = arriba, 100 = abajo

    @classmethod
    def from_form(cls, form) -> "WatermarkParams":
        """Construye y valida parámetros desde un form de Flask (request.form)."""
        values: dict = {}
        for name, (default, lo, hi) in _INT_PARAMS.items():
            raw = form.get(name)
            value = default if raw in (None, "") else _to_int(name, raw)
            values[name] = _clamp(name, value, lo, hi)

        values["primary_text"] = _clean_text(form.get("primary_text"))
        values["secondary_text"] = _clean_text(form.get("secondary_text"))

        values["watermark"] = (form.get("watermark") or "").strip()
        wm_x = (form.get("watermark_x") or "center").lower()
        if wm_x not in _VALID_WATERMARK_X:
            raise ValueError(
                f"watermark_x debe ser uno de {sorted(_VALID_WATERMARK_X)}"
            )
        values["watermark_x"] = wm_x

        params = cls(**values)
        if not params.has_text and not params.has_watermark:
            raise ValueError("Agregá texto o un watermark (no hay nada que componer).")
        return params

    @property
    def has_text(self) -> bool:
        return bool(self.primary_text or self.secondary_text)

    @property
    def has_watermark(self) -> bool:
        return bool(self.watermark)


def _to_int(name: str, raw):
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        raise ValueError(f"{name} debe ser numérico, se recibió: {raw!r}")


def _clamp(name: str, value, lo, hi):
    if value < lo or value > hi:
        raise ValueError(f"{name} fuera de rango [{lo}, {hi}]: {value}")
    return value


def _clean_text(raw) -> str:
    text = (raw or "").strip()
    if len(text) > MAX_TEXT_LEN:
        raise ValueError(f"El texto no puede superar {MAX_TEXT_LEN} caracteres.")
    return text
