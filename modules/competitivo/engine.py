"""
esports_highlights.py — motor de highlights automáticos para partidas de LoL Esports.

Qué hace:
  1. Baja el timeline de una partida del feed público de lolesports (sin llave).
  2. Detecta eventos: primera sangre, peleas, dragones, barones, torres, inhibidores, fin.
  3. Les da un puntaje de "hype" y arma clips (inicio/fin) fusionando los que se enciman.
  4. Traduce la hora real (UTC) de cada clip al segundo del VOD usando UN ancla:
     el segundo del VOD donde ocurre la primera sangre.
  5. Escupe un JSON con todo + los comandos de yt-dlp y ffmpeg para bajar SOLO esos tramos.

Pensado para integrarse a ReelForge como modules/esports_highlights/processor.py.
También corre solo desde consola:

  python esports_highlights.py --game 115660588583171787 \
      --vod https://www.youtube.com/watch?v=X4QZM2FMNwQ \
      --anchor-fb 1:22:15 --out salida/kc_c9_g1

  # sin internet, con un timeline guardado:
  python esports_highlights.py --fixture fixtures/kc_c9_g1.json --anchor-fb 4935 --out salida/test

Dependencias: Python 3.11+, requests (solo si se usa --game). yt-dlp y ffmpeg para el paso de corte.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

FEED = "https://feed.lolesports.com/livestats/v1"

# ---------------------------------------------------------------------------
# Layout del VOD VERTICAL oficial de LoL Esports (medido el 16/10/2026 sobre
# "VERTICAL | FUR vs MVK | WORLDS 2026 | Play Ins Day 1", 1080x1920 / 1440x2560).
# Porcentajes de la altura. Verificar si cambia en fase suiza.
# ---------------------------------------------------------------------------
VERTICAL_LAYOUT = {
    "marcador": (0.0, 0.202),
    "juego": (0.202, 0.667),
    "camaras_minimapa": (0.667, 0.787),
    "casters": (0.787, 1.0),
}

# Puntaje base por tipo de evento (0-100). Ajustable.
HYPE = {
    "primera_sangre": 60,
    "kill": 25,          # se suma por kill dentro de una pelea
    "dragon": 35,
    "dragon_anciano": 85,
    "baron": 70,
    "torre": 15,
    "inhibidor": 50,
    "fin": 80,
}

FIGHT_GAP_S = 15      # kills separadas por menos de esto = misma pelea
PRE_S = 15            # segundos antes del evento
POST_S = 8            # segundos después del evento
MERGE_GAP_S = 6       # clips separados por menos de esto se fusionan


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------
@dataclass
class TeamState:
    gold: int
    kills: int
    towers: int
    inhib: int
    barons: int
    dragons: list[str]


@dataclass
class Frame:
    ts: datetime
    state: str
    blue: TeamState
    red: TeamState


@dataclass
class Event:
    ts: datetime
    tipo: str
    equipo: str
    cantidad: int = 1
    detalle: str = ""
    oro_diff: int = 0          # azul - rojo en ese momento
    hype: int = 0
    reloj: str = ""            # tiempo desde el inicio del feed (incluye pausas)


@dataclass
class Clip:
    inicio_utc: datetime
    fin_utc: datetime
    eventos: list[Event] = field(default_factory=list)
    hype: int = 0
    vod_inicio: float | None = None
    vod_fin: float | None = None

    @property
    def titulo(self) -> str:
        e = max(self.eventos, key=lambda x: x.hype)
        return f"{e.reloj} {e.equipo} {e.detalle or e.tipo}"


# ---------------------------------------------------------------------------
# Utilidades de tiempo
# ---------------------------------------------------------------------------
def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso10(dt: datetime) -> str:
    """ISO redondeado a 10 s, como lo pide el feed."""
    secs = math.ceil(dt.timestamp() / 10) * 10
    return datetime.fromtimestamp(secs, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_vod_time(s: str | float | int) -> float:
    """Acepta 4935, '4935', '1:22:15' o '82:15'."""
    if isinstance(s, (int, float)):
        return float(s)
    parts = [float(p) for p in str(s).split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def fmt_hms(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------
def _team(raw: dict) -> TeamState:
    return TeamState(
        gold=raw.get("totalGold", 0),
        kills=raw.get("totalKills", 0),
        towers=raw.get("towers", 0),
        inhib=raw.get("inhibitors", 0),
        barons=raw.get("barons", 0),
        dragons=list(raw.get("dragons", [])),
    )


def fetch_timeline(game_id: str, step_s: int = 10, max_min: int = 70, retries: int = 3):
    """Baja todos los frames de una partida. Devuelve (meta, frames)."""
    import requests  # import tardío: el modo fixture no lo necesita

    def get(url):
        for i in range(retries):
            try:
                r = requests.get(url, timeout=15)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 404:
                    return None
            except requests.RequestException:
                pass
            time.sleep(1.5 * (i + 1))
        return None

    first = get(f"{FEED}/window/{game_id}")
    if not first or not first.get("frames"):
        raise RuntimeError(f"El feed no tiene la partida {game_id} (¿ID equivocado?)")
    meta = first["gameMetadata"]
    start = parse_ts(first["frames"][0]["rfc460Timestamp"])

    frames: dict[str, Frame] = {}
    t = start
    end = start + timedelta(minutes=max_min)
    finished = False
    while t < end and not finished:
        data = get(f"{FEED}/window/{game_id}?startingTime={iso10(t)}")
        for f in (data or {}).get("frames", []):
            fr = Frame(parse_ts(f["rfc460Timestamp"]), f["gameState"], _team(f["blueTeam"]), _team(f["redTeam"]))
            frames[f["rfc460Timestamp"]] = fr
            if f["gameState"] == "finished":
                finished = True
        t += timedelta(seconds=step_s)
    ordered = sorted(frames.values(), key=lambda x: x.ts)
    return meta, ordered


def load_fixture(path: str | Path):
    """Formato compacto generado en las pruebas (ver fixtures/)."""
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    frames = []
    for r in d["rows"]:
        ts, st, *v = r
        blue = TeamState(v[0], v[1], v[2], v[3], v[4], ["?"] * v[5])
        red = TeamState(v[6], v[7], v[8], v[9], v[10], ["?"] * v[11])
        frames.append(Frame(parse_ts(ts), {"i": "in_game", "f": "finished", "p": "paused"}[st], blue, red))
    meta = {
        "blueTeamMetadata": {"participantMetadata": [{"summonerName": n.split("|")[0]} for n in d["meta"]["blue"]]},
        "redTeamMetadata": {"participantMetadata": [{"summonerName": n.split("|")[0]} for n in d["meta"]["red"]]},
    }
    return meta, frames


def team_codes(meta: dict) -> tuple[str, str]:
    b = meta["blueTeamMetadata"]["participantMetadata"][0]["summonerName"].split(" ")[0]
    r = meta["redTeamMetadata"]["participantMetadata"][0]["summonerName"].split(" ")[0]
    return b, r


# ---------------------------------------------------------------------------
# Detección
# ---------------------------------------------------------------------------
def detect_events(frames: list[Frame], blue: str, red: str) -> list[Event]:
    if not frames:
        return []
    t0 = frames[0].ts
    raw: list[Event] = []

    def rel(ts):
        return fmt_hms((ts - t0).total_seconds())

    first_blood_done = False
    for a, b in zip(frames, frames[1:]):
        diff = b.blue.gold - b.red.gold
        for side, code, sa, sb in (("blue", blue, a.blue, b.blue), ("red", red, a.red, b.red)):
            dk = sb.kills - sa.kills
            if dk > 0:
                if not first_blood_done:
                    raw.append(Event(b.ts, "primera_sangre", code, 1, "primera sangre", diff))
                    first_blood_done = True
                    dk -= 1
                if dk > 0:
                    raw.append(Event(b.ts, "kill", code, dk, "", diff))
            for _ in range(sb.towers - sa.towers):
                raw.append(Event(b.ts, "torre", code, 1, "torre", diff))
            for _ in range(sb.inhib - sa.inhib):
                raw.append(Event(b.ts, "inhibidor", code, 1, "inhibidor", diff))
            for _ in range(sb.barons - sa.barons):
                raw.append(Event(b.ts, "baron", code, 1, "barón", diff))
            new_drags = sb.dragons[len(sa.dragons):]
            for d in new_drags:
                tipo = "dragon_anciano" if str(d).lower() == "elder" else "dragon"
                raw.append(Event(b.ts, tipo, code, 1, "dragón anciano" if tipo == "dragon_anciano" else f"dragón {d}".replace(" ?", ""), diff))
        if b.state == "finished" and a.state != "finished":
            winner = blue if b.blue.gold >= b.red.gold else red   # aproximado; confirmar con Leaguepedia
            raw.append(Event(b.ts, "fin", winner, 1, "cae el nexo", diff))

    # Agrupar kills en peleas
    events: list[Event] = []
    fight: list[Event] = []

    def close_fight():
        if not fight:
            return
        by_team: dict[str, int] = {}
        for e in fight:
            by_team[e.equipo] = by_team.get(e.equipo, 0) + e.cantidad
        total = sum(by_team.values())
        winner = max(by_team, key=by_team.get)
        loser_kills = total - by_team[winner]
        if total == 1:
            detalle = "pick"
        elif loser_kills == 0:
            detalle = f"pelea {by_team[winner]}-0"
        else:
            detalle = f"pelea {by_team[winner]}-{loser_kills}"
        hype = min(100, HYPE["kill"] * total + (10 if loser_kills else 0) + (15 if by_team[winner] >= 3 else 0))
        events.append(Event(fight[-1].ts, "pelea" if total > 1 else "kill", winner, total, detalle, fight[-1].oro_diff, hype))
        fight.clear()

    for e in sorted(raw, key=lambda x: x.ts):
        if e.tipo == "kill":
            if fight and (e.ts - fight[-1].ts).total_seconds() > FIGHT_GAP_S:
                close_fight()
            fight.append(e)
        else:
            e.hype = HYPE.get(e.tipo, 20)
            events.append(e)
    close_fight()

    for e in events:
        e.reloj = rel(e.ts)
    return sorted(events, key=lambda x: x.ts)


def build_clips(events: list[Event], min_hype: int = 30) -> list[Clip]:
    """Un clip por evento relevante; los que se enciman se fusionan."""
    clips: list[Clip] = []
    for e in events:
        if e.hype < min_hype and e.tipo not in ("primera_sangre", "fin"):
            # torres sueltas: se quedan solo si caen dentro de otro clip
            continue
        start = e.ts - timedelta(seconds=PRE_S)
        end = e.ts + timedelta(seconds=POST_S)
        if clips and (start - clips[-1].fin_utc).total_seconds() <= MERGE_GAP_S:
            clips[-1].fin_utc = max(clips[-1].fin_utc, end)
            clips[-1].eventos.append(e)
        else:
            clips.append(Clip(start, end, [e]))
    # torres y eventos chicos que caen dentro de un clip suben su puntaje
    for c in clips:
        for e in events:
            if c.inicio_utc <= e.ts <= c.fin_utc and e not in c.eventos:
                c.eventos.append(e)
        hs = sorted((x.hype for x in c.eventos), reverse=True)
        c.hype = min(100, int(hs[0] + 0.3 * sum(hs[1:])))
    return clips


# ---------------------------------------------------------------------------
# Review (datos para el carrusel)
# ---------------------------------------------------------------------------
def review(frames: list[Frame], events: list[Event], blue: str, red: str) -> dict:
    t0 = frames[0].ts
    last = frames[-1]
    max_b = max(frames, key=lambda f: f.blue.gold - f.red.gold)
    max_r = max(frames, key=lambda f: f.red.gold - f.blue.gold)
    final = last.blue.gold - last.red.gold
    winner = blue if final >= 0 else red
    winner_worst = (max_r.red.gold - max_r.blue.gold) if winner == blue else (max_b.blue.gold - max_b.red.gold)
    worst_frame = max_r if winner == blue else max_b
    fb = next((e for e in events if e.tipo == "primera_sangre"), None)
    return {
        "ganador_aprox": winner,
        "kills": f"{blue} {last.blue.kills} - {last.red.kills} {red}",
        "oro_final": f"{'+' if final >= 0 else ''}{final} para {blue if final >= 0 else red}",
        "max_ventaja": {
            blue: {"oro": max_b.blue.gold - max_b.red.gold, "reloj": fmt_hms((max_b.ts - t0).total_seconds())},
            red: {"oro": max_r.red.gold - max_r.blue.gold, "reloj": fmt_hms((max_r.ts - t0).total_seconds())},
        },
        "remontada": {
            "hubo": winner_worst >= 3000,
            "ganador_estuvo_abajo_por": max(0, winner_worst),
            "reloj": fmt_hms((worst_frame.ts - t0).total_seconds()),
        },
        "primera_sangre": {"equipo": fb.equipo, "reloj": fb.reloj, "utc": fb.ts.isoformat()} if fb else None,
        "duracion_feed": fmt_hms((last.ts - t0).total_seconds()),
        "nota": "La duración del feed incluye pausas; para cifras al aire usar Leaguepedia.",
    }


# ---------------------------------------------------------------------------
# Sincronización con el VOD
# ---------------------------------------------------------------------------
def sync(clips: list[Clip], events: list[Event], anchor_fb_vod: float | None = None,
         vod_start_utc: datetime | None = None, delay_s: float = 30.0) -> str:
    """
    Opción A (recomendada): anchor_fb_vod = segundo del VOD donde cae la primera sangre.
    Opción B (aproximada):  vod_start_utc = startTimestamp del directo en YouTube + delay_s.
                            Medido el día 1: ~30 s al inicio del día, ~9 s al final -> calibrar por partida.
    """
    fb = next((e for e in events if e.tipo == "primera_sangre"), None)
    if anchor_fb_vod is not None and fb:
        base_utc, base_vod, how = fb.ts, anchor_fb_vod, "ancla de primera sangre"
    elif vod_start_utc is not None:
        base_utc, base_vod, how = vod_start_utc, delay_s, f"inicio del VOD + {delay_s:.0f}s (aprox.)"
    else:
        return "sin sincronizar"
    for c in clips:
        c.vod_inicio = base_vod + (c.inicio_utc - base_utc).total_seconds()
        c.vod_fin = base_vod + (c.fin_utc - base_utc).total_seconds()
    return how


# ---------------------------------------------------------------------------
# Comandos de corte
# ---------------------------------------------------------------------------
def crop_filter(sin_casters: bool) -> str:
    if not sin_casters:
        return "scale=1080:1920"
    top, bottom = VERTICAL_LAYOUT["marcador"][0], VERTICAL_LAYOUT["camaras_minimapa"][1]
    # recorta marcador + juego + cámaras/minimapa y centra sobre fondo negro 1080x1920
    return (f"crop=iw:ih*{bottom - top:.3f}:0:ih*{top:.3f},scale=1080:-2,"
            "pad=1080:1920:0:(oh-ih)/2:black")


def dq(s: str) -> str:
    """Comillas dobles: funcionan en PowerShell, cmd y bash."""
    return '"' + str(s).replace('"', '\\"') + '"'


def commands(clips: list[Clip], vod_url: str, outdir: Path, top: int, sin_casters: bool) -> list[str]:
    cmds = []
    ranked = sorted([c for c in clips if c.vod_inicio is not None], key=lambda c: -c.hype)[:top]
    ranked.sort(key=lambda c: c.vod_inicio)
    listfile = outdir / "lista_resumen.txt"
    lines = []
    for i, c in enumerate(ranked, 1):
        raw = outdir / f"raw_{i:02d}.mp4"
        out = outdir / f"clip_{i:02d}.mp4"
        sec = f"*{c.vod_inicio:.1f}-{c.vod_fin:.1f}"
        cmds.append(
            "yt-dlp -f \"bv*[height<=1920]+ba/b\" --download-sections " + dq(sec)
            + " --force-keyframes-at-cuts -o " + dq(str(raw)) + " " + dq(vod_url)
        )
        cmds.append(
            f"ffmpeg -y -i {dq(str(raw))} -vf \"{crop_filter(sin_casters)}\" "
            f"-c:v libx264 -preset veryfast -crf 20 -c:a aac -b:a 160k {dq(str(out))}"
        )
        lines.append(f"file '{out.name}'")
    if lines:
        listfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cmds.append(f"ffmpeg -y -f concat -safe 0 -i {dq(str(listfile))} -c copy {dq(str(outdir / 'resumen.mp4'))}")
    return cmds


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description="Highlights automáticos de LoL Esports")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--game", help="esports gameId del feed (ej. 115660588583171787)")
    src.add_argument("--fixture", help="timeline guardado (modo prueba sin internet)")
    p.add_argument("--vod", default="", help="URL del VOD (YouTube vertical oficial recomendado)")
    p.add_argument("--anchor-fb", help="segundo del VOD con la primera sangre (4935 o 1:22:15)")
    p.add_argument("--vod-start", help="startTimestamp del directo, ISO UTC (sincronización aproximada)")
    p.add_argument("--delay", type=float, default=30.0, help="retraso del VOD vs feed para --vod-start")
    p.add_argument("--top", type=int, default=8, help="cuántos clips para el resumen")
    p.add_argument("--min-hype", type=int, default=30)
    p.add_argument("--sin-casters", action="store_true", help="recorta la franja de casters del vertical")
    p.add_argument("--out", default="salida")
    a = p.parse_args(argv)

    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    meta, frames = load_fixture(a.fixture) if a.fixture else fetch_timeline(a.game)
    blue, red = team_codes(meta)
    events = detect_events(frames, blue, red)
    clips = build_clips(events, a.min_hype)
    how = sync(clips, events,
               parse_vod_time(a.anchor_fb) if a.anchor_fb else None,
               parse_ts(a.vod_start) if a.vod_start else None, a.delay)
    rev = review(frames, events, blue, red)
    cmds = commands(clips, a.vod or "<URL_DEL_VOD>", outdir, a.top, a.sin_casters)

    def ser(o):
        if isinstance(o, datetime):
            return o.isoformat()
        raise TypeError

    result = {
        "partida": {"azul": blue, "rojo": red, "frames": len(frames)},
        "sincronizacion": how,
        "review": rev,
        "eventos": [asdict(e) for e in events],
        "clips": [{**asdict(c), "titulo": c.titulo,
                   "vod": f"{fmt_hms(c.vod_inicio)}-{fmt_hms(c.vod_fin)}" if c.vod_inicio is not None else None}
                  for c in clips],
        "comandos": cmds,
    }
    (outdir / "highlights.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=ser), encoding="utf-8")
    (outdir / "comandos.txt").write_text("\n".join(cmds) + "\n", encoding="utf-8")

    print(f"{blue} vs {red} · {len(frames)} frames · {len(events)} eventos · {len(clips)} clips · {how}")
    print("Review:", json.dumps(rev, ensure_ascii=False))
    for c in sorted(clips, key=lambda c: -c.hype)[: a.top]:
        vod = f"{fmt_hms(c.vod_inicio)}-{fmt_hms(c.vod_fin)}" if c.vod_inicio is not None else "-"
        print(f"  hype {c.hype:3d} · VOD {vod:>17} · {c.titulo}")
    return result


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
