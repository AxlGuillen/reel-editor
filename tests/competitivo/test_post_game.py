"""The transcription goes on after the nexus (post_s), the opinions about the
players (elogio / critica) and transcript=1 in /process (offline: feed fixture,
VOD and Whisper mocked)."""
import json
import os
import tempfile
import threading
import unittest
from datetime import datetime
from unittest import mock

import config
from app import create_app
from core import job_manager
from modules.competitivo import kills, processor
from modules.competitivo import transcript as T
from modules.competitivo.schema import CompetitivoParams

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kc_c9_g1.json")
TWITCH = "https://www.twitch.tv/videos/2908607306"
PLAYERS = ["Myrwn", "Elyoya", "Jojopyun", "Supa", "Alvaro", "Rest", "Shad0w", "POUT", "Doggo", "Kino"]


def _seg(vod_s, text, post=False, clock=None):
    s = {"clock_s": vod_s - 100, "vod_s": float(vod_s), "vod_end_s": vod_s + 4.0, "text": text,
         "clock": clock or T.fmt_clock(vod_s - 100)}
    if post:
        s["post"] = True
    return s


class Opinions(unittest.TestCase):
    def test_praise_with_a_misheard_name(self):
        segs = [_seg(1000, "Es que Mirwin está siendo una auténtica bestia."),
                _seg(1004, "No hay quien lo pare, imparable."),
                _seg(1300, "Y ahora la ha liado Elyoya, qué error.")]
        ms = [m for m in T.moments(segs, [], PLAYERS, []) if m["kind"] in ("elogio", "critica")]
        self.assertEqual([(m["kind"], m["player"]) for m in ms], [("elogio", "Myrwn"), ("critica", "Elyoya")])
        m = ms[0]                                        # the same praise said twice in a row: one
        self.assertEqual((m["vod_s"], m["vod_from"], m["vod_to"]), (1000.0, 1000.0, 1008.0))   # ±12 s, to cut it
        self.assertIn("imparable", m["context"])
        self.assertIn("bestia", m["text"])

    def test_the_name_comes_from_the_nearest_sentence(self):
        segs = [_seg(500, "Jojopyun juega esta línea."), _seg(506, "Y es el mejor de la partida."),
                _seg(560, "Rest, por su parte, está imparable.")]
        ms = [(m["kind"], m["player"]) for m in T.moments(segs, [], PLAYERS, []) if m["kind"] == "elogio"]
        self.assertEqual(ms, [("elogio", "Jojopyun"), ("elogio", "Rest")])
        # More than 10 s from the name: nobody is praised.
        far = [_seg(500, "Jojopyun juega esta línea."), _seg(520, "Y es el mejor de la partida.")]
        self.assertEqual([m for m in T.moments(far, [], PLAYERS, []) if m["kind"] == "elogio"], [])

    def test_names_are_not_words(self):
        # "resto" is not Rest, "kinos" is not Kino; 5+ letters may be misheard.
        self.assertEqual(T.named_fuzzy("el resto de los kinos es brutal", PLAYERS), [])
        self.assertEqual(T.named_fuzzy("Mirwin y Supa, y Shadow", PLAYERS), ["Myrwn", "Supa"])
        self.assertEqual(T.named_fuzzy("rest y Kino", PLAYERS), ["Rest", "Kino"])

    def test_post_game_only_has_opinions_and_its_own_clock(self):
        segs = [_seg(3000, "Le quitaron el barón a Myrwn, qué robo.", post=True, clock="+0:10"),
                _seg(3050, "Myrwn ha hecho un partidazo.", post=True, clock="+1:00")]
        ms = T.moments(segs, [], PLAYERS, [])
        self.assertEqual([(m["kind"], m["clock"], m["post"]) for m in ms], [("elogio", "+1:00", True)])
        txt = T.text_file(segs, "MKOI vs CFO")
        self.assertIn("Después del nexo", txt)
        self.assertLess(txt.index("Después del nexo"), txt.index("[+0:10]"))
        self.assertEqual(T.fmt_post(83.4), "+1:23")

    def test_in_the_game_old_moments_are_as_before(self):
        ms = T.moments([_seg(1000, "Van al barón.")], [], PLAYERS, [])
        self.assertEqual([m["kind"] for m in ms], ["barón"])
        self.assertEqual(ms[0]["vod_s"], 1000.0)


class PostGame(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(config, "SESSIONS_FOLDER", os.path.join(self.tmp.name, "sessions")),
                        mock.patch.object(config, "COMPETITIVO_FOLDER", os.path.join(self.tmp.name, "out")),
                        mock.patch.object(processor, "TRABAJO", os.path.join(self.tmp.name, "trabajo"))]
        for p in self.patches:
            p.start()
        self.partida = kills.partida_de_fixture(FIXTURE)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _phase1(self, **form):
        partida = self.partida
        params = CompetitivoParams.from_form({"game_ids": partida.game_id, "vod_url": TWITCH,
                                              "carpeta_dia": "Día 2", "partido": "KC vs C9", **form})
        ancla = {"ancla_s": 100.0, "ancla_propuesta_s": 100.0, "desfase_s": 0.0, "confianza": "alta",
                 "suspicious": False, "coincidencias": 3, "esperadas": 3, "referencia": "x",
                 "referencia_vod_s": 0, "frame": None, "primera_kill_utc": partida.frames[1].ts.isoformat(),
                 "clock0_utc": partida.frames[0].ts.isoformat()}
        job = job_manager.create_job(TWITCH, output_path="")
        with mock.patch.object(processor.fuente, "info_vod",
                               return_value={"inicio_utc": partida.t0, "duracion": 1, "titulo": "t",
                                             "platform": "twitch"}), \
                mock.patch.object(processor.kills, "fetch_partida", return_value=partida), \
                mock.patch.object(processor, "_ancla", return_value=ancla), \
                mock.patch.object(processor, "transcribe_in_a_row") as chain:
            started = threading.Event()
            chain.side_effect = lambda specs: started.set()
            processor.process(job, params)
            if params.transcript:
                self.assertTrue(started.wait(5))
        return job, chain

    def test_transcript_1_starts_it_and_the_status_has_the_job(self):
        job, chain = self._phase1(transcript="1")
        st = create_app().test_client().get(f"/api/competitivo/status/{job}").get_json()
        self.assertEqual(st["status"], "done")
        self.assertEqual(st["transcript_jobs"], [{"game_id": self.partida.game_id, "job_id": st["transcript_job_id"]}])
        (specs,) = chain.call_args.args
        t = specs[0]
        self.assertEqual((t[0], t[1], t[2], t[4], t[5], t[6], t[7]),
                         (st["transcript_job_id"], job, self.partida.game_id, processor.DRAFT_LEAD_S,
                          processor.POST_S, "Día 2", "KC vs C9"))
        self.assertEqual(job_manager.get_job(st["transcript_job_id"])["modo"], "transcript")
        # It survives a restart of the server with the rest of the session.
        processor._SESIONES.pop(job)
        with job_manager._lock:
            job_manager._jobs.pop(job)
        again = create_app().test_client().get(f"/api/competitivo/status/{job}").get_json()
        self.assertEqual(again["transcript_job_id"], st["transcript_job_id"])

    def test_without_transcript_1_nothing_starts(self):
        job, chain = self._phase1()
        chain.assert_not_called()
        st = create_app().test_client().get(f"/api/competitivo/status/{job}").get_json()
        self.assertIsNone(st["transcript_job_id"])

    def test_route_post_s(self):
        job, _ = self._phase1()
        c = create_app().test_client()
        for bad in ("2000", "-5", "x"):
            r = c.post("/api/competitivo/transcript", data={"job_id": job, "post_s": bad})
            self.assertEqual(r.status_code, 400, bad)
        got = []
        done = threading.Event()
        with mock.patch.object(processor, "transcript_job", side_effect=lambda *a: (got.append(a), done.set())):
            self.assertEqual(c.post("/api/competitivo/transcript", data={"job_id": job}).status_code, 202)
            self.assertTrue(done.wait(5))
            done.clear()
            self.assertEqual(c.post("/api/competitivo/transcript", data={"job_id": job, "post_s": "0"}).status_code, 202)
            self.assertTrue(done.wait(5))
        self.assertEqual([a[-1] for a in got], [processor.POST_S, 0.0])

    def test_the_transcription_goes_past_the_nexus(self):
        job, _ = self._phase1()
        sesion = processor.get_session(job)
        d = sesion["partidas"][0]
        offset = processor._offsets(sesion, {})[d["game_id"]]
        inicio = sesion["vod"]["inicio_utc"]
        nexus_vod = processor._vod(self.partida.frames[-1].ts, inicio, offset)
        a = processor._vod(datetime.fromisoformat(d["clock0_utc"]), inicio, offset) - processor.DRAFT_LEAD_S

        def word(t, w):
            return {"word": w, "start": t, "end": t + 0.4}

        def seg(vod, text):
            t = vod - a
            ws = [word(t + 0.5 * i, w) for i, w in enumerate(text.split())]
            return {"start": t, "end": ws[-1]["end"], "text": text, "words": ws}

        star, flop = [r["player"] for r in d["roster"]][:2]
        segs = [seg(nexus_vod - 20, "Van al barón."), seg(nexus_vod + 5, "Se acabó, cae el nexo."),
                seg(nexus_vod + 120, f"{star} ha hecho un partidazo, una bestia."),
                seg(nexus_vod + 300, f"Y {flop} fatal, la ha liado.")]
        audio = os.path.join(self.tmp.name, "audio.m4a")
        open(audio, "wb").close()
        with mock.patch.object(processor.fuente, "bajar_audio", return_value=audio) as bajar, \
                mock.patch.object(processor.kills, "fetch_partida", return_value=self.partida), \
                mock.patch("modules.subtitles.processor.transcribe", return_value=segs):
            tid = processor.new_transcript_job(job, TWITCH, d["game_id"])
            processor.transcript_job(tid, job, d["game_id"], "es", processor.DRAFT_LEAD_S, "Día 2", "KC vs C9", 1, 900)
        st = job_manager.get_job(tid)
        self.assertEqual(st["status"], "done", st.get("error"))
        self.assertAlmostEqual(bajar.call_args.args[2], nexus_vod + 900, places=1)       # up to 15 min after
        res = st["resultado"]
        self.assertEqual((res["post_segments"], res["praise"], res["criticism"]), (3, 1, 1))
        data = json.load(open(res["json"], encoding="utf-8"))
        self.assertEqual(data["post_s"], 900)
        self.assertAlmostEqual(data["vod"]["nexus_s"], nexus_vod, places=1)
        clocks = [s["clock"] for s in data["segments"]]
        self.assertEqual(clocks[1:], ["+0:05", "+2:00", "+5:00"])        # since the nexus, not the game clock
        self.assertNotIn("post", data["segments"][0])
        self.assertTrue(all("post_s" in w for w in data["segments"][2]["words"]))
        praise = next(m for m in res["moments"] if m["kind"] == "elogio")
        self.assertEqual((praise["player"], praise["clock"], praise["post"]), (star, "+2:00", True))
        self.assertAlmostEqual(praise["vod_s"], nexus_vod + 120, places=1)
        txt = open(res["txt"], encoding="utf-8").read()
        self.assertIn("[+2:00]", txt)
        self.assertIn("Después del nexo", txt)
        # No tail at all: it ends at the nexus.
        with mock.patch.object(processor.fuente, "bajar_audio", return_value=audio) as bajar, \
                mock.patch.object(processor.kills, "fetch_partida", return_value=self.partida), \
                mock.patch("modules.subtitles.processor.transcribe", return_value=segs[:1]):
            tid = processor.new_transcript_job(job, TWITCH, d["game_id"])
            processor.transcript_job(tid, job, d["game_id"], "es", processor.DRAFT_LEAD_S, "", "", 1, 0)
        self.assertAlmostEqual(bajar.call_args.args[2], nexus_vod, places=1)


if __name__ == "__main__":
    unittest.main()
