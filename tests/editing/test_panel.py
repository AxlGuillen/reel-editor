"""The manga edit's additions to the engine: the `panel` event over a frozen
frame, the "mix" flash, the video fade and the exact source phase."""
import os
import unittest

import numpy as np
from PIL import Image

from core import ffmpeg_runner
from modules.editing import effects, render as R
from modules.editing.edl import Edl, Event, Piece, Segment
from modules.motion import processor as motion
from tests import synthetic as S

W, H, FPS = 540, 960, 30


def frames(path: str) -> np.ndarray:
    raw = ffmpeg_runner.capture(motion.decode_command(path, 0, 10 ** 6, FPS, W, H), timeout=300)
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)[..., ::-1].astype(np.float32)


class PanelRender(unittest.TestCase):
    """2 s of a moving gray square, frozen 0.9 s at 1.0 s with a red panel
    (400×200) over it, a 55 % flash at 0.5 s and a 0.5 s fade at the end."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("panel")
        cls.video = S.shots_video(os.path.join(cls.tmp, "v.mp4"), [3.0], with_audio=False)
        cls.png = os.path.join(cls.tmp, "panel.png")
        Image.new("RGB", (400, 200), (20, 230, 60)).save(cls.png)    # green: the video's first shot is red
        segs = [Segment(cls.video, [Piece(0.0, 1.0, 1.0)], "a"),
                Segment(cls.video, [Piece(0.95, 0.95, 0.9)], "hold"),
                Segment(cls.video, [Piece(1.0, 2.1, 1.1)], "b")]
        cls.edl = Edl(segs, width=W, height=H, fps=FPS, look="neutral",
                      events=[Event(1.0, "panel", dur=0.9, image=cls.png),
                              Event(0.5, "flash", intensity=0.55, dur=0.07, shape="mix")],
                      video_fade_out=0.5, fade_out=0)
        cls.out = os.path.join(cls.tmp, "out.mp4")
        R.render(cls.edl, cls.out, None, os.path.join(cls.tmp, "work"))
        cls.fr = frames(cls.out)
        cls.base = effects.panel_size((400, 200), W, H)

    @classmethod
    def tearDownClass(cls):
        S.remove(cls.tmp)

    def red_width(self, k: int) -> int:
        f = self.fr[k]
        green = (f[..., 1] > 170) & (f[..., 0] < 90) & (f[..., 2] < 120)
        cols = np.nonzero(green.mean(axis=0) > 0.05)[0]
        return int(cols.max() - cols.min() + 1) if cols.size else 0

    def test_panel_window_is_frame_exact(self):
        self.assertEqual(self.red_width(29), 0)
        self.assertGreater(self.red_width(30), 0)           # 1.0 s = frame 30
        self.assertGreater(self.red_width(56), 0)           # last frame of the 0.9 s (27 frames)
        self.assertEqual(self.red_width(57), 0)

    def test_panel_enters_and_keeps_growing(self):
        w0 = self.base[0]
        self.assertAlmostEqual(self.red_width(30), 0.93 * w0, delta=6)
        self.assertAlmostEqual(self.red_width(34), (1 + 0.035 * 4 / 27) * w0, delta=6)   # 0.12 s in: full size
        self.assertAlmostEqual(self.red_width(56), (1 + 0.035 * 26 / 27) * w0, delta=6)
        widths = [self.red_width(k) for k in range(30, 57)]
        self.assertEqual(widths, sorted(widths))

    def test_frame_behind_the_panel_darkens_to_062(self):
        corner = lambda k: self.fr[k][:120, :120].mean()   # noqa: E731 - away from the panel and the square
        self.assertAlmostEqual(corner(40) - 16 * 255 / 219 * 0, corner(28) * 0.62, delta=0.08 * corner(28) + 3)

    def test_mix_flash_lasts_its_frames(self):
        mean = self.fr.mean(axis=(1, 2, 3))
        a, b = effects.frames_of(0.5, 0.07, FPS)
        self.assertEqual((a, b), (15, 17))
        for k in (a, b):
            expected = mean[14] + (255 - mean[14]) * 0.55
            self.assertAlmostEqual(mean[k], expected, delta=8)
        self.assertLess(abs(mean[18] - mean[14]), 6)

    def test_fade_to_black(self):
        mean = self.fr.mean(axis=(1, 2, 3))
        self.assertEqual(len(mean), 90)
        self.assertLess(mean[-1], 22)
        self.assertGreater(mean[-16], mean[-8] + 10)


class ExactPhase(unittest.TestCase):
    def test_setpts_keeps_the_requested_start(self):
        seg = Segment("x.mp4", [Piece(10.0, 11.0, 1.0)], exact_phase=True)
        cmd = " ".join(R.build_pass1(seg, "o.mkv", W, H, FPS, False, 23.976))
        self.assertIn("setpts=(PTS-0.050000/TB)/1.000000", cmd)
        seg.exact_phase = False
        cmd = " ".join(R.build_pass1(seg, "o.mkv", W, H, FPS, False, 23.976))
        self.assertIn("setpts=(PTS-STARTPTS)/1.000000", cmd)


class PanelValidation(unittest.TestCase):
    def test_missing_image(self):
        edl = Edl([Segment("x.mp4", [Piece(0, 1, 1)])], events=[Event(0.2, "panel", dur=0.5, image="no.png")])
        with self.assertRaisesRegex(ValueError, "panel"):
            edl.validate()

    def test_unknown_flash_shape(self):
        edl = Edl([Segment("x.mp4", [Piece(0, 1, 1)])], events=[Event(0.2, "flash", shape="star")])
        with self.assertRaisesRegex(ValueError, "destello"):
            edl.validate()


if __name__ == "__main__":
    unittest.main()
