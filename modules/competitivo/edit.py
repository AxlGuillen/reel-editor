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

structure="acts" (plan_acts) is less linear, with Ibai's voice:

    cold_open  the best fight in real time, game and Ibai at 100 %, music at
               15 % (5–8 s), with a slow push-in
    build      1–2 more fights in real time, music at 20 %
    silence    0.3 s: everything cuts to black and mute
    drop       on the song's drop: music at 100 %, game at 15 %, the fights
               on the beat with a RECIPE per kill (ramp, freeze_zoom,
               instant_replay, hard_cut, stair_zoom) and whips between them
    outro      the closing fight's tail (the nexus) in real time, the game
               rising back and the song closing on its bar

Ibai's words in cold_open and build are transcribed by the processor
(voice_spans) and burned with the subtitles "invert" layout, never over the
lyrics.
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
STRUCTURES = ("beat", "acts")

# --- "acts" ------------------------------------------------------------------
RECIPES = ("ramp", "freeze_zoom", "instant_replay", "hard_cut", "stair_zoom")
ROTATING = ("ramp", "freeze_zoom", "hard_cut")      # the ones that rotate kill by kill
COLD_MIN, COLD_MAX = 5.0, 8.0
COLD_FOCUS = 0.7               # the cold open's best moment at 70 % of it
BUILD_BEFORE, BUILD_AFTER, BUILD_MAX = 2.5, 1.5, 7.0
SILENCE_S = 0.3
MUSIC_COLD, MUSIC_BUILD, GAME_DROP = 0.15, 0.20, 0.15
RAMP_S = 0.05                  # volume changes take this long (no clicks)
PUSH_LEVEL = 0.12              # slow push-in 1.0 → 1.12× over each real-time shot
WHIP_DUR = 0.12
RAMP_PROFILE = [(0.22, 0.3), (0.48, 1.0), (0.30, 1.5)]   # 0.3× at the kill → 1× → 1.5× into the next
FREEZE_S, FREEZE_ZOOM = 0.15, 0.3
REPLAY_SHOW, REPLAY_BACK, REPLAY_SPEED, REPLAY_ZOOM = 0.45, 1.5, 0.4, 0.6
STAIR = (0.1, 0.2, 0.3, 0.4)
TEASER_MIN, TEASER_MAX = 2.0, 3.0
SHORT_MAX_S = 25.0
AMBIENT_COLD_S = 2.5          # the ambient shot after the teaser
AMBIENT_MIN_S = 1.5


@dataclass
class EditKill:
    t: float                          # s in the span (already corrected)
    side: str = ""
    player: str = ""
    champion: str = ""
    count: int = 1
    focus: tuple | None = None        # (x, y) of the action in the OUTPUT frame, 0–1; None = center


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
    ambient: list = field(default_factory=list)   # [(a, b)] s of the span: stage, players, crowd
    ambient_vf: str = ""              # geometry of those shots (layout.ambient_geometry)


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
    structure: str = "beat"           # beat | acts
    recipes: tuple = ()               # acts: the allowed recipes; empty = all (auto)
    cold_open_s: float = 6.5
    build_fights: int = 2
    chronological: bool = True        # acts: every fight in order, the cold open a teaser
    teaser_s: float = 2.5             # chronological: the key fight's teaser (2–3 s)
    short: bool = False               # acts: only the key fight and the best other one, ~20–25 s
    ambient: bool = False             # acts: stage/crowd shots in the cold open and the outro


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
    # For the status: [{act, start, end, clips, music, game}] and
    # [{clip, t, recipe, count, player, champion}] in output time.
    acts: list = field(default_factory=list)
    kills: list = field(default_factory=list)
    # acts: the real-time shots with Ibai's voice, to transcribe:
    # [{source, start, end, out}] (s in the source / output start).
    voice_spans: list = field(default_factory=list)
    subtitle_until: float | None = None

    def plan_dict(self) -> dict:
        return {"acts": self.acts, "kills": self.kills}


def merge(kills: list[EditKill]) -> list[EditKill]:
    """Kills less than MERGE_S apart are one hit (the first, with the sum)."""
    out: list[EditKill] = []
    for k in sorted(kills, key=lambda k: k.t):
        if out and k.t - out[-1].t < MERGE_S:
            out[-1] = EditKill(out[-1].t, out[-1].side, out[-1].player, out[-1].champion,
                               out[-1].count + k.count, out[-1].focus or k.focus)
        else:
            out.append(EditKill(k.t, k.side, k.player, k.champion, k.count, k.focus))
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
    if op.structure not in STRUCTURES:
        raise ValueError(f"Estructura inválida: {op.structure!r}. Opciones: {', '.join(STRUCTURES)}.")
    if op.structure == "acts":
        return plan_acts(clips, bm, op)
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
    total = T(total_beats)
    acts = [{"act": "beat", "start": 0.0, "end": round(total, 3), "clips": [p.clip.id for p in plans],
             "music": 1.0, "game": None}]
    kills = [{"t": round(t, 3), "recipe": op.style} for t in output_kills]
    return EditResult(edl, beats[m0], drop_aligned, [p.clip.id for p in plans], dropped, warning,
                      acts=acts, kills=kills)


# --- structure="acts" ---------------------------------------------------------------
def _pick(n: int, salt: int) -> int:
    """Fixed pseudo-random integer for item n (Knuth hash, as in subtitles)."""
    return ((n + 1) * 2654435761 + salt) % 4294967296


def _real_time(clip: EditClip, a: float, b: float) -> tuple[float, float]:
    """[a, b] moved inside the clip's live span, keeping its length if it fits."""
    length = max(0.1, min(b - a, clip.end - clip.start))
    a = max(clip.start, min(a, clip.end - length))
    return a, a + length


def assign_recipes(plans: list[_Plan], key: int, allowed: tuple) -> list[list[str]]:
    """A recipe per hit, deterministic (same fights → same edit):
    - the key fight's last kill: instant_replay;
    - a fight with 3+ hits (multikill): stair_zoom on each one;
    - the rest rotate among ramp / freeze_zoom / hard_cut by a fixed hash,
      never the same twice in a row."""
    rotating = [r for r in ROTATING if r in allowed] or ["hard_cut"]
    out, prev, g = [], None, 0
    for n, pl in enumerate(plans):
        stair = "stair_zoom" in allowed and len(pl.hits) >= 3
        recs = []
        for j in range(len(pl.hits)):
            if n == key and j == len(pl.hits) - 1 and "instant_replay" in allowed:
                r = "instant_replay"
            elif stair:
                r = "stair_zoom"
            else:
                i = _pick(g, 7817) % len(rotating)
                r = rotating[i]
                if r == prev and len(rotating) > 1:
                    r = rotating[(i + 1) % len(rotating)]
            recs.append(r)
            prev, g = r, g + 1
        out.append(recs)
    return out


def _close_bar(plan_: _Plan, at_least: int = MIN_TAIL) -> None:
    """Tail of at least `at_least` beats that closes the bar."""
    plan_.tail = max(at_least, MIN_TAIL)
    plan_.tail += (-(plan_.last_kill_beat + plan_.tail)) % 4


def _constant(a: float, b: float, d: float) -> list[Piece]:
    return timing.ramp(a, b, d, "constant")


def _ramp_recipe(a: float, b: float, d: float) -> list[Piece]:
    return timing.ramp(a, b, d, RAMP_PROFILE)


def _ambient_shot(clips: list[EditClip], used: set, prefer: int | None = None,
                  after: float | None = None) -> tuple[int, float, float] | None:
    """An unused ambient shot (clip index, a, b), the longest; with `prefer`,
    one of that clip after `after` (the reaction to its last kill) goes first."""
    pool = [(i, a, b) for i, c in enumerate(clips) for a, b in c.ambient
            if b - a >= AMBIENT_MIN_S and (i, a) not in used]
    if not pool:
        return None
    best = max(pool, key=lambda x: (prefer is not None and x[0] == prefer and (after is None or x[1] >= after),
                                    x[2] - x[1]))
    used.add(best[:2])
    return best


def plan_acts(clips: list[EditClip], bm: dict, op: EditOptions) -> EditResult:
    """EDL of the "acts" edit (see the module docstring)."""
    if not clips:
        raise ValueError("No hay peleas para el edit.")
    allowed = tuple(op.recipes) or RECIPES
    bad = [r for r in allowed if r not in RECIPES]
    if bad:
        raise ValueError(f"Receta desconocida: {bad[0]!r}. Opciones: {', '.join(RECIPES)}.")
    beats = bm["beats"]
    per = analysis.period(bm)
    hits_of = [merge([k for k in c.kills if c.start <= k.t <= c.end]) or [EditKill((c.start + c.end) / 2)]
               for c in clips]
    n_clips = len(clips)
    key = max(range(n_clips), key=lambda i: (clips[i].closes, clips[i].score, i))
    chrono = op.chronological or op.short
    max_duration = min(op.max_duration_s, SHORT_MAX_S) if op.short else op.max_duration_s
    if op.short:
        # The key fight and the best other one, both on the drop, in order.
        others = [i for i in range(n_clips) if i != key]
        best_other = max(others, key=lambda i: (clips[i].score, -i)) if others else None
        cold, build = key, []
        drop_idx = sorted([key] + ([best_other] if best_other is not None else []))
    elif chrono:
        # Every fight in order: the first ones (before the key one) in real
        # time with Ibai, the rest on the beat; the cold open only teases the key.
        cold = key
        build = [i for i in range(n_clips) if i < key][:max(0, op.build_fights)]
        drop_idx = [i for i in range(n_clips) if i not in build]
    else:
        cold = max(range(n_clips), key=lambda i: (clips[i].score, i == key, i))
        others = sorted((i for i in range(n_clips) if i not in (cold, key)), key=lambda i: (-clips[i].score, i))
        build = sorted(others[:max(0, op.build_fights)])
        drop_idx = [i for i in range(n_clips) if i not in build] or [key]
    # Ambient shots: the outro's first (the reaction right after the last fight
    # is the best one), then the cold open's.
    used_ambient: set = set()
    outro_guess = drop_idx[-1] if chrono else key
    outro_shot = _ambient_shot(clips, used_ambient, prefer=outro_guess,
                               after=hits_of[outro_guess][-1].t) if op.ambient else None
    cold_shot = _ambient_shot(clips, used_ambient) if op.ambient else None

    segments: list[Segment] = []
    events: list[Event] = []
    texts: list[Text] = []
    acts: list[dict] = []
    kills_info: list[dict] = []
    voice: list[dict] = []

    def focus(h: EditKill) -> tuple:
        return h.focus or (0.5, 0.5)

    def real_shot(i: int, a: float, b: float, label: str, t: float, aim: EditKill) -> float:
        c = clips[i]
        a, b = _real_time(c, a, b)
        segments.append(Segment(c.source, [Piece(a, b, b - a)], f"{c.id}:{label}", c.vf))
        x, y = focus(aim)
        events.append(Event(t, "push", dur=b - a, level=PUSH_LEVEL, x=x, y=y))
        voice.append({"source": c.source, "start": round(a, 3), "end": round(b, 3), "out": round(t, 3)})
        return b - a

    # cold open: the best moment of a fight at 70 % of the shot. Chronological:
    # a 2–3 s teaser of the key fight; if not, 5–8 s of the best one.
    t = 0.0
    hs = hits_of[cold]
    best = max(hs, key=lambda h: (h.count, h.t))
    d = (max(TEASER_MIN, min(TEASER_MAX, op.teaser_s)) if chrono
         else max(COLD_MIN, min(COLD_MAX, op.cold_open_s)))
    t += real_shot(cold, best.t - COLD_FOCUS * d, best.t + (1 - COLD_FOCUS) * d, "cold_open", t, best)
    cold_clips = [clips[cold].id]
    if cold_shot:
        # Then the stage or the crowd, with Ibai's voice.
        i, a, b = cold_shot
        c = clips[i]
        dur = min(AMBIENT_COLD_S, b - a)
        events.append(Event(t, "whip", 0.8, WHIP_DUR))
        segments.append(Segment(c.source, [Piece(a, a + dur, dur)], f"{c.id}:ambient", c.ambient_vf or c.vf))
        events.append(Event(t, "push", dur=dur, level=PUSH_LEVEL))
        voice.append({"source": c.source, "start": round(a, 3), "end": round(a + dur, 3), "out": round(t, 3)})
        t += dur
        cold_clips.append(f"{c.id}:ambient")
    acts.append({"act": "cold_open", "start": 0.0, "end": t, "clips": cold_clips,
                 "music": MUSIC_COLD, "game": 1.0})
    # build: 1–2 more fights in real time, whips between the shots.
    build_start = t
    for i in build:
        hs = hits_of[i]
        a, b = hs[0].t - BUILD_BEFORE, hs[-1].t + BUILD_AFTER
        if b - a > BUILD_MAX:
            a = b - BUILD_MAX
        events.append(Event(t, "whip", 0.8, WHIP_DUR))
        t += real_shot(i, a, b, "build", t, max(hs, key=lambda h: (h.count, h.t)))
    if build:
        acts.append({"act": "build", "start": build_start, "end": t,
                     "clips": [clips[i].id for i in build], "music": MUSIC_BUILD, "game": 1.0})
    voice_end = silence_start = t
    drop_out = silence_start + SILENCE_S

    # drop: the beat structure, with a recipe per kill.
    plans = [structure(clips[i], per, op.speed) for i in drop_idx]
    ids = list(drop_idx)
    outro_est = 4 * per
    dropped: list[str] = []

    def drop_dur(pl: list[_Plan]) -> float:
        return sum(p.beats for p in pl) * per

    def key_index() -> int:
        return ids.index(key) if key in ids else max(range(len(plans)), key=lambda n: plans[n].clip.score)

    key_p = key_index()
    while len(plans) > 1 and drop_out + drop_dur(plans) + outro_est > max_duration:
        worst = min((n for n in range(len(plans)) if n != key_p), key=lambda n: (plans[n].clip.score, -n))
        dropped.append(plans.pop(worst).clip.id)
        ids.pop(worst)
        key_p = key_index()
    if op.short:
        # ~20–25 s: one bar of lead-in per fight (two fit only that way).
        for pl in plans:
            pl.lead_in = 4
            pl.lead_in_src = min(pl.lead_in_src, 4 * per * op.speed)
    recipes = assign_recipes(plans, key_p, allowed)
    for pl, recs in zip(plans, recipes):
        if recs and all(r == "stair_zoom" for r in recs):
            pl.gaps = [1] * len(pl.gaps)                       # one kill per beat
            _close_bar(pl)
    _align_climax(plans[key_p])
    if recipes[key_p] and recipes[key_p][-1] == "instant_replay":
        replay = (REPLAY_BACK + 0.3) / REPLAY_SPEED
        _close_bar(plans[key_p], math.ceil((REPLAY_SHOW + replay + 0.6) / per))
    for pl in plans:
        pl.tail_src = max(0.0, min(pl.clip.end - pl.hits[-1].t, pl.tail * per * op.speed))
    drop_beats = sum(p.beats for p in plans)
    outro_beats = 4 if 4 * per >= 2.5 else 8
    if op.ambient and any(len(c.ambient) for c in clips):
        outro_beats *= 2                  # half the nexus, half the stage or the crowd

    # Where the music starts: the drop act begins exactly on the song's drop,
    # or on the beat of music_start if it was given.
    warning, drop_aligned = None, False
    downs = {round(x, 3) for x in bm["downbeats"]}
    drop_t = bm.get("drop") if op.music_start is None else op.music_start
    if drop_t is None:
        drop_t = next((s["start"] for s in bm.get("sections", []) if s["type"] == "chorus"),
                      beats[len(beats) // 3])
    d0 = analysis.nearest_beat(beats, drop_t)
    if beats[d0] - drop_out >= 0:
        drop_aligned = op.music_start is None
    else:
        warning = ("La canción tiene poco antes de music_start: el acto drop arranca más adelante."
                   if op.music_start is not None else
                   "La canción tiene poco antes del drop: el drop del edit no cae en el de la canción.")
        d0 = next((i for i, b in enumerate(beats) if b >= drop_out and round(b, 3) in downs),
                  next((i for i, b in enumerate(beats) if b >= drop_out), len(beats) - 1))
    if d0 + drop_beats + outro_beats >= len(beats):
        missing = (d0 + drop_beats + outro_beats - len(beats) + 1) * per
        raise ValueError(f"La canción es corta para estas peleas (faltan {missing:.0f} s de música). "
                         "Bajá max_duration_s o elegí otra canción.")
    music_start = beats[d0] - drop_out

    def T(r: int) -> float:
        return drop_out + beats[d0 + r] - beats[d0]

    # silence: 0.3 s of black over the first frame of the drop.
    first = plans[0]
    x0 = first.hits[0].t - first.lead_in_src
    segments.append(Segment(first.clip.source, [Piece(x0, x0, SILENCE_S)], "silence", first.clip.vf))
    events.append(Event(silence_start, "black", dur=SILENCE_S))

    end_src: list[float] = []
    r = 0
    for n, (pl, recs) in enumerate(zip(plans, recipes)):
        c = pl.clip
        r0 = r
        h = pl.hits
        pieces: list[Piece] = []
        r = r0 + pl.lead_in
        pieces += _fill(h[0].t - pl.lead_in_src, h[0].t, T(r) - T(r0), _constant, "start")
        stair_step = 0
        for j, (a, b, nb) in enumerate(zip(h, h[1:] + [None], pl.gaps + [pl.tail])):
            rec = recs[j]
            end = b.t if b else a.t + pl.tail_src
            d = T(r + nb) - T(r)
            t_kill = T(r)
            x, y = focus(a)
            strong = n == key_p and b is None
            multi = a.count >= 2
            if rec == "instant_replay":
                # The kill, then rewind REPLAY_BACK s and see it again at 0.4× and zoomed.
                show = min(REPLAY_SHOW, d * 0.2)
                replay = min((REPLAY_BACK + 0.3) / REPLAY_SPEED, max(0.1, d - show - 0.1))
                back = max(c.start, a.t - REPLAY_BACK)
                pieces += [Piece(a.t, a.t + show, show), Piece(back, back + replay * REPLAY_SPEED, replay)]
                rest = d - show - replay
                if rest > 1e-3:
                    pieces += _fill(a.t + show, max(end, a.t + show), rest, _constant, "end")
                events += [Event(t_kill, "flash", 1.0, 0.16), Event(t_kill, "shake", 1.0, 0.4),
                           Event(t_kill, "rgb", 1.0, 0.3),
                           Event(t_kill + show, "hold", dur=replay, level=REPLAY_ZOOM, x=x, y=y)]
            elif rec == "freeze_zoom":
                pieces += [Piece(a.t, a.t, FREEZE_S)] + _fill(a.t, end, d - FREEZE_S, _constant, "end")
                events += [Event(t_kill, "flash", 0.6, 0.14),
                           Event(t_kill, "hold", dur=FREEZE_S + 0.25, level=FREEZE_ZOOM, x=x, y=y),
                           Event(t_kill + FREEZE_S, "shake", 0.4, 0.25)]
            elif rec == "ramp":
                pieces += _fill(a.t, end, d, _ramp_recipe, "end")
                events += [Event(t_kill, "flash", 0.75 if multi else 0.55, 0.16),
                           Event(t_kill, "zoom", 0.6, 0.22, x=x, y=y), Event(t_kill, "shake", 0.5, 0.25)]
            elif rec == "stair_zoom":
                pieces += _fill(a.t, end, d, _constant, "end")
                events += [Event(t_kill, "hold", dur=d if b else per, level=STAIR[min(stair_step, 3)], x=x, y=y),
                           Event(t_kill, "flash", 0.45, 0.12), Event(t_kill, "shake", 0.3, 0.2)]
                stair_step += 1
            else:                                          # hard_cut: no slow motion
                pieces += _fill(a.t, end, d, _constant, "end")
                events += [Event(t_kill, "flash", 0.45, 0.12), Event(t_kill, "shake", 0.35, 0.2)]
            if strong and rec != "instant_replay":
                events.append(Event(t_kill, "rgb", 1.0, 0.3))
            if op.text in ("kills", "both") and a.player:
                text_end = T(r + nb) if b else t_kill + max(per * 2, 1.0)
                texts.append(Text(t_kill, min(text_end, t_kill + 1.6), a.player, "kill", sub=a.champion))
            kills_info.append({"clip": c.id, "t": round(t_kill, 3), "recipe": rec, "count": a.count,
                               "player": a.player, "champion": a.champion})
            r += nb
        mk = _multikill(c.multikill)
        if mk and op.text in ("kills", "both"):
            t_last = kills_info[-1]["t"]
            texts.append(Text(t_last + 0.05, t_last + max(per * 3, 1.4), mk, "multikill"))
        pieces = timing.clamp_speeds(pieces, 0.0, timing.MAX_SPEED)
        segments.append(Segment(c.source, pieces, c.id, c.vf))
        events.append(Event(T(r0), "whip", 0.8, WHIP_DUR) if n > 0 else Event(T(r0), "flash", 1.0, 0.2))
        end_src.append(max(p.end for p in pieces))
    drop_end = T(drop_beats)
    acts.append({"act": "silence", "start": silence_start, "end": drop_out, "clips": [],
                 "music": 0.0, "game": 0.0})
    acts.append({"act": "drop", "start": drop_out, "end": drop_end,
                 "clips": [p.clip.id for p in plans], "music": 1.0, "game": GAME_DROP})

    # outro: the tail (the nexus) in real time, frozen if it runs out: of the
    # LAST fight when chronological (it goes on, it does not repeat the key one),
    # of the key one if not. With ambient shots, its second half is the stage
    # or the crowd (better: the reaction right after that fight).
    oi = len(plans) - 1 if chrono else key_p
    oc = plans[oi].clip
    src = end_src[oi]
    od = T(drop_beats + outro_beats) - drop_end
    shot = outro_shot
    nexus_d = T(drop_beats + outro_beats // 2) - drop_end if shot else od
    avail = max(0.0, oc.end - src)
    if avail >= nexus_d:
        outro = [Piece(src, src + nexus_d, nexus_d)]
    elif avail > 0.05:
        outro = [Piece(src, oc.end, avail), Piece(oc.end, oc.end, nexus_d - avail)]
    else:
        outro = [Piece(src, src, nexus_d)]
    segments.append(Segment(oc.source, outro, f"{oc.id}:outro", oc.vf))
    outro_clips = [oc.id]
    if shot:
        i, a, b = shot
        c = clips[i]
        amb_d = od - nexus_d
        pieces = [Piece(a, a + amb_d, amb_d)] if b - a >= amb_d else [Piece(a, b, b - a), Piece(b, b, amb_d - (b - a))]
        events.append(Event(drop_end + nexus_d, "whip", 0.6, WHIP_DUR))
        segments.append(Segment(c.source, pieces, f"{c.id}:ambient", c.ambient_vf or c.vf))
        outro_clips.append(f"{c.id}:ambient")
    end = drop_end + od
    acts.append({"act": "outro", "start": drop_end, "end": end, "clips": outro_clips,
                 "music": 1.0, "game": [GAME_DROP, 1.0]})

    # Volumes per act (short ramps: no clicks).
    pre = MUSIC_BUILD if build else MUSIC_COLD
    music_gain = [(0.0, MUSIC_COLD)]
    if build:
        music_gain += [(build_start, MUSIC_COLD), (build_start + 0.3, MUSIC_BUILD)]
    music_gain += [(silence_start - RAMP_S, pre), (silence_start, 0.0), (drop_out - 0.01, 0.0), (drop_out, 1.0)]
    original_gain = [(0.0, 1.0), (silence_start - RAMP_S, 1.0), (silence_start, 0.0), (drop_out - 0.01, 0.0),
                     (drop_out, GAME_DROP), (drop_end, GAME_DROP), (end, 1.0)]

    # Music accents and lyrics: only from the drop on (never over Ibai).
    win = analysis.window(bm, beats[d0], end - drop_out)
    out_kills = [k["t"] for k in kills_info]
    if op.music_accents:
        for x in win["downbeats"]:
            t_d = drop_out + x
            if analysis.section_at(win, x) == "chorus" and all(abs(t_d - k) > per for k in out_kills):
                events.append(Event(t_d, "zoom", 0.3, 0.2))
    if op.text in ("lyrics", "both"):
        taken = [(tx.start, tx.end) for tx in texts]
        words = win.get("words") or []
        for w, nxt in zip(words, words[1:] + [None]):
            ws = drop_out + w["start"]
            we = drop_out + min(nxt["start"] if nxt else w["end"] + 0.3, w["start"] + 1.2)
            if we - ws < 0.08 or any(a < we and ws < b for a, b in taken):
                continue
            word = re.sub(r"[^\w'¿?¡!áéíóúñüÁÉÍÓÚÑÜ-]", "", w["text"])
            if word:
                texts.append(Text(ws, we, word, "word"))
    if op.title:
        texts.append(Text(0.0, min(2.5, voice_end), op.title, "title"))

    edl = Edl(segments, width=op.width, height=op.height, fps=op.fps, events=events,
              texts=sorted(texts, key=lambda tx: tx.start), music_start=music_start,
              music_gain=music_gain, original_gain=original_gain, fade_out=od,
              subtitle_until=voice_end)
    used = [clips[cold].id] + [clips[i].id for i in build] + [p.clip.id for p in plans]
    acts = [{**a, "start": round(a["start"], 3), "end": round(a["end"], 3)} for a in acts]
    return EditResult(edl, music_start, drop_aligned, list(dict.fromkeys(used)), dropped, warning,
                      acts=acts, kills=kills_info, voice_spans=voice, subtitle_until=voice_end)
