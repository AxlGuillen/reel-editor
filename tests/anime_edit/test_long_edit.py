"""The long edit's plan file: the example template loads, its sections add
up, a wrong sum is caught, and lyric blocks sung twice are repeated."""
import os
import unittest

from modules.anime_edit import long_edit

EXAMPLE = os.path.join(os.path.dirname(long_edit.__file__), "examples", "long_edit_magna_vs_dante.json")


class Plan(unittest.TestCase):
    def test_example(self):
        plan = long_edit.load(EXAMPLE)
        items = long_edit.takes(plan)
        self.assertEqual(sum(1 for it in items if "t" in it), 121)          # + 10 panels: 131 shots
        self.assertEqual(plan["grid"], {"bpm": 128.0, "phase": 0.05})

    def test_wrong_sum(self):
        plan = {"sections": [{"name": "x", "beats": 8, "items": [{"t": 1, "beats": 4}]}]}
        with self.assertRaises(ValueError):
            long_edit.takes(plan)

    def test_repeats(self):
        text = "a\nb\n\nc\nd\n"
        self.assertEqual(long_edit.with_repeats(text, [[3, 4]]), "a\nb\n\nc\nd\n\nc\nd\n")
        self.assertEqual(long_edit.with_repeats(text, []), text)


if __name__ == "__main__":
    unittest.main()
