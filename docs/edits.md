# Edits al ritmo de la música

Cuatro piezas que se apoyan una en otra:

```
beatmap ──────────────► editing (motor) ◄────── competitivo/edit.py  (las partidas de Worlds)
(ritmo de la canción)   EDL → render          ◄────── anime_edit/plan.py   (una pelea de anime)
                                                         montage (capas animadas, aparte)
```

- **beatmap** dice CUÁNDO pegan los golpes de una canción.
- Los **planificadores** (competitivo/edit.py, anime_edit/plan.py) deciden QUÉ va en cada momento y lo escriben como una EDL. Son funciones puras: no tocan FFmpeg y se prueban sin video.
- El **motor** (`modules/editing/`) ejecuta la EDL. No decide nada.
- **montage** pega capas con transparencia (flyers, láminas de presentations) sobre un video ya hecho.

La regla que sale de walkthrough vale acá también: **el LLM elige momentos y estilo, nunca tiempos.** Claude elige planos, héroe y variantes; los cortes, las velocidades y los acentos los calcula el código.

**Idioma.** Todo este código está en inglés (identificadores, comentarios, archivos, rutas, campos de los formularios y claves del JSON); los mensajes de error, las etapas (`stage`) y la UI siguen en español. La excepción son los campos que el modo edit hereda de competitivo (`carpeta_dia`, `partido`, `titulo`, `mapa`, `anchors`, `clips`, `zoom`, `watermark`, `marca_alto`) y las claves que su status ya exponía (`resultado`, `aviso`, `modo`): son el contrato de ese módulo y no cambian. Ver «Idioma del código» en `CLAUDE.md`.

## El edit de referencia

Todo esto sale del análisis de un edit real (Levi Ackerman, @med_uchiha, 2.2 M vistas, 22/04/2026), medido con ffmpeg, PySceneDetect y librosa:

- 66 s, **horizontal** 1280×720, plano mediano de **0.43 s**, una sola pelea de anime.
- Ni los cortes ni los flashes caen en la rejilla de beats más que al azar (52 % contra 52 % a ±67 ms de un medio beat). **Lo que se siente sincronizado es la letra** (una palabra gigante con degradado cuando se canta) **y los golpes del propio anime**.
- En el puente de la canción (40–50 s, el beatmap lo detecta solo) mete una escena de diálogo sin texto: el respiro antes del último coro.
- Gradación cálida dorado-teal muy saturada, brasas, flashes, cámara girada y congelados de 2–3 cuadros antes del golpe.

De ahí los presets de anime_edit (`impact`, `velocity`, `cinematic`, `lyrics`) y el look `golden`.

---

## beatmap — `/api/beatmap`

`modules/beatmap/analysis.py` (puro sobre la señal) + `lyrics.py` + `processor.py` (`get()`, con caché).

| Campo | Qué es |
|---|---|
| `bpm`, `beats`, `downbeats` | tempo y tiempos (s) de cada beat y del primero de cada compás |
| `onsets`, `hits` | ataques; `hits` = los del 20 % más fuerte (para sacudidas) |
| `energy_per_beat` | 0–1 por beat (RMS relativo al máximo) |
| `sections` | `[{type, start, end, energy}]`: `intro`, `verse`, `chorus`, `bridge`, `outro` |
| `drop`, `buildups` | arranque del coro con el mayor salto de energía; los 2 compases antes de cada coro |
| `words` | `[{text, start, end}]` de la letra (vacío si no se pidió o no hay voz) |
| `engine`, `source`, `fingerprint`, `lyrics_from` | qué lo analizó, de qué archivo y si la letra salió de la voz separada (`vocals`) o de la mezcla (`mix`) |

- **librosa** con `hop` de 128 para el pulso: con 512 un 120 BPM salía 117.45 (el período va en cuadros enteros del envelope) y los beats se corrían ~40 ms; con 128 sale 120.2 y ~16 ms.
- **Downbeats**: la fase de 4 donde más pega el bombo (onset bajo 150 Hz). Con **Beat This!** instalado (`pip install beat-this`) y `engine=auto`, beats y downbeats salen de la red.
- **Secciones por energía**: cada compás es alto o bajo según su energía relativa (percentiles 10–90, mediana de 3 compases); los tramos de menos de 2 compases se funden. Tosco a propósito: explicable y suficiente para decidir cada cuánto cortar.
- **Letra**: `audio-separator` (opcional) separa la voz y faster-whisper da cada palabra con su tiempo. Sin separación transcribe la mezcla.
- **Caché** en `cache/beatmaps/<huella>-<engine>-v<versión>[-lyrics…].json`: la misma canción no se vuelve a analizar. La versión (`analysis.VERSION`, hoy 2) cambió con las claves en inglés, así que las cachés viejas se ignoran solas.
- **Preview**: video negro con la canción, un pitido por beat (agudo en el 1 del compás), destellos y la barra de secciones. Para validar la rejilla de oído antes de un edit.

```
POST /api/beatmap/process   audio (archivo) | music_url  [engine, lyrics, separate_vocals, language, model, preview]
GET  /api/beatmap/status/<id>   → {status, progress, stage, error, summary, beatmap}
GET  /api/beatmap/download/<id> → preview.mp4 ·  GET /api/beatmap/json/<id> → beatmap.json
```

## editing — el motor (`modules/editing/`)

`edl.py` (modelo), `timing.py` (curvas de velocidad y cuantizado), `effects.py` (filtros), `titles.py` (letreros ASS), `render.py` (dos pasadas). `GET /api/editing/options` lista `looks`, `luts`, `profiles` y qué opcionales están instalados (`rife`, `esrgan`, `beat_this`, `vocal_separation`).

**EDL**: `Segment` (un plano: archivo + `pieces` + `vf` de geometría) → `Piece(start, end, dur)` (fuente a velocidad constante; `start == end` es un congelado), más `Event(t, kind, intensity, dur, level, x, y)` y `Text(start, end, text, style)` en tiempo de la salida, la música (`music`, `music_start`), el look, el LUT, glow, viñeta y watermark.

- **Eventos**: `flash`, `zoom` (golpe), `shake`, `rgb`, `push` (acercamiento lento de 1.0 a `1+level` a lo largo de `dur`), `hold` (zoom sostenido a `1+level` durante `dur`; la escalera de zoom), `whip` (golpe de zoom + desenfoque de movimiento horizontal que empieza 0.08 s ANTES del corte) y `black` (cuadro negro). `x`/`y` (0–1 de la salida) dicen a dónde apunta el zoom: el centro del cuadro se corre hacia ahí en proporción a la envolvente del movimiento, así vuelve solo al centro.
- **Volumen automatizado**: `music_gain` / `original_gain` son `[(t, ganancia)]` lineales entre puntos (`volume=…:eval=frame`); vacías = los volúmenes fijos de siempre.
- **Subtítulos invertidos**: `subtitle_words` (`{word, start, end}` en tiempo de la salida) + `subtitle_style` (los campos `inv_*` y `font` de `SubtitlesParams`) se queman con el layout `invert` de Subtítulos, cortados en `subtitle_until`.

**Render**:
1. **Pasada 1, por plano** (3 en paralelo): geometría + cada pieza recortada, retemporizada y cuadrada al cuadro exacto (`trim=end_frame`). Intermedio `.mkv` H.264 con audio PCM: con AAC cada unión corría el audio (lo que se midió en competitivo).
2. **Pasada 2**: concat + LUT/look + glow + flash (`eq brightness` con `eval=frame`: por cuadro, no por píxel) + todos los movimientos de cámara (un solo `zoompan`, `d=1`) + desenfoque de los whips + RGB (`rgbashift` con `enable`) + negro + viñeta + subtítulos invertidos + ASS + watermark, con la música y el juego (volúmenes automatizados, fundido al final, limitador).

`timing.quantize()` lleva cada borde al cuadro más cercano de su tiempo ACUMULADO ideal: el error no se arrastra, un golpe planeado en 31.250 s a 60 fps cae en el cuadro 1875.

**Letreros** (`titles.py`): `word` (la letra, gigante, con degradado: libass no tiene degradados y se dibuja la palabra en 12 franjas con `\clip`, sobre una copia con contorno y sombra), `kill` (NOMBRE + campeón), `multikill` (QUADRA KILL estampado), `title` y `sub`. Se centran por la altura de las MAYÚSCULAS, no por la caja de la línea (en Anton caían ~25 px más abajo).

**Looks**: `neutral`, `vivid`, `golden` (el de referencia), `film`, `cold`, `night`, `fire`. LUTs propias en `assets/luts/*.cube` (`lut`).

**Opcionales** (`core/external.py`, binarios ncnn-vulkan, corren en cualquier GPU y en Mac):
- **RIFE** (`rife-ncnn-vulkan`): interpola los planos con piezas más lentas que 0.6× (cámara lenta fluida en vez de cuadros repetidos).
- **Real-ESRGAN** (`realesrgan-ncnn-vulkan`, modelo `realesr-animevideov3`): escala fuentes de menos de 1080p.
Sin ellos todo funciona; `REELFORGE_RIFE` / `REELFORGE_REALESRGAN` apuntan al ejecutable si no está en el PATH.

## competitivo — modo edit (`POST /api/competitivo/edit`)

Las peleas de la fase 1 al ritmo de una canción. Con algo que un edit de anime no tiene: **los datos**. Cada kill ya está ubicada al cuadro (feed + contador del marcador), así que no se busca en el video qué coincide con la música: **se fuerza que cada kill caiga en un beat** estirando o comprimiendo el tramo de antes.

- Cada pelea: **entrada** (1 compás, 2 si hay mucho antes), **kills** separadas por `n` beats (lo más cerca de la velocidad base, entre 0.5× y 2.5×) y **cola** hasta cerrar el compás. Así cada pelea arranca en un primer tiempo.
- La **pelea clave** (la de cierre, o la de más puntaje) se ajusta para que su última kill caiga en un primer tiempo, y **la música arranca de modo que esa kill caiga en el drop**.
- Si se pasa de `max_duration_s` salen las de menos puntaje (nunca la clave).
- En cada kill: flash, golpe de zoom y sacudida (más fuertes en la clave), RGB en multikills; letrero **NOMBRE · CAMPEÓN** (el feed guarda el campeón en `Partida.champions` y `kills.kill_authors()` reparte las kills por jugador) y TRIPLE / QUADRA / PENTAKILL.
- Layout: el de siempre, **sin la cámara de Ibai** por defecto (en su lugar, el gameplay difuminado; `layout.edit_geometry()`); `with_camera=1` la deja.
- **Quita la voz de Ibai** (el audio es la música, `game_volume` opcional) y es una obra propia: lo que pedía la política de Riot.

```
POST /api/competitivo/edit   job_id (de la fase 1) + music (archivo) | music_url
     [music_start (s | m:ss | auto), align_drop, style (impact | velocity | clean),
      text (kills | lyrics | both | off), speed (0.5–2), max_duration_s (10–180),
      intensity (0–1.5), look, lut, glow, with_camera, zoom, game_volume, kill_lead_s,
      music_accents, engine, edit_title, watermark, marca_alto, anchors, clips, carpeta_dia,
      partido, titulo, mapa]                                            → 202 {job_id NUEVO}
GET  /api/competitivo/status/<id> → … resultado {video, folder, name, download_name, total_duration,
     music_start, drop_aligned, bpm, used_clips, dropped_clips, warning, edl}
GET  /api/competitivo/download/<id>
```

Queda junto a los resúmenes como `Edit Mapa N - <titulo>.mp4` (no empieza con "Mapa": `publicar()` nunca lo descarta) con un `.json` al lado (la EDL), o en `EDITS_FOLDER/<fecha> <A> vs <B>/` sin día ni partido. `kill_lead_s` (0.25 s) corre cada kill hacia antes: el contador del marcador sube un poco después del golpe; **calibrar con un edit real**.

### structure=acts (por defecto desde v2)

Menos lineal y con la voz de Ibai. `structure=beat` es todo lo de arriba, tal cual.

| Acto | Qué es | Música | Juego + Ibai |
|---|---|---|---|
| `cold_open` | la pelea de más puntaje en tiempo real, `cold_open_s` (5–8 s, 6.5) con su mejor momento al 70 % | 15 % | 100 % |
| `build` | `build_fights` (0–2) peleas más en tiempo real (2.5 s antes de la 1ª kill → 1.5 s después de la última, máx. 7 s) | 20 % | 100 % |
| `silence` | 0.3 s de negro y mudo | 0 | 0 |
| `drop` | arranca EXACTO en el drop de la canción; el resto de las peleas al beat, con una receta por kill y whips entre peleas | 100 % | 15 % |
| `outro` | la cola de la pelea de cierre (el nexo) en tiempo real; el juego vuelve a subir y la canción cierra en su compás (fundido) | 100 % | 15 → 100 % |

- En `cold_open` y `build` hay un **push-in** lento (1.0 → 1.12×) apuntando a la acción de la kill más grande, y **whips** entre los planos. Los cambios de volumen tardan 0.05 s (sin clics).
- **Recetas por kill** (`recipes`: `auto` o lista, p. ej. `ramp,hard_cut`), con un hash fijo (misma serie → mismo edit) que nunca repite dos veces seguidas:
  - `ramp`: 0.3× en la kill → 1× → 1.5× hacia la siguiente;
  - `freeze_zoom`: 0.15 s congelado con zoom 1.3× sostenido;
  - `hard_cut`: sin cámara lenta, solo el golpe;
  - `instant_replay`: SOLO en la última kill de la pelea clave: se ve, rebobina 1.5 s y la repite a 0.4× con zoom 1.6× (la cola se alarga hasta cerrar el compás);
  - `stair_zoom`: peleas con 3+ kills: una kill por beat, con zoom 1.1 / 1.2 / 1.3 / 1.4×.
- **El zoom apunta a la acción** (`layout.action_focus`): el centroide del movimiento (diferencia entre cuadros, el 5 % que más se mueve) dentro del gameplay alrededor de cada kill, llevado a coordenadas de la salida. Si no se mueve nada o se mueve todo (la cámara del observador paneando), el centro.
- **La voz de Ibai con subtítulos invertidos** (`voice_subs`, `voice_language`=es): cada plano en tiempo real se transcribe con faster-whisper y sus palabras se queman con el layout `invert` (los mismos campos `sub_inv_*` y `sub_font` que Subtítulos), solo hasta el silencio: **nunca encima de la letra**, que en este modo sale recién desde el drop.
- El status (y `resultado.plan`) trae el **plan por actos** (`acts: [{act, start, end, clips, music, game}]`) y la **receta de cada kill** (`kills: [{clip, t, recipe, count, player, champion}]`), para revisarlo; `resultado.subtitle_words` dice cuántas palabras de Ibai se quemaron.

```
POST /api/competitivo/edit   … [structure (acts | beat), recipes (auto | lista), cold_open_s (5–8),
      build_fights (0–2), voice_subs, voice_language, sub_inv_size, sub_inv_y, sub_inv_direction,
      sub_inv_words, sub_font]
GET  /api/competitivo/status/<id> → … plan {structure, acts, kills}
```

**Como lo opera Claude:** fase 1 (`/process` con `modo=revisar`) → `/edit` con el `job_id` y la canción → poll de `/status/<nuevo>` hasta `finished=true`.

## anime_edit — `/api/anime-edit` (dos fases)

Una pelea de anime + una canción → varias variantes.

**Fase 1** (`/process`): video (`video`, `library_clip` o `video_url`) y canción (`music`, `music_url` o `music_from_video=1`); beatmap (con la letra por defecto; `lyrics=0` la apaga, `language`, `engine`) y **planos** (`shots.py`):
- Cortes con PySceneDetect (`AdaptiveDetector`) o, si no está, el `scdet` de FFmpeg. Los planos de menos de 0.3 s se funden (los impact frames del anime parecen cortes).
- Por plano (`{id, start, end, dur, peak, motion, brightness, saturation, flashes, score}`): movimiento, brillo, saturación, destellos y un puntaje 0–100; los que son sobre todo blanco o negro (impact frames, fundidos) se penalizan.
- **Hojas de cuadros** numeradas (8×6) y una miniatura por plano: lo que mira Claude.
- Queda en `stage="esperando_seleccion"` con `shots`, `proposal` (los 24 de más puntaje, en orden), `hero`, `sheets`, `beatmap` y `video_duration`.

**Fase 2** (`/finish`): `selection` (ids en orden), `hero`, `variants`, `format` (`vertical` con fondo difuminado, `vertical_fill`, `horizontal`), `duration_s`, `music_start`, `text` (`auto` | `lyrics` | `off`), `look`, `lut`, `intensity`, `fps` (24/30/60), `interpolate`, `upscale`, `original_volume`, `watermark`, `watermark_height`, `title`. Al terminar, `result: {folder, variants: [{variant, video, name, duration, cuts, hero, drop, words, rife, esrgan, …}]}`.

| Preset | Cortes | Velocidad | Acentos | Texto / look |
|---|---|---|---|---|
| `impact` | coro cada beat, resto cada 2, puente cada 4, subida cada medio beat | constante | flash en los compases del coro, zoom en los cortes del coro, sacudida en los golpes | letra / golden |
| `velocity` | cada 2 beats | lenta al arrancar el plano, rápida después | flash en cada corte, zoom en cada beat del coro, RGB en los cortes del coro | letra / vivid |
| `cinematic` | cada compás (coro cada 2 beats) | respira (lenta en el medio) | solo el drop | sin texto / film |
| `lyrics` | en cada palabra cantada (compases donde no hay voz) | constante | drop, zoom en cortes del coro | letra / golden |

En todos, el **plano héroe va en el drop** un compás, congelado 0.1 s y en cámara lenta (RIFE si está). La ventana de la canción arranca en un compás de modo que el drop caiga hacia el 35 % del edit, y termina en un compás. Los videos quedan en `EDITS_FOLDER/<title>/<title> - <variante>.mp4` con un `.json` (selección, beatmap y EDL de cada variante).

```
GET /api/anime-edit/sheet/<id>/<n>   → hoja de cuadros n (desde 1)
GET /api/anime-edit/thumb/<id>/<plano>
GET /api/anime-edit/download/<id>/<n> → la variante n
GET /api/anime-edit/presets          → {presets: [{id, description}], formats}
```

**Como lo opera Claude:** `/process` → poll → mirar `GET /sheet/<id>/<n>` (y las miniaturas) → `/finish` con la selección, el héroe y 2–4 variantes → poll hasta `finished=true` → `GET /download/<id>/<n>`.

## montage — `/api/montage`

Capas animadas con transparencia (ProRes 4444, VP9 con alfa o PNG) sobre un video vertical terminado, en un encode. Es el diseño de `PKB/…/montaje-automatico-con-animaciones-html.md`.

- Video base: `video`, `library_clip` o `job_id` (de un Reel Express u otro job terminado).
- Capas: `layers` (JSON) con archivos subidos (`layer_files`) o leídos de `layers_folder` (dentro de `LAYERS_FOLDER`; acepta el `manifest.json` de `laminas.mjs` por `id`, con sus claves en español: `archivo`, `laminas`).
- Cada capa (`layers.py`) entra en `start` o en una `word` de la transcripción (`n`: la n-ésima vez; números escritos pasados a dígitos: "dieciséis" = "16", lo que falló en Lux). Sale en `end`, `end_word` (+ `end_n`) o con la siguiente.
- Reglas medidas en suiza y Lux: 0.1 s antes de la palabra (`lead_s`), tope de 12 s (`max_s`), si el hueco que deja el tope es menor a 2.5 s se pega a la siguiente, pegadas se funden (`fade_in_s`, `fade_out_s`), encimadas no.
- Caja (`box`): `{width, x (center|left|right), bottom}` o `{mode: "band", y, height}` (la de Lux: la capa centrada sobre su copia a lo ancho, difuminada).
- `subtitles=1` quema los subtítulos en la misma pasada (encima de las capas). Sin `segments`, transcribe.
- **Reel Express** acepta `layers` + `layers_folder` en `/finish` y los monta en la misma pasada que los subtítulos.

```
GET /api/montage/status/<id> → {status, progress, stage, error, layers: [{file, start, end, id}]}
GET /api/montage/download/<id>
```

## Tests

`tests/synthetic.py` arma canciones (bombo y hi-hat con tempo conocido) y videos de planos de colores con cortes en segundos conocidos: nada con derechos y sin internet. `tests/beatmap`, `tests/editing`, `tests/competitivo/test_edit.py` (incluye el job completo con el VOD simulado), `tests/anime_edit`, `tests/montage`.

## Pendiente / ideas

- **Capas GSAP de presentations**: `cuadros.mjs --alfa` + un kit de recetas para edits (brasas, rayos, letreros de kill, palabra con máscara) que entren por montage o por la EDL.
- **Máscaras** (SAM 2.1 / ToonOut) para el texto detrás del personaje, como en el edit de referencia.
- Calibrar `kill_lead_s` y la intensidad de los acentos con un edit real de Worlds.
- Detección de secciones con `allin1` (etiquetas reales) si la energía no alcanza.
