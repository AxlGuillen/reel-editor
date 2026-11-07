"""«La TF explicada» engine: the layout and its broadcast → output mapping,
the clock-based pieces, the fight timeline from a dossier and the screen
words in the script's spelling (all pure, no network)."""
import unittest

from modules.tf_explained import dossier, layout, montage, plan


class Layout(unittest.TestCase):
    def test_to_output(self):
        crop = (360, 185, 800, 630)
        self.assertEqual(layout.to_output((360, 185), crop), (0, layout.GP_TOP))
        self.assertEqual(layout.to_output((1160, 815), crop), (1080, layout.GP_TOP + layout.GP_H))
        self.assertEqual(layout.minimap_to_output((0, 0)), (15, 1255))
        self.assertEqual(layout.minimap_to_output((284, 276)), (665, 1887))

    def test_variants(self):
        full = layout.layout(270, 116, 900, 708)
        self.assertTrue(full.startswith("[0:v]split=4"))
        self.assertIn("crop=900:708:270:116", full)
        self.assertIn("crop=284:276:1636:804", full)
        self.assertFalse(full.endswith("]"))                     # last chain open (the engine appends)
        self.assertNotIn("1636", layout.layout(0, 0, 1000, 788, minimap=False))
        self.assertIn("pad=1080:850", layout.layout(0, 0, 1920, 1080, minimap=False, fit=True))


class Plan(unittest.TestCase):
    def test_pieces(self):
        s = plan.Span("mid.mp4", 1486)
        self.assertEqual(plan.parse_clock("26:50.8"), 1610.8)
        p = s.run("26:50.35", "26:50.8", 0.8)
        self.assertAlmostEqual(p.start, 124.35)
        self.assertAlmostEqual(p.end, 124.8)
        self.assertTrue(s.freeze("26:50.8", 5.5).frozen)
        seg = s.shot("vf", p, label="gancho")
        self.assertEqual((seg.source, seg.label, seg.vf), ("mid.mp4", "gancho", "vf"))


def _frame(c, k_a=0, d_b=0, hp_a=1000, hp_b=1000):
    return {"clock": c, "fmt": f"{int(c // 60)}:{c % 60:04.1f}", "teams": {},
            "players": {"1": {"kills": k_a, "deaths": 0, "assists": 0, "currentHealth": hp_a, "maxHealth": 1000},
                        "6": {"kills": 0, "deaths": d_b, "assists": 0, "currentHealth": hp_b, "maxHealth": 1000}}}


class Timeline(unittest.TestCase):
    def test_deaths_and_hp(self):
        d = {"who": {"1": {"side": "blue", "name": "T1 Peyz", "champion": "Yunara"},
                     "6": {"side": "red", "name": "KC Caliste", "champion": "Ezreal"}},
             "frames": [_frame(100.0), _frame(100.4, hp_b=300), _frame(101.2, k_a=1, d_b=1, hp_b=0)]}
        tl = dossier.timeline(d)
        self.assertEqual(tl["events"], [{"clock": 101.2, "fmt": "1:41.2", "died": ["6"], "killed": ["1"],
                                         "assisted": []}])
        self.assertEqual([r["hp"]["6"] for r in tl["hp"]], [100, 0])          # one row per second
        self.assertEqual(dossier.hp_at(d, 100.5)["6"], 30)


class ScreenWords(unittest.TestCase):
    def test_script_spelling_on_whisper_times(self):
        segs = [{"words": [{"word": "pace", "start": 0.0, "end": 0.4}, {"word": "flasea", "start": 0.4, "end": 0.9},
                           {"word": "adelante", "start": 0.9, "end": 1.5}]}]
        out = montage.screen_words(segs, "⟦lamina:a⟧ Peyz flashea para adelante")
        words = [w["word"] for w in out[0]["words"]]
        self.assertEqual(words[0], "Peyz")
        self.assertIn("flashea para", " ".join(words))
        self.assertEqual(out[0]["words"][0]["start"], 0.0)
        self.assertEqual(out[0]["end"], 1.5)


class Sheets(unittest.TestCase):
    def test_crop_sheets(self):
        import os
        from PIL import Image
        from modules.tf_explained import sheets
        from tests import synthetic as S
        tmp = S.tmpdir("tfsheets")
        try:
            v = S.motion_video(os.path.join(tmp, "v.mp4"), frames=48, w=640, h=360, fps=24)
            out = sheets.crop_sheets(v, c0=100.0, a=100.0, b=101.5, step=0.5, out_prefix=os.path.join(tmp, "z"),
                                     crop=(100, 50, 320, 180))
            self.assertEqual(len(out), 1)                            # 4 tiles → one 2×2 sheet
            self.assertEqual(Image.open(out[0]).size, (1930, 2 * 540 + 10))
        finally:
            S.remove(tmp)


if __name__ == "__main__":
    unittest.main()
