"""Lógica FFmpeg del módulo insert.

Inserta un mini-clip completo (corte seco) en cada marcador del timeline del
original, sin transformar el contenido visible. El original y el mini-clip se
asumen del mismo formato (resolución); el procesador valida eso antes.

Se arma con el `concat` filter: se trocea el original en los marcadores, se
duplica (split) el mini-clip una vez por inserción, y se concatena alternando.
Lo único que se normaliza es lo invisible que `concat` exige: fps común, SAR=1,
pixel format y parámetros de audio. Si una fuente no trae audio, se genera
silencio para ese tramo.
"""
import config
from core import ffmpeg_runner, job_manager
from modules.insert.schema import InsertParams

# Audio común para que concat empalme sin glitches.
_SR = 44100
_AFMT = f"aformat=sample_fmts=fltp:sample_rates={_SR}:channel_layouts=stereo"
_EPS = 0.001


class ResolutionMismatch(Exception):
    """El mini-clip no coincide en resolución con el original."""


def build_command(original_path: str, clip_path: str, output_path: str,
                  params: InsertParams, *, fps: float,
                  orig_dur: float | None, clip_dur: float | None,
                  orig_has_audio: bool, clip_has_audio: bool) -> list[str]:
    """Construye el comando FFmpeg completo."""
    fps_s = f"{fps:.5f}"

    # Marcadores acotados al largo del original.
    markers = list(params.markers)
    if orig_dur is not None:
        markers = [min(m, orig_dur) for m in markers]
    n = len(markers)

    # Tramos del original: [0,t1], [t1,t2], ..., [tn, EOF]. None = hasta el final.
    segments: list[tuple[float, float | None]] = []
    prev = 0.0
    for t in markers:
        segments.append((prev, t))
        prev = t
    segments.append((prev, None))

    # Inputs extra de silencio (solo si alguna fuente no tiene audio).
    inputs: list[str] = ["-i", original_path, "-i", clip_path]
    next_idx = 2
    sil_o = sil_c = None
    if not orig_has_audio:
        inputs += ["-f", "lavfi", "-i",
                   f"anullsrc=channel_layout=stereo:sample_rate={_SR}"]
        sil_o = next_idx
        next_idx += 1
    if not clip_has_audio:
        inputs += ["-f", "lavfi", "-i",
                   f"anullsrc=channel_layout=stereo:sample_rate={_SR}"]
        sil_c = next_idx
        next_idx += 1

    filters: list[str] = []

    # --- Mini-clip: normalizado una vez y duplicado n veces ---
    filters.append(
        f"[1:v]fps={fps_s},setsar=1,format=yuv420p,split={n}"
        + "".join(f"[cv{i}]" for i in range(n))
    )
    if clip_has_audio:
        filters.append(f"[1:a]{_AFMT},asplit={n}" + "".join(f"[ca{i}]" for i in range(n)))
    else:
        dur = clip_dur if clip_dur is not None else 0
        filters.append(
            f"[{sil_c}:a]atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,{_AFMT},"
            f"asplit={n}" + "".join(f"[ca{i}]" for i in range(n))
        )

    # --- Tramos del original (se omiten los de longitud cero) ---
    order: list[str] = []  # secuencia de labels [v][a] para el concat
    for k, (a, b) in enumerate(segments):
        is_last = b is None
        seg_len = (orig_dur - a) if (is_last and orig_dur is not None) else (
            (b - a) if b is not None else None)
        # Saltar tramos vacíos (marca en 0, duplicadas, o final sin contenido).
        if seg_len is not None and seg_len <= _EPS:
            pass
        else:
            end = "" if is_last else f":end={b:.3f}"
            filters.append(
                f"[0:v]trim=start={a:.3f}{end},setpts=PTS-STARTPTS,"
                f"fps={fps_s},setsar=1,format=yuv420p[ov{k}]"
            )
            if orig_has_audio:
                a_end = "" if is_last else f":end={b:.3f}"
                filters.append(
                    f"[0:a]atrim=start={a:.3f}{a_end},asetpts=PTS-STARTPTS,{_AFMT}[oa{k}]"
                )
            else:
                trim = f"0:{seg_len:.3f}" if seg_len is not None else "0"
                filters.append(
                    f"[{sil_o}:a]atrim={trim},asetpts=PTS-STARTPTS,{_AFMT}[oa{k}]"
                )
            order += [f"[ov{k}]", f"[oa{k}]"]

        # Tras cada tramo intermedio (no el último) va una inserción del mini-clip.
        if k < n:
            order += [f"[cv{k}]", f"[ca{k}]"]

    total = len(order) // 2
    filters.append("".join(order) + f"concat=n={total}:v=1:a=1[v][a]")

    return [
        config.FFMPEG_PATH, "-y", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]


def process(job_id: str, original_path: str, clip_path: str, output_path: str,
            params: InsertParams) -> None:
    """Ejecuta el job completo (bloqueante). Actualiza job_manager en cada paso."""
    try:
        # Validación de resolución: deben coincidir (el usuario procesa ambos
        # por el módulo Vertical antes). Si difieren, error claro.
        res_o = ffmpeg_runner.probe_resolution(original_path)
        res_c = ffmpeg_runner.probe_resolution(clip_path)
        if res_o and res_c and res_o != res_c:
            raise ResolutionMismatch(
                f"El mini-clip es {res_c[0]}x{res_c[1]} y el original "
                f"{res_o[0]}x{res_o[1]}. Pásalo primero por el módulo Vertical "
                f"para que coincidan."
            )

        fps = ffmpeg_runner.probe_fps(original_path) or 30.0
        orig_dur = ffmpeg_runner.probe_duration(original_path)
        clip_dur = ffmpeg_runner.probe_duration(clip_path)
        orig_has_audio = ffmpeg_runner.has_audio_stream(original_path)
        clip_has_audio = ffmpeg_runner.has_audio_stream(clip_path)

        command = build_command(
            original_path, clip_path, output_path, params,
            fps=fps, orig_dur=orig_dur, clip_dur=clip_dur,
            orig_has_audio=orig_has_audio, clip_has_audio=clip_has_audio,
        )

        total = None
        if orig_dur is not None and clip_dur is not None:
            total = orig_dur + len(params.markers) * clip_dur
        ffmpeg_runner.run(command, job_id, total_duration=total)
        job_manager.update_job(job_id, status="done", progress=100)
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=str(exc))
