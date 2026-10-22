"""Tests del montaje: tiempos de las capas (puro), validación y el render con
capas con transparencia sobre un video sintético."""
import json
import os
import subprocess
import time
import unittest
from unittest import mock

import numpy as np

import config
from core import ffmpeg_runner, job_manager
from modules.montaje import capas as C
from modules.montaje import processor
from modules.montaje.schema import MontajeParams
from tests import sinteticos as S

_FRASE = "Primero el Rey Demonio estará enfrentando a los dieciséis campeones y después T1 cierra"
SEGMENTS = [{"id": 0, "start": 0.0, "end": 6.9, "text": _FRASE,
             "words": [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate(_FRASE.split())]}]


class Normalizar(unittest.TestCase):
    def test_tokens(self):
        self.assertEqual(C.tokens("¡Rey DEMONIO!"), ["rey", "demonio"])
        self.assertEqual(C.tokens("a los dieciséis años"), ["a", "los", "16", "anos"])
        self.assertEqual(C.tokens("treinta y dos"), ["32"])
        self.assertEqual(C.tokens("un campeón"), ["un", "campeon"])

    def test_buscar(self):
        t = C.palabras_de(SEGMENTS)
        self.assertEqual(C.buscar(t, "rey demonio"), 1.0)
        self.assertEqual(C.buscar(t, "16 campeones"), 4.0)            # Whisper escribió "dieciséis"
        self.assertEqual(C.buscar(t, "t1"), 6.0)
        self.assertIsNone(C.buscar(t, "rey demonio", n=2))


class Resolver(unittest.TestCase):
    CAJA = C.Caja()

    def test_reglas_de_lux(self):
        specs = [{"archivo": "a", "palabra": "rey demonio"}, {"archivo": "b", "desde": 30},
                 {"archivo": "c", "desde": 44}]
        a, b, c = C.resolver(specs, SEGMENTS, 60, self.CAJA)
        self.assertAlmostEqual(a.desde, 0.9)               # 0.1 s antes de la palabra
        self.assertAlmostEqual(a.hasta, 0.9 + C.TOPE_S)    # tope de 12 s (el hueco que deja es largo)
        self.assertEqual(b.hasta, 44.25)                   # pegada a la siguiente (+ su fundido)
        self.assertEqual(b.fundido_salida, 0.0)
        self.assertEqual(c.hasta, 56)                      # tope

    def test_hueco_corto_se_pega(self):
        a, b = C.resolver([{"archivo": "a", "desde": 0}, {"archivo": "b", "desde": 13}], None, 30, self.CAJA)
        self.assertEqual(a.hasta, 13 + 0.25)

    def test_errores(self):
        casos = [([{"archivo": "a", "palabra": "zapato"}], SEGMENTS),
                 ([{"archivo": "a", "palabra": "rey"}], None),
                 ([{"archivo": "a"}], None),
                 ([{"archivo": "a", "desde": 0, "hasta": 10}, {"archivo": "b", "desde": 5}], None),
                 ([{"archivo": "a", "desde": 3, "hasta": 3.1}], None)]
        for specs, segs in casos:
            with self.assertRaises(ValueError, msg=specs):
                C.resolver(specs, segs, 60, self.CAJA)


class Validacion(unittest.TestCase):
    def setUp(self):
        self.tmp = S.tmpdir("montaje-val")
        self.p = mock.patch.object(config, "CAPAS_FOLDER", self.tmp)
        self.p.start()
        os.makedirs(os.path.join(self.tmp, "laminas"))
        with open(os.path.join(self.tmp, "laminas", "manifest.json"), "w") as fh:
            json.dump([{"id": "p-kda", "archivo": "p-kda.mov"}], fh)

    def tearDown(self):
        self.p.stop()
        S.borrar(self.tmp)

    def test_manifest_y_carpeta(self):
        p = MontajeParams.from_form({"capas": json.dumps([{"id": "p-kda", "desde": 1}]),
                                     "carpeta_capas": os.path.join(self.tmp, "laminas"),
                                     "caja": json.dumps({"ancho": 600})})
        self.assertEqual(p.specs[0]["archivo"], "p-kda.mov")
        self.assertEqual(p.specs[0]["caja"]["ancho"], 600)
        with self.assertRaises(ValueError):            # el archivo no existe
            p.ruta("p-kda.mov")
        with self.assertRaises(ValueError):            # fuera de la carpeta
            p.ruta("../../etc/passwd")

    def test_errores(self):
        casos = [{}, {"capas": "x"}, {"capas": "[]"},
                 {"capas": json.dumps([{"desde": 1}])},
                 {"capas": json.dumps([{"id": "nada", "desde": 1}]), "carpeta_capas": os.path.join(self.tmp, "laminas")},
                 {"capas": json.dumps([{"archivo": "a.mov", "desde": 1}]), "carpeta_capas": "/"},
                 {"capas": json.dumps([{"archivo": "a.mov", "desde": 1, "caja": {"x": "arriba"}}])},
                 {"capas": json.dumps([{"archivo": "a.mov", "desde": 1, "caja": {"color": 1}}])},
                 {"capas": json.dumps([{"archivo": "a.mov", "desde": 1, "caja": {"modo": "franja", "y": 1800}}])}]
        for form in casos:
            with self.assertRaises(ValueError, msg=form):
                MontajeParams.from_form(form)


def _capa_alfa(path: str, dur: float = 1.0) -> str:
    """ProRes 4444 con alfa: un cuadrado amarillo opaco en el centro de un
    lienzo transparente de 400x300."""
    subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=black:s=400x300:r=30:d={dur},format=rgba,"
                    # drawbox no escribe el canal alfa: el cuadrado se arma con geq
                    "geq=r=255:g=255:b=0:a='if(between(X,100,299)*between(Y,75,224),255,0)'",
                    "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", path], check=True)
    return path


class Render(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("montaje")
        cls.base = S.video_planos(os.path.join(cls.tmp, "base.mp4"), [6.0], w=1080, h=1920, fps=30)
        cls.capa = _capa_alfa(os.path.join(cls.tmp, "flyer.mov"))
        cls.png = os.path.join(cls.tmp, "fijo.png")
        subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=blue:s=300x300:d=1", "-frames:v", "1", cls.png], check=True)

    @classmethod
    def tearDownClass(cls):
        S.borrar(cls.tmp)

    def _frame(self, video, t):
        raw = subprocess.run([config.FFMPEG_PATH, "-v", "error", "-ss", f"{t}", "-i", video, "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(1920, 1080, 3).astype(int)

    def test_capas_en_su_tiempo_con_transparencia(self):
        capas = [C.Capa(self.capa, 1.0, 3.0, C.Caja(ancho=400, abajo=100), dur_clip=1.0),
                 C.Capa(self.png, 4.0, 5.0, C.Caja(modo="franja", y=1240, alto=600))]
        out = os.path.join(self.tmp, "out.mp4")
        ffmpeg_runner.capture(processor.build_command(self.base, capas, out), cwd=config.BASE_DIR)
        self.assertAlmostEqual(ffmpeg_runner.probe_duration(out), 6.0, delta=0.1)
        self.assertTrue(ffmpeg_runner.has_audio_stream(out))
        # caja: 400x300 abajo al centro, 100 px sobre el borde → centro en (540, 1670)
        base = self._frame(out, 0.5)[1670, 540]
        durante = self._frame(out, 2.5)[1670, 540]             # congelado después de su 1 s
        borde = self._frame(out, 2.5)[1530, 360]               # zona transparente de la capa
        despues = self._frame(out, 3.6)[1670, 540]
        self.assertGreater(durante[0], 170)
        self.assertGreater(durante[1], 170)
        self.assertLess(durante[2], 90)                        # amarillo
        self.assertLess(np.abs(borde - base).max(), 25)        # se ve el video de atrás
        self.assertLess(np.abs(despues - base).max(), 25)
        # franja: el PNG azul al centro de la franja y su copia difuminada a lo ancho
        f = self._frame(out, 4.5)
        self.assertGreater(f[1540, 540][2], 150)
        self.assertLess(f[1540, 540][0], 80)
        self.assertGreater(np.abs(f[1540, 40] - base).max(), 25)    # la copia tapa los costados

    def test_job_por_la_api_con_palabras(self):
        from app import create_app
        c = create_app().test_client()
        with mock.patch.object(processor.subtitles, "transcribe", return_value=SEGMENTS):
            with open(self.base, "rb") as fv, open(self.capa, "rb") as fc:
                r = c.post("/api/montaje/process", content_type="multipart/form-data", data={
                    "video": (fv, "base.mp4"), "capas_archivos": [(fc, "flyer.mov")],
                    "capas": json.dumps([{"archivo": "flyer.mov", "palabra": "rey demonio", "hasta": 2.5}]),
                })
            self.assertEqual(r.status_code, 202, r.json)
            job = r.json["job_id"]
            for _ in range(200):
                st = c.get(f"/api/montaje/status/{job}").json
                if st["status"] in ("done", "error"):
                    break
                time.sleep(0.2)
        self.assertEqual(st["status"], "done", st["error"])
        self.assertEqual(st["capas"][0]["desde"], 0.9)
        self.assertEqual(c.get(f"/api/montaje/download/{job}").status_code, 200)
        self.assertEqual(c.post("/api/montaje/process", data={"capas": "[]"}).status_code, 400)


class ReelExpressFinish(unittest.TestCase):
    """Las capas van en la misma pasada que los subtítulos de Reel Express."""

    def test_finish_con_capas(self):
        import shutil
        from modules.reel_express import processor as rx
        from modules.subtitles.schema import SubtitlesParams
        tmp = S.tmpdir("rx-capas")
        try:
            base = S.video_planos(os.path.join(tmp, "base.mp4"), [4.0], w=1080, h=1920, fps=30)
            copia = os.path.join(tmp, "copia.mp4")
            shutil.copy(base, copia)
            _capa_alfa(os.path.join(tmp, "flyer.mov"))
            with mock.patch.object(config, "CAPAS_FOLDER", tmp):
                m = MontajeParams.from_form({"capas": json.dumps([{"archivo": "flyer.mov", "palabra": "demonio"}]),
                                             "carpeta_capas": tmp})
            job = job_manager.create_job(copia, "")
            out = os.path.join(config.OUTPUT_FOLDER, f"{job}.mp4")
            rx.finish(job, copia, SEGMENTS, SubtitlesParams(), out, montaje=m)
            j = job_manager.get_job(job)
            self.assertEqual(j["status"], "done", j["error"])
            self.assertEqual(j["capas"][0]["desde"], 1.4)
            self.assertTrue(os.path.isfile(j["output_path"]))
            self.assertFalse(os.path.exists(copia))          # el video base se borra, como siempre
            os.remove(j["output_path"])
        finally:
            S.borrar(tmp)
