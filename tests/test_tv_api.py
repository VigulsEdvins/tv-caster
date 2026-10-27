import json
import threading
import time
import unittest
import urllib.request
import urllib.error
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server


class TestTVApi(unittest.TestCase):
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

    def _get(self, path):
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8"), resp.headers.get("Content-Type", "")

    def _post(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_tv_page_route(self):
        status, body, ctype = self._get("/tv")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("TV Caster", body)

    def test_tv_state_endpoint(self):
        status, body, ctype = self._get("/api/tv/state")
        self.assertEqual(status, 200)
        self.assertIn("application/json", ctype)
        data = json.loads(body)
        self.assertIn("status", data)
        self.assertIn("title", data)
        self.assertIn("stream_url", data)
        self.assertIn("seasons", data)
        self.assertIn("translations", data)
        self.assertIn("qualities", data)
        self.assertIn("speeds", data)
        self.assertIn("opts", data)

    def test_tv_report_position(self):
        status, res = self._post("/api/tv/report", {
            "state": "PLAYING",
            "position": 125,
            "duration": 3600
        })
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        pos = server.tv_get_position()
        self.assertEqual(pos["state"], "PLAYING")
        self.assertEqual(pos["rel_time"], "00:02:05")
        self.assertEqual(pos["duration"], "01:00:00")

    def test_tv_action_set_speed(self):
        status, res = self._post("/api/tv/action", {
            "action": "set_speed",
            "speed": "1.5x"
        })
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        _, body, _ = self._get("/api/tv/state")
        data = json.loads(body)
        self.assertEqual(data["opts"].get("speed"), "1.5x")

    def test_tv_action_playback_commands(self):
        status, res = self._post("/api/tv/action", {
            "action": "pause"
        })
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        _, body, _ = self._get("/api/tv/state")
        data = json.loads(body)
        self.assertEqual(data.get("last_command", {}).get("type"), "pause")


if __name__ == "__main__":
    unittest.main()
