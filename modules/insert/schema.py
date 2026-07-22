"""Parámetros y validación del módulo insert.

Toma un video original y un mini-clip aparte. Dos modos:

  - "full"        → inserta el mini-clip completo (corte seco) en cada marcador
                    (comportamiento clásico).
  - "progressive" → "caída progresiva": trocea el mini-clip y va metiendo el
                    pedazo siguiente en cada marcador (0–2s, 2–4s, …); al final
                    (o en un punto elegido) muestra el mini-clip completo, con
                    opción de acelerarlo para más dinamismo.
"""
import json
from dataclasses import dataclass, field

MAX_MARKERS = 50  # tope sano para no armar un filtergraph gigante
MODES = ("full", "progressive")
CLIP_AUDIO = ("clip", "mute")
# Velocidades permitidas para el reveal (atempo soporta hasta 2.0 sin encadenar).
ALLOWED_SPEEDS = (1.0, 1.2, 1.3, 1.5, 2.0)


@dataclass
class InsertParams:
    # Marcadores en segundos (puntos del original donde se inserta el mini-clip).
    markers: list[float] = field(default_factory=list)

    # --- Modo ---
    mode: str = "full"

    # --- Solo modo "progressive" ---
    # Duración de cada recorte (adelanto). None = reparto automático parejo.
    slice_durations: list[float] | None = None
    # Reveal: mostrar el mini-clip completo al final (o en `reveal_at`).
    reveal_enabled: bool = True
    reveal_at: float | None = None      # segundo del original; None = al final
    reveal_speed: float = 1.0           # 1.0 / 1.2 / 1.3 / 1.5 / 2.0
    # Audio de los recortes/reveal: "clip" (audio del mini-clip) o "mute".
    clip_audio: str = "clip"

    @property
    def is_progressive(self) -> bool:
        return self.mode == "progressive"

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

        mode = (form.get("mode") or "full").strip()
        if mode not in MODES:
            raise ValueError(f"Modo inválido: {mode!r} (usar {MODES}).")

        params = cls(markers=markers, mode=mode)
        if mode == "progressive":
            _fill_progressive(params, form, n=len(markers))
        return params


def _fill_progressive(params: "InsertParams", form, *, n: int) -> None:
    """Completa/valida los campos del modo progresivo sobre `params`."""
    # Duraciones manuales de cada recorte (opcional).
    raw_dur = form.get("slice_durations")
    if raw_dur not in (None, ""):
        try:
            values = json.loads(raw_dur) if raw_dur.strip().startswith("[") \
                else [p for p in raw_dur.split(",") if p.strip() != ""]
            durations = [float(v) for v in values]
        except (ValueError, TypeError, json.JSONDecodeError):
            raise ValueError(f"Duraciones de recorte inválidas: {raw_dur!r}")
        if len(durations) != n:
            raise ValueError(
                f"Hay {n} marcadores pero {len(durations)} duraciones de recorte; "
                "deben ser la misma cantidad."
            )
        if any(d <= 0 for d in durations):
            raise ValueError("Cada recorte debe durar más de 0 segundos.")
        params.slice_durations = durations

    params.reveal_enabled = _to_bool(form.get("reveal_enabled"), True)

    raw_at = form.get("reveal_at")
    if raw_at not in (None, ""):
        try:
            at = float(raw_at)
        except (ValueError, TypeError):
            raise ValueError(f"Posición del reveal inválida: {raw_at!r}")
        if at < 0:
            raise ValueError("La posición del reveal no puede ser negativa.")
        params.reveal_at = at

    raw_speed = form.get("reveal_speed")
    if raw_speed not in (None, ""):
        try:
            speed = round(float(raw_speed), 2)
        except (ValueError, TypeError):
            raise ValueError(f"Velocidad del reveal inválida: {raw_speed!r}")
        if speed not in ALLOWED_SPEEDS:
            raise ValueError(
                f"Velocidad {speed}× no permitida (usar {ALLOWED_SPEEDS})."
            )
        params.reveal_speed = speed

    clip_audio = (form.get("clip_audio") or "clip").strip()
    if clip_audio not in CLIP_AUDIO:
        raise ValueError(f"Audio del clip inválido: {clip_audio!r} (usar {CLIP_AUDIO}).")
    params.clip_audio = clip_audio


def _to_bool(value, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "on", "yes", "si", "sí")


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
