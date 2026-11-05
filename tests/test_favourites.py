import tempfile
import unittest

from support import make_tv


class FavouriteTests(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.tv = make_tv(self._d.name, n_chans=5)
        self.tuned = []
        self.tv.tune = lambda i: (self.tuned.append(i), setattr(self.tv, "idx", i))

    def tearDown(self):
        self._d.cleanup()

    def ids(self, *idx):
        return [self.tv.chans[i]["id"] for i in idx]

    def test_up_and_down_skip_hidden_channels(self):
        tv = self.tv
        tv.hidden = set(self.ids(1, 4))
        tv.command("up")
        self.assertEqual(tv.idx, 2)
        tv.command("down")
        self.assertEqual(tv.idx, 0)
        tv.command("down")                                # wraps past the hidden last channel
        self.assertEqual(tv.idx, 3)

    def test_only_favourites(self):
        tv = self.tv
        tv.favs = set(self.ids(1, 3))
        tv.cfg["favourites_only"] = True
        tv.command("up")
        self.assertEqual(tv.idx, 1)
        tv.command("up")
        self.assertEqual(tv.idx, 3)
        tv.command("up")
        self.assertEqual(tv.idx, 1)

    def test_only_favourites_with_none_set_surfs_everything(self):
        tv = self.tv
        tv.cfg["favourites_only"] = True
        tv.command("up")
        self.assertEqual(tv.idx, 1)

    def test_everything_hidden_still_surfs(self):
        tv = self.tv
        tv.hidden = {c["id"] for c in tv.chans}
        tv.command("up")
        self.assertEqual(tv.idx, 1)

    def test_digits_still_reach_a_hidden_channel(self):
        tv = self.tv
        tv.hidden = set(self.ids(2))
        tv.digits = str(tv.chans[2]["dial"])
        tv.commit_digits()
        self.assertEqual(tv.idx, 2)

    def test_toggle_favourite_and_hide_are_exclusive_and_persist(self):
        tv = self.tv
        cid = tv.chan["id"]
        tv.command("favourite")
        self.assertIn(cid, tv.favs)
        tv.command("hide")
        self.assertNotIn(cid, tv.favs)
        self.assertIn(cid, tv.hidden)
        tv.command("favourite")
        self.assertIn(cid, tv.favs)
        self.assertNotIn(cid, tv.hidden)
        again = make_tv(self._d.name, n_chans=5)          # a fresh start reads the same state file
        self.assertEqual(again.favs, {cid})

    def test_guide_marks_the_selected_channel_not_the_playing_one(self):
        tv = self.tv
        tv.open_guide()
        tv.guide["sel"] = 3
        tv.command("favourite")
        self.assertEqual(tv.favs, set(self.ids(3)))

    def test_flags_reach_the_screens(self):
        tv = self.tv
        tv.favs = set(self.ids(0))
        self.assertTrue(tv.dress(tv.chans[0])["fav"])
        self.assertFalse(tv.dress(tv.chans[1])["fav"])
        import osd
        row = {"chan": tv.dress(tv.chans[0]), "programs": []}
        self.assertIn("★", osd.guide([row], 0, 0, 0, 0))
        self.assertIn("★", osd.now_list([row], 0, 0, 0))

    def test_menu_entries_cycle_this_channel(self):
        tv = self.tv
        entry = lambda: next(e for e in tv.menu_entries() if e[0] == "This channel")
        self.assertEqual(entry()[1], "normal")
        entry()[2](1)
        self.assertEqual(entry()[1], "favourite")
        entry()[2](1)
        self.assertEqual(entry()[1], "hidden")
        entry()[2](1)
        self.assertEqual(entry()[1], "normal")


if __name__ == "__main__":
    unittest.main()
