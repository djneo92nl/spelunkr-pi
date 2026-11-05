"""Ceefax-style teletext: a 40x25 character grid drawn as ASS overlays (like osd.py), plus the page model.

A page is a few sub-pages of rows. A row is a string with teletext-like colour codes in braces:

    {r} {g} {y} {b} {m} {c} {w} {k}     foreground: red green yellow blue magenta cyan white black
    {R} {G} {Y} {B} {M} {C} {W} {K}     background (a coloured run of spaces makes a block, a bar, a rule)
    {#RRGGBB} {@RRGGBB}                 any foreground / background colour (used for the channel logos)
    {dh}                                first thing in a row: double height (the row below is skipped)

Every row starts white on black. Text from outside (feeds, titles) goes through clean() so it cannot contain codes.
The look comes from a monospace font stretched to the grid: set the font with set_font() (config "teletext_font").
"""
import datetime
import re
import sys
import textwrap
import unicodedata

import osd

COLS, ROWS = 40, 25
CW, CH = 17, 22                      # one cell on the 768x576 canvas: 40 x 17 = 680 wide, 25 x 22 = 550 high
BODY_FIRST, BODY_ROWS = 3, 20        # rows 0 header, 1-2 title, 3-22 body, 23 status line, 24 fastext
FS = 24
FSCX = round(100 * CW / (0.602 * FS))    # monospace fonts advance ~0.6 em: stretch the glyphs to fill the cell
GLYPH_DY = -3                            # libass puts the line box a little low

COLOURS = {"k": "&H000000&", "r": "&H0000FF&", "g": "&H00FF00&", "y": "&H00FFFF&",
           "b": "&HFF0000&", "m": "&HFF00FF&", "c": "&HFFFF00&", "w": "&HFFFFFF&"}
BLACK, WHITE = COLOURS["k"], COLOURS["w"]
_TOKEN = re.compile(r"\{(dh|[a-zA-Z]|[#@][0-9A-Fa-f]{6})\}")

_font = "Menlo" if sys.platform == "darwin" else "DejaVu Sans Mono"


def set_font(name):
    global _font
    if name:
        _font = name


# ---- text helpers ----
def clean(s):
    """Plain single-line text that is safe inside a row: no braces, no control characters, nothing double-width."""
    out = []
    for ch in str(s):
        if ch in "{}":
            ch = "()"["{}".index(ch)]
        elif ch in "\r\n\t":
            ch = " "
        cat = unicodedata.category(ch)
        if cat.startswith("C") or cat in ("Mn", "Me", "Zl", "Zp"):
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F") or ord(ch) > 0xFFFF:
            ch = "?"
        out.append(ch)
    return "".join(out)


def wrap(s, width=COLS - 2):
    """Word-wrapped lines of clean text; blank lines in the input are kept as blank lines."""
    lines = []
    for para in str(s).split("\n"):
        para = clean(para).strip()
        lines.extend(textwrap.wrap(para, width, break_long_words=True) or [""])
    return lines


def fit(s, n):
    s = clean(s)
    return s if len(s) <= n else s[:max(0, n - 1)] + "~"


def dots(label, right, width=COLS, lc="{w}", dc="{b}", rc="{y}"):
    """'label ........ right' filled to `width` columns."""
    label, right = clean(label), clean(right)
    label = label[:max(0, width - len(right) - 2)]
    return f"{lc}{label} {dc}{'.' * max(1, width - len(label) - len(right) - 2)} {rc}{right}"


def progress(frac, width=20, done="{G}", todo="{B}"):
    n = max(0, min(width, round(frac * width)))
    return f"{done}{' ' * n}{todo}{' ' * (width - n)}{{K}}"


def paginate(rows, per=BODY_ROWS):
    """Cut a long list of rows into sub-pages (a double-height row counts for two)."""
    subs, cur, used = [], [], 0
    for r in rows:
        h = 2 if r.startswith("{dh}") else 1
        if used + h > per and cur:
            subs.append(cur)
            cur, used = [], 0
        cur.append(r)
        used += h
    if cur or not subs:
        subs.append(cur)
    return subs


# ---- page model ----
class Page:
    """number: 100-999. subpages: [[row, ...], ...]. links: {'r'|'g'|'y'|'c': (page number, label)} for the coloured keys.
    tune: a channel id that ENTER switches to. rotate_s: seconds per sub-page when there are several (0 = never).
    refresh_s: rebuild this often while shown (0 = never).
    actions: {'r'|'g'|'y'|'c': (name, label, payload)}: a colour key that does something instead of jumping to a page
    (it wins over a link on the same key); the TV runs tt_action(name, payload)."""

    def __init__(self, number, title, subpages, links=None, tune=None, rotate_s=12, status="", refresh_s=0, actions=None):
        self.number, self.title = number, title
        self.refresh_s = refresh_s          # rebuild the page this often while it is on screen (live status pages)
        self.subpages = subpages or [[]]
        self.links = links or {}
        self.actions = actions or {}
        self.tune = tune
        self.rotate_s = rotate_s
        self.status = status

    @classmethod
    def message(cls, number, title, *lines, links=None):
        return cls(number, title, [[f"{{w}}{ln}" for ln in lines]], links=links, rotate_s=0)


# ---- row parsing ----
def _spec(code):
    if code[0] in "#@":
        h = code[1:]
        return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()
    return COLOURS.get(code.lower(), WHITE)


class Row:
    __slots__ = ("double", "runs")

    def __init__(self, double, runs):
        self.double, self.runs = double, runs      # runs: [(column, text, fg, bg)]


def parse_row(markup):
    double, fg, bg = False, WHITE, BLACK
    runs, col = [], 0
    parts = _TOKEN.split(markup)
    for i, part in enumerate(parts):
        if i % 2 == 0:                                         # text
            part = part[:max(0, COLS - col)]
            if not part:
                continue
            if runs and runs[-1][2] == fg and runs[-1][3] == bg and runs[-1][0] + len(runs[-1][1]) == col:
                runs[-1] = (runs[-1][0], runs[-1][1] + part, fg, bg)
            else:
                runs.append((col, part, fg, bg))
            col += len(part)
        elif part == "dh":
            double = True
        elif part[0] == "@" or (part[0] != "#" and part.isupper()):
            bg = _spec(part)
        else:
            fg = _spec(part)
    return Row(double, runs)


# ---- drawing ----
def _geometry():
    return (osd.W - COLS * CW) // 2, (osd.H - ROWS * CH) // 2


def _row_events(row, y, x0):
    bgs, fgs = [], []
    h = CH * (2 if row.double else 1)
    for col, txt, fg, bg in row.runs:
        x = x0 + col * CW
        if bg != BLACK:
            bgs.append(osd.rect(x, y, x + len(txt) * CW, y + h, bg))
        body = txt.strip(" ")
        if body:
            lead = len(txt) - len(txt.lstrip(" "))
            fgs.append(f"{{\\an7\\pos({x + lead * CW},{y + GLYPH_DY})\\fn{_font}\\fs{FS}\\fscx{FSCX}"
                       f"\\fscy{200 if row.double else 100}\\b0\\bord0\\shad0\\1c{fg}}}{osd.esc(body)}")
    return bgs, fgs


def _draw(rows):
    """rows: {row index: markup}. Everything not given stays black."""
    x0, y0 = _geometry()
    bgs, fgs = [], []
    for r, markup in rows.items():
        b, f = _row_events(parse_row(markup), y0 + r * CH, x0)
        bgs += b
        fgs += f
    return bgs + fgs


def _pad(s, n):
    return s + " " * max(0, n - len(s))


def header_row(number_text, now_ms):
    t = datetime.datetime.fromtimestamp(now_ms / 1000)
    right = t.strftime("%a %d %b %H:%M/%S")
    return f"{{w}}{_pad(clean(number_text), 5)}{{y}}SPELUNKR TEXT{{w}}{' ' * (COLS - 5 - 13 - len(right))}{right}"


def render_header(number_text, now_ms):
    """Row 0 on its own (an overlay of its own, redrawn every second for the clock)."""
    x0, y0 = _geometry()
    out = [osd.rect(x0, y0, x0 + COLS * CW, y0 + CH, BLACK)]
    out += _draw({0: header_row(number_text, now_ms)})
    return "\n".join(out)


def fastext_row(links):
    cells = []
    for key in "rgyc":
        num, label = links.get(key, (None, ""))
        cells.append(f"{{{key}}}{_pad(clean(label)[:9], 10)}" if num else " " * 10)
    return "".join(cells)


def render(page, sub, hold=False, status=""):
    """The page below the header row (title band, body, status line, fastext links) as one ASS overlay."""
    sub = max(0, min(sub, len(page.subpages) - 1))
    rows = {1: "{dh}{B}{w}" + _pad(" " + clean(page.title)[:COLS - 2], COLS)}
    r = BODY_FIRST
    for markup in page.subpages[sub]:
        if r >= BODY_FIRST + BODY_ROWS:
            break
        rows[r] = markup
        r += 2 if markup.startswith("{dh}") else 1
    n = len(page.subpages)
    left = clean(status or page.status)
    right = (f"{'HOLD ' if hold and n > 1 else ''}{sub + 1}/{n}") if n > 1 else ""
    rows[23] = f"{{c}}{_pad(left[:COLS - len(right) - 1], COLS - len(right))}{{y}}{right}"
    rows[24] = fastext_row({**page.links, **{k: (name, label) for k, (name, label, _) in page.actions.items()}})
    out = [osd.rect(0, 0, osd.W, osd.H, BLACK)] + _draw(rows)
    return "\n".join(out)


# ---- channel logo as block graphics ----
def _hex(h):
    h = (h or "#00C853").lstrip("#")
    if len(h) != 6:
        h = "00C853"
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(c, to, k):
    return tuple(round(a + (b - a) * k) for a, b in zip(c, to))


def accent(chan):
    """The channel's colour as a foreground code, lifted when it is too dark to read on black."""
    r, g, b = _hex(chan.get("color"))
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    if lum < 120:
        k = (120 - lum) / (255 - lum)
        r, g, b = (round(c + (255 - c) * k) for c in (r, g, b))
    return "{#%02X%02X%02X}" % (r, g, b)


def logo_rows(chan, cols=16, rows=8):
    """The channel's pixel logo shrunk to cols x rows character cells (a half block per cell: two pixels high)."""
    pix = (chan.get("logomark") or {}).get("rows") or []
    if not pix or not pix[0]:
        return [" " * cols] * rows
    base = _hex(chan.get("color"))
    shade = {"c": base, "d": _mix(base, (0, 0, 0), 0.45), "l": _mix(base, (255, 255, 255), 0.45),
             "k": _mix(base, (0, 0, 0), 0.85), "g": (255, 255, 255), "p": _mix(base, (255, 255, 255), 0.75)}
    h, w = len(pix), len(pix[0])

    def at(i, k):
        y = min(h - 1, int((k + 0.5) * h / (rows * 2)))
        x = min(len(pix[y]) - 1, int((i + 0.5) * w / cols))
        return shade.get(pix[y][x])

    out = []
    for j in range(rows):
        cells = []
        for i in range(cols):
            top, bot = at(i, 2 * j), at(i, 2 * j + 1)
            fg = "{#%02X%02X%02X}" % top if top else "{k}"
            bg = "{@%02X%02X%02X}" % bot if bot else "{K}"
            cells.append(f"{bg}{fg}▀" if (top or bot) else "{K} ")
        out.append("".join(cells) + "{K}{w}")
    return out
