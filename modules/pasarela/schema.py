"""The pasarela's request (JSON with local paths: nothing is uploaded) and its validation."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from modules.pasarela import insights

BANDS = {"abajo": 1240, "arriba": 0}        # where the band starts
BAND_H = 680
AUDIO_EXT = {"mp3", "wav", "m4a", "aac", "ogg", "flac"}
VIDEO_EXT = {"mp4", "mov", "mkv", "webm"}
IMAGE_EXT = {"png", "jpg", "jpeg", "webp"}


def _path(value, name: str, exts: set[str] | None = None, folder: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Falta '{name}' (una ruta local).")
    path = os.path.abspath(os.path.expanduser(value.strip().strip('"')))
    if folder:
        if not os.path.isdir(path):
            raise ValueError(f"'{name}': no existe la carpeta {path}.")
        return path
    if not os.path.isfile(path):
        raise ValueError(f"'{name}': no existe el archivo {path}.")
    if exts and os.path.splitext(path)[1].lower().lstrip(".") not in exts:
        raise ValueError(f"'{name}' tiene que ser {', '.join(sorted(exts))}: {os.path.basename(path)}.")
    return path


def _num(data: dict, name: str, default: float, lo: float, hi: float) -> float:
    raw = data.get(name, default)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' tiene que ser un número.")
    if not lo <= v <= hi:
        raise ValueError(f"'{name}' fuera de rango ({lo:g}–{hi:g}).")
    return v


@dataclass
class Highlight:
    recording: str
    before: float = 8.0
    after: float = 3.0
    events: list[str] = field(default_factory=lambda: ["kill", "assist"])

    @classmethod
    def from_json(cls, data) -> "Highlight":
        if not isinstance(data, dict) or not str(data.get("grabacion") or "").strip():
            raise ValueError("'insights' necesita 'grabacion' (el nombre o el uuid; GET /api/pasarela/recordings).")
        events = data.get("eventos", ["kill", "assist"])
        if not isinstance(events, list) or not events or any(e not in insights.EVENTS for e in events):
            raise ValueError(f"'eventos' tiene que ser una lista de {', '.join(insights.EVENTS)}.")
        return cls(str(data["grabacion"]).strip(), _num(data, "antes", 8, 0, 60),
                   _num(data, "despues", 3, 0, 60), list(dict.fromkeys(events)))

    @property
    def key(self) -> dict:
        return {"grabacion": self.recording, "antes": self.before, "despues": self.after, "eventos": self.events}


@dataclass
class ClipRequest:
    file: str | None = None         # None: the manifest's
    mode: str = "pausa"             # pausa | encima


@dataclass
class PasarelaParams:
    voice: str
    deck: str                       # creator/<deck>
    slides: str                     # creator/<deck>/video-out/laminas
    output: str
    gameplay_file: str | None = None
    highlight: Highlight | None = None
    band: str = "abajo"
    watermark: str | None = None
    subtitles: dict | None = None   # None = no subtitles; {} = the default style; or SubtitlesParams fields
    clips: dict[str, ClipRequest] = field(default_factory=dict)
    gameplay_volume: float = 0.0    # % of the gameplay's own audio (silent by default)
    language: str = "es"
    model: str = "large-v3-turbo"
    # The script's names as Whisper's initial_prompt (studio's rule). Off by default (Axl, 10/11):
    # without it Whisper gives Tom's approved transcript word for word; with it 9 of 31 slides moved
    # up to 0.2 s. On is for subtitles, where the names show.
    whisper_hint: bool = False

    @property
    def band_y(self) -> int:
        return BANDS[self.band]

    @classmethod
    def from_json(cls, data) -> "PasarelaParams":
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                raise ValueError("El cuerpo tiene que ser JSON.")
        if not isinstance(data, dict):
            raise ValueError("El cuerpo tiene que ser un objeto JSON.")
        gp = data.get("gameplay")
        if not isinstance(gp, dict) or not (gp.get("archivo") or gp.get("insights")):
            raise ValueError("'gameplay' tiene que ser {archivo} o {insights: {grabacion, antes, despues, eventos}}.")
        if gp.get("archivo") and gp.get("insights"):
            raise ValueError("'gameplay': va 'archivo' o 'insights', no los dos.")
        gameplay_file = _path(gp["archivo"], "gameplay.archivo", VIDEO_EXT) if gp.get("archivo") else None
        highlight = Highlight.from_json(gp["insights"]) if gp.get("insights") else None

        band = str(data.get("banda") or "abajo").strip().lower()
        if band not in BANDS:
            raise ValueError("'banda' tiene que ser abajo o arriba.")
        deck = _path(data.get("deck"), "deck", folder=True)
        slides = slides_folder(deck, band)
        for name in ("manifest.json", "guion-marcado.txt"):
            if not os.path.isfile(os.path.join(slides, name)):
                raise ValueError(f"El deck no tiene {name} en {slides} (exportá las láminas primero).")
        wm = data.get("watermark")
        watermark = _path(wm, "watermark", IMAGE_EXT) if wm else None

        subs = data.get("subtitulos", False)
        if subs is True:
            subs = {}
        elif subs in (False, None):
            subs = None
        elif not isinstance(subs, dict):
            raise ValueError("'subtitulos' es false, true o un objeto con el estilo.")

        out = data.get("salida")
        if not isinstance(out, str) or not out.strip().lower().endswith(".mp4"):
            raise ValueError("'salida' tiene que ser la ruta del .mp4 final.")
        out = os.path.abspath(os.path.expanduser(out.strip().strip('"')))
        if not os.path.isdir(os.path.dirname(out)):
            raise ValueError(f"No existe la carpeta de salida {os.path.dirname(out)}.")

        clips = {}
        raw_clips = data.get("clips") or {}
        if not isinstance(raw_clips, dict):
            raise ValueError("'clips' tiene que ser un objeto {id: ruta | {archivo, modo}}.")
        for cid, spec in raw_clips.items():
            if isinstance(spec, str):
                spec = {"archivo": spec}
            if not isinstance(spec, dict):
                raise ValueError(f"El clip «{cid}» tiene que ser una ruta o {{archivo, modo}}.")
            mode = str(spec.get("modo") or "pausa").lower()
            if mode not in ("pausa", "encima"):
                raise ValueError(f"El clip «{cid}»: modo tiene que ser pausa o encima.")
            file = _path(spec["archivo"], f"clips.{cid}", VIDEO_EXT) if spec.get("archivo") else None
            clips[str(cid)] = ClipRequest(file, mode)

        language = str(data.get("idioma") or "es").strip().lower()
        return cls(voice=_path(data.get("voz"), "voz", AUDIO_EXT | VIDEO_EXT), deck=deck, slides=slides,
                   output=out, gameplay_file=gameplay_file, highlight=highlight, band=band, watermark=watermark,
                   subtitles=subs, clips=clips, gameplay_volume=_num(data, "volumen_gameplay", 0, 0, 100),
                   language=language, model=str(data.get("modelo") or "large-v3-turbo"),
                   whisper_hint=bool(data.get("pista_whisper", False)))


def slides_folder(deck: str, band: str) -> str:
    """The deck's exported slides for that band: Presentations exports the band
    on top (its rail turned over) to video-out/laminas-arriba/ (laminas.mjs
    --banda=arriba). Without that variant, the bottom one is used (the report
    says so). A folder with a manifest.json is taken as the slides folder itself."""
    if os.path.isfile(os.path.join(deck, "manifest.json")):
        return deck
    if band == "arriba":
        top = os.path.join(deck, "video-out", "laminas-arriba")
        if os.path.isfile(os.path.join(top, "manifest.json")):
            return top
    return os.path.join(deck, "video-out", "laminas")


def load_manifest(slides: str) -> list[dict]:
    with open(os.path.join(slides, "manifest.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    items = data if isinstance(data, list) else data.get("laminas") or []
    if not items:
        raise ValueError("El manifest.json del deck no tiene láminas.")
    return [m for m in items if isinstance(m, dict) and m.get("id")]


def load_script(slides: str) -> str:
    with open(os.path.join(slides, "guion-marcado.txt"), encoding="utf-8-sig") as fh:
        return fh.read()
