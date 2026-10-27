"""/finish with the analysis of a clip running while the one before is rendered, the
anchors already measured, the per-clip breakdown of the metrics and the batched
Whisper's sentences (simulated VOD: synthetic 1920x1080 60 fps spans)."""
import json
import os
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

import config
from core import ffmpeg_runner, job_manager, scheduler
from modules.competitivo import fuente, kills, layout, metrics, processor
from modules.competitivo.schema import CompetitivoParams, FinishParams
from tests.competitivo import test_edit


class FinishPipeline(unittest.TestCase):
    def setUp(self):
        self.fj = test_edit.FullJob()
        self.fj.setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(config, "COMPETITIVO_FOLDER", os.path.join(self.tmp.name, "out")),
                        mock.patch.object(metrics, "FILE", os.path.join(self.tmp.name, "jobs.jsonl"))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.fj.tearDown()
        self.tmp.cleanup()

    def _finish(self, span=None, **form):
        params = self.fj._session()
        sesion = processor._SESIONES["phase1"]
        sesion["partidas"][0].update(inicio_utc=sesion["partidas"][0]["inicio_utc"])
        fp = FinishParams.from_form({"intro_modo": "off", "carpeta_dia": "Día 1", "partido": "KC vs C9",
                                     "titulo": "x", **form}, params)
        job = job_manager.create_job("x", "")
        # finish() reads its session by the job id
        processor._SESIONES[job] = sesion
        with mock.patch.object(processor.fuente, "bajar_tramo", side_effect=span or self.fj._span):
            processor.finish(job, fp)
        processor._SESIONES.pop(job, None)
        return job, job_manager.get_job(job)

    def test_two_clips_one_decode_each_and_the_breakdown(self):
        scans = []
        real_scan = layout.Scan

        def spy(*a, **k):
            scans.append(a[1] if len(a) > 1 else k.get("want"))
            return real_scan(*a, **k)
        with mock.patch.object(processor.layout, "Scan", side_effect=spy):
            job, j = self._finish()
        self.assertEqual(j["status"], "done", j["error"])
        res = j["resultado"]
        self.assertEqual([c["id"] for c in res["clips"]], ["1-1", "1-2"])           # in order
        self.assertEqual(len(scans), 2)                                              # ONE decode per clip
        self.assertTrue(all(scans))
        self.assertTrue(os.path.isfile(res["video"]))
        rec = [r for r in metrics.load() if r["kind"] == "resumen"][-1]
        self.assertEqual([c["id"] for c in rec["clips_detail"]], ["1-1", "1-2"])
        for c in rec["clips_detail"]:
            self.assertEqual({"id", "video_s", "download_s", "wait_s", "queue_s", "analysis_s", "render_s"}, set(c))
            self.assertGreater(c["render_s"], 0)
        self.assertIn("concurrent_peak", rec)
        self.assertEqual(rec["clips"], 2)

    def test_a_clip_without_video_is_skipped_and_the_rest_goes_on(self):
        def span(url, a, b, destination, job_id=None):
            if destination.endswith("tramo_01"):
                raise fuente.SpanWithoutVideo("sin video")
            return self.fj._span(url, a, b, destination, job_id)
        job, j = self._finish(span=span)
        self.assertEqual(j["status"], "done", j["error"])
        self.assertEqual([c["id"] for c in j["resultado"]["clips"]], ["1-2"])
        self.assertEqual(j["resultado"]["skipped_clips"], ["pelea 1"])
        self.assertIn("pelea 1", j["aviso"])

    def test_a_failing_analysis_fails_the_job_and_leaves_no_files(self):
        with mock.patch.object(processor.layout, "analizar", side_effect=RuntimeError("se rompió")):
            job, j = self._finish()
        self.assertEqual(j["status"], "error")
        self.assertIn("se rompió", j["error"])
        sesion_dir = os.path.join(self.fj.tmp, "work", "tramos")
        self.assertFalse(os.path.exists(sesion_dir))

    def test_the_analysis_waits_for_its_slot(self):
        scheduler.reset()
        with mock.patch.object(config, "SLOTS", {**config.SLOTS, "analysis": 1}):
            scheduler.reset()
            res = scheduler.get("analysis")
            res.acquire()                       # somebody else's analysis
            import threading
            import time

            def release_when_someone_waits():
                while not res.snapshot()["waiting"]:
                    time.sleep(0.02)
                time.sleep(0.5)
                res.release()
            threading.Thread(target=release_when_someone_waits, daemon=True).start()
            job, j = self._finish()
        scheduler.reset()
        self.assertEqual(j["status"], "done", j["error"])
        rec = [r for r in metrics.load() if r["kind"] == "resumen"][-1]
        self.assertGreaterEqual(rec["waits"]["analysis"], 0.3)


class AnchorCache(unittest.TestCase):
    FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kc_c9_g1.json")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(config, "ANCHORS_FOLDER", os.path.join(self.tmp.name, "anchors"))
        self.patch.start()
        self.partida = kills.partida_de_fixture(self.FIXTURE)
        self.peleas = kills.detectar_peleas(self.partida, CompetitivoParams.from_form(
            {"game_ids": self.partida.game_id, "vod_url": "https://www.twitch.tv/videos/2908607306"}).reglas)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    @staticmethod
    def _frame(src, t, out):
        with open(out, "wb") as fh:                 # the control frame (a synthetic span has no JPEG-able color)
            fh.write(b"jpg")

    def _ancla(self, folder, **patches):
        calls = []

        def span(url, a, b, destination, job_id=None):
            calls.append((a, b))
            import shutil
            src = os.path.join(self.tmp.name, "span.mp4")
            if not os.path.exists(src):
                from tests import synthetic as S
                S.shots_video(src, [3.0], w=1920, h=1080, fps=60)
            shutil.copy(src, destination + ".mp4")
            return destination + ".mp4"
        first_kill = self.partida.frames[1].ts
        with mock.patch.object(processor.fuente, "bajar_tramo", side_effect=span), \
                mock.patch.object(processor.layout, "estimar_desfase", return_value=(2.0, 3)), \
                mock.patch.object(processor.clock, "calibrate", return_value=10.0), \
                mock.patch.object(processor, "_captura", side_effect=self._frame), \
                mock.patch.object(processor.layout, "cambios_kills", return_value=[]):
            res = processor._ancla("https://www.twitch.tv/videos/2908607306", self.partida.t0, self.partida,
                                   self.peleas, folder, 1, offset=44.0)
        return res, calls, first_kill

    def test_the_second_phase_1_of_a_game_does_not_download(self):
        a, b = os.path.join(self.tmp.name, "a"), os.path.join(self.tmp.name, "b")
        os.makedirs(a)
        os.makedirs(b)
        first, calls1, _ = self._ancla(a)
        self.assertEqual((first["confianza"], len(calls1)), ("alta", 1))
        second, calls2, _ = self._ancla(b)
        self.assertEqual(len(calls2), 0)                                   # nothing downloaded
        self.assertTrue(second["cached"])
        self.assertEqual({k: v for k, v in second.items() if k != "cached"},
                         {k: v for k, v in first.items() if k != "cached"})
        self.assertTrue(os.path.isfile(os.path.join(b, "ancla_1.jpg")))      # its control frame came along

    def test_a_dubious_anchor_is_not_kept(self):
        a = os.path.join(self.tmp.name, "a")
        os.makedirs(a)
        with mock.patch.object(processor, "confidence", return_value=("media", False)):
            res, calls, _ = self._ancla(a)
        self.assertEqual(res["confianza"], "media")
        self.assertFalse(os.path.isdir(config.ANCHORS_FOLDER) and os.listdir(config.ANCHORS_FOLDER))

    def test_another_first_kill_is_another_game_state(self):
        a, b = os.path.join(self.tmp.name, "a"), os.path.join(self.tmp.name, "b")
        os.makedirs(a)
        os.makedirs(b)
        self._ancla(a)
        base = processor._anchor_path("https://www.twitch.tv/videos/2908607306", self.partida.game_id)
        with open(base + ".json", encoding="utf-8") as fh:
            data = json.load(fh)
        data["primera_kill_utc"] = (self.partida.t0 - timedelta(seconds=999)).isoformat()      # the feed changed
        with open(base + ".json", "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        _, calls, _ = self._ancla(b)
        self.assertEqual(len(calls), 1)                                    # measured again


class BatchedSentences(unittest.TestCase):
    def test_long_segments_are_cut_in_sentences(self):
        from modules.subtitles import processor as sp
        words = []
        t = 0.0
        for i in range(60):
            words.append({"word": "palabra" + ("." if i % 12 == 11 else ""), "start": round(t, 2), "end": round(t + 0.4, 2)})
            t += 0.5
        seg = {"id": 0, "start": 0.0, "end": words[-1]["end"], "text": " ".join(w["word"] for w in words), "words": words}
        out = sp._split_long([seg])
        self.assertGreater(len(out), 3)
        self.assertEqual([s["id"] for s in out], list(range(len(out))))
        self.assertEqual([w for s in out for w in s["words"]], words)               # nothing lost, same order
        self.assertTrue(all(s["end"] - s["start"] <= 9.5 for s in out))
        self.assertTrue(all(s["start"] < s["end"] for s in out))
        short = {"id": 0, "start": 0.0, "end": 3.0, "text": "hola", "words": words[:2]}
        self.assertEqual(sp._split_long([short]), [short])


class MetricsConcurrency(unittest.TestCase):
    def test_runs_that_overlap_count_each_other(self):
        with mock.patch.object(metrics, "FILE", os.path.join(tempfile.mkdtemp(), "m.jsonl")):
            a = metrics.Run("fase1", "ja")
            b = metrics.Run("resumen", "jb")
            c = metrics.Run("edit", "jc")
            c.finish("ok")
            b.finish("ok")
            d = metrics.Run("fase1", "jd")
            a.finish("ok")
            d.finish("ok")
            rows = {r["job_id"]: r for r in metrics.load()}
        self.assertEqual((rows["ja"]["concurrent_at_start"], rows["ja"]["concurrent_peak"]), (0, 3))
        self.assertEqual((rows["jb"]["concurrent_at_start"], rows["jb"]["concurrent_peak"]), (1, 3))
        self.assertEqual(rows["jc"]["concurrent_at_start"], 2)
        self.assertEqual((rows["jd"]["concurrent_at_start"], rows["jd"]["concurrent_peak"]), (1, 2))

    def test_summary_has_the_cost_of_sharing(self):
        rows = [{"kind": "resumen", "status": "ok", "seconds": 40, "clips": 2, "concurrent_peak": 1, "at": "2026-10-27"},
                {"kind": "resumen", "status": "ok", "seconds": 80, "clips": 2, "concurrent_peak": 3,
                 "waits": {"analysis": 5.0, "nvenc": 7.5}, "at": "2026-10-27"}]
        s = metrics.summary(rows)
        self.assertEqual((s["s_per_clip"], s["s_per_clip_alone"], s["queue_wait_s"], s["concurrent_median"]),
                         (30.0, 20.0, 12.5, 2.0))


if __name__ == "__main__":
    unittest.main()
