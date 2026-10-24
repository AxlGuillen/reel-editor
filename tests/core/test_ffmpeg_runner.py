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


class NvencLimit(unittest.TestCase):
    """NVENC allows a few sessions at once: 7 edits at the same time got
    "Invalid argument (-22)". At most NVENC_MAX encodes on the GPU and a
    failed NVENC encode goes again with libx264."""
    NV = ["ffmpeg", "-i", "a.mp4", "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "20",
          "-b:v", "0", "-maxrate", "16M", "-c:a", "aac", "out.mp4"]

    def test_cpu_fallback(self):
        self.assertEqual(ffmpeg_runner.cpu_fallback(self.NV),
                         ["ffmpeg", "-i", "a.mp4", "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                          "-maxrate", "16M", "-c:a", "aac", "out.mp4"])
        self.assertTrue(ffmpeg_runner.nvenc_failed("[h264_nvenc @ 0x1] OpenEncodeSessionEx failed: out of memory"))
        self.assertTrue(ffmpeg_runner.nvenc_failed("Error while opening encoder - maybe incorrect parameters"))
        self.assertFalse(ffmpeg_runner.nvenc_failed("No such filter: 'blurr'"))

    def test_a_failed_nvenc_encode_goes_again_on_the_cpu(self):
        calls = []

        def fake(command, *args):
            calls.append(command)
            if "h264_nvenc" in command:
                raise RuntimeError("FFmpeg falló (code -22):\n[h264_nvenc @ x] InitializeEncoder failed: "
                                   "invalid param (8)\nError while opening encoder")
        job = job_manager.create_job("x", "")
        from unittest import mock
        with mock.patch.object(ffmpeg_runner, "_run", side_effect=fake):
            ffmpeg_runner.run(self.NV, job)
        self.assertEqual([("h264_nvenc" in c, "libx264" in c) for c in calls], [(True, False), (False, True)])
        with mock.patch.object(ffmpeg_runner, "_run", side_effect=RuntimeError("No such filter: 'blurr'")):
            with self.assertRaises(RuntimeError):
                ffmpeg_runner.run(self.NV, job)                                # not NVENC's: no retry

    def test_at_most_nvenc_max_at_once(self):
        import threading
        import time
        from unittest import mock
        lock, now, peak = threading.Lock(), [0], [0]

        def slow(*args):
            with lock:
                now[0] += 1
                peak[0] = max(peak[0], now[0])
            time.sleep(0.15)
            with lock:
                now[0] -= 1
        job = job_manager.create_job("x", "")
        with mock.patch.object(ffmpeg_runner, "_run", side_effect=slow), \
                mock.patch.object(ffmpeg_runner, "_capture", side_effect=lambda *a: slow() or b""):
            threads = [threading.Thread(target=ffmpeg_runner.run, args=(self.NV, job)) for _ in range(4)]
            threads += [threading.Thread(target=ffmpeg_runner.capture, args=(self.NV,)) for _ in range(4)]
            cpu = [threading.Thread(target=ffmpeg_runner.run, args=(ffmpeg_runner.cpu_fallback(self.NV), job))
                   for _ in range(4)]
            for t in threads + cpu:
                t.start()
            for t in threads + cpu:
                t.join()
        self.assertEqual(peak[0], ffmpeg_runner.NVENC_MAX + 4)               # the CPU ones do not wait
