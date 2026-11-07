"""The motion map on synthetic videos whose truth is known (tests/synthetic.py):
the frame of the hit, its direction, the cadence, the camera, flashes, cuts,
impact frame bursts, frame-exact spans and the cache."""
import os
import subprocess
import unittest
from unittest import mock

import numpy as np

import config
from modules.anime_edit import shots
from modules.motion import analysis, processor
from tests import synthetic

HIT = 36


def angle_error(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


class MotionSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = synthetic.tmpdir("motion")
        cls._folder = config.MOTION_FOLDER
        config.MOTION_FOLDER = os.path.join(cls.dir, "cache")

    @classmethod
    def tearDownClass(cls):
        config.MOTION_FOLDER = cls._folder
        synthetic.remove(cls.dir)

    def video(self, name: str, **kw) -> str:
        path = os.path.join(self.dir, f"{name}.mp4")
        if not os.path.isfile(path):
            synthetic.motion_video(path, hit=HIT, **kw)
        return path

    def impacts(self, m: dict) -> list[dict]:
        return [e for e in m["events"] if e["type"] == "impact"]

    def test_direction_and_exact_hit_frame(self):
        for angle in (0, 45, 135, 200, 270, 315):
            with self.subTest(angle=angle):
                m = processor.get(self.video(f"a{angle}", angle=angle))
                hits = self.impacts(m)
                self.assertEqual([e["f"] for e in hits], [HIT])
                self.assertLess(angle_error(hits[0]["angle"], angle), 15)
                self.assertEqual(hits[0]["dir"], analysis.direction(angle))

    def test_on_twos_counts_repeats_and_makes_no_comb(self):
        m = processor.get(self.video("twos", angle=0, cadence=2, drift=1.0, speed=25))
        self.assertAlmostEqual(m["held_ratio"], 0.5, delta=0.06)
        self.assertEqual([e["f"] for e in self.impacts(m)], [HIT])
        # the drift is spread over both frames of each drawing: no peak every 2 frames
        fr = m["frames"]
        drift = [fr["force"][k] for k in range(4, HIT - 6)]
        self.assertLess(max(drift) - min(drift), 0.5 * max(max(drift), 1.0))

    def test_camera_is_removed_from_the_character(self):
        m = processor.get(self.video("pan", angle=160, pan=(6, 0)))
        hits = self.impacts(m)
        self.assertEqual([e["f"] for e in hits], [HIT])
        self.assertLess(angle_error(hits[0]["angle"], 160), 15)
        pans = [e for e in m["events"] if e["type"] == "pan"]
        self.assertTrue(pans)
        self.assertEqual(pans[0]["dir"], "derecha")      # the crop moves right: the camera pans right
        self.assertGreater(pans[0]["frames"], 50)

    def test_flash_after_contact(self):
        m = processor.get(self.video("flash", angle=90, flash=True))
        flashes = [e["f"] for e in m["events"] if e["type"] == "flash"]
        self.assertIn(HIT + 1, flashes)
        self.assertNotIn(HIT + 1, [e["f"] for e in m["events"] if e["type"] == "cut"])
        hits = self.impacts(m)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["f"], HIT + 1)           # with a flash right after contact, the hit is the flash
        self.assertIn("flash", hits[0]["signals"])

    def test_cut_is_found_on_its_frame(self):
        m = processor.get(self.video("cut", angle=60, cut_at=50))
        self.assertEqual([e["f"] for e in m["events"] if e["type"] == "cut"], [50])
        self.assertEqual([(s["start"], s["end"]) for s in m["shots"]], [(0.0, round(50 / 24, 3)),
                                                                          (round(50 / 24, 3), 3.0)])
        self.assertEqual([e["f"] for e in self.impacts(m)], [HIT])

    def test_impact_frame_burst_is_not_a_cut(self):
        m = processor.get(self.video("burst", angle=0, burst=(HIT + 6, HIT + 8)))
        self.assertEqual([e for e in m["events"] if e["type"] == "cut"], [])
        hits = {e["f"]: e for e in self.impacts(m)}
        self.assertIn(HIT, hits)
        self.assertIn(HIT + 6, hits)
        self.assertIn("impact_frame", hits[HIT + 6]["signals"])

    def test_span_keeps_file_frame_numbers(self):
        path = self.video("a45", angle=45)
        whole = processor.get(path)
        span = processor.get(path, 1.0, 2.5)
        self.assertEqual(span["f0"], 24)
        self.assertEqual(span["frames_count"], 36)
        self.assertEqual([e["f"] for e in self.impacts(span)], [e["f"] for e in self.impacts(whole)])
        self.assertAlmostEqual(span["start"], 1.0, places=3)

    def test_cache_is_reused(self):
        path = self.video("a45", angle=45)
        first = processor.get(path)
        with mock.patch.object(analysis, "analyze", side_effect=AssertionError("re-analyzed")):
            again = processor.get(path)
        self.assertEqual(first["events"], again["events"])

    def test_summary_lines(self):
        m = processor.get(self.video("a45", angle=45))
        lines = processor.summary(m)
        self.assertIn("cuadros a 24.000 fps", lines[0])
        self.assertRegex(lines[1], r"^toma 1 · golpe (fuerte|medio|suave) en 1\.50 s \(cuadro 36\), "
                                   r"hacia arriba a la derecha, centro \(0\.\d\d, 0\.\d\d\)$")
        cx, cy = processor.focus_at(m, HIT / 24)
        self.assertTrue(0.3 < cx < 0.8 and 0.2 < cy < 0.8)

    def test_speed_at_480(self):
        m = processor.get(self.video("a45", angle=45), refresh=True)
        self.assertGreater(m["fps_analysis"], 100)


class DecodeIsFrameExact(unittest.TestCase):
    """An input seek alone dropped or repeated frames on a real mkv; decode_command
    selects on the file timestamps. Frame k of this video has brightness 3k."""

    @classmethod
    def setUpClass(cls):
        cls.dir = synthetic.tmpdir("motion-decode")
        cls.path = os.path.join(cls.dir, "ramp.mkv")
        frames = np.stack([np.full((90, 160, 3), 3 * k, np.uint8) for k in range(84)])
        subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "160x90",
                        "-r", "24000/1001", "-i", "-", "-c:v", "libx264", "-g", "48", "-crf", "1", "-pix_fmt",
                        "yuv420p", cls.path], input=frames.tobytes(), check=True)

    @classmethod
    def tearDownClass(cls):
        synthetic.remove(cls.dir)

    def test_first_frame_is_f0(self):
        from core import ffmpeg_runner
        fps = 24000 / 1001
        for f0 in (0, 1, 47, 48, 49, 77):
            cmd = processor.decode_command(self.path, f0, 3, fps, 160, 90)
            got = [float(np.frombuffer(c, np.uint8).mean()) for c in ffmpeg_runner.stream(cmd, 160 * 90 * 3)]
            self.assertEqual([round(g / 3) for g in got], [f0, f0 + 1, f0 + 2], f0)


class Routes(unittest.TestCase):
    def setUp(self):
        import app
        self.client = app.create_app().test_client()

    def test_validation_is_a_readable_400(self):
        cases = [({}, "Falta el video"), ({"path": "C:/no-existe.mp4"}, "No existe"),
                 ({"path": __file__}, "extensión"), ({"start": "1:10", "end": "0:30", "path": "x"}, "mayor"),
                 ({"width": "9000"}, "fuera de rango")]
        for form, text in cases:
            with self.subTest(form=form):
                r = self.client.post("/api/motion/process", data=form)
                self.assertEqual(r.status_code, 400)
                self.assertIn(text, r.get_json()["error"])

    def test_unknown_job(self):
        self.assertEqual(self.client.get("/api/motion/status/nope").status_code, 404)
        self.assertEqual(self.client.get("/api/motion/sheet/nope").status_code, 404)


class ShotsSpan(unittest.TestCase):
    def test_cuts_in_a_span_and_from_frames(self):
        d = synthetic.tmpdir("motion-shots")
        try:
            path = synthetic.shots_video(os.path.join(d, "s.mp4"), [2.0, 2.0, 2.0], with_audio=False)
            self.assertEqual([round(c, 2) for c in shots.cuts(path, 1.0, 5.0)], [2.0, 4.0])
            self.assertEqual([round(c, 2) for c in shots.cuts(path, 2.5, 6.0)], [4.0])
        finally:
            synthetic.remove(d)


if __name__ == "__main__":
    unittest.main()
