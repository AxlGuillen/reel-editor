"""Tests of anime_edit: shots, planner (pure), validation and the two-phase
flow with a synthetic video and song."""
import os
import time
import unittest
from unittest import mock

import numpy as np

import config
from core import ffmpeg_runner
from modules.anime_edit import plan as PL
from modules.anime_edit import processor
from modules.anime_edit import shots as SH
from modules.anime_edit.presets import PRESETS
from modules.anime_edit.schema import AnimeEditParams, FinishParams
from tests import synthetic as S


class Shots(unittest.TestCase):
    def test_merge_short(self):
        self.assertEqual(SH.merge_short([0, 1.0, 1.1, 3.0, 3.2]), [0, 1.0, 3.2])
        self.assertEqual(SH.merge_short([0, 0.1, 2.0]), [0, 2.0])

    def test_measures_and_score(self):
        # 6 samples per second: shot 1 still, shot 2 moving and shot 3 almost
        # all white (a long impact frame).
        n = 18
        series = {"motion": np.array([0.0] * 6 + [30.0] * 6 + [60.0] * 6),
                  "brightness": np.array([100.0] * 12 + [250.0] * 6), "saturation": np.full(n, 0.5)}
        still, moving, white = SH.shots_from([0, 1.0, 2.0, 3.0], series)
        self.assertGreater(moving["score"], still["score"])
        self.assertLess(white["score"], moving["score"])
        self.assertTrue(1.0 <= moving["peak"] <= 2.0)

    def test_detects_the_cuts_of_a_video(self):
        tmp = S.tmpdir("shots")
        try:
            v = S.shots_video(os.path.join(tmp, "v.mp4"), [1.5, 2.0, 1.0, 2.5])
            shots = SH.detect(v)
            self.assertEqual(len(shots), 4)
            for p, expected in zip(shots, [0, 1.5, 3.5, 4.5]):
                self.assertAlmostEqual(p["start"], expected, delta=0.1)
            SH.thumbnails(v, shots, tmp)
            sheets = SH.contact_sheets(tmp, len(shots))
            self.assertEqual(len(sheets), 1)
            self.assertTrue(os.path.isfile(sheets[0]))
            # without PySceneDetect, FFmpeg's scdet finds the same cuts
            with mock.patch.object(SH, "_cuts_scenedetect", return_value=None):
                self.assertEqual(len(SH.detect(v)), 4)
            # if PySceneDetect can't read the file, it falls back to scdet too
            import scenedetect
            with mock.patch.object(scenedetect, "detect", side_effect=OSError("no puede abrirlo")):
                self.assertIsNone(SH._cuts_scenedetect(v))
                self.assertEqual(len(SH.detect(v)), 4)
        finally:
            S.remove(tmp)


def _bm(bpm=120.0, dur=60.0, drop=24.0):
    per = 60 / bpm
    beats = [round(k * per, 3) for k in range(int(dur / per))]
    return {"beats": beats, "downbeats": beats[::4], "bpm": bpm, "duration": dur, "onsets": [],
            "hits": [b for b in beats[::2] if b >= drop], "energy_per_beat": [0.5] * len(beats),
            "sections": [{"type": "verse", "start": 0.0, "end": drop, "energy": 0.2},
                         {"type": "chorus", "start": drop, "end": dur, "energy": 1.0}],
            "drop": drop, "buildups": [[drop - 4.0, drop]],
            "words": [{"text": "hey!", "start": drop + 0.5, "end": drop + 0.8},
                      {"text": "go", "start": drop + 1.5, "end": drop + 1.8}]}


SHOTS = [{"id": i, "start": 2.0 * (i - 1), "end": 2.0 * i, "peak": 2.0 * i - 1, "score": 10 * i}
         for i in range(1, 9)]


class Plan(unittest.TestCase):
    def test_window_puts_the_drop_around_35_percent(self):
        start, dur = PL.song_window(_bm(), 20, None)
        self.assertIn(start, _bm()["downbeats"])
        self.assertAlmostEqual(24 - start, 8, delta=1.0)        # 35 % of 20 s ≈ 7 s, on a bar
        self.assertIn(round(start + dur, 3), _bm()["downbeats"])

    def test_cuts_per_section_and_drop(self):
        bmw = {**_bm(), "duration": 40.0}
        c = PL.cut_points(bmw, PRESETS["impact"])
        self.assertIn(24.0, c)
        chorus = [b - a for a, b in zip(c, c[1:]) if a >= 24]
        verse = [b - a for a, b in zip(c, c[1:]) if b <= 20]
        buildup = [b - a for a, b in zip(c, c[1:]) if 20 <= a < 24]
        self.assertAlmostEqual(np.median(chorus), 0.5, places=3)    # 1 beat
        self.assertAlmostEqual(np.median(verse), 1.0, places=3)     # 2 beats
        self.assertAlmostEqual(np.median(buildup), 0.25, places=3)  # half a beat

    def test_cuts_on_the_words(self):
        c = PL.cut_points({**_bm(), "duration": 40.0}, PRESETS["lyrics"])
        self.assertIn(24.5, c)
        self.assertIn(25.5, c)

    def test_full_variant(self):
        for preset in PRESETS:
            edl, info = PL.plan("v.mp4", SHOTS, _bm(), preset, PL.PlanOptions(duration_s=20, hero=3))
            self.assertAlmostEqual(edl.dur, info["dur"], places=6)
            self.assertEqual(info["hero"], 3)
            # the hero shot starts exactly on the output's drop
            t = 0.0
            for s in edl.segments:
                if abs(t - info["drop"]) < 1e-6:
                    self.assertEqual(s.label, "plano 3")
                    self.assertTrue(any(p.frozen for p in s.pieces))
                t += s.dur
            # each piece uses source from ITS shot
            for s in edl.segments:
                sid = int(s.label.split()[1])
                shot = SHOTS[sid - 1]
                for p in s.pieces:
                    self.assertGreaterEqual(p.start, shot["start"] - 1e-6)
                    self.assertLessEqual(p.end, shot["end"] + 1e-6)
        edl, _ = PL.plan("v.mp4", SHOTS, _bm(), "lyrics", PL.PlanOptions(duration_s=20))
        self.assertEqual([t.text for t in edl.texts], ["hey!", "go"])
        edl, _ = PL.plan("v.mp4", SHOTS, _bm(), "cinematic", PL.PlanOptions(duration_s=20))
        self.assertEqual(edl.texts, [])

    def test_formats(self):
        edl, _ = PL.plan("v.mp4", SHOTS, _bm(), "impact", PL.PlanOptions(format="horizontal"))
        self.assertEqual((edl.width, edl.height), (1920, 1080))
        with self.assertRaises(ValueError):
            PL.plan("v.mp4", SHOTS, _bm(), "impact", PL.PlanOptions(format="square"))
        with self.assertRaises(ValueError):
            PL.plan("v.mp4", SHOTS, _bm(), "nada", PL.PlanOptions())


class Validation(unittest.TestCase):
    def test_phase1(self):
        p = AnimeEditParams.from_form({"music_from_video": "1"})
        self.assertTrue(p.music_from_video and p.lyrics.lyrics)
        self.assertEqual(AnimeEditParams.from_form({"video_url": "https://youtu.be/x"}).video.format, "video")
        for form in ({"video_url": "x"}, {"engine": "x"}, {"music_url": "x"}):
            with self.assertRaises(ValueError):
                AnimeEditParams.from_form(form)

    def test_phase2(self):
        ids = {1, 2, 3}
        fp = FinishParams.from_form({"selection": "[3, 1]", "hero": "3", "variants": "impact,cinematic"}, ids)
        self.assertEqual((fp.selection, fp.hero, fp.variants), ([3, 1], 3, ["impact", "cinematic"]))
        self.assertEqual(FinishParams.from_form({}, ids).variants, ["impact"])
        for form in ({"selection": "[9]"}, {"hero": "9"}, {"variants": "nada"}, {"format": "x"},
                     {"fps": "25"}, {"text": "x"}, {"look": "x"}, {"lut": "x.cube"}, {"duration_s": "500"}):
            with self.assertRaises(ValueError, msg=form):
                FinishParams.from_form(form, ids)


class TwoPhases(unittest.TestCase):
    def test_process_and_finish_through_the_api(self):
        from app import create_app
        tmp = S.tmpdir("anime")
        try:
            video = S.shots_video(os.path.join(tmp, "v.mp4"), [1.5, 2.0, 1.0, 2.5, 1.5, 2.0])
            music = S.song(os.path.join(tmp, "m.wav"), bpm=120, dur=30, loud_from=8)
            with mock.patch.object(config, "BEATMAPS_FOLDER", os.path.join(tmp, "bm")), \
                    mock.patch.object(config, "EDITS_FOLDER", os.path.join(tmp, "edits")):
                c = create_app().test_client()
                with open(video, "rb") as fv, open(music, "rb") as fm:
                    r = c.post("/api/anime-edit/process", content_type="multipart/form-data",
                               data={"video": (fv, "v.mp4"), "music": (fm, "m.wav"), "lyrics": "0"})
                self.assertEqual(r.status_code, 202, r.json)
                job = r.json["job_id"]
                st = self._wait(c, job)
                self.assertEqual(st["stage"], "esperando_seleccion")
                self.assertEqual(len(st["shots"]), 6)
                self.assertEqual(c.get(f"/api/anime-edit/sheet/{job}/1").status_code, 200)
                self.assertEqual(c.get(f"/api/anime-edit/thumb/{job}/2").status_code, 200)
                r = c.post("/api/anime-edit/finish", json={"job_id": job, "selection": [2, 4, 5], "hero": 4,
                                                           "variants": ["impact", "velocity"],
                                                           "duration_s": 12, "fps": 24, "title": "prueba"})
                self.assertEqual(r.status_code, 202, r.json)
                st = self._wait(c, job)
                self.assertTrue(st["finished"])
                vs = st["result"]["variants"]
                self.assertEqual([v["variant"] for v in vs], ["impact", "velocity"])
                for v in vs:
                    self.assertEqual(ffmpeg_runner.probe_resolution(v["video"]), (1080, 1920))
                    self.assertAlmostEqual(ffmpeg_runner.probe_duration(v["video"]), v["duration"], delta=0.1)
                self.assertEqual(c.get(f"/api/anime-edit/download/{job}/2").status_code, 200)
                self.assertEqual(c.post("/api/anime-edit/finish", json={"job_id": job, "hero": 99}).status_code, 400)
        finally:
            processor._SESSIONS.clear()
            S.remove(tmp)

    @staticmethod
    def _wait(c, job):
        for _ in range(600):
            st = c.get(f"/api/anime-edit/status/{job}").json
            if st["status"] in ("done", "error") and st["stage"] != "Preparando el edit…":
                if st["status"] == "error":
                    raise AssertionError(st["error"])
                return st
            time.sleep(0.25)
        raise AssertionError("took too long")
