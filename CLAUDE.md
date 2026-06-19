# CLAUDE.md — ReelForge

## Qué es este proyecto

App web **local** para procesar clips de gaming (League of Legends, Valorant) para reels/shorts. Corre completamente en local: un servidor Flask en Python que llama a FFmpeg (y a yt-dlp) para el procesamiento, con una interfaz web que se abre en el browser.

**No está pensado para hostearse.** Es una herramienta personal de escritorio que se corre con `python3 app.py` y se usa en `http://localhost:5001`.

---

## Stack

- **Backend:** Python 3.11+ con Flask
- **Procesamiento de video/audio:** FFmpeg + ffprobe (como subprocess desde Python)
- **Descarga de assets:** yt-dlp (librería Python)
- **Frontend:** HTML + CSS + Vanilla JS (sin frameworks, sin build steps)
- **Almacenamiento:** Sistema de archivos local (`uploads/`, `outputs/`, `downloads/`, todos temporales)

---

## Estructura del proyecto

```
reel-editor/
├── app.py                      ← Entry point Flask, registra los blueprints
├── config.py                   ← Configuración global (paths, FFmpeg/ffprobe, extensiones)
├── requirements.txt            ← Flask, yt-dlp
│
├── modules/                    ← Cada feature es un módulo independiente
│   ├── vertical_convert/       ← 16:9 → 9:16 con fondo blur + realce
│   ├── sound_drop/             ← Audio de fondo sobre un video vertical
│   ├── insert/                 ← Insertar un mini-clip en marcadores
│   ├── audio_merge/            ← Unir varios audios en orden + capar pausas
│   └── downloader/             ← Descargar assets (YouTube/TikTok/IG) con yt-dlp
│       ├── __init__.py
│       ├── routes.py           ← Blueprint Flask con sus endpoints
│       ├── processor.py        ← Lógica del módulo (FFmpeg o yt-dlp)
│       └── schema.py           ← Parámetros y validación del módulo
│
├── core/
│   ├── ffmpeg_runner.py        ← Wrapper central para ejecutar FFmpeg + helpers ffprobe
│   ├── job_manager.py          ← Estado de los jobs (en memoria, thread-safe)
│   └── file_utils.py           ← Helpers de archivos temporales
│
├── static/
│   ├── css/main.css            ← Estilos de toda la app
│   └── js/
│       ├── shared.js           ← Helpers comunes: el(), runJob() (POST + polling)
│       ├── app.js              ← Navegación de la sidebar
│       ├── vertical_convert.js
│       ├── sound_drop.js
│       ├── insert.js
│       └── downloader.js
│
└── templates/
    ├── index.html              ← App shell: sidebar + <section> por módulo
    └── modules/                ← Un parcial HTML por módulo (se incluyen en index.html)
        ├── vertical_convert.html
        ├── sound_drop.html
        ├── insert.html
        └── downloader.html
```

---

## Arquitectura: cómo escala

Cada feature nueva es un **módulo** dentro de `modules/`. Patrón para agregar uno:

1. Crear carpeta `modules/nombre_modulo/` con `schema.py`, `processor.py`, `routes.py`.
2. **`schema.py`** — un dataclass de parámetros con `from_form(form)` que valida y lanza `ValueError` con un mensaje legible (las rutas lo traducen a un 400).
3. **`processor.py`** — la lógica. Separar siempre `build_command(...)` (puro, arma el comando) de `process(job_id, ...)` (orquesta y reporta al job_manager). Esa separación es la que permite **encadenar módulos** a futuro.
4. **`routes.py`** — un Blueprint con `POST /process` (devuelve `202 {job_id}` y corre en un thread), `GET /status/<job_id>` y `GET /download/<job_id>`.
5. Registrar el blueprint en `app.py`.
6. **Frontend:** un parcial en `templates/modules/`, un `static/js/<modulo>.js`, y sumar el nav item + include + `<script>` en `index.html`.

### Reglas de oro

- **`core/ffmpeg_runner.py` es el único lugar que ejecuta FFmpeg/ffprobe.** Ningún módulo llama a subprocess directo.
- El procesamiento corre en un **`threading.Thread`** por job, así `POST /process` devuelve `202` al instante y la UI hace polling. FFmpeg sigue siendo bloqueante *por job*.
- Cada módulo **valida la entrada** y reporta errores amigables al usuario (no stack traces).
- Los módulos preservan formato cuando tiene sentido, para **componer** entre sí (ej. `downloader → vertical → insert/sound_drop`).

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
| `enhance_intensity` | int | 85 | 0–100 (realce de imagen) |

**Input:** `video`. **UI:** preview vertical en vivo en un `<canvas>` que replica el efecto mientras movés los sliders.

### 2. sound_drop — `/api/sound-drop`

Superpone un audio externo (archivo de audio o la pista de otro video) sobre un video vertical.

| Parámetro | Tipo | Default | Rango |
|---|---|---|---|
| `original_volume` | int | 0 | 0–100 (%) |
| `new_volume` | int | 100 | 0–150 (%) |
| `fade` | bool | true | fade in/out del audio nuevo |
| `speed_match` | bool | true | acelera el video para igualar la duración del audio (requiere ffprobe) |

**Inputs:** `video` + `audio`. **UI:** preview de mezcla en vivo con Web Audio API.

### 3. insert — `/api/insert`

Inserta un mini-clip completo (corte seco) en cada marcador del timeline del original. Automatiza "partir el timeline y duplicar el asset" de CapCut.

| Parámetro | Tipo | Notas |
|---|---|---|
| `markers` | list[float] | segundos; JSON `[1.5, 12]` o CSV `1.5,12`. Máx 50 |

**Inputs:** `video` (original) + `clip` (mini-clip). Audio y video se cortan juntos. Se arma con el `concat` filter; solo se normaliza lo invisible que concat exige (fps, SAR, pixfmt, audio). **Valida que las resoluciones coincidan** (si no, error claro apuntando a Vertical). **UI:** timeline clicable con marcadores por click / punto actual / `mm:ss`.

### 4. downloader — `/api/downloader`

Descarga un asset desde un link (YouTube, TikTok, Instagram, …) con yt-dlp. Reemplaza el flujo manual de bajar a teléfono + Telegram.

| Parámetro | Tipo | Notas |
|---|---|---|
| `url` | string | http(s) |
| `format` | string | `audio` (mp3) o `video` (mp4) |
| `quality` | string | audio: 320/192/128 kbps · video: max/1080/720/480 |

**Sin upload** (es un link). Usa el FFmpeg configurado para extraer audio / hacer merge. Captura el título para nombrar la descarga. YouTube y TikTok públicos son sólidos; Instagram es best-effort.

### 5. audio_merge — `/api/audio-merge`

Une N audios en el orden de subida (concat filter) y luego **capa las pausas** con `silenceremove`: recorta el silencio inicial y limita cada pausa interna/final a `max_pause` segundos, para que la narración (típicamente voz de TTS) fluya. Output mp3.

| Parámetro | Tipo | Default | Rango |
|---|---|---|---|
| `max_pause` | float | 0.5 | 0.1–1.5 (s; tope de cada pausa) |

**Input:** `audios` (múltiple, 2–20). El umbral de silencio es una constante interna (`SILENCE_THRESHOLD_DB = -40`), holgado para TTS limpio. **UI:** lista reordenable (▲▼), escuchar cada clip, y slider de pausa máxima.

### Endpoints (mismo contrato en todos los módulos)

```
POST /api/<modulo>/process       → 202 { job_id }   (corre en thread)
GET  /api/<modulo>/status/<id>   → { status, progress, error, [title] }
GET  /api/<modulo>/download/<id> → archivo (mimetype correcto, as_attachment)
```
`status` ∈ `pending | processing | done | error`.

---

## core/ — contratos

### ffmpeg_runner.py

```python
run(command: list[str], job_id: str, total_duration: float | None = None) -> None
    # Ejecuta FFmpeg, parsea stderr (time= / Duration=) para el progreso,
    # actualiza job_manager y lanza RuntimeError si FFmpeg falla.

probe_duration(path) -> float | None      # duración en segundos (ffprobe)
has_audio_stream(path) -> bool            # ¿tiene pista de audio?
probe_resolution(path) -> tuple|None      # (width, height)
probe_fps(path) -> float | None           # fps del primer stream de video
```

Si `total_duration` es None, se intenta sacar de la línea `Duration:` que imprime FFmpeg (fallback cuando no hay ffprobe).

### job_manager.py

Jobs en memoria (dict + lock), thread-safe. Cada job:

```python
{
  "job_id": str,
  "status": "pending" | "processing" | "done" | "error",
  "progress": int,            # 0-100
  "input_path": str,
  "output_path": str,
  "error": str | None,
  # campos extra opcionales por módulo (ej. downloader añade "title")
}
```

API: `create_job`, `get_job`, `update_job(**fields)`, `set_progress`.

### file_utils.py

`ensure_dirs()` (crea uploads/outputs/downloads), `save_upload(file_storage)` (nombre único), `output_path_for(job_id)`, `cleanup_paths(*paths)`.

---

## config.py

Resuelve FFmpeg y ffprobe de forma robusta (PATH → ubicaciones típicas de Mac → variable de entorno). Claves:

```python
UPLOAD_FOLDER / OUTPUT_FOLDER / DOWNLOAD_FOLDER
MAX_UPLOAD_SIZE_MB = 2000
FFMPEG_PATH   = os.environ.get("FFMPEG_PATH")  or _resolve_ffmpeg()
FFPROBE_PATH  = os.environ.get("FFPROBE_PATH") or _resolve_ffprobe()
ALLOWED_EXTENSIONS       = {"mp4","mov","mkv","avi"}
ALLOWED_AUDIO_EXTENSIONS = {"mp3","wav","m4a","aac","ogg"} | ALLOWED_EXTENSIONS
OUTPUT_WIDTH, OUTPUT_HEIGHT = 1080, 1920
```

En Windows `FFMPEG_PATH` puede necesitar el path completo (`C:/ffmpeg/bin/ffmpeg.exe`); o setear las env vars `FFMPEG_PATH` / `FFPROBE_PATH`.

---

## UI

`index.html` es un **app shell**: una **sidebar** con un nav item por módulo y un `<section class="module-view">` por módulo (parciales incluidos con `{% include %}`). `app.js` muestra una vista a la vez.

Cada módulo trae su propio JS y usa `runJob(apiBase, formData, {onProgress, onDone, onError})` de `shared.js` para el POST + polling de status. Sin librerías frontend externas (solo Vanilla JS).

---

## Dependencias del sistema

- **FFmpeg** y **ffprobe** en el PATH (o resueltos por `config.py`). ffprobe es necesario para el speed match de sound_drop y la duración exacta; sin él, varias features degradan en vez de crashear.
- **yt-dlp** (pip) para el módulo downloader. Se actualiza seguido: `pip install -U yt-dlp` cada tanto.

---

## Cómo correrlo

```bash
pip install -r requirements.txt   # Flask, yt-dlp
ffmpeg -version && ffprobe -version   # deben existir

python3 app.py                    # ojo: python3, no python (en macOS)
# http://localhost:5001           # puerto 5001 (el 5000 lo ocupa AirPlay en Mac)
```

---

## Convenciones de desarrollo

- **Commits por bloques lógicos**, con prefijos `feat(modulo): …` / `chore: …` / `fix: …`.
- **Verificar end-to-end** antes de dar por hecho un módulo: import check + prueba real por HTTP (process → status → download) + casos de validación.
- El procesamiento corre en threads; si se necesita concurrencia robusta o persistencia, migrar `job_manager` a una store externa.
- `uploads/`, `outputs/`, `downloads/` son temporales y están en `.gitignore`. **Pendiente:** limpieza automática (TTL / borrar tras descarga).
- No hay autenticación porque es local. No exponer el puerto en red pública.

## Ideas futuras (v2)

- **Trim** como módulo o paso previo (rango start/end).
- **insert:** preview reproducible del resultado ensamblado, transiciones (flash/crossfade), marcadores arrastrables.
- **downloader:** preview de título/miniatura antes de bajar, cookies para contenido privado, preferir H.264 para máxima compatibilidad.
- **Hand-off interno entre módulos** (que el output de uno sea input del siguiente sin pasar por el browser) — el norte de "combinar módulos".
- **Endurecimiento de dependencias:** pin + hashes, `pip-audit`, venv.
