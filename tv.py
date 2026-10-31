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
import prefetch
import schedule as S
import subs as subsmod
from mpvctl import Mpv

HERE = os.path.dirname(os.path.abspath(__file__))
OVL_BAR, OVL_SCREEN, OVL_DIGITS, OVL_GUIDE, OVL_TOAST, OVL_DIAG, OVL_PANEL, OVL_MENU = 1, 2, 3, 4, 5, 6, 7, 8
SLEEP_STEPS = [0, 15, 30, 60, 90, 120]       # minutes


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

    def details(self, channel_id, version, index):
        """The site's description entry for one show, or None."""
        ch = next(c for c in self.lineup["channels"] if c["id"] == channel_id)
        v = next((v for v in ch["versions"] if v["v"] == version), None)
        if not v or not v.get("details"):
            return None
        items = S.fetch(v["details"], self.cache)[0].get("items") or []
        return items[index] if isinstance(index, int) and 0 <= index < len(items) else None

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
        self.prefetch = prefetch.Prefetcher()
        self.subs = subsmod.Subtitles(cfg["cache_dir"], lambda: self.src.lineup)
        self.captions = False
        self.diag = False
        self.diag_at = 0
        self.panel, self.panel_at = None, 0       # "help" or "testcard": full-screen, any key closes
        self.last_load = None                     # seconds from loadfile to first frame, for the diagnostics box
        self.load_started = None
        self.media_orig = None                    # the original (redirecting) URL of what is playing
        self.prefetched_key = None
        self.gain = 0              # what mpv currently has
        self.want_gain = 0         # what the current entry wants (ads are turned down by their gainDb)
        self.web_url = None
        self.menu = None                          # {"sel": n} while the settings menu is open
        self.panel_payload = None
        self.sleep_at, self.sleep_warned, self.asleep = None, False, False
        self.tune_after = 0                       # boot splash: tune once this time has passed
        self.sys_cache = (0, "")

    # ---- lifecycle ----
    def run(self):
        bindings = dict(inputs.KEY_BINDINGS)
        self.mpv = Mpv(self.cfg["prof"]["mpv"], bindings, mono=bool(self.cfg["prof"].get("mono")))
        threading.Thread(target=self._pump_mpv, daemon=True).start()
        threading.Thread(target=self._guide_worker, daemon=True).start()
        for i, prop in enumerate(("osd-width", "osd-height"), 1):
            self.mpv.command("observe_property", i, prop)      # overlays follow the window / display shape
        st = self.store.load()
        if self.cfg.get("remember", True):
            if isinstance(st.get("volume"), (int, float)):
                self.volume = st["volume"]          # applied through mpv's start-up options (see main)
            self.muted = bool(st.get("muted"))
            self.captions = bool(st.get("captions"))
        kb = inputs.KeyboardInput()
        kb.start(lambda c: self.q.put(("cmd", c)))
        for extra in self.extra_inputs:
            extra.start(lambda c: self.q.put(("cmd", c)))
        secs = self.cfg.get("splash_seconds", 2.5)
        if secs > 0 and not os.environ.get("TV_NO_SPLASH"):
            self.splash(secs)
        else:
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
                  f"title={self.mpv.get('media-title')!r} sat={self.mpv.get('saturation')} want={self.want_gain} gain={self.mpv.get('volume-gain')} direct={bool(self.media_orig and self.prefetch.is_resolved(self.media_orig))} load={self.last_load} vol={self.mpv.get('volume')} mute={self.mpv.get('mute')} time-pos={self.mpv.get('time-pos')} idle={self.mpv.get('idle-active')} err={self.error!r}",
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
                        if self.load_started:
                            self.last_load = round(time.time() - self.load_started, 1)
                        self.finish_loading()
                        self.apply_gain()
                        if self.captions:
                            self.request_captions()
        elif kind == "bundle":
            self.on_bundle(*val)
        elif kind == "subs":
            self.on_subs(*val)
        elif kind == "details":
            self.on_details(*val)
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
        if self.tune_after and c != "quit":          # boot splash: ignore keys until we are tuned
            return
        if self.asleep:                              # any key (or the web remote) wakes the box
            self.wake()
            return
        if self.sleep_at and self.sleep_warned and c != "sleep":
            self.set_sleep(0, "SLEEP TIMER CANCELLED")   # a key press in the last minute means "still watching"
            return
        if c == "click":
            c = "bar" if (self.guide is None and self.menu is None and self.panel is None) else "back"
        if c == "back":                              # TV remote "exit": closes whatever is open
            if self.menu is not None:
                self.close_menu()
            elif self.guide is not None:
                self.close_guide()
            elif self.panel is not None:
                self.close_panel()
            return
        if c == "enter" and not self.digits and self.guide is None and self.menu is None:
            c = "info"                               # OK / Select with nothing else going on shows the info bar
        if c in ("volup", "voldown", "mute"):
            self.adjust_volume(c)
            return
        if self.menu is not None:
            self.menu_command(c)
            return
        if self.panel is not None:                   # help / test card / details: any other key just closes it
            self.close_panel()
            return
        if c == "diag":
            self.toggle_diag()
            return
        if c in ("help", "testcard"):
            self.open_panel(c)
            return
        if c == "captions":
            self.toggle_captions()
            return
        if c == "details":
            self.open_details()
            return
        if c == "menu":
            self.open_menu()
            return
        if c == "sleep":
            self.step_sleep(1)
            return
        if c.startswith("sleep:"):                   # exact minutes (web API, automation): sleep:45, sleep:0 = off
            try:
                self.set_sleep(max(0.0, min(480.0, float(c[6:]))))
            except ValueError:
                pass
            return
        if c == "bar":
            self.show_bar()
            return
        if c == "now" and self.guide is None:
            self.open_guide("now")
            return
        if c == "info" and self.guide is None:       # first press shows the bar, a second one while it is up shows details
            if time.time() < self.bar_until and self.cur and self.cur["entry"]["type"] == "video":
                self.open_details()
            else:
                self.show_bar()
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
                "guide_open": self.guide is not None, "asleep": self.asleep,
                "sleep_in_s": round(self.sleep_at - time.time()) if self.sleep_at else None, "captions": self.captions, "last_load_s": self.last_load, "error": self.error or None,
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
            self.mpv.overlay(OVL_SCREEN, self.standby_ass("Schedule unavailable. Trying again..."))
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
        if self.tune_after:                          # boot splash
            if now < self.tune_after:
                return
            self.tune_after = 0
            self.mpv.overlay(OVL_PANEL, "")
            self.tune(self.idx)
            return
        if self.asleep:
            self.maybe_snapshot(now)
            return
        if self.sleep_at:
            left = self.sleep_at - now
            if left <= 0:
                self.go_to_sleep()
                return
            if left <= 60 and not self.sleep_warned:
                self.sleep_warned = True
                self.toast(osd.toast_text("SLEEPING IN 1 MINUTE - PRESS A KEY TO STAY"), 10)
        if self.digits and now - self.digits_at > self.cfg["digit_timeout_s"]:
            self.commit_digits()
        self.maybe_refresh_lineup()
        if self.toast_until and now > self.toast_until:
            self.toast_until = 0
            self.mpv.overlay(OVL_TOAST, "")
        self.maybe_snapshot(now)
        self.update_diag(now)
        self.update_panel(now)
        if self.guide is None and self.panel is None and self.menu is None:
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
            self.mpv.overlay(OVL_SCREEN, self.standby_ass("Trying again..."))
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
        if self.guide is not None:
            self.refresh_guide_clock()
        elif self.panel is None and self.menu is None:
            self.update_screen(r, t)
            self.update_bar(r, t)
        self.maybe_prefetch(r)
        self.check_drift(r, t)

    def start_entry(self, r, t):
        e = r["entry"]
        kind = e["type"]
        if kind == "video":
            self.set_title(f"CH {self.chan['dial']} · {e['item']['title']}")
        else:
            self.set_title(f"CH {self.chan['dial']} · {self.chan['name']}")
        self.set_gain(0)
        if kind == "video":
            it = e["item"]
            base = float(it.get("inS") or 0)
            self.play_media(S.media_url(it), base, r)
        elif kind == "ad":
            sl = e["slice"]
            gain = sl.get("gainDb")                         # the site only ever turns ads down, never up
            self.set_gain(gain if isinstance(gain, (int, float)) and gain < 0 else 0)
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
        self.media_orig = url
        url = self.prefetch.direct(url)                      # the resolved item-server URL when we have one (saves ~2 s)
        self.load_started = time.time()
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
            self.mpv.overlay(OVL_BAR, osd.info_bar(self.chan, r, self.upcoming(r), t,
                                                   sleep_s=(self.sleep_at - now) if self.sleep_at else None))

    # ---- guide ----
    def open_guide(self, mode="grid"):
        if self.panel is not None:
            self.close_panel()
        t = self.src.now_ms()
        self.guide = {"sel": self.idx, "off_ms": 0, "rows": {}, "t0": t, "asked": set(), "minute": t // 60000,
                      "mode": mode}               # "grid" = the Prevue-style guide, "now" = list of what is on now
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
        if c == "now":
            if g["mode"] == "now":
                self.close_guide()
            else:
                g["mode"] = "now"
        elif c == "guide" and g["mode"] == "now":
            g["mode"] = "grid"
        elif c in ("guide", "quit", "info"):
            self.close_guide()
        elif c in ("left", "right") and g["mode"] == "now":
            return True
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

    def _page(self):
        return osd.NOW_ROWS if self.guide["mode"] == "now" else osd.GUIDE_ROWS

    def _window(self):
        g, per = self.guide, self._page()
        first = max(0, min(g["sel"] - per // 2, len(self.chans) - per))
        return first, self.src.now_ms() + g["off_ms"]

    def draw_guide(self):
        g = self.guide
        if g is None:
            return
        first, t0 = self._window()
        g["t0"] = t0
        rows = []
        for ch in self.chans[first:first + self._page()]:
            rows.append({"chan": ch, "programs": g["rows"].get(ch["id"])})
            if ch["id"] not in g["asked"]:
                g["asked"].add(ch["id"])
                self.guide_jobs.put((ch["id"], t0, g["off_ms"]))
        if g["mode"] == "now":
            self.mpv.overlay(OVL_GUIDE, osd.now_list(rows, g["sel"], first, self.src.now_ms()))
        else:
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

    # ---- full-screen panels (help / test card) ----
    def standby_ass(self, reason=""):
        return osd.stand_by(reason, self.cfg.get("test_card", True))

    def open_panel(self, kind, payload=None):
        if self.guide is not None:
            self.close_guide()
        self.panel, self.panel_at, self.panel_payload = kind, 0, payload
        self.mpv.overlay(OVL_BAR, "")
        self.mpv.overlay(OVL_SCREEN, "")
        self.screen_key = None
        self.update_panel(time.time())

    def close_panel(self):
        self.panel = None
        self.mpv.overlay(OVL_PANEL, "")
        self.screen_key, self.bar_second, self.load_tick = None, None, None
        self.draw_loading(force=True)

    def update_panel(self, now):
        if self.panel is None or now - self.panel_at < (2 if self.panel == "help" else 3600):
            return
        self.panel_at = now
        if self.panel == "details":
            ass = osd.details_screen(self.chan, *self.panel_payload)
        elif self.panel == "testcard":
            ass = osd.test_card("SPELUNKR TV", f"CH {self.chan['dial']}  {self.chan['name']}")
        else:
            ass = osd.help_screen(self.help_lines())
        self.mpv.overlay(OVL_PANEL, ass)

    def help_lines(self):
        import webremote
        skew = self.src.skew_ms / 1000
        w, h = self.win.get("osd-width"), self.win.get("osd-height")
        lines = [f"profile {self.cfg['profile']}    display {w}x{h}    mpv overlays {osd.W}x{osd.H}",
                 f"remote  {self.web_url or 'off (start with --web)'}",
                 f"this device  {webremote.lan_ip()}",
                 "clock  in sync with the server" if not skew else f"clock  {skew:+.0f}s off the server (corrected)",
                 f"channels {len(self.chans)}    lineup {str(self.src.lineup.get('generatedAt', '?'))[:16]}",
                 f"captions {'on' if self.captions else 'off'}    cec {'on' if self.cfg.get('cec', {}).get('enabled') else 'off'}"]
        return lines

    # ---- show details ----
    def open_details(self):
        r = self.cur
        if not r or r["entry"]["type"] != "video" or not self.bundle:
            self.toast(osd.toast_text("NO SHOW TO DESCRIBE RIGHT NOW"))
            return
        entry, key, cid, ver = r["entry"], self.key, self.chan["id"], r["version"]

        def work():
            try:
                det = self.src.details(cid, ver, entry.get("videoIndex"))
            except Exception:
                det = None                      # the screen still shows title, year and runtime
            self.q.put(("details", (key, entry["item"], det, entry["durMs"] / 1000)))
        threading.Thread(target=work, daemon=True, name="details").start()

    def on_details(self, key, item, det, runtime):
        if key != self.key or self.guide is not None or self.menu is not None or self.asleep:
            return
        self.open_panel("details", (item, det, runtime))

    # ---- settings menu ----
    def menu_entries(self):
        def onoff(b):
            return "on" if b else "off"

        def cycle(options, cur, d):
            i = options.index(cur) if cur in options else 0
            return options[(i + d) % len(options)]

        mono = bool(self.cfg["prof"].get("mono"))
        mins = self.sleep_minutes()
        return [
            ("Volume", "muted" if self.muted else f"{self.volume}%",
             lambda d: self.adjust_volume("volup" if d > 0 else "voldown")),
            ("Captions", onoff(self.captions), lambda d: self.toggle_captions()),
            ("Info bar stays", f"{self.cfg['bar_seconds']} s",
             lambda d: self.set_setting("bar_seconds", cycle([3, 6, 10], self.cfg["bar_seconds"], d))),
            ("Static between channels", onoff(self.cfg["noise"] == "lavfi"),
             lambda d: self.set_setting("noise", "none" if self.cfg["noise"] == "lavfi" else "lavfi")),
            ("Station-break slides", onoff(self.cfg["bumps"]), lambda d: self.set_setting("bumps", not self.cfg["bumps"])),
            ("Black & white", onoff(mono), lambda d: self.set_mono(not mono)),
            ("Sleep timer", f"{mins} min" if mins else "off", lambda d: self.step_sleep(d)),
        ]

    def open_menu(self):
        if self.guide is not None:
            self.close_guide()
        if self.panel is not None:
            self.close_panel()
        self.menu = {"sel": 0}
        self.mpv.overlay(OVL_BAR, "")
        self.mpv.overlay(OVL_SCREEN, "")
        self.screen_key = None
        self.draw_menu()

    def close_menu(self):
        self.menu = None
        self.mpv.overlay(OVL_MENU, "")
        self.screen_key, self.bar_second, self.load_tick = None, None, None
        self.draw_loading(force=True)

    def draw_menu(self):
        if self.menu is None:
            return
        entries = self.menu_entries()
        self.mpv.overlay(OVL_MENU, osd.menu_screen([(lbl, val) for lbl, val, _ in entries], self.menu["sel"]))

    def menu_command(self, c):
        entries = self.menu_entries()
        if c in ("menu", "quit", "info", "guide", "bar", "now"):
            self.close_menu()
            return
        if c in ("up", "down"):
            self.menu["sel"] = (self.menu["sel"] + (1 if c == "down" else -1)) % len(entries)
        elif c in ("left", "right", "enter"):
            entries[self.menu["sel"]][2](-1 if c == "left" else 1)
        self.draw_menu()

    def set_setting(self, key, value):
        self.cfg[key] = value
        saved = self.store.load().get("settings", {})
        saved[key] = value
        self.store.save(settings=saved)

    def set_mono(self, on):
        self.cfg["prof"]["mono"] = on
        self.mpv.mono = on
        self.mpv.command("set_property", "saturation", -100 if on else 0)
        self.set_setting("mono", on)
        self.screen_key, self.bar_second, self.load_tick = None, None, None      # redraw everything in the new colours
        if self.guide is not None:
            self.draw_guide()

    # ---- sleep timer ----
    def sleep_minutes(self):
        return max(1, round((self.sleep_at - time.time()) / 60)) if self.sleep_at else 0

    def step_sleep(self, d):
        cur = self.sleep_minutes()
        i = min(range(len(SLEEP_STEPS)), key=lambda k: abs(SLEEP_STEPS[k] - cur)) if cur else 0
        self.set_sleep(SLEEP_STEPS[(i + d) % len(SLEEP_STEPS)])

    def set_sleep(self, minutes, note=None):
        self.sleep_at = time.time() + minutes * 60 if minutes else None
        self.sleep_warned = False
        self.toast(osd.toast_text(note or (f"SLEEP TIMER: {minutes} MIN" if minutes else "SLEEP TIMER OFF")))
        if self.menu is not None:
            self.draw_menu()

    def go_to_sleep(self):
        self.asleep, self.sleep_at, self.sleep_warned = True, None, False
        self.guide, self.menu, self.panel = None, None, None
        self.loading, self.expect_url, self.digits = None, None, ""
        for ov in (OVL_BAR, OVL_SCREEN, OVL_DIGITS, OVL_GUIDE, OVL_TOAST, OVL_DIAG, OVL_MENU):
            self.mpv.overlay(ov, "")
        self.play_black(12 * 3600)                    # stops streaming from archive.org
        self.mpv.overlay(OVL_PANEL, osd.goodnight_card())
        for extra in self.extra_inputs:
            if hasattr(extra, "standby"):
                extra.standby()                       # HDMI-CEC: put the TV to sleep too
        if self.cfg.get("sleep_action", "blank") == "quit":
            self.running = False

    def wake(self):
        self.asleep = False
        self.mpv.overlay(OVL_PANEL, "")
        for extra in self.extra_inputs:
            if hasattr(extra, "wake"):
                extra.wake()
        self.tune(self.idx)

    # ---- boot splash ----
    def splash(self, secs):
        self.play_black(secs + 10)
        self.mpv.overlay(OVL_PANEL, osd.splash(["TUNING IN..."]))
        self.tune_after = time.time() + secs

    # ---- diagnostics box ----
    def toggle_diag(self):
        self.diag = not self.diag
        self.diag_at = 0
        if not self.diag:
            self.mpv.overlay(OVL_DIAG, "")
        else:
            self.update_diag(time.time())

    def update_diag(self, now):
        if not self.diag or now - self.diag_at < 1:
            return
        self.diag_at = now
        self.mpv.overlay(OVL_DIAG, osd.diag_panel(self.diag_lines()))

    def _sys_line(self, now):
        if now - self.sys_cache[0] < 10:
            return self.sys_cache[1]
        parts = []
        try:
            parts.append(f"{int(open('/sys/class/thermal/thermal_zone0/temp').read()) / 1000:.0f}C")
        except (OSError, ValueError):
            pass
        try:
            parts.append(f"load {os.getloadavg()[0]:.2f}")
            for ln in open("/proc/meminfo"):
                if ln.startswith("MemAvailable"):
                    parts.append(f"{int(ln.split()[1]) // 1024}MB free")
                    break
        except OSError:
            pass
        import shutil
        import subprocess
        if shutil.which("vcgencmd"):
            try:
                v = int(subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2)
                        .stdout.strip().split("=")[1], 16)
                flags = [name for bit, name in ((0, "UNDERVOLTAGE"), (2, "THROTTLED"), (3, "TEMP LIMIT")) if v & (1 << bit)]
                parts.append("power ok" if not flags else " ".join(flags))
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                pass
        self.sys_cache = (now, "   ".join(parts))
        return self.sys_cache[1]

    def diag_lines(self):
        def g(prop):
            return self.mpv.request("get_property", prop, timeout=0.25)

        def num(v, fmt="{:.1f}", none="?"):
            return fmt.format(v) if isinstance(v, (int, float)) else none

        now, r = time.time(), self.cur
        what = f"{r['entry']['type']}  v{r['version']} cycle {r['cycle']}" if r else (
            f"loading ({self.loading['stage']})" if self.loading else "idle")
        lines = [f"DIAGNOSTICS   CH {self.chan['dial']} {self.chan['name']}   {what}"]
        if r and self.media_base is not None and r["entry"]["type"] in ("video", "ad") and not self.loading:
            pos = g("time-pos")
            want = self.media_base + (self.src.now_ms() - r["start_ms"]) / 1000
            vp = g("video-params") or {}
            kbps = g("video-bitrate")
            lines += [
                f"position {num(pos)}s   schedule {num(want)}s   drift {num(pos - want if pos is not None else None, '{:+.1f}')}s",
                f"{g('video-format') or '?'} {vp.get('w', '?')}x{vp.get('h', '?')}  {num(g('estimated-vf-fps'))} fps   "
                f"decoder: {g('hwdec-current') or 'software'}",
                f"dropped: decoder {g('decoder-frame-drop-count')}  output {g('frame-drop-count')}   "
                f"a/v sync {num(g('avsync'), '{:+.2f}')}s",
                f"cache {num(g('demuxer-cache-duration'))}s   bitrate {num(kbps / 1000 if kbps else None, '{:.0f}')} kbps"]
        direct = self.media_orig and self.prefetch.is_resolved(self.media_orig)
        lines.append(f"last start {num(self.last_load)}s  ({'direct url' if direct else 'via redirect'})   "
                     f"volume {self.volume}{' muted' if self.muted else ''}")
        lines.append(f"lineup {int(now - self.src._checked)}s old   clock skew {self.src.skew_ms / 1000:+.1f}s"
                     + (f"   ERROR {self.error[:40]}" if self.error else ""))
        sysl = self._sys_line(now)
        if sysl:
            lines.append(sysl)
        return lines

    # ---- captions ----
    def toast(self, ass, secs=1.8):
        self.mpv.overlay(OVL_TOAST, ass)
        self.toast_until = time.time() + secs

    def toggle_captions(self):
        self.captions = not self.captions
        self.store.save(captions=self.captions)
        if self.captions:
            self.toast(osd.toast_text("CAPTIONS ON"))
            self.request_captions(announce=True)
        else:
            self.mpv.command("set", "sub-visibility", "no")
            self.toast(osd.toast_text("CAPTIONS OFF"))

    def request_captions(self, announce=False):
        r = self.cur
        if not r or r["entry"]["type"] != "video":
            return
        item, key = r["entry"]["item"], self.key

        def work():
            try:
                res = self.subs.for_item(item)
            except Exception:
                res = None
            self.q.put(("subs", (key, res, announce)))
        threading.Thread(target=work, daemon=True, name="captions").start()

    def on_subs(self, key, res, announce):
        if key != self.key or not self.captions:
            return                                   # the show changed, or captions were switched off meanwhile
        if res:
            path, entry = res
            self.mpv.command("sub-add", path, "select")
            self.mpv.command("set", "sub-visibility", "yes")
            label = "SUBTITLES BY MACHINE" if entry.get("source") == "machine" else "SUBTITLES"
            self.toast(osd.toast_text(label), 2.5)
        elif announce:
            self.toast(osd.toast_text("NO CAPTIONS FOR THIS SHOW"))

    # ---- ad loudness, prefetch ----
    def set_gain(self, db):
        """Remember the wanted gain; mpv only accepts it once audio is up, so it is applied after each load."""
        self.want_gain = db

    def apply_gain(self):
        if self.want_gain != self.gain:
            self.gain = self.want_gain
            self.mpv.command("set_property", "volume-gain", self.want_gain)

    def next_media_url(self, r):
        ch, manifests, platforms = self.bundle
        t = int(r["end_ms"]) + 1
        for _ in range(4):
            nr = S.now_playing(ch, manifests, platforms, t)
            e = nr["entry"]
            if e["type"] == "video":
                return S.media_url(e["item"])
            if e["type"] == "ad":
                return S.ad_url(e["slice"])
            t = int(nr["end_ms"]) + 1
        return None

    def maybe_prefetch(self, r):
        if self.loading or self.key == self.prefetched_key or r["remaining_s"] > self.cfg.get("prefetch_s", 60):
            return
        self.prefetched_key = self.key
        try:
            url = self.next_media_url(r)
        except Exception:
            url = None
        if url:
            self.prefetch.warm(url)

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
        if self.media_orig and self.prefetch.is_resolved(self.media_orig):
            self.prefetch.forget(self.media_orig)            # the shortcut may be stale: retry via the original URL
        self.fails += 1
        back = self.cfg["retry_backoff_s"]
        delay = back[min(self.fails - 1, len(back) - 1)]
        self.retry_at = time.time() + delay
        self.need_entry = True          # reload at the *current* offset after the pause
        self.error = f"play failed ({why}), attempt {self.fails}"
        self.loading, self.expect_url = None, None
        self.play_black(delay + 1)      # never leave mpv idle: it shows a stale frame
        note = "Signal lost. Archive.org may be slow." if self.fails >= 3 else "Reconnecting..."
        self.mpv.overlay(OVL_SCREEN, self.standby_ass(note))
        self.screen_key = "standby"


def create(argv=None):
    """Build a TV from command-line style arguments (also used by mac/menubar.py). Returns None for --list."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--profile")
    ap.add_argument("--channel", help="dial number or channel id")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--fs", action="store_true", help="start fullscreen")
    ap.add_argument("--mono", action="store_true", help="black-and-white screen: grayscale video and overlays")
    ap.add_argument("--web", action="store_true", help="start the web remote / JSON API (see webremote.py)")
    ap.add_argument("--web-port", type=int)
    ap.add_argument("--web-token", help="require this token for the web remote")
    ap.add_argument("--cec", action="store_true", help="control with the TV remote over HDMI-CEC (needs cec-utils)")
    ap.add_argument("--mpv-arg", action="append", default=[], metavar="ARG", help="extra mpv option, repeatable")
    a = ap.parse_args(argv)
    cfg = load_config(a.config, a.profile)
    saved_settings = Store(cfg["cache_dir"]).load().get("settings", {}) if cfg.get("remember", True) else {}
    for key in ("bar_seconds", "noise", "bumps"):         # things changed in the settings menu stay changed
        if key in saved_settings:
            cfg[key] = saved_settings[key]
    if "mono" in saved_settings and not a.mono:
        cfg["prof"]["mono"] = saved_settings["mono"]
    if a.mono:
        cfg["prof"]["mono"] = True
    if cfg["prof"].get("mono") and not any(x.startswith("--saturation") for x in cfg["prof"]["mpv"] + a.mpv_arg):
        cfg["prof"]["mpv"] = cfg["prof"]["mpv"] + ["--saturation=-100"]      # grayscale picture
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
            tv.web_url = f"http://{shown}:{port}/"
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
