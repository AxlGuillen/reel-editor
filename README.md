# ⚡ ReelForge

Herramienta de escritorio **local** para convertir y editar clips de gaming en reels/shorts. Sube un video (o pega un link), ajustá, y descargá el resultado — todo en tu máquina, sin subir nada a la nube.

Es una app web que corre en tu propia computadora: un servidor Flask + FFmpeg por detrás, y una interfaz que se abre en el browser.

> No está pensada para hostearse ni exponerse a internet. Es una herramienta personal.

---

## ✨ Módulos

| | Módulo | Qué hace |
|---|---|---|
| 📐 | **Vertical** | Convierte clips 16:9 → 9:16 con fondo desenfocado (blur) y realce de imagen. Preview en vivo mientras ajustás. |
| 🔊 | **SoundDrop** | Le pone un audio de fondo a un video vertical, con control de volúmenes, fade y ajuste de velocidad. Preview de mezcla en vivo. |
| ✂️ | **Insert** | Inserta un mini-clip en los segundos que marques del video. Marcás los puntos en un timeline y se arma solo. |
| ⬇️ | **Downloader** | Descarga el video o el audio de un link de YouTube, TikTok o Instagram. Elegís formato (mp3/mp4) y calidad. |

Cada módulo sirve por separado o **en cadena**: por ejemplo, descargás un audio con Downloader → lo usás en SoundDrop; o pasás dos clips por Vertical → los combinás con Insert.

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

pip install -r requirements.txt  # Flask + yt-dlp
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

1. Elegí un módulo en la barra lateral.
2. Subí tu video (o pegá un link, en Downloader) y ajustá los controles.
3. Dale a procesar — vas a ver una barra de progreso.
4. Cuando termina, aparece el botón de **descarga**.

Los archivos quedan temporalmente en `uploads/`, `outputs/` y `downloads/` (ignorados por git).

---

## ⚙️ Configuración

`config.py` resuelve FFmpeg/ffprobe automáticamente (busca en el PATH y en ubicaciones típicas). Si los tenés en un lugar no estándar, podés forzar la ruta con variables de entorno:

```bash
export FFMPEG_PATH=/ruta/a/ffmpeg
export FFPROBE_PATH=/ruta/a/ffprobe
```

En Windows suele ser algo como `C:/ffmpeg/bin/ffmpeg.exe`.

---

## 🛠️ Stack

Python · Flask · FFmpeg/ffprobe · yt-dlp · HTML/CSS/Vanilla JS (sin frameworks, sin build step).

La arquitectura interna y las guías para agregar módulos están en [`CLAUDE.md`](CLAUDE.md).

---

## 📌 Notas

- **yt-dlp** se actualiza seguido; si el Downloader empieza a fallar con algún sitio, corré `pip install -U yt-dlp`.
- El **Instagram** es el menos confiable (a veces pide login). YouTube y TikTok públicos andan bien.
- Descargar de estas plataformas puede ir contra sus términos de servicio; usalo para contenido propio o de uso personal.
- No hay autenticación: no expongas el puerto en una red pública.
