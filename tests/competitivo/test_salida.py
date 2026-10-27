"""Tests de la salida organizada por día y partido (modules/competitivo/salida.py)."""
import json
import os
import tempfile
import unittest
from unittest import mock

from flask import Flask

import config
from core import job_manager
from modules.competitivo import kills, processor, salida
from modules.competitivo.routes import bp
from modules.competitivo.schema import CAMPOS_SALIDA, CompetitivoParams, FinishParams

IDS = "115660588583171787"
VOD = "https://www.youtube.com/watch?v=8ci8rGBi4lU"
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kc_c9_g1.json")
PARTIDAS = [{"inicio_utc": "2026-10-17T18:00:00+00:00", "azul": "FUR", "rojo": "MVK"}]


def _base(**extra):
    return CompetitivoParams.from_form({"game_ids": IDS, "vod_url": VOD, **extra})


class _Carpeta(unittest.TestCase):
    """COMPETITIVO_FOLDER en una carpeta temporal."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.join(self.tmp.name, "Competitivo")
        self.patch = mock.patch.object(config, "COMPETITIVO_FOLDER", self.base)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _escribir(self, path, texto="x"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(texto)

    def _leer(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def _corrida(self, version, mapa=2, titulo="", dia="Play-In Día 3 (17-10)", partido="MVK vs FUR"):
        """Lo que hace finish(): preparar → renderizar en dest.carpeta → publicar."""
        d = salida.preparar(PARTIDAS, mapa, f"job{version}", dia, partido, titulo)
        self._escribir(d.resumen, f"resumen {version}")
        self._escribir(os.path.join(d.carpeta, "clip_01.mp4"), f"clip {version}")
        self._escribir(os.path.join(d.carpeta, "portada.jpg"), f"portada {version}")
        info = {"creado": f"2026-10-17T20:00:0{version}", "mapa": mapa, "duracion_total": 100 + version,
                "clips": [{"archivo": "clip_01.mp4", "titulo": "C9 · pelea 3-0", "duracion": 20.0}]}
        self._escribir(os.path.join(d.carpeta, "info.json"), json.dumps(info))
        salida.publicar(d)
        return d


class Nombres(unittest.TestCase):
    def test_limpiar(self):
        self.assertEqual(salida.limpiar("Play-In Día 3 (17:10)"), "Play-In Día 3 (17 -10)")
        self.assertEqual(salida.limpiar('MVK vs FUR: "la final"?'), "MVK vs FUR - la final")
        self.assertEqual(salida.limpiar("a<b>c/d\\e|f*g"), "abcdefg")
        self.assertEqual(salida.limpiar("Remontada...  "), "Remontada")
        self.assertEqual(salida.limpiar("con"), "con_")                       # nombre reservado
        self.assertEqual(salida.limpiar("???"), "")
        self.assertEqual(salida.nombre_video(2, "Remontada"), "Mapa 2 - Remontada.mp4")
        self.assertEqual(salida.nombre_video(2), "Mapa 2.mp4")

    def test_schema(self):
        p = _base(carpeta_dia="Play-In Día 3 (17:10)", partido="MVK vs FUR", titulo="Remontada: épica")
        self.assertEqual((p.carpeta_dia, p.partido, p.titulo),
                         ("Play-In Día 3 (17 -10)", "MVK vs FUR", "Remontada - épica"))
        self.assertEqual((_base().carpeta_dia, _base().partido, _base().titulo), ("", "", ""))
        for form in ({"carpeta_dia": "Día 3"},                      # sin partido
                     {"partido": "MVK vs FUR"},                     # sin día
                     {"carpeta_dia": "Día 3", "partido": "???"},    # nada válido
                     {"titulo": "x" * 121},
                     {"carpeta_dia": "x" * 81, "partido": "MVK vs FUR"}):
            with self.assertRaises(ValueError, msg=form):
                _base(**form)
        _base(titulo="x" * 120)
        base = _base(carpeta_dia="Día 3", partido="MVK vs FUR", titulo="A")
        fp = FinishParams.from_form({}, base)
        for campo in CAMPOS_SALIDA:
            self.assertEqual(getattr(fp, campo), getattr(base, campo), campo)          # se hereda
        self.assertEqual(FinishParams.from_form({"titulo": "B: final"}, base).titulo, "B - final")
        fp = FinishParams.desde(base)                                                   # modo auto
        self.assertEqual((fp.carpeta_dia, fp.partido, fp.titulo), ("Día 3", "MVK vs FUR", "A"))


class Estructura(_Carpeta):
    def test_organizada(self):
        d = self._corrida(1, titulo="Remontada")
        partido = os.path.join(self.base, "Play-In Día 3 (17-10)", "MVK vs FUR")
        self.assertEqual(self._leer(os.path.join(partido, "Mapa 2 - Remontada.mp4")), "resumen 1")
        extras = os.path.join(partido, "_extras", "Mapa 2")
        self.assertEqual(sorted(os.listdir(extras)), ["clip_01.mp4", "info.json", "portada.jpg"])
        self.assertEqual(d.carpeta, extras)
        self.assertEqual(d.ruta_video, "Play-In Día 3 (17-10)/MVK vs FUR/Mapa 2 - Remontada.mp4")
        self.assertEqual(os.listdir(os.path.join(partido, "_extras")), ["Mapa 2"])   # sin temporales

    def test_sin_titulo(self):
        self._corrida(1, mapa=3)
        self.assertTrue(os.path.isfile(os.path.join(self.base, "Play-In Día 3 (17-10)", "MVK vs FUR", "Mapa 3.mp4")))

    def test_sin_dia_ni_partido_la_de_siempre(self):
        d = salida.preparar(PARTIDAS, 1, "job", "", "", "")
        self.assertFalse(d.organizada)
        self.assertTrue(os.path.basename(d.carpeta).endswith("FUR vs MVK · Mapa 1"))
        self.assertEqual(d.resumen, os.path.join(d.carpeta, "resumen.mp4"))
        self.assertEqual(d.video_final, d.resumen)

    def test_choque_de_nombres(self):
        partido = os.path.join(self.base, "Play-In Día 3 (17-10)", "MVK vs FUR")
        descartados = os.path.join(partido, "_extras", "_descartados")
        self._corrida(1, titulo="Remontada")
        self._corrida(2, titulo="Remontada")
        self._corrida(3, titulo="Remontada")
        # el nuevo queda con el nombre limpio; los anteriores, en _descartados con (2)
        self.assertEqual(self._leer(os.path.join(partido, "Mapa 2 - Remontada.mp4")), "resumen 3")
        self.assertEqual(self._leer(os.path.join(partido, "_extras", "Mapa 2", "clip_01.mp4")), "clip 3")
        self.assertEqual(sorted(os.listdir(descartados)),
                         ["Mapa 2", "Mapa 2 (2)", "Mapa 2 - Remontada (2).mp4", "Mapa 2 - Remontada.mp4"])
        self.assertEqual(self._leer(os.path.join(descartados, "Mapa 2 - Remontada.mp4")), "resumen 1")
        self.assertEqual(self._leer(os.path.join(descartados, "Mapa 2 - Remontada (2).mp4")), "resumen 2")
        self.assertEqual(self._leer(os.path.join(descartados, "Mapa 2", "clip_01.mp4")), "clip 1")
        self.assertEqual(self._leer(os.path.join(descartados, "Mapa 2 (2)", "clip_01.mp4")), "clip 2")

    def test_otro_titulo_del_mismo_mapa_tambien_se_descarta(self):
        partido = os.path.join(self.base, "Play-In Día 3 (17-10)", "MVK vs FUR")
        self._corrida(1, titulo="Remontada")
        self._corrida(2, mapa=3)
        self._corrida(3, titulo="Otra")
        videos = sorted(f for f in os.listdir(partido) if f.endswith(".mp4"))
        self.assertEqual(videos, ["Mapa 2 - Otra.mp4", "Mapa 3.mp4"])               # el Mapa 3 no se toca
        self.assertIn("Mapa 2 - Remontada.mp4", os.listdir(os.path.join(partido, "_extras", "_descartados")))

    def test_un_finish_nuevo_conserva_la_transcripcion(self):
        """La transcripción de /transcript vive en _extras/Mapa N: un render nuevo la
        deja en su lugar (y la versión anterior de todo lo demás, en _descartados)."""
        self._corrida(1)
        extras = os.path.join(self.base, "Play-In Día 3 (17-10)", "MVK vs FUR", "_extras")
        for f in salida.TRANSCRIPCION:
            self._escribir(os.path.join(extras, "Mapa 2", f), f"{f} v1")
        self._corrida(2)
        for f in salida.TRANSCRIPCION:
            self.assertEqual(self._leer(os.path.join(extras, "Mapa 2", f)), f"{f} v1", f)
        self.assertEqual(self._leer(os.path.join(extras, "Mapa 2", "portada.jpg")), "portada 2")
        self.assertEqual(self._leer(os.path.join(extras, "_descartados", "Mapa 2", "portada.jpg")), "portada 1")
        self.assertEqual([x for x in os.listdir(extras) if x.startswith(".")], [])    # sin restos
        # Una transcripción nueva (de otro render que la trae) no se pisa con la vieja.
        d = salida.preparar(PARTIDAS, 2, "job3", "Play-In Día 3 (17-10)", "MVK vs FUR", "")
        self._escribir(d.resumen)
        self._escribir(os.path.join(d.carpeta, "transcripcion.json"), "nueva")
        self._escribir(os.path.join(d.carpeta, "info.json"), "{}")
        salida.publicar(d)
        self.assertEqual(self._leer(os.path.join(extras, "Mapa 2", "transcripcion.json")), "nueva")

    def test_si_falla_no_toca_lo_anterior(self):
        self._corrida(1, titulo="Remontada")
        d = salida.preparar(PARTIDAS, 2, "job9", "Play-In Día 3 (17-10)", "MVK vs FUR", "Remontada")
        self._escribir(os.path.join(d.carpeta, "clip_01.mp4"), "a medias")
        salida.descartar(d)
        partido = os.path.join(self.base, "Play-In Día 3 (17-10)", "MVK vs FUR")
        self.assertEqual(self._leer(os.path.join(partido, "Mapa 2 - Remontada.mp4")), "resumen 1")
        self.assertEqual(os.listdir(os.path.join(partido, "_extras")), ["Mapa 2"])

    def test_finish_que_falla_no_toca_lo_anterior(self):
        # finish() de verdad, con la red cortada al bajar los tramos.
        self._corrida(1, mapa=1, titulo="Remontada", dia="Día 3", partido="KC vs C9")
        params = _base(modo="revisar", carpeta_dia="Día 3", partido="KC vs C9", titulo="Remontada")
        partida = kills.partida_de_fixture(FIXTURE)
        ancla = {"ancla_s": 100.0, "ancla_propuesta_s": 100.0, "desfase_s": 0.0, "confianza": "alta",
                 "coincidencias": 3, "esperadas": 3, "referencia": "x", "referencia_vod_s": 0,
                 "frame": None, "primera_kill_utc": partida.frames[1].ts.isoformat()}
        job = job_manager.create_job(VOD, output_path="")
        with mock.patch.object(processor, "TRABAJO", os.path.join(self.tmp.name, "trabajo")), \
                mock.patch.object(processor.fuente, "info_vod",
                                  return_value={"inicio_utc": partida.t0, "duracion": 1, "titulo": "t"}), \
                mock.patch.object(processor.kills, "fetch_partida", return_value=partida), \
                mock.patch.object(processor, "_ancla", return_value=ancla), \
                mock.patch.object(processor.fuente, "bajar_tramo", side_effect=RuntimeError("sin red")):
            processor.process(job, params)
            processor.finish(job, FinishParams.desde(params))
        processor._SESIONES.pop(job, None)
        self.assertEqual(job_manager.get_job(job)["status"], "error")
        self.assertIn("sin red", job_manager.get_job(job)["error"])          # falló bajando, no antes
        partido = os.path.join(self.base, "Día 3", "KC vs C9")
        self.assertEqual(self._leer(os.path.join(partido, "Mapa 1 - Remontada.mp4")), "resumen 1")
        self.assertEqual(os.listdir(os.path.join(partido, "_extras")), ["Mapa 1"])


class Listado(_Carpeta):
    def setUp(self):
        super().setUp()
        self._corrida(1, titulo="Remontada")                                          # Día 3 / MVK vs FUR / Mapa 2
        self._corrida(2, titulo="Remontada")                                          # → la 1 a descartados
        self._corrida(3, mapa=1, dia="Play-In Día 2 (16-10)", partido="KC vs C9")     # sin título
        # una carpeta de serie de las de siempre
        serie = os.path.join(self.base, "2026-10-15 KC vs C9 · Mapa 1")
        self._escribir(os.path.join(serie, "resumen.mp4"))
        self._escribir(os.path.join(serie, "info.json"), json.dumps(
            {"creado": "2026-10-15T20:00:00", "serie": "KC vs C9", "clips": [], "intro_modo_usado": "encima"}))
        self._escribir(os.path.join(self.base, "_algo", "x.mp4"))                     # se ignora

    def test_recorre_dia_y_partido(self):
        res = salida.listar()
        self.assertEqual([(r["dia"], r["partido"], r["mapa"], r["titulo"]) for r in res], [
            ("Play-In Día 2 (16-10)", "KC vs C9", 1, None),
            ("Play-In Día 3 (17-10)", "MVK vs FUR", 2, "Remontada"),
            (None, None, None, None),
        ])
        org = res[1]
        self.assertEqual(org["ruta"], "Play-In Día 3 (17-10)/MVK vs FUR/Mapa 2 - Remontada.mp4")
        self.assertEqual(org["portada"], "Play-In Día 3 (17-10)/MVK vs FUR/_extras/Mapa 2/portada.jpg")
        self.assertEqual(org["clips"][0]["ruta"], "Play-In Día 3 (17-10)/MVK vs FUR/_extras/Mapa 2/clip_01.mp4")
        self.assertEqual(org["duracion_total"], 102)                                  # la versión publicada
        self.assertEqual((res[2]["estructura"], res[2]["ruta"]), ("serie", "2026-10-15 KC vs C9 · Mapa 1/resumen.mp4"))
        # _extras y _descartados no aparecen
        self.assertFalse(any("_extras" in r["ruta"] or "_descartados" in r["ruta"] for r in res))

    def test_rutas_del_endpoint(self):
        app = Flask(__name__)
        app.register_blueprint(bp)
        c = app.test_client()
        r = c.get("/api/competitivo/resumenes")
        self.assertEqual(len(r.get_json()["resumenes"]), 3)
        ruta = salida.listar()[1]["ruta"]
        r = c.get(f"/api/competitivo/resumenes/{ruta}")
        self.assertEqual((r.status_code, r.mimetype, r.data), (200, "video/mp4", b"resumen 2"))
        r.close()
        r = c.get("/api/competitivo/resumenes/2026-10-15 KC vs C9 · Mapa 1/info.json")   # la forma vieja sigue
        self.assertEqual(r.status_code, 200)
        r.close()
        secreto = os.path.join(self.tmp.name, "secreto.txt")
        self._escribir(secreto, "no")
        for malo in ("../secreto.txt", "..%2Fsecreto.txt", "Play-In Día 3 (17-10)/../../secreto.txt",
                     "%2E%2E/secreto.txt", secreto.replace("\\", "/"), "Play-In Día 3 (17-10)/MVK vs FUR",
                     "no-existe.mp4"):
            r = c.get(f"/api/competitivo/resumenes/{malo}")
            self.assertEqual(r.status_code, 404, malo)
            self.assertEqual(r.get_json()["error"], "Archivo no encontrado.")

    def test_resolver(self):
        self.assertIsNone(salida.resolver("../secreto.txt"))
        self.assertIsNone(salida.resolver("C:/Windows/win.ini"))
        self.assertIsNone(salida.resolver(""))
        self.assertIsNone(salida.resolver("Play-In Día 3 (17-10)"))                  # carpeta, no archivo
        self.assertTrue(salida.resolver("Play-In Día 3 (17-10)/MVK vs FUR/Mapa 2 - Remontada.mp4"))


if __name__ == "__main__":
    unittest.main()
