# CLAUDE.md — ReelForge

## Qué es este proyecto

App web **local** para procesar clips de gaming (League of Legends, Valorant) para reels/shorts. Corre completamente en local: un servidor Flask en Python que llama a FFmpeg (y a yt-dlp / faster-whisper) para el procesamiento, con una interfaz web que se abre en el browser.

**No está pensado para hostearse.** Es una herramienta personal de escritorio que se corre con `python3 app.py` y se usa en `http://localhost:5001`.

---

## Stack

- **Backend:** Python 3.11+ con Flask
- **Procesamiento de video/audio:** FFmpeg + ffprobe (como subprocess desde Python)
- **Descarga de assets:** yt-dlp (librería Python)
- **Transcripción de subtítulos:** faster-whisper (Whisper local; CPU en Mac)
- **Frontend:** HTML + CSS + Vanilla JS (sin frameworks, sin build steps)
- **Almacenamiento:** Sistema de archivos local. `uploads/`, `outputs/`, `downloads/` son temporales; `assets/` es persistente (versionado).

---

## Estructura del proyecto

```
reel-editor/
├── app.py                      ← Entry point Flask: registra blueprints, /api/cleanup, errores JSON
├── config.py                   ← Config global (paths, FFmpeg/ffprobe, extensiones, fuente)
├── requirements.txt            ← Flask, yt-dlp, faster-whisper
│
├── assets/                     ← Assets PERSISTENTES (versionados, NO los toca "Limpiar archivos")
│   ├── watermarks/             ← librería de PNG con transparencia (overlay)
│   └── fonts/                  ← la fuente fija para textos y subtítulos (.ttf/.otf)
│
├── modules/                    ← Cada feature es un módulo independiente
│   ├── reel_express/           ← PIPELINE: encadena vertical+audio+mezcla+watermark+subs
│   ├── vertical_convert/       ← 16:9 → 9:16 con fondo blur + realce
│   ├── sound_drop/             ← Audio de fondo sobre un video vertical
│   ├── watermark/              ← Texto + marca PNG sobre un video vertical
│   ├── subtitles/              ← Subtítulos karaoke (faster-whisper), en 2 fases
│   ├── insert/                 ← Insertar un mini-clip en marcadores
│   ├── downloader/             ← Descargar assets (YouTube/TikTok/IG) con yt-dlp
│   ├── audio_merge/            ← Unir varios audios + capar pausas
│   └── assets/                 ← CRUD de watermarks + fuente (blueprint, no procesa video)
│       ├── routes.py           ← Blueprint Flask con sus endpoints
│       ├── processor.py        ← Lógica del módulo (FFmpeg / yt-dlp / whisper)
│       └── schema.py           ← Parámetros y validación del módulo
│
├── core/
│   ├── ffmpeg_runner.py        ← Único lugar que ejecuta FFmpeg + helpers ffprobe
│   ├── job_manager.py          ← Estado de los jobs (en memoria, thread-safe)
│   └── file_utils.py           ← Helpers de archivos temporales
│
├── static/
│   ├── css/main.css            ← Estilos de toda la app
│   └── js/
│       ├── shared.js           ← Helpers comunes: el(), runJob() (POST + polling)
│       ├── subtitle_editor.js  ← SubtitleEditor: editor de segmentos COMPARTIDO
│       ├── app.js              ← Navegación de la sidebar
│       └── <modulo>.js         ← uno por módulo (reel_express, vertical_convert, …)
│
└── templates/
    ├── index.html              ← App shell: sidebar (por secciones) + <section> por módulo
    └── modules/                ← Un parcial HTML por módulo (se incluyen en index.html)
```

---

## Arquitectura: cómo escala

Cada feature nueva es un **módulo** dentro de `modules/`. Patrón para agregar uno:

1. Crear carpeta `modules/nombre_modulo/` con `schema.py`, `processor.py`, `routes.py`.
2. **`schema.py`** — un dataclass de parámetros con `from_form(form)` que valida y lanza `ValueError` con un mensaje legible (las rutas lo traducen a un 400).
3. **`processor.py`** — la lógica. Separar siempre `build_command(...)` (puro, arma el comando) de `process(job_id, ...)` (orquesta y reporta al job_manager). Esa separación es la que permite **encadenar módulos** (lo hace `reel_express`).
4. **`routes.py`** — un Blueprint con `POST /process` (devuelve `202 {job_id}` y corre en un thread), `GET /status/<job_id>` y `GET /download/<job_id>`.
5. Registrar el blueprint en `app.py`.
6. **Frontend:** un parcial en `templates/modules/`, un `static/js/<modulo>.js`, y sumar el nav item + include + `<script>` en `index.html`.

### Reglas de oro

- **`core/ffmpeg_runner.py` es el único lugar que ejecuta FFmpeg/ffprobe.** Ningún módulo llama a subprocess directo.
- El procesamiento corre en un **`threading.Thread`** por job, así `POST /process` devuelve `202` al instante y la UI hace polling. FFmpeg sigue siendo bloqueante *por job*.
- Cada módulo **valida la entrada** y reporta errores amigables al usuario (no stack traces). La API **siempre responde JSON**, nunca HTML (ver `app.py`).
- Los módulos preservan formato cuando tiene sentido, para **componer** entre sí. `reel_express` ya encadena varios en el servidor.
- Algunos flujos son de **dos fases** (hay una pausa de revisión humana): `subtitles` (transcribir → editar → quemar) y `reel_express` cuando lleva subtítulos.

---

## Los módulos

### 1. vertical_convert — `/api/vertical-convert`

Convierte 16:9 → 9:16 con efecto "split blur background": fondo = el clip escalado a 9:16 con blur gaussiano + brillo; capa principal = el clip centrado en su aspect ratio, con realce de imagen opcional (curvas + saturación + unsharp, estilo CapCut).

| Parámetro | Tipo | Default | Rango |
|---|---|---|---|
| `blur_intensity` | int | 50 | 0–50 (sigma gblur) |
| `bg_brightness` | float | 0.5 | 0.3–1.0 (multiplicador, vía colorchannelmixer) |
| `main_clip_scale` | float | 1.55 | 0.7–5.0 (zoom del clip principal) |
| `main_clip_position` | string | "center" | center / top / bottom |
| `main_clip_offset` | int | 0 | −50–50 (% del alto; se SUMA al preset) |
| `enhance_intensity` | int | 85 | 0–100 (realce de imagen) |
| `hud_enabled` | bool | false (Reel Express: ON) | Marcador (HUD): recorta una región del clip fuente y la flota como placa redondeada (borde blanco + sombra) |
| `hud_left` / `hud_right` / `hud_top` / `hud_bottom` | float | 80 / 99.5 / 0 / 3 | bordes de la región (% del clip fuente; defaults = marcador de LoL 1080p) |
| `hud_scale` / `hud_pos_y` | int | 91 / 23 | ancho de la placa (% del lienzo) / posición vertical (%) |

**Input:** `video`. **UI:** preview vertical en vivo en un `<canvas>` que replica el efecto mientras movés los sliders. La placa del HUD se arma en el mismo filter_complex (máscara redondeada y sombra calculadas sobre UN frame con `trim=end_frame=1`, reutilizadas vía `repeatlast`): costo extra nulo. Reel Express hereda todo esto vía `VerticalConvertParams`.

### 2. sound_drop — `/api/sound-drop`

Superpone un audio externo (archivo de audio o la pista de otro video) sobre un video vertical.

| Parámetro | Tipo | Default | Rango |
|---|---|---|---|
| `original_volume` | int | 0 | 0–100 (%) |
| `new_volume` | int | 100 | 0–150 (%) |
| `fade` | bool | true | fade in/out del audio nuevo |
| `speed_match` | bool | true | acelera el video para igualar la duración del audio (requiere ffprobe) |

**Inputs:** `video` + `audio`. **UI:** preview de mezcla en vivo con Web Audio API.

### 3. watermark — `/api/watermark`

Compone sobre un video vertical un bloque de texto (principal blanco + secundario rojo, con contorno, un `drawtext` por renglón) y/o un watermark PNG de la librería de assets. **No toca el audio** (lo copia tal cual).

| Parámetro | Tipo | Default | Notas |
|---|---|---|---|
| `primary_text` / `secondary_text` | string | "" | máx 200 chars; multilínea |
| `text_size` | int | 55 | 24–140 px |
| `text_y` | int | 20 | 0–100 (% del alto) |
| `watermark` | string | "" | nombre del PNG en `assets/watermarks/` |
| `watermark_x` | string | "center" | left / center / right |
| `watermark_size` | int | 50 | 10–100 (% del ancho) |
| `watermark_y` | int | 100 | 0–100 (% del alto) |

**Input:** `video`. Requiere texto **o** watermark. Usa la fuente de `assets/fonts/`. **Escaping:** rutas relativas a `BASE_DIR` + `cwd=BASE_DIR` (el `:` del drive en Windows rompe el parser de filtros). **UI:** preview en canvas + galería de watermarks (CRUD).

### 4. subtitles — `/api/subtitles` (dos fases)

Transcribe el audio con **faster-whisper** y quema subtítulos estilo **karaoke** (palabra activa coloreada) con libass. Flujo:
- `POST /transcribe` → sube el video, transcribe en thread; el status, al terminar, trae los `segments` (con palabras + timestamps).
- El usuario **revisa y corrige** el texto en la UI.
- `POST /render` → con los segmentos (editados) + estilo, arma un `.ass` y FFmpeg lo quema. Si el texto cambió, los timestamps por palabra se **redistribuyen** proporcional a la longitud.

| Parámetro | Tipo | Default | Rango |
|---|---|---|---|
| `font_size` | int | 64 | 30–140 px |
| `position_y` | int | 1560 | 0–1920 (px en 1080×1920) |
| `words_per_line` | int | 3 | 1–6 |
| `highlight_color` | string | #fbff00 | hex CSS → ASS BGR |
| `language` | string | es | auto/es/en/pt/fr/de/it/ja/ko/zh |
| `model` | string | large-v3-turbo | tiny…large-v3 |

Device automático: intenta cuda/float16, cae a cpu/int8 (en Mac es CPU). La 1ª vez **descarga el modelo** (~1.6 GB para turbo).

### 5. insert — `/api/insert`

Inserta un mini-clip completo (corte seco) en cada marcador del timeline del original. Automatiza "partir el timeline y duplicar el asset" de CapCut.

| Parámetro | Tipo | Notas |
|---|---|---|
| `markers` | list[float] | segundos; JSON `[1.5, 12]` o CSV `1.5,12`. Máx 50 |

**Inputs:** `video` (original) + `clip` (mini-clip). Audio y video se cortan juntos. Se arma con el `concat` filter; solo se normaliza lo invisible que concat exige (fps, SAR, pixfmt, audio). **Valida que las resoluciones coincidan** (si no, error claro apuntando a Vertical). **UI:** timeline clicable con marcadores por click / punto actual / `mm:ss`.

### 6. downloader — `/api/downloader`

Descarga un asset desde un link (YouTube, TikTok, Instagram, …) con yt-dlp. Reemplaza el flujo manual de bajar a teléfono + Telegram.

| Parámetro | Tipo | Notas |
|---|---|---|
| `url` | string | http(s) |
| `format` | string | `audio` (mp3) o `video` (mp4) |
| `quality` | string | audio: 320/192/128 kbps · video: max/1080/720/480 |

**Sin upload** (es un link). Usa el FFmpeg configurado para extraer audio / hacer merge. Captura el título para nombrar la descarga. YouTube y TikTok públicos son sólidos; Instagram es best-effort.

### 7. audio_merge — `/api/audio-merge`

Une N audios en el orden de subida (concat filter) y luego **capa las pausas** con `silenceremove`: recorta el silencio inicial y limita cada pausa interna/final a `max_pause` segundos, para que la narración (típicamente voz de TTS) fluya. Output mp3.

| Parámetro | Tipo | Default | Rango |
|---|---|---|---|
| `max_pause` | float | 0.5 | 0.1–1.5 (s; tope de cada pausa) |

**Input:** `audios` (múltiple, 2–20). Umbral de silencio interno (`SILENCE_THRESHOLD_DB = -40`). **UI:** lista reordenable (▲▼), escuchar cada clip, slider de pausa máxima.

### 8. reel_express — `/api/reel-express` (pipeline, dos fases con subs)

Del clip 16:9 + audio (link o archivo) al reel final, encadenando módulos **en el servidor** (sin rebotes por el browser). No tiene FFmpeg propio: reusa los `process()` de cada módulo vía sub-jobs internos; el progreso se agrega en la ruta de status leyendo esos sub-jobs.

```
clip 16:9 ─vertical─┐
                    ├─sound_drop─→ [watermark] ─→ [transcribir → ✋ editar → quemar subs] ─→ reel final
audio (link│file) ──┘
```

- `vertical` y la descarga (si el audio es link) corren **en paralelo**.
- Reúne los `from_form` de vertical/sound_drop/watermark/subtitles (los nombres de campo no se solapan).
- **Watermark** opcional (si hay texto o marca). **Subtítulos** opcional (`add_subtitles`, default ON); su `position_y` default acá es **1601**.
- Con subtítulos es **dos fases**: `POST /process` produce el video base y lo transcribe (status done + `segments`); `POST /finish` quema los subs editados → reel final. Sin subs, es un solo paso como antes.

### assets — blueprint de assets persistentes

No procesa video: gestiona la **librería de watermarks** y la **fuente**.

```
GET    /api/watermarks            → lista de watermarks
POST   /api/watermarks            → subir un PNG
DELETE /api/watermarks/<name>     → borrar
GET    /assets/watermarks/<name>  → servir el PNG (galería / preview)
GET    /api/font                  → ¿hay fuente cargada?
GET    /assets/font               → servir la fuente (para el @font-face del preview)
```

### Endpoints (contrato común)

```
POST /api/<modulo>/process       → 202 { job_id }   (corre en thread)
GET  /api/<modulo>/status/<id>   → { status, progress, error, [stage], [segments], [title] }
GET  /api/<modulo>/download/<id> → archivo (mimetype correcto, as_attachment)
```
`status` ∈ `pending | processing | done | error`. **Excepciones de dos fases:** `subtitles` usa `/transcribe` + `/render`; `reel_express` usa `/process` + `/finish`. Global: `POST /api/cleanup` vacía las carpetas temporales.

---

## core/ — contratos

### ffmpeg_runner.py

```python
run(command, job_id, total_duration=None, cwd=None, progress_range=(0, 100)) -> None
    # Ejecuta FFmpeg, parsea stderr (time= / Duration=) para el progreso,
    # actualiza job_manager y lanza RuntimeError si FFmpeg falla.
    # cwd: dir de trabajo (filtros con rutas relativas, p. ej. drawtext/ass).
    # progress_range: banda (lo, hi) para reportar el % cuando FFmpeg es una
    #                 fase de un pipeline mayor.

probe_duration(path) -> float | None      # duración en segundos (ffprobe)
has_audio_stream(path) -> bool            # ¿tiene pista de audio?
probe_resolution(path) -> tuple | None    # (width, height)
probe_fps(path) -> float | None           # fps del primer stream de video
```

Si `total_duration` es None, se intenta sacar de la línea `Duration:` que imprime FFmpeg.

### job_manager.py

Jobs en memoria (dict + lock), thread-safe. Base: `job_id`, `status`, `progress` (0-100), `input_path`, `output_path`, `error`. Los módulos añaden campos extra: `title` (downloader), `stage`/`segments` (subtitles), `phase`/`base_path`/`sub_*`/`has_wm`/`has_subs` (reel_express). API: `create_job`, `get_job`, `update_job(**fields)`, `set_progress`.

### file_utils.py

`ensure_dirs()` (crea uploads/outputs/downloads), `save_upload(file_storage)`, `output_path_for(job_id, ext="mp4")`, `cleanup_paths(*paths)`, `clear_temp_dirs()` (lo usa `/api/cleanup`).

---

## config.py

Resuelve FFmpeg y ffprobe de forma robusta (PATH → ubicaciones típicas de Mac → env var). Claves:

```python
UPLOAD_FOLDER / OUTPUT_FOLDER / DOWNLOAD_FOLDER        # temporales
ASSETS_FOLDER / WATERMARKS_FOLDER / FONTS_FOLDER        # persistentes
MAX_UPLOAD_SIZE_MB = 2000
FFMPEG_PATH   = os.environ.get("FFMPEG_PATH")  or _resolve_ffmpeg()
FFPROBE_PATH  = os.environ.get("FFPROBE_PATH") or _resolve_ffprobe()
ALLOWED_EXTENSIONS        = {"mp4","mov","mkv","avi"}
ALLOWED_AUDIO_EXTENSIONS  = {"mp3","wav","m4a","aac","ogg"} | ALLOWED_EXTENSIONS
ALLOWED_WATERMARK_EXTENSIONS = {"png"}
ALLOWED_FONT_EXTENSIONS      = {"ttf","otf"}
OUTPUT_WIDTH, OUTPUT_HEIGHT  = 1080, 1920
resolve_font_path()  # primer .ttf/.otf en assets/fonts/, o None
```

En Windows `FFMPEG_PATH` puede necesitar el path completo; o setear las env vars.

---

## app.py

- Registra los 9 blueprints + `/` (index) + `POST /api/cleanup`.
- **La API siempre responde JSON:** `errorhandler` para `RequestEntityTooLarge` (413), `HTTPException` y `Exception` (500) — sin esto el frontend rompía al parsear el HTML de error de Flask.
- `SEND_FILE_MAX_AGE_DEFAULT = 0`: no cachea estáticos en dev, así el browser toma el JS/CSS recién editado.

---

## UI

`index.html` es un **app shell**: una **sidebar dividida en secciones** (`.nav-section`: Automático / Módulos / Tools Specific videos / Tools) con un nav item por módulo, y un `<section class="module-view">` por módulo (parciales con `{% include %}`). `app.js` muestra una vista a la vez (busca `.nav-item`). La vista por defecto es **Reel Express**.

Cada módulo trae su propio JS y usa `runJob(apiBase, formData, {onProgress, onDone, onError})` de `shared.js` (POST + polling). Los flujos de dos fases (subtitles, reel_express) usan un poller propio porque el status trae `segments`. El **editor de segmentos** (`SubtitleEditor` en `subtitle_editor.js`) es compartido por subtitles y reel_express. Sin librerías frontend externas (solo Vanilla JS).

---

## Dependencias del sistema

- **FFmpeg** y **ffprobe** en el PATH (o resueltos por `config.py`). ffprobe es necesario para el speed match, resoluciones y duración exacta; sin él, varias features degradan en vez de crashear.
- **yt-dlp** (pip) para el downloader. Se actualiza seguido: `pip install -U yt-dlp`.
- **faster-whisper** (pip) para subtitles. La 1ª transcripción descarga el modelo (~1.6 GB turbo). En Mac corre en **CPU** (CTranslate2 no usa Metal/GPU); modelos chicos (small/medium) son más livianos.

---

## Cómo correrlo

```bash
pip install -r requirements.txt   # Flask, yt-dlp, faster-whisper
ffmpeg -version && ffprobe -version   # deben existir

python3 app.py                    # ojo: python3, no python (en macOS)
# http://localhost:5001           # puerto 5001 (el 5000 lo ocupa AirPlay en Mac)
```

---

## Convenciones de desarrollo

- **Commits por bloques lógicos**, con prefijos `feat(modulo): …` / `fix: …` / `chore: …` / `refactor: …` / `docs: …`.
- **Verificar end-to-end** antes de dar por hecho un módulo: import check + prueba real por HTTP (process → status → download) + casos de validación.
- El procesamiento corre en threads; si se necesita concurrencia robusta o persistencia, migrar `job_manager` a una store externa.
- `uploads/`, `outputs/`, `downloads/` son temporales (`.gitignore`) y se vacían con el botón "Limpiar archivos" (`/api/cleanup`). `assets/` es persistente.
- No hay autenticación porque es local. No exponer el puerto en red pública.

## Ideas futuras (v2)

- **Trim** como módulo o paso previo (rango start/end).
- **insert:** preview reproducible del resultado ensamblado, transiciones (flash/crossfade), marcadores arrastrables.
- **downloader:** preview de título/miniatura antes de bajar, cookies para contenido privado, preferir H.264 para máxima compatibilidad.
- **subtitles:** wrappers que usen la GPU del Mac (mlx-whisper / whisper.cpp) si la CPU queda corta.
- **Endurecimiento de dependencias:** pin + hashes, `pip-audit`, venv.
- **Limpieza automática** de temporales (TTL), en vez de solo el botón manual.
