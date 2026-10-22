"""Tests of competitivo's edit mode: kill authors, the rhythm planner (pure)
and the full job with a simulated VOD (offline)."""
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
from tests import synthetic as S

T0 = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)


def _team(kills: int, gold: int = 0) -> engine.TeamState:
    return engine.TeamState(gold, kills, 0, 0, 0, [])


class KillAuthors(unittest.TestCase):
    def test_assigned_per_team_and_in_order(self):
        # KC (blue) kills twice in a row (Caliste, then Canna) and C9 once.
        kb = [0, 0, 1, 2, 2, 2]
        kr = [0, 0, 0, 0, 1, 1]
        frames = [engine.Frame(T0 + timedelta(seconds=i), "in_game", _team(kb[i]), _team(kr[i]))
                  for i in range(6)]
        per_player = [{1: 0, 4: 0, 7: 0}, {1: 0, 4: 0, 7: 0}, {1: 0, 4: 1, 7: 0},
                      {1: 1, 4: 1, 7: 0}, {1: 1, 4: 1, 7: 1}, {1: 1, 4: 1, 7: 1}]
        p = K.Partida("1", "KC", "C9", frames, per_player, {1: "KC Canna", 4: "KC Caliste", 7: "C9 Blaber"},
                      {1: "Rumble", 4: "MissFortune", 7: "XinZhao"})
        (fight,) = K.detectar_peleas(p, K.Reglas(min_kills=1))
        self.assertEqual(K.kill_authors(p, fight), [
            {"player": "Caliste", "champion": "MissFortune"},
            {"player": "Canna", "champion": "Rumble"},
            {"player": "Blaber", "champion": "XinZhao"},
        ])

    def test_no_per_player_kills(self):
        frames = [engine.Frame(T0 + timedelta(seconds=i), "in_game", _team(min(i, 1)), _team(0))
                  for i in range(3)]
        p = K.Partida("1", "KC", "C9", frames)
        (fight,) = K.detectar_peleas(p, K.Reglas(min_kills=1))
        self.assertEqual(K.kill_authors(p, fight), [None])

    def test_fixture_has_names_and_champions(self):
        p = K.partida_de_fixture(os.path.join(os.path.dirname(__file__), "fixtures", "kc_c9_g1.json"))
        self.assertEqual((p.nombres[4], p.champions[4]), ("KC Caliste", "MissFortune"))
        self.assertEqual(p.champions[7], "XinZhao")


def _beatmap(bpm=120.0, dur=90.0, drop=32.0):
    per = 60 / bpm
    beats = [round(k * per, 3) for k in range(int(dur / per))]
    downs = beats[::4]
    split = drop if drop is not None else dur
    return {"beats": beats, "downbeats": downs, "bpm": bpm, "duration": dur,
            "energy_per_beat": [0.3 if b < split else 1.0 for b in beats], "onsets": [], "hits": [],
            "sections": [{"type": "intro", "start": 0.0, "end": split, "energy": 0.2},
                         {"type": "chorus", "start": split, "end": dur, "energy": 1.0}],
            "drop": drop, "buildups": [[split - 4 * per * 2, split]] if drop is not None else [],
            "words": [{"text": "survive", "start": split + 0.1, "end": split + 0.5}]}


def _clip(i, kills, score=50, closes=False, multikill=None):
    return E.EditClip(f"1-{i}", f"span{i}.mp4", 0.0, 20.0,
                      [E.EditKill(t, "izq", f"Player{i}{j}", "Ahri") for j, t in enumerate(kills)],
                      score=score, closes=closes, multikill=multikill, title=f"pelea {i}")


class Planner(unittest.TestCase):
    BM = _beatmap()

    def _output_kills(self, res):
        return [e.t for e in res.edl.events if e.kind == "shake"]

    def test_each_kill_on_a_beat_and_the_key_one_on_the_drop(self):
        clips = [_clip(1, [8.0, 9.1, 10.7], 40), _clip(2, [9.0, 11.5], 90, closes=True, multikill="triple de X")]
        res = E.plan(clips, self.BM, E.EditOptions())
        self.assertTrue(res.drop_aligned)
        beats_rel = [b - res.music_start for b in self.BM["beats"]]
        for t in self._output_kills(res):
            self.assertLess(min(abs(t - b) for b in beats_rel), 1e-6, t)
        # the last kill of the key fight, on the song's drop
        self.assertAlmostEqual(self._output_kills(res)[-1] + res.music_start, self.BM["drop"], places=3)
        # the first kill of each fight, on a downbeat
        downs_rel = [d - res.music_start for d in self.BM["downbeats"]]
        firsts = [self._output_kills(res)[0], self._output_kills(res)[3]]
        for t in firsts:
            self.assertLess(min(abs(t - d) for d in downs_rel), 1e-6)
        # titles: one kill per hit and the key fight's TRIPLE KILL
        styles = [t.style for t in res.edl.texts]
        self.assertEqual(styles.count("kill"), 5)
        self.assertIn("multikill", styles)

    def test_source_fits_without_leaving_the_span(self):
        clips = [_clip(1, [5.0, 6.0]), _clip(2, [3.0, 15.0], closes=True)]
        res = E.plan(clips, self.BM, E.EditOptions())
        for seg, c in zip(res.edl.segments, clips):
            self.assertGreaterEqual(min(p.start for p in seg.pieces), c.start - 1e-6)
            self.assertLessEqual(max(p.end for p in seg.pieces), c.end + 1e-6)
            for p in seg.pieces:
                self.assertLessEqual(p.speed, 4.0 + 1e-6)
        # the output lasts exactly the beats it takes
        total = sum(s.dur for s in res.edl.segments)
        self.assertAlmostEqual(total % (60 / 120), 0, places=3)

    def test_budget_drops_the_lowest_scores(self):
        clips = [_clip(i, [6.0, 8.0], score=10 * i) for i in range(1, 7)]
        clips[0].closes = True
        res = E.plan(clips, self.BM, E.EditOptions(max_duration_s=20, align_drop=False))
        self.assertLessEqual(res.edl.dur, 20)
        self.assertIn("1-1", res.used)                       # the key fight never goes
        self.assertIn("1-2", res.dropped)                    # the lowest score does
        self.assertNotIn("1-6", res.dropped)

    def test_no_room_before_the_drop_warns(self):
        res = E.plan([_clip(1, [5.0, 6.0, 7.0, 8.0, 9.0, 10.0], closes=True)], _beatmap(drop=2.0), E.EditOptions())
        self.assertFalse(res.drop_aligned)
        self.assertIsNotNone(res.warning)

    def test_manual_music_start_lands_on_a_downbeat(self):
        res = E.plan([_clip(1, [5.0])], self.BM, E.EditOptions(music_start=10.3))
        self.assertIn(round(res.music_start, 3), self.BM["downbeats"])

    def test_short_song(self):
        with self.assertRaises(ValueError):
            E.plan([_clip(1, [5.0, 15.0], closes=True)], _beatmap(dur=6, drop=None), E.EditOptions())

    def test_lyrics(self):
        res = E.plan([_clip(1, [5.0], closes=True)], self.BM, E.EditOptions(text="lyrics"))
        self.assertEqual([t.text for t in res.edl.texts if t.style == "word"], ["survive"])
        self.assertFalse([t for t in res.edl.texts if t.style == "kill"])

    def test_styles(self):
        for style in E.STYLES:
            res = E.plan([_clip(1, [5.0, 7.0], closes=True)], self.BM, E.EditOptions(style=style))
            self.assertTrue(res.edl.segments)
        with self.assertRaises(ValueError):
            E.plan([_clip(1, [5.0])], self.BM, E.EditOptions(style="x"))


class Validation(unittest.TestCase):
    BASE = CompetitivoParams.from_form({"game_ids": "115660588583171787", "vod_url": "https://youtu.be/x"})

    def test_defaults(self):
        ep = EditParams.from_form({}, self.BASE)
        self.assertEqual((ep.style, ep.text, ep.look, ep.music_start, ep.align_drop),
                         ("impact", "kills", "vivid", None, True))
        self.assertFalse(ep.lyrics.lyrics)
        self.assertEqual(EditParams.from_form({"text": "both"}, self.BASE).lyrics.lyrics, True)
        self.assertEqual(EditParams.from_form({"music_start": "1:05"}, self.BASE).music_start, 65)

    def test_errors(self):
        for form in ({"style": "x"}, {"text": "x"}, {"look": "sepia"}, {"lut": "no.cube"},
                     {"speed": "3"}, {"max_duration_s": "500"}, {"music_url": "nada"},
                     {"music_start": "ayer"}, {"anchors": "{\"999\": 1}"}):
            with self.assertRaises(ValueError, msg=form):
                EditParams.from_form(form, self.BASE)


class FullJob(unittest.TestCase):
    """The job end to end with a simulated VOD: the spans are synthetic
    1920x1080 60 fps videos and the music a synthetic song."""

    def setUp(self):
        self.tmp = S.tmpdir("comp-edit")
        self.patches = [mock.patch.object(config, "BEATMAPS_FOLDER", os.path.join(self.tmp, "bm")),
                        mock.patch.object(config, "EDITS_FOLDER", os.path.join(self.tmp, "edits")),
                        mock.patch.object(processor.fuente, "bajar_tramo", side_effect=self._span)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        processor._SESIONES.pop("phase1", None)
        S.remove(self.tmp)

    @staticmethod
    def _span(url, a, b, destination):
        return S.shots_video(destination + ".mp4", [round(b - a, 2)], w=1920, h=1080, fps=60)

    def _session(self):
        start = T0 - timedelta(seconds=1000)

        def iso(s):
            return (T0 + timedelta(seconds=s)).isoformat()

        def clip(i, kills, score, closes):
            return {"id": f"1-{i}", "game_id": "115660588583171787", "partida": 1, "titulo": f"pelea {i}",
                    "puntaje": score, "multikill": None, "cierra": closes, "seleccionada": True,
                    "clip_inicio": iso(kills[0] - 6), "clip_fin": iso(kills[-1] + 3), "primera": iso(kills[0]),
                    "eventos": [[iso(t), "izq"] for t in kills],
                    "authors": [{"player": "Caliste", "champion": "Xayah"}] * len(kills),
                    "counts": [1] * len(kills)}

        params = CompetitivoParams.from_form({"game_ids": "115660588583171787", "vod_url": "https://youtu.be/x"})
        processor._SESIONES["phase1"] = {
            "params": params, "vod": {"inicio_utc": start, "titulo": "VOD", "url": params.vod_url},
            "partidas": [{"n": 1, "game_id": "115660588583171787", "azul": "KC", "rojo": "C9",
                          "inicio_utc": iso(0), "ancla_s": 100.0, "primera_kill_utc": iso(0)}],
            "clips": [clip(1, [0.0, 1.5], 40, False), clip(2, [30.0, 31.0, 32.5], 90, True)],
            "carpeta": os.path.join(self.tmp, "work"),
        }
        return params

    def test_edit(self):
        params = self._session()
        music = S.song(os.path.join(self.tmp, "m.wav"), bpm=120, dur=60, loud_from=24)
        ep = EditParams.from_form({"edit_title": "KC vs C9", "intensity": "0.8"}, params)
        job = job_manager.create_job("x", "")
        processor.edit(job, "phase1", ep, music)
        j = job_manager.get_job(job)
        self.assertEqual(j["status"], "done", j["error"])
        res = j["resultado"]
        self.assertTrue(os.path.isfile(res["video"]))
        self.assertTrue(res["video"].startswith(os.path.join(self.tmp, "edits")))
        self.assertTrue(os.path.isfile(os.path.splitext(res["video"])[0] + ".json"))
        self.assertEqual(ffmpeg_runner.probe_resolution(res["video"]), (1080, 1920))
        self.assertAlmostEqual(ffmpeg_runner.probe_duration(res["video"]), res["total_duration"], delta=0.06)
        self.assertTrue(res["drop_aligned"])
        # the last hit (the key one) lands on the drop: the brightest frame
        # (100 % flash) is there
        fr = S.gray_frames(res["video"], 30)
        brightness = fr.mean(axis=(1, 2))
        key = max(e["t"] for e in res["edl"]["events"] if e["kind"] == "shake")
        self.assertLessEqual(abs(int(np.argmax(brightness)) / 30 - key), 2 / 30)
        # the user's music lives outside uploads: it is not deleted
        self.assertTrue(os.path.isfile(music))

    def test_api_validates(self):
        from app import create_app
        self._session()
        c = create_app().test_client()
        self.assertEqual(c.post("/api/competitivo/edit", data={"job_id": "nada"}).status_code, 404)
        self.assertEqual(c.post("/api/competitivo/edit", data={"job_id": "phase1"}).status_code, 400)
        self.assertEqual(c.post("/api/competitivo/edit", data={"job_id": "phase1", "style": "x"}).status_code, 400)
