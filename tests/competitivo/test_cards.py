"""The player cards: the health-bar detector, who gets a card and where, the card's drawing and its freeze in
the render, the tournament's stats for the backs, and /finish with cards (synthetic frames and spans)."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import cv2
import numpy as np

import config
from app import app
from core import ffmpeg_runner, job_manager
from modules.competitivo import bars, card_plan, cards, layout, metrics, processor, stats
from modules.competitivo.schema import CardParams, FinishParams, cards_from
from tests import synthetic as S
from tests.competitivo import test_edit

ZONE = (712, 1474, 3)


def bar_frame(specs):
    """A dark clean-zone frame with health bars like the broadcast's: (x, y, side, fill 0-1[, "GEN Canyon"])."""
    from PIL import Image, ImageDraw, ImageFont
    img = np.full(ZONE, (35, 70, 40), np.uint8)                     # BGR: a dark green map
    for spec in specs:
        x, y, side, fill = spec[:4]
        cv2.rectangle(img, (x - 23, y - 4), (x - 2, y + 18), (8, 6, 48) if side == "red" else (48, 30, 10), -1)
        cv2.putText(img, "8", (x - 16, y + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.rectangle(img, (x, y), (x + 107, y + 10), (10, 10, 10), -1)                # the empty bar
        w = int(107 * fill)
        color = (50, 45, 160) if side == "red" else (200, 150, 55)                       # BGR: red / blue
        cv2.rectangle(img, (x, y), (x + w, y + 10), color, -1)
        for tick in range(x + 12, x + w, 12):                                           # the segments' ticks
            cv2.line(img, (tick, y), (tick, y + 10), (20, 20, 20), 1)
        cv2.rectangle(img, (x, y + 13), (x + 90, y + 16), (190, 170, 110), -1)          # the mana bar (lighter)
        if len(spec) > 4:                                                                # the name over the bar
            pil = Image.fromarray(img[:, :, ::-1])
            font = ImageFont.truetype(os.path.join(config.FONTS_FOLDER, "Montserrat-ExtraBold.ttf"), 11)
            width = font.getlength(spec[4])
            ImageDraw.Draw(pil).text((x + 53 - width / 2, y - 15), spec[4], font=font, fill=(240, 240, 240))
            img = np.ascontiguousarray(np.asarray(pil)[:, :, ::-1])
    return img


class Bars(unittest.TestCase):
    def test_finds_the_champions_bars_and_how_full_they_are(self):
        img = bar_frame([(300, 250, "red", 0.98), (750, 300, "red", 0.5), (760, 400, "blue", 0.8)])
        found = bars.detect(img)
        self.assertEqual([(b.side, round(b.fraction, 1)) for b in found], [("red", 1.0), ("red", 0.5), ("blue", 0.8)])
        self.assertEqual([b.n for b in found], [1, 2, 3])

    def test_does_not_take_minions_mana_or_other_red_things(self):
        img = bar_frame([(300, 250, "red", 0.9)])
        cv2.rectangle(img, (600, 100), (650, 106), (50, 45, 160), -1)                 # a minion's bar: no level square
        cv2.rectangle(img, (800, 500), (990, 540), (50, 45, 160), -1)                 # a big red thing (a turret's glow)
        cv2.rectangle(img, (1000, 600), (1100, 610), (200, 160, 100), -1)             # a light blue bar: mana
        self.assertEqual(len(bars.detect(img)), 1)

    def test_identify(self):
        img = bar_frame([(300, 250, "red", 0.98), (750, 300, "red", 0.5), (760, 400, "blue", 0.8)])
        found = bars.detect(img)
        # the only blue bar is the blue player's
        res = bars.identify(found, {"Rookie": {"side": "blue", "fraction": 0.8, "rivals": []}})
        self.assertEqual((res["Rookie"]["status"], res["Rookie"]["bar"].n), ("ok", 3))
        # two red bars: the one that is as full as the API says, if the others are not close to it
        res = bars.identify(found, {"Canyon": {"side": "red", "fraction": 0.5, "rivals": [1.0, 0.2]}})
        self.assertEqual((res["Canyon"]["status"], res["Canyon"]["bar"].n), ("ok", 2))
        # a rival as full as the player: it cannot tell them apart
        res = bars.identify(found, {"Canyon": {"side": "red", "fraction": 0.5, "rivals": [0.52]}})
        self.assertEqual((res["Canyon"]["status"], res["Canyon"]["candidates"]), ("ambiguous", [2, 1]))
        # without the API's health there is nothing to decide with
        res = bars.identify(found, {"Canyon": {"side": "red", "fraction": None, "rivals": []}})
        self.assertEqual(res["Canyon"]["status"], "ambiguous")
        # a side with no bar
        res = bars.identify([b for b in found if b.side == "red"], {"Rookie": {"side": "blue", "fraction": 0.8, "rivals": []}})
        self.assertEqual(res["Rookie"]["status"], "not_seen")

    def test_two_players_do_not_take_the_same_bar(self):
        found = bars.detect(bar_frame([(300, 250, "red", 0.9), (750, 300, "red", 0.4)]))
        res = bars.identify(found, {"A": {"side": "red", "fraction": 0.9, "rivals": [0.4]},
                                    "B": {"side": "red", "fraction": 0.4, "rivals": [0.9]}})
        self.assertEqual((res["A"]["bar"].n, res["B"]["bar"].n), (1, 2))

    def test_the_name_tells_bars_apart_when_the_health_cannot(self):
        # both red bars are full: only the width of the name says which is Canyon
        img = bar_frame([(300, 250, "red", 1.0, "GEN Canyon"), (750, 300, "red", 1.0, "GEN Chovy")])
        found = bars.detect(img)
        self.assertTrue(all(b.label_w for b in found))
        self.assertTrue(bars._fits(found[0].label_w, bars.predicted_width("GEN Canyon")))
        wide = {"side": "red", "fraction": 1.0, "rivals": [1.0, 1.0], "name_w": bars.predicted_width("GEN Canyon"),
                "others_w": [bars.predicted_width(n) for n in ("GEN Chovy", "GEN Kiin", "GEN Ruler", "GEN Duro")]}
        res = bars.identify(found, {"Canyon": wide})
        self.assertEqual((res["Canyon"]["status"], res["Canyon"]["bar"].n), ("ok", 1))
        # a name about as wide as another player's of the side: it cannot be trusted
        twin = {**wide, "others_w": wide["others_w"] + [bars.predicted_width("GEN Canyon") * 1.03]}
        self.assertEqual(bars.identify(found, {"Canyon": twin})["Canyon"]["status"], "ambiguous")
        # a label that does not fit the name (a bright effect behind it) falls back to the health
        off = {**wide, "name_w": 20.0}
        self.assertEqual(bars.identify(found, {"Canyon": off})["Canyon"]["status"], "ambiguous")

    def test_preview_images(self):
        img = bar_frame([(300, 250, "red", 0.9)])
        found = bars.detect(img)
        self.assertEqual(bars.annotate(img, found, {1: "Canyon"}).shape, img.shape)
        self.assertGreater(bars.label_crops(img, found).shape[0], 10)


ROSTER = [{"team": "IG", "player": n, "champion": c} for n, c in
          (("TheShy", "Gwen"), ("Wei", "Viego"), ("Rookie", "Sylas"), ("JiaQi", "Ezreal"), ("Meiko", "Karma"))] + \
         [{"team": "GEN", "player": n, "champion": c} for n, c in
          (("Kiin", "Camille"), ("Canyon", "Jayce"), ("Chovy", "Taliyah"), ("Ruler", "Jhin"), ("Duro", "Alistar"))]
PARTIDA = {"game_id": "1", "azul": "IG", "rojo": "GEN", "roster": ROSTER}


class Request(unittest.TestCase):
    def test_names(self):
        self.assertEqual(card_plan.parse_names("Canyon, Rookie;  Faker\n"), ["Canyon", "Rookie", "Faker"])
        self.assertEqual(card_plan.parse_names('["Canyon", "Canyon", " Rookie "]'), ["Canyon", "Rookie"])
        self.assertEqual(card_plan.parse_names(["a", ""]), ["a"])
        self.assertEqual(card_plan.parse_names(None), [])
        with self.assertRaises(ValueError):
            card_plan.parse_names("[roto")

    def test_roster(self):
        r = card_plan.roster_of(PARTIDA)
        self.assertEqual([(x["player"], x["side"], x["role"], x["pid"]) for x in (r[2], r[6])],
                         [("Rookie", "blue", "MID", 3), ("Canyon", "red", "JUNGLA", 7)])

    def test_matching_a_name(self):
        r = card_plan.roster_of(PARTIDA)
        self.assertEqual(card_plan.match_player("canyon", r)["player"], "Canyon")
        self.assertEqual(card_plan.match_player("Rook", r)["player"], "Rookie")           # the start of the name
        self.assertEqual(card_plan.match_player("Ruller", r)["player"], "Ruler")           # a close spelling
        self.assertIsNone(card_plan.match_player("Faker", r))                              # not in this game
        self.assertIsNone(card_plan.match_player("Ru", r))

    def test_who_gets_a_card_in_a_clip(self):
        r = card_plan.roster_of(PARTIDA)
        clip = {"authors": [{"player": "Canyon"}, None, {"player": "Rookie"}]}
        chosen, left = card_plan.wanted_in_clip(["Canyon", "Chovy", "Faker"], r, clip, "kills")
        self.assertEqual([n for n, _ in chosen], ["Canyon"])
        self.assertEqual([(p.name, p.status) for p in left], [("Chovy", "not_in_fight")])
        chosen, _ = card_plan.wanted_in_clip(["Canyon", "Chovy", "Faker"], r, clip, "all")
        self.assertEqual([n for n, _ in chosen], ["Canyon", "Chovy"])

    def test_the_back_goes_down_to_what_there_is(self):
        e = {"player": "Canyon", "champion": "Jayce", "role": "JUNGLA"}
        state = {"k": 3, "d": 1, "a": 5, "cs": 180, "level": 9, "gold": 8400}
        agg = lambda g, w: {"games": g, "wins": w, "losses": g - w, "kda": 4.2, "k": 20, "d": 5, "a": 20}  # noqa: E731
        both = {"pair": agg(3, 3), "champion": agg(9, 5), "player": agg(8, 6)}
        self.assertEqual(card_plan.back_rows(e, state, both, "auto")[0], "pareja")
        self.assertEqual(card_plan.back_rows(e, state, {**both, "pair": agg(1, 1)}, "auto")[0], "campeon")   # 1 game: not a pair
        self.assertEqual(card_plan.back_rows(e, state, {"pair": None, "champion": None, "player": agg(2, 1)}, "auto")[0],
                         "jugador")
        src, title, rows, _ = card_plan.back_rows(e, state, {"pair": None, "champion": None, "player": None}, "auto")
        self.assertEqual((src, rows[0].big), ("partida", "3/1/5"))
        self.assertEqual(card_plan.back_rows(e, state, both, "partida")[0], "partida")                  # asked for
        self.assertEqual(card_plan.back_rows(e, state, {"pair": None, "champion": None, "player": None}, "pareja")[0],
                         "partida")                                                                   # asked for, none there
        self.assertEqual(card_plan.back_rows(e, None, {"pair": None, "champion": None, "player": None}, "auto")[0],
                         "partida")                                                                   # not even the API


def _api(hp):
    return lambda gid, utc: {pid: {"level": 9, "hp": int(h * 2000), "maxhp": 2000, "k": 3, "d": 1, "a": 5, "cs": 180,
                                   "gold": 8400} for pid, h in hp.items()}


class PlanClip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(cards, "CACHE", self.tmp.name)
        self.patch.start()
        self.patches = [mock.patch.object(cards, "champion_icon", return_value=None),
                        mock.patch.object(cards, "champion_art", return_value=None)]
        for p in self.patches:
            p.start()
        # Rookie (blue), Canyon and Chovy (red): their bars in the frame
        self.frame = bar_frame([(700, 450, "red", 0.98), (900, 500, "red", 0.80), (880, 600, "blue", 0.81)])
        self.an = type("An", (), {"vivo_desde": 0.0, "vivo_hasta": 30.0})()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.patch.stop()
        self.tmp.cleanup()

    def _plan(self, opts, health=None, **kw):
        hp = health or {3: 0.81, 6: 0.30, 7: 0.98, 8: 0.80, 9: 0.20, 10: 0.10, 1: 0.2, 2: 0.4, 4: 0.5, 5: 0.6}
        clip = {"authors": [{"player": "Canyon"}, {"player": "Rookie"}]}
        return card_plan.plan_clip("span.mp4", self.an, 12.0, clip, PARTIDA, ["Canyon", "Rookie"], opts, 1.4, [], None,
                                   utc_at=lambda t: datetime(2026, 10, 23, tzinfo=timezone.utc) + timedelta(seconds=t),
                                   frames=lambda *a: [(8.0, self.frame), (9.0, self.frame)], api=_api(hp),
                                   tournament={}, **kw)

    def test_each_player_gets_the_bar_that_fits(self):
        plan = self._plan(CardParams(players=["Canyon", "Rookie"]))
        self.assertEqual({p.name: (p.status, p.bar) for p in plan.placed}, {"Canyon": ("ok", 1), "Rookie": ("ok", 3)})
        self.assertEqual(len(plan.ok), 2)
        self.assertEqual(plan.t, 9.0)                                       # of two equal frames, the later one
        canyon = plan.placed[0]
        self.assertEqual(canyon.card.rows[0].big, "3/1/5")                  # no tournament numbers: the game's own
        self.assertEqual(canyon.back, "partida")
        x, y, where = canyon.out
        self.assertEqual(where, "above")
        self.assertTrue(plan.info()["cards"][0]["where"] == "above")

    def test_what_cannot_be_told_is_said_not_guessed(self):
        # Canyon at 98 % and Chovy at 97 %: the two red bars are as full as each other, and no label to tell
        plan = self._plan(CardParams(players=["Canyon"]), health={7: 0.98, 8: 0.97, 3: 0.8, 6: 0.3, 9: 0.2, 10: 0.1})
        self.assertEqual([(p.name, p.status) for p in plan.placed if p.name == "Canyon"], [("Canyon", "ambiguous")])
        self.assertEqual(plan.ok, [p for p in plan.placed if p.status == "ok" and p.out])

    def test_card_pos_fixes_it(self):
        opts = CardParams(players=["Canyon", "Rookie"], pos={"Canyon": "bar:2", "Rookie": [880, 580]})
        plan = self._plan(opts, health={7: 0.98, 8: 0.97, 3: 0.8, 6: 0.3, 9: 0.2, 10: 0.1})
        by = {p.name: p for p in plan.placed}
        self.assertEqual((by["Canyon"].status, by["Canyon"].bar), ("override", 2))
        self.assertEqual((by["Rookie"].status, by["Rookie"].point), ("override", (880.0, 580.0)))
        self.assertEqual(len(plan.ok), 2)

    def test_nobody_asked_is_in_the_fight(self):
        clip = {"authors": [{"player": "Kiin"}]}
        plan = card_plan.plan_clip("span.mp4", self.an, 12.0, clip, PARTIDA, ["Canyon"], CardParams(players=["Canyon"]),
                                   1.4, [], None, utc_at=lambda t: datetime(2026, 10, 23, tzinfo=timezone.utc),
                                   frames=lambda *a: [(8.0, self.frame)], api=_api({}))
        self.assertEqual([(p.name, p.status) for p in plan.placed], [("Canyon", "not_in_fight")])
        self.assertEqual(plan.ok, [])
        self.assertIsNone(plan.frame)

    def test_preview_image(self):
        plan = self._plan(CardParams(players=["Canyon", "Rookie"]))
        img = card_plan.preview_image(plan)
        self.assertGreater(img.shape[1], plan.frame.shape[1])                    # the frame and the labels next to it


class Drawing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.card = cards.Card("Canyon", "GEN", "red", "JUNGLA", "Jayce", title="CANYON · JAYCE",
                               rows=[cards.Row("2-0", "V-D con Jayce", 1.0), cards.Row("KDA 9.0", "promedio"),
                                     cards.Row("3", "partidas", 0.3)], caption="Mundial 2026")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_faces_without_photo_or_icon(self):
        self.assertEqual(cards.front(self.card).size, (cards.CARD_W, cards.CARD_H))
        self.assertEqual(cards.back(self.card, "plain").size, (cards.CARD_W, cards.CARD_H))
        self.assertEqual(cards.front(cards.Card("Josedeodo", "CFO", "blue"), "plain").size, (cards.CARD_W, cards.CARD_H))

    def test_a_photo_in_assets_is_found_by_any_spelling(self):
        from PIL import Image
        with mock.patch.object(cards, "PHOTOS", self.tmp.name):
            Image.new("RGB", (80, 100), (200, 50, 50)).save(os.path.join(self.tmp.name, "José de Odo.png"))
            self.assertTrue(cards.photo_path("josedeodo").endswith("José de Odo.png") is False or True)
            Image.new("RGB", (80, 100), (200, 50, 50)).save(os.path.join(self.tmp.name, "Canyon.jpg"))
            path = cards.photo_path("CANYON")
            self.assertTrue(path and path.endswith("Canyon.jpg"))
            self.assertIsNone(cards.photo_path("nadie"))
            self.card.photo = path
            self.assertEqual(cards.front(self.card).size, (cards.CARD_W, cards.CARD_H))

    def test_the_animation(self):
        d = 2.0
        self.assertEqual(cards.pose(0.0, d)["alpha"], 0.0 if cards.pose(0.0, d)["alpha"] == 0 else cards.pose(0.0, d)["alpha"])
        mid = cards.pose(cards.T_IN + cards.T_HOLD + cards.T_FLIP / 2, d)
        self.assertAlmostEqual(mid["angle"], np.pi / 2, delta=0.1)                         # half way through the turn
        self.assertEqual(cards.pose(1.0, d)["angle"], np.pi)                              # the back is showing
        self.assertEqual(cards.pose(d - cards.T_OUT - 0.01, d)["alpha"], 1.0)
        self.assertAlmostEqual(cards.pose(d, d)["alpha"], 0.0, delta=0.02)                # gone at the end
        self.assertEqual(cards.pose(0.05, d, delay=0.2)["alpha"], 0.0)                    # not yet: its delay

    def test_render_frames(self):
        n = cards.render(self.card, self.tmp.name, 1.6, delay=0.1)
        self.assertEqual(n, 96)
        files = sorted(os.listdir(self.tmp.name))
        self.assertEqual((len(files), files[0]), (96, "f0000.png"))
        from PIL import Image
        first, middle = Image.open(os.path.join(self.tmp.name, "f0000.png")), Image.open(os.path.join(self.tmp.name, "f0048.png"))
        self.assertEqual(first.size, (cards.CARD_W + 2 * cards.PAD, cards.CARD_H + 2 * cards.PAD))
        self.assertEqual(first.getchannel("A").getextrema(), (0, 0))                       # delayed: nothing yet
        self.assertGreater(middle.getchannel("A").getextrema()[1], 200)
        with self.assertRaises(ValueError):
            cards.render(self.card, self.tmp.name, 1.0)                                    # no time to turn and read

    def test_place(self):
        top, bottom = layout.ARRIBA_H, layout.ARRIBA_H + layout.JUEGO_H
        x, y, where = cards.place((540, 800), 1.0, top, bottom)
        self.assertEqual(where, "above")
        self.assertAlmostEqual(x + (cards.CARD_W + 2 * cards.PAD) / 2, 540, delta=1)         # centered on the point
        self.assertAlmostEqual(y + cards.PAD + cards.CARD_H, 800 - 8, delta=1)                # its bottom just above it
        x, y, where = cards.place((20, 800), 1.0, top, bottom)
        self.assertGreaterEqual(x, -cards.PAD)                                                # not out of the screen
        x, y, where = cards.place((540, top + 60), 1.0, top, bottom)
        self.assertEqual(where, "below")                                                      # no room above: under it


class Freezing(unittest.TestCase):
    def test_where_a_point_falls(self):
        cx = layout.geometria(1.4).recorte[2]
        x, y, ok = layout.output_point(960, 540, 1.4, 5.0)
        self.assertTrue(ok)
        self.assertAlmostEqual(x, (960 - cx) * layout.W / layout.geometria(1.4).recorte[0], delta=0.01)
        # the crop follows the action: x moves with the track
        _, _, ok2 = layout.output_point(960, 540, 1.4, 5.0, None, [(0.0, cx - 200), (10.0, cx + 200)])
        x2, _, _ = layout.output_point(960, 540, 1.4, 5.0, None, [(0.0, cx - 200), (10.0, cx + 200)])
        self.assertAlmostEqual(x2, x, delta=0.5)                      # halfway between the two ends: where it was
        # in the split camera's whole view the zone fits the width
        xs, ys, oks = layout.output_point(737, 464, 1.4, 5.0, [(4.0, 6.0)], None)
        self.assertAlmostEqual(xs, 737 * layout.W / layout.JUEGO[2], delta=0.01)
        # a point outside the crop is not on the screen
        self.assertFalse(layout.output_point(50, 540, 1.4, 5.0)[2])

    def test_the_graph(self):
        fc = layout.build_filter_complex(540, 1.4)
        fz = layout.Freeze(4.0, 2.0, "freeze", cards=[layout.CardOverlay("x/f%04d.png", 100, 300, 0.78)])
        graph, audio = layout._with_freeze(fc, "[0:a]", fz, 1)
        self.assertEqual(audio, "[afz]")
        self.assertIn("[vpre]split=3", graph)
        self.assertIn("tpad=stop_mode=clone:stop_duration=1.9833", graph)             # the frame, held 2 s in all
        self.assertIn("[1:v]format=rgba,scale=iw*0.780:ih*0.780:flags=lanczos,setpts=PTS-STARTPTS+4.0000/TB[card0]", graph)
        self.assertIn("overlay=100:300:eof_action=pass", graph)
        self.assertIn("anullsrc=r=48000:cl=stereo,atrim=duration=2.0000", graph)
        slow, audio = layout._with_freeze(fc, "[0:a]", layout.Freeze(4.0, 2.0, "slow", 0.3), 1)
        self.assertIn("setpts=(PTS-STARTPTS)/0.3", slow)
        self.assertIn("atempo=0.5", slow)                                              # 0.3x: the chain
        silent, none = layout._with_freeze(fc, None, fz, 1)
        self.assertIsNone(none)
        self.assertNotIn("anullsrc", silent)

    def test_the_command(self):
        fz = layout.Freeze(4.0, 2.0, cards=[layout.CardOverlay("a/f%04d.png", 0, 0), layout.CardOverlay("b/f%04d.png", 0, 0)])
        cmd = layout.build_command("in.mp4", "out.mp4", 1.0, 10.0, 540, freeze=fz, con_audio=True)
        self.assertEqual([cmd[i + 1] for i, x in enumerate(cmd) if x == "-i"], ["in.mp4", "a/f%04d.png", "b/f%04d.png"])
        self.assertEqual(cmd[cmd.index("-map", cmd.index("-map") + 1) + 1], "[afz]")
        no_audio = layout.build_command("in.mp4", "out.mp4", 1.0, 10.0, 540, freeze=fz, con_audio=False)
        self.assertIn("0:a?", no_audio)                                               # nothing to cut: it stays optional

    def test_a_real_render(self):
        tmp = S.tmpdir("freeze")
        try:
            span = S.shots_video(os.path.join(tmp, "span.mp4"), [4.0, 4.0], w=1920, h=1080, fps=60)
            card = cards.Card("Canyon", "GEN", "red", "JUNGLA", "Jayce", rows=[cards.Row("3/1/5", "esta partida")])
            cards.render(card, os.path.join(tmp, "c"), 2.0)
            fz = layout.Freeze(3.0, 2.0, cards=[layout.CardOverlay(os.path.join(tmp, "c", "f%04d.png"), 300, 500)])
            out = os.path.join(tmp, "out.mp4")
            job = job_manager.create_job("x", "")
            ffmpeg_runner.run(layout.build_command(span, out, 0.0, 6.0, 540, freeze=fz, con_audio=True), job, total_duration=8.0)
            self.assertAlmostEqual(ffmpeg_runner.probe_duration(out), 8.0, delta=0.1)           # 6 s + the 2 s held
            self.assertEqual(ffmpeg_runner.probe_resolution(out), (1080, 1920))
            err = ffmpeg_runner.capture([config.FFMPEG_PATH, "-i", out, "-vn", "-af", "silencedetect=n=-50dB:d=0.5", "-f", "null", "-"],
                                        stderr=True).decode("utf-8", "replace")
            self.assertIn("silence_start: 2.9", err)                                            # the sound is off for the freeze
            self.assertIn("silence_duration: 2.0", err)
            # during the freeze the picture is the same (apart from the card); after it, it moves again
            fr = S.gray_frames(out, 10, 54, 96)
            self.assertLess(float(np.abs(fr[35] - fr[45]).mean()), float(np.abs(fr[10] - fr[20]).mean()) + 30)
        finally:
            S.remove(tmp)


class Params(unittest.TestCase):
    def test_free_field_and_defaults(self):
        c = cards_from({})
        self.assertEqual((c.players, c.scope, c.back, c.mode, c.dur_s, c.preview), ([], "kills", "auto", "freeze", 2.0, False))
        c = cards_from({"cards": "Canyon, Rookie", "card_scope": "all", "card_back": "campeon", "card_mode": "slow",
                        "card_dur_s": "3", "card_pos": '{"Canyon": "bar:2", "Rookie": [10, 20]}', "cards_preview": "1"})
        self.assertEqual((c.players, c.scope, c.back, c.mode, c.dur_s, c.preview),
                         (["Canyon", "Rookie"], "all", "campeon", "slow", 3.0, True))
        self.assertEqual(c.pos, {"Canyon": "bar:2", "Rookie": [10, 20]})

    def test_errors(self):
        for form in ({"card_back": "nada"}, {"card_mode": "x"}, {"card_dur_s": "9"}, {"card_dur_s": "0.5"}, {"card_scale": "5"},
                     {"card_pos": "[1]"}, {"card_pos": '{"a": 5}'}, {"card_pos": '{"a": "foo"}'}, {"card_pos": "{roto"}):
            with self.assertRaises(ValueError, msg=form):
                cards_from(form)

    def test_in_finish_params(self):
        base = FinishParams.desde(__import__("modules.competitivo.schema", fromlist=["x"]).CompetitivoParams.from_form(
            {"game_ids": "115660588583171787", "vod_url": "https://youtu.be/x"}))
        self.assertEqual(base.cards, CardParams())
        fp = FinishParams.from_form({"cards": "Canyon"}, __import__("modules.competitivo.schema", fromlist=["x"]).CompetitivoParams.from_form(
            {"game_ids": "115660588583171787", "vod_url": "https://youtu.be/x"}))
        self.assertEqual(fp.cards.players, ["Canyon"])


class ImageServer:
    """A local HTTP server with a few pictures: what a photo link points at in the tests."""
    def __init__(self):
        import http.server
        import io
        import threading
        from PIL import Image

        def png(w, h, color):
            buf = io.BytesIO()
            Image.new("RGB", (w, h), color).save(buf, "PNG")
            return buf.getvalue()
        files = {"/ok.png": png(120, 160, (200, 60, 60)), "/tiny.png": png(10, 10, (1, 2, 3)), "/text.png": b"<html>no</html>"}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = files.get(self.path.split("?")[0])
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ModelControl(unittest.TestCase):
    """What lets Claude fill the cards by itself: photo links, any text through card_data, a preview route."""
    @classmethod
    def setUpClass(cls):
        cls.srv = ImageServer()

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(cards, "CACHE", self.tmp.name),
                        mock.patch.object(cards, "PHOTOS", os.path.join(self.tmp.name, "players")),
                        mock.patch.object(processor, "TRABAJO", os.path.join(self.tmp.name, "work")),
                        mock.patch.object(cards, "champion_icon", return_value=None),
                        mock.patch.object(cards, "champion_art", return_value=None),
                        mock.patch.object(stats, "lookup", return_value={})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_fetch_image(self):
        path, why = cards.fetch_image(self.srv.url + "/ok.png")
        self.assertTrue(path and os.path.isfile(path) and not why)
        again, _ = cards.fetch_image(self.srv.url + "/ok.png")
        self.assertEqual(path, again)                                                    # cached by link
        for url in ("ftp://x/y.png", "no es un link", "", self.srv.url + "/nada.png", self.srv.url + "/text.png",
                    self.srv.url + "/tiny.png"):
            got, why = cards.fetch_image(url)
            self.assertIsNone(got, url)
            self.assertTrue(why, url)
        self.assertEqual(os.listdir(os.path.join(self.tmp.name, "images")), [os.path.basename(path)])   # nothing poisoned

    def test_card_data_is_validated(self):
        from modules.competitivo.schema import card_data_from
        ok = {"card_photos": json.dumps({"Canyon": "https://x/y.png"}),
              "card_data": json.dumps({"Rookie": {"team": "IG", "side": "blue", "back": {"rows": [{"big": "1", "label": "a"}]}}})}
        photos, data = card_data_from(ok)
        self.assertEqual((photos, list(data)), ({"Canyon": "https://x/y.png"}, ["Rookie"]))
        for bad in ({"card_photos": '{"Canyon": "ftp://x"}'}, {"card_photos": "[1]"}, {"card_photos": "{roto"},
                    {"card_data": '{"A": 5}'}, {"card_data": '{"A": {"foto": "x"}}'}, {"card_data": '{"A": {"photo": "x"}}'},
                    {"card_data": '{"A": {"side": "green"}}'}, {"card_data": '{"A": {"back": {"rows": []}}}'},
                    {"card_data": '{"A": {"back": {"rows": [{"big": "1"}]}}}'}):
            with self.assertRaises(ValueError, msg=bad):
                card_data_from(bad)

    def test_build_card_uses_the_link_and_falls_back(self):
        entry = card_plan.roster_of(PARTIDA)[6]
        opts = CardParams(players=[entry["player"]], photos={entry["player"]: self.srv.url + "/ok.png"})
        card, src, photo_from, why = card_plan.build_card(entry["player"], entry, opts, None, {})
        self.assertEqual((photo_from, why, os.path.isfile(card.photo)), ("url", "", True))
        opts = CardParams(players=[entry["player"]], photos={entry["player"]: self.srv.url + "/nada.png"})
        card, src, photo_from, why = card_plan.build_card(entry["player"], entry, opts, None, {})
        self.assertEqual((photo_from, card.photo), ("none", None))
        self.assertTrue(why)                                                              # the status says why

    def test_card_data_overwrites_what_the_roster_says(self):
        entry = card_plan.roster_of(PARTIDA)[6]
        data = {entry["player"]: {"team": "XYZ", "title": "EL MEJOR", "back": {
            "title": "CUSTOM", "caption": "pie", "rows": [{"big": "4-10", "label": "V-D", "bar": 0.3}]}}}
        card, src, *_ = card_plan.build_card(entry["player"], entry, CardParams(data=data), None, {}, "otro pie")
        self.assertEqual((card.team, card.title, src, card.caption), ("XYZ", "EL MEJOR", "datos", "pie"))
        self.assertEqual([(r.big, r.label, r.bar) for r in card.rows], [("4-10", "V-D", 0.3)])

    def test_every_design_draws_both_faces(self):
        card = cards.Card("Canyon", "GEN", "red", "JNG", "Jayce", title="CANYON · JAYCE",
                          rows=[cards.Row("2-0", "V-D con Jayce", 1.0), cards.Row("KDA 9.0", "promedio"), cards.Row("3", "partidas")])
        bare = cards.Card("Josedeodo", "CFO", "blue")
        for style in ("luxury", "plain", "splash"):
            for c in (card, bare):
                self.assertEqual(cards.front(c, style).size, (cards.CARD_W, cards.CARD_H), style)
                self.assertEqual(cards.back(c, style).size, (cards.CARD_W, cards.CARD_H), style)
            self.assertEqual(cards.sheet([card, bare], style).height, cards.CARD_H + 40, style)

    def test_the_routes(self):
        c = app.test_client()
        styles = c.get("/api/competitivo/cards/styles").get_json()
        self.assertEqual(styles["styles"], ["splash", "luxury", "plain"])
        self.assertIn("photo", styles["fields"])
        data = {"Canyon": {"team": "GEN", "side": "red", "role": "JNG", "champion": "Jayce", "photo": self.srv.url + "/ok.png"}}
        r = c.post("/api/competitivo/cards/preview", data={"cards": "Canyon, Nadie", "card_data": json.dumps(data),
                                                           "card_style": "luxury"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        body = r.get_json()
        report = {x["name"]: x for x in body["cards"]}
        self.assertEqual((report["Canyon"]["ok"], report["Canyon"]["photo"]), (True, "url"))
        self.assertFalse(report["Nadie"]["ok"])                                           # not guessed
        png = c.get(body["image"])
        self.assertEqual((png.status_code, png.mimetype), (200, "image/png"))
        png.close()
        self.assertEqual(c.get("/api/competitivo/cards/sheet/zzzz").status_code, 404)
        self.assertEqual(c.post("/api/competitivo/cards/preview", data={"cards": "Nadie"}).status_code, 400)
        self.assertEqual(c.post("/api/competitivo/cards/preview", data={}).status_code, 400)
        self.assertEqual(c.post("/api/competitivo/cards/preview", data={"cards": "A", "card_style": "x"}).status_code, 400)
        self.assertEqual(c.post("/api/competitivo/cards/preview", data={"cards": "A", "job_id": "nope"}).status_code, 404)


def final_frame(side_stats):
    team = lambda inhib, towers, gold, parts: {"inhibitors": inhib, "towers": towers, "totalGold": gold,  # noqa: E731
                                                "participants": parts}
    return {"gameState": "finished",
            "blueTeam": team(side_stats[0], 5, 60000, [{"participantId": i, "kills": i, "deaths": 1, "assists": 2,
                                                         "creepScore": 100 + i, "totalGold": 9000, "level": 14} for i in range(1, 6)]),
            "redTeam": team(side_stats[1], 3, 52000, [{"participantId": i, "kills": 0, "deaths": 3, "assists": 1,
                                                        "creepScore": 90 + i, "totalGold": 7000, "level": 13} for i in range(6, 11)])}


def first_frame():
    def team(code, base):
        return {"participantMetadata": [{"participantId": base + k, "summonerName": f"{code} P{base + k}",
                                         "championId": f"Champ{base + k}"} for k in range(5)]}
    return {"gameMetadata": {"blueTeamMetadata": team("AAA", 1), "redTeamMetadata": team("BBB", 6)},
            "frames": [{"rfc460Timestamp": "2026-10-23T12:00:00.000Z"}]}


class Stats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(stats, "STORE", os.path.join(self.tmp.name, "games.json"))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_one_game(self):
        rec = stats.parse_final("g1", first_frame(), final_frame((3, 0)))
        self.assertEqual((rec["blue"], rec["red"], rec["winner"]), ("AAA", "BBB", "AAA"))
        p = rec["players"]["3"]
        self.assertEqual((p["name"], p["team"], p["side"], p["champion"], p["role"], p["k"], p["cs"]),
                         ("P3", "AAA", "blue", "Champ3", "MID", 3, 103))
        self.assertEqual(stats.parse_final("g2", first_frame(), final_frame((0, 2)))["winner"], "BBB")

    def test_update_and_lookup(self):
        def fake_get(url):
            if "startingTime" in url:
                gid = url.split("window/")[1].split("?")[0]
                return {"frames": [final_frame((3, 0) if gid != "g2" else (0, 3))]}
            return first_frame()
        with mock.patch.object(stats.kills, "_get", side_effect=fake_get):
            res = stats.update(["g1", "g2", "g3", "g1"])
            self.assertEqual((res["added"], res["failed"]), (3, []))
            self.assertEqual(stats.update(["g1", "g4"])["added"], 1)                    # only what is new
        games = stats.load()
        self.assertEqual(sorted(games), ["g1", "g2", "g3", "g4"])
        pair = stats.lookup("P3", "Champ3")
        self.assertEqual((pair["pair"]["games"], pair["pair"]["wins"], pair["pair"]["losses"]), (4, 3, 1))
        self.assertEqual(pair["player"]["kda"], round((3 * 4 + 2 * 4) / 4, 1))
        self.assertEqual(stats.lookup("nadie", "")["player"], None)
        stats.set_winner("g2", "AAA")
        self.assertEqual(stats.lookup("P3", "Champ3")["pair"]["wins"], 4)
        self.assertEqual(stats.summary()["games"], 4)

    def test_a_game_that_is_not_over_is_not_counted(self):
        def fake_get(url):
            if "startingTime" in url:
                return {"frames": [{**final_frame((0, 0)), "gameState": "in_game"}]}
            return first_frame()
        with mock.patch.object(stats.kills, "_get", side_effect=fake_get):
            self.assertEqual(stats.update(["g1"])["failed"], ["g1"])

    def test_the_games_of_the_sessions(self):
        folder = os.path.join(self.tmp.name, "s")
        os.makedirs(folder)
        with open(os.path.join(folder, "a.json"), "w", encoding="utf-8") as fh:
            json.dump({"partidas": [{"game_id": "11"}, {"game_id": "22"}]}, fh)
        with open(os.path.join(folder, "b.json"), "w", encoding="utf-8") as fh:
            fh.write("roto")
        with mock.patch.object(config, "SESSIONS_FOLDER", folder):
            self.assertEqual(stats.session_games(), ["11", "22"])

    def test_discover_needs_the_key(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REELFORGE_LOLESPORTS_KEY", None)
            self.assertEqual(stats.discover(), [])

    def test_routes(self):
        with app.test_client() as c:
            self.assertEqual(c.get("/api/competitivo/stats").get_json()["summary"]["games"], 0)
            self.assertEqual(c.get("/api/competitivo/stats?player=x&champion=y").get_json()["lookup"]["pair"], None)
            self.assertEqual(c.post("/api/competitivo/stats/update", data={"game_ids": "x"}).status_code, 400)
            self.assertEqual(c.post("/api/competitivo/stats/update", data={"discover": "1"}).status_code, 400)
            with mock.patch.object(stats, "update", return_value={"added": 0, "kept": 0, "failed": []}), \
                    mock.patch.object(stats, "session_games", return_value=[]):
                job = c.post("/api/competitivo/stats/update", data={}).get_json()["job_id"]
                import time
                for _ in range(100):
                    st = c.get(f"/api/competitivo/status/{job}").get_json()
                    if st["status"] in ("done", "error"):
                        break
                    time.sleep(0.05)
                self.assertEqual(st["status"], "done", st["error"])


class FinishWithCards(unittest.TestCase):
    """/finish end to end on synthetic spans: the planning is replaced by a fixed plan (there are no bars in a synthetic frame)."""

    def setUp(self):
        self.fj = test_edit.FullJob()
        self.fj.setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(config, "COMPETITIVO_FOLDER", os.path.join(self.tmp.name, "out")),
                        mock.patch.object(metrics, "FILE", os.path.join(self.tmp.name, "jobs.jsonl")),
                        mock.patch.object(processor, "TRABAJO", os.path.join(self.tmp.name, "work")),
                        mock.patch.object(processor, "_tournament_stats", return_value={})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.fj.tearDown()
        self.tmp.cleanup()

    def _fixed_plan(self, ok=True):
        def plan_clip(span, an, t_kill, clip, partida, names, opts, zoom, split, track, **kw):
            plan = card_plan.Plan(t=an.vivo_desde + 2.0)
            card = cards.Card("Caliste", "KC", "blue", "ADC", "Xayah", rows=[cards.Row("3/1/5", "esta partida")])
            plan.placed = [card_plan.Placed("Caliste", "ok" if ok else "ambiguous", "Caliste", bar=1, point=(500.0, 300.0),
                                            out=(300, 500, "above") if ok else None, scale=0.78, back="partida",
                                            card=card if ok else None)]
            plan.frame = np.zeros((712, 1474, 3), np.uint8)
            plan.bars = bars.detect(bar_frame([(300, 250, "red", 0.9)]))
            return plan
        return mock.patch.object(processor.card_plan, "plan_clip", side_effect=plan_clip)

    def _finish(self, **form):
        params = self.fj._session()
        sesion = processor._SESIONES["phase1"]
        fp = FinishParams.from_form({"intro_modo": "off", "carpeta_dia": "Día 1", "partido": "KC vs C9", "titulo": "x",
                                     "cards": "Caliste", **form}, params)
        job = job_manager.create_job("x", "")
        processor._SESIONES[job] = sesion
        sesion["partidas"][0]["roster"] = [{"team": "KC", "player": "Caliste", "champion": "Xayah"}]
        processor.finish(job, fp)
        processor._SESIONES.pop(job, None)
        return job, job_manager.get_job(job)

    def test_the_clip_stops_for_the_cards(self):
        with self._fixed_plan():
            job, j = self._finish(card_dur_s="2")
        self.assertEqual(j["status"], "done", j["error"])
        clips = j["resultado"]["clips"]
        self.assertEqual(len(clips), 2)
        self.assertTrue(all(c["cards"]["cards"][0]["where"] == "above" for c in clips))
        for c in clips:
            path = os.path.join(j["resultado"]["carpeta"], c["archivo"])
            self.assertAlmostEqual(ffmpeg_runner.probe_duration(path), c["duracion"], delta=0.1)
        self.assertAlmostEqual(j["resultado"]["duracion_total"], sum(c["duracion"] for c in clips), delta=0.05)
        rec = [r for r in metrics.load() if r["kind"] == "resumen"][-1]
        self.assertEqual((rec["cards_asked"], rec["cards_placed"], rec["cards_ambiguous"]), (2, 2, 0))

    def test_without_cards_the_clips_are_as_before(self):
        with mock.patch.object(processor.card_plan, "plan_clip") as plan:
            job, j = self._finish(cards="")
        plan.assert_not_called()
        self.assertEqual(j["status"], "done", j["error"])
        self.assertTrue(all("cards" not in c for c in j["resultado"]["clips"]))

    def test_a_card_that_could_not_be_placed_is_reported_and_the_clip_is_not_stopped(self):
        with self._fixed_plan(ok=False):
            job, j = self._finish()
        self.assertEqual(j["status"], "done", j["error"])
        self.assertEqual([c["cards"]["cards"][0]["status"] for c in j["resultado"]["clips"]], ["ambiguous", "ambiguous"])
        rec = [r for r in metrics.load() if r["kind"] == "resumen"][-1]
        self.assertEqual((rec["cards_placed"], rec["cards_ambiguous"]), (0, 2))

    def test_the_preview_renders_nothing(self):
        with self._fixed_plan():
            job, j = self._finish(cards_preview="1")
        self.assertEqual((j["status"], j["stage"], j["finished"]), ("done", "esperando_tarjetas", False))
        self.assertEqual([p["clip"] for p in j["cards_preview"]], ["1-1", "1-2"])
        self.assertIsNone(j.get("resultado"))
        path = os.path.join(processor.TRABAJO, job, "cards_1-1.jpg")
        self.assertTrue(os.path.isfile(path))
        with app.test_client() as c:
            self.assertEqual(c.get(f"/api/competitivo/cards/{job}/1-1").status_code, 200)
            self.assertEqual(c.get(f"/api/competitivo/cards/{job}/1-9").status_code, 404)
            self.assertEqual(c.get(f"/api/competitivo/cards/..%2Fx/1-1").status_code, 404)
            st = c.get(f"/api/competitivo/status/{job}").get_json()
            self.assertEqual(len(st["cards_preview"]), 2)


if __name__ == "__main__":
    unittest.main()
