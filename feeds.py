"""RSS 2.0 / Atom reader for the teletext news pages. Standard library only.

    entries = fetch(url, cache_dir)       # [{title, summary, link, published}], newest first as the feed lists them

Feeds are cached on disk (default 15 minutes). If the network is down the last cached copy is served, however old.
"""
import hashlib
import html
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

UA = "spelunkr-pi-client/0.1 (personal, non-commercial)"
MAX_BYTES = 2_000_000
MAX_ENTRIES = 30

_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"(?i)</?(p|div|br|li|h[1-6]|tr|blockquote)[^>]*>")
_SPACE = re.compile(r"[ \t\r\f\v]+")


def strip_html(s):
    """Plain text from an HTML snippet: block tags become line breaks, entities are decoded, blank runs collapse."""
    s = _BLOCK.sub("\n", s or "")
    s = html.unescape(_TAG.sub("", s))
    lines = [_SPACE.sub(" ", ln).strip() for ln in s.replace("\xa0", " ").split("\n")]
    out = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child_text(el, *names):
    for c in el:
        if _local(c.tag) in names and (c.text or "").strip():
            return c.text.strip()
    return ""


def _date(s):
    """Seconds since the epoch from an RFC 822 or ISO 8601 date, or None."""
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        import datetime
        return datetime.datetime.fromisoformat(s.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse(xml_bytes):
    """Entries from an RSS or Atom document. Raises ValueError if it is neither."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as ex:
        raise ValueError(f"not XML: {ex}")
    kind = _local(root.tag)
    if kind == "rss" or kind == "RDF":
        items = [e for e in root.iter() if _local(e.tag) == "item"]
    elif kind == "feed":
        items = [e for e in root if _local(e.tag) == "entry"]
    else:
        raise ValueError("not an RSS or Atom feed")
    out = []
    for it in items[:MAX_ENTRIES]:
        link = _child_text(it, "link")
        if not link:                                       # Atom: <link href="..."/>
            for c in it:
                if _local(c.tag) == "link" and c.get("href") and c.get("rel", "alternate") == "alternate":
                    link = c.get("href")
                    break
        body = _child_text(it, "encoded", "content", "description", "summary")
        out.append({"title": strip_html(_child_text(it, "title")) or "(untitled)",
                    "summary": strip_html(body),
                    "link": link,
                    "published": _date(_child_text(it, "pubDate", "published", "updated", "date"))})
    return out


def feed_title(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    for el in root.iter():
        if _local(el.tag) == "title" and (el.text or "").strip():
            return strip_html(el.text)
    return ""


def fetch(url, cache_dir, max_age=900, timeout=15):
    """Entries for `url`, from the disk cache while fresh, else from the network, else from a stale cache."""
    os.makedirs(os.path.join(cache_dir, "feeds"), exist_ok=True)
    fn = os.path.join(cache_dir, "feeds", hashlib.sha1(url.encode()).hexdigest() + ".json")
    have = os.path.exists(fn)
    if have and time.time() - os.path.getmtime(fn) < max_age:
        with open(fn) as f:
            return json.load(f)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, text/xml, */*"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(MAX_BYTES)
        entries = parse(body)
    except Exception:
        if have:
            os.utime(fn, None)                              # back off: do not retry on every page view
            with open(fn) as f:
                return json.load(f)
        raise
    tmp = fn + ".tmp"
    with open(tmp, "w") as f:
        json.dump(entries, f)
    os.replace(tmp, fn)
    return entries
