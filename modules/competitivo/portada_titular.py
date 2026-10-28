"""The cover with a headline (the style of the Day 4 covers): the phase in lime, both logos with VS, a big
white headline, a yellow pill and the champion's art, over the same art blurred and dimmed.

    WORLDS 2026 · FASE SUIZA · RONDA 3
    [logo] VS [logo]
    MKOI        DK
    PRIMER PENTAKILL
    DEL MUNDIAL
    [ SMASH LE HACE PENTAKILL A KOI ]
    [ the champion's splash art ]

It draws with Pillow and gives the same answer as intro.render_portada ({png, jpg, fondo, logos}), so the
"aparte" intro and the cover image use it as they use the template. Nothing here can fail the job: without
the art the background is the gradient, without a logo the team's code is written in a circle.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config
from modules.competitivo import cards, intro

W, H = 1080, 1920
ANTON = intro.TITULO
POPPINS = os.path.join(config.FONTS_FOLDER, "Poppins-ExtraBold.ttf")
WHITE, BLACK = (255, 255, 255), (0, 0, 0)
LIME = intro.ACENTO_RGB
YELLOW = (253, 212, 38)
ART_DIM = 0.32                    # the background art, dimmed
Y_FASE, Y_LOGOS, LOGO = 351, 590, 300
Y_CODES = 835
Y_TITULAR, TITULAR_MAX_W, TITULAR_MAX_PX = 1010, 960, 250
Y_PILDORA, PILDORA_H, PILDORA_MAX_W = 1370, 112, 940
Y_ARTE, ARTE_W, ARTE_H = 1530, 920, 330


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _fit(text: str, path: str, max_w: int, max_px: int, min_px: int = 40, stroke: int = 0) -> ImageFont.FreeTypeFont:
    """The biggest font (up to max_px) whose text is at most max_w wide."""
    size = max_px
    while size > min_px:
        f = _font(path, size)
        if f.getlength(text) + 2 * stroke <= max_w:
            return f
        size -= 4
    return _font(path, min_px)


def _text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font, fill=WHITE, stroke: int = 8) -> None:
    """Centered text with a black outline (xy is the center)."""
    draw.text(xy, text, font=font, fill=fill, anchor="mm", stroke_width=stroke, stroke_fill=BLACK)


def _background(art: str | None) -> tuple[Image.Image, str]:
    if art and os.path.isfile(art):
        img = Image.open(art).convert("RGB")
        k = H / img.height
        img = img.resize((round(img.width * k), H), Image.LANCZOS)
        left = (img.width - W) // 2
        img = img.crop((left, 0, left + W, H)).filter(ImageFilter.GaussianBlur(26))
        return img.point(lambda v: int(v * ART_DIM)), "imagen"
    top, bottom = ((int(c[2:4], 16), int(c[4:6], 16), int(c[6:8], 16)) for c in intro.DEGRADADO)
    img = Image.new("RGB", (W, H))
    px = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        px.line([(0, y), (W, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return img, "degradado"


def _logo(path: str | None, code: str, side: int) -> tuple[Image.Image, str]:
    if path:
        try:
            img = Image.open(path).convert("RGBA")
            img.thumbnail((side, side), Image.LANCZOS)
            return img, "imagen"
        except Exception:  # noqa: BLE001 - a broken logo: the code in a circle
            pass
    img = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, side - 4, side - 4), fill=intro.CIRCULO_RGB + (255,), outline=LIME + (255,), width=8)
    d.text((side / 2, side / 2), code, font=_fit(code, ANTON, side - 70, 130), fill=WHITE, anchor="mm")
    return img, "codigo"


def _paste_shadowed(base: Image.Image, img: Image.Image, center: tuple[int, int]) -> None:
    """The image over a soft shadow, centered at `center`."""
    x, y = center[0] - img.width // 2, center[1] - img.height // 2
    shadow = Image.new("RGBA", (img.width + 80, img.height + 80), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 150), (40, 48), img.getchannel("A"))
    shadow = shadow.filter(ImageFilter.GaussianBlur(16))
    base.alpha_composite(shadow, (x - 40, y - 40)) if x >= 40 and y >= 40 else None
    base.alpha_composite(img, (x, y))


def _rounded(img: Image.Image, radius: int) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def _splash(champion: str) -> str | None:
    """The champion's splash art (1215x717, Data Dragon), cached; None without it."""
    if not champion:
        return None
    path = os.path.join(cards.CACHE, "art", f"splash_{cards._plain(champion)}.jpg")
    if os.path.isfile(path):
        return path
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        intro.descargar(f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{champion}_0.jpg", path,
                        timeout=10, reintentos=1, max_bytes=3_000_000)
        Image.open(path).verify()
        return path
    except Exception:  # noqa: BLE001
        if os.path.isfile(path):
            os.remove(path)
        return None


def render(enc: "intro.Encuentro", carpeta: str, titular: str, pildora: str = "", campeon: str = "",
           jugador: str = "") -> dict:
    """The cover in `carpeta`/portada.png (+ portada.jpg). `titular`: up to 3 lines separated by \\n.
    `campeon`: the champion (Data Dragon's name, "Kaisa") whose splash art goes at the bottom and blurred
    behind; `jugador`: his name for the caption."""
    os.makedirs(carpeta, exist_ok=True)
    splash = _splash(campeon)
    base, fondo = _background(splash)
    img = base.convert("RGBA")
    d = ImageDraw.Draw(img)

    if enc.fase:
        _text(d, (W / 2, Y_FASE), enc.fase.upper(), _fit(enc.fase.upper(), POPPINS, 980, 52, 30, 6), LIME, 6)

    logos = {}
    for code, path, name, cx, key in ((enc.codigo_azul, enc.logo_azul, enc.azul, 270, "azul"),
                                      (enc.codigo_rojo, enc.logo_rojo, enc.rojo, 810, "rojo")):
        logo, logos[key] = _logo(path, code, LOGO)
        _paste_shadowed(img, logo, (cx, Y_LOGOS))
        _text(d, (cx, Y_CODES), code, _fit(code, ANTON, 400, 96, 50, 6), WHITE, 6)
    _text(d, (W / 2, Y_LOGOS + 25), "VS", _font(ANTON, 230), WHITE, 9)

    lines = [ln.strip().upper() for ln in (titular or "").splitlines() if ln.strip()][:3]
    if lines:
        size = min(_fit(ln, ANTON, TITULAR_MAX_W, TITULAR_MAX_PX, 60, 10).size for ln in lines)
        f = _font(ANTON, size)
        gap = round(size * 1.02)
        y0 = Y_TITULAR + (len(lines) - 2) * (-gap / 2)
        for i, ln in enumerate(lines):
            _text(d, (W / 2, y0 + i * gap), ln, f, WHITE, 10)

    if pildora:
        f = _fit(pildora.upper(), ANTON, PILDORA_MAX_W - 90, 84, 40)
        w = min(PILDORA_MAX_W, round(f.getlength(pildora.upper())) + 90)
        d.rounded_rectangle((W / 2 - w / 2, Y_PILDORA - PILDORA_H / 2, W / 2 + w / 2, Y_PILDORA + PILDORA_H / 2),
                            radius=26, fill=YELLOW)
        d.text((W / 2, Y_PILDORA + 3), pildora.upper(), font=f, fill=(20, 20, 20), anchor="mm")

    if splash:
        art = Image.open(splash).convert("RGB")
        k = ARTE_W / art.width
        art = art.resize((ARTE_W, round(art.height * k)), Image.LANCZOS)
        top = max(0, round((art.height - ARTE_H) * 0.18))           # a bit above the middle: the face
        banner = _rounded(art.crop((0, top, ARTE_W, top + ARTE_H)), 34)
        ring = Image.new("RGBA", (ARTE_W + 12, ARTE_H + 12), (0, 0, 0, 0))
        ImageDraw.Draw(ring).rounded_rectangle((0, 0, ring.width - 1, ring.height - 1), 40, fill=WHITE + (255,))
        img.alpha_composite(ring, ((W - ring.width) // 2, Y_ARTE - 6))
        img.alpha_composite(banner, ((W - ARTE_W) // 2, Y_ARTE))
        if jugador:
            cap = jugador.upper()
            f = _font(ANTON, 64)
            w = round(f.getlength(cap)) + 70
            d = ImageDraw.Draw(img)
            d.rounded_rectangle((W / 2 - w / 2, Y_ARTE + ARTE_H - 52, W / 2 + w / 2, Y_ARTE + ARTE_H + 40),
                                radius=22, fill=LIME)
            d.text((W / 2, Y_ARTE + ARTE_H - 6), cap, font=f, fill=(20, 20, 20), anchor="mm")

    png = os.path.join(carpeta, "portada.png")
    jpg = os.path.join(carpeta, "portada.jpg")
    img.convert("RGB").save(png)
    img.convert("RGB").save(jpg, quality=92)
    return {"png": png, "jpg": jpg, "fondo": fondo, "logos": logos}
