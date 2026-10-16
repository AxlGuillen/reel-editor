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
├── setup.bat                   ← Windows: crea el venv e instala todo (doble click)
├── Iniciar ReelForge.bat       ← Windows: levanta el server y abre el browser
│
├── assets/                     ← Assets PERSISTENTES (versionados, NO los toca "Limpiar archivos")
│   ├── watermarks/             ← librería de PNG con transparencia (overlay)
│   ├── competitivo/            ← fondo_portada.png y sting.mp3 opcionales (intro del módulo competitivo)
│   ├── fonts/                  ← fuentes de subtítulos (Poppins Bold/ExtraBold/Black, Anton,
│   │                             Archivo Black); SOLO archivos de fuente (libass los abre todos)
│   └── font_licenses/          ← licencias OFL de las fuentes (fuera de fonts/ a propósito)
│
├── modules/                    ← Cada feature es un módulo independiente
│   ├── reel_express/           ← PIPELINE: encadena vertical+audio+mezcla+watermark+subs
│   ├── hook_reel/              ← PIPELINE "Reel Frase": 2 videos + música con ducking
│   ├── vertical_convert/       ← 16:9 → 9:16 con fondo blur + realce
│   ├── sound_drop/             ← Audio de fondo sobre un video vertical
│   ├── watermark/              ← Texto + marca PNG sobre un video vertical
│   ├── subtitles/              ← Subtítulos karaoke (faster-whisper), en 2 fases
│   ├── insert/                 ← Insertar mini-clips en marcadores (corte / progresivo / PiP)
│   ├── downloader/             ← Descargar assets (YouTube/TikTok/IG) con yt-dlp
│   ├── audio_merge/            ← Unir varios audios + capar pausas
│   ├── assets/                 ← CRUD de watermarks + fuente (blueprint, no procesa video)
│   ├── library/                ← Librería de clips: lista la carpeta local de capturas (sin upload)
│   ├── timeline/               ← Historial del proyecto leído de git (vista informativa)
│   └── competitivo/            ← PIPELINE: highlights de Worlds (solo kills) desde el VOD de Ibai
│
│   Cada módulo que procesa video tiene los mismos tres archivos:
│       routes.py      ← Blueprint Flask con sus endpoints
│       processor.py   ← Lógica del módulo (FFmpeg / yt-dlp / whisper)
│       schema.py      ← Parámetros y validación del módulo
│   assets, library y timeline son solo routes.py: no procesan nada.
│
├── cookies/                    ← cookies.txt de yt-dlp. SENSIBLE: gitignored
├── cache/thumbs/               ← miniaturas de la librería de clips (temporal)
│
├── tests/                      ← unittest (sin pytest): python -m unittest discover -s tests -t .
│   └── competitivo/            ← detección de peleas (fixture real), layout, sincronía, validación
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
| `bg_brightness` | float | 0.5 | 0.3–1.0 (multiplicador, vía `lutyuv` en YUV: luma escalada sobre el negro y croma sobre el neutro, que es lo mismo que multiplicar R, G y B) |
| `main_clip_scale` | float | 1.55 | 0.7–5.0 (zoom del clip principal) |
| `main_clip_position` | string | "center" | center / top / bottom |
| `main_clip_offset` | int | 0 | −50–50 (% del alto; se SUMA al preset) |
| `enhance_intensity` | int | 85 | 0–100 (realce de imagen) |
| `hud_enabled` | bool | false (Reel Express: ON) | Marcador (HUD): recorta una región del clip fuente y la flota como placa redondeada (borde blanco + sombra) |
| `hud_left` / `hud_right` / `hud_top` / `hud_bottom` | float | 80 / 99.5 / 0 / 3 | bordes de la región (% del clip fuente; defaults = marcador de LoL 1080p) |
| `hud_scale` / `hud_pos_y` | int | 80 / 23 | ancho de la placa (% del lienzo) / posición vertical (%) |

La placa **acompaña al `main_clip_offset`**: se le suma el mismo desplazamiento en px que al clip principal, así el ajuste fino mueve video y marcador juntos y no hay que reacomodar la placa cada vez. El preset (top/center/bottom) **no** la arrastra, y `hud_pos_y` sigue moviéndola por separado. El preview en canvas aplica la misma suma (`drawHudPlate` en `shared.js`).

**Input:** `video`. **UI:** preview vertical en vivo en un `<canvas>` que replica el efecto mientras movés los sliders. La placa del HUD se arma en el mismo filter_complex (máscara redondeada y sombra calculadas sobre UN frame con `trim=end_frame=1`, reutilizadas vía `repeatlast`): costo extra nulo. Reel Express hereda todo esto vía `VerticalConvertParams`.

**El grafo va en YUV 4:2:0 tv de punta a punta** (`build_filter_complex`). Lo hace la mitad de caro (−41 % medido) y que el reel salga bt709/tv con píxeles cuadrados; antes salía etiquetado gbr/pc. Así se arma:
- Brillo del fondo con `lutyuv`, a 1/4 de resolución. Antes era `colorchannelmixer`, que pasaba el fondo entero a RGB, y NVENC terminaba recibiendo RGBA.
- Recorte 9:16 del fondo ANTES de achicarlo.
- Clip principal recortado a los 1080 px visibles ANTES del realce. La x replica el redondeo a par del overlay en 4:2:0 (−297 → −298); con 297 se corría 1 px.
- `out_range=tv` en el primer scale, para las fuentes de rango completo.
- `setsar=1`, porque el recorte 9:16 da 607.5 px.
- Por RGB pasan solo `curves` (sobre el recorte) y la placa chica del HUD.

`head=` aplica filtros una sola vez y reparte el clip con `split` a fondo, clip y HUD; Reel Express pone ahí el retime.

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
| `watermark_size` | int | 50 | 10–200 (% del ancho; >100 desborda el cuadro) |
| `watermark_y` | int | 100 | −50–200 (% del alto; permite desbordar el cuadro) |

**Input:** `video`. Requiere texto **o** watermark. Usa la fuente de `assets/fonts/`. **Escaping:** rutas relativas a `BASE_DIR` + `cwd=BASE_DIR` (el `:` del drive en Windows rompe el parser de filtros). **UI:** preview en canvas + galería de watermarks (CRUD).

### 4. subtitles — `/api/subtitles` (dos fases)

Transcribe el audio con **faster-whisper** y quema subtítulos estilo **karaoke** (palabra activa coloreada) con libass. Flujo:
- `POST /transcribe` → sube el video, transcribe en thread; el status, al terminar, trae los `segments` (con palabras + timestamps).
- El usuario **revisa y corrige** el texto en la UI.
- `POST /render` → con los segmentos (editados) + estilo, arma un `.ass` y FFmpeg lo quema. Si el texto cambió, los timestamps por palabra se **redistribuyen** proporcional a la longitud.

| Parámetro | Tipo | Default | Rango |
|---|---|---|---|
| `font_size` | int | 64 | 30–140 px (**em** de la letra, igual que el preview) |
| `position_y` | int | 1560 | 0–1920 (px en 1080×1920; borde inferior del texto) |
| `words_per_line` | int | 3 | 1–6 |
| `highlight_color` | string | #fbff00 | hex CSS → ASS BGR (inválido → 400) |
| `sub_font` | string | Poppins-Bold.ttf | archivo de `assets/fonts/` |
| `sub_uppercase` | bool | false | todo en mayúsculas (respeta tildes) |
| `sub_color_mode` | string | single | `single` (color de resalte) / `rotate` (cada palabra dicha toma el siguiente color de `ROTATE_PALETTE`) |
| `sub_outline_color` | string | black | `black` (texto blanco) / `white` (texto negro) |
| `sub_outline_size` | int | 6 | 0–20 px |
| `sub_layout` | string | line | `line` (en línea) / `stairs` (escalera: un renglón por palabra, cada uno más abajo y con el centro 0.8 em más a la derecha) / `mixed` (variada: ~2 de cada 5 frases en escalera) |
| `sub_sizes` | string | same | `same` / `grow` (crecientes: 0.85→1.4× hasta la última palabra con 3+ letras, que queda grande y a color) / `random` (variados: una palabra de 3+ letras a 1.45× y a color) |
| `sub_neon` | string | off | `off` / `soft` (una sombra leve) / `strong`: mientras se dice, la palabra brilla con SU color. Capa debajo del texto con la misma palabra sin relleno, `\bord` grueso del color y `\blur`; entra con `\fad`. En línea va como una línea igual con todo invisible salvo la activa (medido: el contorno gigante no corre la alineación, 0 px) |
| `language` | string | es | auto/es/en/pt/fr/de/it/ja/ko/zh |
| `model` | string | large-v3-turbo | tiny…large-v3 |

Los campos de estilo llevan prefijo `sub_` porque reel_express y hook_reel arman todos sus params desde el mismo form. **Disposición (`_group_style` / `_place_words` / `_word_events`).** Con `line`+`same` se emite el karaoke de siempre (una línea por tramo, byte a byte igual que antes). Si no, cada palabra va en sus propios eventos con `\an1\pos(x,y)` (abajo-izquierda), en sucesión SIN superposición: base hasta que se dice, resaltada mientras se dice, base de nuevo (dibujarla dos veces dejaba un halo blanco con contorno 0). Las destacadas ya están en su color y al decirse \"laten\" (`\t` a 1.12×). Medido en libass: con `\an1` el origen es x y el borde inferior es baseline + winDescent·em·escala; `\fscx`/`\fscy` escalan letras y descendente pero no el contorno. Los renglones de la escalera se separan por la **tinta real** (`fonts.ink_extent`: acentos arriba, descendentes abajo, cada uno con su escala) + 0.12 em + 2·contorno; un espaciado fijo hacía que se tocaran. La escalera crece hacia arriba desde `position_y`: es más alta que una línea y a la altura por defecto puede tapar la barra de habilidades (la UI lo avisa). La elección de escalera/palabra destacada usa un hash fijo (`_pick`, Knuth) que el preview replica en JS: misma transcripción → mismo video, y el preview elige lo mismo (±3 px). Los tres módulos usan el mismo `build_ass` vía `prepare_ass`.

**Fuentes (`fonts.py`).** libass busca por **familia** (nameID 1) + negrita (nameID 2 dice "Bold"), NO por el nombre del archivo: con "Poppins-Bold" (el nombre PostScript) caía en silencio a Arial Bold. Y libass escala el `Fontsize` del ASS como el alto `winAscent+winDescent`, no como el em: el ASS lleva `font_size * size_ratio` (Poppins 1.762, Anton 1.733, Archivo Black 1.347) para que el em sea `font_size` px como en el canvas. Para verificar qué fuente usó libass: `ffmpeg -v verbose … | grep fontselect`. El `.ass` lleva `YCbCr Matrix: None`: libass convierte los colores con la matriz y el rango del propio video. Sin esa línea asume BT.601, y sobre un reel bt709 el amarillo salía anaranjado. `GET /api/subtitles/styles` expone fuentes, paleta y rangos al preview (una sola fuente de verdad).

Device automático: intenta cuda/float16, cae a cpu/int8 (en Mac es CPU). La 1ª vez **descarga el modelo** (~1.6 GB para turbo).

### 5. insert — `/api/insert`

Inserta un mini-clip completo (corte seco) en cada marcador del timeline del original. Automatiza "partir el timeline y duplicar el asset" de CapCut.

Tres modos, y **varios mini-clips**: cada uno trae SUS marcadores, así se reparten distintas caídas a lo largo del video (clip A en 0:05 y 0:30, clip B en 1:10).

| Parámetro | Tipo | Default | Notas |
|---|---|---|---|
| `clips` | JSON | — | `[{"markers":[1.5,12],"vertical":true}, …]` — uno por mini-clip subido. Máx 10 clips y 50 marcadores en total. Se acepta el formato viejo (`markers` JSON/CSV + `clip_vertical`) |
| `mode` | string | "full" | `full` (corte seco) / `progressive` / `overlay` |
| `slice_durations` | list[float] | auto | **progressive**: cuánto dura cada adelanto. Auto = reparto parejo |
| `reveal_enabled` / `reveal_at` / `reveal_speed` | bool / float / float | true / final / 1.0 | **progressive**: mostrar el mini-clip entero al final o en un segundo dado, con opción de acelerarlo (1.0 / 1.2 / 1.3 / 1.5 / 2.0) |
| `clip_audio` | string | "clip" | audio del mini-clip o `mute` |
| `overlay_position` / `overlay_size` | string / int | center / 70 | **overlay**: dónde y qué tamaño (% del ancho) va el PiP |

- **full:** inserta cada mini-clip completo en sus marcadores.
- **progressive** ("caída progresiva"): trocea UN mini-clip y mete el pedazo siguiente en cada marcador (0–2 s, 2–4 s, …); al final muestra el clip completo.
- **overlay:** el original se **congela** y el mini-clip va superpuesto como PiP.

**Inputs:** `video` (original) + `clips` (uno o varios mini-clips). Audio y video se cortan juntos. Se arma con el `concat` filter; solo se normaliza lo invisible que concat exige (fps, SAR, pixfmt, audio). **Valida que las resoluciones coincidan** (si no, error claro apuntando a Vertical), salvo que el clip venga marcado `vertical`, que lo convierte solo. **UI:** timeline clicable con marcadores por click / punto actual / `mm:ss`.

### 6. downloader — `/api/downloader`

Descarga un asset desde un link (YouTube, TikTok, Instagram, …) con yt-dlp. Reemplaza el flujo manual de bajar a teléfono + Telegram.

| Parámetro | Tipo | Notas |
|---|---|---|
| `url` | string | http(s) |
| `format` | string | `audio` (mp3) o `video` (mp4) |
| `quality` | string | audio: 320/192/128 kbps · video: max/1080/720/480 |
| `cookies_browser` | string | `""` (sin cookies), un navegador, o `file` (el cookies.txt subido) |

**Sin upload** (es un link). Usa el FFmpeg configurado para extraer audio / hacer merge. Captura el título para nombrar la descarga. YouTube y TikTok públicos son sólidos; Instagram es best-effort.

**Cookies (bot-check de YouTube).** En Windows los navegadores basados en Chrome cifran sus cookies (App-Bound Encryption) y yt-dlp no puede leerlas: por eso existe el `cookies.txt` subido a mano (formato Netscape). Al guardarlo se **filtran** las cookies que no son de YouTube/Google y queda en `cookies/`, **fuera de git** — es material sensible, jamás se commitea ni se copia a otra máquina.

```
GET    /api/downloader/cookies   → { exists, mtime, size }
POST   /api/downloader/cookies   → sube/reemplaza el cookies.txt (campo `cookies`)
DELETE /api/downloader/cookies   → lo borra
```

Para que YouTube entregue formatos de video hace falta además un **runtime de JS** (node/deno) y el paquete `yt-dlp-ejs`; sin eso solo ofrece imágenes.

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
- `convert_vertical` (default ON) se apaga cuando el clip ya viene 9:16.
- `skip_review` ("No revisar"): sin pausa de edición, el backend puede transcribir antes y quemar los subs **en la misma pasada visual** (un encode en vez de dos).
- **Retime al principio del grafo** (`_retime_head`). Con speed match y sf < 1, `setpts` + `fps=source_fps` van ANTES de repartir el clip, así los frames que sobran se tiran antes de los filtros caros. Antes se procesaban todos y la salida tiraba el 45 % (clip típico) o el 92 % (captura de 9 min). Detalles:
  - Además los subtítulos caen en el frame exacto; antes llegaban 1–2 frames tarde.
  - Solo en clips de frame rate constante (`_decimates`): en un VFR, `fps` usaría el nominal y DUPLICARÍA frames.
  - Con sf ≥ 1 va solo `setpts` (la salida queda VFR, como siempre).
  - **NVDEC:** si además hay NVIDIA y el clip es h264/hevc/av1/vp9 8 bits 4:2:0, se decodifica en la GPU, el descarte pasa en la VRAM y bajan solo los frames que quedan (`hwdownload`). Si esa corrida falla, se repite entera en la CPU; la salida es idéntica (mismo md5).
  - Medido: camino rápido 46.7 → 14.5 s; pasada visual de una captura de 9 min 287 → 20 s.
- `dynamic_bg` / `dynamic_markers` siguen en el schema pero **no tienen UI**: esa función se trabaja en el módulo Insert, que soporta varios clips. Sin la UI quedan en False.
- Con subtítulos es **dos fases**: `POST /process` produce el video base y lo transcribe (status done + `segments`); `POST /finish` quema los subs editados → reel final. Sin subs, es un solo paso como antes.
- **UI:** el panel de ajustes son bloques colapsables (`<details class="rx-block">`: audio, video, hud, text, subs, advanced) que recuerdan si los dejaste abiertos y muestran un resumen de su estado aunque estén plegados.

### 9. hook_reel — `/api/hook-reel` (pipeline, dos fases con subs)

"Reel Frase": dos videos encadenados con música de fondo.

```
Video 1 (avatar, trae la voz) ──→ segmento 1 (dura lo que dura el video)
Video 2 (clip de cierre)      ──→ segmento 2 (dura `seg2_duration`; el clip se
                                   acelera o frena con setpts para cubrirla)
Música (link o archivo)       ──→ baja mientras habla el avatar, sube a tope en
                                   el corte. Se recorta al total; NO se acelera.
```

| Parámetro | Tipo | Default | Rango |
|---|---|---|---|
| `seg2_duration` | float | 6.0 | 0.5–300 (s) |
| `music_start` | float | 0 | 0–3600 (desde qué segundo de la música arrancar) |
| `music_low_volume` | int | 25 | 0–100 (%, mientras habla el avatar) |
| `music_full_volume` | int | 100 | 0–150 (%, después del corte) |
| `ramp` | float | 0.4 | 0–2 (s de la rampa de volumen; 0 = golpe seco) |
| `seg1_vertical` / `seg2_vertical` | bool | false / true | convertir cada clip a 9:16 |
| `add_subtitles` | bool | true | karaoke sobre el segmento 1; `position_y` default 1601 |

**Inputs:** `video1` + `video2` + música: `music` (archivo) o un link, según `audio_source` (`url` \| `file`). Reusa `VerticalConvertParams` y `SubtitlesParams`. Con subtítulos es **dos fases** (`/process` + `/finish`), igual que reel_express.

### 10. competitivo — `/api/competitivo` (pipeline, dos fases, ancla automática)

Highlights de una serie de Worlds **solo con kills** (los objetivos sin pelea nunca dan clip), sacados del directo de **Ibai en YouTube** (`@Ibai_Extra`, 1080p60) con un layout vertical propio. Entrega `resumen.mp4` (≤ 60 s por defecto) y cada clip suelto en una **carpeta persistente** (`config.COMPETITIVO_FOLDER`, por defecto `~/Videos/ReelForge/Competitivo/<fecha> <A> vs <B>/`, que "Limpiar archivos" no toca), con un `info.json`.

```
feed lolesports (window, ~5 frames/s) ─kills.py─→ peleas + puntaje ─→ selección (presupuesto)
VOD de Ibai ─fuente.py─→ tramos HLS ─layout.py─→ ancla por partida + ajuste por clip ─→ render 1080x1920 ─→ resumen
```

| Archivo | Qué hace |
|---|---|
| `engine.py` | Motor del prototipo `esports_highlights`, **copiado sin cambios** (eventos, fmt_hms, dataclasses) |
| `kills.py` | Baja el feed (urllib, ventanas en paralelo, con las K/D/A por jugador del propio `window`) y arma las peleas: kills a <15 s = pelea; ventanas a <6 s se fusionan; filtro de kills mínimas; puntaje del brief + **oro que movió la pelea** (+1 c/100, máx. 20, pedido de Axl); selección por puntaje sin tope con presupuesto y tope por partida; la pelea que cierra la serie entra siempre; orden cronológico. CLI: `python -m modules.competitivo.kills <gameId…>` |
| `layout.py` | Layout, análisis del tramo (cámara del set, repeticiones, contador de kills) y comando de render |
| `fuente.py` | yt-dlp con la config del Downloader (cookies, runtimes JS, reintentos): hora de inicio del directo y tramos HLS |
| `intro.py` | Portada (vertical, `portada.jpg`) y placa (horizontal) con plantilla fija; voz de la intro por URL; segmento "aparte" |
| `schema.py` / `processor.py` / `routes.py` | Contrato estándar de dos fases |

**Layout** (1080x1920, de arriba a abajo): barra de la transmisión (cámaras de jugadores + tabla de stats; en peleas, retratos con vida) con aire y esquinas redondeadas → gameplay con zoom (default **1.3×**, centrado donde el observador pone la acción, sin desenfoque) → marcador oficial → cámara del set de Ibai al 70 % del ancho. Con zoom < ~1.6 el gameplay no llena su alto (la zona limpia de la fuente mide 712 px): la barra baja, marcador y cámara suben, y lo que sobra queda arriba y abajo de todo (las franjas que tapa la UI de TikTok). Watermark opaco "parado" en la esquina inferior derecha (se recorta su margen transparente). Zonas medidas en 169 frames (ver docstring de `layout.py`); la **y de la cámara del set cambia por partida** y se detecta por las aristas del recuadro. Tope de 16 Mbps (con cq 18 salía a 34 Mbps).

**Sincronía (lo más importante).** La fórmula del brief (`vod = utc − inicio del directo + 38 s`) es solo el punto de partida: el desfase real fue **+7.5 s en la G1 y −5 s en la G2/G3 del mismo día**. Se mide solo: `cambios_kills()` ve en qué segundo del video sube el contador de kills del marcador oficial (cada lado por separado: el azul siempre a la izquierda) y `estimar_desfase()` alinea ese patrón con las kills del feed (±0.2 s). Fase 1: ancla por partida con el tramo de su pelea más grande (confianza **alta** con 2+ kills alineadas). Fase 2: ajuste fino de cada clip con sus propias kills. Medido en KC-C9 G1-G3: todas las kills dentro de sus clips, ajuste fino < 0.1 s. Las **repeticiones** (sin marcador) se detectan y el clip queda en el tramo en vivo que contiene la kill.

| Parámetro (fase 1) | Tipo | Default | Notas |
|---|---|---|---|
| `game_ids` | str | — | gameId de 15–20 dígitos, uno por partida **en orden** (la última es la decisiva) |
| `vod_url` | str | — | VOD de IbaiExtra (necesita `release_timestamp`) |
| `modo` | str | revisar | `revisar` (pausa con todo prellenado) / `auto` (sigue solo si todas las anclas son de confianza alta) |
| `duracion_max_s` / `max_por_partida` / `min_kills` | int | 60 / 3 / 2 | 20–180 / 1–5 / 1–5 |
| `incluir_picks` / `incluir_primera_sangre` | bool | false | |
| `pre_s` / `post_s` | float | 8 / 3 | antes del frame anterior a la 1ª kill / después de la última |
| `zoom` / `color` | float / str | 1.3 / vivo | 0.8–2.2 / off·vivo·cine |
| `watermark` / `marca_alto` | str / int | "" / 190 | PNG de `assets/watermarks` / alto en px |
| `intro_modo` | str | encima | `encima` (placa sobre el clip 1) · `aparte` (segmento propio) · `off` |
| `intro_audio_url` | str | "" | link http(s) del mp3 de la voz; vacío → sin intro |
| `mapa` | int | 1 | 1–5 ("MAPA N" en la portada; la carpeta lleva "· Mapa N" si el trabajo es de un mapa) |
| `fase` | str | "" | máx. 40, p. ej. "PLAY-IN · DÍA 2" |
| `equipo_azul` / `equipo_rojo` | str | códigos del feed | máx. 30; nombre completo para la portada (según el lado de ESE mapa) |
| `logo_azul_url` / `logo_rojo_url` | str | "" | http(s) o vacío (se cachean en `cache/competitivo/logos/`) |
| `serie_marcador` | str | "" | máx. 12, p. ej. "1-0" |
| `marca_4xl` | bool | false | el `watermark`, chico y al 40 % en la portada |
| `intro_volumen_juego` | int | 17 | 0–100: % del audio del juego mientras habla la voz (modo `encima`) |

Todos estos campos se pueden mandar también en `/finish` (lo que no llega se hereda de la fase 1; el modo `auto` los pasa con `FinishParams.desde()`).

**Intro con voz y portada (`intro.py`).** La **voz no la genera ReelForge**: la genera Claude con Fish Audio (MCP) y llega como link de un mp3 en `intro_audio_url` (no hay API de Fish Audio ni claves en la app). Se baja una vez, en la fase 1 (un link roto falla antes de bajar el VOD). Se le recorta el silencio final de la TTS (`silencedetect`) y se normaliza en dos pasadas a **−11 LUFS / −1.5 dBTP** (`loudnorm` lineal con lo medido; sin eso la voz quedaba casi al nivel del juego y 9–11 LU por debajo de los clips). `D` = fin de la voz + 0.4 s, **redondeado a frames enteros de 60 fps**; si la voz dura más de 8 s el job falla con un error claro. Lo visual es una **plantilla fija** (MAPA N en Anton, la fase en el lima de la app, logos VS, nombres, serie y fecha): la portada vertical 1080x1920 (`portada.jpg`, se genera **siempre**, en la carpeta de la serie) y la misma plantilla en horizontal como placa (864x560, esquinas redondeadas, borde blanco y sombra como la placa del HUD). Fallbacks, nunca falla: sin `assets/competitivo/fondo_portada.png` → degradado oscuro; sin logo o si falla la descarga → el código del equipo en un círculo; sin nombres → los códigos del feed; nombres largos → se achican o se parten en dos renglones (ancho real con `fonts.text_width`). `assets/competitivo/sting.mp3`, si existe, suena al 35 % debajo de la voz y se apaga con un fade de 0.3 s al llegar a `D`. El limitador (`alimiter=limit=0.89:level=0`) no sube la ganancia: solo frena picos.
- **`encima`** (default): dentro del encode del clip 1. La placa entra con un fade de 0.2 s y sale con otro de 0.3 s durante `[0, D]`; el juego baja a `intro_volumen_juego` (17 % ≈ −15 dB; misma expresión `volume=…:eval=frame` que hook_reel) y vuelve al 100 % con una rampa de 0.4 s; `amix` con la voz (+ sting) y un limitador. El clip 1 se renderiza **dos veces**: `clip_01.mp4` limpio y la versión con intro solo para el resumen. Cae solo a `aparte` (y lo anota con el motivo) si el clip 1 dura menos de `D + 2 s` o si su primera kill cae antes de `D + 0.5 s` (la placa la taparía).
- **`aparte`**: `intro.mp4` propio (la portada con zoom lento 1.00→1.06 y fade-in), primero en el resumen. Sale con **los mismos parámetros que los clips** (60 fps, encoder, tope de 16 Mbps, AAC 160k 48 kHz estéreo, yuv420p, SAR 1:1, **bt709/tv**): el resumen se une con `concat -c copy` y hereda la cabecera de video del primer archivo. El **audio sí se re-encodea** al unir (`layout.build_concat`: `aresample=async=1` + limitador → AAC): con `-c copy` el relleno AAC de cada archivo corría el audio de cada unión y se acumulaba (+59/+94/+117 ms al 3er clip); ahora queda fijo (~20–30 ms, lo mismo que mide un clip solo). Por eso los clips también salen etiquetados bt709/tv explícitamente (`layout.SALIDA`). Lo verifica un test con ffprobe.
- **`off`**: sin intro; la portada se genera igual.

**API (form-urlencoded; también para que Claude la opere sola con el server corriendo):**

```
POST /api/competitivo/process    game_ids, vod_url, [modo, reglas, render…]  → 202 {job_id}
GET  /api/competitivo/status/<id>  → {status, progress, stage, error, finished, aviso,
        vod, partidas:[{n, game_id, azul, rojo, ancla_s, ancla_propuesta_s, desfase_s,
        confianza, coincidencias, esperadas, referencia, frame}],
        clips:[{id "partida-n", titulo, puntaje, bruto, desglose, kills, duracion,
        elegible, seleccionada, forzada, …}], seleccion:[ids], resultado, portada, intro}
POST /api/competitivo/finish     job_id, [anchors='{"gameId": "1:22:15"|4935}'],
        [clips='["2-9","3-19"]'], [zoom, color, watermark, marca_alto,
        intro_modo, intro_audio_url, intro_volumen_juego, mapa, fase, equipo_*, logo_*,
        serie_marcador, marca_4xl]  → 202
GET  /api/competitivo/download/<id>      → resumen.mp4   (·/<n> → clip n)
GET  /api/competitivo/frame/<id>/<n>     → JPG de control del ancla de la partida n
GET  /api/competitivo/portada/<id>       → portada.jpg (después de finish)
GET  /api/competitivo/resumenes          → resúmenes guardados (carpeta persistente)
GET  /api/competitivo/resumenes/<carpeta>/<archivo>
```

Fase 1 termina en `status=done`, `finished=false`, `stage="esperando_ancla"`; fase 2 en `finished=true` con `resultado.carpeta`. Al terminar, `status` y `resultado` traen `portada` ("portada.jpg") e `intro: {modo_usado, duracion_s, motivo}`; `info.json` además `intro_modo_usado`. **Como lo opera Claude:** un `/process` **por mapa** (`game_ids=<uno>`) con `modo=auto`, `intro_audio_url` (la voz de Fish Audio), `mapa`, `fase`, `equipo_*`, `logo_*`, `serie_marcador`; poll hasta `finished=true`; bajar `resumen.mp4` y `portada.jpg`. Para operarlo de punta a punta: `process` con `modo=auto` → poll de `status` hasta `finished=true` (si queda en `esperando_ancla` con `aviso`, revisar el `frame` de esa partida, corregir con `anchors` y llamar `finish`). Para elegir otras peleas: tomar ids de `clips` y pasarlos en `finish`. Tiempos medidos: 3 partidas y 3 clips, fase 1 ~64 s y fase 2 ~54 s; un mapa con intro (3 clips), ~110–115 s en total. Las sesiones viven en memoria: si el server se reinicia (el modo debug recarga al cambiar código), hay que repetir la fase 1. Tests: `tests/competitivo/` (fixture real de KC-C9 G1).

### assets — blueprint de assets persistentes

No procesa video: gestiona la **librería de watermarks** y la **fuente**.

```
GET    /api/watermarks            → lista de watermarks
POST   /api/watermarks            → subir un PNG
DELETE /api/watermarks/<name>     → borrar
GET    /assets/watermarks/<name>  → servir el PNG (galería / preview)
GET    /api/font                  → ¿hay fuente cargada? (la de por defecto)
GET    /assets/font               → servir la fuente por defecto (preview del watermark)
GET    /assets/fonts/<name>       → servir cualquier fuente de assets/fonts (preview de subtítulos)
```

### library — librería de clips locales

No procesa video: lista la **carpeta local de capturas** (`config.CLIPS_LIBRARY_FOLDER`, por defecto `~/Videos/Overwolf/Insights Capture`; override con la env var `REELFORGE_CLIPS_FOLDER`) para elegir un clip **sin subirlo**: las grabaciones de Overwolf pesan varios GB y superan el límite de upload. Vertical y Reel Express aceptan `library_clip=<nombre>` en lugar del archivo y procesan el original **in situ** (Reel Express lo marca `keep_clip` para no borrarlo con los intermedios).

```
GET /api/library/clips                → { folder, available, clips:[{name,size,mtime,duration,ok,url,thumb}] } (más nuevos primero)
GET /api/library/clips/<name>/file    → el video (con Range, para el <video> del preview)
GET /api/library/clips/<name>/thumb   → miniatura JPG (cache en cache/thumbs/; 3 s o 25 % si dura >5 min)
```
`ok=false` = ffprobe no lo pudo leer (grabación cortada sin `moov`): se lista como dañado, no elegible. **UI:** botón "Elegir de mis capturas" en la drop zone → galería modal (`openClipLibrary` en `shared.js`); el clip elegido es `{name, url, fromLibrary:true}` y `appendClip()` manda `library_clip` en vez del archivo.

### timeline — historial del proyecto

No procesa nada ni toca archivos: corre `git log` sobre el propio repo y devuelve los commits **convencionales** (`feat|fix|perf|refactor|docs|chore`) ya clasificados, para la vista **Historial**. Lo que no matchea el prefijo (assets sueltos, merges) queda afuera: no es un hito. El cuerpo del mensaje se desarma en párrafos y viñetas (se reunen las líneas cortadas a 72 columnas y se descartan los trailers). Cache por HEAD: el log solo se relee cuando hay un commit nuevo.

```
GET /api/timeline → { available, entries:[{hash,date,type,kind,scopes,title,detail}], stats }
```
`kind` ∈ `feat | fix | perf | otro` (lo que la UI colorea) y `detail` es una lista de bloques `{type:"p",text}` / `{type:"ul",items}`. Sin git (o sin repo) responde `available:false` con el motivo, no un error. **UI:** KPIs, chips de filtro por tipo, buscador (título, scope, hash y cuerpo) y línea del tiempo agrupada por mes. Al leerse de git se mantiene sola: cada commit nuevo aparece sin tocar código.

### Endpoints (contrato común)

```
POST /api/<modulo>/process       → 202 { job_id }   (corre en thread)
GET  /api/<modulo>/status/<id>   → { status, progress, error, [stage], [segments], [title] }
GET  /api/<modulo>/download/<id> → archivo (mimetype correcto, as_attachment)
```
`status` ∈ `pending | processing | done | error`. **Excepciones de dos fases:** `subtitles` usa `/transcribe` + `/render`; `reel_express`, `hook_reel` y `competitivo` usan `/process` + `/finish`. Global: `POST /api/cleanup` vacía las carpetas temporales.

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
probe_video_stream(path) -> dict | None   # codec, pix_fmt, fps (nominal), avg_fps
cuda_decode_supported(stream) -> bool     # ¿NVDEC? NVIDIA + h264/hevc/av1/vp9 8 bits 4:2:0
CUDA_DECODE_FLAGS                         # -hwaccel cuda -hwaccel_output_format cuda: el
                                          # grafo tiene que bajar con hwdownload,format=nv12

video_encode_flags(crf=18) -> list[str]   # flags del encoder: h264_nvenc si la
                                          # GPU está (probe cacheado), si no libx264
encoder_info() -> str                     # "GPU NVENC" / "CPU" (lo muestra Info)
atempo_chain(tempo) -> str                # cadena de atempo para tempos > 2.0
capture(command) -> bytes                 # corre FFmpeg y devuelve su stdout (frames en
                                          # rawvideo para analizar con numpy)
```

**Todo módulo que encodea usa `video_encode_flags()`**: es lo que hace que la GPU
se use en todos lados sin repetir la detección.

Si `total_duration` es None, se intenta sacar de la línea `Duration:` que imprime FFmpeg.

La salida de FFmpeg y ffprobe se lee como **UTF-8** (`errors="replace"`). Con la codificación de Windows (cp1252), una "Á" en una ruta o en un tag ID3 tiraba UnicodeDecodeError: el job fallaba y FFmpeg quedaba huérfano, y en los probes la duración salía None en silencio, así que no había speed match. Si la lectura se corta por una excepción, `run()` mata a FFmpeg.

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
COOKIES_FOLDER / COOKIES_FILE      # cookies.txt de yt-dlp (sensible, gitignored)
CLIPS_LIBRARY_FOLDER               # carpeta de capturas (env REELFORGE_CLIPS_FOLDER)
THUMBS_FOLDER                      # cache/thumbs/ (miniaturas de la librería)
COMPETITIVO_FOLDER                 # resúmenes de competitivo, PERSISTENTE (env REELFORGE_COMPETITIVO_FOLDER)
MAX_UPLOAD_SIZE_MB = 2000
FFMPEG_PATH   = os.environ.get("FFMPEG_PATH")  or _resolve_ffmpeg()
FFPROBE_PATH  = os.environ.get("FFPROBE_PATH") or _resolve_ffprobe()
ALLOWED_EXTENSIONS        = {"mp4","mov","mkv","avi"}
ALLOWED_AUDIO_EXTENSIONS  = {"mp3","wav","m4a","aac","ogg"} | ALLOWED_EXTENSIONS
ALLOWED_WATERMARK_EXTENSIONS = {"png"}
ALLOWED_FONT_EXTENSIONS      = {"ttf","otf"}
OUTPUT_WIDTH, OUTPUT_HEIGHT  = 1080, 1920
DEFAULT_FONT_FILE = "Poppins-Bold.ttf"   # explícita: con varias fuentes, la "primera" era Anton
resolve_font_path()  # DEFAULT_FONT_FILE (o la primera si falta); la usa el watermark
```

En Windows `FFMPEG_PATH` puede necesitar el path completo; o setear las env vars.

---

## app.py

- Registra los 13 blueprints + `/` (index) + `POST /api/cleanup`.
- **La API siempre responde JSON:** `errorhandler` para `RequestEntityTooLarge` (413), `HTTPException` y `Exception` (500) — sin esto el frontend rompía al parsear el HTML de error de Flask.
- `SEND_FILE_MAX_AGE_DEFAULT = 0`: no cachea estáticos en dev, así el browser toma el JS/CSS recién editado.

---

## UI

`index.html` es un **app shell**: una **sidebar dividida en secciones** (`.nav-section`: Automático / Módulos / Tools Specific videos / Tools / Sistema) con un nav item por módulo, y un `<section class="module-view">` por módulo (parciales con `{% include %}`). `app.js` muestra una vista a la vez (busca `.nav-item`). La vista por defecto es **Reel Express**.

Cada módulo trae su propio JS y usa `runJob(apiBase, formData, {onProgress, onDone, onError})` de `shared.js` (POST + polling). Los flujos de dos fases (subtitles, reel_express, hook_reel) usan un poller propio porque el status trae `segments`. `shared.js` además concentra lo compartido entre módulos: el estilo de subtítulos (`SubStyle`: llena el parcial `templates/modules/_sub_style.html`, que Subtítulos, Reel Express y Reel Frase incluyen con prefijos `sub-`/`rx-sub-`/`hk-sub-`; arma los params; recuerda el estilo en localStorage y lo sincroniza entre módulos; dibuja la muestra animada apoyada en la misma línea base que libass), el preview de la placa del HUD en canvas (`drawHudPlate`), el selector de archivos (`openFilePicker`) y la galería de la librería de clips (`openClipLibrary` / `appendClip`). Las vistas **Info** e **Historial** no procesan nada: solo leen su endpoint y pintan. El **editor de segmentos** (`SubtitleEditor` en `subtitle_editor.js`) es compartido por subtitles y reel_express. Sin librerías frontend externas (solo Vanilla JS).

---

## Dependencias del sistema

- **FFmpeg** y **ffprobe** en el PATH (o resueltos por `config.py`). ffprobe es necesario para el speed match, resoluciones y duración exacta; sin él, varias features degradan en vez de crashear.
- **yt-dlp** (pip) para el downloader. Se actualiza seguido: `pip install -U yt-dlp`.
- **fonttools** (pip) para leer familia y métricas de las fuentes de subtítulos.
- **faster-whisper** (pip) para subtitles. La 1ª transcripción descarga el modelo (~1.6 GB turbo). En Mac corre en **CPU** (CTranslate2 no usa Metal/GPU); modelos chicos (small/medium) son más livianos.

---

## Cómo correrlo

```bash
pip install -r requirements.txt   # Flask, yt-dlp, faster-whisper
ffmpeg -version && ffprobe -version   # deben existir

python3 app.py                    # ojo: python3, no python (en macOS)
# http://localhost:5001           # puerto 5001 (el 5000 lo ocupa AirPlay en Mac)

python -m unittest discover -s tests -t .   # tests (sin internet)
```

**En Windows** hay dos .bat pensados para doble click, y son la vía recomendada
para instalar en una máquina nueva:

- `setup.bat` — crea `venv\` e instala `requirements.txt`; después chequea
  FFmpeg, ffprobe, Node y Git y ofrece instalar con winget los que falten
  (Python incluido). Idempotente: se puede correr de nuevo para actualizar.
- `Iniciar ReelForge.bat` — levanta el server y abre el browser cuando el
  puerto contesta. La ventana ES el server. Detecta si ya hay una instancia.

El venv **no se copia entre máquinas** (rutas absolutas adentro): se crea en cada
una. Para llevar el proyecto, `git clone` o `git archive` (nunca un zip de la
carpeta entera: arrastraría `venv/` y, peor, `cookies/`).

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
- **downloader:** preview de título/miniatura antes de bajar, preferir H.264 para máxima compatibilidad.
- **subtitles:** wrappers que usen la GPU del Mac (mlx-whisper / whisper.cpp) si la CPU queda corta.
- **Endurecimiento de dependencias:** pin + hashes, `pip-audit`, venv.
- **Limpieza automática** de temporales (TTL), en vez de solo el botón manual.
