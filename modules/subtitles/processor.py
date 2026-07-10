"""Lógica de transcripción y quemado de subtítulos.

Flujo en dos fases (estilo CapCut):
  1. transcribe_job → faster-whisper transcribe el audio y devuelve los
     segmentos (con palabras + timestamps). El usuario los revisa/corrige.
  2. render_job → con los segmentos (posiblemente editados) arma un .ass con
     el efecto karaoke (palabra activa coloreada) y FFmpeg lo quema.

El lienzo ASS es 1080×1920 (igual que el output vertical). Cada evento se
posiciona con {\\an2\\pos(540, position_y)}: ancla abajo-centro en el píxel
exacto, así NO salta de renglón entre grupos y calza con el preview (que usa
textBaseline=bottom). El audio se copia sin tocar.

Device: intenta cuda/float16 primero; si falla (GPU no soportada, DLLs
ausentes) cae a cpu/int8 automáticamente — incluso si el fallo aparece recién
al inferir (los errores de DLL de CUDA en Windows son lazy).
"""
import glob
import os
import sys
import threading
import uuid

import config
from core import ffmpeg_runner, job_manager
from modules.subtitles.schema import SubtitlesParams

# Resolución del lienzo ASS (debe coincidir con el output vertical).
ASS_W = config.OUTPUT_WIDTH   # 1080
ASS_H = config.OUTPUT_HEIGHT  # 1920

COLOR_BASE   = "&H00FFFFFF&"   # blanco
COLOR_BORDER = "&H00000000&"   # contorno negro


# ---------------------------------------------------------------------------
# Transcripción
# ---------------------------------------------------------------------------

def _register_gpu_dlls() -> None:
    """Windows: expone las DLLs de CUDA (cuBLAS/cuDNN, wheels pip de nvidia-*)
    en el PATH para que ctranslate2 las encuentre. Sin esto, el modelo carga en
    GPU pero la inferencia falla con "cublas64_12.dll is not found"."""
    if os.name != "nt":
        return
    pattern = os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")
    dll_dirs = [os.path.abspath(d) for d in glob.glob(pattern) if os.path.isdir(d)]
    if dll_dirs:
        os.environ["PATH"] = os.pathsep.join(dll_dirs) + os.pathsep + os.environ["PATH"]


_register_gpu_dlls()

# Cache de modelos cargados: (nombre, device, compute) -> WhisperModel.
# Cargar turbo tarda ~2.5s en GPU (bastante más en CPU); sin cache se pagaba
# en CADA transcripción. ctranslate2 soporta uso concurrente del mismo modelo.
_MODELS: dict = {}
_MODELS_LOCK = threading.Lock()


def _get_device() -> tuple[str, str]:
    """Detecta el mejor device disponible para faster-whisper."""
    try:
        import ctranslate2
        if "cuda" in ctranslate2.get_supported_compute_types("cuda"):
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def _get_model(name: str, device: str, compute: str):
    """Devuelve el modelo cacheado, o lo carga (una sola vez por combinación)."""
    from faster_whisper import WhisperModel

    key = (name, device, compute)
    with _MODELS_LOCK:
        if key not in _MODELS:
            _MODELS[key] = WhisperModel(name, device=device, compute_type=compute)
        return _MODELS[key]


def transcribe(video_path: str, params: SubtitlesParams,
               job_id: str | None = None) -> list[dict]:
    """Transcribe el audio y devuelve segmentos con palabras y timestamps.

    Cada segmento: {"id", "start", "end", "text", "words":[{word,start,end}]}.
    Lanza RuntimeError si no se detecta voz.
    """
    device, compute = _get_device()
    try:
        model = _get_model(params.model, device, compute)
    except Exception:
        print(f"[subtitles] {device}/{compute} falló al cargar, usando cpu/int8")
        device, compute = "cpu", "int8"
        model = _get_model(params.model, device, compute)

    try:
        segments = _run_transcription(model, video_path, params, job_id)
    except RuntimeError:
        if device != "cuda":
            raise
        # Los errores de CUDA (DLLs, VRAM) aparecen recién al inferir: el job
        # no debe morir por eso. Reintento único en CPU.
        print("[subtitles] la inferencia en cuda falló, reintentando en cpu/int8")
        model = _get_model(params.model, "cpu", "int8")
        segments = _run_transcription(model, video_path, params, job_id)

    if not segments:
        raise RuntimeError(
            "No se detectó voz en el video. "
            "Revisá que el video tenga audio con narración."
        )
    return segments


def _run_transcription(model, video_path: str, params: SubtitlesParams,
                       job_id: str | None) -> list[dict]:
    """Corre la transcripción completa y arma los segmentos (con progreso)."""
    duration = ffmpeg_runner.probe_duration(video_path) or 0
    lang = None if params.language == "auto" else params.language
    seg_gen, _ = model.transcribe(
        video_path,
        language=lang,
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
    )

    segments: list[dict] = []
    for seg in seg_gen:
        words = [
            {"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)}
            for w in (seg.words or []) if w.word.strip()
        ]
        if not words:
            continue
        segments.append({
            "id": len(segments),
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": words,
        })
        if job_id and duration:
            job_manager.set_progress(job_id, min(95, int(seg.end / duration * 100)))
    return segments


def transcribe_job(job_id: str, video_path: str, params: SubtitlesParams) -> None:
    """Job de transcripción (fase 1). Guarda los segmentos en el job."""
    try:
        job_manager.update_job(job_id, status="processing", progress=2,
                               stage="Transcribiendo…")
        segments = transcribe(video_path, params, job_id=job_id)
        job_manager.update_job(job_id, status="done", progress=100, stage=None,
                               segments=segments)
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))


# ---------------------------------------------------------------------------
# Reconstrucción de palabras desde los segmentos (posiblemente editados)
# ---------------------------------------------------------------------------

def _redistribute(text: str, start: float, end: float) -> list[dict]:
    """Reparte el span [start, end] entre las palabras del texto, proporcional
    a la longitud de cada una. Se usa cuando el usuario editó el texto y los
    timestamps por palabra originales ya no aplican."""
    parts = text.split()
    if not parts:
        return []
    total_chars = sum(len(w) for w in parts) or len(parts)
    span = max(0.05, end - start)
    out = []
    t = start
    for w in parts:
        dur = span * (len(w) / total_chars)
        out.append({"word": w, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur
    return out


def words_from_segments(segments_data: list[dict]) -> list[list[dict]]:
    """Convierte los segmentos (del frontend) en listas de palabras por segmento.

    Si el segmento trae `words` (no fue editado), se respetan sus timestamps
    originales. Si no los trae (fue editado), se redistribuye el texto sobre
    el span del segmento.
    """
    result: list[list[dict]] = []
    for seg in segments_data:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        words = seg.get("words")
        start = float(seg.get("start", 0) or 0)
        end = float(seg.get("end", start) or start)
        if words:
            seg_words = [
                {"word": (w.get("word") or "").strip(),
                 "start": float(w["start"]), "end": float(w["end"])}
                for w in words if (w.get("word") or "").strip()
            ]
        else:
            seg_words = _redistribute(text, start, end)
        if seg_words:
            result.append(seg_words)
    return result


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
    if cs == 100:  # redondeo hacia arriba
        cs = 0
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc_text(s: str) -> str:
    """Escapa caracteres que romperían el parser de overrides ASS."""
    return s.replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(segments: list[list[dict]], params: SubtitlesParams,
              font_name: str = "Poppins-Bold") -> str:
    """Genera el contenido del .ass (puro, sin I/O).

    `segments` es una lista de listas de palabras (una por segmento). Dentro de
    cada segmento se chunkea por words_per_line; cada chunk muestra sus palabras
    juntas con la activa coloreada (un evento Dialogue por palabra).
    """
    fs = params.font_size
    border = max(2, round(fs * 0.07))

    header = f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: {ASS_W}
PlayResY: {ASS_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font_name},{fs},{COLOR_BASE},&H000000FF&,{COLOR_BORDER},&H80000000&,-1,0,0,0,100,100,0,0,1,{border},0,2,40,40,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    n = params.words_per_line
    pos_x = ASS_W // 2
    pos_y = params.position_y
    pos_tag = f"{{\\an2\\pos({pos_x},{pos_y})}}"

    # Aplanamos todos los grupos (chunks de n palabras) en una sola lista, así
    # cada grupo sabe cuándo arranca el siguiente y podemos evitar que su última
    # palabra invada ese inicio (la superposición entre grupos).
    groups: list[list[dict]] = []
    for seg_words in segments:
        for gi in range(0, len(seg_words), n):
            groups.append(seg_words[gi:gi + n])

    events = []
    for g_idx, group in enumerate(groups):
        # Inicio del próximo grupo (None si es el último): tope duro para que
        # nada de este grupo siga vivo cuando el siguiente ya empezó.
        next_start = groups[g_idx + 1][0]["start"] if g_idx + 1 < len(groups) else None

        for idx, word in enumerate(group):
            t_start = word["start"]
            if idx + 1 < len(group):
                t_end = group[idx + 1]["start"]
            else:
                # Última palabra del grupo: dura hasta su fin (+ un respiro), pero
                # sin pisar el arranque del siguiente grupo. Si hay silencio real,
                # limpia pantalla; si el siguiente arranca pegado, calza exacto.
                t_end = word["end"] + 0.15
                if next_start is not None:
                    t_end = min(t_end, next_start)
            if t_end <= t_start:
                t_end = t_start + 0.1

            parts = []
            for j, w in enumerate(group):
                wt = _esc_text(w["word"])
                if j == idx:
                    parts.append(f"{{\\c{params.highlight_color}}}{wt}{{\\c{COLOR_BASE}}}")
                else:
                    parts.append(wt)
            text = " ".join(parts)

            events.append(
                f"Dialogue: 0,{_seconds_to_ass(t_start)},{_seconds_to_ass(t_end)},"
                f"Sub,,0,0,0,,{pos_tag}{text}"
            )

    return header + "\n".join(events) + "\n"


def _write_ass(content: str) -> str:
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    path = os.path.join(config.UPLOAD_FOLDER, f"subs_{uuid.uuid4().hex[:12]}.ass")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _esc_filter(path: str) -> str:
    """Ruta relativa a BASE_DIR escapada para el parser de filtros (Windows)."""
    rel = os.path.relpath(path, config.BASE_DIR).replace("\\", "/")
    return rel.replace(":", "\\:").replace("'", "\\'")


def build_command(video_path: str, ass_path: str, output_path: str) -> list[str]:
    """Comando FFmpeg para quemar el .ass sobre el video (audio sin tocar)."""
    fonts_dir = os.path.relpath(config.FONTS_FOLDER, config.BASE_DIR).replace("\\", "/")
    ass_filter = f"ass={_esc_filter(ass_path)}:fontsdir={fonts_dir}"
    return [
        config.FFMPEG_PATH,
        "-y",
        "-i", video_path,
        "-vf", ass_filter,
        *ffmpeg_runner.video_encode_flags(),
        "-c:a", "copy",
        output_path,
    ]


def render_job(job_id: str, video_path: str, segments_data: list[dict],
               params: SubtitlesParams, output_path: str) -> None:
    """Job de quemado (fase 2). Arma el .ass desde los segmentos y lo quema."""
    ass_path = None
    try:
        job_manager.update_job(job_id, status="processing", progress=5,
                               stage="Generando subtítulos…")
        seg_words = words_from_segments(segments_data)
        if not seg_words:
            raise RuntimeError("No hay texto en los subtítulos para generar.")

        font_name = "Poppins-Bold"
        font_path = config.resolve_font_path()
        if font_path:
            font_name = os.path.splitext(os.path.basename(font_path))[0]

        ass_content = build_ass(seg_words, params, font_name=font_name)
        ass_path = _write_ass(ass_content)
        job_manager.update_job(job_id, progress=10, stage="Quemando subtítulos…")

        video_dur = ffmpeg_runner.probe_duration(video_path)
        command = build_command(video_path, ass_path, output_path)
        ffmpeg_runner.run(
            command, job_id,
            total_duration=video_dur,
            cwd=config.BASE_DIR,
            progress_range=(10, 100),
        )
        job_manager.update_job(job_id, status="done", progress=100, stage=None,
                               output_path=output_path)
    except Exception as exc:  # noqa: BLE001
        job_manager.update_job(job_id, status="error", error=str(exc))
    finally:
        if ass_path and os.path.exists(ass_path):
            try:
                os.remove(ass_path)
            except OSError:
                pass
