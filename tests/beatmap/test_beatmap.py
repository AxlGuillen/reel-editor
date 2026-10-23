"""Tests of the beatmap module (offline; they use librosa and the system FFmpeg)."""
import json
import os
import time
import unittest
from unittest import mock

import numpy as np

import config
from modules.beatmap import analysis as A
from modules.beatmap import processor as P
from modules.beatmap.schema import BeatmapParams, LyricsParams
from tests import synthetic as S


class Analysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 120 BPM, first beat at 0.25 s; the chorus (full volume) starts at 16.25.
        cls.bm = A.analyze(S.signal(bpm=120, dur=40, loud_from=16), sr=S.SR, engine="librosa")

    def test_tempo_and_beats(self):
        self.assertAlmostEqual(self.bm["bpm"], 120, delta=1.5)
        beats = np.array(self.bm["beats"])
        # each beat lands less than 25 ms from the real grid (0.25 + k·0.5)
        err = np.abs(beats - (0.25 + np.round((beats - 0.25) / 0.5) * 0.5))
        self.assertLess(float(np.median(err)), 0.025)

    def test_downbeats_on_the_kick(self):
        # The kick hits at 0.25, 2.25, 4.25…: the downbeats must land there.
        d = np.array(self.bm["downbeats"])
        phase = (d - 0.25) % 2.0
        near = np.minimum(phase, 2.0 - phase)
        self.assertLess(float(np.median(near)), 0.05)

    def test_sections_and_drop(self):
        kinds = [s["type"] for s in self.bm["sections"]]
        self.assertEqual(kinds[0], "intro")
        self.assertIn("chorus", kinds)
        self.assertAlmostEqual(self.bm["drop"], 16.25, delta=0.6)
        self.assertTrue(any(abs(b - self.bm["drop"]) < 0.01 for _, b in self.bm["buildups"]))
        # the sections cover the whole song without gaps
        secs = self.bm["sections"]
        self.assertEqual(secs[0]["start"], 0.0)
        for a, b in zip(secs, secs[1:]):
            self.assertEqual(a["end"], b["start"])

    def test_energy_per_beat(self):
        e = self.bm["energy_per_beat"]
        self.assertEqual(len(e), len(self.bm["beats"]))
        self.assertTrue(all(0 <= v <= 1 for v in e))
        before = [v for b, v in zip(self.bm["beats"], e) if b < 15]
        after = [v for b, v in zip(self.bm["beats"], e) if b > 17]
        self.assertGreater(np.mean(after), 2 * np.mean(before))

    def test_invalid_engine(self):
        with self.assertRaises(ValueError):
            A.analyze(S.signal(dur=5), sr=S.SR, engine="x")

    def test_optional_engine_that_fails_falls_back(self):
        y = S.signal(bpm=120, dur=12)
        # beat-this installed but broken (a torch without kernels for the GPU):
        # "auto" goes on with librosa, "beat_this" says why.
        with mock.patch.object(A, "beat_this_available", return_value=True),                 mock.patch.object(A, "_beat_this", side_effect=RuntimeError("no kernel image")):
            self.assertEqual(A.analyze(y, sr=S.SR, engine="auto", path="x.wav")["engine"], "librosa")
            with self.assertRaisesRegex(RuntimeError, "no kernel image"):
                A.analyze(y, sr=S.SR, engine="beat_this", path="x.wav")
        # a torch whose DLLs don't load raises OSError on import, not ImportError
        import builtins
        real_import = builtins.__import__

        def broken(name, *args, **kwargs):
            if name.startswith(("beat_this", "audio_separator")):
                raise OSError("[WinError 126] c10.dll")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", side_effect=broken):
            self.assertFalse(A.beat_this_available())
            from modules.beatmap import lyrics
            self.assertFalse(lyrics.separation_available())

    def test_lyrics_errors_are_not_an_empty_song(self):
        from modules.beatmap import lyrics
        with mock.patch.object(lyrics.subtitles, "transcribe",
                               side_effect=RuntimeError("No se detectó voz en el video.")):
            self.assertEqual(lyrics.words("x.wav"), [])          # instrumental: fine
        with mock.patch.object(lyrics.subtitles, "transcribe",
                               side_effect=RuntimeError("cublas64_12.dll not found")):
            with self.assertRaises(RuntimeError):                 # a real failure is not cached as []
                lyrics.words("x.wav")


class Window(unittest.TestCase):
    BM = {
        "beats": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0], "downbeats": [0.0, 2.0],
        "energy_per_beat": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7], "onsets": [0.5, 2.5],
        "hits": [2.5], "sections": [{"type": "intro", "start": 0.0, "end": 2.0, "energy": 0},
                                    {"type": "chorus", "start": 2.0, "end": 3.5, "energy": 1}],
        "drop": 2.0, "buildups": [[1.0, 2.0]], "words": [{"text": "hey", "start": 2.2, "end": 2.4}],
        "duration": 3.5, "bpm": 120,
    }

    def test_times_relative_to_the_window(self):
        r = A.window(self.BM, 1.0, 2.0)
        self.assertEqual(r["beats"], [0.0, 0.5, 1.0, 1.5])
        self.assertEqual(r["energy_per_beat"], [0.3, 0.4, 0.5, 0.6])
        self.assertEqual(r["downbeats"], [1.0])
        self.assertEqual(r["drop"], 1.0)
        self.assertEqual(r["buildups"], [[0.0, 1.0]])
        self.assertEqual([(s["type"], s["start"], s["end"]) for s in r["sections"]],
                         [("intro", 0.0, 1.0), ("chorus", 1.0, 2.0)])
        self.assertEqual(r["words"][0]["start"], 1.2)
        self.assertEqual(A.section_at(r, 1.5), "chorus")
        self.assertTrue(A.in_buildup(r, 0.5))

    def test_drop_outside_the_window(self):
        self.assertIsNone(A.window(self.BM, 2.5, 1.0)["drop"])


class Process(unittest.TestCase):
    def setUp(self):
        self.tmp = S.tmpdir("beatmap")
        self.song = S.song(os.path.join(self.tmp, "c.wav"), bpm=120, dur=20, loud_from=8)
        self.patches = [mock.patch.object(config, "BEATMAPS_FOLDER", os.path.join(self.tmp, "cache"))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        S.remove(self.tmp)

    def test_cache_by_fingerprint(self):
        a = P.get(self.song, "librosa")
        with mock.patch.object(A, "analyze", side_effect=AssertionError("should not re-analyze")):
            b = P.get(self.song, "librosa")
        self.assertEqual(a, b)
        self.assertEqual(a["source"], "c.wav")

    def test_lyrics_without_vocals_gives_empty_list(self):
        with mock.patch.object(P.lyrics_mod, "words", return_value=[]) as words, \
                mock.patch.object(P.lyrics_mod, "isolate_vocals", return_value=None):
            bm = P.get(self.song, "librosa", LyricsParams(lyrics=True))
        words.assert_called_once()
        self.assertEqual(bm["words"], [])
        self.assertEqual(bm["lyrics_from"], "mix")

    def test_preview_and_job(self):
        from core import job_manager
        job = job_manager.create_job(self.song, "")
        with mock.patch.object(config, "OUTPUT_FOLDER", os.path.join(self.tmp, "out")):
            P.process(job, self.song, BeatmapParams(engine="librosa"))
            j = job_manager.get_job(job)
            self.assertEqual(j["status"], "done", j["error"])
            self.assertTrue(os.path.isfile(j["output_path"]))
            with open(j["json_path"], encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["bpm"], j["beatmap"]["bpm"])
        # the preview lasts as long as the song and has audio
        from core import ffmpeg_runner
        self.assertAlmostEqual(ffmpeg_runner.probe_duration(j["output_path"]), 20, delta=0.2)
        self.assertTrue(ffmpeg_runner.has_audio_stream(j["output_path"]))
        # the user's song lives outside uploads: it is not deleted
        self.assertTrue(os.path.isfile(self.song))


    def test_preview_with_hundreds_of_beats(self):
        # ~400 beats is any 3-minute song. A flat sum of 100+ between() in
        # enable= did not parse (on every OS) and the line passed Windows' limit.
        from core import ffmpeg_runner
        beats = [round(i * 0.04, 3) for i in range(500)]
        bm = {"duration": 20.0, "beats": beats, "downbeats": beats[::4], "drop": 8.0,
              "sections": [{"type": "verse", "start": 0.0, "end": 8.0},
                           {"type": "chorus", "start": 8.0, "end": 20.0}]}
        beeps = P.write_beeps(bm, os.path.join(self.tmp, "beeps.wav"))
        out = os.path.join(self.tmp, "preview.mp4")
        ffmpeg_runner.capture(P.build_preview_command(self.song, beeps, bm, out), timeout=600)
        self.assertAlmostEqual(ffmpeg_runner.probe_duration(out), 20, delta=0.2)


class Validation(unittest.TestCase):
    def test_defaults(self):
        p = BeatmapParams.from_form({})
        self.assertEqual((p.engine, p.preview, p.music, p.lyrics.lyrics), ("auto", True, None, False))

    def test_music_link(self):
        p = BeatmapParams.from_form({"music_url": "https://youtu.be/x", "lyrics": "1", "language": "es"})
        self.assertEqual((p.music.url, p.music.format), ("https://youtu.be/x", "audio"))
        self.assertTrue(p.lyrics.lyrics)

    def test_errors(self):
        for form in ({"engine": "x"}, {"music_url": "ftp://x"}, {"language": "xx"}, {"model": "gigante"}):
            with self.assertRaises(ValueError, msg=form):
                BeatmapParams.from_form(form)


class Api(unittest.TestCase):
    def test_process_status_download(self):
        from app import create_app
        tmp = S.tmpdir("beatmap-api")
        try:
            song = S.song(os.path.join(tmp, "c.wav"), bpm=128, dur=12, loud_from=6)
            with mock.patch.object(config, "BEATMAPS_FOLDER", os.path.join(tmp, "cache")):
                client = create_app().test_client()
                with open(song, "rb") as fh:
                    r = client.post("/api/beatmap/process", data={"audio": (fh, "c.wav"), "engine": "librosa"},
                                    content_type="multipart/form-data")
                self.assertEqual(r.status_code, 202, r.json)
                job = r.json["job_id"]
                for _ in range(120):
                    st = client.get(f"/api/beatmap/status/{job}").json
                    if st["status"] in ("done", "error"):
                        break
                    time.sleep(0.25)
                self.assertEqual(st["status"], "done", st["error"])
                self.assertAlmostEqual(st["summary"]["bpm"], 128, delta=2)
                self.assertEqual(client.get(f"/api/beatmap/download/{job}").status_code, 200)
                self.assertEqual(client.get(f"/api/beatmap/json/{job}").status_code, 200)
            self.assertEqual(client.post("/api/beatmap/process", data={}).status_code, 400)
        finally:
            S.remove(tmp)
