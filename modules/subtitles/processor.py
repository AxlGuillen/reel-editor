"""Lógica de transcripción y quemado de subtítulos.

Pipeline:
  1. faster-whisper transcribe el audio del video con timestamps por palabra.
  2. build_ass() arma un .ass con el efecto karaoke (palabra activa coloreada).
  3. FFmpeg quema el .ass sobre el video con el filtro ass= y fontsdir.

El lienzo ASS es 1080×1920 (igual que el output vertical), así position_y
mapea 1:1 al píxel real. El audio se copia sin tocar.

Device: intenta cuda/float16 primero; si falla (GPU no soportada, DLLs
ausentes, etc.) cae a cpu/int8 automáticamente. El fallback es silencioso
para el usuario y queda logueado en stderr del servidor.
"""
import os
import uuid

import config
from core import ffmpeg_runner, job_manager
from modules.subtitles.schema import SubtitlesParams

# Resolución del lienzo ASS (debe coincidir con el output vertical).
ASS_W = config.OUTPUT_WIDTH   # 1080
ASS_H = config.OUTPUT_HEIGHT  # 1920

# Color del texto base (blanco, contorno negro).
COLOR_BASE    = "&H00FFFFFF&"
COLOR_BORDER  = "&H00000000&"


# ---------------------------------------------------------------------------
# Transcripción
# ---------------------------------------------------------------------------

def _get_device() -> tuple[str, str]:
    """Detecta el mejor device disponible para faster-whisper."""
    try:
        import ctranslate2
        if "cuda" in ctranslate2.get_supported_compute_types("cuda"):
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def transcribe(video_path: str, params: SubtitlesParams) -> list[dict]:
    """Transcribe el audio del video y devuelve lista de palabras con timestamps.

    Cada elemento: {"word": str, "start": float, "end": float}.
    Lanza RuntimeError si no se detectan palabras.
    """
    from faster_whisper import WhisperModel

    device, compute = _get_device()
    try:
        model = WhisperModel(
            params.model,
            device=device,
            compute_type=compute,
        )
    except Exception:
        # Fallback a CPU si el device preferido falla en el momento de cargar.
        print(f"[subtitles] {device}/{compute} falló al cargar, usando cpu/int8")
        model = WhisperModel(params.model, device="cpu", compute_type="int8")

    lang = None if params.language == "auto" else params.language
    segments, _ = model.transcribe(
        video_path,
        language=lang,
        word_timestamps=True,
        vad_filter=True,       # filtra silencios, mejora timestamps
        beam_size=5,
    )

    words = []
    for seg in segments:
        for w in (seg.words or []):
            text = w.word.strip()
            if text:
                words.append({"word": text, "start": w.start, "end": w.end})

    if not words:
        raise RuntimeError(
            "No se detectó voz en el video. "
            "Revisá que el video tenga audio con narración."
        )
    return words


# ---------------------------------------------------------------------------
# Generación del ASS
# ---------------------------------------------------------------------------

def _seconds_to_ass(t: float) -> str:
    """Convierte segundos a formato de tiempo ASS: H:MM:SS.cc"""
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass(words: list[dict], params: SubtitlesParams,
              font_name: str = "Poppins-Bold") -> str:
    """Genera el contenido completo del archivo .ass (puro, sin I/O).

    Efecto: un evento por palabra. En cada evento, el grupo de N palabras
    se muestra en blanco; solo la palabra activa lleva el color de resalte.
    Al pasar a la siguiente palabra, el evento anterior termina y empieza
    el siguiente con la nueva palabra activa.
    """
    fs = params.font_size
    # Margen horizontal: centrado con wrap automático de libass.
    margin_lr = 60
    # position_y es la coordenada del borde INFERIOR del bloque de texto.
    pos_y = params.position_y

    header = f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: {ASS_W}
PlayResY: {ASS_H}
WrapStyle: 1
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font_name},{fs},{COLOR_BASE},&H000000FF&,{COLOR_BORDER},&H80000000&,-1,0,0,0,100,100,0,0,1,3,0,2,{margin_lr},{margin_lr},{pos_y},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    n = params.words_per_line
    events = []

    for i, word in enumerate(words):
        # Grupo: las N palabras centradas en la palabra actual.
        group_start = max(0, i - (i % n))
        group_end   = min(len(words), group_start + n)
        group       = words[group_start:group_end]

        # Tiempo del evento: duración de esta palabra.
        t_start = _seconds_to_ass(word["start"])
        # El evento dura hasta que empiece la siguiente palabra del mismo grupo,
        # o hasta el fin de la palabra actual si es la última del grupo.
        if i + 1 < group_end:
            t_end = _seconds_to_ass(words[i + 1]["start"])
        else:
            t_end = _seconds_to_ass(word["end"] + 0.05)  # pequeño buffer

        # Construir el texto del grupo: palabra activa en highlight_color,
        # el resto en COLOR_BASE (blanco). Sin saltos de línea — libass wraps.
        parts = []
        for j, w in enumerate(group):
            if words.index(w) == i:
                # Palabra activa: cambiamos color y volvemos al base.
                parts.append(
                    f"{{\\c{params.highlight_color}\\b1}}{w['word']}"
                    f"{{\\c{COLOR_BASE}\\b-1}}"
                )
            else:
                parts.append(w["word"])
        text = " ".join(parts)

        events.append(
            f"Dialogue: 0,{t_start},{t_end},Sub,,0,0,0,,{text}"
        )

    return header + "\n".join(events) + "\n"


def _write_ass(content: str) -> str:
    """Escribe el .ass en uploads/ y devuelve el path."""
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    path = os.path.join(config.UPLOAD_FOLDER, f"subs_{uuid.uuid4().hex[:12]}.ass")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _esc_filter(path: str) -> str:
    """Ruta relativa a BASE_DIR, con / y escapada para el parser de filtros ASS.

    El colon del drive en Windows rompe el parser; usamos rutas relativas
    al cwd=BASE_DIR igual que en el módulo watermark.
    """
    rel = os.path.relpath(path, config.BASE_DIR).replace("\\", "/")
    # En el filtro ass= los caracteres especiales van escapados con \\.
    return rel.replace(":", "\\:").replace("'", "\\'")


def build_command(video_path: str, ass_path: str, output_path: str) -> list[str]:
    """Construye el comando FFmpeg para quemar el .ass sobre el video.

    - fontsdir apunta a assets/fonts/ para que libass encuentre Poppins-Bold.
    - El audio se copia sin tocar (-c:a copy).
    - El video se re-encodea con libx264 (necesario para quemar los subs).
    """
    fonts_dir = os.path.relpath(config.FONTS_FOLDER, config.BASE_DIR).replace("\\", "/")
    ass_filter = f"ass={_esc_filter(ass_path)}:fontsdir={fonts_dir}"

    return [
        config.FFMPEG_PATH,
        "-y",
        "-i", video_path,
        "-vf", ass_filter,
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "copy",
        output_path,
    ]


# ---------------------------------------------------------------------------
# Orquestación del job
# ---------------------------------------------------------------------------

def process(job_id: str, video_path: str, output_path: str,
            params: SubtitlesParams) -> None:
    """Ejecuta el job completo (bloqueante). Actualiza job_manager en cada paso.

    Progreso:
      0–50%  → transcripción (faster-whisper, no tiene progreso interno)
      50–100% → quemado FFmpeg (parseado de time=)
    """
    ass_path = None
    try:
        # --- Fase 1: transcribir (0→50%) ---
        job_manager.update_job(job_id, status="processing", progress=5,
                               stage="Transcribiendo…")
        words = transcribe(video_path, params)
        job_manager.update_job(job_id, progress=50, stage="Generando subtítulos…")

        # --- Fase 2: generar .ass ---
        font_name = "Poppins-Bold"
        font_path = config.resolve_font_path()
        if font_path:
            # Usar el nombre lógico de la fuente (sin extensión).
            font_name = os.path.splitext(os.path.basename(font_path))[0]
        ass_content = build_ass(words, params, font_name=font_name)
        ass_path = _write_ass(ass_content)
        job_manager.update_job(job_id, progress=52, stage="Quemando subtítulos…")

        # --- Fase 3: quemar con FFmpeg (50→100%) ---
        video_dur = ffmpeg_runner.probe_duration(video_path)
        command = build_command(video_path, ass_path, output_path)
        ffmpeg_runner.run(
            command, job_id,
            total_duration=video_dur,
            cwd=config.BASE_DIR,
            progress_range=(52, 100),
        )

        job_manager.update_job(job_id, status="done", progress=100, stage=None)

    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
    finally:
        if ass_path and os.path.exists(ass_path):
            try:
                os.remove(ass_path)
            except OSError:
                pass
