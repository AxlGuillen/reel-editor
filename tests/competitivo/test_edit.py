"""Tests del modo edit de competitivo: autores de las kills, el planificador al
ritmo (puro) y el job completo con el VOD simulado (sin internet)."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import numpy as np

import config
from core import ffmpeg_runner, job_manager
from modules.competitivo import edit as E
from modules.competitivo import engine, processor
from modules.competitivo import kills as K
from modules.competitivo.schema import CompetitivoParams, EditParams
from tests import sinteticos as S

T0 = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)


def _equipo(kills: int, gold: int = 0) -> engine.TeamState:
    return engine.TeamState(gold, kills, 0, 0, 0, [])


class Autores(unittest.TestCase):
    def test_reparte_por_equipo_y_en_orden(self):
        # KC (azul) mata dos veces seguidas (Caliste y después Canna) y C9 una.
        kb = [0, 0, 1, 2, 2, 2]
        kr = [0, 0, 0, 0, 1, 1]
        frames = [engine.Frame(T0 + timedelta(seconds=i), "in_game", _equipo(kb[i]), _equipo(kr[i]))
                  for i in range(6)]
        por_jugador = [{1: 0, 4: 0, 7: 0}, {1: 0, 4: 0, 7: 0}, {1: 0, 4: 1, 7: 0},
                       {1: 1, 4: 1, 7: 0}, {1: 1, 4: 1, 7: 1}, {1: 1, 4: 1, 7: 1}]
        p = K.Partida("1", "KC", "C9", frames, por_jugador, {1: "KC Canna", 4: "KC Caliste", 7: "C9 Blaber"},
                      {1: "Rumble", 4: "MissFortune", 7: "XinZhao"})
        (pelea,) = K.detectar_peleas(p, K.Reglas(min_kills=1))
        self.assertEqual(K.autores(p, pelea), [
            {"jugador": "Caliste", "campeon": "MissFortune"},
            {"jugador": "Canna", "campeon": "Rumble"},
            {"jugador": "Blaber", "campeon": "XinZhao"},
        ])

    def test_sin_kills_por_jugador(self):
        frames = [engine.Frame(T0 + timedelta(seconds=i), "in_game", _equipo(min(i, 1)), _equipo(0))
                  for i in range(3)]
        p = K.Partida("1", "KC", "C9", frames)
        (pelea,) = K.detectar_peleas(p, K.Reglas(min_kills=1))
        self.assertEqual(K.autores(p, pelea), [None])

    def test_el_fixture_trae_nombres_y_campeones(self):
        p = K.partida_de_fixture(os.path.join(os.path.dirname(__file__), "fixtures", "kc_c9_g1.json"))
        self.assertEqual((p.nombres[4], p.campeones[4]), ("KC Caliste", "MissFortune"))
        self.assertEqual(p.campeones[7], "XinZhao")


def _beatmap(bpm=120.0, dur=90.0, drop=32.0):
    per = 60 / bpm
    beats = [round(k * per, 3) for k in range(int(dur / per))]
    downs = beats[::4]
    corte = drop if drop is not None else dur
    return {"beats": beats, "downbeats": downs, "bpm": bpm, "duracion": dur,
            "energia_por_beat": [0.3 if b < corte else 1.0 for b in beats], "onsets": [], "golpes": [],
            "secciones": [{"tipo": "intro", "inicio": 0.0, "fin": corte, "energia": 0.2},
                          {"tipo": "coro", "inicio": corte, "fin": dur, "energia": 1.0}],
            "drop": drop, "subidas": [[corte - 4 * per * 2, corte]] if drop is not None else [],
            "palabras": [{"texto": "survive", "inicio": corte + 0.1, "fin": corte + 0.5}]}


def _clip(i, kills, puntaje=50, cierra=False, multikill=None):
    return E.ClipEdit(f"1-{i}", f"tramo{i}.mp4", 0.0, 20.0,
                      [E.KillEdit(t, "izq", f"Jugador{i}{j}", "Ahri") for j, t in enumerate(kills)],
                      puntaje=puntaje, cierra=cierra, multikill=multikill, titulo=f"pelea {i}")


class Planificador(unittest.TestCase):
    BM = _beatmap()

    def _salida_kills(self, res):
        return [e.t for e in res.edl.eventos if e.tipo == "shake"]

    def test_cada_kill_cae_en_un_beat_y_la_clave_en_el_drop(self):
        clips = [_clip(1, [8.0, 9.1, 10.7], 40), _clip(2, [9.0, 11.5], 90, cierra=True, multikill="triple de X")]
        res = E.planear(clips, self.BM, E.Opciones())
        self.assertTrue(res.drop_alineado)
        beats_rel = [b - res.musica_desde for b in self.BM["beats"]]
        for t in self._salida_kills(res):
            self.assertLess(min(abs(t - b) for b in beats_rel), 1e-6, t)
        # la última kill de la pelea clave, en el drop de la canción
        self.assertAlmostEqual(self._salida_kills(res)[-1] + res.musica_desde, self.BM["drop"], places=3)
        # la primera kill de cada pelea, en un primer tiempo
        downs_rel = [d - res.musica_desde for d in self.BM["downbeats"]]
        primeras = [self._salida_kills(res)[0], self._salida_kills(res)[3]]
        for t in primeras:
            self.assertLess(min(abs(t - d) for d in downs_rel), 1e-6)
        # letreros: un kill por golpe y el TRIPLE KILL de la clave
        estilos = [t.estilo for t in res.edl.textos]
        self.assertEqual(estilos.count("kill"), 5)
        self.assertIn("multikill", estilos)

    def test_la_fuente_entra_justa_y_sin_pasarse_del_tramo(self):
        clips = [_clip(1, [5.0, 6.0]), _clip(2, [3.0, 15.0], cierra=True)]
        res = E.planear(clips, self.BM, E.Opciones())
        for seg, c in zip(res.edl.segmentos, clips):
            self.assertGreaterEqual(min(p.desde for p in seg.piezas), c.desde - 1e-6)
            self.assertLessEqual(max(p.hasta for p in seg.piezas), c.hasta + 1e-6)
            for p in seg.piezas:
                self.assertLessEqual(p.velocidad, 4.0 + 1e-6)
        # la salida dura lo mismo que los beats que ocupa
        total = sum(s.dur for s in res.edl.segmentos)
        self.assertAlmostEqual(total % (60 / 120), 0, places=3)

    def test_presupuesto_quita_las_de_menos_puntaje(self):
        clips = [_clip(i, [6.0, 8.0], puntaje=10 * i) for i in range(1, 7)]
        clips[0].cierra = True
        res = E.planear(clips, self.BM, E.Opciones(duracion_max_s=20, alinear_drop=False))
        self.assertLessEqual(res.edl.dur, 20)
        self.assertIn("1-1", res.usados)                     # la clave nunca sale
        self.assertIn("1-2", res.descartados)                # la de menos puntaje sí
        self.assertNotIn("1-6", res.descartados)

    def test_sin_lugar_antes_del_drop_avisa(self):
        res = E.planear([_clip(1, [5.0, 6.0, 7.0, 8.0, 9.0, 10.0], cierra=True)], _beatmap(drop=2.0), E.Opciones())
        self.assertFalse(res.drop_alineado)
        self.assertIsNotNone(res.aviso)

    def test_musica_desde_a_mano_arranca_en_un_primer_tiempo(self):
        res = E.planear([_clip(1, [5.0])], self.BM, E.Opciones(musica_desde=10.3))
        self.assertIn(round(res.musica_desde, 3), self.BM["downbeats"])

    def test_cancion_corta(self):
        with self.assertRaises(ValueError):
            E.planear([_clip(1, [5.0, 15.0], cierra=True)], _beatmap(dur=6, drop=None), E.Opciones())

    def test_letra(self):
        res = E.planear([_clip(1, [5.0], cierra=True)], self.BM, E.Opciones(texto="letra"))
        self.assertEqual([t.texto for t in res.edl.textos if t.estilo == "palabra"], ["survive"])
        self.assertFalse([t for t in res.edl.textos if t.estilo == "kill"])

    def test_estilos(self):
        for estilo in E.ESTILOS:
            res = E.planear([_clip(1, [5.0, 7.0], cierra=True)], self.BM, E.Opciones(estilo=estilo))
            self.assertTrue(res.edl.segmentos)
        with self.assertRaises(ValueError):
            E.planear([_clip(1, [5.0])], self.BM, E.Opciones(estilo="x"))


class Validacion(unittest.TestCase):
    BASE = CompetitivoParams.from_form({"game_ids": "115660588583171787", "vod_url": "https://youtu.be/x"})

    def test_defaults(self):
        ep = EditParams.from_form({}, self.BASE)
        self.assertEqual((ep.estilo, ep.texto, ep.look, ep.musica_desde, ep.alinear_drop),
                         ("impacto", "kills", "vivo", None, True))
        self.assertFalse(ep.letra.letra)
        self.assertEqual(EditParams.from_form({"texto": "ambos"}, self.BASE).letra.letra, True)
        self.assertEqual(EditParams.from_form({"musica_desde": "1:05"}, self.BASE).musica_desde, 65)

    def test_errores(self):
        for form in ({"estilo": "x"}, {"texto": "x"}, {"look": "sepia"}, {"lut": "no.cube"},
                     {"velocidad": "3"}, {"duracion_max_s": "500"}, {"musica_url": "nada"},
                     {"musica_desde": "ayer"}, {"anchors": "{\"999\": 1}"}):
            with self.assertRaises(ValueError, msg=form):
                EditParams.from_form(form, self.BASE)


class JobCompleto(unittest.TestCase):
    """El job de punta a punta con el VOD simulado: los tramos son videos
    sintéticos de 1920x1080 a 60 fps y la música una canción sintética."""

    def setUp(self):
        self.tmp = S.tmpdir("comp-edit")
        self.parches = [mock.patch.object(config, "BEATMAPS_FOLDER", os.path.join(self.tmp, "bm")),
                        mock.patch.object(config, "EDITS_FOLDER", os.path.join(self.tmp, "edits")),
                        mock.patch.object(processor.fuente, "bajar_tramo", side_effect=self._tramo)]
        for p in self.parches:
            p.start()

    def tearDown(self):
        for p in self.parches:
            p.stop()
        processor._SESIONES.pop("fase1", None)
        S.borrar(self.tmp)

    @staticmethod
    def _tramo(url, a, b, destino):
        return S.video_planos(destino + ".mp4", [round(b - a, 2)], w=1920, h=1080, fps=60)

    def _sesion(self):
        inicio = T0 - timedelta(seconds=1000)

        def iso(s):
            return (T0 + timedelta(seconds=s)).isoformat()

        def clip(i, kills, puntaje, cierra):
            return {"id": f"1-{i}", "game_id": "115660588583171787", "partida": 1, "titulo": f"pelea {i}",
                    "puntaje": puntaje, "multikill": None, "cierra": cierra, "seleccionada": True,
                    "clip_inicio": iso(kills[0] - 6), "clip_fin": iso(kills[-1] + 3), "primera": iso(kills[0]),
                    "eventos": [[iso(t), "izq"] for t in kills],
                    "autores": [{"jugador": "Caliste", "campeon": "Xayah"}] * len(kills),
                    "cantidades": [1] * len(kills)}

        params = CompetitivoParams.from_form({"game_ids": "115660588583171787", "vod_url": "https://youtu.be/x"})
        processor._SESIONES["fase1"] = {
            "params": params, "vod": {"inicio_utc": inicio, "titulo": "VOD", "url": params.vod_url},
            "partidas": [{"n": 1, "game_id": "115660588583171787", "azul": "KC", "rojo": "C9",
                          "inicio_utc": iso(0), "ancla_s": 100.0, "primera_kill_utc": iso(0)}],
            "clips": [clip(1, [0.0, 1.5], 40, False), clip(2, [30.0, 31.0, 32.5], 90, True)],
            "carpeta": os.path.join(self.tmp, "trabajo"),
        }
        return params

    def test_edit(self):
        params = self._sesion()
        musica = S.cancion(os.path.join(self.tmp, "m.wav"), bpm=120, dur=60, alto_desde=24)
        ep = EditParams.from_form({"titulo_edit": "KC vs C9", "intensidad": "0.8"}, params)
        job = job_manager.create_job("x", "")
        processor.edit(job, "fase1", ep, musica)
        j = job_manager.get_job(job)
        self.assertEqual(j["status"], "done", j["error"])
        res = j["resultado"]
        self.assertTrue(os.path.isfile(res["video"]))
        self.assertTrue(res["video"].startswith(os.path.join(self.tmp, "edits")))
        self.assertTrue(os.path.isfile(os.path.splitext(res["video"])[0] + ".json"))
        self.assertEqual(ffmpeg_runner.probe_resolution(res["video"]), (1080, 1920))
        self.assertAlmostEqual(ffmpeg_runner.probe_duration(res["video"]), res["duracion_total"], delta=0.06)
        self.assertTrue(res["drop_alineado"])
        # el último golpe (la clave) cae en el drop: el cuadro más brillante
        # (flash al 100 %) está ahí
        fr = S.frames_gris(res["video"], 30)
        brillo = fr.mean(axis=(1, 2))
        clave = max(e["t"] for e in res["edl"]["eventos"] if e["tipo"] == "shake")
        self.assertLessEqual(abs(int(np.argmax(brillo)) / 30 - clave), 2 / 30)
        # la música la subió el usuario fuera de uploads: no se borra
        self.assertTrue(os.path.isfile(musica))

    def test_api_valida(self):
        from app import create_app
        self._sesion()
        c = create_app().test_client()
        self.assertEqual(c.post("/api/competitivo/edit", data={"job_id": "nada"}).status_code, 404)
        self.assertEqual(c.post("/api/competitivo/edit", data={"job_id": "fase1"}).status_code, 400)
        self.assertEqual(c.post("/api/competitivo/edit", data={"job_id": "fase1", "estilo": "x"}).status_code, 400)
