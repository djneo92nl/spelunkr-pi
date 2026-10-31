"""Faster starts: resolve archive.org's download redirect for the next item ahead of time.

https://archive.org/download/<id>/<file> redirects to the item's own server. Following that redirect costs about 2 s on
every start, and the first bytes of a cold file take ~3 s. A small ranged request shortly before an item is due costs the
server almost nothing, shows us the final URL, and warms the file. mpv then gets the final URL directly.
If the direct URL ever fails, the caller forgets it and falls back to the original (see TV.on_play_error).
"""
import threading
import time
import urllib.request

import schedule as S

TTL = 20 * 60          # a resolved URL is trusted for 20 minutes


class Prefetcher:
    def __init__(self):
        self._resolved = {}      # original url -> (final url, time)
        self._busy = set()
        self._lock = threading.Lock()

    def direct(self, url):
        """The resolved URL if we have a fresh one, otherwise the original."""
        with self._lock:
            ent = self._resolved.get(url)
        return ent[0] if ent and time.time() - ent[1] < TTL else url

    def is_resolved(self, url):
        return self.direct(url) != url

    def forget(self, url):
        with self._lock:
            self._resolved.pop(url, None)

    def warm(self, url):
        """Resolve + touch `url` in the background (once at a time per URL)."""
        with self._lock:
            if url in self._busy or (url in self._resolved and time.time() - self._resolved[url][1] < TTL):
                return
            self._busy.add(url)
        threading.Thread(target=self._work, args=(url,), daemon=True, name="prefetch").start()

    def _work(self, url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": S.UA, "Range": "bytes=0-32767"})
            with urllib.request.urlopen(req, timeout=20) as r:
                r.read(32768)
                final = r.geturl()
            with self._lock:
                self._resolved[url] = (final, time.time())
        except Exception:
            pass                 # prefetching is best effort; playback works without it
        finally:
            with self._lock:
                self._busy.discard(url)
