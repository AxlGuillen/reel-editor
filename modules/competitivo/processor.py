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
                     y concatena resumen.mp4. Todo queda en una carpeta
                     persistente por serie (config.COMPETITIVO_FOLDER).
"""
from __future__ import annotations

import json
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import config
from core import ffmpeg_runner, job_manager
from modules.competitivo import fuente, kills, layout
from modules.competitivo.schema import CompetitivoParams, FinishParams

OFFSET_FORMULA_S = 38      # vod ≈ (utc − inicio del directo) + 38 s (medido en KC-C9 G3, día 1)
VENTANA_ANCLA_S = 30       # margen del tramo que se baja para calcular el ancla
RANGO_ANCLA_S = 25         # desfase máximo contra la fórmula que se busca
MARGEN_CLIP_S = 6          # cada clip se baja con este margen, para el ajuste fino
RANGO_AJUSTE_S = 5         # ajuste fino por clip: corrimiento máximo

TRABAJO = os.path.join(config.OUTPUT_FOLDER, "competitivo")

# Datos de cada sesión entre la fase 1 y la 2 (los jobs guardan lo que se
# muestra; acá queda lo que hace falta para renderizar).
_SESIONES: dict[str, dict] = {}


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
        "elegible": pe.elegible, "forzada": pe.forzada, "seleccionada": id(pe) in seleccion,
        "duracion": round(pe.duracion, 1),
        "antes": _iso(pe.antes), "primera": _iso(pe.primera), "ultima": _iso(pe.ultima),
        "clip_inicio": _iso(pe.clip_inicio), "clip_fin": _iso(pe.clip_fin),
        "eventos": [[_iso(p.frames[k.i].ts), _lado(p, k.equipo)] for k in pe.kills],
    }


def _captura(src: str, t: float, out: str) -> None:
    ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-y", "-ss", f"{max(0.0, t):.2f}",
                           "-i", src, "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "3", out])


def _ancla(url: str, inicio: datetime, p: kills.Partida, peleas: list[kills.Pelea],
           carpeta: str, n_partida: int) -> dict:
    """Segundo del VOD de la primera kill de la partida, alineando el contador
    de kills del video con el feed en el tramo de la pelea más grande."""
    eventos = [(p.frames[k.i].ts, _lado(p, k.equipo)) for k in kills._kills(p)]
    fb_utc = eventos[0][0]
    propuesta = _vod(fb_utc, inicio, OFFSET_FORMULA_S)
    ref = max(peleas, key=lambda pe: (pe.elegible, pe.total, pe.bruto))
    a = _vod(ref.antes, inicio, OFFSET_FORMULA_S) - VENTANA_ANCLA_S
    b = _vod(ref.ultima, inicio, OFFSET_FORMULA_S) + VENTANA_ANCLA_S
    desfase, n, esperadas = 0.0, 0, 0
    frame = None
    try:
        tramo = fuente.bajar_tramo(url, a, b, os.path.join(carpeta, f"ancla_{n_partida}"))
        vistos = layout.cambios_kills(tramo)
        esp = [(_vod(ts, inicio, OFFSET_FORMULA_S) - a, lado) for ts, lado in eventos]
        esp = [e for e in esp if -RANGO_ANCLA_S <= e[0] <= (b - a) + RANGO_ANCLA_S]
        esperadas = sum(1 for t, _ in esp if 0 <= t <= b - a)
        desfase, n = layout.estimar_desfase(vistos, esp, rango=RANGO_ANCLA_S)
        if not n:
            desfase = 0.0
        # Frame de control: la primera kill de la pelea de referencia, ya corregida.
        frame = f"ancla_{n_partida}.jpg"
        _captura(tramo, _vod(ref.primera, inicio, OFFSET_FORMULA_S + desfase) - a + 0.5,
                 os.path.join(carpeta, frame))
        os.remove(tramo)
    except RuntimeError as exc:
        print(f"[competitivo] ancla de {p.game_id}: {exc}".encode("ascii", "replace").decode())
    return {
        "ancla_s": round(propuesta + desfase, 2),
        "ancla_propuesta_s": round(propuesta, 2),
        "desfase_s": desfase,
        "confianza": "alta" if n >= 2 else "media" if n == 1 else "baja",
        "coincidencias": n, "esperadas": esperadas,
        "referencia": ref.titulo,
        "referencia_vod_s": round(_vod(ref.primera, inicio, OFFSET_FORMULA_S + desfase), 2),
        "frame": frame,
        "primera_kill_utc": _iso(fb_utc),
    }


# --- Fase 1 ---------------------------------------------------------------------
def process(job_id: str, params: CompetitivoParams) -> None:
    try:
        carpeta = os.path.join(TRABAJO, job_id)
        os.makedirs(carpeta, exist_ok=True)
        job_manager.update_job(job_id, status="processing", progress=2, stage="Leyendo el VOD…")
        vod = fuente.info_vod(params.vod_url)
        inicio = vod["inicio_utc"]

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

        job_manager.update_job(job_id, progress=45, stage="Sincronizando el VOD con el feed…")
        with ThreadPoolExecutor(max_workers=3) as ex:
            anclas = list(ex.map(lambda i: _ancla(params.vod_url, inicio, partidas[i], peleas[i],
                                                  carpeta, i + 1), range(len(partidas))))

        datos_partidas, clips = [], []
        for i, (p, pe_lista, ancla) in enumerate(zip(partidas, peleas, anclas), 1):
            datos_partidas.append({
                "n": i, "game_id": p.game_id, "azul": p.azul, "rojo": p.rojo,
                "inicio_utc": _iso(p.t0), **ancla,
            })
            clips += [_pelea_dict(p, pe, i, j, seleccion) for j, pe in enumerate(pe_lista, 1)]
        ids_sel = [c["id"] for c in sorted(clips, key=lambda c: (c["partida"], c["clip_inicio"]))
                   if c["seleccionada"]]
        # Con UNA sola coincidencia siempre se puede alinear algo por casualidad:
        # el modo auto solo sigue si todas las anclas tienen 2+ kills alineadas.
        dudosas = [d["n"] for d in datos_partidas if d["confianza"] != "alta"]
        _SESIONES[job_id] = {"params": params, "vod": vod, "partidas": datos_partidas, "clips": clips,
                             "carpeta": carpeta}
        job_manager.update_job(
            job_id, status="done", progress=100, stage="esperando_ancla", finished=False,
            vod={"url": params.vod_url, "titulo": vod["titulo"], "inicio_utc": _iso(inicio)},
            partidas=datos_partidas, clips=clips, seleccion=ids_sel,
            aviso=(f"Ancla dudosa en la partida {', '.join(map(str, dudosas))}: revisala antes de renderizar."
                   if dudosas else None),
        )
        if params.modo == "auto" and not dudosas:
            finish(job_id, FinishParams.desde(params))
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=str(exc))


# --- Fase 2 ---------------------------------------------------------------------
def _carpeta_salida(partidas: list[dict]) -> str:
    primera = partidas[0]
    fecha = datetime.fromisoformat(primera["inicio_utc"]).astimezone().strftime("%Y-%m-%d")
    nombre = re.sub(r'[<>:"/\\|?*]+', "", f"{fecha} {primera['azul']} vs {primera['rojo']}").strip()
    base = os.path.join(config.COMPETITIVO_FOLDER, nombre)
    destino, n = base, 2
    while os.path.exists(destino):
        destino, n = f"{base} ({n})", n + 1
    os.makedirs(destino)
    return destino


def finish(job_id: str, fp: FinishParams) -> None:
    sesion = _SESIONES.get(job_id)
    temp = None
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
                               stage="Bajando los tramos del VOD…", error=None)
        temp = os.path.join(sesion["carpeta"], "tramos")
        os.makedirs(temp, exist_ok=True)
        salida = _carpeta_salida(sesion["partidas"])
        marca = os.path.join(config.WATERMARKS_FOLDER, fp.watermark) if fp.watermark else None

        def ventana(c):
            off = offsets[c["game_id"]]
            a = _vod(datetime.fromisoformat(c["clip_inicio"]), inicio, off) - MARGEN_CLIP_S
            b = _vod(datetime.fromisoformat(c["clip_fin"]), inicio, off) + MARGEN_CLIP_S
            return a, b

        n = len(elegidos)
        hechos = []
        with ThreadPoolExecutor(max_workers=3) as ex:
            futuros = [ex.submit(fuente.bajar_tramo, params.vod_url, *ventana(c),
                                 os.path.join(temp, f"tramo_{k:02d}")) for k, c in enumerate(elegidos, 1)]
            for k, (c, fut) in enumerate(zip(elegidos, futuros), 1):
                job_manager.update_job(job_id, stage=f"Clip {k}/{n}: bajando el tramo…")
                tramo = fut.result()
                off = offsets[c["game_id"]]
                a, _ = ventana(c)
                # Ajuste fino con las kills del propio clip (el ancla es por partida).
                vistos = layout.cambios_kills(tramo)
                esp = [(_vod(datetime.fromisoformat(ts), inicio, off) - a, lado) for ts, lado in c["eventos"]]
                ajuste, coinc = layout.estimar_desfase(vistos, esp, rango=RANGO_AJUSTE_S)
                if coinc < min(2, len(esp)):
                    ajuste = 0.0          # una sola coincidencia en una pelea puede ser casualidad
                ini = _vod(datetime.fromisoformat(c["clip_inicio"]), inicio, off) - a + ajuste
                fin = _vod(datetime.fromisoformat(c["clip_fin"]), inicio, off) - a + ajuste
                t_kill = _vod(datetime.fromisoformat(c["primera"]), inicio, off) - a + ajuste
                dur_tramo = ffmpeg_runner.probe_duration(tramo) or (fin + MARGEN_CLIP_S)
                ini, fin = max(0.0, ini), min(dur_tramo, fin)
                an = layout.analizar(tramo, t_kill, ini, fin)
                dur = an.vivo_hasta - an.vivo_desde
                if dur < 3:
                    raise RuntimeError(f"El clip «{c['titulo']}» cae casi entero fuera del vivo "
                                       "(repetición o pausa). Quitalo o corregí el ancla.")
                archivo = f"clip_{k:02d}.mp4"
                job_manager.update_job(job_id, stage=f"Clip {k}/{n}: renderizando…")
                cmd = layout.build_command(tramo, os.path.join(salida, archivo), an.vivo_desde, dur,
                                           an.camara_y, zoom=fp.zoom, color=fp.color, marca_path=marca,
                                           marca_alto=fp.marca_alto)
                lo = 5 + int(85 * (k - 1) / n)
                ffmpeg_runner.run(cmd, job_id, total_duration=dur, progress_range=(lo, 5 + int(85 * k / n)))
                job_manager.update_job(job_id, status="processing")
                hechos.append({
                    "archivo": archivo, "id": c["id"], "titulo": c["titulo"], "partida": c["partida"],
                    "puntaje": c["puntaje"], "duracion": round(dur, 2),
                    "vod_inicio": round(a + an.vivo_desde, 2), "vod_fin": round(a + an.vivo_hasta, 2),
                    "ajuste_s": ajuste, "kills_vistas": coinc, "kills_esperadas": len(c["eventos"]),
                    "fuera_del_vivo_s": an.no_vivo_s, "camara_y": an.camara_y,
                })
                os.remove(tramo)

        job_manager.update_job(job_id, stage="Uniendo el resumen…", progress=92)
        lista = os.path.join(temp, "lista.txt")
        with open(lista, "w", encoding="utf-8") as fh:
            for h in hechos:
                ruta = os.path.join(salida, h["archivo"]).replace("\\", "/").replace("'", "'\\''")
                fh.write(f"file '{ruta}'\n")
        resumen = os.path.join(salida, "resumen.mp4")
        ffmpeg_runner.run([config.FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0", "-i", lista,
                           "-c", "copy", "-movflags", "+faststart", resumen],
                          job_id, total_duration=sum(h["duracion"] for h in hechos),
                          progress_range=(92, 99))
        info = {
            "creado": datetime.now(timezone.utc).isoformat(),
            "serie": f"{sesion['partidas'][0]['azul']} vs {sesion['partidas'][0]['rojo']}",
            "vod": {**sesion["vod"], "inicio_utc": _iso(inicio), "url": params.vod_url},
            "partidas": [{**d, "ancla_usada_s": round(offsets[d["game_id"]] + (
                datetime.fromisoformat(d["primera_kill_utc"]) - inicio).total_seconds(), 2)}
                for d in sesion["partidas"]],
            "clips": hechos,
            "duracion_total": round(sum(h["duracion"] for h in hechos), 2),
            "parametros": {**asdict(params), "render": asdict(fp)},
        }
        with open(os.path.join(salida, "info.json"), "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False, indent=2)
        job_manager.update_job(
            job_id, status="done", progress=100, stage="listo", finished=True,
            output_path=resumen,
            resultado={"carpeta": salida, "nombre": os.path.basename(salida),
                       "resumen": "resumen.mp4", "clips": hechos,
                       "duracion_total": info["duracion_total"]},
        )
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
    finally:
        if temp:
            shutil.rmtree(temp, ignore_errors=True)


# --- Resúmenes guardados ----------------------------------------------------------
def listar_resumenes() -> list[dict]:
    """Los resúmenes de la carpeta persistente, del más nuevo al más viejo."""
    base = config.COMPETITIVO_FOLDER
    if not os.path.isdir(base):
        return []
    out = []
    for nombre in os.listdir(base):
        info_path = os.path.join(base, nombre, "info.json")
        if not os.path.isfile(info_path):
            continue
        try:
            with open(info_path, encoding="utf-8") as fh:
                info = json.load(fh)
        except (OSError, ValueError):
            continue
        out.append({
            "nombre": nombre, "serie": info.get("serie"), "creado": info.get("creado"),
            "duracion_total": info.get("duracion_total"),
            "clips": [{"archivo": c["archivo"], "titulo": c["titulo"], "duracion": c["duracion"]}
                      for c in info.get("clips", [])],
            "resumen": "resumen.mp4" if os.path.isfile(os.path.join(base, nombre, "resumen.mp4")) else None,
        })
    return sorted(out, key=lambda r: r["creado"] or "", reverse=True)
