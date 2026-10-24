"""Ibai's emotion peaks (voice.py), the team's fights and an edit render that
can be retried (offline: synthetic audio and video)."""
import os
import subprocess
import unittest
from unittest import mock

import numpy as np

import config
from core import job_manager
from modules.competitivo import processor, voice
from modules.competitivo.schema import EditParams
from modules.subtitles import processor as subtitles
from modules.subtitles.schema import SubtitlesParams
from tests import synthetic as S
from tests.competitivo import test_edit


def _voice_track(path: str, parts: list[tuple[float, float, float]]) -> str:
    """A "voice": a harmonic tone (pitch Hz, level dB) per part of (dur, f0, dB)
    over a quiet noise floor, like Ibai over the game."""
    sr = 16000
    chunks = []
    for dur, f0, db in parts:
        t = np.arange(int(dur * sr)) / sr
        tone = sum(np.sin(2 * np.pi * f0 * k * t) / k for k in (1, 2, 3))
        chunks.append(10 ** (db / 20) * tone / 1.8)
    y = np.concatenate(chunks)
    y = y + np.random.default_rng(1).normal(0, 0.003, len(y))
    pcm = (np.clip(y, -1, 1) * 32000).astype("<i2")
    raw = path + ".pcm"
    pcm.tofile(raw)
    subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", raw,
                    "-c:a", "aac", path], check=True)
    os.remove(raw)
    return path


class Peaks(unittest.TestCase):
    def setUp(self):
        self.tmp = S.tmpdir("voice")

    def tearDown(self):
        S.remove(self.tmp)

    def test_louder_and_higher_for_long_enough(self):
        calm = (180.0, -26.0)
        path = _voice_track(os.path.join(self.tmp, "v.m4a"), [
            (4.0, *calm),
            (1.5, 320.0, -16.0),           # a peak: louder AND higher, 1.5 s
            (3.0, *calm),
            (0.4, 320.0, -16.0),           # too short
            (3.0, *calm),
            (1.5, 180.0, -16.0),           # louder only (the game, not Ibai getting excited)
            (3.0, *calm),
        ])
        feat = voice.features(path)
        found = voice.peaks(feat, *voice.medians([feat]))
        self.assertEqual(len(found), 1, found)
        a, b, db = found[0]
        self.assertAlmostEqual(a, 4.0, delta=0.15)
        self.assertAlmostEqual(b, 5.5, delta=0.15)
        self.assertGreater(db, voice.PEAK_DB)

    def test_medians_are_the_games(self):
        # the same shout in a calm span and in a loud one: compared with the whole game
        a = _voice_track(os.path.join(self.tmp, "a.m4a"), [(6.0, 180.0, -26.0), (1.2, 320.0, -15.0)])
        b = _voice_track(os.path.join(self.tmp, "b.m4a"), [(6.0, 180.0, -26.0)])
        fa, fb = voice.features(a), voice.features(b)
        med = voice.medians([fa, fb])
        self.assertEqual(len(voice.peaks(fa, *med)), 1)
        self.assertEqual(voice.peaks(fb, *med), [])


class SubtitleWindows(unittest.TestCase):
    def test_words_only_inside_their_windows(self):
        words = [{"word": w, "start": t, "end": t + 0.3} for w, t in
                 (("vamos", 0.5), ("ahí", 1.0), ("no", 5.0), ("madre", 8.2), ("mía", 8.6))]
        ass = subtitles._build_invert_ass([words], SubtitlesParams(layout="invert"),
                                          windows=[(0.0, 1.2), (8.0, 9.0)])
        lines = [x for x in ass.splitlines() if x.startswith("Dialogue:")]
        self.assertEqual([x.rsplit("}", 1)[1] for x in lines], ["VAMOS", "AHÍ", "MADRE", "MÍA"])
        self.assertIn(",0:00:01.20,", lines[1])                                  # cut at its window's end
        self.assertIn(",0:00:09.00,", lines[3])


class Team(unittest.TestCase):
    SESION = {"partidas": [{"game_id": "1", "azul": "LOS", "rojo": "T1"}],
              "clips": [{"id": "1-1", "game_id": "1", "eventos": [["x", "izq"]]},
                        {"id": "1-2", "game_id": "1", "eventos": [["x", "izq"], ["y", "der"]]},
                        {"id": "1-3", "game_id": "1", "eventos": [["x", "der"]]}]}

    def test_every_fight_where_the_team_killed(self):
        self.assertEqual(processor.team_clips(self.SESION, "t1"), ["1-2", "1-3"])
        self.assertEqual(processor.team_clips(self.SESION, "LOS"), ["1-1", "1-2"])
        self.assertEqual(processor.team_clips(self.SESION, "GEN"), [])
        self.assertEqual(processor.team_codes(self.SESION), {"LOS", "T1"})


class RetryAndTeam(unittest.TestCase):
    """The job with the synthetic VOD (FullJob's session: KC vs C9)."""
    setUp, tearDown = test_edit.FullJob.setUp, test_edit.FullJob.tearDown
    _span, _session = staticmethod(test_edit.FullJob._span), test_edit.FullJob._session

    def test_api_team(self):
        from app import create_app
        self._session()
        c = create_app().test_client()
        r = c.post("/api/competitivo/edit", data={"job_id": "phase1", "team": "T1", "music_url": "https://x.com/a.mp3"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("KC", r.get_json()["error"])

    def test_team_takes_every_fight_it_killed_in(self):
        params = self._session()
        music = S.song(os.path.join(self.tmp, "m.wav"), bpm=120, dur=60, loud_from=24)
        ep = EditParams.from_form({"team": "KC", "structure": "beat", "max_duration_s": "0"}, params)
        job = job_manager.create_job("x", "")
        processor.edit(job, "phase1", ep, music)
        j = job_manager.get_job(job)
        self.assertEqual(j["status"], "done", j["error"])
        self.assertEqual(sorted(j["resultado"]["used_clips"]), ["1-1", "1-2"])
        self.assertEqual(set(j["split_camera"]), {"1-1", "1-2"})               # per clip, for checking

    def test_a_failed_render_can_be_retried(self):
        params = self._session()
        music = S.song(os.path.join(self.tmp, "m.wav"), bpm=120, dur=60, loud_from=24)
        ep = EditParams.from_form({"voice_subs": "0"}, params)
        real = processor.editing.render
        calls = []

        def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("FFmpeg falló (code -22): h264_nvenc")
            return real(*args, **kwargs)

        job = job_manager.create_job("x", "")
        with mock.patch.object(processor.editing, "render", side_effect=flaky):
            processor.edit(job, "phase1", ep, music)
            j = job_manager.get_job(job)
            self.assertEqual((j["status"], j["retryable"]), ("error", True))
            self.assertTrue(processor.edit_waiting(job))
            from app import create_app
            c = create_app().test_client()
            r = c.post("/api/competitivo/edit/finish", data={"job_id": job})          # no segments: retry
            self.assertEqual(r.status_code, 202)
            for _ in range(600):
                if job_manager.get_job(job)["status"] in ("done", "error") and not processor.edit_waiting(job):
                    if job_manager.get_job(job)["status"] == "done":
                        break
                import time
                time.sleep(0.1)
        j = job_manager.get_job(job)
        self.assertEqual((j["status"], j["finished"]), ("done", True), j["error"])
        self.assertTrue(os.path.isfile(j["resultado"]["video"]))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
