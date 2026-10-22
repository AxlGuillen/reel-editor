"""FFmpeg filters for the accents and the color of an edit (pure: they build text).

Measured on the system FFmpeg (1080x1920, 60 fps):
  flash   eq=brightness with eval=frame: evaluated per FRAME, not per pixel
          (half the cost of hue=b; per-pixel blend/geq do not keep up).
  zoom +  zoompan with d=1: one output frame per input frame; z, x and y are
  shake   expressions in the frame time (in_time). The zoom punch and the
          shake share the SAME zoompan: the shake needs margin, so while it
          shakes a small zoom is added to provide it.
  rgb     rgbashift with enable: red and blue planes shifted on the hits.

Each accent is a pulse: it rises in `attack` s and falls quadratically in `dur` s.
"""
from __future__ import annotations

import os

import config
from modules.editing.edl import Event

ZOOM_ATTACK = 0.04
FLASH_MAX = 0.75              # eq brightness (−1..1): 0.75 ≈ almost white
ZOOM_MAX = 0.12               # zoom punch at intensity 1 (12 %)
SHAKE_MAX = 0.025             # shake at intensity 1: 2.5 % of the width
SHAKE_HZ = 13.0
RGB_PX = 10

# Looks: FFmpeg chains. "golden" is the one from the reference edit (Levi):
# shadows toward teal, highlights toward amber and high saturation.
LOOKS = {
    "neutral": ("Sin cambios", ""),
    "vivid": ("Más contraste y saturación", "eq=contrast=1.08:saturation=1.25:gamma=0.98"),
    "golden": ("Sombras teal, luces ámbar, muy saturado (anime edit)",
               "colorbalance=rs=-0.06:gs=0.01:bs=0.06:rm=0.04:gm=0.02:bm=-0.04:rh=0.08:gh=0.03:bh=-0.08,"
               "eq=contrast=1.08:saturation=1.38:gamma=0.97"),
    "film": ("Contraste de película, un poco desaturado",
             "eq=contrast=1.12:saturation=0.92,curves=preset=medium_contrast"),
    "cold": ("Azul frío y contraste", "colorbalance=rs=-0.05:bs=0.07:rh=-0.03:bh=0.05,eq=contrast=1.1:saturation=1.1"),
    "night": ("Oscuro con sombras azules",
              "colorbalance=rs=-0.04:bs=0.08,eq=brightness=-0.05:contrast=1.15:saturation=1.05:gamma=0.92"),
    "fire": ("Rojo y naranja intensos", "colorbalance=rs=0.08:gs=0.02:bs=-0.08:rh=0.06:bh=-0.06,eq=saturation=1.3:contrast=1.1"),
}


def looks() -> list[dict]:
    return [{"id": k, "description": v[0]} for k, v in LOOKS.items()]


def luts() -> list[str]:
    if not os.path.isdir(config.LUTS_FOLDER):
        return []
    return sorted(f for f in os.listdir(config.LUTS_FOLDER) if f.lower().endswith(".cube"))


def lut_filter(name: str) -> str:
    """lut3d with the path RELATIVE to BASE_DIR (FFmpeg runs with cwd=BASE_DIR):
    the "C:" of an absolute Windows path breaks the filter parser."""
    path = os.path.join(config.LUTS_FOLDER, os.path.basename(name))
    rel = os.path.relpath(path, config.BASE_DIR).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f"lut3d=file='{rel}':interp=tetrahedral"


def color(look: str, lut: str = "") -> str:
    """Color chain: the LUT (if any) and the look on top."""
    if look not in LOOKS:
        raise ValueError(f"Look desconocido: {look!r}. Opciones: {', '.join(LOOKS)}.")
    parts = []
    if lut:
        parts.append(lut_filter(lut))
    if LOOKS[look][1]:
        parts.append(LOOKS[look][1])
    return ",".join(parts)


def _f(x: float) -> str:
    return f"{x:.4f}".rstrip("0").rstrip(".") if x else "0"


def _pulse(t0: float, dur: float, attack: float = 0.0, var: str = "t") -> str:
    """0 → 1 in `attack` s before t0, and 1 → 0 quadratically in `dur` s after."""
    fall = f"if(between({var},{_f(t0)},{_f(t0 + dur)}),pow(1-({var}-{_f(t0)})/{_f(dur)},2),0)"
    if attack <= 0:
        return fall
    rise = f"if(between({var},{_f(t0 - attack)},{_f(t0)}),({var}-{_f(t0 - attack)})/{_f(attack)},0)"
    return f"({rise}+{fall})"


def _sum(terms: list[str]) -> str:
    return "+".join(terms) if terms else "0"


def flash(events: list[Event], intensity: float = 1.0) -> str:
    """eq that whitens on every flash (empty if there are none)."""
    fl = [e for e in events if e.kind == "flash"]
    if not fl:
        return ""
    expr = _sum([f"{_f(FLASH_MAX * e.intensity * intensity)}*{_pulse(e.t, e.dur)}" for e in fl])
    return f"eq=brightness='min({_f(FLASH_MAX)},{expr})':eval=frame"


def camera(events: list[Event], width: int, height: int, fps: int, intensity: float = 1.0) -> str:
    """zoompan with the zoom punches and the shakes (empty if there are none)."""
    zooms = [e for e in events if e.kind == "zoom"]
    shakes = [e for e in events if e.kind == "shake"]
    if not zooms and not shakes:
        return ""
    v = "in_time"
    z = [f"{_f(ZOOM_MAX * e.intensity * intensity)}*{_pulse(e.t, e.dur, ZOOM_ATTACK, v)}" for e in zooms]
    # Margin to shake: twice the amplitude, while the shake lasts.
    z += [f"{_f(2 * SHAKE_MAX * e.intensity * intensity)}*{_pulse(e.t, e.dur, 0, v)}" for e in shakes]
    sx = [f"{_f(SHAKE_MAX * e.intensity * intensity)}*iw*{_pulse(e.t, e.dur, 0, v)}"
          f"*sin({_f(2 * 3.14159 * SHAKE_HZ)}*{v}+{_f(i * 1.7)})" for i, e in enumerate(shakes)]
    sy = [f"{_f(SHAKE_MAX * e.intensity * intensity)}*ih*{_pulse(e.t, e.dur, 0, v)}"
          f"*cos({_f(2 * 3.14159 * SHAKE_HZ * 1.3)}*{v}+{_f(i * 2.3)})" for i, e in enumerate(shakes)]
    zexpr = f"1+{_sum(z)}"
    x = f"clip(iw/2-iw/zoom/2+{_sum(sx)},0,iw-iw/zoom)"
    y = f"clip(ih/2-ih/zoom/2+{_sum(sy)},0,ih-ih/zoom)"
    return f"zoompan=z='{zexpr}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps}"


def rgb(events: list[Event], intensity: float = 1.0) -> str:
    """rgbashift switched on during the "rgb" events (empty if there are none)."""
    ev = [e for e in events if e.kind == "rgb"]
    if not ev:
        return ""
    px = max(2, int(round(RGB_PX * intensity * max(e.intensity for e in ev))))
    enable = "+".join(f"between(t,{_f(e.t)},{_f(e.t + e.dur)})" for e in ev)
    return f"rgbashift=rh=-{px}:bh={px}:enable='{enable}'"


def glow(src: str, dst: str, amount: float, width: int, height: int) -> str:
    """Soft glow (cheap bloom): the image blurred at 1/4, added in screen mode.
    `src`/`dst` are filter_complex labels."""
    return (f"[{src}]split[gl_a][gl_b];"
            f"[gl_b]scale={width // 4}:{height // 4},gblur=sigma=6,scale={width}:{height}[gl_c];"
            f"[gl_a][gl_c]blend=all_mode=screen:all_opacity={_f(min(1.0, amount) * 0.45)}[{dst}]")
