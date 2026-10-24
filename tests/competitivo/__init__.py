"""The tests run real jobs (with the network mocked): their metrics go to a
temporary file, never to the user's records in COMPETITIVO_FOLDER."""
import atexit
import os
import shutil
import tempfile

from modules.competitivo import metrics

_tmp = tempfile.mkdtemp(prefix="reelforge-metrics-")
atexit.register(shutil.rmtree, _tmp, True)
metrics.FILE = os.path.join(_tmp, "jobs.jsonl")
