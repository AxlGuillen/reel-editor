"""Names right in Ibai's subtitles (offline): the game's vocabulary for
Whisper, the corrections of assets/vocab.json and the review's words."""
import json
import os
import tempfile
import unittest
from unittest import mock

from core import job_manager
from modules.competitivo import kills, processor, vocab
from modules.competitivo.schema import CompetitivoParams

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kc_c9_g1.json")


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


class GameTerms(unittest.TestCase):
    def test_names_as_they_are_said(self):
        self.assertEqual(vocab.champion_name("MissFortune"), "Miss Fortune")
        self.assertEqual(vocab.champion_name("XinZhao"), "Xin Zhao")
        self.assertEqual(vocab.champion_name("Ahri"), "Ahri")
        self.assertEqual(vocab.player_name("C9 Blaber", ("KC", "C9")), "Blaber")
        self.assertEqual(vocab.player_name("Caliste", ("KC", "C9")), "Caliste")

    def test_prompt_and_hotwords(self):
        roster = [{"team": "IG", "player": "TheShy", "champion": "Jax"},
                  {"team": "GEN", "player": "Ruler", "champion": "MissFortune"},
                  {"team": "GEN", "player": "Ruler", "champion": "MissFortune"}]
        terms = vocab.game_terms(roster, ["Invictus Gaming", "Gen.G", "IG", "GEN", "IG"])
        self.assertEqual(terms, {"teams": ["Invictus Gaming", "Gen.G", "IG", "GEN"],
                                 "players": ["TheShy", "Ruler"], "champions": ["Jax", "Miss Fortune"]})
        initial, hot = vocab.prompt(terms)
        self.assertIn("Invictus Gaming contra Gen.G", initial)
        self.assertIn("Jugadores: TheShy, Ruler", initial)
        self.assertIn("Campeones: Jax, Miss Fortune", initial)
        self.assertEqual(hot, "Invictus Gaming Gen.G IG GEN TheShy Ruler Jax Miss Fortune")

    def test_phase_1_keeps_the_roster(self):
        partida = kills.partida_de_fixture(FIXTURE)
        params = CompetitivoParams.from_form({"game_ids": partida.game_id, "vod_url": "https://youtu.be/x"})
        ancla = {"ancla_s": 100.0, "ancla_propuesta_s": 100.0, "desfase_s": 0.0, "confianza": "alta",
                 "suspicious": False, "coincidencias": 3, "esperadas": 3, "referencia": "x",
                 "referencia_vod_s": 0, "frame": None, "primera_kill_utc": partida.frames[1].ts.isoformat()}
        job = job_manager.create_job("x", output_path="")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(processor, "TRABAJO", tmp), \
                mock.patch.object(processor.fuente, "info_vod",
                                  return_value={"inicio_utc": partida.t0, "duracion": 1, "titulo": "t"}), \
                mock.patch.object(processor.kills, "fetch_partida", return_value=partida), \
                mock.patch.object(processor, "_ancla", return_value=ancla):
            processor.process(job, params)
        processor._SESIONES.pop(job, None)
        roster = job_manager.get_job(job)["partidas"][0]["roster"]
        self.assertEqual(len(roster), 10)
        self.assertEqual({r["team"] for r in roster}, {partida.azul, partida.rojo})
        self.assertTrue(all(r["player"] and r["champion"] for r in roster))
        self.assertFalse(any(r["player"].split()[0] in (partida.azul, partida.rojo) and " " in r["player"]
                             for r in roster))                            # without the team prefix


class Corrections(unittest.TestCase):
    FIXES = {vocab._key(k): v for k, v in {"James": "Jinx", "Quillana": "Qiyana",
                                           "la fortuna": "Miss Fortune"}.items()}

    def test_word_and_run_of_words(self):
        words = [_w("y", 0, 0.2), _w("la", 0.2, 0.4), _w("Fortuna", 0.4, 0.9), _w("con", 1, 1.2),
                 _w("JAMES!", 1.2, 1.6), _w("y", 1.6, 1.7), _w("quillána,", 1.7, 2.2)]
        out = vocab.correct(words, self.FIXES)
        self.assertEqual([w["word"] for w in out], ["y", "Miss Fortune", "con", "Jinx!", "y", "Qiyana,"])
        self.assertEqual((out[1]["start"], out[1]["end"]), (0.2, 0.9))        # the run's timing
        self.assertEqual(words[4]["word"], "JAMES!")                           # the input stays

    def test_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "vocab.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"_info": "x", "corrections": {"Quillana": "Qiyana", "": "x"}}, fh)
            self.assertEqual(vocab.load(path), {"quillana": "Qiyana"})
            self.assertEqual(vocab.load(os.path.join(tmp, "no.json")), {})
        # the repo's file loads and fixes the known ones
        self.assertEqual(vocab.correct([_w("James", 0, 1)])[0]["word"], "Jinx")


class ReviewWords(unittest.TestCase):
    SPANS = [{"source": "a.mp4", "start": 10.0, "end": 16.5, "out": 0.0},
             {"source": "b.mp4", "start": 3.0, "end": 8.0, "out": 6.5}]
    WORDS = [_w("vamos", 0.5, 0.9), _w("James", 1.0, 1.4), _w("qué", 7.0, 7.3), _w("pelea", 7.4, 8.0)]

    def test_one_segment_per_shot(self):
        segs = processor.review_segments(self.WORDS, self.SPANS)
        self.assertEqual([(s["id"], s["start"], s["end"], s["text"]) for s in segs],
                         [(0, 0.0, 6.5, "vamos James"), (1, 6.5, 11.5, "qué pelea")])

    def test_unchanged_keeps_the_timing_and_fixed_is_spread(self):
        segs = processor.review_segments(self.WORDS, self.SPANS)
        segs[0]["text"] = "vamos Jinx, vamos"                               # fixed by the reviewer
        words = processor.reviewed_words(segs)
        self.assertEqual([w["word"] for w in words], ["vamos", "Jinx,", "vamos", "qué", "pelea"])
        self.assertEqual(words[0]["start"], 0.5)                             # over what the words covered
        self.assertAlmostEqual(words[2]["end"], 1.4, places=3)
        self.assertEqual(words[3:], self.WORDS[2:])                          # untouched


class WhisperHints(unittest.TestCase):
    def test_transcribe_passes_them_to_faster_whisper(self):
        from modules.subtitles import processor as subtitles
        from modules.subtitles.schema import SubtitlesParams
        seen = {}

        class Word:
            word, start, end = " Jinx", 0.1, 0.5

        class Seg:
            start, end, text, words = 0.0, 0.5, " Jinx", [Word()]

        class Model:
            def transcribe(self, path, **kw):
                seen.update(kw)
                return iter([Seg()]), None
        with mock.patch.object(subtitles, "_get_model", return_value=Model()),                 mock.patch.object(subtitles, "_get_device", return_value=("cpu", "int8")),                 mock.patch.object(subtitles.ffmpeg_runner, "probe_duration", return_value=1.0):
            segs = subtitles.transcribe("x.wav", SubtitlesParams(), initial_prompt="Jugadores: Ruler.",
                                        hotwords="Ruler Jinx")
            self.assertEqual(segs[0]["words"][0]["word"], "Jinx")
            self.assertEqual((seen["initial_prompt"], seen["hotwords"]), ("Jugadores: Ruler.", "Ruler Jinx"))
            seen.clear()
            subtitles.transcribe("x.wav", SubtitlesParams())
            self.assertNotIn("initial_prompt", seen)                           # the other modules: as always


if __name__ == "__main__":
    unittest.main()
