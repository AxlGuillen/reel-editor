"""Lógica FFmpeg del módulo audio_merge.

Une N audios en el orden recibido (concat filter) y luego capa las pausas: el
silencio inicial se recorta y cada pausa interna/final se limita a `max_pause`
segundos, para que la narración fluya. Cada input se normaliza (sample rate +
layout) antes de concatenar, para tolerar archivos con formatos distintos.
"""
import config
from core import ffmpeg_runner, job_manager
from modules.audio_merge.schema import AudioMergeParams

# Parámetros comunes a los que se normaliza cada input antes de concatenar.
SAMPLE_RATE = 44100
CHANNEL_LAYOUT = "stereo"

# Nivel por debajo del cual se considera silencio. La voz de TTS deja un silencio
# casi digital, así que -40 dB es holgado; subir si el TTS tiene piso de ruido.
SILENCE_THRESHOLD_DB = -40


def build_command(input_paths: list[str], output_path: str,
                  params: AudioMergeParams) -> list[str]:
    """Construye el comando FFmpeg: une los audios en orden y capa las pausas."""
    n = len(input_paths)

    cmd = [config.FFMPEG_PATH, "-y"]
    for path in input_paths:
        cmd += ["-i", path]

    # Normaliza cada input (sample rate + layout) y concatena en orden.
    filters = []
    labels = []
    for i in range(n):
        label = f"[a{i}]"
        filters.append(
            f"[{i}:a]aresample={SAMPLE_RATE},"
            f"aformat=channel_layouts={CHANNEL_LAYOUT}{label}"
        )
        labels.append(label)
    filters.append(f"{''.join(labels)}concat=n={n}:v=0:a=1[merged]")

    # Capa de pausas: recorta el silencio inicial por completo (start) y deja
    # como mucho `max_pause` de cada pausa interna/final (stop).
    thr = f"{SILENCE_THRESHOLD_DB}dB"
    filters.append(
        f"[merged]silenceremove="
        f"start_periods=1:start_threshold={thr}:"
        f"stop_periods=-1:stop_duration={params.max_pause}:stop_threshold={thr}:"
        f"detection=peak[out]"
    )

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[out]",
        "-c:a", "libmp3lame",
        "-q:a", "2",
        output_path,
    ]
    return cmd


def process(job_id: str, input_paths: list[str], output_path: str,
            params: AudioMergeParams) -> None:
    """Ejecuta el job completo (bloqueante). Actualiza job_manager en cada paso."""
    try:
        # La salida queda más corta (se recortan pausas); la suma de inputs es
        # una cota superior razonable para la barra de progreso.
        durations = [ffmpeg_runner.probe_duration(p) for p in input_paths]
        total = sum(durations) if all(d is not None for d in durations) else None

        command = build_command(input_paths, output_path, params)
        ffmpeg_runner.run(command, job_id, total_duration=total)
        job_manager.update_job(job_id, status="done", progress=100)
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=str(exc))
