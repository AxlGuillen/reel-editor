# assets/overlays

Assets para el efecto `overlay` del motor de edits (`modules/editing/bank2.py`):
light leaks, brasas, polvo, destellos de lente, texturas de papel… Los consigue
Axl; ReelForge no los descarga.

- **Formatos:** PNG, JPG o WebP (una imagen fija) o MP4 / MOV / WebM (un video,
  que se repite en bucle si dura menos que el efecto).
- **Modo:** `style` del evento — `screen` (pantalla, el de siempre para luces
  sobre fondo negro), `add` (suma, más fuerte) o `normal` (con su alfa, para un
  PNG con transparencia). `opacity` (0.05–1) por la intensidad.
- **Encuadre:** el asset se escala para cubrir el cuadro (1080×1920) y se
  recorta al centro.
- **En la EDL:** `Event(t, "overlay", dur=0.8, image="assets/overlays/leak.mp4", style="screen")`.

La galería de efectos usa el primero de esta carpeta (en orden alfabético); si
está vacía, el overlay no sale en la galería.
