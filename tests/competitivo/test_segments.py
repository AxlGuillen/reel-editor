"""A long Twitch audio comes down by its HLS segments, many at once (a local HLS
server: segments of different length, each one a different tone)."""
import functools
import http.server
import os
import threading
import unittest
from unittest import mock

import numpy as np

import config
from core import ffmpeg_runner, scheduler
from modules.competitivo import fuente
from tests import synthetic as S

TWITCH = "https://www.twitch.tv/videos/1"
# (tone Hz, seconds): the playlist's times must be the real ones
SEGMENTS = [(300, 10.0), (500, 10.0), (700, 10.3), (900, 9.8), (1100, 10.0), (1300, 10.0), (1500, 10.0)]


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def dominant(path, start, seconds=1.0):
    raw = ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{seconds}", "-i",
                                 path, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"])
    x = np.frombuffer(raw, np.int16).astype(np.float32)
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.argmax(spec) * 8000 / len(x))


class BySegments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = S.tmpdir("hls")
        cls.served = os.path.join(cls.tmp, "served")
        os.makedirs(cls.served)
        t = 0.0
        for k, (hz, secs) in enumerate(SEGMENTS):
            # like Twitch's: the timestamps go on from one segment to the next
            ffmpeg_runner.capture([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "lavfi", "-i",
                                   f"sine=f={hz}:r=48000:d={secs}", "-c:a", "aac", "-b:a", "96k",
                                   "-output_ts_offset", f"{t:.3f}", "-f", "mpegts",
                                   os.path.join(cls.served, f"{k}.ts")])
            t += secs
        handler = functools.partial(Quiet, directory=cls.served)
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        t, cls.playlist = 0.0, []
        for k, (hz, secs) in enumerate(SEGMENTS):
            cls.playlist.append((f"{cls.base}/{k}.ts", t, secs))
            t += secs

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        S.remove(cls.tmp)

    def setUp(self):
        scheduler.reset()
        self.dest = os.path.join(self.tmp, "audio")
        for name in os.listdir(self.tmp):
            if name.startswith("audio"):
                os.remove(os.path.join(self.tmp, name))
        # the synthetic VOD is 70 s long: by segments from the first second
        self.min = mock.patch.object(fuente, "BY_SEGMENTS_MIN_S", 0)
        self.min.start()
        self.addCleanup(self.min.stop)

    def _get(self, a, b, **kw):
        with mock.patch.object(fuente, "_audio_segments", return_value=self.playlist):
            return fuente.bajar_audio(TWITCH, a, b, self.dest, **kw)

    def test_the_range_starts_and_ends_where_it_was_asked(self):
        path = self._get(12.0, 52.0)
        self.assertTrue(path.endswith(".m4a"))
        self.assertAlmostEqual(ffmpeg_runner.probe_duration(path), 40.0, delta=0.1)
        # t=12 s of the VOD is 2 s into the second segment (500 Hz); 20.3 starts the third (700 Hz) ...
        for t_vod, hz in ((13.0, 500), (25.0, 700), (35.0, 900), (45.0, 1100), (51.0, 1300)):
            got = dominant(path, t_vod - 12.0 - 0.5, 0.8)
            self.assertAlmostEqual(got, hz, delta=40, msg=f"at {t_vod} s of the VOD")

    def test_a_range_past_the_end_stops_at_the_end(self):
        path = self._get(60.0, 400.0)
        self.assertAlmostEqual(ffmpeg_runner.probe_duration(path), sum(s for _, s in SEGMENTS) - 60.0, delta=0.1)

    def test_nothing_left_of_the_parts(self):
        self._get(0.0, 70.0)
        self.assertEqual(sorted(x for x in os.listdir(self.tmp) if x.startswith("audio")), ["audio.m4a"])

    def test_it_uses_a_twitch_slot_and_counts_the_wait(self):
        self._get(0.0, 70.0, job_id="job-seg")
        self.assertEqual(scheduler.snapshot()["twitch"]["runs"], 1)

    def test_a_short_audio_or_another_site_goes_the_yt_dlp_way(self):
        calls = []
        with mock.patch.object(fuente, "BY_SEGMENTS_MIN_S", 300), \
                mock.patch.object(fuente, "_audio_segments", side_effect=AssertionError("not by segments")), \
                mock.patch.object(fuente.downloader, "_download_with_retry",
                                  side_effect=lambda opts, url: calls.append(url) or open(self.dest + ".mp3", "wb").close()):
            fuente.bajar_audio(TWITCH, 0, 120, self.dest)                           # under BY_SEGMENTS_MIN_S
            fuente.bajar_audio("https://www.youtube.com/watch?v=x", 0, 3600, self.dest)
        self.assertEqual(len(calls), 2)

    def test_if_the_segments_fail_yt_dlp_takes_over(self):
        calls = []
        with mock.patch.object(fuente, "_audio_segments", side_effect=RuntimeError("sin lista")), \
                mock.patch.object(fuente.downloader, "_download_with_retry",
                                  side_effect=lambda opts, url: calls.append(url) or open(self.dest + ".mp3", "wb").close()):
            path = fuente.bajar_audio(TWITCH, 0, 3600, self.dest)
        self.assertEqual((len(calls), os.path.basename(path)), (1, "audio.mp3"))

    def test_a_segment_that_fails_is_tried_again(self):
        real = fuente._get_segment
        seen = {}

        def flaky(seg_url, path):
            seen[seg_url] = seen.get(seg_url, 0) + 1
            if seen[seg_url] == 1 and seg_url.endswith("/2.ts"):
                raise RuntimeError("no se pudo bajar un segmento del audio: HTTP 503")
            return real(seg_url, path)
        # _get_segment retries by itself on OSError; a RuntimeError means it gave up: yt-dlp takes over
        calls = []
        with mock.patch.object(fuente, "_get_segment", side_effect=flaky), \
                mock.patch.object(fuente.downloader, "_download_with_retry",
                                  side_effect=lambda opts, url: calls.append(url) or open(self.dest + ".mp3", "wb").close()):
            with mock.patch.object(fuente, "_audio_segments", return_value=self.playlist):
                fuente.bajar_audio(TWITCH, 0, 70, self.dest)
        self.assertEqual(len(calls), 1)

    def test_the_playlist_is_read_from_the_audio_only_rendition(self):
        text = "#EXTM3U\n#EXTINF:10.000,\n0.ts\n#EXTINF:10.200,\n1.ts\n#EXTINF:0.814,\n2.ts\n#EXT-X-ENDLIST\n"

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return text.encode()

        class FakeYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=False):
                return {"formats": [{"format_id": "720p", "url": "https://x/720/index.m3u8", "protocol": "m3u8_native"},
                                    {"format_id": "Audio_Only", "url": "https://x/audio_only/index-dvr.m3u8",
                                     "protocol": "m3u8_native"}]}
        with mock.patch.object(fuente.yt_dlp, "YoutubeDL", FakeYDL), \
                mock.patch("urllib.request.urlopen", return_value=Resp()):
            segs = fuente._audio_segments(TWITCH)
        self.assertEqual([(u.rsplit("/", 2)[-2:], round(t, 3), d) for u, t, d in segs],
                         [(["audio_only", "0.ts"], 0.0, 10.0), (["audio_only", "1.ts"], 10.0, 10.2),
                          (["audio_only", "2.ts"], 20.2, 0.814)])


if __name__ == "__main__":
    unittest.main()
