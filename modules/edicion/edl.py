"""Lista de decisiones de edición (EDL): qué tramo de qué archivo va en qué
momento de la salida, a qué velocidad, y qué acentos y letreros lleva.

La arman los planificadores (competitivo/edit.py, anime_edit/plan.py) como
funciones puras y la ejecuta render.py. Ningún planificador toca FFmpeg; el
render no decide nada: así cada lado se prueba por separado.

Unidades: segundos. Los tiempos de `Evento` y `Texto` son de la SALIDA; los de
`Pieza` son de su archivo fuente.
"""
from __future__ import annotations

from dataclasses import dataclass, field

CONGELADO_EPS = 1e-3          # una pieza con menos fuente que esto es un cuadro congelado
TIPOS_EVENTO = ("flash", "zoom", "shake", "rgb")
ESTILOS_TEXTO = ("palabra", "kill", "multikill", "titulo", "sub")


@dataclass
class Pieza:
    """Un tramo contiguo de la fuente a velocidad constante."""
    desde: float                  # s en la fuente
    hasta: float                  # == desde → cuadro congelado durante `dur`
    dur: float                    # s en la salida

    @property
    def congelado(self) -> bool:
        return self.hasta - self.desde < CONGELADO_EPS

    @property
    def velocidad(self) -> float:
        return 0.0 if self.congelado else (self.hasta - self.desde) / self.dur


@dataclass
class Segmento:
    """Un plano: un archivo y sus piezas, una detrás de otra en la salida."""
    fuente: str
    piezas: list[Pieza]
    etiqueta: str = ""
    # Filtro de geometría propio del plano (layout, recorte): lo pone el
    # planificador; vacío = el escalado por defecto del render.
    vf: str = ""
    # Hace falta interpolar (RIFE) porque alguna pieza va muy lenta.
    interpolar: bool = False

    @property
    def dur(self) -> float:
        return sum(p.dur for p in self.piezas)


@dataclass
class Evento:
    """Un acento en la salida: flash, golpe de zoom, sacudida o RGB separado."""
    t: float
    tipo: str
    intensidad: float = 1.0       # 0–1.5 (se multiplica por la intensidad global)
    dur: float = 0.18


@dataclass
class Texto:
    desde: float
    hasta: float
    texto: str
    estilo: str = "palabra"
    sub: str = ""                 # segunda línea (el campeón debajo del jugador)
    color: str = ""               # #RRGGBB; vacío = el del estilo


@dataclass
class Edl:
    segmentos: list[Segmento]
    ancho: int = 1080
    alto: int = 1920
    fps: int = 60
    eventos: list[Evento] = field(default_factory=list)
    textos: list[Texto] = field(default_factory=list)
    musica: str | None = None
    musica_desde: float = 0.0     # s de la canción donde arranca la salida
    volumen_musica: float = 1.0
    volumen_original: float = 0.0 # 0 = solo música
    look: str = "neutro"
    lut: str = ""                 # archivo de assets/luts ("" = sin LUT)
    glow: float = 0.0             # 0–1
    viñeta: bool = False
    marca: str | None = None      # PNG del watermark
    marca_alto: int = 150
    fade_salida: float = 0.6      # fundido de la música al final

    @property
    def dur(self) -> float:
        return sum(s.dur for s in self.segmentos)

    def validar(self) -> None:
        """ValueError legible si la EDL no se puede renderizar."""
        if not self.segmentos:
            raise ValueError("La edición no tiene ningún plano.")
        for i, s in enumerate(self.segmentos, 1):
            if not s.piezas:
                raise ValueError(f"El plano {i} no tiene piezas.")
            for p in s.piezas:
                if p.dur <= 0:
                    raise ValueError(f"El plano {i} tiene una pieza sin duración.")
                if p.hasta < p.desde - CONGELADO_EPS:
                    raise ValueError(f"El plano {i} tiene una pieza al revés ({p.desde} → {p.hasta}).")
        for e in self.eventos:
            if e.tipo not in TIPOS_EVENTO:
                raise ValueError(f"Evento desconocido: {e.tipo!r}.")
        for t in self.textos:
            if t.estilo not in ESTILOS_TEXTO:
                raise ValueError(f"Estilo de texto desconocido: {t.estilo!r}.")
            if t.hasta <= t.desde:
                raise ValueError(f"El texto «{t.texto}» no dura nada.")

    def a_dict(self) -> dict:
        """Resumen para el status y el info.json (sin rutas largas repetidas)."""
        return {
            "dur": round(self.dur, 3), "planos": len(self.segmentos), "fps": self.fps,
            "ancho": self.ancho, "alto": self.alto, "musica_desde": round(self.musica_desde, 3),
            "eventos": [{"t": round(e.t, 3), "tipo": e.tipo, "intensidad": round(e.intensidad, 2)}
                        for e in self.eventos],
            "textos": [{"desde": round(t.desde, 3), "hasta": round(t.hasta, 3), "texto": t.texto,
                        "estilo": t.estilo, "sub": t.sub} for t in self.textos],
            "segmentos": [{"etiqueta": s.etiqueta, "dur": round(s.dur, 3),
                           "piezas": [[round(p.desde, 3), round(p.hasta, 3), round(p.dur, 3)]
                                      for p in s.piezas]} for s in self.segmentos],
        }
