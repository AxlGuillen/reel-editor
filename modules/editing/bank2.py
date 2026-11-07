"""Effects bank 2: the FFmpeg side of the second batch of effects (pure: text).

Two kinds of filter:

  chains   one filter in the main chain, switched on by FRAME NUMBER over
           [t, t + dur) (effects.frames_of): impactframe, grain, satpulse,
           glitch "vhs" / "flicker", and the blur that goes with a shake.
  windows  a branch of the edit trimmed to the event's frames, processed and
           overlaid back (same timestamps, so the overlay is frame-exact): spin,
           zoomblur, glitch "blocks", echo, lens, tilt and overlay. Outside the
           window the frame is never touched (the expensive work runs only there).

Every random-looking thing (glitch offsets, speed lines) comes from the frame
number and the event's seed: the same edit renders the same video.
"""
from __future__ import annotations

import math

from core import ffmpeg_runner
from modules.editing.edl import Event
from modules.editing.effects import _f, frames_of, whip_axis

WINDOW_KINDS = ("spin", "zoomblur", "echo", "lens", "tilt", "overlay")


def is_window(e: Event) -> bool:
    """Effects processed in a trimmed branch (glitch "blocks" too: the default glitch)."""
    return e.kind in WINDOW_KINDS or (e.kind == "glitch" and (e.style or "blocks") == "blocks")
IMPACT_THR = 110              # luma (16–235) that splits ink from paper in an impact frame
GRAIN = 18                    # noise strength at intensity 1
SAT_PULSE = 0.6               # extra saturation at intensity 1
ZOOMBLUR = 0.16               # radial blur reach at intensity 1 (16 % zoom between the copies)
ZOOMBLUR_COPIES = 6
ECHO_WEIGHTS = "1 0.75 0.55 0.4 0.3"
LENS_K = 0.22                 # barrel distortion at intensity 1
TILT = 0.10                   # how far the near edge grows (fraction of the frame) at intensity 1
GLITCH_BANDS = 5
GLITCH_SHIFT = 0.06           # band offset (fraction of the width) at intensity 1


def _sum(terms: list[str]) -> str:
    return ffmpeg_runner.expr_sum(terms)


def _on(events: list[Event], fps: int) -> str:
    return _sum([f"between(n,{a},{b})" for a, b in (frames_of(e.t, e.dur, fps) for e in events)])


def _even(x: float) -> int:
    return int(math.ceil(x / 2)) * 2


# ---------------------------------------------------------------- chains

def impactframe(events: list[Event], fps: int) -> str:
    """The frame redrawn as an impact frame: black ink on white paper (bw),
    white on black (inv) or 4 flat tones (poster), no color."""
    out = []
    looks = {"bw": f"if(gt(val,{IMPACT_THR}),235,16)", "inv": f"if(gt(val,{IMPACT_THR}),16,235)",
             "poster": "16+floor((val-16)/55)*55"}
    for style, y in looks.items():
        ev = [e for e in events if e.kind == "impactframe" and (e.style or "bw") == style]
        if ev:
            out.append(f"format=yuv420p,lutyuv=y='{y}':u=128:v=128:enable='{_on(ev, fps)}'")
    return ",".join(out)


def grain(events: list[Event], fps: int, intensity: float = 1.0) -> str:
    ev = [e for e in events if e.kind == "grain"]
    if not ev:
        return ""
    amount = int(round(GRAIN * intensity * max(e.intensity for e in ev)))
    return f"noise=alls={max(1, amount)}:allf=t:enable='{_on(ev, fps)}'"


def satpulse(events: list[Event], fps: int, intensity: float = 1.0) -> str:
    """Saturation that swells and falls over each event's frames (a sine
    pulse, never 0 inside the window)."""
    ev = [e for e in events if e.kind == "satpulse"]
    if not ev:
        return ""
    terms = []
    for e in ev:
        a, b = frames_of(e.t, e.dur, fps)
        terms.append(f"between(n,{a},{b})*{_f(SAT_PULSE * e.intensity * intensity)}*sin(PI*(n-{a - 1})/{b - a + 2})")
    # enable: eq at saturation 1 still resamples the chroma a little
    return f"eq=saturation='1+{_sum(terms)}':eval=frame:enable='{_on(ev, fps)}'"


def glitch_chain(events: list[Event], fps: int, intensity: float = 1.0) -> str:
    """glitch "vhs" (noise, chroma bleed, scanlines, washed color) and
    "flicker" (brightness jumping frame to frame)."""
    out = []
    vhs = [e for e in events if e.kind == "glitch" and e.style == "vhs"]
    if vhs:
        on = _on(vhs, fps)
        out.append(f"noise=alls={int(22 * intensity)}:allf=t+u:enable='{on}',"
                   f"chromashift=cbh=4:crh=-4:cbv=1:enable='{on}',"
                   f"drawgrid=w=iw:h=4:t=1:c=black@0.35:enable='{on}',"
                   f"eq=saturation=0.75:contrast=1.08:enable='{on}'")
    flick = [e for e in events if e.kind == "glitch" and e.style == "flicker"]
    if flick:
        terms = []
        for e in flick:
            a, b = frames_of(e.t, e.dur, fps)
            amp = 0.22 * e.intensity * intensity
            # alternating brighter / darker, 50–100 % of amp: never a still frame
            terms.append(f"between(n,{a},{b})*{_f(amp)}*(0.5+mod(n*7919+{e.seed * 31},7)/12)*(2*mod(n,2)-1)")
        out.append(f"eq=brightness='{_sum(terms)}':eval=frame")
    return ",".join(out)


def shake_blur(events: list[Event], fps: int, width: int) -> str:
    """The blur that travels with a shake (Event.blur px of a 1920 frame),
    along its axis, quantized to horizontal or vertical like the whip's."""
    out = []
    for axis in ("h", "v"):
        ev = [e for e in events if e.kind == "shake" and e.blur > 0 and whip_axis(e) == axis]
        if not ev:
            continue
        sigma = _f(max(e.blur for e in ev) * width / 1920 / 2)
        size = f"sigma={sigma}:sigmaV=0.01" if axis == "h" else f"sigma=0.01:sigmaV={sigma}"
        out.append(f"gblur={size}:enable='{_on(ev, fps)}'")
    return ",".join(out)


# ---------------------------------------------------------------- windows

def window(src: str, dst: str, e: Event, j: int, fps: int, width: int, height: int,
           intensity: float = 1.0, asset_input: int | None = None) -> str:
    """filter_complex parts: [src] → the event's processed window over it → [dst]."""
    a, b = frames_of(e.t, e.dur, fps)
    lead = 4 if e.kind == "echo" else 0               # the trail needs the frames before
    m, br, fx = f"w{j}m", f"w{j}b", f"w{j}x"
    parts = [f"[{src}]split[{m}][{br}]",
             f"[{br}]trim=start_frame={max(0, a - lead)}:end_frame={b + 1}[{br}t]"]
    parts += _window_chain(f"{br}t", fx, e, j, a, b, fps, width, height, intensity, asset_input)
    parts.append(f"[{m}][{fx}]overlay=x=0:y=0:eof_action=pass:enable='between(n,{a},{b})'[{dst}]")
    return ";".join(parts)


def _window_chain(src: str, dst: str, e: Event, j: int, a: int, b: int, fps: int, width: int,
                  height: int, intensity: float, asset_input: int | None) -> list[str]:
    amp = e.intensity * intensity
    t0, dur = a / fps, max(1, b - a + 1) / fps
    # progress of the window, 1/(n+1) on its first frame and n/(n+1) on its
    # last: every frame of the window shows the effect (a 360° spin is back
    # to the original only on the frame after it)
    step = 1 / fps
    u = f"clip((t-{t0:.6f}+{step:.6f})/{dur + step:.6f},0,1)"
    if e.kind == "spin":
        # mirrored borders on a canvas as big as the diagonal: no black corners
        diag = math.hypot(width, height)
        px, py = _even((diag - width) / 2), _even((diag - height) / 2)   # even: 4:2:0 chroma
        cw, ch = width + 2 * px, height + 2 * py                        # a square-ish canvas ≥ the diagonal
        rad = math.radians(e.degrees) * (1 if (e.angle or 0) >= 0 else -1)
        return [f"[{src}]pad={cw}:{ch}:{px}:{py},fillborders=left={px}:right={px}:top={py}:bottom={py}:mode=mirror,"
                f"rotate=a='{rad:.6f}*{u}*{u}*(3-2*{u})':ow={cw}:oh={ch},crop={width}:{height}[{dst}]"]
    if e.kind == "zoomblur":
        k = ZOOMBLUR_COPIES
        reach = ZOOMBLUR * amp
        names = [f"zb{j}_{i}" for i in range(k)]
        parts = [f"[{src}]split={k}" + "".join(f"[{n}]" for n in names)]
        outs = []
        for i, n in enumerate(names):
            s = 1 + reach * i / (k - 1)
            if i == 0:
                outs.append(f"[{n}]")
                continue
            parts.append(f"[{n}]scale={_even(width * s)}:{_even(height * s)},crop={width}:{height}[{n}s]")
            outs.append(f"[{n}s]")
        parts.append("".join(outs) + f"mix=inputs={k}:weights='{' '.join(['1'] * k)}'[{dst}]")
        return parts
    if e.kind == "glitch":                     # blocks: horizontal bands thrown sideways
        n = GLITCH_BANDS
        h = height // n
        parts = [f"[{src}]chromashift=cbh=6:crh=-6,split={n + 1}[gb{j}base]"
                 + "".join(f"[gb{j}_{i}]" for i in range(n))]
        cur = f"gb{j}base"
        for i in range(n):
            shift = GLITCH_SHIFT * amp * width
            x = f"{_f(shift)}*(mod(n*{97 + 13 * i}+{e.seed * 7 + i * 3},7)-3)*gt(mod(n+{i},3),0)"
            parts.append(f"[gb{j}_{i}]crop={width}:{h}:0:{i * h}[gb{j}c{i}]")
            nxt = f"gb{j}o{i}" if i < n - 1 else dst
            parts.append(f"[{cur}][gb{j}c{i}]overlay=x='{x}':y={i * h}:eval=frame[{nxt}]")
            cur = nxt
        return parts
    if e.kind == "echo":
        return [f"[{src}]tmix=frames=5:weights='{ECHO_WEIGHTS}'[{dst}]"]
    if e.kind == "lens":
        # lenscorrection: r_src = r·(1 + k1·(r/r0)² + k2·(r/r0)⁴), r0 = half the
        # diagonal. Barrel (k > 0) reaches outside the frame at the corners:
        # zoomed back by 1 + k1 + k2 it fills the frame and the center bulges
        # (a fisheye punch). Pincushion (k < 0) stays inside: no zoom.
        k1, k2 = LENS_K * amp, LENS_K * amp / 2
        fill = 1 + k1 + k2 if k1 > 0 else 1.0
        chain = f"[{src}]lenscorrection=k1={_f(k1)}:k2={_f(k2)}:i=bilinear"
        if fill > 1:
            chain += f",scale={_even(width * fill)}:{_even(height * fill)},crop={width}:{height},setsar=1"
        if e.ca <= 0:
            return [chain + f"[{dst}]"]
        r = 1 + e.ca
        g = 1 + e.ca / 2
        # extractplanes emits its pads in r, g, b order whatever the order asked
        # (measured); gbrp is G, B, R
        return [chain + f",format=gbrp,extractplanes=r+g+b[ln{j}r][ln{j}g][ln{j}b]",
                f"[ln{j}r]scale={_even(width * r)}:{_even(height * r)},crop={width}:{height},setsar=1[ln{j}r2]",
                f"[ln{j}g]scale={_even(width * g)}:{_even(height * g)},crop={width}:{height},setsar=1[ln{j}g2]",
                f"[ln{j}b]setsar=1[ln{j}b2]",
                f"[ln{j}g2][ln{j}b2][ln{j}r2]mergeplanes=0x001020:gbrp,format=yuv420p[{dst}]"]
    if e.kind == "tilt":
        # the near edge grows (a 3D tilt) and comes back: a sine pulse over the
        # window, by the branch's own frame number (perspective has no t)
        # perspective (sense=source): each output corner comes from a source
        # point. Pulling one edge's points inward magnifies that edge: it
        # looks nearer, the frame tilts away from the other side.
        # `in` counts from 1 on the branch's first frame (measured): the pulse
        # is sin(π·k/(n+1)), never 0 inside the window
        nfr = b - a + 2
        d = f"({_f(TILT * amp)}*sin(PI*min(in,{nfr})/{nfr}))"
        c = {"x0": "0", "y0": "0", "x1": "W", "y1": "0", "x2": "0", "y2": "H", "x3": "W", "y3": "H"}
        style = e.style or "left"
        if style == "left":                    # the right edge comes near
            c.update(y1=f"H*{d}", y3=f"H*(1-{d})")
        elif style == "right":                 # the left edge
            c.update(y0=f"H*{d}", y2=f"H*(1-{d})")
        elif style == "up":                    # the bottom edge
            c.update(x2=f"W*{d}", x3=f"W*(1-{d})")
        else:                                  # down: the top edge
            c.update(x0=f"W*{d}", x1=f"W*(1-{d})")
        coords = ":".join(f"{k}='{v}'" for k, v in c.items())
        return [f"[{src}]perspective={coords}:interpolation=linear:eval=frame[{dst}]"]
    if e.kind == "overlay":
        mode = {"screen": "screen", "add": "addition", "normal": "normal"}[e.style or "screen"]
        start = a / fps
        return [f"[{asset_input}:v]trim=duration={dur + 1 / fps:.6f},settb=AVTB,setpts=PTS-STARTPTS+{start:.6f}/TB,"
                f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                f"format=gbrp[ov{j}]",
                f"[{src}]format=gbrp[ovb{j}]",
                f"[ovb{j}][ov{j}]blend=all_mode={mode}:all_opacity={_f(min(1.0, e.opacity * amp))}:shortest=1,"
                f"format=yuv420p[{dst}]"]
    raise ValueError(f"{e.kind} no es un efecto de ventana.")
