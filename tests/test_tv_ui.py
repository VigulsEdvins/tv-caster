import os
import subprocess
import sys
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server


class TestTVPlayerUI(unittest.TestCase):
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

    def test_tv_html_structure(self):
        status, html, ctype = self._get("/tv")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn("/static/tv/tv_player.css", html)
        self.assertIn("/static/tv/tv_player.js", html)
        self.assertIn('id="tv-video"', html)
        self.assertIn('id="tv-overlay"', html)
        self.assertIn('data-action="open-episodes"', html)
        self.assertIn('data-action="open-voices"', html)
        self.assertIn('data-action="open-quality"', html)
        self.assertIn('data-action="open-speed"', html)
        self.assertIn('data-action="toggle-dock"', html)
        self.assertIn('id="hero-banner"', html)
        self.assertIn('id="tv-history-carousel"', html)
        self.assertIn('data-action="open-history"', html)
        self.assertIn('id="tv-overlay" class="tv-overlay dock-bottom hidden"', html)

    def test_tv_css_served(self):
        status, css, ctype = self._get("/static/tv/tv_player.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", ctype)
        self.assertIn(".tv-overlay", css)
        self.assertIn(".focused", css)
        self.assertIn(".dock-top", css)
        self.assertIn(".dock-bottom", css)
        self.assertIn(".tv-history-card", css)
        self.assertIn(".hero-banner", css)
        self.assertIn(".standby-screen:not(.hidden) ~ .tv-overlay", css)

    def test_tv_js_served_and_valid_syntax(self):
        status, js, ctype = self._get("/static/tv/tv_player.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", ctype)
        self.assertIn("ArrowLeft", js)
        self.assertIn("ArrowRight", js)
        self.assertIn("ArrowUp", js)
        self.assertIn("ArrowDown", js)
        self.assertIn("switch_episode", js)
        self.assertIn("switch_translation", js)
        self.assertIn("switch_quality", js)
        self.assertIn("set_speed", js)
        self.assertIn("renderHistoryCarousel", js)
        self.assertIn("openHistoryItemModal", js)
        self.assertIn("cast_from_tv", js)

        js_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "tv", "tv_player.js")
        node_bin = "/home/edvins/.local/bin/node"
        if os.path.exists(node_bin):
            proc = subprocess.run([node_bin, "--check", js_file], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"JS syntax error: {proc.stderr}")

    def test_tv_js_direct_stream_support(self):
        status, js, _ = self._get("/static/tv/tv_player.js")
        self.assertEqual(status, 200)
        self.assertIn("loadStreamWithHeaders", js)
        self.assertIn("direct_stream", js)
        self.assertIn("onNativePlayerError", js)


if __name__ == "__main__":
    unittest.main()
