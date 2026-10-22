"""Blueprint Flask del motor de edición (/api/edicion): solo opciones para la UI
y para Claude (looks, LUTs, perfiles de velocidad y binarios opcionales)."""
from flask import Blueprint, jsonify

from core import externos
from modules.beatmap import analisis, letra
from modules.edicion import efectos, ritmo

bp = Blueprint("edicion", __name__, url_prefix="/api/edicion")


@bp.get("/opciones")
def opciones():
    return jsonify(
        looks=efectos.looks(), luts=efectos.luts(), perfiles=list(ritmo.PERFILES),
        rife=externos.rife_disponible(), esrgan=externos.esrgan_disponible(),
        beat_this=analisis.beat_this_disponible(), separar_voz=letra.separacion_disponible(),
    )
