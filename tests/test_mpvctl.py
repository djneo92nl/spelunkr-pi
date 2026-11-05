import unittest

from support import make_tv  # noqa: F401  (puts the project on sys.path)
from mpvctl import Mpv


class OverlayTests(unittest.TestCase):
    def fake(self):
        m = Mpv.__new__(Mpv)                      # no mpv process: only the command it would send matters
        m.mono, m.res, m.sent = False, (768, 576), []
        m.command = lambda *a: m.sent.append(a)
        return m

    def test_overlays_have_a_fixed_stacking_order(self):
        m = self.fake()
        m.overlay(9, "{\\an7}x")
        m.overlay(2, "{\\an7}y")
        self.assertEqual(m.sent[0], ("osd-overlay", 9, "ass-events", "{\\an7}x", 768, 576, 9))
        self.assertEqual(m.sent[1][-1], 2)         # z follows the id, whatever was updated last

    def test_clearing_an_overlay(self):
        m = self.fake()
        m.overlay(4, "")
        self.assertEqual(m.sent[0], ("osd-overlay", 4, "none", ""))


if __name__ == "__main__":
    unittest.main()
