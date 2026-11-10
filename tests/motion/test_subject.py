"""Subject tracking (phase 5): the chest from person and head boxes, the
`follow` overlay landing on its track frame by frame, the track ending at a
cut, and (when the ONNX models and the episode are on this machine) a real
detection on Dante's close-up."""
import os
import unittest
from unittest import mock

import numpy as np
from PIL import Image

from core import ffmpeg_runner
from modules.editing import panels, render as R
from modules.editing.edl import Edl, Event, Piece, Segment
from modules.motion import processor as motion, subject
from tests import synthetic as S

EPISODE = r"C:\Users\axl15\Downloads\[Anku & mutagenb] Black Clover 2nd Season - 05 (175) [1080p rus].mkv"


class Chest(unittest.TestCase):
    def test_from_the_head_inside_the_person(self):
        person = (100, 100, 500, 900, 0.9)
        head = (250, 120, 350, 260, 0.8)                  # 100 × 140
        x, y, w = subject.chest(person, [head, (700, 100, 800, 200, 0.9)])
        self.assertEqual((x, w), (300, 130))
        self.assertAlmostEqual(y, 260 + subject.CHEST_BELOW * 140)

    def test_without_a_head_and_close_up(self):
        self.assertEqual(subject.chest((0, 0, 400, 1000, 0.9), []), (200, 350, 140))
        x, y, w = subject.chest_from_head((300, 300, 800, 1000, 0.7), 1920)
        self.assertEqual((x, round(y), round(w)), (550, 1280, 650))


class Follow(unittest.TestCase):
    """A green square that the overlay follows along a diagonal track."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("follow")
        cls.video = S.shots_video(os.path.join(cls.tmp, "v.mp4"), [2.0], with_audio=False)
        cls.logo = os.path.join(cls.tmp, "logo.png")
        Image.new("RGBA", (40, 40), (20, 230, 60, 255)).save(cls.logo)
        cls.track = [[0.5 + k / 30, 100 + 8 * k, 200 + 5 * k, 40] for k in range(31)]
        edl = Edl([Segment(cls.video, [Piece(0, 2, 2)])], width=360, height=640, fps=30, fade_out=0,
                  events=[Event(0.5, "follow", dur=1.0, image=cls.logo, track=cls.track, opacity=1.0, fade=0)])
        cls.out = os.path.join(cls.tmp, "out.mp4")
        R.render(edl, cls.out, None, os.path.join(cls.tmp, "w"))
        raw = ffmpeg_runner.capture(motion.decode_command(cls.out, 0, 100, 30, 360, 640), timeout=120)
        cls.fr = np.frombuffer(raw, np.uint8).reshape(-1, 640, 360, 3)[..., ::-1].astype(np.float32)

    @classmethod
    def tearDownClass(cls):
        S.remove(cls.tmp)

    def center(self, k: int):
        f = self.fr[k]
        g = (f[..., 1] > 170) & (f[..., 0] < 90) & (f[..., 2] < 120)
        ys, xs = np.nonzero(g)
        return (float(xs.mean()), float(ys.mean())) if len(xs) > 200 else None

    def test_follows_its_track_on_every_frame(self):
        self.assertIsNone(self.center(14))
        for k in (15, 25, 35, 44):
            x, y, _ = panels.track_at(self.track, k / 30)
            c = self.center(k)
            self.assertIsNotNone(c, k)
            self.assertAlmostEqual(c[0], x, delta=2.5)
            self.assertAlmostEqual(c[1], y, delta=2.5)
        self.assertIsNone(self.center(45))

    def test_validation(self):
        edl = Edl([Segment(self.video, [Piece(0, 1, 1)])], events=[Event(0.1, "follow", dur=0.5, image=self.logo)])
        with self.assertRaisesRegex(ValueError, "track"):
            edl.validate()


class TrackEndsAtCut(unittest.TestCase):
    def test_rows_stop_before_the_first_cut(self):
        def fake_stream(cmd, size):
            for _ in range(10):
                yield bytes(size)

        with mock.patch.object(motion, "_geometry", return_value=(30.0, 10.0, 96, 54)), \
                mock.patch.object(ffmpeg_runner, "probe_resolution", return_value=(96, 54)), \
                mock.patch.object(ffmpeg_runner, "stream", side_effect=fake_stream), \
                mock.patch.object(subject, "detect", side_effect=lambda img, kind:
                                  [(40, 10, 60, 50, 0.9)] if kind == "person" else []), \
                mock.patch.object(motion, "get", return_value={"events": [{"type": "cut", "f": 36}]}):
            rows = subject.track("x.mp4", 1.0, 1.333, (50, 24))
        self.assertEqual([r["f"] for r in rows], list(range(30, 36)))
        self.assertTrue(all(r["detected"] for r in rows))


@unittest.skipUnless(subject.available() and os.path.isfile(EPISODE), "models or episode not on this machine")
class RealDetection(unittest.TestCase):
    def test_dante_close_up_has_a_head(self):
        raw = ffmpeg_runner.capture(motion.decode_command(EPISODE, 9000, 1, 24000 / 1001, 960, 540), timeout=120)
        img = np.frombuffer(raw, np.uint8).reshape(540, 960, 3)[..., ::-1]
        boxes = subject.detect(np.ascontiguousarray(img), "person")
        self.assertTrue(boxes)
        self.assertTrue(all(0 <= b[0] < b[2] <= 960 and 0 <= b[1] < b[3] <= 540 for b in boxes))


if __name__ == "__main__":
    unittest.main()
