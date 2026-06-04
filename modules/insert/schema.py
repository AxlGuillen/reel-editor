"""Parámetros y validación del módulo insert.

Toma un video original y un mini-clip aparte, e inserta el mini-clip completo
(corte seco) en cada marcador indicado sobre el timeline del original.
"""
import json
from dataclasses import dataclass, field

MAX_MARKERS = 50  # tope sano para no armar un filtergraph gigante


@dataclass
class InsertParams:
    # Marcadores en segundos (puntos del original donde se inserta el mini-clip).
    markers: list[float] = field(default_factory=list)

    @classmethod
    def from_form(cls, form) -> "InsertParams":
        """Construye y valida parámetros desde un form de Flask (request.form).

        Espera `markers` como JSON (lista de números) o como string separada por
        comas. Lanza ValueError con un mensaje legible si algo es inválido.
        """
        raw = form.get("markers")
        if raw in (None, ""):
            raise ValueError("Hay que indicar al menos un marcador.")

        markers = _parse_markers(raw)
        if not markers:
            raise ValueError("Hay que indicar al menos un marcador.")
        if len(markers) > MAX_MARKERS:
            raise ValueError(f"Demasiados marcadores (máximo {MAX_MARKERS}).")
        if any(m < 0 for m in markers):
            raise ValueError("Los marcadores no pueden ser negativos.")

        # Orden ascendente; los duplicados se permiten (dos inserciones seguidas).
        markers.sort()
        return cls(markers=markers)


def _parse_markers(raw: str) -> list[float]:
    """Acepta JSON (`[1.5, 12]`) o CSV (`1.5, 12`) y devuelve floats."""
    raw = raw.strip()
    try:
        if raw.startswith("["):
            values = json.loads(raw)
        else:
            values = [p for p in raw.split(",") if p.strip() != ""]
        return [float(v) for v in values]
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError(f"Marcadores inválidos: {raw!r}")
