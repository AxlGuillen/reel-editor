"""Lógica FFmpeg del módulo vertical_convert.

Construye el filtro "split blur background": el clip llena el lienzo 9:16 con
blur de fondo, y el clip original se superpone centrado en su aspect ratio.
"""
import config
from core import ffmpeg_runner, job_manager
from modules.vertical_convert.schema import VerticalConvertParams

W = config.OUTPUT_WIDTH
H = config.OUTPUT_HEIGHT


def _even(value: float) -> int:
    """Redondea a entero par (requerido por libx264)."""
    return max(2, int(round(value / 2)) * 2)


# Placa del marcador (HUD): estilo del marco.
HUD_BORDER = 4            # px de borde blanco alrededor del recorte
HUD_RADIUS = 18           # px de radio de las esquinas (placa incluida)
HUD_SHADOW_MARGIN = 24    # px de margen alrededor para que el blur no se corte
HUD_SHADOW_SIGMA = 7      # suavidad de la sombra
HUD_SHADOW_DY = 6         # desplazamiento vertical de la sombra (efecto 3D)
HUD_SHADOW_ALPHA = 0.55   # opacidad de la sombra


def _rounded_mask_expr(radius: int) -> str:
    """Expresión geq (0-255) de un rectángulo redondeado antialiasado del
    tamaño del frame (W×H). Distancia al rectángulo interior reducido en
    `radius`, recortada a 1 px de transición."""
    r = radius
    dx = f"max(abs(X+0.5-W/2)-(W/2-{r}),0)"
    dy = f"max(abs(Y+0.5-H/2)-(H/2-{r}),0)"
    return f"255*clip({r}+0.5-sqrt({dx}*{dx}+{dy}*{dy}),0,1)"


def _hud_plate(params: VerticalConvertParams, src: str) -> list[str]:
    """Fragmentos de filter_complex que producen [hud_plate] y [hud_shadow].

    Truco de rendimiento: la máscara redondeada y la sombra son ESTÁTICAS, así
    que se calculan sobre UN solo frame (trim=end_frame=1) y los filtros de
    composición (alphamerge/overlay, con repeatlast por defecto) reutilizan
    ese frame para todo el video. geq, que es carísimo por frame, corre una
    sola vez.
    """
    hud_px = _even(W * params.hud_scale / 100)
    fx, fy = params.hud_left / 100, params.hud_top / 100
    fw = (params.hud_right - params.hud_left) / 100
    fh = (params.hud_bottom - params.hud_top) / 100
    b, m = HUD_BORDER, HUD_SHADOW_MARGIN
    mask = _rounded_mask_expr(HUD_RADIUS)
    return [
        # Recorte + escala + borde blanco (opaco, con alpha para el merge).
        (f"{src}crop=iw*{fw:.4f}:ih*{fh:.4f}:iw*{fx:.4f}:ih*{fy:.4f},"
         f"scale={hud_px}:-2,pad=iw+{2 * b}:ih+{2 * b}:{b}:{b}:white,"
         f"format=rgba,split[hud_rgb][hud_m0]"),
        # Máscara redondeada (1 frame, gris = alpha).
        f"[hud_m0]trim=end_frame=1,format=gray,geq=lum='{mask}',split[hud_mask][hud_sm0]",
        # Placa: recorte con las esquinas recortadas por la máscara.
        "[hud_rgb][hud_mask]alphamerge[hud_plate]",
        # Sombra: la misma silueta, con margen, blureada, negra y translúcida.
        (f"[hud_sm0]pad=iw+{2 * m}:ih+{2 * m}:{m}:{m}:black,"
         f"gblur=sigma={HUD_SHADOW_SIGMA},split[hud_sa][hud_sb]"),
        (f"[hud_sa]format=rgba[hud_sc];[hud_sc][hud_sb]alphamerge,"
         f"colorchannelmixer=rr=0:gg=0:bb=0:aa={HUD_SHADOW_ALPHA}[hud_shadow]"),
    ]


def build_filter_complex(params: VerticalConvertParams,
                         src: str = "[0:v]", out: str = "[out]") -> str:
    """Arma el filter_complex a partir de los parámetros validados.

    `src`/`out` permiten encadenar esta cadena dentro de un filter_complex
    mayor (lo usa reel_express para fusionar vertical + watermark en una sola
    pasada). OJO: `src` se consume dos veces (fondo y clip principal), así que
    debe ser un stream de entrada (p. ej. "[0:v]"), no un label intermedio.
    """
    sigma = params.blur_intensity
    # bg_brightness es un multiplicador (0.3-1.0); colorchannelmixer lo aplica
    # de forma multiplicativa igual que CSS brightness(), evitando el clipping
    # negro que produce eq=brightness (que es aditivo).
    bm = round(params.bg_brightness, 3)

    # Capa principal: escalada por ancho del lienzo y zoom configurable.
    fg_width = _even(W * params.main_clip_scale)

    # Posición vertical del clip principal: preset + ajuste fino (px).
    off_px = round(params.main_clip_offset / 100 * H)
    if params.main_clip_position == "top":
        overlay_y = str(off_px)
    elif params.main_clip_position == "bottom":
        overlay_y = f"H-h{off_px:+d}" if off_px else "H-h"
    else:
        overlay_y = f"(H-h)/2{off_px:+d}" if off_px else "(H-h)/2"

    if sigma >= 8:
        # gblur es el filtro más caro de la cadena y su costo escala con los
        # píxeles. Con blur fuerte, blurear a 1/4 de resolución (sigma/4) y
        # reescalar da un resultado indistinguible (SSIM ~0.994 medido) por una
        # fracción del costo. Con blur suave no se usa: el reescalado se vería.
        w4, h4 = _even(W / 4), _even(H / 4)
        bg = (
            f"{src}scale={w4}:{h4}:force_original_aspect_ratio=increase,"
            f"crop={w4}:{h4},"
            f"gblur=sigma={sigma / 4:.2f},"
            f"scale={W}:{H}:flags=bilinear,"
            f"colorchannelmixer=rr={bm}:gg={bm}:bb={bm}[bg]"
        )
    else:
        bg = (
            f"{src}scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},"
            f"gblur=sigma={sigma},"
            f"colorchannelmixer=rr={bm}:gg={bm}:bb={bm}[bg]"
        )

    parts = [bg]

    if params.enhance_intensity > 0:
        i = params.enhance_intensity / 100
        # Curva asimétrica: sombras casi intactas, medios y brillos levantados.
        shadow    = round(0.25 - i * 0.02, 4)   # toca poco las sombras
        midtone   = round(0.50 + i * 0.04, 4)   # levanta los medios
        highlight = round(0.75 + i * 0.10, 4)   # levanta bien los brillos
        brightness = round(i * 0.04, 3)          # boost general de exposición
        sat       = round(1.0 + i * 0.5, 3)
        sharp     = round(i * 0.5, 3)
        parts.append(f"{src}scale={fg_width}:-2[fg_raw]")
        parts.append(
            f"[fg_raw]curves=all='0/0 0.25/{shadow} 0.5/{midtone} 0.75/{highlight} 1/1',"
            f"eq=brightness={brightness}:saturation={sat},"
            f"unsharp=lx=3:ly=3:la={sharp}:cx=3:cy=3:ca=0[fg]"
        )
    else:
        parts.append(f"{src}scale={fg_width}:-2[fg]")

    if params.hud:
        # Marcador (HUD): tercer consumo del clip fuente. Se recorta la región
        # (en fracciones de iw/ih: independiente de la resolución del clip),
        # se escala al ancho pedido y se presenta como placa redondeada con
        # borde blanco y sombra suave (ver _hud_plate). Va ENCIMA del clip
        # principal, centrada.
        parts.append(f"[bg][fg]overlay=(W-w)/2:{overlay_y}[vmain]")
        parts.extend(_hud_plate(params, src))
        hud_y = params.hud_pos_y / 100
        # La sombra es más grande que la placa (margen de blur) y va desplazada
        # hacia abajo; la placa queda centrada dentro de ese margen.
        m = HUD_SHADOW_MARGIN
        parts.append(
            f"[vmain][hud_shadow]overlay="
            f"(W-w)/2:(H-h+{2 * m})*{hud_y:.4f}-{m}+{HUD_SHADOW_DY}[vsh]")
        parts.append(f"[vsh][hud_plate]overlay=(W-w)/2:(H-h)*{hud_y:.4f}{out}")
    else:
        parts.append(f"[bg][fg]overlay=(W-w)/2:{overlay_y}{out}")
    return ";".join(parts)


def build_command(input_path: str, output_path: str,
                  params: VerticalConvertParams) -> list[str]:
    """Construye el comando FFmpeg completo."""
    return [
        config.FFMPEG_PATH,
        "-y",
        "-i", input_path,
        "-filter_complex", build_filter_complex(params),
        "-map", "[out]",
        "-map", "0:a?",
        *ffmpeg_runner.video_encode_flags(),
        "-c:a", "aac",
        output_path,
    ]


def process(job_id: str, input_path: str, output_path: str,
            params: VerticalConvertParams) -> None:
    """Ejecuta el job completo (bloqueante). Actualiza job_manager en cada paso."""
    try:
        duration = ffmpeg_runner.probe_duration(input_path)
        command = build_command(input_path, output_path, params)
        ffmpeg_runner.run(command, job_id, total_duration=duration)
        job_manager.update_job(job_id, status="done", progress=100)
    except Exception as exc:  # noqa: BLE001 - reportamos cualquier fallo al job
        job_manager.update_job(job_id, status="error", error=str(exc))
