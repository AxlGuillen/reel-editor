"""The motion map of a video span: what happens in the image, frame by frame.

Pure analysis (like beatmap): it describes and never decides. One pass over
small BGR frames (480 px wide by default) computes, per frame:

1. Cadence: anime is drawn "on twos" or "on threes". A frame that repeats the
   previous one (mean |Δluma| < HELD_DIFF) is `held`; optical flow (DIS) runs
   only between DISTINCT drawings and is divided by how many frames the change
   took. Without that the curve is a comb of peaks.
2. Camera: a homography (RANSAC) fitted on a sparse grid of the flow gives the
   pan (cam_dx, cam_dy) and the zoom.
3. Character: the flow left after removing the camera. Force = its 90th
   percentile (px per frame, in 1920-px-wide units), angle (0° right, 90° up)
   and a directogram of 8 directions (Davis & Agrawala, Visual Rhythm and Beat).
4. Brightness and color: luma, saturation, inverted frame, black and white.
5. Action center: the centroid of the LARGEST blob of character motion,
   smoothed per shot (not the top 5 % of pixels, which jumps around).
6. Cuts: PySceneDetect's AdaptiveDetector (anime_edit.shots) on the same
   frames, minus the "cuts" that are a flash or an impact frame burst.

Then the events: impact, smear, pan, calm, flash, cut (see `events`).
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Iterable

import cv2
import numpy as np

from modules.anime_edit.shots import FrameCuts

VERSION = 1
REF_W = 1920                 # forces are reported in px of a 1920-wide frame
HELD_DIFF = 1.0              # mean |Δluma| (0–255) below which a frame repeats the drawing
HELD_REL = 0.35              # …or below 35 % of the median Δ of the last HELD_WINDOW frames
HELD_WINDOW = 8
HELD_FLOW = 1.0              # px/frame: a "new drawing" whose flow moves less than this is a repeat
FLASH_JUMP = 25              # luma rise (0–255) that counts as a flash
GRID_STEP = 20               # px between the points the homography is fitted on (~330 at 480 px)
RANSAC_ITERS = 300           # the default 2000 took 70 % of the analysis (8 ms a frame)
SUB = 4                      # residual statistics on every 4th pixel
IMPACT_BURST = 3             # a "shot" of ≤ 3 frames between two cuts is an impact frame burst
THUMB = (32, 18)             # tiny gray copy of each frame: does the picture come back after a burst?
BACK_RATIO = 0.35            # "back" = closer to the frame before the burst than 35 % of the jump
PAN_MIN = 4.0                # px/frame (1920 units) of camera motion for a pan
PAN_FRAMES = 6
PAN_SPREAD = 35.0            # degrees the pan direction may wander
CALM_MAX = 2.5               # px/frame of character force for calm
CALM_S = 2.0                 # calm needs this long (or one bar, if the caller passes it)
PEAK_MIN = 10.0              # px/frame: below this no peak is an impact
BRAKE = 0.6                  # one of the BRAKE_FRAMES frames after the peak slows to ≤ 60 %
BRAKE_FRAMES = 4
CUT_ERR = 0.5                # a "cut" the flow explains (error ≤ 50 % of the change) is fast motion
LOCAL_FRAMES = 12            # "normal motion right there" = median of ±12 frames (~0.5 s)
PROMINENCE = 6.0             # an impact peaks at ≥ 6× that median…
PROMINENCE_MARKED = 2.0      # …or ≥ 2× when a flash or impact frame marks it
PROMINENCE_SHARP = 2.5       # …or ≥ 2.5× when it brakes hard (a flurry, where everything moves)
SHARP_BRAKE = 0.3            # hard brake: the next drawings drop to ≤ 30 %
FLASH_HIT_PROMINENCE = 1.5   # a passing flash on ≥ 1.5× the local motion is a hit too
PROMINENCE_FULL = 20.0       # prominence that scores the full 0.55
MIN_GAP = 5                  # frames: one impact per moment, the strongest
SHAKE_RATIO = 3.0            # camera jolt after the hit: 3× the local camera motion
SMEAR_ERR = 0.75             # flow explains ≤ 25 % of the change: a smear drawing
SMEAR_PCT = 90
DIRS = ["derecha", "arriba a la derecha", "arriba", "arriba a la izquierda",
        "izquierda", "abajo a la izquierda", "abajo", "abajo a la derecha"]


def direction(angle: float | None) -> str | None:
    """Spanish name of the 8-sector direction of an angle (0° right, 90° up)."""
    if angle is None:
        return None
    return DIRS[int(((angle % 360) + 22.5) // 45) % 8]


class _Flow:
    """Optical flow between two drawings, split into camera and character."""

    def __init__(self, w: int, h: int):
        self.dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_FAST)
        self.w, self.h = w, h
        ys, xs = np.mgrid[0:h:GRID_STEP, 0:w:GRID_STEP]
        self.grid = (ys.ravel(), xs.ravel())
        self.grid_pts = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float32)
        sy, sx = np.mgrid[0:h:SUB, 0:w:SUB]
        self.sub_shape = sy.shape
        self.sub = (sy.ravel(), sx.ravel())
        self.sub_h = np.stack([sx.ravel(), sy.ravel(), np.ones(sx.size)], 0).astype(np.float64)
        mx, my = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
        self.map_x, self.map_y = mx, my
        self.center = np.array([w / 2, h / 2, 1.0])
        self.kernel = np.ones((3, 3), np.uint8)

    def measure(self, prev: np.ndarray, cur: np.ndarray, dur: int) -> dict:
        flow = self.dis.calc(prev, cur, None)
        # camera: homography on a sparse grid (RANSAC ignores the character)
        g = flow[self.grid]
        H, _ = cv2.findHomography(self.grid_pts, self.grid_pts + g, cv2.RANSAC, 2.0,
                                  maxIters=RANSAC_ITERS, confidence=0.99)
        if H is None or not np.isfinite(H).all():
            H = np.eye(3)
        c = H @ self.center
        c = c[:2] / c[2]
        scale = REF_W / self.w / dur
        cam_dx, cam_dy = (c[0] - self.w / 2) * scale, (c[1] - self.h / 2) * scale
        zoom = math.sqrt(abs(np.linalg.det(H[:2, :2]))) ** (1 / dur)
        # character: what the camera does not explain
        p = H @ self.sub_h
        pred = (p[:2] / p[2]) - self.sub_h[:2]
        f = flow[self.sub]
        r = f - pred.T
        mag = np.hypot(r[:, 0], r[:, 1])
        force = float(np.percentile(mag, 90)) * scale
        peak = float(np.percentile(mag, 97)) * scale     # a small subject still counts as motion
        strong = mag >= max(np.percentile(mag, 75), 0.5)
        if strong.sum() >= 4:
            wv = mag[strong]
            vx, vy = float((r[strong, 0] * wv).sum() / wv.sum()), float((r[strong, 1] * wv).sum() / wv.sum())
            angle = math.degrees(math.atan2(-vy, vx)) % 360
        else:
            angle = None
        sector = ((np.degrees(np.arctan2(-r[:, 1], r[:, 0])) % 360 + 22.5) // 45).astype(int) % 8
        dirgram = np.bincount(sector, weights=mag, minlength=8)
        dirgram = dirgram / dirgram.sum() if dirgram.sum() > 0 else dirgram
        # action center: the blob of character motion with the most motion in it
        m2 = mag.reshape(self.sub_shape)
        thr = max(float(np.percentile(m2, 85)), 0.5 * float(np.percentile(m2, 99)), 1.0)
        mask = cv2.morphologyEx((m2 >= thr).astype(np.uint8), cv2.MORPH_CLOSE, self.kernel)
        center = None
        n, labels = cv2.connectedComponents(mask, connectivity=8)
        if n > 1:
            weight = np.bincount(labels.ravel(), weights=m2.ravel(), minlength=n)
            weight[0] = 0
            best = int(weight.argmax())
            if weight[best] > 0:
                ys, xs = np.nonzero(labels == best)
                wv = m2[ys, xs]
                center = (float((xs * wv).sum() / wv.sum() + 0.5) / self.sub_shape[1],
                          float((ys * wv).sum() / wv.sum() + 0.5) / self.sub_shape[0])
        # how much of the change the flow explains (low = a smear drawing)
        warped = cv2.remap(cur, self.map_x + flow[..., 0], self.map_y + flow[..., 1],
                           cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        base = float(cv2.absdiff(cur, prev).mean())
        err = float(cv2.absdiff(warped, prev).mean()) / max(base, 1.0)
        return {"force": force, "peak": peak, "angle": angle, "dirgram": dirgram, "cam_dx": cam_dx, "cam_dy": cam_dy,
                "cam_zoom": zoom, "center": center, "err": err}


def analyze(frames: Iterable[np.ndarray], fps: float, f0: int, width: int, height: int,
            calm_s: float = CALM_S, on_progress=None, total: int | None = None) -> dict:
    """The motion map of `frames` (BGR uint8, width × height), whose first
    frame is frame `f0` of the file. Returns the map without `source`/span
    metadata (the processor adds it). `on_progress(fraction)` every ~2 %."""
    t_start = time.time()
    flow = _Flow(width, height)
    detector = FrameCuts(fps)
    cols: dict[str, list] = {k: [] for k in ("drawing", "held", "cam_dx", "cam_dy", "cam_zoom", "force",
                                             "angle", "dirgram", "luma", "sat", "inverted", "bw", "err")}
    centers: list[tuple[float, float] | None] = []
    prev = draw = None
    draw_k = 0
    recent: deque[float] = deque(maxlen=HELD_WINDOW)
    thumbs: list[np.ndarray] = []
    n_draw = 0
    step = max(1, (total or 0) // 50)
    k = -1
    for k, frame in enumerate(frames):
        detector.feed(k, frame)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        thumbs.append(cv2.resize(gray, THUMB, interpolation=cv2.INTER_AREA).astype(np.float32))
        small = frame[::SUB, ::SUB]
        sat = float(cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[..., 1].mean())
        luma = float(gray.mean())
        cols["luma"].append(round(luma, 1))
        cols["sat"].append(round(sat, 1))
        cols["bw"].append(sat < 12)
        if prev is None:
            held, inverted = False, False
        else:
            diff = float(cv2.absdiff(gray, prev).mean())
            # encode noise lifts a repeated drawing above HELD_DIFF when the
            # rest of the shot moves a lot: the bar rises with the local motion
            limit = max(HELD_DIFF, HELD_REL * float(np.median(recent))) if recent else HELD_DIFF
            recent.append(diff)
            held = diff < limit
            inverted = diff > 30 and float(cv2.absdiff(gray, 255 - prev).mean()) < 0.5 * diff
        cols["held"].append(held)
        cols["inverted"].append(inverted)
        for key, value in (("cam_dx", 0.0), ("cam_dy", 0.0), ("cam_zoom", 1.0), ("force", 0.0),
                           ("angle", None), ("dirgram", [0.0] * 8), ("err", 0.0)):
            cols[key].append(value)
        centers.append(None)
        if not held and draw is not None:
            dur = k - draw_k
            m = flow.measure(draw, gray, dur)
            if m["peak"] < HELD_FLOW and math.hypot(m["cam_dx"], m["cam_dy"]) < HELD_FLOW:
                held = True              # "new" only by noise: the flow barely moved
                cols["held"][k] = True
        if not held:
            if draw is not None:
                # the change happened over the frames since the last drawing
                for j in range(draw_k + 1, k + 1):
                    cols["force"][j] = round(m["force"], 2)
                    cols["cam_dx"][j] = round(m["cam_dx"], 2)
                    cols["cam_dy"][j] = round(m["cam_dy"], 2)
                    cols["cam_zoom"][j] = round(m["cam_zoom"], 4)
                cols["angle"][k] = None if m["angle"] is None else round(m["angle"], 1)
                cols["dirgram"][k] = [round(float(x), 3) for x in m["dirgram"]]
                cols["err"][k] = round(m["err"], 3)
                centers[k] = m["center"]
            draw, draw_k = gray, k
            n_draw += 1
        cols["drawing"].append(n_draw - 1)
        prev = gray
        if on_progress and k % step == 0 and total:
            on_progress(min(0.99, k / total))
    n = k + 1
    raw_cuts = detector.finish()
    analysis_s = time.time() - t_start
    frames_out = _finish(cols, centers, raw_cuts, n, thumbs)
    shots = frames_out.pop("_shots")
    ev = events(frames_out, shots, fps, f0, calm_s)
    return {"version": VERSION, "fps": fps, "f0": f0, "frames_count": n, "width": width, "height": height,
            "elapsed_s": round(analysis_s, 2), "fps_analysis": round(n / max(analysis_s, 1e-6), 1),
            "held_ratio": round(sum(cols["held"]) / max(1, n), 3),
            "frames": frames_out, "shots": shots, "events": ev,
            "_raw_cuts": raw_cuts}


def _transient(luma: list[float], k: int, look: int = IMPACT_BURST + 1) -> bool:
    """Does the brightness jump at k come back down within `look` frames?"""
    base, jump = luma[k - 1], luma[k] - luma[k - 1]
    return any(luma[j] < base + 0.5 * jump for j in range(k + 1, min(len(luma), k + 1 + look)))


def _comes_back(thumbs: list[np.ndarray], c: int) -> int | None:
    """If 1–IMPACT_BURST frames after the change at c the picture is the one
    from before it again, the frame where it comes back (an impact frame
    burst, not a cut); else None. PySceneDetect's min_scene_len hides the
    second "cut" of a 2-frame burst."""
    if c < 1:
        return None
    before = thumbs[c - 1]
    jump = float(np.abs(thumbs[c] - before).mean())
    if jump < 1.0:
        return None
    for j in range(c + 1, min(len(thumbs), c + IMPACT_BURST + 1)):
        if float(np.abs(thumbs[j] - before).mean()) < BACK_RATIO * jump:
            return j
    return None


def _finish(cols: dict, centers: list, raw_cuts: list[int] | None, n: int,
            thumbs: list[np.ndarray] | None = None) -> dict:
    """Real cuts (without flashes and impact frame bursts), smoothed centers
    and no motion across a cut."""
    raw = sorted(raw_cuts or [])
    luma, inverted = cols["luma"], cols["inverted"]
    real, bursts = [], []
    i = 0
    while i < len(raw):
        c = raw[i]
        nxt = raw[i + 1] if i + 1 < len(raw) else None
        # a flash goes back down; a cut to a brighter shot stays bright (the scream, 31332)
        flash = c > 0 and luma[c] - luma[c - 1] > FLASH_JUMP and _transient(luma, c)
        if nxt is not None and nxt - c <= IMPACT_BURST:
            bursts.append((c, nxt))          # 1–3 frames of something else: an impact frame burst
            i += 1
            continue
        back = _comes_back(thumbs, c) if thumbs else None
        if back is not None:                 # same, when PySceneDetect skipped the second cut
            bursts.append((c, back))
            i += 1
            continue
        if flash or inverted[c]:
            bursts.append((c, c + 1))
        elif 0 < cols["err"][c] < CUT_ERR:
            pass                             # the flow explains the change: fast motion, not a cut
        else:
            real.append(c)
        i += 1
    edges = [0] + real + [n]
    shot_of = np.zeros(n, int)
    for s in range(len(edges) - 1):
        shot_of[edges[s]:edges[s + 1]] = s
    # no flow across a cut: the first drawing of a shot has no motion
    # (the change measured at c was spread back over the held frames before it)
    for c in real:
        j = c
        while j > 0 and (j == c or cols["held"][j]):
            for key, value in (("force", 0.0), ("cam_dx", 0.0), ("cam_dy", 0.0), ("cam_zoom", 1.0)):
                cols[key][j] = value
            j -= 1
        cols["angle"][c] = None
        cols["err"][c] = 0.0
    # centers: EMA per shot over the drawings that measured one
    cx, cy = [], []
    cur = None
    for k in range(n):
        if k in real or k == 0:
            cur = None
        if centers[k] is not None:
            cur = centers[k] if cur is None else (0.6 * cur[0] + 0.4 * centers[k][0],
                                                   0.6 * cur[1] + 0.4 * centers[k][1])
        c = cur or (0.5, 0.5)
        cx.append(round(c[0], 3))
        cy.append(round(c[1], 3))
    out = {k: v for k, v in cols.items()}
    out["cx"], out["cy"] = cx, cy
    out["shot"] = shot_of.tolist()
    out["_shots"] = [{"id": s + 1, "f_start": edges[s], "f_end": edges[s + 1]} for s in range(len(edges) - 1)]
    out["_bursts"] = bursts
    return out


def _drawings(fr: dict) -> list[dict]:
    """One row per distinct drawing: first frame, length, speed, angle, error."""
    starts = [k for k in range(len(fr["held"])) if not fr["held"][k]]
    rows = []
    for i, k in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(fr["held"])
        rows.append({"k": k, "len": end - k, "v": fr["force"][k], "angle": fr["angle"][k],
                     "err": fr["err"][k], "shot": fr["shot"][k]})
    return rows


def events(fr: dict, shots: list[dict], fps: float, f0: int, calm_s: float = CALM_S) -> list[dict]:
    """impact, smear, pan, calm, flash and cut events, sorted by frame.
    Each one: f (file frame), t (file seconds), type, score 0–1, signals[]."""
    n = len(fr["held"])
    ev: list[dict] = []
    luma, inverted = fr["luma"], fr["inverted"]

    def add(k: int, kind: str, score: float, signals: list[str], **extra) -> None:
        e = {"f": f0 + k, "t": round((f0 + k) / fps, 3), "type": kind, "score": round(min(1.0, max(0.0, score)), 2),
             "signals": signals, "shot": fr["shot"][k], "cx": fr["cx"][k], "cy": fr["cy"][k]}
        e.update(extra)
        ev.append(e)

    cut_frames = {s["f_start"] for s in shots[1:]}
    flashes = {k for k in range(1, n) if luma[k] - luma[k - 1] > FLASH_JUMP and k not in cut_frames}
    bursts = fr.get("_bursts", [])
    burst_frames = {c for a, b in bursts for c in range(a, b + 1)}
    for k in sorted(flashes):
        add(k, "flash", (luma[k] - luma[k - 1]) / 100, ["luma"], jump=round(luma[k] - luma[k - 1], 1))
    for k in sorted(cut_frames):
        add(k, "cut", 1.0, ["shot"])

    rows = _drawings(fr)
    force = np.asarray(fr["force"], float)
    speeds = np.array([r["v"] for r in rows if r["v"] > 0]) if rows else np.array([])
    ref = max(float(np.percentile(speeds, 98)), PEAK_MIN * 2) if speeds.size else PEAK_MIN * 2
    smear_thr = max(PEAK_MIN * 1.5, float(np.percentile(speeds, SMEAR_PCT))) if speeds.size else PEAK_MIN * 1.5
    cam = np.hypot(np.asarray(fr["cam_dx"], float), np.asarray(fr["cam_dy"], float))

    def local(arr: np.ndarray, k: int) -> float:
        """Median of ±LOCAL_FRAMES around k: what "normal" motion is right there."""
        return float(np.median(arr[max(0, k - LOCAL_FRAMES):k + LOCAL_FRAMES + 1]))

    candidates = []
    for i in range(1, len(rows)):
        r, a = rows[i], rows[i - 1]
        b = rows[i + 1] if i + 1 < len(rows) else None
        if r["v"] < PEAK_MIN or r["v"] < a["v"] or (b is not None and r["v"] <= b["v"]) or r["k"] in cut_frames:
            continue
        # the brake, on the frames after the hit (a held drawing after it is a
        # stop: those frames only move when the next drawing comes)
        end = min(n, r["k"] + 1 + BRAKE_FRAMES)
        if b is not None and b["k"] in cut_frames:
            end = min(end, b["k"])
        after_v = [force[k] for k in range(r["k"] + 1, end)] or [0.0]
        brake = min(after_v) <= BRAKE * r["v"]
        cut_after = b is not None and (b["k"] in cut_frames or b["k"] in burst_frames) and b["k"] - r["k"] <= 2
        if not brake and not cut_after:
            continue
        after = [{"v": v} for v in after_v]
        # contact: the peak drawing itself (the frame where the big displacement
        # shows up), unless an impact frame burst starts right there (that IS
        # the hit) or a flash lands just after it
        hit_k = r["k"]
        burst = [s for s, _ in bursts if hit_k - 2 <= s <= hit_k + 2]
        flash = [k for k in range(hit_k, hit_k + 3) if k in flashes]
        hit_k = burst[0] if burst else flash[0] if flash else hit_k
        hit = next((x for x in rows if x["k"] >= hit_k), r)
        prominence = r["v"] / max(local(force, r["k"]), 1.0)
        signals = ["peak", "brake" if brake else "cut_after"]
        near = range(hit_k - 2, hit_k + 3)
        boost = 0.0
        if any(k in flashes for k in near):
            signals.append("flash")
            boost += 0.15
        if any(0 <= k < n and inverted[k] for k in near):
            signals.append("inverted")
            boost += 0.1
        if any(k in burst_frames for k in near):
            signals.append("impact_frame")
            boost += 0.2
        if hit["len"] >= 3:
            signals.append("hold_after")
            boost += 0.1
        shake = cam[hit_k:hit_k + 4]
        if shake.size and float(shake.max()) > SHAKE_RATIO * max(local(cam, hit_k), PAN_MIN):
            signals.append("shake_after")
            boost += 0.05
        sharp = min((x["v"] for x in after), default=r["v"]) <= SHARP_BRAKE * r["v"]
        need = PROMINENCE_MARKED if boost >= 0.15 else PROMINENCE_SHARP if sharp else PROMINENCE
        if prominence < need:
            continue
        score = 0.55 * min(1.0, math.log(prominence) / math.log(PROMINENCE_FULL)) + 0.3 * min(1.0, r["v"] / ref) + boost
        candidates.append((score, hit_k, r, signals, prominence))
    # one hit per moment: the strongest wins within MIN_GAP frames
    kept: list[int] = []
    taken: set[int] = set()
    for score, hit_k, r, signals, prominence in sorted(candidates, key=lambda c: -c[0]):
        if any(abs(hit_k - k) < MIN_GAP for k in kept):
            continue
        kept.append(hit_k)
        taken.add(r["k"])
        add(hit_k, "impact", score, signals, angle=r["angle"], dir=direction(r["angle"]), force=r["v"],
            prominence=round(prominence, 1), peak_f=f0 + r["k"])
    for r in rows:
        if r["k"] in taken or r["k"] in cut_frames or r["v"] < smear_thr or r["err"] < SMEAR_ERR:
            continue
        if r["v"] / max(local(force, r["k"]), 1.0) < PROMINENCE:
            continue
        add(r["k"], "smear", r["v"] / ref * 0.8, ["fast", "bad_match"], angle=r["angle"],
            dir=direction(r["angle"]), force=r["v"])
    # impact frame bursts that no peak claimed are impacts too (a stylized hit)
    for s, _ in bursts:
        if not any(abs(s - k) < MIN_GAP for k in kept):
            kept.append(s)
            add(s, "impact", 0.6, ["impact_frame"] + (["flash"] if s in flashes else []),
                angle=None, dir=None, force=fr["force"][s])
    # so is a flash that blows up on strong motion (the fire of 27114: no brake,
    # the explosion keeps everything moving; the flash is the contact)
    for k in sorted(flashes):
        if any(abs(k - x) < MIN_GAP for x in kept):
            continue
        prominence = force[k] / max(local(force, k), 1.0)
        if force[k] < PEAK_MIN * 3 or prominence < FLASH_HIT_PROMINENCE:
            continue
        kept.append(k)
        ang = next((fr["angle"][j] for j in range(k, min(n, k + 3)) if fr["angle"][j] is not None), None)
        add(k, "impact", 0.45 + 0.1 * min(1.0, prominence / 3), ["flash", "strong_motion"], angle=ang,
            dir=direction(ang), force=force[k], prominence=round(prominence, 1))
    # pans: the camera keeps going one way for PAN_FRAMES frames or more
    k = 1
    while k < n:
        v = math.hypot(fr["cam_dx"][k], fr["cam_dy"][k])
        if v < PAN_MIN:
            k += 1
            continue
        ang0 = math.degrees(math.atan2(-fr["cam_dy"][k], fr["cam_dx"][k]))
        j = k
        while j + 1 < n and fr["shot"][j + 1] == fr["shot"][k]:
            vx, vy = fr["cam_dx"][j + 1], fr["cam_dy"][j + 1]
            if math.hypot(vx, vy) < PAN_MIN:
                break
            d = abs((math.degrees(math.atan2(-vy, vx)) - ang0 + 180) % 360 - 180)
            if d > PAN_SPREAD:
                break
            j += 1
        if j - k + 1 >= PAN_FRAMES:
            speed = float(np.mean([math.hypot(fr["cam_dx"][x], fr["cam_dy"][x]) for x in range(k, j + 1)]))
            # the camera moves the image the other way: the pan points where the camera goes
            pan_angle = (ang0 + 180) % 360
            add(k, "pan", speed / 30, ["camera"], angle=round(pan_angle, 1), dir=direction(pan_angle),
                frames=j - k + 1, dur=round((j - k + 1) / fps, 3), speed=round(speed, 1))
        k = j + 1
    # calm: low character force for calm_s or more
    need = max(2, int(round(calm_s * fps)))
    k = 0
    while k < n:
        if fr["force"][k] >= CALM_MAX:
            k += 1
            continue
        j = k
        while j + 1 < n and fr["force"][j + 1] < CALM_MAX:
            j += 1
        if j - k + 1 >= need:
            mean = float(np.mean(fr["force"][k:j + 1]))
            add(k, "calm", 1 - mean / CALM_MAX, ["low_force"], frames=j - k + 1, dur=round((j - k + 1) / fps, 3))
        k = j + 1
    ev.sort(key=lambda e: (e["f"], e["type"]))
    return ev
