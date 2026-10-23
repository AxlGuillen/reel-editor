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
import re
import sys
import threading
import uuid

import config
from core import ffmpeg_runner, job_manager
from modules.subtitles import fonts
from modules.subtitles.schema import ROTATE_PALETTE, SubtitlesParams, _css_to_ass

# Resolución del lienzo ASS (debe coincidir con el output vertical).
ASS_W = config.OUTPUT_WIDTH   # 1080
ASS_H = config.OUTPUT_HEIGHT  # 1920

WHITE = "&H00FFFFFF&"
BLACK = "&H00000000&"
# Texto y contorno van siempre en contraste: contorno negro → texto blanco,
# contorno blanco → texto negro (blanco sobre blanco no se leería).
_TEXT_FOR_OUTLINE = {"black": WHITE, "white": BLACK}
_OUTLINE_ASS = {"black": BLACK, "white": WHITE}
_ROTATE_ASS = tuple(_css_to_ass(c) for c in ROTATE_PALETTE)

# Ancho útil de una línea: el cuadro menos los márgenes laterales del Style.
SIDE_MARGIN = 40
MAX_LINE_W = ASS_W - 2 * SIDE_MARGIN   # 1000 px
MIN_FIT = 0.3                          # nunca achicar más que esto


def _fit_group(font: dict, words: list[str], em_px: int, outline: int) -> tuple[float, float]:
    """(escala, espacio extra en unidades ASS) para un grupo de palabras.

    Dos problemas que aparecieron al usar la letra en su tamaño real:
    - Grupos largos en mayúsculas no entran en 1080 px (WrapStyle 2 no corta
      renglones) y se salían del cuadro por los dos lados → se achica SOLO ese
      grupo con \\fscx/\\fscy.
    - Con contornos gruesos, el contorno de cada palabra tapa el espacio y
      "Y AÚN ASÍ" se lee como una sola mancha → entre palabras se agrega el
      doble del contorno, así el hueco visible entre contornos es el espacio
      natural de la fuente.
    Medido en libass: \\fscx escala las letras y el \\fsp, pero NO el contorno.
    Por eso el extra se divide por la escala y el contorno queda fuera de ella.
    """
    gaps = len(words) - 1
    letters = sum(fonts.text_width(font, w, em_px) for w in words)
    spaces = gaps * fonts.text_width(font, " ", em_px)
    fixed = 2 * outline * (gaps + 1)   # contorno de los bordes + extra entre palabras
    natural = letters + spaces + fixed
    if natural <= MAX_LINE_W or letters + spaces <= 0:
        fit = 1.0
    else:
        fit = max(MIN_FIT, (MAX_LINE_W - fixed) / (letters + spaces))
    return fit, (2 * outline / fit if outline else 0.0)


# --- Disposición y tamaños por palabra --------------------------------------
# Para que la frase no sea siempre una línea recta: en escalera (cada palabra
# un renglón más abajo y más a la derecha), con palabras de distinto tamaño, y
# las grandes en color vivo. Todo determinista (la misma transcripción da el
# mismo video) y espejado en el preview (SubStyle.drawSample en shared.js).
GROW_MIN, GROW_MAX = 0.85, 1.4   # "crecientes": hasta la última palabra con contenido
EMPHASIS = 1.45                  # "variados": la palabra destacada
STAIR_DIAG = 0.8                 # cada renglón corre su CENTRO 0.8 em a la derecha
STAIR_GAP = 0.12                 # aire entre la tinta de dos renglones, en em
MIXED_STAIRS = 2                 # "variada": ~2 de cada 5 frases van en escalera
EMPH_POP = 1.12                  # la destacada "late" al decirse (ya está en color)

# Neón: mientras se dice, la palabra brilla con su color. Es una capa DEBAJO del
# texto: la misma palabra en la misma posición, sin relleno, con un contorno
# grueso del color de la palabra y difuminado. Medido en libass: el contorno
# gigante de una palabra no corre la alineación de la línea (0 px), así que
# sirve igual en el karaoke en línea. (abre, difumina) en em de la palabra.
NEON = {"soft": (0.16, 0.22, "&H60&"), "strong": (0.28, 0.31, "&H30&")}
NEON_FADE = r"\fad(80,60)"       # el brillo "sale" al empezar la palabra


def _neon_tags(params: SubtitlesParams, color: str, em_px: float) -> str:
    """Override de la capa de brillo (sin relleno, contorno de color difuso).

    \\bord y \\blur no los escala \\fscx: se calculan con el em real de la palabra.
    """
    spread, blur, alpha = NEON[params.neon]
    bord = params.outline_size + spread * em_px
    return f"\\1a&HFF&\\3c{color}\\3a{alpha}\\bord{bord:.1f}\\blur{blur * em_px:.1f}"


def _pick(n: int, salt: int) -> int:
    """Entero pseudoaleatorio pero fijo para la palabra n (hash de Knuth).

    El preview hace la misma cuenta en JS: con n de hasta ~10^5 el producto
    entra en los 53 bits exactos de un double, así que da lo mismo.
    """
    return ((n + 1) * 2654435761 + salt) % 4294967296


def _content_len(word: str) -> int:
    """Letras y números de la palabra (la puntuación no cuenta)."""
    return sum(ch.isalnum() for ch in word)


def _group_style(params: SubtitlesParams, words: list[str], first: int):
    """(disposición, escalas, palabras destacadas) de una frase.

    `first` es el índice global de su primera palabra: decide qué frases van
    en escalera ("variada") y qué palabra se destaca ("variados"). Nunca se
    destaca una palabra de menos de 3 letras ("de", "en", "y" no lucen
    grandes) salvo que la frase no tenga otra.
    """
    n = len(words)
    layout = params.layout
    if layout == "mixed":
        layout = "stairs" if _pick(first, 0) % 5 < MIXED_STAIRS else "line"
    if n < 2:
        return "line", [1.0] * n, set()
    content = [i for i, w in enumerate(words) if _content_len(w) >= 3]
    if params.sizes == "grow":
        # Crescendo hasta la ÚLTIMA palabra con contenido; lo que venga
        # después ("…olvidarte DE") vuelve al tamaño normal.
        e = content[-1] if content else n - 1
        scales = [GROW_MIN + (GROW_MAX - GROW_MIN) * i / e if i <= e and e else 1.0
                  for i in range(n)]
        if e == 0:
            scales[0] = GROW_MAX
        return layout, scales, {e}
    if params.sizes == "random":
        cands = content or list(range(n))
        e = cands[_pick(first, 7919) % len(cands)]
        return layout, [EMPHASIS if i == e else 1.0 for i in range(n)], {e}
    return layout, [1.0] * n, set()


def _place_words(font: dict, words: list[str], scales: list[float], em: int,
                 outline: int, layout: str, pos_y: int) -> list[tuple[float, float, float]]:
    """(x, y, escala) de cada palabra para \\an1\\pos(x,y) (abajo a la izquierda).

    Medido en libass: con \\an1 el origen horizontal es x y el borde inferior
    es la línea base + winDescent·em·escala; \\fscx/\\fscy escalan letras y
    descendente pero NO el contorno. `position_y` sigue siendo el borde
    inferior del texto, y si no entra en el ancho útil se achica todo junto.
    """
    desc = font["descent"]
    widths = [fonts.text_width(font, w, em * s) for w, s in zip(words, scales)]
    n = len(words)
    if layout == "line":
        # Una línea con las palabras sobre una MISMA línea base: la palabra con
        # el descendente más bajo apoya en position_y.
        space = fonts.text_width(font, " ", em)
        body = sum(widths) + (n - 1) * space
        fixed = 2 * outline * n       # contornos de los bordes + aire extra entre palabras
        fit = 1.0 if body + fixed <= MAX_LINE_W else max(MIN_FIT, (MAX_LINE_W - fixed) / body)
        baseline = pos_y - desc * em * max(scales) * fit
        x = ASS_W / 2 - (body * fit + fixed) / 2 + outline
        placed = []
        for w, s in zip(widths, scales):
            placed.append((x, baseline + desc * em * s * fit, s * fit))
            x += (w + space) * fit + 2 * outline
        return placed

    # Escalera: un renglón por palabra; cada uno corre su CENTRO hacia la
    # derecha (corriendo el borde izquierdo, una primera palabra larga se
    # "comía" el escalón y se leía como una lista). La última apoya en
    # position_y y la escalera crece hacia arriba.
    offs = [(i - (n - 1) / 2) * STAIR_DIAG * em for i in range(n)]
    half = MAX_LINE_W / 2 - outline
    fit = 1.0
    for off, w in zip(offs, widths):
        need = abs(off) + w / 2
        if need > half:
            fit = min(fit, half / need)
    fit = max(MIN_FIT, fit)
    # Entre renglones va lo que sube la tinta de abajo (acentos, puntos) más lo
    # que baja la de arriba (g, y, p, comas), cada una con SU escala: un
    # espaciado fijo hacía que se tocaran, y en mayúsculas sin acentos sobraba.
    ink = [fonts.ink_extent(font, w) for w in words]
    placed = [None] * n
    baseline = pos_y - desc * em * scales[-1] * fit
    for i in range(n - 1, -1, -1):
        if i < n - 1:
            rise = ink[i + 1][0] * scales[i + 1] + ink[i][1] * scales[i] + STAIR_GAP
            baseline -= rise * em * fit + 2 * outline
        x = ASS_W / 2 + offs[i] * fit - widths[i] * fit / 2
        placed[i] = (x, baseline + desc * em * scales[i] * fit, scales[i] * fit)
    return placed


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
    """Detecta el mejor device disponible para faster-whisper.

    OJO: get_supported_compute_types("cuda") devuelve los TIPOS soportados
    ({"float16", "int8", …}), nunca la string "cuda". Preguntar por "cuda" ahí
    daba siempre falso y la transcripción caía a CPU en silencio; la GPU se
    detecta con get_cuda_device_count().
    """
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            tipos = ctranslate2.get_supported_compute_types("cuda")
            for compute in ("float16", "int8_float16", "bfloat16", "float32"):
                if compute in tipos:
                    return "cuda", compute
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
               job_id: str | None = None, initial_prompt: str | None = None,
               hotwords: str | None = None) -> list[dict]:
    """Transcribe el audio y devuelve segmentos con palabras y timestamps.

    Cada segmento: {"id", "start", "end", "text", "words":[{word,start,end}]}.
    Lanza RuntimeError si no se detecta voz.
    `initial_prompt` / `hotwords`: faster-whisper's, to get names right
    (competitivo passes the teams, players and champions of the game).
    """
    hints = {k: v for k, v in (("initial_prompt", initial_prompt), ("hotwords", hotwords)) if v}
    device, compute = _get_device()
    try:
        model = _get_model(params.model, device, compute)
    except Exception:
        print(f"[subtitles] {device}/{compute} falló al cargar, usando cpu/int8")
        device, compute = "cpu", "int8"
        model = _get_model(params.model, device, compute)

    try:
        segments = _run_transcription(model, video_path, params, job_id, **hints)
    except RuntimeError:
        if device != "cuda":
            raise
        # Los errores de CUDA (DLLs, VRAM) aparecen recién al inferir: el job
        # no debe morir por eso. Reintento único en CPU.
        print("[subtitles] la inferencia en cuda falló, reintentando en cpu/int8")
        model = _get_model(params.model, "cpu", "int8")
        segments = _run_transcription(model, video_path, params, job_id, **hints)

    if not segments:
        raise RuntimeError(
            "No se detectó voz en el video. "
            "Revisá que el video tenga audio con narración."
        )
    return segments


def _run_transcription(model, video_path: str, params: SubtitlesParams,
                       job_id: str | None, **hints) -> list[dict]:
    """Corre la transcripción completa y arma los segmentos (con progreso)."""
    duration = ffmpeg_runner.probe_duration(video_path) or 0
    lang = None if params.language == "auto" else params.language
    seg_gen, _ = model.transcribe(
        video_path,
        language=lang,
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
        **hints,
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


def _active_color(params: SubtitlesParams, word_index: int) -> str:
    """Color de la palabra que está sonando. En "rotate", cada palabra de la
    transcripción (índice global, corrido entre grupos) toma el siguiente color
    de la paleta; en "single", el color de resalte elegido."""
    if params.color_mode == "rotate":
        return _ROTATE_ASS[word_index % len(_ROTATE_ASS)]
    return params.highlight_color


def build_ass(segments: list[list[dict]], params: SubtitlesParams) -> str:
    """Genera el contenido del .ass (puro salvo leer la fuente del registro).

    `segments` es una lista de listas de palabras (una por segmento). Dentro de
    cada segmento se chunkea por words_per_line; cada chunk muestra sus palabras
    juntas con la activa coloreada (un evento Dialogue por palabra).

    El tamaño que elige el usuario (font_size) es el em en px, igual que en el
    preview; el Fontsize del ASS se convierte por fuente (ver fonts.py).
    """
    if params.layout == "invert":
        return _build_invert_ass(segments, params)
    font = fonts.font_info(params.font)
    fs = fonts.ass_font_size(font, params.font_size)
    bold = -1 if font["bold"] else 0
    italic = -1 if font["italic"] else 0
    text_color = _TEXT_FOR_OUTLINE[params.outline_color]
    outline_color = _OUTLINE_ASS[params.outline_color]

    # YCbCr Matrix: None → libass convierte los colores con la matriz y el
    # rango del PROPIO video. Sin la línea asume BT.601: sobre un reel bt709
    # el amarillo #fbff00 salía (252,238,0), anaranjado; medido con None:
    # (249-251, 253-254, 0) en bt709, bt601 y rango completo.
    header = f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: {ASS_W}
PlayResY: {ASS_H}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: None

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font["family"]},{fs},{text_color},&H000000FF&,{outline_color},&H80000000&,{bold},{italic},0,0,100,100,0,0,1,{params.outline_size},0,2,{SIDE_MARGIN},{SIDE_MARGIN},60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    n = params.words_per_line
    pos_x = ASS_W // 2
    pos_y = params.position_y

    # Aplanamos todos los grupos (chunks de n palabras) en una sola lista, así
    # cada grupo sabe cuándo arranca el siguiente y podemos evitar que su última
    # palabra invada ese inicio (la superposición entre grupos).
    groups: list[list[dict]] = []
    for seg_words in segments:
        for gi in range(0, len(seg_words), n):
            groups.append(seg_words[gi:gi + n])

    events = []
    word_base = 0   # índice global de la primera palabra del grupo (para rotar colores)
    for g_idx, group in enumerate(groups):
        # Inicio del próximo grupo (None si es el último): tope duro para que
        # nada de este grupo siga vivo cuando el siguiente ya empezó.
        next_start = groups[g_idx + 1][0]["start"] if g_idx + 1 < len(groups) else None

        shown = [w["word"].upper() if params.uppercase else w["word"] for w in group]

        # Tramo de cada palabra: desde que se dice hasta que arranca la
        # siguiente. La última dura hasta su fin (+ un respiro) sin pisar el
        # arranque del grupo que sigue: si hay silencio real limpia pantalla,
        # y si el siguiente arranca pegado calza exacto.
        spans = []
        for idx, word in enumerate(group):
            t_start = word["start"]
            if idx + 1 < len(group):
                t_end = group[idx + 1]["start"]
            else:
                t_end = word["end"] + 0.15
                if next_start is not None:
                    t_end = min(t_end, next_start)
            if t_end <= t_start:
                t_end = t_start + 0.1
            spans.append((t_start, t_end))

        layout, scales, emph = _group_style(params, shown, word_base)
        if layout != "line" or emph:
            events.extend(_word_events(params, font, shown, spans, word_base, text_color,
                                       layout, scales, emph))
            word_base += len(group)
            continue

        fit, extra = _fit_group(font, shown, params.font_size, params.outline_size)
        scale = f"\\fscx{fit * 100:.1f}\\fscy{fit * 100:.1f}" if fit < 1 else ""
        pos_tag = f"{{\\an2\\pos({pos_x},{pos_y}){scale}}}"
        sep = f"{{\\fsp{extra:.1f}}} {{\\fsp0}}" if extra else " "

        # Con neón el texto sube a la capa 1 y el brillo va en la 0, debajo.
        layer = 1 if params.neon != "off" else 0
        for idx, (t_start, t_end) in enumerate(spans):
            parts = []
            for j, raw in enumerate(shown):
                wt = _esc_text(raw)
                if j == idx:
                    color = _active_color(params, word_base + idx)
                    parts.append(f"{{\\c{color}}}{wt}{{\\c{text_color}}}")
                else:
                    parts.append(wt)
            text = sep.join(parts)

            if layer:
                # La misma línea con todo invisible salvo el brillo de la activa.
                glow = sep.join(
                    f"{{\\alpha&H00&{_neon_tags(params, _active_color(params, word_base + idx), params.font_size * fit)}}}"
                    f"{_esc_text(raw)}{{\\alpha&HFF&}}" if j == idx else _esc_text(raw)
                    for j, raw in enumerate(shown))
                events.append(
                    f"Dialogue: 0,{_seconds_to_ass(t_start)},{_seconds_to_ass(t_end)},"
                    f"Sub,,0,0,0,,{pos_tag[:-1]}{NEON_FADE}\\alpha&HFF&}}{glow}")
            events.append(
                f"Dialogue: {layer},{_seconds_to_ass(t_start)},{_seconds_to_ass(t_end)},"
                f"Sub,,0,0,0,,{pos_tag}{text}"
            )
        word_base += len(group)

    return header + "\n".join(events) + "\n"


def _word_events(params: SubtitlesParams, font: dict, shown: list[str],
                 spans: list[tuple[float, float]], word_base: int, text_color: str,
                 layout: str, scales: list[float], emph: set) -> list[str]:
    """Eventos de una frase con cada palabra en su propio lugar.

    Cada palabra es una sucesión SIN superposición: base hasta que se dice,
    resaltada mientras se dice, base de nuevo hasta que termina la frase.
    (Dibujar el resaltado encima de la base dejaba un halo blanco con contorno
    0.) Las destacadas ya están en su color vivo, así que al decirse "laten".
    """
    placed = _place_words(font, shown, scales, params.font_size, params.outline_size,
                          layout, params.position_y)
    g_start, g_end = spans[0][0], spans[-1][1]
    events = []

    layer = 1 if params.neon != "off" else 0   # con neón, el brillo va debajo (capa 0)

    def add(t0, t1, tags, text, at=None):
        if t1 > t0:
            events.append(f"Dialogue: {layer if at is None else at},{_seconds_to_ass(t0)},"
                          f"{_seconds_to_ass(t1)},Sub,,0,0,0,,{{{tags}}}{text}")

    for j, ((x, y, s), raw) in enumerate(zip(placed, shown)):
        size = f"\\fscx{s * 100:.1f}\\fscy{s * 100:.1f}" if abs(s - 1) > 1e-6 else ""
        pos = f"\\an1\\pos({x:.1f},{y:.1f}){size}"
        active = _active_color(params, word_base + j)
        base = active if j in emph else text_color
        wt = _esc_text(raw)
        t_start, t_end = spans[j]
        add(g_start, t_start, f"{pos}\\c{base}", wt)
        pop = ""
        if j in emph:
            big = s * EMPH_POP * 100
            pop = (f"\\t(0,90,\\fscx{big:.1f}\\fscy{big:.1f})"
                   f"\\t(90,200,\\fscx{s * 100:.1f}\\fscy{s * 100:.1f})")
        add(t_start, t_end, f"{pos}\\c{active}{pop}", wt)
        if layer:
            add(t_start, t_end, f"{pos}{NEON_FADE}{_neon_tags(params, active, params.font_size * s)}", wt, at=0)
        add(t_end, g_end, f"{pos}\\c{base}", wt)
    return events


# ---------------------------------------------------------------------------
# "invert": the word in the middle, in the negative of the video
# ---------------------------------------------------------------------------
# Measured on the reference edit (@med_uchiha, Levi): white text with a
# DIFFERENCE blend. Inverting the frame shows the scene continuing inside the
# letters, so each letter is the video in negative: blue over skin, white over
# shadows. One word at a time, centered, in capitals, no outline or shadow.
# libass cannot blend, so the .ass only draws the MASK (white on black) and
# ass_filter() turns it into the difference blend (see INV_MARKER).
INV_MARKER = "; reelforge-effect: invert"
INV_TILT = 8                  # "diagonal": degrees (rising to the right)
INV_POP = (0.86, 1.04)        # each word comes in at 86 %, overshoots to 104 %, settles
INV_POP_MS = (90, 160)
INV_FADE = (70, 40)           # ms: soft in and out (a hard cut flickered between words)
INV_LINE_GAP = 0.16           # em between the capitals of two stacked words
INV_BRIDGE = 1.0              # pause up to this long: the word stays until the next one
INV_HOLD = 0.5                # longer pause: it stays this long after it is sung
INV_MAX_W = {0: 1000, 90: 1700}   # usable length of a line: across / along the frame
_INV_RANDOM = ("horizontal", "horizontal", "horizontal", "vertical", "diagonal", "diagonal_down")
_INV_ANGLE = {"horizontal": 0, "vertical": 90, "diagonal": INV_TILT, "diagonal_down": -INV_TILT}


def inv_group_size(mode: str, first: int) -> int:
    """Words on screen at once for the group that starts at word `first`.
    "mixed": 1 half of the time, 2 or 3 the rest (fixed per word index)."""
    if mode != "mixed":
        return int(mode)
    r = _pick(first, 104729) % 10
    return 1 if r < 5 else 2 if r < 8 else 3


def inv_angle(direction: str, first: int) -> int:
    """Rotation (degrees, \\frz) of the group; "random" draws it per group."""
    if direction == "random":
        direction = _INV_RANDOM[_pick(first, 31337) % len(_INV_RANDOM)]
    return _INV_ANGLE[direction]


def _invert_groups(segments: list[list[dict]], mode: str) -> list[tuple[int, list[dict]]]:
    """(index of the first word, words) per group, never across segments."""
    groups, first = [], 0
    for seg_words in segments:
        i = 0
        while i < len(seg_words):
            size = inv_group_size(mode, first)
            groups.append((first, seg_words[i:i + size]))
            first += len(seg_words[i:i + size])
            i += size
    return groups


_INV_TRAILING = re.compile(r"[.,;:!?…»\"')\]]+$")


def _inv_clean(word: str) -> str:
    return _INV_TRAILING.sub("", (word or "").strip())


def _build_invert_ass(segments: list[list[dict]], params: SubtitlesParams,
                      until: float | None = None) -> str:
    """`until`: nothing stays on screen after this (the edit's acts: Ibai's
    words never spill into the drop)."""
    font = fonts.font_info(params.font)
    em = params.inv_size
    fs = fonts.ass_font_size(font, em)
    cap = font["cap"] / font["upm"]
    desc = font["descent"]
    asc = font["size_ratio"] - desc
    cx, cy = ASS_W // 2, params.inv_y
    header = f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: {ASS_W}
PlayResY: {ASS_H}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: None
{INV_MARKER}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Inv,{font["family"]},{fs},{WHITE},{WHITE},{BLACK},{BLACK},{-1 if font["bold"] else 0},{-1 if font["italic"] else 0},0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # Without the punctuation at the end of each word ("vamos!" → "VAMOS"):
    # one word alone in the middle of the frame reads better clean.
    segments = [[{**w, "word": c} for w in seg if (c := _inv_clean(w["word"]))] for seg in segments]
    segments = [seg for seg in segments if seg]
    groups = _invert_groups(segments, params.inv_words)
    events = []
    for g, (first, words) in enumerate(groups):
        shown = [w["word"].upper() for w in words]
        angle = inv_angle(params.inv_direction, first)
        # Until the next group if the pause is short (no empty frames between
        # words, like the reference); after a real pause the screen clears.
        last_end = words[-1]["end"]
        nxt = groups[g + 1][1][0]["start"] if g + 1 < len(groups) else None
        end = nxt if nxt is not None and nxt - last_end <= INV_BRIDGE else last_end + INV_HOLD
        if nxt is not None:
            end = min(end, nxt)
        if until is not None:
            end = min(end, until)
        # Stacked, one word per line, the block centered on inv_y; too long a
        # word shrinks the whole group (the vertical one has the frame's height).
        widths = [fonts.text_width(font, w, em) for w in shown]
        fit = min(1.0, INV_MAX_W[90 if abs(angle) == 90 else 0] / max(max(widths), 1))
        step = (cap + INV_LINE_GAP) * em * fit
        top = cy - (len(shown) * cap * em * fit + (len(shown) - 1) * INV_LINE_GAP * em * fit) / 2
        rot = f"\\org({cx},{cy})\\frz{angle}" if angle else ""
        a, b = INV_POP_MS
        p0, p1, p2 = (f"{v * fit * 100:.1f}" for v in (*INV_POP, 1.0))
        for k, (word, raw) in enumerate(zip(words, shown)):
            t0 = word["start"]
            if end - t0 < 0.05:
                continue
            baseline = top + cap * em * fit + k * step
            y = baseline + (desc - asc) / 2 * em * fit      # \\an5: the middle of the line box
            tags = (f"\\an5\\pos({cx},{y:.1f}){rot}\\fad({INV_FADE[0]},{INV_FADE[1]})"
                    f"\\fscx{p0}\\fscy{p0}\\t(0,{a},\\fscx{p1}\\fscy{p1})\\t({a},{b},\\fscx{p2}\\fscy{p2})")
            events.append(f"Dialogue: 0,{_seconds_to_ass(t0)},{_seconds_to_ass(end)},Inv,,0,0,0,,"
                          f"{{{tags}}}{_esc_text(raw)}")
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


def _is_invert(ass_path: str) -> bool:
    try:
        with open(ass_path, encoding="utf-8") as fh:
            return INV_MARKER in fh.read(2048)
    except OSError:
        return False


def ass_filter(ass_path: str) -> str:
    """Fragmento de filtro `ass=…:fontsdir=…` (para -vf o un filter_complex).

    Requiere correr FFmpeg con cwd=BASE_DIR (las rutas van relativas). Lo usan
    build_command y la pasada fusionada de reel_express.

    With an "invert" .ass (INV_MARKER) it is a whole chain, still one input
    and one output, so every caller keeps working: the .ass drawn white on a
    black copy of the frame is the mask, and a DIFFERENCE blend with it leaves
    the video as is where it is black and in negative inside the letters. In
    RGB: the inversion is exact, the round trip changes nothing else (measured
    < 1/255 outside the letters) and it was faster than negating in YUV.
    """
    fonts_dir = os.path.relpath(config.FONTS_FOLDER, config.BASE_DIR).replace("\\", "/")
    burn = f"ass={_esc_filter(ass_path)}:fontsdir={fonts_dir}"
    if not _is_invert(ass_path):
        return burn
    return ("format=gbrp,split[_inv_v][_inv_m];"
            f"[_inv_m]drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill,{burn}[_inv_k];"
            "[_inv_v][_inv_k]blend=all_mode=difference,format=yuv420p")


def prepare_ass(segments_data: list[dict], params: SubtitlesParams) -> str:
    """Arma el .ass desde los segmentos y devuelve su ruta (el caller lo borra).

    Centraliza lo que hacían render_job y los pipelines por su cuenta:
    reconstruir palabras, resolver la fuente y escribir el archivo.
    """
    seg_words = words_from_segments(segments_data)
    if not seg_words:
        raise RuntimeError("No hay texto en los subtítulos para generar.")
    return _write_ass(build_ass(seg_words, params))


def build_command(video_path: str, ass_path: str, output_path: str) -> list[str]:
    """Comando FFmpeg para quemar el .ass sobre el video (audio sin tocar)."""
    return [
        config.FFMPEG_PATH,
        "-y",
        "-i", video_path,
        "-vf", ass_filter(ass_path),
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
        ass_path = prepare_ass(segments_data, params)
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
