"""The official feed around a fight (lolesports livestats), in the clock of
the scoreboard.

The `window` endpoint has, for every player, HP (currentHealth/maxHealth,
updated about once a second), level, gold, K/D/A and CS; `details` has items,
abilities, wards and championDamageShare. The feed has NO positions. Kill
events show up 0.5–1 s after the video.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from modules.competitivo import clock, engine
from modules.competitivo.kills import _get

WINDOW_STEP_S = 10            # one window request every 10 s covers the game (each returns ~10 s of frames)
MAX_MINUTES = 70
DETAILS_STEP_S = 5
PLAYER_KEYS = ("level", "kills", "deaths", "assists", "totalGold", "creepScore", "currentHealth", "maxHealth")
TEAM_KEYS = ("totalGold", "totalKills", "towers", "inhibitors", "barons", "dragons")


def game_frames(game_id: str, workers: int = 16) -> tuple[dict, list[dict], dict]:
    """(who, frames, metadata) of the whole game: every window frame, sorted.
    who = {participantId: {name, champion, role, side}}."""
    first = _get(f"{engine.FEED}/window/{game_id}")
    if not first or not first.get("frames"):
        raise RuntimeError(f"El feed no tiene datos de la partida {game_id}.")
    meta = first["gameMetadata"]
    start = engine.parse_ts(first["frames"][0]["rfc460Timestamp"])
    times = [start + timedelta(seconds=WINDOW_STEP_S * k) for k in range(MAX_MINUTES * 60 // WINDOW_STEP_S)]
    raw = {}
    with ThreadPoolExecutor(workers) as ex:
        for data in ex.map(lambda t: _get(f"{engine.FEED}/window/{game_id}?startingTime={engine.iso10(t)}"), times):
            for f in (data or {}).get("frames", []):
                raw[f["rfc460Timestamp"]] = f
    frames = sorted(raw.values(), key=lambda f: f["rfc460Timestamp"])
    who = {}
    for key, side in (("blueTeamMetadata", "blue"), ("redTeamMetadata", "red")):
        for p in meta[key]["participantMetadata"]:
            who[p["participantId"]] = {"name": p["summonerName"], "champion": p["championId"],
                                       "role": p.get("role"), "side": side}
    return who, frames, meta


def fight(game_id: str, clock0_utc: str, clock_from: float, clock_to: float, details: bool = True) -> dict:
    """The fight's dossier: per frame (clock, teams, players) from clock_from
    − 1 s to clock_to + 1 s, and a `details` frame every DETAILS_STEP_S.
    clock0_utc: when the scoreboard read 0:00 (competitivo's calibration)."""
    who, frames, _ = game_frames(game_id)
    efs = [engine.Frame(engine.parse_ts(f["rfc460Timestamp"]), f["gameState"], engine._team(f["blueTeam"]),
                        engine._team(f["redTeam"])) for f in frames]
    paused = clock.pauses(efs)
    c0 = engine.parse_ts(clock0_utc)
    rows = []
    for f in frames:
        c = clock.to_clock(engine.parse_ts(f["rfc460Timestamp"]), c0, paused)
        if not clock_from - 1 <= c <= clock_to + 1:
            continue
        row = {"clock": round(c, 2), "fmt": clock.fmt(c), "state": f["gameState"], "teams": {}, "players": {}}
        for side in ("blueTeam", "redTeam"):
            t = f[side]
            row["teams"][side] = {k: t.get(k) for k in TEAM_KEYS}
            for p in t["participants"]:
                row["players"][str(p["participantId"])] = {k: p.get(k) for k in PLAYER_KEYS}
        rows.append(row)
    out = {"game_id": game_id, "clock0_utc": clock0_utc, "who": {str(k): v for k, v in who.items()},
           "frames": rows, "details": [], "pauses": [[a.isoformat(), (b - a).total_seconds()] for a, b in paused]}
    if details:
        stamps, t, end = [], clock.from_clock(clock_from, c0, paused), clock.from_clock(clock_to, c0, paused)
        while t <= end:
            stamps.append(t)
            t += timedelta(seconds=DETAILS_STEP_S)
        with ThreadPoolExecutor(8) as ex:
            for data in ex.map(lambda x: _get(f"{engine.FEED}/details/{game_id}?startingTime={engine.iso10(x)}"),
                               stamps):
                fr = (data or {}).get("frames") or []
                if fr:
                    d = fr[0]
                    out["details"].append({"clock": round(clock.to_clock(engine.parse_ts(d["rfc460Timestamp"]), c0,
                                                                         paused), 2),
                                           "participants": d.get("participants")})
    return out


def hp(p: dict) -> int:
    return round(100 * (p.get("currentHealth") or 0) / max(1, p.get("maxHealth") or 1))


def timeline(d: dict) -> dict:
    """What happened in the fight, from its dossier: the state at the start,
    every K/D/A change (who died, who killed, who assisted) and the HP of the
    10 once per second. Pure: the tests cover it."""
    who = d["who"]
    fr = d["frames"]
    if not fr:
        return {"start": None, "events": [], "hp": []}
    order = sorted(who, key=lambda p: (who[p]["side"], int(p)))
    start = {"clock": fr[0]["clock"], "teams": fr[0]["teams"],
             "players": {p: {**fr[0]["players"][p], "hp": hp(fr[0]["players"][p])} for p in order}}
    events, prev = [], fr[0]
    for f in fr[1:]:
        ch = {k: [p for p in order if f["players"][p][k] > prev["players"][p][k]] for k in ("deaths", "kills", "assists")}
        if any(ch.values()):
            events.append({"clock": f["clock"], "fmt": f["fmt"], "died": ch["deaths"], "killed": ch["kills"],
                           "assisted": ch["assists"]})
        prev = f
    series, last = [], None
    for f in fr:
        s = int(f["clock"])
        if s != last:
            last = s
            series.append({"clock": f["clock"], "fmt": f["fmt"], "hp": {p: hp(f["players"][p]) for p in order}})
    return {"start": start, "events": events, "hp": series, "order": order}


def hp_at(d: dict, clock_s: float) -> dict:
    """HP of the 10 at the frame closest to clock_s."""
    best = min(d["frames"], key=lambda f: abs(f["clock"] - clock_s))
    return {p: hp(best["players"][p]) for p in d["who"]}
