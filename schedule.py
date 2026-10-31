#!/usr/bin/env python3
"""Python port of spelunkr.tv's synchronized schedule (engine 5, no ad breaks yet).

Library:  now_playing(channel_lineup, manifests, platform, t_ms)
CLI:      python3 schedule.py computer-chronicles [--at ISO8601] [--no-clock-sync]

Ported from the site bundle (functions c0/Ua/ja/y0/b0/on/uo and the PRNG helpers).
Channels with ad breaks ("ads" in the manifest) raise NotImplementedError.
"""
import argparse
import datetime
import json
import math
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime

DATA = "https://data.spelunkr.tv/"
UA = "spelunkr-pi-client/0.1 (personal, non-commercial)"
M32 = 0xFFFFFFFF

# ---- timing constants (bundle: Nc, Os, Hi, vf, Hg, Fg, Wg, yf) ----
INTRO_MS = 3500
SLIDE_TAIL_MS = 1000
HOLD_MS = 1000
GAP_MS = 300
SLIDE_MIN_MS = 2500
SLIDE_BASE_MS = 1500
SLIDE_PER_CHAR_MS = 55
DEFAULT_CARD_MS = 10000


# ---- JS-compatible hashing / PRNG ----
def js_hash(s):
    """Fn(): h = (h<<5)-h+charCodeAt |0 over UTF-16 code units, returned signed."""
    h = 0
    b = s.encode("utf-16-le")
    for i in range(0, len(b), 2):
        h = (h * 31 + (b[i] | (b[i + 1] << 8))) & M32
    return h - (1 << 32) if h & 0x80000000 else h


def mulberry32(seed):
    """Wr(): mulberry32 seeded with `seed|0`; returns a float generator."""
    t = seed & M32

    def rnd():
        nonlocal t
        t = (t + 0x6D2B79F5) & M32
        n = ((t ^ (t >> 15)) * (1 | t)) & M32
        n ^= (n + (((n ^ (n >> 7)) * (61 | n)) & M32)) & M32
        return ((n ^ (n >> 14)) & M32) / 4294967296

    return rnd


def utf16_len(s):
    return len(s.encode("utf-16-le")) // 2


# ---- bump pools (bundle: Da, bf, wf) ----
def in_date_range(rng, t_ms):
    a, b = rng
    if len(a) > 4 and a[:4].isdigit() and a[4] == "-":
        def ms(day, end):
            d = datetime.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
            return int(d.timestamp() * 1000) + (86399999 if end else 0)
        return ms(a, False) <= t_ms <= ms(b, True)

    def md(u):
        m, d = u.split("-")
        return int(m) * 100 + int(d)

    now = datetime.datetime.fromtimestamp(t_ms / 1000, datetime.timezone.utc)
    o, c, l = now.month * 100 + now.day, md(a), md(b)
    return (c <= o <= l) if c <= l else (o >= c or o <= l)


def active_pools(bumps):
    live = [b for b in bumps if b.get("active") is not False]
    return live, [b for b in live if not b.get("date_range")], [b for b in live if b.get("date_range")]


def eligible(bumps, t_ms):
    live, year_round, seasonal = active_pools(bumps)
    if any(in_date_range(s["date_range"], t_ms) for s in seasonal):
        return [b for b in live if not b.get("date_range") or in_date_range(b["date_range"], t_ms)]
    return year_round


def pick_random(bumps, index, channel_id, seed, t_ms):
    """Mu(): platform bump, independent hash pick per break."""
    pool = eligible(bumps, t_ms)
    if not pool:
        return None
    r = mulberry32(js_hash(f"{channel_id}-break-{index}-{seed}"))
    return pool[int(r() * len(pool))]


def _deck_shuffle(pool, recent, seed):
    """zg(): shuffle, then push recently-played bumps toward the back of the new deck."""
    r = mulberry32(seed)
    s = list(pool)
    for l in range(len(s) - 1, 0, -1):
        u = int(r() * (l + 1))
        s[l], s[u] = s[u], s[l]
    half = len(s) // 2
    floor = {}
    for l in range(min(half, len(recent)), 0, -1):
        floor[recent[len(recent) - l]["id"]] = half + 1 - l

    if not floor:
        return s

    def fl(b):
        return floor.get(b["id"], 0)

    ids = lambda: [b["id"] for b in s]
    for l in range(1, min(half, len(recent)) + 1):
        u = recent[len(recent) - l]
        d = ids().index(u["id"]) if u["id"] in ids() else -1
        if d < 0 or d >= fl(u):
            continue
        cand = [p for p in range(fl(u), len(s)) if fl(s[p]) <= d]
        h = cand[int(r() * len(cand))]
        s[d], s[h] = s[h], s[d]
    return s


def new_deck(carry=None):
    if carry:
        return {"order": carry["order"], "pos": carry["pos"], "key": carry["key"],
                "recent": list(carry["recent"]), "rounds": 0}
    return {"order": [], "pos": 0, "key": None, "recent": [], "rounds": 0}


def deck_pick(deck, bumps, channel_id, seed, t_ms):
    """Yg(): draw next channel bump from a reshuffling deck (engine >= 5)."""
    pool = eligible(bumps, t_ms)
    if not pool:
        return None
    _, _, seasonal = active_pools(bumps)
    key = "|".join(s["id"] for s in seasonal if in_date_range(s["date_range"], t_ms)) if seasonal else ""
    if deck["key"] != key or deck["pos"] >= len(deck["order"]):
        deck["order"] = _deck_shuffle(pool, deck["recent"], js_hash(f"{channel_id}-deck-{seed}-{deck['rounds']}"))
        deck["rounds"] += 1
        deck["pos"] = 0
        deck["key"] = key
    b = deck["order"][deck["pos"]]
    deck["pos"] += 1
    deck["recent"].append(b)
    if len(deck["recent"]) > len(deck["order"]):
        deck["recent"].pop(0)
    return b


# ---- bump-break length (bundle: La, Fi, qg) ----
def slide_ms(text):
    return max(SLIDE_MIN_MS, SLIDE_BASE_MS + utf16_len(text) * SLIDE_PER_CHAR_MS)


def slides_ms(bump):
    return sum(slide_ms(s["text"]) for s in bump["slides"])


def break_ms(ch, plat):
    return (INTRO_MS + slides_ms(ch) + SLIDE_TAIL_MS + HOLD_MS + GAP_MS
            + slides_ms(plat) + SLIDE_TAIL_MS + HOLD_MS)



# ---- ad planner (bundle: e0, t0, n0, r0, s0, Nu, i0, o0) ----
LT = 1_000_000
AD_BREAK_EST_MS = 20_000      # ui
AD_GAP_MS = 20 * 60_000       # Lc
ROUND_MS = 336 * 3_600_000    # xu
MIN_ROUND_MS = 3_600_000      # Qg
RECENT_WINDOW_MS = 24 * 3_600_000  # _f
TOLERANCES = [3, 6]           # Zg
INF = float("inf")


def js_parse_int(v):
    """parseInt(v, 10) -> int or None."""
    if v is None:
        return None
    s = str(v).strip()
    sign = 1
    if s[:1] in "+-":
        sign = -1 if s[0] == "-" else 1
        s = s[1:]
    d = ""
    for ch in s:
        if ch.isdigit():
            d += ch
        else:
            break
    return sign * int(d) if d else None


def item_year(x):
    return js_parse_int(x.get("year")) if x else None


def lower_bound(a, v):
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) >> 1
        if a[mid] < v:
            lo = mid + 1
        else:
            hi = mid
    return lo


def nu(e, t, n):
    r = ((e % t) + t) % t
    return (int(math.floor((r * LT) / t)) + n) % LT


class AdPlan:
    def __init__(self, ads, lineup, block, gap_ms, channel_id):
        self.slices = ads["slices"]
        sl = self.slices
        hashes = [int(mulberry32(js_hash("ad-" + s["id"]))() * LT) for s in sl]
        order = sorted(range(len(sl)), key=lambda i: (hashes[i], sl[i]["id"], i))
        self.phases = [0] * len(sl)
        for rank, i in enumerate(order):
            self.phases[i] = (rank * LT) // len(sl)
        mean = sum(s["lengthMs"] for s in sl) / len(sl)
        self.rates = self._rates(lineup, block, gap_ms, mean)
        self.years = [item_year(s) for s in sl]
        dec = ads.get("decades")
        self.decade_key = f"d{dec}" if isinstance(dec, int) and dec >= 1 else None
        st = ads.get("start")
        st = st if isinstance(st, int) and 0 <= st < 1000 else (js_hash(f"ads-{channel_id}") & M32) % 1000
        self.start = st * (LT // 1000)
        self._tiers = {}
        self._tier = {}

    @staticmethod
    def _rates(items, block, gap_ms, slice_ms):
        s = i = o = c = 0
        by_year = {}
        for d, u in enumerate(items):
            f = u["duration"] * 1000
            s += f; i += f; o += f
            if block and d < len(items) - 1 and o < block["lengthMs"]:
                card = block.get("cardMs", DEFAULT_CARD_MS)
                s += card; i += card; o += card
                continue
            o = 0
            h = items[(d + 1) % len(items)]
            if u.get("offAir") is not True and h.get("offAir") is not True and i >= gap_ms:
                c += 1
                y = item_year(u)
                by_year[y] = by_year.get(y, 0) + 1
                s += AD_BREAK_EST_MS + slice_ms
                i = AD_BREAK_EST_MS / 2
            else:
                s += AD_BREAK_EST_MS
                i += AD_BREAK_EST_MS
        return {"cycleMs": s, "total": c, "byYear": by_year}

    def _p(self, g, v):
        key = (g, v)
        if key in self._tier:
            return self._tier[key]
        S = int(v[1:]) if isinstance(v, str) else None

        def y(x):
            if v == INF:
                return True
            if x is None or g is None:
                return False
            if S is not None:
                return abs(x // 10 - g // 10) <= S
            return abs(x - g) <= v

        w = [i for i in range(len(self.slices)) if self.slices[i].get("anywhere") is True or y(self.years[i])]
        w.sort(key=lambda i: (self.phases[i], self.slices[i]["id"], i))
        R = sum(n for yr, n in self.rates["byYear"].items() if y(yr))
        cyc = self.rates["cycleMs"]
        need = (R * ROUND_MS) / cyc if cyc > 0 else 0
        healthy = len(w) > 0 and len(w) >= need
        if healthy or len(w) == 0:
            rnd = ROUND_MS
        else:
            rnd = max(MIN_ROUND_MS, math.floor(len(w) * cyc / R)) if R else INF
        t = {"tol": v, "list": w, "places": [self.phases[i] for i in w], "roundMs": rnd, "healthy": healthy}
        self._tier[key] = t
        return t

    def tiers(self, g):
        if g in self._tiers:
            return self._tiers[g]
        if g is None:
            v = [self._p(None, INF)]
        else:
            v = [self._p(g, S) for S in TOLERANCES + ([self.decade_key] if self.decade_key else []) + [INF]]
        k = next(i for i, t in enumerate(v) if t["healthy"] or t["tol"] == INF)
        self._tiers[g] = v[k:]
        return self._tiers[g]

    def pick(self, recent, year, r, s):
        """o0(): choose a slice index for a break ending-the-show at time r, window start s."""
        if len(recent) >= len(self.phases):
            recent.clear()
        tiers = [t for t in self.tiers(year) if t["list"]]

        def take(i):
            recent.add(i)
            return i

        cs = []
        for l in tiers:
            u = len(l["list"])
            d = nu(s, l["roundMs"], self.start)
            f = nu(r, l["roundMs"], self.start)
            h = 0 if r <= s else (LT if r - s >= l["roundMs"] else (f - d + LT) % LT)
            p = int(math.floor((min(RECENT_WINDOW_MS, l["roundMs"]) * LT) / l["roundMs"]))
            m = lower_bound(l["places"], d + 1)
            cs.append(m)
            for g in range(u):
                v = (m + g) % u
                if ((l["places"][v] - d + LT) % LT or LT) > h:
                    break
                if l["list"][v] not in recent:
                    return take(l["list"][v])
            for g in range(1, u + 1):
                v = (m - g + u * 2) % u
                if (d - l["places"][v] + LT) % LT >= p:
                    break
                if l["list"][v] not in recent:
                    return take(l["list"][v])
        for li, l in enumerate(tiers):
            d = len(l["list"])
            for f in range(1, d + 1):
                h = l["list"][(cs[li] - f + d * 2) % d]
                if h not in recent:
                    return take(h)
        return -1

# ---- cycle builder (bundle: c0 + Ua) ----
class Version:
    """One lineup version of a channel plus its manifest."""

    def __init__(self, channel_id, lineup_v, manifest, platform):
        self.channel_id = channel_id
        self.v = lineup_v["v"]
        self.epoch_ms = lineup_v["epochMs"]
        self.effective_ms = lineup_v.get("effectiveAtMs", self.epoch_ms)
        self.start_index = lineup_v.get("startIndex", 0)
        self.items = manifest["items"]
        self.channel_bumps = [b for b in manifest.get("bumps", []) if b.get("active") is not False]
        self.platform_bumps = [b for b in (platform or {}).get("bumps", []) if b.get("active") is not False]
        self.block = manifest.get("block")
        ads = manifest.get("ads")
        self.ads = ads if ads and ads.get("slices") else None
        self.ad_gap_ms = (ads or {}).get("gapMs") if isinstance((ads or {}).get("gapMs"), int) else AD_GAP_MS
        self._ad_plan = None
        self._ad_carry = {}   # cycle -> {"recent": [[slice_idx, ms]], "fromMs": int}
        self.deck_mode = manifest.get("engine", 1) >= 5
        self.cycles = []     # [{start_ms, length_ms, entries}]
        self._deck_carry = {}  # cycle -> carry for that cycle

    def _build_cycle(self, k, start_ms):
        first = self.start_index if k == 0 else 0
        items = self.items[first:]
        have_bumps = bool(self.channel_bumps and self.platform_bumps)
        seed = f"{self.v}.{k}"
        deck = new_deck(self._deck_carry.get(k)) if (have_bumps and self.deck_mode) else None
        ads = self.ads if have_bumps else None
        if ads and self._ad_plan is None:
            self._ad_plan = AdPlan(ads, self.items, self.block, self.ad_gap_ms, self.channel_id)
        carry = self._ad_carry.get(k) if ads else None
        recent_set = {i for i, _ in (carry or {}).get("recent", []) if 0 <= i < (len(ads["slices"]) if ads else 0)}
        S = carry["fromMs"] if carry else None
        ad_log = []
        since_break = 0     # "$" in the bundle: programming ms since the last break
        entries, f, h = [], start_ms, 0
        video_idx = first
        block = self.block
        for K, it in enumerate(items):
            U = first + K
            dur = it["duration"] * 1000
            entries.append({"type": "video", "item": it, "videoIndex": video_idx, "durMs": dur})
            video_idx += 1
            f += dur
            h += dur
            since_break += dur
            if not have_bumps:
                continue
            if block and K < len(items) - 1 and h < block["lengthMs"]:
                stings = block.get("stings") or []
                if stings:
                    r = mulberry32(js_hash(f"{self.channel_id}-card-{U}-{seed}"))
                    sting = stings[int(r() * len(stings))]
                    ee = sting["lengthMs"]
                else:
                    sting, ee = None, block.get("cardMs", DEFAULT_CARD_MS)
                entries.append({"type": "up-next", "breakIndex": U, "sting": sting, "durMs": ee})
                f += ee
                h += ee
                since_break += ee
                continue
            h = 0
            if deck is not None:
                H = deck_pick(deck, self.channel_bumps, self.channel_id, seed, f)
            else:
                H = pick_random(self.channel_bumps, U, self.channel_id, seed, f)
            Z = pick_random(self.platform_bumps, U, "spelunkr", seed, f)
            if H and Z:
                nxt = items[K + 1] if K < len(items) - 1 else self.items[0]
                if ads and it.get("offAir") is not True and nxt.get("offAir") is not True \
                        and since_break >= self.ad_gap_ms:
                    bi = self._ad_plan.pick(recent_set, item_year(it), f, S if S is not None else f - since_break)
                    S = None
                    ad_log.append([bi, f])
                    sl = ads["slices"][bi]
                    z = INTRO_MS + slides_ms(H) + SLIDE_TAIL_MS + HOLD_MS
                    q = slides_ms(Z) + SLIDE_TAIL_MS + HOLD_MS
                    entries.append({"type": "bump-break", "half": "open", "breakIndex": U,
                                    "channelBump": H, "durMs": z})
                    entries.append({"type": "ad", "breakIndex": U, "slice": sl, "sliceIndex": bi,
                                    "durMs": sl["lengthMs"]})
                    entries.append({"type": "bump-break", "half": "close", "breakIndex": U,
                                    "platformBump": Z, "durMs": q})
                    f += z + sl["lengthMs"] + q
                    since_break = q
                    ad_from = f
                    continue
                ee = break_ms(H, Z)
                entries.append({"type": "bump-break", "breakIndex": U, "channelBump": H,
                                "platformBump": Z, "durMs": ee})
                f += ee
                since_break += ee
        if ads:
            from_ms = ad_from if ad_log else (S if S is not None else start_ms)
            keep = f - RECENT_WINDOW_MS
            self._ad_carry.setdefault(k + 1, {
                "recent": [r for r in (carry or {}).get("recent", []) + ad_log if r[1] >= keep],
                "fromMs": from_ms})
        if deck is not None:
            self._deck_carry.setdefault(k + 1, {k_: deck[k_] for k_ in ("order", "pos", "key", "recent")})
        off = 0
        for e in entries:
            e["offMs"] = off
            off += e["durMs"]
        return {"start_ms": start_ms, "length_ms": off, "entries": entries}

    def cycle_at(self, t_ms):
        if t_ms < self.epoch_ms:
            raise NotImplementedError("time is before this version's epoch (site builds backwards cycles)")
        if not self.cycles:
            self.cycles.append(self._build_cycle(0, self.epoch_ms))
        i = 0
        while True:
            c = self.cycles[i]
            if t_ms < c["start_ms"] + c["length_ms"]:
                return i, c
            i += 1
            if i == len(self.cycles):
                self.cycles.append(self._build_cycle(i, c["start_ms"] + c["length_ms"]))


# ---- lookup (bundle: uo, v0, on) ----
_VERSIONS = {}                      # built Version objects, keyed by (channel, version, epoch, startIndex)
_LOCK = threading.RLock()           # the TV loop and the guide's loader thread both call now_playing()


def forget(keep_channel=None):
    """Free built schedules (they are big for ad channels), except for one channel."""
    with _LOCK:
        for k in [k for k in _VERSIONS if k[0] != keep_channel]:
            del _VERSIONS[k]


def now_playing(channel, manifests, platforms, t_ms):
    """channel: lineup channel dict; manifests / platforms: {v: json} (each manifest names its own platform file)."""
    with _LOCK:
        return _now_playing(channel, manifests, platforms, t_ms, _VERSIONS)


def timeline(channel, manifests, platforms, t0_ms, t1_ms):
    """Shows on air between t0 and t1 (the one already playing at t0 included): [{start_ms, end_ms, item}]."""
    out, t = [], t0_ms
    for _ in range(600):
        if t >= t1_ms:
            break
        r = now_playing(channel, manifests, platforms, t)
        if r["entry"]["type"] == "video":
            out.append({"start_ms": r["start_ms"], "end_ms": r["end_ms"], "item": r["entry"]["item"]})
        t = int(r["end_ms"]) + 1
    return out


def _now_playing(channel, manifests, platforms, t_ms, _cache):
    versions = sorted(channel["versions"], key=lambda x: x["v"])
    cur = None
    for lv in reversed(versions):
        if lv["effectiveAtMs"] <= t_ms:
            cur = lv
            break
    if cur is None:
        cur = min(versions, key=lambda x: (x["effectiveAtMs"], -x["v"]))
    ck = (channel["id"], cur["v"], cur["epochMs"], cur.get("startIndex", 0))
    if ck not in _cache:
        _cache[ck] = Version(channel["id"], cur, manifests[cur["v"]], platforms[cur["v"]])
    ver = _cache[ck]
    k, cyc = ver.cycle_at(t_ms)
    rel = t_ms - cyc["start_ms"]
    entries = cyc["entries"]
    e = entries[0]
    for cand in entries:
        if cand["offMs"] <= rel:
            e = cand
        else:
            break
    start = cyc["start_ms"] + e["offMs"]
    end = start + e["durMs"]
    later = [v["effectiveAtMs"] for v in versions if v["v"] > cur["v"] and v["effectiveAtMs"] > t_ms]
    if later and min(later) < end:
        end = min(later)
    return {"entry": e, "version": ver.v, "cycle": k, "offset_s": (t_ms - start) / 1000,
            "start_ms": start, "end_ms": end, "remaining_s": (end - t_ms) / 1000}


def media_url(item):
    name = item.get("file") or item.get("mp4")
    return f"https://archive.org/download/{item['id']}/{urllib.parse.quote(name, safe=chr(33)+'~*()'+chr(39))}"


def ad_url(sl):
    return f"https://archive.org/download/{sl['tape']}/{urllib.parse.quote(sl['file'], safe=chr(33)+'~*()'+chr(39))}"


# ---- fetching with a disk cache (CLI / test mode) ----
def fetch(path, cache_dir, max_age=None, timeout=20):
    """Return (json, http_date_or_None). Serves the disk cache while fresh; if the network fails
    and any cached copy exists, serves that (stale) instead of raising."""
    url = path if path.startswith("http") else DATA + path
    fn = os.path.join(cache_dir, urllib.parse.quote(path, safe=""))
    have = os.path.exists(fn)
    if have and (max_age is None or time.time() - os.path.getmtime(fn) < max_age):
        with open(fn) as f:
            return json.load(f), None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            date = r.headers.get("Date")
        data = json.loads(body)
    except Exception:
        if have:
            os.utime(fn, None)   # back off: don't retry on every call
            with open(fn) as f:
                return json.load(f), None
        raise
    os.makedirs(cache_dir, exist_ok=True)
    tmp = fn + ".tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, fn)
    return data, date


def upcoming(channel, manifests, platforms, t_ms, count=3):
    """Next `count` shows (video entries) after the one playing at t_ms: [{start_ms, item}]."""
    out, t = [], t_ms
    for _ in range(count * 12 + 12):
        r = now_playing(channel, manifests, platforms, t)
        t = int(r["end_ms"]) + 1
        if r["entry"]["type"] == "video" and r["start_ms"] > t_ms:
            out.append({"start_ms": r["start_ms"], "item": r["entry"]["item"]})
            if len(out) >= count:
                break
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("channel", nargs="?")
    ap.add_argument("--at", help="ISO8601 UTC time instead of now")
    ap.add_argument("--cache", default=os.path.expanduser("~/.cache/spelunkr"))
    ap.add_argument("--no-clock-sync", action="store_true")
    a = ap.parse_args()

    lineup, date = fetch("lineup.json", a.cache, max_age=300)
    if not a.channel:
        print("\n".join(c["id"] for c in lineup["channels"]))
        return
    ch = next((c for c in lineup["channels"] if c["id"] == a.channel), None)
    if not ch:
        sys.exit(f"unknown channel {a.channel}")
    manifests = {v["v"]: fetch(v["manifest"], a.cache)[0] for v in ch["versions"]}
    platforms = {v: fetch(m["platform"], a.cache)[0] for v, m in manifests.items()}

    t_ms = time.time() * 1000
    if a.at:
        t_ms = datetime.datetime.fromisoformat(a.at.replace("Z", "+00:00")).timestamp() * 1000
    elif date and not a.no_clock_sync:
        skew = parsedate_to_datetime(date).timestamp() * 1000 - t_ms
        if abs(skew) > 2000:
            t_ms += skew
            print(f"(clock skew {skew/1000:+.1f}s applied, as the site does)", file=sys.stderr)
    t_ms = int(t_ms)

    r = now_playing(ch, manifests, platforms, t_ms)
    e = r["entry"]
    when = datetime.datetime.fromtimestamp(t_ms / 1000, datetime.timezone.utc).isoformat()
    print(f"{a.channel} @ {when}  (lineup v{r['version']}, cycle {r['cycle']})")
    if e["type"] == "video":
        it = e["item"]
        seek = r["offset_s"] + (it.get("inS") or 0)
        print(f"VIDEO  #{e['videoIndex']}  {it['title']}  ({it.get('year', '?')})")
        print(f"  offset into slot: {r['offset_s']:.1f}s   seek in file: {seek:.1f}s   remaining: {r['remaining_s']:.1f}s")
        print(f"  url: {media_url(it)}")
    elif e["type"] == "ad":
        sl = e["slice"]
        print(f"AD  slice #{e['sliceIndex']}  ({sl.get('year', '?')})  offset {r['offset_s']:.1f}s of {e['durMs']/1000:.1f}s")
        print(f"  seek in file: {sl['inMs']/1000 + r['offset_s']:.1f}s   remaining: {r['remaining_s']:.1f}s")
        print(f"  url: {ad_url(sl)}")
    elif e["type"] == "bump-break" and e.get("half"):
        bump = e.get("channelBump") or e.get("platformBump")
        print(f"AD BREAK ({e['half']} bumper)  offset {r['offset_s']:.1f}s of {e['durMs']/1000:.1f}s")
        print("  " + " / ".join(x["text"] for x in bump["slides"]))
    elif e["type"] == "bump-break":
        print(f"BUMP BREAK  offset {r['offset_s']:.1f}s of {e['durMs']/1000:.1f}s, remaining {r['remaining_s']:.1f}s")
        print(f"  channel bump {e['channelBump']['id']}: " + " / ".join(s["text"] for s in e["channelBump"]["slides"]))
        print(f"  platform bump {e['platformBump']['id']}: " + " / ".join(s["text"] for s in e["platformBump"]["slides"]))
    else:
        print(f"UP-NEXT CARD  offset {r['offset_s']:.1f}s, remaining {r['remaining_s']:.1f}s")


if __name__ == "__main__":
    main()
