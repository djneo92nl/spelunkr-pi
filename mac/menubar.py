#!/usr/bin/env python3
"""Menu bar front-end for the TV client (macOS).

    pip3 install rumps            # the only extra dependency, and only for this file
    python3 mac/menubar.py        # takes the same options as tv.py (--channel, --fs, --web ...)

Shows what is on in the menu bar, lists all channels, and has the usual controls. The TV itself (the mpv window) runs
exactly as with tv.py; closing that window or choosing Quit ends everything.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import rumps
except ImportError:
    sys.exit("The menu bar needs the 'rumps' package:  pip3 install rumps\n(tv.py works without it.)")

import tv as tvmod


def short(s, n):
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


class MenuBar(rumps.App):
    def __init__(self, tv):
        super().__init__("TV", title="📺", quit_button=None)
        self.tv = tv
        self.now_item = rumps.MenuItem("Starting…")
        self.next_item = rumps.MenuItem("")
        self.channel_items = {}
        channels = rumps.MenuItem("Channels")
        for ch in tv.chans:
            item = rumps.MenuItem(f"{ch['dial']:>3}   {ch['name'].title()}",
                                  callback=lambda _s, cid=ch["id"]: tv.send("channel:" + cid))
            self.channel_items[ch["id"]] = item
            channels.add(item)

        def action(title, cmd, key=None):
            return rumps.MenuItem(title, callback=lambda _s: tv.send(cmd), key=key)

        self.menu = [
            self.now_item, self.next_item, None,
            action("Channel up", "up"), action("Channel down", "down"), action("Last channel", "last"),
            channels, None,
            action("TV guide", "guide", "g"), action("Info bar", "info", "i"), action("Fullscreen", "fullscreen", "f"), None,
            action("Volume up", "volup"), action("Volume down", "voldown"), action("Mute", "mute", "m"), None,
            rumps.MenuItem("Quit", callback=self.quit_all, key="q"),
        ]
        self.timer = rumps.Timer(self.refresh, 2)
        self.timer.start()

    def refresh(self, _timer):
        thread = getattr(self, "tv_thread", None)
        if thread is not None and not thread.is_alive():
            return rumps.quit_application()
        s = dict(self.tv.snapshot)
        if not s:
            return
        ch = s["channel"]
        self.title = f"📺 {ch['dial']}"
        state = s.get("state")
        if s.get("title"):
            self.now_item.title = f"CH {ch['dial']} · {short(s['title'], 48)}"
        else:
            self.now_item.title = f"CH {ch['dial']} · {ch['name'].title()} ({state})"
        nxt = s.get("next")
        self.next_item.title = ("Next: " + short(nxt["title"], 44)) if nxt else ""
        for cid, item in self.channel_items.items():
            item.state = 1 if cid == ch["id"] else 0

    def quit_all(self, _):
        self.tv.q.put(("exit", None))
        rumps.quit_application()


def main():
    tv = tvmod.create(sys.argv[1:])
    if tv is None:
        return
    app = MenuBar(tv)
    app.tv_thread = threading.Thread(target=tv.run, name="tv", daemon=True)
    app.tv_thread.start()
    app.run()


if __name__ == "__main__":
    main()
