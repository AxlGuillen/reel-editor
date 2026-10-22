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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import config
from core import ffmpeg_runner, file_utils, job_manager
from modules.beatmap import processor as beatmap
from modules.competitivo import edit as edit_plan
from modules.competitivo import fuente, intro, kills, layout, salida
from modules.competitivo.schema import CompetitivoParams, EditParams, FinishParams
from modules.edicion import render as edicion

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
        "elegible": pe.elegible, "forzada": pe.forzada, "relleno": pe.relleno,
        "seleccionada": id(pe) in seleccion,
        "duracion": round(pe.duracion, 1),
        "antes": _iso(pe.antes), "primera": _iso(pe.primera), "ultima": _iso(pe.ultima),
        "clip_inicio": _iso(pe.clip_inicio), "clip_fin": _iso(pe.clip_fin),
        "eventos": [[_iso(p.frames[k.i].ts), _lado(p, k.equipo)] for k in pe.kills],
        # Quién hizo cada kill de `eventos` (para los letreros del modo edit).
        "autores": kills.autores(p, pe),
        "cantidades": [k.cantidad for k in pe.kills],
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
            minutos = kills.minutos_de(p)
            datos_partidas.append({
                "n": i, "game_id": p.game_id, "azul": p.azul, "rojo": p.rojo,
                "inicio_utc": _iso(p.t0), "minutos": minutos,
                "objetivo_s": kills.objetivo_s(reglas, minutos),
                "seleccion_s": round(sum(pe.duracion for pe in pe_lista if id(pe) in seleccion), 1),
                **ancla,
            })
            clips += [_pelea_dict(p, pe, i, j, seleccion) for j, pe in enumerate(pe_lista, 1)]
        ids_sel = [c["id"] for c in sorted(clips, key=lambda c: (c["partida"], c["clip_inicio"]))
                   if c["seleccionada"]]
        # Con UNA sola coincidencia siempre se puede alinear algo por casualidad:
        # el modo auto solo sigue si todas las anclas tienen 2+ kills alineadas.
        dudosas = [d["n"] for d in datos_partidas if d["confianza"] != "alta"]
        _SESIONES[job_id] = {"params": params, "vod": vod, "partidas": datos_partidas, "clips": clips,
                             "carpeta": carpeta, "voz": (params.intro_audio_url, voz) if voz else None}
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


def finish(job_id: str, fp: FinishParams) -> None:
    sesion = _SESIONES.get(job_id)
    temp = dest = None
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
        hechos = []
        en_resumen: list[str] = []     # lo que entra al resumen (el clip 1 puede llevar la intro)
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
                if k == 1 and modo_intro == "encima":
                    modo_intro, motivo = intro.modo_para_clip1(voz.D, dur, t_kill - an.vivo_desde)
                    if modo_intro == "aparte":
                        intro_info.update(modo_usado="aparte", motivo=motivo)
                        intro_mp4 = render_aparte()
                    else:
                        intro_info["placa_s"] = intro.fin_de_placa(voz.D, t_kill - an.vivo_desde,
                                                                   fp.placa_max_s)
                job_manager.update_job(job_id, stage=f"Clip {k}/{n}: renderizando…")
                render = dict(zoom=fp.zoom, color=fp.color, marca_path=marca, marca_alto=fp.marca_alto)
                cmd = layout.build_command(tramo, os.path.join(carpeta, archivo), an.vivo_desde, dur,
                                           an.camara_y, **render)
                lo, hi = 5 + int(85 * (k - 1) / n), 5 + int(85 * k / n)
                con_intro = k == 1 and modo_intro == "encima"
                ffmpeg_runner.run(cmd, job_id, total_duration=dur,
                                  progress_range=(lo, (lo + hi) // 2 if con_intro else hi))
                en_resumen.append(os.path.join(carpeta, archivo))
                if con_intro:
                    # El clip suelto queda limpio; la versión con la intro encima es
                    # solo para el resumen.
                    job_manager.update_job(job_id, status="processing",
                                           stage="Clip 1: renderizando con la intro encima…")
                    con = os.path.join(temp, "clip_01_intro.mp4")
                    cmd = layout.build_command(
                        tramo, con, an.vivo_desde, dur, an.camara_y, **render,
                        intro=layout.IntroEncima(placa=placa, voz=voz.path, D=voz.D, sting=intro.sting(),
                                                 sting_vol=intro.STING_VOL, voz_filtro=intro.cadena_voz(voz),
                                                 sting_filtro=intro.cadena_sting(voz.D),
                                                 volumen_juego=fp.intro_volumen_juego / 100,
                                                 placa_fin=intro_info["placa_s"]),
                        con_audio=ffmpeg_runner.has_audio_stream(tramo))
                    ffmpeg_runner.run(cmd, job_id, total_duration=dur, progress_range=((lo + hi) // 2, hi))
                    en_resumen[-1] = con
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
            output_path=dest.video_final, portada="portada.jpg", intro=intro_corto,
            resultado={"carpeta": dest.carpeta, "nombre": os.path.basename(dest.carpeta),
                       "video": dest.video_final, "ruta": dest.ruta_video,
                       "resumen": os.path.basename(dest.video_final), "organizada": dest.organizada,
                       "portada": "portada.jpg", "intro": intro_corto,
                       "clips": hechos, "duracion_total": duracion_total},
        )
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
        # Lo que quedó a medias se borra (si no, el reintento terminaba en
        # "… (2)" con una carpeta rota al lado); lo publicado antes, nunca.
        salida.descartar(dest)
    finally:
        if temp:
            shutil.rmtree(temp, ignore_errors=True)


# --- Modo edit --------------------------------------------------------------------
def _offsets(sesion: dict, anclas: dict[str, float]) -> dict[str, float]:
    """offset de cada partida (vod = utc − inicio + offset) a partir de su ancla."""
    inicio: datetime = sesion["vod"]["inicio_utc"]
    out = {}
    for d in sesion["partidas"]:
        ancla = anclas.get(d["game_id"], d["ancla_s"])
        fb = datetime.fromisoformat(d["primera_kill_utc"])
        out[d["game_id"]] = ancla - (fb - inicio).total_seconds()
    return out


def _elegidos(sesion: dict, ids: list[str] | None) -> list[dict]:
    por_id = {c["id"]: c for c in sesion["clips"]}
    ids = ids or [c["id"] for c in sesion["clips"] if c["seleccionada"]]
    faltan = [i for i in ids if i not in por_id]
    if faltan:
        raise RuntimeError(f"No existe el clip {faltan[0]}.")
    elegidos = sorted((por_id[i] for i in ids), key=lambda c: (c["partida"], c["clip_inicio"]))
    if not elegidos:
        raise RuntimeError("No hay clips para el edit.")
    return elegidos


def _destino_edit(ep: EditParams, sesion: dict) -> str:
    """Dónde queda el edit. Con día y partido, junto a los resúmenes como
    "Edit Mapa N - <título>.mp4" (no empieza con "Mapa", así publicar() nunca
    lo manda a _descartados); si no, en EDITS_FOLDER por serie."""
    nombre = f"Edit Mapa {ep.mapa}" + (f" - {ep.titulo}" if ep.titulo else "") + ".mp4"
    if ep.carpeta_dia and ep.partido:
        carpeta = os.path.join(config.COMPETITIVO_FOLDER, ep.carpeta_dia, ep.partido)
    else:
        d = sesion["partidas"][0]
        fecha = datetime.fromisoformat(d["inicio_utc"]).astimezone().strftime("%Y-%m-%d")
        carpeta = os.path.join(config.EDITS_FOLDER, salida.limpiar(f"{fecha} {d['azul']} vs {d['rojo']}"))
    os.makedirs(carpeta, exist_ok=True)
    return salida._unico(carpeta, nombre)


def texto_y_edit(zoom: float) -> float:
    """Altura de los letreros de kill: el tercio de arriba del gameplay (no
    tapa el centro, donde el observador pone la pelea)."""
    g = layout.geometria(zoom)
    return (layout.ARRIBA_H + g.extra_arriba + g.juego_h * 0.3) / layout.H


def edit(job_id: str, fase1_id: str, ep: EditParams, musica_path: str | None = None) -> None:
    """Las peleas de la fase 1 al ritmo de una canción (ver competitivo/edit.py).

    Baja los mismos tramos que el resumen (con el ajuste fino por clip), pero en
    vez del layout con los casters arma una EDL donde cada kill cae en un beat y
    la pelea clave en el drop, y la renderiza con el motor de edición."""
    sesion = _SESIONES.get(fase1_id)
    temp = descargada = None
    try:
        if not sesion:
            raise RuntimeError("La sesión ya no existe (¿se reinició el servidor?). Volvé a generar la propuesta.")
        params: CompetitivoParams = sesion["params"]
        inicio: datetime = sesion["vod"]["inicio_utc"]
        offsets = _offsets(sesion, ep.anclas)
        elegidos = _elegidos(sesion, ep.clips)
        temp = os.path.join(sesion["carpeta"], f"edit_{job_id}")
        os.makedirs(temp, exist_ok=True)
        job_manager.update_job(job_id, status="processing", progress=1, finished=False, stage="Preparando la música…")
        if ep.musica:
            musica_path = descargada = beatmap.bajar_musica(job_id, ep.musica)
        if not musica_path:
            raise RuntimeError("Falta la música del edit.")
        bm = beatmap.obtener(musica_path, ep.motor, ep.letra, job_id=job_id)
        job_manager.update_job(job_id, status="processing", progress=12, stage="Bajando los tramos del VOD…")

        def ventana(c):
            off = offsets[c["game_id"]]
            a = _vod(datetime.fromisoformat(c["clip_inicio"]), inicio, off) - MARGEN_CLIP_S
            b = _vod(datetime.fromisoformat(c["clip_fin"]), inicio, off) + MARGEN_CLIP_S
            return a, b

        clips: list[edit_plan.ClipEdit] = []
        with ThreadPoolExecutor(max_workers=3) as ex:
            futuros = [ex.submit(fuente.bajar_tramo, params.vod_url, *ventana(c),
                                 os.path.join(temp, f"tramo_{k:02d}")) for k, c in enumerate(elegidos, 1)]
            for k, (c, fut) in enumerate(zip(elegidos, futuros), 1):
                job_manager.update_job(job_id, stage=f"Pelea {k}/{len(elegidos)}: bajando y sincronizando…",
                                       progress=12 + int(18 * (k - 1) / len(elegidos)))
                tramo = fut.result()
                off = offsets[c["game_id"]]
                a, _ = ventana(c)
                vistos = layout.cambios_kills(tramo)
                esp = [(_vod(datetime.fromisoformat(ts), inicio, off) - a, lado) for ts, lado in c["eventos"]]
                ajuste, coinc = layout.estimar_desfase(vistos, esp, rango=RANGO_AJUSTE_S)
                if coinc < min(2, len(esp)):
                    ajuste = 0.0
                ini = _vod(datetime.fromisoformat(c["clip_inicio"]), inicio, off) - a + ajuste
                fin = _vod(datetime.fromisoformat(c["clip_fin"]), inicio, off) - a + ajuste
                t_kill = _vod(datetime.fromisoformat(c["primera"]), inicio, off) - a + ajuste
                dur_tramo = ffmpeg_runner.probe_duration(tramo) or (fin + MARGEN_CLIP_S)
                an = layout.analizar(tramo, t_kill, max(0.0, ini - 4), min(dur_tramo, fin))
                autores = c.get("autores") or [None] * len(c["eventos"])
                cantidades = c.get("cantidades") or [1] * len(c["eventos"])
                ks = [edit_plan.KillEdit(t - ep.adelanto_kill_s, lado, (au or {}).get("jugador", ""),
                                         (au or {}).get("campeon", ""), n)
                      for (t, lado), au, n in zip(esp, autores, cantidades)]
                for kk in ks:
                    kk.t += ajuste
                clips.append(edit_plan.ClipEdit(
                    c["id"], tramo, an.vivo_desde, an.vivo_hasta, ks, c["puntaje"], c.get("multikill"),
                    c.get("cierra", False), c["titulo"],
                    layout.geometria_edit(an.camara_y, ep.zoom, ep.con_camara)))

        job_manager.update_job(job_id, stage="Armando el edit al ritmo…", progress=30)
        op = edit_plan.Opciones(estilo=ep.estilo, velocidad=ep.velocidad, duracion_max_s=ep.duracion_max_s,
                                alinear_drop=ep.alinear_drop, musica_desde=ep.musica_desde, texto=ep.texto,
                                acentos_musica=ep.acentos_musica, titulo=ep.titulo_edit)
        res = edit_plan.planear(clips, bm, op)
        edl = res.edl
        edl.musica = musica_path
        edl.look, edl.lut, edl.glow = ep.look, ep.lut, ep.glow
        edl.volumen_original = ep.volumen_juego / 100
        marca = os.path.join(config.WATERMARKS_FOLDER, ep.watermark) if ep.watermark else None
        edl.marca, edl.marca_alto = marca, ep.marca_alto
        destino = _destino_edit(ep, sesion)
        info_render = edicion.render(edl, destino, job_id, os.path.join(temp, "render"), progreso=(30, 99),
                                     intensidad=ep.intensidad, texto_y=texto_y_edit(ep.zoom),
                                     marca_recorte=layout.recorte_marca(marca) if marca else None)
        resultado = {
            "video": destino, "carpeta": os.path.dirname(destino), "nombre": os.path.basename(destino),
            "descarga": os.path.basename(destino), "duracion_total": info_render["dur"],
            "musica_desde": round(res.musica_desde, 3), "drop_alineado": res.drop_alineado,
            "bpm": bm["bpm"], "clips_usados": res.usados, "clips_descartados": res.descartados,
            "aviso": res.aviso, "edl": edl.a_dict(),
        }
        with open(os.path.splitext(destino)[0] + ".json", "w", encoding="utf-8") as fh:
            json.dump({**resultado, "creado": datetime.now(timezone.utc).isoformat(),
                       "parametros": {k: v for k, v in asdict(ep).items() if k not in ("musica", "letra")}},
                      fh, ensure_ascii=False, indent=2, default=str)
        job_manager.update_job(job_id, status="done", progress=100, stage="listo", finished=True,
                               output_path=destino, resultado=resultado, aviso=res.aviso)
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
    finally:
        if temp:
            shutil.rmtree(temp, ignore_errors=True)
        if descargada:
            file_utils.cleanup_paths(descargada)
        elif musica_path and musica_path.startswith(config.UPLOAD_FOLDER):
            file_utils.cleanup_paths(musica_path)


# --- Resúmenes guardados ----------------------------------------------------------
def listar_resumenes() -> list[dict]:
    """Los resúmenes de la carpeta persistente, del más nuevo al más viejo."""
    return salida.listar()
