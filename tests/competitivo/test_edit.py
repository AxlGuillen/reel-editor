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

    def test_frames_without_participants_are_skipped(self):
        # The frame right before the fight came without `participants` ({}):
        # the baseline is the last frame that has them. With an empty one,
        # Canna's earlier kill counted again and took Caliste's.
        kb = [0, 1, 1, 2, 2]
        frames = [engine.Frame(T0 + timedelta(seconds=10 * i), "in_game", _team(kb[i]), _team(0))
                  for i in range(5)]
        per_player = [{1: 0, 4: 0}, {1: 1, 4: 0}, {}, {1: 1, 4: 1}, {1: 1, 4: 1}]
        p = K.Partida("1", "KC", "C9", frames, per_player, {1: "KC Canna", 4: "KC Caliste"},
                      {1: "Rumble", 4: "MissFortune"})
        reglas = K.Reglas(min_kills=1, pre_s=0, post_s=0)
        fight = [f for f in K.detectar_peleas(p, reglas) if f.kills[0].i == 3][0]
        self.assertEqual(K.kill_authors(p, fight), [{"player": "Caliste", "champion": "MissFortune"}])

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
        self.assertEqual((ep.structure, ep.recipes, ep.cold_open_s, ep.build_fights, ep.voice_subs,
                          ep.voice_language), ("acts", (), 6.5, 2, True, "es"))
        self.assertEqual(ep.subs_style["font"], "Poppins-Bold.ttf")
        for raw, want in (("auto", ()), ("ramp, hard_cut", ("ramp", "hard_cut")),
                          ('["stair_zoom","ramp","ramp"]', ("stair_zoom", "ramp"))):
            self.assertEqual(EditParams.from_form({"recipes": raw}, self.BASE).recipes, want)
        ep = EditParams.from_form({"sub_inv_size": "200", "sub_inv_direction": "vertical", "sub_font": "Anton-Regular.ttf"},
                                  self.BASE)
        self.assertEqual((ep.subs_style["inv_size"], ep.subs_style["inv_direction"], ep.subs_style["font"]),
                         (200, "vertical", "Anton-Regular.ttf"))

    def test_errors(self):
        for form in ({"style": "x"}, {"text": "x"}, {"look": "sepia"}, {"lut": "no.cube"},
                     {"speed": "3"}, {"max_duration_s": "500"}, {"music_url": "nada"},
                     {"music_start": "ayer"}, {"anchors": "{\"999\": 1}"}, {"structure": "x"},
                     {"recipes": "slowmo"}, {"recipes": "[1"}, {"cold_open_s": "9"}, {"build_fights": "3"},
                     {"voice_language": "auto"}):
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
        ep = EditParams.from_form({"edit_title": "KC vs C9", "intensity": "0.8", "structure": "beat"}, params)
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

    def test_edit_acts(self):
        params = self._session()
        music = S.song(os.path.join(self.tmp, "m.wav"), bpm=120, dur=60, loud_from=24)
        ep = EditParams.from_form({"edit_title": "KC vs C9"}, params)          # acts, by default
        words = [{"words": [{"word": "vamos", "start": 0.4, "end": 0.8}, {"word": "KC", "start": 1.0, "end": 1.4}]}]
        job = job_manager.create_job("x", "")
        with mock.patch("modules.subtitles.processor.transcribe", return_value=words) as tr:
            processor.edit(job, "phase1", ep, music)
        j = job_manager.get_job(job)
        self.assertEqual(j["status"], "done", j["error"])
        res = j["resultado"]
        acts = {a["act"]: a for a in res["plan"]["acts"]}
        self.assertEqual(list(acts), ["cold_open", "build", "silence", "drop", "outro"])
        self.assertEqual(tr.call_count, 2)                                    # cold open + build
        self.assertEqual(res["subtitle_words"], 4)
        self.assertTrue(res["drop_aligned"])
        self.assertTrue(all(k["recipe"] in E.RECIPES for k in res["plan"]["kills"]))
        self.assertEqual(res["plan"]["kills"][-1]["recipe"], "instant_replay")
        self.assertEqual(ffmpeg_runner.probe_resolution(res["video"]), (1080, 1920))
        self.assertAlmostEqual(ffmpeg_runner.probe_duration(res["video"]), acts["outro"]["end"], delta=0.06)
        # the silence: black and mute
        s0 = acts["silence"]["start"]
        fr = S.gray_frames(res["video"], 60)
        self.assertLess(fr[int((s0 + 0.15) * 60)].mean(), 8)
        self.assertLess(_rms_db(res["video"], s0 + 0.08, 0.14), -50)
        self.assertGreater(_rms_db(res["video"], 1.0, 2.0), -40)             # the game (and Ibai) at 100 %
        # the status brings the plan
        from app import create_app
        st = create_app().test_client().get(f"/api/competitivo/status/{job}").get_json()
        self.assertEqual(st["plan"]["structure"], "acts")
        self.assertEqual(st["plan"]["kills"], res["plan"]["kills"])

    def test_review_pause_then_finish(self):
        from app import create_app
        params = self._session()
        music = S.song(os.path.join(self.tmp, "m.wav"), bpm=120, dur=60, loud_from=24)
        ep = EditParams.from_form({"review": "1"}, params)                      # acts, by default
        words = [{"words": [{"word": "vamos", "start": 0.4, "end": 0.8}, {"word": "James!", "start": 1.0, "end": 1.4}]}]
        job = job_manager.create_job("x", "")
        with mock.patch("modules.subtitles.processor.transcribe", return_value=words) as tr:
            processor.edit(job, "phase1", ep, music)
        j = job_manager.get_job(job)
        self.assertEqual((j["status"], j["stage"], j["finished"]), ("done", "esperando_revision", False), j["error"])
        self.assertIn("KC", tr.call_args.kwargs["hotwords"])                     # the game's names
        self.assertIn("KC contra C9", tr.call_args.kwargs["initial_prompt"])
        segs = j["segments"]
        self.assertEqual([s_["text"] for s_ in segs], ["vamos Jinx!", "vamos Jinx!"])   # vocab.json applied
        self.assertTrue(processor.edit_waiting(job))
        c = create_app().test_client()
        self.assertEqual(c.post("/api/competitivo/edit/finish", data={"job_id": job, "segments": "nada"}).status_code, 400)
        self.assertEqual(c.post("/api/competitivo/edit/finish", data={"job_id": "no", "segments": "[]"}).status_code, 404)
        segs[0]["text"] = "vamos Jinx vamos"
        processor.edit_finish(job, segs)
        j = job_manager.get_job(job)
        self.assertEqual((j["status"], j["finished"]), ("done", True), j["error"])
        self.assertEqual(j["resultado"]["subtitle_words"], 5)
        self.assertTrue(os.path.isfile(j["resultado"]["video"]))
        self.assertFalse(processor.edit_waiting(job))
        self.assertEqual(c.post("/api/competitivo/edit/finish", data={"job_id": job, "segments": "[]"}).status_code, 409)

    def test_short_with_ambient_shots(self):
        params = self._session()
        music = S.song(os.path.join(self.tmp, "m.wav"), bpm=120, dur=60, loud_from=24)
        ep = EditParams.from_form({"short": "1", "ambient": "1", "voice_subs": "0"}, params)
        job = job_manager.create_job("x", "")
        with mock.patch.object(processor.layout, "ambient_ranges", return_value=[(0.5, 3.5)]):
            processor.edit(job, "phase1", ep, music)
        j = job_manager.get_job(job)
        self.assertEqual(j["status"], "done", j["error"])
        acts = {a["act"]: a for a in j["resultado"]["plan"]["acts"]}
        self.assertEqual(list(acts), ["cold_open", "silence", "drop", "outro"])
        self.assertTrue(any(c.endswith(":ambient") for c in acts["cold_open"]["clips"] + acts["outro"]["clips"]))
        self.assertAlmostEqual(ffmpeg_runner.probe_duration(j["resultado"]["video"]), acts["outro"]["end"], delta=0.06)

    def test_a_span_without_video_is_skipped(self):
        params = self._session()
        music = S.song(os.path.join(self.tmp, "m.wav"), bpm=120, dur=60, loud_from=24)
        ep = EditParams.from_form({"structure": "beat"}, params)

        def span(url, a, b, destination):
            if destination.endswith("tramo_01"):
                raise processor.fuente.SpanWithoutVideo("sin video")
            return self._span(url, a, b, destination)

        job = job_manager.create_job("x", "")
        with mock.patch.object(processor.fuente, "bajar_tramo", side_effect=span):
            processor.edit(job, "phase1", ep, music)
        j = job_manager.get_job(job)
        self.assertEqual(j["status"], "done", j["error"])
        self.assertEqual(j["resultado"]["used_clips"], ["1-2"])
        self.assertEqual(j["resultado"]["skipped_clips"], ["pelea 1"])
        self.assertIn("«pelea 1»", j["aviso"])

    def test_failed_render_leaves_nothing(self):
        params = self._session()
        music = S.song(os.path.join(self.tmp, "m.wav"), bpm=120, dur=60, loud_from=24)
        ep = EditParams.from_form({"titulo": "KC vs C9", "structure": "beat"}, params)

        def broken(edl, out, *args, **kwargs):
            with open(out, "wb") as fh:                     # half-written by FFmpeg
                fh.write(b"x")
            raise RuntimeError("FFmpeg falló")

        job = job_manager.create_job("x", "")
        with mock.patch.object(processor.editing, "render", side_effect=broken):
            processor.edit(job, "phase1", ep, music)
        self.assertEqual(job_manager.get_job(job)["status"], "error")
        for _, _, files in os.walk(os.path.join(self.tmp, "edits")):
            self.assertEqual(files, [])                      # no broken "Edit Mapa 1.mp4"
        # and the next one keeps the clean name, without "(2)"
        job = job_manager.create_job("x", "")
        processor.edit(job, "phase1", ep, music)
        j = job_manager.get_job(job)
        self.assertEqual(j["status"], "done", j["error"])
        self.assertEqual(os.path.basename(j["resultado"]["video"]), "Edit Mapa 1 - KC vs C9.mp4")
        folder = os.path.dirname(j["resultado"]["video"])
        self.assertFalse([f for f in os.listdir(folder) if f.endswith(".tmp.mp4")])

    def test_api_validates(self):
        from app import create_app
        self._session()
        c = create_app().test_client()
        # an /edit job has neither clips nor a cover: 404, not a 500
        job = job_manager.create_job("x", "")
        job_manager.update_job(job, status="done", finished=True, resultado={"video": "x.mp4"})
        self.assertEqual(c.get(f"/api/competitivo/download/{job}/1").status_code, 404)
        self.assertEqual(c.get(f"/api/competitivo/portada/{job}").status_code, 404)
        # while phase 1 (or its /finish) is processing: 409
        job_manager.create_job("x", "")
        with mock.patch.object(job_manager, "get_job", return_value={"status": "processing"}):
            self.assertEqual(c.post("/api/competitivo/edit", data={"job_id": "phase1"}).status_code, 409)
        self.assertEqual(c.post("/api/competitivo/edit", data={"job_id": "nada"}).status_code, 404)
        self.assertEqual(c.post("/api/competitivo/edit", data={"job_id": "phase1"}).status_code, 400)
        self.assertEqual(c.post("/api/competitivo/edit", data={"job_id": "phase1", "style": "x"}).status_code, 400)


def _rms_db(path: str, start: float, dur: float) -> float:
    import re
    import subprocess
    err = subprocess.run([config.FFMPEG_PATH, "-v", "info", "-ss", f"{start}", "-t", f"{dur}", "-i", path,
                          "-af", "astats=measure_overall=RMS_level:measure_perchannel=none", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(re.findall(r"RMS level dB: (-?[\d.]+|-inf)", err)[-1].replace("-inf", "-200"))


def _gain_at(keys, t):
    """Value of a [(t, gain)] automation at t (as effects.gain evaluates it)."""
    keys = sorted(keys)
    if t < keys[0][0]:
        return keys[0][1]
    for (a, ga), (b, gb) in zip(keys, keys[1:]):
        if a <= t < b:
            return ga + (gb - ga) * (t - a) / (b - a)
    return keys[-1][1]


class Acts(unittest.TestCase):
    """structure="acts": cold open and build in real time with Ibai's voice,
    0.3 s of silence, the drop on the song's drop with a recipe per kill, outro."""
    BM = _beatmap()

    def _clips(self):
        return [_clip(1, [8.0, 9.1], 60), _clip(2, [6.0, 10.0], 40), _clip(3, [7.0, 12.0], 30),
                _clip(4, [9.0, 11.5], 90, closes=True)]

    def _plan(self, clips=None, **kw):
        kw.setdefault("chronological", False)          # the v2 order (best fight first)
        return E.plan(clips or self._clips(), self.BM, E.EditOptions(structure="acts", **kw))

    def test_acts_in_order_with_their_volumes(self):
        res = self._plan(text="both", title="KC vs C9")
        acts = {a["act"]: a for a in res.acts}
        self.assertEqual([a["act"] for a in res.acts], ["cold_open", "build", "silence", "drop", "outro"])
        for a, b in zip(res.acts, res.acts[1:]):
            self.assertAlmostEqual(a["end"], b["start"], places=3)
        self.assertAlmostEqual(acts["cold_open"]["end"], 6.5, places=3)
        self.assertEqual(acts["cold_open"]["clips"], ["1-4"])                # the best one
        self.assertEqual(acts["build"]["clips"], ["1-1", "1-2"])
        self.assertAlmostEqual(acts["silence"]["end"] - acts["silence"]["start"], E.SILENCE_S, places=3)
        self.assertEqual(acts["drop"]["clips"], ["1-3", "1-4"])               # the key one closes
        self.assertEqual(acts["outro"]["clips"], ["1-4"])
        # the drop act starts exactly on the song's drop
        self.assertTrue(res.drop_aligned)
        self.assertAlmostEqual(res.music_start + acts["drop"]["start"], 32.0, places=3)
        edl = res.edl
        self.assertAlmostEqual(edl.dur, acts["outro"]["end"], places=2)
        mid = {k: (a["start"] + a["end"]) / 2 for k, a in acts.items()}
        for act, music, game in (("cold_open", 0.15, 1.0), ("build", 0.20, 1.0), ("silence", 0.0, 0.0),
                                 ("drop", 1.0, 0.15)):
            self.assertAlmostEqual(_gain_at(edl.music_gain, mid[act]), music, places=3, msg=act)
            self.assertAlmostEqual(_gain_at(edl.original_gain, mid[act]), game, places=3, msg=act)
        self.assertAlmostEqual(_gain_at(edl.original_gain, acts["outro"]["end"]), 1.0)  # the game rises back
        black = [e for e in edl.events if e.kind == "black"]
        self.assertEqual(len(black), 1)
        self.assertAlmostEqual(black[0].t, acts["silence"]["start"], places=3)
        # the outro ends on a bar and the music fades over it
        bar = (acts["outro"]["end"] + res.music_start) / (60 / 120)
        self.assertAlmostEqual(bar % 4, 0, places=2)
        self.assertAlmostEqual(edl.fade_out, acts["outro"]["end"] - acts["outro"]["start"], places=3)
        # lyrics only from the drop on; the title only while Ibai talks
        words = [t for t in edl.texts if t.style == "word"]
        self.assertTrue(words)
        self.assertTrue(all(t.start >= acts["drop"]["start"] for t in words))
        title = next(t for t in edl.texts if t.style == "title")
        self.assertLessEqual(title.end, acts["silence"]["start"])
        edl.validate()

    def test_real_time_shots_push_in_and_carry_the_voice(self):
        res = self._plan()
        acts = {a["act"]: a for a in res.acts}
        pushes = [e for e in res.edl.events if e.kind == "push"]
        self.assertEqual(len(pushes), 3)                                     # cold open + 2 build
        self.assertTrue(all(e.level == E.PUSH_LEVEL for e in pushes))
        self.assertEqual(len(res.voice_spans), 3)
        self.assertEqual([round(p.t, 3) for p in pushes], [s["out"] for s in res.voice_spans])
        for s in res.voice_spans:
            self.assertLessEqual(s["out"] + s["end"] - s["start"], acts["silence"]["start"] + 1e-3)
        self.assertAlmostEqual(res.subtitle_until, acts["silence"]["start"], places=3)
        self.assertAlmostEqual(res.edl.subtitle_until, res.subtitle_until)
        for seg in res.edl.segments[:3]:                                     # real time: 1×
            self.assertTrue(all(abs(p.speed - 1) < 1e-6 for p in seg.pieces), seg.label)
        # whips: between the build shots and between the drop fights
        whips = sorted(round(e.t, 3) for e in res.edl.events if e.kind == "whip")
        self.assertEqual(len(whips), 3)
        self.assertIn(round(acts["cold_open"]["end"], 3), whips)

    def test_recipes_rotate_and_the_replay_is_the_key_ones(self):
        clips = [_clip(i, [6.0, 9.0], 50 + i) for i in range(1, 6)] + [_clip(6, [8.0, 11.0], 99, closes=True)]
        res = self._plan(clips, build_fights=0, max_duration_s=120)
        recipes = [k["recipe"] for k in res.kills]
        self.assertTrue(all(a != b for a, b in zip(recipes, recipes[1:])), recipes)
        self.assertEqual(recipes.count("instant_replay"), 1)
        self.assertEqual((res.kills[-1]["clip"], res.kills[-1]["recipe"]), ("1-6", "instant_replay"))
        self.assertEqual({"ramp", "freeze_zoom", "hard_cut"} - set(recipes), set())
        self.assertNotIn("stair_zoom", recipes)                              # no multikill
        again = self._plan(clips, build_fights=0, max_duration_s=120)
        self.assertEqual(recipes, [k["recipe"] for k in again.kills])        # fixed hash
        # the replay rewinds REPLAY_BACK s and shows it at REPLAY_SPEED with the zoom held
        key = next(s for s in res.edl.segments if s.label == "1-6")
        slow = [p for p in key.pieces if abs(p.speed - E.REPLAY_SPEED) < 1e-6]
        self.assertEqual(len(slow), 1)
        self.assertAlmostEqual(slow[0].start, 11.0 - E.REPLAY_BACK, places=3)
        t_kill = res.kills[-1]["t"]
        self.assertTrue(any(e.kind == "hold" and e.level == E.REPLAY_ZOOM and e.t > t_kill
                            for e in res.edl.events))
        # a fixed list
        only = self._plan(clips, build_fights=0, max_duration_s=120, recipes=("hard_cut",))
        self.assertEqual({k["recipe"] for k in only.kills}, {"hard_cut"})
        two = self._plan(clips, build_fights=0, max_duration_s=120, recipes=("ramp", "freeze_zoom"))
        r2 = [k["recipe"] for k in two.kills]
        self.assertEqual(set(r2), {"ramp", "freeze_zoom"})
        self.assertTrue(all(a != b for a, b in zip(r2, r2[1:])))
        with self.assertRaises(ValueError):
            self._plan(recipes=("slowmo",))

    def test_multikill_climbs_a_stair_one_kill_per_beat(self):
        clips = [_clip(1, [5.0, 6.0, 7.2, 8.4], 50, multikill="quadra de X"),
                 _clip(2, [8.0, 11.0], 90, closes=True)]
        res = self._plan(clips, build_fights=0)
        stair = [k for k in res.kills if k["recipe"] == "stair_zoom"]
        self.assertEqual(len(stair), 4)
        self.assertTrue(all(k["clip"] == "1-1" for k in stair))
        gaps = [b["t"] - a["t"] for a, b in zip(stair, stair[1:])]
        self.assertTrue(all(abs(g - 0.5) < 1e-3 for g in gaps), gaps)       # 120 bpm: one beat
        levels = [e.level for e in res.edl.events if e.kind == "hold"
                  and any(abs(e.t - k["t"]) < 1e-6 for k in stair)]
        self.assertEqual(levels, list(E.STAIR))
        self.assertTrue(any(t.style == "multikill" for t in res.edl.texts))

    def test_the_zoom_aims_at_the_action(self):
        clips = self._clips()
        clips[2].kills[0].focus = (0.3, 0.6)
        res = self._plan(clips, recipes=("freeze_zoom",))
        t_kill = next(k["t"] for k in res.kills if k["clip"] == "1-3")
        aimed = [e for e in res.edl.events if abs(e.t - t_kill) < 1e-6 and e.kind == "hold"]
        self.assertEqual([(e.x, e.y) for e in aimed], [(0.3, 0.6)])
        # without a measured focus: the center
        others = [e for e in res.edl.events if e.kind == "hold" and e is not aimed[0]]
        self.assertTrue(others)
        self.assertTrue(all((e.x, e.y) == (0.5, 0.5) for e in others))

    def test_music_start_is_where_the_drop_act_starts(self):
        res = self._plan(music_start=40.2)
        acts = {a["act"]: a for a in res.acts}
        self.assertAlmostEqual(res.music_start + acts["drop"]["start"], 40.0, places=3)   # its beat
        self.assertFalse(res.drop_aligned)
        self.assertIsNone(res.warning)
        early = self._plan(music_start=3.0)                              # before the cold open fits
        self.assertGreaterEqual(early.music_start, 0)
        self.assertIn("music_start", early.warning)

    def test_chronological_teases_the_key_fight_and_keeps_the_order(self):
        res = self._plan(chronological=True)
        acts = {a["act"]: a for a in res.acts}
        self.assertAlmostEqual(acts["cold_open"]["end"], 2.5, places=3)          # a teaser
        self.assertEqual(acts["cold_open"]["clips"], ["1-4"])                    # of the key fight
        self.assertEqual(acts["build"]["clips"], ["1-1", "1-2"])                 # the first ones
        self.assertEqual(acts["drop"]["clips"], ["1-3", "1-4"])                  # the rest, in order
        order = [k["clip"] for k in res.kills]
        self.assertEqual(order, sorted(order))
        # the outro goes on from where the drop left the last fight: no repeat
        drop_seg = next(s_ for s_ in res.edl.segments if s_.label == "1-4")
        outro = next(s_ for s_ in res.edl.segments if s_.label == "1-4:outro")
        self.assertGreaterEqual(min(p.start for p in outro.pieces), max(p.end for p in drop_seg.pieces) - 1e-6)
        # the key fight is not the last one: the outro is the last one's tail
        clips = [_clip(1, [8.0, 9.1], 50), _clip(2, [9.0, 11.5], 95), _clip(3, [7.0, 12.0], 40)]
        res = self._plan(clips, chronological=True)
        acts = {a["act"]: a for a in res.acts}
        self.assertEqual((acts["cold_open"]["clips"], acts["build"]["clips"], acts["drop"]["clips"],
                          acts["outro"]["clips"]), (["1-2"], ["1-1"], ["1-2", "1-3"], ["1-3"]))

    def test_short_is_the_key_fight_and_the_best_other(self):
        clips = [_clip(i, [6.0, 9.0], 50 + i) for i in range(1, 6)] + [_clip(6, [8.0, 11.0], 99, closes=True)]
        res = self._plan(clips, short=True)
        acts = {a["act"]: a for a in res.acts}
        self.assertEqual([a["act"] for a in res.acts], ["cold_open", "silence", "drop", "outro"])
        self.assertEqual(acts["drop"]["clips"], ["1-5", "1-6"])
        self.assertLessEqual(acts["outro"]["end"], E.SHORT_MAX_S + 1)
        self.assertAlmostEqual(acts["cold_open"]["end"], 2.5, places=3)

    def test_ambient_shots_in_the_cold_open_and_the_outro(self):
        clips = self._clips()
        clips[0].ambient = [(14.0, 18.0)]
        clips[3].ambient = [(2.0, 3.6), (15.0, 19.5)]                           # after its last kill: 15
        for c in clips:
            c.ambient_vf = "AMBIENT"
        res = self._plan(clips, chronological=True, ambient=True)
        acts = {a["act"]: a for a in res.acts}
        self.assertEqual(acts["outro"]["clips"], ["1-4", "1-4:ambient"])         # the reaction to the last one
        amb = [s_ for s_ in res.edl.segments if s_.label.endswith(":ambient")]
        self.assertEqual([(s_.label, s_.pieces[0].start, s_.vf) for s_ in amb],
                         [("1-1:ambient", 14.0, "AMBIENT"), ("1-4:ambient", 15.0, "AMBIENT")])
        self.assertEqual(acts["cold_open"]["clips"], ["1-4", "1-1:ambient"])
        self.assertAlmostEqual(acts["cold_open"]["end"], 2.5 + E.AMBIENT_COLD_S, places=3)
        self.assertEqual(len(res.voice_spans), 4)                                # teaser, ambient, 2 build
        # the outro ends on its bar, half nexus half stage
        bar = (acts["outro"]["end"] + res.music_start) / 0.5
        self.assertAlmostEqual(bar % 4, 0, places=2)
        # without ambient=1 they are not used
        self.assertFalse(any(s_.label.endswith(":ambient") for s_ in self._plan(clips, chronological=True).edl.segments))

    def test_plan_dict_and_beat_still_works(self):
        res = self._plan()
        d = res.plan_dict()
        self.assertEqual(d["acts"][0]["act"], "cold_open")
        self.assertTrue(all({"clip", "t", "recipe", "player", "champion"} <= set(k) for k in d["kills"]))
        beat = E.plan(self._clips(), self.BM, E.EditOptions())
        self.assertEqual([a["act"] for a in beat.acts], ["beat"])
        self.assertFalse(beat.edl.music_gain or beat.edl.original_gain)
        with self.assertRaises(ValueError):
            E.plan(self._clips(), self.BM, E.EditOptions(structure="x"))


class ActionFocus(unittest.TestCase):
    """layout.action_focus: the centroid of the motion inside the gameplay,
    in output coordinates; None if nothing moves or everything moves."""

    def setUp(self):
        self.tmp = S.tmpdir("focus")

    def tearDown(self):
        S.remove(self.tmp)

    def _video(self, name, graph):
        import subprocess
        path = os.path.join(self.tmp, name)
        subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=0x404040:s=1920x1080:r=30:d=1.2", "-f", "lavfi", "-i",
                        "color=c=white:s=80x80:r=30:d=1.2", "-filter_complex", graph, "-map", "[v]",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", path], check=True)
        return path

    def test_focus(self):
        from modules.competitivo import layout as L
        g = L.geometria(1.3)
        cw, ch, cx, cy = g.recorte
        u, v = 0.25, 0.7                                     # where the box moves, in the crop
        bx, by = cx + u * cw, cy + v * ch
        # (drawbox evaluates x only once: overlay moves it frame by frame)
        moving = self._video("m.mp4", f"[0:v][1:v]overlay=x='{bx - 40:.0f}+60*sin(12*t)':y={by - 40:.0f}[v]")
        f = L.action_focus(moving, 0.6, 1.3)
        self.assertIsNotNone(f)
        self.assertAlmostEqual(f[0], u, delta=0.04)
        self.assertAlmostEqual(f[1], (L.ARRIBA_H + g.extra_arriba + v * g.juego_h) / L.H, delta=0.03)
        self.assertIsNone(L.action_focus(self._video("s.mp4", "[0:v]null[v]"), 0.6, 1.3))
        self.assertIsNone(L.action_focus(self._video("n.mp4", "[0:v]noise=alls=90:allf=t[v]"), 0.6, 1.3))
