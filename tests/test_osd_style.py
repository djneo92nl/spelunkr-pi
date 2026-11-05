import os
import tempfile
import unittest

from support import chan, make_tv, prog, NOW  # noqa: F401  (puts the project on sys.path)
import doctor
import osd
from test_doctor import FakeEnv, cfg as doctor_cfg


def sample_screens():
    ch = chan(0)
    item = {"id": "x", "title": "A Rather Long Title That Needs To Wrap Onto Two Lines Of The Details Screen", "year": 1986, "mature": True}
    r = {"entry": {"type": "video", "item": item, "durMs": 1_800_000}, "offset_s": 300, "remaining_s": 1500}
    up = [{"start_ms": NOW, "item": {"title": "Next"}}]
    rows = [{"chan": dict(ch, fav=True), "programs": [prog(0, 30, "Show"), prog(30, 30, "Other")], "breaks": [{"start_ms": NOW + 1_800_000, "end_ms": NOW + 1_900_000, "kind": "ad"}]},
            {"chan": dict(ch, hidden=True), "programs": None}, {"chan": ch, "programs": "error"}]
    bump = {"half": None, "channelBump": {"slides": [{"text": "a line of trivia that is long enough to wrap " * 2}]}, "platformBump": None}
    return {
        "info_bar": osd.info_bar(dict(ch, fav=True), r, up, NOW, sleep_s=120),
        "info_bar_ad": osd.info_bar(ch, {"entry": {"type": "ad"}, "offset_s": 0, "remaining_s": 0}, up, NOW),
        "guide": osd.guide(rows, 0, 0, NOW, NOW, "A description"),
        "now": osd.now_list(rows, 0, 0, NOW),
        "details": osd.details_screen(ch, item, {"description": "word " * 80, "season": 1, "imdbId": "tt1", "stars": ["A", "B"]}, 1800),
        "details_bare": osd.details_screen(ch, {"title": "T", "id": "t"}, None, None),
        "menu": osd.menu_screen([("Volume", "100%"), ("Audio output", "Speakers")], 1),
        "help": osd.help_screen(["profile mac"]),
        "tune": osd.tune_card(ch, ["CONNECTING", "3 s"], opaque=True),
        "up_next": osd.up_next_card(ch, {"item": {"title": "Something " * 8}, "start_ms": NOW}),
        "stand_by": osd.stand_by("Trying again...", False),
        "test_card": osd.test_card("PLEASE STAND BY", "reason"),
        "splash": osd.splash(["TUNING IN..."]),
        "slide": osd.bump_frame(bump, 4000, ch)[1],
        "card": osd.bump_frame(bump, 100, ch)[1],
        "toasts": osd.toast_text("HELLO") + osd.volume_toast(40, False) + osd.volume_toast(0, True) + osd.channel_entry("12"),
        "diag": osd.diag_panel(["a", "b"]),
        "goodnight": osd.goodnight_card(),
    }


class StyleTests(unittest.TestCase):
    def tearDown(self):
        osd.set_pixel_fonts(True)

    def test_every_screen_renders_in_both_looks(self):
        for pixel in (True, False):
            osd.set_pixel_fonts(pixel)
            for name, ass in sample_screens().items():
                self.assertTrue(ass.strip(), f"{name} is empty (pixel={pixel})")

    def test_pixel_fonts_use_the_websites_faces(self):
        osd.set_pixel_fonts(True)
        out = osd.info_bar(chan(0), {"entry": {"type": "video", "item": {"title": "T"}, "durMs": 60000}, "offset_s": 1, "remaining_s": 59}, [], NOW)
        self.assertIn("\\fnStar4 Large", out)
        self.assertIn("\\fnSTAR 4 Extended", out)
        self.assertNotIn("\\b1", out)

    def test_plain_look_is_the_old_default_font_in_bold(self):
        osd.set_pixel_fonts(False)
        out = osd.text(0, 0, "x", 20)
        self.assertNotIn("\\fn", out)
        self.assertIn("\\b1", out)
        self.assertIn("\\fs20", out)

    def test_nominal_sizes_scale_for_the_pixel_fonts(self):
        osd.set_pixel_fonts(True)
        self.assertIn("\\fs16", osd.text(0, 0, "x", 20))                    # body: 0.8
        self.assertIn("\\fs23", osd.text(0, 0, "x", 20, font="title"))      # title: 1.15

    def test_multiline_places_each_line(self):
        out = osd.multiline(100, 100, ["a", "b", "c"], 20, 30, an=5)
        self.assertEqual(out.count("\\pos("), 3)
        self.assertIn("\\pos(100,70)", out)                                 # centred on y
        self.assertIn("\\pos(100,130)", out)

    def test_logo_is_cached_and_scaled(self):
        c = chan(0)
        a = osd.logo(c, 10, 10, 3, 4)
        self.assertEqual(a, osd.logo(c, 10, 10, 3, 4))
        self.assertIn("\\p1", a)
        self.assertEqual(osd.logo({}, 0, 0), "")

    def test_text_cannot_inject_override_codes(self):
        out = osd.toast_text("{\\an9}evil")
        self.assertNotIn("{\\an9}evil", out)

    def test_canvas_width_follows_the_window_shape(self):
        try:
            osd.set_canvas(1024)
            self.assertIn("1024", "".join(osd.rect(0, 0, osd.W, osd.H, osd.BLACK)))
            self.assertTrue(osd.guide([], 0, 0, NOW, NOW))
        finally:
            osd.set_canvas(768)


class FontSetupTests(unittest.TestCase):
    def test_doctor_checks_the_fonts(self):
        with tempfile.TemporaryDirectory() as d:
            c = doctor_cfg(d)
            ok = FakeEnv({"--list-options": (0, " --osd-fonts-dir  String")}, have=["mpv"], readable=[os.path.join(os.path.dirname(doctor.__file__), "fonts")])
            self.assertEqual(doctor.check_pixel_fonts(c, ok)[0][0], doctor.OK)
            old = FakeEnv({"--list-options": (0, " --sub-fonts-dir  String")}, have=["mpv"], readable=[os.path.join(os.path.dirname(doctor.__file__), "fonts")])
            self.assertEqual(doctor.check_pixel_fonts(c, old)[0][0], doctor.WARN)
            self.assertEqual(doctor.check_pixel_fonts(dict(c, pixel_fonts=False), old)[0][0], doctor.INFO)

    def test_setup_fonts_switches_the_look(self):
        import tv as TVM
        cfg = {"prof": {"mpv": ["mpv"]}}
        real = TVM.subprocess.run
        try:
            class R:
                def __init__(self, out):
                    self.stdout = out
            TVM.subprocess.run = lambda argv, **k: R(" --osd-fonts-dir  String" if "--list-options" in argv else "")
            args = TVM.setup_fonts(cfg)
            self.assertTrue(args[0].startswith("--osd-fonts-dir=") and args[0].endswith("fonts"))
            self.assertTrue(osd.PIXEL)
            TVM.subprocess.run = lambda argv, **k: R("")
            self.assertEqual(TVM.setup_fonts(cfg), [])
            self.assertFalse(osd.PIXEL)                                      # no folder option, not installed: the old look
            self.assertEqual(TVM.setup_fonts({"prof": {"mpv": ["mpv"]}, "pixel_fonts": False}), [])
        finally:
            TVM.subprocess.run = real
            osd.set_pixel_fonts(True)


if __name__ == "__main__":
    unittest.main()
