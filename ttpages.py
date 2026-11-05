"""What is on each teletext page. build() may block (manifests, details, feeds are fetched on demand), so the TV runs it on a
worker thread; the pages marked sync (SYNC and the lists that need no data) are cheap and built on the main loop.

    100 index        101 now & next on this channel     110 on now, every channel
    200 channels     201.. one page per channel         300 listings   301.. next 12 hours of one channel
    400..409 this programme and the next ones (description, season, IMDb id, mature flag)
    600 trivia, music credits and station-break lines    700 news from RSS/Atom feeds (config "feeds")
    800 status       801 this device                    900 keys
"""
import datetime
import time
from concurrent.futures import ThreadPoolExecutor

import feeds as feedmod
import osd
import search as searchmod
import teletext as T
from teletext import Page, accent, clean, dots, fit, paginate, progress, wrap

LISTING_HOURS = 12
SYNC = {100, 200, 300, 500, 501, 503, 700, 800, 801, 900}
RESULT_PAGES = 20
LINKS = {"r": (100, "INDEX"), "g": (101, "NOW"), "y": (300, "LISTINGS"), "c": (700, "NEWS")}
MAX_FEEDS = 9
MAX_SUBS = 8                 # long lists (trivia, music) stop after this many sub-pages


def hhmm(ms):
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%H:%M")


def age(ts, now_s):
    if not ts:
        return ""
    d = max(0, now_s - ts)
    return f"{int(d // 60)}m ago" if d < 3600 else f"{int(d // 3600)}h ago" if d < 86400 else f"{int(d // 86400)}d ago"


def runtime(ms):
    m = round(ms / 60000)
    return f"{m} min" if m < 120 else f"{m // 60}h{m % 60:02d}"


class Pages:
    def __init__(self, tv):
        self.tv = tv

    # ---- structure ----
    def feed_list(self):
        return [f for f in (self.tv.cfg.get("feeds") or []) if isinstance(f, dict) and f.get("url")][:MAX_FEEDS]

    def numbers(self):
        """Pages that UP / DOWN step through (stories are reached from their headline page)."""
        n = len(self.tv.chans)
        nums = [100, 101, 110, 200] + [200 + i + 1 for i in range(n)] + [300] + [300 + i + 1 for i in range(n)]
        nums += list(range(400, 410)) + [500, 501, 502, 503, 600, 601, 602, 603, 700] + [700 + f + 1 for f in range(len(self.feed_list()))]
        return nums + [800, 801, 900]

    def valid(self, number):
        if number in self.numbers():
            return True
        if 510 <= number < 510 + RESULT_PAGES:
            return number - 510 < len(self.tv.search["results"])
        f, s = divmod(number - 700, 10)
        return 10 <= number - 700 < 100 and 1 <= f <= len(self.feed_list()) and 0 <= s <= 9

    def is_sync(self, number):
        return number in SYNC

    # ---- dispatch ----
    def build(self, number):
        try:
            return self._build(number)
        except Exception as ex:
            return Page.message(number, "PAGE UNAVAILABLE", "{r}Could not build this page:", "", *wrap(f"{type(ex).__name__}: {ex}"),
                                "", "{c}Try again in a moment.", links=LINKS)

    def _build(self, n):
        if n == 100:
            return self.index()
        if n == 101:
            return self.now_next()
        if n == 110:
            return self.on_now()
        if n == 200 or n == 300:
            return self.channel_index(n)
        if 201 <= n <= 299 and n - 200 <= len(self.tv.chans):
            return self.channel_page(n - 201)
        if 301 <= n <= 399 and n - 300 <= len(self.tv.chans):
            return self.listing(n - 301)
        if 400 <= n <= 409:
            return self.programme(n)
        if n == 500:
            return self.search_index_page()
        if n == 501:
            return self.search_entry()
        if n == 502:
            return self.search_results()
        if n == 503:
            return self.reminders()
        if 510 <= n < 510 + RESULT_PAGES:
            return self.search_hit(n)
        if 600 <= n <= 603:
            return self.extras(n)
        if n == 700:
            return self.feed_index()
        if 701 <= n <= 709 and n - 700 <= len(self.feed_list()):
            return self.headlines(n - 700)
        if n in (800, 801):
            return self.status(n)
        if n == 900:
            return self.keys()
        if 710 <= n <= 799 and self.valid(n):
            return self.story(*divmod(n - 700, 10))
        return Page.message(n, "PAGE NOT FOUND", "{r}There is no page %d." % n, "", "{w}Try 100 for the index.", links=LINKS)

    # ---- helpers ----
    def now_ms(self):
        return self.tv.src.now_ms()

    def shows(self, chan, hours=LISTING_HOURS):
        t = self.now_ms()
        return self.tv.src.timeline(chan["id"], t, t + int(hours * 3600 * 1000))

    def title_of(self, p):
        it = p["item"]
        show = it.get("show")
        return f"{show}: {it['title']}" if show and show not in it["title"] else it["title"]

    # ---- 100 ----
    def index(self):
        rows = ["{dh}{y}Welcome to SPELUNKR TEXT", "", "{c}Lost TV, found again.  Press a number.", ""]
        for label, num in [("Now & next on this channel", 101), ("On now: every channel", 110), ("Channels", 200),
                           ("Listings: the next %d hours" % LISTING_HOURS, 300), ("This programme", 400),
                           ("Search and reminders", 500), ("Trivia, music credits", 600), ("News headlines", 700),
                           ("Status", 800), ("Keys and help", 900)]:
            rows.append(dots(label, str(num)))
        rows += ["", "{w}Colour keys R G Y B follow the links.", "{w}UP/DOWN next page  LEFT/RIGHT sub-page",
                 "{w}X or BACK returns to the picture."]
        return Page(100, "INDEX", [rows], links=LINKS, rotate_s=0)

    # ---- 101 ----
    def now_next(self):
        tv, t = self.tv, self.now_ms()
        ch = tv.chan
        progs = self.shows(ch, 8)
        cur = next((p for p in progs if p["start_ms"] <= t < p["end_ms"]), None)
        up = [p for p in progs if p["start_ms"] > t]
        rows = [accent(ch) + f"CH {ch['dial']}  {clean(ch['name'])}", ""]
        if cur:
            rows.append("{y}NOW")
            rows += [f"{{w}}{ln}" for ln in wrap(self.title_of(cur), 38)[:3]]
            meta = [str(cur["item"]["year"])] if cur["item"].get("year") else []
            meta.append(runtime(cur["end_ms"] - cur["start_ms"]))
            rows.append("{c}" + "  ".join(meta) + f"   {hhmm(cur['start_ms'])}-{hhmm(cur['end_ms'])}")
            frac = (t - cur["start_ms"]) / max(1, cur["end_ms"] - cur["start_ms"])
            left = max(0, (cur["end_ms"] - t) // 60000)
            rows.append(progress(frac, 26) + f"{{w}} {left} min left")
        else:
            rows += ["{y}NOW", "{w}Station break", "{c}the show starts again shortly"]
        rows += ["", "{y}NEXT"]
        for p in up[:6]:
            ls = wrap(self.title_of(p), 32)
            rows.append(f"{{c}}{hhmm(p['start_ms'])} {{w}}{ls[0]}")
            rows += [f"      {{w}}{ln}" for ln in ls[1:2]]
        links = dict(LINKS, g=(400, "DETAILS"), y=(300 + tv.idx + 1, "LISTINGS"))
        return Page(101, f"NOW & NEXT  CH {ch['dial']}", paginate(rows), links=links, tune=ch["id"], rotate_s=0)

    # ---- 110 ----
    def on_now(self):
        t, chans = self.now_ms(), self.tv.chans

        def one(ch):
            try:
                progs = self.tv.src.timeline(ch["id"], t, t + 2 * 3600 * 1000)
            except Exception:
                return None
            return next((p for p in progs if p["start_ms"] <= t < p["end_ms"]), "break")

        with ThreadPoolExecutor(4) as ex:
            cur = list(ex.map(one, chans))
        rows = []
        for ch, p in zip(chans, cur):
            if p is None:
                what = "{r}unavailable"
            elif p == "break":
                what = "{c}station break"
            else:
                what = "{w}" + fit(self.title_of(p), 23)
            rows.append(f"{accent(ch)}{ch['dial']:>3} {{w}}{fit(ch['name'], 11):<11} {what}")
        return Page(110, "ON NOW  ALL CHANNELS", paginate(rows), links=dict(LINKS, g=(200, "CHANNELS")), rotate_s=10)

    # ---- 200 / 300 ----
    def channel_index(self, base):
        chans = self.tv.chans
        favs = getattr(self.tv, "favs", ())
        rows = []
        for i, c in enumerate(chans):
            star, name, num = "★" if c["id"] in favs else " ", fit(c["name"], 27), str(base + i + 1)
            fill = "." * max(1, T.COLS - 5 - len(name) - len(num) - 2)
            rows.append(f"{accent(c)}{c['dial']:>3}{{y}}{star}{{w}}{name} {{b}}{fill} {{y}}{num}")
        title = "CHANNELS" if base == 200 else "LISTINGS  CHOOSE A CHANNEL"
        return Page(base, title, paginate(rows), links=LINKS, rotate_s=10)

    def channel_page(self, i):
        ch = self.tv.chans[i]
        st = ch.get("stats") or {}
        right = [accent(ch) + f"CHANNEL {ch['dial']}", "{w}" + fit(ch["name"], 22), "", "{c}%s programmes" % st.get("items", "?"),
                 "{c}%d hours of video" % round((st.get("videoSeconds") or 0) / 3600), "", "{w}" + fit(ch.get("family") or "", 22)]
        rows = [lg + "  " + (right[k] if k < len(right) else "") for k, lg in enumerate(T.logo_rows(ch))]
        rows.append("")
        desc = wrap(ch.get("description") or "", 38)
        rows += [f"{{w}}{ln}" for ln in desc[:5]]
        if len(desc) > 5:
            rows[-1] = rows[-1] + "{c}~"
        rows.append("")
        try:
            t = self.now_ms()
            progs = self.shows(ch, 4)
            cur = next((p for p in progs if p["start_ms"] <= t < p["end_ms"]), None)
            rows.append("{y}ON NOW  {w}" + (fit(self.title_of(cur), 29) if cur else "station break"))
        except Exception:
            rows.append("{y}ON NOW  {r}unavailable")
        rows.append("{g}ENTER tunes to this channel")
        links = dict(LINKS, g=(300 + i + 1, "SCHEDULE"), c=(200, "CHANNELS"))
        return Page(201 + i, f"CH {ch['dial']}  {clean(ch['name'])}".upper(), paginate(rows), links=links, tune=ch["id"], rotate_s=0)

    # ---- 300 ----
    def listing(self, i):
        ch = self.tv.chans[i]
        t = self.now_ms()
        progs = self.shows(ch, LISTING_HOURS)
        rows, prev_end = [], None
        for p in progs:
            if prev_end is not None and p["start_ms"] - prev_end >= 90_000:       # commercials; the short slides are not listed
                rows.append(f"{{m}}      -- break {round((p['start_ms'] - prev_end) / 60000)} min --")
            prev_end = p["end_ms"]
            ls = wrap(self.title_of(p) + (f" ({p['item']['year']})" if p["item"].get("year") and str(p["item"]["year"]) not in p["item"]["title"] else ""), 33)
            on = p["start_ms"] <= t
            rows.append(f"{'{g}' if on else '{c}'}{hhmm(p['start_ms'])} {{w}}{ls[0]}")
            rows += [f"      {{w}}{ln}" for ln in ls[1:]]
        if not rows:
            rows = ["{w}No programme data for this channel."]
        links = dict(LINKS, g=(200 + i + 1, "CHANNEL"), c=(200, "CHANNELS"))
        return Page(301 + i, f"CH {ch['dial']}  {clean(ch['name'])}".upper(), paginate(rows), links=links, tune=ch["id"], rotate_s=12)

    # ---- 400.. ----
    def programme(self, n):
        tv, t = self.tv, self.now_ms()
        ch = tv.chan
        coming = [p for p in self.shows(ch, 24) if p["end_ms"] > t]
        k = n - 400
        links = dict(LINKS, g=(101, "NOW"), c=(min(409, n + 1), "NEXT"))
        if k >= len(coming):
            return Page.message(n, "NO SUCH PROGRAMME", "{w}Nothing more is scheduled on", "{w}this channel in the next day.", links=links)
        p = coming[k]
        it = p["item"]
        try:
            det = tv.src.details(ch["id"], p["version"], p["videoIndex"]) or {}
        except Exception:
            det = {}
        on = p["start_ms"] <= t
        rows = [accent(ch) + f"CH {ch['dial']}  {clean(ch['name'])}", "{c}" + f"{hhmm(p['start_ms'])}-{hhmm(p['end_ms'])}" +
                ("  ON NOW" if on else ""), ""]
        if it.get("show") and it["show"] not in it["title"]:
            rows.append("{c}" + fit(it["show"], 38))
        rows += [f"{{y}}{ln}" for ln in wrap(it["title"], 38)]
        meta = []
        if it.get("year"):
            meta.append(str(it["year"]))
        meta.append(runtime(p["end_ms"] - p["start_ms"]))
        if det.get("season") or det.get("episode"):
            meta.append("S%sE%s" % (det.get("season", "?"), det.get("episode", "?")))
        rows += ["{c}" + "  ".join(meta), ""]
        if it.get("mature"):
            rows += ["{R}{w} MATURE CONTENT {K}", ""]
        desc = det.get("description")
        rows += [f"{{w}}{ln}" for ln in wrap(desc, 38)] if desc else ["{w}No description for this one."]
        for label, key in (("Director", "director"), ("Stars", "stars")):
            v = det.get(key)
            if v:
                rows += ["", f"{{c}}{label}: {{w}}" + fit(", ".join(v) if isinstance(v, list) else v, 38 - len(label) - 2)]
        if det.get("imdbId"):
            rows += ["", "{c}imdb.com/title/" + clean(det["imdbId"])]
        rows.append("{m}archive.org/details/" + clean(it["id"]))
        actions = {} if on else {"y": self.remind_action(ch, p)}
        return Page(n, "NOW PLAYING" if on else "UP NEXT", paginate(rows), links=links, tune=ch["id"], rotate_s=12, actions=actions)

    # ---- 600.. ----
    def _bundle(self):
        tv = self.tv
        b = tv.bundle or tv.src.bundle(tv.chan["id"])
        ch, manifests, platforms = b
        v = (tv.cur or {}).get("version")
        if v not in manifests:
            v = max(manifests)
        return ch, manifests[v], platforms.get(v) or {}

    def extras(self, n):
        ch, man, plat = self._bundle()
        links = dict(LINKS, g=(600, "EXTRAS"))
        if n == 600:
            rows = [accent(ch) + f"CH {ch['dial']}  {clean(ch['name'])}", "", dots("Channel trivia", "601"), dots("Music credits", "602"),
                    dots("Station-break lines", "603"), "", "{w}What the station-break slides say, and who", "{w}made the music between the shows."]
            return Page(600, "EXTRAS", [rows], links=links, rotate_s=0)
        if n == 601:
            rows = []
            for b in man.get("bumps") or []:
                text = " ".join(s.get("text", "") for s in b.get("slides") or [] if s.get("text"))
                if text:
                    rows += [("{y}* " if i == 0 else "  ") + "{w}" + ln for i, ln in enumerate(wrap(text, 36))]
            return Page(601, f"TRIVIA  {clean(ch['name'])}".upper(), paginate(rows or ["{w}No trivia on this channel."])[:MAX_SUBS], links=links, rotate_s=12)
        if n == 602:
            seen, rows = set(), []
            for a in (man.get("audio") or []) + (plat.get("audio") or []):
                key = (a.get("artist"), a.get("title"))
                if key in seen or not a.get("title"):
                    continue
                seen.add(key)
                rows.append("{y}" + fit(a.get("artist") or "unknown", 38))
                rows.append("{w}  " + fit(a["title"], 36))
                lic = ("licence: " + a["license"] + "  ") if a.get("license") else ""
                rows.append("{c}  " + fit(lic + (a.get("url") or "").replace("https://", ""), 36))
            return Page(602, "MUSIC CREDITS", paginate(rows or ["{w}No music credits listed."])[:MAX_SUBS], links=links, rotate_s=12)
        rows = []
        for b in plat.get("bumps") or []:
            text = " / ".join(s.get("text", "") for s in b.get("slides") or [] if s.get("text") and s["text"] != "spelunkr.tv")
            if text:
                rows += [("{y}* " if i == 0 else "  ") + "{w}" + ln for i, ln in enumerate(wrap(text, 36))]
        return Page(603, "STATION-BREAK LINES", paginate(rows or ["{w}Nothing here."])[:MAX_SUBS], links=links, rotate_s=12)

    # ---- 700.. news ----
    def feed_index(self):
        fl = self.feed_list()
        if not fl:
            rows = ["{y}No news feeds set up yet.", "", "{w}Add RSS or Atom feeds to config.json:", "",
                    '{c}"feeds": [', '{c}  {"name": "BBC News",', '{c}   "url": "https://feeds.bbci.co.uk/',
                    '{c}          news/rss.xml"}', "{c}]", "", "{w}Up to %d feeds. They show up here." % MAX_FEEDS]
        else:
            rows = [dots(clean(f.get("name") or f["url"]), str(701 + i), width=T.COLS) for i, f in enumerate(fl)]
            rows += ["", "{w}Headlines refresh every 15 minutes."]
        return Page(700, "NEWS", [rows], links=LINKS, rotate_s=0)

    def _entries(self, f):
        return feedmod.fetch(f["url"], self.tv.cfg["cache_dir"], max_age=self.tv.cfg.get("feed_refresh_s", 900))

    def headlines(self, k):
        f = self.feed_list()[k - 1]
        entries = self._entries(f)[:10]
        now_s = time.time()
        rows = []
        for s, e in enumerate(entries):
            ls = wrap(e["title"], 34)
            rows.append(f"{{y}}7{k}{s} {{w}}{ls[0]}")
            rows += [f"    {{w}}{ln}" for ln in ls[1:2]]
            a = age(e["published"], now_s)
            if a:
                rows.append(f"    {{c}}{a}")
        title = clean(f.get("name") or "NEWS").upper()[:30]
        return Page(700 + k, title, paginate(rows or ["{w}This feed has no stories."]),
                    links=dict(LINKS, g=(700, "FEEDS")), rotate_s=15)

    def story(self, k, s):
        f = self.feed_list()[k - 1]
        entries = self._entries(f)
        if s >= len(entries):
            return Page.message(700 + k * 10 + s, "NO SUCH STORY", "{w}That story is no longer in the feed.",
                                links=dict(LINKS, g=(700 + k, "HEADLINES")))
        e = entries[s]
        rows = [f"{{y}}{ln}" for ln in wrap(e["title"], 38)]
        a = age(e["published"], time.time())
        if a:
            rows.append("{c}" + a)
        rows.append("")
        rows += [f"{{w}}{ln}" for ln in wrap(e["summary"], 38)] if e["summary"] else ["{w}(no text in the feed)"]
        if e["link"]:
            rows += [""] + [f"{{c}}{ln}" for ln in wrap(e["link"].replace("https://", "").replace("http://", ""), 38)]
        return Page(700 + k * 10 + s, clean(f.get("name") or "NEWS").upper()[:30], paginate(rows),
                    links=dict(LINKS, g=(700 + k, "HEADLINES")), rotate_s=15)

    # ---- 800.. ----
    def status(self, n):
        tv = self.tv
        if n == 800:
            rows = [f"{{w}}{ln}" for line in tv.diag_lines() for ln in wrap(line, 38)[:2]]
            rows += ["", "{c}Refreshes every 2 seconds."]
            return Page(800, "STATUS", paginate(rows), links=LINKS, rotate_s=0, refresh_s=2)
        rows = [f"{{w}}{ln}" for line in tv.help_lines() for ln in wrap(line, 38)[:2]]
        return Page(801, "THIS DEVICE", paginate(rows), links=LINKS, rotate_s=0)

    def keys(self):
        rows = []
        for k, v in osd.HELP_KEYS:
            rows.append(f"{{y}}{k:<12}{{w}}{fit(v, 27)}")
        return Page(900, "KEYS", paginate(rows), links=LINKS, rotate_s=10)


    # ---- 500.. search and reminders ----
    def remind_action(self, chan, prog):
        """The yellow key on a programme page: set (or remove) a reminder for that showing."""
        have = self.tv.has_reminder(chan["id"], prog["start_ms"])
        return ("remind", "NO REMIND" if have else "REMIND",
                {"cid": chan["id"], "dial": chan["dial"], "name": chan["name"], "start_ms": prog["start_ms"],
                 "end_ms": prog["end_ms"], "title": self.title_of(prog)})

    def search_index_page(self):
        s = self.tv.search
        rows = [dots("Search by title or words", "501"), dots("Results of the last search", "502"), dots("Your reminders", "503"), "",
                "{w}Search looks through every channel's", "{w}titles, series and descriptions, then says",
                "{w}when each show is on in the next day.", "",
                "{c}Type on a keyboard, pick letters with the", "{c}arrow keys and OK, or use the search box in", "{c}the phone remote."]
        if s["query"]:
            rows += ["", "{y}Last search: {w}" + fit(s["query"], 24)]
        return Page(500, "SEARCH AND REMINDERS", [rows], links=dict(LINKS, g=(501, "SEARCH"), c=(503, "REMINDERS")), rotate_s=0)

    def search_entry(self):
        s = self.tv.search
        rows = ["{c}Type a title, a series or a few words.", "", "{y}Search: {w}" + clean(s["query"]) + "{W} {K}", ""]
        cur = s["cur"]
        for r, keys in enumerate(self.tv.SEARCH_KEYS):
            row = "    "
            for c, (label, _) in enumerate(keys):
                row += ("{W}{k}" + f" {label} " + "{K}{w}") if [r, c] == cur else ("{w}" + f" {label} ")
            rows.append(row)
            rows.append("")
        rows += ["{w}Arrows move, OK picks a key.", "{w}On a keyboard just type, ENTER searches.", "",
                 "{c}Red delete  Green search  Yellow clear"]
        actions = {"r": ("kbd_delete", "DELETE", None), "g": ("kbd_go", "SEARCH", None), "y": ("kbd_clear", "CLEAR", None)}
        return Page(501, "SEARCH", [rows], links=dict(LINKS, c=(502, "RESULTS")), rotate_s=0, actions=actions)

    @staticmethod
    def when(hit, now):
        if not hit.get("airs"):
            return "{r}not within 24h"
        s, e = hit["airs"][0]
        if s <= now < e:
            return "{g}ON NOW"
        day = datetime.datetime.fromtimestamp(s / 1000)
        today = datetime.datetime.fromtimestamp(now / 1000).date()
        return "{y}" + (day.strftime("%H:%M") if day.date() == today else day.strftime("%a %H:%M"))

    def search_results(self):
        tv, s = self.tv, self.tv.search
        q = s["query"]
        if not q:
            return Page.message(502, "SEARCH RESULTS", "{w}You have not searched for anything yet.", "", "{c}Press the green key for the search page.",
                                links=dict(LINKS, g=(501, "SEARCH")))
        idx = tv.search_index()
        idx.ensure()
        now = self.now_ms()
        hits = searchmod.airings(tv.src, idx.query(q, limit=RESULT_PAGES), now)
        s["results"] = hits
        rows = [f"{{c}}{len(hits)}{'+' if len(hits) >= RESULT_PAGES else ''} found for {{w}}{fit(q, 22)}", ""]
        for k, h in enumerate(hits):
            title = h["title"] + (f" ({h['year']})" if h.get("year") and str(h["year"]) not in h["title"] else "")
            rows.append(f"{{y}}{510 + k} {{w}}{fit(title, 34)}")
            rows.append(f"    {{c}}CH {h['dial']} {fit(h['cname'], 11):<11} {self.when(h, now)}")
        if not hits:
            rows += ["{w}Nothing matches. Try fewer or shorter", "{w}words, or part of a title."]
        if idx.failed:
            rows += ["", f"{{m}}{idx.total - idx.failed} of {idx.total} channels searched (others offline)"]
        elif idx.no_details:
            rows += ["", f"{{m}}Descriptions missing for {idx.no_details} of {idx.total} channels", "{m}(offline): titles still match."]
        return Page(502, "SEARCH RESULTS", paginate(rows), links=dict(LINKS, g=(501, "SEARCH"), c=(503, "REMINDERS")), rotate_s=15)

    def search_hit(self, n):
        s = self.tv.search
        k = n - 510
        links = dict(LINKS, g=(502, "RESULTS"), c=(501, "SEARCH"))
        if k >= len(s["results"]):
            return Page.message(n, "NO SUCH RESULT", "{w}Search again from page 501.", links=links)
        h, now = s["results"][k], self.now_ms()
        rows = [f"{{y}}{ln}" for ln in wrap(h["title"], 38)]
        meta = [str(h["year"])] if h.get("year") else []
        if h.get("show") and h["show"] not in h["title"]:
            meta.append(h["show"])
        rows.append("{c}" + fit("  ".join(meta), 38) if meta else "")
        rows += [f"{{w}}CH {h['dial']}  {fit(h['cname'], 28)}", ""]
        if h.get("mature"):
            rows += ["{R}{w} MATURE CONTENT {K}", ""]
        rows.append(self.when(h, now).replace("not within 24h", "not on in the next 24 hours"))
        for st, en in (h.get("airs") or [])[1:4]:
            rows.append("{y}" + datetime.datetime.fromtimestamp(st / 1000).strftime("%a %H:%M") + f" {{w}}for {runtime(en - st)}")
        rows.append("")
        rows += [f"{{w}}{ln}" for ln in wrap(h["desc"], 38)[:9]] if h.get("desc") else ["{w}No description for this one."]
        if h.get("imdb"):
            rows += ["", "{c}imdb.com/title/" + clean(h["imdb"])]
        actions = {}
        nxt = next(((st, en) for st, en in (h.get("airs") or []) if st > now), None)
        if nxt:
            actions["y"] = self.remind_action({"id": h["cid"], "dial": h["dial"], "name": h["cname"]},
                                              {"start_ms": nxt[0], "end_ms": nxt[1], "item": {"title": h["title"]}})
        return Page(n, f"RESULT {k + 1}", paginate(rows), links=links, tune=h["cid"], rotate_s=12, actions=actions)

    def reminders(self):
        now = self.now_ms()
        rems = sorted(self.tv.reminders, key=lambda r: r["start_ms"])
        links = dict(LINKS, g=(501, "SEARCH"), c=(502, "RESULTS"))
        if not rems:
            return Page.message(503, "REMINDERS", "{w}No reminders set.", "",
                                "{c}On a programme page (400-409) or a", "{c}search result, press the yellow key.", links=links)
        rows = []
        for r in rems:
            d = datetime.datetime.fromtimestamp(r["start_ms"] / 1000)
            when = d.strftime("%H:%M") if abs(r["start_ms"] - now) < 12 * 3600 * 1000 else d.strftime("%a %H:%M")
            rows.append(f"{{y}}{when} {{w}}{fit(r['title'], 31)}")
            rows.append(f"      {{c}}CH {r['dial']} {fit(r['name'], 24)}")
        rows += ["", "{w}You get a banner two minutes before."]
        if self.tv.cfg.get("reminder_autotune"):
            rows.append("{w}The TV tunes itself when the show starts.")
        return Page(503, "REMINDERS", paginate(rows), links=links, rotate_s=12, actions={"r": ("clear_reminders", "CLEAR ALL", None)})
