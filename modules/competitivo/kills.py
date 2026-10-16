"""Peleas de una partida a partir del feed de lolesports: SOLO kills.

El motor (engine.py, copia sin cambios del prototipo) detecta todo: kills,
dragones, barones, torres, fin. Acá los objetivos nunca generan clip: solo
etiquetan y suman puntaje a una pelea que los precede. El disparador es una
kill.

Resolución del feed. La ventana de `window` trae varios frames por segundo
(medido: ~0.2 s entre frames), así que la kill queda ubicada entre el frame
anterior y el frame donde sube el contador. El fixture de los tests viene
compactado a un frame cada ~9.5 s; la misma regla ("la kill pasó entre el
frame anterior y este") cubre los dos casos.

Flujo:
    partida = fetch_partida(game_id)            # o partida_de_fixture(path)
    peleas  = detectar_peleas(partida, reglas)  # todas, con puntaje y ventana
    clips   = seleccionar([peleas_g1, ...], reglas)
"""
from __future__ import annotations

import bisect
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from modules.competitivo import engine

# --- Reglas -------------------------------------------------------------------
OBJ_LABEL_S = 30         # objetivo grande ≤ esto después de la pelea → etiqueta + bonus
CIERRE_S = 40            # nexo ≤ esto después de la última kill → la pelea cierra la partida
SWING_S = 20             # el oro "después" se mide esto después de la última kill
LAG_JUGADOR_S = 5        # las kills por jugador llegan unos segundos tarde vs el total
REMONTADA_ORO = 2000     # el ganador iba abajo por al menos esto antes de la pelea
OBJETIVOS_GRANDES = {"baron": "barón", "dragon_anciano": "anciano", "inhibidor": "inhibidor"}
MULTIKILL = {3: ("triple", 25), 4: ("quadra", 40), 5: ("penta", 60)}


@dataclass
class Reglas:
    """Parámetros de detección y selección (los arma schema.py)."""
    min_kills: int = 2
    incluir_picks: bool = False
    incluir_primera_sangre: bool = False
    pre_s: float = 10
    post_s: float = 5
    duracion_max_s: float = 60
    max_por_partida: int = 3


# --- Modelo -------------------------------------------------------------------
@dataclass
class Partida:
    game_id: str
    azul: str
    rojo: str
    frames: list[engine.Frame]
    # kills por jugador en cada frame (alineado con `frames`), None sin datos
    kills_jugador: list[dict[int, int]] | None = None
    nombres: dict[int, str] = field(default_factory=dict)   # participantId → "C9 Blaber"

    def __post_init__(self):
        self._ts = [f.ts for f in self.frames]
        self.eventos = engine.detect_events(self.frames, self.azul, self.rojo)

    @property
    def t0(self) -> datetime:
        return self.frames[0].ts

    def indice(self, ts: datetime) -> int:
        """Índice del primer frame con ts >= `ts` (acotado al último)."""
        return min(bisect.bisect_left(self._ts, ts), len(self.frames) - 1)


@dataclass
class Kill:
    """Una subida del contador de kills de un equipo entre dos frames."""
    i: int               # índice del frame donde subió el contador
    equipo: str
    cantidad: int


@dataclass
class Pelea:
    game_id: str
    kills: list[Kill]
    ganador: str
    marcador: dict[str, int]          # equipo → kills en la pelea
    antes: datetime                   # frame anterior a la primera kill
    primera: datetime                 # frame de la primera kill
    ultima: datetime                  # frame de la última kill
    reloj: str                        # reloj del feed en la última kill
    primera_sangre: bool
    objetivo: str | None              # "barón" / "anciano" / "inhibidor" / "nexo"
    cierra: bool
    multikill: str | None             # "triple de Blaber"
    oro_antes: int                    # ventaja del ganador antes de la pelea
    oro_despues: int                  # ventaja del ganador SWING_S después
    desglose: dict[str, int]
    puntaje: int
    clip_inicio: datetime
    clip_fin: datetime
    elegible: bool = True
    forzada: bool = False             # la que cierra la serie: entra siempre

    @property
    def bruto(self) -> int:
        """Puntaje sin tope: ordena la selección. Con el tope de 100 empataban
        las peleas grandes (medido en C9-KC G2: un 6-1 perdía contra un 3-1)."""
        return sum(self.desglose.values())

    @property
    def total(self) -> int:
        return sum(self.marcador.values())

    @property
    def duracion(self) -> float:
        return (self.clip_fin - self.clip_inicio).total_seconds()

    @property
    def detalle(self) -> str:
        if self.total == 1:
            return "primera sangre" if self.primera_sangre else "pick"
        perdedor = self.total - self.marcador[self.ganador]
        return f"pelea {self.marcador[self.ganador]}-{perdedor}"

    @property
    def titulo(self) -> str:
        """"C9 · pelea 3-0 → barón · 34:50" (va en la franja del clip)."""
        partes = [self.ganador, self.detalle]
        if self.multikill:
            partes.append(self.multikill)
        if self.objetivo:
            partes[-1] += f" → {self.objetivo}"
        partes.append(self.reloj)
        return " · ".join(partes)


# --- Feed ---------------------------------------------------------------------
def _get(url: str, retries: int = 3):
    """GET JSON del feed. None si no hay datos (404/204) o si falla tras reintentos."""
    for intento in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ReelForge"})
            with urllib.request.urlopen(req, timeout=15) as r:
                body = r.read()
                return json.loads(body.decode("utf-8")) if body else None
        except urllib.error.HTTPError as exc:
            if exc.code in (204, 404):
                return None
        except (urllib.error.URLError, TimeoutError, ValueError):
            pass
        time.sleep(1.5 * (intento + 1))
    return None


def fetch_partida(game_id: str, step_s: int = 10, max_min: int = 80,
                  workers: int = 8) -> Partida:
    """Baja el timeline completo de una partida, CON las kills de cada jugador.

    Mismo endpoint y misma lógica que engine.fetch_timeline (ventanas de 10 s
    desde el primer frame hasta 'finished'), pero guarda `participants`, que
    trae las K/D/A por jugador en cada frame (lo usan los multikills), baja
    las ventanas en paralelo y usa urllib (sin depender de `requests`).
    """
    first = _get(f"{engine.FEED}/window/{game_id}")
    if not first or not first.get("frames"):
        raise RuntimeError(f"El feed no tiene la partida {game_id} (¿gameId equivocado?)")
    meta = first["gameMetadata"]
    start = engine.parse_ts(first["frames"][0]["rfc460Timestamp"])
    end = start + timedelta(minutes=max_min)

    raw: dict[str, dict] = {}
    t = start
    with ThreadPoolExecutor(max_workers=workers) as ex:
        terminado = False
        while t < end and not terminado:
            lote = [t + timedelta(seconds=step_s * k) for k in range(workers * 2)]
            urls = [f"{engine.FEED}/window/{game_id}?startingTime={engine.iso10(x)}" for x in lote]
            for data in ex.map(_get, urls):
                for f in (data or {}).get("frames", []):
                    raw[f["rfc460Timestamp"]] = f
                    terminado = terminado or f["gameState"] == "finished"
            t = lote[-1] + timedelta(seconds=step_s)

    ordenados = sorted(raw.values(), key=lambda f: f["rfc460Timestamp"])
    frames = [engine.Frame(engine.parse_ts(f["rfc460Timestamp"]), f["gameState"],
                           engine._team(f["blueTeam"]), engine._team(f["redTeam"]))
              for f in ordenados]
    kills_jugador = [{p["participantId"]: p.get("kills", 0)
                      for lado in ("blueTeam", "redTeam") for p in f[lado].get("participants", [])}
                     for f in ordenados]
    nombres = {}
    for lado in ("blueTeamMetadata", "redTeamMetadata"):
        for p in meta[lado]["participantMetadata"]:
            nombres[p["participantId"]] = p.get("summonerName", "").strip()
    azul, rojo = engine.team_codes(meta)
    return Partida(game_id, azul, rojo, frames, kills_jugador, nombres)


def partida_de_fixture(path, game_id: str | None = None) -> Partida:
    """Timeline guardado (formato compacto de engine.load_fixture, sin jugadores)."""
    meta, frames = engine.load_fixture(path)
    azul, rojo = engine.team_codes(meta)
    if game_id is None:
        with open(path, encoding="utf-8") as fh:
            game_id = json.load(fh)["meta"].get("id", "fixture")
    return Partida(game_id, azul, rojo, frames)


# --- Detección ----------------------------------------------------------------
def _kills(p: Partida) -> list[Kill]:
    out = []
    for i in range(1, len(p.frames)):
        a, b = p.frames[i - 1], p.frames[i]
        for code, sa, sb in ((p.azul, a.blue, b.blue), (p.rojo, a.red, b.red)):
            if sb.kills > sa.kills:
                out.append(Kill(i, code, sb.kills - sa.kills))
    return out


def _agrupar(p: Partida, kills: list[Kill]) -> list[list[Kill]]:
    """Kills a menos de FIGHT_GAP_S (15 s) entre sí = misma pelea."""
    grupos: list[list[Kill]] = []
    for k in kills:
        if grupos and (p.frames[k.i].ts - p.frames[grupos[-1][-1].i].ts).total_seconds() <= engine.FIGHT_GAP_S:
            grupos[-1].append(k)
        else:
            grupos.append([k])
    return grupos


def _ventaja(p: Partida, i: int, equipo: str) -> int:
    f = p.frames[i]
    diff = f.blue.gold - f.red.gold
    return diff if equipo == p.azul else -diff


def _multikill(p: Partida, i_antes: int, i_ultima: int) -> tuple[str, int] | None:
    if not p.kills_jugador:
        return None
    a, b = p.kills_jugador[i_antes], p.kills_jugador[i_ultima]
    mejor = max(((b.get(pid, 0) - a.get(pid, 0), pid) for pid in b), default=(0, 0))
    n, pid = mejor
    if n < 3:
        return None
    nombre = p.nombres.get(pid, "").split(" ", 1)[-1] or f"jugador {pid}"
    return f"{MULTIKILL[min(n, 5)][0]} de {nombre}", n


def _armar(p: Partida, grupo: list[Kill], reglas: Reglas, primera_sangre_i: int,
           ultima_kill_i: int) -> Pelea:
    i0, i1 = grupo[0].i, grupo[-1].i
    marcador: dict[str, int] = {}
    for k in grupo:
        marcador[k.equipo] = marcador.get(k.equipo, 0) + k.cantidad
    ganador = max(marcador, key=lambda e: (marcador[e], e == grupo[-1].equipo))
    total = sum(marcador.values())
    antes, primera, ultima = p.frames[i0 - 1].ts, p.frames[i0].ts, p.frames[i1].ts

    # Objetivos que caen después de la pelea: solo etiqueta y bonus. Un
    # objetivo pasó entre el frame anterior al suyo y el suyo: se compara el
    # momento más temprano posible (con el fixture, frames cada ~9.5 s).
    objetivo, cierra = None, False
    for ev in p.eventos:
        ev_i = p.indice(ev.ts)
        temprano = p.frames[max(ev_i - 1, 0)].ts
        dt = (temprano - ultima).total_seconds()
        # Solo la ÚLTIMA pelea de la partida puede cerrarla (un pick 30 s
        # antes de la pelea final no "cierra" nada aunque el nexo caiga cerca).
        if ev.tipo == "fin" and dt <= CIERRE_S and ev.ts >= ultima and i1 == ultima_kill_i:
            cierra = True
        elif ev.tipo in OBJETIVOS_GRANDES and 0 <= dt <= OBJ_LABEL_S and ev.ts > ultima and not objetivo:
            objetivo = OBJETIVOS_GRANDES[ev.tipo]
            if ev.equipo != ganador:
                objetivo += f" ({ev.equipo})"
    if cierra and not objetivo:
        objetivo = "nexo"

    mk = _multikill(p, i0 - 1, p.indice(ultima + timedelta(seconds=LAG_JUGADOR_S)))
    oro_antes = _ventaja(p, i0 - 1, ganador)
    oro_despues = _ventaja(p, p.indice(ultima + timedelta(seconds=SWING_S)), ganador)
    minuto = int((ultima - p.t0).total_seconds() // 60)

    desglose = {
        "kills": 20 * total,
        "intercambio": 10 if len(marcador) > 1 else 0,
        "ganador_3": 15 if marcador[ganador] >= 3 else 0,
        "multikill": MULTIKILL[min(mk[1], 5)][1] if mk else 0,
        "cierre": 30 if cierra else 0,
        "remontada": 15 if oro_antes <= -REMONTADA_ORO else 0,
        "minuto": min(15, max(0, minuto - 20)),
        "objetivo": 10 if objetivo and objetivo not in ("nexo",) else 0,
        # Pedido de Axl: priorizar las peleas que más mueven el oro. +1 por
        # cada 100 de oro que el ganador saca de diferencia (máx. +20).
        "oro": max(0, min(20, (oro_despues - oro_antes) // 100)),
    }
    return Pelea(
        game_id=p.game_id, kills=grupo, ganador=ganador, marcador=marcador,
        antes=antes, primera=primera, ultima=ultima,
        reloj=engine.fmt_hms((ultima - p.t0).total_seconds()),
        primera_sangre=i0 == primera_sangre_i, objetivo=objetivo, cierra=cierra,
        multikill=mk[0] if mk else None, oro_antes=oro_antes, oro_despues=oro_despues,
        desglose=desglose, puntaje=min(100, sum(desglose.values())),
        clip_inicio=antes - timedelta(seconds=reglas.pre_s),
        clip_fin=ultima + timedelta(seconds=reglas.post_s),
    )


def _elegible(pe: Pelea, reglas: Reglas) -> bool:
    if pe.total >= reglas.min_kills:
        return True
    if pe.total == 1:
        return reglas.incluir_primera_sangre if pe.primera_sangre else reglas.incluir_picks
    return False


def detectar_peleas(p: Partida, reglas: Reglas | None = None) -> list[Pelea]:
    """Todas las peleas de la partida (también las que no pasan el filtro,
    con `elegible=False`, para que la UI pueda sumarlas a mano)."""
    reglas = reglas or Reglas()
    kills = _kills(p)
    if not kills:
        return []
    grupos = _agrupar(p, kills)
    fb, ultima_i = kills[0].i, kills[-1].i
    peleas = [_armar(p, g, reglas, fb, ultima_i) for g in grupos]
    for pe in peleas:
        pe.elegible = _elegible(pe, reglas)

    # Peleas elegibles cuyos clips quedan a menos de MERGE_GAP_S (6 s) se
    # fusionan en una sola (se rearma con todas las kills).
    out: list[Pelea] = []
    for pe in peleas:
        prev = out[-1] if out and out[-1].elegible else None
        if (pe.elegible and prev is not None
                and (pe.clip_inicio - prev.clip_fin).total_seconds() <= engine.MERGE_GAP_S):
            out[-1] = _armar(p, prev.kills + pe.kills, reglas, fb, ultima_i)
            out[-1].elegible = True
        else:
            out.append(pe)
    return out


# --- Selección ----------------------------------------------------------------
def seleccionar(partidas: list[list[Pelea]], reglas: Reglas | None = None) -> list[Pelea]:
    """Elige los clips del video con el presupuesto de tiempo.

    `partidas` va en el orden de la serie: la ÚLTIMA es la decisiva y su
    pelea de cierre entra siempre (aunque no pase el filtro). El resto se
    rellena por puntaje (sin tope) mientras quepa en `duracion_max_s`, con hasta
    `max_por_partida` por partida. Devuelve en orden cronológico.
    """
    reglas = reglas or Reglas()
    orden = {id(pe): (g, pe.clip_inicio) for g, peleas in enumerate(partidas) for pe in peleas}
    elegidas: list[Pelea] = []
    usados = 0.0
    por_partida: dict[str, int] = {}

    def tomar(pe: Pelea) -> None:
        nonlocal usados
        elegidas.append(pe)
        usados += pe.duracion
        por_partida[pe.game_id] = por_partida.get(pe.game_id, 0) + 1

    if partidas and partidas[-1]:
        cierre = next((pe for pe in reversed(partidas[-1]) if pe.cierra), None)
        if cierre is not None:
            cierre.forzada = True
            tomar(cierre)

    candidatas = sorted((pe for peleas in partidas for pe in peleas if pe.elegible and not pe.forzada),
                        key=lambda pe: (-pe.bruto, orden[id(pe)]))
    for pe in candidatas:
        if por_partida.get(pe.game_id, 0) >= reglas.max_por_partida:
            continue
        if usados + pe.duracion > reglas.duracion_max_s:
            continue
        tomar(pe)
    return sorted(elegidas, key=lambda pe: orden[id(pe)])


# --- Consola (para probar sin la UI) ------------------------------------------
def _main(argv: list[str]) -> None:
    """python -m modules.competitivo.kills <gameId> [<gameId> ...]   (en orden de la serie)"""
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    reglas = Reglas()
    todas = []
    for gid in argv:
        t = time.time()
        p = partida_de_fixture(gid) if gid.endswith(".json") else fetch_partida(gid)
        print(f"\n{p.azul} vs {p.rojo} · {gid} · {len(p.frames)} frames ({time.time() - t:.0f} s)")
        peleas = detectar_peleas(p, reglas)
        todas.append(peleas)
        for pe in peleas:
            marca = "✓" if pe.elegible else " "
            print(f"  {marca} {pe.titulo:46s} puntaje {pe.puntaje:3d} (bruto {pe.bruto:3d}) · {pe.duracion:4.1f} s")
    elegidas = seleccionar(todas, reglas)
    print(f"\nVideo ({sum(pe.duracion for pe in elegidas):.0f} s de {reglas.duracion_max_s:.0f}):")
    for pe in elegidas:
        print(f"  {pe.game_id[-3:]} · {pe.titulo}{'  [cierre de la serie]' if pe.forzada else ''}")


if __name__ == "__main__":
    import sys
    _main(sys.argv[1:])
