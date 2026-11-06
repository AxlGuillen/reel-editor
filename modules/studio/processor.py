"""Studio: the slides of a Presentations deck over a base video, placed and
seen live (no FFmpeg here: the render is montage's).

Everything that decides WHEN a slide comes in is montage's resolver
(modules/montage/layers.py): the preview and the render call the same
`layers.resolve` with the same specs, so they cannot disagree. This module
only translates:

  montaje.json (the contract, docs/contratos/montaje.schema.json)
      → montage specs ({id, word, n, end_word, end_n, start, end, box})
      → resolved layers (start, end, fades, box) for the preview.

Where a slide comes in, with no manual work (the rules):
  1. The marked script of the deck (`video-out/laminas/guion-marcado.txt`,
     `⟦lamina:id⟧` right before the word): it is aligned with the transcript
     (difflib over normalized words; Whisper writes "Leblán", "16", "Liz"),
     and each mark becomes an anchor in the transcript's OWN words.
  2. Otherwise, the slide's `palabras` from the manifest (data-palabras).
  3. Otherwise it stays unplaced (the UI lists it).

The script also feeds Whisper: its proper names go as `initial_prompt` so the
names come out right. Never as `hotwords`: measured on the LeBlanc video
(6/11), hotwords made Whisper loop ("tienden tienden tienden…", "Y la de
Invictus" ×20) and the prompt alone did not.
"""
from __future__ import annotations

import difflib
import hashlib
import os
import re

from modules.montage import layers as L

MARK = re.compile(r"⟦lamina:([A-Za-z0-9_.-]+)⟧")
CROP_Y = 275            # the panel's crop in the deck (laminas.mjs: 1080:1370:0:275)
CROP_H = 1370
ANCHOR_MAX_WORDS = 4    # an anchor is a phrase of up to 4 words, unique if possible
PROMPT_MAX_NAMES = 40


# ---------------------------------------------------------------- the script

def parse_script(text: str) -> tuple[list[str], list[tuple[str, int]]]:
    """(normalized words of the script without the marks, [(slide id, index of
    the word it comes in on)]). Only the first mark of each slide counts."""
    words: list[str] = []
    marks: list[tuple[str, int]] = []
    seen = set()
    pos = 0
    for m in MARK.finditer(text or ""):
        words += L.tokens(text[pos:m.start()])
        if m.group(1) not in seen:
            seen.add(m.group(1))
            marks.append((m.group(1), len(words)))
        pos = m.end()
    words += L.tokens((text or "")[pos:])
    return words, marks


def script_names(text: str) -> list[str]:
    """The script's proper names (words with a capital that do not open a
    paragraph or a sentence), in order and without repeats."""
    clean = MARK.sub(" ", text or "")
    names: list[str] = []
    for sentence in re.split(r"(?:\n\s*\n|[.!?¿¡]\s+)", clean):
        found = re.findall(r"[\wÀ-ÿ'’]+", sentence)
        for w in found[1:]:
            if any(c.isupper() for c in w) and w not in names:
                names.append(w)
    return names[:PROMPT_MAX_NAMES]


def whisper_prompt(text: str) -> str | None:
    """Whisper's `initial_prompt` for a video narrated from this script."""
    names = script_names(text)
    return ("Vocabulario: " + ", ".join(names) + ".") if names else None


def align(script_words: list[str], transcript: list[str]) -> tuple[list[int], int]:
    """For each script word, its index in the transcript; and how many words
    matched exactly. Unmatched words are interpolated between the matched
    blocks around them (Whisper heard "Liz" where the script says "Elise")."""
    sm = difflib.SequenceMatcher(None, script_words, transcript, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    matched = sum(b.size for b in blocks)
    n = len(transcript)
    out: list[int] = []
    for g in range(len(script_words)):
        prev = nxt = None
        for b in blocks:
            if b.a <= g < b.a + b.size:
                prev = nxt = None
                out.append(b.b + g - b.a)
                break
            if b.a + b.size <= g:
                prev = b
            elif b.a > g and nxt is None:
                nxt = b
        else:
            # Between the end of `prev` and the start of `nxt`.
            ga, ta = (prev.a + prev.size - 1, prev.b + prev.size - 1) if prev else (-1, -1)
            gb, tb = (nxt.a, nxt.b) if nxt else (len(script_words), n)
            t = ta + (g - ga) * (tb - ta) / max(1, gb - ga)
            out.append(int(min(max(round(t), ta + 1, 0), max(0, n - 1))))
    return out, matched


# ------------------------------------------------------------ the transcript

def anchor_at(words: list[str], i: int) -> dict:
    """The anchor of the transcript word i: the shortest phrase from i (up to
    ANCHOR_MAX_WORDS) said only once; if none is unique, the longest one and
    which occurrence it is. `layers.find` resolves it back to word i."""
    best = None
    for k in range(1, ANCHOR_MAX_WORDS + 1):
        phrase = words[i:i + k]
        if len(phrase) < k:
            break
        starts = [j for j in range(len(words) - k + 1) if words[j:j + k] == phrase]
        best = {"palabra": " ".join(phrase), "n": starts.index(i) + 1}
        if len(starts) == 1:
            break
    return best or {"palabra": words[i], "n": 1}


def word_list(segments: list[dict] | None) -> list[dict]:
    """The transcript as montage sees it (normalized words with their times),
    each one with the anchor that pins a slide to it."""
    tr = L.transcript_words(segments or [])
    words = [w for w, _, _ in tr]
    return [{"i": i, "w": w, "start": round(a, 3), "end": round(b, 3), "anchor": anchor_at(words, i)}
            for i, (w, a, b) in enumerate(tr)]


# ------------------------------------------------------ automatic placement

def auto_layers(manifest: list[dict], segments: list[dict] | None, script: str | None) -> dict:
    """The montaje's layers from the rules (see the module docstring):
    {capas, unplaced: [ids], source: {id: "guion"|"palabras"}, alignment}."""
    tr = L.transcript_words(segments or [])
    words = [w for w, _, _ in tr]
    ids = [str(m["id"]) for m in manifest]
    capas, source = {}, {}
    alignment = None
    if script and words:
        script_words, marks = parse_script(script)
        if marks and script_words:
            mapping, matched = align(script_words, words)
            alignment = {"script_words": len(script_words), "transcript_words": len(words),
                         "matched": matched}
            for lamina, g in marks:
                if lamina in ids and g < len(mapping):
                    capas[lamina] = {"lamina": lamina, "ancla": anchor_at(words, mapping[g])}
                    source[lamina] = "guion"
    last_t = 0.0
    for m in manifest:
        lamina = str(m["id"])
        if lamina in capas:
            last_t = tr[_index_of(words, capas[lamina]["ancla"])][1]
            continue
        for phrase in m.get("palabras") or []:
            hit = _first_after(tr, words, phrase, last_t)
            if hit is not None:
                capas[lamina] = {"lamina": lamina, "ancla": anchor_at(words, hit)}
                source[lamina] = "palabras"
                last_t = tr[hit][1]
                break
    ordered = sorted(capas.values(), key=lambda c: _index_of(words, c["ancla"]))
    return {"capas": ordered, "unplaced": [i for i in ids if i not in capas],
            "source": source, "alignment": alignment}


def _index_of(words: list[str], anchor: dict) -> int:
    wanted = L.tokens(anchor["palabra"])
    seen = 0
    for j in range(len(words) - len(wanted) + 1):
        if words[j:j + len(wanted)] == wanted:
            seen += 1
            if seen == int(anchor.get("n") or 1):
                return j
    return 0


def _first_after(tr, words, phrase: str, t: float) -> int | None:
    wanted = L.tokens(phrase)
    if not wanted:
        return None
    for j in range(len(words) - len(wanted) + 1):
        if words[j:j + len(wanted)] == wanted and tr[j][1] >= t:
            return j
    return None


# ------------------------------------------------- montaje.json → montage

def layer_box(capa: dict, manifest_item: dict | None, default: dict | str | None) -> dict:
    """The box of a layer in montage's keys: the manifest's, overridden by the
    montaje's default and by the layer's own. "banda" = montage's band mode."""
    box: dict = {}
    for part in ((manifest_item or {}).get("caja"), default, capa.get("caja")):
        if part == "banda":
            box = {**box, "mode": "band"}
        elif isinstance(part, dict):
            box = {**box, **part}
            if "mode" not in part and part.keys() & {"width", "bottom", "x"}:
                box["mode"] = "box"
    return box


def to_specs(montaje: dict, manifest: list[dict]) -> list[dict]:
    """The `layers` of POST /api/montage/process (by manifest id)."""
    by_id = {str(m["id"]): m for m in manifest}
    specs = []
    for capa in montaje.get("capas") or []:
        spec: dict = {"id": capa["lamina"],
                      "box": layer_box(capa, by_id.get(capa["lamina"]), montaje.get("caja"))}
        if capa.get("ancla"):
            spec["word"] = capa["ancla"]["palabra"]
            spec["n"] = int(capa["ancla"].get("n") or 1)
        else:
            spec["start"] = float(capa["desde"])
        until = capa.get("hasta")
        if isinstance(until, dict):
            spec["end_word"] = until["palabra"]
            spec["end_n"] = int(until.get("n") or 1)
        elif until is not None:
            spec["end"] = float(until)
        specs.append(spec)
    return specs


def resolve(specs: list[dict], segments: list[dict] | None, duration: float) -> list[dict]:
    """The layers' times with montage's own resolver and defaults (the same
    call MontageParams.resolved_layers makes): what the render will do."""
    from modules.montage.schema import MontageParams, parse_box
    defaults = MontageParams(specs=[])
    clean = [{**s, "file": s["id"], "box": parse_box(s.get("box"), defaults.box).__dict__} for s in specs]
    layers = L.resolve(clean, segments, duration, defaults.box, defaults.fade_in_s, defaults.fade_out_s,
                       defaults.max_s, defaults.lead_s)
    return [{"id": c.label, "start": c.start, "end": c.end, "fade_in": c.fade_in, "fade_out": c.fade_out,
             "box": dict(c.box.__dict__), "geometry": preview_geometry(dict(c.box.__dict__))}
            for c in layers]


CROP_W = 1080


def preview_geometry(box: dict, out_w: int = 1080, out_h: int = 1920) -> dict:
    """Where montage's FFmpeg graph puts a slide, in px of the 1080x1920 frame,
    for the preview's iframe. Same numbers as processor.build_command:
      - scale=w:-2 (or -2:h): the other side rounded to the nearest EVEN px;
      - overlay on yuv420p: x and y go down to even (FFmpeg's normalize_xy),
        so a centered 706 px slide lands on x = 186, not 187."""
    def xy(v: float) -> int:
        return int(v) & ~1

    if box.get("mode") == "band":
        h = int(box["height"])
        w = _even(CROP_W * h / CROP_H)
        x = {"left": 0, "right": out_w - w}.get(box.get("x", "center"), (out_w - w) / 2)
        return {"x": xy(x), "y": xy(int(box["y"])), "w": w, "h": h, "scale": h / CROP_H, "band": True,
                "crop_y": CROP_Y, "band_y": xy(int(box["y"]))}
    w = int(box["width"])
    h = _even(CROP_H * w / CROP_W)
    x = {"left": 0, "right": out_w - w}.get(box.get("x", "center"), (out_w - w) / 2)
    return {"x": xy(x), "y": xy(out_h - h - int(box.get("bottom", 0))), "w": w, "h": h,
            "scale": w / CROP_W, "band": False, "crop_y": CROP_Y}


def _even(v: float) -> int:
    """FFmpeg's -2 in scale: av_rescale to half, rounded to nearest (half away
    from zero), times 2."""
    return int(v / 2 + 0.5) * 2


# ------------------------------------------------------------- base video

def fingerprint(path: str) -> str:
    """Identifies a video file without reading 600 MB: its size plus the first
    and last MB (a re-upload or another name gives the same one)."""
    size = os.path.getsize(path)
    h = hashlib.sha256(str(size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(1 << 20))
        if size > 2 << 20:
            fh.seek(-(1 << 20), os.SEEK_END)
            h.update(fh.read())
    return h.hexdigest()[:20]
