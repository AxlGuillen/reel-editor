"""The manga preset's planner (pure): the cuts of edit 3 to the frame, panel
pairs, the grid from a beatmap, flashes, the lyric timing files and errors."""
import os
import tempfile
import unittest

from PIL import Image

from modules.anime_edit import framing, manga, repro
from modules.anime_edit.schema import FinishParams


class Edit3Plan(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="reelforge-manga-")
        for s in repro.P:
            if s[0] == "h":
                Image.new("RGB", (300, 400), (240, 240, 240)).save(os.path.join(self.dir, f"{s[2]}.png"))
        self.op = repro.options("unused")
        self.op.panels_folder = self.dir

    def plan(self):
        return manga.plan("ep.mkv", (1920, 1080), {"duration": 0}, repro.takes(), self.op)

    def test_cuts_are_edit3_frames(self):
        edl, info = self.plan()
        self.assertEqual(info["cuts"], repro.edit3_cuts())
        self.assertEqual(info["frames"], 2022)
        self.assertEqual(len(edl.segments), 43)
        self.assertEqual(info["first_downbeat"], 0.5196)

    def test_holds_freeze_the_last_frame_of_their_take(self):
        edl, info = self.plan()
        hold = edl.segments[2]
        self.assertTrue(hold.pieces[0].frozen)
        self.assertAlmostEqual(hold.pieces[0].start, 372.05 + (55 - 1) / 30, places=6)
        self.assertEqual(info["panels"][0], {"name": "m09_black-bulls-jefe_con-texto", "start": 4.2, "end": 5.1})
        panels = [e for e in edl.events if e.kind == "panel"]
        self.assertEqual(len(panels), 10)
        self.assertAlmostEqual(panels[0].dur, 0.9)

    def test_last_take_fills_to_the_end_at_its_speed(self):
        edl, _ = self.plan()
        last = edl.segments[-1].pieces[0]
        self.assertAlmostEqual(last.dur, (2022 - 1805) / 30)
        self.assertAlmostEqual(last.speed, 0.85, places=6)

    def test_flashes_and_fade(self):
        edl, info = self.plan()
        self.assertEqual(info["flashes"], [30.22, 41.7, 56.48])
        fl = [e for e in edl.events if e.kind == "flash"]
        self.assertTrue(all(e.shape == "mix" and e.intensity == 0.55 and e.dur == 0.07 for e in fl))
        self.assertEqual(edl.video_fade_out, 1.5)
        self.assertEqual(edl.fade_out, 0)

    def test_panel_pairs_shift_the_take_to_end_on_t(self):
        takes = [t for t in repro.takes()[:3] if "panel" not in t]            # 317.30 and 372.05, no inline panel
        self.op.panels = [{"panel": "m09_black-bulls-jefe_con-texto", "t": 373.80}]
        self.op.music_end = (126 + 27 + 30) / 30 + 0.0001
        takes.append({"t": 375.0, "beats": None, "frame": "A"})
        edl, info = manga.plan("ep.mkv", (1920, 1080), {"duration": 0}, takes, self.op)
        take = edl.segments[1].pieces[0]
        self.assertAlmostEqual(take.start + (55 - 1) / 30, 373.80, places=6)
        self.assertAlmostEqual(edl.segments[2].pieces[0].start, 373.80, places=6)

    def test_overflow_and_short_plans_fail_clearly(self):
        self.op.music_end = 10.0
        with self.assertRaisesRegex(ValueError, "no caben"):
            self.plan()
        self.op.music_end = 100.0
        takes = repro.takes()
        takes[-1]["beats"] = 4
        with self.assertRaisesRegex(ValueError, "sigue hasta"):
            manga.plan("ep.mkv", (1920, 1080), {"duration": 0}, takes, self.op)


class BeatmapGrid(unittest.TestCase):
    def test_tracked_beats_and_default_flashes(self):
        bm = {"duration": 10.0, "beats": [0.25 + 0.5 * k for k in range(20)],
              "downbeats": [0.25 + 2.0 * k for k in range(5)],
              "words": [{"start": 1.0, "end": 1.3}, {"start": 1.4, "end": 1.8}, {"start": 3.0, "end": 3.5}]}
        op = manga.MangaOptions(music_start=0.0, music_end=4.0, fps=30)
        takes = [{"t": 10, "beats": 2, "frame": "B"}, {"t": 20, "beats": None, "frame": "C"}]
        edl, info = manga.plan("v.mp4", (1920, 1080), bm, takes, op)
        self.assertEqual(info["cuts"], [round(1.25 * 30)])                  # downbeat 0.25 + 2 beats
        self.assertEqual(info["flashes"], [1.0, 3.0])                         # one per phrase (pause ≥ 0.45 s)
        self.assertEqual(info["grid"], "beatmap")

    def test_srt_are_times_only(self):
        bm = {"duration": 10.0, "words": [{"start": 1.0, "end": 1.3}, {"start": 1.4, "end": 1.45},
                                          {"start": 3.0, "end": 3.5}]}
        d = tempfile.mkdtemp(prefix="reelforge-srt-")
        paths = manga.srt_files(bm, manga.MangaOptions(music_start=0.5), d, "edit")
        with open(paths[0], encoding="utf-8") as fh:
            phrases = fh.read()
        with open(paths[1], encoding="utf-8") as fh:
            words = fh.read()
        self.assertIn("00:00:00,500 --> 00:00:00,950\n♪ frase 01", phrases)
        self.assertIn("00:00:00,900 --> 00:00:01,050\n♪ 002", words)      # a 50 ms word lasts 150 ms
        self.assertNotIn("Hola", phrases)


class Framing(unittest.TestCase):
    def test_letters_and_errors(self):
        vf = framing.frame_filter("z", "cold", (0.3, 0.45), 372.05, 1.833, (1920, 1080))
        self.assertIn("crop=864:1080", vf)
        self.assertIn("scale=1080:1350", vf)
        self.assertIn("372.0500)/1.8330", vf)
        self.assertIn("crop=608:1080", framing.frame_filter("A", "warm", (0.5, 0.5), 0, 1, (1920, 1080)))
        self.assertIn("scale=1080:608", framing.frame_filter("C", "warm", (0.5, 0.5), 0, 1, (1920, 1080)))
        with self.assertRaisesRegex(ValueError, "Encuadre"):
            framing.frame_filter("Q", "cold", (0.5, 0.5), 0, 1, (1920, 1080))


class Params(unittest.TestCase):
    def test_manga_needs_takes(self):
        with self.assertRaisesRegex(ValueError, "takes"):
            FinishParams.from_form({"variants": '["manga"]'}, set())
        fp = FinishParams.from_form({"variants": ["manga"], "takes": [{"t": 1, "beats": None}],
                                     "grid": {"bpm": 120, "phase": 0}, "flashes": [1.5], "music_end": "30"},
                                    set())
        self.assertEqual(fp.grid, {"bpm": 120, "phase": 0})
        self.assertEqual(fp.music_end, 30)


if __name__ == "__main__":
    unittest.main()
