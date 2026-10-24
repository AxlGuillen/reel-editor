"""Another creator's camera instead of Ibai's.

With a creator's VOD of the same broadcast: find where its webcam is, sync
the two VODs and put ONLY the webcam in the box where Ibai's camera goes.

    sync     the creator's VOD runs at a different time: the offset is where
             the two audios line up (onset envelopes, cross-correlation), in
             a window around what the two streams' start times suggest
    webcam   the blocks of the creator's frame that are a real camera (no
             saturated green: the map always has it, ambient_ranges) and move
             (a static overlay does not), their largest rectangle; or the box
             given by hand (creator_cam=x,y,w,h in 1920x1080)
    compose  the span with the creator's webcam over Ibai's camera box
             (cover, cropped to its aspect), the audio and the rest intact:
             everything after (summaries, edits) works as always
"""
from __future__ import annotations

import numpy as np

import config
from core import ffmpeg_runner

SR = 8000
HOP = 80                       # onset envelope every 10 ms
SYNC_MIN_SCORE = 0.25          # normalized correlation to trust a sync
GRID = (32, 18)                # blocks of the frame for the webcam search
CAM_GREEN = 0.02
CAM_MIN_BLOCKS = 6


def _audio(path: str, start: float = 0.0, dur: float | None = None) -> np.ndarray:
    cmd = [config.FFMPEG_PATH, "-v", "error", "-ss", f"{max(0.0, start):.3f}"]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    return np.frombuffer(ffmpeg_runner.capture(cmd), np.int16).astype(np.float32) / 32768.0


def _envelope(y: np.ndarray) -> np.ndarray:
    """Onset strength (positive changes of the log energy), normalized."""
    n = len(y) // HOP
    if n < 4:
        return np.zeros(0)
    e = np.log1p(1000 * (y[: n * HOP].reshape(n, HOP) ** 2).mean(axis=1))
    on = np.maximum(np.diff(e, prepend=e[0]), 0)
    on -= on.mean()
    return on / (on.std() or 1)


def sync(ours: str, theirs: str, search_s: float = 90.0) -> tuple[float, float]:
    """(delta, score): second t of OUR file is t + delta of THEIR file.
    `theirs` covers ours ± search_s (downloaded so)."""
    a, b = _envelope(_audio(ours)), _envelope(_audio(theirs))
    if not len(a) or len(b) < len(a):
        return 0.0, 0.0
    # correlation of a over every lag of b (FFT)
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    corr = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)[: len(b) - len(a) + 1]
    k = int(np.argmax(corr))
    score = float(corr[k] / len(a))
    return round(k * HOP / SR, 3), round(score, 3)


def detect_webcam(path: str, start: float = 0.0, dur: float = 30.0, fps: float = 1.0) -> tuple | None:
    """(x, y, w, h) of the creator's webcam in 1920x1080, or None."""
    from modules.competitivo.layout import _green_share
    gw, gh = GRID
    w, h = gw * 8, gh * 8
    raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                                 "-i", path, "-an", "-vf", f"fps={fps},scale={w}:{h},format=rgb24",
                                 "-f", "rawvideo", "-"])
    fr = np.frombuffer(raw, np.uint8)
    n = fr.size // (w * h * 3)
    if n < 3:
        return None
    fr = fr[: n * w * h * 3].reshape(n, h, w, 3).astype(np.float32)
    blocks = fr.reshape(n, gh, 8, gw, 8, 3).transpose(0, 1, 3, 2, 4, 5).reshape(n * gh * gw, 8, 8, 3)
    green = _green_share(blocks).reshape(n, gh, gw).mean(axis=0)
    gray = fr.mean(axis=3)
    # per pixel, then per block: a webcam moves (people, sensor noise); an overlay does not
    moving = np.abs(np.diff(gray, axis=0)).mean(axis=0).reshape(gh, 8, gw, 8).mean(axis=(1, 3))
    cam = (green < CAM_GREEN) & (moving > 1.5) & (moving < 80)
    # largest connected group of camera blocks
    seen = np.zeros_like(cam)
    best = []
    for y0 in range(gh):
        for x0 in range(gw):
            if cam[y0, x0] and not seen[y0, x0]:
                stack, comp = [(y0, x0)], []
                seen[y0, x0] = True
                while stack:
                    y, x = stack.pop()
                    comp.append((y, x))
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        yy, xx = y + dy, x + dx
                        if 0 <= yy < gh and 0 <= xx < gw and cam[yy, xx] and not seen[yy, xx]:
                            seen[yy, xx] = True
                            stack.append((yy, xx))
                if len(comp) > len(best):
                    best = comp
    if len(best) < CAM_MIN_BLOCKS or len(best) > 0.6 * gw * gh:
        return None                                   # nothing, or the whole frame (not a webcam overlay)
    ys, xs = zip(*best)
    bw, bh = 1920 / gw, 1080 / gh
    x, y = int(min(xs) * bw), int(min(ys) * bh)
    return x, y, int((max(xs) + 1) * bw) - x, int((max(ys) + 1) * bh) - y


DIFF_W, DIFF_H = 240, 135
DIFF_ON = 25.0                 # a pixel that differs this much between the two streams
DIFF_LAYOUT_MAX = 18.0         # if the median difference is above this, the layouts are not the same
IBAI_COUNTER = (1630, 0, 290, 115)   # Ibai's viewer counter (top right), also ours only


def _frames(path: str, ts: list[float]) -> np.ndarray:
    out = []
    for t in ts:
        raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
                                     "-frames:v", "1", "-vf", f"scale={DIFF_W}:{DIFF_H},format=gray",
                                     "-f", "rawvideo", "-"])
        if len(raw) == DIFF_W * DIFF_H:
            out.append(np.frombuffer(raw, np.uint8).reshape(DIFF_H, DIFF_W).astype(np.float32))
    return np.array(out)


def webcam_by_difference(ours: str, theirs: str, delta: float, ibai_box: tuple, dur: float) -> tuple | None:
    """The creator's webcam where its stream (synced) differs from ours: in a
    co-stream both show Riot's broadcast in the same place, so what differs
    is its webcam (and Ibai's camera and counter, which are known). None if
    the layouts are not the same (then detect_webcam)."""
    ts = [dur * (k + 0.5) / 10 for k in range(10)]
    a, b = _frames(ours, ts), _frames(theirs, [t + delta for t in ts])
    if len(a) < 5 or len(a) != len(b):
        return None
    diff = np.median(np.abs(a - b), axis=0)
    sx, sy = DIFF_W / 1920, DIFF_H / 1080
    for x, y, w, h in (ibai_box, IBAI_COUNTER):
        diff[int(y * sy):int((y + h) * sy) + 2, int(x * sx):int((x + w) * sx) + 2] = 0
    if float(np.median(diff)) > DIFF_LAYOUT_MAX:
        return None
    import cv2
    mask = (diff > DIFF_ON).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if n <= 1:
        return None
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, area = stats[k]
    if area < 0.01 * DIFF_W * DIFF_H:
        return None
    return int(x / sx), int(y / sy), int(w / sx), int(h / sy)


def parse_box(text: str) -> tuple[int, int, int, int]:
    """ "1500,780,400,280" → (x, y, w, h) inside 1920x1080. ValueError."""
    x, y, w, h = (int(float(v)) for v in str(text).replace(" ", "").split(","))
    if w < 40 or h < 40 or x < 0 or y < 0 or x + w > 1920 or y + h > 1080:
        raise ValueError(text)
    return x, y, w, h


SEARCH_S = 90.0                # the first sync looks this far around the start times' guess
REFINE_S = 8.0                 # the next spans, around the first sync


class CreatorCam:
    """A creator's webcam over Ibai's in every span of a job: the creator's
    VOD start (info_vod), the offset measured on the first span and reused
    (refined) on the next, and the webcam box found once."""

    def __init__(self, url: str, ibai_start, cam: tuple | None = None, delta: float | None = None):
        from modules.competitivo import fuente
        self.url, self.cam = url, cam
        info = fuente.info_vod(url)
        # creator_t = ibai_t + offset; first guess: same latency in both streams
        self.guess = delta if delta is not None else (ibai_start - info["inicio_utc"]).total_seconds()
        self.measured = delta is not None
        self.notes: list[str] = []

    def apply(self, span: str, vod_a: float, vod_b: float, box: tuple, work: str, name: str) -> str:
        """The span with the creator's webcam (or the span as it was, with a note)."""
        import os
        from modules.competitivo import fuente
        search = REFINE_S if self.measured else SEARCH_S
        a = vod_a + self.guess - search
        theirs = fuente.bajar_tramo(self.url, max(0.0, a), vod_b + self.guess + search,
                                    os.path.join(work, f"creator_{name}"))
        delta, score = sync(span, theirs)
        if score < SYNC_MIN_SCORE:
            self.notes.append(f"{name}: no se pudo sincronizar con el VOD del creador (score {score}).")
            return span
        self.guess, self.measured = max(0.0, a) + delta - vod_a, True
        if self.cam is None:
            self.cam = webcam_by_difference(span, theirs, delta, box, vod_b - vod_a) or detect_webcam(theirs)
            if self.cam is None:
                self.notes.append("No se encontró la webcam del creador: pasala con creator_cam=x,y,w,h.")
                return span
        out = os.path.join(work, f"composed_{name}.mp4")
        ffmpeg_runner.capture(compose_command(span, theirs, delta, self.cam, box, out))
        os.remove(theirs)
        return out


def compose_command(span: str, creator_span: str, delta: float, cam: tuple, box: tuple, out: str) -> list[str]:
    """The span with the creator's webcam (cam, in its file; second t of ours
    is t + delta of theirs) over Ibai's camera box (box = x, y, w, h)."""
    cx, cy, cw, ch = cam
    bx, by, bw, bh = box
    # cover: crop the webcam to the box's aspect, centered
    if cw / ch > bw / bh:
        nw = int(ch * bw / bh) // 2 * 2
        cx, cw = cx + (cw - nw) // 2, nw
    else:
        nh = int(cw * bh / bw) // 2 * 2
        cy, ch = cy + (ch - nh) // 2, nh
    return [config.FFMPEG_PATH, "-y", "-i", span, "-ss", f"{max(0.0, delta):.3f}", "-i", creator_span,
            "-filter_complex",
            f"[1:v]crop={cw}:{ch}:{cx}:{cy},scale={bw}:{bh}:flags=lanczos,setsar=1,"
            f"setpts=PTS-STARTPTS[cam];[0:v][cam]overlay={bx}:{by}:eof_action=pass[v]",
            "-map", "[v]", "-map", "0:a?", "-c:a", "copy", *ffmpeg_runner.video_encode_flags(16), out]
