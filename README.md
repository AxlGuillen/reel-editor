# ⚡ ReelForge

Herramienta de escritorio **local** para convertir y editar clips de gaming en reels/shorts. Subí un video (o pegá un link), ajustá, y descargá el resultado — todo en tu máquina, sin subir nada a la nube.

Es una app web que corre en tu propia computadora: un servidor Flask + FFmpeg por detrás, y una interfaz que se abre en el browser.

> No está pensada para hostearse ni exponerse a internet. Es una herramienta personal.

---

## ✨ Módulos

La barra lateral está dividida en secciones:

**Automático**
| | Módulo | Qué hace |
|---|---|---|
| ⚡ | **Reel Express** | El pipeline completo en un paso: toma tu clip 16:9 + audio (link o archivo), lo verticaliza, le mezcla el audio, le pone texto/marca y subtítulos automáticos. Antes de terminar, te deja **revisar y corregir los subtítulos**. |

**Módulos** (bloques de construcción)
| | Módulo | Qué hace |
|---|---|---|
| 📐 | **Vertical** | Convierte clips 16:9 → 9:16 con fondo desenfocado (blur) y realce de imagen. Preview en vivo. |
| ⬇️ | **Downloader** | Descarga el video o el audio de un link de YouTube, TikTok o Instagram. Elegís formato (mp3/mp4) y calidad. |
| 🔊 | **SoundDrop** | Le pone un audio de fondo a un video vertical, con control de volúmenes, fade y ajuste de velocidad. Preview de mezcla en vivo. |
| 🖋️ | **Watermark** | Agrega texto (principal + secundario) y/o una marca PNG sobre el video. Galería de watermarks reutilizables. |
| 🗣️ | **Subtítulos** | Transcribe el audio (Whisper local) y quema subtítulos estilo **karaoke**. Podés corregir el texto antes de generar. |

**Tools Specific videos**
| | Módulo | Qué hace |
|---|---|---|
| ✂️ | **Insert** | Inserta un mini-clip en los segundos que marques del video. Marcás los puntos en un timeline y se arma solo. |

**Tools**
| | Módulo | Qué hace |
|---|---|---|
| 🎙️ | **Unir Audio** | Une varias narraciones en un solo audio, en orden, y recorta las pausas largas para que fluya. |

Cada módulo sirve por separado o **en cadena** — y **Reel Express** ya hace esa cadena por vos.

---

## 🚀 Requisitos

- **Python 3.11+**
- **FFmpeg** y **ffprobe** instalados ([ffmpeg.org](https://ffmpeg.org/download.html) o `brew install ffmpeg` en Mac, que trae ambos)

Verificá que estén:

```bash
ffmpeg -version
ffprobe -version
```

---

## 📦 Instalación

```bash
git clone <este-repo>
cd reel-editor

# (recomendado) entorno virtual
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt  # Flask + yt-dlp + faster-whisper
```

---

## ▶️ Cómo correrlo

```bash
python3 app.py
```

Luego abrí en el browser:

```
http://localhost:5001
```

Para detenerlo: `Ctrl + C` en la terminal.

> **macOS:** usá `python3` (no `python`). El puerto es **5001** porque el 5000 lo ocupa el receptor de AirPlay.

---

## 🕹️ Cómo se usa

1. Elegí un módulo en la barra lateral (arranca en **Reel Express**).
2. Subí tu video (o pegá un link) y ajustá los controles.
3. Dale a procesar — vas a ver una barra de progreso.
4. En **Subtítulos** y **Reel Express**, primero revisás/corregís el texto detectado y luego generás el video final.
5. Cuando termina, aparece el botón de **descarga**.

Los archivos quedan temporalmente en `uploads/`, `outputs/` y `downloads/` (ignorados por git). El botón **🧹 Limpiar archivos** los vacía. Tus watermarks y la fuente viven en `assets/` y no se borran.

---

## ⚙️ Configuración

`config.py` resuelve FFmpeg/ffprobe automáticamente (busca en el PATH y en ubicaciones típicas). Si los tenés en un lugar no estándar, podés forzar la ruta con variables de entorno:

```bash
export FFMPEG_PATH=/ruta/a/ffmpeg
export FFPROBE_PATH=/ruta/a/ffprobe
```

En Windows suele ser algo como `C:/ffmpeg/bin/ffmpeg.exe`.

Para los **textos y subtítulos** se usa la fuente que dejes en `assets/fonts/` (un `.ttf`/`.otf`). Para los **watermarks**, PNG con transparencia (se suben desde la app).

---

## 🛠️ Stack

Python · Flask · FFmpeg/ffprobe · yt-dlp · faster-whisper · HTML/CSS/Vanilla JS (sin frameworks, sin build step).

La arquitectura interna y las guías para agregar módulos están en [`CLAUDE.md`](CLAUDE.md).

---

## 📌 Notas

- **Subtítulos:** la primera transcripción **descarga el modelo de Whisper** (~1.6 GB el recomendado). En Mac corre en CPU; si va lento, elegí un modelo más chico (Small/Medium).
- **yt-dlp** se actualiza seguido; si el Downloader empieza a fallar con algún sitio, corré `pip install -U yt-dlp`.
- El **Instagram** es el menos confiable (a veces pide login). YouTube y TikTok públicos andan bien.
- Descargar de estas plataformas puede ir contra sus términos de servicio; usalo para contenido propio o de uso personal.
- No hay autenticación: no expongas el puerto en una red pública.
