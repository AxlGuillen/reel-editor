"""The reference analyzer (phase 6): is an edit cut on a grid, and the camera
moves (zoom punches, shakes) and inverted runs read from a motion map."""
import unittest

import numpy as np

from modules.motion import reference


def fake_map(n: int, zoom=None, dx=None, inverted=None, fps: float = 30.0) -> dict:
    return {"fps": fps, "f0": 0, "frames_count": n,
            "frames": {"cam_zoom": zoom or [1.0] * n, "cam_dx": dx or [0.0] * n, "cam_dy": [0.0] * n,
                       "inverted": inverted or [False] * n}}


class Grid(unittest.TestCase):
    def test_on_grid_and_off_grid(self):
        beat = 60 / 120
        on = [0.1 + k * beat for k in range(40)]
        g = reference.grid_alignment(on, 30, (100, 140))
        self.assertGreaterEqual(g["on_grid"], 0.99)
        self.assertAlmostEqual(g["best_bpm"], 120, delta=1.0)   # ±2 frames lets ±0.8 BPM fit 40 beats
        rng = np.random.default_rng(3)
        off = sorted(rng.uniform(0, 20, 40))
        g2 = reference.grid_alignment(off, 30, (100, 140))
        self.assertLess(g2["on_grid"], 0.6)
        self.assertAlmostEqual(g2["chance"], 2 * (2 / 30) / (60 / g2["best_bpm"]), places=3)


class Camera(unittest.TestCase):
    def test_zoom_punch_and_shake(self):
        n = 60
        zoom = [1.0] * n
        zoom[10:13] = [1.03, 1.03, 1.03]                 # a 3-frame zoom in
        dx = [0.0] * n
        for k in range(30, 38):
            dx[k] = 10.0 if k % 2 else -10.0             # back and forth: a shake
        ev = reference.camera_events(fake_map(n, zoom=zoom, dx=dx))
        kinds = [(e["type"], e["f"]) for e in ev]
        self.assertIn(("zoom", 10), kinds)
        self.assertTrue(any(t == "shake" and 26 <= f <= 31 for t, f in kinds))
        self.assertEqual(sum(t == "zoom" for t, _ in kinds), 1)

    def test_inverted_runs(self):
        inv = [False] * 20
        inv[5:7] = [True, True]
        runs = reference.inverted_runs(fake_map(20, inverted=inv))
        self.assertEqual([(r["f"], r["frames"]) for r in runs], [(5, 2)])


if __name__ == "__main__":
    unittest.main()
