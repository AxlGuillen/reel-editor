"""Song analysis: beats, bars, energy, sections and the drop.

The base of the music edits (anime_edit and competitivo's edit mode): they
decide WHAT goes at each moment, and this module tells them WHEN the hits
land. Everything is in seconds from the start of the file, in a dict saved
as-is as beatmap.json (the contract is in docs/edits.md).

Engines:
  librosa    beats with its tracker (dynamic programming over the onset
             envelope) and the first beat of each bar (downbeat) from the
             phase of 4 where the kick hits hardest (energy below 150 Hz).
  beat_this  Beat This! (CPJKU, ISMIR 2024): beats and downbeats with a
             network. Optional (pip install beat-this); "auto" uses it if present.

Sections by energy, without labels from a model: each bar is "high" or
"low" by its loudness relative to the song, and the runs are named by
position (intro, verse, chorus, bridge, outro). The drop is the start of the
high run with the biggest energy jump. Crude on purpose: explainable,
deterministic and enough to decide how often to cut.
"""
from __future__ import annotations

import numpy as np

import config
from core import ffmpeg_runner

SR = 22050
HOP = 512                     # energy and kick
# The beat tracker works with the period in whole frames of the onset
# envelope: with 512 (23 ms) a 120 BPM song came out as 117.45 (a 22-frame lag)
# and the beats drifted ~40 ms. With 128 (5.8 ms) it is 120.2 and ~16 ms.
HOP_BEAT = 128
VERSION = 2
HIT_PERCENTILE = 80           # onsets above this percentile are hits (shakes)
HIGH_THRESHOLD = 0.55         # relative bar energy (0–1) to count as "high"
MIN_BARS = 2                  # a shorter run is merged into the previous one
ENGINES = ("auto", "librosa", "beat_this")


def load_audio(path: str, sr: int = SR) -> np.ndarray:
    """Mono float32 audio at `sr` Hz, decoded by FFmpeg (any format it reads,
    including the track of a video)."""
    raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-i", path, "-vn",
                                 "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], timeout=900)
    y = np.frombuffer(raw, np.float32)
    if not len(y):
        raise RuntimeError("El archivo no tiene audio.")
    return y


def beat_this_available() -> bool:
    # Not just ImportError: on Windows a torch whose DLLs don't load raises
    # OSError (WinError 126/1114), and that killed the whole analysis.
    try:
        import beat_this.inference  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _beat_this(path: str) -> tuple[np.ndarray, np.ndarray]:
    """(beats, downbeats) in seconds with Beat This!. The first time it
    downloads the model weights (~80 MB)."""
    from beat_this.inference import File2Beats
    try:
        import torch
        devices = ["cuda", "cpu"] if torch.cuda.is_available() else ["cpu"]
    except Exception:  # noqa: BLE001
        devices = ["cpu"]
    for device in devices:
        try:
            beats, downbeats = File2Beats(checkpoint_path="final0", device=device, dbn=False)(path)
            return np.asarray(beats, float), np.asarray(downbeats, float)
        except Exception:  # noqa: BLE001 - e.g. a torch build without kernels for this GPU
            if device == devices[-1]:
                raise
    raise RuntimeError("Beat This! no pudo analizar la canción.")


def _mean_per_span(values: np.ndarray, times: np.ndarray, cuts: np.ndarray, end: float) -> np.ndarray:
    """Mean of `values` (sampled at `times`) in each [cuts[i], cuts[i+1])."""
    edges = np.append(cuts, end)
    out = np.zeros(len(cuts))
    for i in range(len(cuts)):
        m = (times >= edges[i]) & (times < edges[i + 1])
        out[i] = float(values[m].mean()) if m.any() else 0.0
    return out


def _downbeat_phase(beats: np.ndarray, kick: np.ndarray, sr: int) -> int:
    """Of the 4 possible bar phases, the one where the kick hits hardest
    (`kick`: onset envelope below 150 Hz, in HOP frames)."""
    idx_all = np.clip(np.round(beats * sr / HOP).astype(int), 0, len(kick) - 1)
    best, phase = -1.0, 0
    for p in range(min(4, len(beats))):
        score = float(kick[idx_all[p::4]].mean())
        if score > best:
            best, phase = score, p
    return phase


def sections(downbeats: list[float], bar_energy: list[float], duration: float) -> list[dict]:
    """Runs of the song by energy: [{type, start, end, energy}].

    Each bar is high or low by its energy relative to the song (percentiles
    10–90, smoothed with a 3-bar median). Runs shorter than MIN_BARS are
    merged into the previous one. Names by position: low before the first
    high is the intro, low between highs is a bridge (or verse if long) and
    low at the end is the outro; high is chorus."""
    n = len(downbeats)
    if not n:
        return [{"type": "verse", "start": 0.0, "end": round(duration, 3), "energy": 0.0}]
    e = np.asarray(bar_energy, float)
    if n >= 3:
        smooth = np.array([np.median(e[max(0, i - 1):i + 2]) for i in range(n)])
    else:
        smooth = e
    p10, p90 = np.percentile(smooth, 10), np.percentile(smooth, 90)
    rel = (smooth - p10) / (p90 - p10) if p90 > p10 else np.full(n, 0.5)
    high = rel > HIGH_THRESHOLD

    runs: list[list] = []                      # [high, start_index, end_index_exclusive]
    for i, h in enumerate(high):
        if runs and runs[-1][0] == h:
            runs[-1][2] = i + 1
        else:
            runs.append([bool(h), i, i + 1])
    merged: list[list] = []
    for r in runs:
        if merged and (r[2] - r[1] < MIN_BARS or merged[-1][0] == r[0]):
            merged[-1][2] = r[2]
        else:
            merged.append(r)
    if len(merged) > 1 and merged[0][2] - merged[0][1] < MIN_BARS:
        merged[1][1] = 0
        merged.pop(0)

    seen_high = False
    out = []
    for k, (h, i0, i1) in enumerate(merged):
        start = 0.0 if i0 == 0 else downbeats[i0]
        end = duration if i1 >= n else downbeats[i1]
        if h:
            kind = "chorus"
            seen_high = True
        elif not seen_high:
            kind = "intro"
        elif k == len(merged) - 1:
            kind = "outro"
        else:
            kind = "bridge" if i1 - i0 <= 8 else "verse"
        out.append({"type": kind, "start": round(start, 3), "end": round(end, 3),
                    "energy": round(float(rel[i0:i1].mean()), 3)})
    return out


def drop_and_buildups(downbeats: list[float], bar_energy: list[float],
                      secs: list[dict]) -> tuple[float | None, list[list[float]]]:
    """The drop (start of the chorus with the biggest energy jump) and the
    buildups (the 2 bars before each chorus that comes from something low)."""
    e = np.asarray(bar_energy, float)
    if len(e) < 2:
        return None, []
    idx = {round(d, 3): i for i, d in enumerate(downbeats)}
    best, drop = -np.inf, None
    buildups = []
    for k, s in enumerate(secs):
        if s["type"] != "chorus":
            continue
        i = idx.get(s["start"])
        if i is None or i == 0:
            continue
        jump = float(e[i:i + 2].mean() - e[max(0, i - 2):i].mean())
        if jump > best:
            best, drop = jump, s["start"]
        if k > 0 and secs[k - 1]["type"] != "chorus" and i >= 2:
            buildups.append([round(downbeats[i - 2], 3), s["start"]])
    if drop is None:
        # No chorus coming from something low: the bar where energy rises the most.
        d = np.diff(e)
        drop = round(downbeats[int(np.argmax(d)) + 1], 3)
    return drop, buildups


def analyze(y: np.ndarray, sr: int = SR, engine: str = "auto", path: str | None = None) -> dict:
    """Beatmap of the signal `y`. `path` (the file) is only used by Beat This!."""
    import librosa

    if engine not in ENGINES:
        raise ValueError(f"Motor de beats inválido: {engine!r}. Opciones: {', '.join(ENGINES)}.")
    duration = len(y) / sr
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP_BEAT)
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    kick = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S[freqs < 150], ref=np.max),
                                        sr=sr, hop_length=HOP)
    used = "librosa"
    beats = None
    if engine in ("auto", "beat_this") and path and beat_this_available():
        try:
            beats, downbeats = _beat_this(path)
            used = "beat_this"
        except Exception as exc:  # noqa: BLE001 - "auto" goes on with librosa
            if engine == "beat_this":
                raise RuntimeError(f"Beat This! falló: {exc}") from exc
            beats = None
    elif engine == "beat_this":
        raise RuntimeError("Beat This! no está instalado (pip install beat-this).")
    if beats is None:
        _, frames = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=HOP_BEAT, trim=False)
        beats = librosa.frames_to_time(frames, sr=sr, hop_length=HOP_BEAT)
        downbeats = beats[_downbeat_phase(beats, kick, sr)::4]
    if len(beats) < 4:
        raise RuntimeError("No se encontró un pulso en la canción (¿es música con ritmo?).")
    beat_period = float(np.median(np.diff(beats)))

    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0]
    t_rms = librosa.times_like(rms, sr=sr, hop_length=HOP)
    beat_energy = _mean_per_span(rms, t_rms, beats, duration)
    bar_energy = _mean_per_span(rms, t_rms, downbeats, duration)
    peak = float(beat_energy.max()) or 1.0

    onsets = librosa.onset.onset_detect(onset_envelope=onset, sr=sr, hop_length=HOP_BEAT, units="frames")
    strength = onset[onsets] if len(onsets) else np.array([])
    cut = np.percentile(strength, HIT_PERCENTILE) if len(strength) else 0
    t_onsets = librosa.frames_to_time(onsets, sr=sr, hop_length=HOP_BEAT)

    db = [round(float(d), 3) for d in downbeats]
    secs = sections(db, list(bar_energy), duration)
    drop, buildups = drop_and_buildups(db, list(bar_energy), secs)
    return {
        "version": VERSION,
        "engine": used,
        "duration": round(duration, 3),
        "bpm": round(60 / beat_period, 2),
        "beats": [round(float(b), 3) for b in beats],
        "downbeats": db,
        "onsets": [round(float(t), 3) for t in t_onsets],
        "hits": [round(float(t), 3) for t, f in zip(t_onsets, strength) if f >= cut],
        "energy_per_beat": [round(float(v) / peak, 3) for v in beat_energy],
        "sections": secs,
        "drop": drop,
        "buildups": buildups,
        "words": [],
    }


# --- Helpers for the planners that work on a beatmap ------------------------------
def window(bm: dict, start: float, dur: float) -> dict:
    """The beatmap seen from the window [start, start + dur] of the song: times
    become relative to `start` and only what falls inside remains. This is
    what the planners use, since they work in output time."""
    end = start + dur

    def inside(values):
        return [round(t - start, 3) for t in values if start - 1e-6 <= t < end]

    beats = bm["beats"]
    e = bm.get("energy_per_beat") or [0.0] * len(beats)
    energy = [v for t, v in zip(beats, e) if start - 1e-6 <= t < end]
    secs = []
    for s in bm.get("sections", []):
        a, b = max(s["start"], start), min(s["end"], end)
        if b > a:
            secs.append({**s, "start": round(a - start, 3), "end": round(b - start, 3)})
    drop = bm.get("drop")
    return {
        **bm,
        "duration": round(dur, 3),
        "beats": inside(beats),
        "downbeats": inside(bm["downbeats"]),
        "onsets": inside(bm.get("onsets", [])),
        "hits": inside(bm.get("hits", [])),
        "energy_per_beat": energy,
        "sections": secs,
        "drop": round(drop - start, 3) if drop is not None and start <= drop < end else None,
        "buildups": [[round(a - start, 3), round(b - start, 3)] for a, b in bm.get("buildups", [])
                     if start <= a and b <= end],
        "words": [{**w, "start": round(w["start"] - start, 3), "end": round(w["end"] - start, 3)}
                  for w in bm.get("words", []) if start <= w["start"] < end],
    }


def section_at(bm: dict, t: float) -> str:
    """Type of the section playing at `t` ("verse" if none)."""
    for s in bm.get("sections", []):
        if s["start"] <= t < s["end"]:
            return s["type"]
    return "verse"


def in_buildup(bm: dict, t: float) -> bool:
    return any(a <= t < b for a, b in bm.get("buildups", []))


def period(bm: dict) -> float:
    """Typical duration of a beat (s)."""
    b = bm["beats"]
    return float(np.median(np.diff(b))) if len(b) > 1 else 60 / max(bm.get("bpm") or 120, 1)


def nearest_beat(beats: list[float], t: float) -> int:
    """Index of the beat closest to `t`."""
    arr = np.asarray(beats)
    return int(np.argmin(np.abs(arr - t)))
