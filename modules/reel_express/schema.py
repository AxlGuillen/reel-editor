"""Parámetros y validación del módulo reel_express (pipeline).

Encadena vertical_convert + audio (downloader | archivo subido) + sound_drop +
watermark opcional. No define parámetros propios: agrupa los de los módulos
reutilizando sus `from_form`, así toda la validación vive en un solo lugar.

La fuente de audio puede ser un link (se descarga con yt-dlp) o un archivo
subido (típicamente una narración). El watermark es opcional: solo se construye
si llega texto o una marca.
"""
import json
from dataclasses import dataclass, field

from modules.vertical_convert.schema import VerticalConvertParams
from modules.downloader.schema import DownloaderParams, AUDIO_QUALITIES, DEFAULT_AUDIO_QUALITY
from modules.sound_drop.schema import SoundDropParams
from modules.watermark.schema import WatermarkParams
from modules.subtitles.schema import SubtitlesParams

AUDIO_SOURCES = {"url", "file"}

# Posición vertical por defecto de los subtítulos en el pipeline (px en 1080×1920).
# Distinta a la del módulo suelto (1560): acá los queremos un poco más abajo.
REEL_SUBTITLE_POSITION_Y = 1601


@dataclass
class ReelExpressParams:
    vertical: VerticalConvertParams
    sound_drop: SoundDropParams
    audio_source: str = "url"
    downloader: DownloaderParams | None = None   # solo si audio_source == "url"
    convert_vertical: bool = True                 # off si el clip ya viene 9:16
    watermark: WatermarkParams | None = None      # solo si hay texto o marca
    add_subtitles: bool = True                    # subtítulos karaoke al final
    subtitles: SubtitlesParams | None = None       # solo si add_subtitles
    # Fondo dinámico (cutaway): mete pedazos de una "caída" entre partes del
    # video, SIN cortar la narración. Marcadores como fracciones (0–1) del base.
    dynamic_bg: bool = False
    dynamic_markers: list[float] = field(default_factory=list)
    dynamic_slice_durations: list[float] | None = None

    @classmethod
    def from_form(cls, form) -> "ReelExpressParams":
        """Construye los sub-params desde el mismo form.

        Los nombres de campo de cada módulo no se solapan; los que falten caen
        en los defaults de cada módulo. Lanza ValueError (lo traduce la ruta a
        un 400) si algún sub-módulo invalida algo, p. ej. la URL.
        """
        vertical = VerticalConvertParams.from_form(form)
        sound_drop = SoundDropParams.from_form(form)

        audio_source = (form.get("audio_source") or "url").strip().lower()
        if audio_source not in AUDIO_SOURCES:
            raise ValueError(
                f"audio_source debe ser uno de {sorted(AUDIO_SOURCES)}"
            )

        # Fuente link: validamos URL + calidad y armamos el downloader (siempre
        # audio). Fuente archivo: el upload lo valida y guarda la ruta.
        downloader = None
        if audio_source == "url":
            quality = (form.get("quality") or "").strip() or DEFAULT_AUDIO_QUALITY
            if quality not in AUDIO_QUALITIES:
                raise ValueError(
                    f"Calidad de audio inválida: {quality!r}. Opciones: {sorted(AUDIO_QUALITIES)}"
                )
            url = (form.get("url") or "").strip()
            if not url:
                raise ValueError("Falta el link del audio.")
            if not (url.startswith("http://") or url.startswith("https://")):
                raise ValueError("El link debe empezar con http:// o https://")
            downloader = DownloaderParams(url=url, format="audio", quality=quality)

        # Conversión a vertical: activa por defecto. Se apaga cuando el clip ya
        # viene en 9:16, para no pasarlo dos veces por el mismo tratamiento.
        convert_vertical = _to_bool(form.get("convert_vertical"), True)

        watermark = _optional_watermark(form)

        # Subtítulos: activos por defecto. La posición default en el pipeline es
        # 1601 (si el form no la trae). El resto de los params usa sus defaults.
        add_subtitles = _to_bool(form.get("add_subtitles"), True)
        subtitles = None
        if add_subtitles:
            subtitles = SubtitlesParams.from_form(form)
            if not (form.get("position_y") or "").strip():
                subtitles.position_y = REEL_SUBTITLE_POSITION_Y

        # Fondo dinámico: opcional. Los marcadores son fracciones (0–1) del video
        # base, así el punto no se corre si sound_drop cambia la duración.
        dynamic_bg = _to_bool(form.get("dynamic_bg"), False)
        dynamic_markers: list[float] = []
        dynamic_slice_durations = None
        if dynamic_bg:
            dynamic_markers = _parse_fractions(form.get("dynamic_markers"))
            if not dynamic_markers:
                raise ValueError("El fondo dinámico necesita al menos un marcador.")
            raw_dur = form.get("dynamic_slice_durations")
            if raw_dur not in (None, ""):
                dynamic_slice_durations = _parse_floats(raw_dur)
                if len(dynamic_slice_durations) != len(dynamic_markers):
                    raise ValueError(
                        "Las duraciones del fondo dinámico no coinciden con los marcadores."
                    )

        return cls(
            vertical=vertical, sound_drop=sound_drop, audio_source=audio_source,
            downloader=downloader, convert_vertical=convert_vertical,
            watermark=watermark,
            add_subtitles=add_subtitles, subtitles=subtitles,
            dynamic_bg=dynamic_bg, dynamic_markers=dynamic_markers,
            dynamic_slice_durations=dynamic_slice_durations,
        )

    @property
    def has_watermark(self) -> bool:
        return self.watermark is not None

    @property
    def has_subtitles(self) -> bool:
        return self.add_subtitles and self.subtitles is not None

    @property
    def has_dynamic(self) -> bool:
        return self.dynamic_bg and bool(self.dynamic_markers)


def _to_bool(raw, default: bool) -> bool:
    if raw is None:
        return default
    return str(raw).lower() in {"1", "true", "on", "yes"}


def _parse_floats(raw: str) -> list[float]:
    """Acepta JSON (`[1, 2]`) o CSV (`1, 2`) y devuelve floats."""
    raw = (raw or "").strip()
    try:
        values = json.loads(raw) if raw.startswith("[") else \
            [p for p in raw.split(",") if p.strip() != ""]
        return [float(v) for v in values]
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError(f"Valores inválidos: {raw!r}")


def _parse_fractions(raw) -> list[float]:
    """Marcadores como fracciones 0–1 del video base (ordenados, validados)."""
    if raw in (None, ""):
        return []
    fracs = _parse_floats(raw)
    if any(f < 0 or f > 1 for f in fracs):
        raise ValueError("Los marcadores del fondo dinámico deben ir entre 0 y 1.")
    if len(fracs) > 50:
        raise ValueError("Demasiados marcadores de fondo dinámico (máximo 50).")
    fracs.sort()
    return fracs


def _optional_watermark(form) -> WatermarkParams | None:
    """Construye WatermarkParams solo si hay contenido; si no, None.

    El `from_form` de watermark lanza ValueError cuando no hay nada que componer
    (correcto en su módulo, donde es obligatorio). En el pipeline el watermark
    es opcional, así que solo intentamos construirlo si llega texto o una marca;
    cualquier otro error de validación (p. ej. tamaño fuera de rango) sí propaga.
    """
    has_content = (
        (form.get("primary_text") or "").strip()
        or (form.get("secondary_text") or "").strip()
        or (form.get("watermark") or "").strip()
    )
    if not has_content:
        return None
    return WatermarkParams.from_form(form)
