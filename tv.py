#!/usr/bin/env python3
"""spelunkr.tv client: tunes to a channel, works out what is on now and plays it with mpv.

    python3 tv.py                    # start on the configured channel
    python3 tv.py --channel 203      # by dial number or id (e.g. ray-gun)
    python3 tv.py --list             # list channels
    python3 tv.py --profile pi

Keys: up/down or +/- change channel, digits + Enter jump to a dial number, l = last channel,
i = info bar, g = guide, f = fullscreen (also double click), q = quit. Works from the terminal, and in mpv's own window.
"""
import argparse
import json
import os
import queue
import sys
import threading
import time
from urllib.parse import quote

import inputs
import osd
import schedule as S
from mpvctl import Mpv

HERE = os.path.dirname(os.path.abspath(__file__))
OVL_BAR, OVL_SCREEN, OVL_DIGITS, OVL_GUIDE, OVL_TOAST = 1, 2, 3, 4, 5


def load_config(path, profile=None):
    with open(path) as f:
        cfg = json.load(f)
    if profile:
        cfg["profile"] = profile
    cfg["cache_dir"] = os.path.expanduser(cfg["cache_dir"])
    cfg["prof"] = cfg["profiles"][cfg["profile"]]
    return cfg


class Store:
    """Small JSON file with what to remember between runs (last channel, volume, window size)."""

    def __init__(self, cache_dir):
        self.path = os.path.join(cache_dir, "state.json")

    def load(self):
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def save(self, **kw):
        d = self.load()
        d.update(kw)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(d, f)
            os.replace(tmp, self.path)
        except OSError:
            pass


class Source:
    """Lineup, manifests and platform files, cached on disk and refreshed politely."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.cache = cfg["cache_dir"]
        self.lineup = None
        self.skew_ms = 0
        self._bundles = {}
        self._gbundles = {}
        self._checked = 0
        self.refresh(force=True)

    def due(self):
        return time.time() - self._checked >= self.cfg["lineup_refresh_s"]

    def refresh(self, force=False):
        if not force and time.time() - self._checked < self.cfg["lineup_refresh_s"]:
            return
        self._checked = time.time()
        data, date = S.fetch("lineup.json", self.cache, max_age=0 if force else self.cfg["lineup_refresh_s"])
        self.lineup = data
        if date:
            from email.utils import parsedate_to_datetime
            skew = parsedate_to_datetime(date).timestamp() * 1000 - time.time() * 1000
            self.skew_ms = skew if abs(skew) > 2000 else 0   # same rule as the site

    def now_ms(self):
        return int(time.time() * 1000 + self.skew_ms + float(os.environ.get("TV_TIME_OFFSET_S", 0)) * 1000)

    def channels(self):
        chs = [c for c in self.lineup["channels"]
               if not c.get("debugOnly") and not c.get("comingSoon") and c.get("dial") is not None]
        return sorted(chs, key=lambda c: c["dial"])

    def timeline(self, channel_id, t0, t1):
        """Shows for the guide. Uses its own small cache so the playing channel's bundle is not evicted."""
        ch = next(c for c in self.lineup["channels"] if c["id"] == channel_id)
        key = tuple(v["v"] for v in ch["versions"])
        b = self._gbundles.get(channel_id)
        if not b or b[0] != key:
            manifests = {v["v"]: S.fetch(v["manifest"], self.cache)[0] for v in ch["versions"]}
            platforms = {v: S.fetch(m["platform"], self.cache)[0] for v, m in manifests.items()}
            b = (key, ch, manifests, platforms)
            self._gbundles[channel_id] = b
        return S.timeline(*b[1:], t0, t1)

    def bundle(self, channel_id):
        """(channel, manifests, platforms) for the channel's current lineup versions."""
        ch = next(c for c in self.lineup["channels"] if c["id"] == channel_id)
        key = tuple(v["v"] for v in ch["versions"])
        b = self._bundles.get(channel_id)
        if b and b[0] == key:
            return b[1:]
        manifests = {v["v"]: S.fetch(v["manifest"], self.cache)[0] for v in ch["versions"]}
        platforms = {v: S.fetch(m["platform"], self.cache)[0] for v, m in manifests.items()}
        self._bundles = {channel_id: (key, ch, manifests, platforms)}   # keep only the active channel in RAM
        return ch, manifests, platforms


class TV:
    def __init__(self, cfg, source, start):
        self.cfg, self.src = cfg, source
        self.chans = source.channels()
        self.idx = start
        self.last_idx = start
        self.q = queue.Queue()
        self.mpv = None
        self.bundle = None
        self.tuning_until = 0
        self.need_entry = True
        self.cur = None          # current now_playing result
        self.key = None
        self.media_base = 0.0    # media timestamp that corresponds to the entry's offset 0
        self.bar_until = 0
        self.bar_second = None
        self.upnext_cache = (None, [])
        self.screen_key = None
        self.digits, self.digits_at = "", 0
        self.fails, self.retry_at, self.last_drift = 0, 0, 0
        self.error = ""
        self.running = True
        self.guide = None          # {sel, off_ms, rows: {chan_id: programs}, t0}
        self.guide_jobs = queue.Queue()
        self.gen = 0               # bumped on every tune; late results for an older channel are dropped
        self.loading = None        # {stage: schedule|connect|buffer, since} while something is being waited for
        self.load_tick = None
        self.expect_url = None
        self.bundle_busy = False
        self.refresh_busy = False
        self.win = {}              # osd-width / osd-height reported by mpv
        self.extra_inputs = []     # e.g. HDMI-CEC; each has start(put) and stop()
        self.store = Store(cfg["cache_dir"])
        self.snapshot = {}         # read by the web remote and the menu bar; replaced as a whole once a second
        self.snap_at = 0
        self.volume, self.muted = 100, False
        self.toast_until = 0
        self.wheel_at = 0

    # ---- lifecycle ----
    def run(self):
        bindings = dict(inputs.KEY_BINDINGS)
        self.mpv = Mpv(self.cfg["prof"]["mpv"], bindings)
        threading.Thread(target=self._pump_mpv, daemon=True).start()
        threading.Thread(target=self._guide_worker, daemon=True).start()
        for i, prop in enumerate(("osd-width", "osd-height"), 1):
            self.mpv.command("observe_property", i, prop)      # overlays follow the window / display shape
        st = self.store.load()
        if self.cfg.get("remember", True):
            if isinstance(st.get("volume"), (int, float)):
                self.volume = st["volume"]          # applied through mpv's start-up options (see main)
            self.muted = bool(st.get("muted"))
        kb = inputs.KeyboardInput()
        kb.start(lambda c: self.q.put(("cmd", c)))
        for extra in self.extra_inputs:
            extra.start(lambda c: self.q.put(("cmd", c)))
        self.tune(self.idx)
        shot = os.environ.get("TV_DEBUG_SHOT")      # "path:seconds[,path:seconds...]" for screenshots while developing
        if shot:
            threading.Thread(target=self._debug_shots, args=(shot,), daemon=True).start()
        cmds = os.environ.get("TV_DEBUG_CMDS")      # "secs:command,..." e.g. "6:up,12:digit:2"
        if cmds:
            threading.Thread(target=self._debug_cmds, args=(cmds,), daemon=True).start()
        try:
            while self.running:
                try:
                    kind, val = self.q.get(timeout=0.25)
                    self.handle(kind, val)
                except queue.Empty:
                    pass
                self.tick()
        finally:
            kb.stop()
            for extra in self.extra_inputs:
                extra.stop()
            self.save_window()
            self.mpv.quit()

    def _debug_shots(self, spec):
        t0 = time.time()
        for part in spec.split(","):
            path, secs = part.rsplit(":", 1)
            time.sleep(max(0, float(secs) - (time.time() - t0)))
            self.mpv.request("screenshot-to-file", path, "window")
            print(f"[debug {secs}s] entry={self.cur and self.cur['entry']['type']} path={self.mpv.get('path')} "
                  f"title={self.mpv.get('media-title')!r} vol={self.mpv.get('volume')} mute={self.mpv.get('mute')} time-pos={self.mpv.get('time-pos')} idle={self.mpv.get('idle-active')} err={self.error!r}",
                  file=sys.stderr)
        time.sleep(0.5)
        self.q.put(("exit", None))

    def _debug_cmds(self, spec):
        t0 = time.time()
        for part in spec.split(","):
            secs, cmd = part.split(":", 1)
            time.sleep(max(0, float(secs) - (time.time() - t0)))
            self.q.put(("cmd", cmd))

    def _pump_mpv(self):
        while True:
            ev = self.mpv.events.get()
            if ev["event"] == "mpv-exited":
                self.q.put(("exit", None))
                return
            self.q.put(("mpv", ev))

    # ---- events ----
    def handle(self, kind, val):
        if kind == "exit":
            self.running = False
        elif kind == "guide":
            self.draw_guide()
        elif kind == "mpv":
            if val["event"] == "client-message" and val.get("args", [None])[0] == "tv":
                self.command(val["args"][1])
            elif val["event"] == "property-change" and val.get("name") in ("osd-width", "osd-height"):
                self.win[val["name"]] = val.get("data")
                self.on_resize()
            elif val["event"] == "end-file" and val.get("reason") == "error":
                self.on_play_error(val.get("file_error", "error"))
            elif val["event"] in ("file-loaded", "playback-restart") and self.loading and self.expect_url:
                if self.mpv.get("path") == self.expect_url:
                    if val["event"] == "file-loaded":
                        self.loading["stage"] = "buffer"
                        self.draw_loading(force=True)
                    else:
                        self.finish_loading()
        elif kind == "bundle":
            self.on_bundle(*val)
        else:
            self.command(val)

    def send(self, cmd):
        """Thread-safe entry point for the web remote, menu bar etc."""
        self.q.put(("cmd", cmd))

    def command(self, c):
        n = len(self.chans)
        if c in ("wheel_up", "wheel_down"):          # trackpads fire many events per swipe: act at most every 0.35 s
            now = time.time()
            if now - self.wheel_at < 0.35:
                return
            self.wheel_at = now
            c = "up" if c == "wheel_up" else "down"
        if c == "click":
            c = "info" if self.guide is None else "guide"
        if c == "back":                              # TV remote "exit": closes the guide, otherwise nothing
            if self.guide is not None:
                self.close_guide()
            return
        if c == "enter" and not self.digits and self.guide is None:
            c = "info"                               # OK / Select with nothing else going on shows the info bar
        if c in ("volup", "voldown", "mute"):
            self.adjust_volume(c)
            return
        if c.startswith("channel:"):
            self.tune_to(c[8:])
            return
        if self.guide is not None and self.guide_command(c):
            return
        if c == "up":
            self.tune((self.idx + 1) % n)
        elif c == "down":
            self.tune((self.idx - 1) % n)
        elif c == "last":
            self.tune(self.last_idx)
        elif c == "info":
            self.show_bar()
        elif c == "guide":
            self.open_guide()
        elif c == "fullscreen":
            self.mpv.command("cycle", "fullscreen")
        elif c == "quit":
            self.running = False
        elif c.startswith("digit:"):
            self.digits = (self.digits + c[6:])[-3:]
            self.digits_at = time.time()
            self.mpv.overlay(OVL_DIGITS, osd.channel_entry(self.digits))
        elif c == "enter" and self.digits:
            self.commit_digits()

    def adjust_volume(self, c):
        if c == "mute":
            self.mpv.command("cycle", "mute")
        else:
            self.mpv.command("set", "mute", "no")
            self.mpv.command("add", "volume", 5 if c == "volup" else -5)
        vol, mute = self.mpv.get("volume"), self.mpv.get("mute")
        if vol is not None:
            self.volume = round(vol)
        self.muted = bool(mute)
        self.store.save(volume=self.volume, muted=self.muted)
        self.mpv.overlay(OVL_TOAST, osd.volume_toast(self.volume, self.muted))
        self.toast_until = time.time() + 1.6

    def tune_to(self, which):
        """Tune by dial number or channel id; ignore unknown ones."""
        for i, ch in enumerate(self.chans):
            if which in (str(ch["dial"]), ch["id"]):
                if self.guide is not None:
                    self.close_guide()
                self.tune(i)
                return

    def commit_digits(self):
        d, self.digits = int(self.digits), ""
        self.mpv.overlay(OVL_DIGITS, "")
        for i, ch in enumerate(self.chans):
            if ch["dial"] == d:
                self.tune(i)
                return

    # ---- tuning ----
    @property
    def chan(self):
        return self.chans[self.idx]

    def tune(self, idx):
        if idx != self.idx:
            self.last_idx = self.idx
        self.idx = idx
        self.gen += 1
        self.bundle, self.cur, self.key = None, None, None
        self.fails, self.retry_at, self.error = 0, 0, ""
        self.need_entry = True
        self.expect_url = None
        self.store.save(channel=self.chan["id"])
        self.set_title(f"CH {self.chan['dial']} · {self.chan['name']}")
        self.mpv.overlay(OVL_BAR, "")
        secs = self.cfg["noise_seconds"]
        self.loading = {"stage": "schedule", "since": time.time()}
        self.draw_loading(force=True)
        self.screen_key = "tune"
        if self.cfg["noise"] == "lavfi" and secs > 0:
            self.mpv.play(self.cfg["prof"]["noise_graph"], 0)      # static until something replaces it
        else:
            self.play_black(60)
        self.tuning_until = time.time() + secs                      # minimum time on static, like a real tuner
        self.bar_until = 0
        self.show_bar()

    def maybe_snapshot(self, now):
        """Plain-data picture of what the TV is doing, for the web remote and the menu bar (updated each second)."""
        if now - self.snap_at < 1:
            return
        self.snap_at = now
        ch = self.chan
        snap = {"channel": {"dial": ch["dial"], "id": ch["id"], "name": ch["name"], "color": ch.get("color")},
                "time_ms": self.src.now_ms(), "volume": self.volume, "muted": self.muted,
                "guide_open": self.guide is not None, "error": self.error or None,
                "loading": self.loading["stage"] if self.loading else None, "state": "tuning"}
        r = self.cur
        if r and self.bundle:
            e = r["entry"]
            snap["state"] = {"video": "playing", "ad": "commercial"}.get(e["type"], "break")
            if e["type"] == "video":
                it = e["item"]
                snap.update(title=it["title"], year=it.get("year"), duration_s=e["durMs"] / 1000,
                            offset_s=round(r["offset_s"], 1), remaining_s=round(r["remaining_s"], 1))
            try:
                up = self.upcoming(r)
            except Exception:
                up = []
            if up:
                snap["next"] = {"title": up[0]["item"]["title"], "start_ms": up[0]["start_ms"]}
        if self.loading:
            snap["state"] = "loading"
        elif self.error:
            snap["state"] = "error"
        self.snapshot = snap

    def set_title(self, text):
        """Window title (needs --title=${force-media-title} in the profile; harmless otherwise)."""
        self.mpv.command("set", "force-media-title", text)

    def save_window(self):
        if not self.cfg.get("remember", True) or not self.mpv:
            return
        try:
            fs = bool(self.mpv.get("fullscreen"))
            data = {"fullscreen": fs}
            w, h = self.mpv.get("osd-width"), self.mpv.get("osd-height")   # same units as --geometry
            if not fs and w and h:
                data["win"] = [w, h]
            self.store.save(**data)
        except Exception:
            pass

    # ---- window shape ----
    def on_resize(self):
        w, h = self.win.get("osd-width"), self.win.get("osd-height")
        if not w or not h:
            return
        width = max(512, min(1280, round(osd.H * w / h)))
        if abs(width - osd.W) < 4:
            return
        osd.set_canvas(width)
        self.mpv.res = (osd.W, osd.H)
        # redraw whatever is on screen with the new geometry
        self.screen_key, self.bar_second, self.load_tick = None, None, None
        self.draw_loading(force=True)
        if self.guide is not None:
            self.draw_guide()

    # ---- loading status ----
    def loading_lines(self):
        L = self.loading
        now = time.time()
        el = int(now - L["since"])
        base = {"schedule": "CHECKING SCHEDULE", "connect": "CONNECTING TO ARCHIVE.ORG",
                "buffer": "BUFFERING"}[L["stage"]]
        first = base + "." * (1 + int(now * 2) % 3) + (f"   {el}s" if el >= 2 else "")
        lines = [first]
        if L["stage"] == "schedule" and el >= 8:
            lines.append("NETWORK IS SLOW - USING SAVED SCHEDULE IF AVAILABLE")
        elif L["stage"] != "schedule" and el >= 15:
            lines.append("ARCHIVE.ORG IS SLOW TODAY. STILL TRYING.")
        return lines

    def draw_loading(self, force=False):
        if not self.loading:
            return
        step = int(time.time() * 2)
        if not force and step == self.load_tick:
            return
        self.load_tick = step
        self.mpv.overlay(OVL_SCREEN, osd.tune_card(self.chan, self.loading_lines(),
                                                   opaque=self.loading["stage"] != "schedule"))

    def finish_loading(self):
        self.loading, self.expect_url = None, None
        self.screen_key = None
        self.mpv.overlay(OVL_SCREEN, "")

    def start_bundle_load(self):
        if self.bundle_busy:
            return
        self.bundle_busy = True
        gen, cid = self.gen, self.chan["id"]

        def work():
            try:
                res = (gen, self.src.bundle(cid), None)
            except Exception as ex:
                res = (gen, None, f"{type(ex).__name__}: {ex}")
            self.q.put(("bundle", res))
        threading.Thread(target=work, daemon=True).start()

    def on_bundle(self, gen, bundle, err):
        self.bundle_busy = False
        if gen != self.gen:
            return                     # the user already moved on; tick() starts a load for the current channel
        if err:
            self.error = err
            self.loading = None
            self.mpv.overlay(OVL_SCREEN, osd.stand_by("Schedule unavailable. Trying again..."))
            self.screen_key = "standby"
            self.retry_at = time.time() + 10
        else:
            self.bundle = bundle

    def maybe_refresh_lineup(self):
        if self.refresh_busy or not self.src.due():
            return

        def work():
            try:
                self.src.refresh()
            except Exception:
                pass                   # keep the lineup we have
            finally:
                self.refresh_busy = False
        self.refresh_busy = True
        threading.Thread(target=work, daemon=True).start()

    def show_bar(self):
        self.bar_until = time.time() + self.cfg["bar_seconds"]
        self.bar_second = None

    # ---- main tick ----
    def tick(self):
        now = time.time()
        if self.digits and now - self.digits_at > self.cfg["digit_timeout_s"]:
            self.commit_digits()
        self.maybe_refresh_lineup()
        if self.toast_until and now > self.toast_until:
            self.toast_until = 0
            self.mpv.overlay(OVL_TOAST, "")
        self.maybe_snapshot(now)
        if self.guide is None:
            self.draw_loading()
        if self.retry_at and now < self.retry_at:
            return
        if self.bundle is None:
            self.start_bundle_load()
            return
        if now < self.tuning_until:
            return
        try:
            ch, manifests, platforms = self.bundle
            t = self.src.now_ms()
            r = S.now_playing(ch, manifests, platforms, t)
        except Exception as ex:           # bad data, unported feature ...
            self.error = f"{type(ex).__name__}: {ex}"
            self.bundle = None
            self.loading = None
            self.mpv.overlay(OVL_SCREEN, osd.stand_by("Trying again..."))
            self.screen_key = "standby"
            self.retry_at = now + 10
            return
        key = (self.chan["id"], r["start_ms"], r["entry"]["type"], r["entry"].get("half"))
        if self.need_entry or key != self.key:
            if key != self.key:
                self.fails = 0           # a new entry; a retry of the same entry keeps its back-off
            self.need_entry = False
            self.key, self.cur = key, r
            self.retry_at = 0
            self.start_entry(r, t)
        self.cur = r
        if self.guide is None:
            self.update_screen(r, t)
            self.update_bar(r, t)
        else:
            self.refresh_guide_clock()
        self.check_drift(r, t)

    def start_entry(self, r, t):
        e = r["entry"]
        kind = e["type"]
        if kind == "video":
            self.set_title(f"CH {self.chan['dial']} · {e['item']['title']}")
        else:
            self.set_title(f"CH {self.chan['dial']} · {self.chan['name']}")
        if kind == "video":
            it = e["item"]
            base = float(it.get("inS") or 0)
            self.play_media(S.media_url(it), base, r)
        elif kind == "ad":
            sl = e["slice"]
            self.play_media(S.ad_url(sl), sl["inMs"] / 1000.0, r)
        else:
            self.play_black(r["remaining_s"] + 0.5)
            self.media_base = None
            self.finish_loading()
        # first sync check soon after loading: archive.org can take several seconds to start streaming
        self.last_drift = time.time() - self.cfg["drift_check_s"] + 6

    def play_black(self, length):
        self.mpv.play(self.cfg["prof"]["black_graph"], 0, length)

    def play_media(self, url, base, r):
        self.media_base = base
        self.expect_url = url
        self.loading = {"stage": "connect", "since": (self.loading or {}).get("since", time.time())}
        self.screen_key = None
        self.draw_loading(force=True)
        self.mpv.play(url, base + r["offset_s"], r["remaining_s"] + 0.5)

    # ---- overlays ----
    def update_screen(self, r, t):
        e = r["entry"]
        if e["type"] == "bump-break" and self.cfg["bumps"]:
            k, ass = osd.bump_frame(e, (t - r["start_ms"]), self.chan)
            if k != self.screen_key:
                self.screen_key = k
                self.mpv.overlay(OVL_SCREEN, ass)
        elif e["type"] == "up-next":
            if self.screen_key != "upnext":
                self.screen_key = "upnext"
                self.mpv.overlay(OVL_SCREEN, osd.up_next_card(self.chan, self.upcoming(r)[0] if self.upcoming(r) else None))

    def upcoming(self, r):
        if self.upnext_cache[0] != self.key:
            ch, manifests, platforms = self.bundle
            try:
                up = S.upcoming(ch, manifests, platforms, r["start_ms"], 2)
            except Exception:
                up = []
            self.upnext_cache = (self.key, up)
        return self.upnext_cache[1]

    def update_bar(self, r, t):
        now = time.time()
        if now >= self.bar_until:
            if self.bar_second is not None:
                self.bar_second = None
                self.mpv.overlay(OVL_BAR, "")
            return
        sec = int(now)
        if sec != self.bar_second:
            self.bar_second = sec
            self.mpv.overlay(OVL_BAR, osd.info_bar(self.chan, r, self.upcoming(r), t))

    # ---- guide ----
    def open_guide(self):
        t = self.src.now_ms()
        self.guide = {"sel": self.idx, "off_ms": 0, "rows": {}, "t0": t, "asked": set(), "minute": t // 60000}
        self.mpv.overlay(OVL_BAR, "")
        self.mpv.overlay(OVL_SCREEN, "")
        self.screen_key = None
        self.digits = ""
        self.mpv.overlay(OVL_DIGITS, "")
        self.draw_guide()

    def close_guide(self):
        self.guide = None
        self.mpv.overlay(OVL_GUIDE, "")
        S.forget(keep_channel=self.chan["id"])   # free the other channels' built schedules
        self.src._gbundles.clear()
        self.need_entry = False
        self.screen_key = None
        self.bar_second = None
        self.load_tick = None
        self.draw_loading(force=True)            # the loading card (if any) was paused while the guide was up

    def guide_command(self, c):
        g, n = self.guide, len(self.chans)
        if c in ("guide", "quit", "info"):
            self.close_guide()
        elif c == "up":
            g["sel"] = (g["sel"] - 1) % n
        elif c == "down":
            g["sel"] = (g["sel"] + 1) % n
        elif c == "left":
            g["off_ms"] = max(0, g["off_ms"] - 30 * 60000)
            g["rows"].clear(); g["asked"].clear()
        elif c == "right":
            g["off_ms"] = min(10 * 3600000, g["off_ms"] + 30 * 60000)
            g["rows"].clear(); g["asked"].clear()
        elif c == "enter":
            idx = g["sel"]
            self.close_guide()
            self.tune(idx)
            return True
        else:
            return True        # swallow everything else while the guide is open
        self.draw_guide()
        return True

    def _window(self):
        g = self.guide
        first = max(0, min(g["sel"] - osd.GUIDE_ROWS // 2, len(self.chans) - osd.GUIDE_ROWS))
        return first, self.src.now_ms() + g["off_ms"]

    def draw_guide(self):
        g = self.guide
        if g is None:
            return
        first, t0 = self._window()
        g["t0"] = t0
        rows = []
        for ch in self.chans[first:first + osd.GUIDE_ROWS]:
            rows.append({"chan": ch, "programs": g["rows"].get(ch["id"])})
            if ch["id"] not in g["asked"]:
                g["asked"].add(ch["id"])
                self.guide_jobs.put((ch["id"], t0, g["off_ms"]))
        self.mpv.overlay(OVL_GUIDE, osd.guide(rows, g["sel"], first, t0, self.src.now_ms()))

    def refresh_guide_clock(self):
        g = self.guide
        minute = self.src.now_ms() // 60000
        if minute != g["minute"]:
            g["minute"] = minute
            g["rows"].clear(); g["asked"].clear()
            self.draw_guide()

    def _guide_worker(self):
        """Loads one channel's upcoming shows at a time, off the main loop (manifests may need the network)."""
        while True:
            cid, t0, off = self.guide_jobs.get()
            try:
                progs = self.src.timeline(cid, t0, t0 + osd.GUIDE_SPAN_MS)
            except Exception:
                progs = "error"
            g = self.guide
            if g is not None and g["off_ms"] == off:     # drop results for a window the user already left
                g["rows"][cid] = progs
                self.q.put(("guide", None))

    # ---- sync & errors ----
    def check_drift(self, r, t):
        if self.media_base is None or r["entry"]["type"] not in ("video", "ad"):
            return
        now = time.time()
        if now - self.last_drift < self.cfg["drift_check_s"]:
            return
        self.last_drift = now
        pos = self.mpv.get("time-pos")
        if pos is None:
            return
        expect = self.media_base + (t - r["start_ms"]) / 1000.0
        if abs(pos - expect) > self.cfg["drift_s"]:
            self.mpv.seek_abs(expect)

    def on_play_error(self, why):
        if time.time() < self.tuning_until or self.cur is None:
            return
        if self.cur["entry"]["type"] not in ("video", "ad"):
            return
        self.fails += 1
        back = self.cfg["retry_backoff_s"]
        delay = back[min(self.fails - 1, len(back) - 1)]
        self.retry_at = time.time() + delay
        self.need_entry = True          # reload at the *current* offset after the pause
        self.error = f"play failed ({why}), attempt {self.fails}"
        self.loading, self.expect_url = None, None
        self.play_black(delay + 1)      # never leave mpv idle: it shows a stale frame
        note = "Signal lost. Archive.org may be slow." if self.fails >= 3 else "Reconnecting..."
        self.mpv.overlay(OVL_SCREEN, osd.stand_by(note))
        self.screen_key = "standby"


def create(argv=None):
    """Build a TV from command-line style arguments (also used by mac/menubar.py). Returns None for --list."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--profile")
    ap.add_argument("--channel", help="dial number or channel id")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--fs", action="store_true", help="start fullscreen")
    ap.add_argument("--web", action="store_true", help="start the web remote / JSON API (see webremote.py)")
    ap.add_argument("--web-port", type=int)
    ap.add_argument("--web-token", help="require this token for the web remote")
    ap.add_argument("--cec", action="store_true", help="control with the TV remote over HDMI-CEC (needs cec-utils)")
    ap.add_argument("--mpv-arg", action="append", default=[], metavar="ARG", help="extra mpv option, repeatable")
    a = ap.parse_args(argv)
    cfg = load_config(a.config, a.profile)
    cfg["prof"]["mpv"] = cfg["prof"]["mpv"] + (["--fs"] if a.fs else []) + a.mpv_arg
    src = Source(cfg)
    chans = src.channels()
    if a.list:
        for c in chans:
            print(f"{c['dial']:>4}  {c['id']:<22} {c['name']}")
        return None
    store = Store(cfg["cache_dir"])
    saved = store.load() if cfg.get("remember", True) else {}
    want = a.channel or saved.get("channel") or str(cfg["start_channel"])
    idx = next((i for i, c in enumerate(chans) if want in (str(c["dial"]), c["id"])), None)
    if idx is None and not a.channel:        # a remembered channel can disappear from the lineup
        idx = next((i for i, c in enumerate(chans) if str(c["dial"]) == str(cfg["start_channel"])), 0)
    if idx is None:
        sys.exit(f"unknown channel {want!r}; try --list")
    if cfg.get("remember", True):               # mpv ignores `set volume` while idle, so pass them as start-up options
        extra = []
        if isinstance(saved.get("volume"), (int, float)):
            extra.append(f"--volume={saved['volume']}")
        if saved.get("muted"):
            extra.append("--mute=yes")
        cfg["prof"]["mpv"] = cfg["prof"]["mpv"] + extra
    if cfg["profile"] == "mac" and cfg.get("remember", True):      # window size comes back too (position: macOS decides)
        if saved.get("win") and not any(x.startswith("--geometry") for x in a.mpv_arg):
            cfg["prof"]["mpv"] = [x for x in cfg["prof"]["mpv"] if not x.startswith("--geometry")] + \
                [f"--geometry={saved['win'][0]}x{saved['win'][1]}"]
        if saved.get("fullscreen") and not a.fs:
            cfg["prof"]["mpv"] = cfg["prof"]["mpv"] + ["--fs"]
    tv = TV(cfg, src, idx)
    web = dict(cfg.get("web", {}))
    if a.web:
        web["enabled"] = True
    if web.get("enabled"):
        import webremote
        host, port = web.get("host", "0.0.0.0"), a.web_port or web.get("port", 8080)
        token = a.web_token or web.get("token", "")
        try:
            webremote.start(tv, host, port, token)
        except OSError as ex:
            print(f"web remote could not start on {host}:{port}: {ex}", file=sys.stderr)
        else:
            shown = webremote.lan_ip() if host in ("0.0.0.0", "") else host
            print(f"web remote: http://{shown}:{port}/" + (f"?token={token}" if token else ""), file=sys.stderr)
            if not token and host not in ("127.0.0.1", "localhost"):
                print("  note: no token set; anyone on this network can use the remote", file=sys.stderr)
    cec = dict(cfg.get("cec", {}))
    if a.cec or cec.get("enabled"):
        tv.extra_inputs.append(inputs.CecInput(cec.get("name", "Spelunkr TV"), cec.get("wake_tv", True),
                                               cec.get("binary", "cec-client"), cec.get("log_level", 16)))
    return tv


def main():
    tv = create()
    if tv:
        tv.run()


if __name__ == "__main__":
    main()
