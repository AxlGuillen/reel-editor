"""Player cards over the champions: a card that comes in, turns over and goes.

At the start of a play the clip freezes for a moment (layout.Freeze) and a card
appears over each chosen player's champion: front (photo, name, role, champion),
then it turns and the back shows three numbers (the champion in the tournament,
the player, the pair, or this game so far). The art is drawn here with Pillow as
a sequence of PNGs with alpha (one per frame, 60 fps) that FFmpeg overlays during
the freeze: the cards are not tracked, they are placed once (bars.py) and stay.

Nothing here can tear a render down: no photo → a silhouette with the initials;
no champion icon → a circle with its initial; no data for the back → the game's
own numbers (the API always has them).
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import config
from modules.competitivo import intro

FPS = 60
CARD_W, CARD_H = 300, 420           # the face, in px of the 1080x1920 output
PAD = 40                            # room around it for the shadow
RADIUS = 24
SS = 2                              # faces are drawn at this scale and brought down

FONT_TITLE = os.path.join(config.FONTS_FOLDER, "Anton-Regular.ttf")
FONT_TEXT = os.path.join(config.FONTS_FOLDER, "Montserrat-ExtraBold.ttf")
PHOTOS = os.path.join(config.ASSETS_FOLDER, "players")          # <name>.png|jpg, Axl's own
CACHE = os.path.join(config.BASE_DIR, "cache", "competitivo", "cards")

COLORS = {
    "blue": {"top": (22, 58, 122), "bottom": (7, 16, 40), "accent": (96, 200, 255)},
    "red": {"top": (126, 24, 36), "bottom": (38, 7, 12), "accent": (255, 128, 92)},
}
GOLD = ((255, 236, 170), (203, 152, 58))          # light → dark of the border
ROLES = {0: "TOP", 1: "JUNGLA", 2: "MID", 3: "ADC", 4: "SOPORTE"}
# timeline of one card, seconds from its start (the back shows from FLIP_END to the exit)
T_IN, T_HOLD, T_FLIP, T_OUT = 0.22, 0.20, 0.32, 0.20


@dataclass
class Row:
    big: str                        # "4-10"
    label: str                      # "V-D CON KSANTE"
    bar: float | None = None        # 0-1: a micro bar under it


@dataclass
class Card:
    player: str
    team: str
    side: str                       # "blue" | "red"
    role: str = ""
    champion: str = ""              # Data Dragon's key ("XinZhao")
    photo: str | None = None
    icon: str | None = None
    art: str | None = None          # the champion's loading-screen art (the "splash" design's picture)
    photo_from: str = "none"        # where the photo came from: "url" | "assets" | "none"
    title: str = ""                 # head of the back: "CANYON · JAYCE"
    rows: list[Row] = field(default_factory=list)
    source: str = ""                # "pareja" | "campeon" | "jugador" | "partida": where the back came from
    caption: str = ""               # foot of the back


# --- Files ------------------------------------------------------------------------
def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower())
    return re.sub(r"[^a-z0-9]+", "", "".join(c for c in text if unicodedata.category(c) != "Mn"))


def photo_path(player: str) -> str | None:
    """The player's photo in assets/players/ (any case, any accents) or in the cache."""
    key = _plain(player)
    for folder in (PHOTOS, os.path.join(CACHE, "photos")):
        if os.path.isdir(folder):
            for f in os.listdir(folder):
                if os.path.splitext(f)[1].lower() in (".png", ".jpg", ".jpeg", ".webp") and _plain(os.path.splitext(f)[0]) == key:
                    return os.path.join(folder, f)
    return None


MAX_IMAGE_BYTES = 12 * 1024 * 1024


def fetch_image(url: str) -> tuple[str | None, str]:
    """A picture from a link (the model fills the card's photo with it): downloaded once (cache by link),
    checked to be a real image and kept as it came. Returns (path, "") or (None, why it failed). Never raises:
    a card without its photo is still a card, and the reason goes to the status for whoever sent the link."""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return None, "el link de la imagen tiene que empezar con http:// o https://"
    folder = os.path.join(CACHE, "images")
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    ext = ext if ext in (".png", ".jpg", ".jpeg", ".webp") else ".img"
    path = os.path.join(folder, hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ext)
    try:
        if not os.path.isfile(path):
            os.makedirs(folder, exist_ok=True)
            intro.descargar(url, path, timeout=10, reintentos=1, max_bytes=MAX_IMAGE_BYTES)
        with Image.open(path) as im:
            im.load()
            if im.width < 40 or im.height < 40:
                raise ValueError(f"es muy chica ({im.width}x{im.height})")
        return path, ""
    except Exception as exc:  # noqa: BLE001
        if os.path.isfile(path):
            try:
                os.remove(path)                    # nothing poisoned stays in the cache
            except OSError:
                pass
        return None, f"no se pudo usar esa imagen ({str(exc)[:120]})"


def champion_art(champion: str) -> str | None:
    """The champion's loading-screen art (308x560, Data Dragon: no version needed), cached; None without it."""
    if not champion:
        return None
    path = os.path.join(CACHE, "art", f"{_plain(champion)}.jpg")
    if os.path.isfile(path):
        return path
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        intro.descargar(f"https://ddragon.leagueoflegends.com/cdn/img/champion/loading/{champion}_0.jpg", path,
                        timeout=8, reintentos=1, max_bytes=2_000_000)
        Image.open(path).verify()
        return path
    except Exception:  # noqa: BLE001
        if os.path.isfile(path):
            os.remove(path)
        return None


def _ddragon_version() -> str | None:
    path = os.path.join(CACHE, "ddragon_version.txt")
    try:
        if os.path.isfile(path) and time.time() - os.path.getmtime(path) < 7 * 86400:
            return open(path, encoding="utf-8").read().strip()
        import json
        import urllib.request
        with urllib.request.urlopen("https://ddragon.leagueoflegends.com/api/versions.json", timeout=8) as r:
            version = json.loads(r.read().decode())[0]
        os.makedirs(CACHE, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(version)
        return version
    except Exception:  # noqa: BLE001 - an icon is not worth a failed render
        return None


def champion_icon(champion: str) -> str | None:
    """The champion's square icon (Data Dragon, cached), or None."""
    if not champion:
        return None
    path = os.path.join(CACHE, "icons", f"{_plain(champion)}.png")
    if os.path.isfile(path):
        return path
    version = _ddragon_version()
    if not version:
        return None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        intro.descargar(f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{champion}.png", path,
                        timeout=8, reintentos=1, max_bytes=200_000)
        Image.open(path).verify()
        return path
    except Exception:  # noqa: BLE001
        if os.path.isfile(path):
            os.remove(path)
        return None


# --- Drawing ------------------------------------------------------------------------
def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _ink_bottom(font: ImageFont.FreeTypeFont, text: str) -> int:
    """How far down from the y it is drawn at the text's ink reaches (Anton's em is much taller than its letters)."""
    return font.getbbox(text)[3]


def _fit(draw: ImageDraw.ImageDraw, text: str, path: str, size: int, max_w: int, min_size: int = 18):
    while size > min_size and draw.textlength(text, font=_font(path, size)) > max_w:
        size -= 2
    return _font(path, size)


def _gradient(w: int, h: int, top, bottom) -> Image.Image:
    t = np.linspace(0, 1, h)[:, None, None]
    arr = np.array(top)[None, None, :] * (1 - t) + np.array(bottom)[None, None, :] * t
    return Image.fromarray(np.repeat(arr, w, axis=1).astype(np.uint8), "RGB")


def _rounded_mask(w: int, h: int, r: int) -> Image.Image:
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255)
    return m


def _border(img: Image.Image, r: int, width: int) -> None:
    """A metallic border: gold from light to dark along the diagonal, a thin dark line inside."""
    w, h = img.size
    diag = (np.linspace(0, 1, w)[None, :] * 0.5 + np.linspace(0, 1, h)[:, None] * 0.5)[..., None]
    # light → dark → light again: it looks like brushed metal, not a flat color
    t = np.abs(np.sin(diag * np.pi * 1.4))
    metal = Image.fromarray((np.array(GOLD[0]) * (1 - t) + np.array(GOLD[1]) * t).astype(np.uint8), "RGB")
    ring = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(ring)
    d.rounded_rectangle([0, 0, w - 1, h - 1], r, fill=255)
    d.rounded_rectangle([width, width, w - 1 - width, h - 1 - width], max(2, r - width), fill=0)
    img.paste(metal, (0, 0), ring)
    ImageDraw.Draw(img).rounded_rectangle([width, width, w - 1 - width, h - 1 - width], max(2, r - width),
                                          outline=(0, 0, 0, 120), width=max(1, width // 4))


def _silhouette(w: int, h: int, color, initials: str) -> Image.Image:
    img = Image.new("RGB", (w, h), tuple(int(c * 0.55) for c in color))
    d = ImageDraw.Draw(img)
    light = tuple(min(255, int(c * 0.95) + 20) for c in color)
    d.ellipse([w * 0.31, h * 0.17, w * 0.69, h * 0.17 + w * 0.38], fill=light)
    d.ellipse([w * 0.12, h * 0.52, w * 0.88, h * 1.25], fill=light)
    f = _font(FONT_TITLE, int(h * 0.20))
    tw = d.textlength(initials, font=f)
    d.text(((w - tw) / 2, h * 0.30), initials, font=f, fill=(255, 255, 255))
    return img


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Cropped to fill w x h (the face of a portrait stays in the upper part)."""
    img = img.convert("RGB")
    scale = max(w / img.width, h / img.height)
    img = img.resize((max(w, int(img.width * scale)), max(h, int(img.height * scale))), Image.LANCZOS)
    left = (img.width - w) // 2
    top = int((img.height - h) * 0.2)
    return img.crop((left, top, left + w, top + h))


def _icon(card: Card, size: int, accent) -> Image.Image:
    """The champion's icon in a ring (a circle with its initial without the icon)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 0, size - 1, size - 1], fill=accent)
    inner = size - 8
    face = Image.new("RGBA", (inner, inner), (20, 24, 32, 255))
    if card.icon and os.path.isfile(card.icon):
        try:
            face = Image.open(card.icon).convert("RGBA").resize((inner, inner), Image.LANCZOS)
        except Exception:  # noqa: BLE001
            pass
    else:
        fd = ImageDraw.Draw(face)
        f = _font(FONT_TITLE, int(inner * 0.62))
        ch = (card.champion or "?")[:1].upper()
        fd.text(((inner - fd.textlength(ch, font=f)) / 2, inner * 0.14), ch, font=f, fill=(255, 255, 255))
    mask = Image.new("L", (inner, inner), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, inner - 1, inner - 1], fill=255)
    img.paste(face, (4, 4), mask)
    return img


def _grain(w: int, h: int, amount: int = 9) -> Image.Image:
    rng = np.random.default_rng(7)
    g = rng.integers(-amount, amount + 1, (h, w), dtype=np.int16)
    return Image.fromarray(np.clip(128 + g, 0, 255).astype(np.uint8), "L")


DESIGNS = ("luxury", "plain", "splash")


def front(card: Card, style: str = "luxury") -> Image.Image:
    """The face with the photo: RGBA, CARD_W x CARD_H."""
    if style == "splash":
        from modules.competitivo import card_designs
        return card_designs.front(card, style)
    W, H = CARD_W * SS, CARD_H * SS
    c = COLORS[card.side]
    base = _gradient(W, H, c["top"], c["bottom"])
    photo_h = int(H * 0.68)
    initials = (card.player[:2] or "?").upper()
    ph = _cover(Image.open(card.photo), W, photo_h) if card.photo and os.path.isfile(card.photo) else \
        _silhouette(W, photo_h, c["top"], initials)
    # the photo fades into the base at the bottom
    fade = Image.fromarray((np.clip((np.linspace(1, 0, photo_h) - 0.0) * 3.2, 0, 1) * 255).astype(np.uint8)[:, None]
                           .repeat(W, axis=1), "L")
    base.paste(ph, (0, 0), fade)
    img = base.convert("RGBA")
    d = ImageDraw.Draw(img)
    pad = 20 * SS
    # team and role
    f_small = _font(FONT_TEXT, 17 * SS)
    d.text((pad, int(H * 0.668)), (card.team or "").upper(), font=f_small, fill=c["accent"] + (255,))
    role = card.role.upper()
    if role:
        d.text((W - pad - d.textlength(role, font=f_small), int(H * 0.668)), role, font=f_small, fill=(235, 238, 245, 235))
    # name
    name = card.player.upper()
    f_name = _fit(d, name, FONT_TITLE, 66 * SS, W - 2 * pad - 76 * SS, 30 * SS)
    name_y = int(H * 0.70)
    d.text((pad, name_y), name, font=f_name, fill=(255, 255, 255, 255))
    # champion and its icon
    if card.champion:
        f_ch = _font(FONT_TEXT, 15 * SS)
        d.text((pad, name_y + _ink_bottom(f_name, name) + 8 * SS), card.champion.upper(), font=f_ch,
               fill=(200, 208, 222, 230))
    ic = _icon(card, 66 * SS, c["accent"] + (255,))
    img.alpha_composite(ic, (W - pad - ic.width, int(H * 0.715)))
    if style == "luxury":
        grain = _grain(W, H)
        img = Image.composite(img, ImageChops.overlay(img.convert("RGB"), Image.merge("RGB", (grain, grain, grain))).convert("RGBA"),
                              Image.new("L", (W, H), 245))
        _border(img, RADIUS * SS, 6 * SS)
    else:
        ImageDraw.Draw(img).rounded_rectangle([0, 0, W - 1, H - 1], RADIUS * SS, outline=c["accent"] + (255,), width=4 * SS)
    img.putalpha(_rounded_mask(W, H, RADIUS * SS))
    return img.resize((CARD_W, CARD_H), Image.LANCZOS)


def back(card: Card, style: str = "luxury") -> Image.Image:
    """The back: the three numbers of the card."""
    if style == "splash":
        from modules.competitivo import card_designs
        return card_designs.back(card, style)
    W, H = CARD_W * SS, CARD_H * SS
    c = COLORS[card.side]
    img = _gradient(W, H, tuple(int(x * 0.55) for x in c["top"]), (6, 8, 14)).convert("RGBA")
    d = ImageDraw.Draw(img)
    pad = 22 * SS
    # head: name and champion's icon
    title = (card.title or card.player).upper()
    f_title = _fit(d, title, FONT_TEXT, 21 * SS, W - 2 * pad - 52 * SS, 13 * SS)
    d.text((pad, 24 * SS), title, font=f_title, fill=c["accent"] + (255,))
    ic = _icon(card, 40 * SS, c["accent"] + (255,))
    img.alpha_composite(ic, (W - pad - ic.width, 14 * SS))
    d.line([(pad, 66 * SS), (W - pad, 66 * SS)], fill=c["accent"] + (160,), width=2 * SS)
    # rows: measured first, then spread over the free height with the same gap
    rows = card.rows[:3]
    top, bottom = 76 * SS, H - 72 * SS
    blocks = []
    for k, row in enumerate(rows):
        f_big = _fit(d, row.big, FONT_TITLE, (54 if k == 0 else 42) * SS, W - 2 * pad, 26 * SS)
        f_lab = _fit(d, row.label.upper(), FONT_TEXT, 15 * SS, W - 2 * pad, 10 * SS)
        big_h = _ink_bottom(f_big, row.big)
        blocks.append((row, f_big, f_lab, big_h, big_h + 8 * SS + f_lab.size + (16 * SS if row.bar is not None else 0)))
    gap = max(8 * SS, ((bottom - top) - sum(b[4] for b in blocks)) / (len(blocks) + 1)) if blocks else 0
    y = top + gap
    for row, f_big, f_lab, big_h, height in blocks:
        d.text((pad, int(y)), row.big, font=f_big, fill=(255, 255, 255, 255))
        low = int(y) + big_h + 8 * SS
        d.text((pad, low), row.label.upper(), font=f_lab, fill=(190, 200, 218, 235))
        if row.bar is not None:
            by = low + f_lab.size + 10 * SS
            d.rounded_rectangle([pad, by, W - pad, by + 6 * SS], 3 * SS, fill=(255, 255, 255, 45))
            d.rounded_rectangle([pad, by, pad + int((W - 2 * pad) * max(0.03, min(1.0, row.bar))), by + 6 * SS], 3 * SS,
                                fill=c["accent"] + (255,))
        y += height + gap
    if card.caption:
        f_cap = _fit(d, card.caption.upper(), FONT_TEXT, 13 * SS, W - 2 * pad, 9 * SS)
        d.text((pad, H - 40 * SS), card.caption.upper(), font=f_cap, fill=(150, 160, 180, 220))
    if style == "luxury":
        _border(img, RADIUS * SS, 6 * SS)
    else:
        ImageDraw.Draw(img).rounded_rectangle([0, 0, W - 1, H - 1], RADIUS * SS, outline=c["accent"] + (255,), width=4 * SS)
    img.putalpha(_rounded_mask(W, H, RADIUS * SS))
    return img.resize((CARD_W, CARD_H), Image.LANCZOS)


# --- Animation ------------------------------------------------------------------------
def _ease_out_back(x: float) -> float:
    c1, c3 = 1.30, 2.30
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def _ease_in_out(x: float) -> float:
    return x * x * (3 - 2 * x)


def pose(t: float, dur: float, delay: float = 0.0) -> dict:
    """Where the card is `t` seconds into the freeze: scale, alpha, angle of the turn (0 front, π back)."""
    t -= delay
    exit_start = dur - delay - T_OUT                    # the back stays from the end of the turn to here
    if t < 0:
        return {"alpha": 0.0, "scale": 0.6, "angle": 0.0, "lift": 14.0}
    if t < T_IN:
        k = t / T_IN
        return {"alpha": min(1.0, k * 1.6), "scale": 0.6 + 0.4 * _ease_out_back(k), "angle": 0.0, "lift": 14 * (1 - k)}
    t2 = t - T_IN
    if t2 < T_HOLD:
        return {"alpha": 1.0, "scale": 1.0, "angle": 0.0, "lift": 0.0}
    t3 = t2 - T_HOLD
    if t3 < T_FLIP:
        return {"alpha": 1.0, "scale": 1.0 + 0.05 * math.sin(math.pi * t3 / T_FLIP),
                "angle": math.pi * _ease_in_out(t3 / T_FLIP), "lift": 0.0}
    if t < exit_start:
        return {"alpha": 1.0, "scale": 1.0, "angle": math.pi, "lift": 0.0}
    k = min(1.0, (t - exit_start) / T_OUT)
    return {"alpha": 1.0 - k, "scale": 1.0 - 0.06 * k, "angle": math.pi, "lift": -10.0 * k}


def _sweep(w: int, h: int, x: float) -> Image.Image:
    """A diagonal band of light at x (0-1 across), as an alpha mask."""
    xs = np.arange(w)[None, :] + np.arange(h)[:, None] * 0.55
    centre = (x * (w + h * 0.55)) - h * 0.1
    band = np.exp(-(((xs - centre) / (w * 0.13)) ** 2))
    return Image.fromarray((band * 70).astype(np.uint8), "L")


def frame(front_img: Image.Image, back_img: Image.Image, st: dict, t: float, dur: float, luxury: bool) -> Image.Image:
    """One frame of the card on its transparent canvas (CARD_W + 2 PAD)."""
    cw, ch = CARD_W + 2 * PAD, CARD_H + 2 * PAD
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    if st["alpha"] <= 0.001:
        return canvas
    cos = math.cos(st["angle"])
    face = front_img if cos >= 0 else back_img
    sx = max(0.03, abs(cos))
    sy = 1 - 0.05 * (1 - sx)                         # a little taller in the middle: it turns toward the camera
    w, h = max(2, int(CARD_W * sx * st["scale"])), max(2, int(CARD_H * sy * st["scale"]))
    img = face.resize((w, h), Image.LANCZOS)
    shade = 0.55 + 0.45 * sx                          # darker at the edge while it turns
    rgb = np.asarray(img).astype(np.float32)
    rgb[..., :3] *= shade
    if luxury:                                        # a band of light going across, inside the card only
        alpha = rgb[..., 3:4] / 255.0
        rgb[..., :3] += np.asarray(_sweep(w, h, (t * 0.9) % 1.6 - 0.3), np.float32)[..., None] * alpha
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGBA")
    # the shadow under it
    sh = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([cw / 2 - w / 2 + 6, ch / 2 - h / 2 + 14, cw / 2 + w / 2 - 6, ch / 2 + h / 2 + 14],
                                         RADIUS, fill=(0, 0, 0, 150))
    sh = sh.filter(ImageFilter.GaussianBlur(14))
    canvas.alpha_composite(sh)
    canvas.alpha_composite(img, (int(cw / 2 - w / 2), int(ch / 2 - h / 2 - st["lift"])))
    if st["alpha"] < 1.0:
        a = canvas.getchannel("A").point(lambda v: int(v * st["alpha"]))
        canvas.putalpha(a)
    return canvas


def place(point: tuple[float, float], scale: float, top_limit: float, bottom_limit: float, width: int = 1080,
          gap: float = 8.0) -> tuple[int, int, str]:
    """Where the canvas of a card goes so its face sits right over `point` (the top of a champion's
    name label, in output px): its bottom edge `gap` px above, centered, and inside the gameplay
    box. If there is no room above, it goes below the point (the champion is under it: a card
    there would hide it, so a card below only happens at the very top of the box).
    Returns (x, y, "above" | "below") of the canvas' top-left."""
    cw, ch = (CARD_W + 2 * PAD) * scale, (CARD_H + 2 * PAD) * scale
    px, py = point
    x = px - cw / 2
    y = py - gap - (CARD_H * scale + PAD * scale)            # the face's bottom at py - gap
    where = "above"
    if y + PAD * scale < top_limit:
        y = py + 70 * scale                                   # under the champion
        where = "below"
    face_h = CARD_H * scale
    y = min(y, bottom_limit - PAD * scale - face_h)
    x = min(max(x, -PAD * scale * 0.5), width - cw + PAD * scale * 0.5)
    return int(round(x)), int(round(y)), where


def render(card: Card, out_dir: str, dur: float, delay: float = 0.0, style: str = "luxury", fps: int = FPS) -> int:
    """The card's frames (PNG with alpha, f0000.png …) for a freeze of `dur` s; how many."""
    if dur - delay < T_IN + T_HOLD + T_FLIP + T_OUT + 0.4:
        raise ValueError(f"El congelado de {dur:.1f} s no alcanza para que la tarjeta entre, gire y se lea el dorso "
                         f"(hacen falta {T_IN + T_HOLD + T_FLIP + T_OUT + 0.4 + delay:.1f} s).")
    os.makedirs(out_dir, exist_ok=True)
    fr, bk = front(card, style), back(card, style)
    n = int(round(dur * fps))
    for k in range(n):
        t = k / fps
        frame(fr, bk, pose(t, dur, delay), t, dur, style == "luxury").save(os.path.join(out_dir, f"f{k:04d}.png"),
                                                                          compress_level=1)
    return n


def sheet(items: list[Card], style: str = "luxury") -> Image.Image:
    """The faces of several cards (front and back of each, in a row) on a dark background: what the
    /cards/preview route returns, to check photos and data before rendering."""
    gap = 20
    img = Image.new("RGBA", (gap + (CARD_W * 2 + gap) * len(items), CARD_H + 2 * gap), (24, 26, 32, 255))
    x = gap
    for card in items:
        img.alpha_composite(front(card, style), (x, gap))
        img.alpha_composite(back(card, style), (x + CARD_W + 6, gap))
        x += CARD_W * 2 + gap + 6
    return img.convert("RGB")


def preview(card: Card, style: str = "luxury") -> Image.Image:
    """Front and back side by side (to look at the design)."""
    fr, bk = front(card, style), back(card, style)
    img = Image.new("RGBA", (CARD_W * 2 + 3 * 20, CARD_H + 40), (24, 26, 32, 255))
    img.alpha_composite(fr, (20, 20))
    img.alpha_composite(bk, (CARD_W + 40, 20))
    return img


def cache_key(card: Card, style: str, dur: float, delay: float) -> str:
    raw = repr((card.player, card.team, card.side, card.role, card.champion, card.photo, card.icon, card.title,
                [(r.big, r.label, r.bar) for r in card.rows], card.caption, style, dur, delay))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
