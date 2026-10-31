"""On-screen graphics as ASS overlays (drawn by mpv). Virtual canvas is 768x576 (PAL 4:3).

Everything is plain text and a few rectangles, so it stays cheap on a Pi 1B.
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


def rect(x0, y0, x1, y1, color, alpha="00"):
    return (f"{{\\an7\\pos(0,0)\\bord0\\shad0\\1c{color}\\1a&H{alpha}&\\p1}}"
            f"m {x0} {y0} l {x1} {y0} {x1} {y1} {x0} {y1}{{\\p0}}")


def text(x, y, s, size, color="&HFFFFFF&", an=7, bold=True, border=2):
    return (f"{{\\an{an}\\pos({x},{y})\\fs{size}\\b{int(bold)}\\bord{border}\\shad0"
            f"\\1c{color}\\3c&H000000&}}{s}")


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


def short(s, n):
    s = esc(s)
    return s if len(s) <= n else s[: n - 1] + "…"


# ---- the cable-box bar ----
def info_bar(chan, r, upcoming, now_ms):
    """chan: dict(dial, name, color). r: now_playing() result. upcoming: [{start_ms, item}]."""
    e = r["entry"]
    color = rgb(chan.get("color"))
    top = H - SAFE_Y - 132
    x0, x1 = SAFE_X, W - SAFE_X
    lines = [rect(x0, top, x1, H - SAFE_Y, "&H201008&", alpha="30"),
             rect(x0, top, x0 + 8, H - SAFE_Y, color)]
    dial = chan.get("dial")
    lines.append(text(x0 + 22, top + 8, f"CH {dial:>3}" if dial is not None else "CH --", 40, color))
    lines.append(text(x0 + 178, top + 14, short(chan["name"].upper(), 24), 30))
    lines.append(text(x1 - 14, top + 14, clock(now_ms), 30, an=9))

    if e["type"] == "video":
        it = e["item"]
        lines.append(text(x0 + 22, top + 56, short(it["title"], 44) + year_suffix(it), 26))
        total = e["durMs"] / 1000
        frac = min(1.0, max(0.0, r["offset_s"] / total)) if total else 0
        bx0, bx1, by = x0 + 22, x1 - 14, top + 92
        lines.append(rect(bx0, by, bx1, by + 8, "&H605040&"))
        lines.append(rect(bx0, by, int(bx0 + (bx1 - bx0) * frac), by + 8, color))
        lines.append(text(bx0, by + 12, mmss(r["offset_s"]), 18, "&HC0C0C0&", border=1))
        lines.append(text(bx1, by + 12, "-" + mmss(r["remaining_s"]), 18, "&HC0C0C0&", an=9, border=1))
        if upcoming:
            n = upcoming[0]
            lines.append(text(x0 + 130, by + 12, f"NEXT {clock(n['start_ms'])}  {short(n['item']['title'], 34)}",
                              18, "&HC0C0C0&", border=1))
    elif e["type"] == "ad":
        lines.append(text(x0 + 22, top + 56, "COMMERCIAL BREAK", 26))
        if upcoming:
            n = upcoming[0]
            lines.append(text(x0 + 22, top + 94, f"BACK TO: {short(n['item']['title'], 40)}", 20, "&HC0C0C0&"))
    else:
        lines.append(text(x0 + 22, top + 56, "STATION BREAK", 26))
        if upcoming:
            n = upcoming[0]
            lines.append(text(x0 + 22, top + 94, f"NEXT {clock(n['start_ms'])}  {short(n['item']['title'], 36)}",
                              20, "&HC0C0C0&"))
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
    body = wrap(txt, 26)
    brand = text(W // 2, H - SAFE_Y - 6, "spelunkr.tv", 20, "&H808080&", an=2, border=0)
    return (text(W // 2, H // 2, body, 40, an=5) + "\n" + brand + "\n" +
            rect(W // 2 - 30, H // 2 + 90, W // 2 + 30, H // 2 + 94, c))


def _card(chan):
    c = rgb(chan.get("color"))
    dial = chan.get("dial")
    return "\n".join([
        text(W // 2, H // 2 - 40, f"CH {dial}" if dial is not None else "", 56, c, an=5),
        text(W // 2, H // 2 + 30, esc(chan["name"].upper()), 38, an=5)])


# ---- other screens ----
def up_next_card(chan, nxt):
    c = rgb(chan.get("color"))
    lines = [text(W // 2, H // 2 - 70, "UP NEXT", 36, c, an=5)]
    if nxt:
        lines.append(text(W // 2, H // 2 + 10, wrap(nxt["item"]["title"], 28), 36, an=5))
        lines.append(text(W // 2, H // 2 + 110, clock(nxt["start_ms"]), 28, "&HC0C0C0&", an=5))
    return "\n".join(lines)


def tune_card(chan, status=(), opaque=False):
    """Channel number + name top-left, status lines under it. `opaque` hides whatever frame mpv has underneath."""
    c = rgb(chan.get("color"))
    dial = chan.get("dial")
    lines = [rect(0, 0, W, H, "&H180C04&")] if opaque else []
    lines += [
        text(SAFE_X, SAFE_Y, f"CH {dial}" if dial is not None else "CH", 64, c, an=7, border=3),
        text(SAFE_X, SAFE_Y + 70, esc(chan["name"].upper()), 28, an=7)]
    for i, line in enumerate(status):
        lines.append(text(SAFE_X, SAFE_Y + 122 + i * 30, esc(line), 24 if i == 0 else 20,
                          "&HFFFFFF&" if i == 0 else "&H80C0FF&", an=7, border=2))
    return "\n".join(lines)


def stand_by(reason="", test_card_bg=True):
    """"PLEASE STAND BY" over the colour-bar test card (or plain black if test_card_bg is False)."""
    if test_card_bg:
        return test_card("PLEASE STAND BY", reason)
    lines = [text(W // 2, H // 2 - 30, "PLEASE STAND BY", 52, an=5)]
    if reason:
        lines.append(text(W // 2, H // 2 + 40, wrap(reason, 40), 24, "&HC0C0C0&", an=5))
    return "\n".join(lines)


def channel_entry(digits):
    return text(W - SAFE_X, SAFE_Y, digits or "", 64, "&H00FF00&", an=9, border=3)


# ---- TV guide (Prevue-style grid) ----
GUIDE_SPAN_MS = 2 * 3600 * 1000
GUIDE_ROWS = 8
_GX0, _GX1 = SAFE_X + 172, W - SAFE_X
_ROW_H, _ROW_Y0 = 44, 118


def _tick_times(t0_ms):
    """Half-hour marks inside the window."""
    step = 30 * 60 * 1000
    t = (t0_ms // step + 1) * step
    while t < t0_ms + GUIDE_SPAN_MS:
        yield t
        t += step


def _x(t_ms, t0_ms):
    return int(_GX0 + (t_ms - t0_ms) / GUIDE_SPAN_MS * (_GX1 - _GX0))


def guide(rows, sel, first, t0_ms, now_ms):
    """rows: [{chan, programs: list | None (loading) | 'error'}] for the visible slice, rows[sel-first] is selected.
    Returns the ASS for a full-screen opaque guide."""
    lines = [rect(0, 0, W, H, "&H180C04&"),
             text(SAFE_X, SAFE_Y - 6, "SPELUNKR TV GUIDE", 30, "&H40D0FF&"),
             text(W - SAFE_X, SAFE_Y - 6, clock(now_ms), 30, an=9)]
    lines.append(rect(_GX0, _ROW_Y0 - 30, _GX1, _ROW_Y0 - 28, "&H806040&"))
    lines.append(text(_GX0 + 2, _ROW_Y0 - 26, "NOW" if t0_ms - now_ms < 60000 else clock(t0_ms), 18, "&HC0C0C0&", border=1))
    for t in _tick_times(t0_ms):
        x = _x(t, t0_ms)
        if x - _GX0 > 70:                      # leave room for the NOW / start label
            lines.append(rect(x, _ROW_Y0 - 30, x + 2, _ROW_Y0 - 6, "&H806040&"))
            lines.append(text(x + 6, _ROW_Y0 - 26, clock(t), 18, "&HC0C0C0&", border=1))
    for i, row in enumerate(rows):
        y = _ROW_Y0 + i * _ROW_H
        chan, selected = row["chan"], (first + i == sel)
        col = rgb(chan.get("color"))
        if selected:
            lines.append(rect(SAFE_X - 4, y, _GX1, y + _ROW_H - 2, "&H603018&", alpha="60"))
        lines.append(text(SAFE_X, y + 8, f"{chan['dial']:>3}", 24, col, border=1))
        lines.append(text(SAFE_X + 50, y + 13, short(chan["name"], 12).upper(), 16, border=1))
        progs = row["programs"]
        if progs is None or progs == "error":
            lines.append(text(_GX0 + 8, y + 12, "loading..." if progs is None else "unavailable", 18,
                              "&H808080&", border=1))
            continue
        for k, p in enumerate(progs):
            cs, ce = max(p["start_ms"], t0_ms), min(p["end_ms"], t0_ms + GUIDE_SPAN_MS)
            x0, x1 = _x(cs, t0_ms), _x(ce, t0_ms)
            if x1 - x0 < 4:
                continue
            fill = ("&HA05020&" if selected else "&H703818&") if k % 2 == 0 else ("&H904818&" if selected else "&H502810&")
            lines.append(rect(x0 + 1, y + 3, x1 - 1, y + _ROW_H - 5, fill))
            fit = int((x1 - x0 - 12) / 9.4)
            if fit >= 4:
                label = ("< " if p["start_ms"] < t0_ms else "") + p["item"]["title"]
                lines.append(text(x0 + 6, y + 12, short(label, fit), 18, border=1))
    # footer: what is selected
    cur = rows[sel - first] if 0 <= sel - first < len(rows) else None
    fy = _ROW_Y0 + GUIDE_ROWS * _ROW_H + 6
    if cur and isinstance(cur["programs"], list) and cur["programs"]:
        p = cur["programs"][0]
        lines.append(text(SAFE_X, fy, short(p["item"]["title"], 48) + year_suffix(p["item"]), 22))
        lines.append(text(SAFE_X, fy + 30, f"{clock(p['start_ms'])} - {clock(p['end_ms'])}   "
                          f"CH {cur['chan']['dial']} {esc(cur['chan']['name'])}", 18, "&HC0C0C0&", border=1))
    lines.append(text(W // 2, H - SAFE_Y + 8, "UP/DOWN channel   LEFT/RIGHT time   ENTER watch   G close",
                      16, "&H909090&", an=2, border=0))
    return "\n".join(lines)


# ---- small toasts (volume / mute) ----
def volume_toast(level, muted):
    """Top-right box: VOLUME with ten cells, or MUTE."""
    bw, bh = 250, 50
    x1, y0 = W - SAFE_X, SAFE_Y
    x0 = x1 - bw
    lines = [rect(x0, y0, x1, y0 + bh, "&H201008&", alpha="30")]
    if muted:
        lines.append(text(x0 + 14, y0 + 10, "MUTE", 26, "&H4060FF&"))
        return "\n".join(lines)
    lines.append(text(x0 + 12, y0 + 12, "VOL", 22))
    filled = max(0, min(10, round((level or 0) / 10)))
    for i in range(10):
        cx = x0 + 68 + i * 17
        lines.append(rect(cx, y0 + 14, cx + 13, y0 + bh - 14, "&H40D0FF&" if i < filled else "&H605040&"))
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
    bw, bh = int(W * 0.60), 108 if sub else 78
    x0, y0 = (W - bw) // 2, int(H * 0.28)
    lines.append(rect(x0, y0, x0 + bw, y0 + bh, "&H000000&", alpha="18"))
    lines.append(text(W // 2, y0 + 12, esc(title), 40, an=8))
    if sub:
        lines.append(text(W // 2, y0 + 64, esc(sub), 22, "&HC0C0C0&", an=8, border=1))
    lines.append(text(W // 2, H - SAFE_Y + 6, esc(footer), 18, "&H00FFFF&", an=2, border=2))
    return "\n".join(lines)


# ---- diagnostics box, help screen, text toast ----
def diag_panel(lines):
    """Translucent box top-left with one line of text per entry (the first is brighter)."""
    lh, x0, y0 = 20, SAFE_X - 14, SAFE_Y - 14
    w = min(W - 2 * x0, 600)
    out = [rect(x0, y0, x0 + w, y0 + 12 + lh * len(lines), "&H000000&", alpha="40")]
    for i, ln in enumerate(lines):
        out.append(text(x0 + 10, y0 + 8 + i * lh, esc(ln), 15, "&HFFFFFF&" if i == 0 else "&HD0D0D0&", bold=False, border=1))
    return "\n".join(out)


HELP_KEYS = [
    ("UP / DOWN", "channel up / down"), ("0-9, ENTER", "jump to a channel"), ("L", "last channel"),
    ("I / ENTER", "info bar"), ("G", "TV guide"), ("C", "captions on / off"),
    ("M, [ , ]", "mute, volume"), ("D", "diagnostics"), ("T", "test card"),
    ("F", "fullscreen"), ("? / H", "this screen"), ("Q", "quit"),
]


def help_screen(info_lines):
    out = [rect(0, 0, W, H, "&H180C04&"), text(SAFE_X, SAFE_Y - 6, "SPELUNKR TV", 34, "&H40D0FF&"),
           text(W - SAFE_X, SAFE_Y - 2, "HELP & STATUS", 22, "&HC0C0C0&", an=9, border=1)]
    rows = (len(HELP_KEYS) + 1) // 2
    col_w = (W - 2 * SAFE_X) // 2
    for i, (k, v) in enumerate(HELP_KEYS):
        x = SAFE_X + (i // rows) * col_w
        y = SAFE_Y + 56 + (i % rows) * 30
        out.append(text(x, y, k, 19, "&H40D0FF&", border=1))
        out.append(text(x + 140, y, v, 19, border=1))
    y = SAFE_Y + 56 + rows * 30 + 12
    out.append(rect(SAFE_X, y, W - SAFE_X, y + 2, "&H806040&"))
    y += 14
    for ln in info_lines:
        out.append(text(SAFE_X, y, esc(ln), 18, "&HD0D0D0&", bold=False, border=1))
        y += 25
    out.append(text(W // 2, H - SAFE_Y + 14, "any key closes this screen", 16, "&H909090&", an=2, border=0))
    return "\n".join(out)


def toast_text(msg):
    """Small box top-right with a line of text (captions on/off ...)."""
    bw = max(200, 20 + 14 * len(msg))
    x1, y0 = W - SAFE_X, SAFE_Y
    return "\n".join([rect(x1 - bw, y0, x1, y0 + 46, "&H201008&", alpha="30"),
                      text(x1 - bw + 12, y0 + 11, esc(msg), 22)])
