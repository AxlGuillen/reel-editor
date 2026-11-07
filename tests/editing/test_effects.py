"""The effects bank (phase 3): every output effect starts on its frame and
lasts its duration (measured against the same render without events), the
direction of shake / whip, the angle of mblur, the hit of hit_ramp on its
frame, dropping repeated drawings before RIFE, the catalog and the gallery."""
import json
import os
import subprocess
import unittest

import cv2
import numpy as np
from PIL import Image

import config
from core import external, ffmpeg_runner
from modules.editing import effects, gallery, render as R, timing
from modules.editing.edl import Edl, Event, Piece, Segment
from modules.motion import processor as motion
from tests import synthetic as S

W, H, FPS = 360, 640, 30
DUR = 5.0


def frames(path: str) -> np.ndarray:
    raw = ffmpeg_runner.capture(motion.decode_command(path, 0, 10 ** 6, FPS, W, H), timeout=300)
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3).astype(np.float32)


def active(base: np.ndarray, fx: np.ndarray, lo: int, hi: int, thr: float = 2.0) -> list[int]:
    """Frames in [lo, hi] where the effect render differs from the base."""
    return [k for k in range(lo, hi + 1) if float(np.abs(fx[k] - base[k]).mean()) > thr]


def shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Mean optical flow (dx, dy) from frame a to frame b: a centered zoom (the
    shake's margin) averages out, a translation does not."""
    ga = cv2.cvtColor(a.astype(np.uint8), cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(b.astype(np.uint8), cv2.COLOR_BGR2GRAY)
    flow = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM).calc(ga, gb, None)
    m = 24                                    # away from the clipped borders
    return float(flow[m:-m, m:-m, 0].mean()), float(flow[m:-m, m:-m, 1].mean())


class OutputEffects(unittest.TestCase):
    """One render with every output effect at its own time, against the same
    render with no events: where they differ is where each effect acts."""

    EVENTS = [Event(0.3, "flash", dur=0.1), Event(0.8, "invert", dur=0.1),
              Event(1.2, "strobe", intensity=0.8, dur=0.3, every=2),
              Event(1.8, "glow", dur=0.3), Event(2.4, "shake", intensity=1.0, dur=0.3, angle=0.0),
              Event(3.0, "rgb", dur=0.1), Event(3.4, "black", dur=0.1),
              Event(3.8, "flash", intensity=0.55, dur=0.07, shape="mix"),
              Event(4.3, "shake", intensity=1.0, dur=0.3, angle=90.0)]

    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("effects")
        cls.video = S.motion_video(os.path.join(cls.tmp, "v.mp4"), hit=150, frames=160)   # still for 6 s
        seg = lambda: [Segment(cls.video, [Piece(0.0, DUR, DUR)], "a")]   # noqa: E731
        # a zero zoom puts zoompan in both renders (it resamples every frame)
        still = Event(0.0, "zoom", intensity=0.0)
        base = Edl(seg(), width=W, height=H, fps=FPS, look="neutral", fade_out=0, events=[still])
        fx = Edl(seg(), width=W, height=H, fps=FPS, look="neutral", fade_out=0, events=[still] + list(cls.EVENTS))
        cls.base_path, cls.fx_path = os.path.join(cls.tmp, "base.mp4"), os.path.join(cls.tmp, "fx.mp4")
        R.render(base, cls.base_path, None, os.path.join(cls.tmp, "w1"))
        R.render(fx, cls.fx_path, None, os.path.join(cls.tmp, "w2"))
        cls.base, cls.fx = frames(cls.base_path), frames(cls.fx_path)

    @classmethod
    def tearDownClass(cls):
        S.remove(cls.tmp)

    def window(self, t: float, dur: float) -> tuple[int, int]:
        return effects.frames_of(t, dur, FPS)

    def test_flash_pulse(self):
        on = active(self.base, self.fx, 5, 18)
        self.assertEqual(on[0], 9)                      # 0.3 s
        self.assertLessEqual(on[-1], 12)                # it fades within its 0.1 s

    def test_invert_frames(self):
        a, b = self.window(0.8, 0.1)
        self.assertEqual(active(self.base, self.fx, a - 3, b + 3), list(range(a, b + 1)))
        k = a + 1
        self.assertAlmostEqual(float((self.fx[k] + self.base[k]).mean()), 255, delta=12)

    def test_strobe_every_two_frames(self):
        a, b = self.window(1.2, 0.3)
        self.assertEqual(active(self.base, self.fx, a - 3, b + 3, thr=20), list(range(a, b + 1, 2)))

    def test_glow_pulse(self):
        a, b = self.window(1.8, 0.3)
        on = active(self.base, self.fx, a - 6, b + 6, thr=1.0)
        self.assertGreaterEqual(on[0], a - 1)           # the 30 ms attack: at most a frame early
        self.assertLessEqual(on[-1], b)
        self.assertGreater(float(self.fx[a + 1].mean()), float(self.base[a + 1].mean()) + 5)

    def test_shake_along_its_axis(self):
        for t, axis in ((2.4, "x"), (4.3, "y")):
            with self.subTest(angle=axis):
                a, b = self.window(t, 0.3)
                on = active(self.base, self.fx, a - 4, b + 4)
                self.assertEqual(on[0], a)              # its zoom margin starts on the first frame
                self.assertLessEqual(on[-1], b)
                # the zoom margin adds a steady bias to both axes; the shake is
                # the oscillation around it
                moves = np.array([shift(self.base[k], self.fx[k]) for k in range(a + 1, a + 7)])
                sx, sy = moves[:, 0].std(), moves[:, 1].std()
                if axis == "x":
                    self.assertGreater(sx, 2.5 * sy)
                else:
                    self.assertGreater(sy, 2.5 * sx)

    def test_rgb_black_mix(self):
        for t, dur, thr in ((3.0, 0.1, 1.0), (3.4, 0.1, 20), (3.8, 0.07, 20)):
            with self.subTest(t=t):
                a, b = self.window(t, dur)
                self.assertEqual(active(self.base, self.fx, a - 3, b + 3, thr), list(range(a, b + 1)))

    def test_whip_with_angle_moves_and_blurs_on_its_axis(self):
        self.assertIn("sigma=0.01:sigmaV=28", effects.whip_blur([Event(1, "whip", angle=95)]))
        self.assertIn("sigma=28:sigmaV=0.01", effects.whip_blur([Event(1, "whip", angle=170)]))
        cam = effects.camera([Event(1, "whip", angle=90)], W, H, FPS)
        self.assertIn("*iw*", cam.split(":y=")[1])     # a vertical throw lives in y


class SourceEffects(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("effects-src")
        cls.video = S.motion_video(os.path.join(cls.tmp, "v.mp4"), hit=70, frames=72)

    @classmethod
    def tearDownClass(cls):
        S.remove(cls.tmp)

    def grad(self, path: str) -> tuple[float, float]:
        f = cv2.cvtColor(frames(path)[10].astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
        return float(np.abs(np.diff(f, axis=1)).mean()), float(np.abs(np.diff(f, axis=0)).mean())

    def test_mblur_blurs_along_its_angle_over_the_whole_shot(self):
        out = {}
        for angle in (None, 0, 90):
            seg = Segment(self.video, [Piece(0.0, 1.0, 1.0)])
            if angle is not None:
                seg.mblur = {"angle": angle, "amount": 40}
            path = os.path.join(self.tmp, f"mb{angle}.mp4")
            R.render(Edl([seg], width=W, height=H, fps=FPS, fade_out=0), path, None,
                     os.path.join(self.tmp, f"w{angle}"))
            out[angle] = self.grad(path)
        gx0, gy0 = out[None]
        self.assertLess(out[0][0] / gx0, 0.75 * out[0][1] / gy0)     # 0°: x edges soften much more
        self.assertLess(out[90][1] / gy0, 0.75 * out[90][0] / gx0)   # 90°: y edges soften much more
        self.assertEqual(len(frames(os.path.join(self.tmp, "mb0.mp4"))), 30)

    def test_hit_ramp_lands_the_hit_on_its_frame(self):
        d = S.tmpdir("ramp")
        try:
            path = os.path.join(d, "ramp.mp4")
            n = 120
            stack = np.stack([np.full((90, 160, 3), 2 * k, np.uint8) for k in range(n)])
            subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", "160x90", "-r", "30", "-i", "-", "-c:v", "libx264", "-crf", "1",
                            "-pix_fmt", "yuv420p", path], input=stack.tobytes(), check=True)
            hit = 60 / 30                                # source frame 60
            pieces = timing.hit_ramp(0.5, hit, 3.5, 2.0, 0.6, "constant", "breathe")
            self.assertAlmostEqual(sum(p.dur for p in pieces), 2.0)
            self.assertAlmostEqual(pieces[0].speed, (hit - 0.5) / 0.6)
            out = os.path.join(d, "out.mp4")
            R.render(Edl([Segment(path, pieces, exact_phase=True)], width=160, height=90, fps=30, fade_out=0),
                     out, None, os.path.join(d, "w"))
            raw = ffmpeg_runner.capture(motion.decode_command(out, 0, 100, 30, 160, 90), timeout=120)
            got = [round(float(x.mean()) / 2) for x in np.frombuffer(raw, np.uint8).reshape(-1, 90, 160, 3)]
            self.assertEqual(got[18], 60)                 # 0.6 s × 30 = output frame 18 shows source frame 60
        finally:
            S.remove(d)

    def test_hit_ramp_errors(self):
        with self.assertRaisesRegex(ValueError, "dentro del plano"):
            timing.hit_ramp(0, 1, 2, 1.0, 1.0)
        with self.assertRaisesRegex(ValueError, "tramo"):
            timing.hit_ramp(0, 3, 2, 1.0, 0.5)

    def test_drop_repeats_keeps_the_drawings(self):
        d = S.tmpdir("dedupe")
        try:
            values = [10, 10, 80, 80, 80, 150, 151, 220]          # on twos, threes, then a noise-level repeat
            for i, v in enumerate(values, 1):
                img = np.full((90, 160, 3), v, np.uint8)
                img[30:60, (i * 7) % 100:(i * 7) % 100 + 30] = 255 - v if v not in (80, 10) else v
                Image.fromarray(img).save(os.path.join(d, f"{i:08d}.png"))
            self.assertEqual(external.drop_repeats(d), 5)
            self.assertEqual(sorted(os.listdir(d)), [f"{i:08d}.png" for i in range(1, 6)])
        finally:
            S.remove(d)


class Catalog(unittest.TestCase):
    def test_every_effect_is_known_to_the_engine(self):
        from modules.editing.edl import EVENT_KINDS
        cat = gallery.catalog()
        ids = [e["id"] for e in cat["effects"]]
        self.assertEqual(len(ids), len(set(ids)))
        for e in cat["effects"]:
            self.assertIn(e["stage"], ("output", "source"))
            if e["stage"] == "output":
                self.assertIn(e.get("event", e["id"]), EVENT_KINDS)
            self.assertTrue(e["description"])
            self.assertTrue(1 <= e["level"] <= 5 or e["id"] == "dedupe")
        self.assertTrue(set(EVENT_KINDS) <= set(ids) | {"flash"})


class Gallery(unittest.TestCase):
    def test_variants_and_a_small_gallery(self):
        d = S.tmpdir("gallery")
        try:
            video = S.motion_video(os.path.join(d, "v.mp4"), angle=200, hit=24, frames=72)
            hints = gallery.map_hints(video, 0.0)
            self.assertIsNotNone(hints.get("angle"))
            for e in gallery.catalog()["effects"]:
                edl = gallery.variant(e, video, 0.0, hints)
                if e["id"] in ("panel", "dedupe"):
                    self.assertIsNone(edl)
                    continue
                edl.validate()
            from core import job_manager
            job = job_manager.create_job(video, output_path="")
            gallery.run(job, video, 0.0, ["shake", "invert", "mblur"], os.path.join(d, "g"))
            g = job_manager.get_job(job)
            self.assertEqual(g["status"], "done", g.get("error"))
            self.assertEqual(g["gallery"]["clips"], ["sin efecto", "shake", "invert", "mblur"])
            self.assertTrue(os.path.isfile(g["output_path"]))
        finally:
            S.remove(d)


if __name__ == "__main__":
    unittest.main()
