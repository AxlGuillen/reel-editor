"""Lógica FFmpeg del módulo sound_drop.

Mezcla la pista de un audio externo sobre un video vertical. Soporta:
- Volumen independiente del audio original y del nuevo.
- Fade in/out del audio nuevo.
- Speed match: acelera/ralentiza el video para igualar la duración del audio,
  manteniendo el audio original sincronizado vía atempo.
"""
import config
from core import ffmpeg_runner, job_manager
from modules.sound_drop.schema import SoundDropParams, FADE_DURATION


def build_command(video_path: str, audio_path: str, output_path: str,
                  params: SoundDropParams, *, video_dur: float | None,
                  audio_dur: float | None, has_audio: bool) -> list[str]:
    """Construye el comando FFmpeg completo.

    video_dur / audio_dur pueden ser None si ffprobe falló; en ese caso se
    desactiva el speed match y se recorta la salida al stream más corto.
    """
    Vo = params.original_gain
    Vn = params.new_gain

    speed_match = bool(params.speed_match and video_dur and audio_dur)
    # Duración objetivo de la salida.
    target = audio_dur if speed_match else video_dur

    filters: list[str] = []

    # --- Video ---
    if speed_match:
        k = audio_dur / video_dur  # estira (k>1) o comprime (k<1) el video
        filters.append(f"[0:v]setpts={k:.6f}*PTS[v]")
        vmap = "[v]"
    else:
        vmap = "0:v"

    # --- Audio nuevo (input 1) ---
    new_chain = f"[1:a]volume={Vn:.3f}"
    if params.fade:
        new_chain += f",afade=t=in:d={FADE_DURATION}"
        if target:
            st = max(0.0, target - FADE_DURATION)
            new_chain += f",afade=t=out:st={st:.3f}:d={FADE_DURATION}"
    # Sin speed match, ajustamos el audio nuevo exactamente al largo del video.
    if not speed_match and target:
        new_chain += f",apad,atrim=0:{target:.3f}"
    new_chain += "[a_new]"
    filters.append(new_chain)
    audio_labels = ["[a_new]"]

    # --- Audio original (input 0), solo si existe y no está muteado ---
    if has_audio and params.original_volume > 0:
        orig_chain = "[0:a]"
        if speed_match:
            tempo = video_dur / audio_dur  # mantiene el original sincronizado
            orig_chain += f"atempo={tempo:.6f},"
        orig_chain += f"volume={Vo:.3f}[a_orig]"
        filters.append(orig_chain)
        audio_labels.append("[a_orig]")

    # --- Mezcla ---
    if len(audio_labels) == 2:
        filters.append(
            f"{audio_labels[0]}{audio_labels[1]}"
            f"amix=inputs=2:normalize=0:duration=longest[a]"
        )
        amap = "[a]"
    else:
        amap = audio_labels[0]

    cmd = [
        config.FFMPEG_PATH,
        "-y",
        "-i", video_path,
        "-i", audio_path,
        "-filter_complex", ";".join(filters),
        "-map", vmap,
        "-map", amap,
    ]
    if speed_match:
        cmd += ffmpeg_runner.video_encode_flags()
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k"]
    if target is None:
        cmd += ["-shortest"]
    cmd += [output_path]
    return cmd


def process(job_id: str, video_path: str, audio_path: str, output_path: str,
            params: SoundDropParams) -> None:
    """Ejecuta el job completo (bloqueante). Actualiza job_manager en cada paso."""
    try:
        video_dur = ffmpeg_runner.probe_duration(video_path)
        audio_dur = ffmpeg_runner.probe_duration(audio_path)
        has_audio = ffmpeg_runner.has_audio_stream(video_path)

        command = build_command(
            video_path, audio_path, output_path, params,
            video_dur=video_dur, audio_dur=audio_dur, has_audio=has_audio,
        )
        # La salida dura lo del audio (speed match) o lo del video.
        total = audio_dur if (params.speed_match and video_dur and audio_dur) else video_dur
        ffmpeg_runner.run(command, job_id, total_duration=total)
        job_manager.update_job(job_id, status="done", progress=100)
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=str(exc))
