"""On-screen graphics as ASS overlays (drawn by mpv). Virtual canvas is 768x576 (PAL 4:3).

Styled after the spelunkr.tv website: the WeatherStar 4000 pixel fonts, navy panels with blue bevelled borders and hard shadows,
gold and yellow titles, orange gradient "buttons", the purple-to-orange sky and the blue striped guide grid.

Everything is plain text and rectangles (no images), so it stays cheap on a Pi 1B.
Safe area: ~7% in from each edge so nothing is clipped by a CRT's overscan.
"""
import datetime
import textwrap

import schedule as S

W, H = 768, 576
SAFE_X, SAFE_Y = 54, 40
DEFAULT_COLOR = "#00C853"


def set_canvas(width):
    """The canvas is always 576 high; its width follows the window shape (768 = 4:3, 1024 = 16:9)."""
    global W, _GX1
    W = int(width)
    _GX1 = W - SAFE_X


# ---- colours (ASS wants &HBBGGRR&) ----
def bgr(h):
    h = h.lstrip("#")
    return f"&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _rgb_tuple(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(c0, c1, t):
    return tuple(round(a + (b - a) * t) for a, b in zip(c0, c1))


def _col(rgb_tuple):
    return "&H%02X%02X%02X&" % (rgb_tuple[2], rgb_tuple[1], rgb_tuple[0])


WHITE, BLACK = "&HFFFFFF&", "&H000000&"
INK = bgr("#e8eaf0")            # the site's text colour
INK_MID = bgr("#8899cc")
INK_DIM = bgr("#445588")
GOLD = bgr("#ffd740")
YELLOW = bgr("#ffff00")
CYAN = bgr("#00ccdd")
GREEN = bgr("#44dd88")
RED = bgr("#ff5566")
PANEL = bgr("#0d1238")
PANEL_DEEP = bgr("#060b1e")
BORDER = bgr("#2a3580")
BORDER_HI = bgr("#4a5aaa")
STRIPE_A, STRIPE_B = bgr("#001040"), bgr("#102080")           # the guide's repeating blue
CELL_A, CELL_B = bgr("#2845b8"), bgr("#1f37a0")
CELL_SEL_A, CELL_SEL_B = bgr("#4468e8"), bgr("#3a5cd8")
TIME_STRIP = bgr("#26147a")
SKY_TOP, SKY_BOTTOM = _rgb_tuple("#2f1250"), _rgb_tuple("#be5a03")      # the site's purple-to-orange gradient
BTN_TOP, BTN_MID, BTN_BOT, BTN_EDGE, BTN_HI = bgr("#bc5904"), bgr("#974717"), bgr("#4e243c"), bgr("#141414"), bgr("#e2853a")

# ---- fonts ----
# WeatherStar 4000 pixel fonts from the website's own files (see fonts/README.md). Without them (old mpv, not installed,
# "pixel_fonts": false) everything falls back to the old look: the default font, bold, at the nominal size.
PIXEL = True
FONT_BODY, FONT_TITLE, FONT_SMALL = "Star4 Large", "STAR 4 Extended", "STAR 4"
_SCALE = {"body": 0.80, "title": 1.15, "small": 1.2}       # nominal size -> \fs, so one number means the same height in either look


def set_pixel_fonts(on):
    global PIXEL
    PIXEL = bool(on)


def cw(size):
    """About how many pixels one character of the body font takes at a nominal size (for fitting text)."""
    return size * (0.43 if PIXEL else 0.55)


def _font(kind, size):
    if not PIXEL:
        return "", size
    name = {"title": FONT_TITLE, "small": FONT_SMALL}.get(kind, FONT_BODY)
    return f"\\fn{name}", max(6, round(size * _SCALE.get(kind or "body", _SCALE["body"])))


def rgb(hex_color, fallback=DEFAULT_COLOR):
    h = (hex_color or fallback).lstrip("#")
    if len(h) != 6:
        h = fallback.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    if lum < 120:                              # channel colours are tuned for a light UI; lift dark ones for our dark one
        k = (120 - lum) / (255 - lum)
        r, g, b = (int(c + (255 - c) * k) for c in (r, g, b))
    return f"&H{b:02X}{g:02X}{r:02X}&"


def esc(text):
    return str(text).replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def wrap(text, width):
    return "\\N".join(textwrap.wrap(esc(text), width) or [""])


def lines_of(text_, width):
    return textwrap.wrap(esc(text_), width) or [""]


def rect(x0, y0, x1, y1, color, alpha="00"):
    return (f"{{\\an7\\pos(0,0)\\bord0\\shad0\\1c{color}\\1a&H{alpha}&\\p1}}"
            f"m {x0} {y0} l {x1} {y0} {x1} {y1} {x0} {y1}{{\\p0}}")


def text(x, y, s, size, color=WHITE, an=7, bold=True, border=2, font=None, shadow=0):
    """font: None (body, the site's default), "title" (the wide display face) or "small" (the compact one)."""
    fn, fs = _font(font, size)
    b = "" if PIXEL else f"\\b{int(bold)}"
    return (f"{{\\an{an}\\pos({x},{y}){fn}\\fs{fs}{b}\\bord{border}\\shad{shadow}"
            f"\\1c{color}\\3c&H000000&\\4c&H000000&}}{s}")


def multiline(x, y, lines, size, line_h, color=WHITE, an=7, **kw):
    """Several lines of text, each placed on its own (ASS's own line spacing follows the font and is too tight for the pixel fonts).
    an 5 / 8 / 2 centre the block on y / hang it from y / stand it on y."""
    n = len(lines)
    top = y - (n - 1) * line_h // 2 if an in (4, 5, 6) else y - (n - 1) * line_h if an in (1, 2, 3) else y
    return "\n".join(text(x, top + i * line_h, ln, size, color, an=an, **kw) for i, ln in enumerate(lines))


def clock(now_ms):
    t = datetime.datetime.fromtimestamp(now_ms / 1000)
    return t.strftime("%I:%M %p").lstrip("0")


def mmss(sec):
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def year_suffix(it):
    """"  (1984)" unless the title already says the year."""
    y = it.get("year")
    return f"  ({y})" if y and str(y) not in it.get("title", "") else ""


def _marked(chan, n):
    """Channel name for the guide lists: a star in front of a favourite."""
    return ("★ " if chan.get("fav") else "") + chan["name"][:n]


def short(s, n):
    s = esc(s)
    return s if len(s) <= n else s[: n - 1] + "…"


# ---- building blocks: gradients, bevelled panels, orange buttons, the S logo ----
def bands(x0, y0, x1, y1, c0, c1, n=None, alpha="00"):
    """A vertical gradient drawn as bands (c0, c1 are (r, g, b)); a few bands look like the site's banded sky and cost little."""
    n = n or max(2, (y1 - y0) // 10)
    out = []
    for i in range(n):
        ya, yb = y0 + (y1 - y0) * i // n, y0 + (y1 - y0) * (i + 1) // n
        out.append(rect(x0, ya, x1, yb, _col(_mix(c0, c1, i / max(1, n - 1))), alpha))
    return out


def panel(x0, y0, x1, y1, fill=PANEL, border=BORDER_HI, alpha="00", shadow=True):
    """Navy box with a 3 px blue border, a lit top edge and the site's hard drop shadow."""
    out = [rect(x0 + 5, y0 + 5, x1 + 5, y1 + 5, BLACK, "70")] if shadow else []
    out += [rect(x0, y0, x1, y1, border, alpha), rect(x0 + 3, y0 + 3, x1 - 3, y1 - 3, fill, alpha),
            rect(x0 + 3, y0 + 3, x1 - 3, y0 + 5, BORDER, alpha)]
    return out


def button(x0, y0, x1, y1, active=True):
    """The site's orange gradient button (or a blue one when not active)."""
    h = y1 - y0
    top, mid, bot = (BTN_TOP, BTN_MID, BTN_BOT) if active else (bgr("#5a55c8"), bgr("#2a26c8"), bgr("#0202c8"))
    return [rect(x0 - 2, y0 - 2, x1 + 2, y1 + 2, BTN_EDGE), rect(x0, y0, x1, y0 + int(h * .45), top),
            rect(x0, y0 + int(h * .45), x1, y0 + int(h * .75), mid), rect(x0, y0 + int(h * .75), x1, y1, bot),
            rect(x0, y0, x1, y0 + 2, BTN_HI if active else bgr("#8a85f0"))]


def keychip(x, y, label, size=15):
    """A small orange key cap with yellow text. Returns (events, width)."""
    w = int(len(label) * cw(size) * 1.0) + 18
    return button(x, y, x + w, y + 26) + [text(x + w // 2, y + 7, esc(label), size, YELLOW, an=8, border=1)], w


def sky(top=0, bottom=None):
    return bands(0, top, W, bottom or H, SKY_TOP, SKY_BOTTOM, n=24)


def s_logo(x, y, size=36):
    """The website's "S" mark: a gold box with a navy square and a gold S."""
    return [rect(x, y, x + size, y + size, GOLD), rect(x + 3, y + 3, x + size - 3, y + size - 3, PANEL_DEEP),
            text(x + size // 2, y + size // 2 + 1, "S", int(size * .75), GOLD, an=5, border=0, font="title")]


def header_bar(title, sub="", right=""):
    """Top band: S logo, a gold title (two small lines like the site), a clock or page label on the right."""
    out = bands(0, 0, W, 58, _rgb_tuple("#2a1160"), _rgb_tuple("#6a2a44"), n=6)
    out += [rect(0, 58, W, 62, bgr("#be5a03"))] + s_logo(SAFE_X - 10, 11, 36)
    if sub:
        out += [text(SAFE_X + 38, 12, esc(title), 17, GOLD, border=1, font="title"),
                text(SAFE_X + 38, 33, esc(sub), 17, GOLD, border=1, font="title")]
    else:
        out.append(text(SAFE_X + 38, 19, esc(title), 22, GOLD, border=1, font="title"))
    if right:
        out.append(text(W - SAFE_X + 6, 17, esc(right), 24, YELLOW, an=9, border=2, shadow=2))
    return out


def footer_bar(hint="", right="SPELUNKR.TV"):
    out = [rect(0, H - 38, W, H, bgr("#0a0e2a")), rect(0, H - 38, W, H - 35, bgr("#be5a03"))]
    if hint:
        out.append(text(SAFE_X - 10, H - 28, esc(hint), 15, INK_MID, border=0))
    out.append(text(W - SAFE_X + 6, H - 30, right, 18, GOLD, an=9, border=0, font="title"))
    return out


# ---- channel logo: the site's pixel mark, sampled down ----
_LOGO_CACHE = {}


def logo(chan, x, y, px=3, n=12):
    """The channel's pixel logo as n x n cells of px pixels, run-length merged into rectangles (cached per channel)."""
    pix = (chan.get("logomark") or {}).get("rows") or []
    if not pix or not pix[0]:
        return ""
    key = (chan.get("id"), chan.get("color"), x, y, px, n)
    if key not in _LOGO_CACHE:
        base = _rgb_tuple(chan.get("color") or DEFAULT_COLOR)
        shade = {"c": base, "d": _mix(base, (0, 0, 0), 0.45), "l": _mix(base, (255, 255, 255), 0.45),
                 "k": _mix(base, (0, 0, 0), 0.85), "g": (255, 255, 255), "p": _mix(base, (255, 255, 255), 0.75)}
        h, w = len(pix), len(pix[0])
        out = [rect(x - 2, y - 2, x + n * px + 2, y + n * px + 2, BLACK)]
        for j in range(n):
            row = pix[min(h - 1, int((j + .5) * h / n))]
            cells = [shade.get(row[min(len(row) - 1, int((i + .5) * w / n))]) for i in range(n)]
            i = 0
            while i < n:
                c = cells[i]
                k = i
                while k < n and cells[k] == c:
                    k += 1
                if c:
                    out.append(rect(x + i * px, y + j * px, x + k * px, y + (j + 1) * px, _col(c)))
                i = k
        if len(_LOGO_CACHE) > 80:
            _LOGO_CACHE.clear()
        _LOGO_CACHE[key] = "\n".join(out)
    return _LOGO_CACHE[key]


# ---- the cable-box bar ----
def info_bar(chan, r, upcoming, now_ms, sleep_s=None):
    """chan: dict(dial, name, color). r: now_playing() result. upcoming: [{start_ms, item}]."""
    e = r["entry"]
    color = rgb(chan.get("color"))
    top = H - SAFE_Y - 132
    x0, x1 = SAFE_X, W - SAFE_X
    lines = panel(x0, top, x1, H - SAFE_Y, fill=PANEL, alpha="18")
    lines.append(rect(x0 + 3, top + 5, x0 + 9, H - SAFE_Y - 3, color))
    dial = chan.get("dial")
    lg = logo(chan, x0 + 20, top + 14, 3, 12)
    if lg:
        lines.append(lg)
    lines.append(text(x0 + 70, top + 12, f"{dial}" if dial is not None else "--", 32, YELLOW, border=2, shadow=2, font="title"))
    lines.append(text(x0 + 150, top + 15, short(("★ " if chan.get("fav") else "") + chan["name"].upper(), int(340 / cw(26))), 26, WHITE))
    lines.append(text(x1 - 14, top + 15, clock(now_ms), 26, GOLD, an=9))
    if sleep_s:
        lines.append(text(x1 - 14, top + 50, f"SLEEP {mmss(sleep_s)}", 15, bgr("#ffa060"), an=9, border=1))

    tchars = int((x1 - x0 - 50) / cw(26))
    if e["type"] == "video":
        it = e["item"]
        mature = "{\\fs12\\1c&H5566FF&}  MATURE" if it.get("mature") else ""     # the site flags some shows
        lines.append(text(x0 + 22, top + 56, short(it["title"], tchars - (10 if mature else 0)) + year_suffix(it) + mature, 26, INK))
        total = e["durMs"] / 1000
        frac = min(1.0, max(0.0, r["offset_s"] / total)) if total else 0
        bx0, bx1, by = x0 + 22, x1 - 14, top + 94
        lines.append(rect(bx0 - 2, by - 2, bx1 + 2, by + 11, BTN_EDGE))
        lines.append(rect(bx0, by, bx1, by + 9, bgr("#1a2250")))
        lines.append(rect(bx0, by, int(bx0 + (bx1 - bx0) * frac), by + 9, GOLD))
        lines.append(text(bx0, by + 13, mmss(r["offset_s"]), 15, INK_MID, border=1))
        lines.append(text(bx1, by + 13, "-" + mmss(r["remaining_s"]), 15, INK_MID, an=9, border=1))
        if upcoming:
            n = upcoming[0]
            nxt = short(n["item"]["title"], int((bx1 - bx0 - 170) / cw(15)))
            lines.append(text(bx0 + 90, by + 13, f"{{\\1c{GOLD}}}NEXT {clock(n['start_ms'])}  {{\\1c{INK}}}{nxt}", 15, INK, border=1))
    elif e["type"] == "ad":
        lines.append(text(x0 + 22, top + 56, "COMMERCIAL BREAK", 26, GOLD))
        if upcoming:
            n = upcoming[0]
            lines.append(text(x0 + 22, top + 94, f"BACK TO: {short(n['item']['title'], int(560 / cw(20)))}", 20, INK_MID))
    else:
        lines.append(text(x0 + 22, top + 56, "STATION BREAK", 26, GOLD))
        if upcoming:
            n = upcoming[0]
            lines.append(text(x0 + 22, top + 94, f"NEXT {clock(n['start_ms'])}  {short(n['item']['title'], int(520 / cw(20)))}",
                              20, INK_MID))
    return "\n".join(lines)


# ---- bump breaks ----
def _slide_segments(bump):
    return [("slide", S.slide_ms(s["text"]), s["text"]) for s in bump["slides"]]


def bump_segments(entry):
    """Timeline of a break: list of (kind, duration_ms, payload). Mirrors schedule.break_ms()."""
    half = entry.get("half")
    ch, pl = entry.get("channelBump"), entry.get("platformBump")
    seq = []
    if half in (None, "open") and ch:
        seq += [("card", S.INTRO_MS, None)] + _slide_segments(ch) + [("blank", S.SLIDE_TAIL_MS + S.HOLD_MS, None)]
    if half is None:
        seq.append(("blank", S.GAP_MS, None))
    if half in (None, "close") and pl:
        seq += _slide_segments(pl) + [("blank", S.SLIDE_TAIL_MS + S.HOLD_MS, None)]
    return seq


def bump_frame(entry, offset_ms, chan):
    """Return (key, ass) for the current moment of a bump break. `key` changes only when the picture changes."""
    t = 0
    for i, (kind, dur, payload) in enumerate(bump_segments(entry)):
        if offset_ms < t + dur:
            if kind == "card":
                return (i, _card(chan))
            if kind == "slide":
                return (i, _slide(payload, chan))
            return (i, "")
        t += dur
    return (-1, "")


def _slide(txt, chan):
    c = rgb(chan.get("color"))
    body = multiline(W // 2, H // 2, lines_of(txt, max(14, int(560 / cw(40)))), 40, 52, WHITE, an=5, shadow=2)
    brand = text(W // 2, H - SAFE_Y - 6, "spelunkr.tv", 20, GOLD, an=2, border=0, font="title")
    return (body + "\n" + brand + "\n" +
            rect(W // 2 - 30, H // 2 + 90, W // 2 + 30, H // 2 + 94, c))


def _card(chan):
    c = rgb(chan.get("color"))
    dial = chan.get("dial")
    return "\n".join([
        text(W // 2, H // 2 - 40, f"CH {dial}" if dial is not None else "", 56, c, an=5, shadow=4, font="title"),
        text(W // 2, H // 2 + 30, esc(chan["name"].upper()), 38, WHITE, an=5, shadow=3)])


# ---- other screens ----
def up_next_card(chan, nxt):
    c = rgb(chan.get("color"))
    lines = [text(W // 2, H // 2 - 70, "UP NEXT", 36, GOLD, an=5, shadow=3, font="title")]
    if nxt:
        lines.append(multiline(W // 2, H // 2 + 10, lines_of(nxt["item"]["title"], max(12, int(600 / cw(36)))), 36, 46, WHITE, an=5, shadow=2))
        lines.append(text(W // 2, H // 2 + 110, clock(nxt["start_ms"]), 28, c, an=5, shadow=2))
    return "\n".join(lines)


def tune_card(chan, status=(), opaque=False):
    """Channel number + name top-left, status lines under it. `opaque` hides whatever frame mpv has underneath."""
    c = rgb(chan.get("color"))
    dial = chan.get("dial")
    lines = bands(0, 0, W, H, _rgb_tuple("#060b1e"), _rgb_tuple("#101a50"), n=16) if opaque else []
    lines += [
        text(SAFE_X, SAFE_Y, f"CH {dial}" if dial is not None else "CH", 64, YELLOW, an=7, border=3, shadow=4, font="title"),
        text(SAFE_X, SAFE_Y + 70, esc(chan["name"].upper()), 28, c, an=7, shadow=2)]
    for i, line in enumerate(status):
        lines.append(text(SAFE_X, SAFE_Y + 122 + i * 30, esc(line), 24 if i == 0 else 20,
                          WHITE if i == 0 else GOLD, an=7, border=2, shadow=2))
    return "\n".join(lines)


def stand_by(reason="", test_card_bg=True):
    """"PLEASE STAND BY" over the colour-bar test card (or plain black if test_card_bg is False)."""
    if test_card_bg:
        return test_card("PLEASE STAND BY", reason)
    lines = [text(W // 2, H // 2 - 30, "PLEASE STAND BY", 52, YELLOW, an=5, shadow=4, font="title")]
    if reason:
        lines.append(multiline(W // 2, H // 2 + 40, lines_of(reason, max(20, int(600 / cw(24)))), 24, 30, INK_MID, an=5))
    return "\n".join(lines)


def channel_entry(digits):
    return text(W - SAFE_X, SAFE_Y, digits or "", 64, YELLOW, an=9, border=3, shadow=4, font="title")


# ---- TV guide (Prevue-style grid) ----
GUIDE_SPAN_MS = 2 * 3600 * 1000
GUIDE_ROWS = 8
_GX0, _GX1 = SAFE_X + 172, W - SAFE_X
_ROW_H, _ROW_Y0 = 44, 106


def _tick_times(t0_ms):
    """Half-hour marks inside the window."""
    step = 30 * 60 * 1000
    t = (t0_ms // step + 1) * step
    while t < t0_ms + GUIDE_SPAN_MS:
        yield t
        t += step


def _x(t_ms, t0_ms):
    return int(_GX0 + (t_ms - t0_ms) / GUIDE_SPAN_MS * (_GX1 - _GX0))


def _outline(x0, y0, x1, y1, color, t=3):
    return [rect(x0, y0, x1, y0 + t, color), rect(x0, y1 - t, x1, y1, color), rect(x0, y0, x0 + t, y1, color),
            rect(x1 - t, y0, x1, y1, color)]


def _grid_panel(first_row_y, rows, row_h):
    """The navy box behind the rows of the guide / on-now list."""
    return panel(SAFE_X - 14, first_row_y - 40, W - SAFE_X + 14, first_row_y + rows * row_h + 6)


def guide(rows, sel, first, t0_ms, now_ms, desc=None):
    """rows: [{chan, programs: list | None (loading) | 'error', breaks: [{start_ms, end_ms, kind}] (optional)}] for the
    visible slice, rows[sel-first] is selected. desc: the selected show's description, once it has been fetched.
    Returns the ASS for a full-screen opaque guide."""
    lines = sky() + header_bar("Spelunkr", "TV Guide", clock(now_ms))
    lines += _grid_panel(_ROW_Y0, GUIDE_ROWS, _ROW_H)
    lines.append(rect(SAFE_X - 11, _ROW_Y0 - 36, W - SAFE_X + 11, _ROW_Y0 - 6, TIME_STRIP))
    lines.append(text(SAFE_X, _ROW_Y0 - 31, "CHANNEL", 15, WHITE, border=1))
    lines.append(text(_GX0 + 4, _ROW_Y0 - 31, "NOW" if t0_ms - now_ms < 60000 else clock(t0_ms), 15, YELLOW, border=1))
    for t in _tick_times(t0_ms):
        x = _x(t, t0_ms)
        if x - _GX0 > 70:                      # leave room for the NOW / start label
            lines.append(rect(x, _ROW_Y0 - 36, x + 2, _ROW_Y0 - 6, BORDER_HI))
            lines.append(text(x + 6, _ROW_Y0 - 31, clock(t), 15, WHITE, border=1))
    for i, row in enumerate(rows):
        y = _ROW_Y0 + i * _ROW_H
        chan, selected = row["chan"], (first + i == sel)
        col = rgb(chan.get("color"))
        lines.append(rect(SAFE_X - 11, y, W - SAFE_X + 11, y + _ROW_H, STRIPE_B if i % 2 else STRIPE_A))
        lines.append(rect(SAFE_X - 11, y, _GX0 - 4, y + _ROW_H, STRIPE_A if i % 2 else bgr("#000c30")))
        lines.append(text(SAFE_X, y + 11, f"{chan['dial']:>3}", 26, col, border=1, shadow=2, font="title"))
        lines.append(text(SAFE_X + 64, y + 15, short(_marked(chan, 11), 12).upper(), 15, INK_DIM if chan.get("hidden") else WHITE, border=1))
        progs = row["programs"]
        if progs is None or progs == "error":
            lines.append(text(_GX0 + 8, y + 14, "loading..." if progs is None else "unavailable", 16, INK_MID, border=1))
        else:
            for b in row.get("breaks") or []:                  # commercials and station-break slides between the shows
                bx0, bx1 = _x(max(b["start_ms"], t0_ms), t0_ms), _x(min(b["end_ms"], t0_ms + GUIDE_SPAN_MS), t0_ms)
                if bx1 - bx0 < 3:
                    continue
                ad = b.get("kind") == "ad"
                lines.append(rect(bx0 + 1, y + 4, bx1 - 1, y + _ROW_H - 4, bgr("#5a2a08") if ad else bgr("#0a1450")))
                if bx1 - bx0 >= 44:
                    lines.append(text(bx0 + 5, y + 16, "ADS" if ad else "BREAK", 13, bgr("#ffb060") if ad else INK_MID, border=0))
            for k, p in enumerate(progs):
                cs, ce = max(p["start_ms"], t0_ms), min(p["end_ms"], t0_ms + GUIDE_SPAN_MS)
                x0, x1 = _x(cs, t0_ms), _x(ce, t0_ms)
                if x1 - x0 < 4:
                    continue
                fill = (CELL_SEL_A if selected else CELL_A) if k % 2 == 0 else (CELL_SEL_B if selected else CELL_B)
                lines.append(rect(x0 + 1, y + 4, x1 - 1, y + _ROW_H - 4, fill))
                lines.append(rect(x0 + 1, y + 4, x1 - 1, y + 6, BORDER_HI))
                fit = int((x1 - x0 - 12) / cw(18))
                if fit >= 4:
                    label = ("< " if p["start_ms"] < t0_ms else "") + p["item"]["title"]
                    lines.append(text(x0 + 6, y + 13, short(label, fit), 18, WHITE, border=1))
        if selected:
            lines += _outline(SAFE_X - 11, y, W - SAFE_X + 11, y + _ROW_H, bgr("#ffee22"))
    # footer: what is selected
    cur = rows[sel - first] if 0 <= sel - first < len(rows) else None
    fy = _ROW_Y0 + GUIDE_ROWS * _ROW_H + 14
    if cur and isinstance(cur["programs"], list) and cur["programs"]:
        p = cur["programs"][0]
        lines.append(text(SAFE_X - 6, fy, short(p["item"]["title"], int((W - 2 * SAFE_X) / cw(24))) + year_suffix(p["item"]), 24, WHITE, shadow=2))
        if desc:
            lines.append(text(SAFE_X - 6, fy + 33, f"{clock(p['start_ms'])}-{clock(p['end_ms'])}  " + short(desc, int((W - 2 * SAFE_X - 150) / cw(16))),
                              16, INK, border=1))
        else:
            lines.append(text(SAFE_X - 6, fy + 33, f"{clock(p['start_ms'])} - {clock(p['end_ms'])}   "
                              f"CH {cur['chan']['dial']} {esc(cur['chan']['name'])}", 16, GOLD, border=1))
    lines += footer_bar("UP/DOWN channel   LEFT/RIGHT time   ENTER watch   G close")
    return "\n".join(lines)


# ---- small toasts (volume / mute) ----
def volume_toast(level, muted):
    """Top-right box: VOLUME with ten cells, or MUTE."""
    bw, bh = 262, 50
    x1, y0 = W - SAFE_X, SAFE_Y
    x0 = x1 - bw
    lines = panel(x0, y0, x1, y0 + bh, fill=PANEL, alpha="10")
    if muted:
        lines.append(text(x0 + 18, y0 + 14, "MUTE", 24, RED, shadow=2, font="title"))
        return "\n".join(lines)
    lines.append(text(x0 + 16, y0 + 14, "VOL", 20, GOLD, shadow=2, font="title"))
    filled = max(0, min(10, round((level or 0) / 10)))
    for i in range(10):
        cx = x0 + 78 + i * 17
        lines.append(rect(cx, y0 + 15, cx + 13, y0 + bh - 15, GOLD if i < filled else bgr("#1a2250")))
    return "\n".join(lines)


# ---- test card ----
_BARS = ["&HBFBFBF&", "&H00BFBF&", "&HBFBF00&", "&H00BF00&", "&HBF00BF&", "&H0000BF&", "&HBF0000&"]   # 75% EBU-style bars (BGR)
_REV = ["&HBF0000&", "&H101010&", "&HBF00BF&", "&H101010&", "&HBFBF00&", "&H101010&", "&HBFBFBF&"]


def test_card(title="", sub="", footer="spelunkr.tv"):
    """Colour bars with a label box: for stand-by / technical difficulties, and the `t` key."""
    lines = []
    top, mid = int(H * 0.64), int(H * 0.76)
    for i in range(7):
        x0, x1 = W * i // 7, W * (i + 1) // 7
        lines.append(rect(x0, 0, x1, top, _BARS[i]))
        lines.append(rect(x0, top, x1, mid, _REV[i]))
    quarter = W // 4
    for i, c in enumerate(("&H4C2100&", "&HFFFFFF&", "&H6A0032&", "&H090909&")):     # -I, white, +Q, black
        lines.append(rect(quarter * i, mid, quarter * (i + 1) if i < 3 else W, H, c))
    bw, bh = int(W * 0.66), 112 if sub else 84
    x0, y0 = (W - bw) // 2, int(H * 0.27)
    lines += panel(x0, y0, x0 + bw, y0 + bh, fill=PANEL, alpha="08")
    lines.append(text(W // 2, y0 + 16, esc(title), 36, YELLOW, an=8, shadow=3, font="title"))
    if sub:
        lines.append(text(W // 2, y0 + 68, esc(sub), 20, INK, an=8, border=1))
    lines.append(text(W // 2, H - SAFE_Y + 6, esc(footer), 18, YELLOW, an=2, border=2, font="title"))
    return "\n".join(lines)


# ---- diagnostics box, help screen, text toast ----
def diag_panel(lines):
    """Translucent box top-left with one line of text per entry (the first is brighter)."""
    lh, x0, y0 = 20, SAFE_X - 14, SAFE_Y - 14
    w = min(W - 2 * x0, 600)
    out = panel(x0, y0, x0 + w, y0 + 18 + lh * len(lines), fill=PANEL_DEEP, alpha="30")
    for i, ln in enumerate(lines):
        out.append(text(x0 + 12, y0 + 11 + i * lh, esc(ln), 14, GOLD if i == 0 else INK, border=1))
    return "\n".join(out)


HELP_KEYS = [
    ("UP / DOWN", "channel up / down"), ("0-9, ENTER", "jump to a channel"), ("L", "last channel"),
    ("I", "info bar; again = details"), ("E", "show details"), ("G", "TV guide"), ("N", "what is on now"),
    ("C", "captions on / off"), ("S", "settings"), ("Z", "sleep timer"),
    ("M, [ , ]", "mute, volume"), ("D", "diagnostics"), ("T", "test card"),
    ("F", "fullscreen"), ("A / O", "favourite / hide channel"), ("X", "teletext; R G Y B links"), ("? / H", "this screen"), ("Q", "quit"),
]


def help_screen(info_lines):
    out = sky() + header_bar("Spelunkr", "Help & Status")
    rows = (len(HELP_KEYS) + 1) // 2
    out += panel(SAFE_X - 14, 74, W - SAFE_X + 14, H - 50)
    col_w = (W - 2 * SAFE_X) // 2
    for i, (k, v) in enumerate(HELP_KEYS):
        x = SAFE_X + (i // rows) * col_w
        y = 88 + (i % rows) * 31
        chip, cwid = keychip(x, y, k, 14)
        out += chip
        out.append(text(x + 124, y + 5, v, 15, INK, border=1))
    y = 88 + rows * 31 + 10
    out.append(rect(SAFE_X, y, W - SAFE_X, y + 3, BORDER))
    y += 14
    for ln in info_lines:
        out.append(text(SAFE_X, y, esc(ln), 15, INK_MID, border=1))
        y += 24
    out += footer_bar("any key closes this screen")
    return "\n".join(out)


def toast_text(msg):
    """Small box top-right with a line of text (captions on/off ...)."""
    bw = max(200, 30 + int(cw(21) * len(msg)))
    x1, y0 = W - SAFE_X, SAFE_Y
    return "\n".join(panel(x1 - bw, y0, x1, y0 + 46, fill=PANEL, alpha="10") + [text(x1 - bw + 16, y0 + 13, esc(msg), 21, GOLD, shadow=2)])


# ---- "on now" list: every channel and what it is showing ----
NOW_ROWS = 9
_NOW_Y0, _NOW_RH = 106, 42


def now_list(rows, sel, first, now_ms):
    out = sky() + header_bar("Spelunkr", "On Now", clock(now_ms))
    out += _grid_panel(_NOW_Y0, NOW_ROWS, _NOW_RH)
    out.append(rect(SAFE_X - 11, _NOW_Y0 - 36, W - SAFE_X + 11, _NOW_Y0 - 6, TIME_STRIP))
    out.append(text(SAFE_X, _NOW_Y0 - 31, "CHANNEL", 15, WHITE, border=1))
    out.append(text(SAFE_X + 190, _NOW_Y0 - 31, "NOW PLAYING", 15, YELLOW, border=1))
    for i, row in enumerate(rows):
        y = _NOW_Y0 + i * _NOW_RH
        chan, selected = row["chan"], first + i == sel
        out.append(rect(SAFE_X - 11, y, W - SAFE_X + 11, y + _NOW_RH, STRIPE_B if i % 2 else STRIPE_A))
        out.append(text(SAFE_X, y + 8, f"{chan['dial']:>3}", 26, rgb(chan.get("color")), border=1, shadow=2, font="title"))
        out.append(text(SAFE_X + 64, y + 14, short(_marked(chan, 13), 14).upper(), 15, INK_DIM if chan.get("hidden") else WHITE, border=1))
        progs = row["programs"]
        x = SAFE_X + 190
        chars = max(10, int((W - SAFE_X - 150 - x) / cw(20)))
        if not isinstance(progs, list) or not progs:
            out.append(text(x, y + 12, "loading..." if progs is None else "unavailable", 16, INK_MID, border=1))
        else:
            cur = progs[0]
            out.append(text(x, y + 5, short(cur["item"]["title"] + year_suffix(cur["item"]), chars), 20, WHITE, border=1))
            out.append(text(W - SAFE_X, y + 7, f"until {clock(cur['end_ms'])}", 14, GOLD, an=9, border=1))
            if len(progs) > 1:
                out.append(text(x, y + 27, "next: " + short(progs[1]["item"]["title"], int(chars * 1.25)), 13, INK_MID, border=0))
        if selected:
            out += _outline(SAFE_X - 11, y, W - SAFE_X + 11, y + _NOW_RH, bgr("#ffee22"))
    out += footer_bar("UP/DOWN channel   ENTER watch   G guide   N close")
    return "\n".join(out)


# ---- show details ----
def details_screen(chan, item, det, runtime_s):
    """item: manifest item; det: the site's details entry (description, season, episode, director, stars) or None."""
    det = det or {}
    col = rgb(chan.get("color"))
    px0, px1 = SAFE_X - 14, W - SAFE_X + 14
    out = sky() + header_bar("Spelunkr", "Now Playing")
    out += panel(px0, 74, px1, H - 50)
    lg = logo(chan, px0 + 22, 92, 3, 14)
    tx = px0 + (22 + 14 * 3 + 16 if lg else 22)
    if lg:
        out.append(lg)
    out.append(text(tx, 92, f"CH {chan['dial']}", 22, YELLOW, border=2, shadow=2, font="title"))
    out.append(text(tx, 124, esc(chan["name"].upper()), 20, col, border=1))
    if item.get("mature"):
        out += [rect(px1 - 130, 92, px1 - 22, 116, bgr("#5a0a18")), rect(px1 - 130, 92, px1 - 22, 95, RED),
                text(px1 - 76, 97, "MATURE", 15, RED, an=8, border=0)]
    chars = max(24, int((px1 - px0 - 50) / cw(20)))
    tchars = max(16, int((px1 - px0 - 50) / cw(30)))
    title = lines_of(item["title"], tchars)[:2]
    y = 156
    out.append(multiline(px0 + 22, y, title, 30, 38, GOLD, shadow=2))
    y += 38 * len(title) + 6
    meta = []
    if item.get("year"):
        meta.append(str(item["year"]))
    if runtime_s:
        meta.append(f"{round(runtime_s / 60)} min")
    if det.get("season") or det.get("episode"):
        meta.append("S%sE%s" % (det.get("season", "?"), det.get("episode", "?")))
    if meta:
        out.append(text(px0 + 22, y, "   ".join(meta), 18, CYAN, border=1))
        y += 32
    desc = det.get("description")
    if desc:
        lines_ = lines_of(desc, chars)[:8]
        out.append(multiline(px0 + 22, y, lines_, 18, 24, INK, bold=False, border=1))
        y += 24 * len(lines_) + 12
    else:
        out.append(text(px0 + 22, y, "No description for this one.", 18, INK_MID, bold=False, border=1))
        y += 36
    for label, key in (("Director", "director"), ("Stars", "stars")):
        v = det.get(key)
        if v:
            v = ", ".join(v) if isinstance(v, list) else v
            out.append(text(px0 + 22, y, f"{{\\1c{GOLD}}}{label}: {{\\1c{INK}}}{short(v, chars - len(label) - 2)}", 16, INK, bold=False, border=1))
            y += 26
    if det.get("imdbId"):
        chip = button(px0 + 22, y + 2, px0 + 22 + 50, y + 26, active=False) + [text(px0 + 47, y + 8, "IMDb", 14, YELLOW, an=8, border=0)]
        out += chip
        out.append(text(px0 + 86, y + 6, f"imdb.com/title/{esc(det['imdbId'])}", 15, INK_MID, border=0))
    out.append(text(px0 + 22, H - 80, f"archive.org/details/{esc(item['id'])}"[:70], 14, INK_DIM, bold=False, border=0))
    out += footer_bar("any key closes")
    return "\n".join(out)


# ---- settings menu ----
def menu_screen(entries, sel):
    """entries: [(label, value)]. A centred box over the picture; the selected row has < > around its value."""
    rh, bw = 38, min(W - 2 * SAFE_X, 580)
    bh = 66 + rh * len(entries) + 46
    x0, y0 = (W - bw) // 2, max(SAFE_Y - 10, (H - bh) // 2)
    out = panel(x0, y0, x0 + bw, y0 + bh, fill=PANEL, alpha="08")
    out += button(x0 + 3, y0 + 3, x0 + bw - 3, y0 + 46)
    out.append(text(x0 + 22, y0 + 15, "SETTINGS", 24, YELLOW, border=2, shadow=2, font="title"))
    for i, (label, value) in enumerate(entries):
        y = y0 + 58 + i * rh
        out.append(rect(x0 + 6, y, x0 + bw - 6, y + rh, STRIPE_B if i % 2 else STRIPE_A))
        out.append(text(x0 + 22, y + 10, esc(label), 18, WHITE, border=1))
        v = esc(value)
        out.append(text(x0 + bw - 22, y + 10, f"<  {v}  >" if i == sel else v, 18, GOLD if i == sel else INK_MID, an=9, border=1))
        if i == sel:
            out += _outline(x0 + 6, y, x0 + bw - 6, y + rh, bgr("#ffee22"), 2)
    out.append(text(x0 + bw // 2, y0 + bh - 32, "UP/DOWN pick   LEFT/RIGHT change   S close", 14, INK_MID, an=8, bold=False, border=0))
    return "\n".join(out)


# ---- goodnight (sleep timer) ----
def goodnight_card():
    return "\n".join([rect(0, 0, W, H, BLACK),
                      text(W // 2, H // 2 - 30, "GOODNIGHT", 52, bgr("#8899cc"), an=5, border=0, font="title"),
                      text(W // 2, H // 2 + 36, "sleep timer  -  press any key to wake up", 20, INK_DIM, an=5, bold=False, border=0)])


# ---- boot splash: the pixel TV from the app icon ----
def _hex(r, g, b):
    return f"&H{b:02X}{g:02X}{r:02X}&"


_TV_COL = {"w": _hex(255, 111, 0), "d": _hex(178, 70, 0), "s": _hex(0, 200, 83), "S": _hex(105, 240, 174),
           "k": _hex(8, 40, 24), "g": _hex(200, 205, 220), "b": _hex(40, 40, 48)}


def _tv_grid():
    n = 32
    g = [["_"] * n for _ in range(n)]

    def box(x0, y0, x1, y1, c):
        for yy in range(y0, y1):
            for xx in range(x0, x1):
                g[yy][xx] = c
    for i in range(7):
        g[9 - i][16 - i - 1] = "g"
        g[9 - i][16 + i] = "g"
    box(4, 10, 28, 26, "w")
    box(4, 24, 28, 26, "d")
    box(6, 12, 22, 24, "k")
    box(7, 13, 21, 23, "s")
    for yy in range(13, 23, 2):
        box(7, yy, 21, yy + 1, "S")
    box(23, 13, 26, 16, "b")
    box(23, 18, 26, 21, "b")
    box(7, 26, 10, 28, "b")
    box(22, 26, 25, 28, "b")
    return g


def splash(status=()):
    px = 6
    x0, y0 = (W - 32 * px) // 2, int(H * 0.12)
    out = sky()
    for yy, row in enumerate(_tv_grid()):
        xx = 0
        while xx < 32:
            c = row[xx]
            if c == "_":
                xx += 1
                continue
            run = xx
            while run < 32 and row[run] == c:
                run += 1
            out.append(rect(x0 + xx * px, y0 + yy * px, x0 + run * px, y0 + (yy + 1) * px, _TV_COL[c]))
            xx = run
    ty = y0 + 32 * px + 22
    out.append(text(W // 2, ty, "SPELUNKR", 50, YELLOW, an=8, border=3, shadow=4, font="title"))
    out.append(text(W // 2, ty + 66, "cable tv, but weirder", 22, WHITE, an=8, bold=False, border=2, shadow=2))
    for i, line in enumerate(status):
        out.append(text(W // 2, ty + 116 + i * 28, esc(line), 20, GOLD, an=8, border=2, shadow=2))
    return "\n".join(out)
