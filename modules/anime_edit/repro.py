"""Rebuild edit 3 of Magna vs Dante from its plan with the `manga` preset, and
compare it against the reference video (phase 2 acceptance).

    python -m modules.anime_edit.repro "<Edit Magna vs Dante folder>" "<episode.mkv>" [--out file.mp4]
           [--motion off|on] [--baseline phase2.mp4]

--motion off checks that the plan equals phase 2's and, with --baseline, that
every decoded frame matches (framemd5). --motion on checks phase 4: every
hit-locked effect within ±1 frame of its hit, the drop above the buildup and
the transition variety.

edit3.py's plan (P) and framings (MODES) are copied below as data: 37 items,
11 panels. The song is audio/glee_desde-145s_67s.wav (already cut at 144.70 s
of the full song), so the grid's phase moves by −144.70. The criterion:

  1. every cut on the exact frame edit3.py computes: round((PH + cum·BEAT)·30);
  2. measured on both videos: a spike of the frame difference on each cut;
  3. 5 control frames (a B take, a C take, a panel, a flash and the fade)
     with SSIM ≥ SSIM_MIN against the reference, plus a side-by-side sheet.
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw

from core import ffmpeg_runner
from modules.anime_edit import manga
from modules.editing import render
from modules.motion import processor as motion

START, END = 144.70, 212.09
GRID = {"bpm": 130.75, "phase": 0.21 - START}
FLASHES = [174.92 - START, 186.4 - START, 201.18 - START]   # phr.json, the phrases marked `surv`
SSIM_MIN = 0.97
REFERENCE = os.path.join("edit-3", "magna-vs-dante_edit-3_base-B_sin-texto.mp4")
MUSIC = os.path.join("audio", "glee_desde-145s_67s.wav")

P = [
    ('p', 317.30, 4, 0.50, 0.50, 'cold', 'v'),
    ('p', 372.05, 4, 0.30, 0.45, 'cold', 'z'), ('h', 2, 'm09_black-bulls-jefe_con-texto'),
    ('p', 375.00, 2, 0.50, 0.50, 'cold', 'v'),
    ('p', 516.00, 6, 0.45, 0.50, 'cold', 'v'),
    ('p', 524.17, 3, 0.55, 0.55, 'cold', 'v'), ('h', 2, 'm06_dante-demonio-cadena'),
    ('p', 531.50, 1, 0.40, 0.40, 'cold', 'v'),
    ('p', 539.00, 4, 0.50, 0.50, 'warm', 'z'),
    ('p', 543.00, 4, 0.50, 0.50, 'warm', 'v'),
    ('p', 551.44, 2, 0.45, 0.45, 'warm', 'v'), ('h', 2, 'm10_eso-es-todo_con-texto'),
    ('p', 1100.00, 4, 0.45, 0.50, 'cold', 'z'),
    ('p', 1121.30, 4, 0.50, 0.50, 'warm', 'z'), ('h', 2, 'm07_choque-DAK'),
    ('p', 1126.45, 3, 0.30, 0.25, 'cold', 'z'), ('h', 2, 'm03_golpe-1'),
    ('p', 1127.95, 1, 0.50, 0.50, 'cold', 'v'),
    ('p', 480.00, 4, 0.50, 0.50, 'cold', 'w'),
    ('p', 486.00, 2, 0.50, 0.50, 'cold', 'v'),
    ('p', 498.00, 2, 0.55, 0.55, 'cold', 'v'),
    ('p', 420.00, 2, 0.45, 0.45, 'cold', 'v'),
    ('p', 423.00, 2, 0.45, 0.45, 'cold', 'v'),
    ('p', 1129.79, 2, 0.60, 0.60, 'warm', 'v'), ('h', 2, 'm04_golpe-2'),
    ('p', 1133.07, 4, 0.50, 0.50, 'warm', 'z'), ('h', 2, 'm05_golpe-3'),
    ('p', 1136.00, 6, 0.50, 0.50, 'warm', 'v'),
    ('p', 1147.00, 4, 0.50, 0.50, 'cold', 'v'),
    ('p', 1150.00, 4, 0.50, 0.50, 'cold', 'v'),
    ('p', 1156.00, 4, 0.50, 0.50, 'cold', 'v'),
    ('p', 1163.10, 6, 0.52, 0.68, 'cold', 'v'), ('h', 2, 'm02_cadena-pecho-dante'),
    ('p', 1166.00, 4, 0.50, 0.50, 'cold', 'z'),
    ('p', 1170.00, 4, 0.45, 0.45, 'cold', 'v'),
    ('p', 1254.00, 4, 0.50, 0.50, 'warm', 'v'),
    ('p', 1256.89, 4, 0.45, 0.40, 'warm', 'z'), ('h', 2, 'm08_cierra-la-boca_con-texto'),
    ('p', 1259.40, 2, 0.45, 0.45, 'warm', 'v'),
    ('p', 1305.07, 2, 0.38, 0.38, 'cold', 'v'), ('h', 2, 'm01_grito'),
    ('p', 1306.04, 6, 0.38, 0.34, 'cold', 'v'),
    ('p', 1263.80, None, 0.50, 0.50, 'cold', 'w', 0.85),
]
MODES = "z w z z z v w z z w z w z w z v z z z w z z w z z w z z w z z v w".split()


def takes() -> list[dict]:
    """edit3.py's P with MODES applied, as manga takes."""
    out, k = [], 0
    for s in P:
        if s[0] == 'h':
            out.append({"panel": s[2], "beats": s[1]})
            continue
        out.append({"t": s[1], "beats": s[2], "focus": [s[3], s[4]], "tone": s[5], "frame": MODES[k],
                    "speed": s[7] if len(s) > 7 else 1.0})
        k += 1
    assert k == len(MODES)
    return out


def edit3_cuts(fps: int = 30) -> list[int]:
    """The cut frames exactly as edit3.py computes them."""
    beat = 60 / 130.75
    ph = (0.21 + 79 * 4 * beat) - START
    n = round((END - START) * fps)
    cum, cuts = 0.0, []
    for s in P:
        beats = s[2] if s[0] == 'p' else s[1]
        if beats is None:
            break
        cum += beats
        cuts.append(round((ph + cum * beat) * fps))
    assert len(cuts) == len(P) - 1 and cuts[-1] < n
    return cuts


def options(material: str) -> manga.MangaOptions:
    return manga.MangaOptions(music_start=0.0, music_end=END - START, fps=30, grid=GRID, flashes=FLASHES,
                              phrases=[], panels_folder=os.path.join(material, "manga"))


def gray_small(path: str, w: int = 108, h: int = 192) -> np.ndarray:
    raw = ffmpeg_runner.capture(motion.decode_command(path, 0, 10 ** 6, 30, w, h), timeout=900)
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).mean(axis=3)


def spikes(frames: np.ndarray, cuts: list[int]) -> list[bool]:
    """Is there a difference spike exactly on each cut (≥ 2.5× its neighbors)?"""
    d = np.r_[0, np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))]
    out = []
    for c in cuts:
        near = np.r_[d[max(0, c - 4):c], d[c + 1:c + 5]]
        out.append(bool(d[c] > 2.5 * max(float(np.median(near)), 0.5)))
    return out


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """SSIM of two gray images (Wang et al. 2004, Gaussian window σ 1.5)."""
    a, b = a.astype(np.float64), b.astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    blur = lambda x: cv2.GaussianBlur(x, (11, 11), 1.5)  # noqa: E731
    ma, mb = blur(a), blur(b)
    va, vb, cov = blur(a * a) - ma ** 2, blur(b * b) - mb ** 2, blur(a * b) - ma * mb
    m = ((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma ** 2 + mb ** 2 + c1) * (va + vb + c2))
    return float(m.mean())


def frame_rgb(path: str, f: int, w: int = 1080, h: int = 1920) -> np.ndarray:
    raw = ffmpeg_runner.capture(motion.decode_command(path, f, 1, 30, w, h), timeout=300)
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3)[..., ::-1]


def control_frames(info: dict) -> list[tuple[str, int]]:
    """A B take, a C take, a panel at its middle, a flash and the fade."""
    cuts = [0] + info["cuts"] + [info["frames"]]
    take = lambda i: (cuts[i] + cuts[i + 1]) // 2  # noqa: E731
    panel = info["panels"][0]
    return [("toma C (372.05)", take(1)), ("toma B (1127.95)", take(17)),
            (f"panel {panel['name'][:14]}", round((panel["start"] + panel["end"]) / 2 * 30)),
            ("destello", math.ceil(info["flashes"][0] * 30 - 1e-6)), ("fundido", info["frames"] - 20)]


def compare(ours: str, reference: str, info: dict, sheet: str) -> dict:
    cuts = info["cuts"]
    expected = edit3_cuts()
    a, b = gray_small(ours), gray_small(reference)
    rows, tiles = [], []
    for name, f in control_frames(info):
        x, y = frame_rgb(ours, f), frame_rgb(reference, f)
        s = ssim(cv2.cvtColor(x, cv2.COLOR_RGB2GRAY), cv2.cvtColor(y, cv2.COLOR_RGB2GRAY))
        rows.append({"name": name, "frame": f, "ssim": round(s, 4)})
        tiles.append((name, f, s, x, y))
    report = {"frames": [len(a), len(b), info["frames"]], "cuts_equal_edit3": cuts == expected,
              "cuts": len(cuts), "spikes_ours": sum(spikes(a, cuts)), "spikes_reference": sum(spikes(b, cuts)),
              "control": rows, "ssim_min": SSIM_MIN,
              "ok": cuts == expected and len(a) == len(b) == info["frames"]
              and all(r["ssim"] >= SSIM_MIN for r in rows)}
    _sheet(tiles, sheet)
    return report


def _sheet(tiles, out: str) -> None:
    tw, th = 270, 480
    im = Image.new("RGB", (len(tiles) * (2 * tw + 16) + 8, th + 70), (14, 11, 16))
    d = ImageDraw.Draw(im)
    for i, (name, f, s, x, y) in enumerate(tiles):
        x0 = 8 + i * (2 * tw + 16)
        im.paste(Image.fromarray(x).resize((tw, th)), (x0, 50))
        im.paste(Image.fromarray(y).resize((tw, th)), (x0 + tw + 4, 50))
        d.text((x0, 8), f"{name} · c{f}", fill=(200, 255, 0))
        d.text((x0, 26), f"ReelForge | referencia · SSIM {s:.3f}", fill=(255, 255, 255))
    im.save(out, quality=88)


def framemd5(path: str) -> list[str]:
    """MD5 of every decoded frame (the container's bytes may differ with the
    same frames)."""
    text = ffmpeg_runner.capture([ffmpeg_runner.config.FFMPEG_PATH, "-v", "error", "-i", path, "-map", "0:v",
                                  "-f", "framemd5", "-"], timeout=900).decode()
    return [line.rsplit(",", 1)[-1].strip() for line in text.splitlines() if line and not line.startswith("#")]


def plan_motion(material: str, episode: str, motion_mode: str):
    """The plan with motion off (phase 2) or on (phase 4: real beatmap + motion maps)."""
    from modules.beatmap import processor as beatmap
    from modules.motion import processor as motion
    res = ffmpeg_runner.probe_resolution(episode)
    op = options(material)
    op.motion = motion_mode
    music = os.path.join(material, MUSIC)
    if motion_mode == "off":
        return manga.plan(episode, res, {"duration": END - START}, takes(), op)
    bm = beatmap.get(music, "librosa")
    return manga.plan(episode, res, bm, takes(), op, map_for=lambda a, b: motion.get(episode, a, b))


def main(material: str, episode: str, out: str | None, keep: bool, motion_mode: str | None = None,
         baseline: str | None = None) -> dict:
    out = out or os.path.join(tempfile.gettempdir(), "reelforge-edit3-repro.mp4")
    if motion_mode is None:
        res = ffmpeg_runner.probe_resolution(episode)
        edl, info = manga.plan(episode, res, {"duration": END - START}, takes(), options(material))
    else:
        edl, info = plan_motion(material, episode, motion_mode)
    edl.music = os.path.join(material, MUSIC)
    work = tempfile.mkdtemp(prefix="reelforge-repro-")
    render.render(edl, out, None, work)
    if motion_mode == "on":
        from modules.anime_edit import blocks
        report = blocks.check(info["motion"], 30)
        report["blocks"] = [(b["block"], b["section"], b["target"]) for b in info["motion"]["blocks"]]
        report["intensity"] = info["motion"]["intensity"]
    else:
        report = compare(out, os.path.join(material, REFERENCE), info, os.path.splitext(out)[0] + "-control.jpg")
        if motion_mode == "off":
            phase2_edl, _ = manga.plan(episode, ffmpeg_runner.probe_resolution(episode), {"duration": END - START},
                                       takes(), options(material))
            phase2_edl.music = edl.music
            report["edl_identical_to_phase2"] = edl.to_dict() == phase2_edl.to_dict()
            if baseline:
                a, b = framemd5(out), framemd5(baseline)
                report["framemd5_identical_to_phase2"] = a == b
                report["framemd5_frames"] = [len(a), len(b), sum(x != y for x, y in zip(a, b))]
    report["video"] = out
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    def opt(name):
        return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None
    out, mode, base = opt("--out"), opt("--motion"), opt("--baseline")
    args = [a for a in sys.argv[1:] if not a.startswith("--") and a not in (out, mode, base)]
    main(args[0], args[1], out, "--keep" in sys.argv, mode, base)
