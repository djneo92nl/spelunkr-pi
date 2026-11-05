"""Shared fakes for the tests: no network, no mpv."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NOW = 1_800_000_000_000          # fixed "now" in ms


def chan(i, dial=None, color="#FF6F00"):
    return {"id": f"chan-{i}", "name": f"CHANNEL {i}", "dial": dial or i + 2, "color": color, "family": "test",
            "description": "A channel made for tests. " * 4, "stats": {"items": 10, "videoSeconds": 36000},
            "logomark": {"type": "pixel", "rows": ["ccc.", "ckkc", "cddc", ".ccc"]}, "versions": []}


def prog(start_min, length_min, title, **item):
    it = {"id": title.lower().replace(" ", "-"), "title": title, "year": 1985, **item}
    return {"start_ms": NOW + start_min * 60000, "end_ms": NOW + (start_min + length_min) * 60000, "item": it,
            "version": 1, "videoIndex": 0}


class FakeSrc:
    skew_ms = 0
    lineup = {"channels": []}
    _gbundles = {}
    _checked = 0

    def __init__(self, chans, shows=None, details=None):
        self._chans, self.shows, self._details = chans, shows or {}, details or {}

    def channels(self):
        return self._chans

    def now_ms(self):
        return NOW + 5 * 60000        # five minutes into the first programme

    def timeline(self, cid, t0, t1, breaks=False):
        return [p for p in self.shows.get(cid, []) if p["end_ms"] > t0 and p["start_ms"] < t1]

    def details(self, cid, version, index):
        return self._details.get(cid)

    def bundle(self, cid):
        raise RuntimeError("no bundle in tests")


class StubMpv:
    """Records overlays and swallows commands."""
    res = (768, 576)
    mono = False

    def __init__(self):
        self.overlays = {}

    def overlay(self, oid, ass):
        if ass:
            self.overlays[oid] = ass
        else:
            self.overlays.pop(oid, None)

    def command(self, *a, **k):
        pass

    def get(self, *a, **k):
        return None

    def request(self, *a, **k):
        return None


def make_tv(tmpdir, n_chans=3, feeds=None, **extra):
    import tv as TVM
    chans = [chan(i) for i in range(n_chans)]
    shows = {c["id"]: [prog(0, 30, f"Show A{i}"), prog(30, 30, f"Show B{i}", show="Series", mature=True),
                       prog(60, 30, f"Show C{i}")] for i, c in enumerate(chans)}
    details = {c["id"]: {"description": "Something described here.", "season": 2, "imdbId": "tt1234567"} for c in chans}
    cfg = {"cache_dir": str(tmpdir), "bar_seconds": 6, "profile": "test", "prof": {"mpv": ["mpv"]}, "noise": "none",
           "bumps": False, "digit_timeout_s": 1.5, "feeds": feeds or [], **extra}
    src = FakeSrc(chans, shows, details)
    tv = TVM.TV(cfg, src, 0)
    tv.mpv = StubMpv()
    return tv
