"""Captions: the site's subtitle index -> SRT files that mpv can show.

lineup.json names an index (subtitles/index-<hash>.json) that maps a tape key to a cue file (subtitles/<hash>.json) with
cues {t0, t1 (ms into the file), en (text)}. About 170 tapes have them: mostly machine-translated anime and foreign commercials.
The tape key is the item's id, or "id/<file stem>" when the item names a file (the same rule as the site).
"""
import os
import re

import schedule as S

_FILE = re.compile(r"^subtitles/[0-9a-f]{16}\.json$")


def tape_key(item):
    if not item.get("file"):
        return item["id"]
    stem = re.sub(r"[^A-Za-z0-9]+", "-", re.sub(r"\.[^.]+$", "", item["file"])).strip("-")[:120]
    return f"{item['id']}/{stem}"


def _ts(ms):
    ms = int(ms)
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues):
    out, n = [], 0
    for c in cues:
        text = str(c.get("en") or "").strip()
        if not text or "t0" not in c or "t1" not in c:
            continue
        n += 1
        out.append(f"{n}\n{_ts(c['t0'])} --> {_ts(max(c['t1'], c['t0'] + 500))}\n{text}\n")
    return "\n".join(out)


class Subtitles:
    def __init__(self, cache_dir, lineup_getter):
        self.cache = cache_dir
        self.lineup = lineup_getter          # callable returning the current lineup dict
        self._index, self._index_path = None, None

    def _tapes(self):
        path = self.lineup().get("subtitles")
        if self._index is None or path != self._index_path:
            self._index_path, tapes = path, {}
            if isinstance(path, str) and re.match(r"^subtitles/index-[0-9a-f]{16}\.json$", path):
                data = S.fetch(path, self.cache)[0]
                for key, entry in (data.get("tapes") or {}).items():
                    if isinstance(entry, dict) and _FILE.match(str(entry.get("file"))):
                        tapes[key] = entry
            self._index = tapes
        return self._index

    def for_item(self, item):
        """(srt_path, entry) for an item, or None when it has no captions. May touch the network: call from a thread."""
        entry = self._tapes().get(tape_key(item))
        if not entry:
            return None
        name = entry["file"].split("/")[-1][:-5] + ".srt"
        folder = os.path.join(self.cache, "subs")
        path = os.path.join(folder, name)
        if not os.path.exists(path):
            cues = S.fetch(entry["file"], self.cache)[0].get("cues") or []
            os.makedirs(folder, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(to_srt(cues))
            os.replace(tmp, path)
        return path, entry
