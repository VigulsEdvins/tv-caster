import json
import os
import sys
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server


class TestTVFavorites(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.ThreadedServer(("127.0.0.1", 0), server.TVCasterHandler)
        cls.port = cls.httpd.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _post(self, path, payload):
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def _get(self, path):
        req = urllib.request.Request(f"{self.base_url}{path}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_favorites_crud_and_tv_payload(self):
        url = "https://kinogoo.zone/11180-papiny-dochki-novye-6-sezon.html"
        title = "Папины дочки. Новые (6 сезон)"

        # Ensure starting state
        if server.is_favorite(url):
            server.toggle_favorite(url)

        self.assertFalse(server.is_favorite(url))

        # Toggle to add
        is_fav = server.toggle_favorite(url, title=title, thumb="https://example.com/p.jpg")
        self.assertTrue(is_fav)
        self.assertTrue(server.is_favorite(url))

        # Check payload
        favs = server.build_tv_favorites_payload()
        matching = [f for f in favs if f["url"] == url]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["series_title"], "Папины дочки. Новые")

        # Test GET /api/favorites
        status, data = self._get("/api/favorites")
        self.assertEqual(status, 200)
        self.assertTrue(data.get("ok"))
        self.assertTrue(any(f["url"] == url for f in data.get("items", [])))

        # Test POST /api/tv/action toggle_favorite
        status, res = self._post("/api/tv/action", {"action": "toggle_favorite", "url": url})
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        self.assertFalse(res.get("is_favorite"))
        self.assertFalse(server.is_favorite(url))


if __name__ == "__main__":
    unittest.main()
