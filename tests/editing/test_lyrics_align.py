"""Aligning a song's text to the sung words: matched words take the sung
time, the ones Whisper misheard are spread between their neighbours, backing
vocals in parentheses are never shown, and sung parts the text lacks are
reported."""
import unittest

from modules.editing import lyrics_align as L


def sung(*items):
    return [{"text": w, "start": s, "end": s + 0.3} for w, s in items]


class Align(unittest.TestCase):
    def test_match_fill_and_adlibs(self):
        heard = sung(("Bright", 1.0), ("morn", 1.4), ("ing", 1.7), ("my", 2.2), ("friend", 2.9), ("what", 3.4))
        words, rep = L.align("Bright morning, my old friend (what?)", heard)
        self.assertEqual([w["word"] for w in words], ["Bright", "morning,", "my", "old", "friend"])
        self.assertEqual(words[0]["start"], 1.0)
        self.assertEqual(words[2]["start"], 2.2)
        self.assertEqual(words[3]["start"], 2.5)                        # "old": between "my" and "friend"
        self.assertNotIn("what", [w["word"] for w in words])          # aligned, not shown
        self.assertEqual(rep["matched"], 4)

    def test_reports_a_sung_part_the_text_lacks(self):
        heard = sung(*[(f"w{k}", float(k)) for k in range(10)]) + sung(("end", 20.0))
        _, rep = L.align("end", heard)
        self.assertEqual(rep["uncovered"], [[0.0, 9.3, 10]])
        self.assertEqual(rep["sung_covered"], round(1 / 11, 3))

    def test_no_fill_across_a_long_gap(self):
        heard = sung(("a", 1.0), ("b", 9.0))
        words, _ = L.align("a lost b", heard)
        self.assertEqual([w["word"] for w in words], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
