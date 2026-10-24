"""Another creator's webcam instead of Ibai's (offline, synthetic): sync by
the audio, the webcam found in the frame, the composition."""
import os
import subprocess
import tempfile
import unittest

import numpy as np

import config
from core import ffmpeg_runner
from modules.competitivo import creator
from modules.competitivo.schema import CompetitivoParams, EditParams, FinishParams


def _clicks(path, times, dur, sr=8000, extra=0.0):
    """A wav with clicks (the game's sounds) at `times`, and some noise."""
    y = np.random.default_rng(int(extra * 10) + 3).normal(0, 0.01 + extra, int(dur * sr))
    for t in times:
        i = int(t * sr)
        y[i:i + 200] += np.hanning(200) * 0.8
    pcm = (np.clip(y, -1, 1) * 32000).astype("<i2")
    raw = path + ".pcm"
    pcm.tofile(raw)
    subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", raw,
                    path], check=True)
    os.remove(raw)
    return path


class Creator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reelforge-creator-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sync_by_the_game_sounds(self):
        rng = np.random.default_rng(7)
        clicks = np.sort(rng.uniform(0, 200, 120))
        ours = _clicks(os.path.join(self.tmp, "ours.wav"), [t for t in clicks if 60 <= t < 120] and
                       [t - 60 for t in clicks if 60 <= t < 120], 60)
        # theirs starts 23.4 s before ours does (60 − 36.6), with the creator talking over it
        theirs = _clicks(os.path.join(self.tmp, "theirs.wav"), [t - 36.6 for t in clicks if 36.6 <= t < 156.6],
                         120, extra=0.02)
        delta, score = creator.sync(ours, theirs)
        self.assertAlmostEqual(delta, 23.4, delta=0.03)
        self.assertGreater(score, creator.SYNC_MIN_SCORE)
        unrelated = _clicks(os.path.join(self.tmp, "other.wav"), list(rng.uniform(0, 120, 80)), 120)
        self.assertLess(creator.sync(ours, unrelated)[1], creator.SYNC_MIN_SCORE)

    def test_webcam_found_and_composed(self):
        src = os.path.join(self.tmp, "stream.mp4")
        # the game (green, moving) with the creator's webcam (no green, moving) at 1380,700 480x300
        subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error", "-f", "lavfi", "-t", "12", "-i",
                        "color=c=0x2E7A3A:s=1920x1080:r=10", "-f", "lavfi", "-t", "12", "-i",
                        "color=c=0x6A4A3A:s=480x300:r=10", "-filter_complex",
                        "[0:v]noise=alls=40:allf=t[g];[1:v]noise=alls=40:allf=t[c];[g][c]overlay=1380:700[v]",
                        "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", src],
                       check=True)
        cam = creator.detect_webcam(src, 0, 10)
        self.assertIsNotNone(cam)
        x, y, w, h = cam
        self.assertAlmostEqual(x, 1380, delta=70)
        self.assertAlmostEqual(y, 700, delta=70)
        self.assertAlmostEqual(w, 480, delta=130)
        self.assertAlmostEqual(h, 300, delta=130)
        # ours: a plain span; the webcam goes into the camera box
        ours = os.path.join(self.tmp, "ours.mp4")
        subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error", "-f", "lavfi", "-t", "4", "-i",
                        "color=c=0x101010:s=1920x1080:r=10", "-f", "lavfi", "-t", "4", "-i", "sine=f=440:d=4",
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                        ours], check=True)
        out = os.path.join(self.tmp, "composed.mp4")
        box = (1468, 541, 450, 254)
        ffmpeg_runner.capture(creator.compose_command(ours, src, 2.0, (1380, 700, 480, 300), box, out))
        raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-ss", "1", "-i", out, "-frames:v", "1",
                                     "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
        img = np.frombuffer(raw, np.uint8).reshape(1080, 1920, 3).astype(int)
        inside = img[541 + 30:541 + 220, 1468 + 30:1468 + 420].mean(axis=(0, 1))
        outside = img[200:400, 200:400].mean(axis=(0, 1))
        self.assertGreater(inside[0], outside[0] + 40)                         # the webcam's warm color
        self.assertTrue(ffmpeg_runner.has_audio_stream(out))                   # Ibai's audio stays

    def test_params(self):
        base = CompetitivoParams.from_form({"game_ids": "115660588583171787", "vod_url": "https://youtu.be/x"})
        ep = EditParams.from_form({"creator_vod_url": "https://www.twitch.tv/videos/1", "creator_cam": "1480,760,420,300",
                                   "creator_delta_s": "12.5"}, base)
        self.assertEqual((ep.creator_cam, ep.creator_delta_s), ((1480, 760, 420, 300), 12.5))
        fp = FinishParams.from_form({"creator_vod_url": "https://www.twitch.tv/videos/1"}, base)
        self.assertEqual((fp.creator_vod_url, fp.creator_cam), ("https://www.twitch.tv/videos/1", None))
        for bad in ({"creator_vod_url": "nada"}, {"creator_cam": "1,2,3,4"}, {"creator_cam": "1480,760,420,300"},
                    {"creator_vod_url": "https://x.com/v", "creator_cam": "1800,900,400,400"}):
            with self.assertRaises(ValueError, msg=bad):
                EditParams.from_form(bad, base)


if __name__ == "__main__":
    unittest.main()


class ByDifference(unittest.TestCase):
    def test_a_co_stream_webcam(self):
        tmp = tempfile.mkdtemp(prefix="reelforge-creator-")
        try:
            game = "noise=alls=50:allf=t"
            ours = os.path.join(tmp, "ours.mp4")
            theirs = os.path.join(tmp, "theirs.mp4")
            # the same broadcast; ours with Ibai's box, theirs (3 s later) with its webcam bottom-left
            subprocess.run([config.FFMPEG_PATH, "-y", "-v", "error", "-f", "lavfi", "-t", "13", "-i",
                            "testsrc2=s=1920x1080:r=10", "-filter_complex",
                            f"[0:v]split[a][b];[a]trim=3:13,setpts=PTS-STARTPTS,"
                            "drawbox=x=1468:y=541:w=450:h=254:color=0x302060:t=fill[v1];"
                            f"[b]drawbox=x=40:y=700:w=420:h=300:color=0xA07050:t=fill,{game}[v2]",
                            "-map", "[v1]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", ours,
                            "-map", "[v2]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", theirs],
                           check=True)
            cam = creator.webcam_by_difference(ours, theirs, 3.0, (1468, 541, 450, 254), 10.0)
            self.assertIsNotNone(cam)
            x, y, w, h = cam
            self.assertAlmostEqual(x, 40, delta=30)
            self.assertAlmostEqual(y, 700, delta=30)
            self.assertAlmostEqual(w, 420, delta=40)
            self.assertAlmostEqual(h, 300, delta=40)
            # the same stream: nothing differs, no webcam
            self.assertIsNone(creator.webcam_by_difference(ours, ours, 0.0, (1468, 541, 450, 254), 10.0))
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
