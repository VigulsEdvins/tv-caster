import base64
import json
import os
import sys
import threading
import unittest
from unittest import mock
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
        self.assertTrue(server.is_nextembed("https://kinogoo.zone/11180-papiny-dochki-novye-6-sezon.html"))
        self.assertEqual(server._ep_num("1-2"), 1)

    def test_resolve_kinogo_url_and_inspect(self):
        kinogo_url = "https://go3.kinogo.cc/119352-cuzaia-zizn-zizn-drugix-baskalarinin-hayati-kinogo-2026.html"
        norm = server.normalize_url(kinogo_url)
        self.assertTrue(server.is_nextembed(norm))
        fake = {"title": "Чужая жизнь", "kind": "series", "translations": [], "thumb": "https://img.example/p.jpg",
                "seasons": [{"id": "s01", "title": "Сезон 1", "episodes": [{"id": "s01e%02d" % n, "title": f"Серия {n}"} for n in range(1, 7)]}]}
        # live site markup changes often, so the structure is stubbed and only the inspect wiring is checked
        with mock.patch.object(server, "nextembed_structure", return_value=fake):
            info = server.inspect_url(kinogo_url)
        self.assertTrue(info.get("ok"))
        self.assertEqual(info.get("kind"), "series")
        self.assertIn("Чужая жизнь", info.get("title", ""))
        self.assertTrue(len(info.get("seasons", [])) >= 1)
        self.assertEqual(len(info["seasons"][0]["episodes"]), 6)
        self.assertTrue(info.get("thumb", "").startswith("http"))

    def test_is_sportacentrs_and_extract(self):
        url = "https://sportacentrs.com/sportacentrs_tv/live/29102026-b_ezerzeme_du_homelike_robezsardze_rsu_b_"
        self.assertTrue(server.is_sportacentrs(url))
        self.assertTrue(server.is_sportacentrs("https://live.tiesraides.lv/sportacentrs.tv/live/6?preroll=53"))
        self.assertTrue(server.is_sportacentrs("https://cdn.tiesraides.lv/sportacentrs.com/test"))
        self.assertFalse(server.is_sportacentrs("https://youtube.com/watch?v=123"))

        info = server.inspect_url(url)
        self.assertTrue(info.get("ok"))
        self.assertEqual(info.get("kind"), "live")
        self.assertIn("Ezerzeme", info.get("title", ""))
        self.assertIn("720p", info.get("qualities", []))
        self.assertTrue(info.get("thumb", "").startswith("http"))

    def test_is_facebook_and_extract(self):
        fb_url = "https://www.facebook.com/vklotos/videos/2142310449990727/"
        self.assertTrue(server.is_facebook(fb_url))
        self.assertTrue(server.is_facebook("https://fb.watch/xyz123/"))
        self.assertFalse(server.is_facebook("https://youtube.com/watch?v=123"))

        info = server.inspect_url(fb_url)
        self.assertTrue(info.get("ok"))
        self.assertIn("Čempionats", info.get("title", ""))
        self.assertIn("720p", info.get("qualities", []))
        self.assertTrue(bool(info.get("thumb")))

    def test_start_direct_stream_dash_and_proxy(self):
        sid, _ = server.new_session()
        server.start_direct_stream(
            sid,
            title="DASH Test",
            stream_url="https://video.example.com/live/stream.mpd",
            headers={"User-Agent": "Mozilla/5.0"},
            kind="dash",
            speed="1.0x",
            start_at=0,
        )
        status, raw, _ = self._get("/api/tv/state")
        self.assertEqual(status, 200)
        state = json.loads(raw.decode("utf-8"))
        self.assertTrue(state.get("direct_stream"))
        self.assertEqual(state.get("direct_kind"), "dash")
        self.assertIn(f"/direct/dash.mpd?v={sid}", state.get("direct_url", ""))
        self.assertEqual(state.get("cdn_url"), "https://video.example.com/live/stream.mpd")

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

    def test_gidonline_extraction_and_inspect(self):
        url = "https://ag.gidonline.fun/film/sokrovishhe-nacii/"
        self.assertTrue(server.is_nextembed(url))
        info = server.inspect_url(url)
        self.assertTrue(info.get("ok"))
        self.assertEqual(info.get("title"), "Сокровище нации")
        self.assertEqual(info.get("kind"), "movie")
        self.assertTrue(len(info.get("translations", [])) >= 5)
        self.assertTrue(info.get("thumb", "").startswith("http"))

    def test_kinogo_hd_moy_su_extraction_and_inspect(self):
        url = "https://kinogo-hd.moy.su/load/boeviki/sokrovishhe_nacii_3/10-1-0-1924"
        self.assertTrue(server.is_nextembed(url))
        self.assertFalse(server.is_kinogo(url))
        info = server.inspect_url(url)
        self.assertTrue(info.get("ok"))
        self.assertEqual(info.get("kind"), "movie")
        self.assertIn("Сокровище нации", info.get("title"))
        self.assertTrue(len(info.get("translations", [])) >= 5)
        self.assertTrue(info.get("thumb", "").startswith("http"))


if __name__ == "__main__":
    unittest.main()
