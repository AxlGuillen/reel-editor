"""The whole game transcribed, from the draft to the post-game analysis after the
nexus, on the scoreboard's clock, and an index of moments.

The narration (Ibai over the broadcast) is transcribed with the game's
vocabulary (vocab.py) and every word goes to the scoreboard's clock (before
0:00 it is the draft and the loading screen: negative clock, "-3:12"). The
index crosses the narration with the feed: a sentence with a keyword (robo,
steal, barón, dragón, red…) is a moment; if the feed has an event of that
kind within EVENT_WINDOW_S it goes with it, and the players and champions
of the game named in it are listed. The feed's events (fights, objectives,
the nexus) go in the index too, so it has everything in order. That is how
"el robo del red de Josedeodo" at 1:45 is found without a kill.

After the nexus the clock is "+m:ss" (time since the nexus: not to be mistaken
for the game's). There the index only looks for what a player is praised
(`elogio`) or blamed (`critica`) for, and that is searched in the WHOLE text
(draft, game and post-game): a player of the game named within PRAISE_WINDOW_S
of a word like "bestia" or "fatal", with the phrase, CONTEXT_S around it and
the VOD's seconds to cut the clip.
"""
from __future__ import annotations

import difflib
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
# What is said about a player (on the sentence without accents, lowercase).
PRAISE_WINDOW_S = 10.0
OPINIONS = {
    "elogio": r"\b(bestia\w*|imparable\w*|el mejor|la mejor|monstruo\w*|partidazo|mvp|increible\w*|brutal\w*"
              r"|top (1|uno)|destroz\w*)\b",
    "critica": r"\b(horrible\w*|fatal\w*|la (ha |han )?lia(do|ste)|la lio|regal\w*|error\w*|desastre\w*)\b",
}
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


def fmt_post(s: float) -> str:
    """Seconds since the nexus: "+1:23"."""
    s = max(0, int(round(s)))
    return f"+{s // 60}:{s % 60:02d}"


def _sound(word: str) -> str:
    return word.replace("y", "i")


def named_fuzzy(text: str, names: list[str]) -> list[str]:
    """The names said in the text, also misheard ("Mirwin" for Myrwn): a word
    of 5+ letters with the same first letter and a spelling 85 % alike (y = i).
    Shorter names ("Rest", "Kino") only match exactly: "resto" is a word."""
    plain = _plain(text)
    words = re.findall(r"\w+", plain)
    out = []
    for n in names:
        if not n:
            continue
        p = _plain(n)
        if re.search(rf"\b{re.escape(p)}\b", plain) or (len(p) >= 5 and any(
                w[:1] == p[:1] and abs(len(w) - len(p)) <= 2 and len(w) >= 4 and
                difflib.SequenceMatcher(None, _sound(w), _sound(p)).ratio() >= 0.85 for w in words)):
            out.append(n)
    return out


def named(text: str, names: list[str]) -> list[str]:
    plain = f" {_plain(text)} "
    return [n for n in names if n and re.search(rf"\b{re.escape(_plain(n))}\b", plain)]


def _when(seg: dict) -> float:
    """Where a segment is for "near": the VOD's seconds (real time) if it has them."""
    return seg["vod_s"] if "vod_s" in seg else seg["clock_s"]


def _opinions(segments: list[dict], players: list[str]) -> list[dict]:
    """elogio / critica: a player named within PRAISE_WINDOW_S of an opinion word."""
    mentions = [(seg, named_fuzzy(seg["text"], players)) for seg in segments]
    out = []
    for seg in segments:
        plain = _plain(seg["text"])
        for kind, rx in OPINIONS.items():
            if not re.search(rx, plain):
                continue
            near = [(abs(_when(s) - _when(seg)), s, ns) for s, ns in mentions
                    if ns and abs(_when(s) - _when(seg)) <= PRAISE_WINDOW_S]
            if not near:
                continue
            _, said, who = min(near, key=lambda x: x[0])      # the nearest sentence naming someone
            ctx = [x for x in segments if abs(_when(x) - _when(seg)) <= CONTEXT_S]
            for player in who:
                m = {"clock": seg.get("clock") or fmt_clock(seg["clock_s"]), "clock_s": round(seg["clock_s"], 2),
                     "source": "voz", "kind": kind, "player": player, "text": seg["text"].strip(),
                     "context": " ".join(x["text"].strip() for x in ctx),
                     "players": [player], "champions": [], "feed": None, "post": bool(seg.get("post"))}
                if "vod_s" in seg:
                    m.update(vod_s=seg["vod_s"], vod_from=min(x["vod_s"] for x in ctx),
                             vod_to=max(x.get("vod_end_s", x["vod_s"] + 5) for x in ctx))
                out.append(m)
    return out


def moments(segments: list[dict], events: list[dict], players: list[str], champions: list[str]) -> list[dict]:
    """The index: the narration's moments (with their feed event, if any) and
    the feed's events, by clock. segments: [{clock_s, text, [clock, vod_s,
    post]}]; events: [{clock_s, tipo, equipo, detalle, cantidad}]. A steal and
    the buff are often said in different sentences ("le quiere quitar el
    golem… se lo ha robado"): CONTEXT_S around a sentence counts as its
    context. The post-game (post=True) only has the opinions about players."""
    out = _opinions(segments, players)
    for seg in segments:
        if seg.get("post"):
            continue
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
                        **({"vod_s": seg["vod_s"]} if "vod_s" in seg else {}),
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
        opinion = m["kind"] in OPINIONS
        if dedup and m["source"] == "voz" and any(
                d["source"] == "voz" and d["kind"] == m["kind"] and d.get("player") == m.get("player")
                and m["clock_s"] - d["clock_s"] < (20 if opinion else 8)
                for d in dedup[-(12 if opinion else 4):]):
            continue
        dedup.append(m)
    return dedup


def text_file(segments: list[dict], title: str) -> str:
    lines = [title, ""]
    post = False
    for s in segments:
        if s.get("post") and not post:
            post = True
            lines += ["", "— Después del nexo (+m:ss = tiempo desde el nexo, no el reloj del juego) —", ""]
        lines.append(f"[{s.get('clock') or fmt_clock(s['clock_s'])}] {s['text'].strip()}")
    return "\n".join(lines) + "\n"


def to_game_clock(audio_t: float, vod_start: float, inicio: datetime, offset: float, to_clock) -> tuple[float, float]:
    """(vod second, clock second) of a second of the downloaded audio."""
    from datetime import timedelta
    vod = vod_start + audio_t
    return vod, to_clock(inicio + timedelta(seconds=vod - offset))
