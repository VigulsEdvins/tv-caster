import json
import os
import sys
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server


class TestPendingSeek(unittest.TestCase):
    """A seek sent while the TV is still loading a direct stream is repeated once the TV plays."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = server.ThreadedServer(("127.0.0.1", 0), server.TVCasterHandler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _post(self, path, payload):
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode())

    def setUp(self):
        self.sid = server.current_session_id = 424242
        server.active_sessions[self.sid] = {"direct_stream": True, "cancel_event": threading.Event()}
        server.tv_player_state.update(last_load_ts=time.time(), duration=0.0, state="PLAYING", position=0.0)
        server.tv_player_state.pop("pending_seek", None)
        self.seeks = []
        self._orig = server.tv_seek
        server.tv_seek = lambda t: self.seeks.append(float(t))

    def tearDown(self):
        server.tv_seek = self._orig
        server.active_sessions.pop(self.sid, None)
        server.tv_player_state.pop("pending_seek", None)

    def test_seek_while_loading_is_repeated(self):
        self._post("/api/tv/action", {"action": "seek", "target": 3000})
        self.assertEqual(self.seeks, [3000.0])
        self.assertEqual(server.tv_player_state["pending_seek"]["target"], 3000)
        self._post("/api/tv/report", {"state": "PLAYING", "position": 2.0})  # duration unknown: still loading
        self.assertEqual(self.seeks, [3000.0])
        self._post("/api/tv/report", {"state": "PLAYING", "position": 2.0, "duration": 7200})
        self.assertEqual(self.seeks, [3000.0, 3000.0])
        self.assertNotIn("pending_seek", server.tv_player_state)

    def test_no_repeat_when_already_there(self):
        self._post("/api/tv/action", {"action": "seek", "target": 3000})
        self._post("/api/tv/report", {"state": "PLAYING", "position": 3001.0, "duration": 7200})
        self.assertEqual(self.seeks, [3000.0])
        self.assertNotIn("pending_seek", server.tv_player_state)

    def test_seek_of_loaded_player_is_not_pending(self):
        server.tv_player_state.update(last_load_ts=time.time() - 120, duration=7200.0)
        self._post("/api/tv/action", {"action": "seek", "target": 600})
        self.assertNotIn("pending_seek", server.tv_player_state)

    def test_pending_dropped_for_other_session(self):
        self._post("/api/tv/action", {"action": "seek", "target": 3000})
        server.current_session_id = 999
        self._post("/api/tv/report", {"state": "PLAYING", "position": 1.0, "duration": 7200})
        self.assertEqual(self.seeks, [3000.0])
        self.assertNotIn("pending_seek", server.tv_player_state)


if __name__ == "__main__":
    unittest.main()
