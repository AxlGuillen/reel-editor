"""The narrated base through the Studio: subtitles in the script's spelling,
the deck's slides placed by the marked script, the montage with subtitles.

It drives the HTTP APIs (ReelForge on 5001 and Presentations on 3125), as
Claude does by hand: open → segments → place (auto) → fix `hasta` → place →
save → Presentations render (webm with alpha) → montage. The subtitle style
is the one of the Peyz video (Poppins Bold 58 at y = 1205, 4 words per line,
#F2C14E): it sits in the free row between the gameplay and the band."""
from __future__ import annotations

import difflib
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid

import config

MARK = re.compile(r"⟦lamina:[^⟧]*⟧\s*")
SUBTITLES = {"subtitles": "1", "font_size": "58", "position_y": "1205", "words_per_line": "4",
             "highlight_color": "#F2C14E", "sub_font": "Poppins-Bold.ttf", "sub_outline_size": "5"}


def _norm(w: str) -> str:
    w = unicodedata.normalize("NFD", w.lower())
    return "".join(c for c in w if c.isalnum())


def screen_words(segments: list[dict], script: str) -> list[dict]:
    """Whisper's segments of the voice with the words of the (marked) script:
    the times stay Whisper's, the spelling becomes the script's ("pace" →
    Peyz, "simbra" → Syndra). A replaced span spreads the script's words over
    it by length; a word Whisper missed joins its neighbour. Returns the
    segments in the Studio's shape ({start, end, text, words})."""
    target = MARK.sub("", script).split()
    heard = [(k, w) for k, s in enumerate(segments) for w in s["words"]]
    sm = difflib.SequenceMatcher(None, [_norm(w["word"]) for _, w in heard], [_norm(t) for t in target],
                                 autojunk=False)
    out: list[tuple[int, dict]] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out += [(heard[i1 + d][0], {**heard[i1 + d][1], "word": target[j1 + d]}) for d in range(i2 - i1)]
        elif op == "replace":
            t0, t1 = heard[i1][1]["start"], heard[i2 - 1][1]["end"]
            lens = [len(t) for t in target[j1:j2]]
            acc = t0
            for d, n in enumerate(lens):
                e = acc + (t1 - t0) * n / sum(lens)
                out.append((heard[i1][0], {"word": target[j1 + d], "start": round(acc, 3), "end": round(e, 3)}))
                acc = e
        elif op == "insert" and out:
            k, w = out[-1]
            out[-1] = (k, {**w, "word": w["word"] + " " + " ".join(target[j1:j2])})
    clean = []
    for k in sorted({k for k, _ in out}):
        words = [w for kk, w in out if kk == k]
        clean.append({"start": words[0]["start"], "end": words[-1]["end"],
                      "text": " ".join(x["word"] for x in words), "words": words})
    return clean


def _call(url: str, data=None, form=None, files=None, timeout: int = 900):
    if files is not None:
        b = uuid.uuid4().hex
        body = b""
        for k, v in (form or {}).items():
            body += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        for k, (name, raw) in files.items():
            body += (f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                     f"Content-Type: application/octet-stream\r\n\r\n").encode() + raw + b"\r\n"
        body += f"--{b}--\r\n".encode()
        req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={b}"})
    elif form is not None:
        req = urllib.request.Request(url, urllib.parse.urlencode(form).encode(),
                                     {"Content-Type": "application/x-www-form-urlencoded"})
    elif data is not None:
        req = urllib.request.Request(url, json.dumps(data).encode(), {"Content-Type": "application/json"})
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} → {e.code}: {e.read().decode()[:500]}")


def _wait(url: str, done: tuple, key: str = "status", every: float = 3.0) -> dict:
    while True:
        st = _call(url)
        if st.get(key) in done:
            return st
        time.sleep(every)


def run(base_video: str, voice_segments: list[dict], desk: str, deck: str, name: str, *,
        hasta: dict | None = None, subtitles: dict | None = None, rf: str = "http://localhost:5001",
        presentations: str | None = None) -> dict:
    """The whole montage. `hasta`: {slide id: s} for slides that must end
    earlier than the rule says (an arrow over the gameplay that should not
    outlive its shot). Returns {"studio_id", "layers", "montage_job"}."""
    pres = presentations or config.PRESENTATIONS_URL
    script = _call(f"{rf}/api/studio/script?desk={desk}&deck={deck}")
    segs = screen_words(voice_segments, script["script"])
    with open(base_video, "rb") as fh:
        s = _call(f"{rf}/api/studio/open", form={}, files={"video": (os.path.basename(base_video), fh.read())})
    sid = s["studio_id"]
    _call(f"{rf}/api/studio/segments", {"studio_id": sid, "segments": segs})
    manifest = _call(f"{pres}/api/decks/{desk}/{deck}/laminas")
    montaje = {"version": 1, "base": {"archivo": "x"}, "deck": {"desk": desk, "deck": deck, "servidor": pres},
               "capas": [], "subtitulos": True, "reposo": False, "render": {"formato": "webm", "fps": 30}}
    d = _call(f"{rf}/api/studio/place", {"studio_id": sid, "montaje": montaje, "manifest": manifest,
                                          "script": script["script"], "auto": True})
    if hasta:
        for c in d["montaje"]["capas"]:
            if c["lamina"] in hasta:
                c["hasta"] = hasta[c["lamina"]]
        d = _call(f"{rf}/api/studio/place", {"studio_id": sid, "montaje": d["montaje"], "manifest": manifest,
                                              "script": script["script"], "auto": False})
    _call(f"{rf}/api/studio/save", {"studio_id": sid, "montaje": d["montaje"], "manifest": manifest})
    job = _call(f"{pres}/api/render", {"desk": desk, "deck": deck, "laminas": [x["id"] for x in d["layers"]],
                                        "alfa": "webm", "fps": 30})["job_id"]
    st = _wait(f"{pres}/api/render/{job}", ("listo", "error"), key="estado")
    if st["estado"] == "error":
        raise RuntimeError("Presentations: " + str(st.get("error")))
    _call(f"{rf}/api/studio/session/{sid}")
    form = {"job_id": sid, "layers": json.dumps(d["specs"]), "layers_folder": st["carpeta"],
            "segments": json.dumps(segs), "output_name": name, **(subtitles or SUBTITLES)}
    mj = _call(f"{rf}/api/montage/process", form=form)["job_id"]
    m = _wait(f"{rf}/api/montage/status/{mj}", ("done", "error"))
    if m["status"] == "error":
        raise RuntimeError("montage: " + str(m.get("error")))
    return {"studio_id": sid, "layers": d["layers"], "montage_job": mj}
