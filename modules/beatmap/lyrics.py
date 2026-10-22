"""The lyrics of the song, word by word with their times.

In the reference edit (Levi, 2.2 M views) what feels synced is not the cuts
against the metronome: it is the lyrics, one giant word when it is sung. That
needs every word with its time:

  1. Isolate the vocals from the rest (optional, python-audio-separator with a
     UVR model). Whisper on the full mix misses words under the drums; on the
     vocals alone it finds them.
  2. faster-whisper with word timestamps (the same one the subtitles use).

Without the separator the mix is transcribed; without vocals the list is
empty (an instrumental song is not an error).
"""
from __future__ import annotations

import glob
import os

from modules.subtitles import processor as subtitles
from modules.subtitles.schema import SubtitlesParams


def separation_available() -> bool:
    try:
        import audio_separator.separator  # noqa: F401
        return True
    except ImportError:
        return False


def isolate_vocals(path: str, folder: str) -> str | None:
    """The vocals track (wav) in `folder`, or None if it cannot be separated.
    The first time it downloads audio-separator's default model."""
    if not separation_available():
        return None
    from audio_separator.separator import Separator

    os.makedirs(folder, exist_ok=True)
    sep = Separator(output_dir=folder, output_single_stem="Vocals")
    sep.load_model()
    outputs = sep.separate(path) or []
    for s in outputs:
        candidate = s if os.path.isabs(s) else os.path.join(folder, s)
        if os.path.isfile(candidate):
            return candidate
    found = glob.glob(os.path.join(folder, "*Vocals*"))
    return found[0] if found else None


def words(path: str, language: str = "auto", model: str = "large-v3-turbo",
          job_id: str | None = None) -> list[dict]:
    """[{text, start, end}] of what is sung in `path` ([] if there are no vocals)."""
    params = SubtitlesParams(language=language, model=model)
    try:
        segments = subtitles.transcribe(path, params, job_id=job_id)
    except RuntimeError:
        return []
    return [{"text": w["word"], "start": w["start"], "end": w["end"]}
            for s in segments for w in s["words"]]
