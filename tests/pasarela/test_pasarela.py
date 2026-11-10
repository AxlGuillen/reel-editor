"""Tests of the pasarela: the marked script against Whisper, the pauses a clip
puts in the voice, the Insights highlight windows (and that its database is
never written), the band pieces, the geometry and the report's contract
(with a whole run on synthetic files and a mocked transcript)."""
import json
import os
import shutil
import sqlite3
import subprocess
import unittest
from unittest import mock

import config
from core import ffmpeg_runner, job_manager
from modules.pasarela import insights, plan, processor, render
from modules.pasarela.schema import PasarelaParams
from tests import synthetic as S


def segments(text: str, step: float = 0.4, gaps: dict[int, float] | None = None) -> list[dict]:
    """A transcript: one word every `step` s (0.3 s long); `gaps` adds silence before word i."""
    words, t = [], 0.0
    for i, w in enumerate(text.split()):
        t += (gaps or {}).get(i, 0.0)
        words.append({"word": w, "start": round(t, 3), "end": round(t + 0.3, 3)})
        t += step
    return [{"id": 0, "start": 0.0, "end": words[-1]["end"], "text": text, "words": words}]


MANIFEST = [
    {"id": "portada", "archivo": "portada.webm", "palabras": []},
    {"id": "dos", "archivo": "dos.webm", "palabras": []},
    {"id": "tres", "archivo": "tres.webm", "palabras": ["la tercera"]},
    {"id": "reposo", "archivo": "reposo.webm", "reposo": True},
    {"id": "vertical", "tipo": "clip", "archivo": "vertical.mp4", "despues": "dos"},
]
# What Whisper heard: "dieciséis" as "16", no accents, a word dropped.
SCRIPT = ("⟦lamina:portada⟧ Hola a todos hoy cumplió dieciséis años. ⟦lamina:dos⟧ Y el mapa dos fue "
          "otra cosa. {clip:ancho} Y ahora la tercera parte del video y se acabó.")
HEARD = "Hola a todos hoy cumplio 16 anos Y el mapa dos fue otra cosa Y ahora la tercera parte del video se acabo"


def infos(**modes):
    return {"ancho": plan.ClipInfo("ancho", 5.0, False, modes.get("ancho", "pausa")),
            "vertical": plan.ClipInfo("vertical", 3.0, True, modes.get("vertical", "pausa"), after="dos")}


class Alignment(unittest.TestCase):
    def test_slides_come_in_on_their_word(self):
        tl = plan.build(segments(HEARD), SCRIPT, MANIFEST, {}, voice_dur=9.0)
        by = {l["id"]: l for l in tl.layers}
        self.assertEqual(by["portada"]["start"], 0.0)               # the cover at 0
        self.assertAlmostEqual(by["dos"]["start"], 7 * 0.4 - plan.LEAD_S, places=3)   # «Y el mapa…»
        self.assertAlmostEqual(by["tres"]["start"], 16 * 0.4 - plan.LEAD_S, places=3)   # by its `palabras`
        self.assertGreater(tl.alignment["matched"], 0.8 * tl.alignment["script_words"])
        self.assertEqual(tl.unplaced, [])

    def test_a_slide_without_a_mark_or_words_is_reported(self):
        manifest = MANIFEST + [{"id": "suelta", "archivo": "suelta.webm", "palabras": []}]
        tl = plan.build(segments(HEARD), SCRIPT, manifest, {}, voice_dur=9.0)
        self.assertIn({"id": "suelta", "kind": "lamina", "reason": mock.ANY}, tl.unplaced)
        self.assertNotIn("suelta", [l["id"] for l in tl.layers])

    def test_slides_follow_montage_rules(self):
        tl = plan.build(segments(HEARD), SCRIPT, MANIFEST, {}, voice_dur=9.0)
        portada, dos = tl.layers[0], tl.layers[1]
        # Back to back: the previous one stays under the new one while it fades in.
        self.assertAlmostEqual(portada["end"], dos["start"] + plan.FADE_IN_S, places=3)
        self.assertEqual(portada["fade_out"], 0.0)
        self.assertEqual(tl.layers[-1]["end"], tl.duration)
        self.assertEqual(tl.layers[-1]["fade_out"], 0.0)            # the last one stays to the end

    def test_clip_marks_do_not_become_words(self):
        self.assertEqual(plan.clip_marks_as_slides("a {clip:x-1} b"), "a ⟦lamina:x-1⟧ b")


class Pauses(unittest.TestCase):
    def setUp(self):
        # A real silence before «Y ahora» (word 14): 0.5 s more.
        self.seg = segments(HEARD, gaps={14: 0.5})
        self.words = self.seg[0]["words"]

    def test_the_voice_pauses_in_the_middle_of_the_silence(self):
        tl = plan.build(self.seg, SCRIPT, MANIFEST, {"ancho": infos()["ancho"]}, voice_dur=9.5)
        gap = (self.words[13]["end"] + self.words[14]["start"]) / 2
        self.assertEqual(len(tl.pauses), 1)
        self.assertAlmostEqual(tl.pauses[0]["voice_at"], gap, places=3)
        self.assertAlmostEqual(tl.pauses[0]["dur"], 5.0 + 2 * plan.BREATH_S, places=3)
        clip = tl.clips[0]
        self.assertAlmostEqual(clip["start"], gap + plan.BREATH_S, places=3)
        self.assertAlmostEqual(clip["end"], clip["start"] + 5.0, places=3)

    def test_everything_after_the_pause_moves_later(self):
        alone = plan.build(self.seg, SCRIPT, MANIFEST, {}, voice_dur=9.5)
        paused = plan.build(self.seg, SCRIPT, MANIFEST, {"ancho": infos()["ancho"]}, voice_dur=9.5)
        d = 5.0 + 2 * plan.BREATH_S
        start = {l["id"]: l["start"] for l in alone.layers}
        moved = {l["id"]: l["start"] for l in paused.layers}
        self.assertAlmostEqual(moved["dos"], start["dos"], places=3)        # before the clip
        self.assertAlmostEqual(moved["tres"], start["tres"] + d, places=3)  # after it
        self.assertAlmostEqual(paused.duration, alone.duration + d, places=3)
        self.assertAlmostEqual(paused.duration, 9.5 + d + plan.TAIL_S, places=3)

    def test_the_voice_is_cut_at_the_pause(self):
        tl = plan.build(self.seg, SCRIPT, MANIFEST, {"ancho": infos()["ancho"]}, voice_dur=9.5)
        p, d = tl.pauses[0]["voice_at"], tl.pauses[0]["dur"]
        self.assertEqual(tl.voice_pieces, [{"from": 0.0, "to": round(p, 3), "at": 0.0},
                                           {"from": round(p, 3), "to": 9.5, "at": round(p + d, 3)}])
        self.assertAlmostEqual(tl.final(p + 0.01), p + 0.01 + d, places=3)
        self.assertAlmostEqual(tl.final(p - 0.01), p - 0.01, places=3)

    def test_the_slide_before_a_clip_ends_where_the_clip_starts(self):
        tl = plan.build(self.seg, SCRIPT, MANIFEST, {"ancho": infos()["ancho"]}, voice_dur=9.5)
        dos = next(l for l in tl.layers if l["id"] == "dos")
        clip = next(l for l in tl.layers if l["id"] == "ancho")
        self.assertAlmostEqual(dos["end"], clip["start"], places=3)  # not stretched under the clip
        self.assertEqual(dos["fade_out"], 0.0)
        self.assertAlmostEqual(clip["end"], clip["start"] + 5.0, places=3)

    def test_mode_encima_does_not_pause(self):
        tl = plan.build(self.seg, SCRIPT, MANIFEST, {"ancho": infos(ancho="encima")["ancho"]}, voice_dur=9.5)
        self.assertEqual(tl.pauses, [])
        self.assertAlmostEqual(tl.duration, 9.5 + plan.TAIL_S, places=3)
        self.assertNotIn("ancho", [l["id"] for l in tl.layers])
        self.assertEqual(tl.clips[0]["mode"], "encima")

    def test_a_clip_without_a_mark_goes_after_its_slide(self):
        tl = plan.build(self.seg, SCRIPT, MANIFEST, {"vertical": infos()["vertical"]}, voice_dur=9.5)
        # `despues: dos` → right before the next slide («la tercera», word 16).
        gap = (self.words[15]["end"] + self.words[16]["start"]) / 2
        self.assertAlmostEqual(tl.clips[0]["voice_at"], gap, places=3)
        self.assertTrue(tl.clips[0]["full"])

    def test_a_clip_with_no_place_is_reported(self):
        lost = {"x": plan.ClipInfo("x", 2.0, False)}
        tl = plan.build(self.seg, SCRIPT, MANIFEST, lost, voice_dur=9.5)
        self.assertEqual([u["id"] for u in tl.unplaced], ["x"])
        self.assertEqual(tl.clips, [])


class Windows(unittest.TestCase):
    def test_overlapping_windows_join(self):
        ev = [(100.0, "kill"), (104.0, "assist"), (200.0, "kill"), (200.001, "kill")]
        w = insights.merge_windows(ev, 8, 3)
        self.assertEqual(w, [{"start": 92.0, "end": 107.0, "events": ["kill", "assist"]},
                             {"start": 192.0, "end": 203.001, "events": ["kill", "kill"]}])

    def test_touching_windows_join_and_separate_ones_do_not(self):
        self.assertEqual(len(insights.merge_windows([(10, "kill"), (21, "kill")], 8, 3)), 1)    # 2–13, 13–24
        self.assertEqual(len(insights.merge_windows([(10, "kill"), (21.1, "kill")], 8, 3)), 2)

    def test_clipped_to_the_recording(self):
        w = insights.merge_windows([(2, "kill"), (99, "kill")], 8, 3, duration=100)
        self.assertEqual((w[0]["start"], w[-1]["end"]), (0.0, 100.0))

    def test_unsorted_events(self):
        w = insights.merge_windows([(50, "kill"), (10, "kill")], 1, 1)
        self.assertEqual([x["start"] for x in w], [9.0, 49.0])


class InsightsDatabase(unittest.TestCase):
    """A small copy of Insights Capture's schema, read through the module."""

    def setUp(self):
        self.dir = S.tmpdir("insights")
        self.db = os.path.join(self.dir, "db.sqlite3")
        self.rec = S.shots_video(os.path.join(self.dir, "League of Legends - 2026-11-01T13-32-04.mp4"), [2, 2])
        c = sqlite3.connect(self.db)
        c.executescript("""
            create table video (uuid text primary key, type text, created_at real, duration real,
                                file_path text, size integer, user_provided_name text);
            create table video_game_event (video_uuid text, timestamp real, occurred_at real,
                                           game_class_id integer, feature text, key text, value text);
            create table video_game_info_update (video_uuid text, timestamp real, occurred_at real,
                                                 game_class_id integer, feature text, category text,
                                                 key text, value text);""")
        c.execute("insert into video values ('u1','recording',1,4.0,?,1,null)", (self.rec,))
        c.execute("insert into video values ('u2','recording',2,9.0,'C:/no/existe.mp4',1,null)")
        for t, k in ((1.0, "kill"), (1.5, "assist"), (3.5, "death"), (3.9, "victory")):
            c.execute("insert into video_game_event values ('u1',?,0,0,'f',?,null)", (t, k))
        for k, v in (("champion", '"Jhin"'), ("kills", "8"), ("deaths", "8"), ("assists", "17"),
                     ("doubleKills", "1")):
            c.execute("insert into video_game_info_update values ('u1',0,0,0,'f','c',?,?)", (k, v))
        c.commit()
        c.close()

    def tearDown(self):
        S.remove(self.dir)

    def test_listing(self):
        rows = {r["uuid"]: r for r in insights.recordings(self.db)}
        self.assertEqual(rows["u1"]["campeon"], "Jhin")
        self.assertEqual(rows["u1"]["kda"], "8/8/17")
        self.assertEqual(rows["u1"]["resultado"], "victoria")
        self.assertEqual(rows["u1"]["multikills"], {"double": 1})
        self.assertTrue(rows["u1"]["existe"])
        self.assertFalse(rows["u2"]["existe"])

    def test_highlight_by_name_or_uuid(self):
        by_name = insights.plan_highlight("League of Legends - 2026-11-01T13-32-04", 0.5, 0.2,
                                          ["kill", "assist"], self.db)
        by_uuid = insights.plan_highlight("u1", 0.5, 0.2, ["kill", "assist"], self.db)
        self.assertEqual(by_name["windows"], by_uuid["windows"])
        self.assertEqual(by_name["windows"], [{"start": 0.5, "end": 1.7, "events": ["kill", "assist"]}])
        with self.assertRaises(ValueError):
            insights.plan_highlight("otra", 8, 3, ["kill"], self.db)
        with self.assertRaises(ValueError):                          # the file is gone
            insights.plan_highlight("u2", 8, 3, ["kill"], self.db)

    def _bytes(self) -> bytes:
        with open(self.db, "rb") as fh:
            return fh.read()

    def test_the_database_is_never_written(self):
        before = self._bytes()
        mtime = os.path.getmtime(self.db)
        insights.recordings(self.db)
        insights.plan_highlight("u1", 1, 1, ["kill"], self.db)
        self.assertEqual(self._bytes(), before)
        self.assertEqual(os.path.getmtime(self.db), mtime)
        self.assertEqual(sorted(os.listdir(self.dir)), sorted(["db.sqlite3", os.path.basename(self.rec)]))


class Band(unittest.TestCase):
    LAYERS = [
        {"id": "a", "kind": "lamina", "start": 0.0, "end": 2.25, "fade_in": 0.25, "fade_out": 0.0},
        {"id": "b", "kind": "lamina", "start": 2.0, "end": 3.0, "fade_in": 0.25, "fade_out": 0.2},
        {"id": "clip", "kind": "clip", "start": 3.5, "end": 5.0, "fade_in": 0.25, "fade_out": 0.0},
        {"id": "c", "kind": "lamina", "start": 5.2, "end": 7.0, "fade_in": 0.25, "fade_out": 0.0},
    ]

    def test_pieces_cover_the_video_frame_exact(self):
        pieces = render.band_pieces(self.LAYERS, 7.0, rest_dur=3.5)
        self.assertEqual(sum(p["frames"] for p in pieces), 7 * render.BAND_FPS)
        self.assertEqual([p["start"] for p in pieces], [0.0, 2.0, 5.2])     # clips are not band pieces

    def test_the_previous_slide_stays_under_the_new_one(self):
        b = render.band_pieces(self.LAYERS, 7.0, rest_dur=3.5)[1]
        self.assertEqual(b["prev"], {"id": "a", "ss": 2.0, "dur": 0.25})
        self.assertEqual(b["cur"]["fade_out"], 0.2)          # a gap follows: it fades to the rest slide
        self.assertAlmostEqual(b["cur"]["fade_out_at"], 0.8, places=3)

    def test_the_rest_slide_plays_once_then_stays(self):
        pieces = render.band_pieces(self.LAYERS, 7.0, rest_dur=3.5)
        self.assertEqual([p["rest_ss"] for p in pieces], [0.0, 2.0, round(3.5 - 1 / 30, 3)])


class Geometry(unittest.TestCase):
    def test_band_abajo(self):
        p = render.vertical_params("abajo")
        self.assertEqual(p.main_clip_offset, -10)
        self.assertTrue(p.hud)
        # The gameplay ends right where the band starts.
        fg_h = render.ffmpeg_even(1080 * 1.55 * 9 / 16)
        self.assertEqual((1920 - fg_h) // 2 + round(-0.10 * 1920) + fg_h, 1239)
        self.assertEqual(render.watermark_y("abajo"), 70)

    def test_band_arriba_mirrors_it(self):
        p = render.vertical_params("arriba")
        top = render.hud_plate_top(p, 1920, 1080) + round(p.main_clip_offset / 100 * 1920)
        self.assertGreaterEqual(top, 680)          # the HUD plate is not under the band
        self.assertLess(top, 690)
        fg_h = render.ffmpeg_even(1080 * 1.55 * 9 / 16)
        bottom = (1920 - fg_h) // 2 + round(p.main_clip_offset / 100 * 1920) + fg_h
        self.assertLessEqual(bottom, render.watermark_y("arriba"))   # the watermark is on free ground
        self.assertEqual(render.watermark_y("arriba") + render.WM_HEIGHT + 70, 1920)


class Snap(unittest.TestCase):
    """The word Whisper stretched over a pause starts where the voice comes back."""

    def levels(self):
        import numpy as np
        lv = np.full(400, -20.0)            # 4 s of voice at −20 dBFS, every 10 ms
        lv[100:132] = -120                  # a 0.32 s pause (digital silence) at 1.00–1.32
        lv[132:140] = -35                   # the breath before the next word (above ONSET_DB)
        lv[300:307] = -120                  # a short one (0.07 s): not a swallowed pause
        return lv

    def test_swallowed_pause(self):
        seg = [{"words": [{"word": "Al", "start": 0.95, "end": 1.6}, {"word": "y", "start": 2.98, "end": 3.3}]}]
        out, moved = plan.snap_starts(seg, self.levels())
        self.assertEqual(moved, 1)
        self.assertAlmostEqual(out[0]["words"][0]["start"], 1.32, places=2)   # where the sound comes back
        self.assertEqual(out[0]["words"][1]["start"], 2.98)                   # 70 ms: kept

    def test_a_normal_word_is_kept(self):
        seg = [{"words": [{"word": "hola", "start": 0.5, "end": 0.9}]}]
        self.assertEqual(plan.snap_starts(seg, self.levels()), (seg, 0))


class Request(unittest.TestCase):
    def test_validation(self):
        with self.assertRaises(ValueError):
            PasarelaParams.from_json({})
        with self.assertRaises(ValueError):
            PasarelaParams.from_json({"gameplay": {"archivo": "C:/no/existe.mp4"}})
        with self.assertRaises(ValueError):
            PasarelaParams.from_json({"gameplay": {"insights": {"grabacion": "x", "eventos": ["baron"]}}})

    def test_whisper_hint_is_off_by_default(self):
        d = S.tmpdir("pasarela-req")
        try:
            slides = os.path.join(d, "deck", "video-out", "laminas")
            os.makedirs(slides)
            for name in ("manifest.json", "guion-marcado.txt"):
                with open(os.path.join(slides, name), "w", encoding="utf-8") as fh:
                    fh.write("[]" if name.endswith(".json") else "")
            video = S.shots_video(os.path.join(d, "g.mp4"), [1.0])
            base = {"gameplay": {"archivo": video}, "voz": video, "deck": os.path.join(d, "deck"),
                    "salida": os.path.join(d, "out.mp4")}
            self.assertFalse(PasarelaParams.from_json(base).whisper_hint)
            self.assertTrue(PasarelaParams.from_json({**base, "pista_whisper": True}).whisper_hint)
        finally:
            S.remove(d)

    def test_routes_answer_json(self):
        import app
        client = app.create_app().test_client()
        r = client.post("/api/pasarela/process", json={"voz": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())
        self.assertEqual(client.get("/api/pasarela/report/nada").status_code, 404)
        r = client.post("/api/pasarela/highlight", json={"grabacion": "x", "eventos": ["baron"]})
        self.assertEqual(r.status_code, 400)


def _webm(path: str, color: str, dur: float = 1.0) -> str:
    subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={color}:s=1080x680:r=30:d={dur}", "-vf", "format=yuva420p",
                    "-c:v", "libvpx-vp9", "-b:v", "200k", "-deadline", "realtime", "-cpu-used", "8",
                    "-auto-alt-ref", "0", path], check=True)
    return path


class Run(unittest.TestCase):
    """The whole pipeline on synthetic files (Whisper mocked): the report's
    contract, the duration, the pauses and that the output is where asked."""

    @classmethod
    def setUpClass(cls):
        cls.dir = S.tmpdir("pasarela")
        d = cls.dir
        slides = os.path.join(d, "deck", "video-out", "laminas")
        os.makedirs(slides)
        for i, color in (("portada", "red"), ("dos", "blue"), ("tres", "green"), ("reposo", "gray")):
            _webm(os.path.join(slides, f"{i}.webm"), color)
        S.shots_video(os.path.join(slides, "vertical.mp4"), [1.0], w=270, h=480)
        with open(os.path.join(slides, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(MANIFEST, fh)
        with open(os.path.join(slides, "guion-marcado.txt"), "w", encoding="utf-8") as fh:
            fh.write(SCRIPT)
        cls.ancho = S.shots_video(os.path.join(d, "ancho.mp4"), [1.0, 0.5], w=640, h=360)
        cls.gameplay = S.shots_video(os.path.join(d, "gameplay.mp4"), [4, 4, 4], w=640, h=360)
        cls.voice = os.path.join(d, "voz.wav")
        subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "sine=frequency=220:sample_rate=48000:duration=9.5", cls.voice], check=True)
        cls.cache = os.path.join(d, "cache")

    @classmethod
    def tearDownClass(cls):
        S.remove(cls.dir)

    def test_report_contract(self):
        out = os.path.join(self.dir, "final.mp4")
        params = PasarelaParams.from_json({
            "gameplay": {"archivo": self.gameplay}, "voz": self.voice, "deck": os.path.join(self.dir, "deck"),
            "banda": "abajo", "watermark": None, "salida": out, "clips": {"ancho": self.ancho}})
        job = job_manager.create_job(self.voice, out)
        seg = segments(HEARD, gaps={14: 0.5})
        with mock.patch.object(processor, "transcript", return_value=seg), \
                mock.patch.multiple(processor, BASE_DIR=os.path.join(self.cache, "base"),
                                    WORK_DIR=os.path.join(self.cache, "work"),
                                    REPORTS_DIR=os.path.join(self.cache, "reports")):
            processor.process(job, params)
            report = processor.load_report(job)
        self.assertEqual(job_manager.get_job(job)["status"], "done", report.get("error"))
        self.assertTrue(os.path.isfile(out))
        for key in ("duracion", "alineacion", "capas", "clips", "pausas", "reposo", "sin_colocar",
                    "audio", "tiempos", "base", "avisos"):
            self.assertIn(key, report)
        for key in ("palabras_guion", "alineadas", "porcentaje"):
            self.assertIn(key, report["alineacion"])
        for key in ("lufs", "pico_dbfs", "lufs_voz"):
            self.assertIn(key, report["audio"])
        for key in ("base", "transcripcion", "plan", "banda", "final", "total"):
            self.assertIn(key, report["tiempos"])
        self.assertEqual({c["id"] for c in report["capas"]}, {"portada", "dos", "tres", "ancho", "vertical"})
        # The slides also go as a montaje.json v1 (the studio's contract), already validated.
        self.assertEqual([c["lamina"] for c in report["montaje"]["capas"]], ["portada", "dos", "tres"])
        self.assertEqual(report["montaje"]["version"], 1)
        self.assertEqual([p["clip"] for p in report["pausas"]], ["ancho", "vertical"])
        # Duration: the voice + both pauses + the tail.
        expected = 9.5 + (1.5 + 0.3) + (1.0 + 0.3) + plan.TAIL_S
        self.assertAlmostEqual(report["duracion_plan"], expected, places=3)
        self.assertAlmostEqual(report["duracion"], expected, delta=0.05)
        self.assertLessEqual(report["audio"]["pico_dbfs"], -0.5)       # the limiter at −1 dBFS
        self.assertIn("vertical", {c["id"] for c in report["clips"] if c["pantalla"] == "completa"})
        # A second montage of the same gameplay does not convert it again.
        job2 = job_manager.create_job(self.voice, out)
        with mock.patch.object(processor, "transcript", return_value=seg), \
                mock.patch.multiple(processor, BASE_DIR=os.path.join(self.cache, "base"),
                                    WORK_DIR=os.path.join(self.cache, "work"),
                                    REPORTS_DIR=os.path.join(self.cache, "reports")):
            processor.process(job2, params)
            self.assertTrue(processor.load_report(job2)["base"]["cache"])
            self.assertEqual(report["banda_pista"]["codec"], "ffv1")          # slides with alpha
            # An opaque export (`alfa: false`, Presentations' band decks) goes through NVENC lossless.
            manifest = os.path.join(self.dir, "deck", "video-out", "laminas", "manifest.json")
            with open(manifest, "w", encoding="utf-8") as fh:
                json.dump([{**m, "alfa": False} for m in MANIFEST], fh)
            job3 = job_manager.create_job(self.voice, out)
            processor.process(job3, params)
            third = processor.load_report(job3)
            self.assertEqual(job_manager.get_job(job3)["status"], "done", third.get("error"))
            if ffmpeg_runner.video_encode_flags()[1] == "h264_nvenc":
                self.assertEqual(third["banda_pista"]["codec"], "h264 sin pérdida")


if __name__ == "__main__":
    unittest.main()
