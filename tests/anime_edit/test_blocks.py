"""The block planner (phase 4): the source→output frame mapping against a real
render, the hit–beat fit, block choice by curve and motion map, hit-locked
effects, transitions variety, and motion=off leaving the plan untouched."""
import os
import subprocess
import tempfile
import unittest

import numpy as np
from PIL import Image

import config
from core import ffmpeg_runner
from modules.anime_edit import blocks, fit, manga, repro
from modules.anime_edit.schema import FinishParams
from modules.editing import render as R
from modules.editing.edl import Edl, Piece, Segment
from modules.motion import processor as motion
from tests import synthetic as S

SRC_FPS = 24000 / 1001


class Mapping(unittest.TestCase):
    """out_frame against FFmpeg: a 23.976 fps source whose frame k has
    brightness 2k, retimed to 30 fps with exact phase."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("blocks-map")
        cls.src = os.path.join(cls.tmp, "n.mkv")
        stack = np.stack([np.full((36, 64, 3), 2 * k, np.uint8) for k in range(120)])
        subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x36",
                        "-r", "24000/1001", "-i", "-", "-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv420p",
                        cls.src], input=stack.tobytes(), check=True)

    @classmethod
    def tearDownClass(cls):
        S.remove(cls.tmp)

    def shown(self, pieces: list[Piece]) -> list[int]:
        out = os.path.join(self.tmp, "o.mp4")
        R.render(Edl([Segment(self.src, pieces, exact_phase=True)], width=64, height=36, fps=30, fade_out=0),
                 out, None, os.path.join(self.tmp, "w"))
        raw = ffmpeg_runner.capture(motion.decode_command(out, 0, 1000, 30, 64, 36), timeout=120)
        return [round(float(x.mean()) / 2) for x in np.frombuffer(raw, np.uint8).reshape(-1, 36, 64, 3)]

    def check(self, pieces: list[Piece]) -> None:
        shown = self.shown(pieces)
        for j in sorted(set(shown)):
            first = shown.index(j)
            self.assertEqual(fit.out_frame(pieces, 0, 30, j / SRC_FPS, SRC_FPS), first, f"source frame {j}")

    def test_constant_and_slow(self):
        self.check([Piece(0.30, 1.50, 1.2)])
        self.check([Piece(0.50, 0.50 + 1.2 * 0.6, 1.2)])

    def test_fit_lands_the_hit_on_its_frame(self):
        hit = 40 / SRC_FPS
        pieces = fit.fit_hit(0.5, 45, 1.0, hit, 20, 30)
        self.assertAlmostEqual(sum(p.dur for p in pieces), 1.5)
        self.assertEqual(self.shown(pieces)[20], 40)
        self.assertEqual(fit.out_frame(pieces, 0, 30, hit, SRC_FPS), 20)

    def test_fit_clamps_without_moving_the_hit(self):
        hit = 3.0
        pieces = fit.fit_hit(0.0, 30, 1.0, hit, 10, 30)          # 3 s of source into 1/3 s: 9× → 2.5×
        self.assertAlmostEqual(pieces[0].end, hit)
        self.assertAlmostEqual(pieces[0].speed, fit.V_MAX)
        self.assertLessEqual(pieces[1].speed, fit.V_MAX + 1e-9)
        self.assertGreaterEqual(pieces[1].speed, fit.V_MIN - 1e-9)


def fake_map(t0: float, t1: float, hits: list[float], fps: float = SRC_FPS) -> dict:
    n = int(round((t1 - t0) * fps))
    return {"fps": fps, "f0": int(round(t0 * fps)), "frames_count": n,
            "frames": {"cx": [0.4] * n, "cy": [0.6] * n},
            "events": [{"type": "impact", "t": t, "f": int(round(t * fps)), "score": 0.8, "angle": 30.0,
                        "cx": 0.4, "cy": 0.6} for t in hits]}


class Choice(unittest.TestCase):
    def test_curve_and_needs(self):
        data = blocks.load()
        m3 = fake_map(10, 12, [10.5, 11.0, 11.5])
        self.assertEqual(blocks.choose(5, "drop", m3, 10, 12, data)["id"], "rafaga_golpes")
        self.assertEqual(blocks.choose(5, "drop", fake_map(10, 12, []), 10, 12, data)["id"], "rafaga_zooms")
        self.assertEqual(blocks.choose(4, "buildup", None, 0, 1, data)["id"], "estrobo")
        self.assertEqual(blocks.choose(3, "chorus", fake_map(10, 12, [11]), 10, 12, data)["id"], "rampa")
        self.assertEqual(blocks.choose(1, "intro", None, 0, 1, data)["id"], "calma")
        self.assertFalse(data["by_id"]["pantalla_dividida"].get("available", True))

    def test_sections(self):
        bm = {"sections": [{"type": "verse", "start": 0, "end": 10}, {"type": "chorus", "start": 10, "end": 20}],
              "drop": 10.0, "buildups": [[8.0, 10.0]]}
        self.assertEqual([blocks.section_of(t, bm) for t in (2, 9, 12, 25)], ["verse", "buildup", "drop", "verse"])


class Plan(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="reelforge-blocks-")
        for s in repro.P:
            if s[0] == "h":
                Image.new("RGB", (300, 400), (240, 240, 240)).save(os.path.join(self.dir, f"{s[2]}.png"))
        self.bm = {"duration": 70, "sections": [{"type": "verse", "start": 0, "end": 40},
                                                {"type": "chorus", "start": 40, "end": 70}],
                   "drop": 40.0, "buildups": [[36.0, 40.0]]}

    def op(self, motion_mode):
        op = repro.options("unused")
        op.panels_folder = self.dir
        op.motion = motion_mode
        return op

    def map_for(self, a, b):
        return fake_map(a, b, [a + 0.6, a + 0.9, a + 1.2])

    def test_off_is_phase_two(self):
        base, _ = manga.plan("ep.mkv", (1920, 1080), self.bm, repro.takes(), self.op("off"))
        again, info = manga.plan("ep.mkv", (1920, 1080), self.bm, repro.takes(), self.op("off"),
                                 map_for=self.map_for)
        self.assertEqual(base.to_dict(), again.to_dict())
        self.assertNotIn("motion", info)

    def test_on_locks_effects_and_keeps_the_cuts(self):
        off, info_off = manga.plan("ep.mkv", (1920, 1080), self.bm, repro.takes(), self.op("off"))
        on, info = manga.plan("ep.mkv", (1920, 1080), self.bm, repro.takes(), self.op("on"), map_for=self.map_for)
        self.assertEqual(info["cuts"], info_off["cuts"])                 # the rhythm of cuts does not move
        report = blocks.check(info["motion"], 30)
        self.assertGreater(report["locked"], 20)
        self.assertLessEqual(report["max_offset_frames"], 1)
        self.assertTrue(report["drop_above_buildup"], report)
        self.assertGreaterEqual(report["transition_types"], 3)
        self.assertLessEqual(report["max_per_type"], 6)
        # every panel pause freezes its take's final last frame
        for i, seg in enumerate(on.segments):
            if seg.label.startswith("panel"):
                prev = on.segments[i - 1].pieces[-1]
                n = round(prev.dur * 30)
                self.assertAlmostEqual(seg.pieces[0].start, prev.start + (n - 1) / 30 * prev.speed, places=6)

    def test_explicit_blocks(self):
        op = self.op("on")
        op.blocks = [{"take": 0, "block": "estrobo"}]
        _, info = manga.plan("ep.mkv", (1920, 1080), self.bm, repro.takes(), op, map_for=self.map_for)
        self.assertEqual(info["motion"]["blocks"][0]["block"], "estrobo")
        op.blocks = [{"take": 0, "block": "pantalla_dividida"}]
        with self.assertRaisesRegex(ValueError, "no disponible"):
            manga.plan("ep.mkv", (1920, 1080), self.bm, repro.takes(), op, map_for=self.map_for)


class Params(unittest.TestCase):
    def test_motion_switch(self):
        base = {"variants": ["manga"], "takes": [{"t": 1, "beats": None}]}
        self.assertEqual(FinishParams.from_form(base, set()).motion, "off")
        self.assertEqual(FinishParams.from_form({**base, "motion": "on"}, set()).motion, "on")
        with self.assertRaisesRegex(ValueError, "motion"):
            FinishParams.from_form({**base, "motion": "maybe"}, set())


if __name__ == "__main__":
    unittest.main()
