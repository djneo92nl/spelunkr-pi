"""Search the whole lineup for a show: titles, series names and descriptions of every channel's current manifest.

    idx = Index(src)          # src: tv.Source (lineup + cached manifests and details)
    idx.ensure()              # fetches what is missing (a few seconds the first time); later calls return at once
    hits = idx.query("twin peaks")
    airings(src, hits, now_ms)  # when each hit is on in the next 24 hours

The index is built once per run, in whatever thread first asks. Channels whose files cannot be fetched are skipped and counted.
"""
import logging
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import schedule as S

log = logging.getLogger("tv")
MAX_QUERY = 40


def norm(s):
    """Lower case without accents, so 'Café' matches 'cafe'."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()


class Index:
    def __init__(self, src, workers=4):
        self.src, self.workers = src, workers
        self.entries = []
        self.state = "idle"                  # idle | building | ready
        self.total = self.done = self.failed = self.no_details = 0
        self._lock = threading.Lock()

    def ensure(self):
        """Build the index if nobody has yet (other callers wait for the builder)."""
        with self._lock:
            if self.state == "ready":
                return
            self.state = "building"
            chans = self.src.channels()
            self.total, self.done, self.failed, self.no_details = len(chans), 0, 0, 0
            with ThreadPoolExecutor(self.workers) as ex:
                parts = list(ex.map(self._channel, chans))
            self.entries = [e for part in parts for e in part]
            self.state = "ready"
            if self.failed or self.no_details:
                log.warning("search: %d entries; %d of %d channels have no manifest, %d have no descriptions (see the log at INFO level)",
                            len(self.entries), self.failed, self.total, self.no_details)

    def _channel(self, ch):
        try:
            v = max(ch["versions"], key=lambda x: x["v"])
            manifest = S.fetch(v["manifest"], self.src.cache)[0]
        except Exception as ex:
            log.info("search: no manifest for %s: %s", ch.get("id"), ex)
            self.failed += 1
            self.done += 1
            return []
        descs, imdb = [], []
        if v.get("details"):
            try:
                det = S.fetch(v["details"], self.src.cache)[0].get("items") or []
                descs = [d.get("description") or "" for d in det]
                imdb = [d.get("imdbId") or "" for d in det]
            except Exception as ex:                       # titles still work without descriptions
                log.info("search: no details for %s: %s", ch.get("id"), ex)
                self.no_details += 1
        out = []
        for i, it in enumerate(manifest.get("items") or []):
            if it.get("offAir") or not it.get("title"):
                continue
            desc = descs[i] if i < len(descs) else ""
            show = it.get("show") or ""
            out.append({"cid": ch["id"], "dial": ch["dial"], "cname": ch["name"], "version": v["v"], "index": i,
                        "id": it.get("id"), "title": it["title"], "show": show, "year": it.get("year"),
                        "mature": bool(it.get("mature")), "desc": desc, "imdb": imdb[i] if i < len(imdb) else "",
                        "_t": norm(it["title"]), "_s": norm(show), "_d": norm(desc)})
        self.done += 1
        return out

    def query(self, text, limit=30):
        """Best matches first. Every word must appear somewhere (title counts most, then series, then description)."""
        text = norm(text)[:MAX_QUERY].strip()
        words = text.split()
        if not words:
            return []
        scored = []
        for e in self.entries:
            score = 0
            for w in words:
                if w in e["_t"]:
                    score += 4 if (" " + w) in (" " + e["_t"]) else 3        # a word start beats the middle of a word
                elif w in e["_s"]:
                    score += 2
                elif w in e["_d"]:
                    score += 1
                else:
                    score = 0
                    break
            if score:
                if text in e["_t"]:
                    score += 4                                              # the whole phrase in the title
                scored.append((-score, e["_t"], e["dial"], e))
        scored.sort(key=lambda s: s[:3])
        seen, out = set(), []
        for _, _, _, e in scored:
            key = (e["cid"], e["id"])
            if key not in seen:
                seen.add(key)
                out.append({k: v for k, v in e.items() if not k.startswith("_")})
                if len(out) >= limit:
                    break
        return out


def airings(src, hits, now_ms, hours=24, max_channels=15):
    """Adds "airs": [(start_ms, end_ms)] to each hit that is on within `hours` (channels past the first `max_channels` are skipped)."""
    by_chan = {}
    for h in hits:
        by_chan.setdefault(h["cid"], []).append(h)
    for h in hits:
        h["airs"] = []
    for cid in list(by_chan)[:max_channels]:
        try:
            progs = src.timeline(cid, now_ms, now_ms + int(hours * 3600 * 1000))
        except Exception as ex:
            log.warning("search: no schedule for %s: %s", cid, ex)
            continue
        for h in by_chan[cid]:
            h["airs"] = [(p["start_ms"], p["end_ms"]) for p in progs if p["item"].get("id") == h["id"]]
    return hits
