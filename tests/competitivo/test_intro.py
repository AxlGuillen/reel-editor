"""Tests de la intro con voz y la portada del módulo competitivo (sin internet)."""
import os
import subprocess
import tempfile
import unittest
from datetime import date
from unittest import mock

import config
from core import ffmpeg_runner, job_manager
from modules.competitivo import intro as I
from modules.competitivo import kills, processor
from modules.competitivo.schema import CAMPOS_INTRO, CompetitivoParams, FinishParams

IDS = "115660588583171787"
VOD = "https://www.youtube.com/watch?v=8ci8rGBi4lU"
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kc_c9_g1.json")
INTRO = {
    "intro_modo": "aparte", "intro_audio_url": "https://platform.r2.fish.audio/voz.mp3",
    "mapa": "2", "fase": "PLAY-IN · DÍA 2", "equipo_azul": "Karmine Corp", "equipo_rojo": "Cloud9",
    "logo_azul_url": "https://static.lolesports.com/teams/kc.png", "logo_rojo_url": "",
    "serie_marcador": "1-0", "marca_4xl": "1",
}


def _base(**extra):
    return CompetitivoParams.from_form({"game_ids": IDS, "vod_url": VOD, **extra})


class ValidacionIntro(unittest.TestCase):
    def test_defaults(self):
        p = _base()
        self.assertEqual((p.intro_modo, p.intro_audio_url, p.mapa, p.fase), ("encima", "", 1, ""))
        self.assertEqual((p.equipo_azul, p.logo_azul_url, p.serie_marcador, p.marca_4xl), ("", "", "", False))

    def test_valores(self):
        p = _base(**INTRO)
        self.assertEqual(p.intro_modo, "aparte")
        self.assertEqual(p.mapa, 2)
        self.assertEqual(p.fase, "PLAY-IN · DÍA 2")
        self.assertTrue(p.marca_4xl)
        self.assertEqual(_base(fase="  Play-In   ·  Día 2 ").fase, "Play-In · Día 2")   # espacios colapsados

    def test_errores(self):
        casos = [
            {"intro_modo": "medio"},
            {"intro_audio_url": "ftp://x/voz.mp3"}, {"intro_audio_url": "voz.mp3"},
            {"mapa": "0"}, {"mapa": "6"}, {"mapa": "uno"},
            {"fase": "x" * 41},
            {"equipo_azul": "x" * 31}, {"equipo_rojo": "x" * 31},
            {"logo_azul_url": "static.lolesports.com/kc.png"}, {"logo_rojo_url": "javascript:alert(1)"},
            {"serie_marcador": "1-0 a favor de KC"},
            {"fase": "PLAY-IN\x00"},
        ]
        for form in casos:
            with self.assertRaises(ValueError, msg=form):
                _base(**form)
        # límites exactos: pasan
        _base(fase="x" * 40, equipo_azul="x" * 30, serie_marcador="x" * 12, mapa="5")

    def test_finish_hereda_la_intro(self):
        base = _base(**INTRO)
        fp = FinishParams.from_form({}, base)
        for campo in CAMPOS_INTRO:
            self.assertEqual(getattr(fp, campo), getattr(base, campo), campo)
        # lo que llega en /finish pisa lo de la fase 1
        fp = FinishParams.from_form({"intro_modo": "off", "mapa": "3", "fase": ""}, base)
        self.assertEqual((fp.intro_modo, fp.mapa, fp.fase), ("off", 3, ""))
        self.assertEqual(fp.equipo_azul, "Karmine Corp")

    def test_desde_copia_todo(self):
        base = _base(**INTRO, zoom="1.5", watermark="jhin-faker.png")
        fp = FinishParams.desde(base)
        self.assertEqual((fp.anclas, fp.clips, fp.zoom, fp.watermark), ({}, None, 1.5, "jhin-faker.png"))
        for campo in CAMPOS_INTRO:
            self.assertEqual(getattr(fp, campo), getattr(base, campo), campo)


class ModoAutoPasaLaIntro(unittest.TestCase):
    """process() en modo auto llama a finish() con los campos de intro (mock de
    la red: VOD, feed y ancla)."""

    def test_auto_llama_finish_con_la_intro(self):
        params = _base(modo="auto", **INTRO)
        partida = kills.partida_de_fixture(FIXTURE)
        ancla = {"ancla_s": 100.0, "ancla_propuesta_s": 100.0, "desfase_s": 0.0, "confianza": "alta",
                 "coincidencias": 3, "esperadas": 3, "referencia": "x", "referencia_vod_s": 0,
                 "frame": None, "primera_kill_utc": partida.frames[1].ts.isoformat()}
        job = job_manager.create_job(VOD, output_path="")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(processor, "TRABAJO", tmp), \
                mock.patch.object(processor.fuente, "info_vod",
                                  return_value={"inicio_utc": partida.t0, "duracion": 1, "titulo": "t"}), \
                mock.patch.object(processor.kills, "fetch_partida", return_value=partida), \
                mock.patch.object(processor, "_ancla", return_value=ancla), \
                mock.patch.object(processor, "finish") as fin:
            processor.process(job, params)
        self.assertEqual(job_manager.get_job(job)["error"], None)
        fin.assert_called_once()
        fp = fin.call_args.args[1]
        self.assertIsInstance(fp, FinishParams)
        for campo in CAMPOS_INTRO:
            self.assertEqual(getattr(fp, campo), getattr(params, campo), campo)
        processor._SESIONES.pop(job, None)


class Portada(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _png(self, nombre, fuente):
        path = os.path.join(self.dir, nombre)
        subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error", "-f", "lavfi", "-i", fuente,
                        "-frames:v", "1", path], check=True)
        return path

    def test_sin_fondo_ni_logos_usa_degradado_y_codigos(self):
        enc = I.Encuentro(mapa=1, codigo_azul="KC", codigo_rojo="C9")
        self.assertEqual((enc.azul, enc.rojo), ("KC", "C9"))      # sin nombres: los códigos
        info = I.render_portada(enc, os.path.join(self.dir, "a"), fondo=os.path.join(self.dir, "no-existe.png"))
        self.assertEqual(info["fondo"], "degradado")
        self.assertEqual(info["logos"], {"azul": "codigo", "rojo": "codigo"})
        self.assertEqual(ffmpeg_runner.probe_resolution(info["png"]), (1080, 1920))
        self.assertEqual(ffmpeg_runner.probe_resolution(info["jpg"]), (1080, 1920))
        restos = [f for f in os.listdir(os.path.join(self.dir, "a")) if f.endswith(".txt")]
        self.assertEqual(restos, [])                                # textfiles borrados

    def test_con_fondo_logo_y_marca(self):
        fondo = self._png("fondo.png", "testsrc2=s=1600x900:d=1")
        logo = self._png("logo.png", "color=c=red:s=500x300:d=1")
        marca = os.path.join(config.WATERMARKS_FOLDER, "jhin-faker.png")
        enc = I.Encuentro(mapa=2, codigo_azul="KC", codigo_rojo="C9", azul="Karmine Corp", rojo="Cloud9",
                          fase="Play-In · Día 2", logo_azul=logo, serie_marcador="1-0",
                          fecha=date(2026, 10, 16), marca=marca if os.path.isfile(marca) else None)
        info = I.render_portada(enc, os.path.join(self.dir, "b"), fondo=fondo)
        self.assertEqual(info["fondo"], "imagen")
        self.assertEqual(info["logos"], {"azul": "logo", "rojo": "codigo"})
        self.assertEqual(ffmpeg_runner.probe_resolution(info["jpg"]), (1080, 1920))

    def test_logo_que_falla_no_rompe(self):
        with mock.patch.object(I, "descargar", side_effect=RuntimeError("sin red")), \
                mock.patch.object(I, "LOGOS_CACHE", self.dir):
            self.assertIsNone(I.logo("https://static.lolesports.com/teams/no.png"))
        self.assertIsNone(I.logo(""))

    def test_nombres_largos(self):
        lineas, tam = I.ajustar_texto("Karmine Corp", I.TITULO, 74, 450, 48)
        self.assertEqual((lineas, tam), (["Karmine Corp"], 74))
        lineas, tam = I.ajustar_texto("Team Liquid Honda Academy", I.TITULO, 74, 300, 48)
        self.assertEqual(len(lineas), 2)                            # partido en dos renglones
        info = I.fonts.font_info("Anton-Regular.ttf")
        for linea in lineas:
            self.assertLessEqual(I.fonts.text_width(info, linea, tam), 300)


if __name__ == "__main__":
    unittest.main()
