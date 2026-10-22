"""Variantes de un edit: el mismo material y la misma canción, otro ritmo.

Salen del análisis del edit de referencia (Levi, 2.2 M vistas, 22/04/2026):
plano mediano de 0.43 s, sin cuantizar a la rejilla (los cortes siguen la
LETRA y los golpes del propio anime), un puente sin texto antes del último
coro y gradación dorado-teal muy saturada. Cada preset decide:

  cortes      "beats" (cada `paso` beats según la sección) o "palabras" (en
              cada palabra de la letra, como el de referencia)
  paso        beats por plano en cada tipo de sección (0.5 = medio beat)
  perfil      curva de velocidad dentro de cada plano (edicion/ritmo.PERFILES)
  acentos     dónde van flash, zoom, sacudida y RGB
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Preset:
    descripcion: str
    cortes: str = "beats"                 # beats | palabras
    paso: dict = field(default_factory=dict)
    paso_subida: float = 0.5              # en las subidas al drop
    perfil: str = "constante"
    velocidad: float = 1.0
    flash: str | None = None              # compases_coro | cortes | drop
    zoom: str | None = None               # cortes_coro | beats_coro
    shake: str | None = None              # golpes_coro | drop
    rgb: str | None = None                # drop | cortes_coro
    texto: str = "letra"                  # letra | off
    look: str = "dorado"
    glow: float = 0.3


PRESETS: dict[str, Preset] = {
    "impacto": Preset(
        "Corte por beat en el coro y cada 2 en el resto, medio beat en la subida, el plano "
        "héroe congelado y en cámara lenta en el drop. Flash en los compases del coro.",
        paso={"intro": 2, "verso": 2, "puente": 4, "coro": 1, "outro": 2}, paso_subida=0.5,
        flash="compases_coro", zoom="cortes_coro", shake="golpes_coro", rgb="drop"),
    "velocidad": Preset(
        "Velocity edit: cada plano arranca en cámara lenta justo en el beat y acelera hacia "
        "el siguiente. Flash en cada corte y zoom en cada beat del coro.",
        paso={"intro": 4, "verso": 2, "puente": 4, "coro": 2, "outro": 4}, paso_subida=1,
        perfil="velocidad", velocidad=1.15, flash="cortes", zoom="beats_coro", shake="drop",
        rgb="cortes_coro", look="vivo", glow=0.25),
    "cine": Preset(
        "Planos largos que respiran (cámara lenta en el medio), sin sacudidas, look de "
        "película y sin letra. Para trailers y tributos.",
        paso={"intro": 4, "verso": 4, "puente": 8, "coro": 2, "outro": 4}, paso_subida=2,
        perfil="respiro", velocidad=0.9, flash="drop", texto="off", look="cine", glow=0.15),
    "letra": Preset(
        "Como el edit de referencia: corta en cada palabra cantada, con la palabra gigante "
        "en el centro y el héroe en el drop.",
        cortes="palabras", paso={"intro": 2, "verso": 2, "puente": 4, "coro": 2, "outro": 2},
        paso_subida=1, flash="drop", zoom="cortes_coro", shake="golpes_coro", rgb="drop"),
}


def lista() -> list[dict]:
    return [{"id": k, "descripcion": p.descripcion} for k, p in PRESETS.items()]
