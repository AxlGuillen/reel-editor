"""Slots per resource: the jobs of ReelForge take turns instead of all running at once.

Measured on 27/10: 13 transcriptions, 8 phase-1 jobs and 4 renders ran at the
same time and each came out slower than alone (four renders at once took 21 s
per clip against 15.5–18 s when they ran by themselves). A resource here is
something with a limit of its own, whatever the job:

    twitch    downloads from Twitch (it drops sections when there are too many)
    whisper   the speech-to-text model on the GPU (one at a time)
    analysis  the CPU-bound reading of a span (frames, counter, clock)
    nvenc     the video encodes on the GPU

A job takes a slot with `with slot("whisper", job_id):` and the others wait
their turn (first come, first served, or by `priority`: lower goes first).
The seconds a job waited are kept per job (`take_waits`) for its metrics.
Capacities come from config.SLOTS (and the REELFORGE_SLOTS_<NAME> variables).
No job holds a slot while it waits for another one (so they cannot lock each other).
"""
from __future__ import annotations

import heapq
import itertools
import threading
import time
from contextlib import contextmanager

import config

DEFAULTS = {"twitch": 4, "whisper": 1, "analysis": 2, "nvenc": 2}


class Resource:
    def __init__(self, name: str, capacity: int):
        self.name = name
        self.capacity = max(1, int(capacity))
        self.running = 0
        self.peak = 0
        self.runs = 0
        self.total_wait_s = 0.0
        self._cond = threading.Condition()
        self._queue: list[tuple[int, int]] = []      # (priority, ticket)
        self._tickets = itertools.count()

    def acquire(self, priority: int = 0) -> float:
        """Takes a slot (waits for its turn). Returns the seconds waited."""
        t0 = time.monotonic()
        with self._cond:
            ticket = (priority, next(self._tickets))
            heapq.heappush(self._queue, ticket)
            while not (self._queue[0] == ticket and self.running < self.capacity):
                self._cond.wait()
            heapq.heappop(self._queue)
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.runs += 1
            waited = time.monotonic() - t0
            self.total_wait_s += waited
            self._cond.notify_all()            # the next in line may fit too
        return waited

    def release(self) -> None:
        with self._cond:
            self.running -= 1
            self._cond.notify_all()

    def set_capacity(self, capacity: int) -> None:
        with self._cond:
            self.capacity = max(1, int(capacity))
            self._cond.notify_all()

    def snapshot(self) -> dict:
        with self._cond:
            return {"capacity": self.capacity, "running": self.running, "waiting": len(self._queue),
                    "peak": self.peak, "runs": self.runs, "total_wait_s": round(self.total_wait_s, 1)}


_resources: dict[str, Resource] = {}
_lock = threading.Lock()
_waits: dict[str, dict[str, float]] = {}


def capacity_of(name: str) -> int:
    return int(getattr(config, "SLOTS", {}).get(name, DEFAULTS.get(name, 1)))


def get(name: str) -> Resource:
    with _lock:
        if name not in _resources:
            _resources[name] = Resource(name, capacity_of(name))
        return _resources[name]


@contextmanager
def slot(name: str, job_id: str | None = None, priority: int = 0):
    """`with slot("whisper", job_id):` — waits for a free slot of that resource."""
    res = get(name)
    waited = res.acquire(priority)
    if job_id and waited >= 0.05:
        with _lock:
            mine = _waits.setdefault(job_id, {})
            mine[name] = round(mine.get(name, 0.0) + waited, 2)
    try:
        yield waited
    finally:
        res.release()


def take_waits(job_id: str) -> dict[str, float]:
    """The seconds the job waited for each resource since the last call."""
    with _lock:
        return _waits.pop(job_id, {})


def snapshot() -> dict[str, dict]:
    with _lock:
        names = sorted(set(_resources) | set(DEFAULTS))
    return {n: get(n).snapshot() for n in names}


def reset() -> None:
    """Forget every resource (the tests, after changing config.SLOTS)."""
    with _lock:
        _resources.clear()
        _waits.clear()
