"""The VOD spans of a fight, by scoreboard clock.

vod_s = clock + offset. The offset is measured once per VOD (competitivo's
anchor gives it: on T1 vs KC, 2584 s = when the VOD starts + Twitch's 44 s +
0.91 s). A downloaded span starts a little before what was asked: measure
its own offset (Span.offset) on a frame with the scoreboard clock."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

from modules.competitivo import fuente
from modules.tf_explained.plan import parse_clock


def download(vod_url: str, spans: dict[str, tuple], offset: float, folder: str, workers: int = 3) -> dict[str, str]:
    """{name: (clock_from, clock_to)} → {name: path}. Twitch downloads share
    competitivo's turns (core/scheduler.py)."""
    os.makedirs(folder, exist_ok=True)

    def one(item):
        name, (a, b) = item
        return name, fuente.bajar_tramo(vod_url, parse_clock(a) + offset, parse_clock(b) + offset,
                                        os.path.join(folder, f"{name}.mp4"))

    with ThreadPoolExecutor(workers) as ex:
        return dict(ex.map(one, spans.items()))
