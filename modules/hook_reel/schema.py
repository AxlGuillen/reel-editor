"""Parámetros y validación del módulo hook_reel ("Reel Frase").

Reel de dos videos encadenados con música de fondo:

    Video 1 (avatar + audio):  trae la voz adentro → su duración = segmento 1
    Video 2 (clip de cierre):  vos elegís su duración; el clip se acelera/frena
                               con setpts para cubrirla
    Música (link):             se descarga con yt-dlp; baja mientras habla el
                               avatar y sube a tope en el corte (ducking). Se
                               recorta al total; NO se acelera.

Duraciones:
  D1 = duración del video 1                 → segmento 1
  D2 = `seg2_duration` (elegida por el user) → segmento 2 (el clip 2 se reescala)
  total = D1 + D2                           → la música se recorta a esto

No define parámetros de imagen propios: reusa VerticalConvertParams (para los
clips marcados 16:9) y SubtitlesParams (subtítulos sobre el segmento 1).
"""
from dataclasses import dataclass

from modules.vertical_convert.schema import VerticalConvertParams
from modules.subtitles.schema import SubtitlesParams
from modules.downloader.schema import (
    DownloaderParams, AUDIO_QUALITIES, DEFAULT_AUDIO_QUALITY,
)

# volumen (%) -> (default, min, max). La música baja durante el video 1 y sube
# a "full" en el corte; el full llega a 150% para poder empujar el beat.
_VOL_PARAMS = {
    "music_low_volume": (25, 0, 100),
    "music_full_volume": (100, 0, 150),
}

# Rampa (segundos) del cambio de volumen en el corte: 0 = golpe seco.
DEFAULT_RAMP = 0.4
RAMP_MIN, RAMP_MAX = 0.0, 2.0

# Duración del segmento 2 (segundos), elegida por el usuario.
DEFAULT_SEG2 = 6.0
SEG2_MIN, SEG2_MAX = 0.5, 300.0

# Desde qué segundo de la música empezar a reproducir (saltea el inicio).
MUSIC_START_MIN, MUSIC_START_MAX = 0.0, 3600.0

# Posición vertical por defecto de los subtítulos del segmento 1 (px en 1080×1920).
HOOK_SUBTITLE_POSITION_Y = 1601


@dataclass
class HookReelParams:
    vertical: VerticalConvertParams
    downloader: DownloaderParams        # música: link que se descarga (yt-dlp)
    seg2_duration: float = DEFAULT_SEG2
    music_start: float = 0.0            # desde qué segundo de la música arrancar
    music_low_volume: int = 25
    music_full_volume: int = 100
    ramp: float = DEFAULT_RAMP
    seg1_vertical: bool = False     # convertir el video 1 a 9:16
    seg2_vertical: bool = True      # convertir el video 2 a 9:16
    add_subtitles: bool = True      # subtítulos karaoke sobre el segmento 1
    subtitles: SubtitlesParams | None = None  # solo si add_subtitles

    @classmethod
    def from_form(cls, form) -> "HookReelParams":
        vertical = VerticalConvertParams.from_form(form)
        downloader = _music_downloader(form)

        values: dict = {}

        raw_seg2 = form.get("seg2_duration")
        seg2 = DEFAULT_SEG2 if raw_seg2 in (None, "") else _to_float("seg2_duration", raw_seg2)
        values["seg2_duration"] = _clamp("seg2_duration", seg2, SEG2_MIN, SEG2_MAX)

        raw_ms = form.get("music_start")
        ms = 0.0 if raw_ms in (None, "") else _to_float("music_start", raw_ms)
        values["music_start"] = _clamp("music_start", ms, MUSIC_START_MIN, MUSIC_START_MAX)

        for name, (default, lo, hi) in _VOL_PARAMS.items():
            raw = form.get(name)
            value = default if raw in (None, "") else _to_int(name, raw)
            values[name] = _clamp(name, value, lo, hi)

        raw_ramp = form.get("ramp")
        ramp = DEFAULT_RAMP if raw_ramp in (None, "") else _to_float("ramp", raw_ramp)
        values["ramp"] = _clamp("ramp", ramp, RAMP_MIN, RAMP_MAX)

        values["seg1_vertical"] = _to_bool(form.get("seg1_vertical"), False)
        values["seg2_vertical"] = _to_bool(form.get("seg2_vertical"), True)

        add_subtitles = _to_bool(form.get("add_subtitles"), True)
        values["add_subtitles"] = add_subtitles
        subtitles = None
        if add_subtitles:
            subtitles = SubtitlesParams.from_form(form)
            if not (form.get("position_y") or "").strip():
                subtitles.position_y = HOOK_SUBTITLE_POSITION_Y
        values["subtitles"] = subtitles

        return cls(vertical=vertical, downloader=downloader, **values)

    @property
    def music_low_gain(self) -> float:
        return self.music_low_volume / 100

    @property
    def music_full_gain(self) -> float:
        return self.music_full_volume / 100

    @property
    def has_subtitles(self) -> bool:
        return self.add_subtitles and self.subtitles is not None


def _music_downloader(form) -> DownloaderParams:
    """Arma el DownloaderParams de la música (siempre audio) desde el link."""
    url = (form.get("music_url") or "").strip()
    if not url:
        raise ValueError("Falta el link de la música.")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("El link de la música debe empezar con http:// o https://")
    quality = (form.get("music_quality") or "").strip() or DEFAULT_AUDIO_QUALITY
    if quality not in AUDIO_QUALITIES:
        raise ValueError(
            f"Calidad de música inválida: {quality!r}. Opciones: {sorted(AUDIO_QUALITIES)}"
        )
    return DownloaderParams(url=url, format="audio", quality=quality)


def _to_int(name: str, raw) -> int:
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        raise ValueError(f"{name} debe ser numérico, se recibió: {raw!r}")


def _to_float(name: str, raw) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} debe ser numérico, se recibió: {raw!r}")


def _clamp(name: str, value, lo, hi):
    if value < lo or value > hi:
        raise ValueError(f"{name} fuera de rango [{lo}, {hi}]: {value}")
    return value


def _to_bool(raw, default: bool) -> bool:
    if raw is None:
        return default
    return str(raw).lower() in {"1", "true", "on", "yes"}
