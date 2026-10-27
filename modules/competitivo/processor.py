"""Orquestación del módulo competitivo: highlights de Worlds solo con kills.

Flujo de DOS FASES con ancla por partida (como subtitles / reel_express):

  Fase 1  process()  feed de cada partida → peleas con puntaje → selección con
                     presupuesto de tiempo → ANCLA de cada partida: el segundo
                     del VOD donde cae su primera kill.
                     El ancla se calcula sola. La fórmula (inicio del directo +
                     38 s) es solo el punto de partida: el desfase real cambió
                     de −5 a +8 s entre partidas del mismo día. Se baja el tramo
                     de la pelea más grande de cada partida, se ven los cambios
                     del contador de kills del marcador y se alinean con el
                     feed (layout.cambios_kills / estimar_desfase).
                     Queda en stage "esperando_ancla" con todo prellenado. En
                     modo "auto" sigue solo si todas las anclas son confiables.
  Fase 2  finish()   con las anclas (las detectadas o las que corrigió el
                     usuario) baja SOLO los tramos de los clips elegidos, ajusta
                     cada clip con sus propias kills, lo renderiza con el layout
                     y concatena resumen.mp4. Todo queda en la carpeta
                     persistente (config.COMPETITIVO_FOLDER): por día y
                     partido si llegan carpeta_dia y partido, o por serie
                     (ver salida.py).
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import config
from core import ffmpeg_runner, file_utils, job_manager, scheduler
from modules.beatmap import processor as beatmap
from modules.competitivo import edit as edit_plan
from modules.competitivo import (camera, card_plan, cards, clock, creator, fuente, intro, kills, layout, metrics,
                                 salida, stats, storyboard, transcript, vocab, voice)
from modules.competitivo.schema import CompetitivoParams, EditParams, FinishParams
from modules.editing import render as editing

# vod ≈ (utc − inicio del directo) + offset, per platform. YouTube: measured in
# KC-C9 G3, day 1 (later games of the same day: −5 to +7.5 s around it).
# Twitch: its VOD starts at its own timestamp (36 s before YouTube's on
# 23/10) and runs behind it; IG-GEN on 23/10 measured +44.2 s (7/7 kills).
OFFSET_FORMULA_S = {"youtube": 38, "twitch": 44}
SUSPICIOUS_SHIFT_S = 15    # an anchor this far from the formula is "media" even with 2+ kills
VENTANA_ANCLA_S = 30       # margen del tramo que se baja para calcular el ancla
RANGO_ANCLA_S = 25         # desfase máximo contra la fórmula que se busca
MARGEN_CLIP_S = 6          # cada clip se baja con este margen, para el ajuste fino
RANGO_AJUSTE_S = 5         # ajuste fino por clip: corrimiento máximo

TRABAJO = os.path.join(config.OUTPUT_FOLDER, "competitivo")

# Datos de cada sesión entre la fase 1 y la 2 (los jobs guardan lo que se
# muestra; acá queda lo que hace falta para renderizar).
_SESIONES: dict[str, dict] = {}
# The job fields of a phase 1 that are saved with its session.
_SAVED_JOB_FIELDS = ("status", "progress", "stage", "finished", "modo", "aviso", "vod", "partidas", "clips",
                     "seleccion", "resultado", "portada", "intro", "error", "output_path",
                     "transcript_job_id", "transcript_jobs")


def _session_path(job_id: str) -> str:
    return os.path.join(config.SESSIONS_FOLDER, f"{os.path.basename(job_id)}.json")


def save_session(job_id: str) -> None:
    """The phase-1 session (and its job's status) on disk: a restart of the
    server no longer loses it (yesterday it happened three times)."""
    sesion = _SESIONES.get(job_id)
    if not sesion:
        return
    voz = sesion.get("voz")
    data = {
        "params": asdict(sesion["params"]),
        "vod": {**sesion["vod"], "inicio_utc": _iso(sesion["vod"]["inicio_utc"])},
        "partidas": sesion["partidas"], "clips": sesion["clips"], "carpeta": sesion["carpeta"],
        "voz": [voz[0], asdict(voz[1])] if voz else None,
        "job": {k: v for k, v in (job_manager.get_job(job_id) or {}).items() if k in _SAVED_JOB_FIELDS},
    }
    os.makedirs(config.SESSIONS_FOLDER, exist_ok=True)
    tmp = _session_path(job_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, default=str)
    os.replace(tmp, _session_path(job_id))


def get_session(job_id: str) -> dict | None:
    """The phase-1 session: from memory, or from disk after a restart (then
    its job is put back too, so /status, /finish and /edit work as before)."""
    sesion = _SESIONES.get(job_id)
    if sesion or not job_id:
        return sesion
    path = _session_path(job_id)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    voz = data.get("voz")
    if voz and not os.path.isfile(voz[1]["path"]):
        voz = None
    sesion = {"params": CompetitivoParams(**data["params"]),
              "vod": {**data["vod"], "inicio_utc": datetime.fromisoformat(data["vod"]["inicio_utc"])},
              "partidas": data["partidas"], "clips": data["clips"], "carpeta": data["carpeta"],
              "voz": (voz[0], intro.Voz(**voz[1])) if voz else None}
    os.makedirs(sesion["carpeta"], exist_ok=True)
    _SESIONES[job_id] = sesion
    job = dict(data.get("job") or {})
    if job.get("status") == "processing":
        # It was cut by the restart: what is left is the proposal of phase 1.
        job.update(status="done", stage="esperando_ancla", finished=False,
                   aviso="El servidor se reinició a mitad del render: volvé a llamar /finish.")
    job_manager.restore_job(job_id, job)
    return sesion


# Edits paused for review (review=1): what /edit/finish needs to render.
_EDIT_REVIEW: dict[str, dict] = {}


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _vod(ts: datetime, inicio: datetime, offset: float) -> float:
    return (ts - inicio).total_seconds() + offset


def _lado(p: kills.Partida, equipo: str) -> str:
    # El azul va siempre a la izquierda del marcador; el rojo, a la derecha.
    return "izq" if equipo == p.azul else "der"


def _pelea_dict(p: kills.Partida, pe: kills.Pelea, n_partida: int, n: int,
                seleccion: set[int]) -> dict:
    return {
        "id": f"{n_partida}-{n}",
        "game_id": pe.game_id, "partida": n_partida,
        "titulo": pe.titulo, "detalle": pe.detalle, "reloj": pe.reloj,
        "ganador": pe.ganador, "marcador": pe.marcador, "kills": pe.total,
        "objetivo": pe.objetivo, "multikill": pe.multikill, "cierra": pe.cierra,
        "puntaje": pe.puntaje, "bruto": pe.bruto, "desglose": pe.desglose,
        "elegible": pe.elegible, "forzada": pe.forzada, "relleno": pe.relleno,
        "seleccionada": id(pe) in seleccion,
        "duracion": round(pe.duracion, 1),
        "antes": _iso(pe.antes), "primera": _iso(pe.primera), "ultima": _iso(pe.ultima),
        "clip_inicio": _iso(pe.clip_inicio), "clip_fin": _iso(pe.clip_fin),
        "eventos": [[_iso(p.frames[k.i].ts), _lado(p, k.equipo)] for k in pe.kills],
        # Who made each kill of `eventos` (for the edit mode titles).
        "authors": kills.kill_authors(p, pe),
        "counts": [k.cantidad for k in pe.kills],
    }


def _captura(src: str, t: float, out: str) -> None:
    ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-y", "-ss", f"{max(0.0, t):.2f}",
                           "-i", src, "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "3", out])


def formula_offset(platform: str | None) -> float:
    return OFFSET_FORMULA_S.get(platform or "youtube", OFFSET_FORMULA_S["youtube"])


def confidence(matches: int, shift: float) -> tuple[str, bool]:
    """(confianza, suspicious): 2+ aligned kills are "alta" only if the shift
    against the platform's formula is plausible. A YouTube stream still live
    gave 6/7 kills of ANOTHER game at −23.5 s: that has to stop modo=auto."""
    suspicious = matches >= 2 and abs(shift) > SUSPICIOUS_SHIFT_S
    if suspicious:
        return "media", True
    return ("alta" if matches >= 2 else "media" if matches == 1 else "baja"), False


def _ancla(url: str, inicio: datetime, p: kills.Partida, peleas: list[kills.Pelea],
           carpeta: str, n_partida: int, offset: float = OFFSET_FORMULA_S["youtube"],
           job_id: str | None = None) -> dict:
    """Segundo del VOD de la primera kill de la partida, alineando el contador
    de kills del video con el feed en el tramo de la pelea más grande.
    `offset`: the platform's formula (formula_offset)."""
    eventos = [(p.frames[k.i].ts, _lado(p, k.equipo)) for k in kills._kills(p)]
    fb_utc = eventos[0][0]
    propuesta = _vod(fb_utc, inicio, offset)
    ref = max(peleas, key=lambda pe: (pe.elegible, pe.total, pe.bruto))
    a = _vod(ref.antes, inicio, offset) - VENTANA_ANCLA_S
    b = _vod(ref.ultima, inicio, offset) + VENTANA_ANCLA_S
    cached = _anchor_cached(url, p.game_id, fb_utc, os.path.join(carpeta, f"ancla_{n_partida}.jpg"))
    if cached:
        return cached
    desfase, n, esperadas = 0.0, 0, 0
    frame = clock0_utc = None
    try:
        tramo = fuente.bajar_tramo(url, a, b, os.path.join(carpeta, f"ancla_{n_partida}"), job_id=job_id)
        with scheduler.slot("analysis", job_id):
            scan = layout.Scan(tramo, layout.SCAN_ANCHOR)         # ONE decode: counter and clock
            vistos = layout.cambios_kills(tramo, scan=scan)
            esp = [(_vod(ts, inicio, offset) - a, lado) for ts, lado in eventos]
            esp = [e for e in esp if -RANGO_ANCLA_S <= e[0] <= (b - a) + RANGO_ANCLA_S]
            esperadas = sum(1 for t, _ in esp if 0 <= t <= b - a)
            desfase, n = layout.estimar_desfase(vistos, esp, rango=RANGO_ANCLA_S)
            if not n:
                desfase = 0.0
            # Frame de control: la primera kill de la pelea de referencia, ya corregida.
            frame = f"ancla_{n_partida}.jpg"
            _captura(tramo, _vod(ref.primera, inicio, offset + desfase) - a + 0.5,
                     os.path.join(carpeta, frame))
            # The scoreboard's clock: when it said 0:00 (UTC). From there every
            # time of the feed becomes the clock everyone talks about.
            zero = clock.calibrate(tramo, [3.0 * k for k in range(1, int((b - a) / 3))][:24], scan=scan)
            if zero is not None:
                clock0_utc = inicio + timedelta(seconds=a + zero - offset - desfase)
        os.remove(tramo)
    except RuntimeError as exc:
        print(f"[competitivo] ancla de {p.game_id}: {exc}".encode("ascii", "replace").decode())
    conf, suspicious = confidence(n, desfase)
    result = {
        "ancla_s": round(propuesta + desfase, 2),
        "ancla_propuesta_s": round(propuesta, 2),
        "desfase_s": desfase,
        "confianza": conf, "suspicious": suspicious,
        "coincidencias": n, "esperadas": esperadas,
        "referencia": ref.titulo,
        "referencia_vod_s": round(_vod(ref.primera, inicio, offset + desfase), 2),
        "frame": frame,
        "primera_kill_utc": _iso(fb_utc),
        "clock0_utc": _iso(clock0_utc) if clock0_utc else None,
    }
    if conf == "alta" and not suspicious and clock0_utc and frame:
        _anchor_store(url, p.game_id, result, os.path.join(carpeta, frame))
    return result


def _anchor_path(url: str, game_id: str) -> str:
    return os.path.join(config.ANCHORS_FOLDER, f"{fuente.vod_key(url)}_{game_id}")


def _anchor_cached(url: str, game_id: str, fb_utc: datetime, frame_dest: str) -> dict | None:
    """The anchor already measured for this game of this VOD (confidence "alta" and
    with its clock only; and only if the feed's first kill is still the same one):
    its control frame goes back to the job's folder. None if there is none."""
    base = _anchor_path(url, game_id)
    try:
        with open(base + ".json", encoding="utf-8") as fh:
            result = json.load(fh)
        if result.get("primera_kill_utc") != _iso(fb_utc) or not os.path.isfile(base + ".jpg"):
            return None
        shutil.copyfile(base + ".jpg", frame_dest)
    except (OSError, ValueError):
        return None
    print(f"[competitivo] ancla de {game_id}: ya medida en este VOD".encode("ascii", "replace").decode())
    return {**result, "frame": os.path.basename(frame_dest), "cached": True}


def _anchor_store(url: str, game_id: str, result: dict, frame_path: str) -> None:
    base = _anchor_path(url, game_id)
    try:
        os.makedirs(config.ANCHORS_FOLDER, exist_ok=True)
        shutil.copyfile(frame_path, base + ".jpg")
        with open(base + ".json", "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False)
    except OSError as exc:          # the cache is an extra: a job never fails for it
        print(f"[competitivo] no se pudo guardar el ancla: {exc}".encode("ascii", "replace").decode())


def _clock_titles(clips: list[dict], clock0_utc: datetime, pauses: list) -> None:
    """The fights' clocks as the scoreboard showed them (they counted from the
    start of the feed, ~5 min before 0:00): the title, its kills and the
    feed's, kept as reloj_feed."""
    for c in clips:
        new = clock.fmt(clock.to_clock(datetime.fromisoformat(c["ultima"]), clock0_utc, pauses))
        c["reloj_feed"] = c["reloj"]
        if c["titulo"].endswith(c["reloj"]):
            c["titulo"] = c["titulo"][: -len(c["reloj"])] + new
        c["reloj"] = new
        c["kills_reloj"] = [clock.fmt(clock.to_clock(datetime.fromisoformat(ts), clock0_utc, pauses))
                            for ts, _ in c["eventos"]]


def game_clock(partida: dict, utc: datetime) -> float | None:
    """Seconds of the scoreboard clock at a UTC time of that game (None if its
    clock was not calibrated)."""
    if not partida.get("clock0_utc"):
        return None
    pauses = [(datetime.fromisoformat(x), datetime.fromisoformat(y)) for x, y in partida.get("pauses") or []]
    return clock.to_clock(utc, datetime.fromisoformat(partida["clock0_utc"]), pauses)


def utc_at_clock(partida: dict, clock_s: float) -> datetime | None:
    if not partida.get("clock0_utc"):
        return None
    pauses = [(datetime.fromisoformat(x), datetime.fromisoformat(y)) for x, y in partida.get("pauses") or []]
    return clock.from_clock(clock_s, datetime.fromisoformat(partida["clock0_utc"]), pauses)


# --- Fase 1 ---------------------------------------------------------------------
def _preset(job_id: str) -> str | None:
    return (job_manager.get_job(job_id) or {}).get("preset")


def _shot_counts(shots: list[dict]) -> dict:
    """How the automatic camera did: kills followed with confidence, kills in the full view."""
    return {"kills": len(shots), "kills_sure": sum(1 for s in shots if s.get("sure")),
            "kills_full": sum(1 for s in shots if s.get("mode") == "full")}


def process(job_id: str, params: CompetitivoParams) -> None:
    run = metrics.Run("fase1", job_id, games=len(params.game_ids), modo=params.modo, preset=_preset(job_id))
    try:
        carpeta = os.path.join(TRABAJO, job_id)
        os.makedirs(carpeta, exist_ok=True)
        # La voz de la intro se valida ANTES que nada: un link roto o una voz de
        # más de 20 s tienen que fallar en segundos, no después de la fase 1.
        voz = None
        if params.intro_modo != "off" and params.intro_audio_url:
            job_manager.update_job(job_id, status="processing", progress=1,
                                   stage="Bajando la voz de la intro…")
            voz = intro.preparar_voz(params.intro_audio_url, carpeta)
        job_manager.update_job(job_id, status="processing", progress=2, stage="Leyendo el VOD…")
        vod = fuente.info_vod(params.vod_url)
        inicio = vod["inicio_utc"]
        offset = formula_offset(vod.get("platform"))

        job_manager.update_job(job_id, progress=5,
                               stage=f"Bajando el feed de {len(params.game_ids)} partida(s)…")
        with ThreadPoolExecutor(max_workers=len(params.game_ids)) as ex:
            partidas = list(ex.map(lambda g: kills.fetch_partida(g, workers=6), params.game_ids))
        reglas = params.reglas
        peleas = [kills.detectar_peleas(p, reglas) for p in partidas]
        sin_kills = [p.game_id for p, pe in zip(partidas, peleas) if not pe]
        if sin_kills:
            raise RuntimeError(f"La partida {sin_kills[0]} no tiene kills en el feed.")
        elegidas = kills.seleccionar(peleas, reglas)
        seleccion = {id(pe) for pe in elegidas}
        run.mark("feed")

        job_manager.update_job(job_id, progress=45, stage="Sincronizando el VOD con el feed…")
        with ThreadPoolExecutor(max_workers=3) as ex:
            anclas = list(ex.map(lambda i: _ancla(params.vod_url, inicio, partidas[i], peleas[i],
                                                  carpeta, i + 1, offset=offset, job_id=job_id),
                                   range(len(partidas))))
        run.mark("anchors")

        datos_partidas, clips = [], []
        for i, (p, pe_lista, ancla) in enumerate(zip(partidas, peleas, anclas), 1):
            minutos = kills.minutos_de(p)
            datos_partidas.append({
                "n": i, "game_id": p.game_id, "azul": p.azul, "rojo": p.rojo,
                "inicio_utc": _iso(p.t0), "minutos": minutos,
                "objetivo_s": kills.objetivo_s(reglas, minutos),
                # The 10 players and champions (Whisper's vocabulary in the edits).
                "roster": [{"team": p.azul if pid <= 5 else p.rojo,
                            "player": vocab.player_name(p.nombres.get(pid, ""), (p.azul, p.rojo)),
                            "champion": p.champions.get(pid, "")} for pid in sorted(p.nombres)],
                "seleccion_s": round(sum(pe.duracion for pe in pe_lista if id(pe) in seleccion), 1),
                "ganador": kills.ganador_partida(p),
                **ancla,
            })
            game_clips = [_pelea_dict(p, pe, i, j, seleccion) for j, pe in enumerate(pe_lista, 1)]
            pauses = clock.pauses(p.frames)
            datos_partidas[-1]["pauses"] = [[_iso(x), _iso(y)] for x, y in pauses]
            if ancla.get("clock0_utc"):
                _clock_titles(game_clips, datetime.fromisoformat(ancla["clock0_utc"]), pauses)
            clips += game_clips
        ids_sel = [c["id"] for c in sorted(clips, key=lambda c: (c["partida"], c["clip_inicio"]))
                   if c["seleccionada"]]
        # Con UNA sola coincidencia siempre se puede alinear algo por casualidad:
        # el modo auto solo sigue si todas las anclas tienen 2+ kills alineadas.
        dudosas = [d["n"] for d in datos_partidas if d["confianza"] != "alta"]
        lejos = [f"{d['n']} ({d['desfase_s']:+.1f} s)" for d in datos_partidas if d.get("suspicious")]
        _SESIONES[job_id] = {"params": params, "vod": vod, "partidas": datos_partidas, "clips": clips,
                             "carpeta": carpeta, "voz": (params.intro_audio_url, voz) if voz else None}
        # transcript=1: one transcription per game, one after the other, from the
        # moment phase 1 is done (their ids go in this job's status).
        transcripts = [(new_transcript_job(job_id, params.vod_url, d["game_id"]), job_id, d["game_id"], "es",
                        DRAFT_LEAD_S, POST_S, params.carpeta_dia, params.partido,
                        params.mapa + d["n"] - 1 if len(datos_partidas) > 1 else params.mapa)
                       for d in datos_partidas] if params.transcript else []
        job_manager.update_job(
            job_id, status="done", progress=100, stage="esperando_ancla", finished=False,
            transcript_job_id=transcripts[0][0] if transcripts else None,
            transcript_jobs=[{"game_id": t[2], "job_id": t[0]} for t in transcripts] or None,
            vod={"url": params.vod_url, "titulo": vod["titulo"], "inicio_utc": _iso(inicio),
                 "platform": vod.get("platform"), "offset_formula_s": offset},
            partidas=datos_partidas, clips=clips, seleccion=ids_sel,
            aviso=(f"Ancla dudosa en la partida {', '.join(map(str, dudosas))}: revisala antes de renderizar."
                   + (f" Desfase demasiado lejos de lo esperado en la partida {', '.join(lejos)}: "
                      "¿el frame de control muestra la partida correcta?" if lejos else "")
                   if dudosas else None),
        )
        save_session(job_id)
        run.set(platform=vod.get("platform"), anchors=[d["confianza"] for d in datos_partidas],
                clock_calibrated=[bool(d.get("clock0_utc")) for d in datos_partidas],
                suspicious=sum(1 for d in datos_partidas if d.get("suspicious")),
                fights=len(clips), warning=bool(dudosas))
        run.finish("ok")
        if transcripts:
            threading.Thread(target=transcribe_in_a_row, args=(transcripts,), daemon=True).start()
        if params.modo == "auto" and not dudosas:
            finish(job_id, FinishParams.desde(params))
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=str(exc))
        run.finish("error", str(exc))


# --- Fase 2 ---------------------------------------------------------------------
def _render_con_respaldo(render, enc: intro.Encuentro, carpeta: str):
    """render_portada / render_placa; si falla con logos, de nuevo con los
    códigos de los equipos: la portada nunca tumba el job."""
    try:
        return render(enc, carpeta)
    except RuntimeError:
        if not (enc.logo_azul or enc.logo_rojo):
            raise
        enc.logo_azul = enc.logo_rojo = None
        return render(enc, carpeta)


def _encuentro(fp: FinishParams, sesion: dict) -> intro.Encuentro:
    """Datos de la portada: lo que mandó Claude/el usuario y, si falta, el feed
    (códigos de equipo del mapa, fecha local de la partida)."""
    d = sesion["partidas"][0]
    marca = (os.path.join(config.WATERMARKS_FOLDER, fp.watermark)
             if fp.marca_4xl and fp.watermark else None)
    return intro.Encuentro(
        mapa=fp.mapa, codigo_azul=d["azul"], codigo_rojo=d["rojo"],
        azul=fp.equipo_azul, rojo=fp.equipo_rojo, fase=fp.fase,
        logo_azul=intro.logo(fp.logo_azul_url), logo_rojo=intro.logo(fp.logo_rojo_url),
        serie_marcador=fp.serie_marcador,
        fecha=datetime.fromisoformat(d["inicio_utc"]).astimezone().date(), marca=marca,
    )


def _tournament_stats(job_id: str, sesion: dict) -> dict:
    """The tournament's games for the cards' backs: the ones in the store plus the games of every
    saved phase 1 and of this series (their final frame, one call each, only the ones not yet there)."""
    try:
        stats.update(list(dict.fromkeys(stats.session_games() + [d["game_id"] for d in sesion["partidas"]])))
        for d in sesion["partidas"]:
            if d.get("ganador"):
                stats.set_winner(d["game_id"], d["ganador"])
        return stats.load()
    except Exception as exc:  # noqa: BLE001 - the backs fall back to the game's own numbers
        print(f"[competitivo] estadísticas del torneo: {str(exc)[:80]}".encode("ascii", "replace").decode())
        return {}


def _freeze_of(plan: "card_plan.Plan", opts, desde: float, dur: float, folder: str) -> "layout.Freeze | None":
    """The freeze of a clip with its cards drawn: the frames of each card go to `folder`."""
    t_out = min(max(plan.t - desde, 0.5), max(0.5, dur - 1.0))
    overlays = []
    for i, p in enumerate(plan.ok):
        sub_dir = os.path.join(folder, f"{i}_{cards._plain(p.player)}")
        cards.render(p.card, sub_dir, opts.dur_s, delay=0.08 * i, style=opts.style)
        overlays.append(layout.CardOverlay(os.path.join(sub_dir, "f%04d.png"), p.out[0], p.out[1], p.scale))
    return layout.Freeze(t_out, opts.dur_s, opts.mode, cards=overlays) if overlays else None


def _save_preview(job_id: str, clip_id: str, plan: "card_plan.Plan", previews: list[dict]) -> None:
    """The preview image of a clip (the frame with its bars) in outputs/competitivo/<job>/cards_<clip>.jpg."""
    import cv2
    img = card_plan.preview_image(plan)
    if img is None:
        previews.append({"clip": clip_id, "image": None, **plan.info()})
        return
    folder = os.path.join(TRABAJO, job_id)
    os.makedirs(folder, exist_ok=True)
    cv2.imwrite(os.path.join(folder, f"cards_{clip_id}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    previews.append({"clip": clip_id, "image": f"cards_{clip_id}.jpg", **plan.info()})


def finish(job_id: str, fp: FinishParams) -> None:
    sesion = get_session(job_id)
    temp = dest = None
    run = metrics.Run("resumen", job_id, preset=_preset(job_id), zoom_mode=fp.zoom_mode,
                      overrides=len(fp.kill_overrides or []), creator=bool(fp.creator_vod_url))
    try:
        if not sesion:
            raise RuntimeError("La sesión ya no existe (¿se reinició el servidor?). Volvé a generar la propuesta.")
        params: CompetitivoParams = sesion["params"]
        inicio: datetime = sesion["vod"]["inicio_utc"]
        partidas = {d["game_id"]: d for d in sesion["partidas"]}
        # offset de cada partida a partir de su ancla (segundo de la primera kill)
        offsets = {}
        for gid, d in partidas.items():
            ancla = fp.anclas.get(gid, d["ancla_s"])
            fb = datetime.fromisoformat(d["primera_kill_utc"])
            offsets[gid] = ancla - (fb - inicio).total_seconds()
        por_id = {c["id"]: c for c in sesion["clips"]}
        ids = fp.clips or [c["id"] for c in sorted(sesion["clips"], key=lambda c: (c["partida"], c["clip_inicio"]))
                           if c["seleccionada"]]
        faltan = [i for i in ids if i not in por_id]
        if faltan:
            raise RuntimeError(f"No existe el clip {faltan[0]}.")
        elegidos = sorted((por_id[i] for i in ids), key=lambda c: (c["partida"], c["clip_inicio"]))
        if not elegidos:
            raise RuntimeError("No hay clips para renderizar.")

        job_manager.update_job(job_id, status="processing", progress=1, finished=False,
                               stage="Armando la portada…", error=None)
        temp = os.path.join(sesion["carpeta"], "tramos")
        os.makedirs(temp, exist_ok=True)
        # Portada (siempre) y voz de la intro (si hay link y el modo no es off),
        # ANTES de bajar nada: un link roto o una voz larga fallan enseguida.
        enc = _encuentro(fp, sesion)
        portada = _render_con_respaldo(intro.render_portada, enc, temp)
        voz = None
        if fp.intro_modo != "off" and fp.intro_audio_url:
            job_manager.update_job(job_id, stage="Bajando la voz de la intro…")
            previa = sesion.get("voz")
            if previa and previa[0] == fp.intro_audio_url and os.path.isfile(previa[1].path):
                voz = previa[1]                      # ya bajada y medida en la fase 1
            else:
                voz = intro.preparar_voz(fp.intro_audio_url, temp)
        modo_intro = fp.intro_modo if voz else "off"
        intro_info = {"modo_pedido": fp.intro_modo, "modo_usado": modo_intro,
                      "duracion_s": voz.D if voz else None, "motivo": None, "placa_s": None}
        dest = salida.preparar(sesion["partidas"], fp.mapa, job_id, fp.carpeta_dia, fp.partido, fp.titulo)
        carpeta = dest.carpeta
        intro.guardar_portada(portada, carpeta)
        marca = os.path.join(config.WATERMARKS_FOLDER, fp.watermark) if fp.watermark else None
        intro_mp4 = None
        placa = _render_con_respaldo(intro.render_placa, enc, temp) if modo_intro == "encima" else None

        def render_aparte():
            # Segmento propio, primero en el resumen. Los clips sueltos no llevan intro.
            job_manager.update_job(job_id, stage="Renderizando la intro…")
            path = os.path.join(temp, "intro.mp4")
            ffmpeg_runner.run(intro.build_intro_aparte(portada["png"], voz, path), job_id,
                              total_duration=voz.D, progress_range=(1, 5))
            job_manager.update_job(job_id, status="processing")
            return path

        if modo_intro == "aparte":
            intro_mp4 = render_aparte()
        job_manager.update_job(job_id, status="processing", stage="Bajando los tramos del VOD…")

        def ventana(c):
            off = offsets[c["game_id"]]
            a = _vod(datetime.fromisoformat(c["clip_inicio"]), inicio, off) - MARGEN_CLIP_S
            b = _vod(datetime.fromisoformat(c["clip_fin"]), inicio, off) + MARGEN_CLIP_S
            return a, b

        n = len(elegidos)
        cam_of = (creator.CreatorCam(fp.creator_vod_url, inicio, fp.creator_cam, fp.creator_delta_s)
                  if fp.creator_vod_url else None)
        overrides = camera.overrides_from(fp.kill_overrides, {
            f"{c['id']}#{n_k}": (clock.parse(ck) if ck else None)
            for c in elegidos for n_k, ck in enumerate(c.get("kills_reloj") or [""] * len(c["eventos"]), 1)})
        hechos = []
        skipped: list[str] = []        # clips whose section came without video (twice)
        en_resumen: list[str] = []     # lo que entra al resumen (el clip 1 puede llevar la intro)
        clip_log: list[dict] = []      # per clip: download, wait, analysis and render seconds (the metrics)
        card_names = fp.cards.players
        tournament, card_games = None, {d["game_id"]: d for d in sesion["partidas"]}
        previews: list[dict] = []      # cards_preview: the frame of each clip with its bars
        if card_names:
            tournament = _tournament_stats(job_id, sesion)
            os.makedirs(os.path.join(temp, "cards"), exist_ok=True)

        def download(k, c):
            t0 = time.monotonic()
            path = fuente.bajar_tramo(params.vod_url, *ventana(c), os.path.join(temp, f"tramo_{k:02d}"),
                                      job_id=job_id)
            return path, time.monotonic() - t0

        def analyze(k, c, fut):
            """What the layout has to know about a clip (the kill counter, the live part, Riot's split
            camera, where the action is). It runs while the clip before it is being rendered."""
            t0 = time.monotonic()
            try:
                tramo, download_s = fut.result()
            except fuente.SpanWithoutVideo:
                return None
            wait_s = time.monotonic() - t0
            with scheduler.slot("analysis", job_id) as queue_s:
                t1 = time.monotonic()
                off = offsets[c["game_id"]]
                a, _ = ventana(c)
                scan = layout.Scan(tramo, layout.SCAN_CLIP)        # ONE decode for all of it
                # Ajuste fino con las kills del propio clip (el ancla es por partida).
                vistos = layout.cambios_kills(tramo, scan=scan)
                esp = [(_vod(datetime.fromisoformat(ts), inicio, off) - a, lado) for ts, lado in c["eventos"]]
                ajuste, coinc = layout.estimar_desfase(vistos, esp, rango=RANGO_AJUSTE_S)
                if coinc < min(2, len(esp)):
                    ajuste = 0.0          # una sola coincidencia en una pelea puede ser casualidad
                ini = _vod(datetime.fromisoformat(c["clip_inicio"]), inicio, off) - a + ajuste
                fin = _vod(datetime.fromisoformat(c["clip_fin"]), inicio, off) - a + ajuste
                t_kill = _vod(datetime.fromisoformat(c["primera"]), inicio, off) - a + ajuste
                dur_tramo = ffmpeg_runner.probe_duration(tramo) or (fin + MARGEN_CLIP_S)
                ini, fin = max(0.0, ini), min(dur_tramo, fin)
                an = layout.analizar(tramo, t_kill, ini, fin, scan=scan)
                if cam_of:
                    tramo = cam_of.apply(tramo, a, a + dur_tramo, an.camara or (layout.CAMARA_X, an.camara_y,
                                                                              layout.CAMARA_W, layout.CAMARA_H),
                                         temp, f"{k:02d}")
                    scan = layout.Scan(tramo, ("strip", "track"))   # the composite replaced the span
                split = layout.split_ranges(tramo, scan=scan)      # Riot's split camera: the whole view
                track, shots = None, []
                if fp.zoom_mode != "zoom" or overrides:
                    # Following the action (or the full view where it is not clear).
                    frames = kill_frames(vistos, esp, ajuste, KILL_LEAD_S)
                    ids = [f"{c['id']}#{n_k}" for n_k in range(1, len(frames) + 1)]
                    tracks = {kid: (None if any(x <= t <= y for x, y in split)
                                    else layout.action_track(tramo, t, scan=scan))
                              for kid, t in zip(ids, frames)}
                    track, full, shots = camera.plan(list(zip(ids, frames)), tracks, fp.zoom, fp.zoom_mode,
                                                     overrides, span=(0.0, dur_tramo))
                    split = sorted(list(split) + full)
                plan = None
                if card_names and c["game_id"] in card_games:
                    plan = card_plan.plan_clip(
                        tramo, an, t_kill, c, card_games[c["game_id"]], card_names, fp.cards, fp.zoom, split, track,
                        utc_at=lambda tt: inicio + timedelta(seconds=a + tt - off - ajuste),
                        caption=fp.fase or "Mundial 2026", tournament=tournament)
                    if fp.cards.preview:
                        _save_preview(job_id, c["id"], plan, previews)
            return {"tramo": tramo, "a": a, "ajuste": ajuste, "coinc": coinc, "t_kill": t_kill, "an": an,
                    "split": split, "track": track, "shots": shots, "download_s": download_s, "wait_s": wait_s,
                    "queue_s": queue_s, "analysis_s": time.monotonic() - t1, "cards": plan}

        ex = ThreadPoolExecutor(max_workers=3)
        an_ex = ThreadPoolExecutor(max_workers=1)    # in order: the creator's camera keeps its sync from clip to clip
        try:
            futuros = [ex.submit(download, k, c) for k, c in enumerate(elegidos, 1)]
            analisis = [an_ex.submit(analyze, k, c, f) for k, (c, f) in enumerate(zip(elegidos, futuros), 1)]
            for k, (c, afut) in enumerate(zip(elegidos, analisis), 1):
                job_manager.update_job(job_id, stage=f"Clip {k}/{n}: bajando y analizando el tramo…")
                r = afut.result()
                if r is None:
                    skipped.append(c["titulo"])
                    continue
                tramo, a, ajuste, coinc, t_kill = r["tramo"], r["a"], r["ajuste"], r["coinc"], r["t_kill"]
                an, split, track, shots = r["an"], r["split"], r["track"], r["shots"]
                first = not hechos           # the first clip that made it carries the intro
                dur = an.vivo_hasta - an.vivo_desde
                if dur < 3:
                    raise RuntimeError(f"El clip «{c['titulo']}» cae casi entero fuera del vivo "
                                       "(repetición o pausa). Quitalo o corregí el ancla.")
                archivo = f"clip_{len(hechos) + 1:02d}.mp4"
                if first and modo_intro == "encima":
                    modo_intro, motivo = intro.modo_para_clip1(voz.D, dur, t_kill - an.vivo_desde)
                    if modo_intro == "aparte":
                        intro_info.update(modo_usado="aparte", motivo=motivo)
                        intro_mp4 = render_aparte()
                    else:
                        intro_info["placa_s"] = intro.fin_de_placa(voz.D, t_kill - an.vivo_desde,
                                                                   fp.placa_max_s)
                plan = r["cards"]
                if fp.cards.preview:                       # only the frames with the bars: nothing is rendered
                    os.remove(tramo)
                    continue
                freeze, cards_info = None, plan.info() if plan else None
                if plan and plan.ok:
                    freeze = _freeze_of(plan, fp.cards, an.vivo_desde, dur, os.path.join(temp, "cards", c["id"]))
                    if freeze and first and modo_intro == "encima" and freeze.t < (voz.D if voz else 0) + 0.5:
                        freeze = None                       # the intro's plate is on the screen: no cards there
                        cards_info["notes"].append("La intro está en pantalla en ese momento: sin tarjetas en este clip.")
                job_manager.update_job(job_id, stage=f"Clip {k}/{n}: renderizando…")
                t_render = time.monotonic()
                render = dict(zoom=fp.zoom, color=fp.color, marca_path=marca, marca_alto=fp.marca_alto)
                has_audio = ffmpeg_runner.has_audio_stream(tramo) if freeze else True
                cmd = layout.build_command(tramo, os.path.join(carpeta, archivo), an.vivo_desde, dur,
                                           an.camara or an.camara_y, split=split, crop_track=track, freeze=freeze,
                                           con_audio=has_audio, **render)
                lo, hi = 5 + int(85 * (k - 1) / n), 5 + int(85 * k / n)
                con_intro = first and modo_intro == "encima"
                held = freeze.dur if freeze else 0.0
                ffmpeg_runner.run(cmd, job_id, total_duration=dur + held,
                                  progress_range=(lo, (lo + hi) // 2 if con_intro else hi))
                en_resumen.append(os.path.join(carpeta, archivo))
                if con_intro:
                    # El clip suelto queda limpio; la versión con la intro encima es
                    # solo para el resumen.
                    job_manager.update_job(job_id, status="processing",
                                           stage="Clip 1: renderizando con la intro encima…")
                    con = os.path.join(temp, "clip_01_intro.mp4")
                    cmd = layout.build_command(
                        tramo, con, an.vivo_desde, dur, an.camara or an.camara_y, split=split, crop_track=track,
                        **render, freeze=freeze,
                        intro=layout.IntroEncima(placa=placa, voz=voz.path, D=voz.D, sting=intro.sting(),
                                                 sting_vol=intro.STING_VOL, voz_filtro=intro.cadena_voz(voz),
                                                 sting_filtro=intro.cadena_sting(voz.D),
                                                 volumen_juego=fp.intro_volumen_juego / 100,
                                                 placa_fin=intro_info["placa_s"]),
                        con_audio=ffmpeg_runner.has_audio_stream(tramo))
                    ffmpeg_runner.run(cmd, job_id, total_duration=dur + held, progress_range=((lo + hi) // 2, hi))
                    en_resumen[-1] = con
                job_manager.update_job(job_id, status="processing")
                dur += held                                # the clip lasts what the freeze added
                clip_log.append({"id": c["id"], "video_s": round(dur, 1), "download_s": round(r["download_s"], 1),
                                 "wait_s": round(r["wait_s"], 1), "queue_s": round(r["queue_s"], 1),
                                 "analysis_s": round(r["analysis_s"], 1),
                                 "render_s": round(time.monotonic() - t_render, 1)})
                hechos.append({
                    "archivo": archivo, "id": c["id"], "titulo": c["titulo"], "partida": c["partida"],
                    "puntaje": c["puntaje"], "duracion": round(dur, 2),
                    "vod_inicio": round(a + an.vivo_desde, 2), "vod_fin": round(a + an.vivo_hasta, 2),
                    "ajuste_s": ajuste, "kills_vistas": coinc, "kills_esperadas": len(c["eventos"]),
                    "fuera_del_vivo_s": an.no_vivo_s, "camara_y": an.camara_y,
                    "camera": [sh.as_dict() for sh in shots],
                    "split_camera": [[round(a - an.vivo_desde, 2), round(b - an.vivo_desde, 2)] for a, b in split
                                     if b > an.vivo_desde and a < an.vivo_hasta],
                    **({"cards": cards_info} if cards_info else {}),
                })
                os.remove(tramo)
        finally:
            an_ex.shutdown(wait=True, cancel_futures=True)
            ex.shutdown(wait=True, cancel_futures=True)
        run.set(clips_detail=clip_log)
        run.mark("clips")
        if fp.cards.preview:
            # The frame of each clip with its bars numbered and who got which: to correct (card_pos) and render.
            salida.descartar(dest)
            job_manager.update_job(job_id, status="done", progress=100, stage="esperando_tarjetas", finished=False,
                                   cards_preview=previews, aviso=None)
            run.set(preview=True, cards_clips=len(previews))
            run.finish("paused")
            return
        if not hechos:
            raise RuntimeError("Ningún tramo del VOD bajó con video (cada uno se intentó dos veces).")
        aviso = (f"Se saltó {'el clip' if len(skipped) == 1 else 'los clips'} "
                 f"{', '.join(f'«{t}»' for t in skipped)}: el tramo bajó sin video dos veces."
                 if skipped else None)
        if cam_of and cam_of.notes:
            aviso = " ".join(([aviso] if aviso else []) + cam_of.notes)

        job_manager.update_job(job_id, stage="Uniendo el resumen…", progress=92)
        lista = os.path.join(temp, "lista.txt")
        partes = ([intro_mp4] if intro_mp4 else []) + en_resumen
        with open(lista, "w", encoding="utf-8") as fh:
            for ruta in partes:
                ruta = ruta.replace("\\", "/").replace("'", "'\\''")
                fh.write(f"file '{ruta}'\n")
        resumen = dest.resumen
        ffmpeg_runner.run(layout.build_concat(lista, resumen),
                          job_id, total_duration=sum(h["duracion"] for h in hechos) + (voz.D if intro_mp4 else 0),
                          progress_range=(92, 99))
        duracion_total = round(sum(h["duracion"] for h in hechos) + (voz.D if intro_mp4 else 0), 2)
        intro_res = {"modo_pedido": intro_info["modo_pedido"], "modo_usado": modo_intro,
                     "duracion_s": intro_info["duracion_s"], "motivo": intro_info["motivo"],
                     "placa_s": intro_info["placa_s"],
                     "fondo": portada["fondo"], "logos": portada["logos"]}
        info = {
            "creado": datetime.now(timezone.utc).isoformat(),
            "serie": f"{sesion['partidas'][0]['azul']} vs {sesion['partidas'][0]['rojo']}",
            "mapa": fp.mapa,
            "dia": fp.carpeta_dia or None, "partido": fp.partido or None, "titulo": fp.titulo or None,
            "video": dest.ruta_video,
            "equipos": {"azul": enc.azul, "rojo": enc.rojo},
            "vod": {**sesion["vod"], "inicio_utc": _iso(inicio), "url": params.vod_url},
            "partidas": [{**d, "ancla_usada_s": round(offsets[d["game_id"]] + (
                datetime.fromisoformat(d["primera_kill_utc"]) - inicio).total_seconds(), 2)}
                for d in sesion["partidas"]],
            "clips": hechos,
            "skipped_clips": skipped,
            "portada": "portada.jpg",
            "intro": intro_res,
            "intro_modo_usado": modo_intro,
            "duracion_total": duracion_total,
            "parametros": {**asdict(params), "render": asdict(fp)},
        }
        with open(os.path.join(carpeta, "info.json"), "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False, indent=2)
        # Organizada: lo anterior de este mapa pasa a _descartados y lo nuevo
        # queda en su lugar (recién ahora, cuando ya está todo hecho).
        salida.publicar(dest)
        intro_corto = {"modo_usado": modo_intro, "duracion_s": intro_info["duracion_s"],
                       "motivo": intro_info["motivo"], "placa_s": intro_info["placa_s"]}
        job_manager.update_job(
            job_id, status="done", progress=100, stage="listo", finished=True,
            output_path=dest.video_final, portada="portada.jpg", intro=intro_corto, aviso=aviso,
            resultado={"carpeta": dest.carpeta, "nombre": os.path.basename(dest.carpeta),
                       "video": dest.video_final, "ruta": dest.ruta_video,
                       "resumen": os.path.basename(dest.video_final), "organizada": dest.organizada,
                       "portada": "portada.jpg", "intro": intro_corto,
                       "clips": hechos, "skipped_clips": skipped, "duracion_total": duracion_total},
        )
        save_session(job_id)
        run.mark("concat")
        placed = [p for h in hechos for p in (h.get("cards") or {}).get("cards", [])]
        run.set(cards_asked=len(placed), cards_placed=sum(1 for p in placed if p["where"]),
                cards_ambiguous=sum(1 for p in placed if p["status"] == "ambiguous"),
                cards_not_seen=sum(1 for p in placed if p["status"] == "not_seen"))
        run.set(clips=len(hechos), skipped=len(skipped), video_s=duracion_total, intro=modo_intro,
                warning=bool(aviso), **_shot_counts([s for h in hechos for s in h["camera"]]))
        run.finish("ok")
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
        run.finish("error", str(exc))
        # Lo que quedó a medias se borra (si no, el reintento terminaba en
        # "… (2)" con una carpeta rota al lado); lo publicado antes, nunca.
        salida.descartar(dest)
    finally:
        if temp:
            shutil.rmtree(temp, ignore_errors=True)


# --- Edit mode ------------------------------------------------------------------
def _offsets(sesion: dict, anchors: dict[str, float]) -> dict[str, float]:
    """Offset of each game (vod = utc − start + offset) from its anchor."""
    inicio: datetime = sesion["vod"]["inicio_utc"]
    out = {}
    for d in sesion["partidas"]:
        anchor = anchors.get(d["game_id"], d["ancla_s"])
        fb = datetime.fromisoformat(d["primera_kill_utc"])
        out[d["game_id"]] = anchor - (fb - inicio).total_seconds()
    return out


def team_codes(sesion: dict) -> set[str]:
    return {x.upper() for d in sesion["partidas"] for x in (d["azul"], d["rojo"]) if x}


def team_clips(sesion: dict, team: str) -> list[str]:
    """Every fight and pick of phase 1 where `team` killed (eligible or not):
    the blue team's kills are on the left of the scoreboard ("izq")."""
    team = team.upper()
    side = {d["game_id"]: ("izq" if d["azul"].upper() == team else "der" if d["rojo"].upper() == team else None)
            for d in sesion["partidas"]}
    return [c["id"] for c in sesion["clips"] if side.get(c["game_id"])
            and any(s_ == side[c["game_id"]] for _, s_ in c["eventos"])]


def _chosen_clips(sesion: dict, ids: list[str] | None, team: str = "") -> list[dict]:
    by_id = {c["id"]: c for c in sesion["clips"]}
    if team and not ids:
        ids = team_clips(sesion, team)
        if not ids:
            raise RuntimeError(f"{team} no mató en ninguna pelea de la fase 1.")
    ids = ids or [c["id"] for c in sesion["clips"] if c["seleccionada"]]
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise RuntimeError(f"No existe el clip {missing[0]}.")
    chosen = sorted((by_id[i] for i in ids), key=lambda c: (c["partida"], c["clip_inicio"]))
    if not chosen:
        raise RuntimeError("No hay clips para el edit.")
    return chosen


def _edit_destination(ep: EditParams, sesion: dict) -> tuple[str, str]:
    """(folder, name) of the edit. With day and match, next to the summaries
    as "Edit Mapa N - <title>.mp4" (it does not start with "Mapa", so publicar()
    never moves it to _descartados); otherwise in EDITS_FOLDER per series.
    The final name ("(2)"… if taken) is reserved only once the render is done."""
    name = f"Edit Mapa {ep.mapa}" + (f" - {ep.titulo}" if ep.titulo else "") + ".mp4"
    if ep.carpeta_dia and ep.partido:
        folder = os.path.join(config.COMPETITIVO_FOLDER, ep.carpeta_dia, ep.partido)
    else:
        d = sesion["partidas"][0]
        date = datetime.fromisoformat(d["inicio_utc"]).astimezone().strftime("%Y-%m-%d")
        folder = os.path.join(config.EDITS_FOLDER, salida.limpiar(f"{date} {d['azul']} vs {d['rojo']}"))
    os.makedirs(folder, exist_ok=True)
    return folder, name


def _voice_words(spans: list[dict], language: str, work: str, terms: dict | None = None) -> list[dict]:
    """Ibai's words in the real-time shots ("acts"), in OUTPUT time: each shot's
    audio is transcribed (faster-whisper) and shifted to where the shot goes.
    A shot without a voice simply adds nothing. `terms` (vocab.game_terms):
    the game's names, as Whisper's prompt and hotwords."""
    from modules.subtitles import processor as subtitles
    from modules.subtitles.schema import SubtitlesParams
    params = SubtitlesParams(language=language)
    initial_prompt, hotwords = vocab.prompt(terms) if terms else (None, None)
    out: list[dict] = []
    for k, s in enumerate(spans):
        dur = s["end"] - s["start"]
        wav = os.path.join(work, f"voz_{k}.wav")
        ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-y", "-ss", f"{s['start']:.3f}",
                               "-t", f"{dur:.3f}", "-i", s["source"], "-vn", "-ac", "1", "-ar", "16000", wav])
        try:
            segments = subtitles.transcribe(wav, params, initial_prompt=initial_prompt, hotwords=hotwords)
        except RuntimeError:
            continue
        finally:
            file_utils.cleanup_paths(wav)
        for seg in segments:
            for w in seg["words"]:
                if w["start"] >= dur - 0.05:
                    continue
                out.append({"word": w["word"], "start": round(s["out"] + w["start"], 3),
                            "end": round(s["out"] + min(w["end"], dur), 3)})
    return out


def edit_text_y(zoom: float) -> float:
    """Height of the kill titles: the top third of the gameplay (it does not
    cover the center, where the observer puts the fight)."""
    g = layout.geometria(zoom)
    return (layout.ARRIBA_H + g.extra_arriba + g.juego_h * 0.3) / layout.H


def edit(job_id: str, phase1_id: str, ep: EditParams, music_path: str | None = None) -> None:
    """The phase 1 fights to the rhythm of a song (see competitivo/edit.py).

    Downloads the same spans as the summary (with the per-clip fine
    adjustment), but instead of the layout with the casters it builds an EDL
    where each kill lands on a beat and the key fight on the drop, and renders
    it with the editing engine."""
    sesion = get_session(phase1_id)
    temp = downloaded = None
    paused = False
    run = metrics.Run("edit", job_id, preset=_preset(job_id), structure=ep.structure, short=ep.short,
                      overrides=len(ep.kill_overrides or []), moments=len(ep.moments or []),
                      creator=bool(ep.creator_vod_url), storyboard=ep.storyboard, review=ep.review)
    try:
        if not sesion:
            raise RuntimeError("La sesión ya no existe (¿se reinició el servidor?). Volvé a generar la propuesta.")
        params: CompetitivoParams = sesion["params"]
        inicio: datetime = sesion["vod"]["inicio_utc"]
        offsets = _offsets(sesion, ep.anchors)
        chosen = _chosen_clips(sesion, ep.clips, ep.team)
        if ep.moments:
            chosen = sorted(chosen + [_moment_clip(sesion, m, n) for n, m in enumerate(ep.moments, 1)],
                            key=lambda c: (c["partida"], c["clip_inicio"]))
        temp = os.path.join(sesion["carpeta"], f"edit_{job_id}")
        os.makedirs(temp, exist_ok=True)
        job_manager.update_job(job_id, status="processing", progress=1, finished=False, stage="Preparando la música…")
        if ep.music:
            music_path = downloaded = beatmap.download_music(job_id, ep.music)
        if not music_path:
            raise RuntimeError("Falta la música del edit.")
        bm = beatmap.get(music_path, ep.engine, ep.lyrics, job_id=job_id)
        run.mark("music")
        job_manager.update_job(job_id, status="processing", progress=12, stage="Bajando los tramos del VOD…")

        def window(c):
            off = offsets[c["game_id"]]
            a = _vod(datetime.fromisoformat(c["clip_inicio"]), inicio, off) - MARGEN_CLIP_S
            b = _vod(datetime.fromisoformat(c["clip_fin"]), inicio, off) + MARGEN_CLIP_S
            return a, b

        raw: list[dict] = []                    # the analysis of each span (to re-plan the camera)
        cam_of = (creator.CreatorCam(ep.creator_vod_url, inicio, ep.creator_cam, ep.creator_delta_s)
                  if ep.creator_vod_url else None)
        skipped: list[str] = []
        split_info: dict[str, list] = {}
        span_log: list[dict] = []

        def download(k, c):
            t0 = time.monotonic()
            path = fuente.bajar_tramo(params.vod_url, *window(c), os.path.join(temp, f"tramo_{k:02d}"),
                                      job_id=job_id)
            return path, time.monotonic() - t0

        def analyze_span(k, c, fut):
            try:
                span, download_s = fut.result()
            except fuente.SpanWithoutVideo:
                return None
            with scheduler.slot("analysis", job_id) as queue_s:
                t1 = time.monotonic()
                off = offsets[c["game_id"]]
                a, _ = window(c)
                scan = layout.Scan(span, layout.SCAN_EDIT if ep.ambient else layout.SCAN_CLIP)   # ONE decode
                seen = layout.cambios_kills(span, scan=scan)
                expected = [(_vod(datetime.fromisoformat(ts), inicio, off) - a, side) for ts, side in c["eventos"]]
                shift, matches = layout.estimar_desfase(seen, expected, rango=RANGO_AJUSTE_S)
                if matches < min(2, len(expected)):
                    shift = 0.0
                start = _vod(datetime.fromisoformat(c["clip_inicio"]), inicio, off) - a + shift
                end = _vod(datetime.fromisoformat(c["clip_fin"]), inicio, off) - a + shift
                t_kill = _vod(datetime.fromisoformat(c["primera"]), inicio, off) - a + shift
                span_dur = ffmpeg_runner.probe_duration(span) or (end + MARGEN_CLIP_S)
                an = layout.analizar(span, t_kill, max(0.0, start - 4), min(span_dur, end), scan=scan)
                box = an.camara or an.camara_y
                if cam_of:
                    span = cam_of.apply(span, a, a + span_dur, an.camara or (layout.CAMARA_X, an.camara_y,
                                                                             layout.CAMARA_W, layout.CAMARA_H),
                                        temp, c["id"])
                    scan = layout.Scan(span, ("strip", "track") + (("amb_marc", "amb_rgb") if ep.ambient else ()))
                split = layout.split_ranges(span, scan=scan)
                authors = c.get("authors") or [None] * len(c["eventos"])
                counts = c.get("counts") or [1] * len(c["eventos"])
                clocks = c.get("kills_reloj") or [""] * len(c["eventos"])
                # The kill's frame: when the scoreboard's counter goes up (not the feed's time).
                frames = kill_frames(seen, expected, shift, ep.kill_lead_s)
                ks = [edit_plan.EditKill(t_kill, side, (au or {}).get("player", ""), (au or {}).get("champion", ""),
                                         n, id=f"{c['id']}#{n_k}", clock=ck)
                      for n_k, (t_kill, (_, side), au, n, ck) in enumerate(zip(frames, expected, authors, counts,
                                                                                clocks), 1)]
                # Where the action is in the 1.5 s before each kill (none inside the
                # split camera: that is already the full view).
                tracks = {}
                for kk in ks:
                    if not any(x <= kk.t <= y for x, y in split):
                        try:
                            tracks[kk.id] = layout.action_track(span, kk.t, scan=scan)
                        except RuntimeError:
                            tracks[kk.id] = None
                feat = None
                if ep.structure == "acts" and ep.voice_peaks:
                    try:
                        feat = voice.features(span, an.vivo_desde, an.vivo_hasta - an.vivo_desde)
                    except RuntimeError:
                        feat = None
                if c.get("moment"):
                    c = {**c, "moment": {**c["moment"], "t": t_kill}}       # its instant in the span
                entry = {"c": c, "span": span, "an": an, "split": split, "box": box, "kills": ks,
                         "tracks": tracks, "feat": feat,
                         "ambient": layout.ambient_ranges(span, scan=scan) if ep.ambient else []}
            return entry, {"id": c["id"], "download_s": round(download_s, 1), "queue_s": round(queue_s, 1),
                           "analysis_s": round(time.monotonic() - t1, 1)}

        ex = ThreadPoolExecutor(max_workers=3)
        # The spans are analyzed at the same time (as many as the "analysis" slots allow),
        # but one at a time when the creator's camera is used: it keeps its sync from span to span.
        an_ex = ThreadPoolExecutor(max_workers=1 if cam_of else scheduler.capacity_of("analysis"))
        try:
            futures = [ex.submit(download, k, c) for k, c in enumerate(chosen, 1)]
            analyses = [an_ex.submit(analyze_span, k, c, f) for k, (c, f) in enumerate(zip(chosen, futures), 1)]
            for k, (c, afut) in enumerate(zip(chosen, analyses), 1):
                job_manager.update_job(job_id, stage=f"Pelea {k}/{len(chosen)}: bajando y sincronizando…",
                                       progress=12 + int(18 * (k - 1) / len(chosen)))
                r = afut.result()
                if r is None:
                    skipped.append(c["titulo"])
                    continue
                entry, info = r
                raw.append(entry)
                span_log.append(info)
                split_info[entry["c"]["id"]] = [[round(x, 2), round(y, 2)] for x, y in entry["split"]]
        finally:
            an_ex.shutdown(wait=True, cancel_futures=True)
            ex.shutdown(wait=True, cancel_futures=True)
        run.set(spans_detail=span_log)
        # Ibai's peaks: against the medians of HIS game (every span of it together).
        by_game: dict[str, list[dict]] = {}
        for r in raw:
            if r["feat"] is not None:
                by_game.setdefault(r["c"]["game_id"], []).append(r["feat"])
        medians = {gid: voice.medians(feats) for gid, feats in by_game.items()}
        for r in raw:
            r["peaks"] = voice.peaks(r["feat"], *medians[r["c"]["game_id"]]) if r["feat"] is not None else []
            r["feat"] = None

        run.mark("spans")
        if not raw:
            raise RuntimeError("Ningún tramo del VOD bajó con video (cada uno se intentó dos veces).")
        job_manager.update_job(job_id, stage="Armando el edit al ritmo…", progress=30)
        overrides = camera.overrides_from(ep.kill_overrides, _kill_clocks(raw))
        if cam_of and cam_of.notes:
            skipped_notes = cam_of.notes
        else:
            skipped_notes = []
        state = {"bm": bm, "ep": ep, "sesion": sesion, "temp": temp, "raw": raw, "split_info": split_info,
                 "music_path": music_path, "downloaded": downloaded, "skipped": skipped, "notes": skipped_notes}
        res, plan_info = _plan_edit(job_id, state, overrides)
        edl = res.edl
        state.update(edl=edl, res=res, plan=plan_info)
        run.mark("plan")
        run.set(clips=len(raw), skipped=len(skipped), drop_aligned=res.drop_aligned, **_edit_shots(state))
        if ep.structure == "acts" and ep.voice_subs and res.voice_spans:
            job_manager.update_job(job_id, stage="Transcribiendo a Ibai…", progress=31)
            terms = vocab.game_terms([r for c in chosen for r in _roster(sesion, c["game_id"])],
                                     _teams(sesion, chosen, ep))
            words = vocab.correct(_voice_words(res.voice_spans, ep.voice_language, temp, terms))
            edl.subtitle_words = words
            edl.subtitle_style = dict(ep.subs_style or {})
            if ep.review:
                job_manager.update_job(job_id, segments=review_segments(words, res.voice_spans), vocabulary=terms)
            run.mark("voice")
        if ep.review or ep.storyboard:
            # A pause before the long render: Ibai's segments to fix the names
            # (review) and/or a storyboard with a thumbnail per kill to correct
            # the camera (storyboard); /edit/finish resumes with the fixes.
            if ep.storyboard:
                job_manager.update_job(job_id, stage="Armando el storyboard…", progress=32)
                job_manager.update_job(job_id, storyboard=_storyboard(job_id, state))
            _EDIT_REVIEW[job_id] = state
            paused = True
            job_manager.update_job(job_id, status="done", progress=33, stage="esperando_revision", finished=False)
            run.finish("paused")
            return
        try:
            result = _render_edit(job_id, state)
            run.mark("render")
            run.set(video_s=result["total_duration"], warning=bool(res.warning))
            run.finish("ok")
        except Exception as exc:  # noqa: BLE001
            # The plan, the spans and Ibai's words stay: /edit/finish renders again.
            _EDIT_REVIEW[job_id] = state
            state["failed"] = True
            paused = True
            job_manager.update_job(job_id, status="error", error=str(exc), retryable=True)
            run.set(retryable=True)
            run.finish("error", str(exc))
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
        run.finish("error", str(exc))
    finally:
        if not paused:
            _cleanup_edit(temp, downloaded, music_path)


def _cleanup_edit(temp: str | None, downloaded: str | None, music_path: str | None) -> None:
    if temp:
        shutil.rmtree(temp, ignore_errors=True)
    if downloaded:
        file_utils.cleanup_paths(downloaded)
    elif music_path and music_path.startswith(config.UPLOAD_FOLDER):
        file_utils.cleanup_paths(music_path)


def _edit_shots(state: dict) -> dict:
    return _shot_counts([s for info in state["plan"]["camera"].values() for s in info.get("shots") or []])


def _render_edit(job_id: str, state: dict) -> dict:
    """The edit's render, from the plan (and Ibai's words, already reviewed)."""
    edl, res, bm, ep, sesion = state["edl"], state["res"], state["bm"], state["ep"], state["sesion"]
    temp, skipped, plan_info = state["temp"], state["skipped"], state["plan"]
    edl.music = state["music_path"]
    edl.look, edl.lut, edl.glow, edl.vignette = ep.look, ep.lut, ep.glow, ep.vignette
    edl.original_volume = ep.game_volume / 100      # "beat"; "acts" brings its own automation
    watermark = os.path.join(config.WATERMARKS_FOLDER, ep.watermark) if ep.watermark else None
    edl.watermark, edl.watermark_height = watermark, ep.marca_alto
    folder, name = _edit_destination(ep, sesion)
    # Rendered aside (same folder, so os.replace works on any drive): a
    # failed render used to leave a broken "Edit Mapa N.mp4" among the
    # summaries, and the retry came out as "(2)".
    partial = os.path.join(folder, f".edit-{job_id}.tmp.mp4")
    try:
        render_info = editing.render(edl, partial, job_id, os.path.join(temp, "render"), progress=(33, 99),
                                     intensity=ep.intensity, text_y=edit_text_y(ep.zoom),
                                     watermark_crop=layout.recorte_marca(watermark) if watermark else None)
    except BaseException:
        file_utils.cleanup_paths(partial)
        raise
    destination = salida._unico(folder, name)
    os.replace(partial, destination)
    result = {
        "video": destination, "folder": os.path.dirname(destination), "name": os.path.basename(destination),
        "download_name": os.path.basename(destination), "total_duration": render_info["dur"],
        "music_start": round(res.music_start, 3), "drop_aligned": res.drop_aligned,
        "bpm": bm["bpm"], "used_clips": res.used, "dropped_clips": res.dropped,
        "warning": res.warning, "skipped_clips": skipped, "plan": plan_info,
        "subtitle_words": len(edl.subtitle_words),
        "edl": edl.to_dict(),
    }
    with open(os.path.splitext(destination)[0] + ".json", "w", encoding="utf-8") as fh:
        json.dump({**result, "created": datetime.now(timezone.utc).isoformat(),
                   "subtitles": edl.subtitle_words,
                   "params": {k: v for k, v in asdict(ep).items() if k not in ("music", "lyrics")}},
                  fh, ensure_ascii=False, indent=2, default=str)
    # `resultado` and `aviso` are the keys the competitivo status already exposes.
    notes = ([res.warning] if res.warning else []) + list(state.get("notes") or [])
    if skipped:
        notes.append(f"Se saltó {'la pelea' if len(skipped) == 1 else 'las peleas'} "
                     f"{', '.join(f'«{t}»' for t in skipped)}: el tramo bajó sin video dos veces.")
    job_manager.update_job(job_id, status="done", progress=100, stage="listo", finished=True,
                           output_path=destination, resultado=result, aviso=" ".join(notes) or None)
    return result


def check_overrides(job_id: str, overrides: list) -> None:
    """ValueError if the corrections do not match the kills of that edit."""
    state = _EDIT_REVIEW.get(job_id)
    if state and overrides:
        camera.overrides_from(overrides, _kill_clocks(state["raw"]))


def edit_waiting(job_id: str) -> bool:
    return job_id in _EDIT_REVIEW


def edit_finish(job_id: str, segments: list[dict] | None, kill_overrides: list | None = None) -> None:
    """Resumes an edit paused for review (review=1) with Ibai's segments as
    they were fixed (None = as they are), or renders again one whose render
    failed: the words get the corrections again and it renders. If it fails
    again it can be retried again."""
    state = _EDIT_REVIEW.pop(job_id, None)
    kept = False
    run = metrics.Run("edit_render", job_id, preset=_preset(job_id))
    try:
        if not state:
            raise RuntimeError("Ese edit no está esperando revisión ni se puede reintentar (¿se reinició el servidor?).")
        if state.pop("failed", False):
            state["retries"] = state.get("retries", 0) + 1
        state["overrides"] = state.get("overrides", len(state["ep"].kill_overrides or [])) + len(kill_overrides or [])
        run.set(retries=1 if state.get("retries") else 0, overrides=state["overrides"],
                reviewed=segments is not None, structure=state["ep"].structure)
        job_manager.update_job(job_id, status="processing", progress=33, stage="Renderizando el edit…",
                               finished=False, error=None, retryable=False)
        if kill_overrides:
            # The camera corrected by hand: same timing, other zooms and views.
            old = state["edl"]
            overrides = camera.overrides_from(kill_overrides, _kill_clocks(state["raw"]))
            res, plan_info = _plan_edit(job_id, state, overrides)
            res.edl.subtitle_words, res.edl.subtitle_style = old.subtitle_words, old.subtitle_style
            state.update(edl=res.edl, res=res, plan=plan_info)
        if segments is not None:
            edl = state["edl"]
            words = [w for w in reviewed_words(segments) if _in_windows(w["start"], edl)]
            edl.subtitle_words = vocab.correct(words)
        try:
            result = _render_edit(job_id, state)
        except Exception:
            _EDIT_REVIEW[job_id] = state
            state["failed"] = kept = True
            raise
        run.set(video_s=result["total_duration"], warning=bool(state["res"].warning), **_edit_shots(state))
        run.finish("ok")
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc), retryable=kept)
        run.set(retryable=kept)
        run.finish("error", str(exc))
    finally:
        if state and not kept:
            _cleanup_edit(state["temp"], state["downloaded"], state["music_path"])


KILL_MATCH_S = 1.0           # a scoreboard change this close to the feed's kill is that kill
KILL_LEAD_S = 0.25           # without a change seen: the feed's time, this much earlier


def kill_frames(seen: list, expected: list, shift: float, lead: float) -> list[float]:
    """Each expected kill on the frame where the scoreboard's counter goes up
    (same side, within KILL_MATCH_S of the feed's time + shift); if no change
    is seen there, the feed's time + shift − lead."""
    out = []
    for t, side in expected:
        t_feed = t + shift
        near = [tv for tv, sv in seen if sv == side and abs(tv - t_feed) <= KILL_MATCH_S]
        out.append(min(near, key=lambda tv: abs(tv - t_feed)) if near else t_feed - lead)
    return out


def _moment_clip(sesion: dict, m: dict, n: int) -> dict:
    """A moment without a kill ({clock, dur, label}) as a fight of the session:
    its span around the scoreboard's clock (MOMENT_LEAD of it before)."""
    d = next(x for x in sesion["partidas"] if x["game_id"] == m["game_id"])
    center = utc_at_clock(d, clock.parse(m["clock"]))
    if center is None:
        raise RuntimeError(f"La partida {d['n']} no tiene el reloj del marcador calibrado: no se puede ubicar "
                           f"el momento del {m['clock']} (repetí la fase 1).")
    lead = edit_plan.MOMENT_LEAD * m["dur"]
    return {"id": f"m{n}", "game_id": d["game_id"], "partida": d["n"], "titulo": m["label"], "puntaje": 0,
            "multikill": None, "cierra": False, "eventos": [], "authors": [], "counts": [], "kills_reloj": [],
            "clip_inicio": _iso(center - timedelta(seconds=lead)),
            "clip_fin": _iso(center + timedelta(seconds=m["dur"] - lead)), "primera": _iso(center),
            "moment": {"dur": m["dur"], "label": m["label"], "clock": m["clock"]}}


def _kill_clocks(raw: list[dict]) -> dict[str, float | None]:
    """Each kill's scoreboard clock, in seconds (None if the game's clock was not calibrated)."""
    out = {}
    for r in raw:
        for k in r["kills"]:
            try:
                out[k.id] = clock.parse(k.clock) if k.clock else None
            except ValueError:
                out[k.id] = None
    return out


def _edit_clips(state: dict, overrides: dict) -> tuple[list, dict]:
    """The EditClips of the edit from the analysis of each span, with its
    camera (camera.plan): the crop following the action, the full view where
    it is not clear (and the split camera), and where each zoom aims."""
    ep = state["ep"]
    clips, shots_info = [], {}
    for r in state["raw"]:
        c, an, box = r["c"], r["an"], r["box"]
        track, full, shots = camera.plan([(k.id, k.t) for k in r["kills"]], r["tracks"], ep.zoom, ep.zoom_mode,
                                         overrides, span=(0.0, an.vivo_hasta + 60))
        by_id = {sh.id: sh for sh in shots}
        ks = [edit_plan.EditKill(k.t, k.side, k.player, k.champion, k.count, by_id[k.id].aim, k.id, k.clock,
                                 by_id[k.id].mode) for k in r["kills"]]
        views = sorted(list(r["split"]) + full)
        clips.append(edit_plan.EditClip(
            c["id"], r["span"], an.vivo_desde, an.vivo_hasta, ks, c["puntaje"], c.get("multikill"),
            c.get("cierra", False), c["titulo"], layout.edit_geometry(box, ep.zoom, ep.with_camera, views, track),
            ambient=r["ambient"], ambient_vf=layout.ambient_geometry(box, ep.with_camera) if ep.ambient else "",
            voice_peaks=r.get("peaks") or [],
            moment_dur=(c.get("moment") or {}).get("dur", 0.0), moment_t=(c.get("moment") or {}).get("t", 0.0)))
        shots_info[c["id"]] = {"shots": [sh.as_dict() for sh in shots], "track": track,
                               "full": [[round(x, 2), round(y, 2)] for x, y in full]}
    return clips, shots_info


def _plan_edit(job_id: str, state: dict, overrides: dict):
    """(EditResult, plan_info) of the edit, and the status updated with it."""
    ep, bm = state["ep"], state["bm"]
    clips, shots_info = _edit_clips(state, overrides)
    op = edit_plan.EditOptions(style=ep.style, speed=ep.speed, max_duration_s=ep.max_duration_s,
                               align_drop=ep.align_drop, music_start=ep.music_start, text=ep.text,
                               music_accents=ep.music_accents, title=ep.edit_title,
                               structure=ep.structure, recipes=ep.recipes,
                               cold_open_s=ep.cold_open_s, build_fights=ep.build_fights,
                               chronological=ep.chronological, teaser_s=ep.teaser_s, short=ep.short,
                               ambient=ep.ambient, kill_before_s=ep.kill_before_s,
                               kill_after_s=ep.kill_after_s, voice_peaks=ep.voice_peaks,
                               last_fight_look=ep.last_fight_look)
    res = edit_plan.plan(clips, bm, op)
    # split_camera: [[a, b]] seconds of each clip's span (to check the detector);
    # camera: per clip, each kill's shot (zoom / full, where) and the crop's track.
    plan_info = {"structure": ep.structure, **res.plan_dict(), "split_camera": state["split_info"],
                 "camera": shots_info, "overrides": overrides,
                 "detected_voice_peaks": {cl.id: cl.voice_peaks for cl in clips if cl.voice_peaks}}
    job_manager.update_job(job_id, plan=plan_info, voice_peaks=res.voice_peaks, split_camera=state["split_info"])
    return res, plan_info


def _storyboard(job_id: str, state: dict) -> dict:
    """The plan with a thumbnail per kill, to correct it before the render."""
    folder = os.path.join(state["temp"], "storyboard")
    spans = {r["c"]["id"]: r for r in state["raw"]}
    camera_info = state["plan"]["camera"]
    shots = []
    for n, k in enumerate(state["res"].kills, 1):
        r = spans.get(k.get("clip"))
        if not r:
            continue
        sh = next((x for x in camera_info[k["clip"]]["shots"] if x["id"] == k.get("id")), None)
        shots.append({"n": n, **k, "shot": sh, "span": r["span"], "track": camera_info[k["clip"]]["track"]})
    return storyboard.build(job_id, shots, folder, state["ep"].zoom)


def _in_windows(t: float, edl) -> bool:
    """Whether a reviewed word falls where Ibai's subtitles may go."""
    if edl.subtitle_windows:
        return any(a - 1e-3 <= t < b for a, b in edl.subtitle_windows)
    return edl.subtitle_until is None or t < edl.subtitle_until


def review_segments(words: list[dict], spans: list[dict]) -> list[dict]:
    """Ibai's words for the review, one segment per real-time shot (output
    time): {id, start, end, text, words}. A segment whose text comes back
    changed gets its timing spread over the same span (reviewed_words)."""
    out = []
    for k, sp in enumerate(spans):
        a, b = sp["out"], sp["out"] + sp["end"] - sp["start"]
        ws = [w for w in words if a <= w["start"] < b]
        if ws:
            out.append({"id": k, "start": round(a, 3), "end": round(b, 3),
                        "text": " ".join(w["word"] for w in ws), "words": ws})
    return out


def reviewed_words(segments: list[dict]) -> list[dict]:
    """The words of the reviewed segments. If a segment keeps its words and
    they still say its text, their timing stays; if the text changed, the
    words are spread over the time the original ones covered (or the
    segment's), proportional to their length."""
    from modules.subtitles import processor as subtitles
    out: list[dict] = []
    for seg in segments:
        text = " ".join(str(seg.get("text") or "").split())
        words = [w for w in seg.get("words") or [] if str(w.get("word") or "").strip()]
        if words and " ".join(str(w["word"]).strip() for w in words) == text:
            out += [{"word": str(w["word"]).strip(), "start": float(w["start"]), "end": float(w["end"])}
                    for w in words]
        elif text:
            a = float(words[0]["start"]) if words else float(seg["start"])
            b = float(words[-1]["end"]) if words else float(seg["end"])
            out += subtitles._redistribute(text, a, b)
    return sorted(out, key=lambda w: w["start"])


def _roster(sesion: dict, game_id: str) -> list[dict]:
    return next((d.get("roster") or [] for d in sesion["partidas"] if d["game_id"] == game_id), [])


def _teams(sesion: dict, chosen: list[dict], ep: EditParams) -> list[str]:
    """Team names for Whisper: the full names if they came, and the codes."""
    p = sesion["params"]
    names = [getattr(p, "equipo_azul", ""), getattr(p, "equipo_rojo", "")]
    codes = [x for d in sesion["partidas"] if d["game_id"] in {c["game_id"] for c in chosen}
             for x in (d.get("azul"), d.get("rojo"))]
    return [n for n in names + codes if n]


# --- The daily summary -------------------------------------------------------------
DAILY_MIN_SCORE = 90         # the winner's fights that make it (besides the closing one)
DAILY_MAX_CLIPS = 3
DAILY_MAX_S = 55.0


def daily_pick(clips: list[dict], winner: str | None, max_s: float = DAILY_MAX_S) -> list[str]:
    """The fights of a game for the day's summary, as Axl picked them by hand
    on 23/10: the closing one and the WINNER's best (score ≥ DAILY_MIN_SCORE),
    at most DAILY_MAX_CLIPS and max_s, in order."""
    closing = [c for c in clips if c.get("cierra")]
    best = sorted((c for c in clips if c.get("elegible") and c.get("ganador") == winner and not c.get("cierra")
                   and c["puntaje"] >= DAILY_MIN_SCORE), key=lambda c: (-c["puntaje"], c["clip_inicio"]))
    if not closing and not best:
        best = sorted((c for c in clips if c.get("ganador") == winner), key=lambda c: -c["puntaje"])[:1]
    pick, total = [], 0.0
    for c in closing[:1] + best:
        if len(pick) < DAILY_MAX_CLIPS and (not pick or total + c["duracion"] <= max_s):
            pick.append(c)
            total += c["duracion"]
    return [c["id"] for c in sorted(pick, key=lambda c: c["clip_inicio"])]


def daily(job_id: str, game_ids: list[str], vod_url: str, carpeta_dia: str, titulo: str = "highlights",
          picks: dict | None = None, render: dict | None = None, montage_name: str = "") -> None:
    """The day's summary, by itself: per game a phase 1, the fights picked
    (daily_pick, or `picks` {gameId: [ids]}) rendered like the summaries of
    23/10 (pre 7 s, post 4 s, zoom 1.4, no intro) in <carpeta_dia>/<Winner vs
    Loser>/, and the montage of all of them in the order they were played,
    with the game's audio. A game whose anchor is doubtful is left out (it
    says so) instead of putting a wrong video in the montage."""
    render = {"pre_s": 7.0, "post_s": 4.0, "zoom": 1.4, "color": "vivo", "zoom_mode": "zoom", **(render or {})}
    games, notes = [], []
    run = metrics.Run("diario", job_id, preset=_preset(job_id), games=len(game_ids))
    try:
        n = len(game_ids)
        for k, gid in enumerate(game_ids, 1):
            band = (2 + int(90 * (k - 1) / n), 2 + int(90 * k / n))
            job_manager.update_job(job_id, status="processing", progress=band[0],
                                   stage=f"Partida {k}/{n}: fase 1 (feed, ancla y reloj)…")
            sub = job_manager.create_job(vod_url, output_path="")
            params = CompetitivoParams.from_form({
                "game_ids": gid, "vod_url": vod_url, "modo": "revisar", "pre_s": str(render["pre_s"]),
                "post_s": str(render["post_s"]), "zoom": str(render["zoom"]), "color": render["color"],
                "intro_modo": "off", "max_por_partida": "12", "carpeta_dia": carpeta_dia,
                "partido": "x", "titulo": titulo, "watermark": render.get("watermark", "")})
            process(sub, params)
            j = job_manager.get_job(sub)
            if j["status"] != "done":
                notes.append(f"{gid}: la fase 1 falló ({j['error']}).")
                games.append({"game_id": gid, "phase1": sub, "status": "error", "error": j["error"]})
                continue
            d = j["partidas"][0]
            if d["confianza"] != "alta":
                notes.append(f"{d['azul']} vs {d['rojo']}: ancla dudosa ({d['confianza']}), quedó fuera; "
                             f"revisala con la fase 1 {sub}.")
                games.append({"game_id": gid, "phase1": sub, "status": "ancla_dudosa", "match": f"{d['azul']} vs {d['rojo']}"})
                continue
            winner = d.get("ganador") or d["azul"]
            loser = d["rojo"] if winner == d["azul"] else d["azul"]
            ids = (picks or {}).get(gid) or daily_pick(j["clips"], winner)
            job_manager.update_job(job_id, progress=(band[0] + band[1]) // 2,
                                   stage=f"Partida {k}/{n}: {winner} vs {loser}, {len(ids)} pelea(s)…")
            fp = FinishParams.from_form({"clips": json.dumps(ids), "carpeta_dia": carpeta_dia,
                                         "partido": f"{winner} vs {loser}", "titulo": titulo, "intro_modo": "off",
                                         "zoom_mode": render["zoom_mode"]}, params)
            finish(sub, fp)
            j = job_manager.get_job(sub)
            if j["status"] != "done":
                notes.append(f"{winner} vs {loser}: el render falló ({j['error']}).")
                games.append({"game_id": gid, "phase1": sub, "status": "error", "error": j["error"]})
                continue
            res = j["resultado"]
            games.append({"game_id": gid, "phase1": sub, "status": "ok", "match": f"{winner} vs {loser}",
                          "video": res["video"], "clips": ids, "duration": res["duracion_total"],
                          "start_utc": d["inicio_utc"]})
        ok = sorted((g for g in games if g["status"] == "ok"), key=lambda g: g["start_utc"])
        if not ok:
            raise RuntimeError("Ninguna partida salió: " + " ".join(notes))
        job_manager.update_job(job_id, progress=93, stage="Montando el resumen del día…")
        folder = os.path.join(config.COMPETITIVO_FOLDER, salida.limpiar(carpeta_dia))
        name = salida.limpiar(montage_name or f"Highlights {carpeta_dia}") + ".mp4"
        os.makedirs(folder, exist_ok=True)
        out = salida._unico(folder, name)
        lista = os.path.join(TRABAJO, f"daily_{job_id}.txt")
        os.makedirs(TRABAJO, exist_ok=True)
        with open(lista, "w", encoding="utf-8") as fh:
            for g in ok:
                fh.write("file '{}'\n".format(g["video"].replace("\\", "/").replace("'", "'\\''")))
        total = sum(g["duration"] for g in ok)
        try:
            ffmpeg_runner.run(layout.build_concat(lista, out), job_id, total_duration=total, progress_range=(93, 99))
        finally:
            file_utils.cleanup_paths(lista)
        job_manager.update_job(job_id, status="done", progress=100, stage="listo", finished=True, output_path=out,
                               aviso=" ".join(notes) or None,
                               resultado={"video": out, "duration": round(total, 2), "games": games,
                                          "order": [g["match"] for g in ok]})
        run.set(ok=len(ok), doubtful=sum(1 for g in games if g["status"] == "ancla_dudosa"),
                video_s=round(total, 2), warning=bool(notes))
        run.finish("ok")
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc), resultado={"games": games})
        run.finish("error", str(exc))


# --- The whole game transcribed --------------------------------------------------
DRAFT_LEAD_S = 600           # the transcription starts this long before 0:00 (the draft)
POST_S = 900                 # and ends this long after the nexus (the post-game analysis)


def new_transcript_job(phase1_id: str, vod_url: str, game_id: str | None = None) -> str:
    """The job record of a transcription (the thread is started apart)."""
    job_id = job_manager.create_job(vod_url, output_path="")
    job_manager.update_job(job_id, modo="transcript", phase1=phase1_id, game_id=game_id)
    return job_id


def transcribe_in_a_row(specs: list[tuple]) -> None:
    """Several transcriptions one after the other (one Whisper at a time):
    [(job_id, phase1_id, game_id, language, draft_lead_s, post_s, carpeta_dia, partido, mapa)]."""
    for job_id, phase1_id, game_id, language, lead, post_s, carpeta_dia, partido, mapa in specs:
        transcript_job(job_id, phase1_id, game_id, language, lead, carpeta_dia, partido, mapa, post_s)


def transcript_job(job_id: str, phase1_id: str, game_id: str | None, language: str = "es",
                   draft_lead_s: float = DRAFT_LEAD_S, carpeta_dia: str = "", partido: str = "",
                   mapa: int | None = None, post_s: float = POST_S, batched: bool = False) -> None:
    """The narration of a whole game (draft → nexus → post-game) on the
    scoreboard's clock, with the index of moments (transcript.py). After the
    nexus the clock is "+m:ss" (time since the nexus). Files:
    transcripcion.txt, transcripcion.json and momentos.json."""
    from modules.subtitles import processor as subtitles
    from modules.subtitles.schema import SubtitlesParams
    sesion = get_session(phase1_id)
    work = None
    run = metrics.Run("transcripcion", job_id, language=language, post_s=post_s, batched=batched)
    try:
        if not sesion:
            raise RuntimeError("La sesión ya no existe. Volvé a generar la propuesta (fase 1).")
        params: CompetitivoParams = sesion["params"]
        d = next((x for x in sesion["partidas"] if not game_id or x["game_id"] == game_id), None)
        if d is None:
            raise RuntimeError(f"La partida {game_id} no es de esa sesión.")
        if not d.get("clock0_utc"):
            raise RuntimeError("Esa partida no tiene el reloj del marcador calibrado (repetí la fase 1).")
        job_manager.update_job(job_id, status="processing", progress=2, stage="Bajando el feed de la partida…")
        p = kills.fetch_partida(d["game_id"], workers=6)
        inicio: datetime = sesion["vod"]["inicio_utc"]
        offset = _offsets(sesion, {})[d["game_id"]]
        clock0 = datetime.fromisoformat(d["clock0_utc"])
        pauses = [(datetime.fromisoformat(x), datetime.fromisoformat(y)) for x, y in d.get("pauses") or []]

        def to_clock(utc):
            return clock.to_clock(utc, clock0, pauses)

        a = _vod(clock0, inicio, offset) - draft_lead_s
        nexus_vod = _vod(p.frames[-1].ts, inicio, offset)
        b = nexus_vod + post_s
        work = os.path.join(sesion["carpeta"], f"transcript_{job_id}")
        os.makedirs(work, exist_ok=True)
        job_manager.update_job(job_id, progress=5, stage=f"Bajando el audio de la partida ({(b - a) / 60:.0f} min)…")
        audio = fuente.bajar_audio(params.vod_url, a, b, os.path.join(work, "audio"), job_id=job_id)
        run.mark("audio")
        job_manager.update_job(job_id, progress=25, stage="Transcribiendo la partida…")
        terms = vocab.game_terms(d.get("roster") or [], [x for x in (params.equipo_azul, params.equipo_rojo,
                                                                     d["azul"], d["rojo"]) if x])
        initial_prompt, hotwords = vocab.prompt(terms)
        segs = subtitles.transcribe(audio, SubtitlesParams(language=language), job_id=job_id,
                                    initial_prompt=initial_prompt, hotwords=hotwords, batched=batched)
        run.mark("whisper")
        fixes = vocab.load()
        segments = []
        for seg in segs:
            words = vocab.correct(seg["words"], fixes)
            vod_s, clock_s = transcript.to_game_clock(seg["start"], a, inicio, offset, to_clock)
            since_nexus = vod_s - nexus_vod
            post = since_nexus >= 0
            segments.append({"clock": transcript.fmt_post(since_nexus) if post else transcript.fmt_clock(clock_s),
                             "clock_s": round(clock_s, 2), "vod_s": round(vod_s, 2),
                             "vod_end_s": round(vod_s + (seg["end"] - seg["start"]), 2),
                             **({"post": True, "post_s": round(since_nexus, 2)} if post else {}),
                             "text": " ".join(w["word"] for w in words),
                             "words": [{"word": w["word"],
                                        "clock_s": round(transcript.to_game_clock(w["start"], a, inicio, offset,
                                                                                   to_clock)[1], 2),
                                        **({"post_s": round(a + w["start"] - nexus_vod, 2)} if post else {})}
                                       for w in words]})
        events = [{"clock_s": to_clock(e.ts), "tipo": e.tipo, "equipo": e.equipo, "detalle": e.detalle,
                   "cantidad": e.cantidad} for e in p.eventos]
        players = [r.get("player", "") for r in d.get("roster") or []]
        champions = [vocab.champion_name(r.get("champion", "")) for r in d.get("roster") or []]
        index = transcript.moments(segments, events, players, champions)

        mapa = mapa or params.mapa
        if carpeta_dia and partido:
            folder = os.path.join(config.COMPETITIVO_FOLDER, salida.limpiar(carpeta_dia), salida.limpiar(partido),
                                  "_extras", f"Mapa {mapa}")
        else:
            date = clock0.astimezone().strftime("%Y-%m-%d")
            folder = os.path.join(config.COMPETITIVO_FOLDER, "_transcripciones",
                                  salida.limpiar(f"{date} {d['azul']} vs {d['rojo']} · Mapa {mapa}"))
        os.makedirs(folder, exist_ok=True)
        title = (f"{d['azul']} vs {d['rojo']} · Mapa {mapa} · reloj del marcador (antes de 0:00: draft y carga"
                 + ("; +m:ss: después del nexo)" if post_s else ")"))
        files = {"txt": os.path.join(folder, "transcripcion.txt"), "json": os.path.join(folder, "transcripcion.json"),
                 "moments": os.path.join(folder, "momentos.json")}
        with open(files["txt"], "w", encoding="utf-8") as fh:
            fh.write(transcript.text_file(segments, title))
        with open(files["json"], "w", encoding="utf-8") as fh:
            json.dump({"game_id": d["game_id"], "azul": d["azul"], "rojo": d["rojo"], "clock0_utc": d["clock0_utc"],
                       "vod": {"url": params.vod_url, "from_s": round(a, 2), "to_s": round(b, 2),
                               "nexus_s": round(nexus_vod, 2)}, "post_s": post_s,
                       "vocabulary": terms, "segments": segments}, fh, ensure_ascii=False, indent=1)
        with open(files["moments"], "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False, indent=1)
        job_manager.update_job(job_id, status="done", progress=100, stage="listo", finished=True,
                               output_path=files["txt"],
                               resultado={**files, "segments": len(segments), "moments": index,
                                          "game_id": d["game_id"], "minutes": round((b - a) / 60, 1),
                                          "post_segments": sum(1 for s in segments if s.get("post")),
                                          "praise": sum(1 for m in index if m["kind"] == "elogio"),
                                          "criticism": sum(1 for m in index if m["kind"] == "critica")})
        run.set(minutes=round((b - a) / 60, 1), segments=len(segments), moments=len(index))
        run.finish("ok")
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
        run.finish("error", str(exc))
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


# --- Resúmenes guardados ----------------------------------------------------------
def listar_resumenes() -> list[dict]:
    """Los resúmenes de la carpeta persistente, del más nuevo al más viejo."""
    return salida.listar()
