"""Which cards a clip gets, over which champions and with what on their backs.

Per clip, once (no tracking): around the start of the play it looks at a few frames
of the clean zone, takes the one where the chosen players' health bars are best seen
(bars.py), ties each player to a bar with what the API says at that second (their
side and their health) and puts a card over each. What cannot be tied is reported
(`ambiguous`, `not_seen`) and can be fixed with `card_pos`: {"Canyon": "bar:2"} or
{"Canyon": [x, y]} (pixels of the preview image).

The numbers on the back come from the tournament's store (stats.py) and, where it
has none, from the game itself at that second: the API always has those.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

import config
from core import ffmpeg_runner
from modules.competitivo import bars, cards, engine, kills, layout, stats, vocab

ZONE_W = 1474                    # the clean zone as FFmpeg crops it (an even width)
WINDOW_S = 1.5                   # frames from lead ± this
FRAME_STEP_S = 0.5


@dataclass
class Placed:
    name: str                        # as the request wrote it
    status: str                      # ok | override | ambiguous | not_seen | not_in_game | no_room
    player: str = ""                 # the roster's name
    bar: int | None = None
    candidates: list[int] = field(default_factory=list)
    point: tuple[float, float] | None = None       # in the clean zone's pixels
    out: tuple[int, int, str] | None = None        # canvas top-left in the output and "above"/"below"
    scale: float = 1.0
    back: str = ""                   # where its numbers came from
    photo: str = "none"              # url | assets | none: where the photo came from
    photo_error: str = ""            # why a photo link did not work
    card: cards.Card | None = None


@dataclass
class Plan:
    t: float = 0.0                   # second of the span file where it freezes
    placed: list[Placed] = field(default_factory=list)
    frame: np.ndarray | None = None
    bars: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> list[Placed]:
        return [p for p in self.placed if p.out is not None]

    def info(self) -> dict:
        return {"t": round(self.t, 2), "notes": self.notes,
                "cards": [{"name": p.name, "player": p.player, "status": p.status, "bar": p.bar,
                           "candidates": p.candidates, "back": p.back, "photo": p.photo,
                           **({"photo_error": p.photo_error} if p.photo_error else {}),
                           "point": [round(p.point[0]), round(p.point[1])] if p.point else None,
                           "where": p.out[2] if p.out else None} for p in self.placed]}


# --- The request ---------------------------------------------------------------------
def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower())
    return re.sub(r"[^a-z0-9]+", "", "".join(c for c in text if unicodedata.category(c) != "Mn"))


def parse_names(raw) -> list[str]:
    """"Canyon, Rookie" / a JSON list / a list → ["Canyon", "Rookie"]."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith("["):
            try:
                raw = json.loads(raw)
            except ValueError:
                raise ValueError("'cards' va como nombres separados por coma o como lista JSON.")
        else:
            raw = [x for x in re.split(r"[,;\n]+", raw)]
    out = [str(x).strip() for x in raw if str(x).strip()]
    return list(dict.fromkeys(out))


def roster_of(partida: dict) -> list[dict]:
    """The 10 players of a game: [{player, team, champion, side, role, pid}] (the roster is in participant order)."""
    out = []
    for k, r in enumerate(partida.get("roster") or []):
        out.append({"player": r.get("player", ""), "team": r.get("team", ""), "champion": r.get("champion", ""),
                    "side": "blue" if k < 5 else "red", "role": cards.ROLES.get(k % 5, ""), "pid": k + 1})
    return out


def match_player(name: str, roster: list[dict]) -> dict | None:
    """The roster's entry a name in the request means: the same name, its start, or a close spelling."""
    key = _plain(name)
    for r in roster:
        if _plain(r["player"]) == key:
            return r
    starts = [r for r in roster if key and (_plain(r["player"]).startswith(key) or key.startswith(_plain(r["player"])))
              and min(len(key), len(_plain(r["player"]))) >= 3]
    if len(starts) == 1:
        return starts[0]
    from modules.competitivo import transcript
    close = [r for r in roster if r["player"] in transcript.named_fuzzy(name, [r["player"]])]
    return close[0] if len(close) == 1 else None


def wanted_in_clip(names: list[str], roster: list[dict], clip: dict, scope: str) -> tuple[list[tuple[str, dict]], list[Placed]]:
    """Who gets a card in this clip: [(name, roster entry)] and the ones left out (`not_in_game`).
    scope "kills": only whoever made a kill in the fight; "all": anyone of the game."""
    authors = {_plain((a or {}).get("player", "")) for a in (clip.get("authors") or []) if a}
    chosen, left = [], []
    for name in names:
        entry = match_player(name, roster)
        if entry is None:
            continue                                   # of another game of the series
        if scope == "kills" and _plain(entry["player"]) not in authors:
            left.append(Placed(name, "not_in_fight", entry["player"]))
            continue
        chosen.append((name, entry))
    return chosen, left


# --- The frames ---------------------------------------------------------------------
def grab_frames(span: str, t0: float, t1: float, step: float = FRAME_STEP_S) -> list[tuple[float, np.ndarray]]:
    """The clean zone every `step` s of [t0, t1] of the span: [(t, BGR frame)]."""
    t0 = max(0.0, t0)
    jx, jy, jw, jh = layout.JUEGO
    raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{max(step, t1 - t0):.3f}",
                                 "-i", span, "-an", "-vf", f"fps={1 / step},crop={ZONE_W}:{jh}:{jx}:{jy}",
                                 "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], timeout=120)
    size = ZONE_W * jh * 3
    n = len(raw) // size
    arr = np.frombuffer(raw[: n * size], np.uint8).reshape(n, jh, ZONE_W, 3)
    return [(t0 + k * step, arr[k]) for k in range(n)]


def api_players(game_id: str, utc: datetime) -> dict[int, dict]:
    """What the API says of each player around `utc`: {participantId: level, hp, maxhp, k, d, a, cs, gold}."""
    data = kills._get(f"{engine.FEED}/window/{game_id}?startingTime={engine.iso10(utc)}")
    frames = (data or {}).get("frames") or []
    if not frames:
        return {}
    best = min(frames, key=lambda f: abs((engine.parse_ts(f["rfc460Timestamp"]) - utc).total_seconds()))
    out = {}
    for side in ("blueTeam", "redTeam"):
        for p in best[side].get("participants", []):
            out[p["participantId"]] = {"level": p.get("level", 0), "hp": p.get("currentHealth"),
                                       "maxhp": p.get("maxHealth"), "k": p.get("kills", 0), "d": p.get("deaths", 0),
                                       "a": p.get("assists", 0), "cs": p.get("creepScore", 0),
                                       "gold": p.get("totalGold", 0)}
    return out


# --- The back of a card -----------------------------------------------------------------
def back_rows(entry: dict, state: dict | None, tournament: dict, mode: str) -> tuple[str, str, list[cards.Row], str]:
    """(source, title, rows, caption) of a card's back. Auto goes down from the pair (2+ games together)
    to the champion, the player and, always there, this game."""
    champ = vocab.champion_name(entry["champion"]) or entry["champion"]
    player = entry["player"]

    def wl(a):
        return f"{a['wins']}-{a['losses']}"
    options = []
    pair, ch, me = tournament.get("pair"), tournament.get("champion"), tournament.get("player")
    if pair and pair["games"] >= 2:
        options.append(("pareja", f"{player} · {champ}", [
            cards.Row(wl(pair), f"V-D con {champ}", pair["wins"] / pair["games"]),
            cards.Row(f"KDA {pair['kda']}", "promedio con él", min(1.0, pair["kda"] / 6)),
            cards.Row(str(pair["games"]), "partidas con él", min(1.0, pair["games"] / 5))]))
    if ch and ch["games"] >= 1:
        options.append(("campeon", f"{champ} · torneo", [
            cards.Row(wl(ch), f"V-D de {champ}", ch["wins"] / ch["games"]),
            cards.Row(f"KDA {ch['kda']}", "promedio del campeón", min(1.0, ch["kda"] / 6)),
            cards.Row(str(ch["games"]), "picks en el torneo", min(1.0, ch["games"] / 15))]))
    if me and me["games"] >= 1:
        options.append(("jugador", player, [
            cards.Row(wl(me), "V-D en el torneo", me["wins"] / me["games"]),
            cards.Row(f"KDA {me['kda']}", "promedio del jugador", min(1.0, me["kda"] / 6)),
            cards.Row(str(me["games"]), "partidas jugadas", min(1.0, me["games"] / 8))]))
    if state:
        options.append(("partida", f"{player} · {champ}", [
            cards.Row(f"{state['k']}/{state['d']}/{state['a']}", "en esta partida"),
            cards.Row(f"CS {state['cs']}", "farmeo"),
            cards.Row(f"NIV {state['level']}", f"{state['gold'] / 1000:.1f}K de oro", min(1.0, state["level"] / 18))]))
    if not options:
        options.append(("partida", f"{player} · {champ}", [cards.Row(champ.upper() or "—", entry["role"] or "jugador")]))
    names = {o[0]: o for o in options}
    pick = names.get(mode) if mode != "auto" else None
    src, title, rows = pick or options[0]
    return src, title, rows, ""


def build_card(name: str, entry: dict, opts, state: dict | None, tournament: dict, caption: str = "") -> tuple[cards.Card, str, str, str]:
    """The card of one player, from the roster, the game's numbers and what the request set (`card_photos`,
    `card_data`: the model fills the photo by link and can overwrite any text). Returns (card, where the back's
    numbers came from, where the photo came from, why a photo link failed — "" if it did not)."""
    data, photos = opts.data or {}, opts.photos or {}
    mine = {k: v for k, v in (data.get(name) or data.get(entry["player"]) or {}).items() if v}
    link = photos.get(name) or photos.get(entry["player"])
    if link and "photo" not in mine:
        mine["photo"] = link
    e = {**entry, **{k: mine[k] for k in ("team", "side", "role", "champion") if k in mine}}
    photo, photo_from, why = None, "none", ""
    if mine.get("photo"):
        photo, why = cards.fetch_image(mine["photo"])
        photo_from = "url" if photo else "none"
    if not photo:
        photo = cards.photo_path(entry["player"])
        photo_from = "assets" if photo else "none"
    art = cards.fetch_image(mine["art"])[0] if mine.get("art") else None
    icon = cards.fetch_image(mine["icon"])[0] if mine.get("icon") else None
    src, title, rows, _ = back_rows(e, state, stats.lookup(e["player"], e["champion"], tournament) if tournament
                                    else stats.lookup(e["player"], e["champion"]), opts.back)
    foot = ""
    custom = mine.get("back")
    if custom:
        src, title, foot = "datos", custom.get("title") or title, custom.get("caption") or ""
        rows = [cards.Row(str(r["big"]), str(r["label"]), r.get("bar")) for r in custom["rows"]]
    card = cards.Card(e["player"], e["team"], e["side"], e["role"], e["champion"], photo=photo, photo_from=photo_from,
                      icon=icon or cards.champion_icon(e["champion"]), art=art or cards.champion_art(e["champion"]),
                      title=mine.get("title") or title, rows=rows, source=src, caption=foot or caption)
    return card, src, photo_from, why


def entry_from_data(name: str, info: dict) -> dict | None:
    """A roster entry made only of what the request says (no phase 1 to take the roster from): needs the
    player's team, side, role and champion in `card_data`."""
    if not all(info.get(k) for k in ("team", "side", "role", "champion")):
        return None
    return {"player": name, "team": info["team"], "champion": info["champion"], "side": info["side"],
            "role": info["role"], "pid": 0}


def preview_cards(names: list[str], roster: list[dict], opts, caption: str = "") -> tuple[list[cards.Card], list[dict]]:
    """The cards of `names` as a render would make them, without any video: (cards, one report each). A name
    that is neither in the roster nor fully described by `card_data` is reported, not guessed."""
    out, report = [], []
    for name in names:
        info = (opts.data or {}).get(name) or {}
        entry = match_player(name, roster) if roster else None
        entry = entry or entry_from_data(name, info)
        if entry is None:
            report.append({"name": name, "ok": False,
                           "error": "no está en la partida y 'card_data' no trae team, side, role y champion"})
            continue
        card, src, photo_from, why = build_card(name, entry, opts, None, {}, caption)
        out.append(card)
        report.append({"name": name, "ok": True, "player": card.player, "team": card.team, "side": card.side,
                       "role": card.role, "champion": card.champion, "back": src, "photo": photo_from,
                       "has_art": bool(card.art), "title": card.title,
                       "rows": [{"big": r.big, "label": r.label} for r in card.rows],
                       **({"photo_error": why} if why else {})})
    return out, report


# --- The plan ----------------------------------------------------------------------------
def plan_clip(span: str, an, t_kill: float, clip: dict, partida: dict, names: list[str], opts, zoom: float,
              split: list, track: list | None, utc_at, caption: str = "", frames=None, api=None,
              tournament=None) -> Plan:
    """The cards of one clip. `opts`: schema.CardParams. `utc_at(t)`: the UTC of second t of the span.
    (frames / api / tournament: replaceable, for the tests.)"""
    plan = Plan()
    roster = roster_of(partida)
    chosen, left = wanted_in_clip(names, roster, clip, opts.scope)
    plan.placed += left
    if not chosen:
        return plan
    centre = max(an.vivo_desde + WINDOW_S, t_kill - opts.lead_s)
    lo, hi = max(an.vivo_desde + 0.3, centre - WINDOW_S), min(an.vivo_hasta - 0.5, centre + WINDOW_S)
    got = (frames or grab_frames)(span, lo, max(hi, lo + FRAME_STEP_S))
    if not got:
        plan.notes.append("No se pudo leer ningún cuadro para las tarjetas.")
        plan.placed += [Placed(n, "not_seen", e["player"]) for n, e in chosen]
        return plan
    sides = {e["side"] for _, e in chosen}
    scored = []
    for t, fr in got:
        found = bars.detect(fr)
        score = len(found) + 5 * sum(1 for s in sides if any(b.side == s for b in found))
        scored.append((score, t, fr, found))
    score, t, fr, found = max(scored, key=lambda x: (x[0], x[1]))
    plan.t, plan.frame, plan.bars = t, fr, found
    state = (api or api_players)(partida["game_id"], utc_at(t))
    wanted = {}
    for name, e in chosen:
        mine = state.get(e["pid"]) or {}
        frac = (mine["hp"] / mine["maxhp"]) if mine.get("hp") is not None and mine.get("maxhp") else None
        rivals = [(s["hp"] / s["maxhp"]) for pid, s in state.items()
                  if pid != e["pid"] and (pid <= 5) == (e["side"] == "blue") and s.get("hp") and s.get("maxhp")]
        label = lambda r: bars.predicted_width(f"{r['team']} {r['player']}")  # noqa: E731
        wanted[name] = {"side": e["side"], "fraction": frac, "rivals": rivals, "name_w": label(e),
                        "others_w": [label(r) for r in roster if r["side"] == e["side"] and r["pid"] != e["pid"]]}
    found_by = bars.identify(found, wanted)
    tour = tournament or {}
    for name, e in chosen:
        res = found_by[name]
        placed = Placed(name, res["status"], e["player"], candidates=res["candidates"])
        bar = res["bar"]
        override = (opts.pos or {}).get(name) or (opts.pos or {}).get(e["player"])
        if override is not None:
            if isinstance(override, str) and override.startswith("bar:"):
                n = int(override[4:] or 0)
                bar = next((b for b in found if b.n == n), None)
                placed.status = "override" if bar else "not_seen"
            elif isinstance(override, (list, tuple)) and len(override) == 2:
                placed.point, placed.status = (float(override[0]), float(override[1])), "override"
                bar = None
        if bar is not None:
            placed.bar, placed.point = bar.n, tuple(float(v) for v in bar.top_center)
        if placed.point is not None:
            sx, sy = placed.point[0] + layout.JUEGO[0], placed.point[1] + layout.JUEGO[1]
            x, y, visible = layout.output_point(sx, sy, zoom, t, split, track)
            sc = opts.scale * (0.78 if split and any(a <= t <= b for a, b in split) else 1.0)
            if visible:
                px, py, where = cards.place((x, y), sc, layout.ARRIBA_H, layout.ARRIBA_H + layout.JUEGO_H)
                placed.out, placed.scale = (px, py, where), sc
                placed.card, placed.back, placed.photo, placed.photo_error = build_card(
                    name, e, opts, state.get(e["pid"]), tour, caption)
            else:
                placed.status = "no_room"
        plan.placed.append(placed)
    return plan


def preview_image(plan: Plan, marks: bool = True) -> np.ndarray | None:
    """The chosen frame with every bar numbered (and who got it), and the labels enlarged below."""
    import cv2
    if plan.frame is None:
        return None
    named = {p.bar: p.player or p.name for p in plan.placed if p.bar is not None}
    img = bars.annotate(plan.frame, plan.bars, named if marks else None)
    labels = bars.label_crops(plan.frame, plan.bars)
    h = max(img.shape[0], labels.shape[0])
    pad = lambda a: cv2.copyMakeBorder(a, 0, h - a.shape[0], 0, 0, cv2.BORDER_CONSTANT)  # noqa: E731
    return np.hstack([pad(img), pad(labels)])
