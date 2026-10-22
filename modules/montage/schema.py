"""Montage parameters: which layers, where they are read from, where they go and when."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import config
from modules.beatmap.schema import to_bool
from modules.montage import layers as L
from modules.subtitles.schema import SubtitlesParams

VALID_X = ("center", "left", "right")
MODES = ("box", "band")
MAX_LAYERS = 40


def _num(form, name, default, lo, hi, cast=float):
    raw = form.get(name)
    if raw in (None, ""):
        return default
    try:
        v = cast(raw)
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' tiene que ser un número.")
    if not lo <= v <= hi:
        raise ValueError(f"'{name}' fuera de rango ({lo}–{hi}).")
    return v


def allowed_folder(path: str) -> str:
    """Real path of a folder inside LAYERS_FOLDER (ValueError if it escapes)."""
    base = os.path.realpath(config.LAYERS_FOLDER)
    real = os.path.realpath(os.path.expanduser(path))
    try:
        inside = os.path.commonpath([base, real]) == base
    except ValueError:
        inside = False
    if not inside:
        raise ValueError(f"La carpeta de capas tiene que estar dentro de {config.LAYERS_FOLDER}.")
    if not os.path.isdir(real):
        raise ValueError(f"No existe la carpeta de capas {path}.")
    return real


def _box(data: dict, default: L.Box | None = None) -> L.Box:
    base = dict((default or L.Box()).__dict__)
    for k, v in (data or {}).items():
        if k not in base:
            raise ValueError(f"La caja no tiene el campo {k!r} (usá width, x, bottom, mode, y, height).")
        base[k] = v
    try:
        box = L.Box(width=int(base["width"]), x=str(base["x"]), bottom=int(base["bottom"]),
                    mode=str(base["mode"]), y=int(base["y"]), height=int(base["height"]))
    except (TypeError, ValueError):
        raise ValueError("Caja inválida: width, bottom, y y height son números.")
    if box.x not in VALID_X:
        raise ValueError(f"La caja: x tiene que ser {', '.join(VALID_X)}.")
    if box.mode not in MODES:
        raise ValueError(f"La caja: mode tiene que ser {', '.join(MODES)}.")
    if not 40 <= box.width <= config.OUTPUT_WIDTH or not 0 <= box.bottom <= config.OUTPUT_HEIGHT:
        raise ValueError("La caja se sale del cuadro de 1080x1920.")
    if box.mode == "band" and not (0 <= box.y and box.height >= 40 and box.y + box.height <= config.OUTPUT_HEIGHT):
        raise ValueError("La franja se sale del cuadro de 1080x1920.")
    return box


@dataclass
class MontageParams:
    specs: list[dict]
    folder: str | None = None                        # layers folder (real path)
    uploaded: dict[str, str] = field(default_factory=dict)  # name → uploaded file
    box: L.Box = field(default_factory=L.Box)
    fade_in_s: float = 0.25
    fade_out_s: float = 0.2
    max_s: float = L.MAX_S
    lead_s: float = L.LEAD_S
    subtitles: bool = False
    sub_params: SubtitlesParams = field(default_factory=SubtitlesParams)

    @classmethod
    def from_form(cls, form, uploaded: dict[str, str] | None = None) -> "MontageParams":
        raw = form.get("layers")
        try:
            specs = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            raise ValueError("'layers' tiene que ser una lista JSON de capas.")
        if not isinstance(specs, list) or not specs:
            raise ValueError("Faltan las capas: [{file | id, start | word, …}].")
        if len(specs) > MAX_LAYERS:
            raise ValueError(f"Máximo {MAX_LAYERS} capas.")
        folder = form.get("layers_folder")
        folder = allowed_folder(str(folder)) if folder else None
        default = _box(form.get("box") if isinstance(form.get("box"), dict) else
                       json.loads(form.get("box") or "{}"))
        manifest = {}
        if folder and os.path.isfile(os.path.join(folder, "manifest.json")):
            with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as fh:
                data = json.load(fh)
            # The manifest comes from presentations (tools/laminas.mjs): its keys are Spanish.
            items = data if isinstance(data, list) else data.get("laminas") or data.get("flyers") or []
            manifest = {str(m.get("id")): m for m in items if isinstance(m, dict)}
        clean = []
        for k, s in enumerate(specs, 1):
            if not isinstance(s, dict):
                raise ValueError(f"La capa {k} tiene que ser un objeto.")
            file = s.get("file")
            if not file and s.get("id") is not None:
                m = manifest.get(str(s["id"]))
                if not m:
                    raise ValueError(f"La capa {k}: no está el id {s['id']!r} en el manifest.json de la carpeta.")
                file = m.get("archivo")
            if not file:
                raise ValueError(f"La capa {k} no dice qué archivo usar ('file' o 'id').")
            clean.append({**s, "file": str(file), "box": _box(s.get("box"), default).__dict__})
        sub = SubtitlesParams.from_form(form)
        return cls(specs=clean, folder=folder, uploaded=uploaded or {}, box=default,
                   fade_in_s=_num(form, "fade_in_s", 0.25, 0, 2), fade_out_s=_num(form, "fade_out_s", 0.2, 0, 2),
                   max_s=_num(form, "max_s", L.MAX_S, 1, 120), lead_s=_num(form, "lead_s", L.LEAD_S, 0, 2),
                   subtitles=to_bool(form, "subtitles"), sub_params=sub)

    @property
    def needs_transcript(self) -> bool:
        return any(s.get("word") or s.get("end_word") for s in self.specs)

    def path(self, file: str) -> str:
        """A layer's file: an uploaded one with that name or one from the folder."""
        name = os.path.basename(file)
        if name in self.uploaded:
            return self.uploaded[name]
        if not self.folder:
            raise ValueError(f"No subiste «{name}» ni diste 'layers_folder'.")
        real = os.path.realpath(os.path.join(self.folder, file))
        try:
            inside = os.path.commonpath([self.folder, real]) == self.folder
        except ValueError:          # another drive on Windows
            inside = False
        if not inside:
            raise ValueError(f"«{file}» está fuera de la carpeta de capas.")
        if not os.path.isfile(real) or not config.allowed_layer_file(real):
            raise ValueError(f"No existe la capa «{file}» (o no es mov, webm, mp4, mkv ni png).")
        return real

    def resolved_layers(self, segments: list[dict] | None, video_dur: float) -> list[L.Layer]:
        specs = [{**s, "file": self.path(s["file"])} for s in self.specs]
        return L.resolve(specs, segments, video_dur, self.box, self.fade_in_s, self.fade_out_s,
                         self.max_s, self.lead_s)
