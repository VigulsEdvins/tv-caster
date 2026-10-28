import base64
import json
import os
import sys
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server


class TestDirectStreamMode(unittest.TestCase):
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

    def test_rewrite_hls_playlist_variants_and_segments(self):
        raw_master = (
            "#EXTM3U\n"
            "#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"audio\",NAME=\"Original\",URI=\"audio/en.m3u8\"\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720\n"
            "720p/index.m3u8\n"
        )
        rewritten = server.rewrite_hls_playlist(
            raw_master, "https://cdn.example.com/hls/master.m3u8", session_id=101
        )
        self.assertIn("/direct/hls.m3u8?v=101&u=", rewritten)
        self.assertEqual(rewritten.count("/direct/hls.m3u8?v=101&u="), 2)

        raw_media = (
            "#EXTM3U\n"
            "#EXTINF:6.0,\n"
            "seg001.ts\n"
            "#EXTINF:6.0,\n"
            "https://other.cdn.com/seg002.ts\n"
        )
        rewritten_media = server.rewrite_hls_playlist(
            raw_media, "https://cdn.example.com/hls/720p/index.m3u8", session_id=101
        )
        self.assertIn("/direct/stream?v=101&u=", rewritten_media)
        self.assertEqual(rewritten_media.count("/direct/stream?v=101&u="), 2)

    def test_start_direct_stream_populates_tv_state(self):
        sid, _ = server.new_session()
        server.start_direct_stream(
            sid,
            title="Direct Test Movie",
            stream_url="https://cdn.example.com/movie.mp4",
            headers={"Referer": "https://rezka.ag/"},
            kind="mp4",
            speed="1.25x",
            start_at=45,
        )
        status, raw, _ = self._get("/api/tv/state")
        self.assertEqual(status, 200)
        state = json.loads(raw.decode("utf-8"))
        self.assertTrue(state.get("direct_stream"))
        self.assertIn(f"/direct/stream?v={sid}", state.get("direct_url", ""))
        self.assertEqual(state.get("cdn_url"), "https://cdn.example.com/movie.mp4")
        self.assertEqual(state.get("direct_headers", {}).get("Referer"), "https://rezka.ag/")

    def test_is_mover_and_inspect_mover(self):
        self.assertTrue(server.is_mover("https://mover.uz/watch/WMvD0sOO"))
        self.assertTrue(server.is_mover("https://mover.uz/video/embed/WMvD0sOO"))
        self.assertFalse(server.is_mover("https://rezka.ag/films/123.html"))

    def test_direct_stream_set_speed_and_seek_do_not_respawn_cast(self):
        sid, _ = server.new_session()
        server.app_state["url"] = "https://rezka.ag/films/test.html"
        server.start_direct_stream(
            sid,
            title="Speed Test Movie",
            stream_url="https://cdn.example.com/movie.mp4",
            headers={},
            kind="mp4",
            speed="1.0x",
            start_at=0,
        )
        status, res = self._post("/api/tv/action", {"action": "set_speed", "speed": "1.5x"})
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        self.assertEqual(server.current_session_id, sid)
        self.assertEqual(server.tv_player_state["last_command"]["type"], "speed")

        status, res = self._post("/api/tv/action", {"action": "seek", "target": 300, "from_tv": True})
        self.assertEqual(status, 200)
        self.assertTrue(res.get("ok"))
        self.assertEqual(server.current_session_id, sid)
        self.assertEqual(server.tv_player_state["position"], 300.0)


if __name__ == "__main__":
    unittest.main()
