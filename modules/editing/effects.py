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
from core import ffmpeg_runner
from modules.editing.edl import Event

ZOOM_ATTACK = 0.04
FLASH_MAX = 0.75              # eq brightness (−1..1): 0.75 ≈ almost white
ZOOM_MAX = 0.12               # zoom punch at intensity 1 (12 %)
SHAKE_MAX = 0.025             # shake at intensity 1: 2.5 % of the width
SHAKE_HZ = 13.0
RGB_PX = 10
HOLD_EASE = 0.05              # a stair-zoom step reaches its level in this long
WHIP_ZOOM = 0.35              # the whip's zoom punch at intensity 1 (35 %)
WHIP_ATTACK = 0.08            # it starts this long BEFORE the cut (in the outgoing shot)
WHIP_BLUR = 28.0              # horizontal motion blur (gblur sigma) at intensity 1

# Measured on IG-GEN gameplay: mean luma 56.4 → 56.2, std 32.1 → 35.2.
VIVID_CURVE = "curves=all='0/0 0.1/0.085 0.24/0.24 0.45/0.49 0.75/0.80 1/1'"
# Soft: corners at 76 % and the top edge at 81 % (PI/5 left them at 37 and
# 48 %, darkening the stats table of a vertical frame).
VIGNETTE = "vignette=angle=PI/9"

# Looks: FFmpeg chains. "golden" is the one from the reference edit (Levi):
# shadows toward teal, highlights toward amber and high saturation.
LOOKS = {
    "neutral": ("Sin cambios", ""),
    # A contrast curve pivoting where the game lives (dark, luma ~56): eq's
    # contrast pivots on the mid gray and took the gameplay from 57 to 52.
    "vivid": ("Más contraste y saturación", f"{VIVID_CURVE},eq=saturation=1.25"),
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
    # Inside '…' FFmpeg takes backslashes literally, so a quote can't be escaped.
    if "'" in name or not os.path.isfile(path):
        raise ValueError(f"LUT inválido o inexistente: {name!r} (va en assets/luts, sin comillas en el nombre).")
    rel = os.path.relpath(path, config.BASE_DIR).replace("\\", "/")
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
    return ffmpeg_runner.expr_sum(terms)


def flash(events: list[Event], intensity: float = 1.0) -> str:
    """eq that whitens on every flash (empty if there are none)."""
    fl = [e for e in events if e.kind == "flash"]
    if not fl:
        return ""
    expr = _sum([f"{_f(FLASH_MAX * e.intensity * intensity)}*{_pulse(e.t, e.dur)}" for e in fl])
    return f"eq=brightness='min({_f(FLASH_MAX)},{expr})':eval=frame"


def _push(t0: float, dur: float, var: str = "in_time") -> str:
    """0 → 1 linearly over [t0, t0 + dur], 0 outside."""
    return f"if(between({var},{_f(t0)},{_f(t0 + dur)}),({var}-{_f(t0)})/{_f(dur)},0)"


def _hold(t0: float, dur: float, var: str = "in_time") -> str:
    """1 during [t0, t0 + dur] (reached in HOLD_EASE), 0 outside."""
    return f"if(between({var},{_f(t0)},{_f(t0 + dur)}),min(1,({var}-{_f(t0)})/{_f(HOLD_EASE)}),0)"


def camera(events: list[Event], width: int, height: int, fps: int, intensity: float = 1.0) -> str:
    """zoompan with every camera move (empty if there are none): zoom punches,
    shakes, slow push-ins, held zoom steps and the whip's punch.

    Zoom, push and hold can aim somewhere other than the center (Event.x/y):
    the frame center moves toward that point in proportion to the move's own
    envelope, so it goes back to the center when the move ends."""
    v = "in_time"
    z, sx, sy, focus_x, focus_y = [], [], [], [], []
    for i, e in enumerate(events):
        amp = e.intensity * intensity
        if e.kind == "zoom":
            env = _pulse(e.t, e.dur, ZOOM_ATTACK, v)
            z.append(f"{_f(ZOOM_MAX * amp)}*{env}")
        elif e.kind == "shake":
            env = _pulse(e.t, e.dur, 0, v)
            # Margin to shake: twice the amplitude, while the shake lasts.
            z.append(f"{_f(2 * SHAKE_MAX * amp)}*{env}")
            sx.append(f"{_f(SHAKE_MAX * amp)}*iw*{env}*sin({_f(2 * 3.14159 * SHAKE_HZ)}*{v}+{_f(i * 1.7)})")
            sy.append(f"{_f(SHAKE_MAX * amp)}*ih*{env}*cos({_f(2 * 3.14159 * SHAKE_HZ * 1.3)}*{v}+{_f(i * 2.3)})")
            continue
        elif e.kind == "push":
            env = _push(e.t, e.dur, v)
            z.append(f"{_f(e.level)}*{env}")
        elif e.kind == "hold":
            env = _hold(e.t, e.dur, v)
            z.append(f"{_f(e.level)}*{env}")
        elif e.kind == "whip":
            z.append(f"{_f(WHIP_ZOOM * amp)}*{_pulse(e.t, e.dur, WHIP_ATTACK, v)}")
            continue
        else:
            continue
        if (e.x, e.y) != (0.5, 0.5):
            focus_x.append(f"{_f(e.x - 0.5)}*{env}")
            focus_y.append(f"{_f(e.y - 0.5)}*{env}")
    if not z:
        return ""
    fx = f"(0.5+{_sum(focus_x)})" if focus_x else "0.5"
    fy = f"(0.5+{_sum(focus_y)})" if focus_y else "0.5"
    zexpr = f"1+{_sum(z)}"
    x = f"clip(iw*{fx}-iw/zoom/2+{_sum(sx)},0,iw-iw/zoom)"
    y = f"clip(ih*{fy}-ih/zoom/2+{_sum(sy)},0,ih-ih/zoom)"
    return f"zoompan=z='{zexpr}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps}"


def whip_blur(events: list[Event], intensity: float = 1.0) -> str:
    """Horizontal motion blur around each whip (empty if there are none)."""
    ev = [e for e in events if e.kind == "whip"]
    if not ev:
        return ""
    sigma = WHIP_BLUR * intensity * max(e.intensity for e in ev)
    enable = _sum([f"between(t,{_f(e.t - WHIP_ATTACK)},{_f(e.t + e.dur * 0.6)})" for e in ev])
    return f"gblur=sigma={_f(sigma)}:sigmaV=0.01:enable='{enable}'"


def blackout(events: list[Event]) -> str:
    """The whole frame black during the "black" events (empty if there are none)."""
    ev = [e for e in events if e.kind == "black"]
    if not ev:
        return ""
    enable = _sum([f"between(t,{_f(e.t)},{_f(e.t + e.dur)})" for e in ev])
    return f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='{enable}'"


def gain(keys: list) -> str:
    """volume filter for a [(t, gain)] automation: linear between keyframes,
    held before the first and after the last (eval=frame: per audio frame)."""
    keys = sorted(keys)
    if len(keys) == 1:
        return f"volume={_f(keys[0][1])}"
    (t0, g0), (tn, gn) = keys[0], keys[-1]
    terms = [f"if(lt(t,{_f(t0)}),{_f(g0)},0)", f"if(gte(t,{_f(tn)}),{_f(gn)},0)"]
    for (a, ga), (b, gb) in zip(keys, keys[1:]):
        if b - a < 1e-6:
            continue
        terms.append(f"if(gte(t,{_f(a)})*lt(t,{_f(b)}),{_f(ga)}+{_f(gb - ga)}*(t-{_f(a)})/{_f(b - a)},0)")
    return f"volume='{_sum(terms)}':eval=frame"


def rgb(events: list[Event], intensity: float = 1.0) -> str:
    """rgbashift switched on during the "rgb" events (empty if there are none)."""
    ev = [e for e in events if e.kind == "rgb"]
    if not ev:
        return ""
    px = max(2, int(round(RGB_PX * intensity * max(e.intensity for e in ev))))
    enable = _sum([f"between(t,{_f(e.t)},{_f(e.t + e.dur)})" for e in ev])
    return f"rgbashift=rh=-{px}:bh={px}:enable='{enable}'"


def glow(src: str, dst: str, amount: float, width: int, height: int) -> str:
    """Soft glow (cheap bloom): the image blurred at 1/4, added in screen mode.
    `src`/`dst` are filter_complex labels."""
    return (f"[{src}]split[gl_a][gl_b];"
            f"[gl_b]scale={width // 4}:{height // 4},gblur=sigma=6,scale={width}:{height}[gl_c];"
            f"[gl_a][gl_c]blend=all_mode=screen:all_opacity={_f(min(1.0, amount) * 0.45)}[{dst}]")
