"""The long anime edit (1–4 min, the whole song) from a plan file.

The engine of the video type; each video is a plan (JSON), not a script.
Template with a real example: anime_edit/examples/long_edit_magna_vs_dante.json
(07/11/2026: the whole Glee "I Will Survive" over Black Clover episode 175).
The recipe (how to choose the takes, the grid, the lyrics, what was learned)
is in the PKB: worlds-2026/receta-edit-largo-anime.md.

    python -m modules.anime_edit.long_edit <plan.json> <out folder> --episode <video> --song <song>
           --panels <manga folder> [--lyrics letra.txt] [--plan-only]

Plan keys:
  base_name, fps, grid {bpm, phase}   the grid MEASURED on the song (not the tracker's BPM)
  sections [{name, beats, items}]     items = manga takes ({t, beats, frame, tone, focus, speed})
                                      or panels ({panel, beats}); `beats` checks the section's sum
                                      (null on the last section: it fills to the end of the song)
  composites                          [{kind: hit|burst|isolate, at: video s, …}] (manga.MangaOptions)
  blur_cuts, blur_cuts_from, blur_accent_every
  word_style                          the invert subtitles (font, inv_size, inv_y, inv_words)
  repeat_lines [[a, b]]               non-empty lyric lines sung twice in a row (repeated in the text)
"""
from __future__ import annotations

import argparse
import json
import os

from core import ffmpeg_runner
from modules.anime_edit import manga
from modules.editing import lyrics_align, render


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def takes(plan: dict) -> list[dict]:
    """The flat item list, with each section's beats checked."""
    out = []
    for sec in plan["sections"]:
        got = sum(it.get("beats") or 0 for it in sec["items"])
        if sec.get("beats") is not None and got != sec["beats"]:
            raise ValueError(f"La sección «{sec['name']}» suma {got} beats y declara {sec['beats']}.")
        out += sec["items"]
    return out


def with_repeats(text: str, repeats: list) -> str:
    """The lyrics with each block of non-empty lines a..b repeated right after
    b, as sung (later blocks first, so earlier line numbers stay valid)."""
    rows = text.splitlines()
    for a, b in sorted((tuple(r) for r in repeats or []), reverse=True):
        ne = [i for i, r in enumerate(rows) if r.strip()]
        block = [rows[i] for i in ne[a - 1:b]]
        rows = rows[:ne[b - 1] + 1] + [""] + block + rows[ne[b - 1] + 1:]
    return "\n".join(rows) + "\n"


def options(plan: dict, panels: str) -> manga.MangaOptions:
    return manga.MangaOptions(music_start=0.0, music_end=None, fps=int(plan.get("fps", 30)), grid=plan.get("grid"),
                              panels_folder=panels, blur_cuts=plan.get("blur_cuts", "off"),
                              blur_cuts_from=float(plan.get("blur_cuts_from", 0.0)),
                              blur_accent_every=int(plan.get("blur_accent_every", 4)),
                              composites=plan.get("composites", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("out")
    ap.add_argument("--episode", required=True)
    ap.add_argument("--song", required=True)
    ap.add_argument("--panels", required=True)
    ap.add_argument("--lyrics", default="")
    ap.add_argument("--plan-only", action="store_true")
    args = ap.parse_args()
    plan = load(args.plan)
    os.makedirs(args.out, exist_ok=True)
    sung = lyrics_align.sung_words(args.song, os.path.join(args.out, "palabras cantadas.json"))
    bm = {"duration": ffmpeg_runner.probe_duration(args.song),
          "words": [{"start": w["start"], "end": w["end"]} for w in sung]}
    op = options(plan, args.panels)
    base = name = plan.get("base_name", "edit largo")
    if args.lyrics:
        with open(args.lyrics, encoding="utf-8-sig") as fh:
            words, report = lyrics_align.align(with_repeats(fh.read(), plan.get("repeat_lines")), sung)
        print("letra:", report)
        op.words, op.word_style = words, dict(plan.get("word_style", {}))
        name += " (con letra)"
        lyrics_align.srt(words, os.path.join(args.out, f"{base} - letra palabra por palabra.srt"))
    edl, info = manga.plan(args.episode, ffmpeg_runner.probe_resolution(args.episode) or (1920, 1080), bm,
                           takes(plan), op)
    info["srt"] = manga.srt_files(bm, op, args.out, base)
    with open(os.path.join(args.out, f"{name} - plan.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1)
    print("dur", info["duration"], "cuts", len(info["cuts"]), "panels", len(info["panels"]),
          "composites", info.get("composites", {}).get("placed"))
    if args.plan_only:
        return
    edl.music = args.song
    out = os.path.join(args.out, f"{name}.mp4")
    print(render.render(edl, out, None, os.path.join(args.out, "_render")))
    print(out)


if __name__ == "__main__":
    main()
