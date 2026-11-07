"""The analysis layout of «La TF explicada» (1080×1920), as a shot's `vf`
for the editing engine (starts at [0:v], last chain left open):

  y 22–133     the scoreboard (broadcast 510,6 900×96)
  y 150–1000   the gameplay, a crop of the broadcast scaled to 1080×850
  y 1000–1240  free: the subtitles go here
  y 1240–1920  the band: the real minimap on the left (650×632 at 15,1255),
               the deck's cards on the right

Gold lines frame the gameplay and the band. `minimap=False` when the
broadcast covers it (crowd shot, player cam); `fit=True` shows the whole
broadcast frame 1080 wide (a celebration). Broadcast coordinates are of a
1920×1080 source (the LoL Worlds 2026 broadcast)."""
from __future__ import annotations

GP_TOP, GP_W, GP_H = 150, 1080, 850
BAND_TOP = 1240
SCOREBOARD = (510, 6, 900, 96)        # x, y, w, h in the broadcast
MINIMAP = (1636, 804, 284, 276)
MINIMAP_OUT = (15, 1255, 650, 632)    # x, y, w, h in the output
BAND_COLOR = "0x0E0B10"
LINE_COLOR = "0xF2C14E"


def layout(x0: int, y0: int, w: int, h: int, minimap: bool = True, fit: bool = False) -> str:
    """The `vf` of a shot whose gameplay is the broadcast crop (x0, y0, w, h)."""
    sx, sy, sw, sh = SCOREBOARD
    mx, my, mw, mh = MINIMAP
    ox, oy, ow, oh = MINIMAP_OUT
    gp = (f"[lc]scale={GP_W}:608,pad={GP_W}:{GP_H}:0:{(GP_H - 608) // 2}:color=black[gp];" if fit else
          f"[lc]crop={w}:{h}:{x0}:{y0},scale={GP_W}:{GP_H}:flags=lanczos[gp];")
    sb = "" if fit else f"[lb]crop={sw}:{sh}:{sx}:{sy},scale=1040:111[sc];"
    return (
        ("[0:v]split=4[la][lb][lc][le];" if minimap else "[0:v]split=3[la][lb][lc];")
        + "[la]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,gblur=sigma=8,"
          "eq=brightness=-0.32,scale=1080:1920[bg];"
        + sb + gp
        + (f"[le]crop={mw}:{mh}:{mx}:{my},scale={ow}:{oh}[mm];" if minimap else "")
        + f"[bg]drawbox=x=0:y={BAND_TOP}:w=1080:h={1920 - BAND_TOP}:color={BAND_COLOR}:t=fill,"
          f"drawbox=x=0:y={BAND_TOP - 2}:w=1080:h=3:color={LINE_COLOR}:t=fill,"
          f"drawbox=x=0:y={GP_TOP + GP_H}:w=1080:h=3:color={LINE_COLOR}:t=fill[b0];"
        + ("[lb]nullsink;[b0]null[b1];" if fit else "[b0][sc]overlay=20:22[b1];")
        + (f"[b1][gp]overlay=0:{GP_TOP}[b2];[b2][mm]overlay={ox}:{oy}" if minimap
           else f"[b1][gp]overlay=0:{GP_TOP}"))


def to_output(point: tuple[float, float], crop: tuple[int, int, int, int]) -> tuple[int, int]:
    """Where a broadcast point (x, y) lands in the output, in a shot with this
    gameplay crop: where a deck draws its ring or arrow."""
    x, y = point
    x0, y0, w, h = crop
    return round((x - x0) * GP_W / w), round(GP_TOP + (y - y0) * GP_H / h)


def minimap_to_output(point: tuple[float, float]) -> tuple[int, int]:
    """A point of the broadcast's minimap (px of the 284×276 crop) in the band."""
    x, y = point
    ox, oy, ow, oh = MINIMAP_OUT
    return round(ox + x * ow / MINIMAP[2]), round(oy + y * oh / MINIMAP[3])
