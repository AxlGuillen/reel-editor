"""The card design that goes beyond luxury/plain (cards.py draws, animates and overlays; here is how a face LOOKS).

    splash    the champion's loading-screen art full bleed, a broadcast look: the player's photo in a medallion,
              the name big at the bottom, HUD corner brackets in the team's color

Every face is RGBA, CARD_W x CARD_H, drawn at SS times that size and brought down. Without a photo the
medallion shows a silhouette with the initials; without the champion's art a gradient: a card never
fails for a missing picture.
"""
from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from modules.competitivo import cards as C

SS = C.SS
W, H = C.CARD_W * SS, C.CARD_H * SS


# --- Shared pieces -------------------------------------------------------------------------
def _open(path: str | None) -> Image.Image | None:
    if path and os.path.isfile(path):
        try:
            return Image.open(path).convert("RGB")
        except Exception:  # noqa: BLE001
            return None
    return None


def _art(card: C.Card, w: int, h: int, focus: float = 0.12) -> Image.Image:
    """The champion's art cropped to w x h (the top of the picture is the face), or a gradient with its initial."""
    c = C.COLORS[card.side]
    img = _open(card.art)
    if img is None:
        base = C._gradient(w, h, c["top"], c["bottom"])
        d = ImageDraw.Draw(base)
        f = C._font(C.FONT_TITLE, int(h * 0.55))
        ch = (card.champion or card.player or "?")[:1].upper()
        d.text(((w - d.textlength(ch, font=f)) / 2, h * 0.12), ch, font=f, fill=tuple(min(255, x + 40) for x in c["top"]))
        return base
    scale = max(w / img.width, h / img.height)
    img = img.resize((max(w, int(img.width * scale)), max(h, int(img.height * scale))), Image.LANCZOS)
    top = int((img.height - h) * focus)
    left = (img.width - w) // 2
    return img.crop((left, top, left + w, top + h))


def _portrait(card: C.Card, w: int, h: int) -> Image.Image:
    """The player's photo cropped to w x h, or a silhouette with the initials."""
    img = _open(card.photo)
    if img is None:
        return C._silhouette(w, h, C.COLORS[card.side]["top"], (card.player[:2] or "?").upper())
    return C._cover(img, w, h)


def _medallion(card: C.Card, d: int, ring: int, accent) -> Image.Image:
    """The photo in a circle with a ring."""
    out = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    ImageDraw.Draw(out).ellipse([0, 0, d - 1, d - 1], fill=(0, 0, 0, 255))
    ImageDraw.Draw(out).ellipse([ring // 2, ring // 2, d - 1 - ring // 2, d - 1 - ring // 2], fill=accent + (255,))
    inner = d - 2 * ring
    face = _portrait(card, inner, inner).convert("RGBA")
    mask = Image.new("L", (inner, inner), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, inner - 1, inner - 1], fill=255)
    out.paste(face, (ring, ring), mask)
    return out


def _brackets(img: Image.Image, inset: int, length: int, width: int, color) -> None:
    """HUD corner brackets."""
    d = ImageDraw.Draw(img)
    w, h = img.size
    for (x, y, sx, sy) in ((inset, inset, 1, 1), (w - inset, inset, -1, 1), (inset, h - inset, 1, -1), (w - inset, h - inset, -1, -1)):
        d.line([(x, y), (x + sx * length, y)], fill=color, width=width)
        d.line([(x, y), (x, y + sy * length)], fill=color, width=width)


def _shade(h: int, start: float, end: float, top_alpha: int, bottom_alpha: int, width: int = W) -> Image.Image:
    """A black gradient as an alpha layer: transparent above `start`, bottom_alpha below `end`."""
    ys = np.clip((np.arange(h) / h - start) / max(1e-6, end - start), 0, 1)
    a = (top_alpha + (bottom_alpha - top_alpha) * ys).astype(np.uint8)
    return Image.fromarray(np.repeat(a[:, None], width, axis=1), "L")


def _stat_rows(card: C.Card) -> list[C.Row]:
    return card.rows[:3]


def _finish(img: Image.Image, mask: Image.Image) -> Image.Image:
    img.putalpha(mask)
    return img.resize((C.CARD_W, C.CARD_H), Image.LANCZOS)


# --- SPLASH -------------------------------------------------------------------------
def _splash_front(card: C.Card) -> Image.Image:
    c = C.COLORS[card.side]
    img = _art(card, W, H).convert("RGBA")
    tint = C._gradient(W, H, c["top"], c["bottom"]).convert("RGBA")
    img = Image.blend(img, tint, 0.30)
    black = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    img = Image.composite(black, img, _shade(H, 0.42, 0.93, 0, 235))
    img = Image.composite(black, img, _shade(H, 0.0, 0.22, 110, 0))
    d = ImageDraw.Draw(img)
    pad = 22 * SS
    # the team chip and the role
    f_tag = C._font(C.FONT_TEXT, 17 * SS)
    tag = (card.team or "").upper()
    tw = d.textlength(tag, font=f_tag)
    d.rounded_rectangle([pad, pad, pad + tw + 24 * SS, pad + 34 * SS], 8 * SS, fill=c["accent"] + (255,))
    d.text((pad + 12 * SS, pad + 5 * SS), tag, font=f_tag, fill=(10, 12, 20, 255))
    role = card.role.upper()
    if role:
        f_role = C._font(C.FONT_TEXT, 16 * SS)
        d.rounded_rectangle([W - pad - d.textlength(role, font=f_role) - 24 * SS, pad, W - pad, pad + 34 * SS], 8 * SS,
                            fill=(0, 0, 0, 150), outline=c["accent"] + (255,), width=2 * SS)
        d.text((W - pad - d.textlength(role, font=f_role) - 12 * SS, pad + 6 * SS), role, font=f_role, fill=(255, 255, 255, 255))
    # the medallion with the photo, and the name
    md = 104 * SS
    med = _medallion(card, md, 7 * SS, c["accent"])
    my = int(H * 0.70)
    img.alpha_composite(med, (pad, my))
    nx = pad + md + 16 * SS
    name = card.player.upper()
    f_name = C._fit(d, name, C.FONT_TITLE, 62 * SS, W - nx - pad, 28 * SS)
    d.text((nx, my - 6 * SS), name, font=f_name, fill=(255, 255, 255, 255))
    ny = my - 6 * SS + C._ink_bottom(f_name, name) + 6 * SS
    d.rectangle([nx, ny, nx + 70 * SS, ny + 5 * SS], fill=c["accent"] + (255,))
    if card.champion:
        f_ch = C._font(C.FONT_TEXT, 17 * SS)
        d.text((nx, ny + 14 * SS), card.champion.upper(), font=f_ch, fill=(215, 222, 235, 240))
    _brackets(img, 10 * SS, 30 * SS, 4 * SS, c["accent"] + (255,))
    return _finish(img, C._rounded_mask(W, H, C.RADIUS * SS))


def _splash_back(card: C.Card) -> Image.Image:
    c = C.COLORS[card.side]
    art = _art(card, W, H, focus=0.3).filter(ImageFilter.GaussianBlur(10 * SS))
    img = Image.blend(art, Image.new("RGB", (W, H), (4, 6, 12)), 0.72).convert("RGBA")
    d = ImageDraw.Draw(img)
    pad = 26 * SS
    title = (card.title or card.player).upper()
    d.text((pad, 34 * SS), title, font=C._fit(d, title, C.FONT_TEXT, 22 * SS, W - 2 * pad, 13 * SS), fill=(255, 255, 255, 255))
    d.rectangle([pad, 72 * SS, pad + 60 * SS, 76 * SS], fill=c["accent"] + (255,))
    rows = _stat_rows(card)
    top, bottom = 100 * SS, H - 78 * SS
    blocks = []
    for k, row in enumerate(rows):
        f_big = C._fit(d, row.big, C.FONT_TITLE, (52 if k == 0 else 42) * SS, W - 2 * pad - 24 * SS, 26 * SS)
        f_lab = C._fit(d, row.label.upper(), C.FONT_TEXT, 15 * SS, W - 2 * pad - 24 * SS, 10 * SS)
        blocks.append((row, f_big, f_lab, C._ink_bottom(f_big, row.big)))
    heights = [b[3] + 8 * SS + b[2].size for b in blocks]
    gap = max(10 * SS, ((bottom - top) - sum(heights)) / (len(blocks) + 1)) if blocks else 0
    y = top + gap
    for (row, f_big, f_lab, big_h), height in zip(blocks, heights):
        d.rectangle([pad, int(y) + 4 * SS, pad + 6 * SS, int(y + height) - 2 * SS], fill=c["accent"] + (255,))
        d.text((pad + 24 * SS, int(y)), row.big, font=f_big, fill=(255, 255, 255, 255))
        d.text((pad + 24 * SS, int(y) + big_h + 8 * SS), row.label.upper(), font=f_lab, fill=c["accent"] + (255,))
        y += height + gap
    if card.caption:
        d.text((pad, H - 50 * SS), card.caption.upper(),
               font=C._fit(d, card.caption.upper(), C.FONT_TEXT, 13 * SS, W - 2 * pad, 9 * SS), fill=(160, 170, 190, 230))
    _brackets(img, 10 * SS, 30 * SS, 4 * SS, c["accent"] + (255,))
    return _finish(img, C._rounded_mask(W, H, C.RADIUS * SS))


# --- Dispatch ------------------------------------------------------------------------------------
FRONTS = {"splash": _splash_front}
BACKS = {"splash": _splash_back}


def front(card: C.Card, style: str) -> Image.Image:
    return FRONTS[style](card)


def back(card: C.Card, style: str) -> Image.Image:
    return BACKS[style](card)
