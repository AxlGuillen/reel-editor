"""Subtitles "invert" layout: the word in the middle showing the video in
negative (white text with a difference blend). No internet."""
import os
import re
import subprocess
import tempfile
import unittest

import numpy as np

import config
from core import ffmpeg_runner
from modules.subtitles import fonts
from modules.subtitles import processor as P
from modules.subtitles.schema import SubtitlesParams

WORDS = "we will survive cause i know i'll survive".split()


def _segments(words=WORDS, step=0.5, dur=0.4, start=0.0):
    return [[{"word": w, "start": round(start + i * step, 3), "end": round(start + i * step + dur, 3)}
             for i, w in enumerate(words)]]


def _params(**form):
    return SubtitlesParams.from_form({"sub_layout": "invert", "sub_font": "Montserrat-ExtraBold.ttf", **form})


def _events(ass: str):
    """[(start s, end s, tags, text)] of the Dialogue lines."""
    out = []
    for line in ass.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        h, m, s = parts[1].split(":")
        t0 = int(h) * 3600 + int(m) * 60 + float(s)
        h, m, s = parts[2].split(":")
        t1 = int(h) * 3600 + int(m) * 60 + float(s)
        tags, text = re.match(r"\{(.*)\}(.*)", parts[9]).groups()
        out.append((t0, t1, tags, text))
    return out


class Schema(unittest.TestCase):
    def test_defaults_and_values(self):
        p = SubtitlesParams.from_form({})
        self.assertEqual((p.inv_size, p.inv_y, p.inv_direction, p.inv_words), (150, 960, "horizontal", "1"))
        p = _params(sub_inv_size="220", sub_inv_y="700", sub_inv_direction="random", sub_inv_words="mixed")
        self.assertEqual((p.layout, p.inv_size, p.inv_y, p.inv_direction, p.inv_words),
                         ("invert", 220, 700, "random", "mixed"))

    def test_errors(self):
        for form in ({"sub_inv_size": "59"}, {"sub_inv_size": "321"}, {"sub_inv_y": "100"},
                     {"sub_inv_direction": "arriba"}, {"sub_inv_words": "4"}, {"sub_inv_size": "grande"}):
            with self.assertRaises(ValueError, msg=form):
                _params(**form)

    def test_montserrat_is_there(self):
        for f in ("Montserrat-ExtraBold.ttf", "Montserrat-Black.ttf"):
            self.assertTrue(fonts.is_available(f), f)
        self.assertEqual(fonts.font_info("Montserrat-ExtraBold.ttf")["family"], "Montserrat ExtraBold")


class BuildAss(unittest.TestCase):
    def test_one_word_at_a_time_in_capitals(self):
        ass = P.build_ass(_segments(), _params())
        self.assertIn(P.INV_MARKER, ass)
        ev = _events(ass)
        self.assertEqual([e[3] for e in ev], [w.upper() for w in WORDS])
        for (t0, t1, tags, _), (n0, *_rest) in zip(ev, ev[1:]):
            self.assertEqual(t1, n0)                    # no empty frames between words
        self.assertTrue(all(r"\an5\pos(540," in e[2] and r"\frz" not in e[2] for e in ev))
        # the transition: fade in/out and a quick pop from the word's center
        self.assertIn(r"\fad(70,40)\fscx86.0\fscy86.0\t(0,90,\fscx104.0\fscy104.0)\t(90,160,\fscx100.0", ev[0][2])
        self.assertIn("Montserrat ExtraBold", ass)
        self.assertIn(",0,0,5,0,0,0,1", ass)              # no outline, no shadow, centered

    def test_pauses(self):
        # 0.8 s of silence: the word bridges it; 3 s: it leaves 0.5 s after it is sung
        segs = [[{"word": "uno", "start": 0.0, "end": 0.4}, {"word": "dos", "start": 1.2, "end": 1.6},
                 {"word": "tres", "start": 4.6, "end": 5.0}]]
        ev = _events(P.build_ass(segs, _params()))
        self.assertEqual([(e[0], e[1]) for e in ev], [(0.0, 1.2), (1.2, 2.1), (4.6, 5.5)])

    def test_several_words_appear_one_by_one(self):
        ev = _events(P.build_ass(_segments(), _params(sub_inv_words="3")))
        # group 1: WE WILL SURVIVE, each from its own start, all leaving together
        self.assertEqual([(e[3], e[0]) for e in ev[:3]], [("WE", 0.0), ("WILL", 0.5), ("SURVIVE", 1.0)])
        self.assertEqual({e[1] for e in ev[:3]}, {1.5})
        ys = [float(re.search(r"\\pos\(540,([\d.]+)\)", e[2]).group(1)) for e in ev[:3]]
        self.assertTrue(ys[0] < ys[1] < ys[2])            # stacked, top to bottom
        self.assertAlmostEqual((ys[0] + ys[2]) / 2, 960, delta=60)   # the block centered on inv_y

    def test_mixed_and_random_are_fixed_per_word(self):
        p = _params(sub_inv_words="mixed", sub_inv_direction="random")
        a = P.build_ass(_segments(WORDS * 6), p)
        self.assertEqual(a, P.build_ass(_segments(WORDS * 6), p))   # same transcript, same video
        sizes = [len(g) for _, g in P._invert_groups(_segments(WORDS * 6), "mixed")]
        self.assertTrue({1, 2, 3} >= set(sizes) and len(set(sizes)) > 1)
        angles = {m for m in re.findall(r"\\frz(-?\d+)", a)}
        self.assertTrue(angles <= {"90", "8", "-8"} and len(angles) >= 2)
        self.assertTrue(any(r"\frz" not in e[2] for e in _events(a)))   # and some horizontal

    def test_long_word_shrinks(self):
        ev = _events(P.build_ass(_segments(["supercalifragilistico"]), _params(sub_inv_size="300")))
        scale = float(re.search(r"\\t\(90,160,\\fscx([\d.]+)", ev[0][2]).group(1))
        self.assertLess(scale, 100)
        font = fonts.font_info("Montserrat-ExtraBold.ttf")
        width = fonts.text_width(font, "SUPERCALIFRAGILISTICO", 300) * scale / 100
        self.assertLessEqual(width, P.INV_MAX_W[0] + 1)

    def test_vertical_turns_around_the_center(self):
        ev = _events(P.build_ass(_segments(), _params(sub_inv_direction="vertical", sub_inv_y="800")))
        self.assertTrue(all(r"\org(540,800)\frz90" in e[2] for e in ev))

    def test_the_other_layouts_do_not_change(self):
        p = SubtitlesParams.from_form({})
        self.assertNotIn(P.INV_MARKER, P.build_ass(_segments(), p))


class Burn(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        # A colorful moving background, 1080x1920, bt709 like the reels.
        cls.video = os.path.join(d, "v.mp4")
        subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=s=1080x1920:r=30:d=3", "-f", "lavfi", "-i", "sine=d=3",
                        "-c:v", "libx264", "-crf", "8", "-pix_fmt", "yuv420p", "-colorspace", "bt709",
                        "-color_primaries", "bt709", "-color_trc", "bt709", "-c:a", "aac", "-shortest",
                        cls.video], check=True)
        segs = [{"text": "HOLA", "start": 0.5, "end": 2.5,
                 "words": [{"word": "hola", "start": 0.5, "end": 2.5}]}]
        cls.ass = P.prepare_ass(segs, _params(sub_inv_size="300"))
        cls.normal_ass = P.prepare_ass(segs, SubtitlesParams.from_form({}))
        cls.out = os.path.join(d, "out.mp4")
        ffmpeg_runner.capture(P.build_command(cls.video, cls.ass, cls.out), cwd=config.BASE_DIR)

    @classmethod
    def tearDownClass(cls):
        for path in (cls.ass, cls.normal_ass):
            if os.path.exists(path):
                os.remove(path)
        cls.tmp.cleanup()

    def _frame(self, path, t):
        raw = subprocess.run([config.FFMPEG_PATH, "-v", "error", "-ss", f"{t}", "-i", path, "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)

    def test_filter_chain(self):
        self.assertTrue(P.ass_filter(self.normal_ass).startswith("ass="))
        chain = P.ass_filter(self.ass)
        self.assertIn("blend=all_mode=difference", chain)
        # one input and one output: it still drops into -vf and filter_complex chains
        cmd = [config.FFMPEG_PATH, "-v", "error", "-i", self.video, "-filter_complex",
               f"[0:v]{chain}[vsub]", "-map", "[vsub]", "-t", "0.2", "-f", "null", "-"]
        ffmpeg_runner.capture(cmd, cwd=config.BASE_DIR)

    def test_letters_are_the_negative_and_the_rest_untouched(self):
        src, out = self._frame(self.video, 1.5), self._frame(self.out, 1.5)
        changed = np.abs(out - src).sum(axis=2) > 90
        self.assertGreater(changed.mean(), 0.01)                 # the word is there…
        ys, xs = np.where(changed)
        self.assertAlmostEqual(ys.mean(), 960, delta=80)         # …in the middle
        self.assertAlmostEqual(xs.mean(), 540, delta=80)
        # Inside the letters (2 px away from the antialiased edge): 255 − video.
        core = changed.copy()
        for dy, dx in ((2, 0), (-2, 0), (0, 2), (0, -2), (1, 1), (-1, -1)):
            core &= np.roll(np.roll(changed, dy, 0), dx, 1)
        err = np.abs(out[core] - (255 - src[core])).mean(axis=1)
        self.assertLess(float(np.median(err)), 4)
        self.assertLess(float(np.percentile(err, 75)), 6)
        self.assertLess(np.abs(out[~changed] - src[~changed]).mean(), 3)   # nothing else moves

    def test_not_there_before_the_word(self):
        src, out = self._frame(self.video, 0.2), self._frame(self.out, 0.2)
        self.assertLess(np.abs(out - src).mean(), 3)
        self.assertEqual(ffmpeg_runner.probe_resolution(self.out), (1080, 1920))
        self.assertTrue(ffmpeg_runner.has_audio_stream(self.out))


if __name__ == "__main__":
    unittest.main()
