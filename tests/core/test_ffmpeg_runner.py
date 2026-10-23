"""Tests of core/ffmpeg_runner.py that matter on Windows (no internet)."""
import glob
import os
import subprocess
import tempfile
import unittest

import config
from core import ffmpeg_runner, job_manager

# ~40,000 characters: past Windows' 32,767-character limit for a command line.
LONG_GRAPH = ",".join(["null"] * 8000)


def _leftover_graphs():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "reelforge-*.ffgraph")))


class ExprSum(unittest.TestCase):
    def test_ffmpeg_parses_any_number_of_terms(self):
        self.assertEqual(ffmpeg_runner.expr_sum([]), "0")
        self.assertEqual(ffmpeg_runner.expr_sum(["a", "b"]), "a+b")
        for n in (99, 100, 1000, 10000):
            terms = [f"between(t,{i * 0.001:.3f},{i * 0.001 + 0.1:.3f})" for i in range(n)]
            expr = ffmpeg_runner.expr_sum(terms)
            self.assertEqual(expr.count("between("), n)
            # Flat, FFmpeg rejects 100+ terms; grouped, every size parses and runs.
            ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-f", "lavfi", "-i",
                                   "testsrc=s=64x64:d=0.2", "-vf", f"eq=brightness='0.001*({expr})':eval=frame",
                                   "-f", "null", "-"])


class LongGraphs(unittest.TestCase):
    def test_the_graph_goes_through_a_file(self):
        cmd, temps = ffmpeg_runner._spill_long_graphs(
            [config.FFMPEG_PATH, "-i", "x", "-filter_complex", LONG_GRAPH, "-vf", "null", "out.mp4"])
        try:
            self.assertEqual(cmd[3], "-/filter_complex")
            self.assertEqual(cmd[5:7], ["-vf", "null"])            # short ones stay inline
            with open(cmd[4], encoding="utf-8") as fh:
                self.assertEqual(fh.read(), LONG_GRAPH)
        finally:
            ffmpeg_runner._remove(temps)

    def test_long_graph_runs_on_windows(self):
        before = _leftover_graphs()
        base = [config.FFMPEG_PATH, "-v", "error", "-f", "lavfi", "-i", "testsrc=s=64x64:d=0.2",
                "-filter_complex", f"[0:v]{LONG_GRAPH}[v]", "-map", "[v]", "-f", "null", "-"]
        # Inline, Windows can't even start the process.
        if os.name == "nt":
            with self.assertRaises(OSError):
                subprocess.run(base, capture_output=True)
        ffmpeg_runner.capture(base)
        job = job_manager.create_job("x", output_path="")
        ffmpeg_runner.run(base, job)
        self.assertEqual(job_manager.get_job(job)["progress"], 100)
        self.assertEqual(_leftover_graphs() - before, set())         # temp files removed

    def test_files_removed_when_ffmpeg_fails(self):
        before = _leftover_graphs()
        bad = [config.FFMPEG_PATH, "-v", "error", "-f", "lavfi", "-i", "testsrc=s=64x64:d=0.2",
               "-vf", LONG_GRAPH + ",no_such_filter", "-f", "null", "-"]
        with self.assertRaises(RuntimeError):
            ffmpeg_runner.capture(bad)
        with self.assertRaises(RuntimeError):
            ffmpeg_runner.run(bad, job_manager.create_job("x", output_path=""))
        self.assertEqual(_leftover_graphs() - before, set())


if __name__ == "__main__":
    unittest.main()
