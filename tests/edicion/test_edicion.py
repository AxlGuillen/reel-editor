"""Tests del motor de edición (EDL, ritmo, efectos, letreros y render)."""
import os
import unittest

import numpy as np

from core import ffmpeg_runner
from modules.edicion import efectos, ritmo, texto
from modules.edicion import render as R
from modules.edicion.edl import Edl, Evento, Pieza, Segmento, Texto
from tests import sinteticos as S


class Rampa(unittest.TestCase):
    def test_usa_toda_la_fuente_en_la_duracion_pedida(self):
        for perfil in ritmo.PERFILES:
            piezas = ritmo.rampa(10.0, 13.0, 2.0, perfil)
            self.assertAlmostEqual(sum(p.dur for p in piezas), 2.0, places=6, msg=perfil)
            self.assertAlmostEqual(piezas[0].desde, 10.0)
            self.assertAlmostEqual(piezas[-1].hasta, 13.0)
            for a, b in zip(piezas, piezas[1:]):
                self.assertAlmostEqual(a.hasta, b.desde)

    def test_velocidad_relativa_del_perfil(self):
        lento, rapido = ritmo.rampa(0, 3, 2, "velocidad")
        self.assertAlmostEqual(rapido.velocidad / lento.velocidad, 1.6 / 0.35, places=4)

    def test_sin_fuente_es_un_congelado(self):
        (p,) = ritmo.rampa(5, 5, 0.4)
        self.assertTrue(p.congelado)
        self.assertEqual(p.dur, 0.4)


class Impacto(unittest.TestCase):
    def test_congelado_lento_y_resto(self):
        piezas = ritmo.impacto(2.0, 4.0, 1.0, congelado=0.05, lento=0.25, vel_lenta=0.4)
        self.assertTrue(piezas[0].congelado)
        self.assertAlmostEqual(piezas[1].velocidad, 0.4, places=6)
        self.assertAlmostEqual(sum(p.dur for p in piezas), 1.0, places=6)
        self.assertAlmostEqual(piezas[-1].hasta, 4.0)

    def test_tramo_corto_no_se_pasa(self):
        piezas = ritmo.impacto(0, 0.1, 0.1)
        self.assertAlmostEqual(sum(p.dur for p in piezas), 0.1, places=6)
        self.assertLessEqual(piezas[0].dur, 0.03 + 1e-9)


class Cuantizar(unittest.TestCase):
    def test_los_bordes_caen_en_el_cuadro_ideal_sin_arrastre(self):
        # 40 piezas de 0.51 s a 60 fps: el borde k ideal es k·0.51 s.
        edl = Edl([Segmento("x", [Pieza(0, 0.51, 0.51) for _ in range(4)]) for _ in range(10)], fps=60)
        ritmo.cuantizar(edl)
        t = 0.0
        k = 0
        for s in edl.segmentos:
            for p in s.piezas:
                t += p.dur
                k += 1
                self.assertAlmostEqual(t * 60, round(k * 0.51 * 60), places=6)
                self.assertAlmostEqual(p.dur * 60, round(p.dur * 60), places=6)

    def test_quita_las_piezas_de_cero_cuadros(self):
        edl = Edl([Segmento("x", [Pieza(0, 1, 1), Pieza(1, 1.001, 0.001)]), Segmento("y", [Pieza(0, 0, 0.002)])],
                  fps=30)
        ritmo.cuantizar(edl)
        self.assertEqual(len(edl.segmentos), 1)
        self.assertEqual(len(edl.segmentos[0].piezas), 1)

    def test_acotar_respeta_la_duracion(self):
        p = ritmo.acotar([Pieza(0, 10, 1), Pieza(0, 0.01, 1)])
        self.assertEqual([x.dur for x in p], [1, 1])
        self.assertAlmostEqual(p[0].velocidad, ritmo.VEL_MAX)
        self.assertAlmostEqual(p[1].velocidad, ritmo.VEL_MIN)


class Validacion(unittest.TestCase):
    def test_errores(self):
        casos = [
            Edl([]),
            Edl([Segmento("x", [])]),
            Edl([Segmento("x", [Pieza(0, 1, 0)])]),
            Edl([Segmento("x", [Pieza(2, 1, 1)])]),
            Edl([Segmento("x", [Pieza(0, 1, 1)])], eventos=[Evento(0, "explosion")]),
            Edl([Segmento("x", [Pieza(0, 1, 1)])], textos=[Texto(1, 1, "a")]),
            Edl([Segmento("x", [Pieza(0, 1, 1)])], textos=[Texto(0, 1, "a", "comic")]),
        ]
        for edl in casos:
            with self.assertRaises(ValueError):
                edl.validar()


class Efectos(unittest.TestCase):
    EV = [Evento(1.0, "flash"), Evento(2.0, "zoom", 0.5), Evento(3.0, "shake"), Evento(3.0, "rgb")]

    def test_sin_eventos_no_hay_filtro(self):
        self.assertEqual(efectos.flash([]), "")
        self.assertEqual(efectos.camara([], 1080, 1920, 60), "")
        self.assertEqual(efectos.rgb([]), "")

    def test_filtros(self):
        self.assertIn("eval=frame", efectos.flash(self.EV))
        cam = efectos.camara(self.EV, 1080, 1920, 60)
        self.assertTrue(cam.startswith("zoompan="))
        self.assertIn("s=1080x1920:fps=60", cam)
        self.assertIn("enable='between(t,3,3.18)'", efectos.rgb(self.EV))

    def test_looks(self):
        self.assertEqual(efectos.color("neutro"), "")
        self.assertIn("colorbalance", efectos.color("dorado"))
        self.assertTrue(efectos.color("vivo", "x.cube").startswith("lut3d=file='assets/luts/x.cube'"))
        with self.assertRaises(ValueError):
            efectos.color("sepia")


class Letreros(unittest.TestCase):
    def test_ass(self):
        ass = texto.build_ass([Texto(0, 1, "survive", "palabra"), Texto(1, 2, "Caliste", "kill", sub="Xayah"),
                               Texto(2, 3, "quadra kill", "multikill")], 1080, 1920)
        self.assertIn("PlayResX: 1080", ass)
        self.assertIn("YCbCr Matrix: None", ass)
        self.assertEqual(ass.count("SURVIVE"), texto.BANDAS + 1)       # franjas + contorno
        self.assertIn("\\clip(", ass)
        self.assertIn("XAYAH", ass)
        self.assertEqual(ass.count("QUADRA KILL"), texto.BANDAS + 1)

    def test_texto_largo_se_achica(self):
        ass = texto.build_ass([Texto(0, 1, "supercalifragilistico", "palabra")], 1080, 1920)
        self.assertIn("\\fscx", ass.split("\n")[-2])

    def test_sin_textos_no_escribe(self):
        self.assertIsNone(texto.escribir([], 1080, 1920))


class Render(unittest.TestCase):
    """Un edit corto de punta a punta: tres planos (rampa, impacto y vertical
    con fondo difuminado), música, flash, zoom, sacudida, RGB y letreros."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("edicion")
        cls.video = S.video_planos(os.path.join(cls.tmp, "v.mp4"), [2, 2, 2])
        cls.musica = S.cancion(os.path.join(cls.tmp, "m.wav"), bpm=120, dur=12, alto_desde=2)
        segs = [Segmento(cls.video, ritmo.rampa(0.2, 1.8, 1.0, "velocidad"), "a"),
                Segmento(cls.video, ritmo.impacto(2.2, 3.6, 1.5), "b"),
                Segmento(cls.video, ritmo.rampa(4.1, 5.9, 1.3), "c", vf=R.vf_vertical_blur(540, 960))]
        cls.edl = Edl(segs, ancho=540, alto=960, fps=30, musica=cls.musica, musica_desde=1.0,
                      eventos=[Evento(0.5, "flash"), Evento(1.0, "zoom"), Evento(2.5, "shake"), Evento(2.5, "rgb")],
                      textos=[Texto(0.1, 0.9, "survive"), Texto(1.0, 2.0, "Caliste", "kill", sub="Xayah")],
                      look="dorado", glow=0.4, viñeta=True, volumen_original=0.2)
        cls.out = os.path.join(cls.tmp, "out.mp4")
        cls.info = R.render(cls.edl, cls.out, None, os.path.join(cls.tmp, "trabajo"))

    @classmethod
    def tearDownClass(cls):
        S.borrar(cls.tmp)

    def test_duracion_exacta_y_formato(self):
        self.assertEqual(self.info["dur"], 3.8)
        self.assertAlmostEqual(ffmpeg_runner.probe_duration(self.out), 3.8, delta=0.05)
        self.assertEqual(ffmpeg_runner.probe_resolution(self.out), (540, 960))
        self.assertEqual(ffmpeg_runner.probe_fps(self.out), 30)
        self.assertTrue(ffmpeg_runner.has_audio_stream(self.out))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "trabajo")))

    def test_el_flash_cae_en_su_cuadro(self):
        fr = S.frames_gris(self.out, 30)
        brillo = fr.mean(axis=(1, 2))
        # el flash va en 0.5 s, en medio del primer plano (cuadro 15 a 30 fps)
        self.assertEqual(int(np.argmax(brillo[5:25])) + 5, 15)
        self.assertGreater(brillo[15], brillo[12] + 60)

    def test_los_cortes_caen_donde_planeo(self):
        fr = S.frames_gris(self.out, 30)
        d = np.abs(np.diff(fr.mean(axis=(1, 2))))
        # plano 1 → 2 en el cuadro 30 (con el flash encima) y 2 → 3 en el 75
        self.assertGreater(d[74], 10)


class Formatos(unittest.TestCase):
    def test_vertical_blur_es_un_grafo_con_ramas(self):
        vf = R.vf_vertical_blur(1080, 1920)
        self.assertIn("split[vb_a][vb_b]", vf)
        self.assertIn("overlay=(W-w)/2:(H-h)/2", vf)
        self.assertEqual(R.vf_cubrir(1080, 1920).count("crop=1080:1920"), 1)
