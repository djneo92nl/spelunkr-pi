import queue
import tempfile
import time
import unittest

from support import make_tv
import ttpages


def drain(tv, timeout=3.0):
    """Run queued events (page builds finishing) until none arrive."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            kind, val = tv.q.get(timeout=0.2)
        except queue.Empty:
            return
        tv.handle(kind, val)


class TeletextStateTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tv = make_tv(self._d.name)

    def tearDown(self):
        self._d.cleanup()

    def test_open_close(self):
        tv = self.tv
        tv.command("text")
        self.assertEqual(tv.tt["page"], 100)
        self.assertIn(9, tv.mpv.overlays)
        self.assertIn(10, tv.mpv.overlays)
        tv.command("text")
        self.assertIsNone(tv.tt)
        self.assertNotIn(9, tv.mpv.overlays)

    def test_digits_jump_to_a_page_and_do_not_change_channel(self):
        tv = self.tv
        tv.command("text")
        idx = tv.idx
        for d in "101":
            tv.command(f"digit:{d}")
        self.assertEqual(tv.tt["page"], 101)
        drain(tv)
        self.assertTrue(tv.tt["shown"].title.startswith("NOW & NEXT"))
        self.assertEqual(tv.idx, idx)

    def test_partial_digits_show_in_header_and_time_out(self):
        tv = self.tv
        tv.command("text")
        tv.command("digit:2")
        self.assertEqual(tv.tt["entry"], "2")
        tv.update_tt(time.time() + 10)
        self.assertEqual(tv.tt["entry"], "")

    def test_up_down_walk_the_page_list(self):
        tv = self.tv
        tv.command("text")
        tv.command("up")
        self.assertEqual(tv.tt["page"], 101)
        tv.command("down")
        tv.command("down")
        self.assertEqual(tv.tt["page"], 900)            # wraps round

    def test_colour_keys_follow_the_page_links(self):
        tv = self.tv
        tv.command("text")
        tv.command("yellow")
        self.assertEqual(tv.tt["page"], 300)
        tv.command("red")
        self.assertEqual(tv.tt["page"], 100)

    def test_enter_on_a_channel_page_tunes_and_closes(self):
        tv = self.tv
        tv.tune = lambda i: setattr(tv, "tuned", i)
        tv.command("page:202")
        drain(tv)
        self.assertEqual(tv.tt["page"], 202)
        tv.command("enter")
        self.assertIsNone(tv.tt)
        self.assertEqual(tv.tuned, 1)

    def test_back_closes(self):
        tv = self.tv
        tv.command("text")
        tv.command("back")
        self.assertIsNone(tv.tt)

    def test_unknown_page_says_so(self):
        tv = self.tv
        tv.command("page:999")
        drain(tv)
        self.assertEqual(tv.tt["shown"].title, "PAGE NOT FOUND")

    def test_subpages_rotate_and_left_right_hold(self):
        tv = self.tv
        tv.command("page:110")
        drain(tv)
        tv.tt["shown"].subpages = [["a"], ["b"]]
        tv.update_tt(time.time() + 60)
        self.assertEqual(tv.tt["sub"], 1)
        tv.command("left")
        self.assertEqual(tv.tt["sub"], 0)
        self.assertTrue(tv.tt["hold"])
        tv.update_tt(time.time() + 120)
        self.assertEqual(tv.tt["sub"], 0)               # held

    def test_opening_a_panel_closes_teletext(self):
        tv = self.tv
        tv.command("text")
        tv.open_panel("help")
        self.assertIsNone(tv.tt)


class PageContentTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tv = make_tv(self._d.name, feeds=[{"name": "One", "url": "http://127.0.0.1:9/x"}])
        self.pages = ttpages.Pages(self.tv)

    def tearDown(self):
        self._d.cleanup()

    def text(self, n):
        return "\n".join(r for s in self.pages.build(n).subpages for r in s)

    def test_programme_page_shows_extra_site_data(self):
        t = self.text(401)                              # the second show: series, mature
        self.assertIn("Series", t)
        self.assertIn("MATURE", t)
        self.assertIn("imdb.com/title/tt1234567", t)
        self.assertIn("S2E?", t)
        self.assertIn("Something described here.", t)

    def test_channel_page_has_stats_and_description(self):
        t = self.text(201)
        self.assertIn("10 programmes", t)
        self.assertIn("10 hours of video", t)
        self.assertIn("A channel made for tests.", t)

    def test_listing_marks_the_show_on_air(self):
        self.assertIn("Show A0", self.text(301))

    def test_every_listed_page_builds(self):
        for n in self.pages.numbers():
            if n in (600, 601, 602, 603, 701):
                continue                                  # need a real bundle / network
            p = self.pages.build(n)
            self.assertNotEqual(p.title, "PAGE UNAVAILABLE", n)
            for sub in p.subpages:
                self.assertLessEqual(len(sub), 20)

    def test_feed_pages_degrade_when_the_network_is_down(self):
        self.assertEqual(self.pages.build(701).title, "PAGE UNAVAILABLE")
        self.assertIn("One", self.text(700))

    def test_no_feeds_explains_how_to_add_them(self):
        self.tv.cfg["feeds"] = []
        self.assertIn("config.json", self.text(700))


if __name__ == "__main__":
    unittest.main()
