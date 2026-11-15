import json
import os
import sys
import threading
import time
import unittest
import urllib.request
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server


class TestLocalVideo(unittest.TestCase):
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

    def _get(self, path, headers=None):
        req = urllib.request.Request(f"{self.base_url}{path}", headers=headers or {})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(), dict(resp.headers)

    def _head(self, path, headers=None):
        req = urllib.request.Request(f"{self.base_url}{path}", headers=headers or {}, method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(), dict(resp.headers)

    def _post(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_resolve_local_path(self):
        expected = os.path.expanduser("~/elga/combined_video.mp4")
        if not os.path.exists(expected):
            self.skipTest(f"{expected} not present on system")

        self.assertEqual(server.resolve_local_path("elga/combined_video.mp4"), expected)
        self.assertEqual(server.resolve_local_path("~/elga/combined_video.mp4"), expected)
        self.assertEqual(server.resolve_local_path(expected), expected)
        self.assertEqual(server.resolve_local_path(f"file://{expected}"), expected)
        self.assertIsNone(server.resolve_local_path("non_existent_movie_12345.mp4"))

    def test_is_local_file(self):
        self.assertTrue(server.is_local_file("elga/combined_video.mp4"))
        self.assertFalse(server.is_local_file("https://youtube.com/watch?v=123"))
        self.assertFalse(server.is_local_file("random_query_that_is_not_file"))

    def test_inspect_local_file(self):
        info = server.inspect_url("elga/combined_video.mp4")
        self.assertTrue(info.get("ok"))
        self.assertTrue(info.get("is_local"))
        self.assertEqual(info.get("title"), "combined_video.mp4")
        self.assertEqual(info.get("duration"), 1683.0)
        self.assertIn("Оригинал", info.get("qualities", []))
        self.assertTrue(info.get("thumb", "").startswith("/api/thumb/"))

    def test_search_local_video(self):
        # Search by keyword "combined"
        res = server.search_local_videos("combined")
        self.assertTrue(any("combined_video.mp4" in r["title"] for r in res))
        self.assertTrue(all(r.get("is_local") for r in res))

        # Search by directory name "elga"
        res_elga = server.search_local_videos("elga")
        self.assertTrue(len(res_elga) > 0)

        # Search via API endpoint /api/search?q=combined
        status, raw, _ = self._get("/api/search?q=combined")
        self.assertEqual(status, 200)
        data = json.loads(raw.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertTrue(any("combined_video.mp4" in r["title"] for r in data.get("results", [])))

    def test_browse_local_dir_api(self):
        status, raw, _ = self._get("/api/local/browse?path=~/elga")
        self.assertEqual(status, 200)
        data = json.loads(raw.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        file_names = [f["name"] for f in data.get("files", [])]
        self.assertIn("combined_video.mp4", file_names)
        self.assertTrue(len(data.get("quick", [])) > 0)

    def test_find_local_video_api(self):
        status, raw, _ = self._get("/api/local/find?name=combined_video.mp4")
        self.assertEqual(status, 200)
        data = json.loads(raw.decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("path", "").endswith("combined_video.mp4"))

    def test_local_stream_range_and_head(self):
        path = os.path.expanduser("~/elga/combined_video.mp4")
        total_size = os.path.getsize(path)

        # HEAD request
        status, body, headers = self._head(f"/local/stream.mp4?p={urllib.parse.quote(path)}")
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 0)
        self.assertEqual(headers.get("Content-Type"), "video/mp4")
        self.assertEqual(int(headers.get("Content-Length")), total_size)
        self.assertEqual(headers.get("Accept-Ranges"), "bytes")

        # Range request: bytes 1000-1999 (1000 bytes)
        status, body, headers = self._get(
            f"/local/stream.mp4?p={urllib.parse.quote(path)}",
            headers={"Range": "bytes=1000-1999"}
        )
        self.assertEqual(status, 206)
        self.assertEqual(len(body), 1000)
        self.assertEqual(headers.get("Content-Range"), f"bytes 1000-1999/{total_size}")
        self.assertEqual(headers.get("Content-Length"), "1000")

        # Verify binary slice matches disk file exactly
        with open(path, "rb") as f:
            f.seek(1000)
            expected_slice = f.read(1000)
        self.assertEqual(body, expected_slice)

    def test_cast_local_video(self):
        sid, _ = server.new_session()
        server.active_sessions[sid]["local_file"] = os.path.expanduser("~/elga/combined_video.mp4")
        status, body, headers = self._get(
            f"/local/stream.mp4?v={sid}",
            headers={"Range": "bytes=0-99"}
        )
        self.assertEqual(status, 206)
        self.assertEqual(len(body), 100)

        # Trigger cast via API
        status, res = self._post("/api/cast", {"url": "elga/combined_video.mp4"})
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("message"), "Cast initiated")

        # Give background thread a fraction of second to set state
        time.sleep(0.5)
        self.assertIn("combined_video.mp4", server.app_state.get("title", ""))
        self.assertIn("/stream.ts", server.tv_player_state.get("stream_url", ""))

        # Clean up cast session
        sid = server.current_session_id
        if sid in server.active_sessions:
            server.cancel_session(server.active_sessions[sid])
        time.sleep(0.5)
        server.app_state["url"] = ""
        server.app_state["title"] = ""
        server.tv_player_state["duration"] = 0
        server.tv_player_state["position"] = 0
        server.tv_player_state["state"] = "STOPPED"


if __name__ == "__main__":
    unittest.main()
