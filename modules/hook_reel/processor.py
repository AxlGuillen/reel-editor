"""Orquestación del módulo hook_reel ("Reel Frase").

Arma un reel de DOS videos encadenados con música de fondo:

    video 1 (avatar + voz)  ─┐
                             ├─ concat ─→ música con ducking (recortada) ─→ reel
    video 2 (acelerado a D2)─┘

Duraciones:
  D1 = duración nativa del video 1            → segmento 1 (no se reescala)
  D2 = seg2_duration (elegida por el usuario) → segmento 2 (el clip 2 se acelera/
                                                frena con setpts para cubrirla)
  total = D1 + D2                             → la música se recorta a esto

Música: volumen `low` durante [0, D1] y `full` durante [D1, total], con rampa de
`ramp` segundos en el corte. Se recorta a `total` (NO se acelera) y cierra con un
fade-out corto. El video 2 va sin su audio (solo música). El video 1 conserva su
audio (la voz del avatar), mezclado con la música.

Subtítulos (opcional, default ON) → flujo de DOS fases (igual que reel_express):
  - Fase 1 (process): convierte a vertical lo marcado y transcribe el audio del
    video 1 (la voz, limpia, sin música). Deja los segmentos y NO ensambla.
  - Fase 2 (finish): quema los subtítulos editados sobre el seg.1 y ensambla.
"""
import os
import threading

import config
from core import ffmpeg_runner, file_utils, job_manager
from modules.hook_reel.schema import HookReelParams
from modules.vertical_convert import processor as vertical_processor
from modules.downloader import processor as downloader_processor
from modules.subtitles import processor as subtitles_processor

W = config.OUTPUT_WIDTH    # 1080
H = config.OUTPUT_HEIGHT   # 1920
FPS = 30
MUSIC_FADE = 0.4           # fade-out de la música al final (s)

# Relleno a 9:16: cubre el lienzo (sin barras) y recorta el excedente.
_VFILL = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"


def _check_subjob(sub_id: str, etapa: str) -> dict:
    sub = job_manager.get_job(sub_id)
    if not sub or sub["status"] == "error":
        detalle = (sub or {}).get("error") or "error desconocido"
        raise RuntimeError(f"Falló en {etapa}: {detalle}")
    return sub


# ---------------------------------------------------------------------------
# Ensamblado (FFmpeg)
# ---------------------------------------------------------------------------

def _esc_ass(path: str) -> str:
    """Ruta relativa a BASE_DIR escapada para el parser de filtros (Windows)."""
    rel = os.path.relpath(path, config.BASE_DIR).replace("\\", "/")
    return rel.replace(":", "\\:").replace("'", "\\'")


def build_assembly_command(video1_path: str, video2_path: str, music_path: str,
                           output_path: str, *, d1: float, d2: float, c2: float,
                           total: float, has_v1_audio: bool,
                           params: HookReelParams,
                           ass_path: str | None = None) -> list[str]:
    """Comando FFmpeg que ensambla el reel completo en una sola pasada.

    - El video 1 va a su velocidad nativa (duración D1).
    - El video 2 se lleva de su duración `c2` a D2 con setpts (acelera si c2>d2).
    - La música arranca en `low` y sube a `full` con rampa de `ramp`s en t=D1,
      se recorta a `total` y cierra con un fade-out corto. NO se acelera.
    - ass_path (opcional): .ass con los subtítulos del seg.1, quemado al final.
    """
    low = params.music_low_gain
    full = params.music_full_gain
    delta = full - low
    r = params.ramp

    cmd = [
        config.FFMPEG_PATH, "-y",
        "-i", video1_path,   # 0
        "-i", video2_path,   # 1
        "-i", music_path,    # 2
    ]

    filters: list[str] = []

    # --- Video segmento 1 (nativo, duración D1) ---
    filters.append(f"[0:v]{_VFILL},fps={FPS},setpts=PTS-STARTPTS[v0]")
    # --- Video segmento 2 (acelerado/frenado a D2) ---
    k2 = d2 / c2 if c2 else 1.0
    filters.append(f"[1:v]{_VFILL},setpts={k2:.6f}*PTS,fps={FPS}[v1]")
    # --- Concat ---
    filters.append("[v0][v1]concat=n=2:v=1:a=0[vcat]")

    # --- Música: volumen automatizado (ducking), recortada a total + fade-out ---
    # volume con eval=frame: low hasta D1, rampa lineal de `r`s, full después.
    if r > 0:
        ramp_term = f"min(max((t-{d1:.4f})/{r:.4f},0),1)"
    else:
        ramp_term = f"gte(t,{d1:.4f})"  # rampa 0 = escalón seco
    vol_expr = f"volume='{low:.4f}+({delta:.4f})*{ramp_term}':eval=frame"
    fade_st = max(0.0, total - MUSIC_FADE)
    filters.append(
        f"[2:a]aresample=44100,aformat=channel_layouts=stereo,{vol_expr},"
        f"atrim=0:{total:.3f},afade=t=out:st={fade_st:.3f}:d={MUSIC_FADE}[music]"
    )

    # --- Audio: voz del video 1 (si tiene) + música ---
    if has_v1_audio:
        filters.append("[0:a]aresample=44100,aformat=channel_layouts=stereo[voice]")
        filters.append(
            "[voice][music]amix=inputs=2:normalize=0:duration=longest[aout]"
        )
        amap = "[aout]"
    else:
        amap = "[music]"

    # --- Subtítulos (opcional) sobre el video ya concatenado ---
    last = "[vcat]"
    if ass_path:
        fonts_dir = os.path.relpath(config.FONTS_FOLDER, config.BASE_DIR).replace("\\", "/")
        filters.append(f"[vcat]ass={_esc_ass(ass_path)}:fontsdir={fonts_dir}[vsub]")
        last = "[vsub]"

    # Normalización final a yuv420p rango tv. Al concatenar dos videos distintos
    # (p. ej. un avatar en yuvj420p/rango completo + el clip), la salida heredaba
    # rango completo y un pixfmt que el reproductor nativo de Windows rechaza
    # (0x80004005). yuv420p rango tv es lo universalmente compatible.
    filters.append(f"{last}scale=out_range=tv,format=yuv420p[vout]")
    vmap = "[vout]"

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", vmap,
        "-map", amap,
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    return cmd


def _run_assembly(job_id: str, ctx: dict, output_path: str, *,
                  ass_path: str | None, progress_range: tuple[int, int]) -> None:
    command = build_assembly_command(
        ctx["video1_ready"], ctx["video2_ready"], ctx["music_path"], output_path,
        d1=ctx["d1"], d2=ctx["d2"], c2=ctx["c2"], total=ctx["total"],
        has_v1_audio=ctx["has_v1_audio"], params=ctx["params"], ass_path=ass_path,
    )
    cwd = config.BASE_DIR if ass_path else None
    ffmpeg_runner.run(command, job_id, total_duration=ctx["total"],
                      cwd=cwd, progress_range=progress_range)


# ---------------------------------------------------------------------------
# Fase 1 — preparación (vertical) + (opcional) transcripción de la voz
# ---------------------------------------------------------------------------

def process(job_id: str, *, video1_path: str, video2_path: str,
            params: HookReelParams) -> None:
    """Fase 1 (bloqueante). Descarga la música, convierte a vertical lo marcado
    y, si hay subs, transcribe la voz del video 1 y pausa; si no, ensambla."""
    uploads = [video1_path, video2_path]
    intermedios: list[str] = []
    try:
        job_manager.update_job(job_id, status="processing", progress=0)

        # --- Duraciones ---
        d1 = ffmpeg_runner.probe_duration(video1_path)
        c2 = ffmpeg_runner.probe_duration(video2_path)
        if not d1:
            raise RuntimeError("No se pudo leer la duración del video 1.")
        if not c2:
            raise RuntimeError("No se pudo leer la duración del video 2.")
        d2 = params.seg2_duration
        total = d1 + d2
        has_v1_audio = ffmpeg_runner.has_audio_stream(video1_path)

        # --- Prep: descarga de la música + conversión a vertical (en paralelo) ---
        sub_d = job_manager.create_job(params.downloader.url, output_path="")
        sub_v1 = None
        v1_ready = video1_path
        if params.seg1_vertical:
            sub_v1 = job_manager.create_job(video1_path, output_path="")
            v1_ready = file_utils.output_path_for(sub_v1)
        sub_v2 = None
        v2_ready = video2_path
        if params.seg2_vertical:
            sub_v2 = job_manager.create_job(video2_path, output_path="")
            v2_ready = file_utils.output_path_for(sub_v2)

        job_manager.update_job(job_id, phase="prep", sub_d=sub_d,
                               sub_v1=sub_v1, sub_v2=sub_v2)

        threads = [threading.Thread(
            target=downloader_processor.process,
            args=(sub_d, params.downloader), daemon=True)]
        if sub_v1:
            threads.append(threading.Thread(
                target=vertical_processor.process,
                args=(sub_v1, video1_path, v1_ready, params.vertical), daemon=True))
        if sub_v2:
            threads.append(threading.Thread(
                target=vertical_processor.process,
                args=(sub_v2, video2_path, v2_ready, params.vertical), daemon=True))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        music_path = _check_subjob(sub_d, "la descarga de la música")["output_path"]
        intermedios.append(music_path)
        if sub_v1:
            _check_subjob(sub_v1, "la conversión a vertical del video 1")
            intermedios.append(v1_ready)
            d1 = ffmpeg_runner.probe_duration(v1_ready) or d1
            total = d1 + d2
            has_v1_audio = ffmpeg_runner.has_audio_stream(v1_ready)
        if sub_v2:
            _check_subjob(sub_v2, "la conversión a vertical del video 2")
            intermedios.append(v2_ready)
            c2 = ffmpeg_runner.probe_duration(v2_ready) or c2

        ctx = {
            "video1_ready": v1_ready, "video2_ready": v2_ready,
            "music_path": music_path, "c2": c2,
            "d1": d1, "d2": d2, "total": total,
            "has_v1_audio": has_v1_audio, "params": params,
        }

        # --- Con subtítulos: transcribir la voz del video 1 y pausar ---
        if params.has_subtitles:
            if not has_v1_audio:
                raise RuntimeError(
                    "El video 1 no tiene audio: no se puede transcribir. "
                    "Desactivá los subtítulos o usá un video 1 con voz."
                )
            sub_t = job_manager.create_job(v1_ready, output_path="")
            job_manager.update_job(job_id, phase="transcribe", sub_t=sub_t)
            segments = subtitles_processor.transcribe(
                v1_ready, params.subtitles, job_id=sub_t
            )
            job_manager.update_job(sub_t, status="done", progress=100)
            job_manager.update_job(
                job_id, status="done", progress=100, phase=None,
                segments=segments, hook_ctx=ctx,
            )
            return

        # --- Sin subtítulos: ensamblar el reel final ahora ---
        job_manager.update_job(job_id, phase="assemble")
        output_path = file_utils.output_path_for(job_id)
        _run_assembly(job_id, ctx, output_path, ass_path=None,
                      progress_range=(40, 100))
        job_manager.update_job(job_id, status="done", progress=100, phase=None,
                               output_path=output_path)
        file_utils.cleanup_paths(*uploads, *intermedios)
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
        file_utils.cleanup_paths(*uploads, *intermedios)


# ---------------------------------------------------------------------------
# Fase 2 — quemar subtítulos + ensamblar (con subs)
# ---------------------------------------------------------------------------

def finish(job_id: str, ctx: dict, segments: list[dict], sub_params,
           output_path: str) -> None:
    """Fase 2: arma el .ass con los segmentos editados, lo quema sobre el seg.1
    y ensambla el reel final en una sola pasada."""
    ass_path = None
    # video1/video2 originales y los intermedios verticales también se limpian.
    cleanup = [ctx["music_path"], ctx["video1_ready"], ctx["video2_ready"]]
    try:
        job_manager.update_job(job_id, status="processing", progress=5,
                               stage="Generando subtítulos…")
        seg_words = subtitles_processor.words_from_segments(segments)
        if not seg_words:
            raise RuntimeError("No hay texto en los subtítulos para generar.")

        font_name = "Poppins-Bold"
        font_path = config.resolve_font_path()
        if font_path:
            font_name = os.path.splitext(os.path.basename(font_path))[0]

        ass_content = subtitles_processor.build_ass(seg_words, sub_params,
                                                    font_name=font_name)
        ass_path = subtitles_processor._write_ass(ass_content)

        job_manager.update_job(job_id, progress=10, stage="Ensamblando reel…")
        _run_assembly(job_id, ctx, output_path, ass_path=ass_path,
                      progress_range=(10, 100))
        job_manager.update_job(job_id, status="done", progress=100, stage=None,
                               output_path=output_path)
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
    finally:
        file_utils.cleanup_paths(ass_path, *cleanup)
