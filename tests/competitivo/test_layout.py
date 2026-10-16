"""Tests de modules/competitivo/layout.py (sin internet; usan el FFmpeg del sistema)."""
import os
import subprocess
import tempfile
import unittest

import config
from core import ffmpeg_runner
from modules.competitivo import layout as L


class Geometria(unittest.TestCase):
    def test_secciones_suman_1920(self):
        self.assertEqual(L.ARRIBA_H + L.JUEGO_H + L.MARCADOR_H + L.CAMARA_H_OUT, L.H)
        for alto in (L.BARRA_H, L.JUEGO_H, L.MARCADOR_H, L.CAMARA_H_OUT):
            self.assertEqual(alto % 2, 0)

    def test_recorte_dentro_del_juego_limpio(self):
        x0, y0, jw, jh = L.JUEGO
        for zoom in (0.5, 1.0, 1.2, 1.4, 1.6, 1.7, 2.0, 2.2):
            cw, ch, cx, cy, z, alto = L.recorte_juego(zoom)
            self.assertGreaterEqual(z, L.ZOOM_MIN)
            self.assertGreaterEqual(cx, x0)
            self.assertGreaterEqual(cy, y0)
            self.assertLessEqual(cx + cw, x0 + jw)
            self.assertLessEqual(cy + ch, y0 + jh)
            self.assertLessEqual(alto, L.JUEGO_H)
            # el juego nítido conserva la proporción del recorte
            self.assertAlmostEqual(cw / ch, L.W / alto, delta=0.01)

    def test_con_menos_zoom_el_juego_no_llena_el_hueco(self):
        # Más campo de visión a lo ancho; de alto manda la zona limpia (712 px).
        cw, ch, _, _, _, alto = L.recorte_juego(1.2)
        self.assertEqual(ch, L.JUEGO[3])
        self.assertLess(alto, L.JUEGO_H)
        self.assertGreater(cw, L.recorte_juego(1.7)[0])
        # Con zoom >= ZOOM_LLENO llena el hueco entero.
        self.assertAlmostEqual(L.recorte_juego(1.7)[5], L.JUEGO_H, delta=2)

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
