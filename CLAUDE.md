# CLAUDE.md — ReelForge

## Qué es este proyecto

App web local para procesar clips de gaming (League of Legends, Valorant) de formato horizontal (16:9) a vertical (9:16) para reels/shorts. Corre completamente en local: un servidor Flask en Python que llama a FFmpeg para el procesamiento, con una interfaz web que se abre en el browser.

**No está pensado para hostearse.** Es una herramienta personal de escritorio que se corre con `python app.py`.

---

## Stack

- **Backend:** Python 3.11+ con Flask
- **Procesamiento de video:** FFmpeg (llamado como subprocess desde Python)
- **Frontend:** HTML + CSS + Vanilla JS (sin frameworks, sin build steps)
- **Almacenamiento:** Sistema de archivos local (carpeta `uploads/` y `outputs/` temporales)

---

## Estructura del proyecto

```
reelforge/
├── app.py                  ← Entry point Flask, registra blueprints
├── config.py               ← Configuración global (paths, defaults, FFmpeg)
├── requirements.txt
│
├── modules/                ← Cada feature es un módulo independiente
│   ├── __init__.py
│   ├── vertical_convert/   ← MÓDULO 1: Conversión horizontal → vertical
│   │   ├── __init__.py
│   │   ├── routes.py       ← Blueprint Flask con sus endpoints
│   │   ├── processor.py    ← Lógica FFmpeg de este módulo
│   │   └── schema.py       ← Parámetros y validación de este módulo
│   │
│   ├── trim/               ← MÓDULO 2 (futuro): Trim del clip
│   │   └── ...
│   │
│   └── templates_engine/   ← MÓDULO 3 (futuro): Templates de video
│       └── ...
│
├── core/
│   ├── ffmpeg_runner.py    ← Wrapper central para ejecutar FFmpeg
│   ├── job_manager.py      ← Maneja el estado de los jobs de procesamiento
│   └── file_utils.py       ← Helpers para manejo de archivos temporales
│
├── static/
│   ├── css/
│   │   └── main.css
│   └── js/
│       └── main.js
│
└── templates/
    └── index.html          ← UI principal
```

---

## Arquitectura: cómo escala

Cada feature nueva es un **módulo** dentro de `modules/`. Para agregar uno:

1. Crear carpeta `modules/nombre_modulo/`
2. Definir un **Blueprint Flask** en `routes.py` con sus endpoints
3. La lógica de FFmpeg va en `processor.py` — siempre usando `core/ffmpeg_runner.py` para ejecutar comandos
4. Registrar el blueprint en `app.py`

El `core/ffmpeg_runner.py` es el único lugar donde se ejecutan comandos de FFmpeg. Ningún módulo llama a subprocess directamente.

---

## Módulo 1: vertical_convert (MVP)

### Qué hace

Convierte un clip horizontal (16:9) a vertical (9:16) con efecto "split blur background":
- **Capa de fondo:** El mismo clip escalado para llenar 9:16, con blur gaussiano configurable (~40% por defecto) y ligera reducción de brillo/opacidad
- **Capa principal:** El clip original centrado verticalmente, sin modificar, en su aspect ratio original

### Parámetros configurables desde la UI

| Parámetro | Tipo | Default | Rango |
|---|---|---|---|
| `blur_intensity` | int | 20 | 0–50 (radio gaussiano FFmpeg) |
| `bg_brightness` | float | 0.6 | 0.3–1.0 (multiplicador de brillo) |
| `main_clip_scale` | float | 1.0 | 0.7–5.0 (zoom del clip principal) |
| `main_clip_position` | string | "center" | "center", "top", "bottom" |

### Endpoints

```
POST /api/vertical-convert/process
  Body: multipart/form-data
    - video: archivo
    - blur_intensity: int
    - bg_brightness: float
    - main_clip_scale: float
    - main_clip_position: string

  Response: 
    202 { job_id: "abc123" }

GET /api/vertical-convert/status/<job_id>
  Response:
    { status: "processing" | "done" | "error", progress: 0-100 }

GET /api/vertical-convert/download/<job_id>
  Response: video/mp4 (descarga directa al browser)
```

### Lógica FFmpeg (referencia)

El comando base que debe construir `processor.py`:

```bash
ffmpeg -i input.mp4 \
  -filter_complex "
    [0:v]scale=1080:1920:force_original_aspect_ratio=increase,
         crop=1080:1920,
         gblur=sigma=20,
         eq=brightness=-0.2[bg];
    [0:v]scale=-2:720[fg];
    [bg][fg]overlay=(W-w)/2:(H-h)/2[out]
  " \
  -map "[out]" -map 0:a? \
  -c:v libx264 -crf 18 -preset fast \
  -c:a copy \
  output.mp4
```

Los valores de `sigma`, `brightness`, y el scale de `[fg]` se calculan dinámicamente desde los parámetros del request.

---

## Módulo 2 (futuro): trim

Permite al usuario seleccionar un rango de tiempo del clip antes de procesar. Se integra como paso previo al `vertical_convert` o como módulo standalone.

Parámetros: `start_time` (segundos), `end_time` (segundos).

---

## Módulo 3 (futuro): templates_engine

Sistema de templates de edición automática. Cada template es una clase Python con una interfaz común:

```python
class BaseTemplate:
    name: str
    description: str
    params_schema: dict

    def build_ffmpeg_commands(self, input_path: str, params: dict) -> list[str]:
        # Retorna lista de comandos FFmpeg a ejecutar en secuencia
        ...
```

### Template planeado: "Flashforward" (teaser loop)

Patrón de video donde se muestra el clip final (el momento épico) al inicio, luego cortes cada N segundos que intercalan el presente con el final, para crear suspenso. Común en clips de penta kills, fails, etc.

Parámetros del template:
- `spoiler_clip_start`: segundo donde empieza el momento épico
- `spoiler_clip_duration`: duración del spoiler inicial (ej. 2s)
- `cut_interval`: cada cuántos segundos interrumpir con el flashforward (ej. 6s)
- `flashforward_duration`: duración de cada interrupción (ej. 1.5s)

El resultado es una secuencia de segmentos concatenados con `concat` filter de FFmpeg.

---

## core/ffmpeg_runner.py — contrato esperado

```python
def run(command: list[str], job_id: str) -> None:
    # Ejecuta FFmpeg como subprocess
    # Parsea stderr para extraer progreso (frame=, time=)
    # Actualiza job_manager con el progreso
    # Lanza excepción si FFmpeg retorna error
    ...
```

---

## core/job_manager.py — contrato esperado

Jobs en memoria (dict) por simplicidad en MVP. Cada job tiene:

```python
{
  "job_id": str,
  "status": "pending" | "processing" | "done" | "error",
  "progress": int,        # 0-100
  "input_path": str,
  "output_path": str,
  "error": str | None
}
```

---

## config.py

```python
UPLOAD_FOLDER = "./uploads"
OUTPUT_FOLDER = "./outputs"
MAX_UPLOAD_SIZE_MB = 2000
FFMPEG_PATH = "ffmpeg"       # O path absoluto si no está en PATH
ALLOWED_EXTENSIONS = {"mp4", "mov", "mkv", "avi"}
```

---

## UI (templates/index.html)

Una sola página. Layout:

1. **Header** — nombre de la app
2. **Upload zone** — drag & drop o click para seleccionar video
3. **Preview panel** — muestra el video subido con player básico
4. **Controls panel** — sliders para blur, brillo, posición del clip
5. **Process button** — trigger del procesamiento
6. **Progress bar** — polling al endpoint de status cada segundo
7. **Download button** — aparece cuando el job termina, descarga directo

La UI hace fetch a los endpoints REST, sin librerías frontend externas (solo Vanilla JS).

---

## Cómo correrlo

```bash
# Instalar dependencias
pip install flask

# Asegurarse que FFmpeg esté instalado
ffmpeg -version

# Correr la app
python app.py

# Abrir en browser
# http://localhost:5000
```

---

## Notas para desarrollo

- El procesamiento de FFmpeg es **bloqueante** en MVP. Si se necesita concurrencia después, migrar `job_manager` a usar `threading` o `celery`.
- Los archivos en `uploads/` y `outputs/` son temporales. Agregar limpieza automática después de descarga (o TTL).
- En Windows, `FFMPEG_PATH` puede necesitar ser el path completo: `C:/ffmpeg/bin/ffmpeg.exe`
- No hay autenticación porque es local. No exponer el puerto en red pública.
