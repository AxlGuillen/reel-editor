"""The tests run real jobs (with the network mocked): their metrics, their phase-1 sessions, the
anchors and the tournament's stats go to temporary places, never to the user's own (a run used to
leave a hundred sessions of the fixture game next to the real ones)."""
import atexit
import os
import shutil
import tempfile

import config
from modules.competitivo import metrics

_tmp = tempfile.mkdtemp(prefix="reelforge-metrics-")
atexit.register(shutil.rmtree, _tmp, True)
metrics.FILE = os.path.join(_tmp, "jobs.jsonl")
config.SESSIONS_FOLDER = os.path.join(_tmp, "sessions")
config.ANCHORS_FOLDER = os.path.join(_tmp, "anchors")
config.OUTPUT_FOLDER = os.path.join(_tmp, "outputs")      # processor.TRABAJO is taken from it when it is imported

from modules.competitivo import cards, stats  # noqa: E402

stats.STORE = os.path.join(_tmp, "stats", "games.json")
cards.CACHE = os.path.join(_tmp, "cards-cache")
