"""The VOD source (YouTube or Twitch) and the anchor's confidence, offline:
start time and platform, a YouTube stream still live, sections that come
without video, and an anchor too far from the platform's formula."""
import os
import subprocess
import tempfile
import unittest
from datetime import timezone
from unittest import mock

import config
from core import job_manager
from modules.competitivo import fuente, kills, processor
from modules.competitivo.schema import CompetitivoParams, FinishParams
from tests import synthetic as S

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "kc_c9_g1.json")
TWITCH = "https://www.twitch.tv/videos/2908607306"

# What yt-dlp says (the relevant keys), measured on 2026-10-23 with the same stream.
YT_LIVE = {"extractor_key": "Youtube", "live_status": "is_live", "is_live": True,
           "release_timestamp": 1792771950, "timestamp": 1792758850, "title": "Worlds"}
YT_VOD = {**YT_LIVE, "live_status": "was_live", "is_live": False, "duration": 30000}
TW_VOD = {"extractor_key": "TwitchVod", "live_status": "is_live", "is_live": True,
          "release_timestamp": None, "timestamp": 1792771914, "duration": 18635, "title": "Worlds"}


class VodInfo(unittest.TestCase):
    def test_youtube_uses_the_start_of_the_stream(self):
        v = fuente.vod_info(YT_VOD)
        self.assertEqual(v["platform"], "youtube")
        self.assertEqual(v["inicio_utc"].timestamp(), 1792771950)          # not the scheduled time
        self.assertEqual(v["inicio_utc"].tzinfo, timezone.utc)

    def test_twitch_only_has_timestamp_and_may_still_be_growing(self):
        v = fuente.vod_info(TW_VOD)
        self.assertEqual((v["platform"], v["inicio_utc"].timestamp(), v["duracion"]),
                         ("twitch", 1792771914, 18635.0))

    def test_youtube_still_live_is_refused(self):
        for info in (YT_LIVE, {**YT_VOD, "is_live": True}, {**YT_VOD, "live_status": "is_live"}):
            with self.assertRaises(RuntimeError) as ctx:
                fuente.vod_info(info)
            self.assertIn("sigue en vivo", str(ctx.exception))
            self.assertIn("Twitch", str(ctx.exception))

    def test_without_a_start_time(self):
        with self.assertRaises(RuntimeError) as ctx:
            fuente.vod_info({"extractor_key": "TwitchVod", "title": "x"})
        self.assertEqual(str(ctx.exception),
                         "Ese video no trae la hora de inicio del directo: ¿es el VOD de un directo?")

    def test_platform(self):
        self.assertEqual(fuente.platform_of({"extractor_key": "YoutubeTab"}), "youtube")
        self.assertEqual(fuente.platform_of({"extractor": "twitch:vod"}), "twitch")
        self.assertEqual(fuente.platform_of({"extractor_key": "Kick"}), "kick")

    def test_info_vod_reads_through_yt_dlp(self):
        ydl = mock.MagicMock()
        ydl.__enter__.return_value.extract_info.return_value = TW_VOD
        with mock.patch.object(fuente.yt_dlp, "YoutubeDL", return_value=ydl):
            self.assertEqual(fuente.info_vod(TWITCH)["platform"], "twitch")


class SpanWithoutVideo(unittest.TestCase):
    """bajar_tramo: a section without video is downloaded once more; the
    second time it raises SpanWithoutVideo (the job skips that clip)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="reelforge-fuente-")
        self.dest = os.path.join(self.tmp, "tramo_01")
        self.calls = 0

    def tearDown(self):
        S.remove(self.tmp)

    def _download(self, outcomes):
        """_download_with_retry that leaves, in turn, "video", "audio" or
        raises FFmpeg's error (what yt-dlp does when the merge has no streams)."""
        def fake(opts, url):
            kind = outcomes[self.calls]
            self.calls += 1
            if kind == "error":
                raise RuntimeError("ERROR: Postprocessing: Output file does not contain any stream")
            if kind == "video":
                S.shots_video(self.dest + ".mp4", [1.0], w=320, h=180)
            else:
                subprocess.run([config.FFMPEG_PATH, "-v", "error", "-y", "-f", "lavfi", "-i",
                                "sine=frequency=330:duration=1", "-c:a", "aac", self.dest + ".mp4"], check=True)
        return mock.patch.object(fuente.downloader, "_download_with_retry", side_effect=fake)

    def test_first_try(self):
        with self._download(["video"]):
            self.assertEqual(fuente.bajar_tramo(TWITCH, 10, 11, self.dest), self.dest + ".mp4")
        self.assertEqual(self.calls, 1)

    def test_retried_once(self):
        for first in ("audio", "error"):
            self.calls = 0
            with self._download([first, "video"]):
                path = fuente.bajar_tramo(TWITCH, 10, 11, self.dest)
            self.assertEqual(self.calls, 2, first)
            self.assertIsNotNone(processor.ffmpeg_runner.probe_video_stream(path))

    def test_twice_without_video(self):
        with self._download(["audio", "error"]), self.assertRaises(fuente.SpanWithoutVideo):
            fuente.bajar_tramo(TWITCH, 10, 11, self.dest)
        self.assertEqual(os.listdir(self.tmp), [])                          # nothing half-made left

    def test_other_errors_are_not_retried_on_youtube(self):
        def broken(opts, url):
            self.calls += 1
            raise RuntimeError("HTTP Error 404: Not Found")
        with mock.patch.object(fuente.downloader, "_download_with_retry", side_effect=broken), \
                self.assertRaises(RuntimeError) as ctx:
            fuente.bajar_tramo("https://www.youtube.com/watch?v=x", 10, 11, self.dest)
        self.assertNotIsInstance(ctx.exception, fuente.SpanWithoutVideo)
        self.assertEqual(self.calls, 1)

    def test_twitch_retries_twice(self):
        def flaky(opts, url):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("HTTP Error 502: Bad Gateway")
            S.shots_video(self.dest + ".mp4", [1.0], w=320, h=180)
        with mock.patch.object(fuente, "RETRY_WAIT_S", 0), \
                mock.patch.object(fuente.downloader, "_download_with_retry", side_effect=flaky):
            self.assertEqual(fuente.bajar_tramo(TWITCH, 10, 11, self.dest), self.dest + ".mp4")
        self.assertEqual(self.calls, 3)
        self.calls = 0

        def broken(opts, url):
            self.calls += 1
            raise RuntimeError("HTTP Error 502: Bad Gateway")
        with mock.patch.object(fuente, "RETRY_WAIT_S", 0), \
                mock.patch.object(fuente.downloader, "_download_with_retry", side_effect=broken), \
                self.assertRaises(RuntimeError):
            fuente.bajar_tramo(TWITCH, 10, 11, self.dest)
        self.assertEqual(self.calls, 1 + fuente.TWITCH_RETRIES)

    def test_at_most_two_twitch_sections_at_once(self):
        import threading
        import time
        lock, now, peak, errors = threading.Lock(), [0], [0], []

        def slow(opts, url):
            with lock:
                now[0] += 1
                peak[0] = max(peak[0], now[0])
            time.sleep(0.2)
            with lock:
                now[0] -= 1
            raise RuntimeError("HTTP Error 404: Not Found")

        def one(k):
            try:
                fuente.bajar_tramo(TWITCH, 10, 11, f"{self.dest}_{k}")
            except RuntimeError as exc:
                errors.append(exc)
        with mock.patch.object(fuente, "TWITCH_RETRIES", 0), \
                mock.patch.object(fuente.downloader, "_download_with_retry", side_effect=slow):
            threads = [threading.Thread(target=one, args=(k,)) for k in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual((peak[0], len(errors)), (2, 5))

    def test_twitch_takes_the_muxed_format(self):
        seen = []
        with mock.patch.object(fuente, "RETRY_WAIT_S", 0), \
                mock.patch.object(fuente.downloader, "_download_with_retry",
                                  side_effect=lambda opts, url: seen.append(opts["format"])), \
                mock.patch.object(fuente.glob, "glob", return_value=[]):
            for url in (TWITCH, "https://www.youtube.com/watch?v=x"):
                with self.assertRaises(RuntimeError):
                    fuente.bajar_tramo(url, 10, 11, self.dest)
        self.assertEqual(seen[0], fuente.FORMAT_TWITCH)                   # (Twitch retries: same format)
        self.assertEqual(seen[-1], fuente.FORMATO)


class AnchorConfidence(unittest.TestCase):
    def test_confidence(self):
        self.assertEqual(processor.confidence(3, -5.0), ("alta", False))
        self.assertEqual(processor.confidence(2, 15.0), ("alta", False))
        # a YouTube stream still live: 6/7 kills of ANOTHER game at −23.5 s
        self.assertEqual(processor.confidence(6, -23.5), ("media", True))
        self.assertEqual(processor.confidence(1, 0.0), ("media", False))
        self.assertEqual(processor.confidence(1, 20.0), ("media", False))
        self.assertEqual(processor.confidence(0, 0.0), ("baja", False))

    def test_formula_per_platform(self):
        self.assertEqual(processor.formula_offset("youtube"), processor.OFFSET_FORMULA_S["youtube"])
        self.assertEqual(processor.formula_offset("twitch"), processor.OFFSET_FORMULA_S["twitch"])
        self.assertEqual(processor.formula_offset(None), processor.OFFSET_FORMULA_S["youtube"])
        self.assertEqual(processor.formula_offset("kick"), processor.OFFSET_FORMULA_S["youtube"])

    def _process(self, shift, matches, platform="twitch", modo="auto"):
        partida = kills.partida_de_fixture(FIXTURE)
        params = CompetitivoParams.from_form({"game_ids": partida.game_id, "vod_url": TWITCH, "modo": modo})
        job = job_manager.create_job(TWITCH, output_path="")
        offsets = []

        def fake_span(url, a, b, destination):
            offsets.append((a, b))
            return destination + ".mp4"

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(processor, "TRABAJO", tmp), \
                mock.patch.object(processor.fuente, "info_vod",
                                  return_value={"inicio_utc": partida.t0, "duracion": 1, "titulo": "t",
                                                "platform": platform}), \
                mock.patch.object(processor.kills, "fetch_partida", return_value=partida), \
                mock.patch.object(processor.fuente, "bajar_tramo", side_effect=fake_span), \
                mock.patch.object(processor.layout, "cambios_kills", return_value=[]), \
                mock.patch.object(processor.layout, "estimar_desfase", return_value=(shift, matches)), \
                mock.patch.object(processor, "_captura"), mock.patch.object(processor.os, "remove"), \
                mock.patch.object(processor, "finish") as fin:
            processor.process(job, params)
        processor._SESIONES.pop(job, None)
        return job_manager.get_job(job), fin, offsets

    def test_auto_stops_on_a_suspicious_anchor(self):
        j, fin, _ = self._process(-23.5, 6)
        self.assertIsNone(j["error"])
        p = j["partidas"][0]
        self.assertEqual((p["confianza"], p["suspicious"], p["desfase_s"]), ("media", True, -23.5))
        self.assertEqual(j["stage"], "esperando_ancla")
        self.assertIn("lejos de lo esperado", j["aviso"])
        self.assertIn("-23.5 s", j["aviso"])
        fin.assert_not_called()
        self.assertEqual(j["vod"]["platform"], "twitch")

    def test_auto_goes_on_with_a_plausible_one(self):
        j, fin, _ = self._process(-4.0, 3)
        self.assertEqual(j["partidas"][0]["confianza"], "alta")
        self.assertIsNone(j["aviso"])
        fin.assert_called_once()

    def test_the_anchor_uses_the_platforms_formula(self):
        with mock.patch.dict(processor.OFFSET_FORMULA_S, {"twitch": 70}):
            _, _, tw = self._process(0.0, 3, modo="revisar")
        _, _, yt = self._process(0.0, 3, platform="youtube", modo="revisar")
        self.assertAlmostEqual(tw[0][0] - yt[0][0], 70 - processor.OFFSET_FORMULA_S["youtube"], places=3)


class FinishSkipsSpansWithoutVideo(unittest.TestCase):
    def test_all_without_video_is_a_clear_error(self):
        partida = kills.partida_de_fixture(FIXTURE)
        params = CompetitivoParams.from_form({"game_ids": partida.game_id, "vod_url": TWITCH})
        ancla = {"ancla_s": 100.0, "ancla_propuesta_s": 100.0, "desfase_s": 0.0, "confianza": "alta",
                 "suspicious": False, "coincidencias": 3, "esperadas": 3, "referencia": "x",
                 "referencia_vod_s": 0, "frame": None, "primera_kill_utc": partida.frames[1].ts.isoformat()}
        job = job_manager.create_job(TWITCH, output_path="")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(processor, "TRABAJO", os.path.join(tmp, "trabajo")), \
                mock.patch.object(config, "COMPETITIVO_FOLDER", os.path.join(tmp, "salida")), \
                mock.patch.object(processor.fuente, "info_vod",
                                  return_value={"inicio_utc": partida.t0, "duracion": 1, "titulo": "t",
                                                "platform": "twitch"}), \
                mock.patch.object(processor.kills, "fetch_partida", return_value=partida), \
                mock.patch.object(processor, "_ancla", return_value=ancla), \
                mock.patch.object(processor.fuente, "bajar_tramo",
                                  side_effect=fuente.SpanWithoutVideo("sin video")):
            processor.process(job, params)
            processor.finish(job, FinishParams.desde(params))
        processor._SESIONES.pop(job, None)
        j = job_manager.get_job(job)
        self.assertEqual(j["status"], "error")
        self.assertIn("Ningún tramo del VOD bajó con video", j["error"])


if __name__ == "__main__":
    unittest.main()
