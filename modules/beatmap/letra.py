"""La letra de la canción, palabra por palabra y con su tiempo.

En el edit de referencia (Levi, 2.2 M vistas) lo que se siente sincronizado no
son los cortes contra el metrónomo: es la letra, una palabra gigante cuando se
canta. Para eso hace falta cada palabra con su tiempo:

  1. Separar la voz del resto (opcional, python-audio-separator con un modelo
     UVR). Whisper sobre la mezcla completa pierde palabras con la batería
     encima; sobre la voz sola las encuentra.
  2. faster-whisper con timestamps por palabra (el mismo que los subtítulos).

Si la separación no está instalada se transcribe la mezcla; si no hay voz, la
lista sale vacía (una canción instrumental no es un error).
"""
from __future__ import annotations

import glob
import os

from modules.subtitles import processor as subtitles
from modules.subtitles.schema import SubtitlesParams


def separacion_disponible() -> bool:
    try:
        import audio_separator.separator  # noqa: F401
        return True
    except ImportError:
        return False


def separar_voz(path: str, carpeta: str) -> str | None:
    """La pista de la voz (wav) en `carpeta`, o None si no se puede separar.
    La primera vez baja el modelo por defecto de audio-separator."""
    if not separacion_disponible():
        return None
    from audio_separator.separator import Separator

    os.makedirs(carpeta, exist_ok=True)
    sep = Separator(output_dir=carpeta, output_single_stem="Vocals")
    sep.load_model()
    salidas = sep.separate(path) or []
    for s in salidas:
        ruta = s if os.path.isabs(s) else os.path.join(carpeta, s)
        if os.path.isfile(ruta):
            return ruta
    hechos = glob.glob(os.path.join(carpeta, "*Vocals*"))
    return hechos[0] if hechos else None


def palabras(path: str, idioma: str = "auto", modelo: str = "large-v3-turbo",
             job_id: str | None = None) -> list[dict]:
    """[{texto, inicio, fin}] de lo que se canta en `path` ([] si no hay voz)."""
    params = SubtitlesParams(language=idioma, model=modelo)
    try:
        segmentos = subtitles.transcribe(path, params, job_id=job_id)
    except RuntimeError:
        return []
    return [{"texto": w["word"], "inicio": w["start"], "fin": w["end"]}
            for s in segmentos for w in s["words"]]
