"""One decode per span (layout.Scan): the same answers as reading the file once per
function, with a single FFmpeg run (synthetic 1920x1080 60 fps video)."""
import os
import unittest
from unittest import mock

import numpy as np

from core import ffmpeg_runner
from modules.competitivo import clock, layout
from tests import synthetic as S

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "clock")


class ScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("scan")
        cls.span = S.shots_video(os.path.join(cls.tmp, "span.mp4"), [4.0, 4.0], w=1920, h=1080, fps=60)

    @classmethod
    def tearDownClass(cls):
        S.remove(cls.tmp)

    def _count(self):
        """How many FFmpeg runs read frames out of the file."""
        calls = []
        real = ffmpeg_runner.capture

        def spy(cmd, *a, **k):
            calls.append(cmd)
            return real(cmd, *a, **k)
        return calls, mock.patch.object(ffmpeg_runner, "capture", side_effect=spy)

    def test_the_whole_analysis_is_one_ffmpeg_run(self):
        calls, spy = self._count()
        with spy:
            scan = layout.Scan(self.span, layout.SCAN_EDIT)
            layout.cambios_kills(self.span, scan=scan)
            layout.analizar(self.span, 2.0, 0.0, 8.0, scan=scan)
            layout.detect_camera_box(self.span, scan=scan)
            layout.split_ranges(self.span, scan=scan)
            layout.ambient_ranges(self.span, scan=scan)
            layout.action_track(self.span, 3.0, scan=scan)
        self.assertEqual(len(calls), 1)

    def test_same_answers_as_one_run_per_function(self):
        scan = layout.Scan(self.span, layout.SCAN_EDIT)
        self.assertEqual(layout.cambios_kills(self.span), layout.cambios_kills(self.span, scan=scan))
        self.assertEqual(layout.detect_camera_box(self.span), layout.detect_camera_box(self.span, scan=scan))
        self.assertEqual(layout.split_ranges(self.span), layout.split_ranges(self.span, scan=scan))
        self.assertEqual(layout.ambient_ranges(self.span), layout.ambient_ranges(self.span, scan=scan))
        alone, shared = layout.analizar(self.span, 2.0, 0.0, 8.0), layout.analizar(self.span, 2.0, 0.0, 8.0, scan=scan)
        self.assertEqual(alone, shared)
        a, b = layout.action_track(self.span, 3.0), layout.action_track(self.span, 3.0, scan=scan)
        self.assertEqual((a is None, b is None), (False, False))
        # the window of frames is cut by index instead of by seek: the same place, give or take a frame
        self.assertAlmostEqual(a["u"], b["u"], delta=0.08)
        self.assertAlmostEqual(a["v"], b["v"], delta=0.08)

    def test_what_was_not_asked_for_is_decoded_when_read(self):
        scan = layout.Scan(self.span, ("marc_k",))
        self.assertFalse(scan.has("strip"))
        calls, spy = self._count()
        with spy:
            frames = scan.get("strip")
        self.assertTrue(scan.has("strip") and len(calls) == 1)
        self.assertEqual(frames.shape[1:], (layout.JUEGO[3] // 4, layout.SPLIT_X[1] - layout.SPLIT_X[0]))

    def test_another_rate_or_part_gets_its_own_decode(self):
        scan = layout.Scan(self.span, ("marc_k", "kill_izq", "kill_der"))
        calls, spy = self._count()
        with spy:
            layout.cambios_kills(self.span, fps=5, scan=scan)                   # not the rate of the scan
            layout.split_ranges(self.span, ss=1.0, dur=3.0, scan=scan)           # another part of the file
        self.assertEqual(len(calls), 2)

    def test_cpu_and_gpu_decode_give_the_same_pixels(self):
        if not ffmpeg_runner.cuda_decode_supported(ffmpeg_runner.probe_video_stream(self.span)):
            self.skipTest("sin NVDEC")
        cpu = layout.Scan(self.span, layout.SCAN_EDIT, hw=False)
        gpu = layout.Scan(self.span, layout.SCAN_EDIT, hw=True)
        for name in layout.SCAN_EDIT:
            self.assertTrue(np.array_equal(cpu.raw(name), gpu.raw(name)), name)

    def test_a_gpu_failure_goes_again_on_the_cpu(self):
        real = ffmpeg_runner.capture
        runs = []

        def flaky(cmd, *a, **k):
            runs.append("-hwaccel" in cmd)
            if "-hwaccel" in cmd:
                raise RuntimeError("FFmpeg falló: CUDA_ERROR_OUT_OF_MEMORY")
            return real(cmd, *a, **k)
        with mock.patch.object(ffmpeg_runner, "cuda_decode_supported", return_value=True), \
                mock.patch.object(ffmpeg_runner, "capture", side_effect=flaky):
            scan = layout.Scan(self.span, ("marc_k",))
        self.assertEqual(runs, [True, False])
        self.assertGreater(len(scan.get("marc_k")), 10)


class ClockFromScan(unittest.TestCase):
    """clock_at reads the 4 fps frames of a Scan by index (0.25 s apart)."""

    class FakeScan:
        def __init__(self, frames):
            self.frames = frames

        def get(self, name):
            assert name == "clock"
            return self.frames

    def _png(self, name):
        import cv2
        return cv2.imread(os.path.join(FIXTURES, f"{name}.png"), cv2.IMREAD_GRAYSCALE).astype(np.float32)

    def test_reads_by_index(self):
        a, b = self._png("14-33"), self._png("24-33")
        # seconds 0..1.75 show 14:33; from 2.0 on, 24:33
        frames = np.stack([a] * 8 + [b] * 8)
        self.assertEqual(clock.clock_at("x", 0.0, scan=self.FakeScan(frames)), 14 * 60 + 33 + 0.5)
        self.assertEqual(clock.clock_at("x", 3.0, scan=self.FakeScan(frames)), 24 * 60 + 33 + 0.5)
        # past the end of the frames: not enough readings
        self.assertIsNone(clock.clock_at("x", 100.0, scan=self.FakeScan(frames)))

    def test_calibrate_with_a_scan(self):
        a = self._png("14-33")
        frames = np.stack([a] * 40)                                  # the same second all along
        zero = clock.calibrate("x", [3.0, 6.0, 9.0], scan=self.FakeScan(frames))
        self.assertEqual(zero, round(float(np.median([3.0 - 873.5, 6.0 - 873.5, 9.0 - 873.5])), 2))


if __name__ == "__main__":
    unittest.main()
