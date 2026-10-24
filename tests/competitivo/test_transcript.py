"""The whole game transcribed and its index of moments, and moments without a
kill in the edits (offline)."""
import os
import unittest
from unittest import mock

from modules.competitivo import edit as E
from modules.competitivo import transcript as T
from modules.competitivo.schema import CompetitivoParams, EditParams
from tests.competitivo.test_edit import _beatmap, _clip


class Index(unittest.TestCase):
    SEGMENTS = [
        {"clock_s": -200.0, "text": "Empieza el draft, primera elección para TL."},
        {"clock_s": 105.0, "text": "¡Le roba el red Josedeodo! ¡Qué robo!"},
        {"clock_s": 108.0, "text": "Robo del red, increíble."},
        {"clock_s": 1222.0, "text": "Van al barón, cuidado con el smite."},
        {"clock_s": 1500.0, "text": "Ahí está el rojo de nuevo, nada."},
        {"clock_s": 2230.0, "text": "¡Se acabó! GG, cae el nexo."},
    ]
    EVENTS = [{"clock_s": 1230.0, "tipo": "baron", "equipo": "CFO", "detalle": "barón", "cantidad": 1},
              {"clock_s": 2236.0, "tipo": "fin", "equipo": "CFO", "detalle": "cae el nexo", "cantidad": 1}]

    def test_moments(self):
        m = T.moments(self.SEGMENTS, self.EVENTS, ["Josedeodo", "Rest"], ["Lee Sin", "Viego"])
        voz = [x for x in m if x["source"] == "voz"]
        self.assertEqual([(x["clock"], x["kind"]) for x in voz],
                         [("1:45", "robo de buff"), ("20:22", "barón"), ("37:10", "nexo")])
        steal = voz[0]
        self.assertEqual(steal["players"], ["Josedeodo"])                       # who
        self.assertIsNone(steal["feed"])                                        # no kill: the narration finds it
        self.assertEqual(voz[1]["feed"]["tipo"], "baron")                       # crossed with the feed
        self.assertEqual(voz[2]["feed"]["clock"], "37:16")
        # the feed's events are in the index too, in order
        self.assertEqual([x["clock_s"] for x in m], sorted(x["clock_s"] for x in m))
        self.assertIn(("20:30", "feed"), [(x["clock"], x["source"]) for x in m])

    def test_kinds_and_clock(self):
        self.assertEqual(T.kinds_of("le roba el azul"), ["robo_buff"])
        self.assertEqual(T.kinds_of("el red de la jungla"), [])                # "red" alone is not a moment
        self.assertIn("multikill", T.kinds_of("¡PENTAKILL de Ruler!"))
        self.assertEqual((T.fmt_clock(-192), T.fmt_clock(105.4)), ("-3:12", "1:45"))
        txt = T.text_file(self.SEGMENTS[:2], "TL vs CFO")
        self.assertIn("[-3:20] Empieza el draft", txt)
        self.assertIn("[1:45] ¡Le roba el red Josedeodo!", txt)


class Moments(unittest.TestCase):
    BM = _beatmap()

    def _moment(self, i, t, dur=6.0, label="robo del red"):
        c = E.EditClip(f"m{i}", f"m{i}.mp4", 0.0, 20.0, [], score=0, title=label)
        c.moment_dur, c.moment_t = dur, t
        return c

    def test_early_moment_goes_in_real_time_with_ibai(self):
        clips = [self._moment(1, 10.0), _clip(1, [8.0, 9.1], 50), _clip(2, [9.0, 11.5], 90, closes=True)]
        res = E.plan(clips, self.BM, E.EditOptions(structure="acts", chronological=True, build_fights=1))
        acts = {a["act"]: a for a in res.acts}
        self.assertEqual(acts["build"]["clips"], ["m1", "1-1"])                # in order, before the fights
        seg = next(s for s in res.edl.segments if s.label == "m1:moment")
        self.assertEqual([(p.start, p.end, p.dur) for p in seg.pieces], [(6.4, 12.4, 6.0)])   # 60 % before, 1×
        self.assertIn("m1", [v["source"][:2] for v in res.voice_spans])         # Ibai narrates it
        mk = next(k for k in res.kills if k["recipe"] == "moment")
        self.assertEqual((mk["label"], mk["clip"]), ("robo del red", "m1"))

    def test_late_moment_on_the_drop(self):
        clips = [_clip(1, [8.0, 9.1], 50), self._moment(1, 10.0), _clip(2, [9.0, 11.5], 90, closes=True)]
        res = E.plan(clips, self.BM, E.EditOptions(structure="acts", chronological=True, build_fights=0))
        acts = {a["act"]: a for a in res.acts}
        self.assertEqual(acts["drop"]["clips"], ["1-1", "m1", "1-2"])
        seg = next(s for s in res.edl.segments if s.label == "m1")
        self.assertTrue(all(abs(p.speed - 1) < 0.15 for p in seg.pieces if not p.frozen))   # ~real time
        mk = next(k for k in res.kills if k["recipe"] == "moment")
        self.assertAlmostEqual(((mk["t"] + res.music_start) / 0.5) % 1, 0, places=2)     # on a beat
        self.assertFalse([e for e in res.edl.events if abs(e.t - mk["t"]) < 1e-6 and e.kind in ("flash", "zoom")])

    def test_only_moments_is_not_an_edit(self):
        with self.assertRaises(ValueError):
            E.plan([self._moment(1, 10.0)], self.BM, E.EditOptions(structure="acts"))

    def test_schema(self):
        base = CompetitivoParams.from_form({"game_ids": "115660588583171787", "vod_url": "https://youtu.be/x"})
        ep = EditParams.from_form({"moments": '[{"clock": "1:45", "dur": 6, "label": "robo del red"}]'}, base)
        self.assertEqual(ep.moments, [{"clock": "1:45", "dur": 6.0, "label": "robo del red",
                                       "game_id": "115660588583171787"}])
        for bad in ('[{"clock": "1h"}]', '[{"clock": "1:45", "dur": 30}]', '[{"clock": "1:45", "game_id": "9"}]', "nada"):
            with self.assertRaises(ValueError, msg=bad):
                EditParams.from_form({"moments": bad}, base)


if __name__ == "__main__":
    unittest.main()


class MomentJob(unittest.TestCase):
    def setUp(self):
        from tests.competitivo import test_edit
        self.fj = test_edit.FullJob()
        test_edit.FullJob.setUp(self.fj)

    def tearDown(self):
        from tests.competitivo import test_edit
        test_edit.FullJob.tearDown(self.fj)

    def test_a_moment_from_the_clock_to_the_video(self):
        from datetime import timedelta
        from core import job_manager
        from modules.competitivo import processor
        from tests import synthetic as S
        from tests.competitivo.test_edit import T0
        params = self.fj._session()
        d = processor._SESIONES["phase1"]["partidas"][0]
        d.update(clock0_utc=(T0 - timedelta(seconds=120)).isoformat(), pauses=[])   # 0:00 two minutes before T0
        music = S.song(os.path.join(self.fj.tmp, "m.wav"), bpm=120, dur=60, loud_from=24)
        ep = EditParams.from_form({"moments": '[{"clock": "1:45", "dur": 4, "label": "robo del red"}]',
                                   "voice_subs": "0"}, params)
        spans = []
        real = self.fj._span

        def span(url, a, b, dest):
            spans.append((a, b))
            return real(url, a, b, dest)
        job = job_manager.create_job("x", "")
        with mock.patch.object(processor.fuente, "bajar_tramo", side_effect=span):
            processor.edit(job, "phase1", ep, music)
        j = job_manager.get_job(job)
        self.assertEqual(j["status"], "done", j["error"])
        kills = j["resultado"]["plan"]["kills"]
        self.assertEqual([k["recipe"] for k in kills][0], "moment")             # 1:45 comes first
        acts = {a["act"]: a for a in j["resultado"]["plan"]["acts"]}
        self.assertIn("m1", acts["build"]["clips"])
        # its span: 1:45 = T0 − 15 s; the VOD second of T0 is the anchor's (100): 85 ± margins
        a, b = spans[0]
        self.assertLess(a, 85 - 0.6 * 4)
        self.assertGreater(b, 85 + 0.4 * 4)

    def test_without_a_calibrated_clock(self):
        from core import job_manager
        from modules.competitivo import processor
        from tests import synthetic as S
        params = self.fj._session()
        music = S.song(os.path.join(self.fj.tmp, "m.wav"), bpm=120, dur=60, loud_from=24)
        ep = EditParams.from_form({"moments": '[{"clock": "1:45"}]'}, params)
        job = job_manager.create_job("x", "")
        processor.edit(job, "phase1", ep, music)
        self.assertIn("reloj del marcador", job_manager.get_job(job)["error"])
