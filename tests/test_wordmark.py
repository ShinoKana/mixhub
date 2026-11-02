"""The `mixhub` wordmark Easter egg: a click on Home plays an effect, a second click always stops it."""
import json
import os
import random
import tempfile
import unittest

from mixhub.tui.widgets import wordmark as wm
from mixhub.tui.widgets.wordmark import Wordmark


def text(w, now, **kw):
    return "".join(ch for ch, _ in w.render_cells(now, **kw))


class WordmarkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefs = os.path.join(self.tmp.name, "Preferences")

    def make(self, pick=None):
        w = Wordmark(prefs_path=self.prefs, rng=random.Random(7))
        if pick:
            w.rng.choice = lambda pool: pick if pick in pool else pool[0]
        return w

    def test_a_second_click_stops_every_effect_even_mid_animation(self):
        for effect in wm.EFFECTS:
            with self.subTest(effect=effect):
                w = self.make(effect)
                self.assertEqual(w.on_wordmark_click(10.0), effect)
                self.assertTrue(w.is_moving(10.05))
                self.assertEqual(w.on_wordmark_click(10.1), "off")
                self.assertEqual(text(w, 10.1), "mixhub")
                self.assertIsNone(w.next_frame_delay(10.1, working=True))

    def test_one_shot_effects_end_on_their_own_as_mixhub(self):
        for effect in ("decode", "sweep"):
            with self.subTest(effect=effect):
                w = self.make(effect)
                w.on_wordmark_click(0.0)
                self.assertIsNotNone(w.next_frame_delay(0.01))
                frames = {tuple(w.render_cells(t / 50)) for t in range(1, 40)}
                self.assertGreater(len(frames), 3)
                late = wm.EFFECT_SECS[effect] + 0.05
                self.assertEqual(text(w, late), "mixhub")
                self.assertEqual(w.mode, "off")
                self.assertIsNone(w.next_frame_delay(late))

    def test_decode_scrambles_then_settles_left_to_right(self):
        w = self.make("decode")
        w.on_wordmark_click(0.0)
        early = text(w, 0.05)
        self.assertNotEqual(early, "mixhub")
        mid = w.render_cells(wm.DECODE_HOLD + 2.5 * wm.DECODE_STEP)
        self.assertEqual("".join(ch for ch, _ in mid[:2]), "mi")

    def test_pulse_stays_until_clicked_and_survives_a_restart(self):
        w = self.make("pulse")
        w.on_wordmark_click(0.0)
        self.assertEqual(w.mode, "pulse")
        self.assertEqual(json.load(open(self.prefs))["wordmark"], "pulse")
        self.assertEqual(Wordmark(prefs_path=self.prefs).mode, "pulse")
        self.assertEqual(text(w, 100.0, wait_tool=True), "mixhub")
        self.assertEqual(w.render_cells(100.0, wait_tool=True)[wm.PULSE_X_IDX][1], wm.ST_ACCENT)
        w.on_wordmark_click(100.0)
        self.assertEqual(json.load(open(self.prefs))["wordmark"], "off")

    def test_no_effect_twice_in_a_row(self):
        w = Wordmark(prefs_path=self.prefs, rng=random.Random(1))
        picks = []
        for i in range(12):
            picks.append(w.on_wordmark_click(i * 10.0))
            w.on_wordmark_click(i * 10.0 + 0.1)
        self.assertTrue(all(a != b for a, b in zip(picks, picks[1:])))

    def test_old_game_and_shuffle_preferences_read_as_off(self):
        with open(self.prefs, "w") as f:
            json.dump({"wordmark": "lock", "wordmark_mix": "mhbuxi"}, f)
        w = Wordmark(prefs_path=self.prefs)
        self.assertEqual(w.mode, "off")
        self.assertEqual(text(w, 0.0), "mixhub")


if __name__ == "__main__":
    unittest.main()
