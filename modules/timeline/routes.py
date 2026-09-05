"""Blueprint de la línea del tiempo: el historial del proyecto leído de git.

No procesa video ni toca archivos: corre `git log` sobre el propio repo,
descarta el ruido (subir un asset, merges) y devuelve los commits
convencionales (`feat|fix|perf|refactor|docs|chore`) ya clasificados, con el
cuerpo del mensaje desarmado en párrafos y viñetas. La UI lo pinta como una
línea del tiempo informativa: qué se agregó, qué se arregló y qué se optimizó.

Al leerse de git, la vista se mantiene sola: cada commit nuevo aparece sin
tocar código.
"""
import re
import subprocess

from flask import Blueprint, jsonify

import config

bp = Blueprint("timeline", __name__, url_prefix="/api/timeline")

# Separadores raros para que ni el asunto ni el cuerpo puedan romper el parseo.
_FIELD, _RECORD = "\x1f", "\x1e"
_FORMAT = _FIELD.join(["%h", "%ad", "%s", "%b"]) + _RECORD

# `tipo(scope, scope): asunto` — lo que no matchea (assets sueltos, merges,
# "Update styles") queda afuera: no es un hito del producto.
_SUBJECT_RE = re.compile(
    r"^(feat|fix|perf|refactor|docs|chore)(?:\(([^)]*)\))?:\s*(.+)$")

# Los tres tipos que cuentan la historia; el resto es mantenimiento.
_KINDS = {"feat": "feat", "fix": "fix", "perf": "perf"}

# Trailers de git: no aportan nada a la línea del tiempo.
_TRAILER_RE = re.compile(
    r"^(co-authored-by|signed-off-by|generated with|🤖)", re.IGNORECASE)

_SCOPE_LABELS = {
    "reel_express": "Reel Express",
    "hook_reel": "Reel Frase",
    "vertical": "Vertical",
    "vertical_convert": "Vertical",
    "sound_drop": "SoundDrop",
    "watermark": "Watermark",
    "subtitles": "Subtítulos",
    "insert": "Insert",
    "downloader": "Downloader",
    "audio_merge": "Unir Audio",
    "assets": "Assets",
    "library": "Librería",
    "info": "Info",
    "ui": "Interfaz",
    "core": "Core",
    "ffmpeg": "FFmpeg",
    "app": "App",
    "js": "Frontend",
    "enhance": "Realce",
    "branding": "Marca",
    "skills": "Skills",
    "docs": "Docs",
}

# Cache por HEAD: el log solo cambia cuando hay un commit nuevo.
_CACHE: dict = {}


def _git(*args) -> str:
    """Corre git en el repo del proyecto. Lanza RuntimeError si no se puede."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=config.BASE_DIR, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=15)
    except FileNotFoundError:
        raise RuntimeError("git no está instalado o no está en el PATH.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("git tardó demasiado en responder.")
    if out.returncode != 0:
        raise RuntimeError(
            (out.stderr or "").strip() or "git devolvió un error.")
    return out.stdout


def _blocks(body: str) -> list[dict]:
    """Cuerpo del commit → párrafos y listas.

    Los mensajes vienen cortados a 72 columnas: cada bloque se vuelve a unir en
    una sola línea para que fluya con el ancho de la card, y las viñetas (`- `)
    se agrupan como lista (las líneas indentadas continúan la viñeta anterior).
    """
    out: list[dict] = []
    for raw in re.split(r"\n\s*\n", body.strip()):
        lines = [ln for ln in raw.splitlines()
                 if ln.strip() and not _TRAILER_RE.match(ln.strip())]
        if not lines:
            continue
        if any(ln.lstrip().startswith(("- ", "* ")) for ln in lines):
            items: list[str] = []
            for ln in lines:
                stripped = ln.lstrip()
                if stripped.startswith(("- ", "* ")):
                    items.append(stripped[2:].strip())
                elif items:
                    items[-1] += " " + stripped
            if items:
                out.append({"type": "ul", "items": items})
        else:
            out.append({"type": "p", "text": " ".join(
                ln.strip() for ln in lines)})
    return out


def _entries() -> list[dict]:
    log = _git("log", "--date=short", f"--pretty=format:{_FORMAT}")
    entries = []
    for record in log.split(_RECORD):
        record = record.strip("\n")
        if not record:
            continue
        sha, date, subject, body = (record.split(_FIELD) + ["", "", "", ""])[:4]
        match = _SUBJECT_RE.match(subject.strip())
        if not match:
            continue
        kind_raw, scopes_raw, title = match.groups()
        scopes = [_SCOPE_LABELS.get(s.strip(), s.strip().replace("_", " ").title())
                  for s in (scopes_raw or "").split(",") if s.strip()]
        entries.append({
            "hash": sha,
            "date": date,
            "type": kind_raw,
            "kind": _KINDS.get(kind_raw, "otro"),
            "scopes": scopes,
            "title": title.strip(),
            "detail": _blocks(body),
        })
    return entries


@bp.get("")
def timeline():
    """Historial del proyecto: hitos clasificados, del más nuevo al más viejo."""
    try:
        head = _git("rev-parse", "HEAD").strip()
    except RuntimeError as exc:
        # Sin git (o sin repo) la vista no es un error: simplemente no hay
        # historial que mostrar.
        return jsonify(available=False, error=str(exc), entries=[], stats={})

    if _CACHE.get("head") != head:
        _CACHE["head"] = head
        _CACHE["entries"] = _entries()

    entries = _CACHE["entries"]
    stats = {
        "total": len(entries),
        "feat": sum(e["kind"] == "feat" for e in entries),
        "fix": sum(e["kind"] == "fix" for e in entries),
        "perf": sum(e["kind"] == "perf" for e in entries),
        "otro": sum(e["kind"] == "otro" for e in entries),
        "first": entries[-1]["date"] if entries else None,
        "last": entries[0]["date"] if entries else None,
        "months": len({e["date"][:7] for e in entries}),
    }
    return jsonify(available=True, entries=entries, stats=stats)
