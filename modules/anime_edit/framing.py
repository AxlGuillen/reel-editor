"""Framings and color of the manga edit (rules v1.0, edit 3 of Magna vs Dante).

  B (`B`, "z" in edit3.py)  a 4:5 crop of the scene, 1080 × 1350, over a blurred,
                            darker copy of the same frame filling 9:16. The base.
  C (`C`, "w")              the whole 16:9 scene, 1080 wide, over the same blur.
                            For wide shots, alternating with B.
  A (`A`, "v")              a vertical 9:16 crop filling the frame. Close-ups.

B and A take a horizontal focus (0 = left edge, 1 = right edge of the crop's
travel) that can pan from `focus[0]` to `focus[1]` over the take.

Color per take: the edit's grade (contrast, saturation, an S curve and a
screen-blend glow) with a cold or warm balance, and the vignette. Everything
is one filter chain for Segment.vf: it runs in pass 1 on the source frames.
The pan sees source time through editing.render.SOURCE_T0, so it lasts the
take's OUTPUT duration whatever its speed (edit3.py measured it on the output).
"""
from __future__ import annotations

from modules.editing.render import SOURCE_T0

FRAMES = ("A", "B", "C")
TONES = ("cold", "warm")
LEGACY = {"v": "A", "z": "B", "w": "C"}          # edit3.py's letters

GRADE = ("eq=contrast=1.14:saturation=1.38:gamma=0.96,curves=all='0/0 0.20/0.15 0.80/0.86 1/1',{balance},"
         "split[mg_a][mg_b];[mg_b]gblur=sigma=28,eq=brightness=-0.12:saturation=1.2[mg_bl];"
         "[mg_a][mg_bl]blend=all_mode=screen:all_opacity=0.32")
BALANCE = {"cold": "colorbalance=bs=0.10:bm=0.04:rh=-0.03",
           "warm": "colorbalance=rs=0.05:rm=0.05:bs=-0.06"}
TAIL = "vignette=PI/6:eval=init,format=yuv420p"


def _even(x: float) -> int:
    return int(round(x / 2)) * 2


def pan_x(travel: str, focus: tuple[float, float], src_start: float, src_dur: float) -> str:
    """x of a crop that pans from focus[0] to focus[1] over `src_dur` source s
    starting at `src_start` (source time = t + SOURCE_T0 in pass 1)."""
    a, b = focus
    if abs(b - a) < 1e-6 or src_dur <= 0:
        return f"({travel})*{a:.4f}"
    k = f"min(1,max(0,(t+{SOURCE_T0}-{src_start:.4f})/{src_dur:.4f}))"
    return f"({travel})*({a:.4f}+({b - a:.4f})*{k})"


def frame_filter(frame: str, tone: str, focus: tuple[float, float], src_start: float, src_dur: float,
                 source_size: tuple[int, int], width: int = 1080, height: int = 1920) -> str:
    """Segment.vf of one take: grade + framing + vignette."""
    frame = LEGACY.get(frame, frame)
    if frame not in FRAMES:
        raise ValueError(f"Encuadre desconocido: {frame!r}. Opciones: A (vertical), B (4:5) o C (16:9).")
    if tone not in TONES:
        raise ValueError(f"Tono desconocido: {tone!r}. Opciones: cold o warm.")
    sw, sh = source_size
    grade = GRADE.format(balance=BALANCE[tone])
    if frame == "A":
        cw = _even(sh * width / height)                  # 608 of 1080
        x = pan_x(f"iw-{cw}", focus, src_start, src_dur)
        return (f"{grade},crop={cw}:{sh}:x='{x}':y=0,scale={width}:{height}:flags=lanczos,"
                f"unsharp=5:5:0.9:5:5:0.0,{TAIL}")
    bg = (f"[mg_bg]scale=-2:{height},crop={width}:{height},gblur=sigma=45,"
          f"eq=brightness=-0.10:saturation=1.15[mg_bgo]")
    if frame == "B":
        cw = _even(sh * 4 / 5)                           # 864 of 1080
        x = pan_x(f"iw-{cw}", focus, src_start, src_dur)
        fg = (f"[mg_fg]crop={cw}:{sh}:x='{x}':y=0,scale={width}:{_even(width * 5 / 4)}:flags=lanczos,"
              f"unsharp=5:5:0.7:5:5:0.0[mg_fgo]")
    else:
        fg = f"[mg_fg]scale={width}:{_even(width * sh / sw)}:flags=lanczos,unsharp=5:5:0.6:5:5:0.0[mg_fgo]"
    return f"{grade},split[mg_fg][mg_bg];{bg};{fg};[mg_bgo][mg_fgo]overlay=(W-w)/2:(H-h)/2,{TAIL}"
