"""Lógica FFmpeg del módulo insert.

Dos motores de ensamblado, ambos sobre el `concat` filter:

  - Modo "full" (clásico): inserta el mini-clip completo (corte seco) en cada
    marcador. El original se trocea en los marcadores y se concatena alternando.

  - Modo "progressive" ("caída progresiva"): trocea el mini-clip en pedazos que
    avanzan (0–2s, 2–4s, …) y mete el pedazo k en el marcador k; al final (o en
    `reveal_at`) muestra el mini-clip completo, con opción de acelerarlo.

  - Cutaway (para reel_express): REEMPLAZA tramos del video de fondo por pedazos
    del mini-clip SIN cambiar la duración total, y deja el audio del base intacto
    (la narración corre por debajo sin cortarse). Ver `build_cutaway_command`.

Se normaliza solo lo invisible que `concat`/`overlay` exigen: fps común, SAR=1,
pixel format y parámetros de audio. Si una fuente no trae audio, se genera
silencio para ese tramo.
"""
import config
from core import ffmpeg_runner, file_utils, job_manager
from modules.insert.schema import InsertParams
from modules.vertical_convert import processor as vertical_processor

# Audio común para que concat empalme sin glitches.
_SR = 44100
_AFMT = f"aformat=sample_fmts=fltp:sample_rates={_SR}:channel_layouts=stereo"
_EPS = 0.001


class ResolutionMismatch(Exception):
    """El mini-clip no coincide en resolución con el original."""


# ─────────────────────────── troceo (reusable) ───────────────────────────

def _slice_plan(clip_dur: float, n: int,
                slice_durations: list[float] | None = None) -> list[tuple[float, float]]:
    """Devuelve [(start, dur), …] de los n adelantos que avanzan por el mini-clip.

    Sin `slice_durations`, reparte el mini-clip en n partes iguales. Con
    duraciones manuales, cada adelanto arranca donde terminó el anterior.
    """
    if slice_durations:
        plan: list[tuple[float, float]] = []
        acc = 0.0
        for d in slice_durations:
            plan.append((acc, d))
            acc += d
        return plan
    seg = clip_dur / n
    return [(k * seg, seg) for k in range(n)]


def _validate_resolution(original_path: str, clip_path: str) -> None:
    res_o = ffmpeg_runner.probe_resolution(original_path)
    res_c = ffmpeg_runner.probe_resolution(clip_path)
    if res_o and res_c and res_o != res_c:
        raise ResolutionMismatch(
            f"El mini-clip es {res_c[0]}x{res_c[1]} y el original "
            f"{res_o[0]}x{res_o[1]}. Pásalo primero por el módulo Vertical "
            f"para que coincidan."
        )


# ─────────────────────────── modo "full" (clásico) ───────────────────────────

def _clip_events(params: InsertParams, orig_dur: float | None) -> list[tuple[float, int]]:
    """(instante, índice de clip) de cada inserción, en orden temporal.

    Cada mini-clip aporta sus propios marcadores; el orden global es el que
    manda para trocear el original.
    """
    events: list[tuple[float, int]] = []
    for ci, spec in enumerate(params.clips):
        for m in spec.markers:
            t = min(m, orig_dur) if orig_dur is not None else m
            events.append((t, ci))
    events.sort(key=lambda e: (e[0], e[1]))
    return events


def _build_full(original_path: str, clip_paths: list[str], output_path: str,
                params: InsertParams, *, fps_s: str, orig_dur: float | None,
                clip_durs: list[float | None], orig_has_audio: bool,
                clip_has_audios: list[bool]) -> list[str]:
    """Inserta cada mini-clip completo en sus marcadores (corte seco)."""
    events = _clip_events(params, orig_dur)
    n = len(events)

    # Tramos del original: [0,t1], [t1,t2], …, [tn, EOF].
    segments: list[tuple[float, float | None]] = []
    prev = 0.0
    for t, _ in events:
        segments.append((prev, t))
        prev = t
    segments.append((prev, None))

    # Inputs: 0 = original, 1..N = mini-clips, después los silencios que hagan falta.
    inputs: list[str] = ["-i", original_path]
    for path in clip_paths:
        inputs += ["-i", path]
    next_idx = 1 + len(clip_paths)

    sil_o = None
    if not orig_has_audio:
        inputs += ["-f", "lavfi", "-i",
                   f"anullsrc=channel_layout=stereo:sample_rate={_SR}"]
        sil_o = next_idx
        next_idx += 1
    sil_c: dict[int, int] = {}   # índice de clip → input de silencio
    for ci, has_audio in enumerate(clip_has_audios):
        if not has_audio:
            inputs += ["-f", "lavfi", "-i",
                       f"anullsrc=channel_layout=stereo:sample_rate={_SR}"]
            sil_c[ci] = next_idx
            next_idx += 1

    filters: list[str] = []

    # Cada mini-clip se normaliza una vez y se duplica tantas veces como marcas tenga.
    usos: dict[int, int] = {}
    for _, ci in events:
        usos[ci] = usos.get(ci, 0) + 1
    for ci, veces in sorted(usos.items()):
        vlabels = "".join(f"[c{ci}v{j}]" for j in range(veces))
        filters.append(
            f"[{ci + 1}:v]fps={fps_s},setsar=1,format=yuv420p,split={veces}{vlabels}"
        )
        alabels = "".join(f"[c{ci}a{j}]" for j in range(veces))
        if clip_has_audios[ci]:
            filters.append(f"[{ci + 1}:a]{_AFMT},asplit={veces}{alabels}")
        else:
            dur = clip_durs[ci] if clip_durs[ci] is not None else 0
            filters.append(
                f"[{sil_c[ci]}:a]atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,{_AFMT},"
                f"asplit={veces}{alabels}"
            )

    order: list[str] = []
    emitidos: dict[int, int] = {}   # copias ya consumidas de cada clip
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

        # Tras cada tramo intermedio va la inserción que corresponde a esa marca.
        if k < n:
            ci = events[k][1]
            j = emitidos.get(ci, 0)
            emitidos[ci] = j + 1
            order += [f"[c{ci}v{j}]", f"[c{ci}a{j}]"]

    total = len(order) // 2
    filters.append("".join(order) + f"concat=n={total}:v=1:a=1[v][a]")

    return [
        config.FFMPEG_PATH, "-y", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        *ffmpeg_runner.video_encode_flags(),
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]


# ─────────────────────── modo "progressive" (caída) ───────────────────────

def _build_progressive(original_path: str, clip_path: str, output_path: str,
                       params: InsertParams, *, fps_s: str, orig_dur: float | None,
                       clip_dur: float, orig_has_audio: bool,
                       clip_has_audio: bool) -> list[str]:
    """Adelantos que avanzan por el mini-clip + reveal completo (opcional)."""
    markers = list(params.markers)
    if orig_dur is not None:
        markers = [min(m, orig_dur) for m in markers]
    n = len(markers)

    plan = _slice_plan(clip_dur, n, params.slice_durations)
    used = sum(d for _, d in plan)
    if used > clip_dur + _EPS:
        raise ValueError(
            f"Los recortes suman {used:.2f}s pero el mini-clip dura {clip_dur:.2f}s. "
            "Bajá las duraciones o agregá menos marcadores."
        )

    # ¿Necesitamos una fuente de silencio? (mute, o alguna fuente sin audio)
    mute = params.clip_audio == "mute"
    need_clip_sil = mute or not clip_has_audio
    need_orig_sil = not orig_has_audio

    inputs: list[str] = ["-i", original_path, "-i", clip_path]
    next_idx = 2
    sil_o = sil_c = None
    if need_orig_sil:
        inputs += ["-f", "lavfi", "-i",
                   f"anullsrc=channel_layout=stereo:sample_rate={_SR}"]
        sil_o = next_idx
        next_idx += 1
    if need_clip_sil:
        inputs += ["-f", "lavfi", "-i",
                   f"anullsrc=channel_layout=stereo:sample_rate={_SR}"]
        sil_c = next_idx
        next_idx += 1

    # Eventos ordenados por posición: cada marcador es un adelanto; el reveal, si
    # tiene posición fija, es un evento más. (Reveal al final se agrega aparte.)
    events: list[tuple[float, tuple]] = [
        (markers[k], ("teaser", k)) for k in range(n)
    ]
    if params.reveal_enabled and params.reveal_at is not None:
        at = params.reveal_at
        if orig_dur is not None:
            at = min(at, orig_dur)
        events.append((at, ("reveal",)))
    events.sort(key=lambda e: e[0])

    filters: list[str] = []
    order: list[str] = []
    idx = 0  # contador de átomos para etiquetas únicas

    def emit_orig(a: float, b: float | None) -> None:
        nonlocal idx
        is_last = b is None
        seg_len = (orig_dur - a) if (is_last and orig_dur is not None) else (
            (b - a) if b is not None else None)
        if seg_len is not None and seg_len <= _EPS:
            return
        end = "" if is_last else f":end={b:.3f}"
        filters.append(
            f"[0:v]trim=start={a:.3f}{end},setpts=PTS-STARTPTS,"
            f"fps={fps_s},setsar=1,format=yuv420p[v{idx}]"
        )
        if orig_has_audio:
            filters.append(
                f"[0:a]atrim=start={a:.3f}{end},asetpts=PTS-STARTPTS,{_AFMT}[a{idx}]"
            )
        else:
            trim = f"0:{seg_len:.3f}" if seg_len is not None else "0"
            filters.append(
                f"[{sil_o}:a]atrim={trim},asetpts=PTS-STARTPTS,{_AFMT}[a{idx}]"
            )
        order.extend([f"[v{idx}]", f"[a{idx}]"])
        idx += 1

    def emit_clip(start: float, dur: float, speed: float) -> None:
        nonlocal idx
        end = start + dur
        pts = "PTS-STARTPTS" if speed == 1.0 else f"(PTS-STARTPTS)/{speed:.5f}"
        filters.append(
            f"[1:v]trim=start={start:.3f}:end={end:.3f},setpts={pts},"
            f"fps={fps_s},setsar=1,format=yuv420p[v{idx}]"
        )
        if clip_has_audio and not mute:
            atempo = f"atempo={speed:.5f}," if speed != 1.0 else ""
            filters.append(
                f"[1:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,"
                f"{atempo}{_AFMT}[a{idx}]"
            )
        else:
            out_dur = dur / speed
            filters.append(
                f"[{sil_c}:a]atrim=0:{out_dur:.3f},asetpts=PTS-STARTPTS,{_AFMT}[a{idx}]"
            )
        order.extend([f"[v{idx}]", f"[a{idx}]"])
        idx += 1

    prev = 0.0
    for pos, content in events:
        emit_orig(prev, pos)
        if content[0] == "teaser":
            start, dur = plan[content[1]]
            emit_clip(start, dur, 1.0)
        else:  # reveal en posición fija
            emit_clip(0.0, clip_dur, params.reveal_speed)
        prev = pos
    emit_orig(prev, None)

    # Reveal al final (si no tenía posición fija).
    if params.reveal_enabled and params.reveal_at is None:
        emit_clip(0.0, clip_dur, params.reveal_speed)

    total = len(order) // 2
    filters.append("".join(order) + f"concat=n={total}:v=1:a=1[v][a]")

    return [
        config.FFMPEG_PATH, "-y", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        *ffmpeg_runner.video_encode_flags(),
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]


def build_command(original_path: str, clip_paths: list[str], output_path: str,
                  params: InsertParams, *, fps: float,
                  orig_dur: float | None, clip_durs: list[float | None],
                  orig_has_audio: bool, clip_has_audios: list[bool]) -> list[str]:
    """Construye el comando FFmpeg según el modo.

    `clip_paths` lleva un path por mini-clip (el modo progresivo usa el primero).
    """
    fps_s = f"{fps:.5f}"
    if params.is_progressive:
        if clip_durs[0] is None:
            raise ValueError("No pude leer la duración del mini-clip (¿ffprobe?).")
        return _build_progressive(
            original_path, clip_paths[0], output_path, params, fps_s=fps_s,
            orig_dur=orig_dur, clip_dur=clip_durs[0],
            orig_has_audio=orig_has_audio, clip_has_audio=clip_has_audios[0],
        )
    return _build_full(
        original_path, clip_paths, output_path, params, fps_s=fps_s,
        orig_dur=orig_dur, clip_durs=clip_durs,
        orig_has_audio=orig_has_audio, clip_has_audios=clip_has_audios,
    )


def _progressive_total(orig_dur, clip_dur, plan, params) -> float | None:
    if orig_dur is None or clip_dur is None:
        return None
    total = orig_dur + sum(d for _, d in plan)
    if params.reveal_enabled:
        total += clip_dur / params.reveal_speed
    return total


def _convert_clips_to_vertical(job_id: str, clip_paths: list[str],
                               params: InsertParams,
                               hi: int) -> tuple[list[str], list[str]]:
    """Pasa por vertical_convert los mini-clips marcados. Devuelve (paths, temporales).

    Usa build_command() (puro) en vez del process() del módulo: ese marcaría el
    job como "done" a mitad de camino y rompería el polling. El progreso se
    reparte en la banda 0..hi entre los clips a convertir.
    """
    a_convertir = [i for i, spec in enumerate(params.clips) if spec.vertical]
    if not a_convertir or not params.vertical:
        return clip_paths, []

    resultado = list(clip_paths)
    temporales: list[str] = []
    paso = hi / len(a_convertir)
    for orden, ci in enumerate(a_convertir):
        vert_path = file_utils.output_path_for(f"{job_id}_clipv{ci}")
        command = vertical_processor.build_command(
            clip_paths[ci], vert_path, params.vertical)
        ffmpeg_runner.run(
            command, job_id,
            total_duration=ffmpeg_runner.probe_duration(clip_paths[ci]),
            progress_range=(int(orden * paso), int((orden + 1) * paso)),
        )
        resultado[ci] = vert_path
        temporales.append(vert_path)
    return resultado, temporales


def process(job_id: str, original_path: str, clip_paths: list[str],
            output_path: str, params: InsertParams) -> None:
    """Ejecuta el job completo (bloqueante). Actualiza job_manager en cada paso."""
    temporales: list[str] = []
    try:
        # Los mini-clips pueden venir 16:9: se convierten antes de validar, así
        # el usuario no tiene que pasarlos a mano por el módulo Vertical.
        lo = 30 if params.needs_vertical else 0
        clip_paths, temporales = _convert_clips_to_vertical(
            job_id, clip_paths, params, lo)

        for path in clip_paths:
            _validate_resolution(original_path, path)

        fps = ffmpeg_runner.probe_fps(original_path) or 30.0
        orig_dur = ffmpeg_runner.probe_duration(original_path)
        clip_durs = [ffmpeg_runner.probe_duration(p) for p in clip_paths]
        orig_has_audio = ffmpeg_runner.has_audio_stream(original_path)
        clip_has_audios = [ffmpeg_runner.has_audio_stream(p) for p in clip_paths]

        command = build_command(
            original_path, clip_paths, output_path, params,
            fps=fps, orig_dur=orig_dur, clip_durs=clip_durs,
            orig_has_audio=orig_has_audio, clip_has_audios=clip_has_audios,
        )

        if params.is_progressive and clip_durs[0] is not None:
            plan = _slice_plan(clip_durs[0], params.total_markers,
                               params.slice_durations)
            total = _progressive_total(orig_dur, clip_durs[0], plan, params)
        elif orig_dur is not None and all(d is not None for d in clip_durs):
            # Cada clip suma su duración por cada marca que tenga.
            total = orig_dur + sum(
                clip_durs[ci] * len(spec.markers)
                for ci, spec in enumerate(params.clips)
            )
        else:
            total = None

        ffmpeg_runner.run(command, job_id, total_duration=total,
                          progress_range=(lo, 100))
        job_manager.update_job(job_id, status="done", progress=100)
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=str(exc))
    finally:
        file_utils.cleanup_paths(*temporales)


# ─────────────────── cutaway (fondo dinámico, para reel_express) ───────────────────

def build_cutaway_command(base_path: str, clip_path: str, output_path: str,
                          ranges: list[tuple[float, float]], *, fps: float,
                          base_dur: float | None) -> list[str]:
    """Reemplaza tramos del video de fondo por pedazos del mini-clip.

    `ranges` = [(pos, dur), …]: en `pos` (segundos del base) se sustituyen `dur`
    segundos del video por el pedazo que avanza del mini-clip. La duración total
    NO cambia y el audio del base (`[0:a]`, la narración/mezcla) se mapea intacto,
    así la narración corre por debajo sin cortarse.
    """
    fps_s = f"{fps:.5f}"
    ranges = sorted(ranges, key=lambda r: r[0])

    # Los pedazos del mini-clip avanzan igual que en el modo progresivo: el k-ésimo
    # arranca donde terminó el anterior.
    clip_starts: list[float] = []
    acc = 0.0
    for _, dur in ranges:
        clip_starts.append(acc)
        acc += dur

    filters: list[str] = []
    order: list[str] = []
    idx = 0

    def emit_base(a: float, b: float | None) -> None:
        nonlocal idx
        is_last = b is None
        seg_len = (base_dur - a) if (is_last and base_dur is not None) else (
            (b - a) if b is not None else None)
        if seg_len is not None and seg_len <= _EPS:
            return
        end = "" if is_last else f":end={b:.3f}"
        filters.append(
            f"[0:v]trim=start={a:.3f}{end},setpts=PTS-STARTPTS,"
            f"fps={fps_s},setsar=1,format=yuv420p[v{idx}]"
        )
        order.append(f"[v{idx}]")
        idx += 1

    def emit_clip(start: float, dur: float) -> None:
        nonlocal idx
        end = start + dur
        filters.append(
            f"[1:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
            f"fps={fps_s},setsar=1,format=yuv420p[v{idx}]"
        )
        order.append(f"[v{idx}]")
        idx += 1

    prev = 0.0
    for k, (pos, dur) in enumerate(ranges):
        emit_base(prev, pos)
        emit_clip(clip_starts[k], dur)
        prev = pos + dur
    emit_base(prev, None)

    filters.append("".join(order) + f"concat=n={len(order)}:v=1:a=0[vout]")

    return [
        config.FFMPEG_PATH, "-y", "-i", base_path, "-i", clip_path,
        "-filter_complex", ";".join(filters),
        "-map", "[vout]", "-map", "0:a?",
        *ffmpeg_runner.video_encode_flags(),
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]


def plan_cutaway_ranges(base_dur: float, clip_dur: float, positions: list[float],
                        slice_durations: list[float] | None = None) -> list[tuple[float, float]]:
    """Arma los tramos (pos, dur) del cutaway y valida que quepan sin solaparse."""
    positions = sorted(min(max(p, 0.0), base_dur) for p in positions)
    n = len(positions)
    plan = _slice_plan(clip_dur, n, slice_durations)
    ranges = [(positions[k], plan[k][1]) for k in range(n)]

    prev_end = 0.0
    for pos, dur in ranges:
        if pos < prev_end - _EPS:
            raise ValueError(
                "Los tramos de fondo dinámico se solapan; separá más los "
                "marcadores o acortá los recortes."
            )
        if pos + dur > base_dur + _EPS:
            raise ValueError(
                "Un tramo de fondo dinámico se pasa del final del video; "
                "acortá el recorte o movelo antes."
            )
        prev_end = pos + dur
    return ranges


def process_cutaway(job_id: str, base_path: str, clip_path: str, output_path: str,
                    positions: list[float], *, slice_durations: list[float] | None = None,
                    progress_range: tuple[int, int] = (0, 100)) -> None:
    """Corre el cutaway como (sub-)job. Reusable por reel_express.

    `positions` en segundos del base. Valida resolución y arma los tramos.
    """
    try:
        _validate_resolution(base_path, clip_path)
        fps = ffmpeg_runner.probe_fps(base_path) or 30.0
        base_dur = ffmpeg_runner.probe_duration(base_path)
        clip_dur = ffmpeg_runner.probe_duration(clip_path)
        if base_dur is None or clip_dur is None:
            raise ValueError("No pude leer la duración del video o del mini-clip.")

        ranges = plan_cutaway_ranges(base_dur, clip_dur, positions, slice_durations)
        command = build_cutaway_command(
            base_path, clip_path, output_path, ranges, fps=fps, base_dur=base_dur,
        )
        ffmpeg_runner.run(command, job_id, total_duration=base_dur,
                          progress_range=progress_range)
        job_manager.update_job(job_id, status="done", progress=progress_range[1])
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
