"""Tests of the montage: layer timing (pure), validation and the render with
transparent layers over a synthetic video."""
import json
import os
import subprocess
import time
import unittest
from unittest import mock

import numpy as np

import config
from core import ffmpeg_runner, job_manager
from modules.montage import layers as L
from modules.montage import processor
from modules.montage.schema import MontageParams
from tests import synthetic as S

_PHRASE = "Primero el Rey Demonio estará enfrentando a los dieciséis campeones y después T1 cierra"
SEGMENTS = [{"id": 0, "start": 0.0, "end": 6.9, "text": _PHRASE,
             "words": [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate(_PHRASE.split())]}]


class Normalize(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(L.tokens("¡Rey DEMONIO!"), ["rey", "demonio"])
        self.assertEqual(L.tokens("a los dieciséis años"), ["a", "los", "16", "anos"])
        self.assertEqual(L.tokens("treinta y dos"), ["32"])
        self.assertEqual(L.tokens("un campeón"), ["un", "campeon"])

    def test_find(self):
        t = L.transcript_words(SEGMENTS)
        self.assertEqual(L.find(t, "rey demonio"), 1.0)
        self.assertEqual(L.find(t, "16 campeones"), 4.0)            # Whisper wrote "dieciséis"
        self.assertEqual(L.find(t, "t1"), 6.0)
        self.assertIsNone(L.find(t, "rey demonio", n=2))


class Resolve(unittest.TestCase):
    BOX = L.Box()

    def test_lux_rules(self):
        specs = [{"file": "a", "word": "rey demonio"}, {"file": "b", "start": 30},
                 {"file": "c", "start": 44}]
        a, b, c = L.resolve(specs, SEGMENTS, 60, self.BOX)
        self.assertAlmostEqual(a.start, 0.9)               # 0.1 s before the word
        self.assertAlmostEqual(a.end, 0.9 + L.MAX_S)       # 12 s cap (the gap it leaves is long)
        self.assertEqual(b.end, 44.25)                     # stuck to the next one (+ its fade)
        self.assertEqual(b.fade_out, 0.0)
        self.assertEqual(c.end, 56)                        # cap

    def test_short_gap_sticks(self):
        a, b = L.resolve([{"file": "a", "start": 0}, {"file": "b", "start": 13}], None, 30, self.BOX)
        self.assertEqual(a.end, 13 + 0.25)

    def test_errors(self):
        cases = [([{"file": "a", "word": "zapato"}], SEGMENTS),
                 ([{"file": "a", "word": "rey"}], None),
                 ([{"file": "a"}], None),
                 ([{"file": "a", "start": 0, "end": 10}, {"file": "b", "start": 5}], None),
                 ([{"file": "a", "start": 3, "end": 3.1}], None)]
        for specs, segs in cases:
            with self.assertRaises(ValueError, msg=specs):
                L.resolve(specs, segs, 60, self.BOX)


class Validation(unittest.TestCase):
    def setUp(self):
        self.tmp = S.tmpdir("montage-val")
        self.p = mock.patch.object(config, "LAYERS_FOLDER", self.tmp)
        self.p.start()
        os.makedirs(os.path.join(self.tmp, "laminas"))
        with open(os.path.join(self.tmp, "laminas", "manifest.json"), "w") as fh:
            json.dump([{"id": "p-kda", "archivo": "p-kda.mov"}], fh)

    def tearDown(self):
        self.p.stop()
        S.remove(self.tmp)

    def test_manifest_and_folder(self):
        p = MontageParams.from_form({"layers": json.dumps([{"id": "p-kda", "start": 1}]),
                                     "layers_folder": os.path.join(self.tmp, "laminas"),
                                     "box": json.dumps({"width": 600})})
        self.assertEqual(p.specs[0]["file"], "p-kda.mov")
        self.assertEqual(p.specs[0]["box"]["width"], 600)
        with self.assertRaises(ValueError):            # the file does not exist
            p.path("p-kda.mov")
        with self.assertRaises(ValueError):            # outside the folder
            p.path("../../etc/passwd")

    def test_errors(self):
        cases = [{}, {"layers": "x"}, {"layers": "[]"},
                 {"layers": json.dumps([{"start": 1}])},
                 {"layers": json.dumps([{"id": "nada", "start": 1}]), "layers_folder": os.path.join(self.tmp, "laminas")},
                 {"layers": json.dumps([{"file": "a.mov", "start": 1}]), "layers_folder": "/"},
                 {"layers": json.dumps([{"file": "a.mov", "start": 1, "box": {"x": "top"}}])},
                 {"layers": json.dumps([{"file": "a.mov", "start": 1, "box": {"color": 1}}])},
                 {"layers": json.dumps([{"file": "a.mov", "start": 1, "box": {"mode": "band", "y": 1800}}])}]
        for form in cases:
            with self.assertRaises(ValueError, msg=form):
                MontageParams.from_form(form)


def _alpha_layer(path: str, dur: float = 1.0) -> str:
    """ProRes 4444 with alpha: an opaque yellow square in the middle of a
    transparent 400x300 canvas."""
    subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=black:s=400x300:r=30:d={dur},format=rgba,"
                    # drawbox does not write the alpha channel: the square is built with geq
                    "geq=r=255:g=255:b=0:a='if(between(X,100,299)*between(Y,75,224),255,0)'",
                    "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", path], check=True)
    return path


class Render(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("montage")
        cls.base = S.shots_video(os.path.join(cls.tmp, "base.mp4"), [6.0], w=1080, h=1920, fps=30)
        cls.layer = _alpha_layer(os.path.join(cls.tmp, "flyer.mov"))
        cls.png = os.path.join(cls.tmp, "still.png")
        subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=blue:s=300x300:d=1", "-frames:v", "1", cls.png], check=True)

    @classmethod
    def tearDownClass(cls):
        S.remove(cls.tmp)

    def _frame(self, video, t):
        raw = subprocess.run([config.FFMPEG_PATH, "-v", "error", "-ss", f"{t}", "-i", video, "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)

    def test_layers_on_time_with_transparency(self):
        layers = [L.Layer(self.layer, 1.0, 3.0, L.Box(width=400, bottom=100), clip_dur=1.0),
                  L.Layer(self.png, 4.0, 5.0, L.Box(mode="band", y=1240, height=600))]
        out = os.path.join(self.tmp, "out.mp4")
        ffmpeg_runner.capture(processor.build_command(self.base, layers, out), cwd=config.BASE_DIR)
        self.assertAlmostEqual(ffmpeg_runner.probe_duration(out), 6.0, delta=0.1)
        self.assertTrue(ffmpeg_runner.has_audio_stream(out))
        # box: 400x300 bottom center, 100 px over the edge → center at (540, 1670)
        base = self._frame(out, 0.5)[1670, 540]
        during = self._frame(out, 2.5)[1670, 540]              # frozen after its 1 s
        edge = self._frame(out, 2.5)[1530, 360]                # transparent area of the layer
        after = self._frame(out, 3.6)[1670, 540]
        self.assertGreater(during[0], 170)
        self.assertGreater(during[1], 170)
        self.assertLess(during[2], 90)                         # yellow
        self.assertLess(np.abs(edge - base).max(), 25)         # the video behind shows through
        self.assertLess(np.abs(after - base).max(), 25)
        # band: the blue PNG at the band's center and its blurred full-width copy
        f = self._frame(out, 4.5)
        self.assertGreater(f[1540, 540][2], 150)
        self.assertLess(f[1540, 540][0], 80)
        self.assertGreater(np.abs(f[1540, 40] - base).max(), 25)    # the copy covers the sides

    def test_api_job_with_words(self):
        from app import create_app
        c = create_app().test_client()
        with mock.patch.object(processor.subtitles, "transcribe", return_value=SEGMENTS):
            with open(self.base, "rb") as fv, open(self.layer, "rb") as fc:
                r = c.post("/api/montage/process", content_type="multipart/form-data", data={
                    "video": (fv, "base.mp4"), "layer_files": [(fc, "flyer.mov")],
                    "layers": json.dumps([{"file": "flyer.mov", "word": "rey demonio", "end": 2.5}]),
                })
            self.assertEqual(r.status_code, 202, r.json)
            job = r.json["job_id"]
            for _ in range(200):
                st = c.get(f"/api/montage/status/{job}").json
                if st["status"] in ("done", "error"):
                    break
                time.sleep(0.2)
        self.assertEqual(st["status"], "done", st["error"])
        self.assertEqual(st["layers"][0]["start"], 0.9)
        self.assertEqual(c.get(f"/api/montage/download/{job}").status_code, 200)
        self.assertEqual(c.post("/api/montage/process", data={"layers": "[]"}).status_code, 400)


class ReelExpressFinish(unittest.TestCase):
    """The layers go in the same pass as Reel Express's subtitles."""

    def test_finish_with_layers(self):
        import shutil
        from modules.reel_express import processor as rx
        from modules.subtitles.schema import SubtitlesParams
        tmp = S.tmpdir("rx-layers")
        try:
            base = S.shots_video(os.path.join(tmp, "base.mp4"), [4.0], w=1080, h=1920, fps=30)
            copy = os.path.join(tmp, "copy.mp4")
            shutil.copy(base, copy)
            _alpha_layer(os.path.join(tmp, "flyer.mov"))
            with mock.patch.object(config, "LAYERS_FOLDER", tmp):
                m = MontageParams.from_form({"layers": json.dumps([{"file": "flyer.mov", "word": "demonio"}]),
                                             "layers_folder": tmp})
            job = job_manager.create_job(copy, "")
            out = os.path.join(config.OUTPUT_FOLDER, f"{job}.mp4")
            rx.finish(job, copy, SEGMENTS, SubtitlesParams(), out, montage=m)
            j = job_manager.get_job(job)
            self.assertEqual(j["status"], "done", j["error"])
            self.assertEqual(j["layers"][0]["start"], 1.4)
            self.assertTrue(os.path.isfile(j["output_path"]))
            self.assertFalse(os.path.exists(copy))           # the base video is deleted, as always
            os.remove(j["output_path"])
        finally:
            S.remove(tmp)
