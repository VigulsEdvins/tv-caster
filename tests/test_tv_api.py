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

    def test_is_replay_lsm(self):
        self.assertTrue(server.is_replay_lsm("https://replay.lsm.lv/lv/skaties/tiesraide/ltv7/merdoka-noslepumi-9-detektivserials-originalvalodas-izvele"))
        self.assertTrue(server.is_replay_lsm("https://replay.lsm.lv/lv/skaties/tiesraide/ltv1"))
        self.assertTrue(server.is_replay_lsm("https://ltv.lsm.lv/embed/live?c=123"))
        self.assertFalse(server.is_replay_lsm("https://youtube.com/watch?v=123"))

    def test_inspect_lsm_url(self):
        url = "https://replay.lsm.lv/lv/skaties/tiesraide/ltv7/merdoka-noslepumi-9-detektivserials-originalvalodas-izvele"
        info = server.inspect_url(url)
        self.assertTrue(info.get("ok"))
        self.assertTrue(len(info.get("title", "")) > 0)
        self.assertEqual(info.get("kind"), "live")
        self.assertTrue(len(info.get("translations", [])) >= 2)
        # Verify original language is prioritized when requested in URL
        self.assertIn(info.get("selected_translation"), ["fra", "und", "eng"])

    def test_lsm_master_playlist_endpoint(self):
        sample_raw = """#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="mp4a",DEFAULT="YES",NAME="lav-mp4a",LANGUAGE="lav",URI="audio1.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="mp4a",DEFAULT="NO",NAME="fra-mp4a",LANGUAGE="fra",URI="audio2.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=3500000,RESOLUTION=1280x720,AUDIO="mp4a"
video720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5500000,RESOLUTION=1920x1080,AUDIO="mp4a"
video1080.m3u8
"""
        sid = 9999
        server._lsm_sessions[sid] = {
            "master_url": "https://cdn.example.com/live/playlist.m3u8",
            "final_master_url": "https://cdn.example.com/live/playlist.m3u8",
            "raw_playlist": sample_raw,
            "chosen_lang": "fra",
            "quality": "720p",
            "is_live": True,
            "title": "Test LSM Stream",
            "fetched_at": time.time(),
        }
        status, body, ctype = self._get(f"/lsm/master.m3u8?s={sid}&lang=fra&q=720p")
        self.assertEqual(status, 200)
        self.assertIn("application/vnd.apple.mpegurl", ctype)
        self.assertIn("DEFAULT=YES,NAME=\"fra-mp4a\"", body)
        self.assertIn("DEFAULT=NO,NAME=\"lav-mp4a\"", body)
        self.assertIn("https://cdn.example.com/live/audio2.m3u8", body)
        # Verify 720p is listed before 1080p
        idx_720 = body.find("1280x720")
        idx_1080 = body.find("1920x1080")
        self.assertLess(idx_720, idx_1080)

    def test_lsm_url_selectors(self):
        video_streams = {
            "1080p": "https://cdn.example.com/live/video_1080.m3u8",
            "720p": "https://cdn.example.com/live/video_720.m3u8",
            "360p": "https://cdn.example.com/live/video_360.m3u8",
        }
        audio_streams = {
            "lav": "https://cdn.example.com/live/audio_lav.m3u8",
            "fra": "https://cdn.example.com/live/audio_fra.m3u8",
        }
        # Exact match
        self.assertEqual(server.select_lsm_video_url(video_streams, "720p"), "https://cdn.example.com/live/video_720.m3u8")
        # Fallback to closest available <= requested
        self.assertEqual(server.select_lsm_video_url(video_streams, "480p"), "https://cdn.example.com/live/video_360.m3u8")
        # Audio selection
        self.assertEqual(server.select_lsm_audio_url(audio_streams, "fra"), "https://cdn.example.com/live/audio_fra.m3u8")
        self.assertEqual(server.select_lsm_audio_url(audio_streams, "lav"), "https://cdn.example.com/live/audio_lav.m3u8")
        # Fallback
        self.assertIsNotNone(server.select_lsm_audio_url(audio_streams, "unknown"))


if __name__ == "__main__":
    unittest.main()
