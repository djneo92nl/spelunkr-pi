import hashlib
import json
import os
import tempfile
import unittest

from support import make_tv  # noqa: F401  (puts the project on sys.path)
import feeds

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>News</title>
<item><title>First &amp; best</title><link>https://example.com/1</link><pubDate>Wed, 04 Nov 2026 10:00:00 GMT</pubDate>
<description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;&lt;p&gt;Second para&lt;/p&gt;</description></item>
<item><title>Second</title><link>https://example.com/2</link></item></channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Atom news</title>
<entry><title>An entry</title><link rel="alternate" href="https://example.com/a"/><updated>2026-11-04T10:00:00Z</updated>
<summary>Short summary</summary></entry></feed>"""


class FeedTests(unittest.TestCase):
    def test_rss(self):
        e = feeds.parse(RSS)
        self.assertEqual([x["title"] for x in e], ["First & best", "Second"])
        self.assertEqual(e[0]["summary"], "Hello world\n\nSecond para")
        self.assertEqual(e[0]["link"], "https://example.com/1")
        self.assertIsNotNone(e[0]["published"])
        self.assertIsNone(e[1]["published"])

    def test_atom(self):
        e = feeds.parse(ATOM)
        self.assertEqual(e[0]["title"], "An entry")
        self.assertEqual(e[0]["link"], "https://example.com/a")
        self.assertEqual(e[0]["summary"], "Short summary")
        self.assertIsNotNone(e[0]["published"])

    def test_not_a_feed(self):
        with self.assertRaises(ValueError):
            feeds.parse(b"<html></html>")
        with self.assertRaises(ValueError):
            feeds.parse(b"not xml at all")

    def test_fetch_serves_stale_cache_when_the_network_fails(self):
        with tempfile.TemporaryDirectory() as d:
            url = "http://127.0.0.1:9/feed.xml"        # nothing listens here
            os.makedirs(os.path.join(d, "feeds"))
            fn = os.path.join(d, "feeds", hashlib.sha1(url.encode()).hexdigest() + ".json")
            with open(fn, "w") as f:
                json.dump([{"title": "cached", "summary": "", "link": "", "published": None}], f)
            os.utime(fn, (0, 0))                          # very old
            self.assertEqual(feeds.fetch(url, d, max_age=60, timeout=1)[0]["title"], "cached")
            with self.assertRaises(Exception):
                feeds.fetch("http://127.0.0.1:9/other.xml", d, timeout=1)


if __name__ == "__main__":
    unittest.main()
