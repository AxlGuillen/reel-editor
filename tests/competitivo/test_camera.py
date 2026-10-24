"""The scoreboard's clock, the camera that follows the action (or the full
view) and the storyboard (offline: real clock crops, synthetic video)."""
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import numpy as np

import config
from core import ffmpeg_runner
from modules.competitivo import camera, clock, layout, storyboard
from modules.competitivo import edit as E

CLOCKS = os.path.join(os.path.dirname(__file__), "fixtures", "clock")
T0 = datetime(2026, 10, 23, 17, 26, 46, tzinfo=timezone.utc)


def _png(name):
    import cv2
    return cv2.imread(os.path.join(CLOCKS, name), 0).astype(np.float32)


class Clock(unittest.TestCase):
    def test_reads_the_scoreboard(self):
        # real crops of the official scoreboard (IG-GEN, Twitch 23/10)
        for name in ("07-54", "10-18", "14-33", "21-14", "24-33"):
            m, s = (int(x) for x in name.split("-"))
            self.assertEqual(clock.read(_png(f"{name}.png")), m * 60 + s, name)
        self.assertIsNone(clock.read(_png("none.png")))                       # a replay: no clock
        self.assertIsNone(clock.read(None))

    def test_feed_time_and_clock_with_pauses(self):
        pauses = [(T0 + timedelta(seconds=300), T0 + timedelta(seconds=420))]   # a 2 min tech pause at 5:00
        self.assertEqual(clock.to_clock(T0 + timedelta(seconds=100), T0, pauses), 100)
        self.assertEqual(clock.to_clock(T0 + timedelta(seconds=360), T0, pauses), 300)      # paused
        self.assertEqual(clock.to_clock(T0 + timedelta(seconds=600), T0, pauses), 480)
        self.assertEqual(clock.from_clock(480, T0, pauses), T0 + timedelta(seconds=600))
        self.assertEqual((clock.fmt(1301), clock.parse("21:41")), ("21:41", 1301))
        with self.assertRaises(ValueError):
            clock.parse("21:61")

    def test_calibrate_ignores_a_misreading(self):
        readings = {10.0: 100.0, 13.0: 103.0, 16.0: 106.0, 19.0: 999.0, 22.0: 112.0}
        with mock.patch.object(clock, "clock_at", side_effect=lambda p, t: readings[t]):
            self.assertAlmostEqual(clock.calibrate("x", list(readings)), -90.0)

    def test_titles_go_to_the_scoreboard_clock(self):
        from modules.competitivo import processor
        clips = [{"titulo": "GEN · pelea 6-1 · 28:28", "reloj": "28:28", "ultima": (T0 + timedelta(seconds=1382)).isoformat(),
                  "eventos": [[(T0 + timedelta(seconds=1371)).isoformat(), "der"]]}]
        processor._clock_titles(clips, T0, [])
        self.assertEqual((clips[0]["titulo"], clips[0]["reloj_feed"], clips[0]["kills_reloj"]),
                         ("GEN · pelea 6-1 · 23:02", "28:28", ["22:51"]))


class CameraPlan(unittest.TestCase):
    KILLS = [("1-1#1", 10.0), ("1-1#2", 14.0), ("1-1#3", 20.0)]
    TRACKS = {"1-1#1": {"u": 0.35, "v": 0.4, "sure": True}, "1-1#2": {"u": 0.5, "v": 0.5, "sure": False},
              "1-1#3": {"u": 0.8, "v": 0.6, "sure": True}}

    def test_auto(self):
        track, full, shots = camera.plan(self.KILLS, self.TRACKS, 1.3, "auto")
        self.assertEqual([s.mode for s in shots], ["zoom", "full", "zoom"])
        self.assertEqual(full, [(12.5, 15.2)])                                   # the unclear one
        xs = [x for _, x in track]
        self.assertEqual(track[0][1], layout.crop_x_for(0.35, 1.3))              # starts on the first
        self.assertEqual(track[-1][1], layout.crop_x_for(0.8, 1.3))              # and slides to the last
        self.assertEqual(xs, sorted(xs))                                         # smoothly, one way
        ts = [t for t, _ in track]
        self.assertEqual(ts, sorted(ts))
        # it arrives before the kill
        arrive = next(t for t, x in track if x == layout.crop_x_for(0.8, 1.3))
        self.assertLessEqual(arrive, 20.0 - camera.SLIDE_END + 1e-6)
        self.assertAlmostEqual(shots[0].aim[0], 0.5, delta=0.02)                 # the crop centers it
        # an action at the very edge cannot be centered: the crop stops at the zone's border
        _, _, edge = camera.plan([("1-1#1", 10.0)], {"1-1#1": {"u": 0.05, "v": 0.5, "sure": True}}, 1.3, "auto")
        self.assertLess(edge[0].aim[0], 0.2)

    def test_modes_and_overrides(self):
        _, full, shots = camera.plan(self.KILLS, self.TRACKS, 1.3, "full", span=(0, 40))
        self.assertEqual((full, {s.mode for s in shots}), ([(0, 40)], {"full"}))
        _, full, shots = camera.plan(self.KILLS, self.TRACKS, 1.3, "zoom")
        self.assertEqual((full, [s.mode for s in shots]), ([], ["zoom"] * 3))
        clocks = {"1-1#1": 1301.0, "1-1#2": 1305.0, "1-1#3": 1400.0}
        ov = camera.overrides_from([{"clock": "21:41", "mode": "full"}, {"kill": "1-1#2", "focus": [0.9, 0.3]}], clocks)
        self.assertEqual(ov, {"1-1#1": {"mode": "full"}, "1-1#2": {"focus": [0.9, 0.3]}})
        _, full, shots = camera.plan(self.KILLS, self.TRACKS, 1.3, "auto", ov)
        self.assertEqual([(s.mode, s.by) for s in shots], [("full", "override"), ("zoom", "override"), ("zoom", "auto")])
        self.assertEqual((shots[1].u, shots[1].v), (0.9, 0.3))
        for bad in ([{"clock": "5:00", "mode": "full"}], [{"kill": "9-9#1", "mode": "full"}]):
            with self.assertRaises(ValueError):
                camera.overrides_from(bad, clocks)

    def test_no_zoom_on_a_full_kill(self):
        clips = [E.EditClip("1-1", "a.mp4", 0.0, 20.0,
                            [E.EditKill(8.0, "izq", "A", "Ahri", id="1-1#1", mode="full"),
                             E.EditKill(9.1, "izq", "B", "Ahri", id="1-1#2", focus=(0.3, 0.6))], score=90, closes=True)]
        from tests.competitivo.test_edit import _beatmap
        res = E.plan(clips, _beatmap(), E.EditOptions(structure="acts", build_fights=0, recipes=("ramp",)))
        k1, k2 = res.kills[-2:]
        zooms = [e for e in res.edl.events if e.kind in ("zoom", "hold")]
        self.assertFalse([e for e in zooms if abs(e.t - k1["t"]) < 1e-6])
        z2 = [e for e in zooms if abs(e.t - k2["t"]) < 1e-6]
        self.assertEqual([(e.attack, e.x, e.y) for e in z2], [(0.5, 0.3, 0.6)])     # 0.5 s before, aimed
        self.assertEqual((k1["mode"], k1["id"]), ("full", "1-1#1"))


def _video(path, graph, dur=3.0, size="1920x1080", rate=10):
    subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error", "-f", "lavfi", "-t", str(dur), "-i",
                    f"color=c=0x2E6B3A:s={size}:r={rate}", "-f", "lavfi", "-t", str(dur), "-i",
                    f"color=c=white:s=90x90:r={rate}", "-filter_complex", graph, "-map", "[v]",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", path], check=True)
    return path


class FollowTheAction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reelforge-cam-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finds_the_fight_while_the_camera_pans(self):
        # the whole map slides (the observer pans) and a "champion" moves on it at x≈1150
        src = _video(os.path.join(self.tmp, "pan.mp4"),
                     "[0:v]noise=alls=60,crop=1920:1080:0:0[g0];"
                     "[g0]scale=2400:1350,crop=1920:1080:'200+120*t':100[g];"
                     "[g][1:v]overlay=x='1130+80*sin(9*t)':y='420+60*cos(7*t)'[v]")
        tr = layout.action_track(src, 2.5)
        self.assertTrue(tr["sure"], tr)
        self.assertAlmostEqual(tr["u"], (1150 - layout.JUEGO[0]) / layout.JUEGO[2], delta=0.08)

    def test_the_crop_follows_its_track(self):
        # a white square at x≈500 and another at x≈1000: the crop slides from one to the other
        src = _video(os.path.join(self.tmp, "two.mp4"),
                     "[0:v]drawbox=x=480:y=500:w=40:h=40:color=white:t=fill,"
                     "drawbox=x=980:y=500:w=40:h=40:color=white:t=fill[v]", dur=4.0, rate=30)
        out = os.path.join(self.tmp, "out.mp4")
        track = [(0.0, layout.crop_x_for(500 / 1475, 1.3)), (1.0, layout.crop_x_for(500 / 1475, 1.3)),
                 (2.5, layout.crop_x_for(1000 / 1475, 1.3))]
        ffmpeg_runner.capture(layout.build_command(src, out, 0.0, 4.0, layout.CAMARA_Y_DEFECTO, color="off",
                                                   crop_track=track))

        def white_x(t):
            raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-ss", str(t), "-i", out, "-frames:v", "1",
                                         "-vf", f"crop=1080:{layout.JUEGO_H}:0:{layout.ARRIBA_H},format=gray",
                                         "-f", "rawvideo", "-"])
            img = np.frombuffer(raw, np.uint8).reshape(layout.JUEGO_H, 1080)
            xs = np.nonzero(img.max(axis=0) > 200)[0]
            return float(xs.mean()) if len(xs) else None
        self.assertAlmostEqual(white_x(0.5), 540, delta=40)                      # the left square, centered
        self.assertAlmostEqual(white_x(3.5), 540, delta=40)                      # then the right one

    def test_storyboard(self):
        src = _video(os.path.join(self.tmp, "s.mp4"), "[0:v]null[v]", dur=3.0)
        shots = [{"n": 1, "id": "1-1#1", "clip": "1-1", "clock": "21:41", "t": 12.3, "recipe": "ramp", "mode": "zoom",
                  "src_t": 1.0, "span": src, "track": [(0.0, 400.0)],
                  "shot": {"by": "auto", "sure": True, "u": 0.4, "v": 0.5}},
                 {"n": 2, "id": "1-1#2", "clip": "1-1", "clock": "21:44", "t": 14.0, "recipe": "hard_cut",
                  "mode": "full", "src_t": 2.0, "span": src, "track": None, "shot": {"by": "override"}}]
        sb = storyboard.build("job", shots, os.path.join(self.tmp, "sb"), 1.3)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "sb", "storyboard.jpg")))
        self.assertEqual([s["thumb"] for s in sb["shots"]],
                         ["/api/competitivo/storyboard/job/1", "/api/competitivo/storyboard/job/2"])
        self.assertEqual(sb["shots"][1]["mode"], "full")


if __name__ == "__main__":
    unittest.main()


class PanOnly(unittest.TestCase):
    def test_a_pan_without_a_fight_is_not_sure(self):
        tmp = tempfile.mkdtemp(prefix="reelforge-cam-")
        try:
            src = _video(os.path.join(tmp, "p.mp4"), "[0:v]noise=alls=60,scale=2400:1350,"
                                                     "crop=1920:1080:'200+120*t':100[v]")
            self.assertFalse(layout.action_track(src, 2.5)["sure"])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class StoryboardJob(unittest.TestCase):
    """/edit with storyboard=1: it pauses with a thumbnail per kill; /edit/finish
    with kill_overrides re-plans the camera (same timing) and renders."""

    def setUp(self):
        from tests.competitivo import test_edit
        self.fj = test_edit.FullJob()
        test_edit.FullJob.setUp(self.fj)

    def tearDown(self):
        from tests.competitivo import test_edit
        test_edit.FullJob.tearDown(self.fj)

    def test_pause_correct_and_render(self):
        from core import job_manager
        from modules.competitivo import processor
        from modules.competitivo.schema import EditParams
        from tests import synthetic as S
        params = self.fj._session()
        processor._SESIONES["phase1"]["clips"][1]["kills_reloj"] = ["22:51", "22:52", "22:54"]
        music = S.song(os.path.join(self.fj.tmp, "m.wav"), bpm=120, dur=60, loud_from=24)
        ep = EditParams.from_form({"storyboard": "1", "voice_subs": "0"}, params)
        sure = {"u": 0.6, "v": 0.4, "sure": True, "spread": 0.1, "motion": 30}
        job = job_manager.create_job("x", "")
        with mock.patch.object(processor.layout, "action_track", return_value=sure):
            processor.edit(job, "phase1", ep, music)
        j = job_manager.get_job(job)
        self.assertEqual((j["status"], j["stage"], j["finished"]), ("done", "esperando_revision", False), j["error"])
        sb = j["storyboard"]
        self.assertTrue(os.path.isfile(os.path.join(sb["folder"], "storyboard.jpg")))
        self.assertEqual({s["mode"] for s in sb["shots"]}, {"zoom"})
        clocks = [s["clock"] for s in sb["shots"] if s["clip"] == "1-2"]
        self.assertIn("22:51", clocks)
        from app import create_app
        c = create_app().test_client()
        self.assertEqual(c.get(f"/api/competitivo/storyboard/{job}").status_code, 200)
        self.assertEqual(c.get(f"/api/competitivo/storyboard/{job}/1").status_code, 200)
        r = c.post("/api/competitivo/edit/finish", data={"job_id": job, "kill_overrides": '[{"clock": "9:99"}]'})
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/competitivo/edit/finish", data={"job_id": job, "kill_overrides": '[{"clock": "1:00", "mode": "full"}]'})
        self.assertEqual(r.status_code, 400)                                     # no kill at 1:00
        before = job_manager.get_job(job)["plan"]["kills"]
        processor.edit_finish(job, None, [{"clock": "22:51", "mode": "full"}])
        j = job_manager.get_job(job)
        self.assertEqual((j["status"], j["finished"]), ("done", True), j["error"])
        after = j["resultado"]["plan"]["kills"]
        self.assertEqual([k["t"] for k in after], [k["t"] for k in before])     # same timing
        changed = [k for k in after if k["clock"] == "22:51"]
        self.assertEqual({k["mode"] for k in changed}, {"full"})
        self.assertTrue(os.path.isfile(j["resultado"]["video"]))


class KillFrames(unittest.TestCase):
    def test_on_the_scoreboard_change(self):
        from modules.competitivo import processor
        seen = [(10.4, "izq"), (12.0, "der"), (30.0, "izq")]
        expected = [(10.0, "izq"), (11.5, "der"), (20.0, "izq")]
        # shift 0.1: the 1st and 2nd are where the counter went up, the 3rd (not seen) the feed's − lead
        self.assertEqual(processor.kill_frames(seen, expected, 0.1, 0.25), [10.4, 12.0, 19.85])
        self.assertEqual(processor.kill_frames(seen, [(10.0, "der")], 0.0, 0.25), [9.75])     # wrong side
