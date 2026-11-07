"""The effects gallery: every effect of catalog.json on the same take.

One short clip (GALLERY_DUR s from `start`) rendered once per effect, with the
effect at EFFECT_AT s. The directional effects take the motion map of that
span: shake, whip and mblur use the angle of the impact nearest EFFECT_AT,
zoom / push / hold aim at the action center there. A contact sheet shows the
frame where each effect is strongest, labeled. Small (540×960, 30 fps): it is
for choosing, not for publishing.
"""
from __future__ import annotations

import json
import os
import shutil

from PIL import Image, ImageDraw, ImageFont

import config
from core import ffmpeg_runner, job_manager
from modules.editing import render, timing
from modules.editing.edl import Edl, Event, Piece, Segment

CATALOG = os.path.join(os.path.dirname(__file__), "catalog.json")
W, H, FPS = 540, 960, 30
GALLERY_DUR = 2.4
EFFECT_AT = 1.0


def catalog() -> dict:
    with open(CATALOG, encoding="utf-8") as fh:
        return json.load(fh)


def _defaults(effect: dict) -> dict:
    return {k: v["default"] for k, v in effect.get("params", {}).items()
            if isinstance(v, dict) and "default" in v and v["default"] is not None}


OVERLAY_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".webm")


def overlay_asset() -> str:
    """The first asset of assets/overlays/ (for the overlay's gallery clip), or ""."""
    folder = os.path.join(config.ASSETS_FOLDER, "overlays")
    try:
        names = sorted(n for n in os.listdir(folder) if n.lower().endswith(OVERLAY_EXTENSIONS))
    except OSError:
        return ""
    return os.path.join(folder, names[0]) if names else ""


def map_hints(source: str, start: float) -> dict:
    """Angle and action center around EFFECT_AT from the motion map (or {})."""
    try:
        from modules.motion import processor as motion
        m = motion.get(source, start, start + GALLERY_DUR)
    except Exception:  # noqa: BLE001 - the gallery works without a map
        return {}
    t = start + EFFECT_AT
    hits = [e for e in m["events"] if e["type"] in ("impact", "smear") and e.get("angle") is not None]
    near = min(hits, key=lambda e: abs(e["t"] - t), default=None)
    cx, cy = motion.focus_at(m, t)
    return {"angle": near["angle"] if near else None, "x": cx, "y": cy,
            "from": f"{near['type']} {near['t']:.2f} s" if near else None}


def variant(effect: dict, source: str, start: float, hints: dict, vf: str = "") -> Edl:
    """The EDL of one gallery clip."""
    seg = Segment(source, [Piece(start, start + GALLERY_DUR, GALLERY_DUR)], effect["id"], vf=vf)
    events: list[Event] = []
    p = _defaults(effect)
    angle = hints.get("angle")
    if effect["stage"] == "output":
        kind = effect.get("event", effect["id"])
        kw = {k: v for k, v in p.items() if k in ("intensity", "dur", "level", "attack", "decay", "every", "style",
                                                   "degrees", "ca", "blur", "seed", "fade", "opacity", "angle")}
        kw.update(effect.get("fixed", {}))
        if kind in ("zoom", "push", "hold", "speedlines") and hints:
            kw.update(x=hints["x"], y=hints["y"])
        if kind in ("shake", "whip", "speedlines") and angle is not None:
            kw["angle"] = angle
        if kind in ("panel", "follow"):
            return None                       # they need an image (and follow a track)
        if kind == "overlay":
            kw["image"] = overlay_asset()
            if not kw["image"]:
                return None                   # no asset in assets/overlays yet
        if kind == "push":
            events.append(Event(0.2, kind, **kw))
        else:
            events.append(Event(EFFECT_AT, kind, **kw))
    elif effect["id"] == "mblur":
        seg.mblur = {"angle": angle if angle is not None else p.get("angle", 0), "amount": p.get("amount", 24)}
    elif effect["id"] == "ramp":
        hit = start + EFFECT_AT
        seg.pieces = timing.hit_ramp(start, hit, start + GALLERY_DUR * 0.8, GALLERY_DUR, EFFECT_AT,
                                     "constant", "breathe")
    elif effect["id"] == "velocity":
        seg.pieces = timing.velocity(start, start + GALLERY_DUR * 0.9, GALLERY_DUR, p.get("shape", "rise"))
    elif effect["id"] == "velocity_hit":
        hit = start + EFFECT_AT
        seg.pieces = timing.velocity_hit(start, hit, start + GALLERY_DUR * 0.8, GALLERY_DUR, EFFECT_AT)
    elif effect["id"] == "split":
        # the same take, 0.6 s later, in the other half: shows the layout
        seg.split = {"source": source, "start": start + 0.6, "layout": p.get("layout", "top_bottom")}
    else:
        return None                           # dedupe: only visible with RIFE
    return Edl([seg], width=W, height=H, fps=FPS, events=events, look="neutral", fade_out=0)


def _font(size: int):
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def sheet(clips: list[tuple[str, str]], out: str) -> str:
    """One labeled frame per effect, 2 frames after it starts."""
    from modules.motion import processor as motion
    tw, th = 216, 384
    cols = 6
    rows = (len(clips) + cols - 1) // cols
    im = Image.new("RGB", (cols * (tw + 6) + 6, rows * (th + 30) + 6), (14, 11, 16))
    d = ImageDraw.Draw(im)
    font = _font(15)
    for i, (name, path) in enumerate(clips):
        k = round(EFFECT_AT * FPS) + (2 if name not in ("push", "ramp", "velocity", "velocity_hit", "split") else 20)
        raw = ffmpeg_runner.capture(motion.decode_command(path, k, 1, FPS, tw, th), timeout=120)
        frame = Image.frombytes("RGB", (tw, th), raw[:tw * th * 3], "raw", "BGR")
        x, y = 6 + (i % cols) * (tw + 6), 6 + (i // cols) * (th + 30)
        im.paste(frame, (x, y + 24))
        d.text((x, y + 2), name, fill=(200, 255, 0), font=font)
    im.save(out, quality=88)
    return out


def run(job_id: str, source: str, start: float, ids: list[str] | None, folder: str, keep_input: bool = True) -> None:
    """Job: one clip per effect, then the sheet."""
    try:
        effects = [e for e in catalog()["effects"] if not ids or e["id"] in ids]
        os.makedirs(folder, exist_ok=True)
        job_manager.update_job(job_id, status="processing", progress=2, stage="Leyendo el movimiento…")
        hints = map_hints(source, start)
        clips, skipped = [], []
        base = Edl([Segment(source, [Piece(start, start + GALLERY_DUR, GALLERY_DUR)], "sin efecto")],
                   width=W, height=H, fps=FPS, look="neutral", fade_out=0)
        todo = [("sin efecto", base)] + [(e["id"], variant(e, source, start, hints)) for e in effects]
        for k, (name, edl) in enumerate(todo):
            if edl is None:
                skipped.append(name)
                continue
            job_manager.update_job(job_id, progress=5 + int(85 * k / len(todo)), stage=f"Efecto {k}/{len(todo) - 1}: {name}…")
            out = os.path.join(folder, f"{name}.mp4")
            render.render(edl, out, None, os.path.join(folder, f"work_{name}"))
            clips.append((name, out))
        job_manager.update_job(job_id, progress=92, stage="Armando la hoja…")
        sheet_path = sheet(clips, os.path.join(folder, "galeria.jpg"))
        job_manager.update_job(job_id, status="done", progress=100, stage="Listo", output_path=sheet_path,
                               gallery={"clips": [n for n, _ in clips], "skipped": skipped, "hints": hints,
                                        "effect_at": EFFECT_AT, "dur": GALLERY_DUR, "folder": folder})
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
        shutil.rmtree(folder, ignore_errors=True)


def folder_for(job_id: str) -> str:
    return os.path.join(config.OUTPUT_FOLDER, f"gallery-{job_id}")
