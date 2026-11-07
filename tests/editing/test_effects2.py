"""Effects bank 2: every new output effect starts on its frame and lasts its
duration (against the same render without it), the variants of each style,
the smooth velocity curves (the hit on its frame) and the split screen."""
import os
import unittest

import numpy as np
from PIL import Image

from modules.editing import bank2, effects, render as R, timing
from modules.editing.edl import Edl, Event, Piece, Segment
from tests import synthetic as S
from tests.editing.test_effects import FPS, H, W, active, frames

DUR = 5.0


def window(t: float, dur: float) -> list[int]:
    a, b = effects.frames_of(t, dur, FPS)
    return list(range(a, b + 1))


class BankTwo(unittest.TestCase):
    """One render per group of effects, each at its own time, against the
    same render with none: where they differ is where each effect acts."""

    A = [Event(0.2, "speedlines", dur=0.2, x=0.5, y=0.5, seed=3),
         Event(0.6, "impactframe", dur=0.1, style="bw"),
         Event(0.9, "spin", dur=0.3, degrees=360),
         Event(1.4, "zoomblur", dur=0.2),
         Event(1.8, "glitch", dur=0.2, style="blocks", seed=1),
         Event(2.2, "echo", dur=0.2),
         Event(2.6, "grain", dur=0.2),
         Event(3.0, "satpulse", dur=0.2),
         Event(3.4, "lens", dur=0.2, ca=0.02),
         Event(3.8, "tilt", dur=0.3, style="left"),
         Event(4.3, "overlay", dur=0.2)]
    B = [Event(0.3, "speedlines", dur=0.2, style="parallel", angle=30.0),
         Event(0.8, "impactframe", dur=0.1, style="inv"),
         Event(1.2, "impactframe", dur=0.1, style="poster"),
         Event(1.6, "glitch", dur=0.2, style="vhs"),
         Event(2.1, "glitch", dur=0.3, style="flicker", seed=2),
         Event(2.8, "shake", dur=0.3, angle=0.0, blur=6.0),
         Event(3.4, "tilt", dur=0.2, style="up"),
         Event(3.9, "spin", dur=0.2, degrees=-90)]

    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("effects2")
        # the disc drifts every frame (echo needs motion), punches at the end
        cls.video = S.motion_video(os.path.join(cls.tmp, "v.mp4"), hit=140, frames=150, drift=4.0)
        leak = np.zeros((64, 64, 4), np.uint8)
        leak[..., 0], leak[..., 1], leak[..., 3] = 255, 140, 255           # an orange light leak
        cls.leak = os.path.join(cls.tmp, "leak.png")
        Image.fromarray(leak, "RGBA").save(cls.leak)
        for e in cls.A:
            if e.kind == "overlay":
                e.image = cls.leak
        seg = lambda: [Segment(cls.video, [Piece(0.0, DUR, DUR)], "a")]   # noqa: E731
        still = Event(0.0, "zoom", intensity=0.0)
        edl = lambda ev: Edl(seg(), width=W, height=H, fps=FPS, look="neutral", fade_out=0,  # noqa: E731
                             events=[still] + ev)
        paths = {k: os.path.join(cls.tmp, f"{k}.mp4") for k in ("base", "a", "b")}
        R.render(edl([]), paths["base"], None, os.path.join(cls.tmp, "w0"))
        R.render(edl(list(cls.A)), paths["a"], None, os.path.join(cls.tmp, "w1"))
        R.render(edl(list(cls.B)), paths["b"], None, os.path.join(cls.tmp, "w2"))
        cls.base, cls.fa, cls.fb = (frames(paths[k]) for k in ("base", "a", "b"))

    @classmethod
    def tearDownClass(cls):
        S.remove(cls.tmp)

    def check(self, fx: np.ndarray, e: Event, thr: float = 2.0, every: bool = True) -> None:
        """Nothing changes the frame before or after the window; inside, the
        first frame and (with `every`) all of them do."""
        frames_in = window(e.t, e.dur)
        a, b = frames_in[0], frames_in[-1]
        on = active(self.base, fx, a - 3, b + 3, thr)
        self.assertTrue(on, f"{e.kind}/{e.style}: no change")
        self.assertEqual(on[0], a, f"{e.kind}/{e.style} starts at {on[0]}, not {a}")
        self.assertLessEqual(on[-1], b, f"{e.kind}/{e.style} lasts until {on[-1]}, not {b}")
        if every:
            self.assertEqual(on, frames_in, f"{e.kind}/{e.style}")

    def test_group_a_on_their_frames(self):
        for e in self.A:
            with self.subTest(kind=e.kind):
                self.check(self.fa, e)

    def test_group_b_on_their_frames(self):
        for e in self.B:
            with self.subTest(kind=e.kind, style=e.style):
                # the shake decays to nothing on its last frame (effects.camera)
                self.check(self.fb, e, every=e.kind != "shake")

    def test_impact_frames_look(self):
        bw = self.fa[window(0.6, 0.1)[0]]
        self.assertLess(float(np.abs(bw[..., 0] - bw[..., 2]).mean()), 3.0)      # no color
        k = window(0.8, 0.1)[0]
        inv = self.fb[k]
        gray = self.base[k] @ np.array([0.114, 0.587, 0.299])                  # BGR → luma
        # two tones, white where the frame was dark (ink and paper swapped)
        self.assertGreater(float(((inv.mean(axis=2) > 128) == (gray <= bank2.IMPACT_THR)).mean()), 0.85)
        poster = self.fb[window(1.2, 0.1)[0]].mean(axis=2)
        levels = np.unique(np.round((poster - 16) / 55))                       # 16 + k·55
        self.assertLessEqual(len(levels), 5)                                    # flat tones

    def test_spin_turns_and_keeps_no_black_corners(self):
        k = window(0.9, 0.3)[4]
        corners = [self.fa[k][:6, :6], self.fa[k][:6, -6:], self.fa[k][-6:, :6], self.fa[k][-6:, -6:]]
        self.assertGreater(min(float(c.mean()) for c in corners), 8.0)           # mirrored, not black

    def test_lens_keeps_the_colors_and_fills_the_frame(self):
        k = window(3.4, 0.2)[2]
        fx, base = self.fa[k], self.base[k]
        for c in range(3):                                                       # no channel swapped
            self.assertLess(abs(float(fx[..., c].mean()) - float(base[..., c].mean())), 12, c)
        corners = [fx[:6, :6], fx[:6, -6:], fx[-6:, :6], fx[-6:, -6:]]
        self.assertGreater(min(float(q.mean()) for q in corners), 8.0)           # no black corners

    def test_overlay_screens_its_color(self):
        k = window(4.3, 0.2)[1]
        d = self.fa[k] - self.base[k]
        # BGR: screen with orange raises red the most, blue the least
        self.assertGreater(float(d[..., 2].mean()), float(d[..., 0].mean()) + 10)


class Velocity(unittest.TestCase):
    def test_curves_fit_the_source_and_are_smooth(self):
        for shape in timing.VELOCITY_SHAPES:
            ps = timing.velocity(2.0, 4.0, 1.6, shape)
            self.assertAlmostEqual(ps[0].start, 2.0)
            self.assertAlmostEqual(ps[-1].end, 4.0)
            self.assertAlmostEqual(sum(p.dur for p in ps), 1.6)
            v = [p.speed for p in ps]
            jumps = max(abs(x - y) for x, y in zip(v, v[1:]))
            self.assertLess(jumps, 0.5 * max(v), shape)                         # no stairs
        rise = [p.speed for p in timing.velocity(0, 2, 1, "rise")]
        self.assertEqual(rise, sorted(rise))
        dip = [p.speed for p in timing.velocity(0, 2, 1, "dip")]
        self.assertLess(min(dip[2:-2]), min(dip[0], dip[-1]))

    def test_velocity_hit_lands_on_its_frame(self):
        fps = 30
        ps = timing.velocity_hit(10.0, 11.0, 13.0, 2.0, 0.8)
        edl = Edl([Segment("x.mp4", ps, "a")], fps=fps)
        timing.quantize(edl)
        t = 0.0
        for p in edl.segments[0].pieces:
            if abs(p.start - 11.0) < 1e-6:
                break
            t += p.dur
        self.assertAlmostEqual(t * fps, 24, places=6)                            # 0.8 s → frame 24
        before = [p for p in edl.segments[0].pieces if p.end <= 11.0 + 1e-9]
        after = [p for p in edl.segments[0].pieces if p.start >= 11.0 - 1e-9]
        self.assertGreater(before[-1].speed, before[0].speed)                    # into the hit, faster
        self.assertLess(after[0].speed, before[-1].speed)                        # slow right after it

    def test_errors(self):
        with self.assertRaises(ValueError):
            timing.velocity(0, 1, 1, "zigzag")
        with self.assertRaises(ValueError):
            timing.velocity_hit(0, 2, 1, 1, 0.5)


class SplitScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("split")
        cls.v1 = S.motion_video(os.path.join(cls.tmp, "a.mp4"), frames=60, seed=1)
        cls.v2 = S.motion_video(os.path.join(cls.tmp, "b.mp4"), frames=60, seed=9)

    @classmethod
    def tearDownClass(cls):
        S.remove(cls.tmp)

    def render(self, layout: str) -> np.ndarray:
        path = os.path.join(self.tmp, f"{layout}.mp4")
        seg = Segment(self.v1, [Piece(0.5, 1.5, 1.0)], "a", split={"source": self.v2, "start": 0.5, "layout": layout})
        R.render(Edl([seg], width=W, height=H, fps=FPS, look="neutral", fade_out=0), path, None,
                 os.path.join(self.tmp, f"w_{layout}"))
        return frames(path)

    def full(self, source: str) -> np.ndarray:
        path = os.path.join(self.tmp, f"full_{os.path.basename(source)}")
        R.render(Edl([Segment(source, [Piece(0.5, 1.5, 1.0)], "a")], width=W, height=H, fps=FPS,
                     look="neutral", fade_out=0), path, None, os.path.join(self.tmp, "w_" + os.path.basename(source)))
        return frames(path)

    def test_layouts(self):
        top = self.render("top_bottom")
        side = self.render("left_right")
        diag = self.render("diagonal")
        self.assertEqual(len(top), FPS)                                          # 1 s, like the shot
        seam = top[5][H // 2 - 2:H // 2 + 2].mean()
        self.assertGreater(seam, 240)                                            # the white seam
        self.assertGreater(side[5][:, W // 2 - 2:W // 2 + 2].mean(), 240)
        # the halves come from different sources: they don't match each other
        self.assertGreater(float(np.abs(top[5][:H // 2 - 8] - top[5][H // 2 + 8:][:H // 2 - 8]).mean()), 10)
        # diagonal: top-left corner from the first source, bottom-right from the second
        a, b = self.full(self.v1), self.full(self.v2)
        tl, br = (slice(0, 40), slice(0, 40)), (slice(H - 40, H), slice(W - 40, W))
        self.assertLess(float(np.abs(diag[5][tl] - a[5][tl]).mean()), 6)
        self.assertLess(float(np.abs(diag[5][br] - b[5][br]).mean()), 6)

    def test_validation(self):
        seg = Segment(self.v1, [Piece(0, 1, 1)], "a", split={"source": "nope.mp4", "start": 0})
        with self.assertRaises(ValueError):
            Edl([seg]).validate()
        seg = Segment(self.v1, [Piece(0, 1, 1)], "a", split={"source": self.v2, "start": 0, "layout": "x"})
        with self.assertRaises(ValueError):
            Edl([seg]).validate()


class Windows(unittest.TestCase):
    def test_window_kinds(self):
        self.assertTrue(bank2.is_window(Event(0, "spin")))
        self.assertTrue(bank2.is_window(Event(0, "glitch")))                     # blocks, the default
        self.assertFalse(bank2.is_window(Event(0, "glitch", style="vhs")))
        self.assertFalse(bank2.is_window(Event(0, "grain")))


if __name__ == "__main__":
    unittest.main()
