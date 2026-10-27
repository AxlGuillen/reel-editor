"""Presets, metrics and the calibration set (offline: synthetic videos, temp folders)."""
import dataclasses
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import config
from app import app
from modules.competitivo import calibration, metrics, presets
from modules.competitivo.schema import CompetitivoParams, EditParams
from tests import synthetic as S

BASE = CompetitivoParams.from_form({"game_ids": "115660588583171787", "vod_url": "https://youtu.be/x"})


class Presets(unittest.TestCase):
    def test_without_preset_the_form_is_as_is(self):
        self.assertEqual(presets.apply({"zoom": "1.2"}, "edit"), ({"zoom": "1.2"}, ""))

    def test_the_preset_fills_and_the_request_wins(self):
        form, name = presets.apply({"preset": "edit_estandar", "max_duration_s": "45", "text": ""}, "edit")
        self.assertEqual(name, "edit_estandar")
        self.assertEqual(form["max_duration_s"], "45")        # explicit
        self.assertEqual(form["text"], "off")                 # empty does not erase the preset's
        self.assertEqual(form["storyboard"], "1")
        ep = EditParams.from_form(form, BASE)
        self.assertEqual((ep.structure, ep.storyboard, ep.kill_before_s, ep.max_duration_s), ("acts", True, 3.0, 45.0))

    def test_unknown_or_wrong_endpoint(self):
        with self.assertRaisesRegex(ValueError, "No existe el preset"):
            presets.apply({"preset": "nada"}, "edit")
        with self.assertRaisesRegex(ValueError, "es para edit"):
            presets.apply({"preset": "edit_corto"}, "process")

    def test_every_preset_is_valid_for_its_endpoints(self):
        """Every key is a field of its endpoint's schema (from_form ignores unknown
        ones in silence) and the schema accepts the values."""
        fields = {"edit": {f.name for f in dataclasses.fields(EditParams)},
                  "process": {f.name for f in dataclasses.fields(CompetitivoParams)},
                  "daily": {"zoom", "pre_s", "post_s", "zoom_mode", "titulo", "watermark", "montage_name", "picks"}}
        self.assertTrue(presets.listing())
        for p in presets.listing():
            for endpoint in p["endpoints"]:
                self.assertFalse(set(p["params"]) - fields[endpoint], (p["name"], endpoint))
                if endpoint == "edit":
                    EditParams.from_form(p["params"], BASE)
                elif endpoint == "process":
                    CompetitivoParams.from_form({**p["params"], "game_ids": "115660588583171787",
                                                 "vod_url": "https://youtu.be/x"})

    def test_route(self):
        with app.test_client() as c:
            names = [p["name"] for p in c.get("/api/competitivo/presets").get_json()["presets"]]
            self.assertIn("edit_estandar", names)
            r = c.post("/api/competitivo/process", data={"preset": "edit_corto", "game_ids": "1" * 18,
                                                         "vod_url": "https://youtu.be/x"})
            self.assertEqual(r.status_code, 400)
            self.assertIn("es para edit", r.get_json()["error"])


class Metrics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(metrics, "FILE", os.path.join(self.tmp.name, "m", "jobs.jsonl"))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_run_writes_one_record(self):
        run = metrics.Run("edit", "j1", preset="edit_estandar")
        run.mark("spans")
        run.set(kills=4, kills_sure=3, kills_full=1, overrides=None)
        rec = run.finish("ok")
        self.assertIsNone(run.finish("error"))                 # only once
        self.assertEqual((rec["kind"], rec["status"], rec["kills"]), ("edit", "ok", 4))
        self.assertNotIn("overrides", rec)                     # None is not a value
        self.assertIn("spans", rec["stages"])
        self.assertEqual(len(metrics.load()), 1)

    def test_summary(self):
        now = datetime.now(timezone.utc)
        rows = [
            {"kind": "fase1", "status": "ok", "seconds": 60, "anchors": ["alta", "media"], "clock_calibrated": [True, False]},
            {"kind": "fase1", "status": "error", "seconds": 5, "error": "x"},
            {"kind": "edit", "status": "paused", "seconds": 90, "kills": 9, "kills_sure": 9},   # counted by its render
            {"kind": "edit_render", "status": "ok", "seconds": 40, "kills": 4, "kills_sure": 3, "kills_full": 1,
             "overrides": 2, "retries": 1, "video_s": 50, "nvenc_fallbacks": 1, "preset": "edit_estandar"},
            {"kind": "resumen", "status": "ok", "seconds": 50, "kills": 4, "kills_sure": 1, "skipped": 1,
             "warning": True},
        ]
        for r in rows:
            metrics.write({**r, "job_id": "x", "at": now.isoformat()})
        metrics.write({"kind": "fase1", "job_id": "old", "status": "ok", "seconds": 1,
                       "at": (now - timedelta(days=30)).isoformat()})
        s = metrics.summary(metrics.load(7))
        self.assertEqual(s["jobs"], 5)
        self.assertEqual(s["kinds"]["fase1"], {"jobs": 2, "ok": 1, "failed": 1, "median_s": 60, "median_video_s": None})
        self.assertEqual((s["anchors_high_pct"], s["clock_calibrated_pct"]), (50.0, 50.0))
        self.assertEqual((s["kills"], s["kills_sure_pct"], s["kills_full_pct"]), (8, 50.0, 12.5))
        self.assertEqual((s["overrides_per_edit"], s["retries"], s["nvenc_fallbacks"]), (2.0, 1, 1))
        self.assertEqual((s["skipped_spans"], s["warnings"], s["failed"]), (1, 1, 1))
        self.assertEqual(s["presets"], {"edit_estandar": 1})
        self.assertEqual(len(metrics.load()), 6)

    def test_a_broken_file_never_breaks(self):
        os.makedirs(os.path.dirname(metrics.FILE), exist_ok=True)
        with open(metrics.FILE, "w", encoding="utf-8") as fh:
            fh.write('{"kind": "fase1", "status": "ok", "seconds": 1, "at": "2026-10-24T00:00:00+00:00"}\nbasura\n')
        self.assertEqual(len(metrics.load()), 1)

    def test_route(self):
        metrics.write({"kind": "resumen", "job_id": "r", "status": "ok", "seconds": 3,
                       "at": datetime.now(timezone.utc).isoformat()})
        with mock.patch.object(config, "CALIBRATION_FOLDER", self.tmp.name), app.test_client() as c:
            data = c.get("/api/competitivo/metrics?days=7").get_json()
            self.assertEqual(data["summary"]["jobs"], 1)
            self.assertEqual(data["recent"][0]["kind"], "resumen")
            self.assertEqual(sorted(data["slots"]), ["analysis", "nvenc", "twitch", "whisper"])
            self.assertTrue(all({"capacity", "running", "waiting", "peak"} <= set(v) for v in data["slots"].values()))
            self.assertIsNone(data["calibration"])
            self.assertEqual(c.get("/api/competitivo/metrics?days=x").status_code, 400)

    def test_a_failed_phase1_is_recorded(self):
        from core import job_manager
        from modules.competitivo import processor
        job = job_manager.create_job("x", "")
        with mock.patch.object(processor.fuente, "info_vod", side_effect=RuntimeError("VOD roto")), \
                mock.patch.object(processor, "TRABAJO", self.tmp.name):
            processor.process(job, BASE)
        rec = metrics.load()[-1]
        self.assertEqual((rec["kind"], rec["status"], rec["error"]), ("fase1", "error", "VOD roto"))


class Calibration(unittest.TestCase):
    """The runner with a synthetic span: a case that passes, one that fails, a
    known issue, a missing file and --accept."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(config, "CALIBRATION_FOLDER", self.tmp.name),
                        mock.patch.object(metrics, "FILE", os.path.join(self.tmp.name, "jobs.jsonl"))]
        for p in self.patches:
            p.start()
        os.makedirs(os.path.join(self.tmp.name, "media"))
        S.shots_video(os.path.join(self.tmp.name, "media", "v.mp4"), [3.0], w=1920, h=1080)
        live = {"extractor_key": "Youtube", "live_status": "is_live", "release_timestamp": 1792746000}
        calibration.save_cases([
            {"id": "live", "check": "vod_info", "params": {"info": live}, "expect": {"error": "sigue en vivo"}},
            {"id": "wrong", "check": "vod_info", "params": {"info": live}, "expect": {"platform": "youtube"}},
            {"id": "known", "check": "vod_info", "params": {"info": live}, "expect": {"platform": "youtube"},
             "known_issue": "a propósito"},
            {"id": "nosplit", "check": "split", "file": "media/v.mp4", "expect": []},
            {"id": "gone", "check": "split", "file": "media/nada.mp4", "expect": []},
        ])

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_run_and_report(self):
        seen = []
        report = calibration.run(progress=lambda k, n, cid: seen.append(cid))
        by_id = {r["id"]: r["status"] for r in report["results"]}
        self.assertEqual(by_id, {"live": "ok", "wrong": "fail", "known": "known", "nosplit": "ok", "gone": "missing"})
        self.assertFalse(report["passed"])
        self.assertEqual(len(seen), 5)
        self.assertEqual(calibration.last_report()["fail"], 1)
        self.assertEqual(metrics.load()[-1]["kind"], "calibracion")
        # Only some cases.
        self.assertTrue(calibration.run(["live", "known", "nosplit"])["passed"])

    def test_accept(self):
        self.assertEqual(calibration.accept(["wrong"]), ["wrong"])
        self.assertEqual(calibration.run_case(next(c for c in calibration.load_cases() if c["id"] == "wrong"))["status"],
                         "ok")

    def test_comparisons(self):
        self.assertTrue(calibration._same_ranges([[0.0, 32.9]], [[0.3, 32.5]], 1.0))
        self.assertFalse(calibration._same_ranges([], [[0.3, 32.5]], 1.0))
        self.assertTrue(calibration._c_kill_changes([[12.4, "izq"]], [[12.5, "izq"]], 0.3))
        self.assertFalse(calibration._c_kill_changes([[12.4, "der"]], [[12.5, "izq"]], 0.3))
        self.assertTrue(calibration._c_voice_peaks({"a": [[11.7, 13.1]], "b": []}, {"a": [[11.8, 13.0]], "b": []}, 1))
        self.assertFalse(calibration._c_voice_peaks({"a": [], "b": []}, {"a": [[11.8, 13.0]], "b": []}, 1))
        got = [{"kind": "robo de buff", "clock_s": 109.0, "players": ["Josedeodo", "Shad0w"]}]
        self.assertTrue(calibration._c_moments(got, [{"kind": "robo de buff", "clock_s": 108.6,
                                                      "players": ["Josedeodo"]}], 3))
        self.assertFalse(calibration._c_moments(got, [{"kind": "robo de buff", "clock_s": 140, "players": []}], 3))

    def test_routes(self):
        with app.test_client() as c:
            data = c.get("/api/competitivo/calibration").get_json()
            self.assertEqual(len(data["cases"]), 5)
            self.assertIsNone(data["report"])
            job = c.post("/api/competitivo/calibration", data={"ids": "live,nosplit"}).get_json()["job_id"]
            for _ in range(100):
                st = c.get(f"/api/competitivo/status/{job}").get_json()
                if st["status"] in ("done", "error"):
                    break
                time.sleep(0.1)
            self.assertEqual(st["status"], "done")
            self.assertTrue(st["resultado"]["passed"])
            self.assertEqual(c.get("/api/competitivo/calibration").get_json()["report"]["cases"], 2)

    def test_no_cases(self):
        with mock.patch.object(config, "CALIBRATION_FOLDER", os.path.join(self.tmp.name, "vacía")), \
                app.test_client() as c:
            self.assertEqual(c.post("/api/competitivo/calibration").status_code, 400)


if __name__ == "__main__":
    unittest.main()
