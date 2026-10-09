"""Registro de las fuentes de subtítulos (assets/fonts/), leído con fontTools.

Dos cosas que libass necesita y que NO salen del nombre del archivo:

1. El nombre con el que buscarla. libass busca por FAMILIA (nameID 1) más la
   negrita, no por el nombre del archivo: pasarle "Poppins-Bold" (que es el
   nombre PostScript) hacía que cayera en silencio a Arial Bold. La regla que
   resuelve bien (verificada con el log `fontselect` de libass para las cinco
   fuentes del repo) es familia nameID 1 + Bold si el subestilo nameID 2 dice
   "Bold": "Poppins"+Bold, "Poppins Black", "Anton", "Archivo Black"…

2. El tamaño. libass interpreta el Fontsize del ASS como el alto
   winAscent+winDescent de la fuente, no como el em. Poppins tiene ese alto en
   1.76 em: un Fontsize 64 dibujaba letras con un em de 36 px. El preview en
   canvas dibuja em = font_size px, así que para que el render coincida el ASS
   lleva font_size * size_ratio (size_ratio = (winA+winD)/UPM, distinto en
   cada fuente).
"""
import os
import threading

from fontTools.ttLib import TTFont

import config

# file -> (mtime, info). Se relee solo si el archivo cambia.
_CACHE: dict[str, tuple[float, dict]] = {}
_LOCK = threading.Lock()


def _read(path: str) -> dict:
    font = TTFont(path, lazy=True)
    names = font["name"]
    file = os.path.basename(path)
    family = names.getDebugName(1) or os.path.splitext(file)[0]
    subfamily = names.getDebugName(2) or ""
    full = names.getDebugName(4) or family
    # Familia tipográfica (nameID 16 si existe): agrupa Poppins Bold, ExtraBold
    # y Black aunque libass las vea como familias distintas.
    group = names.getDebugName(16) or family
    os2 = font["OS/2"]
    upm = font["head"].unitsPerEm
    # Anchos de avance por carácter, para medir un grupo de palabras antes de
    # quemarlo (ver text_width). Sin kerning: mide apenas de más, del lado
    # seguro (HarfBuzz lo aplica y el texto real sale un poco más angosto).
    hmtx = font["hmtx"]
    cmap = font.getBestCmap()
    adv = {cp: hmtx[gname][0] for cp, gname in cmap.items()}
    notdef = hmtx[".notdef"][0] if ".notdef" in hmtx.metrics else upm // 2
    # Alto y profundidad de tinta de cada glifo (acentos, puntos de la i,
    # descendentes): la escalera separa sus renglones con esto (ink_extent).
    ink = {}
    if "glyf" in font:
        glyf = font["glyf"]
        for cp, gname in cmap.items():
            g = glyf[gname]
            if getattr(g, "numberOfContours", 0) and hasattr(g, "yMax"):
                ink[cp] = (g.yMin, g.yMax)
    font.close()
    label = full[: -len(" Regular")] if full.endswith(" Regular") else full
    return {
        "file": file,
        "family": family,
        "group": group,
        "bold": "bold" in subfamily.lower(),
        "italic": "italic" in subfamily.lower() or "oblique" in subfamily.lower(),
        "label": label,
        "weight": os2.usWeightClass,
        "size_ratio": round((os2.usWinAscent + os2.usWinDescent) / upm, 4),
        # libass apoya el texto (\an2 = abajo) en baseline + winDescent. El
        # canvas usa otra métrica para "bottom" (24 px de diferencia en Poppins
        # a tamaño 64): el preview dibuja sobre esta misma línea base.
        "descent": round(os2.usWinDescent / upm, 4),
        "upm": upm,
        "adv": adv,
        "notdef": notdef,
        "ink": ink,
        "cap": getattr(os2, "sCapHeight", 0) or round(upm * 0.7),
    }


def _info_for(file: str) -> dict | None:
    path = os.path.join(config.FONTS_FOLDER, file)
    if not os.path.isfile(path):
        return None
    try:
        mtime = os.path.getmtime(path)
        with _LOCK:
            cached = _CACHE.get(file)
            if cached and cached[0] == mtime:
                return cached[1]
            info = _read(path)
            _CACHE[file] = (mtime, info)
            return info
    except Exception as exc:  # noqa: BLE001 — una fuente rota no tumba a las demás
        # Archivo corrupto, truncado o borrado a mitad de camino: se ignora (no
        # aparece en la lista ni valida) en vez de dar 500 en todos los renders.
        print(f"[subtitles] fuente ilegible ignorada: {file} ({exc})")
        return None


def available_fonts() -> list[dict]:
    """Fuentes de assets/fonts: la de por defecto, después su misma familia de
    más liviana a más pesada, y luego el resto agrupado por familia."""
    if not os.path.isdir(config.FONTS_FOLDER):
        return []
    infos = [_info_for(f) for f in os.listdir(config.FONTS_FOLDER)
             if config.allowed_font_file(f)]
    infos = [i for i in infos if i]
    default = _info_for(config.DEFAULT_FONT_FILE)
    default_group = default["group"] if default else None
    return sorted(infos, key=lambda i: (i["file"] != config.DEFAULT_FONT_FILE,
                                        i["group"] != default_group,
                                        i["group"], i["weight"], i["label"]))


def is_available(file: str) -> bool:
    safe = os.path.basename(file or "")
    return bool(safe) and config.allowed_font_file(safe) and _info_for(safe) is not None


def font_info(file: str | None) -> dict:
    """Info de la fuente pedida; si no existe, la de por defecto del proyecto.

    El schema ya valida que la fuente exista, así que el fallback solo cubre
    una instalación incompleta (p. ej. la fuente se borró entre el pedido y el
    render) — mejor seguir con la de siempre que cortar el render.
    """
    for candidate in (os.path.basename(file or ""), config.DEFAULT_FONT_FILE):
        if candidate:
            info = _info_for(candidate)
            if info:
                return info
    fonts = available_fonts()
    if fonts:
        return fonts[0]
    raise RuntimeError("No hay ninguna fuente en assets/fonts/.")


def text_width(info: dict, text: str, em_px: float) -> float:
    """Ancho de avance de `text` en px con un em de `em_px` (sin contorno)."""
    adv, notdef = info["adv"], info["notdef"]
    return sum(adv.get(ord(ch), notdef) for ch in text) * em_px / info["upm"]


def ink_extent(info: dict, text: str) -> tuple[float, float]:
    """(alto, profundidad) de la tinta de `text`, en em: cuánto sube sobre la
    línea base (acentos incluidos) y cuánto baja (descendentes, comas)."""
    ink = info["ink"]
    tops = [ink[ord(ch)][1] for ch in text if ord(ch) in ink]
    bottoms = [ink[ord(ch)][0] for ch in text if ord(ch) in ink]
    top = max(tops) if tops else info["cap"]
    depth = max(0, -min(bottoms)) if bottoms else 0
    return top / info["upm"], depth / info["upm"]


def ass_font_size(info: dict, em_px: int) -> int:
    """Fontsize del ASS para que la letra tenga un em de `em_px` px."""
    return max(1, round(em_px * info["size_ratio"]))
