"""Tests of the editing engine (EDL, timing, effects, titles and render)."""
import os
import subprocess
import time
import unittest
from unittest import mock

import numpy as np

from core import ffmpeg_runner
from modules.editing import effects, timing, titles
from modules.editing import render as R
from modules.editing.edl import Edl, Event, Piece, Segment, Text
from tests import synthetic as S


class Ramp(unittest.TestCase):
    def test_uses_all_the_source_in_the_requested_duration(self):
        for profile in timing.PROFILES:
            pieces = timing.ramp(10.0, 13.0, 2.0, profile)
            self.assertAlmostEqual(sum(p.dur for p in pieces), 2.0, places=6, msg=profile)
            self.assertAlmostEqual(pieces[0].start, 10.0)
            self.assertAlmostEqual(pieces[-1].end, 13.0)
            for a, b in zip(pieces, pieces[1:]):
                self.assertAlmostEqual(a.end, b.start)

    def test_relative_speed_of_the_profile(self):
        slow, fast = timing.ramp(0, 3, 2, "velocity")
        self.assertAlmostEqual(fast.speed / slow.speed, 1.6 / 0.35, places=4)

    def test_no_source_is_a_freeze(self):
        (p,) = timing.ramp(5, 5, 0.4)
        self.assertTrue(p.frozen)
        self.assertEqual(p.dur, 0.4)


class Impact(unittest.TestCase):
    def test_freeze_slow_and_rest(self):
        pieces = timing.impact(2.0, 4.0, 1.0, freeze=0.05, slow=0.25, slow_speed=0.4)
        self.assertTrue(pieces[0].frozen)
        self.assertAlmostEqual(pieces[1].speed, 0.4, places=6)
        self.assertAlmostEqual(sum(p.dur for p in pieces), 1.0, places=6)
        self.assertAlmostEqual(pieces[-1].end, 4.0)

    def test_short_span_does_not_overflow(self):
        pieces = timing.impact(0, 0.1, 0.1)
        self.assertAlmostEqual(sum(p.dur for p in pieces), 0.1, places=6)
        self.assertLessEqual(pieces[0].dur, 0.03 + 1e-9)


class Quantize(unittest.TestCase):
    def test_edges_land_on_the_ideal_frame_without_drift(self):
        # 40 pieces of 0.51 s at 60 fps: the ideal edge k is k·0.51 s.
        edl = Edl([Segment("x", [Piece(0, 0.51, 0.51) for _ in range(4)]) for _ in range(10)], fps=60)
        timing.quantize(edl)
        t = 0.0
        k = 0
        for s in edl.segments:
            for p in s.pieces:
                t += p.dur
                k += 1
                self.assertAlmostEqual(t * 60, round(k * 0.51 * 60), places=6)
                self.assertAlmostEqual(p.dur * 60, round(p.dur * 60), places=6)

    def test_drops_zero_frame_pieces(self):
        edl = Edl([Segment("x", [Piece(0, 1, 1), Piece(1, 1.001, 0.001)]), Segment("y", [Piece(0, 0, 0.002)])],
                  fps=30)
        timing.quantize(edl)
        self.assertEqual(len(edl.segments), 1)
        self.assertEqual(len(edl.segments[0].pieces), 1)

    def test_clamp_keeps_the_duration(self):
        p = timing.clamp_speeds([Piece(0, 10, 1), Piece(0, 0.01, 1)])
        self.assertEqual([x.dur for x in p], [1, 1])
        self.assertAlmostEqual(p[0].speed, timing.MAX_SPEED)
        self.assertAlmostEqual(p[1].speed, timing.MIN_SPEED)


class Validation(unittest.TestCase):
    def test_errors(self):
        cases = [
            Edl([]),
            Edl([Segment("x", [])]),
            Edl([Segment("x", [Piece(0, 1, 0)])]),
            Edl([Segment("x", [Piece(2, 1, 1)])]),
            Edl([Segment("x", [Piece(0, 1, 1)])], events=[Event(0, "explosion")]),
            Edl([Segment("x", [Piece(0, 1, 1)])], texts=[Text(1, 1, "a")]),
            Edl([Segment("x", [Piece(0, 1, 1)])], texts=[Text(0, 1, "a", "comic")]),
        ]
        for edl in cases:
            with self.assertRaises(ValueError):
                edl.validate()


class Effects(unittest.TestCase):
    EV = [Event(1.0, "flash"), Event(2.0, "zoom", 0.5), Event(3.0, "shake"), Event(3.0, "rgb")]

    def test_no_events_no_filter(self):
        self.assertEqual(effects.flash([]), "")
        self.assertEqual(effects.camera([], 1080, 1920, 60), "")
        self.assertEqual(effects.rgb([]), "")

    def test_filters(self):
        self.assertIn("eval=frame", effects.flash(self.EV))
        cam = effects.camera(self.EV, 1080, 1920, 60)
        self.assertTrue(cam.startswith("zoompan="))
        self.assertIn("s=1080x1920:fps=60", cam)
        self.assertIn("enable='between(t,3,3.18)'", effects.rgb(self.EV))

    def test_looks(self):
        self.assertEqual(effects.color("neutral"), "")
        self.assertIn("colorbalance", effects.color("golden"))
        with mock.patch("os.path.isfile", return_value=True):
            self.assertTrue(effects.color("vivid", "x.cube").startswith("lut3d=file='assets/luts/x.cube'"))
        with self.assertRaises(ValueError):
            effects.color("sepia")
        for bad in ("no-existe.cube", "it's.cube"):           # missing, or a quote FFmpeg can't escape
            with self.assertRaises(ValueError):
                effects.color("vivid", bad)


class Titles(unittest.TestCase):
    def test_ass(self):
        ass = titles.build_ass([Text(0, 1, "survive", "word"), Text(1, 2, "Caliste", "kill", sub="Xayah"),
                                Text(2, 3, "quadra kill", "multikill")], 1080, 1920)
        self.assertIn("PlayResX: 1080", ass)
        self.assertIn("YCbCr Matrix: None", ass)
        self.assertEqual(ass.count("SURVIVE"), titles.BANDS + 1)       # bands + outline
        self.assertIn("\\clip(", ass)
        self.assertIn("XAYAH", ass)
        self.assertEqual(ass.count("QUADRA KILL"), titles.BANDS + 1)

    def test_long_text_shrinks(self):
        ass = titles.build_ass([Text(0, 1, "supercalifragilistico", "word")], 1080, 1920)
        self.assertIn("\\fscx", ass.split("\n")[-2])

    def test_no_texts_no_file(self):
        self.assertIsNone(titles.write([], 1080, 1920))


class Render(unittest.TestCase):
    """A short edit end to end: three shots (ramp, impact and vertical with a
    blurred background), music, flash, zoom, shake, RGB and titles."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("editing")
        cls.video = S.shots_video(os.path.join(cls.tmp, "v.mp4"), [2, 2, 2])
        cls.music = S.song(os.path.join(cls.tmp, "m.wav"), bpm=120, dur=12, loud_from=2)
        segs = [Segment(cls.video, timing.ramp(0.2, 1.8, 1.0, "velocity"), "a"),
                Segment(cls.video, timing.impact(2.2, 3.6, 1.5), "b"),
                Segment(cls.video, timing.ramp(4.1, 5.9, 1.3), "c", vf=R.vf_vertical_blur(540, 960))]
        cls.edl = Edl(segs, width=540, height=960, fps=30, music=cls.music, music_start=1.0,
                      events=[Event(0.5, "flash"), Event(1.0, "zoom"), Event(2.5, "shake"), Event(2.5, "rgb")],
                      texts=[Text(0.1, 0.9, "survive"), Text(1.0, 2.0, "Caliste", "kill", sub="Xayah")],
                      look="golden", glow=0.4, vignette=True, original_volume=0.2)
        cls.out = os.path.join(cls.tmp, "out.mp4")
        cls.info = R.render(cls.edl, cls.out, None, os.path.join(cls.tmp, "work"))

    @classmethod
    def tearDownClass(cls):
        S.remove(cls.tmp)

    def test_exact_duration_and_format(self):
        self.assertEqual(self.info["dur"], 3.8)
        self.assertAlmostEqual(ffmpeg_runner.probe_duration(self.out), 3.8, delta=0.05)
        self.assertEqual(ffmpeg_runner.probe_resolution(self.out), (540, 960))
        self.assertEqual(ffmpeg_runner.probe_fps(self.out), 30)
        self.assertTrue(ffmpeg_runner.has_audio_stream(self.out))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "work")))

    def test_flash_lands_on_its_frame(self):
        fr = S.gray_frames(self.out, 30)
        brightness = fr.mean(axis=(1, 2))
        # the flash is at 0.5 s, in the middle of the first shot (frame 15 at 30 fps)
        self.assertEqual(int(np.argmax(brightness[5:25])) + 5, 15)
        self.assertGreater(brightness[15], brightness[12] + 60)

    def test_cuts_land_where_planned(self):
        fr = S.gray_frames(self.out, 30)
        d = np.abs(np.diff(fr.mean(axis=(1, 2))))
        # shot 1 → 2 at frame 30 (with the flash on top) and 2 → 3 at frame 75
        self.assertGreater(d[74], 10)


class DenseEdit(unittest.TestCase):
    """220 events (anime_edit's MAX_EVENTS): the final command passes Windows'
    32,767-character limit, so its graph has to go through a file."""

    def test_renders(self):
        tmp = S.tmpdir("dense")
        try:
            video = S.shots_video(os.path.join(tmp, "v.mp4"), [2, 2])
            segs = [Segment(video, timing.ramp(0.1, 1.9, 1.0), "a"),
                    Segment(video, timing.ramp(2.1, 3.9, 1.0), "b")]
            events = [Event(round(0.02 + i * 0.0085, 3), "shake") for i in range(220)]
            edl = Edl(segs, width=360, height=640, fps=30, events=events)
            out = os.path.join(tmp, "out.mp4")
            cmd = R.build_final(os.path.join(tmp, "list.txt"), edl, out, None, 1.0, None)
            self.assertGreater(len(subprocess.list2cmdline(cmd)), 32767)
            info = R.render(edl, out, None, os.path.join(tmp, "work"))
            self.assertAlmostEqual(ffmpeg_runner.probe_duration(out), info["dur"], delta=0.05)
        finally:
            S.remove(tmp)

    def test_a_failing_shot_cancels_the_rest(self):
        tmp = S.tmpdir("fail")
        try:
            video = S.shots_video(os.path.join(tmp, "v.mp4"), [2])
            segs = [Segment(video, timing.ramp(0.1, 1.9, 1.0), str(i)) for i in range(12)]
            edl = Edl(segs, width=360, height=640, fps=30)
            calls = []

            def fail(*args, **kwargs):
                calls.append(1)
                time.sleep(0.3)
                raise RuntimeError("boom")

            with mock.patch.object(R.ffmpeg_runner, "capture", side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    R.render(edl, os.path.join(tmp, "out.mp4"), None, os.path.join(tmp, "work"))
            self.assertLess(len(calls), len(segs))             # the queued shots never started
            self.assertFalse(os.path.exists(os.path.join(tmp, "work")))
        finally:
            S.remove(tmp)


def _rms_db(path: str, start: float, dur: float) -> float:
    import re
    err = subprocess.run([R.config.FFMPEG_PATH, "-v", "info", "-ss", f"{start}", "-t", f"{dur}", "-i", path,
                          "-af", "astats=measure_overall=RMS_level:measure_perchannel=none", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(re.findall(r"RMS level dB: (-?[\d.]+|-inf)", err)[-1].replace("-inf", "-200"))


def _rgb(path: str, t: float, w: int, h: int):
    raw = subprocess.run([R.config.FFMPEG_PATH, "-v", "error", "-ss", f"{t}", "-i", path, "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(int)


class ActsEngine(unittest.TestCase):
    """What the "acts" edit needs from the engine: volume automation, push-in,
    held zoom steps aimed off-center, whip, black and the inverted subtitles."""

    def test_gain_automation(self):
        tmp = S.tmpdir("gain")
        try:
            video = S.shots_video(os.path.join(tmp, "v.mp4"), [4])          # 330 Hz tone
            seg = [Segment(video, [Piece(0.0, 3.6, 3.6)], "a")]
            # the original at 100 % → 15 % from 1.2 s (a 50 ms ramp) → 0 from 2.4 s
            edl = Edl(seg, width=360, height=640, fps=30,
                      original_gain=[(0.0, 1.0), (1.2, 1.0), (1.25, 0.15), (2.4, 0.15), (2.45, 0.0)])
            out = os.path.join(tmp, "out.mp4")
            R.render(edl, out, None, os.path.join(tmp, "work"))
            full, low, mute = _rms_db(out, 0.2, 0.8), _rms_db(out, 1.4, 0.8), _rms_db(out, 2.6, 0.8)
            self.assertAlmostEqual(low - full, -16.5, delta=1.0)         # 15 % = −16.5 dB
            self.assertLess(mute, -80)
            self.assertIn("eval=frame", effects.gain([(0, 1), (1, 0.2)]))
            self.assertEqual(effects.gain([(0, 0.3)]), "volume=0.3")
        finally:
            S.remove(tmp)

    def test_zoom_aims_at_the_action(self):
        # A gradient (R = x, G = y): the frame center after the zoom says where it aimed.
        tmp = S.tmpdir("aim")
        try:
            src = os.path.join(tmp, "grad.mp4")
            subprocess.run([R.config.FFMPEG_PATH, "-v", "error", "-y", "-f", "lavfi", "-i",
                            "color=c=black:s=360x640:r=30:d=3,format=rgb24,geq=r='X/W*255':g='Y/H*255':b=0",
                            "-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv444p", src], check=True)
            ev = [Event(0.5, "hold", dur=1.0, level=0.3, x=0.62, y=0.4),     # aimed
                  Event(1.8, "hold", dur=0.8, level=0.3)]                    # centered
            cam = effects.camera(ev, 360, 640, 30)
            out = os.path.join(tmp, "out.mp4")
            subprocess.run([R.config.FFMPEG_PATH, "-v", "error", "-y", "-i", src, "-vf", cam,
                            "-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv444p", out], check=True)
            aimed, centered, rest = _rgb(out, 1.0, 360, 640), _rgb(out, 2.2, 360, 640), _rgb(out, 2.8, 360, 640)
            self.assertAlmostEqual(aimed[320, 180, 0] / 255, 0.62, delta=0.03)
            self.assertAlmostEqual(aimed[320, 180, 1] / 255, 0.40, delta=0.03)
            self.assertAlmostEqual(centered[320, 180, 0] / 255, 0.5, delta=0.03)
            # zoomed in: the edges show less of the frame
            self.assertGreater(centered[320, 0, 0], 20)
            self.assertLess(rest[320, 0, 0], 8)                              # back to 1×
        finally:
            S.remove(tmp)

    def test_push_whip_black_and_inverted_subtitles(self):
        tmp = S.tmpdir("acts")
        try:
            video = S.shots_video(os.path.join(tmp, "v.mp4"), [2, 2], w=360, h=640, fps=30)
            segs = [Segment(video, [Piece(0.0, 1.9, 1.9)], "a"), Segment(video, [Piece(2.1, 3.9, 1.8)], "b")]
            ev = [Event(0.0, "push", dur=1.5, level=0.12), Event(1.9, "whip", 0.8, 0.12),
                  Event(3.3, "black", dur=0.3)]
            words = [{"word": "vamos", "start": 0.2, "end": 0.6}, {"word": "ganar", "start": 0.7, "end": 1.2}]
            edl = Edl(segs, width=360, height=640, fps=30, events=ev, subtitle_words=words,
                      subtitle_style={"inv_size": 220, "inv_y": 960, "font": "Montserrat-ExtraBold.ttf"},
                      subtitle_until=1.4)
            for f in (effects.whip_blur(ev), effects.blackout(ev)):
                self.assertIn("enable=", f)
            out = os.path.join(tmp, "out.mp4")
            R.render(edl, out, None, os.path.join(tmp, "work"))
            self.assertLess(_rgb(out, 3.45, 360, 640).mean(), 6)               # black
            clean = os.path.join(tmp, "clean.mp4")
            plain = Edl([Segment(video, [Piece(0.0, 1.9, 1.9)], "a"), Segment(video, [Piece(2.1, 3.9, 1.8)], "b")],
                        width=360, height=640, fps=30, events=[e for e in ev if e.kind != "black"])
            R.render(plain, clean, None, os.path.join(tmp, "work2"))
            band = (slice(280, 360), slice(60, 300))
            during = np.abs(_rgb(out, 0.95, 360, 640)[band] - _rgb(clean, 0.95, 360, 640)[band]).mean()
            after = np.abs(_rgb(out, 1.6, 360, 640)[band] - _rgb(clean, 1.6, 360, 640)[band]).mean()
            self.assertGreater(during, 15)          # the inverted word is there…
            self.assertLess(after, 3)               # …and never after subtitle_until
        finally:
            S.remove(tmp)

    def test_validation(self):
        seg = [Segment("x.mp4", [Piece(0, 1, 1)])]
        with self.assertRaises(ValueError):
            Edl(seg, music_gain=[(1.0, 1.0), (0.5, 0.2)]).validate()
        with self.assertRaises(ValueError):
            Edl(seg, original_gain=[(0.0, -1.0)]).validate()
        self.assertTrue(Edl(seg, original_gain=[(0, 0), (1, 0.5)]).uses_original)
        self.assertFalse(Edl(seg, original_gain=[(0, 0)], original_volume=1.0).uses_original)


class Formats(unittest.TestCase):
    def test_vertical_blur_is_a_branched_graph(self):
        vf = R.vf_vertical_blur(1080, 1920)
        self.assertIn("split[vb_a][vb_b]", vf)
        self.assertIn("overlay=(W-w)/2:(H-h)/2", vf)
        self.assertEqual(R.vf_cover(1080, 1920).count("crop=1080:1920"), 1)
