"""Edit mode: the fights of a game cut to the rhythm of a song.

What the summary does with the casters' voice, the edit does with music, and
with something an anime edit does not have: the data. Every kill is already
located to the frame (feed + scoreboard counter, see layout.cambios_kills), so
nothing is searched in the video to match the music: each kill is FORCED onto
a beat by stretching or compressing the span before it (a speed ramp).

Structure of each fight in beats (all integers, so the cuts land on the beat
and each fight starts on the first beat of a bar):

    lead-in  L beats   (1 bar; 2 if there is a lot before the first kill)
    kills    n_i beats between kills (as close as possible to the base speed,
             between 0.5× and 2.5×)
    tail     T beats   (≥ 2, whatever closes the bar)

The most important fight (the closing one, or the highest score) is adjusted
so its last kill lands on a downbeat, and the music starts so that kill lands
exactly on the song's DROP. Everything is pure: it takes the already
downloaded clips and the beatmap, and returns the EDL.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from modules.beatmap import analysis
from modules.editing import timing
from modules.editing.edl import Edl, Event, Piece, Segment, Text

MERGE_S = 0.35               # kills closer than this are a single hit
LEAD_IN_S = 4.0              # how much to show before the first kill
MIN_GAP_SPEED, MAX_GAP_SPEED = 0.5, 2.5
MIN_TAIL = 2
MULTI = {"triple": "TRIPLE KILL", "quadra": "QUADRA KILL", "penta": "PENTAKILL"}
STYLES = ("impact", "velocity", "clean")
TEXT_MODES = ("kills", "lyrics", "both", "off")


@dataclass
class EditKill:
    t: float                          # s in the span (already corrected)
    side: str = ""
    player: str = ""
    champion: str = ""
    count: int = 1


@dataclass
class EditClip:
    id: str
    source: str                       # the downloaded span
    start: float                      # live span (s in the file)
    end: float
    kills: list[EditKill]
    score: int = 0
    multikill: str | None = None      # "quadra de Caliste"
    closes: bool = False
    title: str = ""
    vf: str = ""                      # geometry (vertical layout) of this span


@dataclass
class EditOptions:
    style: str = "impact"
    speed: float = 1.0                # base game speed
    max_duration_s: float = 60
    align_drop: bool = True
    music_start: float | None = None  # s of the song; None = automatic
    text: str = "kills"
    music_accents: bool = True        # small zoom punches on the chorus bars
    title: str = ""
    fps: int = 60
    width: int = 1080
    height: int = 1920


@dataclass
class _Plan:
    clip: EditClip
    hits: list[EditKill]              # merged kills
    lead_in: int                      # L
    gaps: list[int]                   # n_i
    tail: int                         # T
    lead_in_src: float = 0.0
    tail_src: float = 0.0

    @property
    def beats(self) -> int:
        return self.lead_in + sum(self.gaps) + self.tail

    @property
    def last_kill_beat(self) -> int:
        return self.lead_in + sum(self.gaps)


@dataclass
class EditResult:
    edl: Edl
    music_start: float
    drop_aligned: bool
    used: list[str]
    dropped: list[str] = field(default_factory=list)
    warning: str | None = None


def merge(kills: list[EditKill]) -> list[EditKill]:
    """Kills less than MERGE_S apart are one hit (the first, with the sum)."""
    out: list[EditKill] = []
    for k in sorted(kills, key=lambda k: k.t):
        if out and k.t - out[-1].t < MERGE_S:
            out[-1] = EditKill(out[-1].t, out[-1].side, out[-1].player, out[-1].champion,
                               out[-1].count + k.count)
        else:
            out.append(EditKill(k.t, k.side, k.player, k.champion, k.count))
    return out


def _beats_for(src: float, per: float, base: float) -> int:
    """Beats to show `src` s of game near the `base` speed, without leaving
    MIN_GAP_SPEED–MAX_GAP_SPEED."""
    n = max(1, round(src / (base * per)))
    n = max(n, math.ceil(src / (MAX_GAP_SPEED * per)))
    n = min(n, max(1, math.floor(src / (MIN_GAP_SPEED * per))))
    return n


def structure(clip: EditClip, per: float, base: float) -> _Plan:
    hits = merge([k for k in clip.kills if clip.start <= k.t <= clip.end])
    if not hits:
        # No kills inside the live span (rare): one bar from the middle.
        middle = (clip.start + clip.end) / 2
        hits = [EditKill(middle)]
    before = hits[0].t - clip.start
    lead_in = 8 if before >= 6 * per * base and before > LEAD_IN_S * 1.6 else 4
    gaps = [_beats_for(b.t - a.t, per, base) for a, b in zip(hits, hits[1:])]
    total = lead_in + sum(gaps) + MIN_TAIL
    tail = MIN_TAIL + (-total) % 4
    plan = _Plan(clip, hits, lead_in, gaps, tail)
    plan.lead_in_src = max(0.0, min(before, lead_in * per * base))
    plan.tail_src = max(0.0, min(clip.end - hits[-1].t, tail * per * base))
    return plan


def _climax(plans: list[_Plan]) -> int:
    return max(range(len(plans)), key=lambda i: (plans[i].clip.closes, plans[i].clip.score, i))


def _align_climax(plan: _Plan) -> None:
    """The last kill of the key fight on a downbeat: the last gap (or the
    lead-in, if there is a single kill) is stretched and the tail recomputed."""
    rest = plan.last_kill_beat % 4
    if rest:
        extra = 4 - rest
        if plan.gaps:
            plan.gaps[-1] += extra
        else:
            plan.lead_in += extra
    total = plan.last_kill_beat + MIN_TAIL
    plan.tail = MIN_TAIL + (-total) % 4


def _fill(start: float, end: float, dur: float, build, freeze_at: str) -> list[Piece]:
    """The pieces from `build(start, end, d)`; if there is not enough source to
    fill `dur` without going under MIN_SPEED, the rest is frozen at the start
    (lead-in: the still shot before it starts) or at the end (tail)."""
    src = max(0.0, end - start)
    if src >= dur * timing.MIN_SPEED * 0.999:
        return build(start, end, dur)
    used = src / timing.MIN_SPEED
    rest = dur - used
    if freeze_at == "start":
        return [Piece(start, start, rest)] + (build(start, end, used) if used > 0 else [])
    return (build(start, end, used) if used > 0 else []) + [Piece(end, end, rest)]


def _multikill(text: str | None) -> str | None:
    if not text:
        return None
    for key, label in MULTI.items():
        if text.lower().startswith(key):
            return label
    return None


def plan(clips: list[EditClip], bm: dict, op: EditOptions) -> EditResult:
    """EDL of the edit: `clips` in chronological order, `bm` the FULL beatmap
    of the song (output times come from its real beats)."""
    if op.style not in STYLES:
        raise ValueError(f"Estilo inválido: {op.style!r}. Opciones: {', '.join(STYLES)}.")
    if not clips:
        raise ValueError("No hay peleas para el edit.")
    beats = bm["beats"]
    per = analysis.period(bm)
    plans = [structure(c, per, op.speed) for c in clips]
    dropped: list[str] = []

    # Budget: drop the lowest scores (never the key fight) until it fits.
    def dur(pl):
        return sum(p.beats for p in pl) * per

    while len(plans) > 1 and dur(plans) > op.max_duration_s:
        key = _climax(plans)
        worst = min((i for i in range(len(plans)) if i != key),
                    key=lambda i: (plans[i].clip.score, -i))
        dropped.append(plans.pop(worst).clip.id)

    key = _climax(plans)
    _align_climax(plans[key])
    clip_start, acc = [], 0
    for p in plans:
        clip_start.append(acc)
        acc += p.beats
    total_beats = acc
    key_beat = clip_start[key] + plans[key].last_kill_beat

    # Where the music starts (m0: beat index in the song).
    warning = None
    drop_aligned = False
    downs = {round(d, 3) for d in bm["downbeats"]}
    first_down = next((i for i, b in enumerate(beats) if round(b, 3) in downs), 0)
    if op.music_start is not None:
        # The nearest beat and, if it is not a downbeat, the next one that is.
        m0 = analysis.nearest_beat(beats, op.music_start)
        for _ in range(3):
            if round(beats[m0], 3) in downs or m0 >= len(beats) - 1:
                break
            m0 += 1
    elif op.align_drop and bm.get("drop") is not None:
        m0 = analysis.nearest_beat(beats, bm["drop"]) - key_beat
        if m0 < 0:
            warning = "La canción tiene poco antes del drop: la pelea clave no cae en el drop."
            m0 = first_down
        else:
            drop_aligned = True
    else:
        m0 = first_down
    if m0 + total_beats >= len(beats):
        missing = (m0 + total_beats - len(beats) + 1) * per
        raise ValueError(f"La canción es corta para estas peleas (faltan {missing:.0f} s de música). "
                         "Bajá max_duration_s o elegí otra canción.")

    def T(r: int) -> float:
        return beats[m0 + r] - beats[m0]

    segments: list[Segment] = []
    events: list[Event] = []
    texts: list[Text] = []
    output_kills: list[float] = []
    for n, (pl, r0) in enumerate(zip(plans, clip_start)):
        c = pl.clip
        is_key = n == key
        pieces: list[Piece] = []
        h = pl.hits
        r = r0 + pl.lead_in
        pieces += _fill(h[0].t - pl.lead_in_src, h[0].t, T(r) - T(r0),
                        lambda a, b, d: timing.ramp(a, b, d, "constant"), "start")
        spans = list(zip(h, h[1:] + [None], pl.gaps + [pl.tail]))
        for a, b, nb in spans:
            end = b.t if b else a.t + pl.tail_src
            d = T(r + nb) - T(r)
            last = b is None
            strong = is_key and last
            if op.style == "impact":
                def build(x, y, dd, strong=strong):
                    return timing.impact(x, y, dd, freeze=0.1 if strong else 0.05,
                                         slow=min(dd * 0.6, per * (2 if strong else 1)),
                                         slow_speed=0.3 if strong else 0.45)
            else:
                def build(x, y, dd):
                    return timing.ramp(x, y, dd, "velocity" if op.style == "velocity" else "constant")
            pieces += _fill(a.t, end, d, build, "end")
            t_kill = T(r)
            output_kills.append(t_kill)
            multi = a.count >= 2
            events += [Event(t_kill, "flash", 1.0 if strong else 0.75 if multi else 0.55, 0.16),
                       Event(t_kill, "zoom", 1.0 if strong else 0.6, 0.3 if strong else 0.22),
                       Event(t_kill, "shake", 1.0 if strong else 0.5, 0.4 if strong else 0.25)]
            if strong or multi:
                events.append(Event(t_kill, "rgb", 1.0, 0.3))
            if op.text in ("kills", "both") and a.player:
                text_end = T(r + nb) if b else t_kill + max(per * 2, 1.0)
                texts.append(Text(t_kill, min(text_end, t_kill + 1.6), a.player, "kill", sub=a.champion))
            r += nb
        mk = _multikill(c.multikill)
        if mk and op.text in ("kills", "both"):
            t_last = output_kills[-1]
            texts.append(Text(t_last + 0.05, t_last + max(per * 3, 1.4), mk, "multikill"))
        # Only what is too fast is clamped (it uses less source): the slow
        # parts were already solved by _fill with freezes, within the span.
        pieces = timing.clamp_speeds(pieces, 0.0, timing.MAX_SPEED)
        segments.append(Segment(c.source, pieces, c.id, c.vf))
        if n > 0:
            events.append(Event(T(r0), "flash", 0.35, 0.12))

    if op.music_accents:
        win = analysis.window(bm, beats[m0], T(total_beats))
        for d in win["downbeats"]:
            if analysis.section_at(win, d) == "chorus" and all(abs(d - k) > per for k in output_kills):
                events.append(Event(d, "zoom", 0.3, 0.2))
    if op.text in ("lyrics", "both"):
        win = analysis.window(bm, beats[m0], T(total_beats))
        taken = [(t.start, t.end) for t in texts]
        words = win.get("words") or []
        for w, nxt in zip(words, words[1:] + [None]):
            end = min(nxt["start"] if nxt else w["end"] + 0.3, w["start"] + 1.2)
            if end - w["start"] < 0.08 or any(a < end and w["start"] < b for a, b in taken):
                continue
            word = re.sub(r"[^\w'¿?¡!áéíóúñüÁÉÍÓÚÑÜ-]", "", w["text"])
            if word:
                texts.append(Text(w["start"], end, word, "word"))
    if op.title:
        texts.append(Text(0.0, min(2.5, T(total_beats)), op.title, "title"))

    edl = Edl(segments, width=op.width, height=op.height, fps=op.fps, events=events,
              texts=sorted(texts, key=lambda t: t.start), music_start=beats[m0])
    return EditResult(edl, beats[m0], drop_aligned, [p.clip.id for p in plans], dropped, warning)
