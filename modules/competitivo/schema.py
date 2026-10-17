"""Parámetros del módulo competitivo (validación de las dos fases)."""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass

import config
from modules.competitivo import engine, intro, kills, layout, salida

COLORES = set(layout.COLOR)
MODOS = {"revisar", "auto"}
INTRO_MODOS = {"encima", "aparte", "off"}
VOLUMEN_JUEGO_DEFECTO = round(layout.DUCK_VOL * 100)   # % del juego mientras habla la voz
# Campos que /finish hereda de la fase 1 si no llegan (render e intro).
CAMPOS_RENDER = ("zoom", "color", "watermark", "marca_alto")
CAMPOS_SALIDA = ("carpeta_dia", "partido", "titulo")
CAMPOS_INTRO = ("intro_modo", "intro_audio_url", "mapa", "fase", "equipo_azul", "equipo_rojo",
                "logo_azul_url", "logo_rojo_url", "serie_marcador", "marca_4xl", "intro_volumen_juego",
                "placa_max_s")
_GAME_ID = re.compile(r"^\d{15,20}$")


def _bool(form, name: str, default: bool = False) -> bool:
    raw = form.get(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "on", "yes", "si", "sí"}


def _num(form, name: str, default, lo, hi, cast=int):
    raw = form.get(name)
    if raw in (None, ""):
        return default
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        raise ValueError(f"'{name}' tiene que ser un número.")
    if not lo <= value <= hi:
        raise ValueError(f"'{name}' fuera de rango ({lo}–{hi}).")
    return value


def _marca(form, name: str = "watermark") -> str:
    """Nombre de un PNG de assets/watermarks ("" = sin watermark)."""
    nombre = os.path.basename((form.get(name) or "").strip())
    if nombre and not os.path.isfile(os.path.join(config.WATERMARKS_FOLDER, nombre)):
        raise ValueError(f"No existe el watermark «{nombre}» en assets/watermarks.")
    return nombre


def _render(form, base: "CompetitivoParams | None" = None) -> dict:
    """Parámetros del render: en /finish pueden cambiarse sin repetir la fase 1."""
    return {
        "zoom": _num(form, "zoom", base.zoom if base else 1.3, 0.8, 2.2, float),
        "color": _color(form, base.color if base else "vivo"),
        "watermark": _marca(form) if form.get("watermark") is not None or not base else base.watermark,
        "marca_alto": _num(form, "marca_alto", base.marca_alto if base else layout.MARCA_ALTO_DEFECTO, 80, 400),
    }


def _url(form, name: str, default: str = "") -> str:
    """Link http(s) o vacío."""
    raw = form.get(name)
    if raw is None:
        return default
    url = str(raw).strip()
    if url and not re.match(r"^https?://\S+$", url):
        raise ValueError(f"'{name}' tiene que ser un link http(s).")
    return url


def _texto(form, name: str, max_len: int, default: str = "") -> str:
    """Texto de una línea para la portada (espacios colapsados, sin caracteres de control)."""
    raw = form.get(name)
    if raw is None:
        return default
    texto = " ".join(str(raw).split())
    # Controles (C0/C1) y caracteres de formato invisibles (p. ej. U+202E, que da
    # vuelta el texto, o U+200B): no tienen nada que hacer en una portada.
    if any(unicodedata.category(c) in ("Cc", "Cf", "Co", "Cs", "Zl", "Zp") for c in texto):
        raise ValueError(f"'{name}' tiene caracteres no válidos.")
    if len(texto) > max_len:
        raise ValueError(f"'{name}' es demasiado largo (máximo {max_len} caracteres).")
    return texto


def _intro(form, base=None) -> dict:
    """Intro con voz y portada. En /finish, lo que no llega se hereda de la fase 1."""
    def b(campo, defecto):
        return getattr(base, campo) if base else defecto

    modo = (form.get("intro_modo") or b("intro_modo", "encima")).strip().lower()
    if modo not in INTRO_MODOS:
        raise ValueError("'intro_modo' tiene que ser 'encima', 'aparte' u 'off'.")
    return {
        "intro_modo": modo,
        "intro_audio_url": _url(form, "intro_audio_url", b("intro_audio_url", "")),
        "mapa": _num(form, "mapa", b("mapa", 1), 1, 5),
        "fase": _texto(form, "fase", 40, b("fase", "")),
        "equipo_azul": _texto(form, "equipo_azul", 30, b("equipo_azul", "")),
        "equipo_rojo": _texto(form, "equipo_rojo", 30, b("equipo_rojo", "")),
        "logo_azul_url": _url(form, "logo_azul_url", b("logo_azul_url", "")),
        "logo_rojo_url": _url(form, "logo_rojo_url", b("logo_rojo_url", "")),
        "serie_marcador": _texto(form, "serie_marcador", 12, b("serie_marcador", "")),
        "marca_4xl": _bool(form, "marca_4xl", b("marca_4xl", False)),
        "intro_volumen_juego": _num(form, "intro_volumen_juego",
                                    b("intro_volumen_juego", VOLUMEN_JUEGO_DEFECTO), 0, 100),
        "placa_max_s": _num(form, "placa_max_s", b("placa_max_s", intro.PLACA_MAX_S), 1, 15, float),
    }


def _nombre(form, name: str, max_len: int, default: str) -> str:
    """Nombre de carpeta o de archivo: el texto, limpio para Windows."""
    raw = _texto(form, name, max_len, default)
    limpio = salida.limpiar(raw)
    if raw and not limpio:
        raise ValueError(f"'{name}' no tiene ningún carácter válido para un nombre de archivo.")
    return limpio


def _salida(form, base=None) -> dict:
    """Carpeta de salida por día y partido (opcional). En /finish, lo que no
    llega se hereda de la fase 1."""
    def b(campo):
        return getattr(base, campo) if base else ""

    datos = {
        "carpeta_dia": _nombre(form, "carpeta_dia", 80, b("carpeta_dia")),
        "partido": _nombre(form, "partido", 80, b("partido")),
        "titulo": _nombre(form, "titulo", 120, b("titulo")),
    }
    if bool(datos["carpeta_dia"]) != bool(datos["partido"]):
        raise ValueError("'carpeta_dia' y 'partido' van juntos: mandá los dos (o ninguno).")
    return datos


def _color(form, default: str) -> str:
    color = (form.get("color") or default).strip().lower()
    if color not in COLORES:
        raise ValueError(f"'color' tiene que ser uno de {sorted(COLORES)}.")
    return color


@dataclass
class CompetitivoParams:
    """Fase 1 (/process): qué partidas, de qué VOD y con qué reglas."""
    game_ids: list[str]
    vod_url: str
    modo: str = "revisar"            # revisar: pausa para el ancla y los clips; auto: sigue solo
    # Duración POR MAPA (ver kills.objetivo_s): minutos × 4 s entre el mínimo y el máximo.
    duracion_max_s: int = 180
    duracion_min_s: int = 90
    duracion_auto: bool = True
    max_por_partida: int = 8          # el presupuesto de tiempo manda
    min_kills: int = 2
    incluir_picks: bool = False
    incluir_primera_sangre: bool = False
    pre_s: float = 10
    post_s: float = 5
    zoom: float = 1.3
    color: str = "vivo"
    watermark: str = ""
    marca_alto: int = layout.MARCA_ALTO_DEFECTO
    # Intro con voz y portada (ver intro.py). La voz llega hecha, como link de un mp3.
    intro_modo: str = "encima"         # encima | aparte | off
    intro_audio_url: str = ""          # vacío → sin intro (aunque el modo no sea off)
    mapa: int = 1
    fase: str = ""                     # "PLAY-IN · DÍA 2"
    equipo_azul: str = ""              # nombre completo; vacío → código del feed
    equipo_rojo: str = ""
    logo_azul_url: str = ""
    logo_rojo_url: str = ""
    serie_marcador: str = ""           # "1-0"
    marca_4xl: bool = False            # el watermark, chico y translúcido, en la portada
    intro_volumen_juego: int = VOLUMEN_JUEGO_DEFECTO   # "encima": el juego (%) mientras habla la voz
    placa_max_s: float = intro.PLACA_MAX_S             # "encima": lo máximo que se ve la placa
    # Salida organizada (ver salida.py): <carpeta_dia>/<partido>/Mapa N - <titulo>.mp4
    carpeta_dia: str = ""
    partido: str = ""
    titulo: str = ""

    @classmethod
    def from_form(cls, form) -> "CompetitivoParams":
        crudo = form.get("game_ids") or ""
        if isinstance(crudo, (list, tuple)):
            ids = [str(x).strip() for x in crudo]
        else:
            ids = [x for x in re.split(r"[\s,;]+", str(crudo)) if x]
        if not ids:
            raise ValueError("Faltan los gameId de la serie (uno por partida, en orden).")
        malos = [x for x in ids if not _GAME_ID.match(x)]
        if malos:
            raise ValueError(f"gameId inválido: {malos[0]}. Son números largos, p. ej. 115660588583171787.")
        if len(set(ids)) != len(ids):
            raise ValueError("Hay gameId repetidos.")
        if len(ids) > 7:
            raise ValueError("Máximo 7 partidas por serie.")
        vod = (form.get("vod_url") or "").strip()
        if not re.match(r"^https?://", vod):
            raise ValueError("Falta 'vod_url': el link del VOD de Ibai en YouTube.")
        modo = (form.get("modo") or "revisar").strip().lower()
        if modo not in MODOS:
            raise ValueError("'modo' tiene que ser 'revisar' o 'auto'.")
        dmin = _num(form, "duracion_min_s", 90, 0, 300)
        dmax = _num(form, "duracion_max_s", 180, 20, 300)
        if dmin > dmax:
            raise ValueError(f"'duracion_min_s' ({dmin}) no puede ser mayor que 'duracion_max_s' ({dmax}).")
        return cls(
            game_ids=ids, vod_url=vod, modo=modo,
            duracion_max_s=dmax, duracion_min_s=dmin,
            duracion_auto=_bool(form, "duracion_auto", True),
            max_por_partida=_num(form, "max_por_partida", 8, 1, 12),
            min_kills=_num(form, "min_kills", 2, 1, 5),
            incluir_picks=_bool(form, "incluir_picks"),
            incluir_primera_sangre=_bool(form, "incluir_primera_sangre"),
            pre_s=_num(form, "pre_s", 10, 0, 20, float),
            post_s=_num(form, "post_s", 5, 0, 15, float),
            **_render(form),
            **_intro(form),
            **_salida(form),
        )

    @property
    def reglas(self) -> kills.Reglas:
        return kills.Reglas(
            min_kills=self.min_kills, incluir_picks=self.incluir_picks,
            incluir_primera_sangre=self.incluir_primera_sangre, pre_s=self.pre_s,
            post_s=self.post_s, duracion_max_s=self.duracion_max_s,
            duracion_min_s=self.duracion_min_s, duracion_auto=self.duracion_auto,
            max_por_partida=self.max_por_partida,
        )


@dataclass
class FinishParams:
    """Fase 2 (/finish): ancla por partida, clips elegidos y retoques del render."""
    anclas: dict[str, float]          # gameId → segundo del VOD de la primera kill
    clips: list[str] | None           # ids de las peleas; None = la selección propuesta
    zoom: float
    color: str
    watermark: str
    marca_alto: int
    intro_modo: str = "encima"
    intro_audio_url: str = ""
    mapa: int = 1
    fase: str = ""
    equipo_azul: str = ""
    equipo_rojo: str = ""
    logo_azul_url: str = ""
    logo_rojo_url: str = ""
    serie_marcador: str = ""
    marca_4xl: bool = False
    intro_volumen_juego: int = VOLUMEN_JUEGO_DEFECTO
    placa_max_s: float = intro.PLACA_MAX_S
    carpeta_dia: str = ""
    partido: str = ""
    titulo: str = ""

    @classmethod
    def desde(cls, base: CompetitivoParams) -> "FinishParams":
        """Todo heredado de la fase 1: lo usa el modo auto, que llama a finish()
        sin pasar por el form (así no se pierde ningún campo nuevo)."""
        return cls(anclas={}, clips=None,
                   **{c: getattr(base, c) for c in CAMPOS_RENDER + CAMPOS_INTRO + CAMPOS_SALIDA})

    @classmethod
    def from_form(cls, form, base: CompetitivoParams) -> "FinishParams":
        anclas = {}
        crudo = form.get("anchors") or form.get("anclas") or "{}"
        try:
            datos = json.loads(crudo) if isinstance(crudo, str) else dict(crudo)
        except (TypeError, ValueError):
            raise ValueError("'anchors' tiene que ser un JSON {gameId: segundo}.")
        for gid, valor in datos.items():
            if gid not in base.game_ids:
                raise ValueError(f"El ancla {gid} no es de esta serie.")
            try:
                anclas[gid] = engine.parse_vod_time(valor)
            except (TypeError, ValueError):
                raise ValueError(f"Ancla inválida para {gid}: usá segundos (4935) o 1:22:15.")
        clips = form.get("clips")
        if clips not in (None, ""):
            try:
                clips = json.loads(clips) if isinstance(clips, str) else list(clips)
            except (TypeError, ValueError):
                raise ValueError("'clips' tiene que ser una lista JSON de ids.")
            if not isinstance(clips, list) or not all(isinstance(c, str) for c in clips):
                raise ValueError("'clips' tiene que ser una lista de ids (texto).")
            if not clips:
                raise ValueError("Elegí al menos un clip.")
        else:
            clips = None
        return cls(anclas=anclas, clips=clips, **_render(form, base), **_intro(form, base),
                   **_salida(form, base))
