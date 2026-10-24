"""Named sets of parameters (assets/competitivo/presets.json).

Every request of the week came with its own list of parameters; a preset
keeps the ones that worked under a name: preset=edit_estandar fills them in
and whatever the request sends explicitly wins.
"""
from __future__ import annotations

import json
import os

import config

PRESETS_FILE = os.path.join(config.ASSETS_FOLDER, "competitivo", "presets.json")


def load() -> dict[str, dict]:
    if not os.path.isfile(PRESETS_FILE):
        return {}
    with open(PRESETS_FILE, encoding="utf-8") as fh:
        return json.load(fh).get("presets") or {}


def listing() -> list[dict]:
    return [{"name": k, "description": v.get("description", ""), "endpoints": v.get("endpoints", []),
             "params": v.get("params", {})} for k, v in load().items()]


def apply(form, endpoint: str) -> tuple[dict, str]:
    """(the form with the preset's parameters under it, the preset's name or "").
    ValueError if the preset does not exist or is not for this endpoint."""
    data = {k: form.get(k) for k in form.keys()} if hasattr(form, "keys") else dict(form)
    name = str(data.pop("preset", "") or "").strip()
    if not name:
        return data, ""
    presets = load()
    if name not in presets:
        raise ValueError(f"No existe el preset «{name}». Hay: {', '.join(sorted(presets))}.")
    p = presets[name]
    if p.get("endpoints") and endpoint not in p["endpoints"]:
        raise ValueError(f"El preset «{name}» es para {', '.join(p['endpoints'])}, no para {endpoint}.")
    return {**p.get("params", {}), **{k: v for k, v in data.items() if v not in (None, "")}}, name
