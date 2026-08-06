"""Parámetros y validación del módulo downloader.

Descarga un asset (video mp4 o audio mp3) desde un link de YouTube, TikTok,
Instagram, etc., usando yt-dlp. La calidad se elige según el formato.
"""
from dataclasses import dataclass

# Calidades válidas por formato.
VIDEO_QUALITIES = {"max", "1080", "720", "480"}   # altura máxima ("max" = mejor)
AUDIO_QUALITIES = {"320", "192", "128"}           # bitrate mp3 en kbps

DEFAULT_VIDEO_QUALITY = "1080"
DEFAULT_AUDIO_QUALITY = "192"

# Navegadores de los que yt-dlp sabe leer cookies (para el bot-check de YouTube).
BROWSERS = {"chrome", "edge", "firefox", "brave", "opera", "vivaldi", "chromium"}


@dataclass
class DownloaderParams:
    url: str
    format: str = "audio"          # "audio" | "video"
    quality: str = DEFAULT_AUDIO_QUALITY
    # Navegador del que tomar cookies, o "" para no usarlas. Solo hace falta
    # cuando YouTube pide verificar que no sos un bot.
    cookies_browser: str = ""

    @classmethod
    def from_form(cls, form) -> "DownloaderParams":
        """Construye y valida parámetros desde un form de Flask (request.form).

        Lanza ValueError con un mensaje legible si algo es inválido.
        """
        url = (form.get("url") or "").strip()
        if not url:
            raise ValueError("Falta el link del video.")
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("El link debe empezar con http:// o https://")

        fmt = (form.get("format") or "audio").lower()
        if fmt not in {"audio", "video"}:
            raise ValueError("format debe ser 'audio' o 'video'.")

        quality = (form.get("quality") or "").strip()
        valid = VIDEO_QUALITIES if fmt == "video" else AUDIO_QUALITIES
        if not quality:
            quality = DEFAULT_VIDEO_QUALITY if fmt == "video" else DEFAULT_AUDIO_QUALITY
        if quality not in valid:
            raise ValueError(
                f"Calidad inválida para {fmt}: {quality!r}. Opciones: {sorted(valid)}"
            )

        browser = (form.get("cookies_browser") or "").strip().lower()
        if browser and browser not in BROWSERS:
            raise ValueError(
                f"Navegador inválido: {browser!r}. Opciones: {sorted(BROWSERS)}"
            )

        return cls(url=url, format=fmt, quality=quality, cookies_browser=browser)
