# Edits al ritmo de la música

Cuatro piezas que se apoyan una en otra:

```
beatmap ──────────────► edicion (motor) ◄────── competitivo/edit.py  (las partidas de Worlds)
(ritmo de la canción)   EDL → render          ◄────── anime_edit/plan.py   (una pelea de anime)
                                                         montaje (capas animadas, aparte)
```

- **beatmap** dice CUÁNDO pegan los golpes de una canción.
- Los **planificadores** (competitivo/edit.py, anime_edit/plan.py) deciden QUÉ va en cada momento y lo escriben como una EDL. Son funciones puras: no tocan FFmpeg y se prueban sin video.
- El **motor** (`modules/edicion/`) ejecuta la EDL. No decide nada.
- **montaje** pega capas con transparencia (flyers, láminas de presentations) sobre un video ya hecho.

La regla que sale de walkthrough vale acá también: **el LLM elige momentos y estilo, nunca tiempos.** Claude elige planos, héroe y variantes; los cortes, las velocidades y los acentos los calcula el código.

## El edit de referencia

Todo esto sale del análisis de un edit real (Levi Ackerman, @med_uchiha, 2.2 M vistas, 22/04/2026), medido con ffmpeg, PySceneDetect y librosa:

- 66 s, **horizontal** 1280×720, plano mediano de **0.43 s**, una sola pelea de anime.
- Ni los cortes ni los flashes caen en la rejilla de beats más que al azar (52 % contra 52 % a ±67 ms de un medio beat). **Lo que se siente sincronizado es la letra** (una palabra gigante con degradado cuando se canta) **y los golpes del propio anime**.
- En el puente de la canción (40–50 s, el beatmap lo detecta solo) mete una escena de diálogo sin texto: el respiro antes del último coro.
- Gradación cálida dorado-teal muy saturada, brasas, flashes, cámara girada y congelados de 2–3 cuadros antes del golpe.

De ahí los presets de anime_edit (`impacto`, `velocidad`, `cine`, `letra`) y el look `dorado`.

---

## beatmap — `/api/beatmap`

`modules/beatmap/analisis.py` (puro sobre la señal) + `letra.py` + `processor.py` (`obtener()`, con caché).

| Campo | Qué es |
|---|---|
| `bpm`, `beats`, `downbeats` | tempo y tiempos (s) de cada beat y del primero de cada compás |
| `onsets`, `golpes` | ataques; `golpes` = los del 20 % más fuerte (para sacudidas) |
| `energia_por_beat` | 0–1 por beat (RMS relativo al máximo) |
| `secciones` | `[{tipo, inicio, fin, energia}]`: intro, verso, coro, puente, outro |
| `drop`, `subidas` | arranque del coro con el mayor salto de energía; los 2 compases antes de cada coro |
| `palabras` | `[{texto, inicio, fin}]` de la letra (vacío si no se pidió o no hay voz) |
| `motor`, `fuente`, `huella` | qué lo analizó, de qué archivo |

- **librosa** con `hop` de 128 para el pulso: con 512 un 120 BPM salía 117.45 (el período va en cuadros enteros del envelope) y los beats se corrían ~40 ms; con 128 sale 120.2 y ~16 ms.
- **Downbeats**: la fase de 4 donde más pega el bombo (onset bajo 150 Hz). Con **Beat This!** instalado (`pip install beat-this`) y `motor=auto`, beats y downbeats salen de la red.
- **Secciones por energía**: cada compás es alto o bajo según su energía relativa (percentiles 10–90, mediana de 3 compases); los tramos de menos de 2 compases se funden. Tosco a propósito: explicable y suficiente para decidir cada cuánto cortar.
- **Letra**: `audio-separator` (opcional) separa la voz y faster-whisper da cada palabra con su tiempo. Sin separación transcribe la mezcla.
- **Caché** en `cache/beatmaps/<huella>-<motor>[-letra…].json`: la misma canción no se vuelve a analizar.
- **Preview**: video negro con la canción, un pitido por beat (agudo en el 1 del compás), destellos y la barra de secciones. Para validar la rejilla de oído antes de un edit.

```
POST /api/beatmap/process   audio (archivo) | musica_url  [motor, letra, separar_voz, idioma, modelo, preview]
GET  /api/beatmap/status/<id>   → {status, progress, stage, error, resumen, beatmap}
GET  /api/beatmap/download/<id> → preview.mp4 ·  GET /api/beatmap/json/<id> → beatmap.json
```

## edicion — el motor (`modules/edicion/`)

`edl.py` (modelo), `ritmo.py` (curvas de velocidad y cuantizado), `efectos.py` (filtros), `texto.py` (letreros ASS), `render.py` (dos pasadas). `GET /api/edicion/opciones` lista looks, LUTs, perfiles y qué opcionales están instalados.

**EDL**: `Segmento` (un plano: archivo + `piezas` + `vf` de geometría) → `Pieza(desde, hasta, dur)` (fuente a velocidad constante; `desde == hasta` es un congelado), más `Evento(t, tipo, intensidad, dur)` (flash, zoom, shake, rgb) y `Texto(desde, hasta, texto, estilo)` en tiempo de la salida, la música (`musica`, `musica_desde`), el look, el LUT, glow, viñeta y watermark.

**Render**:
1. **Pasada 1, por plano** (3 en paralelo): geometría + cada pieza recortada, retemporizada y cuadrada al cuadro exacto (`trim=end_frame`). Intermedio `.mkv` H.264 con audio PCM: con AAC cada unión corría el audio (lo que se midió en competitivo).
2. **Pasada 2**: concat + LUT/look + glow + flash (`eq brightness` con `eval=frame`: por cuadro, no por píxel) + golpe de zoom y sacudida (un solo `zoompan`, `d=1`) + RGB (`rgbashift` con `enable`) + viñeta + ASS + watermark, con la música (fundido al final, limitador).

`ritmo.cuantizar()` lleva cada borde al cuadro más cercano de su tiempo ACUMULADO ideal: el error no se arrastra, un golpe planeado en 31.250 s a 60 fps cae en el cuadro 1875.

**Letreros** (`texto.py`): `palabra` (la letra, gigante, con degradado: libass no tiene degradados y se dibuja la palabra en 12 franjas con `\clip`, sobre una copia con contorno y sombra), `kill` (NOMBRE + campeón), `multikill` (QUADRA KILL estampado), `titulo` y `sub`. Se centran por la altura de las MAYÚSCULAS, no por la caja de la línea (en Anton caían ~25 px más abajo).

**Looks**: `neutro`, `vivo`, `dorado` (el de referencia), `cine`, `frio`, `noche`, `fuego`. LUTs propias en `assets/luts/*.cube` (`lut`).

**Opcionales** (`core/externos.py`, binarios ncnn-vulkan, corren en cualquier GPU y en Mac):
- **RIFE** (`rife-ncnn-vulkan`): interpola los planos con piezas más lentas que 0.6× (cámara lenta fluida en vez de cuadros repetidos).
- **Real-ESRGAN** (`realesrgan-ncnn-vulkan`, modelo `realesr-animevideov3`): escala fuentes de menos de 1080p.
Sin ellos todo funciona; `REELFORGE_RIFE` / `REELFORGE_REALESRGAN` apuntan al ejecutable si no está en el PATH.

## competitivo — modo edit (`POST /api/competitivo/edit`)

Las peleas de la fase 1 al ritmo de una canción. Con algo que un edit de anime no tiene: **los datos**. Cada kill ya está ubicada al cuadro (feed + contador del marcador), así que no se busca en el video qué coincide con la música: **se fuerza que cada kill caiga en un beat** estirando o comprimiendo el tramo de antes.

- Cada pelea: **entrada** (1 compás, 2 si hay mucho antes), **kills** separadas por `n` beats (lo más cerca de la velocidad base, entre 0.5× y 2.5×) y **cola** hasta cerrar el compás. Así cada pelea arranca en un primer tiempo.
- La **pelea clave** (la de cierre, o la de más puntaje) se ajusta para que su última kill caiga en un primer tiempo, y **la música arranca de modo que esa kill caiga en el drop**.
- Si se pasa de `duracion_max_s` salen las de menos puntaje (nunca la clave).
- En cada kill: flash, golpe de zoom y sacudida (más fuertes en la clave), RGB en multikills; letrero **NOMBRE · CAMPEÓN** (el feed ahora guarda el campeón y `kills.autores()` reparte las kills por jugador) y TRIPLE / QUADRA / PENTAKILL.
- Layout: el de siempre, **sin la cámara de Ibai** por defecto (en su lugar, el gameplay difuminado); `con_camara=1` la deja.
- **Quita la voz de Ibai** (el audio es la música, `volumen_juego` opcional) y es una obra propia: lo que pedía la política de Riot.

```
POST /api/competitivo/edit   job_id (de la fase 1) + musica (archivo) | musica_url
     [musica_desde (s | m:ss | auto), alinear_drop, estilo (impacto | velocidad | limpio),
      texto (kills | letra | ambos | off), velocidad (0.5–2), duracion_max_s (10–180),
      intensidad (0–1.5), look, lut, glow, con_camara, zoom, volumen_juego, adelanto_kill_s,
      acentos_musica, watermark, marca_alto, titulo_edit, anchors, clips, carpeta_dia, partido,
      titulo, mapa]                                                     → 202 {job_id NUEVO}
GET  /api/competitivo/status/<id> · GET /api/competitivo/download/<id>
```

Queda junto a los resúmenes como `Edit Mapa N - <titulo>.mp4` (no empieza con "Mapa": `publicar()` nunca lo descarta) con un `.json` al lado (la EDL), o en `EDITS_FOLDER/<fecha> <A> vs <B>/` sin día ni partido. `adelanto_kill_s` (0.25 s) corre cada kill hacia antes: el contador del marcador sube un poco después del golpe; **calibrar con un edit real**.

**Como lo opera Claude:** fase 1 (`/process` con `modo=revisar`) → `/edit` con el `job_id` y la canción → poll de `/status/<nuevo>` hasta `finished=true`.

## anime_edit — `/api/anime-edit` (dos fases)

Una pelea de anime + una canción → varias variantes.

**Fase 1** (`/process`): video (`video`, `library_clip` o `video_url`) y canción (`musica`, `musica_url` o `musica_del_video=1`); beatmap (con la letra por defecto) y **planos**:
- Cortes con PySceneDetect (`AdaptiveDetector`) o, si no está, el `scdet` de FFmpeg. Los planos de menos de 0.3 s se funden (los impact frames del anime parecen cortes).
- Por plano: movimiento, brillo, saturación, destellos y un puntaje 0–100; los que son sobre todo blanco o negro (impact frames, fundidos) se penalizan.
- **Hojas de cuadros** numeradas (8×6) y una miniatura por plano: lo que mira Claude.
- Queda en `stage="esperando_seleccion"` con `planos`, `propuesta` (los 24 de más puntaje, en orden) y `heroe`.

**Fase 2** (`/finish`): `seleccion` (ids en orden), `heroe`, `variantes`, `formato` (`vertical` con fondo difuminado, `vertical_lleno`, `horizontal`), `duracion_s`, `musica_desde`, `texto`, `look`, `lut`, `intensidad`, `fps` (24/30/60), `interpolar`, `escalar`, `volumen_original`, `watermark`, `titulo`.

| Preset | Cortes | Velocidad | Acentos | Texto / look |
|---|---|---|---|---|
| `impacto` | coro cada beat, resto cada 2, puente cada 4, subida cada medio beat | constante | flash en los compases del coro, zoom en los cortes del coro, sacudida en los golpes | letra / dorado |
| `velocidad` | cada 2 beats | lenta al arrancar el plano, rápida después | flash en cada corte, zoom en cada beat del coro, RGB en los cortes del coro | letra / vivo |
| `cine` | cada compás (coro cada 2 beats) | respira (lenta en el medio) | solo el drop | sin texto / cine |
| `letra` | en cada palabra cantada (compases donde no hay voz) | constante | drop, zoom en cortes del coro | letra / dorado |

En todos, el **plano héroe va en el drop** un compás, congelado 0.1 s y en cámara lenta (RIFE si está). La ventana de la canción arranca en un compás de modo que el drop caiga hacia el 35 % del edit, y termina en un compás. Los videos quedan en `EDITS_FOLDER/<titulo>/<titulo> - <variante>.mp4` con un `.json` (selección, beatmap y EDL de cada variante).

**Como lo opera Claude:** `/process` → poll → mirar `GET /hoja/<id>/<n>` (y las miniaturas) → `/finish` con la selección, el héroe y 2–4 variantes → poll hasta `finished=true` → `GET /download/<id>/<n>`.

## montaje — `/api/montaje`

Capas animadas con transparencia (ProRes 4444, VP9 con alfa o PNG) sobre un video vertical terminado, en un encode. Es el diseño de `PKB/…/montaje-automatico-con-animaciones-html.md`.

- Video base: `video`, `library_clip` o `job_id` (de un Reel Express u otro job terminado).
- Capas: `capas` (JSON) con archivos subidos (`capas_archivos`) o leídos de `carpeta_capas` (dentro de `CAPAS_FOLDER`; acepta el `manifest.json` de `laminas.mjs` por `id`).
- Cada capa entra en `desde` o en una `palabra` de la transcripción (`n`: la n-ésima vez; números escritos pasados a dígitos: "dieciséis" = "16", lo que falló en Lux). Sale en `hasta`, `hasta_palabra` o con la siguiente.
- Reglas medidas en suiza y Lux: 0.1 s antes de la palabra, tope de 12 s, si el hueco que deja el tope es menor a 2.5 s se pega a la siguiente, pegadas se funden, encimadas no.
- Caja: `{ancho, x (centro|izquierda|derecha), abajo}` o `{modo: "franja", y, alto}` (la de Lux: la capa centrada sobre su copia a lo ancho, difuminada).
- `subtitulos=1` quema los subtítulos en la misma pasada (encima de las capas). Sin `segments`, transcribe.
- **Reel Express** acepta `capas` + `carpeta_capas` en `/finish` y los monta en la misma pasada que los subtítulos.

## Tests

`tests/sinteticos.py` arma canciones (bombo y hi-hat con tempo conocido) y videos de planos de colores con cortes en segundos conocidos: nada con derechos y sin internet. `tests/beatmap`, `tests/edicion`, `tests/competitivo/test_edit.py` (incluye el job completo con el VOD simulado), `tests/anime_edit`, `tests/montaje`.

## Pendiente / ideas

- **Capas GSAP de presentations**: `cuadros.mjs --alfa` + un kit de recetas para edits (brasas, rayos, letreros de kill, palabra con máscara) que entren por montaje o por la EDL.
- **Máscaras** (SAM 2.1 / ToonOut) para el texto detrás del personaje, como en el edit de referencia.
- Calibrar `adelanto_kill_s` y la intensidad de los acentos con un edit real de Worlds.
- Detección de secciones con `allin1` (etiquetas reales) si la energía no alcanza.
