"""Parámetros y validación del módulo insert.

Toma un video original y uno o varios mini-clips. Cada mini-clip trae SU PROPIA
lista de marcadores, así se pueden repartir distintas "caídas" a lo largo del
video (clip A en 0:05 y 0:30, clip B en 1:10). Dos modos:

  - "full"        → inserta cada mini-clip completo (corte seco) en sus
                    marcadores. Soporta varios clips.
  - "progressive" → "caída progresiva": trocea el mini-clip y va metiendo el
                    pedazo siguiente en cada marcador (0–2s, 2–4s, …); al final
                    (o en un punto elegido) muestra el mini-clip completo, con
                    opción de acelerarlo para más dinamismo. Usa UN solo clip.
"""
import json
from dataclasses import dataclass, field

from modules.vertical_convert.schema import VerticalConvertParams

MAX_MARKERS = 50  # tope sano para no armar un filtergraph gigante
MAX_CLIPS = 10    # tope de mini-clips distintos
MODES = ("full", "progressive")
CLIP_AUDIO = ("clip", "mute")
# Velocidades permitidas para el reveal (atempo soporta hasta 2.0 sin encadenar).
ALLOWED_SPEEDS = (1.0, 1.2, 1.3, 1.5, 2.0)


@dataclass
class ClipSpec:
    """Un mini-clip con sus propios marcadores sobre el timeline del original."""
    markers: list[float] = field(default_factory=list)
    # Convertirlo a 9:16 antes de insertarlo (para que matchee la resolución
    # del original sin pasarlo a mano por el módulo Vertical).
    vertical: bool = False


@dataclass
class InsertParams:
    # Un ClipSpec por mini-clip subido, cada uno con sus propios marcadores.
    clips: list[ClipSpec] = field(default_factory=list)

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

    # Ajustes de la conversión a vertical (compartidos por los clips que la usen).
    vertical: VerticalConvertParams | None = None

    @property
    def is_progressive(self) -> bool:
        return self.mode == "progressive"

    @property
    def markers(self) -> list[float]:
        """Todos los marcadores juntos, ordenados. Lo usa el modo progresivo,
        que trabaja con un solo clip."""
        return sorted(m for c in self.clips for m in c.markers)

    @property
    def total_markers(self) -> int:
        return sum(len(c.markers) for c in self.clips)

    @property
    def needs_vertical(self) -> bool:
        return any(c.vertical for c in self.clips)

    @classmethod
    def from_form(cls, form) -> "InsertParams":
        """Construye y valida parámetros desde un form de Flask (request.form).

        Formato nuevo: `clips` como JSON, un objeto por mini-clip subido:
            [{"markers": [1.5, 12], "vertical": true}, {"markers": [30]}]
        Formato viejo (un solo clip): `markers` como JSON o CSV + `clip_vertical`.
        """
        mode = (form.get("mode") or "full").strip()
        if mode not in MODES:
            raise ValueError(f"Modo inválido: {mode!r} (usar {MODES}).")

        clips = _parse_clips(form)
        if not clips:
            raise ValueError("Hay que subir al menos un mini-clip con marcadores.")
        if len(clips) > MAX_CLIPS:
            raise ValueError(f"Demasiados mini-clips (máximo {MAX_CLIPS}).")

        total = sum(len(c.markers) for c in clips)
        if not total:
            raise ValueError("Hay que indicar al menos un marcador.")
        if total > MAX_MARKERS:
            raise ValueError(f"Demasiados marcadores (máximo {MAX_MARKERS}).")

        if mode == "progressive" and len(clips) > 1:
            raise ValueError(
                "La caída progresiva trabaja con un solo mini-clip. "
                "Quitá los demás o usá el modo 'Clip completo'."
            )

        params = cls(clips=clips, mode=mode)
        # Ajustes del fondo vertical: se leen si algún clip pide conversión.
        if params.needs_vertical:
            params.vertical = VerticalConvertParams.from_form(form)

        if mode == "progressive":
            _fill_progressive(params, form, n=total)
        return params


def _parse_clips(form) -> list[ClipSpec]:
    """Lee los clips del form (formato nuevo `clips`, o el viejo `markers`)."""
    raw = form.get("clips")
    if raw not in (None, ""):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError, json.JSONDecodeError):
            raise ValueError(f"Lista de clips inválida: {raw!r}")
        if not isinstance(data, list):
            raise ValueError("`clips` debe ser una lista.")
        return [_clip_from_dict(item, i) for i, item in enumerate(data)]

    # Compat: un solo clip con `markers` sueltos.
    raw_markers = form.get("markers")
    if raw_markers in (None, ""):
        return []
    return [ClipSpec(markers=_clean_markers(_parse_markers(raw_markers), 0),
                     vertical=_to_bool(form.get("clip_vertical"), False))]


def _clip_from_dict(item, idx: int) -> ClipSpec:
    if not isinstance(item, dict):
        raise ValueError(f"El clip {idx + 1} no tiene el formato esperado.")
    raw = item.get("markers") or []
    if not isinstance(raw, list):
        raise ValueError(f"Los marcadores del clip {idx + 1} deben ser una lista.")
    try:
        markers = [float(v) for v in raw]
    except (ValueError, TypeError):
        raise ValueError(f"Marcadores inválidos en el clip {idx + 1}.")
    return ClipSpec(markers=_clean_markers(markers, idx),
                    vertical=bool(item.get("vertical")))


def _clean_markers(markers: list[float], idx: int) -> list[float]:
    """Valida y ordena los marcadores de un clip."""
    if any(m < 0 for m in markers):
        raise ValueError(
            f"El clip {idx + 1} tiene marcadores negativos."
        )
    return sorted(markers)


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
