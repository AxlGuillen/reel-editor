# Banco de efectos del motor de edits

Los efectos que entiende `modules/editing`. El catálogo legible por máquina es [`modules/editing/catalog.json`](../modules/editing/catalog.json), que también sirve `GET /api/editing/catalog`. La vista **Efectos** (sección Edits) arma una galería con cada efecto sobre la misma toma. El edit completo y los planificadores están en [`edits.md`](edits.md).

## Dos clases de efecto

- **De salida** (`stage: output`): un `Event` de la EDL en **tiempo de salida**. Va en la pasada 2, sobre el edit ya unido, y no depende de qué toma está debajo.
- **De la toma** (`stage: source`): un campo del `Segment` que actúa sobre los cuadros de la fuente en la pasada 1 (`mblur`, `dedupe`) o la forma en que se reparte su tiempo (`ramp`, con `timing.hit_ramp`).

Todos los efectos de salida con duración (`invert`, `strobe`, `flash` con `shape="mix"`, `rgb`, `black`, `panel`, `glow`) se prenden **por número de cuadro**, en la ventana [t, t + dur) de `effects.frames_of`. Antes, `between(t, a, b)` incluía el borde final y prendía un cuadro de más. Las pruebas (`tests/editing/test_effects.py`) renderizan cada efecto y lo comparan contra el mismo render sin eventos: el primer y el último cuadro que cambian tienen que ser los de su ventana.

## Efectos de salida

| Efecto | Qué hace | Parámetros (por omisión) | Del mapa | Cuadros típicos |
|---|---|---|---|---|
| `flash` | blanquea y se apaga (pulso cuadrático) | `intensity` 1, `dur` 0.18 | — | 3–6 |
| `flash` + `shape="mix"` | mezcla fija con blanco toda su duración | `intensity` 0.55, `dur` 0.07 | — | 2–3 |
| `zoom` | golpe de zoom de 12 % que vuelve | `intensity`, `dur` 0.18, `x`, `y`, `attack` | `x`, `y` = centro de la acción | 5–8 |
| `shake` | sacudida a 13 Hz que decae como (1 − x)^`decay` | `intensity` 0.6, `dur` 0.3, `angle`, `decay` 2 | `angle` = el del golpe | 6–12 |
| `rgb` | flecos de color: los planos de croma corridos | `intensity` 1, `dur` 0.12 | — | 3–6 |
| `push` | acercamiento lento a 1 + `level` | `level` 0.08, `dur` 2 | `x`, `y` | 30–120 |
| `hold` | zoom sostenido (escalón) | `level` 0.15, `dur` 0.45 | `x`, `y` | 10–30 |
| `whip` | transición: golpe de zoom más desenfoque | `intensity` 1, `dur` 0.2, `angle` | `angle` = golpe o paneo | 5–8 |
| `black` | cuadro negro | `dur` 0.3 | — | 6–12 |
| `panel` | panel de manga sobre el cuadro congelado | `image`, `dim` 0.62, `enter` 0.12, `from_scale` 0.93, `drift` 0.035 | — | 27 (2 tiempos) |
| `invert` | negativo | `dur` 0.07 | — | 1–3 |
| `strobe` | un cuadro blanco cada `every` | `intensity` 0.8, `dur` 0.5, `every` 2 | — | 12–30 |
| `glow` | pulso de brillo (bloom) | `intensity` 1, `dur` 0.4, `attack` 0.03 | — | 8–15 |

**Con dirección** (`angle`: 0° a la derecha, 90° arriba, como el mapa de movimiento):
- `shake` se mueve solo en el eje del golpe y arranca hacia donde va: el cuadro retrocede como si lo hubieran golpeado. Sin `angle`, sacude en los dos ejes como antes.
- `whip` lanza el cuadro 8 % del ancho hacia ese lado. El desenfoque va en su eje, **cuantizado a horizontal o vertical**, porque `gblur` no desenfoca en otros ángulos.
- El ángulo lo pone el planificador con el del `impact` (o `smear`, `pan`) más cercano. El zoom, `push` y `hold` apuntan a `motion.processor.focus_at(m, t)`.

**Lo que se midió y se corrigió en esta fase:**
- `rgb` usaba `rgbashift`, que trabaja en RGB. FFmpeg convertía **todo** el edit a RGB y de vuelta, aunque el efecto estuviera apagado, y la ida y vuelta recortaba los colores saturados (luma −2.9 de media en un dibujo con colores planos). Ahora corre los planos de croma en YUV (`chromashift`): da los mismos flecos sin tocar el resto.
- `glow` mezcla en modo pantalla **solo la luma**. En YUV el negro tiene croma 128, y una mezcla de los tres planos corría el color de todos los cuadros; pasar por RGB para mezclar los tres movía todo ~5 niveles. Además se prende solo dentro de sus pulsos (`enable`).

## Efectos de la toma

| Efecto | Campo | Qué hace |
|---|---|---|
| `ramp` | las piezas (`timing.hit_ramp(start, hit, end, dur, hit_out, before, after)`) | Reparte [start, hit] en [0, hit_out] y [hit, end] en [hit_out, dur], cada mitad con su perfil. El cuadro del golpe cae exacto en `hit_out` (medido: el cuadro 18 de salida muestra el cuadro 60 de la fuente). Las velocidades no se recortan acá: lo hace el planificador con `clamp_speeds` |
| `mblur` | `Segment.mblur = {angle, amount}` | Desenfoque de movimiento en **cualquier ángulo**: gira el cuadro hasta que el movimiento queda horizontal, desenfoca en horizontal y lo devuelve. `amount` son px en un cuadro de 1920 de ancho. Va en todo el plano (para un tramo, partir el plano) |
| `dedupe` | `Segment.dedupe = True` | Con RIFE (si está instalado): antes de interpolar, quita los dibujos repetidos del anime (en dos o en tres) con `external.drop_repeats`. RIFE rellena la misma duración solo con dibujos distintos. Sin esto, interpolar entre dos copias del mismo dibujo no inventa nada y después salta |

## Seguir al personaje (`follow`, fase 5)

Un `Event("follow", image=…, track=[[t, x, y, w], …], opacity, fade)` en tiempo y píxeles de salida. `editing/panels.follow_frames` dibuja la imagen centrada en el punto de cada cuadro, `w` de ancho y con fundido de entrada y salida. Con el mismo camino de los paneles (PNG RGBA por cuadro + `overlay`), no hace falta `sendcmd` ni expresiones.

La trayectoria sale de `motion.subject.track(video, inicio, fin, pista)`:
- **Detección sin PyTorch:** los detectores de anime de deepghs (YOLOv8 en ONNX, persona y cabeza) con `onnxruntime`.
  - Se bajan de Hugging Face la primera vez (~42 MB cada uno) a `cache/models/deepghs/`.
  - Sin `onnxruntime` o sin los modelos, `subject.available()` es falso y todo lo demás sigue igual. `GET /api/editing/options` trae `subject_tracking`.
- **Cuál personaje:** el que tiene el pecho más cerca de la pista (en el edit 4, el punto que se había puesto a mano). Después, cuadro a cuadro, la detección más cercana a donde estaba; un salto de más del 15 % del ancho es otro personaje.
- **El pecho:** debajo de la cabeza dentro de la caja de la persona (centro de la cabeza, 0.4 alturas de cabeza bajo su borde). En un primer plano sin persona (el demonio llenando el cuadro), la cabeza sola. Sin cabeza, el 35 % de la caja.
- **Huecos y suavizado:** los cuadros sin detección se interpolan; promedio móvil centrado de 5 cuadros.
- **Corte:** la trayectoria termina en el primer corte de la fuente dentro del tramo (mapa de movimiento). Si no, el logo quedaba flotando sobre el plano siguiente.

**Medido sobre el edit 4** (`python -m modules.anime_edit.edit4 "<carpeta>" <base del edit 3>`):
- Las 7 ventanas de logos en el pecho de Magna y Dante, con detección en 213 de 222 cuadros.
- En los recortes a resolución completa (inicio, mitad y final de cada ventana), el logo va sobre el pecho y lo sigue.
- La ventana de 6.9 s termina en el cambio de plano de la fuente (7.66 s) en vez de quedar flotando.
- ~3 s por segundo de video, porque se detecta en cada cuadro.

## La galería

`POST /api/editing/gallery` con `path` (o `video`, `library_clip`, `job_id`), `start` y `effects` (ids; por omisión, todos):
- Renderiza 2.4 s de la toma por efecto, con el efecto en el segundo 1, a 540×960 y 30 fps, más un clip **sin efecto** para comparar.
- Antes lee el mapa de movimiento de ese tramo: los efectos con dirección toman el ángulo del golpe más cercano y el zoom apunta al centro de la acción.
- La hoja (`/gallery/sheet/<id>`) muestra un cuadro por efecto, dos cuadros después de que empieza; en `push` y `ramp` son 20.
- `panel` (necesita una imagen) y `dedupe` (necesita RIFE) no salen en la galería.

Medido sobre la toma 09 de Magna vs Dante desde 20:49.2: el golpe de 1250.21 s da 327° y centro (0.50, 0.15). Son 15 clips en ~40 s.
