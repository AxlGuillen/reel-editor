"""The whole game transcribed, from the draft to the nexus, on the scoreboard's
clock, and an index of moments.

The narration (Ibai over the broadcast) is transcribed with the game's
vocabulary (vocab.py) and every word goes to the scoreboard's clock (before
0:00 it is the draft and the loading screen: negative clock, "-3:12"). The
index crosses the narration with the feed: a sentence with a keyword (robo,
steal, barón, dragón, red…) is a moment; if the feed has an event of that
kind within EVENT_WINDOW_S it goes with it, and the players and champions
of the game named in it are listed. The feed's events (fights, objectives,
the nexus) go in the index too, so it has everything in order. That is how
"el robo del red de Josedeodo" at 1:45 is found without a kill.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

EVENT_WINDOW_S = 25.0
CONTEXT_S = 12.0
# kind → pattern (on the sentence without accents, lowercase)
KEYWORDS = {
    "robo": r"\b(rob[ao]\w*|robad\w*|steal\w*|stil\w*|quit[aoó]\w*)\b",
    "baron": r"\b(baron|nashor)\b",
    "dragon": r"\b(dragon\w*|drake\w*|alma|anciano)\b",
    "heraldo": r"\b(heraldo|larvas?|grubs?|voidgrubs?|atakhan)\b",
    "buff": r"\b(red|rojo|blue|azul|buffs?|bufos?|golem|campamentos?)\b",
    "multikill": r"\b(pentakill|penta|quadra|cuadra|triple|ace|shutdown)\b",
    "torre": r"\b(torre|inhibidor|inhib)\b",
    "nexo": r"\b(nexo|ggs?|se acabo)\b",
}
# which feed events go with each kind of the narration
FEED_KINDS = {"baron": {"baron"}, "dragon": {"dragon", "dragon_anciano"}, "torre": {"torre", "inhibidor"},
              "nexo": {"fin"}, "multikill": {"pelea", "kill", "primera_sangre"},
              "robo": {"baron", "dragon", "dragon_anciano"}}
LABELS = {"robo": "robo", "baron": "barón", "dragon": "dragón", "heraldo": "heraldo/larvas", "buff": "buff",
          "multikill": "multikill", "torre": "torre", "nexo": "nexo"}


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def fmt_clock(s: float) -> str:
    sign = "-" if s < 0 else ""
    s = abs(int(round(s)))
    return f"{sign}{s // 60}:{s % 60:02d}"


def kinds_of(text: str) -> list[str]:
    plain = _plain(text)
    found = [k for k, rx in KEYWORDS.items() if re.search(rx, plain)]
    # "robo" + a buff word: a stolen buff ("le roba el red")
    if "robo" in found and "buff" in found:
        found = ["robo_buff"] + [k for k in found if k not in ("robo", "buff")]
    elif "buff" in found:
        found.remove("buff")                         # "red" alone is too common to be a moment
    return found


def named(text: str, names: list[str]) -> list[str]:
    plain = f" {_plain(text)} "
    return [n for n in names if n and re.search(rf"\b{re.escape(_plain(n))}\b", plain)]


def moments(segments: list[dict], events: list[dict], players: list[str], champions: list[str]) -> list[dict]:
    """The index: the narration's moments (with their feed event, if any) and
    the feed's events, by clock. segments: [{clock_s, text}]; events:
    [{clock_s, tipo, equipo, detalle, cantidad}]. A steal and the buff are
    often said in different sentences ("le quiere quitar el golem… se lo ha
    robado"): CONTEXT_S around a sentence counts as its context."""
    out = []
    for seg in segments:
        near = [x for x in segments if abs(x["clock_s"] - seg["clock_s"]) <= CONTEXT_S]
        context = " ".join(x["text"] for x in near)
        found = kinds_of(seg["text"])
        if "robo" in found and "robo_buff" in kinds_of(context):
            found = ["robo_buff" if k == "robo" else k for k in found]
        for kind in found:
            feed_kinds = FEED_KINDS.get(kind.replace("_buff", ""), set())
            near = [e for e in events if e["tipo"] in feed_kinds and abs(e["clock_s"] - seg["clock_s"]) <= EVENT_WINDOW_S]
            ev = min(near, key=lambda e: abs(e["clock_s"] - seg["clock_s"])) if near else None
            label = "robo de buff" if kind == "robo_buff" else LABELS.get(kind, kind)
            out.append({"clock": fmt_clock(seg["clock_s"]), "clock_s": round(seg["clock_s"], 2), "source": "voz",
                        "kind": label, "text": seg["text"].strip(),
                        "context": context.strip() if kind == "robo_buff" else None,
                        "players": named(context if kind == "robo_buff" else seg["text"], players),
                        "champions": named(seg["text"], champions),
                        "feed": {"clock": fmt_clock(ev["clock_s"]), "tipo": ev["tipo"], "equipo": ev["equipo"],
                                 "detalle": ev.get("detalle", "")} if ev else None})
    for e in events:
        out.append({"clock": fmt_clock(e["clock_s"]), "clock_s": round(e["clock_s"], 2), "source": "feed",
                    "kind": e["tipo"], "text": f"{e['equipo']} · {e.get('detalle') or e['tipo']}"
                                               + (f" ({e['cantidad']})" if e.get("cantidad", 1) > 1 else ""),
                    "players": [], "champions": [], "feed": None})
    # The same moment said in two sentences in a row: only the first.
    out.sort(key=lambda m: (m["clock_s"], m["source"] != "feed"))
    dedup = []
    for m in out:
        if dedup and m["source"] == "voz" and any(
                d["source"] == "voz" and d["kind"] == m["kind"] and m["clock_s"] - d["clock_s"] < 8 for d in dedup[-4:]):
            continue
        dedup.append(m)
    return dedup


def text_file(segments: list[dict], title: str) -> str:
    lines = [title, ""]
    lines += [f"[{fmt_clock(s['clock_s'])}] {s['text'].strip()}" for s in segments]
    return "\n".join(lines) + "\n"


def to_game_clock(audio_t: float, vod_start: float, inicio: datetime, offset: float, to_clock) -> tuple[float, float]:
    """(vod second, clock second) of a second of the downloaded audio."""
    from datetime import timedelta
    vod = vod_start + audio_t
    return vod, to_clock(inicio + timedelta(seconds=vod - offset))
