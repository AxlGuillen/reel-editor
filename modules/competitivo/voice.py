"""Ibai's emotion peaks: where his narration goes up.

In the stream audio Ibai's voice sits over the game. When he gets excited it
goes up in energy AND in pitch at the same time; a loud teamfight with him
calm only goes up in energy. So a peak is a stretch where, in the voice band
(250–3500 Hz), the loudness is PEAK_DB above its median in the game and the
pitch PEAK_PITCH above his median pitch, for at least PEAK_MIN_S.

The medians are the game's: all the spans of the edit together, per game
(peaks(features of every span) → per span).
"""
from __future__ import annotations

import numpy as np

import config
from core import ffmpeg_runner

SR = 16000
HOP_S = 0.05
PEAK_DB = 3.0                 # dB above the game's median loudness (voice band; the stream is
                              # compressed: its p95 is only +3–4 dB, measured in LOS-T1 and MKOI-BLG)
PEAK_PITCH = 1.12             # × his median pitch
PEAK_MIN_S = 0.8
PEAK_GAP_S = 0.2              # a breath this short inside a peak does not end it
F0_MIN, F0_MAX = 80.0, 600.0


def features(path: str, start: float = 0.0, dur: float | None = None) -> dict:
    """{t, db, f0} every HOP_S of the file's audio (voice band). f0 is NaN
    where the frame is quiet (below the file's median loudness)."""
    import librosa
    cmd = [config.FFMPEG_PATH, "-v", "error", "-ss", f"{start:.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-af", "highpass=f=250,lowpass=f=3500",
            "-f", "s16le", "-"]
    y = np.frombuffer(ffmpeg_runner.capture(cmd), np.int16).astype(np.float32) / 32768.0
    hop = int(SR * HOP_S)
    if len(y) < 4 * hop:
        return {"t": np.zeros(0), "db": np.zeros(0), "f0": np.zeros(0)}
    rms = librosa.feature.rms(y=y, frame_length=4 * hop, hop_length=hop, center=True)[0]
    db = 20 * np.log10(np.maximum(rms, 1e-6))
    f0 = librosa.yin(y, fmin=F0_MIN, fmax=F0_MAX, sr=SR, frame_length=4 * hop, hop_length=hop, center=True)
    n = min(len(db), len(f0))
    db, f0 = db[:n], f0[:n].astype(float)
    f0[db < np.median(db)] = np.nan
    return {"t": start + np.arange(n) * HOP_S, "db": db, "f0": f0}


def medians(feats: list[dict]) -> tuple[float, float]:
    """(loudness, pitch) medians of a game: all its spans together."""
    db = np.concatenate([f["db"] for f in feats if len(f["db"])] or [np.zeros(1)])
    f0 = np.concatenate([f["f0"] for f in feats if len(f["f0"])] or [np.full(1, np.nan)])
    f0 = f0[np.isfinite(f0)]
    return float(np.median(db)), float(np.median(f0)) if len(f0) else float("nan")


def peaks(feat: dict, db_med: float, f0_med: float) -> list[tuple[float, float, float]]:
    """[(a, b, dB above the median)] of a span (its seconds)."""
    if not len(feat["db"]) or not np.isfinite(f0_med):
        return []
    with np.errstate(invalid="ignore"):
        hot = (feat["db"] > db_med + PEAK_DB) & (feat["f0"] > f0_med * PEAK_PITCH)
    gap = int(round(PEAK_GAP_S / HOP_S))
    out, start, last = [], None, None
    for i, h in enumerate(list(hot) + [False]):
        if h:
            if start is None:
                start = i
            last = i
        elif start is not None and (i - last > gap or i == len(hot)):
            a, b = start, last + 1
            if (b - a) * HOP_S >= PEAK_MIN_S:
                out.append((round(float(feat["t"][a]), 2), round(float(feat["t"][a]) + (b - a) * HOP_S, 2),
                            round(float(np.mean(feat["db"][a:b]) - db_med), 1)))
            start = None
    return out
