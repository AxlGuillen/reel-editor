"""A song's lyrics (Axl's text) on the times they are sung.

Whisper hears the sung words but writes them its own way; Axl's text is the
right one. `align` matches both word lists in order (difflib over normalized
words) and gives each lyric word the time of the sung word it matched; the
words in between get times spread between their matched neighbours (only in
a gap shorter than FILL_GAP_S). Backing vocals written in parentheses
("(what?)") help the match but are never shown.

`sung_words` transcribes the song with every word timed. Whisper's voice
filter (VAD) drops most of a sung mix: with it, "I Will Survive" (Glee) gave
33 words; without it, 444.
"""
from __future__ import annotations

import difflib
import json
import os
import re

FILL_GAP_S = 4.0
UNCOVERED_MIN = 6             # sung words in a row that the text does not have: a part missing from it

_TOKEN = re.compile(r"\([^)]*\)|\S+")


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower().replace("’", "'"))


def align(text: str, sung: list[dict]) -> tuple[list[dict], dict]:
    """([{word, start, end}], report). `sung` = [{text, start, end}] in song s."""
    toks = _TOKEN.findall(text)
    adlib = [t.startswith("(") for t in toks]
    words = [t.strip("()") for t in toks]
    sm = difflib.SequenceMatcher(None, [_norm(w) for w in words], [_norm(w["text"]) for w in sung], autojunk=False)
    times: list[tuple[float, float] | None] = [None] * len(words)
    used = set()
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            times[blk.a + k] = (sung[blk.b + k]["start"], sung[blk.b + k]["end"])
            used.add(blk.b + k)
    out: list[dict] = []
    i = 0
    while i < len(words):
        if times[i] is not None:
            if not adlib[i]:
                out.append({"word": words[i], "start": times[i][0], "end": times[i][1]})
            i += 1
            continue
        j = i
        while j < len(words) and times[j] is None:
            j += 1
        prev = times[i - 1][1] if i > 0 and times[i - 1] else None
        nxt = times[j][0] if j < len(words) and times[j] else None
        if prev is not None and nxt is not None and 0 < nxt - prev < FILL_GAP_S:
            step = (nxt - prev) / (j - i)
            for k in range(i, j):
                if not adlib[k]:
                    s = prev + (k - i) * step
                    out.append({"word": words[k], "start": round(s, 3), "end": round(s + step * 0.9, 3)})
        i = j
    uncovered, run = [], []
    for n, w in enumerate(sung + [None]):
        if w is not None and n not in used:
            run.append(w)
            continue
        if len(run) >= UNCOVERED_MIN:
            uncovered.append([round(run[0]["start"], 2), round(run[-1]["end"], 2), len(run)])
        run = []
    matched = sum(t is not None for t in times)
    report = {"lyric_words": len(words), "matched": matched, "sung_words": len(sung),
              "sung_covered": round(len(used) / max(1, len(sung)), 3), "timed": len(out),
              "uncovered": uncovered}
    return out, report


def sung_words(song: str, cache: str | None = None, language: str = "en",
               model: str = "large-v3-turbo") -> list[dict]:
    """[{text, start, end}] of the song, every word timed, without the voice
    filter. Cached in `cache` (a .json) when given."""
    if cache and os.path.isfile(cache):
        with open(cache, encoding="utf-8") as fh:
            return json.load(fh)
    from modules.subtitles import processor as subtitles     # registers the CUDA DLLs on Windows
    device, compute = subtitles._get_device()
    try:
        m = subtitles._get_model(model, device, compute)
        segs, _ = m.transcribe(song, language=language, word_timestamps=True, vad_filter=False,
                               condition_on_previous_text=False, no_speech_threshold=0.9)
        segs = list(segs)
    except RuntimeError:
        m = subtitles._get_model(model, "cpu", "int8")
        segs, _ = m.transcribe(song, language=language, word_timestamps=True, vad_filter=False,
                               condition_on_previous_text=False, no_speech_threshold=0.9)
        segs = list(segs)
    out = [{"text": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)}
           for s in segs for w in (s.words or [])]
    if cache:
        os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False)
    return out


def srt(words: list[dict], path: str) -> str:
    """One word per cue (at least 0.15 s on screen)."""
    def ts(x: float) -> str:
        ms = int(round(max(0.0, x) * 1000))
        h, ms = divmod(ms, 3_600_000)
        m, ms = divmod(ms, 60_000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    with open(path, "w", encoding="utf-8") as fh:
        for i, w in enumerate(words, 1):
            fh.write(f"{i}\n{ts(w['start'])} --> {ts(max(w['end'], w['start'] + 0.15))}\n{w['word']}\n\n")
    return path
