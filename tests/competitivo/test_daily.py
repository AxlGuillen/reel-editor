"""The day's summary by itself (offline: phase 1 and the render mocked, the
montage real)."""
import os
import unittest
from unittest import mock

import config
from core import ffmpeg_runner, job_manager
from modules.competitivo import processor
from tests import synthetic as S


def _c(id_, score, winner, dur, start, eligible=True, closes=False):
    return {"id": id_, "puntaje": score, "ganador": winner, "duracion": dur, "clip_inicio": start,
            "elegible": eligible, "cierra": closes}


class Pick(unittest.TestCase):
    def test_like_axl_picked_t1_vs_los(self):
        # LOS vs T1 of 23/10: Axl kept 18:59, 21:41 and the pick → nexus
        clips = [_c("1-1", 26, "T1", 15, "a1", False), _c("1-8", 100, "T1", 19.4, "a8"),
                 _c("1-10", 100, "T1", 23.5, "b0"), _c("1-11", 48, "LOS", 15, "b1", False),
                 _c("1-12", 42, "T1", 15, "b2", False), _c("1-14", 59, "T1", 11, "b4", False, closes=True)]
        self.assertEqual(processor.daily_pick(clips, "T1"), ["1-8", "1-10", "1-14"])

    def test_only_the_winners_and_a_cap(self):
        clips = [_c("1-2", 100, "IG", 28, "a2"), _c("1-11", 100, "GEN", 26, "b1"), _c("1-14", 100, "GEN", 23, "b4"),
                 _c("1-15", 66, "GEN", 15, "b5", False, closes=True)]
        pick = processor.daily_pick(clips, "GEN")
        self.assertNotIn("1-2", pick)                                         # the loser's fight
        self.assertEqual(pick[-1], "1-15")                                    # the closing one, in order
        self.assertLessEqual(sum(c["duracion"] for c in clips if c["id"] in pick), processor.DAILY_MAX_S)
        self.assertEqual(processor.daily_pick([_c("1-1", 40, "G2", 20, "a")], "G2"), ["1-1"])   # at least one


class DailyJob(unittest.TestCase):
    def setUp(self):
        self.tmp = S.tmpdir("daily")
        self.p = [mock.patch.object(config, "COMPETITIVO_FOLDER", os.path.join(self.tmp, "comp")),
                  mock.patch.object(processor, "TRABAJO", os.path.join(self.tmp, "work"))]
        for x in self.p:
            x.start()

    def tearDown(self):
        for x in self.p:
            x.stop()
        S.remove(self.tmp)

    def test_three_games_one_doubtful(self):
        games = {"111111111111111111": ("KC", "AL", "KC", "2026-10-23T20:16:00+00:00", "alta"),
                 "222222222222222222": ("IG", "GEN", "GEN", "2026-10-23T17:21:00+00:00", "alta"),
                 "333333333333333333": ("DK", "G2", "DK", "2026-10-23T21:16:00+00:00", "media")}
        videos = {}

        def fake_process(job, params):
            gid = params.game_ids[0]
            azul, rojo, win, start, conf = games[gid]
            job_manager.update_job(job, status="done", partidas=[{"game_id": gid, "azul": azul, "rojo": rojo,
                                                                   "ganador": win, "inicio_utc": start,
                                                                   "confianza": conf}],
                                   clips=[_c("1-1", 100, win, 2, "a")])

        def fake_finish(job, fp):
            path = os.path.join(self.tmp, f"{fp.partido}.mp4")
            S.shots_video(path, [1.0], w=320, h=180, fps=30)
            videos[fp.partido] = (fp.clips, fp.carpeta_dia, fp.titulo, fp.intro_modo)
            job_manager.update_job(job, status="done", resultado={"video": path, "duracion_total": 1.0})

        job = job_manager.create_job("x", "")
        with mock.patch.object(processor, "process", side_effect=fake_process), \
                mock.patch.object(processor, "finish", side_effect=fake_finish):
            processor.daily(job, list(games), "https://www.twitch.tv/videos/1", "Resumen Suiza Día 2 (24-10)")
        j = job_manager.get_job(job)
        self.assertEqual(j["status"], "done", j["error"])
        r = j["resultado"]
        self.assertEqual(r["order"], ["GEN vs IG", "KC vs AL"])               # in the order they were played
        self.assertIn("DK vs G2: ancla dudosa", j["aviso"])                   # left out, said
        self.assertEqual(videos["KC vs AL"], (["1-1"], "Resumen Suiza Día 2 (24-10)", "highlights", "off"))
        self.assertTrue(r["video"].endswith(os.path.join("Resumen Suiza Día 2 (24-10)",
                                                          "Highlights Resumen Suiza Día 2 (24-10).mp4")))
        self.assertAlmostEqual(ffmpeg_runner.probe_duration(r["video"]), 2.0, delta=0.15)
        self.assertTrue(ffmpeg_runner.has_audio_stream(r["video"]))           # with the game's audio

    def test_api(self):
        from app import create_app
        c = create_app().test_client()
        self.assertEqual(c.post("/api/competitivo/daily", data={"game_ids": "12", "vod_url": "https://x"}).status_code, 400)
        self.assertEqual(c.post("/api/competitivo/daily", data={"game_ids": "111111111111111111",
                                                                 "vod_url": "https://x"}).status_code, 400)  # no folder
        with mock.patch.object(processor, "daily") as d:
            r = c.post("/api/competitivo/daily", data={"game_ids": "111111111111111111, 222222222222222222",
                                                       "vod_url": "https://x", "carpeta_dia": "Día 2",
                                                       "picks": '{"111111111111111111": ["1-3"]}', "zoom": "1.4"})
            self.assertEqual(r.status_code, 202)
        args = d.call_args.args
        self.assertEqual((args[1], args[3], args[5], args[6]["zoom"]),
                         (["111111111111111111", "222222222222222222"], "Día 2", {"111111111111111111": ["1-3"]}, 1.4))


if __name__ == "__main__":
    unittest.main()
