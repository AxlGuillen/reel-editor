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

    def test_blocks_fit_top_to_bottom(self):
        # table, gameplay, scoreboard and camera, in order and inside 1920
        blocks = [(L.BARRA_Y, L.BARRA_H), (L.ARRIBA_H, L.JUEGO_H), (L.MARCADOR_Y, L.MARCADOR_H),
                  (L.CAMARA_Y_OUT, L.CAMARA_H_OUT)]
        for (y, h), (y2, _) in zip(blocks, blocks[1:]):
            self.assertLessEqual(y + h, y2)
        self.assertLessEqual(L.CAMARA_Y_OUT + L.CAMARA_H_OUT, L.H)
        for y, h in blocks:
            self.assertEqual((y % 2, h % 2), (0, 0))
        # Axl's mockup: table 1020 wide, gameplay ~1080x950, scoreboard ~1040, camera 78 %
        self.assertEqual((L.BARRA_W, L.JUEGO_H, L.MARCADOR_W, L.CAMARA_ANCHO), (1020, 950, 1040, 842))
        self.assertEqual(L.BARRA[:3], (465, 820, 991))
        self.assertEqual(L.MARCADOR_VISTA[0] + L.MARCADOR_VISTA[2], 1400)

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
            self.assertEqual(g.juego_h, L.JUEGO_H)
            # el gameplay conserva la proporción del recorte (sin estirar)
            self.assertAlmostEqual(cw / ch, L.W / g.juego_h, delta=0.01)

    def test_less_zoom_than_the_full_height_is_the_full_height(self):
        lleno = L.geometria(L.ZOOM_LLENO)
        self.assertEqual(lleno.recorte[1], L.JUEGO[3])                # the whole clean height
        self.assertEqual(L.geometria(1.0), lleno)
        self.assertEqual(L.geometria(1.3), lleno)
        self.assertLess(L.geometria(1.7).recorte[1], L.JUEGO[3])      # more zoom crops less


def _video(path, graph, dur=4.0, inputs=("color=c=0x505050:s=1920x1080:r=10",)):
    cmd = [config.FFMPEG_PATH, "-y", "-v", "error"]
    for src in inputs:
        cmd += ["-f", "lavfi", "-t", f"{dur}", "-i", src]
    cmd += ["-filter_complex", graph, "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", path]
    subprocess.run(cmd, check=True)
    return path


class SplitCamera(unittest.TestCase):
    """Riot's split camera: a thin, almost black, slightly diagonal line across
    the gameplay near the center (measured in IG-GEN 23/10, 10:18)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="reelforge-split-")
        line = ("color=c=black:s=7x1100:r=10",)
        base = "[0:v]noise=alls=25:allf=t[g]"
        # the divider from 1.0 to 3.0 s, rotated like Riot's (−0.19: top right → bottom left)
        cls.split = _video(os.path.join(cls.tmp, "split.mp4"),
                           f"{base};[1:v]format=rgba,rotate=a=0.19:c=none:ow=rotw(0.19):oh=roth(0.19)[l];"
                           "[g][l]overlay=x=880:y=-20:enable='between(t,1,3)'[v]",
                           inputs=("color=c=0x505050:s=1920x1080:r=10",) + line)
        # what is NOT the divider: a wide dark band, a short dark line and a dark horizontal one
        cls.other = _video(os.path.join(cls.tmp, "other.mp4"),
                           "[0:v]noise=alls=25:allf=t,drawbox=x=950:y=108:w=60:h=712:color=black:t=fill:enable='lt(t,1.5)',"
                           "drawbox=x=1000:y=300:w=6:h=250:color=black:t=fill:enable='between(t,1.5,3)',"
                           "drawbox=x=860:y=500:w=280:h=6:color=black:t=fill:enable='gte(t,3)'[v]")

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_detected_while_it_lasts(self):
        ranges = L.split_ranges(self.split)
        self.assertEqual(len(ranges), 1, ranges)
        a, b = ranges[0]
        self.assertAlmostEqual(a, 1.0, delta=0.2)
        self.assertAlmostEqual(b, 3.0, delta=0.2)

    def test_other_dark_shapes_are_not(self):
        self.assertEqual(L.split_ranges(self.other), [])

    def test_the_view_switches_to_the_whole_zone(self):
        out = os.path.join(self.tmp, "out.mp4")
        ffmpeg_runner.capture(L.build_command(self.split, out, 0.0, 4.0, L.CAMARA_Y_DEFECTO, color="off",
                                              split=[(1.0, 3.0)]))
        self.assertEqual(ffmpeg_runner.probe_resolution(out), (1080, 1920))

        def band(t):
            # the top of the gameplay box: the zoomed view fills it with the game;
            # the split view has the blurred, dimmed copy there (the full zone is centered)
            raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-ss", f"{t}", "-i", out,
                                         "-frames:v", "1", "-vf", f"crop=1080:150:0:{L.ARRIBA_H},format=gray",
                                         "-f", "rawvideo", "-"])
            import numpy as np
            return np.frombuffer(raw, np.uint8).astype(float)
        zoomed, split = band(0.5), band(2.0)
        self.assertGreater(zoomed.std(), split.std() * 2)               # noise vs blurred
        self.assertLess(split.mean(), zoomed.mean())                    # dimmed

    def test_the_edit_shot_gets_its_input_offset(self):
        from modules.editing import render
        geo = L.edit_geometry(L.CAMARA_Y_DEFECTO, 1.3, True, [(10.0, 12.0)])
        self.assertIn(f"(t+{render.SOURCE_T0})", geo)
        self.assertNotIn(render.SOURCE_T0, L.edit_geometry(L.CAMARA_Y_DEFECTO, 1.3, True))


class CameraBox(unittest.TestCase):
    def test_box_under_moving_gameplay(self):
        # Twitch 23/10: a light border at (1464, 537), interior 450x254 from (1468, 541)
        with tempfile.TemporaryDirectory() as tmp:
            src = _video(os.path.join(tmp, "cam.mp4"),
                         "[0:v]noise=alls=40:allf=t,"
                         "drawbox=x=1464:y=537:w=456:h=262:color=0xA0A0A0:t=fill,"
                         "drawbox=x=1468:y=541:w=452:h=254:color=0x203060:t=fill,"
                         "drawbox=x=1500:y=700:w=380:h=30:color=0x8080C0:t=fill[v]", dur=21.0)
            x, y, w, h = L.detect_camera_box(src)
            self.assertAlmostEqual(x, 1468, delta=5)
            self.assertAlmostEqual(y, 541, delta=4)
            self.assertAlmostEqual(w / h, L.CAMARA_W / L.CAMARA_H, delta=0.02)
            self.assertLessEqual(x + w, 1920)


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


class AmbientShots(unittest.TestCase):
    """Without the scoreboard and without the game: the stage, the players,
    the crowd. A replay (the game, no scoreboard) is not one."""

    def test_detected_and_framed(self):
        with tempfile.TemporaryDirectory() as tmp:
            mx, my, mw, mh = L.MARCADOR
            game = "noise=alls=60:allf=t"                                   # green texture: the Rift
            board = f"drawbox=x={mx}:y={my}:w={mw}:h={mh}:color=0xC8C8C8:t=fill"
            src = _video(os.path.join(tmp, "amb.mp4"),
                         # 0–4 live, 4–6 replay (game, no board), 6–9 a camera (dark blue, no board), 9–12 live
                         f"[0:v]{game},{board}:enable='lt(t,4)+gte(t,9)',"
                         f"drawbox=x={mx}:y={my}:w={mw}:h={mh}:color=0x101010:t=fill:enable='between(t,4,9)',"
                         "drawbox=x=0:y=0:w=1920:h=1080:color=0x202848:t=fill:enable='between(t,6,9)'[v]",
                         dur=12.0, inputs=("color=c=0x2E7A3A:s=1920x1080:r=10",))
            ranges = L.ambient_ranges(src)
            self.assertEqual(len(ranges), 1, ranges)
            a, b = ranges[0]
            self.assertAlmostEqual(a, 6.0, delta=0.6)
            self.assertAlmostEqual(b, 9.0, delta=0.6)
            # its geometry: the whole view in the middle of the gameplay box, the camera below
            out = os.path.join(tmp, "out.mp4")
            subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error", "-ss", "6.5", "-t", "1", "-i", src,
                            "-filter_complex", L.ambient_geometry(L.CAMARA_Y_DEFECTO) + "[v]", "-map", "[v]",
                            "-c:v", "libx264", "-preset", "ultrafast", out], check=True)
            self.assertEqual(ffmpeg_runner.probe_resolution(out), (1080, 1920))
