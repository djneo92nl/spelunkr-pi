import queue
import tempfile
import time
import unittest
from unittest import mock

from support import NOW, make_tv
import osd
import schedule as S


def entry(kind, start, length, **extra):
    """A fake now_playing() result: `kind` playing from start to start + length (ms)."""
    e = {"type": kind, **extra}
    return {"entry": e, "version": 1, "start_ms": start, "end_ms": start + length}


class TimelineBreakTests(unittest.TestCase):
    SCRIPT = [entry("video", 0, 60_000, item={"title": "A"}, videoIndex=0),
              entry("bump", 60_000, 5_000),
              entry("ad", 65_000, 30_000),
              entry("ad", 95_000, 30_000),
              entry("video", 125_000, 60_000, item={"title": "B"}, videoIndex=1),
              entry("bump", 185_000, 4_000)]

    def fake(self, ch, man, plat, t):
        return next(e for e in self.SCRIPT if e["start_ms"] <= t < e["end_ms"])

    def run_timeline(self, **kw):
        with mock.patch.object(S, "now_playing", self.fake):
            return S.timeline({}, {}, {}, 0, 189_000, **kw)

    def test_default_is_videos_only(self):
        out = self.run_timeline()
        self.assertEqual([p["item"]["title"] for p in out], ["A", "B"])
        self.assertEqual(out[1]["videoIndex"], 1)
        self.assertTrue(all("kind" not in p for p in out))

    def test_breaks_become_one_cell_per_run(self):
        out = self.run_timeline(breaks=True)
        self.assertEqual([p.get("kind") or p["item"]["title"] for p in out], ["A", "ad", "B", "break"])
        gap = out[1]
        self.assertEqual((gap["start_ms"], gap["end_ms"]), (60_000, 125_000))      # slide + two ads, merged
        self.assertEqual((out[3]["start_ms"], out[3]["end_ms"]), (185_000, 189_000))


class GuideTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tv = make_tv(self._d.name, n_chans=3)

    def tearDown(self):
        self._d.cleanup()

    def pump(self, until=None, timeout=3):
        end = time.time() + timeout
        while time.time() < end:
            try:
                kind, val = self.tv.q.get(timeout=0.2)
            except queue.Empty:
                return
            self.tv.handle(kind, val)

    def load_guide(self):
        tv = self.tv
        tv.open_guide()
        g = tv.guide
        for ch in tv.chans:
            g["rows"][ch["id"]] = tv.src.timeline(ch["id"], NOW, NOW + osd.GUIDE_SPAN_MS)
        tv.draw_guide()

    def test_footer_description_is_fetched_then_drawn(self):
        tv = self.tv
        self.load_guide()
        self.assertNotIn("Something described here.", tv.mpv.overlays[4])
        self.pump()
        self.assertIn("Something described here.", tv.mpv.overlays[4])

    def test_details_key_in_the_guide_opens_the_selected_show(self):
        tv = self.tv
        self.load_guide()
        tv.guide["sel"] = 2
        tv.command("details")
        self.pump()
        self.assertIsNone(tv.guide)
        self.assertEqual(tv.panel, "details")
        item, det, runtime, chan = tv.panel_payload
        self.assertEqual(item["title"], "Show A2")
        self.assertEqual(chan["id"], tv.chans[2]["id"])
        self.assertEqual(det["imdbId"], "tt1234567")
        tv.update_panel(time.time())
        self.assertIn("CH 4", tv.mpv.overlays[7])          # the selected channel's dial, not the playing one's

    def test_details_key_while_listings_are_still_loading_says_so(self):
        tv = self.tv
        tv.open_guide()
        tv.command("details")
        self.assertEqual(tv.panel, None)

    def test_reset_clears_breaks_too(self):
        tv = self.tv
        tv.open_guide()
        tv.guide["brk"]["x"] = [1]
        tv.guide["rows"]["x"] = [1]
        tv._guide_reset(tv.guide)
        self.assertEqual((tv.guide["rows"], tv.guide["brk"], tv.guide["asked"]), ({}, {}, set()))


class GuideDrawTests(unittest.TestCase):
    def test_breaks_are_drawn_and_labelled_when_wide_enough(self):
        chan = {"dial": 5, "name": "X", "color": "#00C853"}
        t0 = NOW
        prog = {"start_ms": t0, "end_ms": t0 + 40 * 60000, "item": {"title": "Show"}}
        wide = {"start_ms": t0 + 40 * 60000, "end_ms": t0 + 55 * 60000, "kind": "ad"}
        thin = {"start_ms": t0 + 55 * 60000, "end_ms": t0 + 55 * 60000 + 1000, "kind": "break"}
        out = osd.guide([{"chan": chan, "programs": [prog], "breaks": [wide, thin]}], 0, 0, t0, t0)
        self.assertIn("ADS", out)
        self.assertNotIn("BREAK", out)                      # a one-second gap is too thin to draw
        self.assertNotIn("ADS", osd.guide([{"chan": chan, "programs": [prog]}], 0, 0, t0, t0))   # rows without breaks still work


if __name__ == "__main__":
    unittest.main()
