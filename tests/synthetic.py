"""Synthetic files for the edit tests (no internet nor copyrighted material):
a kick + hi-hat song with a known tempo, and videos of colored shots with
cuts at known seconds."""
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
