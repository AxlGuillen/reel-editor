"""Parámetros y validación del módulo vertical_convert."""
from dataclasses import dataclass

# nombre -> (default, min, max)
_INT_PARAMS = {
    "blur_intensity": (50, 0, 50),
    "enhance_intensity": (85, 0, 100),
    # Ajuste fino vertical del clip principal (% del alto del lienzo), se SUMA
    # a la posición elegida (top/center/bottom). Negativo = más arriba.
    "main_clip_offset": (0, -50, 50),
    # Marcador (HUD) recortado del clip fuente y superpuesto como placa.
    "hud_scale": (55, 10, 100),      # ancho de la placa (% del lienzo)
    "hud_pos_y": (10, 0, 100),       # posición vertical (% del alto libre)
}
_FLOAT_PARAMS = {
    "bg_brightness": (0.5, 0.3, 1.0),
    "main_clip_scale": (1.55, 0.7, 5.0),
    # Región del marcador en el clip FUENTE, como BORDES (% del ancho/alto del
    # original): izquierda/derecha y arriba/abajo. Cada borde se mueve solo,
    # así recortar "un poco más de la izquierda" es un slider y nada más.
    # Defaults calibrados al marcador de LoL en 1080p (esquina sup. derecha).
    "hud_left": (80.0, 0.0, 100.0),
    "hud_right": (99.5, 0.0, 100.0),
    "hud_top": (0.0, 0.0, 100.0),
    "hud_bottom": (3.0, 0.0, 100.0),
}
HUD_MIN_SIZE = 1.0   # % mínimo de ancho/alto de la región
_VALID_POSITIONS = {"center", "top", "bottom"}


@dataclass
class VerticalConvertParams:
    blur_intensity: int = 50
    bg_brightness: float = 0.5
    main_clip_scale: float = 1.55
    main_clip_position: str = "center"
    main_clip_offset: int = 0
    enhance_intensity: int = 85
    hud: bool = False
    hud_left: float = 80.0
    hud_right: float = 99.5
    hud_top: float = 0.0
    hud_bottom: float = 3.0
    hud_scale: int = 55
    hud_pos_y: int = 10

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

        values["hud"] = str(form.get("hud_enabled") or "").lower() in {
            "1", "true", "on", "yes"}
        if values["hud"]:
            if values["hud_right"] - values["hud_left"] < HUD_MIN_SIZE:
                raise ValueError(
                    "La región del marcador no tiene ancho: el borde derecho "
                    "tiene que quedar a la derecha del izquierdo.")
            if values["hud_bottom"] - values["hud_top"] < HUD_MIN_SIZE:
                raise ValueError(
                    "La región del marcador no tiene alto: el borde inferior "
                    "tiene que quedar debajo del superior.")

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
