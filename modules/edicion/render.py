"""Render de una EDL en dos pasadas.

  Pasada 1, una por plano: el tramo de la fuente con su geometría (layout,
  recorte vertical…) y sus piezas retemporizadas (velocidad, congelados),
  cada pieza cuadrada al cuadro exacto (trim=end_frame). Sale un intermedio
  .mkv con H.264 de alta calidad y el audio en PCM: con AAC cada unión corría
  el audio unos ms (el relleno del códec), lo mismo que se midió en
  competitivo al unir clips.
  Pasada 2, una sola: los planos unidos + color (LUT y look) + glow + flash +
  cámara (zoom y sacudida) + RGB + viñeta + letreros (ASS) + watermark, con la
  música encima. Los acentos van en tiempo de la SALIDA, por eso viven acá y
  no en cada plano.

Los planos se renderizan en paralelo (3 a la vez): cada uno es corto y la
pasada 2 es la que manda en el tiempo total.
"""
from __future__ import annotations

import os
import shutil
from concurrent.futures import ThreadPoolExecutor

import config
from core import externos, ffmpeg_runner, job_manager
from modules.edicion import efectos, ritmo, texto
from modules.edicion.edl import Edl, Pieza, Segmento

INTERMEDIO_CRF = 14
PARALELO = 3
VEL_INTERPOLAR = 0.6          # con RIFE, las piezas más lentas que esto se interpolan
LIMITADOR = "alimiter=limit=0.89:level=0:latency=1"
SALIDA = "format=yuv420p,setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
MARCA_MARGEN = 14


def vf_cubrir(ancho: int, alto: int) -> str:
    """La fuente llena el cuadro (recorta lo que sobra), centrada."""
    return (f"scale={ancho}:{alto}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={ancho}:{alto}")


def vf_vertical_blur(ancho: int, alto: int, escala: float = 1.0) -> str:
    """16:9 dentro de 9:16: el clip a lo ancho (×`escala`) sobre una copia
    suya difuminada y oscurecida que llena el cuadro (como vertical_convert)."""
    fw = int(round(ancho * escala / 2)) * 2
    return (f"split[vb_a][vb_b];"
            f"[vb_a]scale={ancho // 4}:{alto // 4}:force_original_aspect_ratio=increase,"
            f"crop={ancho // 4}:{alto // 4},gblur=sigma=8,eq=brightness=-0.18,"
            f"scale={ancho}:{alto}[vb_fondo];"
            f"[vb_b]scale={fw}:-2:flags=lanczos[vb_frente];"
            f"[vb_fondo][vb_frente]overlay=(W-w)/2:(H-h)/2")


def _n(dur: float, fps: int) -> int:
    return max(1, round(dur * fps))


def build_pase1(seg: Segmento, out: str, ancho: int, alto: int, fps: int,
                con_audio: bool, fps_fuente: float) -> list[str]:
    """Comando de un plano: geometría + piezas retemporizadas → intermedio .mkv."""
    paso = 1.0 / (fps_fuente or 30)
    a = min(p.desde for p in seg.piezas)
    b = max(max(p.hasta, p.desde + 2 * paso) for p in seg.piezas)
    ss = max(0.0, a - 0.05)
    cmd = [config.FFMPEG_PATH, "-y", "-ss", f"{ss:.3f}", "-t", f"{b - ss + 0.3:.3f}", "-i", seg.fuente]
    k = len(seg.piezas)
    geo = seg.vf or vf_cubrir(ancho, alto)
    # Un grafo que ya nombra su entrada ([0:v]…, como el layout de competitivo)
    # va tal cual; si no, se le antepone. Su última cadena tiene que quedar
    # abierta: acá se le suma el retime.
    cabeza = geo if geo.startswith("[") else f"[0:v]{geo}"
    partes = [f"{cabeza},setsar=1,format=yuv420p,split={k}" + "".join(f"[p{i}]" for i in range(k))]
    for i, p in enumerate(seg.piezas):
        rel = p.desde - ss
        n = _n(p.dur, fps)
        if p.congelado:
            fuente = f"trim=start={rel:.4f}:duration={2 * paso:.4f},setpts=PTS-STARTPTS"
        else:
            fuente = (f"trim=start={rel:.4f}:end={rel + (p.hasta - p.desde):.4f},"
                      f"setpts=(PTS-STARTPTS)/{p.velocidad:.6f}")
        partes.append(f"[p{i}]{fuente},fps={fps},tpad=stop_mode=clone:stop_duration={p.dur + 1:.3f},"
                      f"trim=end_frame={n},setpts=PTS-STARTPTS[v{i}]")
    partes.append("".join(f"[v{i}]" for i in range(k)) + f"concat=n={k}:v=1:a=0[vout]")
    if con_audio:
        partes.append("[0:a]aresample=48000,aformat=channel_layouts=stereo,asplit="
                      f"{k}" + "".join(f"[q{i}]" for i in range(k)))
        for i, p in enumerate(seg.piezas):
            rel = p.desde - ss
            fin = _n(p.dur, fps) / fps
            if p.congelado:
                partes.append(f"[q{i}]atrim=start={rel:.4f}:duration=0.01,asetpts=PTS-STARTPTS,volume=0,"
                              f"apad,atrim=end={fin:.4f}[a{i}]")
            else:
                partes.append(f"[q{i}]atrim=start={rel:.4f}:end={rel + (p.hasta - p.desde):.4f},"
                              f"asetpts=PTS-STARTPTS,{ffmpeg_runner.atempo_chain(p.velocidad)},"
                              f"apad,atrim=end={fin:.4f}[a{i}]")
        partes.append("".join(f"[a{i}]" for i in range(k)) + f"concat=n={k}:v=0:a=1[aout]")
        amap = "[aout]"
    else:
        total = sum(_n(p.dur, fps) for p in seg.piezas) / fps
        cmd += ["-f", "lavfi", "-t", f"{total:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
        amap = "1:a"
    cmd += ["-filter_complex", ";".join(partes), "-map", "[vout]", "-map", amap,
            *ffmpeg_runner.video_encode_flags(INTERMEDIO_CRF), "-c:a", "pcm_s16le", out]
    return cmd


def build_final(lista: str, edl: Edl, out: str, ass: str | None = None,
                intensidad: float = 1.0, marca_recorte: tuple | None = None) -> list[str]:
    """Pasada 2: une los planos y aplica color, acentos, letreros y música."""
    dur = ritmo.cuadros(edl) / edl.fps
    cmd = [config.FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0", "-i", lista]
    idx = 1
    musica_idx = marca_idx = None
    if edl.musica:
        cmd += ["-ss", f"{edl.musica_desde:.3f}", "-i", edl.musica]
        musica_idx, idx = idx, idx + 1
    if edl.marca:
        cmd += ["-i", edl.marca]
        marca_idx, idx = idx, idx + 1

    W, H = edl.ancho, edl.alto
    partes = []
    cadena = [c for c in (efectos.color(edl.look, edl.lut),) if c]
    actual = "0:v"
    if cadena:
        partes.append(f"[{actual}]{','.join(cadena)}[col]")
        actual = "col"
    if edl.glow > 0:
        partes.append(efectos.glow(actual, "brillo", edl.glow, W, H))
        actual = "brillo"
    acentos = [f for f in (efectos.flash(edl.eventos, intensidad),
                           efectos.camara(edl.eventos, W, H, edl.fps, intensidad),
                           efectos.rgb(edl.eventos, intensidad)) if f]
    if edl.viñeta:
        acentos.append("vignette=angle=PI/5")
    if ass:
        from modules.subtitles.processor import ass_filter
        acentos.append(ass_filter(ass))
    partes.append(f"[{actual}]" + (",".join(acentos) + "," if acentos else "") + "setsar=1[pre]")
    if marca_idx is not None:
        if marca_recorte:
            pw, ph, px, py = marca_recorte
            recorte = f"crop={pw}:{ph}:{px}:{py},"
        else:
            recorte = ""
        partes.append(f"[{marca_idx}:v]{recorte}scale=-2:{edl.marca_alto}:flags=lanczos,format=rgba[marca]")
        partes.append(f"[pre][marca]overlay=W-w-{MARCA_MARGEN}:H-h,{SALIDA}[vout]")
    else:
        partes.append(f"[pre]{SALIDA}[vout]")

    fmt = "aresample=48000,aformat=channel_layouts=stereo"
    fuentes = []
    if edl.volumen_original > 0:
        partes.append(f"[0:a]{fmt},volume={edl.volumen_original:.3f}[orig]")
        fuentes.append("[orig]")
    if musica_idx is not None:
        fade = min(edl.fade_salida, dur / 3)
        partes.append(f"[{musica_idx}:a]{fmt},atrim=0:{dur:.4f},asetpts=PTS-STARTPTS,"
                      f"afade=t=out:st={max(0.0, dur - fade):.4f}:d={fade:.4f},"
                      f"volume={edl.volumen_musica:.3f}[mus]")
        fuentes.append("[mus]")
    if not fuentes:
        partes.append(f"[0:a]{fmt},volume=0[orig]")
        fuentes.append("[orig]")
    partes.append(f"{''.join(fuentes)}amix=inputs={len(fuentes)}:normalize=0:duration=first,"
                  f"{LIMITADOR},apad,atrim=0:{dur:.4f}[aout]")
    cmd += ["-filter_complex", ";".join(partes), "-map", "[vout]", "-map", "[aout]",
            "-r", str(edl.fps), "-t", f"{dur:.4f}",
            *ffmpeg_runner.video_encode_flags(), "-maxrate", "16M", "-bufsize", "32M",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", out]
    return cmd


def _preparar(seg: Segmento, trabajo: str, interpolar: bool, escalar_hasta: int) -> Segmento:
    """RIFE / Real-ESRGAN sobre el tramo del plano, si corresponden y están.
    Devuelve el plano apuntando al archivo procesado (tiempos desde 0)."""
    lento = min((p.velocidad for p in seg.piezas if not p.congelado), default=1.0)
    hace_rife = interpolar and seg.interpolar and lento < VEL_INTERPOLAR and externos.rife_disponible()
    res = ffmpeg_runner.probe_resolution(seg.fuente)
    hace_esrgan = bool(escalar_hasta and res and res[1] < escalar_hasta and externos.esrgan_disponible())
    if not (hace_rife or hace_esrgan):
        return seg
    a = min(p.desde for p in seg.piezas)
    b = max(p.hasta for p in seg.piezas) + 0.1
    fuente, desde, hasta = seg.fuente, a, b
    os.makedirs(trabajo, exist_ok=True)
    if hace_esrgan:
        fuente = externos.escalar(fuente, desde, hasta, trabajo)
        desde, hasta = 0.0, b - a
    if hace_rife:
        factor = 4 if lento < 0.3 else 2
        fuente, _ = externos.interpolar(fuente, desde, hasta, factor, trabajo)
    piezas = [Pieza(p.desde - a, p.hasta - a, p.dur) for p in seg.piezas]
    return Segmento(fuente, piezas, seg.etiqueta, seg.vf, False)


def render(edl: Edl, out: str, job_id: str | None, trabajo: str, *,
           progreso: tuple[int, int] = (0, 100), intensidad: float = 1.0,
           texto_y: float = 0.5, acento: str = "#FFD640", marca_recorte: tuple | None = None,
           interpolar: bool = False, escalar_hasta: int = 0) -> dict:
    """Renderiza la EDL en `out`. `trabajo`: carpeta para los intermedios (se
    borra al terminar). Devuelve un resumen (duración, planos, qué se usó)."""
    edl.validar()
    ritmo.cuantizar(edl)
    os.makedirs(trabajo, exist_ok=True)
    lo, hi = progreso
    medio = lo + int((hi - lo) * 0.55)

    def avance(texto_etapa: str, pct: int) -> None:
        if job_id:
            job_manager.update_job(job_id, status="processing", stage=texto_etapa, progress=pct)

    usados = {"rife": 0, "esrgan": 0}
    intermedios = [None] * len(edl.segmentos)
    hechos = 0
    ass = None
    try:
        def uno(i: int) -> str:
            seg = edl.segmentos[i]
            sub = os.path.join(trabajo, f"pre_{i:03d}")
            listo = _preparar(seg, sub, interpolar, escalar_hasta)
            if listo is not seg:
                usados["rife" if listo.fuente.endswith("interpolado.mp4") else "esrgan"] += 1
            fps_fuente = ffmpeg_runner.probe_fps(listo.fuente) or 30.0
            con_audio = edl.volumen_original > 0 and ffmpeg_runner.has_audio_stream(listo.fuente)
            path = os.path.join(trabajo, f"plano_{i:03d}.mkv")
            ffmpeg_runner.capture(build_pase1(listo, path, edl.ancho, edl.alto, edl.fps, con_audio, fps_fuente),
                                  timeout=1800, cwd=config.BASE_DIR)
            shutil.rmtree(sub, ignore_errors=True)
            return path

        avance(f"Planos: 0/{len(edl.segmentos)}…", lo)
        with ThreadPoolExecutor(max_workers=PARALELO) as ex:
            futuros = {ex.submit(uno, i): i for i in range(len(edl.segmentos))}
            for fut in futuros:
                intermedios[futuros[fut]] = fut.result()
                hechos += 1
                avance(f"Planos: {hechos}/{len(edl.segmentos)}…",
                       lo + int((medio - lo) * hechos / len(edl.segmentos)))

        lista = os.path.join(trabajo, "lista.txt")
        with open(lista, "w", encoding="utf-8") as fh:
            for ruta in intermedios:
                ruta = ruta.replace("\\", "/").replace("'", "'\\''")
                fh.write(f"file '{ruta}'\n")
        ass = texto.escribir(edl.textos, edl.ancho, edl.alto, texto_y, acento)
        avance("Color, acentos y música…", medio)
        cmd = build_final(lista, edl, out, ass, intensidad, marca_recorte)
        if job_id:
            ffmpeg_runner.run(cmd, job_id, total_duration=edl.dur, cwd=config.BASE_DIR,
                              progress_range=(medio, hi))
        else:
            ffmpeg_runner.capture(cmd, timeout=3600, cwd=config.BASE_DIR)
    finally:
        if ass and os.path.exists(ass):
            os.remove(ass)
        shutil.rmtree(trabajo, ignore_errors=True)
    return {"dur": round(ritmo.cuadros(edl) / edl.fps, 3), "planos": len(edl.segmentos),
            "rife": usados["rife"], "esrgan": usados["esrgan"]}
