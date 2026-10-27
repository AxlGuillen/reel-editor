"""The tournament's numbers, for the back of the player cards.

Every game the feed has gives, in its LAST frame, what each player ended with
(K/D/A, CS, gold, level). Kept per game (cache/competitivo/stats/games.json), they
add up to what a card back shows: the champion in the tournament (picks, W-L, KDA),
the player (games, W-L, KDA) and the player with that champion (only with 2+ games).

The feed does not say who won: the final frame's buildings and gold decide it (and
phase 1 knows it better: `set_winner`). Which games exist is told by the games a
phase 1 was already made of (their sessions) or, with lolesports' public key in
REELFORGE_LOLESPORTS_KEY, by the tournament's schedule (`discover`). Nothing here
needs a key to work.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone

import config
from modules.competitivo import engine, kills, vocab

STORE = os.path.join(config.BASE_DIR, "cache", "competitivo", "stats", "games.json")
API = "https://esports-api.lolesports.com/persisted/gw"
WORLDS_LEAGUE = "98767975604431411"
_lock = threading.Lock()
ROLES = ("TOP", "JUNGLA", "MID", "ADC", "SOPORTE")


# --- The store -------------------------------------------------------------------------
def load() -> dict[str, dict]:
    try:
        with open(STORE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(games: dict[str, dict]) -> None:
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(games, fh, ensure_ascii=False)
    os.replace(tmp, STORE)


def winner_of(blue: dict, red: dict, blue_code: str, red_code: str) -> str:
    """Who won, from the last frame's two sides: the one with more inhibitors, then towers, then gold."""
    key = lambda t: (t.get("inhibitors", 0), t.get("towers", 0), t.get("totalGold", 0))  # noqa: E731
    return blue_code if key(blue) >= key(red) else red_code


def parse_final(game_id: str, first: dict, last: dict) -> dict | None:
    """One game's record out of the feed's first response (its metadata) and its last frame."""
    meta = first["gameMetadata"]
    blue_code, red_code = engine.team_codes(meta)
    players = {}
    for side, key, code in (("blue", "blueTeam", blue_code), ("red", "redTeam", red_code)):
        metas = {p["participantId"]: p for p in meta[f"{side}TeamMetadata"]["participantMetadata"]}
        for p in last[key].get("participants", []):
            m = metas.get(p["participantId"], {})
            players[str(p["participantId"])] = {
                "name": vocab.player_name(m.get("summonerName", ""), (blue_code, red_code)), "team": code, "side": side,
                "champion": (m.get("championId") or "").strip(), "role": ROLES[(p["participantId"] - 1) % 5],
                "k": p.get("kills", 0), "d": p.get("deaths", 0), "a": p.get("assists", 0),
                "cs": p.get("creepScore", 0), "gold": p.get("totalGold", 0), "level": p.get("level", 0)}
    if not players:
        return None
    return {"game_id": game_id, "blue": blue_code, "red": red_code, "state": last.get("gameState"),
            "winner": winner_of(last["blueTeam"], last["redTeam"], blue_code, red_code),
            "start": first["frames"][0]["rfc460Timestamp"], "players": players}


def fetch_game(game_id: str) -> dict | None:
    """The record of a game from the feed (two calls: the first frame and the last one)."""
    first = kills._get(f"{engine.FEED}/window/{game_id}")
    if not first or not first.get("frames"):
        return None
    start = engine.parse_ts(first["frames"][0]["rfc460Timestamp"])
    # A start time far in the future: the feed answers with the end of the game.
    far = engine.iso10(start + timedelta(hours=6))
    tail = kills._get(f"{engine.FEED}/window/{game_id}?startingTime={far}")
    frames = (tail or {}).get("frames") or first["frames"]
    last = frames[-1]
    if last.get("gameState") != "finished":
        return None                                   # still going: its numbers are not final
    return parse_final(game_id, first, last)


def update(game_ids: list[str], workers: int = 6, progress=None) -> dict:
    """Adds the games that are not in the store yet. {added, kept, failed:[ids]}."""
    from concurrent.futures import ThreadPoolExecutor
    with _lock:
        games = load()
    todo = [g for g in dict.fromkeys(game_ids) if g and g not in games]
    failed, added = [], 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for k, (gid, rec) in enumerate(zip(todo, ex.map(fetch_game, todo))):
            if rec:
                games[gid] = rec
                added += 1
            else:
                failed.append(gid)
            if progress:
                progress(k + 1, len(todo))
    with _lock:
        stored = load()
        stored.update({g: games[g] for g in todo if g in games})
        _save(stored)
    return {"added": added, "kept": len(games) - added, "failed": failed}


def set_winner(game_id: str, team: str) -> None:
    """The winner phase 1 worked out (better than the heuristic of the last frame)."""
    with _lock:
        games = load()
        if game_id in games and team:
            games[game_id]["winner"] = team
            _save(games)


def session_games() -> list[str]:
    """The game ids of every phase 1 saved on disk."""
    out = []
    folder = config.SESSIONS_FOLDER
    if os.path.isdir(folder):
        for f in os.listdir(folder):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(folder, f), encoding="utf-8") as fh:
                        out += [d["game_id"] for d in json.load(fh).get("partidas", [])]
                except (OSError, ValueError, KeyError):
                    pass
    return list(dict.fromkeys(out))


def discover(key: str | None = None, league_id: str = WORLDS_LEAGUE) -> list[str]:
    """Every game id of the tournament, from lolesports' schedule. Needs its public web key
    (REELFORGE_LOLESPORTS_KEY): without it, [] and the sessions' games are what there is."""
    import urllib.request
    key = key or os.environ.get("REELFORGE_LOLESPORTS_KEY")
    if not key:
        return []

    def get(path: str):
        req = urllib.request.Request(f"{API}/{path}", headers={"x-api-key": key, "User-Agent": "ReelForge"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())["data"]
    matches, token = [], None
    for _ in range(40):
        data = get(f"getSchedule?hl=en-US&leagueId={league_id}" + (f"&pageToken={token}" if token else ""))["schedule"]
        matches += [e["match"]["id"] for e in data["events"] if e.get("match", {}).get("id")
                    and e.get("state") in ("completed", "inProgress")]
        token = (data.get("pages") or {}).get("older")
        if not token:
            break
    games = []
    for mid in dict.fromkeys(matches):
        try:
            ev = get(f"getEventDetails?hl=en-US&id={mid}")["event"]
            games += [g["id"] for g in ev["match"]["games"] if g.get("state") == "completed"]
        except Exception:  # noqa: BLE001 - a match that does not answer is skipped
            continue
    return games


# --- What the card backs show -----------------------------------------------------------
def _kda(k: int, d: int, a: int) -> float:
    return (k + a) / max(1, d)


def _agg(records: list[dict]) -> dict | None:
    if not records:
        return None
    w = sum(1 for r in records if r["won"])
    k, d, a = (sum(r[x] for r in records) for x in ("k", "d", "a"))
    return {"games": len(records), "wins": w, "losses": len(records) - w, "kda": round(_kda(k, d, a), 1),
            "k": k, "d": d, "a": a}


def lookup(player: str, champion: str, games: dict[str, dict] | None = None) -> dict:
    """{"pair", "champion", "player"}: the numbers of the tournament that apply (None where there are no games)."""
    games = load() if games is None else games
    me, ch, pair = [], [], []
    for g in games.values():
        for p in g["players"].values():
            row = {**p, "won": g.get("winner") == p["team"]}
            if p["name"].lower() == player.lower():
                me.append(row)
            if p["champion"] == champion and champion:
                ch.append(row)
                if p["name"].lower() == player.lower():
                    pair.append(row)
    return {"pair": _agg(pair), "champion": _agg(ch), "player": _agg(me)}


def summary() -> dict:
    games = load()
    return {"games": len(games), "teams": sorted({g["blue"] for g in games.values()} | {g["red"] for g in games.values()}),
            "players": len({p["name"] for g in games.values() for p in g["players"].values()}),
            "updated": datetime.fromtimestamp(os.path.getmtime(STORE), timezone.utc).isoformat()
            if os.path.isfile(STORE) else None}
