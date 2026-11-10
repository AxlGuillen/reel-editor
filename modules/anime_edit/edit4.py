"""Edit 4 (Lyon vs Gen.G): the team logos on the chests of Magna and Dante,
now FOLLOWING them (phase 5 acceptance).

    python -m modules.anime_edit.edit4 "<Edit Magna vs Dante folder>" <edit-3 base.mp4> [--out file.mp4]

lyon_vs_geng.py placed each logo with two hand keyframes that barely moved.
Here those keyframes are only the hint of which character it is: the chest
is tracked frame by frame (motion.subject: ONNX person and head detectors)
and the logo follows it (the `follow` event). Each window keeps the edit's
logo width. A sheet of full-resolution crops (start, middle and end of every
window) is the check.
"""
from __future__ import annotations

import os
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter

from core import ffmpeg_runner
from modules.anime_edit import repro
from modules.editing import render
from modules.editing.edl import Edl, Event, Piece, Segment
from modules.motion import subject

LOGOS = r"C:/Users/axl15/Proyectos GIT/presentacion-diego/creator/suiza-qf/assets/logos"
# (start, end, team, hint (x, y), logo width) — lyon_vs_geng.py's CHEST list
CHEST = [(5.11, 6.03, "GEN", (541, 1302), 250), (44.9, 45.45, "GEN", (400, 1120), 190),
         (48.24, 50.08, "GEN", (330, 950), 190), (2.36, 4.19, "LYON", (840, 985), 85),
         (6.9, 7.8, "LYON", (546, 1096), 220), (55.59, 56.5, "LYON", (725, 780), 170),
         (57.42, 58.0, "LYON", (650, 875), 200)]
CROP = 480


def chest_logo(team: str, folder: str) -> str:
    """The logo with its soft drop shadow (lyon_vs_geng.chest_logo), as a PNG."""
    lg = Image.open(os.path.join(LOGOS, f"{team}.png")).convert("RGBA")
    sh = Image.new("RGBA", lg.size, (0, 0, 0, 0))
    sh.paste((0, 0, 0, 170), (0, 0), lg.getchannel("A"))
    sh = sh.filter(ImageFilter.GaussianBlur(6))
    out = Image.new("RGBA", (lg.width + 20, lg.height + 20), (0, 0, 0, 0))
    out.alpha_composite(sh, (12, 12))
    out.alpha_composite(lg, (10, 10))
    path = os.path.join(folder, f"chest_{team}.png")
    out.save(path)
    return path


def plan(base: str, folder: str) -> tuple[Edl, list[dict]]:
    dur = ffmpeg_runner.probe_duration(base)
    logos = {t: chest_logo(t, folder) for t in ("GEN", "LYON")}
    events, tracks = [], []
    for a, e, team, hint, width in CHEST:
        rows = subject.track(base, a, e, hint, width_hint=width)
        pts = [[r["t"], r["x"], r["y"], width] for r in rows]
        end = min(e, rows[-1]["t"] + 1 / 30)            # the track stops at a cut inside the window
        events.append(Event(a, "follow", dur=end - a, image=logos[team], track=pts, opacity=0.93, fade=0.12))
        tracks.append({"start": a, "end": e, "team": team, "rows": rows,
                       "detected": sum(r["detected"] for r in rows), "frames": len(rows)})
    edl = Edl([Segment(base, [Piece(0.0, dur, dur)], "edit 3")], fps=30, events=events, look="neutral",
              original_volume=1.0, fade_out=0)
    return edl, tracks


def sheet(video: str, tracks: list[dict], out: str) -> str:
    """Full-resolution crops (CROP×CROP around the tracked chest) at the start,
    middle and end of every window."""
    tiles = []
    for tr in tracks:
        rows = tr["rows"]
        for r in (rows[1], rows[len(rows) // 2], rows[-2]):
            f = round(r["t"] * 30)
            frame = Image.fromarray(repro.frame_rgb(video, f))
            x0 = int(min(max(r["x"] - CROP / 2, 0), 1080 - CROP))
            y0 = int(min(max(r["y"] - CROP / 2, 0), 1920 - CROP))
            tile = frame.crop((x0, y0, x0 + CROP, y0 + CROP))
            ImageDraw.Draw(tile).text((8, 6), f"{tr['team']} c{f} ({r['x']:.0f},{r['y']:.0f})", fill=(200, 255, 0))
            tiles.append(tile)
    im = Image.new("RGB", (3 * CROP + 16, len(tracks) * (CROP + 8) + 8), (14, 11, 16))
    for i, t in enumerate(tiles):
        im.paste(t, (4 + (i % 3) * (CROP + 4), 4 + (i // 3) * (CROP + 8)))
    im.save(out, quality=85)
    return out


def main(material: str, base: str, out: str | None) -> dict:
    out = out or os.path.join(tempfile.gettempdir(), "reelforge-edit4-follow.mp4")
    folder = tempfile.mkdtemp(prefix="reelforge-edit4-")
    edl, tracks = plan(base, folder)
    render.render(edl, out, None, os.path.join(folder, "work"))
    path = sheet(out, tracks, os.path.splitext(out)[0] + "-recortes.jpg")
    report = {"video": out, "sheet": path,
              "windows": [{k: tr[k] for k in ("start", "end", "team", "detected", "frames")} for tr in tracks]}
    print(report)
    return report


if __name__ == "__main__":
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
    args = [a for a in sys.argv[1:] if not a.startswith("--") and a != out]
    main(args[0], args[1], out)
