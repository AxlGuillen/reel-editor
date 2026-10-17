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
        self.assertEqual(p.intro_volumen_juego, 17)
        self.assertEqual(p.placa_max_s, 5.0)

    def test_placa_max(self):
        self.assertEqual(_base(placa_max_s="1").placa_max_s, 1.0)
        self.assertEqual(_base(placa_max_s="7.5").placa_max_s, 7.5)
        for malo in ("0.9", "15.5", "cinco"):
            with self.assertRaises(ValueError, msg=malo):
                _base(placa_max_s=malo)
        base = _base(placa_max_s="8")
        self.assertEqual(FinishParams.from_form({}, base).placa_max_s, 8.0)          # se hereda
        self.assertEqual(FinishParams.from_form({"placa_max_s": "3"}, base).placa_max_s, 3.0)
        self.assertEqual(FinishParams.desde(base).placa_max_s, 8.0)                 # modo auto

    def test_volumen_del_juego(self):
        self.assertEqual(_base(intro_volumen_juego="0").intro_volumen_juego, 0)
        self.assertEqual(_base(intro_volumen_juego="100").intro_volumen_juego, 100)
        for malo in ("-1", "101", "mucho"):
            with self.assertRaises(ValueError, msg=malo):
                _base(intro_volumen_juego=malo)
        base = _base(intro_volumen_juego="30")
        self.assertEqual(FinishParams.from_form({}, base).intro_volumen_juego, 30)          # se hereda
        self.assertEqual(FinishParams.from_form({"intro_volumen_juego": "10"}, base).intro_volumen_juego, 10)
        self.assertEqual(FinishParams.desde(base).intro_volumen_juego, 30)

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
                mock.patch.object(processor.intro, "preparar_voz",
                                  return_value=I.Voz("voz.mp3", 4.0, 4.4)) as voz, \
                mock.patch.object(processor, "finish") as fin:
            processor.process(job, params)
        self.assertEqual(job_manager.get_job(job)["error"], None)
        fin.assert_called_once()
        fp = fin.call_args.args[1]
        self.assertIsInstance(fp, FinishParams)
        for campo in CAMPOS_INTRO:
            self.assertEqual(getattr(fp, campo), getattr(params, campo), campo)
        voz.assert_called_once()                     # la voz se valida en la fase 1
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


class Robustez(unittest.TestCase):
    """Descargas que fallan, logos que no son imágenes, textos raros: nada de eso
    puede tumbar el job (y no tiene que quedar basura en el cache)."""

    @classmethod
    def setUpClass(cls):
        import http.server
        import threading

        png = subprocess.run([config.FFMPEG_PATH, "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
                              "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                             capture_output=True, check=True).stdout
        cls.pedidos = {}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                cls.pedidos[self.path] = cls.pedidos.get(self.path, 0) + 1
                if self.path == "/ok.png":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(png)))
                    self.end_headers()
                    self.wfile.write(png)
                elif self.path == "/cortado.png":            # anuncia todo, manda la mitad
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(png)))
                    self.end_headers()
                    self.wfile.write(png[: len(png) // 2])
                elif self.path == "/pagina.png":             # un HTML con nombre de imagen
                    body = b"<html><body>404 bonito</body></html>"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def test_descarga_cortada_es_error(self):
        with self.assertRaisesRegex(RuntimeError, "No se pudo descargar"):
            I.descargar(self.base + "/cortado.png", os.path.join(self.tmp.name, "c.png"), reintentos=0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "c.png")))

    def test_404_no_se_reintenta(self):
        import time
        t = time.monotonic()
        with self.assertRaises(RuntimeError):
            I.descargar(self.base + "/no.png", os.path.join(self.tmp.name, "n.png"), reintentos=2)
        self.assertEqual(self.pedidos.get("/no.png"), 1)
        self.assertLess(time.monotonic() - t, 1.0)

    def test_logos_malos_no_envenenan_el_cache(self):
        cache = os.path.join(self.tmp.name, "logos")
        with mock.patch.object(I, "LOGOS_CACHE", cache):
            self.assertIsNotNone(I.logo(self.base + "/ok.png"))
            for malo in ("/pagina.png", "/cortado.png", "/no.png"):
                self.assertIsNone(I.logo(self.base + malo), malo)
        self.assertEqual(len([f for f in os.listdir(cache) if not f.endswith(".part")]), 1)   # solo el bueno
        self.assertEqual([f for f in os.listdir(cache) if f.endswith(".part")], [])

    def test_portada_determinista(self):
        enc = I.Encuentro(mapa=3, codigo_azul="KC", codigo_rojo="C9")
        a = I.render_portada(enc, os.path.join(self.tmp.name, "pa"), fondo=None)["png"]
        b = I.render_portada(enc, os.path.join(self.tmp.name, "pb"), fondo=None)["png"]
        with open(a, "rb") as fa, open(b, "rb") as fb:
            self.assertEqual(fa.read(), fb.read())     # el degradado ya no es al azar

    def test_texto_invisible_o_sin_glifos(self):
        with self.assertRaises(ValueError):
            _base(serie_marcador="1-0 ‮")             # da vuelta el texto
        with self.assertRaises(ValueError):
            _base(equipo_azul="KC​")
        self.assertFalse(I.tiene_glifos("한화생명", I.TITULO))  # Anton no trae coreano: va el código
        self.assertTrue(I.tiene_glifos("Karmine Corp · Día", I.TITULO))
        larga = "WORLDS 2026 · CUARTOS DE FINAL · DÍA 3 XX"
        self.assertLess(I.achicar(larga.upper(), I.TEXTO, 46, 900, 28), 46)

    def test_error_en_finish_no_deja_carpeta_a_medias(self):
        params = _base(modo="revisar")
        partida = kills.partida_de_fixture(FIXTURE)
        ancla = {"ancla_s": 100.0, "ancla_propuesta_s": 100.0, "desfase_s": 0.0, "confianza": "alta",
                 "coincidencias": 3, "esperadas": 3, "referencia": "x", "referencia_vod_s": 0,
                 "frame": None, "primera_kill_utc": partida.frames[1].ts.isoformat()}
        job = job_manager.create_job(VOD, output_path="")
        salidas = os.path.join(self.tmp.name, "salidas")
        with mock.patch.object(processor, "TRABAJO", os.path.join(self.tmp.name, "trabajo")), \
                mock.patch.object(processor.config, "COMPETITIVO_FOLDER", salidas), \
                mock.patch.object(processor.fuente, "info_vod",
                                  return_value={"inicio_utc": partida.t0, "duracion": 1, "titulo": "t"}), \
                mock.patch.object(processor.kills, "fetch_partida", return_value=partida), \
                mock.patch.object(processor, "_ancla", return_value=ancla), \
                mock.patch.object(processor.fuente, "bajar_tramo", side_effect=RuntimeError("sin red")):
            processor.process(job, params)
            processor.finish(job, FinishParams.desde(params))
        self.assertEqual(job_manager.get_job(job)["status"], "error")
        self.assertEqual(os.listdir(salidas) if os.path.isdir(salidas) else [], [])
        processor._SESIONES.pop(job, None)


def _ffprobe(path: str) -> dict:
    import json
    out = subprocess.run([config.FFPROBE_PATH, "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name,profile,level,width,height,pix_fmt,r_frame_rate,"
                          "sample_aspect_ratio,color_range,color_space,color_transfer,color_primaries,"
                          "time_base,sample_rate,channels,channel_layout,duration:format=duration",
                          "-of", "json", path], capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    s = {x["codec_type"]: x for x in d["streams"]}
    return {"video": s["video"], "audio": s["audio"], "duracion": float(d["format"]["duration"])}


def _sinteticos(carpeta: str, voz_s: float = 3.0, tramo_s: float = 12) -> tuple[str, str]:
    """Una voz (seno) y un tramo 1920x1080@60 etiquetado bt709/tv como el VOD de Ibai."""
    voz = os.path.join(carpeta, "voz.mp3")
    subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"sine=f=330:d={voz_s}", "-af", "volume=0.2", "-c:a", "libmp3lame", voz], check=True)
    tramo = os.path.join(carpeta, "tramo.mp4")
    subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error",
                    "-f", "lavfi", "-i", f"testsrc2=s=1920x1080:r=60:d={tramo_s}",
                    "-f", "lavfi", "-i", f"sine=f=440:d={tramo_s}", "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                    "-color_range", "tv", "-c:a", "aac", tramo], check=True)
    return voz, tramo


class IntroAparte(unittest.TestCase):
    """intro.mp4 tiene que poder ir primera en un concat -c copy con los clips."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        from modules.competitivo import layout
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        voz_src, tramo = _sinteticos(d)
        # La voz entra por URL, como en producción (file:// para no usar internet).
        cls.voz = I.preparar_voz(Path(voz_src).as_uri(), d)
        portada = I.render_portada(I.Encuentro(mapa=1, codigo_azul="KC", codigo_rojo="C9"),
                                   os.path.join(d, "portada"), fondo=None)
        cls.intro = os.path.join(d, "intro.mp4")
        ffmpeg_runner.capture(I.build_intro_aparte(portada["png"], cls.voz, cls.intro))
        cls.clip = os.path.join(d, "clip.mp4")
        ffmpeg_runner.capture(layout.build_command(tramo, cls.clip, 1.0, 6.0, layout.CAMARA_Y_DEFECTO))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_dura_la_voz_mas_la_cola(self):
        self.assertAlmostEqual(self.voz.duracion, 3.0, delta=0.1)
        self.assertAlmostEqual(self.voz.fin, self.voz.duracion, delta=0.01)     # sin silencio final
        self.assertAlmostEqual(self.voz.D, self.voz.fin + I.COLA_S, delta=1 / 60)
        self.assertAlmostEqual(self.voz.D * 60, round(self.voz.D * 60), places=6)   # frames enteros
        self.assertAlmostEqual(_ffprobe(self.intro)["duracion"], self.voz.D, delta=0.03)

    def test_silencio_final_recortado(self):
        from pathlib import Path
        d = self.tmp.name
        voz = os.path.join(d, "con_silencio.mp3")
        subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=2",
                        "-af", "apad=pad_dur=1.2", "-c:a", "libmp3lame", voz], check=True)
        v = I.preparar_voz(Path(voz).as_uri(), os.path.join(d, "sil"))
        self.assertAlmostEqual(v.duracion, 3.2, delta=0.1)
        self.assertAlmostEqual(v.fin, 2.1, delta=0.1)             # dónde termina de hablar (+0.1)
        self.assertAlmostEqual(v.D, 2.5, delta=1 / 60 + 0.1)

    def test_voz_normalizada(self):
        # La voz de prueba es un seno bajito; la intro tiene que quedar cerca de −11 LUFS.
        import re
        err = subprocess.run([config.FFMPEG_PATH, "-v", "info", "-i", self.intro, "-af", "ebur128",
                              "-f", "null", "-"], capture_output=True, text=True).stderr
        lufs = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", err)[-1])
        self.assertAlmostEqual(lufs, I.VOZ_LUFS, delta=1.5)
        self.assertIsNotNone(self.voz.ln)

    def test_mismos_parametros_que_un_clip(self):
        a, b = _ffprobe(self.intro), _ffprobe(self.clip)
        for k in ("codec_name", "profile", "level", "width", "height", "pix_fmt", "r_frame_rate",
                  "sample_aspect_ratio", "color_range", "color_space", "color_transfer",
                  "color_primaries", "time_base"):
            self.assertEqual(a["video"].get(k), b["video"].get(k), f"video {k}")
        for k in ("codec_name", "profile", "sample_rate", "channels", "channel_layout"):
            self.assertEqual(a["audio"].get(k), b["audio"].get(k), f"audio {k}")
        self.assertEqual(a["video"]["color_space"], "bt709")

    def test_concat_copy_sin_errores(self):
        d = self.tmp.name
        lista = os.path.join(d, "lista.txt")
        with open(lista, "w", encoding="utf-8") as fh:
            for p in (self.intro, self.clip):
                fh.write(f"file '{p.replace(os.sep, '/')}'\n")
        resumen = os.path.join(d, "resumen.mp4")
        from modules.competitivo import layout
        ffmpeg_runner.capture(layout.build_concat(lista, resumen))
        esperado = _ffprobe(self.intro)["duracion"] + _ffprobe(self.clip)["duracion"]
        info = _ffprobe(resumen)
        self.assertAlmostEqual(info["duracion"], esperado, delta=0.1)
        # El audio termina con el video (sin el relleno AAC que corría cada unión).
        self.assertAlmostEqual(float(info["audio"]["duration"]), float(info["video"]["duration"]), delta=0.04)
        # Se decodifica entero sin errores (cabeceras compatibles).
        err = subprocess.run([config.FFMPEG_PATH, "-v", "error", "-i", resumen, "-f", "null", "-"],
                             capture_output=True, text=True).stderr.strip()
        self.assertEqual(err, "")

    def test_voz_larga_o_rota(self):
        from pathlib import Path
        d = self.tmp.name
        # 12 s ya no es demasiado (el tope de 8 s se fue; 20 s queda como protección)
        media = os.path.join(d, "media.mp3")
        subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=d=12",
                        "-c:a", "libmp3lame", media], check=True)
        self.assertAlmostEqual(I.preparar_voz(Path(media).as_uri(), os.path.join(d, "x0")).D, 12.4, delta=0.1)
        larga = os.path.join(d, "larga.mp3")
        subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error", "-f", "lavfi", "-i", "sine=d=21",
                        "-c:a", "libmp3lame", larga], check=True)
        with self.assertRaisesRegex(RuntimeError, r"dura 21\.0 s; máximo 20"):
            I.preparar_voz(Path(larga).as_uri(), os.path.join(d, "x1"))
        rota = os.path.join(d, "rota.mp3")
        with open(rota, "w") as fh:
            fh.write("<html>not found</html>")
        with self.assertRaisesRegex(RuntimeError, "audio de la intro"):
            I.preparar_voz(Path(rota).as_uri(), os.path.join(d, "x2"))


class IntroEncima(unittest.TestCase):
    """La intro dentro del encode del clip 1: placa + voz + ducking del juego."""

    @classmethod
    def setUpClass(cls):
        import numpy as np  # noqa: F401  (lo usan los tests de frames)
        from modules.competitivo import layout
        cls.L = layout
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        cls.voz_src, cls.tramo = _sinteticos(d, voz_s=2.5)
        # Voz en silencio para medir el ducking del juego sin que la voz sume.
        cls.silencio = os.path.join(d, "silencio.mp3")
        subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error", "-f", "lavfi", "-i",
                        "anullsrc=r=48000:cl=stereo", "-t", "2.5", "-c:a", "libmp3lame", cls.silencio],
                       check=True)
        enc = I.Encuentro(mapa=1, codigo_azul="KC", codigo_rojo="C9")
        cls.placa = I.render_placa(enc, os.path.join(d, "placa"), fondo=None)
        cls.D = 2.9
        cls.limpio = os.path.join(d, "limpio.mp4")
        ffmpeg_runner.capture(layout.build_command(cls.tramo, cls.limpio, 1.0, 8.0, layout.CAMARA_Y_DEFECTO))
        cls.con = os.path.join(d, "con_intro.mp4")
        ffmpeg_runner.capture(layout.build_command(
            cls.tramo, cls.con, 1.0, 8.0, layout.CAMARA_Y_DEFECTO,
            intro=layout.IntroEncima(placa=cls.placa, voz=cls.silencio, D=cls.D)))
        # Voz de 4 s y kill a los 2.5 s: la placa se va a los 2.0 s y la voz sigue.
        cls.corta = os.path.join(d, "placa_corta.mp4")
        ffmpeg_runner.capture(layout.build_command(
            cls.tramo, cls.corta, 1.0, 8.0, layout.CAMARA_Y_DEFECTO,
            intro=layout.IntroEncima(placa=cls.placa, voz=cls.silencio, D=4.0, placa_fin=2.0)))
        cls.con_40 = os.path.join(d, "con_intro_40.mp4")
        ffmpeg_runner.capture(layout.build_command(
            cls.tramo, cls.con_40, 1.0, 8.0, layout.CAMARA_Y_DEFECTO,
            intro=layout.IntroEncima(placa=cls.placa, voz=cls.silencio, D=cls.D, volumen_juego=0.4)))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_comando(self):
        L = self.L
        cmd = L.build_command(self.tramo, "x.mp4", 1.0, 8.0, L.CAMARA_Y_DEFECTO,
                              intro=L.IntroEncima(placa=self.placa, voz=self.voz_src, D=3.4, sting="s.mp3"))
        self.assertIn(self.placa, cmd)
        self.assertEqual(cmd[cmd.index(self.placa) - 7:cmd.index(self.placa)],
                         ["-loop", "1", "-framerate", "60", "-t", "3.400", "-i"])
        self.assertIn(self.voz_src, cmd)
        self.assertIn("s.mp3", cmd)
        fc = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("enable='between(t,0,3.400)'", fc)
        self.assertIn("volume='0.17+0.83*min(max((t-3.400)/0.4,0),1)':eval=frame", fc)
        cmd = L.build_command(self.tramo, "x.mp4", 1.0, 8.0, L.CAMARA_Y_DEFECTO,
                              intro=L.IntroEncima(placa=self.placa, voz=self.voz_src, D=3.4, volumen_juego=0.3))
        self.assertIn("volume='0.3+0.7*min(", cmd[cmd.index("-filter_complex") + 1])
        # placa más corta que la voz: el loop y el enable duran la placa; el ducking, la voz
        cmd = L.build_command(self.tramo, "x.mp4", 1.0, 8.0, L.CAMARA_Y_DEFECTO,
                              intro=L.IntroEncima(placa=self.placa, voz=self.voz_src, D=6.0, placa_fin=3.2))
        corta = cmd[cmd.index("-filter_complex") + 1]
        self.assertEqual(cmd[cmd.index(self.placa) - 2], "3.200")
        self.assertIn("enable='between(t,0,3.200)'", corta)
        self.assertIn("fade=t=out:st=2.900:d=0.3", corta)
        self.assertIn("(t-6.000)/0.4", corta)
        self.assertIn("amix=inputs=3:normalize=0", fc)
        self.assertEqual(cmd[cmd.index("-map", cmd.index("-map") + 1) + 1], "[aout]")
        # sin intro: el comando de siempre
        cmd = L.build_command(self.tramo, "x.mp4", 1.0, 8.0, L.CAMARA_Y_DEFECTO)
        self.assertNotIn("-loop", cmd)
        self.assertIn("0:a?", cmd)

    def _frame(self, path, t):
        import numpy as np
        raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-ss", f"{t}", "-i", path,
                                     "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
        return np.frombuffer(raw, np.uint8).reshape(1920, 1080).astype(float)

    def test_placa_durante_D_y_despues_no(self):
        g = self.L.geometria(1.3)
        y0 = self.L.ARRIBA_H + g.extra_arriba
        zona = (slice(y0 + 200, y0 + g.juego_h - 200), slice(200, 880))   # centro del gameplay
        durante = abs(self._frame(self.con, 1.2)[zona] - self._frame(self.limpio, 1.2)[zona]).mean()
        despues = abs(self._frame(self.con, self.D + 0.8)[zona] - self._frame(self.limpio, self.D + 0.8)[zona]).mean()
        self.assertGreater(durante, 20)          # la placa tapa el gameplay
        self.assertLess(despues, 3)              # se fue: igual al clip limpio
        self.assertAlmostEqual(_ffprobe(self.con)["duracion"], _ffprobe(self.limpio)["duracion"], delta=0.05)

    def _rms(self, path, ss, dur):
        import re
        err = subprocess.run([config.FFMPEG_PATH, "-v", "info", "-ss", f"{ss}", "-t", f"{dur}", "-i", path,
                              "-af", "astats=measure_overall=RMS_level:measure_perchannel=none",
                              "-f", "null", "-"], capture_output=True, text=True).stderr
        return float(re.findall(r"RMS level dB: (-?[\d.]+)", err)[-1])

    def test_ducking_baja_el_juego_al_17(self):
        durante = self._rms(self.con, 0.5, self.D - 1.0)
        despues = self._rms(self.con, self.D + 1.0, 2.0)
        limpio = self._rms(self.limpio, self.D + 1.0, 2.0)
        self.assertAlmostEqual(durante - despues, -15.39, delta=1.0)   # 17 % = −15.4 dB
        self.assertAlmostEqual(despues, limpio, delta=0.5)             # después, el juego al 100 %
        # con intro_volumen_juego = 40: −8 dB
        self.assertAlmostEqual(self._rms(self.con_40, 0.5, self.D - 1.0) - despues, -7.96, delta=1.0)

    def test_placa_se_va_antes_de_la_kill_y_la_voz_sigue(self):
        g = self.L.geometria(1.3)
        y0 = self.L.ARRIBA_H + g.extra_arriba
        zona = (slice(y0 + 200, y0 + g.juego_h - 200), slice(200, 880))
        durante = abs(self._frame(self.corta, 1.2)[zona] - self._frame(self.limpio, 1.2)[zona]).mean()
        despues = abs(self._frame(self.corta, 2.6)[zona] - self._frame(self.limpio, 2.6)[zona]).mean()
        self.assertGreater(durante, 20)
        self.assertLess(despues, 3)                         # a los 2.6 s ya no está
        # pero el juego sigue bajado hasta D = 4 s
        bajado = self._rms(self.corta, 2.4, 1.3)
        lleno = self._rms(self.corta, 5.0, 2.0)
        self.assertAlmostEqual(bajado - lleno, -15.39, delta=1.0)

    def test_caida_a_aparte(self):
        # La voz ya puede ser más larga que el tiempo hasta la kill: sigue "encima".
        self.assertEqual(I.modo_para_clip1(4.0, 20.0, 8.0), ("encima", None))
        self.assertEqual(I.modo_para_clip1(9.0, 20.0, 4.2), ("encima", None))
        self.assertEqual(I.modo_para_clip1(4.0, 20.0, 3.0), ("encima", None))
        modo, motivo = I.modo_para_clip1(4.0, 20.0, 2.9)        # kill antes de los 3 s
        self.assertEqual(modo, "aparte")
        self.assertIn("antes de los 3 s", motivo)
        modo, motivo = I.modo_para_clip1(15.0, 12.0, 10.0)      # la voz no entra en el clip
        self.assertEqual(modo, "aparte")
        self.assertIn("dura 12.0 s", motivo)

    def test_fin_de_placa(self):
        self.assertEqual(I.fin_de_placa(4.0, 10.0), 4.0)        # la voz termina antes: la intro entera
        self.assertEqual(I.fin_de_placa(9.0, 4.2), 3.7)         # kill antes de D: kill − 0.5 s
        self.assertEqual(I.fin_de_placa(9.0, 3.0), 2.5)
        self.assertEqual(I.fin_de_placa(15.0, 10.0), 5.0)       # placa_max_s (default 5)
        self.assertEqual(I.fin_de_placa(15.0, 10.0, 8), 8.0)
        self.assertEqual(I.fin_de_placa(15.0, 10.0, 15), 9.5)


class PlacaMaxima(unittest.TestCase):
    """Voz larga (D = 15 s) y primera kill a los 10 s: la placa dura 5 s
    (placa_max_s) y el juego sigue bajado hasta los 15 s."""

    @classmethod
    def setUpClass(cls):
        from modules.competitivo import layout
        cls.L = layout
        cls.tmp = tempfile.TemporaryDirectory()
        d = cls.tmp.name
        _, cls.tramo = _sinteticos(d, tramo_s=20)
        silencio = os.path.join(d, "silencio.mp3")
        subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error", "-f", "lavfi", "-i",
                        "anullsrc=r=48000:cl=stereo", "-t", "14.6", "-c:a", "libmp3lame", silencio], check=True)
        placa = I.render_placa(I.Encuentro(mapa=1, codigo_azul="KC", codigo_rojo="C9"),
                               os.path.join(d, "placa"), fondo=None)
        cls.D = 15.0
        cls.placa_s = I.fin_de_placa(cls.D, 10.0)
        cls.intro = layout.IntroEncima(placa=placa, voz=silencio, D=cls.D, placa_fin=cls.placa_s)
        cls.limpio = os.path.join(d, "limpio.mp4")
        ffmpeg_runner.capture(layout.build_command(cls.tramo, cls.limpio, 0.0, 20.0, layout.CAMARA_Y_DEFECTO))
        cls.con = os.path.join(d, "con.mp4")
        ffmpeg_runner.capture(layout.build_command(cls.tramo, cls.con, 0.0, 20.0, layout.CAMARA_Y_DEFECTO,
                                                   intro=cls.intro))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_modo_y_comando(self):
        self.assertEqual(self.placa_s, 5.0)
        self.assertEqual(I.modo_para_clip1(self.D, 20.0, 10.0), ("encima", None))
        cmd = self.L.build_command(self.tramo, "x.mp4", 0.0, 20.0, self.L.CAMARA_Y_DEFECTO, intro=self.intro)
        fc = cmd[cmd.index("-filter_complex") + 1]
        self.assertEqual(cmd[cmd.index(self.intro.placa) - 2], "5.000")
        self.assertIn("enable='between(t,0,5.000)'", fc)
        self.assertIn("(t-15.000)/0.4", fc)

    def test_placa_5_s_y_ducking_hasta_15(self):
        frame, rms = IntroEncima._frame, IntroEncima._rms
        g = self.L.geometria(1.3)
        y0 = self.L.ARRIBA_H + g.extra_arriba
        zona = (slice(y0 + 200, y0 + g.juego_h - 200), slice(200, 880))
        self.assertGreater(abs(frame(self, self.con, 2.0)[zona] - frame(self, self.limpio, 2.0)[zona]).mean(), 20)
        self.assertLess(abs(frame(self, self.con, 5.6)[zona] - frame(self, self.limpio, 5.6)[zona]).mean(), 3)
        lleno = rms(self, self.con, 16.0, 3.0)
        self.assertAlmostEqual(rms(self, self.con, 6.0, 8.5) - lleno, -15.39, delta=1.0)   # bajado hasta D
        self.assertAlmostEqual(lleno, rms(self, self.limpio, 16.0, 3.0), delta=0.5)


if __name__ == "__main__":
    unittest.main()
