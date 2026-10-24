"""A phase 1 survives a restart of the server (offline: feed fixture, VOD mocked)."""
import os
import tempfile
import unittest
from unittest import mock

import config
from core import job_manager
from modules.competitivo import kills, processor
from modules.competitivo.schema import CompetitivoParams

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kc_c9_g1.json")
TWITCH = "https://www.twitch.tv/videos/2908607306"


class Sessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(config, "SESSIONS_FOLDER", os.path.join(self.tmp.name, "sessions")),
                        mock.patch.object(processor, "TRABAJO", os.path.join(self.tmp.name, "trabajo"))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _phase1(self):
        partida = kills.partida_de_fixture(FIXTURE)
        params = CompetitivoParams.from_form({"game_ids": partida.game_id, "vod_url": TWITCH, "zoom": "1.4",
                                              "carpeta_dia": "Día 2", "partido": "KC vs C9"})
        ancla = {"ancla_s": 100.0, "ancla_propuesta_s": 100.0, "desfase_s": 0.0, "confianza": "alta",
                 "suspicious": False, "coincidencias": 3, "esperadas": 3, "referencia": "x",
                 "referencia_vod_s": 0, "frame": None, "primera_kill_utc": partida.frames[1].ts.isoformat()}
        job = job_manager.create_job(TWITCH, output_path="")
        with mock.patch.object(processor.fuente, "info_vod",
                               return_value={"inicio_utc": partida.t0, "duracion": 1, "titulo": "t",
                                             "platform": "twitch"}), \
                mock.patch.object(processor.kills, "fetch_partida", return_value=partida), \
                mock.patch.object(processor, "_ancla", return_value=ancla):
            processor.process(job, params)
        return job, params

    def _restart(self, job):
        processor._SESIONES.pop(job, None)
        with job_manager._lock:
            job_manager._jobs.pop(job, None)

    def test_phase_1_is_back_after_a_restart(self):
        job, params = self._phase1()
        before = job_manager.get_job(job)
        self.assertTrue(os.path.isfile(processor._session_path(job)))
        self._restart(job)
        self.assertIsNone(job_manager.get_job(job))
        from app import create_app
        st = create_app().test_client().get(f"/api/competitivo/status/{job}").get_json()
        self.assertEqual((st["status"], st["stage"], st["finished"]), ("done", "esperando_ancla", False))
        self.assertEqual(st["clips"], before["clips"])
        self.assertEqual(st["seleccion"], before["seleccion"])
        sesion = processor.get_session(job)
        self.assertEqual(sesion["params"], params)
        self.assertEqual(sesion["vod"]["inicio_utc"], kills.partida_de_fixture(FIXTURE).t0)
        self.assertEqual(sesion["vod"]["platform"], "twitch")

    def test_finish_and_edit_find_it(self):
        job, _ = self._phase1()
        self._restart(job)
        from app import create_app
        c = create_app().test_client()
        # /finish starts (it fails later without a VOD: what matters is that the session is there)
        with mock.patch.object(processor, "finish"):
            self.assertEqual(c.post("/api/competitivo/finish", data={"job_id": job}).status_code, 202)
        self._restart(job)
        r = c.post("/api/competitivo/edit", data={"job_id": job, "style": "x"})
        self.assertEqual(r.status_code, 400)                                       # found; bad style
        self.assertEqual(c.get(f"/api/competitivo/status/nada").status_code, 404)

    def test_a_render_cut_by_the_restart(self):
        job, _ = self._phase1()
        job_manager.update_job(job, status="processing", stage="Clip 2/3: renderizando…")
        processor.save_session(job)
        self._restart(job)
        processor.get_session(job)
        j = job_manager.get_job(job)
        self.assertEqual((j["status"], j["stage"]), ("done", "esperando_ancla"))
        self.assertIn("/finish", j["aviso"])


if __name__ == "__main__":
    unittest.main()
