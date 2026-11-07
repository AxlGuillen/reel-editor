"""Synthetic files for the edit tests (no internet nor copyrighted material):
a kick + hi-hat song with a known tempo, and videos of colored shots with
cuts at known seconds."""
import math
import os
import subprocess
import wave

import numpy as np

import config

SR = 22050


def signal(bpm: float = 120, dur: float = 40, loud_from: float = 16,
           first_beat: float = 0.25) -> np.ndarray:
    """Kick on beat 1 of each bar, hi-hat on the rest; before `loud_from`
    everything plays at 30 % (intro) and after it at full volume (chorus: the drop)."""
    y = np.zeros(int(SR * dur), np.float32)
    per = 60 / bpm
    tt = np.arange(int(SR * 0.12)) / SR
    kick = (np.sin(2 * np.pi * 55 * tt) * np.exp(-tt * 30)).astype(np.float32)
    hat = (np.random.default_rng(0).standard_normal(len(tt)) * np.exp(-tt * 80)).astype(np.float32) * 0.3
    k = 0
    while True:
        t0 = first_beat + k * per
        i = int(t0 * SR)
        if i + len(tt) >= len(y):
            break
        g = 0.3 if t0 < loud_from else 1.0
        y[i:i + len(tt)] += (kick if k % 4 == 0 else hat) * g
        k += 1
    return y


def dembow(bpm: float = 90, dur: float = 40, first_beat: float = 0.25) -> np.ndarray:
    """Reggaeton / dancehall: kick on every beat, snare on the 3+3+2 tresillo
    (sixteenths 3 and 6 of each half bar), hi-hat on the eighths."""
    y = np.zeros(int(SR * dur), np.float32)
    six = 60 / bpm / 4
    tt = np.arange(int(SR * 0.12)) / SR
    kick = (np.sin(2 * np.pi * 55 * tt) * np.exp(-tt * 30)).astype(np.float32)
    snare = (np.random.default_rng(1).standard_normal(len(tt)) * np.exp(-tt * 40)).astype(np.float32) * 0.6
    hat = (np.random.default_rng(0).standard_normal(len(tt)) * np.exp(-tt * 80)).astype(np.float32) * 0.15
    n = 0
    while True:
        i = int((first_beat + n * six) * SR)
        if i + len(tt) >= len(y):
            break
        pos = n % 16
        if pos % 4 == 0:
            y[i:i + len(tt)] += kick
        if pos % 8 in (3, 6):
            y[i:i + len(tt)] += snare
        if pos % 2 == 0:
            y[i:i + len(tt)] += hat
        n += 1
    return y


def song(path: str, **kw) -> str:
    """16-bit mono WAV with signal(**kw)."""
    y = signal(**kw)
    pcm = (np.clip(y, -1, 1) * 32000).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


# Neighbors that differ in brightness AND hue: FFmpeg's scdet looks mostly at
# luma (pure red → green barely changes it) and PySceneDetect averages hue,
# saturation and brightness (pure red → yellow only changes the hue).
COLORS = ("0xC0392B", "0xF7DC6F", "0x1F3A93", "0xECF0F1", "0x111111", "0x48C9B0", "0x8E44AD", "0xF39C12")


def shots_video(path: str, durations: list[float], w: int = 640, h: int = 360,
                fps: int = 24, with_audio: bool = True) -> str:
    """Video with one colored shot (and a moving square) per duration: the
    cuts land on the cumulative sums of `durations`."""
    inputs, filters = [], []
    for i, d in enumerate(durations):
        color = COLORS[i % len(COLORS)]
        inputs += ["-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:r={fps}:d={d}"]
        # A gray square crossing the shot: there is motion to measure.
        filters.append(f"[{i}:v]drawbox=x='mod(t*{40 + 30 * i},{w - 60})':y={h // 3}:w=60:h=60:"
                       f"color=gray:t=fill,setsar=1[v{i}]")
    n = len(durations)
    graph = ";".join(filters) + ";" + "".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    cmd = [config.FFMPEG_PATH, "-v", "error", "-y", *inputs]
    total = sum(durations)
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={total}"]
    cmd += ["-filter_complex", graph, "-map", "[v]"]
    if with_audio:
        cmd += ["-map", f"{n}:a", "-c:a", "aac", "-b:a", "96k"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p", path]
    subprocess.run(cmd, check=True)
    return path


def _texture(h: int, w: int, rng: np.random.Generator, sigma: float, contrast: float) -> np.ndarray:
    import cv2
    noise = rng.random((h, w, 3)).astype(np.float32)
    tex = cv2.GaussianBlur(noise, (0, 0), sigma)
    tex = (tex - tex.mean()) / (tex.std() + 1e-6)
    return np.clip(128 + contrast * tex, 0, 255).astype(np.uint8)


def _cel(h: int, w: int, rng: np.random.Generator, shapes: int, outline: int) -> np.ndarray:
    """Flat colors with dark outlines, like a cel: optical flow needs the hard
    edges anime has (blurred noise has none at coarse scales, and DIS lost it)."""
    import cv2
    img = np.full((h, w, 3), rng.integers(60, 160, 3), np.uint8)
    for _ in range(shapes):
        color = tuple(int(c) for c in rng.integers(30, 230, 3))
        cx, cy = int(rng.integers(0, w)), int(rng.integers(0, h))
        ax, ay = int(rng.integers(w // 30 + 4, w // 6 + 8)), int(rng.integers(h // 30 + 4, h // 6 + 8))
        ang = float(rng.integers(0, 180))
        cv2.ellipse(img, (cx, cy), (ax, ay), ang, 0, 360, color, -1)
        cv2.ellipse(img, (cx, cy), (ax, ay), ang, 0, 360, (20, 20, 25), outline)
    return img


def motion_video(path: str, angle: float = 30.0, speed: float = 40.0, hit: int = 36, frames: int = 72,
                 cadence: int = 1, pan: tuple[float, float] = (0.0, 0.0), flash: bool = False,
                 cut_at: int | None = None, drift: float = 0.0, burst: tuple[int, int] | None = None,
                 w: int = 640, h: int = 360, fps: int = 24, seed: int = 7) -> str:
    """A textured disc that winds up and punches: it is still, accelerates over
    the last 3 drawings (0.25, 0.55, 1 × `speed` px per frame) toward `angle`
    (0° right, 90° up) and stops dead on frame `hit` (the contact frame, where
    the biggest displacement shows up). `cadence` 2 = drawn on twos (each
    drawing held 2 frames; the motion per drawing doubles). `pan` moves the
    background (camera) every frame; `flash` turns frame `hit` + 1 white;
    `cut_at` switches to another background from that frame on; `drift`
    moves the disc slowly (px per frame, drawn on `cadence`) before and after
    the punch; `burst` = (a, b) replaces frames a…b−1 with an inverted other
    picture (an impact frame burst)."""
    rng = np.random.default_rng(seed)
    bg = _cel(h * 2, w * 2, rng, 90, 2)
    bg2 = _cel(h * 2, w * 2, np.random.default_rng(seed + 1), 90, 2)[..., ::-1]
    disc = _cel(240, 240, rng, 14, 3)
    yy, xx = np.mgrid[0:240, 0:240]
    mask = ((xx - 120) ** 2 + (yy - 120) ** 2) <= 110 ** 2   # ~16 % of the frame, like a character
    dx, dy = math.cos(math.radians(angle)), -math.sin(math.radians(angle))
    steps = {hit - 2 * cadence: 0.25, hit - cadence: 0.55, hit: 1.0}  # drawing frame → speed factor
    path_len = sum(steps.values()) * speed * cadence
    x0 = w / 2 - dx * path_len / 2 - 120
    y0 = h / 2 - dy * path_len / 2 - 120
    pos = 0.0
    out = []
    for k in range(frames):
        if k in steps:
            pos += steps[k] * speed * cadence
        elif drift and k < hit - 2 * cadence and k % cadence == 0:
            pos += drift * cadence           # a slow drift, drawn on `cadence`
        elif drift and k > hit and k % cadence == 0:
            pos += drift * cadence
        ox, oy = int(round(pan[0] * k)) % w, int(round(pan[1] * k)) % h
        frame = (bg2 if cut_at is not None and k >= cut_at else bg)[oy:oy + h, ox:ox + w].copy()
        x = min(max(int(round(x0 + dx * pos)), 0), w - 240)
        y = min(max(int(round(y0 + dy * pos)), 0), h - 240)
        region = frame[y:y + 240, x:x + 240]
        region[mask] = disc[mask]
        if flash and k == hit + 1:
            frame[:] = 250
        if burst and burst[0] <= k < burst[1]:
            frame = 255 - bg2[:h, :w]            # an impact frame burst: 1–3 frames of something else
        out.append(frame)
    raw = np.stack(out).tobytes()
    subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "16", "-pix_fmt", "yuv420p", path], input=raw, check=True)
    return path


def gray_frames(path: str, fps: float, w: int = 32, h: int = 32) -> np.ndarray:
    """Gray frames (n, h, w) at `fps`, to measure brightness and cuts."""
    raw = subprocess.run([config.FFMPEG_PATH, "-v", "error", "-i", path, "-vf",
                          f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32)


def tmpdir(prefix: str) -> str:
    import tempfile
    return tempfile.mkdtemp(prefix=f"reelforge-{prefix}-")


def remove(path: str) -> None:
    import shutil
    if path and os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
