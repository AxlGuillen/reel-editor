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

## motion — `/api/motion` (mapa de movimiento)

`modules/motion/analysis.py` (puro sobre los cuadros) + `processor.py` (`get()`, con caché) + `sheet.py` (hoja de eventos, hoja de parejas y vista previa) + `bench.py` (mide contra la verdad anotada). Como beatmap: describe el video y no decide nada. Lo usan los planificadores con `motion.processor.get(path, start, end)`, `hits(m)` y `focus_at(m, t)`.

**Una sola decodificación por tramo**, a 480 px y en BGR, que comparten el flujo óptico, el color y el detector de cortes (`shots.FrameCuts`: el mismo AdaptiveDetector de `shots.cuts()`, alimentado con esos cuadros). **Cuadro exacto:** el seek de entrada de FFmpeg solo, sobre el mkv de Black Clover, a veces se saltaba el primer cuadro y el modo CFR duplicaba o tiraba cuadros a mitad del tramo (las primeras hojas de anotación estaban corridas sin regla fija). `decode_command` busca 1 s antes y `select='gte(t,…)'` con `-copyts` y `-fps_mode passthrough` toma los cuadros por los timestamps del archivo. Lo comprueba un test con un video cuyo cuadro k tiene brillo 3k.

| Señal (por cuadro, en `frames`) | Cómo sale |
|---|---|
| `held`, `drawing` | cadencia: un cuadro repite el dibujo si su diferencia media de luma es < max(1.0, 35 % de la mediana de los últimos 8) **o** si el flujo apenas se movió (p97 < 1 px). El flujo corre solo entre dibujos distintos y se divide entre los cuadros que tardó; ese valor llena los cuadros del cambio (sin peine) |
| `cam_dx`, `cam_dy`, `cam_zoom` | homografía RANSAC sobre una malla de puntos cada 20 px del flujo (300 iteraciones: las 2000 por omisión eran el 70 % del tiempo) |
| `force`, `angle`, `dirgram` | el flujo menos la cámara: percentil 90 (px por cuadro en unidades de 1920 de ancho), ángulo ponderado (0° derecha, 90° arriba) y directograma de 8 direcciones |
| `luma`, `sat`, `inverted`, `bw` | brillo y saturación; invertido = el cuadro se parece más al negativo del anterior |
| `cx`, `cy` | centro de la mancha conectada de movimiento con más movimiento adentro, suavizado por toma (EMA 0.4) |
| `shot`, `err` | toma; `err` = cuánto del cambio NO explica el flujo (alto en un barrido o en un corte) |

| Evento | Regla |
|---|---|
| `impact` | pico de fuerza de un dibujo, frenazo en los 4 cuadros siguientes (≤ 60 %) y **prominencia** (fuerza / mediana de ±12 cuadros) ≥ 6, o ≥ 2.5 si frena fuerte (≤ 30 %: la ráfaga, donde todo se mueve) o ≥ 2 si lo marca un destello o un cuadro de impacto. El cuadro es el del pico (el contacto); se corre a la ráfaga de impacto o al destello si arrancan ahí. Un destello sobre movimiento fuerte (≥ 1.5×) también es golpe (la explosión de fuego no frena). Uno por momento: el más fuerte en 5 cuadros |
| `smear` | un dibujo rápido (≥ percentil 90) que el flujo no explica (`err` ≥ 0.75) |
| `pan` | la cámara se mueve ≥ 4 px/cuadro en una dirección (±35°) durante 6 cuadros o más; la dirección es la de la cámara |
| `calm` | fuerza < 2.5 durante 2 s (o lo que pida `calm_s`, p. ej. un compás) |
| `flash` | la luma sube más de 25 en un cuadro (que no es corte) |
| `cut` | corte de PySceneDetect que no es: un destello que vuelve a bajar, un cuadro invertido, una ráfaga de 1–3 cuadros tras la que vuelve la imagen de antes (miniaturas de 32×18: `min_scene_len` esconde el segundo corte) ni un cambio que el flujo explica |

**Medido (6/11):**
- Las 10 parejas del anexo de las reglas v1.0, con el cuadro exacto anotado a mano en hojas de la decodificación exacta (`tests/motion/pairs.json`). Cinco son golpes y cinco son poses o cortes, porque el anexo marca ventanas de hasta 2 s:
  - **10/10 y golpes 5/5**, con deltas de −1, 0, 0, 0 y 0 cuadros.
  - La pareja 7 es una ráfaga y pasa por su contacto 27219, anotado como alternativa antes de medir; el contacto de la cara, 27212, no tiene pico propio (prominencia 1.6).
- **Ojo:** los umbrales se ajustaron con esas mismas 10 parejas (no hay un set aparte).
- En la toma 09 (1240–1258 s), unos 20 de los 24 eventos más fuertes son golpes o barridos reales. Los dudosos son caras con poco movimiento.
- Densidad: 1–2 golpes por segundo en las peleas y 0.2–0.9 fuera de ellas.
- **~170 cuadros/s** a 480 px en la CPU, con la decodificación de 1080p incluida.

**Límite conocido:** DIS (preset FAST) pierde desplazamientos de más de ~24 px por eje a 480 px (~100 px/cuadro a 1920). Un movimiento así sale como un barrido (`err` alto) con fuerza subestimada. Se probó un paso grueso a media resolución: arreglaba ese caso pero empeoró las parejas reales (8/10), así que se descartó. Los sintéticos usan texturas tipo cel (bordes duros), porque con ruido difuminado DIS falla incluso en desplazamientos chicos.

**Caché** en `cache/motion/<huella>-<f0>-<cuadros>-w<ancho>-v<versión>.json`. La huella es tamaño + primer y último MB (`file_utils.quick_fingerprint`): un episodio de 1.4 GB no se lee entero.

```
POST /api/motion/process   video (archivo) | library_clip | job_id | path (archivo local)
                           [start, end (s o m:ss), width=480 (160–1280), preview]
GET  /api/motion/status/<id>   → {status, progress, stage, error, summary, motion, has_preview}
GET  /api/motion/json/<id> · /summary/<id> (texto) · /sheet/<id> (JPG) · /download/<id> (preview.mp4)
python -m modules.motion.bench pairs <episodio> [--sheet carpeta] [--refresh]   # las 10 parejas
python -m modules.motion.bench speed <video> [inicio fin]
```

El resumen para Claude es una línea por evento: «toma 13 · golpe fuerte en 1249.16 s (cuadro 29950), hacia la izquierda, centro (0.78, 0.51)».

**Analizar un edit terminado** (`python -m modules.motion.reference <edit.mp4> [--out carpeta]`): mapa de movimiento + beatmap de su audio. Mide:
- **Cortes y efectos:** destellos, invertidos y, desde la cámara, golpes de zoom (cambio rápido de zoom) y sacudidas (la cámara invierte la dirección varias veces en pocos cuadros). De cada uno, cuántos caen en un beat, en el 1 del compás y en un golpe del anime.
- **Rejilla:** si los cortes siguen alguna (la mejor rejilla constante contra lo que daría el azar).
- **Densidad por sección.**
- En un edit estilizado, el mapa toma por cortes algunos golpes de zoom. Si la cámara hace zoom fuerte y los colores se mantienen (histograma HSV), cuenta como zoom.
- Los *whips* y negros todavía no se distinguen de un corte.

Medido en el edit de asen (TikTok 7703346653037710610, 46 s):
- 243 cortes, plano mediano de 0.1 s.
- **No corta en una rejilla:** 29 % de los cortes en la mejor, contra 22 % al azar.
- Los efectos sí van **sobre los golpes del anime:** 11 de 13 destellos y 51 de 102 zooms.
- La densidad sube de 0.27 efectos/s en la intro a 2.8 en el coro y 3.6 al final.

## editing — el motor (`modules/editing/`)

> El banco de efectos (todos los eventos, `mblur`, `dedupe`, `hit_ramp`, la galería y lo que se corrigió de `rgb` y `glow`) está en [`efectos.md`](efectos.md).

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

**Looks**: `neutral`, `vivid`, `golden` (el de referencia), `film`, `cold`, `night`, `fire`. LUTs propias en `assets/luts/*.cube` (`lut`). `vivid` es una curva con el pivote donde vive el juego (oscuro): el `eq contrast` pivotea en el gris medio y bajaba el gameplay de 57 a 52 de luma; con la curva queda igual (56.4 → 56.2) y con más contraste. El "vivo" de los resúmenes usa la misma curva. **Viñeta** suave (`PI/9`: esquinas al 76 %, borde de arriba al 81 %; la de antes, `PI/5`, dejaba la tabla de stats al 48 %); el modo edit de competitivo la trae prendida (`vignette=1`).

**Opcionales** (`core/external.py`, binarios ncnn-vulkan, corren en cualquier GPU y en Mac):
- **RIFE** (`rife-ncnn-vulkan`): interpola los planos con piezas más lentas que 0.6× (cámara lenta fluida en vez de cuadros repetidos).
- **Real-ESRGAN** (`realesrgan-ncnn-vulkan`, modelo `realesr-animevideov3`): escala fuentes de menos de 1080p.
Sin ellos todo funciona; `REELFORGE_RIFE` / `REELFORGE_REALESRGAN` apuntan al ejecutable si no está en el PATH.

## competitivo — modo edit (`POST /api/competitivo/edit`)

Las peleas de la fase 1 al ritmo de una canción. Con algo que un edit de anime no tiene: **los datos**. Cada kill ya está ubicada al cuadro (feed + contador del marcador), así que no se busca en el video qué coincide con la música: **se fuerza que cada kill caiga en un beat** estirando o comprimiendo el tramo de antes.

**Presets.** Lo que funcionó la semana del 23/10 está con nombre en `assets/competitivo/presets.json`: `preset=edit_estandar` (actos cronológicos, sin letreros, 3 s antes y 2 s después de cada kill, picos de Ibai, `zoom_mode=auto` y **storyboard** antes del render), `edit_corto`, `edit_equipo` (+ `team`) y `edit_aura_triste`. Lo que se mande explícito gana sobre el preset; `GET /api/competitivo/presets` los lista. Cada edit queda en las métricas (`edit` / `edit_render`: kills con foco seguro, en plano entero, correcciones, reintentos), ver CLAUDE.md.

**Rendimiento.** Los tramos de las peleas se bajan y se analizan a la vez (tantos como turnos de análisis, `config.SLOTS`), cada uno con UN decode (`layout.Scan`), y los trabajos hacen cola por recurso (descargas de Twitch, Whisper, render). Los números y lo que se probó y se descartó están en CLAUDE.md, sección «Rendimiento».

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
| `drop` | arranca EXACTO en el drop de la canción (o en el beat de `music_start`, si se manda); el resto de las peleas al beat, con una receta por kill y whips entre peleas | 100 % | 15 % |
| `outro` | la cola de la pelea de cierre (el nexo) en tiempo real; el juego vuelve a subir y la canción cierra en su compás (fundido) | 100 % | 15 → 100 % |

- **Orden cronológico** (`chronological=1`, por defecto): todas las peleas en orden. El `cold_open` es un **adelanto de 2–3 s** (`teaser_s`) del mejor momento de la pelea clave; el `build` son las primeras peleas (antes de la clave), el `drop` el resto en orden, y el `outro` sigue la cola de la ÚLTIMA pelea del drop: la clave no sale tres veces (antes salía en el arranque, en el drop y en el cierre). Con `chronological=0` queda el orden de v2 (la de más puntaje primero, 5–8 s).
- **Versión corta** (`short=1`): el adelanto, el silencio y en el drop solo la pelea clave y la de más puntaje (en orden, con un compás de entrada cada una), ~20–25 s (tope 25 s).
- **Planos de ambiente** (`ambient=1`): en cada tramo bajado, los segundos sin marcador que NO son juego (`layout.ambient_ranges`: sin verde saturado del mapa; el Rift tiene 5–43 % y una cámara real 0–0.1 %, medido en IG-GEN), al menos 1.5 s: el escenario, los jugadores, el coach, el público. Se usan después del adelanto (2.5 s, con la voz de Ibai) y en la segunda mitad del outro (que pasa a 8 beats), con prioridad para la reacción justo después de la última pelea. Van con su propia geometría (`layout.ambient_geometry`: la vista completa en el medio, sin tabla ni marcador, y la cámara de Ibai).
- En `cold_open` y `build` hay un **push-in** lento (1.0 → 1.12×) apuntando a la acción de la kill más grande, y **whips** entre los planos. Los cambios de volumen tardan 0.05 s (sin clics).
- **Recetas por kill** (`recipes`: `auto` o lista, p. ej. `ramp,hard_cut`), con un hash fijo (misma serie → mismo edit) que nunca repite dos veces seguidas:
  - `ramp`: 0.3× en la kill → 1× → 1.5× hacia la siguiente;
  - `freeze_zoom`: 0.15 s congelado con zoom 1.3× sostenido;
  - `hard_cut`: sin cámara lenta, solo el golpe;
  - `instant_replay`: SOLO en la última kill de la pelea clave: se ve, rebobina 1.5 s y la repite a 0.4× con zoom 1.6× (la cola se alarga hasta cerrar el compás);
  - `stair_zoom`: peleas con 3+ kills: una kill por beat, con zoom 1.1 / 1.2 / 1.3 / 1.4×.
- **El zoom apunta a la acción** (`layout.action_focus`): el centroide del movimiento (diferencia entre cuadros, el 5 % que más se mueve) dentro del gameplay alrededor de cada kill, llevado a coordenadas de la salida. Si no se mueve nada o se mueve todo (la cámara del observador paneando), el centro.
- **La voz de Ibai con subtítulos invertidos** (`voice_subs`, `voice_language`=es): cada plano en tiempo real se transcribe con faster-whisper y sus palabras se queman con el layout `invert` (los mismos campos `sub_inv_*` y `sub_font` que Subtítulos), solo hasta el silencio: **nunca encima de la letra**, que en este modo sale recién desde el drop.
- **Nombres bien escritos** (`competitivo/vocab.py`): Whisper recibe como `initial_prompt` y `hotwords` los equipos, los 10 jugadores y los 10 campeones de ESA partida (la fase 1 guarda el plantel del feed en `partidas[].roster`), y después se aplican las correcciones conocidas de `assets/vocab.json` (mal oído → correcto; sin mayúsculas ni tildes, también frases). En el layout invertido cada palabra va **sin la puntuación final**.
- **Revisión antes de quemar** (`review=1`): el edit se pausa después de transcribir (`status=done`, `finished=false`, `stage="esperando_revision"`) con `segments` (uno por plano en tiempo real, en tiempo de la salida: `{id, start, end, text, words}`) y `vocabulary`. `POST /api/competitivo/edit/finish` con `job_id` y `segments` (JSON, el texto corregido) renderiza: un segmento con el mismo texto conserva sus tiempos; uno cambiado se reparte sobre lo que cubrían sus palabras. Como la sesión vive en memoria, reiniciar el server pierde el edit pausado.
- **Feedback del 23/10 (los 7 edits de Suiza Día 1):**
  - **Sin letreros por defecto** (`text=off`): ni NOMBRE · CAMPEÓN ni el sello de multikill; `text=kills` los vuelve a poner.
  - **Más juego alrededor de cada kill**: `kill_before_s` (3 s, 0–8) antes de la primera kill de cada pelea y `kill_after_s` (2 s, 0–6) después de la última, en el drop (la entrada y la cola de cada pelea) y en el build, redondeados a beats: la kill sigue cayendo en su beat y cada pelea sigue arrancando en un primer tiempo. Entre dos kills de la misma pelea no se corta nada.
  - **Sin tope** (`max_duration_s=0`): entran todas las peleas elegidas; el límite es la canción desde el drop (o `music_start`). Si no alcanza, salen las de menos puntaje (nunca la clave) y el `warning`/`aviso` dice cuántas quedaron fuera; no falla.
  - **Un equipo** (`team=T1`): todas las peleas y picks de la fase 1 donde ese equipo mató, elegibles o no, en orden; lo que mató el rival en esas peleas sale igual, como contexto. Un código que no jugó la sesión es un 400.
  - **La voz de Ibai manda** (`voice_peaks=1`, por defecto; `competitivo/voice.py`): en la banda de la voz (250–3500 Hz) del audio del stream, un pico es ≥ 0.8 s con la energía **+3 dB** sobre su mediana en la partida Y el tono **×1.12** sobre su tono mediano (el stream viene comprimido: su p95 está apenas +3–4 dB sobre la mediana, medido en LOS-T1 y MKOI-BLG; con +6 dB no aparecía ninguno). Las medianas son de la partida (todos los tramos juntos). En el drop, esos tramos van **a velocidad real** (el resto de ese hueco se acelera para que la kill siga en su beat; si no entra, el hueco gana beats), la **música baja al 30 %** con rampas de 0.3 s, el **stream sube al 100 %**, se transcriben y se queman con el subtítulo invertido (`edl.subtitle_windows`: cada palabra solo dentro de su ventana) y nunca llevan letra encima. La kill de ese hueco pierde su receta (sin cámara lenta ni repetición: `voice_peak: true`). Verificado: los picos de LOS-T1 caen en «¡Madre mía, qué…!» y «¡Vamos! ¡Vamos!».
  - `build_fights` llega a **6** (más peleas en tiempo real con su voz).
  - **Look por acto** (`last_fight_look=bw`, opciones none|bw): la última pelea del drop y el outro en blanco y negro con un poco de contraste (`Segment.tint`, se aplica en la pasada 1 solo a esos planos).
  - **Cámara dividida por clip**: el status (`split_camera`) y el `.json` (`plan.split_camera`) guardan los rangos de cada tramo para verificar el detector.
  - **Render que se puede reintentar**: si el render falla (p. ej. NVENC sin sesión libre), el plan, los tramos y las palabras de Ibai quedan en memoria (`retryable: true`) y `POST /edit/finish` con el `job_id` (sin `segments`) renderiza de nuevo.
- **Cámara (24/10):**
  - **El zoom a tiempo**: cada kill cae en el cuadro en que sube el contador de kills del marcador (`processor.kill_frames`: el cambio del mismo lado a ≤ 1 s de la hora del feed corregida; si no se ve, la del feed − `kill_lead_s`), y el zoom entra **0.5 s antes** (`Event.attack`).
  - **Sigue la acción** y **vista completa** (`zoom_mode=auto|zoom|full`, por defecto `auto`; ver `camera.py`): con la acción clara el recorte se desliza hacia ella y el zoom apunta ahí; si no está clara, ese momento va con la zona limpia entera (sin zoom). Medido en 52 kills reales del 23/10: con "seguro" el punto cae en la pelea; "no seguro" son sobre todo cámaras divididas y peleas desparramadas.
  - **Correcciones por kill** (`kill_overrides`, JSON): `[{"clock": "21:41", "mode": "full"}, {"kill": "1-10#2", "focus": [0.62, 0.40]}]`. `clock` es el reloj del marcador (todas las kills a ±2 s), `kill` el id del storyboard, `focus` el punto como fracción de la miniatura. Valen en `/edit`, en `/edit/finish` y en `/finish` de los resúmenes.
  - **Storyboard** (`storyboard=1`): antes del render largo el edit se pausa (`stage="esperando_revision"`, junto con `review=1` si se pidió) con `storyboard: {sheet, shots:[{n, id, clip, clock, t, recipe, mode, by, sure, focus, thumb}]}`; `GET /api/competitivo/storyboard/<id>` es la hoja de contacto y `…/<id>/<n>` cada miniatura. `/edit/finish` con `kill_overrides` rearma la cámara (mismos tiempos) y renderiza.
- **Momentos sin kill** (`moments`, JSON): `[{"clock": "1:49", "dur": 6, "label": "robo del red"}]` (reloj del marcador, 2–20 s, hasta 10; `game_id` si la serie tiene varias). Se baja un tramo alrededor de ese reloj (60 % antes, 40 % después) y entra **en orden cronológico**, a velocidad real y con la voz de Ibai: antes de la primera pelea del drop va en el build; después, en el drop ocupando beats enteros, sin acentos. Sale en `plan.kills` con `recipe: "moment"`. Para encontrarlos: el índice de `/transcript`.
- El status (y `resultado.plan`) trae el **plan por actos** (`acts: [{act, start, end, clips, music, game}]`) y la **receta de cada kill** (`kills: [{clip, t, recipe, count, player, champion}]`), para revisarlo; `resultado.subtitle_words` dice cuántas palabras de Ibai se quemaron.

```
POST /api/competitivo/edit   … [structure (acts | beat), recipes (auto | lista), cold_open_s (5–8),
      build_fights (0–6), chronological, teaser_s (2–3), short, ambient,
      text (off | kills | lyrics | both), kill_before_s (0–8), kill_after_s (0–6),
      max_duration_s (0 = sin tope, 10–600), team (código), voice_peaks, last_fight_look (none | bw),
      zoom_mode (auto | zoom | full), storyboard, kill_overrides (JSON), moments (JSON),
      voice_subs, voice_language, review, vignette, with_camera, sub_inv_size,
      sub_inv_y, sub_inv_direction, sub_inv_words, sub_font]
POST /api/competitivo/edit/finish   job_id [+ segments, kill_overrides]   → 202 (review/storyboard, o reintento)
GET  /api/competitivo/storyboard/<id>[/<n>]                              → la hoja de contacto / una miniatura
GET  /api/competitivo/status/<id> → … plan {structure, acts, kills, voice_peaks, split_camera,
     detected_voice_peaks}, voice_peaks [{t, dur, db, clip}], split_camera {clip: [[a, b]]},
     retryable, [segments, vocabulary]
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

### El preset `manga` (reglas v1.0, edit 3 de Magna vs Dante)

`anime_edit/manga.py` (planificador puro) + `framing.py` (encuadres y color) + `repro.py` (la prueba). No elige planos: recibe **tomas por segundo**, como la lista `P` de `edit3.py`.

- **Fase 1** con `shots=0`: sin detección de planos ni miniaturas (un episodio entero no lo necesita). `video_path` y `music_path` aceptan archivos de esta compu, que no se suben ni se borran.
- **Fase 2** con `variants=["manga"]` y `takes`:

```json
{"takes": [{"t": 317.30, "beats": 4, "frame": "B", "focus": [0.5, 0.5], "tone": "cold"},
           {"t": 372.05, "beats": 4, "frame": "C", "focus": [0.30, 0.45], "tone": "cold"},
           {"panel": "m09_black-bulls-jefe_con-texto", "beats": 2},
           {"t": 1263.80, "beats": null, "frame": "C", "speed": 0.85}],
 "panels_folder": "C:/…/Edit Magna vs Dante/manga",
 "panels": [{"panel": "m03_golpe-1", "t": 1127.79}],
 "music_start": 144.70, "music_end": 212.09, "grid": {"bpm": 130.75, "phase": 0.21},
 "flashes": [174.92, 186.4, 201.18], "phrases": [{"a": 174.92, "b": 182.66, "words": [[174.9, 175.2]], "flash": true}]}
```

- **Encuadres:**
  - `B` es el 4:5 sobre el mismo cuadro difuminado (la base).
  - `C` es el 16:9 completo sobre el difuminado, para las tomas anchas.
  - `A` es el vertical completo, para los primeros planos.
  - También se aceptan las letras de `edit3.py` (`z`/`w`/`v`).
  - El foco horizontal va de `focus[0]` a `focus[1]` durante la toma, medido en el tiempo de salida.
- **Color por toma:** el grado de las reglas (contraste, saturación, curva en S y brillo en modo pantalla) con balance `cold` o `warm`, más la viñeta. Todo va en el `vf` del plano, en la pasada 1.
- **Pausa con panel:**
  - Un ítem `panel` congela el **último cuadro** de la toma anterior, con su encuadre en el foco final.
  - El evento `panel` de la EDL oscurece el cuadro a ×0.62. El panel entra de 93 % a 100 % en 0.12 s y sigue creciendo 3.5 % hasta el final.
  - Las parejas `panels` (`{panel, t}`) se insertan después de la toma que contiene `t`, y esa toma se corre para terminar justo en `t`.
- **Rejilla y cortes:**
  - La primera toma cubre la entrada hasta el primer compás después de `music_start`.
  - Cada corte cae `cum` tiempos después, redondeado al cuadro de salida: `round((PH + cum·BEAT)·fps)`, la fórmula de `edit3.py`.
  - Con `grid` la rejilla es constante (la de `glee/grid.json`). Sin ella se usan los beats medidos del beatmap.
  - La última toma, con `beats: null`, llena hasta `music_end` (por omisión, el final natural de la canción).
- **Destellos:** 55 % de blanco durante los cuadros de [t, t + 0.07), con `shape="mix"`. Por omisión van al inicio de cada frase (palabras del beatmap separadas por pausas de ≥ 0.45 s). `flashes` o las frases con `flash: true` los fijan.
- **Fundido** de video a negro de 1.5 s. La música entra con 30 ms y no se funde: termina sola.
- **Letra:** dos `.srt` **solo con tiempos**, `<title> - tiempos-letra_frases.srt` y `…_palabra-por-palabra.srt` (cada palabra dura al menos 150 ms). La letra la escribe Axl. Los textos propios van en `words` (`{word, start, end}` en segundos del edit), con el layout `invert`.

**En el motor (`editing`):**
- Evento `panel` (`image`, `dim`, `enter`, `from_scale`, `drift`).
  - `editing/panels.py` **dibuja cada cuadro con PIL**, con la cuenta de `edit3.py`: ajusta la imagen a 1040×1700 de 1080×1920, la escala una vez ×1.1 con LANCZOS y, por cuadro, la baja bilineal y la pega centrada. Cada cuadro es un PNG RGBA del tamaño del cuadro, y FFmpeg solo lo superpone.
  - Antes se probó escalar en FFmpeg y quedó descartado por dos razones. `overlay` no acepta una entrada que cambia de tamaño: FFmpeg mete un reescalado y el panel salía quieto a ×1.1. Además, aun con un lienzo fijo, el bilineal de FFmpeg queda medio píxel corrido del de PIL, y en dibujo lineal eso bajó el SSIM contra el edit 3 a 0.88.
  - La secuencia entra medio cuadro antes, en base de tiempo de µs (`settb=AVTB`). En su base propia (1/30), el medio cuadro se redondeaba y el panel salía un cuadro adelantado. Con `eof_action=pass` se perdía el último cuadro.
  - Va antes de los acentos, así un destello lo blanquea también.
- `Event.shape="mix"` (por número de cuadro, `lutyuv` hacia (235, 128, 128): lo mismo que mezclar R, G y B hacia 255).
- `Edl.video_fade_out` y `Edl.music_fade_in`. Con `fade_out=0` la música no se funde.
- `Segment.exact_phase`: el tiempo de la fuente se mapea exacto (`setpts=PTS−inicio/TB`) en vez de poner en 0 el primer cuadro.
  - Con eso el `fps` elige los mismos cuadros que `edit3.py`.
  - En la toma de 1127.95 s el primer cuadro del episodio cae 0.01 s después, y sin esto salía otro cuadro.
  - Solo lo activa `manga`, así los demás módulos no cambian.

### Compuestos en el preset `manga`

Por omisión todo apagado, así el plan sigue siendo el de las reglas v1.0 (`repro.py` sigue dando los cuadros del edit 3). En `/finish`:
- `blur_cuts` = `off` | `radial` | `directional` | `mix`: el corte con desenfoque en cada corte entre dos tomas (no al entrar a un panel). `blur_cuts_from` (s de salida) deja limpios los cortes de una intro lenta.
- `composites` = `[{"kind": "hit" | "burst" | "isolate", "at": segundo del VIDEO, …}]`: Claude elige el momento en el episodio (el cuadro del contacto) y el código encuentra su cuadro de salida (`manga.output_frame`). Un momento que ninguna toma muestra da error. Claves de cada receta: `angle`, `x`, `y`, `whip`, `strength`, `color`, `similarity`.
- `lyrics_srt`: un .srt de la canción con una palabra por cue, con el texto de Axl (`letra_a_srt.py` reparte el texto pegado sobre el .srt de tiempos). Va con los subtítulos invertidos, ubicados lejos del sujeto y borrados en el corte.
- `resolution` = 1080 | 1440 | 2160.

El status trae `composites`: cuántos cortes con desenfoque, dónde cayó cada compuesto (`frame`, `t`) y cuántas palabras de la letra.

### `motion: on` — bloques, curva y efectos en los golpes

`/finish` del preset `manga` con `motion=on` (por omisión `off`: las reglas v1.0 tal cual). Con `off` el plan no cambia nada: la misma EDL y los mismos cuadros que la fase 2. `anime_edit/blocks.py` + `blocks.json` + `fit.py`:

1. **Curva:** la sección de la canción bajo cada toma (beatmap: `intro`, `verse`, `chorus`, `bridge`, `outro`, la subida antes de cada coro y el drop) da una intensidad objetivo. Por omisión `{intro 1, verse 2, bridge 1, outro 1, chorus 4, buildup 3, drop 5}`.
2. **Bloque:** el más intenso con intensidad ≤ objetivo cuyos requisitos cumple el mapa de movimiento de la toma. En un empate gana el bloque propio de la sección. Claude puede fijarlos con `blocks=[{"take": i, "block": id}]`.

   | Bloque | Int. | Qué pone | Necesita |
   |---|---|---|---|
   | `calma` / `texto` | 1 | acercamiento lento al centro de la acción | — |
   | `panel` | 2 | la pausa del preset | — |
   | `camara_lenta` | 2 | la toma a 0.6× (RIFE y `dedupe` si está), zoom en su golpe | 1 golpe |
   | `rampa` | 3 | rápido hacia el golpe más fuerte y lento después, **con el golpe en el beat** (`fit.fit_hit`); destello y sacudida en su dirección | 1 golpe |
   | `rafaga_zooms` | 4 | zoom en escalera, un escalón por beat, al centro de la acción | — |
   | `estrobo` | 4 | estrobo cada 2 cuadros (solo en la subida) | — |
   | `rafaga_golpes` | 5 | cada golpe con su acento (destello / sacudida / zoom), el más fuerte invierte un cuadro | 3 golpes |
   | `pantalla_dividida` | 3 | **no disponible**: necesita un plano con dos fuentes | — |

3. **Efectos en el golpe:** cada efecto va en el cuadro de salida donde aparece el cuadro de su golpe, `fit.out_frame`.
   - El filtro `fps` cuenta desde el **primer cuadro de la fuente** de cada pieza, no desde su inicio. Una pieza que empieza entre dos cuadros se corre `round(desfase · fps / velocidad)`. Medido contra un render real de una fuente de 23.976 fps numerada cuadro a cuadro: sin esa corrección el golpe caía un cuadro antes.
   - El plan guarda cada efecto clavado (`locked`: `{kind, frame, hit_frame}`). El `hit_frame` se recalcula al final desde las piezas definitivas de la toma.
4. **Encaje golpe–beat** (`rampa`):
   - [inicio, golpe] va en [0, beat] y [golpe, fin] en el resto, con velocidades entre 0.4× y 2.5×.
   - La mitad que entra al golpe se limita moviendo su **inicio** y la de después recortando su **final**, así el golpe no se mueve.
   - Si la toma cambia de duración en la fuente, su pausa con panel congela el **nuevo** último cuadro.
5. **Transiciones** en los cortes entre dos tomas con intensidad ≥ 3 (nunca contra una pausa con panel): `whip` (con el ángulo del paneo o golpe más cercano), `flash`, `black`, `invert` y `zoom` en rotación. Al menos 3 tipos y ninguno más de 6 veces; el resto, corte seco.

`blocks.check(info["motion"], fps)` verifica el criterio de la fase 4 sobre el plan: desfase máximo efecto–golpe, intensidad del drop contra la subida y variedad de transiciones. El status trae `motion: {blocks, locked, transitions, intensity}`.

**Rearmar el edit 3** (criterio de la fase 2): `python -m modules.anime_edit.repro "<carpeta Edit Magna vs Dante>" "<episodio.mkv>"`. Rearma el edit desde `P`/`MODES`, compara los cortes con la fórmula de `edit3.py`, busca el salto de diferencia en cada corte de los dos videos y mide el SSIM de 5 cuadros de control con una hoja lado a lado (`…-control.jpg`).

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

## studio — `/api/studio` (Estudio)

La vista «Estudio» (sección Edits) arma un video con las láminas de un deck de Presentations encima, **viéndolo en vivo**, y lo renderiza con montage. Es la fase 2 del brief del estudio de montaje (`PKB/proyectos/personales/reel-editor/brief-estudio-de-montaje.md`). El resumen y la API están en `CLAUDE.md` («12. studio»); acá va cómo se usa y cómo funciona por dentro.

**Flujo (lo que hace Axl):**
1. **Video base:** subirlo, elegirlo de las capturas o abrir el `job_id` de un Reel Express.
2. **Deck:** elegirlo de la lista que da Presentations (`node tools/servidor-local.mjs` en ese repo). Las láminas se leen **sin renderizar**.
3. **Transcripción:** si el video no la trae, «Transcribir» (Whisper con los nombres del guion como pista). Las láminas se colocan solas.
4. **Ajustar:** reproducir, arrastrar un bloque a otra palabra o su borde derecho para que salga en otra. Todo se guarda solo.
5. **Renderizar:** pide a Presentations las láminas que cambiaron (webm con alfa, 30 fps) y corre montage. Al terminar, «Vista previa / Render» compara los dos en el mismo segundo.

**Reglas para no hacer trabajo extra** (`modules/studio/processor.py`):
- **El guion marcado manda.** `video-out/laminas/guion-marcado.txt` (las marcas `⟦lamina:id⟧` antes de la palabra en que entra cada lámina) se lee del servidor de Presentations. Se normaliza igual que montage (sin tildes ni mayúsculas, números escritos a dígitos) y se alinea con la transcripción con `difflib.SequenceMatcher`. Las palabras que Whisper oyó distinto («Liz» por Elise, «Boran» por Boram) se interpolan entre los tramos que sí coinciden.
- **Cada marca se vuelve un ancla de la transcripción**, no del guion: la frase más corta desde esa palabra (hasta 4) que se dice una sola vez; si ninguna es única, la de 4 con su número de aparición. `montage.layers.find` la devuelve a la misma palabra (lo prueba un test con todas las posiciones). Así un ancla sobrevive a mover otras láminas y a regrabar la voz, mientras la frase siga ahí.
- **Sin guion:** las `palabras` del manifest (`data-palabras`), la primera aparición después de la lámina anterior. Sin nada: «sin colocar» (botón «＋» para anclarla donde está el cursor).
- **Whisper:** los nombres propios del guion (palabras con mayúscula que no abren frase, hasta 40) van como `initial_prompt`. Nunca como `hotwords` (ver CLAUDE.md: en LeBlanc hicieron que repitiera frases en bucle).

**Por qué la vista previa coincide con el render:**
- **Tiempos:** `/place` resuelve con `montage.layers.resolve` y las mismas opciones por omisión de `MontageParams` (fundidos 0.25/0.2, tope 12, adelanto 0.1). El render manda esas mismas capas a `/api/montage/process`. Un test compara las dos rutas (inicio, fin, fundidos y caja) con el mismo montaje.
- **Geometría:** el modo capa de Presentations dibuja la lámina a 1080×1920, igual que `cuadros.mjs`. El recorte del panel (`1080:1370:0:275`, el de `laminas.mjs`) lo hace el estudio con un contenedor `overflow: hidden` y el iframe escalado y subido 275 px. La caja sale de `processor.preview_geometry`, con los mismos redondeos que FFmpeg:
  - `scale=w:-2` → el otro lado a par, redondeado;
  - el overlay sobre yuv420p baja x e y impares a par: un recuadro de 706 px centrado cae en x = 186, no en 187, y la caja del deck (bottom 275) en y = 274. Comprobado con FFmpeg el 6/11.
- **Animación:** `{tipo:'seek', t}` con `t` = segundo del video − inicio de la capa, en cada cuadro. El modo capa usa el mismo `section.__tl.time(t, false)` que `cuadros.mjs` y lo acota a la duración, así que después de su animación la lámina queda congelada, como el `tpad=stop_mode=clone` del render.
- **Fundidos y orden:** la opacidad de cada capa sigue el `fade` alfa de montage, y las capas de después van encima, como los overlays encadenados.
- **Lo que no coincide a propósito:** la banda difuminada (CSS `blur(22px) brightness(.7)` contra `gblur=22` + `eq=-0.3`), marcada en la vista como «Banda aproximada».

**Medido con LeBlanc (Cumplelolero #30, 6/11):**
- `prereel 242.mp4`, 2:44, 600 MB: se abre en 1 s.
- Transcripción con la pista: 6–7 s (GPU).
- Colocación automática: 21 de 21 láminas (509 de 537 palabras del guion iguales a las de Whisper).
- Mover una lámina: 29 ms con el iframe cargado, 463 ms cargándolo.
- Render con las láminas ya en la caché de Presentations: montage solo, ~1 min.
- 5 cuadros de control iguales: misma posición al píxel (el mejor alineado es 0,0); entre 27 y 33 dB en el panel por la nitidez de los bordes y la compresión VP9, y 41 dB sin lámina.

**Formato «banda inferior» (Ambessa, 6/11):** el primer montaje (recuadro de 706 px sobre el watermark del campeón) tenía dos capas decorativas encimadas y tapaba la barra de habilidades. Ahora la franja de abajo es del deck: el prereel sale sin watermark y con el ajuste −10 el gameplay ocupa de 297 a 1239, así que queda libre 1080×680 desde y = 1240 (la misma franja que la banda de Lux). El deck la declara (`data-recorte="1080:680:0:1240"`) y el estudio la lee del manifest. La lámina de reposo (`data-reposo`) llena los huecos. Con las láminas ya renderizadas, el montaje completo tardó 21 s.

**Pendiente / ideas:**
- Mostrar el PNG de la lámina mientras carga su iframe: al saltar lejos hay ~0.5 s sin lámina.
- Estirar el inicio de un bloque (hoy se mueve entero; el final se fija con el borde derecho).
- Elegir formato y fps del render desde la vista: hoy son `webm` y 30 en `montaje.render`.

## Tests

`tests/synthetic.py` arma canciones (bombo y hi-hat con tempo conocido) y videos de planos de colores con cortes en segundos conocidos: nada con derechos y sin internet. `tests/beatmap`, `tests/editing`, `tests/competitivo/test_edit.py` (incluye el job completo con el VOD simulado), `tests/anime_edit`, `tests/montage`, `tests/studio`, `tests/motion` (golpes sintéticos con ángulo, cuadro, cadencia, paneo, destello, corte y ráfaga conocidos; `synthetic.motion_video`).

## Pendiente / ideas

- **Mapa de movimiento (después del A/B):**
  - La v2.0 de las reglas se escribe cuando Axl elija en el A/B (`Edits/AB Magna vs Dante - mapa de movimiento`).
  - `pantalla_dividida` necesita planos con dos fuentes.
  - Los umbrales de golpe se ajustaron con 10 parejas: hace falta un set aparte (otra pelea anotada).
  - El analizador de referencias no distingue *whips* ni negros.
  - El beatmap de Glee marca casi toda la canción como verso, y eso aplana la curva.
- **Capas GSAP de presentations**: `cuadros.mjs --alfa` + un kit de recetas para edits (brasas, rayos, letreros de kill, palabra con máscara) que entren por montage o por la EDL.
- **Máscaras** (SAM 2.1 / ToonOut) para el texto detrás del personaje, como en el edit de referencia.
- Calibrar `kill_lead_s` y la intensidad de los acentos con un edit real de Worlds.
- Detección de secciones con `allin1` (etiquetas reales) si la energía no alcanza.
