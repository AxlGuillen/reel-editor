"""Parámetros y validación del módulo reel_express (pipeline).

Encadena vertical_convert + downloader + sound_drop. No define parámetros
propios: agrupa los de los tres módulos reutilizando sus `from_form`, así toda
la validación vive en un solo lugar (cada módulo). El downloader se fuerza a
formato audio, que es lo único que tiene sentido en este flujo.
"""
from dataclasses import dataclass

from modules.vertical_convert.schema import VerticalConvertParams
from modules.downloader.schema import DownloaderParams, AUDIO_QUALITIES, DEFAULT_AUDIO_QUALITY
from modules.sound_drop.schema import SoundDropParams


@dataclass
class ReelExpressParams:
    vertical: VerticalConvertParams
    downloader: DownloaderParams
    sound_drop: SoundDropParams

    @classmethod
    def from_form(cls, form) -> "ReelExpressParams":
        """Construye los 3 sub-params desde el mismo form.

        Los nombres de campo de cada módulo no se solapan; los que falten caen
        en los defaults de cada módulo. Lanza ValueError (lo traduce la ruta a
        un 400) si algún sub-módulo invalida algo, p. ej. la URL.
        """
        vertical = VerticalConvertParams.from_form(form)

        # El pipeline siempre baja audio. Validamos la calidad de audio sin
        # depender de que el form mande 'format'.
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

        sound_drop = SoundDropParams.from_form(form)

        return cls(vertical=vertical, downloader=downloader, sound_drop=sound_drop)
