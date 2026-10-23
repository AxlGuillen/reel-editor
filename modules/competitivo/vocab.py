"""Names right in Ibai's subtitles.

Whisper gets the names of the game wrong ("James" for Jinx, "Quillana" for
Qiyana). Two layers before anything is burned:

1. The game's vocabulary in Whisper's prompt: the teams, the 10 players and
   the 10 champions of THAT game (from the feed), as `initial_prompt` and
   `hotwords` of faster-whisper.
2. Known corrections (assets/vocab.json, misheard → right), applied to the
   transcribed words. Keys ignore case and accents and may be several words.

The third layer is a human (or Claude) review: /edit with review=1.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata

import config

_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def champion_name(champion_id: str) -> str:
    """The feed's id as it is said: "MissFortune" → "Miss Fortune"."""
    return _CAMEL.sub(" ", champion_id or "").strip()


def player_name(name: str, team_codes: tuple[str, ...]) -> str:
    """The feed's summoner name without the team prefix: "C9 Blaber" → "Blaber"."""
    parts = (name or "").split()
    if len(parts) > 1 and parts[0].upper() in {c.upper() for c in team_codes if c}:
        parts = parts[1:]
    return " ".join(parts)


def game_terms(roster: list[dict], teams: list[str]) -> dict:
    """{teams, players, champions} of a game, without repeats, in order."""
    def uniq(xs):
        return list(dict.fromkeys(x for x in xs if x))
    return {"teams": uniq(teams),
            "players": uniq(r.get("player", "") for r in roster),
            "champions": uniq(champion_name(r.get("champion", "")) for r in roster)}


def prompt(terms: dict) -> tuple[str, str]:
    """(initial_prompt, hotwords) for faster-whisper. The prompt reads like
    a caster's line so it also sets the register (Spanish, LoL)."""
    teams = " contra ".join(terms["teams"][:2]) if len(terms["teams"]) >= 2 else ", ".join(terms["teams"])
    parts = ["Partida de League of Legends en el Mundial"]
    if teams:
        parts[0] += f": {teams}"
    if terms["players"]:
        parts.append("Jugadores: " + ", ".join(terms["players"]))
    if terms["champions"]:
        parts.append("Campeones: " + ", ".join(terms["champions"]))
    words = terms["teams"] + terms["players"] + terms["champions"]
    return ". ".join(parts) + ".", " ".join(words)


def _key(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w' ]", "", text).strip()


def load(path: str | None = None) -> dict[str, str]:
    """Corrections of assets/vocab.json, as {normalized misheard: right}."""
    path = path or config.VOCAB_FILE
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return {_key(k): v for k, v in (data.get("corrections") or {}).items() if _key(k) and v}


_TRAIL = re.compile(r"[^\w'¿¡]+$")


def correct(words: list[dict], corrections: dict[str, str] | None = None) -> list[dict]:
    """The words with the known corrections applied. A key of several words
    replaces that run of words (the first one keeps the timing of all of
    them); the punctuation after the misheard word is kept."""
    corrections = load() if corrections is None else corrections
    if not corrections:
        return [dict(w) for w in words]
    longest = max(len(k.split()) for k in corrections)
    out: list[dict] = []
    i = 0
    while i < len(words):
        for n in range(min(longest, len(words) - i), 0, -1):
            run = words[i:i + n]
            key = _key(" ".join(w["word"] for w in run))
            if key in corrections:
                trail = _TRAIL.search(run[-1]["word"])
                out.append({**run[0], "word": corrections[key] + (trail.group(0) if trail else ""),
                            "end": run[-1]["end"]})
                i += n
                break
        else:
            out.append(dict(words[i]))
            i += 1
    return out
