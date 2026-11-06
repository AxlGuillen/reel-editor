"""Tests of the studio: the marked script against the transcript, the anchors,
montaje.json → montage specs, that the preview resolves exactly what the
render resolves, the preview geometry, the contract and the routes."""
import io
import json
import os
import unittest
from unittest import mock

import config
from modules.montage import layers as L
from modules.montage.schema import MontageParams
from modules.studio import processor as P
from modules.studio import schema as SC
from tests import synthetic as S

# The opening of the LeBlanc script (Cumplelolero #30) and what Whisper wrote
# for it (6/11): "dieciséis" → "16", "LeBlanc" → "Leblanc", "Elise" → "Liz",
# the first words dropped ("Y ya ni" → "Y").
SCRIPT = ("⟦lamina:lb-portada⟧ Y ya ni voy a decir que me apendejé simplemente soy pendejo, porque el lunes "
          "también cumplió dieciséis años LeBlanc\n\n⟦lamina:lb-kit⟧ Este personaje es una mamada, y su "
          "⟦lamina:lb-clon⟧ clon es elegante\n\n⟦lamina:lb-elise⟧ Y se acuerdan de Elise, pues es esta señora")
HEARD = ("Y voy a decir que me apendejeé simplemente soy pendejo porque el lunes también cumplió 16 años "
         "Leblanc. Este personaje es una mamada, y su clon es elegante. ¿Y se acuerdan de Liz? Pues es esta señora")


def _segments(text=HEARD, step=0.4):
    words = text.split()
    return [{"id": 0, "start": 0.0, "end": len(words) * step, "text": text,
             "words": [{"word": w, "start": round(i * step, 3), "end": round(i * step + 0.3, 3)}
                       for i, w in enumerate(words)]}]


MANIFEST = [{"id": i, "caja": {"width": 1080, "x": "center", "bottom": 275}, "palabras": [], "dur_anim": 2.8}
            for i in ("lb-portada", "lb-kit", "lb-clon", "lb-elise")]


def _montaje(capas, **kw):
    return {"version": 1, "base": {"archivo": "prereel.mp4"}, "deck": {"desk": "creator", "deck": "leblanc"},
            "capas": capas, **kw}


class Script(unittest.TestCase):
    def test_marks_and_names(self):
        words, marks = P.parse_script(SCRIPT)
        self.assertEqual([m for m, _ in marks], ["lb-portada", "lb-kit", "lb-clon", "lb-elise"])
        self.assertEqual(words[marks[2][1]], "clon")
        self.assertIn("16", words)                       # spelled-out numbers as digits, like Whisper
        names = P.script_names(SCRIPT)
        self.assertIn("LeBlanc", names)
        self.assertIn("Elise", names)
        self.assertNotIn("Este", names)                 # opens a paragraph: not a name
        self.assertTrue(P.whisper_prompt(SCRIPT).startswith("Vocabulario: "))
        self.assertIsNone(P.whisper_prompt("sin nombres propios"))

    def test_alignment_survives_what_whisper_heard(self):
        auto = P.auto_layers(MANIFEST, _segments(), SCRIPT)
        self.assertEqual(auto["unplaced"], [])
        by_id = {c["lamina"]: c["ancla"] for c in auto["capas"]}
        tr = L.transcript_words(_segments())
        start = {k: L.find(tr, a["palabra"], a["n"]) for k, a in by_id.items()}
        words = [w for w, _, _ in tr]
        # "Y ya ni" became "Y": the slide still comes in on the first word.
        self.assertEqual(start["lb-portada"], 0.0)
        self.assertEqual(words[[t for _, t, _ in tr].index(start["lb-clon"])], "clon")
        self.assertEqual(words[[t for _, t, _ in tr].index(start["lb-kit"])], "este")
        # "Elise" → "Liz": the mark is before "Y se acuerdan", which did match.
        self.assertEqual(by_id["lb-elise"]["palabra"].split()[0], "y")
        self.assertEqual(words[[t for _, t, _ in tr].index(start["lb-elise"]) + 1], "se")
        self.assertEqual(auto["source"]["lb-kit"], "guion")
        self.assertGreater(auto["alignment"]["matched"], 30)

    def test_palabras_when_there_is_no_script(self):
        manifest = [{**MANIFEST[0], "palabras": ["clon"]}, {**MANIFEST[1], "palabras": ["no existe"]}]
        auto = P.auto_layers(manifest, _segments(), None)
        self.assertEqual([c["lamina"] for c in auto["capas"]], ["lb-portada"])
        self.assertEqual(auto["capas"][0]["ancla"]["palabra"].split()[0], "clon")
        self.assertEqual(auto["unplaced"], ["lb-kit"])
        self.assertEqual(auto["source"], {"lb-portada": "palabras"})


class Anchors(unittest.TestCase):
    def test_shortest_unique_phrase_or_its_occurrence(self):
        words = "y voy a decir y voy a ir y voy".split()
        self.assertEqual(P.anchor_at(words, 3), {"palabra": "decir", "n": 1})
        self.assertEqual(P.anchor_at(words, 4), {"palabra": "y voy a ir", "n": 1})
        self.assertEqual(P.anchor_at(words, 8), {"palabra": "y voy", "n": 3})
        # And montage's resolver brings each anchor back to its word.
        tr = [(w, float(i), i + 0.5) for i, w in enumerate(words)]
        for i in range(len(words)):
            a = P.anchor_at(words, i)
            self.assertEqual(L.find(tr, a["palabra"], a["n"]), float(i), i)

    def test_word_list(self):
        wl = P.word_list(_segments())
        self.assertEqual(wl[0]["w"], "y")
        self.assertEqual(wl[0]["anchor"]["n"], 1)
        self.assertEqual(P.word_list(None), [])


class Specs(unittest.TestCase):
    def test_box_precedence_and_band(self):
        m = _montaje([{"lamina": "lb-portada", "ancla": {"palabra": "voy", "n": 1}},
                      {"lamina": "lb-kit", "desde": 7.5, "hasta": 9.0, "caja": "banda"},
                      {"lamina": "lb-clon", "ancla": {"palabra": "clon"}, "hasta": {"palabra": "es", "n": 1},
                       "caja": {"width": 500}}],
                     caja={"width": 706, "x": "center", "bottom": 14})
        specs = P.to_specs(m, MANIFEST)
        self.assertEqual(specs[0], {"id": "lb-portada", "word": "voy", "n": 1,
                                    "box": {"width": 706, "x": "center", "bottom": 14, "mode": "box"}})
        self.assertEqual(specs[1]["box"]["mode"], "band")
        self.assertEqual((specs[1]["start"], specs[1]["end"]), (7.5, 9.0))
        self.assertEqual(specs[2]["box"], {"width": 500, "x": "center", "bottom": 14, "mode": "box"})
        self.assertEqual((specs[2]["end_word"], specs[2]["end_n"]), ("es", 1))
        # Without a montaje box, the manifest's.
        self.assertEqual(P.to_specs(_montaje(m["capas"][:1]), MANIFEST)[0]["box"]["bottom"], 275)

    def test_preview_resolves_exactly_what_the_render_resolves(self):
        """The same specs through montage's own form (what the render runs) and
        through the studio: same times, fades and boxes."""
        tmp = S.tmpdir("studio-same")
        try:
            folder = os.path.join(tmp, "laminas")
            os.makedirs(folder)
            with open(os.path.join(folder, "manifest.json"), "w") as fh:
                json.dump([{"id": m["id"], "archivo": f"{m['id']}.webm"} for m in MANIFEST], fh)
            for m in MANIFEST:
                open(os.path.join(folder, f"{m['id']}.webm"), "wb").close()
            segs = _segments()
            m = _montaje(P.auto_layers(MANIFEST, segs, SCRIPT)["capas"], caja={"width": 706, "bottom": 14})
            specs = P.to_specs(m, MANIFEST)
            with mock.patch.object(config, "LAYERS_FOLDER", tmp):
                render = MontageParams.from_form({"layers": json.dumps(specs), "layers_folder": folder}) \
                    .resolved_layers(segs, 30.0)
            preview = P.resolve(specs, segs, 30.0)
            self.assertEqual(len(render), len(preview))
            for r, p in zip(render, preview):
                self.assertEqual((r.label, r.start, r.end, r.fade_in, r.fade_out), (p["id"], p["start"], p["end"],
                                                                                    p["fade_in"], p["fade_out"]))
                self.assertEqual(r.box.__dict__, p["box"])
        finally:
            S.remove(tmp)

    def test_overlap_is_montage_error(self):
        specs = P.to_specs(_montaje([{"lamina": "lb-portada", "desde": 1, "hasta": 5},
                                     {"lamina": "lb-kit", "desde": 3}]), MANIFEST)
        with self.assertRaises(ValueError):
            P.resolve(specs, None, 30)


class Geometry(unittest.TestCase):
    def test_like_ffmpeg(self):
        # 706 px: 1370*706/1080 = 895.6 → 896; centered x = 187 → 186 (yuv420p overlay).
        g = P.preview_geometry({"width": 706, "x": "center", "bottom": 14, "mode": "box"})
        self.assertEqual((g["x"], g["y"], g["w"], g["h"]), (186, 1010, 706, 896))
        g = P.preview_geometry({"width": 1080, "x": "center", "bottom": 275, "mode": "box"})
        # The deck's own place (y = 275) lands on 274: FFmpeg's overlay on yuv420p
        # takes an odd x or y down to even (checked against FFmpeg on 6/11).
        self.assertEqual((g["x"], g["y"], g["h"]), (0, 274, 1370))
        g = P.preview_geometry({"width": 720, "x": "right", "bottom": 0, "mode": "box", "y": 0, "height": 0})
        self.assertEqual((g["x"], g["w"], g["h"]), (360, 720, 914))
        g = P.preview_geometry({"mode": "band", "y": 1240, "height": 680, "x": "center", "width": 720, "bottom": 0})
        self.assertEqual((g["w"], g["h"], g["y"], g["band"]), (536, 680, 1240, True))
        self.assertEqual(g["x"], 272)


class Contract(unittest.TestCase):
    def test_brief_example(self):
        example = {"version": 1, "base": {"job_id": "85567968bac0"},
                   "deck": {"desk": "creator", "deck": "leblanc", "servidor": "http://localhost:3125"},
                   "capas": [{"lamina": "lb-portada", "ancla": {"palabra": "leblanc", "n": 1},
                              "hasta": {"palabra": "otp", "n": 1}, "caja": "banda"},
                             {"lamina": "lb-skins", "desde": 84.2, "hasta": 96.0,
                              "caja": {"width": 720, "x": "center", "bottom": 0}}],
                   "subtitulos": True}
        self.assertEqual(SC.validate_montaje(example)["capas"][1]["desde"], 84.2)
        with open(os.path.join(config.BASE_DIR, "docs", "contratos", "montaje.schema.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["title"], "montaje.json v1")

    def test_errors(self):
        ok = [{"lamina": "lb-kit", "desde": 1}]
        bad = [
            {**_montaje(ok), "version": 2},
            _montaje([{"lamina": "lb-kit"}]),                                       # neither ancla nor desde
            _montaje([{"lamina": "lb-kit", "desde": 1, "ancla": {"palabra": "x"}}]),  # both
            _montaje([{"lamina": "lb-kit", "desde": 1, "caja": {"width": 5000}}]),
            _montaje([{"lamina": "../x", "desde": 1}]),
            _montaje(ok, deck={"desk": "creator", "deck": "leblanc", "servidor": "http://evil.com"}),
            {**_montaje(ok), "base": {}},
            {**_montaje(ok), "extra": 1},
            _montaje(ok + ok),                                                     # twice
        ]
        for m in bad:
            with self.assertRaises(ValueError, msg=m):
                SC.validate_montaje(m)
        with self.assertRaises(ValueError):                                         # not in the deck
            SC.validate_montaje(_montaje(ok), ["lb-portada"])
        with self.assertRaises(ValueError):
            SC.validate_montaje("{no es json")

    def test_segments(self):
        self.assertEqual(len(SC.validate_segments({"segments": _segments()})[0]["words"]), len(HEARD.split()))
        for bad in (None, [], [{"text": "sin palabras"}], [{"words": [{"word": "a"}]}]):
            with self.assertRaises(ValueError, msg=bad):
                SC.validate_segments(bad)


class Routes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import create_app
        cls.tmp = S.tmpdir("studio-routes")
        cls.video = S.shots_video(os.path.join(cls.tmp, "base.mp4"), [10, 10], fps=24)
        cls.patches = [mock.patch.object(config, "STUDIO_FOLDER", os.path.join(cls.tmp, "Estudio")),
                       mock.patch.object(config, "STUDIO_SESSIONS_FOLDER", os.path.join(cls.tmp, "sessions"))]
        for p in cls.patches:
            p.start()
        cls.client = create_app().test_client()

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        S.remove(cls.tmp)

    def _open(self):
        with open(self.video, "rb") as fh:
            res = self.client.post("/api/studio/open", data={"video": (io.BytesIO(fh.read()), "prereel 242.mp4")},
                                   content_type="multipart/form-data")
        self.assertEqual(res.status_code, 200, res.get_json())
        return res.get_json()

    def test_open_place_save_reopen(self):
        s = self._open()
        self.assertAlmostEqual(s["duration"], 20, delta=0.2)
        self.assertIsNone(s["montaje"])
        sid = s["studio_id"]
        res = self.client.post("/api/studio/segments", json={"studio_id": sid, "segments": _segments(step=0.3)})
        self.assertEqual(res.status_code, 200, res.get_json())
        montaje = _montaje([], caja={"width": 706, "x": "center", "bottom": 14})
        res = self.client.post("/api/studio/place", json={"studio_id": sid, "montaje": montaje, "manifest": MANIFEST,
                                                          "script": SCRIPT, "words": True})
        d = res.get_json()
        self.assertEqual(res.status_code, 200, d)
        self.assertEqual([lay["id"] for lay in d["layers"]], ["lb-portada", "lb-kit", "lb-clon", "lb-elise"])
        self.assertEqual(d["layers"][0]["geometry"]["x"], 186)
        self.assertEqual(d["montaje"]["base"]["archivo"], "prereel 242.mp4")
        self.assertTrue(d["words"])
        # Move a slide: anchored to another word (here, the 2nd "y" of the transcript).
        m = d["montaje"]
        target = next(w for w in d["words"] if w["w"] == "pues")
        m["capas"][3]["ancla"] = target["anchor"]
        res = self.client.post("/api/studio/place", json={"studio_id": sid, "montaje": m, "manifest": MANIFEST})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertAlmostEqual(res.get_json()["layers"][3]["start"], target["start"] - 0.1, places=3)
        res = self.client.post("/api/studio/save", json={"studio_id": sid, "montaje": m, "manifest": MANIFEST})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertTrue(os.path.isfile(res.get_json()["path"]))
        # The same video again (another upload, same fingerprint): its montaje and transcript come back.
        again = self._open()
        self.assertEqual(again["fingerprint"], s["fingerprint"])
        self.assertEqual(again["montaje"]["capas"][3]["ancla"], target["anchor"])
        self.assertTrue(again["segments"])
        # The video, with Range (the <video> of the preview).
        res = self.client.get(f"/api/studio/video/{again['studio_id']}", headers={"Range": "bytes=0-99"})
        self.assertEqual(res.status_code, 206)
        res.close()

    def test_errors(self):
        s = self._open()
        sid = s["studio_id"]
        cases = [
            ("/api/studio/place", {"studio_id": "nope", "montaje": _montaje([]), "manifest": MANIFEST}),
            ("/api/studio/place", {"studio_id": sid, "montaje": _montaje([]), "manifest": []}),
            # anchored to a word and no transcript yet (this session has none)
            ("/api/studio/place", {"studio_id": sid, "manifest": MANIFEST,
                                   "montaje": _montaje([{"lamina": "lb-kit", "ancla": {"palabra": "clon"}}])}),
            ("/api/studio/save", {"studio_id": sid, "montaje": _montaje([{"lamina": "otra", "desde": 1}]),
                                  "manifest": MANIFEST}),
            ("/api/studio/segments", {"studio_id": sid, "segments": [{"text": "x"}]}),
        ]
        if s["segments"]:               # another test saved this video's transcript first
            cases.pop(2)
        for url, body in cases:
            res = self.client.post(url, json=body)
            self.assertEqual(res.status_code, 400, (url, body, res.get_json()))
            self.assertTrue(res.get_json()["error"])
        self.assertEqual(self.client.get("/api/studio/script?desk=../x&deck=y").status_code, 400)

    def test_session_survives_a_restart(self):
        from core import job_manager
        s = self._open()
        with job_manager._lock:
            job_manager._jobs.pop(s["studio_id"])
        res = self.client.get(f"/api/studio/session/{s['studio_id']}")
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(job_manager.get_job(s["studio_id"])["output_path"], job_manager.get_job(s["studio_id"])["video_path"])

    def test_script_from_presentations(self):
        class Res(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=Res(SCRIPT.encode("utf-8"))) as op:
            d = self.client.get("/api/studio/script?desk=creator&deck=leblanc").get_json()
        self.assertEqual(op.call_args[0][0],
                         f"{config.PRESENTATIONS_URL}/creator/leblanc/video-out/laminas/guion-marcado.txt")
        self.assertEqual(d["marks"], ["lb-portada", "lb-kit", "lb-clon", "lb-elise"])
        self.assertIn("LeBlanc", d["prompt"])


class TranscribeRoute(unittest.TestCase):
    def test_job_id_and_prompt_reach_whisper(self):
        from app import create_app
        from core import job_manager
        tmp = S.tmpdir("studio-tr")
        try:
            video = S.shots_video(os.path.join(tmp, "v.mp4"), [2], fps=24)
            jid = job_manager.create_job(video, output_path=video)
            job_manager.update_job(jid, status="done")
            client = create_app().test_client()
            with mock.patch("modules.subtitles.processor.transcribe", return_value=_segments()) as tr:
                res = client.post("/api/subtitles/transcribe", data={"job_id": jid, "initial_prompt": "Vocabulario: LeBlanc."})
                self.assertEqual(res.status_code, 202, res.get_json())
                new = res.get_json()["job_id"]
                for _ in range(50):
                    if job_manager.get_job(new)["status"] == "done":
                        break
                    import time
                    time.sleep(0.05)
            self.assertEqual(tr.call_args.kwargs["initial_prompt"], "Vocabulario: LeBlanc.")
            self.assertEqual(tr.call_args.args[0], video)
            self.assertEqual(job_manager.get_job(new)["segments"], _segments())
            res = client.post("/api/subtitles/transcribe", data={"job_id": "ffffffffffff"})
            self.assertEqual(res.status_code, 400)
            res = client.post("/api/subtitles/transcribe", data={"job_id": jid, "initial_prompt": "x" * 2000})
            self.assertEqual(res.status_code, 400)
        finally:
            S.remove(tmp)


if __name__ == "__main__":
    unittest.main()
