"""Magna vs Dante — the long edit (07/11/2026): the whole song, episode 175.

Rules v1.0 (the manga preset: takes in beats, framings A/B/C, cold/warm
tones, the edit 3 manga panels, a flash on every phrase) plus the composites
(blur cuts from the drop, full hits, impact bursts, color isolation) and the
lyrics with the invert subtitles. How it was made and what was learned:
docs/edits.md (usage) and the PKB note worlds-2026/edit-largo-magna-vs-dante.md (how).

    python -m modules.anime_edit.edit_long <out folder> --episode <ep175.mkv> --song <I Will Survive (Glee).mp3>
           --panels "<Edit Magna vs Dante>/manga" [--lyrics letra.txt] [--plan-only]

The grid was measured on the song: 128.00 BPM, phase 0.05 s, a downbeat
every 4 beats, the drop on beat 96 (0:45). The takes are source seconds of
the episode, chosen from its 659 shots (anime_edit.shots contact sheets).
`--lyrics`: Axl's text, aligned to the sung words (editing.lyrics_align). The
Glee track is a mashup (Survivor + I Will Survive) and sings the third
Survivor chorus twice: CHORUS_TWICE repeats those lines of the text.
"""
from __future__ import annotations

import argparse
import json
import os

from modules.anime_edit import manga
from modules.editing import lyrics_align, render

BASE = "Magna vs Dante - edit largo v1"
GRID = {"bpm": 128.0, "phase": 0.05}
DROP_S = 45.0
CHORUS_TWICE = (58, 65)       # non-empty lines (1-based) of the lyrics sung twice in a row
WORD_STYLE = {"font": "Montserrat-ExtraBold.ttf", "inv_size": 104, "inv_y": 960, "inv_words": "1"}


def T(t, beats, frame="B", tone="cold", focus=(0.5, 0.5), speed=1.0):
    return {"t": t, "beats": beats, "frame": frame, "tone": tone, "focus": list(focus), "speed": speed}


def P(name, beats=2):
    return {"panel": name, "beats": beats}


SECTIONS = {
    # 0:00–0:45 — slow intro: Dante's menace, the training with Zora half a year ago
    "intro": (96, [
        T(304.97, 8, "C"),                                  # the hideout under the snow
        T(316.50, 8, "B"),                                  # the mark
        T(347.89, 4, "C"),                                  # Dante, winged silhouette
        T(350.60, 4, "B"),
        T(380.78, 6, "A"),                                  # Dante's teeth
        T(372.05, 4, "B", focus=(0.30, 0.45)), P("m09_black-bulls-jefe_con-texto"),
        T(496.20, 8, "C"),                                  # Magna stands
        T(588.17, 8, "C", "warm"),                          # half a year ago
        T(600.18, 4, "A", "warm"),
        T(621.37, 4, "B", "warm"),                          # Zora laughs
        T(658.24, 4, "C", "warm"),
        T(689.36, 4, "A", "warm"),                          # the thread of light
        T(702.12, 4, "B", "warm"),
        T(723.60, 4, "A", "warm"),
        T(761.84, 4, "C", "cold", speed=0.9),               # the magic circle
        T(767.85, 4, "A", "cold"),                          # soul fire in his hand
        T(772.86, 4, "C", "cold"),
        T(779.70, 4, "C", "cold"),                          # back to the fight: the chains
        T(910.41, 4, "A", "cold"),                          # the buildup: Magna's glare
    ]),
    # 0:45–1:41 — the drop: fire and chains
    "fight1": (120, [
        T(539.00, 4, "B", "warm"),                          # DROP: the explosion (hit)
        T(543.00, 4, "A", "warm"),
        T(516.00, 6, "A", "cold", (0.45, 0.50)),
        T(524.17, 3, "A", "cold", (0.55, 0.55)), P("m06_dante-demonio-cadena"),
        T(531.50, 1, "A", "cold", (0.40, 0.40)),
        T(551.44, 2, "A", "warm", (0.45, 0.45)), P("m10_eso-es-todo_con-texto"),
        T(554.14, 4, "A", "warm"),
        T(562.10, 4, "B", "cold"),
        T(574.74, 4, "C", "warm"),
        T(580.83, 2, "A", "warm"),
        T(786.45, 4, "C"), T(791.08, 4, "B"), T(799.30, 4, "B"), T(805.80, 4, "A"),
        T(824.57, 4, "A", "warm"),                          # the fire bat
        T(827.66, 4, "C", "warm"),
        T(830.41, 2, "A", "warm"), T(833.58, 2, "B"),
        T(845.90, 4, "A"),                                  # Dante laughs
        T(849.01, 4, "C"), T(853.52, 4, "A"),
        T(859.69, 2, "C", "warm"), T(861.94, 2, "A", "warm"),
        T(864.95, 4, "B"), T(876.79, 4, "B"), T(888.30, 2, "A"),
        T(899.65, 4, "C"), T(904.65, 2, "A", "warm"),
        T(914.29, 4, "A"),
        T(916.79, 2, "A", "warm"),                          # the punch to the face (hit)
        T(919.04, 2, "B", "warm"), T(923.00, 2, "C"),
        T(932.22, 4, "C"), T(943.32, 4, "B"), T(945.07, 2, "A"), T(952.62, 2, "B"),
    ]),
    # 1:41–1:53 — the break: the bright memories of the Black Bulls
    "break": (24, [
        T(989.45, 4, "C", "warm"), T(991.66, 4, "B", "warm"), T(997.66, 4, "C", "warm"),
        T(1007.17, 4, "C", "warm", speed=0.95), T(1009.10, 4, "A", "warm"), T(1022.06, 4, "C", "warm"),
    ]),
    # 1:53–2:53 — the deathmatch gets bloody
    "fight2": (128, [
        T(1046.25, 4, "C"),
        T(1052.26, 2, "B"),                                 # the chains cross (burst)
        T(1053.93, 2, "B"), T(1055.01, 4, "A"), T(1057.22, 2, "C"),
        T(1058.39, 2, "B", "warm"), T(1061.31, 2, "A", "warm"),
        T(1064.44, 4, "B"), T(1069.99, 2, "A"), T(1071.57, 2, "A"), T(1074.91, 4, "A"),
        T(1078.37, 4, "B"), T(1083.25, 2, "A", "warm"), T(1087.92, 2, "A"),
        T(1089.96, 4, "C", "warm"), T(1094.72, 2, "B", "warm"),
        T(1100.00, 4, "B", focus=(0.45, 0.50)),
        T(1102.02, 4, "B", "warm"), T(1107.44, 4, "A", "warm"), T(1115.11, 2, "A", "warm"),
        T(1121.30, 4, "B", "warm"), P("m07_choque-DAK"),
        T(1126.45, 3, "B", "cold", (0.30, 0.25)), P("m03_golpe-1"),
        T(1127.95, 1, "A"),
        T(1129.79, 2, "A", "warm", (0.60, 0.60)), P("m04_golpe-2"),
        T(1133.07, 4, "B", "warm"), P("m05_golpe-3"),
        T(1136.00, 6, "A", "warm"),
        T(1147.00, 4, "A"), T(1150.00, 4, "A"), T(1156.00, 4, "A"),
        T(1163.10, 6, "A", "cold", (0.52, 0.68)), P("m02_cadena-pecho-dante"),
        T(1166.00, 4, "B"), T(1170.00, 4, "A", focus=(0.45, 0.45)),
        T(1175.67, 4, "B"), T(1181.18, 4, "A"), T(1189.19, 4, "C"), T(1201.66, 2, "A"),
    ]),
    # 2:53–3:00 — the gap in the vocals: Dante falters (color isolation)
    "bridge": (16, [
        T(1204.16, 4, "A"), T(1218.43, 1, "A"), T(1219.18, 3, "B"),
        T(1222.56, 2, "A"), T(1240.20, 2, "A"), T(1242.78, 2, "A"), T(1247.33, 2, "B"),
    ]),
    # 3:00–3:26 — the final punch
    "climax": (56, [
        T(1249.16, 2, "B"),
        T(1254.00, 4, "A", "warm"),
        T(1256.89, 4, "B", "warm", (0.45, 0.40)), P("m08_cierra-la-boca_con-texto"),
        T(1259.40, 2, "A", "warm", (0.45, 0.45)),
        T(1262.97, 2, "A", "warm"), T(1264.97, 2, "C", "warm"),
        T(1265.97, 4, "B"),
        T(1270.98, 4, "B", "warm"),
        T(1272.98, 4, "A", "warm"),                         # the orange chains (hit)
        T(1274.98, 4, "C"), T(1278.19, 4, "C"),
        T(1298.80, 4, "C"), T(1301.80, 4, "A"),
        T(1305.07, 2, "A", "cold", (0.38, 0.38)), P("m01_grito"),
        T(1306.04, 6, "A", "cold", (0.38, 0.34)),
    ]),
    # 3:26–3:32 — Magna falls, Zora holds him
    "outro": (None, [
        T(1312.81, 4, "B"),
        T(1326.70, None, "C"),
    ]),
}

COMPOSITES = [
    {"kind": "hit", "at": 539.30, "whip": False},
    {"kind": "hit", "at": 917.30, "angle": 180},
    {"kind": "burst", "at": 1052.40},
    {"kind": "isolate", "at": 1219.30, "color": "#d01060", "similarity": 0.3},
    {"kind": "burst", "at": 1257.00},
    {"kind": "hit", "at": 1273.20},
]


def beats_of(items):
    return sum(it["beats"] for it in items if it.get("beats"))


def takes():
    out = []
    for name, (expected, items) in SECTIONS.items():
        got = beats_of(items)
        if expected is not None and got != expected:
            raise SystemExit(f"{name}: {got} beats, expected {expected}")
        out += items
    return out


def with_repeat(text: str, lines: tuple[int, int] = CHORUS_TWICE) -> str:
    """The text with non-empty lines a..b repeated right after b (as sung)."""
    rows = text.splitlines()
    ne = [i for i, r in enumerate(rows) if r.strip()]
    a, b = lines
    block = [rows[i] for i in ne[a - 1:b]]
    return "\n".join(rows[:ne[b - 1] + 1] + [""] + block + rows[ne[b - 1] + 1:]) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--episode", required=True)
    ap.add_argument("--song", required=True)
    ap.add_argument("--panels", required=True)
    ap.add_argument("--lyrics", default="")
    ap.add_argument("--plan-only", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    sung = lyrics_align.sung_words(args.song, os.path.join(args.out, "palabras cantadas.json"))
    bm = {"duration": render.ffmpeg_runner.probe_duration(args.song),
          "words": [{"start": w["start"], "end": w["end"]} for w in sung]}
    op = manga.MangaOptions(music_start=0.0, music_end=None, fps=30, grid=GRID, panels_folder=args.panels,
                            blur_cuts="mix", blur_cuts_from=DROP_S, blur_accent_every=4, composites=COMPOSITES)
    base = BASE
    if args.lyrics:
        with open(args.lyrics, encoding="utf-8-sig") as fh:
            words, report = lyrics_align.align(with_repeat(fh.read()), sung)
        print("letra:", report)
        op.words, op.word_style = words, dict(WORD_STYLE)
        base += " (con letra)"
        lyrics_align.srt(words, os.path.join(args.out, f"{BASE} - letra palabra por palabra.srt"))
    edl, info = manga.plan(args.episode, (1920, 1080), bm, takes(), op)
    info["srt"] = manga.srt_files(bm, op, args.out, BASE)
    with open(os.path.join(args.out, f"{base} - plan.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1)
    print("dur", info["duration"], "cuts", len(info["cuts"]), "panels", len(info["panels"]),
          "composites", info.get("composites", {}).get("placed"))
    if args.plan_only:
        return
    edl.music = args.song
    out = os.path.join(args.out, f"{base}.mp4")
    print(render.render(edl, out, None, os.path.join(args.out, "_render")))
    print(out)


if __name__ == "__main__":
    main()
