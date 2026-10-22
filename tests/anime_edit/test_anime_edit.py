"""Tests de anime_edit: planos, planificador (puro), validación y el flujo de
dos fases con un video y una canción sintéticos."""
import os
import time
import unittest
from unittest import mock

import numpy as np

import config
from core import ffmpeg_runner, job_manager
from modules.anime_edit import plan as PL
from modules.anime_edit import planos as P
from modules.anime_edit import processor
from modules.anime_edit.presets import PRESETS
from modules.anime_edit.schema import AnimeEditParams, FinishParams
from tests import sinteticos as S


class Planos(unittest.TestCase):
    def test_fundir_cortos(self):
        self.assertEqual(P.fundir_cortos([0, 1.0, 1.1, 3.0, 3.2]), [0, 1.0, 3.2])
        self.assertEqual(P.fundir_cortos([0, 0.1, 2.0]), [0, 2.0])

    def test_medidas_y_puntaje(self):
        # 6 muestras por segundo: plano 1 quieto, plano 2 con movimiento y
        # plano 3 casi todo en blanco (un impact frame largo).
        n = 18
        series = {"movimiento": np.array([0.0] * 6 + [30.0] * 6 + [60.0] * 6),
                  "brillo": np.array([100.0] * 12 + [250.0] * 6), "saturacion": np.full(n, 0.5)}
        quieto, movido, blanco = P.planos_de([0, 1.0, 2.0, 3.0], series)
        self.assertGreater(movido["puntaje"], quieto["puntaje"])
        self.assertLess(blanco["puntaje"], movido["puntaje"])
        self.assertTrue(1.0 <= movido["pico"] <= 2.0)

    def test_detecta_los_cortes_de_un_video(self):
        tmp = S.tmpdir("planos")
        try:
            v = S.video_planos(os.path.join(tmp, "v.mp4"), [1.5, 2.0, 1.0, 2.5])
            planos = P.detectar(v)
            self.assertEqual(len(planos), 4)
            for p, esperado in zip(planos, [0, 1.5, 3.5, 4.5]):
                self.assertAlmostEqual(p["desde"], esperado, delta=0.1)
            P.miniaturas(v, planos, tmp)
            hojas = P.hojas(tmp, len(planos))
            self.assertEqual(len(hojas), 1)
            self.assertTrue(os.path.isfile(hojas[0]))
            # sin PySceneDetect, el scdet de FFmpeg encuentra los mismos cortes
            with mock.patch.object(P, "_cortes_scenedetect", return_value=None):
                self.assertEqual(len(P.detectar(v)), 4)
        finally:
            S.borrar(tmp)


def _bm(bpm=120.0, dur=60.0, drop=24.0):
    per = 60 / bpm
    beats = [round(k * per, 3) for k in range(int(dur / per))]
    return {"beats": beats, "downbeats": beats[::4], "bpm": bpm, "duracion": dur, "onsets": [],
            "golpes": [b for b in beats[::2] if b >= drop], "energia_por_beat": [0.5] * len(beats),
            "secciones": [{"tipo": "verso", "inicio": 0.0, "fin": drop, "energia": 0.2},
                          {"tipo": "coro", "inicio": drop, "fin": dur, "energia": 1.0}],
            "drop": drop, "subidas": [[drop - 4.0, drop]],
            "palabras": [{"texto": "hey!", "inicio": drop + 0.5, "fin": drop + 0.8},
                         {"texto": "go", "inicio": drop + 1.5, "fin": drop + 1.8}]}


PLANOS = [{"id": i, "desde": 2.0 * (i - 1), "hasta": 2.0 * i, "pico": 2.0 * i - 1, "puntaje": 10 * i}
          for i in range(1, 9)]


class Plan(unittest.TestCase):
    def test_ventana_pone_el_drop_hacia_el_35(self):
        desde, dur = PL.ventana(_bm(), 20, None)
        self.assertIn(desde, _bm()["downbeats"])
        self.assertAlmostEqual(24 - desde, 8, delta=1.0)        # 35 % de 20 s ≈ 7 s, en compás
        self.assertIn(round(desde + dur, 3), _bm()["downbeats"])

    def test_cortes_por_seccion_y_drop(self):
        bmw = {**_bm(), "duracion": 40.0}
        c = PL.cortes(bmw, PRESETS["impacto"])
        self.assertIn(24.0, c)
        coro = [b - a for a, b in zip(c, c[1:]) if a >= 24]
        verso = [b - a for a, b in zip(c, c[1:]) if b <= 20]
        subida = [b - a for a, b in zip(c, c[1:]) if 20 <= a < 24]
        self.assertAlmostEqual(np.median(coro), 0.5, places=3)    # 1 beat
        self.assertAlmostEqual(np.median(verso), 1.0, places=3)   # 2 beats
        self.assertAlmostEqual(np.median(subida), 0.25, places=3) # medio beat

    def test_cortes_en_las_palabras(self):
        c = PL.cortes({**_bm(), "duracion": 40.0}, PRESETS["letra"])
        self.assertIn(24.5, c)
        self.assertIn(25.5, c)

    def test_variante_completa(self):
        for preset in PRESETS:
            edl, info = PL.planear("v.mp4", PLANOS, _bm(), preset, PL.Opciones(duracion_s=20, heroe=3))
            self.assertAlmostEqual(edl.dur, info["dur"], places=6)
            self.assertEqual(info["heroe"], 3)
            # el plano héroe arranca justo en el drop de la salida
            t = 0.0
            for s in edl.segmentos:
                if abs(t - info["drop"]) < 1e-6:
                    self.assertEqual(s.etiqueta, "plano 3")
                    self.assertTrue(any(p.congelado for p in s.piezas))
                t += s.dur
            # cada pieza usa fuente de SU plano
            for s in edl.segmentos:
                pid = int(s.etiqueta.split()[1])
                pl = PLANOS[pid - 1]
                for p in s.piezas:
                    self.assertGreaterEqual(p.desde, pl["desde"] - 1e-6)
                    self.assertLessEqual(p.hasta, pl["hasta"] + 1e-6)
        edl, _ = PL.planear("v.mp4", PLANOS, _bm(), "letra", PL.Opciones(duracion_s=20))
        self.assertEqual([t.texto for t in edl.textos], ["hey!", "go"])
        edl, _ = PL.planear("v.mp4", PLANOS, _bm(), "cine", PL.Opciones(duracion_s=20))
        self.assertEqual(edl.textos, [])

    def test_formatos(self):
        edl, _ = PL.planear("v.mp4", PLANOS, _bm(), "impacto", PL.Opciones(formato="horizontal"))
        self.assertEqual((edl.ancho, edl.alto), (1920, 1080))
        with self.assertRaises(ValueError):
            PL.planear("v.mp4", PLANOS, _bm(), "impacto", PL.Opciones(formato="cuadrado"))
        with self.assertRaises(ValueError):
            PL.planear("v.mp4", PLANOS, _bm(), "nada", PL.Opciones())


class Validacion(unittest.TestCase):
    def test_fase1(self):
        p = AnimeEditParams.from_form({"musica_del_video": "1"})
        self.assertTrue(p.musica_del_video and p.letra.letra)
        self.assertEqual(AnimeEditParams.from_form({"video_url": "https://youtu.be/x"}).video.format, "video")
        for form in ({"video_url": "x"}, {"motor": "x"}, {"musica_url": "x"}):
            with self.assertRaises(ValueError):
                AnimeEditParams.from_form(form)

    def test_fase2(self):
        ids = {1, 2, 3}
        fp = FinishParams.from_form({"seleccion": "[3, 1]", "heroe": "3", "variantes": "impacto,cine"}, ids)
        self.assertEqual((fp.seleccion, fp.heroe, fp.variantes), ([3, 1], 3, ["impacto", "cine"]))
        self.assertEqual(FinishParams.from_form({}, ids).variantes, ["impacto"])
        for form in ({"seleccion": "[9]"}, {"heroe": "9"}, {"variantes": "nada"}, {"formato": "x"},
                     {"fps": "25"}, {"texto": "x"}, {"look": "x"}, {"lut": "x.cube"}, {"duracion_s": "500"}):
            with self.assertRaises(ValueError, msg=form):
                FinishParams.from_form(form, ids)


class DosFases(unittest.TestCase):
    def test_process_y_finish_por_la_api(self):
        from app import create_app
        tmp = S.tmpdir("anime")
        try:
            video = S.video_planos(os.path.join(tmp, "v.mp4"), [1.5, 2.0, 1.0, 2.5, 1.5, 2.0])
            musica = S.cancion(os.path.join(tmp, "m.wav"), bpm=120, dur=30, alto_desde=8)
            with mock.patch.object(config, "BEATMAPS_FOLDER", os.path.join(tmp, "bm")), \
                    mock.patch.object(config, "EDITS_FOLDER", os.path.join(tmp, "edits")):
                c = create_app().test_client()
                with open(video, "rb") as fv, open(musica, "rb") as fm:
                    r = c.post("/api/anime-edit/process", content_type="multipart/form-data",
                               data={"video": (fv, "v.mp4"), "musica": (fm, "m.wav"), "letra": "0"})
                self.assertEqual(r.status_code, 202, r.json)
                job = r.json["job_id"]
                st = self._esperar(c, job)
                self.assertEqual(st["stage"], "esperando_seleccion")
                self.assertEqual(len(st["planos"]), 6)
                self.assertEqual(c.get(f"/api/anime-edit/hoja/{job}/1").status_code, 200)
                self.assertEqual(c.get(f"/api/anime-edit/thumb/{job}/2").status_code, 200)
                r = c.post("/api/anime-edit/finish", json={"job_id": job, "seleccion": [2, 4, 5], "heroe": 4,
                                                           "variantes": ["impacto", "velocidad"],
                                                           "duracion_s": 12, "fps": 24, "titulo": "prueba"})
                self.assertEqual(r.status_code, 202, r.json)
                st = self._esperar(c, job)
                self.assertTrue(st["finished"])
                vs = st["resultado"]["variantes"]
                self.assertEqual([v["variante"] for v in vs], ["impacto", "velocidad"])
                for v in vs:
                    self.assertEqual(ffmpeg_runner.probe_resolution(v["video"]), (1080, 1920))
                    self.assertAlmostEqual(ffmpeg_runner.probe_duration(v["video"]), v["duracion"], delta=0.1)
                self.assertEqual(c.get(f"/api/anime-edit/download/{job}/2").status_code, 200)
                self.assertEqual(c.post("/api/anime-edit/finish", json={"job_id": job, "heroe": 99}).status_code, 400)
        finally:
            processor._SESIONES.clear()
            S.borrar(tmp)

    @staticmethod
    def _esperar(c, job):
        for _ in range(600):
            st = c.get(f"/api/anime-edit/status/{job}").json
            if st["status"] in ("done", "error") and st["stage"] != "Preparando el edit…":
                if st["status"] == "error":
                    raise AssertionError(st["error"])
                return st
            time.sleep(0.25)
        raise AssertionError("tardó demasiado")
