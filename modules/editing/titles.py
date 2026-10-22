"""Titles of an edit in ASS (burned by libass, same as the subtitles).

Styles:
  word       the song lyrics, one giant word in the center with a vertical
             gradient (the signature of the reference edit). libass has no
             gradients: the word is drawn N times, each copy clipped (\\clip)
             to a horizontal band of its color, and under them an unfilled
             copy with the outline and the shadow.
  kill       the player NAME big and the champion small below, punches in.
  multikill  "QUADRA KILL" stamped, in a gold gradient.
  title      one line at the top (teams, map).
  sub        one line at the bottom, like a subtitle.

The canvas is the video's (vertical or horizontal): sizes are a fraction of
the short side, so a 16:9 and a 9:16 edit both look proportioned. Fonts come
from assets/fonts (fonts.py: family and metrics).
"""
from __future__ import annotations

import os
import uuid

import config
from modules.editing.edl import Text
from modules.subtitles import fonts
from modules.subtitles.processor import _seconds_to_ass

BANDS = 12
WORD_GRADIENT = ("#9CC2FF", "#FFFFFF")          # light blue on top → white at the bottom
MULTIKILL_GRADIENT = ("#FFE27A", "#FF8A1E")
FONTS = {
    "word": "Poppins-Black.ttf",
    "kill": "Anton-Regular.ttf",
    "kill_sub": "Poppins-Bold.ttf",
    "multikill": "Anton-Regular.ttf",
    "title": "Poppins-Black.ttf",
    "sub": "Poppins-Bold.ttf",
}
# em of each style as a fraction of the canvas short side
SIZES = {"word": 0.14, "kill": 0.12, "kill_sub": 0.045, "multikill": 0.15, "title": 0.06, "sub": 0.05}
USABLE_WIDTH = 0.9


def _ass_color(css: str) -> str:
    css = css.lstrip("#").upper()
    return f"&H00{css[4:6]}{css[2:4]}{css[0:2]}&"


def _mix(a: str, b: str, f: float) -> str:
    a, b = a.lstrip("#"), b.lstrip("#")
    channels = [round(int(a[i:i + 2], 16) * (1 - f) + int(b[i:i + 2], 16) * f) for i in (0, 2, 4)]
    return "#" + "".join(f"{c:02X}" for c in channels)


def _esc(s: str) -> str:
    return s.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


class _Canvas:
    def __init__(self, width: int, height: int):
        self.width, self.height = width, height
        self.u = min(width, height)

    def em(self, style: str) -> float:
        return SIZES[style] * self.u


def _font(style: str) -> dict:
    return fonts.font_info(FONTS[style])


def _fit(info: dict, text: str, em: float, width: int) -> float:
    """Scale (≤ 1) so the text fits in the canvas USABLE_WIDTH."""
    w = fonts.text_width(info, text, em)
    limit = width * USABLE_WIDTH
    return 1.0 if w <= limit or w <= 0 else limit / w


def _header(width: int, height: int) -> str:
    # YCbCr Matrix: None → libass uses the video's own matrix (see subtitles).
    return f"""\
[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: None

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,Arial,60,&H00FFFFFF&,&H000000FF&,&H00000000&,&H96000000&,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _event(layer: int, t: Text, tags: str, text: str) -> str:
    return (f"Dialogue: {layer},{_seconds_to_ass(t.start)},{_seconds_to_ass(t.end)},Base,,0,0,0,,"
            f"{{{tags}}}{_esc(text)}")


def _font_tags(info: dict, em: float) -> str:
    return f"\\fn{info['family']}\\fs{fonts.ass_font_size(info, round(em))}\\b{1 if info['bold'] else 0}"


def _cy(info: dict, em: float, center: float) -> float:
    """y of the \\an5 anchor (center of the line BOX) so the CAPITALS are
    centered on `center`. libass centers the winAscent+winDescent box, not
    the ink: with Anton the capitals landed ~25 px too low."""
    box = info["size_ratio"] * em
    cap = info["cap"] / info["upm"] * em
    return center - box / 2 + info["descent"] * em + cap / 2


def _cap_height(info: dict, em: float) -> float:
    return info["cap"] / info["upm"] * em


def _ink_span(info: dict, em: float, cy: float) -> tuple[float, float]:
    """(top, bottom) of the capitals' ink with \\an5 at cy."""
    box = info["size_ratio"] * em
    base = cy + box / 2 - info["descent"] * em
    cap = info["cap"] / info["upm"] * em
    return base - cap * 1.08, base + em * 0.04


def _gradient(t: Text, info: dict, em: float, x: float, y: float, scale: float,
              colors: tuple[str, str], entrance: str, width: int, outline: float,
              shadow: float) -> list[str]:
    """The word in N color bands (layer 1) over its outline (layer 0)."""
    font = _font_tags(info, em)
    sc = f"\\fscx{scale * 100:.1f}\\fscy{scale * 100:.1f}" if scale < 0.999 else ""
    pos = f"\\an5\\pos({x:.1f},{y:.1f}){font}{sc}"
    out = [_event(0, t, f"{pos}{entrance}\\1a&HFF&\\bord{outline:.1f}\\3c&H00140A0A&"
                        f"\\shad{shadow:.1f}\\4a&H60&\\4c&H00000000&", t.text)]
    top, bottom = _ink_span(info, em * scale, y)
    band = (bottom - top) / BANDS
    for k in range(BANDS):
        y0 = 0 if k == 0 else top + k * band
        y1 = 10 ** 5 if k == BANDS - 1 else top + (k + 1) * band
        c = _mix(colors[0], colors[1], k / (BANDS - 1))
        out.append(_event(1, t, f"{pos}{entrance}\\bord0\\shad0\\c{_ass_color(c)}"
                                f"\\clip(0,{y0:.1f},{width},{y1:.1f})", t.text))
    return out


def events(texts: list[Text], width: int, height: int, text_y: float = 0.5,
           accent: str = "#FFD640") -> list[str]:
    C = _Canvas(width, height)
    out: list[str] = []
    for t in texts:
        if t.style == "word":
            info, em = _font("word"), C.em("word")
            text = t.text.upper()
            t = Text(t.start, t.end, text, t.style, t.sub, t.color)
            scale = _fit(info, text, em, width)
            colors = (t.color, "#FFFFFF") if t.color else WORD_GRADIENT
            # small punch-in: 125 % → 100 % in 80 ms, short fade
            entrance = (f"\\fad(30,60)\\fscx{scale * 125:.0f}\\fscy{scale * 125:.0f}"
                        f"\\t(0,80,\\fscx{scale * 100:.1f}\\fscy{scale * 100:.1f})")
            out += _gradient(t, info, em, width / 2, _cy(info, em * scale, height * text_y), scale,
                             colors, entrance, width, outline=em * 0.03, shadow=em * 0.05)
        elif t.style == "kill":
            info, em = _font("kill"), C.em("kill")
            text = t.text.upper()
            scale = _fit(info, text, em, width)
            center = height * text_y
            y = _cy(info, em * scale, center)
            punch = (f"\\fad(0,120)\\blur6\\fscx{scale * 160:.0f}\\fscy{scale * 160:.0f}"
                     f"\\t(0,110,\\blur0\\fscx{scale * 100:.1f}\\fscy{scale * 100:.1f})")
            border = _ass_color(t.color or accent)
            out.append(_event(1, t, f"\\an5\\pos({width / 2:.1f},{y:.1f}){_font_tags(info, em)}{punch}"
                                    f"\\c&H00FFFFFF&\\bord{em * 0.06:.1f}\\3c{border}\\shad{em * 0.05:.1f}"
                                    f"\\4a&H40&", text))
            if t.sub:
                si, sem = _font("kill_sub"), C.em("kill_sub")
                # The champion below: its capitals start 0.22 em of the name
                # below the foot of the name's capitals.
                sub_center = (center + _cap_height(info, em * scale) / 2 + em * 0.22
                              + _cap_height(si, sem) / 2)
                out.append(_event(1, t, f"\\an5\\pos({width / 2:.1f},{_cy(si, sem, sub_center):.1f}){_font_tags(si, sem)}"
                                        f"\\fad(80,120)\\c{border}\\bord{sem * 0.08:.1f}\\3c&H00000000&",
                                  t.sub.upper()))
        elif t.style == "multikill":
            info, em = _font("multikill"), C.em("multikill")
            text = t.text.upper()
            t = Text(t.start, t.end, text, t.style, t.sub, t.color)
            scale = _fit(info, text, em, width)
            entrance = (f"\\fad(0,150)\\frz-6\\fscx{scale * 190:.0f}\\fscy{scale * 190:.0f}"
                        f"\\t(0,120,\\frz0\\fscx{scale * 100:.1f}\\fscy{scale * 100:.1f})")
            out += _gradient(t, info, em, width / 2, _cy(info, em * scale, height * min(0.85, text_y + 0.16)), scale,
                             MULTIKILL_GRADIENT, entrance, width, outline=em * 0.06, shadow=em * 0.06)
        elif t.style == "title":
            info, em = _font("title"), C.em("title")
            scale = _fit(info, t.text, em, width)
            sc = f"\\fscx{scale * 100:.1f}\\fscy{scale * 100:.1f}" if scale < 0.999 else ""
            out.append(_event(1, t, f"\\an8\\pos({width / 2:.1f},{height * 0.07:.1f}){_font_tags(info, em)}{sc}"
                                    f"\\fad(200,200)\\bord{em * 0.08:.1f}\\3c&H00000000&", t.text))
        else:   # sub
            info, em = _font("sub"), C.em("sub")
            scale = _fit(info, t.text, em, width)
            sc = f"\\fscx{scale * 100:.1f}\\fscy{scale * 100:.1f}" if scale < 0.999 else ""
            out.append(_event(1, t, f"\\an2\\pos({width / 2:.1f},{height * 0.86:.1f}){_font_tags(info, em)}{sc}"
                                    f"\\bord{em * 0.1:.1f}\\3c&H00000000&", t.text))
    return out


def build_ass(texts: list[Text], width: int, height: int, text_y: float = 0.5,
              accent: str = "#FFD640") -> str:
    return _header(width, height) + "\n".join(events(texts, width, height, text_y, accent)) + "\n"


def write(texts: list[Text], width: int, height: int, text_y: float = 0.5,
          accent: str = "#FFD640") -> str | None:
    """Writes the .ass to uploads/ and returns its path (None if there are no texts)."""
    if not texts:
        return None
    os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)
    path = os.path.join(config.UPLOAD_FOLDER, f"edit_{uuid.uuid4().hex[:12]}.ass")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build_ass(texts, width, height, text_y, accent))
    return path
