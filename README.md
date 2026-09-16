# ⚡ ReelForge

**Local** desktop tool that turns gaming clips into reels/shorts. Drop in a video (or paste a link), tweak, download the result — everything happens on your own machine, nothing is uploaded to the cloud.

It is a web app that runs on your computer: a Flask + FFmpeg server behind the scenes, and an interface that opens in your browser.

> Not meant to be hosted or exposed to the internet. It is a personal tool.

---

## ✨ What's inside

The sidebar is split into sections.

**Automatic** — full pipelines, one step

| | Module | What it does |
|---|---|---|
| ⚡ | **Reel Express** | The whole chain at once: takes your 16:9 clip + audio (link or file), makes it vertical, mixes the audio, adds text/watermark and karaoke subtitles. Before finishing it lets you **review and fix the subtitles** (or skip the review for a faster single-pass render). |
| 🎬 | **Reel Frase** | Two clips chained with background music: clip 1 carries the voice and sets its own length, clip 2 gets stretched to the length you pick. The music ducks under the voice and jumps to full volume at the cut. |

**Modules** — building blocks

| | Module | What it does |
|---|---|---|
| 📐 | **Vertical** | Converts 16:9 → 9:16 with a blurred background and image enhancement. Optional **HUD plate**: crops a region of the source clip (the LoL scoreboard by default) and floats it as a rounded card with a white border and a drop shadow. Live preview. |
| ⬇️ | **Downloader** | Downloads video or audio from a YouTube, TikTok or Instagram link. You pick format (mp3/mp4) and quality. Supports an uploaded `cookies.txt` for YouTube's bot check. |
| 🔊 | **SoundDrop** | Drops a background audio track onto a vertical video, with volume controls, fade, and speed matching. Live mix preview. |
| 🖋️ | **Watermark** | Adds text (primary + secondary) and/or a PNG mark over the video. Reusable watermark gallery. |
| 🗣️ | **Subtitles** | Transcribes the audio (Whisper, running locally) and burns **karaoke** subtitles. You can correct the text before rendering. |

**Tools Specific videos**

| | Module | What it does |
|---|---|---|
| ✂️ | **Insert** | Drops mini-clips into the seconds you mark on the original. Three modes: full cut, *progressive* (slices the mini-clip and reveals a bit more at each marker) and *overlay* (picture-in-picture over the frozen original). |

**Tools**

| | Module | What it does |
|---|---|---|
| 🎙️ | **Unir Audio** | Joins several narration takes into one audio file, in order, capping long pauses so it flows. |

**System**

| | View | What it does |
|---|---|---|
| ℹ️ | **Info** | Versions of every dependency and which video encoder is active (GPU NVENC or CPU). |
| 🕘 | **Historial** | Project timeline: everything that was added, fixed and optimized, read from the repo's git history. Informational only. |

Every module works on its own or **chained** — and **Reel Express** already does the chaining for you.

---

## 🚀 Requirements

- **Python 3.11+** — the only thing you must install yourself (and on Windows `setup.bat` offers to install it for you)
- **FFmpeg** and **ffprobe** ([ffmpeg.org](https://ffmpeg.org/download.html), `winget install Gyan.FFmpeg` on Windows, or `brew install ffmpeg` on Mac — both binaries come together)
- **Node** (or deno), only for the Downloader: yt-dlp needs a JS runtime to solve YouTube's challenges
- **Git**, to pull updates and for the Historial view (it reads the repo's `git log`)
- **NVIDIA GPU** optional: speeds up encoding (NVENC) and transcription. You do **not** need to install CUDA or cuDNN — they ship as pip packages inside the virtual environment; the driver is enough.

Check that FFmpeg is there:

```bash
ffmpeg -version
ffprobe -version
```

---

## 📦 Install

### Windows: double click

Clone the repo (or unzip it) and **double click `setup.bat`**. It creates the virtual environment in `venv\`, installs everything from `requirements.txt` in it, then checks the system programs (FFmpeg, ffprobe, Node, Git) and offers to install the missing ones with winget. Run it again any time: if the venv already exists it is reused and only the packages are updated.

> If Windows blocks the file ("Smart App Control"), it is because the download left a mark on it: right click the ZIP → Properties → **Unblock**, then extract again. Cloning with git avoids this entirely.

### By hand (or on Mac/Linux)

```bash
git clone https://github.com/AxlGuillen/reel-editor.git
cd reel-editor

python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

pip install -r requirements.txt   # Flask + yt-dlp + faster-whisper
```

---

## ▶️ Run it

On Windows, **double click `Iniciar ReelForge.bat`**: it starts the server and opens the browser by itself once the port answers. That console window *is* the server — the app works while it stays open, and closing it shuts everything down.

Anywhere else:

```bash
python3 app.py
```

Then open:

```
http://localhost:5001
```

To stop it: `Ctrl + C` in the terminal.

> **macOS:** use `python3` (not `python`). The port is **5001** because AirPlay holds 5000.

---

## 🕹️ How to use it

1. Pick a module in the sidebar (it opens on **Reel Express**).
2. Load your video and tweak the controls. In Reel Express the settings are grouped into collapsible blocks, each one showing a summary of what it will do even when collapsed.
3. Hit process — a progress bar shows up.
4. In **Subtitles**, **Reel Express** and **Reel Frase** you first review and correct the detected text, then render the final video.
5. When it finishes, the **download** button appears.

Files live temporarily in `uploads/`, `outputs/` and `downloads/` (git-ignored). The **🧹 Limpiar archivos** button empties them. Your watermarks and font live in `assets/` and are never touched.

The app also ships a **light and a dark theme** — the toggle is at the bottom of the sidebar.

### Clip library

Instead of uploading, you can pick a clip straight from your local captures folder with **"Elegir de mis capturas"** in Vertical and Reel Express. The server lists that folder, shows thumbnails, duration, date and size, and processes the original **in place** — which is what makes multi-GB Overwolf recordings work at all, since they blow past the upload limit. Clips that ffprobe cannot read (a recording cut short) are listed as damaged and cannot be picked.

It defaults to `~/Videos/Overwolf/Insights Capture`; point it elsewhere with the `REELFORGE_CLIPS_FOLDER` environment variable.

---

## ⚙️ Configuration

`config.py` resolves FFmpeg/ffprobe on its own (PATH first, then the usual locations). If yours live somewhere unusual, force the path with environment variables:

```bash
export FFMPEG_PATH=/path/to/ffmpeg
export FFPROBE_PATH=/path/to/ffprobe
export REELFORGE_CLIPS_FOLDER=/path/to/your/captures
```

On Windows it usually looks like `C:/ffmpeg/bin/ffmpeg.exe`.

**Text and subtitles** use whatever font you drop in `assets/fonts/` (a `.ttf`/`.otf`). **Watermarks** are PNGs with transparency, uploaded from the app itself.

---

## 🛠️ Stack

Python · Flask · FFmpeg/ffprobe · yt-dlp · faster-whisper · HTML/CSS/Vanilla JS (no frameworks, no build step).

The internal architecture and the guide to adding modules live in [`CLAUDE.md`](CLAUDE.md).

---

## 📌 Notes

- **Subtitles:** the first transcription **downloads the Whisper model** (~1.6 GB for the recommended one), once. On an NVIDIA GPU it runs on CUDA; on Mac it runs on CPU, so pick a smaller model (Small/Medium) if it drags.
- **yt-dlp** breaks often when platforms change; if the Downloader starts failing, run `pip install -U yt-dlp`.
- **Instagram** is the least reliable (it sometimes asks for a login). Public YouTube and TikTok are solid.
- The `cookies/` folder holds **your YouTube session** if you upload a `cookies.txt`. It is git-ignored on purpose — never commit it, and never copy it to another machine; export a fresh one there instead.
- Downloading from these platforms may go against their terms of service; use it for your own or personal-use content.
- There is no authentication because it is local. Do not expose the port on a public network.

---

<p align="center">
  <img src="static/img/4xl-logo.svg" alt="4XL" width="72">
</p>
