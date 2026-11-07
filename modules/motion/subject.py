"""Subject tracking without PyTorch: where a character's chest (or head) is,
frame by frame, so an overlay can follow it (the `follow` event).

  Detection   deepghs' anime detectors (YOLOv8 exported to ONNX: persons and
              heads), run with onnxruntime. They download from Hugging Face
              the first time (~42 MB each) into cache/models/deepghs. Without
              onnxruntime or the models, available() is False and nothing else
              breaks: only `follow` cannot be planned.
  Which one   the person whose chest is nearest to a hint point (the old
              hand-placed keyframe), then frame to frame the detection nearest
              to where it was (a jump over MAX_JUMP of the width is another
              character: it is not taken).
  Chest       below the head inside the person box: head center x, head
              bottom + CHEST_BELOW head heights; without a head, 35 % down the
              person box. Width ≈ CHEST_W head widths.
  Gaps        frames without a detection are interpolated (holding the last
              value past the ends); the track is smoothed with a centered
              moving average.
"""
from __future__ import annotations

import os
import urllib.request

import cv2
import numpy as np

import config
from core import ffmpeg_runner

MODELS = {"person": ("anime_person_detection", "person_detect_v1.3_s"),
          "head": ("anime_head_detection", "head_detect_v1.6_s")}
HF = "https://huggingface.co/deepghs/{repo}/resolve/main/{name}/model.onnx"
INPUT = 640
CONF = {"person": 0.35, "head": 0.3}
IOU = 0.5
MAX_JUMP = 0.15               # of the frame width, frame to frame
CHEST_BELOW = 0.4             # head heights below the head's bottom edge: the upper chest (a crest)
CHEST_W = 1.3                 # head widths
SMOOTH = 5                    # frames of the centered moving average
DETECT_W = 960                # frames are decoded this wide for detection

_sessions: dict[str, object] = {}


def models_folder() -> str:
    return os.path.join(config.BASE_DIR, "cache", "models", "deepghs")


def model_path(kind: str, download: bool = True) -> str | None:
    repo, name = MODELS[kind]
    path = os.path.join(models_folder(), f"{name}.onnx")
    if not os.path.isfile(path) and download:
        os.makedirs(models_folder(), exist_ok=True)
        tmp = path + ".part"
        try:
            urllib.request.urlretrieve(HF.format(repo=repo, name=name), tmp)
            os.replace(tmp, path)
        except OSError:
            if os.path.exists(tmp):
                os.remove(tmp)
            return None
    return path if os.path.isfile(path) else None


def available(download: bool = False) -> bool:
    """onnxruntime installed and both models on disk (or downloadable)."""
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return all(model_path(k, download) for k in MODELS)


def _session(kind: str):
    if kind not in _sessions:
        import onnxruntime as ort
        path = model_path(kind)
        if not path:
            raise RuntimeError(f"No se pudo bajar el detector de {kind} de Hugging Face (¿sin red?).")
        _sessions[kind] = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    return _sessions[kind]


def detect(img: np.ndarray, kind: str) -> list[tuple[float, float, float, float, float]]:
    """Boxes (x0, y0, x1, y1, score) of persons or heads in an RGB image."""
    h, w = img.shape[:2]
    s = INPUT / max(w, h)
    nw, nh = int(round(w * s)), int(round(h * s))
    canvas = np.full((INPUT, INPUT, 3), 114, np.uint8)
    px, py = (INPUT - nw) // 2, (INPUT - nh) // 2
    canvas[py:py + nh, px:px + nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    x = canvas.astype(np.float32).transpose(2, 0, 1)[None] / 255.0
    sess = _session(kind)
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0][0]
    if out.shape[0] == 5:                   # (5, anchors) or (anchors, 5): both exports exist
        out = out.T
    out = out[out[:, 4] >= CONF[kind]]
    if not len(out):
        return []
    boxes = [[(cx - bw / 2 - px) / s, (cy - bh / 2 - py) / s, bw / s, bh / s] for cx, cy, bw, bh, _ in out]
    keep = cv2.dnn.NMSBoxes(boxes, out[:, 4].tolist(), CONF[kind], IOU)
    res = []
    for i in np.array(keep).flatten():
        x0, y0, bw, bh = boxes[i]
        res.append((max(0.0, x0), max(0.0, y0), min(w, x0 + bw), min(h, y0 + bh), float(out[i, 4])))
    return res


def _inside(hb: tuple, person: tuple) -> bool:
    x0, y0, x1, y1, _ = person
    return (hb[0] >= x0 - 0.1 * (x1 - x0) and hb[2] <= x1 + 0.1 * (x1 - x0)
            and hb[1] >= y0 - 0.1 * (y1 - y0) and hb[3] <= y0 + 0.7 * (y1 - y0))


def chest_from_head(hb: tuple, frame_h: float) -> tuple[float, float, float]:
    """(x, y, width) of the chest under a head box."""
    hx0, hy0, hx1, hy1, _ = hb
    return (hx0 + hx1) / 2, min(frame_h, hy1 + CHEST_BELOW * (hy1 - hy0)), CHEST_W * (hx1 - hx0)


def chest(person: tuple, heads: list[tuple]) -> tuple[float, float, float]:
    """(x, y, width) of the chest of a person box, from its head when found."""
    x0, y0, x1, y1, _ = person
    inside = [hb for hb in heads if _inside(hb, person)]
    if inside:
        x, y, w = chest_from_head(max(inside, key=lambda hb: hb[4]), y1)
        return x, min(y, y1), w
    return (x0 + x1) / 2, y0 + 0.35 * (y1 - y0), 0.35 * (x1 - x0)


def _smooth(values: np.ndarray, k: int = SMOOTH) -> np.ndarray:
    if len(values) < 3:
        return values
    pad = k // 2
    padded = np.r_[np.repeat(values[:1], pad), values, np.repeat(values[-1:], pad)]
    return np.convolve(padded, np.ones(k) / k, mode="valid")


def track(path: str, start: float, end: float, hint: tuple[float, float], width_hint: float | None = None
          ) -> list[dict]:
    """The chest of the character near `hint` (frame px) over [start, end) of
    `path`, one row per frame: {f, t, x, y, w, detected}. Frame px of the
    video itself (its full size)."""
    from modules.motion import processor
    fps, duration, w, h = processor._geometry(path, DETECT_W)
    full = ffmpeg_runner.probe_resolution(path)
    k = full[0] / w
    f0, count = processor.span_frames(fps, duration, start, end)
    size = w * h * 3
    rows, prev = [], (hint[0] / k, hint[1] / k)
    jump = MAX_JUMP * w
    for i, chunk in enumerate(ffmpeg_runner.stream(processor.decode_command(path, f0, count, fps, w, h), size)):
        img = np.frombuffer(chunk, np.uint8).reshape(h, w, 3)[..., ::-1]
        persons = detect(img, "person")
        heads = detect(img, "head")
        cands = [chest(p, heads) for p in persons]
        # a close-up (the demon's face filling the frame) has a head and no person
        used = [hb for p in persons for hb in heads if _inside(hb, p)]
        cands += [chest_from_head(hb, h) for hb in heads if hb not in used]
        best = min(cands, key=lambda c: (c[0] - prev[0]) ** 2 + (c[1] - prev[1]) ** 2, default=None)
        ok = best is not None and np.hypot(best[0] - prev[0], best[1] - prev[1]) <= (jump if rows else 3 * jump)
        if ok:
            prev = (best[0], best[1])
        rows.append({"f": f0 + i, "t": round((f0 + i) / fps, 4),
                     "x": best[0] * k if ok else None, "y": best[1] * k if ok else None,
                     "w": best[2] * k if ok else None, "detected": bool(ok)})
    # the character does not survive a cut: the track ends at the first one
    # (the motion map's cuts, not flashes or impact frames)
    cuts = [e["f"] for e in processor.get(path, start, end)["events"] if e["type"] == "cut" and e["f"] > f0 + 2]
    if cuts:
        rows = [r for r in rows if r["f"] < cuts[0]]
    if not any(r["detected"] for r in rows):
        raise RuntimeError("No encontré al personaje cerca del punto pedido en ese tramo.")
    for key in ("x", "y", "w"):
        idx = np.array([i for i, r in enumerate(rows) if r[key] is not None])
        vals = np.array([rows[i][key] for i in idx], float)
        filled = np.interp(np.arange(len(rows)), idx, vals)
        if key == "w" and width_hint:
            filled = np.full(len(rows), float(np.median(filled)))      # a steady size reads better than a jittery one
        filled = _smooth(filled)
        for i, r in enumerate(rows):
            r[key] = round(float(filled[i]), 1)
    return rows
