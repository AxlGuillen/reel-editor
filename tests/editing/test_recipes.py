"""The composites (editing/recipes.py) and the primitives they add: freeze,
duotone, isolate, glints and blur each act on exactly their frames; the blur
cut touches only the two frames around each cut; the lyric words get their
place and clear at the cut; and the px effects scale with the resolution."""
import os
import re
import unittest

import numpy as np

from modules.editing import effects, recipes, render as R
from modules.editing.edl import Edl, Event, Piece, Segment
from tests import synthetic as S
from tests.editing.test_effects import FPS, H, W, active, frames

DUR = 4.0


def window(t: float, dur: float) -> list[int]:
    a, b = effects.frames_of(t, dur, FPS)
    return list(range(a, b + 1))


def sat(img: np.ndarray) -> np.ndarray:
    return img.max(axis=2) - img.min(axis=2)


class Primitives(unittest.TestCase):
    EV = [Event(0.4, "freeze", dur=0.3),
          Event(1.0, "duotone", dur=0.1, colors=["#400000", "#40F0FF"]),
          Event(1.4, "isolate", dur=0.6, colors=["#E02020"], similarity=0.3, fade=0.2),
          Event(2.4, "glints", intensity=2.0, dur=0.2, x=0.5, y=0.5, seed=4),
          Event(3.0, "blur", dur=0.1, blur=30)]

    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("recipes")
        cls.video = S.motion_video(os.path.join(cls.tmp, "v.mp4"), hit=110, frames=120, drift=4.0)
        seg = lambda: [Segment(cls.video, [Piece(0.0, DUR, DUR)], "a")]   # noqa: E731
        still = Event(0.0, "zoom", intensity=0.0)
        mk = lambda ev: Edl(seg(), width=W, height=H, fps=FPS, look="neutral", fade_out=0,  # noqa: E731
                            events=[still] + ev)
        cls.paths = {k: os.path.join(cls.tmp, f"{k}.mp4") for k in ("base", "fx")}
        R.render(mk([]), cls.paths["base"], None, os.path.join(cls.tmp, "w0"))
        R.render(mk(list(cls.EV)), cls.paths["fx"], None, os.path.join(cls.tmp, "w1"))
        cls.base, cls.fx = frames(cls.paths["base"]), frames(cls.paths["fx"])

    @classmethod
    def tearDownClass(cls):
        S.remove(cls.tmp)

    def test_each_on_its_frames(self):
        for e in self.EV:
            with self.subTest(kind=e.kind):
                fr = window(e.t, e.dur)
                # a few small sparkles barely move the frame mean
                on = active(self.base, self.fx, fr[0] - 3, fr[-1] + 3, 0.8 if e.kind == "glints" else 2.0)
                self.assertTrue(on)
                self.assertGreaterEqual(on[0], fr[0], e.kind)
                self.assertLessEqual(on[-1], fr[-1], e.kind)
                if e.kind != "freeze":           # the freeze's first frame IS the frame
                    self.assertEqual(on[0], fr[0], e.kind)

    def test_freeze_holds_the_first_frame(self):
        fr = window(0.4, 0.3)
        for k in fr:
            self.assertLess(float(np.abs(self.fx[k] - self.fx[fr[0]]).mean()), 1.5)
        self.assertGreater(float(np.abs(self.base[fr[-1]] - self.base[fr[0]]).mean()), 3)   # the video moves
        self.assertLess(float(np.abs(self.fx[fr[0]] - self.base[fr[0]]).mean()), 1.5)

    def test_duotone_maps_luma_to_its_two_colors(self):
        k = window(1.0, 0.1)[0]
        img = self.fx[k]                                    # BGR
        lum = img.mean(axis=2)
        # #400000 → #40F0FF: red stays at 64, green and blue grow with the luma
        self.assertLess(abs(float(img[..., 2].mean()) - 64), 12)
        self.assertLess(float(img[..., 2].std()), 12)
        self.assertGreater(float(np.corrcoef(img[..., 0].ravel(), (self.base[k] @ np.array([0.114, 0.587, 0.299])).ravel())[0, 1]), 0.85)
        light = img[lum > np.percentile(lum, 80)]
        self.assertGreater(light[:, 0].mean(), light[:, 2].mean() + 60)      # the light end is cyan

    def test_isolate_grays_the_rest_and_brings_the_color_back(self):
        fr = window(1.4, 0.6)
        held = self.fx[fr[2]]
        red = (held[..., 2] > held[..., 1] + 60) & (held[..., 2] > held[..., 0] + 60)
        self.assertLess(float(sat(held)[~red].mean()), 16)                   # the rest gray
        self.assertGreater(float(sat(self.base[fr[2]]).mean()), 40)          # it had color
        last = self.fx[fr[-1]]
        self.assertGreater(float(sat(last).mean()), float(sat(held).mean()) + 20)   # coming back

    def test_blur_softens_along_its_axis(self):
        k = window(3.0, 0.1)[0]
        gx = lambda im: float(np.abs(np.diff(im.mean(axis=2), axis=1)).mean())   # noqa: E731
        gy = lambda im: float(np.abs(np.diff(im.mean(axis=2), axis=0)).mean())   # noqa: E731
        rx, ry = gx(self.fx[k]) / gx(self.base[k]), gy(self.fx[k]) / gy(self.base[k])
        self.assertLess(rx, 0.6)
        self.assertLess(rx, ry - 0.15)                    # far more across than along


class BlurCuts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("blurcuts")
        cls.v1 = S.motion_video(os.path.join(cls.tmp, "a.mp4"), frames=60, seed=1)
        cls.v2 = S.motion_video(os.path.join(cls.tmp, "b.mp4"), frames=60, seed=9)

    @classmethod
    def tearDownClass(cls):
        S.remove(cls.tmp)

    def render(self, events, name):
        segs = [Segment(self.v1, [Piece(0.0, 1.0, 1.0)], "a"), Segment(self.v2, [Piece(0.0, 1.0, 1.0)], "b"),
                Segment(self.v1, [Piece(1.0, 2.0, 1.0)], "c")]
        path = os.path.join(self.tmp, f"{name}.mp4")
        R.render(Edl(segs, width=W, height=H, fps=FPS, look="neutral", fade_out=0,
                     events=[Event(0.0, "zoom", intensity=0.0)] + events), path, None,
                 os.path.join(self.tmp, f"w_{name}"))
        return frames(path)

    def test_only_the_frames_around_each_cut(self):
        base = self.render([], "base")
        cuts = [FPS, 2 * FPS]
        for style in ("radial", "directional"):
            with self.subTest(style=style):
                fx = self.render(recipes.blur_cuts(cuts, FPS, style=style, accent_every=0), style)
                on = active(base, fx, 0, len(base) - 1)
                self.assertEqual(on, [FPS - 1, FPS, 2 * FPS - 1, 2 * FPS])

    def test_accent_every_n(self):
        ev = recipes.blur_cuts(list(range(10, 90, 10)), FPS, accent_every=4)
        accents = [(e.kind, round(e.t * FPS)) for e in ev if e.kind in ("invert", "impactframe")]
        self.assertEqual(accents, [("invert", 40), ("impactframe", 80)])


class Composites(unittest.TestCase):
    def test_hit_layers(self):
        ev = recipes.hit(30, FPS, angle=90.0, x=0.4, y=0.3, whip=True)
        kinds = {e.kind: round(e.t * FPS) for e in ev}
        self.assertEqual(kinds["freeze"], 30)
        self.assertEqual(kinds["shake"], 31)
        self.assertEqual(kinds["whip"], 30 + recipes.HIT_FREEZE)
        self.assertEqual(next(e for e in ev if e.kind == "shake").angle, 90.0)

    def test_burst_alternates(self):
        ev = recipes.burst(60, FPS)
        seq = [(round(e.t * FPS), e.kind) for e in ev if e.kind in ("impactframe", "duotone")]
        self.assertEqual(seq, [(60, "impactframe"), (61, "duotone"), (62, "impactframe"), (63, "duotone"),
                               (64, "impactframe"), (65, "duotone")])

    def test_composites_render(self):
        tmp = S.tmpdir("composites")
        try:
            v = S.motion_video(os.path.join(tmp, "v.mp4"), hit=60, frames=96, drift=3.0)
            ev = recipes.hit(20, FPS, angle=0.0, whip=False) + recipes.burst(45, FPS) + recipes.isolate(75, FPS)
            out = os.path.join(tmp, "o.mp4")
            R.render(Edl([Segment(v, [Piece(0, 3.5, 3.5)])], width=W, height=H, fps=FPS, look="neutral",
                         fade_out=0, events=ev), out, None, os.path.join(tmp, "w"))
            fx = frames(out)
            self.assertEqual(len(fx), round(3.5 * FPS))
            bw = fx[45]
            self.assertLess(float(sat(bw).mean()), 4)                          # the b/w ink frame
            duo = fx[46]
            self.assertGreater(float(sat(duo).mean()), 30)                     # the red–cyan one
        finally:
            S.remove(tmp)


class Lyrics(unittest.TestCase):
    def test_words_from_srt_skips_placeholders(self):
        tmp = S.tmpdir("srt")
        try:
            p = os.path.join(tmp, "l.srt")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("1\n00:00:01,000 --> 00:00:01,400\nfirst\n\n2\n00:00:01,400 --> 00:00:01,900\n♪ 002\n\n"
                         "3\n00:01:02,250 --> 00:01:02,700\nI will survive\n")
            w = recipes.words_from_srt(p, offset=-1.0)
            self.assertEqual(w, [{"word": "first", "start": 0.0, "end": 0.4},
                                 {"word": "I will survive", "start": 61.25, "end": 61.7}])
        finally:
            S.remove(tmp)

    def test_place_and_clear(self):
        shots = [{"start": 0, "end": 30, "focus": (0.5, 0.3)}, {"start": 30, "end": 60, "focus": (0.5, 0.8)}]
        w = recipes.lyric_words([{"word": "a", "start": 0.2, "end": 0.5}, {"word": "b", "start": 1.2, "end": 1.4}],
                                shots, FPS)
        self.assertEqual((w[0]["y"], w[0]["clear"]), (recipes.LYRIC_Y["low"], 1.0))
        self.assertEqual((w[1]["y"], w[1]["clear"]), (recipes.LYRIC_Y["high"], 2.0))

    def test_invert_ass_uses_them(self):
        from modules.subtitles import processor as sp
        from modules.subtitles.schema import SubtitlesParams
        params = SubtitlesParams(layout="invert", inv_words="1")
        words = [{"word": "hola", "start": 0.2, "end": 0.5, "y": 1390.0, "clear": 0.8},
                 {"word": "mundo", "start": 2.0, "end": 2.4, "y": 560.0}]
        ass = sp._build_invert_ass([words], params)
        rows = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")]
        self.assertIn("0:00:00.80", rows[0])                    # cleared at the cut
        ys = [float(m.group(1)) for m in (re.search(r"\\pos\(\d+,([\d.]+)\)", r) for r in rows)]
        self.assertGreater(ys[0], 1300)
        self.assertLess(ys[1], 700)


class Resolution(unittest.TestCase):
    def test_px_scale(self):
        self.assertEqual(effects.px_scale(1080, 1920), 1.0)
        self.assertEqual(effects.px_scale(1920, 1080), 1.0)
        self.assertEqual(effects.px_scale(2160, 3840), 2.0)
        self.assertEqual(effects.rgb([Event(0, "rgb")], 1.0, 30, 2.0).count("crh=-10"), 1)

    def test_renders_at_1440(self):
        tmp = S.tmpdir("res1440")
        try:
            v = S.motion_video(os.path.join(tmp, "v.mp4"), frames=30, w=1280, h=720)
            out = os.path.join(tmp, "o.mp4")
            ev = [Event(0.2, "rgb", dur=0.1), Event(0.4, "speedlines", dur=0.1)] + recipes.blur_cuts([15], 30)
            R.render(Edl([Segment(v, [Piece(0, 1, 1)])], width=1440, height=2560, fps=30, look="neutral",
                         fade_out=0, events=ev), out, None, os.path.join(tmp, "w"))
            from core import ffmpeg_runner
            self.assertEqual(ffmpeg_runner.probe_resolution(out), (1440, 2560))
        finally:
            S.remove(tmp)


class ShotWindows(unittest.TestCase):
    def test_windows_go_to_their_shot_split_at_the_cut(self):
        segs = [Segment("a.mp4", [Piece(0, 1, 1)]), Segment("b.mp4", [Piece(0, 1, 1)])]
        edl = Edl(segs, fps=30, events=[Event(0.9, "spin", dur=0.2), Event(1.5, "zoomblur", dur=1 / 30),
                                        Event(0.2, "flash")])
        w = R.shot_windows(edl)
        self.assertEqual([(e.kind, effects.frames_of(e.t, e.dur, 30)) for e in w[0]], [("spin", (27, 29))])
        self.assertEqual([(e.kind, effects.frames_of(e.t, e.dur, 30)) for e in w[1]],
                         [("spin", (0, 2)), ("zoomblur", (15, 15))])


if __name__ == "__main__":
    unittest.main()
