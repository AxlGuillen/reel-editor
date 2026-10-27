"""Parámetros del módulo competitivo (validación de las dos fases)."""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field

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


def creator_from(form) -> dict:
    """creator_vod_url, creator_cam ("x,y,w,h" in 1920x1080) and creator_delta_s."""
    from modules.competitivo.creator import parse_box
    url = (form.get("creator_vod_url") or "").strip()
    if url and not re.match(r"^https?://", url):
        raise ValueError("'creator_vod_url' es el link del VOD del creador (http/https).")
    cam = None
    if form.get("creator_cam"):
        try:
            cam = parse_box(form["creator_cam"])
        except ValueError:
            raise ValueError("'creator_cam' es x,y,w,h de su webcam en 1920x1080 (p. ej. 1480,760,420,300).")
    delta = None
    if form.get("creator_delta_s") not in (None, ""):
        try:
            delta = float(form["creator_delta_s"])
        except ValueError:
            raise ValueError("'creator_delta_s' es un número de segundos.")
    if (cam or delta is not None) and not url:
        raise ValueError("Falta 'creator_vod_url'.")
    return {"creator_vod_url": url, "creator_cam": cam, "creator_delta_s": delta}


def moments_from(raw, base: CompetitivoParams) -> list:
    """moments: JSON [{"clock": "1:45", "dur": 6, "label": "robo del red", "game_id": …}]:
    moments without a kill for the edit (the scoreboard's clock; dur 2–20 s)."""
    if raw in (None, ""):
        return []
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        raise ValueError("'moments' va en JSON: [{\"clock\": \"1:45\", \"dur\": 6, \"label\": \"robo del red\"}].")
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or len(items) > 10:
        raise ValueError("'moments' es una lista de hasta 10 momentos.")
    out = []
    for it in items:
        if not isinstance(it, dict) or not re.fullmatch(r"\d{1,2}:\d{2}", str(it.get("clock", "")).strip()):
            raise ValueError("'moments': cada uno lleva 'clock' (el reloj del marcador, mm:ss).")
        try:
            dur = float(it.get("dur", 6))
        except (TypeError, ValueError):
            raise ValueError("'moments': 'dur' es un número de segundos.")
        if not 2 <= dur <= 20:
            raise ValueError("'moments': 'dur' va de 2 a 20 s.")
        gid = str(it.get("game_id") or base.game_ids[0])
        if gid not in base.game_ids:
            raise ValueError(f"'moments': la partida {gid} no es de esta serie.")
        out.append({"clock": str(it["clock"]).strip(), "dur": dur, "label": str(it.get("label") or "momento")[:40],
                    "game_id": gid})
    return out


def kill_overrides_from(raw) -> list:
    """kill_overrides: JSON [{"kill": "1-10#2" | "clock": "21:41", "mode": "zoom"|"full",
    "focus": [u, v]}] (the shape; which kills they are is checked against the edit)."""
    if raw in (None, ""):
        return []
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        raise ValueError("'kill_overrides' va en JSON: [{\"kill\": \"1-10#2\", \"mode\": \"full\"}].")
    if isinstance(items, dict):
        items = [items]
    out = []
    for it in items if isinstance(items, list) else [None]:
        if not isinstance(it, dict) or not (it.get("kill") or it.get("clock")):
            raise ValueError("'kill_overrides': cada corrección lleva 'kill' (\"1-10#2\") o 'clock' (\"21:41\").")
        if it.get("clock") and not re.fullmatch(r"\d{1,2}:\d{2}", str(it["clock"]).strip()):
            raise ValueError(f"'kill_overrides': reloj inválido {it['clock']!r} (mm:ss).")
        if it.get("mode") not in (None, "zoom", "full"):
            raise ValueError("'kill_overrides': 'mode' es zoom o full.")
        f = it.get("focus")
        if f is not None and not (isinstance(f, list) and len(f) == 2
                                  and all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in f)):
            raise ValueError("'kill_overrides': 'focus' es [u, v] entre 0 y 1 (sobre la miniatura).")
        if f is None and it.get("mode") is None:
            raise ValueError("'kill_overrides': falta 'mode' o 'focus'.")
        out.append({k: (str(v).strip() if k in ("kill", "clock") else v) for k, v in it.items()
                    if k in ("kill", "clock", "mode", "focus")})
    return out


def _recipes(raw, valid) -> tuple:
    """recipes: "auto" (all) or a list (JSON or comma separated)."""
    if raw in (None, "", "auto"):
        return ()
    if isinstance(raw, str):
        try:
            items = json.loads(raw) if raw.strip().startswith("[") else raw.split(",")
        except ValueError:
            raise ValueError("'recipes' es 'auto' o una lista: ramp,freeze_zoom,…")
    else:
        items = list(raw)
    items = [str(x).strip().lower() for x in items if str(x).strip()]
    bad = [x for x in items if x not in valid]
    if bad or not items:
        raise ValueError(f"Receta desconocida: {bad[0] if bad else ''!r}. Opciones: auto o {', '.join(valid)}.")
    return tuple(dict.fromkeys(items))


def _subs_style(p) -> dict:
    """The "invert" fields of a SubtitlesParams (Ibai's subtitles in the edit)."""
    return {"inv_size": p.inv_size, "inv_y": p.inv_y, "inv_direction": p.inv_direction,
            "inv_words": p.inv_words, "font": p.font}


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
    transcript: bool = False           # also transcribe the game(s) (post-game included) once phase 1 is done

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
            raise ValueError("Falta 'vod_url': el link del VOD de Ibai (YouTube o Twitch).")
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
            transcript=_bool(form, "transcript"),
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
class CardParams:
    """The player cards of /finish: at the start of a play the clip stops and a card appears
    over each chosen player's champion (card_plan.py, cards.py). `players` is a free field:
    the names to highlight, as the request writes them ("Canyon, Rookie"); empty = no cards."""
    players: list = field(default_factory=list)
    scope: str = "kills"              # kills: only in the fights where that player made a kill | all: any clip of the game
    back: str = "auto"                # auto | pareja | campeon | jugador | partida
    style: str = "splash"             # splash | luxury | plain
    mode: str = "freeze"              # freeze | slow
    dur_s: float = 2.0                # how long the clip stops (1.2–4)
    lead_s: float = 2.0               # the freeze is looked for this long before the fight's first kill
    scale: float = 1.0                # a multiplier of the card's size
    pos: dict = field(default_factory=dict)   # {"Canyon": "bar:2" | [x, y]} where the detector did not decide
    preview: bool = False             # do not render: only the frame of each clip with its bars, to check them
    photos: dict = field(default_factory=dict)   # {"Canyon": "https://…/canyon.png"}: the photo by link
    data: dict = field(default_factory=dict)     # {"Canyon": {...}}: anything of the card, see CARD_FIELDS


CARD_BACKS = ("auto", "pareja", "campeon", "jugador", "partida")
CARD_STYLES = ("splash", "luxury", "plain")
# What `card_data` can set per player (all optional): photo / art / icon are links; the rest are texts. `back`
# replaces the numbers of the back: {"title": "...", "caption": "...", "rows": [{"big": "4-10", "label": "V-D", "bar": 0.3}]}.
CARD_FIELDS = ("photo", "art", "icon", "team", "side", "role", "champion", "title", "back")


def _json_map(form, key: str) -> dict:
    raw = form.get(key)
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except ValueError:
        raise ValueError(f"'{key}' va en JSON: {{\"Canyon\": ...}}.")
    if not isinstance(data, dict):
        raise ValueError(f"'{key}' va en JSON: {{\"Canyon\": ...}}.")
    return data


def card_data_from(form) -> tuple[dict, dict]:
    """(photos, data): `card_photos` ({name: link}) and `card_data` ({name: {photo, art, icon, team, side, role,
    champion, title, back}}), validated. A photo given in both places: card_data wins."""
    photos = _json_map(form, "card_photos")
    for name, link in photos.items():
        if not isinstance(link, str) or not re.match(r"^https?://", link.strip(), re.I):
            raise ValueError(f"'card_photos': el link de {name} tiene que empezar con http:// o https://.")
    data = _json_map(form, "card_data")
    for name, info in data.items():
        if not isinstance(info, dict):
            raise ValueError(f"'card_data': lo de {name} va como un objeto {{\"photo\": ...}}.")
        extra = set(info) - set(CARD_FIELDS)
        if extra:
            raise ValueError(f"'card_data': {name} trae campos que no existen ({', '.join(sorted(extra))}); "
                             f"son: {', '.join(CARD_FIELDS)}.")
        for key in ("photo", "art", "icon"):
            if key in info and not re.match(r"^https?://", str(info[key]).strip(), re.I):
                raise ValueError(f"'card_data': {key} de {name} tiene que ser un link http:// o https://.")
        if info.get("side") not in (None, "blue", "red"):
            raise ValueError(f"'card_data': side de {name} es blue o red.")
        back = info.get("back")
        if back is not None:
            rows = back.get("rows") if isinstance(back, dict) else None
            if not isinstance(rows, list) or not 1 <= len(rows) <= 3 or not all(
                    isinstance(r, dict) and r.get("big") and r.get("label") for r in rows):
                raise ValueError(f"'card_data': el dorso de {name} lleva 1 a 3 'rows' con 'big' y 'label'.")
    return photos, data


def cards_from(form) -> CardParams:
    """cards (names), card_scope, card_back, card_style, card_mode, card_dur_s, card_lead_s, card_scale,
    card_pos (JSON) and cards_preview."""
    from modules.competitivo import card_plan
    names = card_plan.parse_names(form.get("cards"))

    def pick(field_name: str, options: tuple, default: str) -> str:
        value = str(form.get(field_name) or default).strip().lower()
        if value not in options:
            raise ValueError(f"'{field_name}' tiene que ser uno de: {', '.join(options)}.")
        return value
    pos = {}
    raw = form.get("card_pos")
    if raw:
        try:
            pos = json.loads(raw) if isinstance(raw, str) else dict(raw)
            if not isinstance(pos, dict):
                raise ValueError
        except ValueError:
            raise ValueError("'card_pos' es un JSON {\"Canyon\": \"bar:2\"} o {\"Canyon\": [x, y]}.")
        for name, where in pos.items():
            ok = (isinstance(where, str) and re.fullmatch(r"bar:\d+", where)) or (
                isinstance(where, list) and len(where) == 2 and all(isinstance(v, (int, float)) for v in where))
            if not ok:
                raise ValueError(f"'card_pos': la posición de {name} es \"bar:N\" o [x, y].")
    photos, data = card_data_from(form)
    return CardParams(
        players=names, scope=pick("card_scope", ("kills", "all"), "kills"),
        back=pick("card_back", CARD_BACKS, "auto"), style=pick("card_style", CARD_STYLES, "splash"),
        mode=pick("card_mode", ("freeze", "slow"), "freeze"),
        dur_s=_num(form, "card_dur_s", 2.0, 1.2, 4.0, float), lead_s=_num(form, "card_lead_s", 2.0, 0.0, 8.0, float),
        scale=_num(form, "card_scale", 1.0, 0.5, 1.6, float), pos=pos, preview=_bool(form, "cards_preview"),
        photos=photos, data=data)


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
    zoom_mode: str = "zoom"            # zoom (as always) | auto | full (camera.py)
    kill_overrides: list = field(default_factory=list)
    creator_vod_url: str = ""          # another creator's VOD: its webcam instead of Ibai's (creator.py)
    creator_cam: tuple | None = None   # (x, y, w, h) of its webcam; None = found by itself
    creator_delta_s: float | None = None   # creator_t = ibai_t + this; None = measured
    cards: CardParams = field(default_factory=CardParams)   # the player cards (see CardParams)

    @classmethod
    def desde(cls, base: CompetitivoParams) -> "FinishParams":
        """Todo heredado de la fase 1: lo usa el modo auto, que llama a finish()
        sin pasar por el form (así no se pierde ningún campo nuevo)."""
        return cls(anclas={}, clips=None,
                   **{c: getattr(base, c) for c in CAMPOS_RENDER + CAMPOS_INTRO + CAMPOS_SALIDA})

    @classmethod
    def from_form(cls, form, base: CompetitivoParams) -> "FinishParams":
        anclas, clips = _anclas_y_clips(form, base)
        mode = str(form.get("zoom_mode") or "zoom").strip().lower()
        if mode not in ("auto", "zoom", "full"):
            raise ValueError("'zoom_mode' tiene que ser auto, zoom o full.")
        return cls(anclas=anclas, clips=clips, **_render(form, base), **_intro(form, base),
                   **_salida(form, base), zoom_mode=mode,
                   kill_overrides=kill_overrides_from(form.get("kill_overrides")), cards=cards_from(form),
                   **creator_from(form))


def _anclas_y_clips(form, base: CompetitivoParams) -> tuple[dict[str, float], list[str] | None]:
    """`anchors` ({gameId: segundo o h:mm:ss}) y `clips` (lista de ids) de la
    fase 2: los comparten /finish y /edit."""
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
    return anclas, clips


@dataclass
class EditParams:
    """Edit mode (/edit): the phase 1 fights cut to the rhythm of a song.

    The new fields are in English; the ones shared with /finish (carpeta_dia,
    partido, titulo, mapa, zoom, watermark, marca_alto) keep their names."""
    anchors: dict[str, float]
    clips: list[str] | None
    music: object | None = None       # DownloaderParams of the link; None → `music` file
    music_start: float | None = None  # s of the song; None = automatic (drop)
    align_drop: bool = True
    engine: str = "auto"
    lyrics: object | None = None      # LyricsParams (only if text is "lyrics" or "both")
    style: str = "impact"
    text: str = "off"                 # no kill titles by default (Axl, 23/10)
    speed: float = 1.0
    max_duration_s: float = 60        # 0 = no cap (only the song limits it)
    intensity: float = 1.0
    look: str = "vivid"
    lut: str = ""
    glow: float = 0.25
    with_camera: bool = True
    vignette: bool = True             # soft (editing.effects.VIGNETTE)
    zoom: float = 1.3
    game_volume: int = 0
    kill_lead_s: float = 0.25
    music_accents: bool = True
    watermark: str = ""
    marca_alto: int = layout.MARCA_ALTO_DEFECTO
    edit_title: str = ""
    carpeta_dia: str = ""
    partido: str = ""
    titulo: str = ""
    mapa: int = 1
    # v2 "acts" (see competitivo/edit.py plan_acts)
    structure: str = "acts"           # acts | beat (the previous one)
    recipes: tuple = ()               # () = auto (all)
    cold_open_s: float = 6.5
    build_fights: int = 2
    voice_subs: bool = True           # Ibai's words in cold_open/build, "invert" subtitles
    voice_language: str = "es"
    subs_style: dict | None = None    # SubtitlesParams fields for those subtitles
    review: bool = False              # pause with Ibai's segments before burning (/edit/finish)
    chronological: bool = True        # acts: every fight in order, the cold open a teaser of the key one
    teaser_s: float = 2.5             # that teaser (2–3 s)
    short: bool = False               # acts: only the key fight and the best other one (~20–25 s)
    ambient: bool = False             # acts: stage/crowd shots in the cold open and the outro
    kill_before_s: float = 3.0        # acts: game before the first kill of each fight
    kill_after_s: float = 2.0         # and after the last one
    team: str = ""                    # every fight and pick where this team killed (code, e.g. T1)
    voice_peaks: bool = True          # acts: Ibai's peaks in real time over the drop
    last_fight_look: str = "none"     # acts: none | bw (the last fight and the outro)
    zoom_mode: str = "auto"           # auto | zoom | full (camera.py)
    storyboard: bool = False          # pause with a thumbnail per kill before the render
    kill_overrides: list = field(default_factory=list)   # [{kill | clock, mode?, focus?}]
    moments: list = field(default_factory=list)   # [{clock, dur, label, game_id?}]: moments without a kill
    creator_vod_url: str = ""
    creator_cam: tuple | None = None
    creator_delta_s: float | None = None

    @classmethod
    def from_form(cls, form, base: CompetitivoParams) -> "EditParams":
        from modules.beatmap.analysis import ENGINES
        from modules.beatmap.schema import LyricsParams, music_from_form
        from modules.competitivo.edit import LOOKS_PER_ACT, RECIPES, STRUCTURES, STYLES, TEXT_MODES
        from modules.subtitles.schema import VALID_LANGUAGES, SubtitlesParams
        from modules.editing import effects

        anchors, clips = _anclas_y_clips(form, base)

        def choice(name, options, default):
            value = str(form.get(name) or default).strip().lower()
            if value not in options:
                raise ValueError(f"'{name}' tiene que ser uno de: {', '.join(options)}.")
            return value

        start = form.get("music_start")
        if start in (None, "", "auto"):
            start = None
        else:
            try:
                start = engine.parse_vod_time(start)
            except (TypeError, ValueError):
                raise ValueError("'music_start' va en segundos (12.5), en m:ss o 'auto'.")
        text = choice("text", TEXT_MODES, "off")
        max_duration = _num(form, "max_duration_s", 60, 0, 600, float)
        if 0 < max_duration < 10:
            raise ValueError("'max_duration_s' va de 10 a 600 s, o 0 para no ponerle tope.")
        team = str(form.get("team") or "").strip().upper()
        if team and not re.fullmatch(r"[A-Z0-9]{1,6}", team):
            raise ValueError("'team' es el código del equipo, como en el marcador (p. ej. T1).")
        look = choice("look", list(effects.LOOKS), "vivid")
        lut = os.path.basename(str(form.get("lut") or "").strip())
        if lut and lut not in effects.luts():
            raise ValueError(f"No existe el LUT «{lut}» en assets/luts.")
        return cls(
            anchors=anchors, clips=clips, music=music_from_form(form), music_start=start,
            align_drop=_bool(form, "align_drop", True),
            engine=choice("engine", ENGINES, "auto"),
            lyrics=LyricsParams.from_form(form, lyrics_default=text in ("lyrics", "both")),
            style=choice("style", STYLES, "impact"), text=text,
            speed=_num(form, "speed", 1.0, 0.5, 2.0, float),
            max_duration_s=max_duration,
            intensity=_num(form, "intensity", 1.0, 0.0, 1.5, float),
            look=look, lut=lut, glow=_num(form, "glow", 0.25, 0.0, 1.0, float),
            with_camera=_bool(form, "with_camera", True),
            vignette=_bool(form, "vignette", True),
            zoom=_num(form, "zoom", base.zoom, 0.8, 2.2, float),
            game_volume=_num(form, "game_volume", 0, 0, 100),
            kill_lead_s=_num(form, "kill_lead_s", 0.25, 0.0, 1.0, float),
            music_accents=_bool(form, "music_accents", True),
            watermark=_marca(form) if form.get("watermark") is not None else base.watermark,
            marca_alto=_num(form, "marca_alto", base.marca_alto, 80, 400),
            edit_title=_texto(form, "edit_title", 60, ""),
            mapa=_num(form, "mapa", base.mapa, 1, 5),
            structure=choice("structure", STRUCTURES, "acts"),
            recipes=_recipes(form.get("recipes"), RECIPES),
            cold_open_s=_num(form, "cold_open_s", 6.5, 5.0, 8.0, float),
            build_fights=_num(form, "build_fights", 2, 0, 6),
            voice_subs=_bool(form, "voice_subs", True),
            voice_language=choice("voice_language", sorted(VALID_LANGUAGES - {"auto"}), "es"),
            subs_style=_subs_style(SubtitlesParams.from_form(form)),
            review=_bool(form, "review", False),
            chronological=_bool(form, "chronological", True),
            teaser_s=_num(form, "teaser_s", 2.5, 2.0, 3.0, float),
            short=_bool(form, "short", False),
            ambient=_bool(form, "ambient", False),
            kill_before_s=_num(form, "kill_before_s", 3.0, 0.0, 8.0, float),
            kill_after_s=_num(form, "kill_after_s", 2.0, 0.0, 6.0, float),
            team=team,
            voice_peaks=_bool(form, "voice_peaks", True),
            last_fight_look=choice("last_fight_look", LOOKS_PER_ACT, "none"),
            zoom_mode=choice("zoom_mode", ("auto", "zoom", "full"), "auto"),
            storyboard=_bool(form, "storyboard", False),
            kill_overrides=kill_overrides_from(form.get("kill_overrides")),
            moments=moments_from(form.get("moments"), base),
            **creator_from(form),
            **_salida(form, base),
        )
