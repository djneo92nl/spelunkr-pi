import unittest

from support import make_tv  # noqa: F401  (also puts the project on sys.path)
import teletext as T


class MarkupTests(unittest.TestCase):
    def test_colour_runs(self):
        row = T.parse_row("{y}ab{R}{w}cd")
        self.assertEqual([(c, t) for c, t, fg, bg in row.runs], [(0, "ab"), (2, "cd")])
        self.assertEqual(row.runs[0][2], T.COLOURS["y"])
        self.assertEqual(row.runs[1][3], T.COLOURS["r"])

    def test_rows_are_cut_at_forty_columns(self):
        row = T.parse_row("x" * 100)
        self.assertEqual(sum(len(t) for _, t, _, _ in row.runs), T.COLS)

    def test_double_height_flag(self):
        self.assertTrue(T.parse_row("{dh}hello").double)
        self.assertFalse(T.parse_row("hello").double)

    def test_clean_removes_codes_and_wide_characters(self):
        s = T.clean("a{y}b\x07中\U0001F600")
        self.assertNotIn("{", s)
        self.assertNotIn("\x07", s)
        self.assertEqual(s, "a(y)b??")

    def test_text_from_outside_cannot_inject_codes(self):
        row = T.parse_row("{w}" + T.clean("{R}evil{K}"))
        self.assertTrue(all(bg == T.BLACK for _, _, _, bg in row.runs))

    def test_wrap_keeps_blank_lines(self):
        self.assertEqual(T.wrap("one\n\ntwo", 10), ["one", "", "two"])
        self.assertTrue(all(len(x) <= 10 for x in T.wrap("word " * 30, 10)))

    def test_paginate_counts_double_height_rows_twice(self):
        subs = T.paginate(["{dh}x"] * 15, per=20)
        self.assertEqual([len(s) for s in subs], [10, 5])
        self.assertEqual(len(T.paginate([], 20)), 1)

    def test_dots_fills_the_width(self):
        row = T.parse_row(T.dots("Now", "101"))
        self.assertEqual(sum(len(t) for _, t, _, _ in row.runs), T.COLS)

    def test_logo_is_cols_by_rows(self):
        rows = T.logo_rows({"color": "#336699", "logomark": {"rows": ["cc..", "ckkc", "cddc", ".ggp"]}}, cols=4, rows=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(T.logo_rows({}, cols=4, rows=2)), 2)

    def test_render_produces_overlay_events(self):
        page = T.Page(123, "TITLE", [["{w}hello", "{y}world"], ["{c}two"]], links={"r": (100, "INDEX")})
        ass = T.render(page, 1)
        self.assertIn("two", ass)
        self.assertNotIn("hello", ass)
        self.assertIn("INDEX", ass)
        self.assertIn("2/2", ass)
        self.assertIn("P123", T.render_header("P123", 1_800_000_000_000))


if __name__ == "__main__":
    unittest.main()
