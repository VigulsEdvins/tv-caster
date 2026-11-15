"""Source parsers against saved page copies (tests/fixtures/pages.json): no network, so a site redesign cannot break the suite.
To refresh a fixture, re-fetch the page and replace the entry (big pages are trimmed to the part the code reads)."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server
from extractors import rezka

PAGES = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "pages.json"), encoding="utf-8"))


def _page(url, *a, **kw):
    if url not in PAGES:
        raise AssertionError("page not in fixtures: " + url)
    return PAGES[url]


class OfflineSources(unittest.TestCase):
    def setUp(self):
        server._kinogo_url_cache.clear()
        server._series_cache.clear()
        for target, name in ((server, "fetch_page"), (server, "curl_fetch"), (rezka, "fetch_page")):
            p = mock.patch.object(target, name, _page)
            p.start()
            self.addCleanup(p.stop)

    def test_nextembed_series_with_spaced_seasons_key(self):
        # regression: the site changed "seasons:[" to "seasons: [" and every nextembed series stopped parsing
        info = server.inspect_url("https://api.nextembed.ws/embed/imdb/tt0903747")
        self.assertEqual((info["kind"], info["title"]), ("series", "Во все тяжкие"))
        self.assertEqual([len(s["episodes"]) for s in info["seasons"]], [3, 3])

    def test_kinogo_page_resolves_to_nextembed_movie(self):
        url = "https://kinogo2026.com/12206--sokrovische-nacii.html"
        self.assertEqual(server.normalize_url(url), "https://api.nextembed.ws/embed/movie/575")
        info = server.inspect_url(url)
        self.assertEqual((info["kind"], info["title"]), ("movie", "Сокровище нации"))
        self.assertEqual(len(info["translations"]), 9)

    def test_gidonline_movie_via_kinogram(self):
        info = server.inspect_url("https://ag.gidonline.fun/film/sokrovishhe-nacii/")
        self.assertEqual((info["kind"], info["title"]), ("movie", "Сокровище нации"))
        self.assertEqual(len(info["translations"]), 9)

    def test_moy_su_movie(self):
        info = server.inspect_url("https://kinogo-hd.moy.su/load/boeviki/sokrovishhe_nacii_3/10-1-0-1924")
        self.assertEqual(info["kind"], "movie")
        self.assertIn("Сокровище нации 2", info["title"])
        self.assertEqual(len(info["translations"]), 8)

    def test_rezka_series_structure(self):
        info = server.inspect_url("https://tulsa-king.net/1-lostfilm/1-season/1-episode")
        self.assertEqual((info["kind"], info["title"]), ("series", "Король Талсы"))
        self.assertEqual([len(s["episodes"]) for s in info["seasons"]], [9, 10, 10, 4])
        self.assertEqual(len(info["translations"]), 12)
        self.assertEqual(info["seasons"][0]["episodes"][0]["id"], "s01e01")


if __name__ == "__main__":
    unittest.main()
