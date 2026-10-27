"""Dónde queda cada resumen en la carpeta persistente (config.COMPETITIVO_FOLDER).

Dos estructuras:

  organizada (con carpeta_dia y partido):
      <dia>/<partido>/Mapa N - <titulo>.mp4          (o "Mapa N.mp4" sin título)
      <dia>/<partido>/_extras/Mapa N/                 clips, portada.jpg, info.json
      <dia>/<partido>/_extras/_descartados/           las versiones anteriores
  serie (la de siempre):
      <fecha> <A> vs <B>[ · Mapa N]/                  resumen.mp4, clips, portada, info.json

En la organizada todo se renderiza en una carpeta temporal dentro de _extras y
recién al terminar se publica: lo anterior de ese mapa pasa a _descartados (no
se pisa nada) y lo nuevo queda con el nombre limpio. Si el job falla, se borra
la temporal y lo anterior queda intacto.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime

import config

EXTRAS = "_extras"
DESCARTADOS = "_descartados"
_INVALIDOS = re.compile(r'[<>"/\\|?*]')
_RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_VIDEO = re.compile(r"^Mapa (\d+)(?: - (.+))?\.mp4$", re.IGNORECASE)


def limpiar(texto: str) -> str:
    """Nombre válido en Windows: ":" → " -", fuera < > " / \\ | ? * y los
    caracteres de control, espacios colapsados, sin puntos ni espacios al
    final y sin nombres reservados (CON, NUL, COM1…)."""
    texto = texto.replace(":", " -")
    texto = _INVALIDOS.sub("", texto)
    texto = "".join(c for c in texto if ord(c) >= 32)
    texto = " ".join(texto.split()).rstrip(" .")
    if texto.split(".")[0].upper() in _RESERVADOS:
        texto += "_"
    return texto


def nombre_video(mapa: int, titulo: str = "") -> str:
    return f"Mapa {mapa} - {titulo}.mp4" if titulo else f"Mapa {mapa}.mp4"


def _unico(carpeta: str, nombre: str) -> str:
    """`nombre` dentro de `carpeta`, o "nombre (2)", "(3)"… si ya existe."""
    base, ext = (os.path.splitext(nombre) if nombre.lower().endswith(".mp4") else (nombre, ""))
    destino, n = os.path.join(carpeta, nombre), 2
    while os.path.exists(destino):
        destino, n = os.path.join(carpeta, f"{base} ({n}){ext}"), n + 1
    return destino


def _rel(path: str) -> str:
    return os.path.relpath(path, config.COMPETITIVO_FOLDER).replace(os.sep, "/")


@dataclass
class Destino:
    carpeta: str               # donde se renderiza: clips, portada.jpg, info.json y resumen.mp4
    organizada: bool
    video_final: str           # dónde queda el resumen
    extras_final: str          # dónde quedan clips, portada e info.json
    partido_dir: str = ""
    mapa: int = 0

    @property
    def resumen(self) -> str:
        return os.path.join(self.carpeta, "resumen.mp4")

    @property
    def ruta_video(self) -> str:
        return _rel(self.video_final)


def _carpeta_serie(partidas: list[dict], mapa: int | None) -> str:
    """<fecha> <A> vs <B> (· Mapa N si el trabajo es de un solo mapa)."""
    primera = partidas[0]
    fecha = datetime.fromisoformat(primera["inicio_utc"]).astimezone().strftime("%Y-%m-%d")
    nombre = f"{fecha} {primera['azul']} vs {primera['rojo']}"
    if mapa and len(partidas) == 1:
        nombre += f" · Mapa {mapa}"
    nombre = re.sub(r'[<>:"/\\|?*]+', "", nombre).strip()
    base = os.path.join(config.COMPETITIVO_FOLDER, nombre)
    destino, n = base, 2
    while os.path.exists(destino):
        destino, n = f"{base} ({n})", n + 1
    os.makedirs(destino)
    return destino


def preparar(partidas: list[dict], mapa: int, job_id: str, carpeta_dia: str = "",
             partido: str = "", titulo: str = "") -> Destino:
    if not (carpeta_dia and partido):
        carpeta = _carpeta_serie(partidas, mapa)
        return Destino(carpeta, False, os.path.join(carpeta, "resumen.mp4"), carpeta, mapa=mapa)
    partido_dir = os.path.join(config.COMPETITIVO_FOLDER, carpeta_dia, partido)
    temporal = os.path.join(partido_dir, EXTRAS, f".Mapa {mapa}.tmp-{job_id}")
    shutil.rmtree(temporal, ignore_errors=True)
    os.makedirs(temporal)
    return Destino(temporal, True, os.path.join(partido_dir, nombre_video(mapa, titulo)),
                   os.path.join(partido_dir, EXTRAS, f"Mapa {mapa}"), partido_dir, mapa)


def _anteriores(d: Destino) -> list[str]:
    """Videos de ese mapa ya publicados (con cualquier título)."""
    out = []
    for f in os.listdir(d.partido_dir):
        m = _VIDEO.match(f)
        if m and int(m.group(1)) == d.mapa and os.path.isfile(os.path.join(d.partido_dir, f)):
            out.append(os.path.join(d.partido_dir, f))
    return out


# What /transcript leaves in the extras folder: it is not part of a render, so a
# new /finish takes it along instead of leaving it in _descartados.
TRANSCRIPCION = ("transcripcion.txt", "transcripcion.json", "momentos.json")


def publicar(d: Destino) -> None:
    """Pone el resumen y los extras en su lugar; lo anterior de ese mapa pasa a
    _extras/_descartados/ (con (2), (3)… si ya hay uno con ese nombre). La
    transcripción de ese mapa (si ya estaba hecha) se queda en su lugar."""
    if not d.organizada:
        return
    descartados = os.path.join(d.partido_dir, EXTRAS, DESCARTADOS)
    viejos = _anteriores(d) + ([d.extras_final] if os.path.exists(d.extras_final) else [])
    if viejos:
        os.makedirs(descartados, exist_ok=True)
    guardadas = {f: os.path.join(d.extras_final, f) for f in TRANSCRIPCION
                 if os.path.isfile(os.path.join(d.extras_final, f))}
    guardadas_tmp = {}
    if guardadas:
        # A copy aside: the folder is about to move to _descartados.
        aparte = os.path.join(d.partido_dir, EXTRAS, f".transcripcion.tmp-{d.mapa}")
        shutil.rmtree(aparte, ignore_errors=True)
        os.makedirs(aparte)
        guardadas_tmp = {f: shutil.copy2(src, aparte) for f, src in guardadas.items()}
    for viejo in viejos:
        os.replace(viejo, _unico(descartados, os.path.basename(viejo)))
    os.replace(d.carpeta, d.extras_final)
    for f, src in guardadas_tmp.items():
        if not os.path.exists(os.path.join(d.extras_final, f)):
            shutil.move(src, os.path.join(d.extras_final, f))
    if guardadas_tmp:
        shutil.rmtree(os.path.dirname(next(iter(guardadas_tmp.values()))), ignore_errors=True)
    os.replace(os.path.join(d.extras_final, "resumen.mp4"), d.video_final)
    d.carpeta = d.extras_final


def descartar(d: Destino | None) -> None:
    """El job falló: se borra lo que quedó a medias, nunca lo anterior."""
    if d is None:
        return
    if d.organizada:
        shutil.rmtree(d.carpeta, ignore_errors=True)
    elif not os.path.isfile(os.path.join(d.carpeta, "info.json")):
        shutil.rmtree(d.carpeta, ignore_errors=True)


def resolver(ruta: str) -> str | None:
    """Ruta relativa a COMPETITIVO_FOLDER → archivo, o None si no existe o si
    se sale de la carpeta (.., rutas absolutas, otra unidad, enlaces)."""
    base = os.path.realpath(config.COMPETITIVO_FOLDER)
    try:
        path = os.path.realpath(os.path.join(base, ruta))
        if os.path.commonpath([base, path]) != base or path == base:
            return None
    except ValueError:            # otra unidad en Windows
        return None
    return path if os.path.isfile(path) else None


# --- Listado --------------------------------------------------------------------------
def _info(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _clips(info: dict, carpeta: str) -> list[dict]:
    return [{"archivo": c["archivo"], "titulo": c["titulo"], "duracion": c["duracion"],
             "ruta": _rel(os.path.join(carpeta, c["archivo"]))} for c in info.get("clips", [])]


def _si_existe(path: str) -> str | None:
    return _rel(path) if os.path.isfile(path) else None


def _de_serie(carpeta: str, info: dict) -> dict:
    return {
        "estructura": "serie", "nombre": os.path.basename(carpeta), "dia": None, "partido": None,
        "mapa": info.get("mapa"), "titulo": info.get("titulo") or None, "serie": info.get("serie"),
        "creado": info.get("creado"), "duracion_total": info.get("duracion_total"),
        "intro": info.get("intro_modo_usado"),
        "ruta": _si_existe(os.path.join(carpeta, "resumen.mp4")),
        "portada": _si_existe(os.path.join(carpeta, "portada.jpg")),
        "clips": _clips(info, carpeta),
    }


def _de_partido(dia: str, partido: str, partido_dir: str) -> list[dict]:
    out = []
    for f in os.listdir(partido_dir):
        m = _VIDEO.match(f)
        if not m or not os.path.isfile(os.path.join(partido_dir, f)):
            continue
        mapa = int(m.group(1))
        extras = os.path.join(partido_dir, EXTRAS, f"Mapa {mapa}")
        info = _info(os.path.join(extras, "info.json"))
        creado = info.get("creado") or datetime.fromtimestamp(
            os.path.getmtime(os.path.join(partido_dir, f))).astimezone().isoformat()
        out.append({
            "estructura": "organizada", "nombre": f"{partido} · Mapa {mapa}", "dia": dia, "partido": partido,
            "mapa": mapa, "titulo": m.group(2), "serie": info.get("serie"), "creado": creado,
            "duracion_total": info.get("duracion_total"), "intro": info.get("intro_modo_usado"),
            "ruta": _rel(os.path.join(partido_dir, f)),
            "portada": _si_existe(os.path.join(extras, "portada.jpg")),
            "clips": _clips(info, extras),
        })
    return out


def listar() -> list[dict]:
    """Todos los resúmenes, del más nuevo al más viejo: los organizados (día →
    partido → "Mapa N…mp4"; se ignoran _extras y _descartados) y las carpetas
    de serie de siempre."""
    base = config.COMPETITIVO_FOLDER
    if not os.path.isdir(base):
        return []
    out = []
    for nombre in os.listdir(base):
        carpeta = os.path.join(base, nombre)
        if not os.path.isdir(carpeta) or nombre.startswith(("_", ".")):
            continue
        if os.path.isfile(os.path.join(carpeta, "info.json")):
            out.append(_de_serie(carpeta, _info(os.path.join(carpeta, "info.json"))))
            continue
        for partido in os.listdir(carpeta):
            partido_dir = os.path.join(carpeta, partido)
            if os.path.isdir(partido_dir) and not partido.startswith(("_", ".")):
                out += _de_partido(nombre, partido, partido_dir)
    return sorted(out, key=lambda r: r["creado"] or "", reverse=True)
