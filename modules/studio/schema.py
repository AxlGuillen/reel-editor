"""Studio parameters: montaje.json (validated against its JSON Schema, the
contract in docs/contratos/montaje.schema.json) and the transcript."""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache

import jsonschema

import config

SCHEMA_PATH = os.path.join(config.BASE_DIR, "docs", "contratos", "montaje.schema.json")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
MAX_SCRIPT_CHARS = 60_000
MAX_SEGMENTS = 5_000


@lru_cache(maxsize=1)
def _validator() -> jsonschema.protocols.Validator:
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def validate_montaje(data, lamina_ids: list[str] | None = None) -> dict:
    """The montaje as a dict, or a readable ValueError: first the schema, then
    what the schema cannot know (the slides exist in the deck, no repeats)."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            raise ValueError("montaje.json no es un JSON válido.")
    errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        e = errors[0]
        where = "/".join(str(p) for p in e.absolute_path) or "(raíz)"
        raise ValueError(f"montaje.json no cumple el contrato en «{where}»: {e.message}")
    seen = set()
    for capa in data["capas"]:
        if capa["lamina"] in seen:
            raise ValueError(f"La lámina «{capa['lamina']}» está dos veces en el montaje.")
        seen.add(capa["lamina"])
        if lamina_ids is not None and capa["lamina"] not in lamina_ids:
            raise ValueError(f"La lámina «{capa['lamina']}» no está en el deck "
                             f"{data['deck']['desk']}/{data['deck']['deck']}.")
    return data


def validate_manifest(data) -> list[dict]:
    """The deck's slides as the Presentations API returns them (only what the
    studio reads: id, caja, palabras, dur_anim)."""
    if not isinstance(data, list) or not data:
        raise ValueError("El deck no tiene láminas (data-lamina).")
    out = []
    for m in data:
        if not isinstance(m, dict) or not NAME.match(str(m.get("id", ""))):
            raise ValueError("Una lámina del deck no tiene un id válido.")
        caja = m.get("caja") if isinstance(m.get("caja"), dict) else None
        palabras = [str(p) for p in (m.get("palabras") or []) if str(p).strip()]
        out.append({"id": str(m["id"]), "caja": caja, "palabras": palabras,
                    "dur_anim": float(m.get("dur_anim") or 0)})
    return out


def validate_segments(data) -> list[dict]:
    """A transcript: the segments of subtitles/reel_express ([{start, end, text,
    words: [{word, start, end}]}]) or a file that holds them under "segments"."""
    if isinstance(data, dict):
        data = data.get("segments")
    if not isinstance(data, list) or not data:
        raise ValueError("La transcripción tiene que ser la lista de segmentos (con sus palabras).")
    if len(data) > MAX_SEGMENTS:
        raise ValueError(f"Máximo {MAX_SEGMENTS} segmentos.")
    out = []
    for k, seg in enumerate(data, 1):
        words = seg.get("words") if isinstance(seg, dict) else None
        if not isinstance(words, list):
            raise ValueError(f"El segmento {k} no trae sus palabras con tiempos ('words').")
        clean = []
        for w in words:
            try:
                clean.append({"word": str(w["word"]), "start": float(w["start"]), "end": float(w["end"])})
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"El segmento {k} tiene una palabra sin 'word', 'start' o 'end'.")
        if clean:
            out.append({"id": len(out), "start": clean[0]["start"], "end": clean[-1]["end"],
                        "text": str(seg.get("text") or " ".join(w["word"] for w in clean)), "words": clean})
    if not out:
        raise ValueError("La transcripción no tiene palabras.")
    return out


def valid_name(value, what: str) -> str:
    value = str(value or "").strip()
    if not NAME.match(value):
        raise ValueError(f"{what} inválido.")
    return value
