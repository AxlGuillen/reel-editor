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
├── requirements.txt            ← Flask, yt-dlp, faster-whisper, librosa, scenedetect
├── setup.bat                   ← Windows: crea el venv e instala todo (doble click)
├── Iniciar ReelForge.bat       ← Windows: levanta el server y abre el browser
│
├── assets/                     ← Assets PERSISTENTES (versionados, NO los toca "Limpiar archivos")
│   ├── watermarks/             ← librería de imágenes para el overlay: PNG o WebP (con transparencia) o JPG (sin ella)
│   ├── competitivo/            ← fondo_portada.png y sting.mp3 opcionales (intro del módulo competitivo)
│   ├── players/                ← fotos de los jugadores para las tarjetas (<Nombre>.png|jpg; opcional)
│   ├── fonts/                  ← fuentes de subtítulos (Poppins Bold/ExtraBold/Black, Anton,
│   │                             Archivo Black, Montserrat ExtraBold/Black); SOLO archivos de fuente
│   ├── font_licenses/          ← licencias OFL de las fuentes (fuera de fonts/ a propósito)
│   ├── luts/                   ← LUTs .cube para la gradación de los edits (opcionales)
│   ├── overlays/               ← light leaks, brasas, texturas para el efecto `overlay` (los pone Axl)
│   └── vocab.json              ← correcciones de nombres en los subtítulos de Ibai (mal oído → correcto)
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
│   ├── competitivo/            ← PIPELINE: highlights de Worlds (solo kills) desde el VOD de Ibai;
│   │                             edit.py = modo edit (las peleas al ritmo de una canción)
│   ├── beatmap/                ← Ritmo de una canción: beats, compases, secciones, drop, letra
│   ├── motion/                 ← Mapa de movimiento de un tramo: golpes, barridos, paneos, calma, cortes
│   ├── editing/                ← MOTOR de los edits: EDL → render en dos pasadas (sin routes de proceso)
│   ├── anime_edit/             ← PIPELINE: pelea de anime + canción → variantes de edit (2 fases)
│   ├── montage/                ← Montaje: capas animadas con alfa sobre un video, por palabra o segundo
│   ├── studio/                 ← Estudio: las láminas de un deck de Presentations sobre el video, en vivo
│   ├── tf_explained/           ← MOTOR de «La TF explicada»: expediente del feed, tramos del VOD, hojas, layout de análisis, montaje
│   ├── research/               ← investigación de referencias (TikTok sin descargar: perfiles, vistas, menciones)
│   └── pasarela/               ← PIPELINE: video narrado de punta a punta (banda del deck + gameplay vertical
│                                 + clips), operado por Claude con JSON; highlights de Insights Capture
│
│   Cada módulo que procesa video tiene los mismos tres archivos:
│       routes.py      ← Blueprint Flask con sus endpoints
│       processor.py   ← Lógica del módulo (FFmpeg / yt-dlp / whisper)
│       schema.py      ← Parámetros y validación del módulo
│   assets, library y timeline son solo routes.py: no procesan nada. editing es
│   un motor (edl, timing, effects, titles, render) que usan competitivo y anime_edit.
│   studio tiene los tres archivos pero no encodea: su render es el de montage.
│
├── cookies/                    ← cookies.txt de yt-dlp. SENSIBLE: gitignored
├── cache/thumbs/               ← miniaturas de la librería de clips (temporal)
├── cache/beatmaps/             ← análisis de canciones cacheados por huella (temporal)
├── docs/edits.md               ← beatmap, motor, modo edit, anime_edit, montage y el estudio en detalle
├── docs/efectos.md             ← el banco de efectos del motor (qué hace cada uno, parámetros, lo medido)
├── docs/contratos/             ← montaje.schema.json: el contrato del montaje.json del estudio
│
├── tests/                      ← unittest (sin pytest): python -m unittest discover -s tests -t .
│   ├── synthetic.py            ← canciones y videos sintéticos (tempo y cortes conocidos)
│   ├── competitivo/            ← detección de peleas (fixture real), layout, sincronía, validación, edit
│   └── beatmap/ editing/ anime_edit/ montage/ studio/ motion/ pasarela/ (motion/pairs.json: la verdad de las 10 parejas)
│
├── core/
│   ├── ffmpeg_runner.py        ← Único lugar que ejecuta FFmpeg + helpers ffprobe
│   ├── job_manager.py          ← Estado de los jobs (en memoria, thread-safe)
│   ├── scheduler.py            ← Turnos por recurso (descargas de Twitch, Whisper, análisis, NVENC)
│   ├── external.py             ← Único lugar que ejecuta RIFE y Real-ESRGAN (opcionales)
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
| `watermark` | string | "" | nombre de la imagen (PNG, WebP o JPG) en `assets/watermarks/`; un JPG no tiene transparencia, sale con su fondo |
| `watermark_x` | string | "center" | left / center / right |
| `watermark_size` | int | 50 | 10–200 (% del ancho; >100 desborda el cuadro) |
| `watermark_y` | int | 100 | −50–200 (% del alto; permite desbordar el cuadro) |

**Input:** `video`. Requiere texto **o** watermark. Usa la fuente de `assets/fonts/`. **Escaping:** rutas relativas a `BASE_DIR` + `cwd=BASE_DIR` (el `:` del drive en Windows rompe el parser de filtros). **UI:** preview en canvas + galería de watermarks (CRUD).

### 4. subtitles — `/api/subtitles` (dos fases)

Transcribe el audio con **faster-whisper** y quema subtítulos estilo **karaoke** (palabra activa coloreada) con libass. Flujo:
- `POST /transcribe` → sube el video (o `job_id` de un video que ya está en el server, como la sesión del Estudio), transcribe en thread; el status, al terminar, trae los `segments` (con palabras + timestamps). `initial_prompt` opcional: la pista de nombres para Whisper (nunca `hotwords`: en el video de LeBlanc hicieron que Whisper repitiera frases en bucle).
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
| `sub_layout` | string | line | `line` (en línea) / `stairs` (escalera: un renglón por palabra, cada uno más abajo y con el centro 0.8 em más a la derecha) / `mixed` (variada: ~2 de cada 5 frases en escalera) / `invert` (invertido al centro, ver abajo) |
| `sub_sizes` | string | same | `same` / `grow` (crecientes: 0.85→1.4× hasta la última palabra con 3+ letras, que queda grande y a color) / `random` (variados: una palabra de 3+ letras a 1.45× y a color) |
| `sub_neon` | string | off | `off` / `soft` (una sombra leve) / `strong`: mientras se dice, la palabra brilla con SU color. Capa debajo del texto con la misma palabra sin relleno, `\bord` grueso del color y `\blur`; entra con `\fad`. En línea va como una línea igual con todo invisible salvo la activa (medido: el contorno gigante no corre la alineación, 0 px) |
| `sub_inv_size` / `sub_inv_y` | int | 150 / 960 | 60–320 (em en px) / 200–1720 (centro del bloque): solo `invert` |
| `sub_inv_direction` | string | horizontal | `horizontal` / `vertical` (de abajo hacia arriba) / `diagonal` (8°) / `random` (cambia por grupo): solo `invert` |
| `sub_inv_words` | string | 1 | `1` / `2` / `3` / `mixed` (la mitad de las veces una, si no 2 o 3): palabras a la vez, van apareciendo apiladas; solo `invert` |
| `language` | string | es | auto/es/en/pt/fr/de/it/ja/ko/zh |
| `model` | string | large-v3-turbo | tiny…large-v3 |

**Invertido al centro (`sub_layout=invert`).** Sale del edit de referencia de Levi (@med_uchiha): la palabra es **blanca con modo DIFERENCIA**, así que cada letra muestra el video en negativo (azul sobre piel, blanco sobre sombras: invirtiendo el cuadro se ve la escena continuar dentro de las letras). Una palabra (o 2–3 que van apareciendo apiladas) al centro, en mayúsculas, sin contorno; cada una entra con un fundido de 70 ms y un rebote 86 % → 104 % → 100 % (90/160 ms) y sale con 40 ms. Queda hasta la siguiente si la pausa es de ≤ 1 s (sin cuadros vacíos, como la referencia); si no, 0.5 s después de decirse. La dirección y las palabras por grupo "aleatorias/mezcladas" salen del mismo hash fijo (`_pick`) que el preview. libass no hace mezclas: `_build_invert_ass` escribe la MÁSCARA (blanco sobre negro, con `INV_MARKER` en `[Script Info]`) y `ass_filter()`, al ver el marcador, devuelve una cadena de una entrada y una salida (`format=gbrp,split` → el `.ass` sobre una copia negra del cuadro → `blend=all_mode=difference` → `yuv420p`). Por eso funciona sin cambios en Subtítulos, Reel Express (también en la pasada fusionada de "No revisar"), Reel Frase y Montaje. Medido: dentro de las letras 255 − video (mediana 1.7/255), fuera < 1/255 de diferencia y las etiquetas bt709/tv intactas; cuesta ~2 s por cada 10 s de 1080x1920. El preview lo replica con `globalCompositeOperation = "difference"`. Fuente sugerida: Montserrat ExtraBold (la más parecida a la referencia).

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

Highlights de una serie de Worlds **solo con kills** (los objetivos sin pelea nunca dan clip), sacados del directo de **Ibai en YouTube** (`@Ibai_Extra`, 1080p60) **o de su VOD de Twitch** (mismo layout) con un layout vertical propio. Entrega `resumen.mp4` (por defecto **4 s por minuto de partida**, entre 90 y 180 s por mapa) y cada clip suelto en una **carpeta persistente** (`config.COMPETITIVO_FOLDER`, por defecto `~/Videos/ReelForge/Competitivo/`, que "Limpiar archivos" no toca), con un `info.json`. Con `carpeta_dia` y `partido` queda **por día y partido**; sin ellos, una carpeta por serie como siempre (ver "Salida" abajo).

```
feed lolesports (window, ~5 frames/s) ─kills.py─→ peleas + puntaje ─→ selección (presupuesto)
VOD de Ibai ─fuente.py─→ tramos HLS ─layout.py─→ ancla por partida + ajuste por clip ─→ render 1080x1920 ─→ resumen
```

| Archivo | Qué hace |
|---|---|
| `engine.py` | Motor del prototipo `esports_highlights`, **copiado sin cambios** (eventos, fmt_hms, dataclasses) |
| `kills.py` | Baja el feed (urllib, ventanas en paralelo, con las K/D/A por jugador del propio `window`) y arma las peleas: kills a <15 s = pelea; ventanas a <6 s se fusionan; filtro de kills mínimas; puntaje del brief + **oro que movió la pelea** (+1 c/100, máx. 20, pedido de Axl); selección **por mapa**: por puntaje sin tope dentro del objetivo de duración del mapa (`objetivo_s`) y con tope por partida; si no llega a `duracion_min_s`, relleno con las que no pasaron el filtro (picks y primera sangre) por puntaje, sin encimarse; la **pelea de cierre** entra siempre: la última que **ganó el ganador de la partida** (`ganador_partida`: el que tiró el último edificio antes del final, o el de más oro), no la última antes del nexo, que a veces es una escaramuza que gana el que pierde; lleva +30 y "→ nexo" solo si el nexo cae ≤ 40 s después; orden cronológico. CLI: `python -m modules.competitivo.kills <gameId…>` |
| `layout.py` | Layout, análisis del tramo (cámara del set, repeticiones, contador de kills) y comando de render. El análisis sale de **un solo decode** del tramo (`Scan`, ver «Rendimiento») |
| `fuente.py` | yt-dlp con la config del Downloader (cookies, runtimes JS, reintentos): hora de inicio del directo (`release_timestamp`, o `timestamp` en Twitch), plataforma y tramos HLS. Un directo de YouTube **todavía en vivo** se rechaza: yt-dlp baja lo que se transmite en ese momento, no el tramo (salieron clips de HLE vs TES en vez de IG vs GEN). El VOD de Twitch sí sirve mientras crece (formato `b[height<=1080]`, video y audio juntos; las descargas comparten los **4 turnos "twitch"** de `core/scheduler.py` en todo el server y hay **2 reintentos** si una falla). Un **audio largo** de Twitch (5+ min, una partida entera) se baja **por segmentos HLS, 8 a la vez** (`_audio_by_segments`): lee la lista del audio solo, baja los segmentos que tocan el rango, los une y corta por los tiempos de la propia lista; si algo falla, cae al camino de yt-dlp. Un tramo que baja **sin video** se baja otra vez; si vuelve igual (`SpanWithoutVideo`), ese clip se salta y queda en `aviso` y `skipped_clips` |
| `intro.py` | Portada (vertical, `portada.jpg`) y placa (horizontal) con plantilla fija; voz de la intro por URL; segmento "aparte" |
| `clock.py` | El **reloj del marcador** ("10:18") leído del video con plantillas de la tipografía de la transmisión (`assets/competitivo/clock_digits.json`, hechas de cuadros reales con `build_templates`): celdas fijas (los dígitos son monoespaciados y se tocan), cada dígito recortado a su tinta y escalado por área. La fase 1 lo **calibra** en el tramo del ancla (mediana de ~20 lecturas) → `clock0_utc` por partida; con las pausas del feed (`gameState` "paused") cualquier hora del feed pasa a reloj del marcador. Los títulos, las kills (`kills_reloj`) y lo que Axl nombra ("la del 21:41") usan ESE reloj; el del feed (arrancaba ~5 min antes del 0:00) queda en `reloj_feed` |
| `camera.py` | Dónde va la cámara en cada kill: el recorte del gameplay **sigue la acción** (`layout.action_track`: el movimiento del segundo y medio antes de la kill en toda la zona limpia, con el paneo del observador compensado por correlación de fase y los bordes —donde Riot anima DRAGON SOUL y los patrocinios— con menos peso) y se desliza suave (0.9 s, llega 0.6 s antes); o la **vista completa** (la zona limpia entera a lo ancho, como la cámara dividida). `zoom_mode`: `zoom`, `full` o `auto` (zoom donde la acción es clara, completa donde no). Correcciones por kill (`kill_overrides`) |
| `transcript.py` | La **partida completa transcripta**, del draft (10 min antes del 0:00) al nexo **y el post-partido** (`post_s`, 15 min por defecto: ahí van los análisis de Ibai), en el reloj del marcador (antes de 0:00 va negativo), con el vocabulario de la partida; y el **índice de momentos**: frases con palabras clave (robo/quitar, steal, barón, dragón, heraldo, golem/bufo/red/azul, multikill, torre, nexo; el robo y el buff se buscan en ±12 s de contexto) cruzadas con los eventos del feed (±25 s) y los jugadores nombrados, más los eventos del feed. En TL-CFO encontró "1:49 robo de buff — Josedeodo, Shad0w" sin ninguna kill. Además `elogio` y `critica` (ver «Transcripción») |
| `creator.py` | La **cámara de otro creador** en lugar de la de Ibai (`creator_vod_url` en `/finish` y `/edit`): sincroniza su VOD con nuestro tramo por el audio (envolvente de ataques, correlación; la primera vez ±90 s alrededor de lo que dicen las horas de inicio de los dos directos, después ±8 s alrededor de lo medido), busca su webcam (en un co-stream, donde su imagen sincronizada difiere de la nuestra; si no, bloques de cámara real que se mueven) o la toma de `creator_cam=x,y,w,h`, y la pone sobre el recuadro de Ibai en el tramo (el audio queda el de Ibai). `creator_delta_s` fuerza la sincronía. **Falta probarlo con un VOD real de un creador** |
| `bars.py` / `card_plan.py` / `cards.py` / `card_designs.py` / `stats.py` | Las **tarjetas de jugador** sobre los campeones (ver «Tarjetas de jugador»): detector de barras de vida de UN cuadro, qué jugadores y qué barras, el dibujo (frente, dorso y giro) y las estadísticas del torneo del dorso |
| `storyboard.py` | Una miniatura por kill (la zona limpia en su cuadro con la ventana de recorte o FULL y el punto del enfoque) y una hoja de contacto, antes del render largo |
| `vocab.py` | Nombres bien escritos en los subtítulos de Ibai (modo edit): vocabulario de la partida para Whisper (equipos, 10 jugadores, 10 campeones) y las correcciones de `assets/vocab.json` |
| `presets.py` / `metrics.py` / `calibration.py` | La operación del día a día: parámetros con nombre, un registro por trabajo y el set de calibración con casos reales (ver «Presets, métricas y calibración» abajo) |
| `salida.py` | Dónde queda cada resumen: estructura por día/partido o por serie, nombres limpios para Windows, publicación sin pisar (lo anterior a `_descartados`), listado y rutas seguras para `/resumenes` |
| `schema.py` / `processor.py` / `routes.py` | Contrato estándar de dos fases |

**Layout v2** (maquetas de Axl del 23/10; 1080x1920, de arriba a abajo, sobre el cuadro entero difuminado y oscurecido): tabla de stats **sin las cámaras de jugadores** (fuente x 465–1456, y 820–1080) a 1020 de ancho con esquinas redondeadas → gameplay con **toda la zona limpia de alto** (712 px → 1080x950, zoom 1.33; con más zoom recorta menos, con menos es lo mismo) → **centro del marcador** (x 520–1400) a 1040 de ancho, redondeado → cámara del set de Ibai al **78 %** del ancho, redondeada (también en el modo edit: `with_camera=1` por defecto). Watermark opaco "parado" en la esquina inferior derecha. El recuadro de la cámara cambia con la fuente y el día (día 1 en YouTube 440x246 desde x 1478; 23/10 ~450x254 desde x 1468, y 541 en Twitch): `detect_camera_box()` lo mide por tramo (arista izquierda + 16:9; la y donde el gameplay que se mueve da paso al set quieto). Tope de 16 Mbps (con cq 18 salía a 34 Mbps).

**Cámara dividida.** Riot a veces parte la pantalla en dos vistas con una línea casi negra, delgada y un poco diagonal cerca del centro (IG-GEN 23/10, 10:18). `split_ranges()` la busca en cada tramo (4 cuadros/s, franja x 860–1140 de toda la zona limpia: una racha oscura de 3–16 px en el **65 %** de las filas, alineada en una recta de pendiente ≤ 0.35, durante 0.75 s seguidos; un hueco de ≤ 0.5 s no la corta). Con 85 % se perdía la de TL-CFO (la línea pasa pegada a barras de vida y cruza solo el 73–100 % de las filas) y salía a pedazos; sin cámara dividida el máximo medido fue 51 % (31 tramos del 23/10). En los 33 min del tramo de IG-GEN encontró las dos que hay y ningún falso positivo. Mientras dura, el gameplay muestra **la zona limpia completa (1475x712) a lo ancho, sobre su copia difuminada**, en vez del recorte con zoom (que caía justo en la costura). En los edits, la geometría de cada plano recibe su `-ss` (`editing.render.SOURCE_T0`) para que los rangos queden en segundos del tramo.

**Sincronía (lo más importante).** La fórmula del brief (`vod = utc − inicio del directo + offset`, `OFFSET_FORMULA_S` por plataforma: YouTube **38 s**, Twitch **44 s**, medido en IG-GEN el 23/10 con 7/7 kills; el VOD de Twitch empieza 36 s antes que el directo de YouTube y va corrido) es solo el punto de partida: el desfase real fue **+7.5 s en la G1 y −5 s en la G2/G3 del mismo día**. Un ancla con 2+ kills alineadas pero a **más de 15 s** de la fórmula de su plataforma queda en confianza `media` (`suspicious: true` y el `aviso` lo dice): así `modo=auto` se detiene en `esperando_ancla` (el directo de YouTube en vivo dio 6/7 kills de OTRA partida a −23.5 s). Se mide solo: `cambios_kills()` ve en qué segundo del video sube el contador de kills del marcador oficial (cada lado por separado: el azul siempre a la izquierda) y `estimar_desfase()` alinea ese patrón con las kills del feed (±0.2 s). Fase 1: ancla por partida con el tramo de su pelea más grande (confianza **alta** con 2+ kills alineadas). Fase 2: ajuste fino de cada clip con sus propias kills. Medido en KC-C9 G1-G3: todas las kills dentro de sus clips, ajuste fino < 0.1 s. Las **repeticiones** (sin marcador) se detectan y el clip queda en el tramo en vivo que contiene la kill.

| Parámetro (fase 1) | Tipo | Default | Notas |
|---|---|---|---|
| `game_ids` | str | — | gameId de 15–20 dígitos, uno por partida **en orden** (la última es la decisiva) |
| `vod_url` | str | — | VOD de IbaiExtra en YouTube (ya terminado) o `twitch.tv/videos/…` (sirve mientras sigue en vivo) |
| `modo` | str | revisar | `revisar` (pausa con todo prellenado) / `auto` (sigue solo si todas las anclas son de confianza alta) |
| `duracion_auto` | bool | true | objetivo **por mapa** = minutos de la partida × 4 s, acotado entre `duracion_min_s` y `duracion_max_s` (sin auto: `duracion_max_s`) |
| `duracion_min_s` / `duracion_max_s` | int | 90 / 180 | 0–300 / 20–300 (mín. ≤ máx.). Si las elegibles no llegan al mínimo, se rellena con picks y la primera sangre por puntaje (`relleno: true`), sin pasar el máximo |
| `max_por_partida` / `min_kills` | int | 8 / 2 | 1–12 / 1–5 |
| `incluir_picks` / `incluir_primera_sangre` | bool | false | |
| `pre_s` / `post_s` | float | 10 / 5 | 0–20 / 0–15: antes del frame anterior a la 1ª kill / después de la última |
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
| `placa_max_s` | float | 5 | 1–15: lo máximo que se ve la placa en modo `encima` (la voz sigue igual hasta `D`) |
| `transcript` | bool | false | al terminar la fase 1 lanza la transcripción de cada partida (una tras otra; ver «Transcripción»); los job_id vienen en el status |
| `carpeta_dia` / `partido` | str | "" | máx. 80; van juntos. P. ej. "Play-In Día 3 (17-10)" y "MVK vs FUR" (el mismo para todos los mapas de la serie, no depende del lado) |
| `titulo` | str | "" | máx. 120: el video queda "Mapa N - <titulo>.mp4" (sin título, "Mapa N.mp4") |

Todos estos campos se pueden mandar también en `/finish` (lo que no llega se hereda de la fase 1; el modo `auto` los pasa con `FinishParams.desde()`). `/finish` además acepta `zoom_mode` (`zoom` por defecto, como siempre; `auto` o `full`, ver `camera.py`) y `kill_overrides`.

**Intro con voz y portada (`intro.py`).** La **voz no la genera ReelForge**: la genera Claude con Fish Audio (MCP) y llega como link de un mp3 en `intro_audio_url` (no hay API de Fish Audio ni claves en la app). Se baja una vez, en la fase 1 (un link roto falla antes de bajar el VOD). Se le recorta el silencio final de la TTS (`silencedetect`) y se normaliza en dos pasadas a **−11 LUFS / −1.5 dBTP** (`loudnorm` lineal con lo medido; sin eso la voz quedaba casi al nivel del juego y 9–11 LU por debajo de los clips). `D` = fin de la voz + 0.4 s, **redondeado a frames enteros de 60 fps**; la voz puede durar lo que haga falta; más de 20 s se toma como un error (protección) y el job falla con un mensaje claro. Lo visual es una **plantilla fija** (MAPA N en Anton, la fase en el lima de la app, logos VS, nombres, serie y fecha): la portada vertical 1080x1920 (`portada.jpg`, se genera **siempre**, en la carpeta de la serie) y la misma plantilla en horizontal como placa (864x560, esquinas redondeadas, borde blanco y sombra como la placa del HUD). Fallbacks, nunca falla: sin `assets/competitivo/fondo_portada.png` → degradado oscuro; sin logo o si falla la descarga → el código del equipo en un círculo; sin nombres → los códigos del feed; nombres largos → se achican o se parten en dos renglones (ancho real con `fonts.text_width`). `assets/competitivo/sting.mp3`, si existe, suena al 35 % debajo de la voz y se apaga con un fade de 0.3 s al llegar a `D`. El limitador (`alimiter=limit=0.89:level=0`) no sube la ganancia: solo frena picos.
- **`encima`** (default): dentro del encode del clip 1. La placa entra con un fade de 0.2 s y sale con otro de 0.3 s; dura `min(D, primera kill − 0.5 s, placa_max_s)` (`fin_de_placa`): se va antes de la kill, y como mucho a los 5 s, y **la voz sigue sonando completa** con el juego bajado hasta `D`; el juego baja a `intro_volumen_juego` (17 % ≈ −15 dB; misma expresión `volume=…:eval=frame` que hook_reel) y vuelve al 100 % con una rampa de 0.4 s; `amix` con la voz (+ sting) y un limitador. El clip 1 se renderiza **dos veces**: `clip_01.mp4` limpio y la versión con intro solo para el resumen. Cae solo a `aparte` (y lo anota con el motivo) si la primera kill del clip 1 cae antes de los **3 s** (la placa casi no se vería) o, como protección, si la voz no entra en el clip 1 (`D + 0.5 s` > lo que dura). `intro.placa_s` dice cuánto duró la placa.
- **`aparte`**: `intro.mp4` propio (la portada con zoom lento 1.00→1.06 y fade-in), primero en el resumen. Sale con **los mismos parámetros que los clips** (60 fps, encoder, tope de 16 Mbps, AAC 160k 48 kHz estéreo, yuv420p, SAR 1:1, **bt709/tv**): el resumen se une con `concat -c copy` y hereda la cabecera de video del primer archivo. El **audio sí se re-encodea** al unir (`layout.build_concat`: `aresample=async=1` + limitador → AAC): con `-c copy` el relleno AAC de cada archivo corría el audio de cada unión y se acumulaba (+59/+94/+117 ms al 3er clip); ahora queda fijo (~20–30 ms, lo mismo que mide un clip solo). Por eso los clips también salen etiquetados bt709/tv explícitamente (`layout.SALIDA`). Lo verifica un test con ffprobe.
- **`off`**: sin intro; la portada se genera igual.

**API (form-urlencoded; también para que Claude la opere sola con el server corriendo):**

```
POST /api/competitivo/process    game_ids, vod_url, [modo, reglas, render…]  → 202 {job_id}
GET  /api/competitivo/status/<id>  → {status, progress, stage, error, finished, aviso,
        vod, partidas:[{n, game_id, azul, rojo, minutos, objetivo_s, seleccion_s, ancla_s, ancla_propuesta_s, desfase_s,
        confianza, coincidencias, esperadas, referencia, frame}],
        clips:[{id "partida-n", titulo, puntaje, bruto, desglose, kills, duracion,
        elegible, seleccionada, forzada, relleno, …}], seleccion:[ids], resultado, portada, intro}
POST /api/competitivo/finish     job_id, [anchors='{"gameId": "1:22:15"|4935}'],
        [clips='["2-9","3-19"]'], [zoom, color, watermark, marca_alto,
        intro_modo, intro_audio_url, intro_volumen_juego, placa_max_s, mapa, fase, equipo_*, logo_*,
        serie_marcador, marca_4xl, carpeta_dia, partido, titulo]  → 202
GET  /api/competitivo/download/<id>      → resumen.mp4   (·/<n> → clip n)
GET  /api/competitivo/frame/<id>/<n>     → JPG de control del ancla de la partida n
GET  /api/competitivo/portada/<id>       → portada.jpg (después de finish)
GET  /api/competitivo/resumenes          → resúmenes guardados: [{estructura, nombre, dia, partido, mapa,
        titulo, ruta, portada, clips:[{archivo, titulo, duracion, ruta}], creado, duracion_total, intro}]
GET  /api/competitivo/resumenes/<ruta>   → cualquier archivo de ahí (ruta relativa, con subcarpetas)
POST /api/competitivo/transcript   job_id (fase 1), [game_id, language, draft_lead_s, post_s (900), batched (0), carpeta_dia, partido, mapa]
        → 202 {job_id NUEVO}; resultado {txt, json, moments (lista), segments, post_segments, praise, criticism, minutes}
GET  /api/competitivo/cards/<job>/<clip> → JPG del cuadro de un clip con sus barras numeradas (`cards_preview=1`)
POST /api/competitivo/cards/preview cards, [card_style, card_photos, card_data, card_back, caption, job_id, game_id] → {style, cards:[{name, ok, photo, photo_error?, back, rows…}], image}
GET  /api/competitivo/cards/sheet/<id>   → el PNG (frente y dorso de cada tarjeta) de esa vista previa
GET  /api/competitivo/cards/styles       → {styles, backs, fields, roles, sides, size}
GET  /api/competitivo/stats[?player=&champion=] → las estadísticas del torneo guardadas (y lo que aplica a ese jugador/campeón)
POST /api/competitivo/stats/update [game_ids, sessions, discover] → 202 {job_id}: baja las partidas que faltan
GET  /api/competitivo/presets            → [{name, description, endpoints, params}]
GET  /api/competitivo/metrics?days=7     → {summary, recent (últimos 40), discarded, slots (los turnos: usando/esperando), calibration}
GET  /api/competitivo/calibration        → {folder, cases:[{id, check, note, known_issue}], report (el último)}
POST /api/competitivo/calibration  [ids=a,b]  → 202 {job_id}; el reporte llega en `resultado`

**Rendimiento** (plan del 27/10, medido en la RTX 5070 / Ryzen 5 9600X con `python -m modules.competitivo.bench` y los tramos reales de la calibración; la imagen y la sincronía no cambian):
- **Turnos por recurso** (`core/scheduler.py`, cupos en `config.SLOTS`): antes corrían a la vez 13 transcripciones, 8 fases 1 y 4 renders y cada uno salía más lento que solo. Ahora los trabajos hacen cola por orden de llegada (`priority` para saltarla): `twitch` 4 (descargas), `whisper` 1 (un modelo a la vez en la GPU), `analysis` 2 (la lectura de un clip en la CPU) y `nvenc` 2 (renders). Ningún trabajo guarda un turno mientras espera otro. Las métricas guardan cuánto esperó cada uno (`waits`) y cuántos trabajos había a la vez (`concurrent_at_start`, `concurrent_peak`); la vista Métricas muestra los turnos en vivo.
- **Un solo decode por clip** (`layout.Scan`): el análisis abría el tramo (1080p60) 6 veces más una por kill; ahora UN FFmpeg con una salida por recorte (marcador, dígitos de kills, franja de la cámara dividida, zona limpia, reloj…), cada una con su propio `fps`, así que cada función recibe exactamente los mismos cuadros que antes (la calibración da lo mismo). Se decodifica en NVDEC cuando se puede (`hwdownload,format=nv12,format=yuv420p`: píxeles idénticos a los de la CPU, comprobado en todas las salidas) y, si esa corrida falla, en la CPU. El reloj del marcador (`clock.calibrate`) lanzaba 120 FFmpeg por partida: ahora sale del mismo decode. Medido sobre 5 tramos (205 s de video): **análisis 45.4 → 9.9 s, anclas 64.1 → 8.4 s**.
- **Análisis y render en línea** (`/finish`): mientras se renderiza el clip k, se baja y se analiza el k+1 (un hilo que respeta el orden: la cámara del creador mantiene su sincronía de un clip al siguiente); en `/edit`, los tramos se analizan a la vez (tantos como turnos de análisis). Las métricas traen el desglose por clip (`clips_detail` / `spans_detail`: descarga, espera, análisis, render).
- **Anclas ya medidas** (`cache/competitivo/anchors/`): una fase 1 repetida del mismo (VOD, partida) —el resumen diario, un edit después de un resumen— no baja ni lee su ventana otra vez. Solo se guardan las de confianza `alta`, sin `suspicious` y con su reloj, y solo se usan si la primera kill del feed sigue siendo la misma.
- **Audio por segmentos** (ver `fuente.py`): 20 min de audio de Twitch en frío **9.5 s (126×) contra 31.1 s (39×)** con yt-dlp; el mismo rango por los dos caminos queda alineado (desfase constante de 30 ms al inicio, a la mitad y al final, correlación 0.98: sin huecos ni duplicados).
- **Whisper por lotes, opcional** (`/transcript` con `batched=1`): 135× contra 37.5× en 7.5 min de narración de Ibai, pero **87 % de coincidencia de palabras** con el modo normal (le faltan ~9 %); devuelve segmentos de ~25 s, que se parten en frases (`_split_long`). Por defecto sigue el modo normal: hay que decidir cuál de los dos es el correcto comparando una partida entera. Ojo: con Whisper a 37× las 13 transcripciones juntas (35.7× en conjunto) ya estaban limitadas por Whisper, no por la descarga.
- **Antes y después** de `/finish` con los mismos 5 tramos (178 s de video): solo, **87 → 53.5 s** (17.4 → 10.7 s por clip); 3 trabajos a la vez, antes los tres terminaban juntos a los 212 s (18.6 s por clip en conjunto) y ahora el primero sale a los 107 s y el conjunto rinde **9.4 s por clip**.
- **Probado y descartado:** (1) decodificar el render en NVDEC: no lo acelera (2.79× contra 2.83× tiempo real) porque el 95 % del render es el grafo de filtros en la CPU (12.9 s de 13.6 s para 39 s de video; el color "vivo" son 5 de ellos: `curves` 3.1 s, `unsharp` 1.9 s); (2) subir NVENC de `p4` a `p5`–`p7`: el VMAF sube 0.02–0.03 con el tope de 16 Mbps y el tiempo es el mismo; (3) bajar el ancla en 720p/480p: a 720p el contador de kills da cambios falsos y el reloj se corre ~0.7 s, a 480p y 360p el reloj ya no se lee; (4) más fragmentos de yt-dlp (`concurrent_fragment_downloads`): no cambia nada con `download_ranges`; (5) estrechar la ventana del ancla: el desfase cambia de una partida a otra del mismo VOD (+7.5 s y −5 s), así que uno confirmado no sirve para el siguiente. Lo que queda por ganar en el render está en el grafo de filtros (pasarlos a CUDA cambiaría la imagen: no se hizo).
- **Benchmark** (`modules/competitivo/bench.py`, sobre los tramos de `CALIBRATION_FOLDER`): `python -m modules.competitivo.bench [--scan] [--only analysis,anchors,render] [--label x]` mide análisis, anclas y render con CPU y GPU; `--concurrency 4 [--slots 1,2,3,4]` lanza renders a la vez con cada cupo de NVENC. Los informes quedan en `Calibracion/bench/`.

**Tarjetas de jugador** (`/finish`, primera entrega: **sin seguimiento**). Al inicio de la jugada el clip se **congela** `card_dur_s` s (2 por defecto, con el sonido apagado) y sobre el campeón de cada jugador elegido aparece una tarjeta: entra, muestra el frente (foto, nombre, rol, campeón), **gira** y muestra el dorso con 3 datos; se va y el juego sigue. Las tarjetas se ubican **una sola vez** (no se mueven con el campeón) y quien las elige es el usuario, en un **campo libre**.
- **Parámetros (todos opcionales; sin `cards` no cambia nada):** `cards` (nombres como se escriban, `Canyon, Rookie` o lista JSON; se aceptan nombres cortos o mal escritos del roster), `card_scope` (`kills` = solo en las peleas donde ese jugador hizo una kill, el default · `all` = en cualquier clip de su partida), `card_back` (`auto` · `pareja` · `campeon` · `jugador` · `partida`), `card_style` (`splash`, el default · `luxury` · `plain`, ver «Diseños y control por el modelo»), `card_photos` y `card_data` (JSON, ver abajo), `card_mode` (`freeze` · `slow`: el juego sigue a `rate` 0.3×), `card_dur_s` (1.2–4), `card_lead_s` (2: el congelado se busca ese tiempo antes de la primera kill), `card_scale` (0.5–1.6), `card_pos` (JSON, ver abajo) y `cards_preview`.
- **Cómo se ubican** (`bars.py`, `card_plan.py`): la transmisión dibuja sobre cada campeón una barra con su nivel, su nombre («GEN Canyon») y su vida (roja o azul según el lado). De unos cuadros alrededor del inicio de la pelea (cada 0.5 s, `card_lead_s` ± 1.5 s) se toma el que más barras muestra y se atan los jugadores a sus barras por: (1) el **ancho del nombre** en píxeles (sigue las letras: «GEN Canyon» 72 px, «GEN Chovy» 64; se compara con Montserrat ExtraBold × 0.271, ±6.5 %), solo cuando **un único** jugador del lado encaja con esa barra; (2) la **vida** contra la que dice la API en ese segundo (`window?startingTime=`: `currentHealth` / `maxHealth`), solo si es claramente la más parecida y ningún otro del lado se le acerca. Al inicio de una pelea casi todos tienen la vida llena, por eso manda el nombre. Lo que no se puede atar **no se adivina**: queda `ambiguous` (con las barras candidatas) o `not_seen` en el status del clip (`resultado.clips[].cards`), y la tarjeta no sale.
- **Corregir:** `cards_preview=1` no renderiza nada: devuelve por clip el cuadro con las barras numeradas y los nombres ampliados al lado (`GET /cards/<job>/<clip>`, `status.cards_preview`; el job queda en `esperando_tarjetas` y `/finish` se vuelve a llamar). Con eso se decide `card_pos={"Canyon": "bar:2", "Rookie": [880, 580]}` (el número de la barra o el punto en píxeles de esa imagen). Es lo que hace Claude cuando algo queda ambiguo (Rookie y TheShy tienen nombres casi igual de anchos).
- **Dónde cae la tarjeta** (`layout.output_point`): se calcula con la misma geometría del grafo (el recorte con zoom y su seguimiento de cámara, o la zona entera en la vista completa / cámara dividida), con su borde inferior justo sobre el nombre del campeón y dentro del área de juego; si no cabe arriba va debajo. En la vista completa se achica a 0.78.
- **El congelado** (`layout.Freeze`): va **después** del layout, dentro del mismo encode (el grafo parte `[vout]` en tres: antes, el cuadro detenido con `tpad`, después), así que las expresiones de tiempo del layout no se mueven; el audio se corta igual (un hueco de silencio, con un fundido de 80 ms a cada lado). Las tarjetas son secuencias de PNG con transparencia (60 fps) que se superponen durante el congelado. El clip dura `card_dur_s` más; `duracion` y `duracion_total` lo cuentan. Si el congelado cae donde está la placa de la intro «encima», ese clip va sin tarjetas.
- **El dorso** (`card_plan.back_rows`), 3 datos con barras: `pareja` (jugador + campeón, solo con 2+ partidas juntas en el torneo: V-D, KDA, partidas), `campeon` (picks, V-D y KDA del campeón en el torneo), `jugador` (V-D, KDA, partidas) y `partida` (K/D/A, CS, nivel y oro de ESA partida en ese segundo: la API siempre lo tiene). `auto` baja por ese orden hasta lo que haya.
- **Estadísticas del torneo** (`stats.py`, `cache/competitivo/stats/games.json`): del último cuadro del feed de cada partida terminada salen los K/D/A, CS, oro y nivel de cada jugador; el ganador sale de lo que ya calculó la fase 1 (`ganador`) o, si no, de los inhibidores, torres y oro del último cuadro. Al renderizar con `cards` se agregan las partidas de las sesiones guardadas y de la serie (una llamada por partida: 6 partidas en ~2 s). `POST /stats/update` también las baja a pedido; con `discover=1` y la clave pública de la web de lolesports en `REELFORGE_LOLESPORTS_KEY` lista las del torneo entero por su calendario (la clave no se guarda en ningún archivo).
- **Fotos** (por link con `card_photos`/`card_data`, o `assets/players/<Nombre>.png|jpg`, cualquier mayúscula o tilde; el link manda): si no hay ninguna, el frente lleva una silueta con las iniciales. Los íconos de campeón salen de Data Dragon (`cache/competitivo/cards/icons/`, se bajan una vez); sin red, un círculo con la inicial. Dibujar usa **Pillow** (`requirements.txt`).
- **Diseños y control por el modelo** (`card_designs.py`; **el modelo controla el módulo**, no hay UI): `card_style` elige entre `splash` (**el default**, elegido por Axl entre tres propuestas: el arte de pantalla de carga del campeón a pleno, placas de equipo y rol, el nombre sobre un medallón con la foto y esquinas de HUD en el color del equipo) y `luxury` / `plain` (los primeros diseños). El arte del campeón (`cards.champion_art`, Data Dragon, 1 vez) es el fondo de `splash`; sin red, un degradado con la inicial. Todo lo que dibuja una tarjeta se le puede dar por link o texto:
  - `card_photos={"Canyon": "https://…/canyon.png"}`: la foto del jugador por link. `card_data={"Canyon": {...}}` acepta `photo`, `art`, `icon` (links), `team`, `side` (`blue`/`red`), `role`, `champion`, `title` (el encabezado del dorso) y `back` (`{"title", "caption", "rows": [{"big": "4-10", "label": "V-D", "bar": 0.3}]}`, 1 a 3 filas: reemplaza los números del dorso, así el modelo escribe lo que quiera). `card_data` gana sobre `card_photos` y sobre el roster.
  - `cards.fetch_image` baja el link (10 s, máx. 12 MB, una vez: cache por link en `cache/competitivo/cards/images/`), comprueba que sea una imagen de 40 px o más y **nunca falla el trabajo**: si el link no sirve, la tarjeta sale sin foto (o con la de `assets/players/`) y el motivo viaja en el status (`resultado.clips[].cards[].photo_error`; `photo` dice `url` / `assets` / `none`).
  - **Vista previa sin video** (`POST /cards/preview`): arma el frente y el dorso de cada nombre como los haría un render y devuelve un PNG (`image`) más el informe por tarjeta. Con `job_id` (fase 1; y `game_id` si la serie tiene varias partidas) los datos salen de su roster; sin él, `card_data` tiene que traer `team`, `side`, `role` y `champion` del jugador (un nombre que no está en ninguno de los dos se informa `ok: false`, no se adivina). El flujo de Claude: `GET /cards/styles` → `POST /cards/preview` con 2–3 estilos y las fotos que encontró → mirar el PNG y corregir links/datos → `/finish` con los mismos `cards`, `card_style`, `card_photos` y `card_data`.
- **Lo que NO está (a propósito):** seguimiento del campeón durante la pelea (la tarjeta se ubica una vez y se queda), elegir solo al MVP de la pelea (el campo `cards` es libre; queda para cuando se decida el criterio), fotos oficiales desde la API (hace falta la clave; el modelo las busca y las pasa por link) y el arte hecho en Presentations (se dibuja dentro de ReelForge; el contrato —una secuencia de PNG con alfa por tarjeta— permitiría cambiarlo).

**Presets, métricas y calibración** (la operación del día a día):
- **Presets** (`assets/competitivo/presets.json`, versionado y editable): `preset=<nombre>` en `/process`, `/finish`, `/edit` o `/daily` llena esos parámetros y **lo que el pedido mande explícito gana** (un campo vacío no pisa). Un preset de otro endpoint o inexistente → 400. Hay `edit_estandar` (actos cronológicos, sin letreros, 3 s antes / 2 s después de cada kill, picos de Ibai, cámara `auto`, **storyboard** antes del render, 60 s), `edit_corto`, `edit_equipo` (falta `team`), `edit_aura_triste` (cierre en blanco y negro), `resumen_highlights` (fase 1: zoom 1.4, 7/4 s, sin intro; el `/finish` lo hereda) y `resumen_diario`. El job guarda `preset` y las métricas lo cuentan. **Como lo opera Claude:** un edit es `preset=edit_estandar` + música + lo propio del pedido (`team`, `moments`, `kill_overrides`…), no la lista de parámetros entera.
- **Métricas** (`metrics.py`): cada fase 1, resumen, edit (`edit` = plan, `edit_render` = el render de `/edit/finish`), resumen diario, transcripción y calibración agrega UNA línea a `COMPETITIVO_FOLDER/_metricas/jobs.jsonl` (persistente, "Limpiar archivos" no la toca): `kind`, `status` (`ok` / `paused` / `error`), segundos totales y por etapa (`stages`), fallbacks de NVENC a CPU durante el trabajo (`ffmpeg_runner.stats`), y lo de cada tipo: confianza de las anclas y reloj calibrado (fase 1); kills con foco seguro / en plano entero (`kills`, `kills_sure`, `kills_full`, de los planos de la cámara), correcciones (`overrides`), reintentos, duración del video, tramos sin video, avisos y el preset y, con tarjetas, cuántas se pidieron, se ubicaron, quedaron ambiguas o no se vieron (`cards_*`). Nunca rompe un trabajo (si no puede escribir, lo dice en consola). `summary()` arma los indicadores de la vista **Métricas** (sidebar → Sistema): KPIs, tabla por tipo, últimos trabajos y la calibración con su botón. Los tests escriben en un archivo temporal (`metrics.FILE`, en `tests/competitivo/__init__.py`), nunca en el del usuario.
- **Calibración** (`calibration.py`, carpeta `config.CALIBRATION_FOLDER` = `~/Videos/ReelForge/Calibracion`, **fuera de git**: `cases.json`, `media/` con los tramos cortados de los VODs con `-c copy`, `reports/last.json` y uno por corrida). Cada caso es un tramo REAL con el resultado revisado a ojo: `kill_changes` (el contador de kills: la base de la sincronía), `split` / sin split, `camera_box`, `clock`, `ambient`, `voice_peaks` (con las medianas de los tramos del caso), `moments` (sobre una transcripción guardada) y `vod_info` (directo de YouTube en vivo rechazado, VOD de Twitch aceptado). Correrla **antes de cada día de partidos y después de cada cambio** de un detector: `python -m modules.competitivo.calibration` (o algunos ids), o el botón de Métricas; ~40 s. Un caso con `known_issue` espera el resultado CORRECTO y se sabe que falla: sale «conocida», no tumba la corrida y avisa «arreglada» el día que pasa. Un cambio deliberado que mueve un resultado: `--accept <id>` (lo medido pasa a ser lo esperado). Set inicial (23/10, 18 casos): 17 bien y **1 conocida**: el 18→19 de T1 en LOS-T1 1-14 no se ve (el escalón de dos dígitos que comparten trazos queda al nivel del ruido); la salida es leer los dígitos como el reloj.
POST /api/competitivo/daily        game_ids (todas las del día), vod_url, carpeta_dia, [titulo, montage_name,
        picks {gameId: [ids]}, zoom, pre_s, post_s, zoom_mode, watermark] → 202 {job_id}
```

**Transcripción** (`/transcript`, o `transcript=1` en `/process`): baja solo el audio de la partida, del draft hasta `post_s` segundos **después del nexo** (0–1800, 900 por defecto: ~63 min en Twitch, `fuente.bajar_audio`), la transcribe con faster-whisper y el prompt de la partida, y deja `transcripcion.txt` (`[1:49] …` por frase), `transcripcion.json` (con cada palabra en el reloj) y `momentos.json` en `<carpeta_dia>/<partido>/_extras/Mapa N/` o, sin ellos, en `COMPETITIVO_FOLDER/_transcripciones/<fecha> A vs B · Mapa N/`. Necesita el reloj calibrado (fase 1 de esta versión).
- **Después del nexo** el reloj es **`+m:ss`** (tiempo desde el nexo, no el del juego) en el `.txt` (con una línea que separa el tramo) y en el json (`clock`; esos segmentos llevan `post: true` y `post_s`, y el json `post_s` y `vod.nexus_s`). Ahí el índice solo busca `elogio`/`critica`; antes del nexo sigue todo lo anterior. Cada segmento lleva `vod_s` (y `vod_end_s`): los segundos del VOD, para recortar.
- **`elogio` / `critica`** (se buscan en TODO el texto: draft, partida y post-partido): un jugador de la partida (roster) nombrado a ±10 s de palabras como *bestia, imparable, el mejor, monstruo, partidazo, MVP, increíble, brutal, top 1, destrozo* (elogio) o *horrible, fatal, la ha liado, regalado, error, desastre* (crítica). El jugador es el de la frase más cercana que nombra a alguien; los nombres de 5+ letras se aceptan mal oídos (`Mirwin` → Myrwn: misma inicial, 85 % parecido, y = i) y los cortos (Rest, Kino) solo exactos; `assets/vocab.json` corrige además las variantes conocidas. Cada momento trae `player`, `text` (la frase), `context` (±12 s), `vod_s`, `vod_from` y `vod_to` y `post`; el mismo elogio del mismo jugador a menos de 20 s se cuenta una vez. Medido en MKOI vs CFO (24/10, `post_s=900`): 422 frases después del nexo (hasta +14:59) y 5 elogios, 3 de Myrwn («es una bestia absolutamente imparable», 32:27). Es léxico: «la mejor» dicho de una ban cuenta como elogio de los jugadores nombrados al lado.
- **`/process` con `transcript=1`:** en el status de la fase 1 vienen `transcript_job_id` (el de la primera partida) y `transcript_jobs: [{game_id, job_id}]`; corren una tras otra (un Whisper a la vez) y sobreviven a un reinicio con la sesión.

**Resumen diario** (`/daily`), como el que Axl armó a mano el 23/10: por partida, una fase 1 (en `revisar`; si el ancla no es `alta` la partida queda fuera y el `aviso` lo dice) y un `/finish` con `pre_s` 7, `post_s` 4, zoom 1.4, sin intro, en `<carpeta_dia>/<Ganador vs Perdedor>/Mapa 1 - <titulo>.mp4`; las peleas: la de cierre y las del **ganador** con puntaje ≥ 90, hasta 3 y 55 s (`daily_pick`; con LOS-T1 da las mismas tres que eligió Axl) o las de `picks`. Después el montaje de todos en el orden en que se jugaron, **con el audio del juego**, en `<carpeta_dia>/Highlights <carpeta_dia>.mp4`. La fase 1 guarda el ganador de cada partida (`partidas[].ganador`).

**Salida (`salida.py`).** Con `carpeta_dia` y `partido`:

```
COMPETITIVO_FOLDER/<carpeta_dia>/<partido>/Mapa N - <titulo>.mp4
COMPETITIVO_FOLDER/<carpeta_dia>/<partido>/_extras/Mapa N/          clips, portada.jpg, info.json
COMPETITIVO_FOLDER/<carpeta_dia>/<partido>/_extras/_descartados/    versiones anteriores
```

Los tres campos se limpian para Windows (`:` → ` -`; fuera `< > " / \ | ? *`, controles, puntos o espacios al final y nombres reservados). Se renderiza en una carpeta temporal dentro de `_extras` y **recién al terminar** se publica: el video anterior de ese mapa (`Mapa N.mp4` o `Mapa N - *.mp4`, con cualquier título) y su `_extras/Mapa N/` pasan a `_descartados/` con (2), (3)… si ya hay uno con ese nombre. Nunca se pisa nada, y si el job falla lo anterior queda intacto. `/resumenes` recorre día → partido (ignora `_extras` y `_descartados`) y sigue listando las carpetas de serie viejas con `dia`/`partido` en null. `/resumenes/<ruta>` valida con la ruta real que no salga de `COMPETITIVO_FOLDER` (`..`, absolutas u otra unidad → 404).

Fase 1 termina en `status=done`, `finished=false`, `stage="esperando_ancla"`; fase 2 en `finished=true` con `resultado`: `carpeta` (la de clips, portada e info.json), `video` (ruta completa del resumen), `ruta` (relativa, para `/resumenes/<ruta>`), `resumen` (nombre del archivo) y `organizada`. Al terminar, `status` y `resultado` traen `portada` ("portada.jpg") e `intro: {modo_usado, duracion_s, motivo, placa_s}` (también en `info.json`); `info.json` además `intro_modo_usado`. **Como lo opera Claude:** un `/process` **por mapa** (`game_ids=<uno>`) con `modo=auto`, `intro_audio_url` (la voz de Fish Audio), `mapa`, `fase`, `equipo_*`, `logo_*`, `serie_marcador`, `carpeta_dia`, `partido` y `titulo`; poll hasta `finished=true`; el video queda en `<carpeta_dia>/<partido>/Mapa N - <titulo>.mp4` (también `GET /download/<id>` y `/portada/<id>`). Para operarlo de punta a punta: `process` con `modo=auto` → poll de `status` hasta `finished=true` (si queda en `esperando_ancla` con `aviso`, revisar el `frame` de esa partida, corregir con `anchors` y llamar `finish`). Para elegir otras peleas: tomar ids de `clips` y pasarlos en `finish`. Tiempos medidos: 3 partidas y 3 clips, fase 1 ~64 s y fase 2 ~54 s; un mapa con intro (3 clips), ~110–115 s en total. Las sesiones de la fase 1 se guardan en disco (`cache/competitivo/sessions/<job_id>.json`, con el estado de su job): después de un reinicio del server (el modo debug recarga al cambiar código) `/status`, `/finish`, `/edit` y `/frame` las recuperan solas; un render que el reinicio cortó vuelve a `esperando_ancla` con un aviso para repetir `/finish`. Los edits pausados (`review=1`) siguen viviendo en memoria. Tests: `tests/competitivo/` (fixture real de KC-C9 G1).

### 11. Edits al ritmo: beatmap, editing, anime_edit, montage y el modo edit de competitivo

**El detalle está en [`docs/edits.md`](docs/edits.md)** (contratos, algoritmos, presets, cómo los opera Claude). Lo esencial:

- **motion** (`/api/motion`, vista **Movimiento** en Edits): el mapa de movimiento de un tramo de video, cuadro por cuadro → `motion.json`, cacheado en `cache/motion/` por huella + tramo + ancho + versión. `motion.processor.get(path, start, end)` es lo que usan los demás.
  - Señales: cadencia (flujo DIS solo entre dibujos distintos), cámara (homografía), fuerza, ángulo y directograma del personaje, luma, saturación, invertido y centro de la acción.
  - Eventos: `impact`, `smear`, `pan`, `calm`, `flash`, `cut`.
  - Salidas: un resumen en texto para Claude, la hoja de eventos (JPG) y la vista previa opcional.
  - Decodifica **al cuadro exacto** (`select` sobre los timestamps del archivo: el seek solo se saltaba cuadros en el mkv).
  - Medido: 10/10 parejas de Magna vs Dante (golpes a ±1 cuadro) a ~170 cuadros/s en la CPU.
  - `python -m modules.motion.reference <edit.mp4>` mide un edit terminado:
    - dónde corta y pone destellos, zooms y sacudidas;
    - si caen en el beat o en los golpes del anime;
    - si corta en una rejilla;
    - la densidad por sección.
    - Es lo que se usó con la referencia de asen en el A/B.
- **beatmap** (`/api/beatmap`): beats, primer tiempo de cada compás, energía, secciones (`intro`/`verse`/`chorus`/`bridge`/`outro`), drop, subidas (`buildups`) y la letra palabra por palabra (`words`) → `beatmap.json`, cacheado por huella en `cache/beatmaps/`. `beatmap.processor.get()` es lo que usan los demás. Preview con pitidos para validar la rejilla.
- **editing** (motor, `/api/editing/options`): una EDL (`Segment` con `Piece` a velocidad, `Event` flash/zoom/shake/rgb/push/hold/whip/black que pueden apuntar a un punto, `Text`, volúmenes automatizados de música y juego, y palabras para subtítulos invertidos) que arman los **planificadores puros** y que `render.py` ejecuta en dos pasadas: por plano (geometría y retime cuadrado al cuadro, intermedios mkv con PCM) y final (LUT/look, glow, acentos, subtítulos, ASS, watermark y música). RIFE / Real-ESRGAN por `core/external.py` si están.
  - **Banco de efectos:** [`docs/efectos.md`](docs/efectos.md) y `modules/editing/catalog.json` (`GET /api/editing/catalog`).
    - Eventos nuevos: `panel`, `invert`, `strobe` y `glow`, más el destello `shape="mix"`.
    - `shake` y `whip` toman `angle` (el del golpe del mapa de movimiento).
    - En el `Segment`: `mblur` (cualquier ángulo) y `dedupe` (antes de RIFE). `timing.hit_ramp` pone el cuadro del golpe en su tiempo exacto.
    - **Banco 2** (`editing/bank2.py`): `speedlines` (radiales o paralelas), `impactframe` (bw / inv / poster), `spin`, `zoomblur`, `glitch` (blocks / vhs / flicker), `echo`, `grain`, `satpulse`, `overlay` (assets de `assets/overlays/`), `lens` (con aberración cromática), `tilt` y `shake` con `blur`.
      - Los caros van en **ventanas**: una rama recortada a sus cuadros y superpuesta de vuelta, así fuera de la ventana el cuadro no se toca.
      - En la toma: curvas de velocidad suaves (`timing.velocity`, `velocity_hit`) y pantalla dividida (`Segment.split`: arriba/abajo, lado a lado o diagonal).
    - **Compuestos** (`editing/recipes.py`, de la investigación de los edits más vistos de TikTok): corte con desenfoque, golpe completo, ráfaga de impacto, aislamiento de color y la letra palabra por palabra con los subtítulos invertidos. Primitivas nuevas: `freeze`, `duotone`, `isolate`, `glints`, `blur`. En el preset `manga`: `blur_cuts`, `composites` (momentos en segundos del video), `lyrics_srt` y `resolution` (1080/1440/2160; los px del banco crecen con `effects.px_scale`).
    - Los efectos con duración se prenden por número de cuadro.
    - Vista **Efectos** en Edits: galería con cada efecto sobre la misma toma (`POST /api/editing/gallery`).
    - `follow`: una imagen que sigue al personaje.
      - La trayectoria sale de `motion.subject.track`, con los detectores de anime de deepghs en ONNX y `onnxruntime`, sin PyTorch.
      - Los modelos se bajan de Hugging Face a `cache/models/` la primera vez.
      - `python -m modules.anime_edit.edit4` rearma los logos del pecho del edit 4.
- **competitivo — modo edit** (`POST /api/competitivo/edit`, job nuevo): con la sesión de la fase 1 y una canción, cada kill cae en un beat, cada pelea arranca en un primer tiempo y la pelea clave cae en el drop. Letreros NOMBRE · CAMPEÓN (el feed guarda el campeón; `kills.kill_authors()`), sin la cámara de Ibai por defecto. Queda como `Edit Mapa N - <titulo>.mp4`. **Por defecto `structure=acts`** (v2) en **orden cronológico** (el cold open es un adelanto de 2–3 s de la pelea clave; `short=1` = solo la clave y la de más puntaje, ~20–25 s; `ambient=1` suma planos de escenario y público sin marcador): cold open y build en tiempo real con la voz de Ibai (subtitulada con el layout invertido), 0.3 s de silencio, el drop al ritmo con una **receta por kill** (`ramp`, `freeze_zoom`, `hard_cut`, `instant_replay` en la clave, `stair_zoom` en multikills) y outro con el nexo; el zoom apunta a la acción (`layout.action_focus`) y el status trae el plan por actos. Desde el feedback del 23/10: sin letreros por defecto (`text=off`), `kill_before_s`/`kill_after_s` (3/2 s), `max_duration_s=0` sin tope, `team=T1`, los **picos de emoción de Ibai** en tiempo real sobre el drop (`voice_peaks`, `competitivo/voice.py`), `last_fight_look=bw` y un render que se puede reintentar. `structure=beat` es el edit anterior, todo al ritmo y sin su voz.
- **anime_edit** (`/api/anime-edit`, dos fases): fase 1 analiza la canción y detecta los planos (PySceneDetect o `scdet`) con puntaje y **hojas de cuadros numeradas** para que Claude elija; fase 2 renderiza variantes (`impact`, `velocity`, `cinematic`, `lyrics`) con el héroe en el drop. Salida en `EDITS_FOLDER/<title>/`.
  - El preset **`manga`** son las reglas v1.0 (edit 3 de Magna vs Dante).
    - Recibe **tomas por segundo** (`takes`) con encuadre B/C/A, foco y color frío/cálido, y las parejas panel–escena (`panels`).
    - El panel es un evento nuevo de la EDL que congela el último cuadro de la toma. Los cortes caen en la rejilla de `grid` o del beatmap.
    - Salen además dos `.srt` solo con tiempos.
    - En la fase 1, `shots=0` evita detectar planos en un episodio entero, y `video_path`/`music_path` toman archivos locales.
    - `python -m modules.anime_edit.repro` rearma el edit 3 y lo compara con el original.
    - `python -m modules.anime_edit.long_edit <plan.json>`: el edit largo (la canción entera) desde un plan; plantilla en `anime_edit/examples/`. La letra se alinea con `editing/lyrics_align.py`. El uso, en `docs/edits.md`; la receta, en el PKB (`worlds-2026/receta-edit-largo-anime.md`).
    - Con **`motion=on`** (por omisión `off`, idéntico a las reglas v1.0), cada toma recibe un bloque de `anime_edit/blocks.json` según la curva de la canción y su mapa de movimiento.
      - Los efectos caen en el cuadro de salida de cada golpe real (`fit.out_frame`).
      - `rampa` pone el golpe en el beat.
      - Las transiciones rotan entre al menos 3 tipos.
      - Claude puede fijar bloques con `blocks=[{take, block}]`.
- **montage** (`/api/montage`): capas con alfa (ProRes 4444, VP9, PNG) sobre un video vertical, por segundo o por PALABRA de la transcripción, con las reglas de suiza y Lux; también como `layers` en el `/finish` de Reel Express (misma pasada que los subtítulos).

La regla: **el LLM elige momentos y estilo, nunca tiempos.** Los tiempos los calcula el código.

### 12. studio — `/api/studio` (Estudio, sección Edits)

Las láminas de un deck de **Presentations** encima de un video, **viéndolas en vivo**: entran solas en su palabra, se mueven arrastrando y el render es un clic. Es la fase 2 del brief `PKB/proyectos/personales/reel-editor/brief-estudio-de-montaje.md`; la fase 1 (el «modo capa» de `deck-stage.js` y la API local en el puerto 3125) está en el repo de Presentations. **No tiene motor de render propio** ni llama a FFmpeg (salvo ffprobe para la duración): pide las láminas a Presentations y corre `POST /api/montage/process`. El detalle está en [`docs/edits.md`](docs/edits.md).

- **Una sola fuente de tiempos.** Todo cambio va a `POST /api/studio/place`, que traduce `montaje.json` a las capas de montage (`processor.to_specs`) y las resuelve con **`montage.layers.resolve`** (0.1 s antes de la palabra, tope de 12 s, pegar huecos de menos de 2.5 s, fundido entre pegadas). La vista previa y el render reciben las mismas capas: lo comprueba un test.
- **Dónde entra cada lámina, sin trabajo extra (las reglas):** (1) el guion marcado del deck (`video-out/laminas/guion-marcado.txt`, `⟦lamina:id⟧` antes de la palabra) se alinea con la transcripción (`difflib` sobre palabras normalizadas; Whisper escribe «Liz» por Elise o «16» por dieciséis) y cada marca se vuelve un **ancla en las palabras de la transcripción**: la frase más corta (hasta 4 palabras) que se dice una sola vez, o su n-ésima aparición; (2) si no hay guion, las `palabras` del manifest (`data-palabras`); (3) si no, queda «sin colocar». En LeBlanc: 21 de 21, con 509 de 537 palabras iguales (lo mismo que se logró a mano para CapCut).
- **El guion también ayuda a Whisper:** sus nombres propios van como `initial_prompt` («Vocabulario: LeBlanc, LeBlunK, …»). **Nunca como `hotwords`**: medido en LeBlanc (6/11), con hotwords Whisper repitió «tienden tienden…» y «Y la de Invictus» ×20; con el prompt solo, 534 palabras limpias y «LeBlanc» bien escrito.
- **Transcripción:** la del job base (Reel Express), la guardada junto al montaje, un `.json` cargado a mano o, si no hay ninguna, `POST /api/subtitles/transcribe` con `job_id` = la sesión (el video no se sube dos veces).
- **Vista previa:** un `<video>` con el video base y, encima, un iframe por lámina activa (`<presentations>/<desk>/<deck>/?capa=<id>&alfa=1`). El modo capa entrega la lámina a 1080×1920: el estudio **recorta el panel (1080×1370 desde y = 275, como `laminas.mjs`)** y lo pone donde lo pone el grafo de FFmpeg de montage (`processor.preview_geometry`: el lado escalado redondeado a par como `scale=w:-2`, y x/y bajados a par como el overlay sobre yuv420p, así un recuadro de 706 px centrado cae en x = 186; comprobado contra FFmpeg). En cada `requestAnimationFrame` manda `{tipo:'seek', t: video − inicio}` a cada iframe activo (solo al origen de Presentations, y solo acepta `listo` de ese origen); fundidos y orden de capas como los overlays de montage. Precarga la siguiente y guarda hasta 5 iframes. La banda difuminada es aproximada (CSS `blur`); el render la hace exacta.
- **Línea de tiempo:** arriba las palabras (las ancladas en lima), abajo un bloque por lámina. Soltar un bloque sobre una palabra lo **ancla a esa palabra** (frase + aparición), no al segundo; arrastrar su borde derecho fija `hasta` (doble clic: vuelve a la regla). Si dos se enciman, vuelve a su lugar con el error de montage. «Movida en N ms» mide desde que se suelta hasta que se ve: **29 ms** con el iframe cargado y **463 ms** cargándolo (LeBlanc, 6/11).
- **Formato «banda inferior»** (desde el 6/11, Ambessa): el deck declara su panel con `data-recorte` y el manifest lo trae (`recorte: "1080:680:0:1240"`, la franja libre debajo del gameplay con el ajuste −10; `caja` `{1080, center, bottom: 0}`). La vista previa recorta cada lámina con **su** recorte (`processor.parse_crop`; sin él, 1080×1370 desde y = 275). El prereel va **sin watermark**: la banda es del deck. La lámina con `reposo: true` (`data-reposo`) no se coloca: **llena cada hueco** entre las demás (`processor.fill_rest`, ≥ 0.3 s), pegada a sus vecinas para que montage las funda; `"reposo": false` en el montaje la apaga. Como puede salir varias veces, cada capa resuelta trae una `key` (`id` o `id@inicio`). En Ambessa no hay huecos (las 23 van pegadas), así que no aparece.
- **Guardar:** automático en cada cambio, en `EDITS_FOLDER/Estudio/<video> · <huella>/` (`montaje.json` + `transcripcion.json`). La huella (tamaño + primer y último MB) reconoce el mismo video aunque se suba otra vez o con otro nombre, y al abrirlo se carga su montaje.
- **Renderizar:** `POST <presentations>/api/render {desk, deck, laminas, alfa: webm, fps: 30}` (solo rehace las que cambiaron) → espera su `carpeta` → `POST /api/montage/process` con `job_id` = la sesión, `layers` = las capas de `/place` y `layers_folder` = esa carpeta. Al terminar, el botón **Vista previa / Render** cambia entre los dos en el mismo segundo.
- **Vista previa = render (LeBlanc, 6/11), 5 cuadros de control** capturados en el navegador: cruce entre dos láminas, animación a la mitad, lámina congelada al final de su animación, hueco sin lámina y la última animándose. Mismo contenido y misma posición (el mejor alineado es 0,0 px); 41 dB sin lámina y 27–33 dB en el panel, por la nitidez de los bordes (lanczos de FFmpeg contra el escalado del navegador) y la compresión VP9 (brillo medio 2/255 más bajo). Al saltar a un punto nuevo, la lámina tarda ~0.5 s en aparecer (carga del iframe).
- **Sesiones:** cada video abierto es un job terminado (`kind: studio`, `output_path` = el video), así montage y subtitles lo toman como `job_id`. Se guardan en `cache/studio/<id>.json`: si el server se reinicia (el modo debug recarga al cambiar código), `GET /session/<id>` la recupera; la UI lo llama antes de transcribir y antes de montar.

```
GET  /api/studio/config               → {presentations_url}
POST /api/studio/open                 video | library_clip | job_id → {studio_id, name, duration, video_url,
                                        fingerprint, base, folder, segments, montaje}
GET  /api/studio/session/<id>         → recupera la sesión después de un reinicio
GET  /api/studio/video/<id>           → el video base (Range)
GET  /api/studio/script?desk=&deck=   → {script, marks, prompt}: el guion marcado y la pista de Whisper
POST /api/studio/segments  (JSON)     studio_id, segments → {words}
POST /api/studio/place     (JSON)     studio_id, montaje, manifest, [script, auto, words]
                                      → {montaje, layers (con geometry), specs, unplaced, source, alignment, words, ms}
POST /api/studio/save      (JSON)     studio_id, montaje, [manifest] → {path}
```

**`montaje.json` v1** (`docs/contratos/montaje.schema.json`, JSON Schema 2020-12; `schema.validate_montaje` lo valida con `jsonschema` al colocar y al guardar, más lo que el schema no sabe: que la lámina exista en el deck y no se repita). Claves en español: es el contrato del brief, compartido con Presentations. `base` (`job_id` | `library_clip` | `archivo`, + `huella`, `duracion`), `deck` (`desk`, `deck`, `servidor`), `caja` (opcional: la caja por omisión de todas las capas, pisa la del manifest; LeBlanc usa la de Cumplelolero, 706 px a 14 px del borde; un deck con banda inferior trae la suya), `reposo` (bool, por omisión true), `capas` (`lamina` + `ancla {palabra, n}` **o** `desde`; `hasta` palabra, segundos u omitido; `caja` propia o `"banda"`), `subtitulos`, `render` (`formato`, `fps`).

### 13. pasarela — `/api/pasarela` (pipeline, lo opera Claude)

Un video narrado de punta a punta, como «El draft de Tom» (10/11/2026): gameplay vertical arriba, las láminas de un deck de Presentations en una **banda** de 1080×680, la voz y clips encima. Sin uploads ni UI (la UI es opcional y va después): **JSON con rutas locales**. Reúne lo que ya existe sin duplicarlo: la alineación del guion marcado, el `initial_prompt` y la regla del reposo de **studio**; las reglas de las capas de **montage** (`layers.resolve`); el grafo de **vertical_convert** (HUD y `main_clip_offset`); el contrato **montaje.json**.

```
gameplay (archivo │ highlight de Insights) ─vertical (caché por huella)─┐
voz ─Whisper (caché) + guion marcado → línea de tiempo ─────────────────┼─ encode final → mp4 + reporte
láminas + reposo ─banda aplanada (en paralelo con la base) ─────────────┘
clips ({clip:id} del guion) ─ la voz se pausa y suenan completos
```

| Archivo | Qué hace |
|---|---|
| `plan.py` | La línea de tiempo, **pura**: dónde entra cada lámina (studio + montage), dónde se pausa la voz y cuánto dura el video |
| `render.py` | Los comandos de FFmpeg (constructores puros): base, tramos de banda, pase final, geometría de la banda y del watermark |
| `insights.py` | La base de Insights Capture (copia, solo lectura), las grabaciones y las ventanas del highlight |
| `schema.py` / `processor.py` / `routes.py` | El pedido, la orquestación con cachés, tiempos por fase y reporte, y la API |

**El pedido (`POST /process`, JSON):**

| Campo | Default | Notas |
|---|---|---|
| `gameplay` | — | `{archivo}` o `{insights: {grabacion, antes: 8, despues: 3, eventos: ["kill","assist"]}}` (`grabacion`: el nombre del archivo o el uuid) |
| `voz` | — | el audio ya con su velocidad final |
| `deck` | — | `presentacion-diego/creator/<deck>`: lee `video-out/laminas/` (o `laminas-arriba/` con la banda arriba) con su `manifest.json`, `guion-marcado.txt` y las entradas `tipo: "clip"` |
| `banda` | `abajo` | `abajo` (y=1240) · `arriba` (y=0); alto 680 |
| `watermark` | null | ruta de la imagen |
| `subtitulos` | false | `true` o un objeto con el estilo de `SubtitlesParams` (`position_y` por omisión: 1200 abajo / 1660 arriba) |
| `salida` | — | ruta del mp4 final (se escribe en `.part.mp4` y se renombra al terminar) |
| `clips` | {} | `{id: ruta}` o `{id: {archivo, modo: "pausa" \| "encima"}}`: pisa o completa los clips del manifest (los de Caedrel de Tom no estaban en él) |
| `volumen_gameplay` | 0 | % del audio del gameplay |
| `pista_whisper` | false | los nombres del guion como `initial_prompt` (ver «Lo medido»); encenderla cuando lleva subtítulos, donde se ven los nombres |

**Las reglas (el estándar aprobado; no se cambian sin avisar):**
- **Láminas:** entran 0,1 s antes de su palabra; duran hasta la siguiente, con tope de 12 s, y se pegan a ella si después del tope faltan menos de 2,5 s; fundido de entrada 0,25 s y de salida 0,2 s; pegadas, la anterior queda debajo mientras entra la nueva (todo eso es `montage.layers.resolve`). La portada entra en el 0. El reposo está **debajo de toda la banda** desde el 0 (se anima una vez y queda en su último cuadro): llena cada hueco. La última lámina se queda hasta el final sin fundido.
- **Clips (`{clip:id}` en el guion):** por omisión **la voz se pausa** en la mitad del silencio entre la última palabra antes de la marca y la siguiente; el clip entra entero con 0,15 s de respiro a cada lado y todo lo que sigue se corre. Un clip sin marca con `despues` en el manifest va antes de la lámina que sigue a esa. 16:9 → dentro de la banda (1080×608, centrado en sus 680); 9:16 → pantalla completa, sin banda ni watermark. La lámina anterior a un clip termina donde empieza el clip (sin estirarse debajo). Volumen: ganancia = LUFS de la voz − LUFS del clip. `modo: "encima"`: el clip sobre la voz, sin pausa, con su audio igualado y al 22 %.
- **Duración:** la voz + las pausas + 0,3 s (Tom: 166,10 + 18,80 + 0,30 = 185,20).
- **Base:** banda abajo → `main_clip_offset` −10 (el gameplay de 297 a 1239, la placa del HUD encima; la banda empieza en 1240). Banda arriba → el espejo, **calculado**: el offset que deja la placa del HUD 2 px bajo la banda (`render.vertical_params`; +13,5 % con una fuente 1920×1080: placa 682–763, gameplay 748–1690). El audio del gameplay va en silencio.
- **Watermark:** 150 px de alto, centrado; y=70 con la banda abajo, y=1700 (70 px del borde de abajo) con la banda arriba.
- **Audio final:** mezcla sin normalizar y `alimiter` a −1 dBFS.

**Rendimiento (Tom, RTX 5070 / Ryzen 5 9600X):**
- **Base en una sola pasada** desde Insights: cada ventana es su propia entrada con `-ss`/`-t` (solo se decodifican los cortes, con NVDEC) y el concat alimenta el grafo de vertical_convert. **78 s** contra 115 s del highlight + 76 s de la conversión vertical por separado. Caché por huella del archivo + ventanas + parámetros: montar otra vez con otra voz no la rehace.
- **Banda aplanada:** un FFmpeg chico por tramo (el reposo, la lámina anterior mientras entra la nueva y la nueva con sus fundidos), 4 a la vez, sin pérdida, unidos con el demuxer concat. Ningún filtro tiene las ~30 láminas a la vez. Láminas opacas (`alfa: false`, lo que exporta Presentations desde el 10/11) → **H.264 NVENC sin pérdida**; con alfa → FFV1. Corre **en paralelo con la base**.
- **NVDEC donde conviene:** la base de 1080×1920 en el pase final (60 s: 2,3 s contra 3,1 s en la CPU). Las láminas opacas se leen en la CPU **a propósito**: cada tramo es un FFmpeg corto y abrir CUDA cuesta más que decodificar 1080×680 (28 tramos: 11,2 s con NVDEC, 10,0 s sin).
- **Un solo encode final:** h264_nvenc p7, cq 16, AAC 256k.
- **Medido:** en frío **131 s** (base 78 ∥ banda 17, Whisper 13, final 38) contra ~233 s de máquina del método a mano (highlight 115 + vertical 76 + Whisper 8 + pase final 34 con las mp4 de hoy; con las webm con alfa era más); con la base en caché, **49 s**. El pase final está limitado por NVENC p7 en los dos casos.

**El reporte (`GET /report/<job>`, también en `cache/pasarela/reports/<job>.json`):** `alineacion` (palabras del guion, alineadas, %), `capas` (id, tipo, inicio, fin, `debajo_hasta` si va pegada, fundidos), `clips` (modo, pantalla, inicio, fin, voz_en, LUFS, ganancia), `pausas` (clip, voz_en, video_en, duración), `reposo` (huecos ≥ 0,3 s), **`sin_colocar`** (láminas y clips sin lugar: nunca en silencio) y `marcas_desconocidas`, `audio` (LUFS de la voz y del final, pico), `base` (fuente, ventanas, offset, si salió de la caché), `transcripcion` (prompt, `palabras_corregidas`), `banda_pista` (tramos y códec), `tiempos` (segundos por fase y total), `avisos` y `montaje` (las láminas como **montaje.json v1**, en segundos del video final, validado con `studio.schema.validate_montaje`).

**Lo medido sobre Tom (10/11):**
- Con `pista_whisper: false` la transcripción es **idéntica palabra por palabra** a la del video aprobado y las 31 capas coinciden con las de CapCut al centésimo (duración 185,20). La imagen difiere en ≤ 1 cuadro a 60 fps en la entrada de los clips (entran en el primer cuadro después de su segundo; en CapCut, uno después) y el audio es el mismo (desfase 0 ms).
- Con la pista (la regla de studio; **apagada por omisión** desde el 10/11, decisión de Axl), Whisper corre algunas palabras: 22 capas quedan a ±0,05 s y 9 se mueven hasta 0,2 s. En una de esas el video aprobado era el equivocado: «Y el puto titular» suena en 154,78 y la transcripción vieja decía 154,56.
- **Pausas tragadas** (`plan.snap_starts`): con la pista, Whisper estiró «Al» de 27,34 a 28,18 cuando la voz vuelve en 27,98, y «Y» desde 100,50 cuando vuelve en 101,12 (dos láminas entraban 0,6 s antes). Si en los primeros 0,4 s de una palabra hay ≥ 0,25 s de silencio real (< −55 dBFS: la voz de TTS tiene silencio digital entre frases), la palabra empieza donde vuelve el sonido (−40 dBFS, la inhalación, que es donde Whisper empieza las palabras normalmente). Sobre la transcripción del video aprobado no mueve ninguna.

```
POST /api/pasarela/process      JSON (arriba)                    → 202 {job_id}
GET  /api/pasarela/status/<id>  → {status, progress, stage, error, duration, report_ready}
GET  /api/pasarela/report/<id>  → el reporte (409 mientras procesa)
GET  /api/pasarela/download/<id> → el mp4 (ya está en `salida`)
GET  /api/pasarela/recordings   → [{uuid, nombre, archivo, existe, duracion, campeon, kda, resultado,
                                    multikills, eventos}] de Insights Capture, más nuevas primero
POST /api/pasarela/highlight    JSON {grabacion, antes, despues, eventos, [salida]} → 202 {job_id}:
                                    el highlight 16:9 suelto (por omisión en HIGHLIGHTS_FOLDER)
```

**Insights Capture:** la base (`%APPDATA%\Insights Capture\db.sqlite3`) se lee **siempre sobre una copia** (el archivo con su `-wal` y `-shm`, en una carpeta temporal que se borra) y nunca se escribe: la app la tiene abierta. Tablas: `video` (`type='recording'`), `video_game_event` (`timestamp` en segundos de la grabación; `kill`, `assist`, `death`, `victory`, `defeat`) y `video_game_info_update` (`champion`, `kills`, `deaths`, `assists`, `doubleKills`…). Ventanas `[t − antes, t + despues]`, recortadas a la grabación; las que se tocan o se enciman se unen (una doble kill es UN corte). Tom: 18 cortes, 241,4 s. El highlight suelto (`/highlight`) lee solo los cortes (`-ss`/`-t` por ventana, con NVDEC): **26 s** contra 115 s del script a mano, que pasaba la grabación entera de 29 min por `trim`.

**Cómo lo opera Claude:** `GET /recordings` para elegir la partida → `POST /process` con el deck exportado (`node tools/laminas.mjs creator/<deck>`, y `--banda=arriba` si va arriba) → poll de `/status` → leer el `/report` (sobre todo `sin_colocar`, `avisos` y `alineacion`) y revisar cuadros sueltos. Tests: `tests/pasarela/` (alineado, pausas, ventanas, base de Insights sin escribir, tramos de banda, geometría y una corrida entera sobre archivos sintéticos con Whisper simulado).

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
`status` ∈ `pending | processing | done | error`. **Excepciones de dos fases:** `subtitles` usa `/transcribe` + `/render`; `reel_express`, `hook_reel`, `competitivo` y `anime_edit` usan `/process` + `/finish` (competitivo suma `/edit`, que abre un job nuevo). `studio` no procesa: abre sesiones (`/open`), coloca (`/place`) y guarda (`/save`); su render es el de montage. `pasarela` recibe JSON (no un form) y suma `/report`, `/recordings` y `/highlight`. Global: `POST /api/cleanup` vacía las carpetas temporales.

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
expr_sum(terms) -> str                    # suma de términos de una expresión de FFmpeg,
                                          # en árbol balanceado (ver abajo)
capture(command) -> bytes                 # corre FFmpeg y devuelve su stdout (frames en
                                          # rawvideo para analizar con numpy)
stream(command, frame_bytes)              # lo mismo cuadro por cuadro (generador): un tramo
                                          # largo se analiza sin tenerlo entero en memoria
```

**Dos límites de FFmpeg que en la nube no aparecían:**
- **Largo del comando (solo Windows).** Windows corta la línea de comando entera en 32 767 caracteres; Linux, cada argumento en 128 KB. Un edit con muchos eventos o el preview de beats de una canción larga no llegaban a arrancar (`WinError 206`). `run()` y `capture()` pasan solos los grafos de más de 8 000 caracteres por un archivo temporal (`-/filter_complex <archivo>`, FFmpeg 7+) y lo borran después.
- **Profundidad de una expresión (todos los sistemas).** El parser de expresiones de FFmpeg corta en ~100 niveles, y una suma plana `a+b+c…` tiene tantos niveles como términos: 100 sacudidas en un edit o los ~400 beats de cualquier canción daban `Error when parsing the expression`. Toda suma larga dentro de una expresión (`enable=`, `eq`, `zoompan`) va con `expr_sum()`, que la arma en árbol balanceado (10 000 términos funcionan).

**Todo módulo que encodea usa `video_encode_flags()`**: es lo que hace que la GPU
se use en todos lados sin repetir la detección.

**Límite de NVENC.** Las GeForce admiten pocas sesiones de NVENC a la vez: con 7 edits juntos, 4 fallaron con `h264_nvenc` "Invalid argument (-22)". `run()` y `capture()` dejan pasar como mucho `NVENC_MAX` (el turno "nvenc" de `core/scheduler.py`: 2 por defecto, medido: con 2 renders a la vez ya se llega al máximo de la CPU y el primero termina antes que con 4) encodes con `h264_nvenc` al mismo tiempo (el resto espera) y, si uno falla por NVENC igual, lo repiten con libx264 (`cpu_fallback`).

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
EDITS_FOLDER                       # edits de anime y de series sin día, PERSISTENTE (env REELFORGE_EDITS_FOLDER)
BEATMAPS_FOLDER                    # cache/beatmaps/ (análisis de canciones)
MOTION_FOLDER                      # cache/motion/ (mapas de movimiento por huella + tramo + ancho + versión)
SESSIONS_FOLDER                    # cache/competitivo/sessions/ (fases 1 de competitivo, sobreviven a un reinicio)
CALIBRATION_FOLDER                 # casos reales de calibración, fuera de git (env REELFORGE_CALIBRATION_FOLDER)
ANCHORS_FOLDER                     # cache/competitivo/anchors/: anclas ya medidas por (VOD, partida)
SLOTS                              # turnos por recurso: {"twitch": 4, "whisper": 1, "analysis": 2, "nvenc": 2}
                                   # (env REELFORGE_SLOTS_TWITCH, …_WHISPER, …_ANALYSIS, …_NVENC)
LUTS_FOLDER                        # assets/luts/ (.cube)
LAYERS_FOLDER                      # raíz de las carpetas de capas del montaje (env REELFORGE_LAYERS_FOLDER, default ~)
STUDIO_FOLDER                      # EDITS_FOLDER/Estudio: montaje.json + transcripcion.json por video, PERSISTENTE
STUDIO_SESSIONS_FOLDER             # cache/studio/: las sesiones abiertas del estudio (sobreviven a un reinicio)
PRESENTATIONS_URL                  # el servidor local de Presentations (env REELFORGE_PRESENTATIONS_URL, default http://localhost:3125)
PASARELA_CACHE                     # cache/pasarela/: bases verticales por huella, transcripciones, tramos de banda, reportes
INSIGHTS_DB                        # la base de Insights Capture, SOLO lectura y sobre una copia (env REELFORGE_INSIGHTS_DB)
HIGHLIGHTS_FOLDER                  # los highlights sueltos, PERSISTENTE (env REELFORGE_HIGHLIGHTS_FOLDER, ~/Videos/ReelForge/Highlights)
RIFE_PATH / REALESRGAN_PATH        # binarios opcionales (env REELFORGE_RIFE / REELFORGE_REALESRGAN o el PATH)
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

- Registra los 20 blueprints + `/` (index) + `POST /api/cleanup`.
- **La API siempre responde JSON:** `errorhandler` para `RequestEntityTooLarge` (413), `HTTPException` y `Exception` (500) — sin esto el frontend rompía al parsear el HTML de error de Flask.
- `SEND_FILE_MAX_AGE_DEFAULT = 0`: no cachea estáticos en dev, así el browser toma el JS/CSS recién editado.

---

## UI

`index.html` es un **app shell**: una **sidebar dividida en secciones** (`.nav-section`: Automático / Edits / Módulos / Tools Specific videos / Tools / Sistema) con un nav item por módulo, y un `<section class="module-view">` por módulo (parciales con `{% include %}`). `app.js` muestra una vista a la vez (busca `.nav-item`). La vista por defecto es **Reel Express**.

Cada módulo trae su propio JS y usa `runJob(apiBase, formData, {onProgress, onDone, onError})` de `shared.js` (POST + polling). Los flujos de dos fases (subtitles, reel_express, hook_reel) usan un poller propio porque el status trae `segments`. `shared.js` además concentra lo compartido entre módulos: el estilo de subtítulos (`SubStyle`: llena el parcial `templates/modules/_sub_style.html`, que Subtítulos, Reel Express y Reel Frase incluyen con prefijos `sub-`/`rx-sub-`/`hk-sub-`; arma los params; recuerda el estilo en localStorage y lo sincroniza entre módulos; dibuja la muestra animada apoyada en la misma línea base que libass), el preview de la placa del HUD en canvas (`drawHudPlate`), el selector de archivos (`openFilePicker`) y la galería de la librería de clips (`openClipLibrary` / `appendClip`). Las vistas **Info**, **Historial** y **Métricas** no procesan nada: solo leen su endpoint y pintan (Métricas además lanza la calibración). El **editor de segmentos** (`SubtitleEditor` en `subtitle_editor.js`) es compartido por subtitles y reel_express. Sin librerías frontend externas (solo Vanilla JS).

---

## Dependencias del sistema

- **FFmpeg** y **ffprobe** en el PATH (o resueltos por `config.py`). ffprobe es necesario para el speed match, resoluciones y duración exacta; sin él, varias features degradan en vez de crashear.
- **yt-dlp** (pip) para el downloader. Se actualiza seguido: `pip install -U yt-dlp`.
- **fonttools** (pip) para leer familia y métricas de las fuentes de subtítulos.
- **librosa** (pip) para beatmap y **scenedetect + opencv-python-headless** para los planos de anime_edit (sin scenedetect cae al `scdet` de FFmpeg).
- **Opcionales** de los edits: `beat-this` (beats con red neuronal), `audio-separator` (voz para la letra), y los binarios `rife-ncnn-vulkan` / `realesrgan-ncnn-vulkan`. `GET /api/editing/options` dice cuáles hay.
- **Pillow** (pip) para dibujar las tarjetas de jugador (`modules/competitivo/cards.py`).
- **onnxruntime** (pip, opcional) para seguir personajes (`motion/subject.py`, evento `follow`): los detectores de deepghs se bajan solos de Hugging Face a `cache/models/deepghs/` (~85 MB). Sin él, todo lo demás funciona.
- **jsonschema** (pip) para validar el `montaje.json` del Estudio contra su contrato.
- **El servidor local de Presentations** (`node tools/servidor-local.mjs` en ese repo, puerto 3125) para el Estudio: sin él, la vista lo avisa y no lista decks.
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

## Dónde va cada cosa (decisión de Axl, 07/11/2026)

- **En este repo, el motor y los recursos de cada TIPO de video**, no de cada video: un módulo por tipo (`tf_explained`, `anime_edit` con `long_edit`, `competitivo`…) y lo que reutiliza en `assets/`. Un video concreto es un archivo de datos (un plan JSON, un guion), nunca un script propio. Si hay que escribir un script para un video, lo que sirva para el siguiente se sube al módulo de su tipo.
- **En el PKB, el conocimiento**: la receta de cada tipo de video (cómo se hace, qué se aprendió, los errores) en `redes-sociales/creador-de-contenido-axelsine/worlds-2026/receta-*.md`. Aquí, en `docs/`, solo cómo se usa el código.
- **Fuera de git**: los videos, audios y episodios (pesan y tienen derechos). Las salidas van a `EDITS_FOLDER` / `COMPETITIVO_FOLDER`.
- **Las láminas** siguen en el repo de Presentations: el Estudio las lee de ahí.

## Idioma del código

Desde el 22 de octubre de 2026 **el código nuevo se escribe en inglés**. La documentación y lo que ve el usuario siguen en español.

| Qué | Idioma |
|---|---|
| Identificadores, nombres de archivos y carpetas, rutas de la API, campos de los formularios y claves del JSON | Inglés |
| Comentarios y docstrings | Inglés |
| Mensajes de commit | Inglés |
| Mensajes de error, `stage` de los jobs y textos de la UI | Español |
| `CLAUDE.md`, `README` de `assets/` y `docs/` | Español |

- **Ya están en inglés:** `beatmap`, `motion`, `editing`, `anime_edit`, `montage`, `core/external.py`, el modo edit de competitivo (`competitivo/edit.py`, `EditParams`, `/edit` y lo que suma en `kills.py` y `layout.py`) y sus tests (`tests/synthetic.py`).
- **El código viejo en español** (competitivo, subtitles, reel_express…) **se migra cuando se toca**, no en un refactor aparte. Al tocarlo, se traduce lo que el cambio toca y se deja el resto como está.
- **Los contratos públicos no se rompen al traducir.** Un campo del formulario, una clave del status o una ruta que ya consume algo (la UI, Claude, otro repo) se cambia solo junto con quien la consume, en el mismo cambio. Por eso el modo edit sigue aceptando `carpeta_dia`, `partido`, `titulo`, `mapa`, `anchors` y `marca_alto` (los comparte con `/finish`) y su status sigue usando `resultado`, `aviso` y `modo`.
- Lo que viene de fuera conserva su forma: el `manifest.json` de presentations (`archivo`, `laminas`) y las palabras en español de las transcripciones (los números escritos de `montage/layers.py`).

## Convenciones de desarrollo

- **Commits por bloques lógicos**, con prefijos `feat(modulo): …` / `fix: …` / `chore: …` / `refactor: …` / `docs: …`. En inglés (ver «Idioma del código»).
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
