import json
import os
import sys
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server


class TestTVHistory(unittest.TestCase):
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

    def test_build_tv_history_deduplicates_series_and_tracks_episodes(self):
        sample = [
            {
                "title": "Король Талсы 2 сезон 6 серия [720p] (LostFilm)",
                "url": "https://tulsa-king.net/1-lostfilm/1-season/1-episode",
                "episode": "s02e06",
                "position": 1200,
                "duration": 2400,
                "timestamp": 200,
                "quality": "720p",
                "translation_id": "1-lostfilm",
                "translation_title": "LostFilm",
            },
            {
                "title": "Король Талсы 2 сезон 5 серия [720p] (LostFilm)",
                "url": "https://tulsa-king.net/1-lostfilm/1-season/1-episode",
                "episode": "s02e05",
                "position": 2350,
                "duration": 2400,
                "timestamp": 100,
                "thumb": "https://example.com/tulsa.jpg",
            },
            {
                "title": "Папины дочки. Новые – 6 сезон, 1 серия",
                "url": "https://mover.uz/watch/WMvD0sOO",
                "position": 300,
                "duration": 1500,
                "timestamp": 150,
                "thumb": "https://example.com/papiny.jpg",
            },
        ]
        items = server.build_tv_history_payload(raw_history=sample)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["episode"], "s02e06")
        self.assertEqual(first["series_title"], "Король Талсы")
        self.assertEqual(first["thumb"], "https://example.com/tulsa.jpg")
        self.assertEqual(first["progress_pct"], 50)
        self.assertIn("s02e06", first["watched_episodes"])
        self.assertIn("s02e05", first["watched_episodes"])

    def test_tv_state_includes_history_and_endpoint_works(self):
        with urllib.request.urlopen(f"{self.base_url}/api/tv/history", timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertTrue(data.get("ok"))
            self.assertIsInstance(data.get("items"), list)

        with urllib.request.urlopen(f"{self.base_url}/api/tv/state", timeout=5) as resp:
            state = json.loads(resp.read().decode("utf-8"))
            self.assertIn("history", state)
            self.assertIsInstance(state["history"], list)


if __name__ == "__main__":
    unittest.main()
