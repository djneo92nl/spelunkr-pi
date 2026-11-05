import queue
import tempfile
import time
import unittest
from unittest import mock

from support import NOW, make_tv, prog
import inputs
import search
import ttpages

CH_A = {"id": "alpha", "name": "ALPHA", "dial": 2, "versions": [{"v": 1, "manifest": "m/a1"}, {"v": 2, "manifest": "m/a2", "details": "d/a2"}]}
CH_B = {"id": "beta", "name": "BETA", "dial": 3, "versions": [{"v": 1, "manifest": "m/b1", "details": "d/b1"}]}
FILES = {
    "m/a1": {"items": [{"id": "old", "title": "Old Version Only"}]},
    "m/a2": {"items": [{"id": "tp1", "title": "Twin Peaks", "year": 1990, "show": "Twin Peaks"},
                       {"id": "caf", "title": "Café Society"},
                       {"id": "off", "title": "Twin Gone", "offAir": True},
                       {"id": "d1", "title": "Plain Title"}]},
    "d/a2": {"items": [{"description": "A quirky detective story."}, {"description": ""}, {"description": "x"},
                       {"description": "Mentions twin peaks in passing.", "imdbId": "tt42"}]},
    "m/b1": {"items": [{"id": "pk", "title": "Peaks and Valleys"}, {"id": "tp2", "title": "Gold Rush", "show": "Peaks of Gold"}]},
    "d/b1": {"items": [{}, {"description": "twin brothers dig"}]},
}


def fake_fetch(path, cache, max_age=None, timeout=20):
    if path not in FILES:
        raise OSError("offline: " + path)
    return FILES[path], None


class FakeLineupSrc:
    cache = "/nonexistent"

    def __init__(self, chans=(CH_A, CH_B)):
        self._chans = list(chans)

    def channels(self):
        return self._chans

    def timeline(self, cid, t0, t1):
        if cid == "alpha":
            return [prog(0, 30, "Twin Peaks", id="tp1"), prog(120, 30, "Twin Peaks", id="tp1")]
        raise RuntimeError("no schedule")


class IndexTests(unittest.TestCase):
    def build(self, chans=(CH_A, CH_B)):
        idx = search.Index(FakeLineupSrc(chans))
        with mock.patch.object(search.S, "fetch", fake_fetch):
            idx.ensure()
        return idx

    def test_only_the_latest_version_and_on_air_items_are_indexed(self):
        idx = self.build()
        titles = {e["title"] for e in idx.entries}
        self.assertNotIn("Old Version Only", titles)
        self.assertNotIn("Twin Gone", titles)
        self.assertEqual(len(idx.entries), 4 - 1 + 2)

    def test_ranking_title_then_series_then_description(self):
        hits = self.build().query("twin peaks")
        self.assertEqual([h["title"] for h in hits][:1], ["Twin Peaks"])
        self.assertEqual([h["id"] for h in hits], ["tp1", "tp2", "d1"])  # title, then series + blurb, then blurb only
        self.assertEqual([h["id"] for h in self.build().query("peaks")][:3], ["pk", "tp1", "tp2"])   # equal title scores: alphabetical, then the series match

    def test_every_word_must_match(self):
        idx = self.build()
        self.assertEqual(idx.query("peaks gold")[0]["id"], "tp2")        # series + title
        self.assertEqual(idx.query("peaks zebra"), [])
        self.assertEqual(idx.query("   "), [])

    def test_accents_and_case_do_not_matter(self):
        self.assertEqual(self.build().query("CAFE")[0]["id"], "caf")

    def test_description_search_and_imdb_are_kept(self):
        hits = self.build().query("quirky")
        self.assertEqual(hits[0]["id"], "tp1")
        self.assertEqual(self.build().query("passing")[0]["imdb"], "tt42")

    def test_a_channel_that_cannot_be_fetched_is_counted_not_fatal(self):
        broken = {"id": "gamma", "name": "G", "dial": 4, "versions": [{"v": 1, "manifest": "m/missing"}]}
        idx = self.build((CH_A, broken))
        self.assertEqual((idx.total, idx.failed, idx.state), (2, 1, "ready"))
        self.assertTrue(idx.query("twin"))

    def test_airings_find_the_showings_and_survive_a_missing_schedule(self):
        idx = self.build()
        hits = search.airings(FakeLineupSrc(), idx.query("peaks"), NOW)
        by_id = {h["id"]: h for h in hits}
        self.assertEqual(len(by_id["tp1"]["airs"]), 2)
        self.assertEqual(by_id["pk"]["airs"], [])                        # beta has no schedule here


def drain(tv, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            kind, val = tv.q.get(timeout=0.3)
        except queue.Empty:
            return
        tv.handle(kind, val)


class SearchFlowTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tv = make_tv(self._d.name)
        idx = search.Index(self.tv.src)
        idx.entries = [{"cid": "chan-0", "dial": 2, "cname": "CHANNEL 0", "version": 1, "index": 0, "id": "show-a0", "title": "Show A0",
                        "show": "", "year": 1985, "mature": False, "desc": "About ghosts.", "imdb": "", "_t": "show a0", "_s": "", "_d": "about ghosts."}]
        idx.state = "ready"
        self.tv._search_index = idx
        self.addCleanup(self._d.cleanup)
        self.addCleanup(setattr, inputs, "TEXT_MODE", False)

    def test_web_search_opens_the_results_page(self):
        tv = self.tv
        tv.command("search:ghosts")
        drain(tv)
        self.assertEqual(tv.tt["page"], 502)
        shown = "\n".join(r for s in tv.tt["shown"].subpages for r in s)
        self.assertIn("1 found for", shown)
        self.assertIn("Show A0", shown)
        self.assertIn("ON NOW", shown)
        self.assertEqual(len(tv.search["results"]), 1)

    def test_result_page_describes_and_tunes(self):
        tv = self.tv
        tv.tune = lambda i: setattr(tv, "tuned", i)
        tv.command("search:show")
        drain(tv)
        tv.command("page:510")
        drain(tv)
        text = "\n".join(r for s in tv.tt["shown"].subpages for r in s)
        self.assertIn("About ghosts.", text)
        tv.command("enter")
        self.assertEqual(tv.tuned, 0)

    def test_on_screen_keyboard(self):
        tv = self.tv
        tv.command("page:501")
        self.assertEqual(tv.search["cur"], [3, 8])                       # starts on GO
        tv.search["cur"] = [0, 0]
        tv.command("enter")                                              # A
        tv.command("right")
        tv.command("enter")                                              # B
        tv.command("down")
        tv.command("enter")                                              # L
        self.assertEqual(tv.search["query"], "abl")
        tv.command("red")                                                # delete
        self.assertEqual(tv.search["query"], "ab")
        tv.command("yellow")                                             # clear
        self.assertEqual(tv.search["query"], "")
        self.assertEqual(tv.tt["page"], 501)

    def test_remote_digits_are_typed_on_the_search_page(self):
        tv = self.tv
        tv.command("page:501")
        tv.command("digit:4")
        tv.command("digit:2")
        self.assertEqual(tv.search["query"], "42")
        self.assertEqual(tv.tt["page"], 501)

    def test_typing_and_enter_searches(self):
        tv = self.tv
        tv.command("page:501")
        for c in ("char:s", "char:h", "char:o", "char:space", "char:x", "backspace", "backspace"):
            tv.command(c)
        self.assertEqual(tv.search["query"], "sho")
        tv.command("enter")                                              # the cursor is back on GO after typing
        drain(tv)
        self.assertEqual(tv.tt["page"], 502)

    def test_empty_search_says_so(self):
        tv = self.tv
        tv.command("page:501")
        tv.command("enter")
        self.assertEqual(tv.tt["page"], 501)
        self.assertIn("TYPE SOMETHING", tv.tt["status"])

    def test_text_mode_follows_the_search_page(self):
        tv = self.tv
        sent = []
        tv.mpv.command = lambda *a: sent.append(a)
        tv.command("page:501")
        self.assertTrue(inputs.TEXT_MODE)
        self.assertIn(("keybind", "a", "script-message tv char:a"), sent)
        self.assertIn(("keybind", "SPACE", "script-message tv char:space"), sent)
        sent.clear()
        tv.command("page:100")
        self.assertFalse(inputs.TEXT_MODE)
        self.assertIn(("keybind", "g", "script-message tv guide"), sent)             # normal bindings come back
        self.assertIn(("keybind", "SPACE", "ignore"), sent)

    def test_closing_teletext_leaves_text_mode(self):
        tv = self.tv
        tv.command("page:501")
        tv.command("back")
        self.assertFalse(inputs.TEXT_MODE)
        self.assertIsNone(tv.tt)

    def test_terminal_and_evdev_use_text_mode(self):
        self.assertEqual(inputs._EVDEV_CHARS[30], "a")
        self.assertEqual(inputs._EVDEV_CHARS[57], "space")

    def test_pages_list_includes_search_and_valid_result_numbers(self):
        pages = ttpages.Pages(self.tv)
        self.assertTrue({500, 501, 502, 503} <= set(pages.numbers()))
        self.assertFalse(pages.valid(510))
        self.tv.search["results"] = [{}]
        self.assertTrue(pages.valid(510))
        self.assertFalse(pages.valid(511))


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tv = make_tv(self._d.name)
        self.addCleanup(self._d.cleanup)
        self.rem = {"cid": "chan-1", "dial": 3, "name": "CHANNEL 1", "title": "Show B1", "start_ms": self.tv.src.now_ms() + 90_000,
                    "end_ms": self.tv.src.now_ms() + 90_000 + 1_800_000}
        self.toasts = []
        self.tv.toast = lambda ass, secs=1.8: self.toasts.append(ass)

    def test_toggle_persists_and_removes(self):
        tv = self.tv
        self.assertTrue(tv.toggle_reminder(self.rem))
        self.assertTrue(tv.has_reminder("chan-1", self.rem["start_ms"]))
        self.assertEqual(make_tv(self._d.name).reminders[0]["title"], "Show B1")     # a restart keeps it
        self.assertFalse(tv.toggle_reminder(self.rem))
        self.assertEqual(make_tv(self._d.name).reminders, [])

    def test_banner_comes_once_two_minutes_ahead(self):
        tv = self.tv
        tv.toggle_reminder(self.rem)
        tv.check_reminders(time.time())
        self.assertEqual(len(self.toasts), 1)
        self.assertIn("COMING UP ON CH 3", self.toasts[0])
        tv.check_reminders(time.time() + 2)
        self.assertEqual(len(self.toasts), 1)
        self.assertEqual(len(tv.reminders), 1)

    def test_far_off_reminders_stay_quiet(self):
        tv = self.tv
        tv.toggle_reminder(dict(self.rem, start_ms=tv.src.now_ms() + 3_600_000))
        tv.check_reminders(time.time())
        self.assertEqual(self.toasts, [])

    def test_start_announces_and_drops_the_reminder(self):
        tv = self.tv
        tv.toggle_reminder(dict(self.rem, start_ms=tv.src.now_ms() - 10_000))
        tv.tune = lambda i: self.fail("must not tune unless asked to")
        tv.check_reminders(time.time())
        self.assertIn("STARTING NOW ON CH 3", self.toasts[-1])
        self.assertEqual(tv.reminders, [])

    def test_autotune_switches_channel(self):
        tv = self.tv
        tv.cfg["reminder_autotune"] = True
        tuned = []
        tv.tune = lambda i: tuned.append(i)
        tv.toggle_reminder(dict(self.rem, start_ms=tv.src.now_ms() - 10_000))
        tv.check_reminders(time.time())
        self.assertEqual(tuned, [1])
        self.assertEqual(tv.reminders, [])

    def test_old_reminders_are_dropped_silently(self):
        tv = self.tv
        tv.toggle_reminder(dict(self.rem, start_ms=tv.src.now_ms() - 3_600_000))
        tv.check_reminders(time.time())
        self.assertEqual((self.toasts, tv.reminders), ([], []))

    def test_banner_also_shows_on_the_teletext_status_line(self):
        tv = self.tv
        tv.command("text")
        tv.toggle_reminder(self.rem)
        tv.check_reminders(time.time())
        self.assertIn("COMING UP", tv.tt["status"])

    def test_yellow_key_on_a_programme_page_toggles_the_reminder(self):
        tv = self.tv
        tv.command("page:401")
        drain(tv)
        self.assertEqual(tv.tt["shown"].actions["y"][1], "REMIND")
        tv.command("yellow")
        drain(tv)
        self.assertEqual(len(tv.reminders), 1)
        self.assertEqual(tv.reminders[0]["title"], "Series: Show B0")
        self.assertEqual(tv.tt["shown"].actions["y"][1], "NO REMIND")
        tv.command("page:503")
        self.assertIn("Show B0", "\n".join(r for s in tv.tt["shown"].subpages for r in s))
        tv.command("red")                                                # CLEAR ALL
        self.assertEqual(tv.reminders, [])

    def test_no_reminder_key_on_the_show_that_is_on_air(self):
        tv = self.tv
        tv.command("page:400")
        drain(tv)
        self.assertNotIn("y", tv.tt["shown"].actions)


if __name__ == "__main__":
    unittest.main()
