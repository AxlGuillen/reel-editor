"""The snowball's pure parts: mentions weighted by views and the top with a
cap per profile."""
import unittest

from modules.research import tiktok


class Snowball(unittest.TestCase):
    VIDEOS = [{"handle": "a", "views": 100, "title": "edit ib @Bob. inspo @carl"},
              {"handle": "a", "views": 50, "title": "x @bob"},
              {"handle": "b", "views": 70, "title": "y"},
              {"handle": "c", "views": None, "title": "@dan"}]

    def test_mentions(self):
        self.assertEqual(tiktok.mentions(self.VIDEOS, known={"carl"}), [("bob", 150), ("dan", 0)])

    def test_top_one_per_profile(self):
        self.assertEqual([v["views"] for v in tiktok.top(self.VIDEOS, n=5)], [100, 70])


if __name__ == "__main__":
    unittest.main()
