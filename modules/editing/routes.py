"""Flask blueprint of the editing engine (/api/editing): only options for the UI
and for Claude (looks, LUTs, speed profiles and optional binaries)."""
from flask import Blueprint, jsonify

from core import external
from modules.beatmap import analysis, lyrics
from modules.editing import effects, timing

bp = Blueprint("editing", __name__, url_prefix="/api/editing")


@bp.get("/options")
def options():
    return jsonify(
        looks=effects.looks(), luts=effects.luts(), profiles=list(timing.PROFILES),
        rife=external.rife_available(), esrgan=external.esrgan_available(),
        beat_this=analysis.beat_this_available(), vocal_separation=lyrics.separation_available(),
    )
