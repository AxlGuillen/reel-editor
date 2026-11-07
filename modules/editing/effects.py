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

import math
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
WHIP_SHIFT = 0.08             # with a direction: the frame thrown 8 % of the width that way
GLOW_ATTACK = 0.03
GLOW_SIGMA = 10.0             # bloom blur at 1/4 size
GLOW_OPACITY = 0.7

# Measured on IG-GEN gameplay: mean luma 56.4 → 56.2, std 32.1 → 35.2.
VIVID_CURVE = "curves=all='0/0 0.1/0.085 0.24/0.24 0.45/0.49 0.75/0.80 1/1'"
# Soft: corners at 76 % and the top edge at 81 % (PI/5 left them at 37 and
# 48 %, darkening the stats table of a vertical frame).
VIGNETTE = "vignette=angle=PI/9"

# Per-shot tints (Segment.tint), applied in pass 1 over that shot only: "bw"
# is full black and white with a little contrast (the sad aura of the last fight).
TINTS = {"bw": "hue=s=0,eq=contrast=1.12"}

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


def _pulse(t0: float, dur: float, attack: float = 0.0, var: str = "t", power: float = 2.0) -> str:
    """0 → 1 in `attack` s before t0, and 1 → 0 as (1−x)^power in `dur` s after."""
    fall = f"if(between({var},{_f(t0)},{_f(t0 + dur)}),pow(1-({var}-{_f(t0)})/{_f(dur)},{_f(power)}),0)"
    if attack <= 0:
        return fall
    rise = f"if(between({var},{_f(t0 - attack)},{_f(t0)}),({var}-{_f(t0 - attack)})/{_f(attack)},0)"
    return f"({rise}+{fall})"


def _sum(terms: list[str]) -> str:
    return ffmpeg_runner.expr_sum(terms)


def flash(events: list[Event], intensity: float = 1.0) -> str:
    """eq that whitens on every flash (empty if there are none)."""
    fl = [e for e in events if e.kind == "flash" and e.shape == "pulse"]
    if not fl:
        return ""
    expr = _sum([f"{_f(FLASH_MAX * e.intensity * intensity)}*{_pulse(e.t, e.dur)}" for e in fl])
    return f"eq=brightness='min({_f(FLASH_MAX)},{expr})':eval=frame"


def frames_of(t: float, dur: float, fps: int) -> tuple[int, int]:
    """First and last output frame whose time i/fps falls in [t, t + dur)."""
    a = math.ceil(t * fps - 1e-6)
    b = math.ceil((t + dur) * fps - 1e-6) - 1
    return a, max(a, b)


def _frames_enable(windows: list[tuple[int, int]]) -> str:
    return _sum([f"between(n,{a},{b})" for a, b in windows])


def flash_mix(events: list[Event], fps: int, intensity: float = 1.0) -> str:
    """The "mix" flashes: the frame mixed with white by `intensity` during the
    whole flash, by frame number (exact, like the manga edit: 55 % for the
    frames in [t, t + 0.07)). In YUV tv range white is (235, 128, 128) and the
    mix is linear, so it equals mixing R, G and B toward 255."""
    fl = [e for e in events if e.kind == "flash" and e.shape == "mix"]
    groups: dict[float, list[tuple[int, int]]] = {}
    for e in fl:
        groups.setdefault(round(min(1.0, e.intensity * intensity), 4), []).append(frames_of(e.t, e.dur, fps))
    out = []
    for a, windows in sorted(groups.items()):
        out.append(f"format=yuv420p,lutyuv=y='val+(235-val)*{_f(a)}':u='val+(128-val)*{_f(a)}':"
                   f"v='val+(128-val)*{_f(a)}':enable='{_frames_enable(windows)}'")
    return ",".join(out)


def fade_out(dur_total: float, fade: float) -> str:
    """Linear fade to black over the last `fade` s of the picture."""
    if fade <= 0:
        return ""
    return f"fade=t=out:st={_f(max(0.0, dur_total - fade))}:d={_f(fade)}"


PANEL_PRESCALE = 1.1           # editing.panels resizes the panel ×1.1 once (LANCZOS)


def panel_size(image_size: tuple[int, int], width: int, height: int) -> tuple[float, float]:
    """The panel's size at scale 1: fitted inside PANEL_BOX of the frame."""
    from modules.editing.edl import PANEL_BOX
    w, h = image_size
    s = min(PANEL_BOX[0] * width / w, PANEL_BOX[1] * height / h)
    return w * s, h * s


def panel_start(e: Event, fps: int) -> float:
    """Where the panel input's first frame goes: half a frame before the event,
    so overlay (which takes the last secondary frame at or before each main
    frame, with the main timestamps rounded to the ms) never misses frame 0."""
    return frames_of(e.t, e.dur, fps)[0] / fps - 0.5 / fps


def panel(src: str, img: str, dst: str, e: Event, fps: int) -> str:
    """One manga panel over the frozen frame (filter_complex parts). `img` is
    the panel's frame sequence (editing.panels.frames: full-frame RGBA, the
    panel already scaled and centered on each frame), its timestamps at
    panel_start. The frame darkens to ×dim (black at 1−dim over it, which in
    YUV tv range equals multiplying R, G and B) and the panel goes on top."""
    a, b = frames_of(e.t, e.dur, fps)
    on = f"between(n,{a},{b})"
    return (f"[{src}]drawbox=x=0:y=0:w=iw:h=ih:color=black@{_f(1 - e.dim)}:t=fill:enable='{on}'[{img}d];"
            f"[{img}d][{img}]overlay=x=0:y=0:enable='{on}'[{dst}]")   # eof_action=pass dropped the last frame


def _push(t0: float, dur: float, var: str = "in_time") -> str:
    """0 → 1 linearly over [t0, t0 + dur], 0 outside."""
    return f"if(between({var},{_f(t0)},{_f(t0 + dur)}),({var}-{_f(t0)})/{_f(dur)},0)"


def _hold(t0: float, dur: float, var: str = "in_time", attack: float = 0.0) -> str:
    """1 during [t0, t0 + dur] (reached in HOLD_EASE), 0 outside. With
    `attack` it starts rising that long BEFORE t0 and is at 1 on t0."""
    if attack > 0:
        rise = f"if(between({var},{_f(t0 - attack)},{_f(t0)}),({var}-{_f(t0 - attack)})/{_f(attack)},0)"
        return f"({rise}+if(gt({var},{_f(t0)})*lte({var},{_f(t0 + dur)}),1,0))"
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
            env = _pulse(e.t, e.dur, e.attack or ZOOM_ATTACK, v)
            z.append(f"{_f(ZOOM_MAX * amp)}*{env}")
        elif e.kind == "shake":
            env = _pulse(e.t, e.dur, 0, v, e.decay)
            # Margin to shake: twice the amplitude, while the shake lasts.
            z.append(f"{_f(2 * SHAKE_MAX * amp)}*{env}")
            if e.angle is None:
                sx.append(f"{_f(SHAKE_MAX * amp)}*iw*{env}*sin({_f(2 * 3.14159 * SHAKE_HZ)}*{v}+{_f(i * 1.7)})")
                sy.append(f"{_f(SHAKE_MAX * amp)}*ih*{env}*cos({_f(2 * 3.14159 * SHAKE_HZ * 1.3)}*{v}+{_f(i * 2.3)})")
            else:
                # along the hit's axis only (the frame recoils the way it was hit),
                # starting toward the hit's direction (sin starts at 0 going up)
                ux, uy = math.cos(math.radians(e.angle)), -math.sin(math.radians(e.angle))
                osc = f"sin({_f(2 * 3.14159 * SHAKE_HZ)}*({v}-{_f(e.t)}))"
                # the camera window moves opposite to the image: −u pushes the image along u
                sx.append(f"{_f(-SHAKE_MAX * amp * ux)}*iw*{env}*{osc}")
                sy.append(f"{_f(-SHAKE_MAX * amp * uy)}*iw*{env}*{osc}")
            continue
        elif e.kind == "push":
            env = _push(e.t, e.dur, v)
            z.append(f"{_f(e.level)}*{env}")
        elif e.kind == "hold":
            env = _hold(e.t, e.dur, v, e.attack)
            z.append(f"{_f(e.level)}*{env}")
        elif e.kind == "whip":
            env = _pulse(e.t, e.dur, WHIP_ATTACK, v)
            z.append(f"{_f(WHIP_ZOOM * amp)}*{env}")
            if e.angle is not None:
                # the frame is thrown the way the hit goes (the window opposite)
                ux, uy = math.cos(math.radians(e.angle)), -math.sin(math.radians(e.angle))
                sx.append(f"{_f(-WHIP_SHIFT * amp * ux)}*iw*{env}")
                sy.append(f"{_f(-WHIP_SHIFT * amp * uy)}*iw*{env}")
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


def whip_axis(e: Event) -> str:
    """gblur only blurs horizontally or vertically: the whip's axis, quantized."""
    if e.angle is None:
        return "h"
    a = math.radians(e.angle)
    return "h" if abs(math.cos(a)) >= abs(math.sin(a)) else "v"


def whip_blur(events: list[Event], intensity: float = 1.0) -> str:
    """Motion blur around each whip along its axis (empty if there are none)."""
    out = []
    for axis in ("h", "v"):
        ev = [e for e in events if e.kind == "whip" and whip_axis(e) == axis]
        if not ev:
            continue
        sigma = _f(WHIP_BLUR * intensity * max(e.intensity for e in ev))
        enable = _sum([f"between(t,{_f(e.t - WHIP_ATTACK)},{_f(e.t + e.dur * 0.6)})" for e in ev])
        size = f"sigma={sigma}:sigmaV=0.01" if axis == "h" else f"sigma=0.01:sigmaV={sigma}"
        out.append(f"gblur={size}:enable='{enable}'")
    return ",".join(out)


def invert(events: list[Event], fps: int) -> str:
    """The negative during each "invert" event, by frame number."""
    ev = [e for e in events if e.kind == "invert"]
    if not ev:
        return ""
    return f"negate=enable='{_frames_enable([frames_of(e.t, e.dur, fps) for e in ev])}'"


def strobe(events: list[Event], fps: int, intensity: float = 1.0) -> str:
    """A white frame (mixed by `intensity`) every `every` frames during each
    "strobe" event, starting on its first frame."""
    ev = [e for e in events if e.kind == "strobe"]
    if not ev:
        return ""
    out = []
    for amount in sorted({round(min(1.0, e.intensity * intensity), 4) for e in ev}):
        terms = []
        for e in ev:
            if round(min(1.0, e.intensity * intensity), 4) != amount:
                continue
            a, b = frames_of(e.t, e.dur, fps)
            terms.append(f"between(n,{a},{b})*not(mod(n-{a},{e.every}))")
        out.append(f"format=yuv420p,lutyuv=y='val+(235-val)*{_f(amount)}':u='val+(128-val)*{_f(amount)}':"
                   f"v='val+(128-val)*{_f(amount)}':enable='{_sum(terms)}'")
    return ",".join(out)


def glow_pulse(src: str, dst: str, events: list[Event], width: int, height: int,
               intensity: float = 1.0, fps: int = 30) -> str:
    """A bloom that swells on each "glow" event: the frame blurred at 1/4,
    darkened by (1 − envelope) and added in screen mode (screen with black is
    no change, so outside the pulses the frame is untouched)."""
    ev = [e for e in events if e.kind == "glow"]
    env = _sum([f"{_f(min(1.0, e.intensity * intensity))}*{_pulse(e.t, e.dur, e.attack or GLOW_ATTACK)}"
                for e in ev])
    on = _frames_enable([frames_of(e.t - (e.attack or GLOW_ATTACK), e.dur + (e.attack or GLOW_ATTACK), fps)
                         for e in ev])
    # Screen on the luma only (eq at −1 takes the bloom's luma to 0, and
    # screen with 0 is no change); the chroma stays the frame's (a screen of
    # the chroma planes, centered on 128, shifted every frame's color). And
    # only inside the pulses (enable). A round trip through RGB to blend all
    # planes moved every frame of the edit ~5 levels (range conversion).
    return (f"[{src}]split[gp_a][gp_b];"
            f"[gp_b]scale={width // 4}:{height // 4},gblur=sigma={_f(GLOW_SIGMA)},scale={width}:{height},"
            f"eq=brightness='min(1,{env})-1':eval=frame[gp_c];"
            f"[gp_a][gp_c]blend=c0_mode=screen:c0_opacity={_f(GLOW_OPACITY)}:c1_mode=normal:c1_opacity=0:"
            f"c2_mode=normal:c2_opacity=0:enable='{on}'[{dst}]")


def mblur(spec: dict, source_size: tuple[int, int]) -> str:
    """Directional motion blur at any angle (pass 1, source frames): gblur only
    blurs along x or y, so the frame turns until the motion is horizontal,
    blurs horizontally and turns back. `amount` is px in a 1920-wide frame."""
    sw, sh = source_size
    angle = float(spec.get("angle") or 0.0)
    sigma = max(0.5, float(spec["amount"]) * sw / 1920 / 2)
    diag = int(math.ceil(math.hypot(sw, sh) / 2)) * 2
    a = math.radians(angle)                 # rotate turns clockwise for a positive angle:
    return (f"rotate={a:.6f}:ow={diag}:oh={diag}:c=black,"     # up (90°) becomes right
            f"gblur=sigma={_f(sigma)}:sigmaV=0.01,"
            f"rotate={-a:.6f}:ow={diag}:oh={diag}:c=black,crop={sw}:{sh}")


def _window(events: list[Event], fps: int | None) -> str:
    """enable expression for the events' spans: by frame number when `fps` is
    given ([t, t + dur) exactly), else by time (both ends inclusive: one
    frame more at the end; kept for callers that do not pass the fps)."""
    if fps:
        return _frames_enable([frames_of(e.t, e.dur, fps) for e in events])
    return _sum([f"between(t,{_f(e.t)},{_f(e.t + e.dur)})" for e in events])


def blackout(events: list[Event], fps: int | None = None) -> str:
    """The whole frame black during the "black" events (empty if there are none)."""
    ev = [e for e in events if e.kind == "black"]
    if not ev:
        return ""
    return f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='{_window(ev, fps)}'"


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


def rgb(events: list[Event], intensity: float = 1.0, fps: int | None = None) -> str:
    """Color split during the "rgb" events (empty if there are none): the
    chroma planes shifted opposite ways, in YUV. rgbashift works in RGB, so
    FFmpeg converted the WHOLE edit to RGB and back even while it was off, and
    the round trip clipped saturated colors (luma −2.9 on average, measured)."""
    ev = [e for e in events if e.kind == "rgb"]
    if not ev:
        return ""
    px = max(2, int(round(RGB_PX * intensity * max(e.intensity for e in ev))))
    half = max(1, px // 2)                   # chroma is half the width in 4:2:0
    return f"chromashift=crh=-{half}:cbh={half}:enable='{_window(ev, fps)}'"


def glow(src: str, dst: str, amount: float, width: int, height: int) -> str:
    """Soft glow (cheap bloom): the image blurred at 1/4, added in screen mode.
    `src`/`dst` are filter_complex labels."""
    return (f"[{src}]split[gl_a][gl_b];"
            f"[gl_b]scale={width // 4}:{height // 4},gblur=sigma=6,scale={width}:{height}[gl_c];"
            f"[gl_a][gl_c]blend=all_mode=screen:all_opacity={_f(min(1.0, amount) * 0.45)}[{dst}]")
