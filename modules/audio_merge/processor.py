"""Lógica FFmpeg del módulo audio_merge.

Une N audios en el orden recibido usando el concat filter. Cada input se
normaliza (sample rate + layout) antes de concatenar, para tolerar archivos con
formatos distintos. Opcionalmente inserta un silencio entre clips.
"""
import config
from core import ffmpeg_runner, job_manager
from modules.audio_merge.schema import AudioMergeParams

# Parámetros comunes a los que se normaliza cada input antes de concatenar.
SAMPLE_RATE = 44100
CHANNEL_LAYOUT = "stereo"


def build_command(input_paths: list[str], output_path: str,
                  params: AudioMergeParams) -> list[str]:
    """Construye el comando FFmpeg completo para unir los audios en orden."""
    n = len(input_paths)
    gap = params.gap

    cmd = [config.FFMPEG_PATH, "-y"]
    for path in input_paths:
        cmd += ["-i", path]

    # Normaliza cada input y, salvo el último, le agrega el silencio (gap).
    filters = []
    labels = []
    for i in range(n):
        chain = f"[{i}:a]aresample={SAMPLE_RATE},aformat=channel_layouts={CHANNEL_LAYOUT}"
        if gap > 0 and i < n - 1:
            chain += f",apad=pad_dur={gap}"
        label = f"[a{i}]"
        filters.append(f"{chain}{label}")
        labels.append(label)

    filters.append(f"{''.join(labels)}concat=n={n}:v=0:a=1[out]")
    filter_complex = ";".join(filters)

    cmd += [
        "-filter_complex", filter_complex,
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
        # Duración total ≈ suma de los inputs + los silencios entre clips.
        durations = [ffmpeg_runner.probe_duration(p) for p in input_paths]
        total = None
        if all(d is not None for d in durations):
            total = sum(durations) + params.gap * max(0, len(input_paths) - 1)

        command = build_command(input_paths, output_path, params)
        ffmpeg_runner.run(command, job_id, total_duration=total)
        job_manager.update_job(job_id, status="done", progress=100)
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=str(exc))
