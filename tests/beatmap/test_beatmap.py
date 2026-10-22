"""Tests del módulo beatmap (sin internet; usan librosa y el FFmpeg del sistema)."""
import json
import os
import time
import unittest
from unittest import mock

import numpy as np

import config
from modules.beatmap import analisis as A
from modules.beatmap import processor as P
from modules.beatmap.schema import BeatmapParams, LetraParams
from tests import sinteticos as S


class Analisis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 120 BPM, primer beat en 0.25 s; el coro (todo a tope) arranca en 16.25.
        cls.bm = A.analizar(S.senal(bpm=120, dur=40, alto_desde=16), sr=S.SR, motor="librosa")

    def test_tempo_y_beats(self):
        self.assertAlmostEqual(self.bm["bpm"], 120, delta=1.5)
        beats = np.array(self.bm["beats"])
        # cada beat cae a menos de 25 ms de la rejilla real (0.25 + k·0.5)
        err = np.abs(beats - (0.25 + np.round((beats - 0.25) / 0.5) * 0.5))
        self.assertLess(float(np.median(err)), 0.025)

    def test_downbeats_en_el_bombo(self):
        # El bombo pega en 0.25, 2.25, 4.25…: los downbeats tienen que caer ahí.
        d = np.array(self.bm["downbeats"])
        fase = (d - 0.25) % 2.0
        cerca = np.minimum(fase, 2.0 - fase)
        self.assertLess(float(np.median(cerca)), 0.05)

    def test_secciones_y_drop(self):
        tipos = [s["tipo"] for s in self.bm["secciones"]]
        self.assertEqual(tipos[0], "intro")
        self.assertIn("coro", tipos)
        self.assertAlmostEqual(self.bm["drop"], 16.25, delta=0.6)
        self.assertTrue(any(abs(b - self.bm["drop"]) < 0.01 for _, b in self.bm["subidas"]))
        # las secciones cubren toda la canción sin huecos
        secs = self.bm["secciones"]
        self.assertEqual(secs[0]["inicio"], 0.0)
        for a, b in zip(secs, secs[1:]):
            self.assertEqual(a["fin"], b["inicio"])

    def test_energia_por_beat(self):
        e = self.bm["energia_por_beat"]
        self.assertEqual(len(e), len(self.bm["beats"]))
        self.assertTrue(all(0 <= v <= 1 for v in e))
        antes = [v for b, v in zip(self.bm["beats"], e) if b < 15]
        despues = [v for b, v in zip(self.bm["beats"], e) if b > 17]
        self.assertGreater(np.mean(despues), 2 * np.mean(antes))

    def test_motor_invalido(self):
        with self.assertRaises(ValueError):
            A.analizar(S.senal(dur=5), sr=S.SR, motor="x")


class Recortar(unittest.TestCase):
    BM = {
        "beats": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0], "downbeats": [0.0, 2.0],
        "energia_por_beat": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7], "onsets": [0.5, 2.5],
        "golpes": [2.5], "secciones": [{"tipo": "intro", "inicio": 0.0, "fin": 2.0, "energia": 0},
                                       {"tipo": "coro", "inicio": 2.0, "fin": 3.5, "energia": 1}],
        "drop": 2.0, "subidas": [[1.0, 2.0]], "palabras": [{"texto": "hey", "inicio": 2.2, "fin": 2.4}],
        "duracion": 3.5, "bpm": 120,
    }

    def test_tiempos_relativos_a_la_ventana(self):
        r = A.recortar(self.BM, 1.0, 2.0)
        self.assertEqual(r["beats"], [0.0, 0.5, 1.0, 1.5])
        self.assertEqual(r["energia_por_beat"], [0.3, 0.4, 0.5, 0.6])
        self.assertEqual(r["downbeats"], [1.0])
        self.assertEqual(r["drop"], 1.0)
        self.assertEqual(r["subidas"], [[0.0, 1.0]])
        self.assertEqual([(s["tipo"], s["inicio"], s["fin"]) for s in r["secciones"]],
                         [("intro", 0.0, 1.0), ("coro", 1.0, 2.0)])
        self.assertEqual(r["palabras"][0]["inicio"], 1.2)
        self.assertEqual(A.seccion_en(r, 1.5), "coro")
        self.assertTrue(A.en_subida(r, 0.5))

    def test_drop_fuera_de_la_ventana(self):
        self.assertIsNone(A.recortar(self.BM, 2.5, 1.0)["drop"])


class Proceso(unittest.TestCase):
    def setUp(self):
        self.tmp = S.tmpdir("beatmap")
        self.cancion = S.cancion(os.path.join(self.tmp, "c.wav"), bpm=120, dur=20, alto_desde=8)
        self.parches = [mock.patch.object(config, "BEATMAPS_FOLDER", os.path.join(self.tmp, "cache"))]
        for p in self.parches:
            p.start()

    def tearDown(self):
        for p in self.parches:
            p.stop()
        S.borrar(self.tmp)

    def test_cache_por_huella(self):
        a = P.obtener(self.cancion, "librosa")
        with mock.patch.object(A, "analizar", side_effect=AssertionError("no debía reanalizar")):
            b = P.obtener(self.cancion, "librosa")
        self.assertEqual(a, b)
        self.assertEqual(a["fuente"], "c.wav")

    def test_letra_sin_voz_da_lista_vacia(self):
        with mock.patch.object(P.letra_mod, "palabras", return_value=[]) as palabras, \
                mock.patch.object(P.letra_mod, "separar_voz", return_value=None):
            bm = P.obtener(self.cancion, "librosa", LetraParams(letra=True))
        palabras.assert_called_once()
        self.assertEqual(bm["palabras"], [])
        self.assertEqual(bm["letra_de"], "mezcla")

    def test_preview_y_job(self):
        from core import job_manager
        job = job_manager.create_job(self.cancion, "")
        with mock.patch.object(config, "OUTPUT_FOLDER", os.path.join(self.tmp, "out")):
            P.process(job, self.cancion, BeatmapParams(motor="librosa"))
            j = job_manager.get_job(job)
            self.assertEqual(j["status"], "done", j["error"])
            self.assertTrue(os.path.isfile(j["output_path"]))
            with open(j["json_path"], encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["bpm"], j["beatmap"]["bpm"])
        # el preview dura lo que la canción y trae audio
        from core import ffmpeg_runner
        self.assertAlmostEqual(ffmpeg_runner.probe_duration(j["output_path"]), 20, delta=0.2)
        self.assertTrue(ffmpeg_runner.has_audio_stream(j["output_path"]))
        # la canción la subió el usuario a una carpeta que no es uploads: no se borra
        self.assertTrue(os.path.isfile(self.cancion))


class Validacion(unittest.TestCase):
    def test_defaults(self):
        p = BeatmapParams.from_form({})
        self.assertEqual((p.motor, p.preview, p.musica, p.letra.letra), ("auto", True, None, False))

    def test_link_de_musica(self):
        p = BeatmapParams.from_form({"musica_url": "https://youtu.be/x", "letra": "1", "idioma": "es"})
        self.assertEqual((p.musica.url, p.musica.format), ("https://youtu.be/x", "audio"))
        self.assertTrue(p.letra.letra)

    def test_errores(self):
        for form in ({"motor": "x"}, {"musica_url": "ftp://x"}, {"idioma": "xx"}, {"modelo": "gigante"}):
            with self.assertRaises(ValueError, msg=form):
                BeatmapParams.from_form(form)


class Api(unittest.TestCase):
    def test_process_status_download(self):
        from app import create_app
        tmp = S.tmpdir("beatmap-api")
        try:
            cancion = S.cancion(os.path.join(tmp, "c.wav"), bpm=128, dur=12, alto_desde=6)
            with mock.patch.object(config, "BEATMAPS_FOLDER", os.path.join(tmp, "cache")):
                client = create_app().test_client()
                with open(cancion, "rb") as fh:
                    r = client.post("/api/beatmap/process", data={"audio": (fh, "c.wav"), "motor": "librosa"},
                                    content_type="multipart/form-data")
                self.assertEqual(r.status_code, 202, r.json)
                job = r.json["job_id"]
                for _ in range(120):
                    st = client.get(f"/api/beatmap/status/{job}").json
                    if st["status"] in ("done", "error"):
                        break
                    time.sleep(0.25)
                self.assertEqual(st["status"], "done", st["error"])
                self.assertAlmostEqual(st["resumen"]["bpm"], 128, delta=2)
                self.assertEqual(client.get(f"/api/beatmap/download/{job}").status_code, 200)
                self.assertEqual(client.get(f"/api/beatmap/json/{job}").status_code, 200)
            self.assertEqual(client.post("/api/beatmap/process", data={}).status_code, 400)
        finally:
            S.borrar(tmp)
