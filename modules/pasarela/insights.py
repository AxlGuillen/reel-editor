"""Highlights from Insights Capture (Overwolf), like its own, from its database.

The database is Insights Capture's (`config.INSIGHTS_DB`, by default
%APPDATA%\\Insights Capture\\db.sqlite3). It is ALWAYS read on a copy (the
file plus its -wal/-shm, in a temp folder) and never written: the app keeps
it open and a reader of the original could lock it or see half a write.

Tables used:
  video                   uuid, type ('recording'), file_path, duration
  video_game_event        video_uuid, timestamp (s inside the recording), key: kill | assist | death | victory | defeat
  video_game_info_update  video_uuid, key (champion, kills, deaths, assists, doubleKills…), value (JSON), timestamp

A highlight: one window [t − before, t + after] per event; windows that
overlap are joined (a double kill is ONE cut).
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager

import config

EVENTS = ("kill", "assist", "death")
MULTIKILLS = ("doubleKills", "tripleKills", "quadraKills", "pentaKills")


@contextmanager
def snapshot(db_path: str | None = None):
    """A read-only connection to a COPY of the database (removed afterwards)."""
    src = db_path or config.INSIGHTS_DB
    if not os.path.isfile(src):
        raise ValueError(f"No encontré la base de Insights Capture en {src}.")
    folder = tempfile.mkdtemp(prefix="reelforge-insights-")
    try:
        for path in glob.glob(glob.escape(src) + "*"):          # db.sqlite3, -wal, -shm
            shutil.copy2(path, os.path.join(folder, os.path.basename(path)))
        conn = sqlite3.connect(os.path.join(folder, os.path.basename(src)))
        try:
            yield conn
        finally:
            conn.close()
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def merge_windows(events: list[tuple[float, str]], before: float, after: float,
                  duration: float | None = None) -> list[dict]:
    """[{start, end, events}] from (timestamp, key) pairs: [t − before,
    t + after] each, clipped to the recording, overlapping ones joined."""
    out: list[dict] = []
    for t, key in sorted(events):
        a = max(0.0, t - before)
        b = t + after if duration is None else min(duration, t + after)
        if b <= a:
            continue
        if out and a <= out[-1]["end"]:
            out[-1]["end"] = max(out[-1]["end"], b)
            out[-1]["events"].append(key)
        else:
            out.append({"start": a, "end": b, "events": [key]})
    return [{**w, "start": round(w["start"], 3), "end": round(w["end"], 3)} for w in out]


def _last(conn, uuid: str, key: str):
    row = conn.execute("select value from video_game_info_update where video_uuid=? and key=? "
                       "order by timestamp desc limit 1", (uuid, key)).fetchone()
    if not row or row[0] is None:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return row[0]


def _summary(conn, uuid: str, file_path: str, duration: float) -> dict:
    counts = dict(conn.execute("select key, count(*) from video_game_event where video_uuid=? "
                               "group by key", (uuid,)).fetchall())
    stat = {k: _last(conn, uuid, k) for k in ("kills", "deaths", "assists")}
    kda = None
    if any(v is not None for v in stat.values()):
        kda = "/".join(str(stat[k] if stat[k] is not None else 0) for k in ("kills", "deaths", "assists"))
    result = "victoria" if counts.get("victory") else "derrota" if counts.get("defeat") else None
    multi = {k: int(_last(conn, uuid, k) or 0) for k in MULTIKILLS}
    return {"uuid": uuid, "nombre": os.path.splitext(os.path.basename(file_path))[0], "archivo": file_path,
            "existe": os.path.isfile(file_path), "duracion": round(duration, 1),
            "campeon": _last(conn, uuid, "champion"), "kda": kda, "resultado": result,
            "multikills": {k[:-5].lower(): v for k, v in multi.items() if v},
            "eventos": {k: counts.get(k, 0) for k in EVENTS}}


def recordings(db_path: str | None = None) -> list[dict]:
    """The recordings Insights knows, newest first, with champion, KDA,
    win/loss, multikills, event counts and whether the file is still there."""
    with snapshot(db_path) as conn:
        rows = conn.execute("select uuid, file_path, duration from video where type='recording' "
                            "order by created_at desc").fetchall()
        return [_summary(conn, u, fp, dur) for u, fp, dur in rows]


def find(conn, ref: str) -> tuple[str, str, float]:
    """(uuid, file_path, duration) of a recording given by uuid or by name
    (the file name, with or without .mp4). ValueError if none or several."""
    ref = str(ref).strip()
    row = conn.execute("select uuid, file_path, duration from video where type='recording' and uuid=?",
                       (ref,)).fetchone()
    if row:
        return row
    name = os.path.splitext(os.path.basename(ref))[0].lower()
    rows = [r for r in conn.execute("select uuid, file_path, duration from video where type='recording'")
            if os.path.splitext(os.path.basename(r[1]))[0].lower() == name]
    if not rows:
        raise ValueError(f"No hay ninguna grabación «{ref}» en Insights Capture "
                         "(GET /api/pasarela/recordings lista las que hay).")
    if len(rows) > 1:
        raise ValueError(f"Hay {len(rows)} grabaciones que se llaman «{ref}»: pasá el uuid.")
    return rows[0]


def plan_highlight(ref: str, before: float, after: float, events: list[str],
                   db_path: str | None = None) -> dict:
    """{uuid, archivo, windows, summary} of a recording's highlight."""
    bad = [e for e in events if e not in EVENTS]
    if bad or not events:
        raise ValueError(f"eventos tiene que ser una lista de {', '.join(EVENTS)}.")
    with snapshot(db_path) as conn:
        uuid, fp, dur = find(conn, ref)
        marks = ",".join("?" * len(events))
        ev = conn.execute(f"select timestamp, key from video_game_event where video_uuid=? and key in ({marks}) "
                          "order by timestamp", (uuid, *events)).fetchall()
        summary = _summary(conn, uuid, fp, dur)
    if not summary["existe"]:
        raise ValueError(f"La grabación «{summary['nombre']}» ya no está en {fp}.")
    windows = merge_windows(ev, before, after, dur)
    if not windows:
        raise ValueError(f"La grabación «{summary['nombre']}» no tiene eventos {', '.join(events)}.")
    return {"uuid": uuid, "archivo": fp, "windows": windows, "summary": summary,
            "duracion": round(sum(w["end"] - w["start"] for w in windows), 3)}


def default_name(hl: dict, before: float, after: float) -> str:
    s = hl["summary"]
    kda = (s["kda"] or "").replace("/", "-")
    date = s["nombre"][-19:-9] if len(s["nombre"]) >= 19 else s["nombre"]
    return f"Highlight {s['campeon'] or 'LoL'} {kda} ({date}) - {before:g}s antes {after:g}s despues.mp4"


def concat_graph(windows: list[dict], first_input: int = 0, audio: bool = True, label: str = "hl") -> str:
    """The windows (one input each, already seeked with -ss/-t) joined:
    [<label>v] and [<label>a]."""
    n = len(windows)
    parts = [f"[{first_input + k}:v]setpts=PTS-STARTPTS[w{k}v]" for k in range(n)]
    if audio:
        parts += [f"[{first_input + k}:a]asetpts=PTS-STARTPTS[w{k}a]" for k in range(n)]
        pairs = "".join(f"[w{k}v][w{k}a]" for k in range(n))
        parts.append(f"{pairs}concat=n={n}:v=1:a=1[{label}v][{label}a]")
    else:
        parts.append("".join(f"[w{k}v]" for k in range(n)) + f"concat=n={n}:v=1:a=0[{label}v]")
    return ";".join(parts)


def window_inputs(path: str, windows: list[dict], hwaccel: bool = False) -> list[str]:
    """One input per window, seeked before -i (fast and frame-exact when
    transcoding): only the cuts are decoded, not the whole recording."""
    args: list[str] = []
    for w in windows:
        args += (["-hwaccel", "cuda"] if hwaccel else []) + \
                ["-ss", f"{w['start']:.3f}", "-t", f"{w['end'] - w['start']:.3f}", "-i", path]
    return args
