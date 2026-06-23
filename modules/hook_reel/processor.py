"""Orquestación del módulo hook_reel ("Reel Frase").

Arma un reel de DOS segmentos encadenados con música de fondo continua:

    fondo (imagen|video) + voz ─┐
                                ├─ concat ─→ música con ducking ─→ reel final
    clip 2 (acelerado a D2) ────┘

Duraciones (las manda la música):
  D1 = duración de la voz                → segmento 1
  D2 = duración de la música − D1        → segmento 2 (el clip 2 se acelera/frena)
  total = D1 + D2 = duración de la música

Música: volumen `low` durante [0, D1] y `full` durante [D1, total], con una rampa
de `ramp` segundos en el corte (el "beat drop"). El clip 2 va sin su audio.

Subtítulos (opcional, default ON) → flujo de DOS fases, igual que reel_express:
  - Fase 1 (process): convierte a vertical lo marcado y transcribe LA VOZ (audio
    limpio, sin música). Deja los segmentos en el job y conserva todo para la
    fase 2. NO ensambla todavía.
  - Fase 2 (finish): quema los subtítulos editados sobre el segmento 1 y ensambla
    el reel final en una sola pasada de FFmpeg.

La conversión a vertical reusa vertical_convert; la transcripción y el .ass
reusan subtitles. El ensamblado (concat + ducking) es la única lógica de FFmpeg
propia del módulo.
"""
import os
import threading

import config
from core import ffmpeg_runner, file_utils, job_manager
from modules.hook_reel.schema import HookReelParams
from modules.vertical_convert import processor as vertical_processor
from modules.subtitles import processor as subtitles_processor

W = config.OUTPUT_WIDTH    # 1080
H = config.OUTPUT_HEIGHT   # 1920
FPS = 30

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


def build_assembly_command(bg_path: str, bg_is_image: bool, seg2_path: str,
                           voice_path: str, music_path: str, output_path: str, *,
                           d1: float, d2: float, total: float,
                           cb: float | None, c2: float,
                           params: HookReelParams,
                           ass_path: str | None = None) -> list[str]:
    """Comando FFmpeg que ensambla el reel completo en una sola pasada.

    - bg_is_image: el fondo del seg.1 es una imagen (se loopea a D1) o un video
      (se acelera/frena de su duración nativa `cb` a D1).
    - El clip 2 se lleva de su duración `c2` a D2 con setpts (acelera si c2>d2).
    - La música arranca en `low` y sube a `full` con rampa de `ramp`s en t=D1.
    - ass_path (opcional): .ass con los subtítulos del seg.1, quemado al final.
    """
    low = params.music_low_gain
    full = params.music_full_gain
    delta = full - low
    r = params.ramp

    cmd = [config.FFMPEG_PATH, "-y"]
    # Input 0: fondo del segmento 1
    if bg_is_image:
        cmd += ["-loop", "1", "-t", f"{d1:.3f}", "-i", bg_path]
    else:
        cmd += ["-i", bg_path]
    cmd += ["-i", seg2_path]    # 1: clip 2
    cmd += ["-i", voice_path]   # 2: voz
    cmd += ["-i", music_path]   # 3: música

    filters: list[str] = []

    # --- Video segmento 1 (fondo a D1) ---
    if bg_is_image:
        filters.append(f"[0:v]{_VFILL},fps={FPS},setpts=PTS-STARTPTS[v0]")
    else:
        kb = d1 / cb if cb else 1.0
        filters.append(f"[0:v]{_VFILL},setpts={kb:.6f}*PTS,fps={FPS}[v0]")

    # --- Video segmento 2 (clip 2 acelerado/frenado a D2) ---
    k2 = d2 / c2 if c2 else 1.0
    filters.append(f"[1:v]{_VFILL},setpts={k2:.6f}*PTS,fps={FPS}[v1]")

    # --- Concat de los dos segmentos ---
    filters.append("[v0][v1]concat=n=2:v=1:a=0[vcat]")

    # --- Audio: voz full + música con automatización de volumen (ducking) ---
    # volume con eval=frame evalúa la expresión por frame de audio: low hasta D1,
    # rampa lineal de `r` segundos, full después. min/max recortan la rampa a [0,1].
    if r > 0:
        ramp_term = f"min(max((t-{d1:.4f})/{r:.4f},0),1)"
    else:
        ramp_term = f"gte(t,{d1:.4f})"  # rampa 0 = escalón seco
    vol_expr = f"volume='{low:.4f}+({delta:.4f})*{ramp_term}':eval=frame"

    filters.append("[2:a]aresample=44100,aformat=channel_layouts=stereo[voice]")
    filters.append(
        f"[3:a]aresample=44100,aformat=channel_layouts=stereo,{vol_expr}[music]"
    )
    filters.append(
        "[voice][music]amix=inputs=2:normalize=0:duration=longest[aout]"
    )

    # --- Subtítulos (opcional) sobre el video ya concatenado ---
    vmap = "[vcat]"
    if ass_path:
        fonts_dir = os.path.relpath(config.FONTS_FOLDER, config.BASE_DIR).replace("\\", "/")
        filters.append(f"[vcat]ass={_esc_ass(ass_path)}:fontsdir={fonts_dir}[vout]")
        vmap = "[vout]"

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", vmap,
        "-map", "[aout]",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]
    return cmd


def _run_assembly(job_id: str, ctx: dict, output_path: str, *,
                  ass_path: str | None, progress_range: tuple[int, int]) -> None:
    command = build_assembly_command(
        ctx["bg_ready"], ctx["bg_is_image"], ctx["seg2_ready"],
        ctx["voice_path"], ctx["music_path"], output_path,
        d1=ctx["d1"], d2=ctx["d2"], total=ctx["total"],
        cb=ctx["cb"], c2=ctx["c2"], params=ctx["params"],
        ass_path=ass_path,
    )
    cwd = config.BASE_DIR if ass_path else None
    ffmpeg_runner.run(command, job_id, total_duration=ctx["total"],
                      cwd=cwd, progress_range=progress_range)


# ---------------------------------------------------------------------------
# Fase 1 — preparación (vertical) + (opcional) transcripción de la voz
# ---------------------------------------------------------------------------

def process(job_id: str, *, voice_path: str, bg_path: str, bg_is_image: bool,
            clip2_path: str, music_path: str, params: HookReelParams) -> None:
    """Fase 1 (bloqueante). Convierte a vertical lo marcado y, si hay subs,
    transcribe la voz y pausa para edición; si no, ensambla el reel final."""
    uploads = [voice_path, bg_path, clip2_path, music_path]
    intermedios: list[str] = []
    try:
        job_manager.update_job(job_id, status="processing", progress=0)

        # --- Duraciones (la música manda el total) ---
        d1 = ffmpeg_runner.probe_duration(voice_path)
        total = ffmpeg_runner.probe_duration(music_path)
        c2 = ffmpeg_runner.probe_duration(clip2_path)
        if not d1:
            raise RuntimeError("No se pudo leer la duración de la voz.")
        if not total:
            raise RuntimeError("No se pudo leer la duración de la música.")
        if not c2:
            raise RuntimeError("No se pudo leer la duración del clip 2.")
        if total <= d1 + 0.3:
            raise RuntimeError(
                f"La música ({total:.1f}s) debe durar más que la voz ({d1:.1f}s) "
                "para dejar espacio al segundo clip. Usá una música más larga."
            )
        d2 = total - d1
        cb = None if bg_is_image else ffmpeg_runner.probe_duration(bg_path)
        if not bg_is_image and not cb:
            raise RuntimeError("No se pudo leer la duración del video de fondo.")

        # --- Prep: conversión a vertical de los clips marcados (en paralelo) ---
        sub_bg = None
        bg_ready = bg_path
        if not bg_is_image and params.seg1_vertical:
            sub_bg = job_manager.create_job(bg_path, output_path="")
            bg_ready = file_utils.output_path_for(sub_bg)
        sub_seg2 = None
        seg2_ready = clip2_path
        if params.seg2_vertical:
            sub_seg2 = job_manager.create_job(clip2_path, output_path="")
            seg2_ready = file_utils.output_path_for(sub_seg2)

        job_manager.update_job(job_id, phase="prep", sub_bg=sub_bg, sub_seg2=sub_seg2)

        threads = []
        if sub_bg:
            threads.append(threading.Thread(
                target=vertical_processor.process,
                args=(sub_bg, bg_path, bg_ready, params.vertical), daemon=True))
        if sub_seg2:
            threads.append(threading.Thread(
                target=vertical_processor.process,
                args=(sub_seg2, clip2_path, seg2_ready, params.vertical), daemon=True))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if sub_bg:
            _check_subjob(sub_bg, "la conversión a vertical del fondo")
            intermedios.append(bg_ready)
            # vertical re-encoda: la duración nativa para el setpts ahora es la del
            # vertical (misma duración que el original, pero la re-leemos por las dudas).
            cb = ffmpeg_runner.probe_duration(bg_ready) or cb
        if sub_seg2:
            _check_subjob(sub_seg2, "la conversión a vertical del clip 2")
            intermedios.append(seg2_ready)
            c2 = ffmpeg_runner.probe_duration(seg2_ready) or c2

        ctx = {
            "bg_ready": bg_ready, "bg_is_image": bg_is_image, "cb": cb,
            "seg2_ready": seg2_ready, "c2": c2,
            "voice_path": voice_path, "music_path": music_path,
            "d1": d1, "d2": d2, "total": total, "params": params,
        }

        # --- Con subtítulos: transcribir la voz y pausar (fase 2 ensambla) ---
        if params.has_subtitles:
            sub_t = job_manager.create_job(voice_path, output_path="")
            job_manager.update_job(job_id, phase="transcribe", sub_t=sub_t)
            segments = subtitles_processor.transcribe(
                voice_path, params.subtitles, job_id=sub_t
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
    uploads = [ctx["voice_path"], ctx["music_path"]]
    # bg/seg2 originales y los intermedios verticales también se limpian al final.
    extras = [ctx["bg_ready"], ctx["seg2_ready"]]
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
        file_utils.cleanup_paths(ass_path, *uploads, *extras)
