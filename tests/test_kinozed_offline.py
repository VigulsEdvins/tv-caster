"""kinozed resolving against recorded responses (tests/test_kinozed.py hits the live site)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import netfix
import server

URL = "https://hd.kinozed.org/movie/ulica-straha-koroleva-vypusknogo-2025/"


class KinozedOffline(unittest.TestCase):
    def setUp(self):
        server._kinozed_url_cache.clear()
        server._kinogo_url_cache.clear()

    @netfix.offline
    def test_kinozed_page_resolves_to_nextembed_kp(self):
        self.assertEqual(server.normalize_url(URL), "https://api.nextembed.ws/embed/kp/5516260")

    @netfix.offline
    def test_kinozed_inspect(self):
        info = server.inspect_url(URL)
        self.assertEqual((info["ok"], info["kind"]), (True, "movie"))
        self.assertIn("Улица страха", info["title"])
        self.assertGreaterEqual(len(info["translations"]), 3)
        self.assertEqual(info["thumb"], "https://st.kp.yandex.net/images/film_iphone/iphone360_5516260.jpg")


if __name__ == "__main__":
    unittest.main()
