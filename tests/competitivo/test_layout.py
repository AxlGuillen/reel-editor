"""Tests de modules/competitivo/layout.py (sin internet; usan el FFmpeg del sistema)."""
import os
import subprocess
import tempfile
import unittest

import config
from core import ffmpeg_runner
from modules.competitivo import layout as L


class Geometria(unittest.TestCase):
    ZOOMS = (0.5, 1.0, 1.2, 1.3, 1.4, 1.6, 1.7, 2.0, 2.2)

    def test_siempre_suma_1920_en_alturas_pares(self):
        for zoom in self.ZOOMS:
            g = L.geometria(zoom)
            alturas = (L.ARRIBA_H + g.extra_arriba, g.juego_h, L.MARCADOR_H, L.CAMARA_H_OUT + g.extra_abajo)
            self.assertEqual(sum(alturas), L.H, zoom)
            for alto in alturas:
                self.assertEqual(alto % 2, 0, (zoom, alturas))

    def test_recorte_dentro_del_juego_limpio(self):
        x0, y0, jw, jh = L.JUEGO
        for zoom in self.ZOOMS:
            g = L.geometria(zoom)
            cw, ch, cx, cy = g.recorte
            self.assertGreaterEqual(g.zoom, L.ZOOM_MIN)
            self.assertGreaterEqual(cx, x0)
            self.assertGreaterEqual(cy, y0)
            self.assertLessEqual(cx + cw, x0 + jw)
            self.assertLessEqual(cy + ch, y0 + jh)
            # el gameplay conserva la proporción del recorte (sin estirar)
            self.assertAlmostEqual(cw / ch, L.W / g.juego_h, delta=0.01)

    def test_con_menos_zoom_lo_que_sobra_va_arriba_y_abajo(self):
        lleno, menos = L.geometria(1.7), L.geometria(1.3)
        self.assertEqual((lleno.extra_arriba, lleno.extra_abajo), (0, 0))
        self.assertGreater(menos.recorte[0], lleno.recorte[0])      # más campo a lo ancho
        self.assertEqual(menos.recorte[1], L.JUEGO[3])               # de alto, la zona limpia
        self.assertLess(menos.juego_h, lleno.juego_h)
        self.assertGreater(menos.extra_arriba, 0)
        self.assertLessEqual(abs(menos.extra_arriba - menos.extra_abajo), 2)

class Render(unittest.TestCase):
    def test_render_1080x1920_con_marca(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.mp4")
            subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error",
                            "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=60:d=2",
                            "-f", "lavfi", "-i", "sine=f=440:d=2",
                            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", src],
                           check=True)
            marca = os.path.join(tmp, "marca.png")
            # PNG con margen transparente: el recorte tiene que quedarse con el contenido.
            subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error", "-f", "lavfi",
                            "-i", "color=c=red@1:s=60x100,format=rgba,pad=100:140:20:10:color=black@0",
                            "-frames:v", "1", marca], check=True)
            self.assertEqual(L.recorte_marca(marca), (60, 100, 20, 10))
            out = os.path.join(tmp, "out.mp4")
            cmd = L.build_command(src, out, 0.5, 1.0, L.CAMARA_Y_DEFECTO, marca_path=marca)
            ffmpeg_runner.capture(cmd)
            self.assertEqual(ffmpeg_runner.probe_resolution(out), (1080, 1920))
            self.assertAlmostEqual(ffmpeg_runner.probe_duration(out), 1.0, delta=0.1)
            self.assertTrue(ffmpeg_runner.has_audio_stream(out))


if __name__ == "__main__":
    unittest.main()
