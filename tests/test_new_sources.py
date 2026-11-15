import base64
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server
from extractors import seasonvar

PAGE = """<html><head><title>Сериал Тест 1 сезон Test  смотреть онлайн бесплатно!</title>
<meta property="og:image" content="//cdn.example/p.jpg"></head><body>
<script>var pl = {'0': "/playls2/abc/trans/1/plist.txt?time=1"};</script>
<ul class="pgs-trans">
<li data-click="translate" data-translate="0"   data-translate-percent="78.3">Стандартный</li>
<li data-click="translate" data-translate="68">Трейлеры</li>
<li data-click="translate" data-translate="144"   data-translate-percent="4.6">HDRezka</li>
<script>pl[144] = "/playls2/abc/transHDRezka/1/plist.txt?time=1";</script>
<script>pl[68] = "/playls2/abc/transTrailers/1/plist.txt?time=1";</script>
</ul>
<script>if ( $('[data-translate="'+mark['trans']+'"]').length == 0) { x(); } $('[data-translate=0]').length > 0 && y();</script>
</body></html>"""


def enc(url):
    raw = base64.b64encode(url.encode()).decode()
    return "#2" + raw[:10] + "//b2xvbG8=" + raw[10:]


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestSeasonvar(unittest.TestCase):
    def test_qualities_come_from_file_names(self):
        def fake_open(req, timeout=0):
            n = "S01E01.1080p.mp4" if "transHDRezka" in req.full_url else "S01E01.WEBRip.720p.mp4"
            return FakeResp(json.dumps([{"title": "1 серия", "file": enc("//cdn.x/" + n)}]).encode())
        with mock.patch.object(seasonvar, "fetch_page", return_value=PAGE), mock.patch.object(seasonvar.urllib.request, "urlopen", fake_open):
            d = seasonvar.seasonvar_structure("https://seasonvar.ru/serial-1-x.html")
        self.assertEqual(d["qualities"], ["1080p", "720p"])

    def test_decode_and_flatten(self):
        self.assertEqual(seasonvar._sv_file(enc("//cdn.x/a.mp4")), "https://cdn.x/a.mp4")
        items = [{"title": "1"}, {"folder": [{"title": "2"}, {"title": "3"}]}]
        self.assertEqual([i["title"] for i in seasonvar._sv_flatten(items)], ["1", "2", "3"])

    def test_structure_two_voices(self):
        def fake_open(req, timeout=0):
            ep = "7" if "transHDRezka" in req.full_url else "5"
            return FakeResp(json.dumps([{"title": "1 серия", "file": enc(f"//cdn.x/{ep}.mp4")}]).encode())
        with mock.patch.object(seasonvar, "fetch_page", return_value=PAGE), mock.patch.object(seasonvar.urllib.request, "urlopen", fake_open):
            d = seasonvar.seasonvar_structure("https://seasonvar.ru/serial-1-x.html")
        self.assertEqual(d["files"]["0"]["s01e01"], "https://cdn.x/5.mp4")
        self.assertEqual(d["files"]["144"]["s01e01"], "https://cdn.x/7.mp4")
        self.assertNotIn("68", d["files"])  # trailers are not a voice-over
        self.assertEqual([v["title"] for v in d["translations"]], ["Стандартный", "HDRezka"])
        self.assertEqual(d["thumb"], "https://cdn.example/p.jpg")
        self.assertEqual(d["title"], "Тест")
        self.assertEqual(d["qualities"], ["1080p"])  # the fake files have no quality in their names
        self.assertTrue(seasonvar.is_seasonvar("https://seasonvar.ru/serial-1-x.html"))


class TestLive(unittest.TestCase):
    def test_is_liveball(self):
        self.assertTrue(server.is_liveball("https://liveball.sx/match/1"))
        self.assertFalse(server.is_liveball("https://youtu.be/x"))

    def test_ytdlp_streams_parse_and_cache(self):
        out = mock.Mock(stdout="Title\n120\nhttps://v\nhttps://a\n", stderr="")
        server._ytdlp_cache.clear()
        with mock.patch.object(server.subprocess, "run", return_value=out) as run:
            self.assertEqual(server.ytdlp_streams("https://youtu.be/x", "720p"), ("Title", ["https://v", "https://a"], 120.0))
            server.ytdlp_streams("https://youtu.be/x", "720p")
        self.assertEqual(run.call_count, 1)

    def test_dash_live_falls_back_when_ffmpeg_dies(self):
        sid = 424242
        server.active_sessions[sid] = {"file": os.path.join(tempfile.gettempdir(), "dl_test.ts"), "active": True,
                                       "cancel_event": threading.Event(), "procs": set()}
        proc = mock.Mock(returncode=1, stderr=io.BytesIO(b"boom"))
        proc.poll.return_value = 1
        called = []
        try:
            with mock.patch.object(server.subprocess, "Popen", return_value=proc):
                server.stream_dash_live(sid, "t", ["u1", "u2"], 10, 0, "1.0x", None, fallback=lambda: called.append(1))
        finally:
            server.active_sessions.pop(sid, None)
        self.assertEqual(called, [1])


    def _drop_run(self):
        sid = 434343
        path = os.path.join(tempfile.gettempdir(), "dl_drop.ts")
        open(path, "wb").write(b"x" * (3 * 1024 * 1024))
        server.active_sessions[sid] = {"file": path, "active": True, "cancel_event": threading.Event(), "procs": set()}
        proc = mock.Mock(returncode=1, stderr=io.BytesIO(b"net down"))
        proc.poll.side_effect = [None, 1, 1]
        dropped = []
        try:
            with mock.patch.object(server.subprocess, "Popen", return_value=proc), mock.patch.object(server, "tv_cast_url"):
                server.stream_dash_live(sid, "t", ["u1", "u2"], 10, 0, "1.0x", None, on_drop=lambda: dropped.append(1))
        finally:
            server.active_sessions.pop(sid, None)
            os.remove(path)
        return dropped

    def test_dash_live_restarts_after_mid_playback_drop_but_not_forever(self):
        server._drop_times.clear()
        self.assertEqual([self._drop_run() for _ in range(5)], [[1], [1], [1], [], []])
        server._drop_times.clear()


class _Req:
    def __init__(self, url, referer="https://liveball.sx/"):
        self.url, self.headers = url, {"referer": referer}


class _FakePage:
    def __init__(self, urls):
        self.urls, self.handler = urls, None

    def route(self, *a):
        pass

    def on(self, event, fn):
        self.handler = fn

    def goto(self, *a, **kw):
        for u in self.urls:
            self.handler(_Req(u))

    def wait_for_timeout(self, ms):
        pass

    def title(self):
        return "Грузия — Венгрия, 14 ноября 2026: смотреть онлайн | LiveBall"


def _fake_playwright(urls):
    page = _FakePage(urls)
    browser = mock.Mock()
    browser.new_context.return_value.new_page.return_value = page
    p = mock.Mock()
    p.chromium.launch.return_value = browser
    cm = mock.MagicMock()
    cm.__enter__.return_value = p
    mod = mock.Mock(sync_playwright=lambda: cm)
    return {"playwright": mock.Mock(), "playwright.sync_api": mod}, browser


class TestYtdlpAutoUpdate(unittest.TestCase):
    def setUp(self):
        from extractors import youtube
        self.yt = youtube
        youtube._ytdlp_cache.clear()
        youtube._update["last"] = 0.0
        self.addCleanup(lambda: youtube._update.update(last=0.0))

    def test_looks_outdated_only_for_extractor_breakage(self):
        self.assertTrue(self.yt.looks_outdated("yt-dlp could not extract: ERROR: Unable to extract nsig function"))
        self.assertTrue(self.yt.looks_outdated("ERROR: unable to download video data: HTTP Error 403: Forbidden"))
        self.assertFalse(self.yt.looks_outdated("ERROR: [youtube] x: This video has been removed for violating YouTube's Terms of Service"))
        self.assertFalse(self.yt.looks_outdated("ERROR: [youtube] x: Private video"))

    def _fail_then_ok(self, error):
        calls = []

        def fake(url, quality):
            calls.append(1)
            if len(calls) == 1:
                raise Exception(error)
            return ("T", ["v", "a"], 5.0)
        return fake, calls

    def test_outdated_error_updates_then_retries_once(self):
        fake, calls = self._fail_then_ok("yt-dlp could not extract: ERROR: Unable to extract nsig")
        with mock.patch.object(self.yt, "_ytdlp_streams", fake), mock.patch.object(self.yt, "update_ytdlp", return_value=True) as upd:
            self.assertEqual(self.yt.ytdlp_streams("https://youtu.be/x", "720p")[0], "T")
        self.assertEqual((len(calls), upd.call_count), (2, 1))

    def test_removed_video_does_not_update(self):
        fake, calls = self._fail_then_ok("yt-dlp could not extract: ERROR: This video has been removed")
        with mock.patch.object(self.yt, "_ytdlp_streams", fake), mock.patch.object(self.yt, "update_ytdlp") as upd:
            with self.assertRaises(Exception):
                self.yt.ytdlp_streams("https://youtu.be/x", "720p")
        upd.assert_not_called()

    def test_unchanged_version_keeps_the_original_error(self):
        fake, calls = self._fail_then_ok("yt-dlp could not extract: ERROR: Unable to extract nsig")
        with mock.patch.object(self.yt, "_ytdlp_streams", fake), mock.patch.object(self.yt, "update_ytdlp", return_value=False):
            with self.assertRaises(Exception) as ctx:
                self.yt.ytdlp_streams("https://youtu.be/x", "720p")
        self.assertIn("nsig", str(ctx.exception))
        self.assertEqual(len(calls), 1)

    def test_update_runs_pip_once_per_cooldown_and_reports_version_change(self):
        versions = iter(["2026.10.21", "2026.11.15"])
        runs = []

        def fake_run(cmd, *a, **kw):
            if "pip" in cmd:
                runs.append(cmd)
            return mock.Mock(stdout="", returncode=0)
        with mock.patch.object(self.yt, "ytdlp_version", lambda: next(versions)), mock.patch.object(self.yt.subprocess, "run", fake_run):
            self.assertTrue(self.yt.update_ytdlp(now=1_000_000))
            self.assertFalse(self.yt.update_ytdlp(now=1_000_100))  # inside the cooldown: no second pip run
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0][1:6], ["-m", "pip", "install", "-U", "yt-dlp"])


class TestLiveballExtractor(unittest.TestCase):
    def setUp(self):
        from extractors import liveball
        self.lb = liveball
        liveball._liveball_cache.clear()

    def test_picks_play_m3u8_over_ad_playlist_and_closes_browser(self):
        mods, browser = _fake_playwright(["https://ads.example/pre.m3u8", "https://cdn.example/live/1/play.m3u8?t=abc"])
        with mock.patch.dict(sys.modules, mods):
            r = self.lb.extract_liveball("https://liveball.sx/match/1")
        self.assertEqual(r["stream_url"], "https://cdn.example/live/1/play.m3u8?t=abc")
        self.assertEqual(r["title"], "Грузия — Венгрия, 14 ноября 2026")
        self.assertEqual(r["headers"]["Referer"], "https://liveball.sx/")
        browser.close.assert_called_once()

    def test_no_stream_raises_and_closes_browser(self):
        mods, browser = _fake_playwright([])
        with mock.patch.dict(sys.modules, mods):
            with self.assertRaises(Exception) as ctx:
                self.lb.extract_liveball("https://liveball.sx/match/2")
        self.assertIn("stream not found", str(ctx.exception))
        browser.close.assert_called_once()


def _fixture(mid, st, ts, home, away, score="– : –"):
    return (f'<a class="lb-fixture" href="/match/{mid}" data-st="{st}" data-ts="{ts}" data-teams="1,2">\n'
            f'<span class="lb-fixture__time" data-ts="{ts}">x</span>\n'
            f'<span class="lb-fixture__home"><span>{home}</span><img src="a.png"></span>\n'
            f'<span class="lb-fixture__score lb-score">{score}</span>\n'
            f'<span class="lb-fixture__away"><img src="b.png"><span>{away}</span></span>\n<span>→</span>\n</a>\n')


class TestLiveballMatchList(unittest.TestCase):
    NOW = 1_800_000_000

    def test_running_first_then_upcoming_skipping_finished_and_stale(self):
        from extractors import liveball
        page = "".join([
            _fixture(1, "up", self.NOW + 7200, "Future", "Team"),            # upcoming
            _fixture(2, "up", self.NOW - 1800, "Running &amp; Co", "Other"),  # started 30 min ago -> running
            _fixture(3, "fin", self.NOW - 9000, "Done", "Match", "2 : 1"),    # finished
            _fixture(4, "up", self.NOW - 5 * 3600, "Stale", "Match"),         # "up" for 5 h: stale
        ])
        ms = liveball.liveball_matches("https://liveball.sx/", now=self.NOW, page=page)
        self.assertEqual([m["id"] for m in ms], ["m2", "m1"])
        self.assertTrue(ms[0]["live"] and not ms[1]["live"])
        self.assertIn("Running & Co – Other 🔴", ms[0]["title"])

    def test_match_url_from_list_page_and_direct_link(self):
        from extractors import liveball
        self.assertTrue(liveball.is_liveball_home("https://liveball.sx/"))
        self.assertFalse(liveball.is_liveball_home("https://liveball.sx/match/5"))
        self.assertEqual(liveball.liveball_match_url("https://liveball.sx/", "m77"), "https://liveball.sx/match/77")
        self.assertEqual(liveball.liveball_match_url("https://liveball.sx/match/5", None), "https://liveball.sx/match/5")
        with self.assertRaises(Exception):
            liveball.liveball_match_url("https://liveball.sx/", None)


class TestTvPicker(unittest.TestCase):
    PHILIPS = {"ip": "10.0.0.2", "name": "PHILIPS TV", "controls": {"AVTransport": {"url": "u", "type": "t"}}}

    def setUp(self):
        self.saved = dict(server.TV)
        self.addCleanup(lambda: (server.TV.clear(), server.TV.update(self.saved)))
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        self.addCleanup(os.remove, tmp.name)
        p = mock.patch.object(server, "TV_STATE_FILE", tmp.name)
        p.start()
        self.addCleanup(p.stop)
        self.state_file = tmp.name

    def test_player_box_is_offered_next_to_upnp_renderers(self):
        with mock.patch.object(server.socket, "create_connection", return_value=mock.Mock()):
            devs = server.list_tvs([self.PHILIPS])
        self.assertEqual([d["name"] for d in devs], ["PHILIPS TV", server.PLAYER_NAME])
        self.assertTrue(devs[1]["player"])

    def test_box_without_open_adb_port_is_not_offered(self):
        with mock.patch.object(server.socket, "create_connection", side_effect=OSError):
            self.assertEqual(server.list_tvs([self.PHILIPS]), [self.PHILIPS])

    def test_chosen_player_box_survives_rediscovery_and_is_saved(self):
        box = {"ip": "10.0.0.9", "name": server.PLAYER_NAME, "player": True, "controls": self.PHILIPS["controls"]}
        server.select_tv(box)
        self.assertEqual(json.load(open(self.state_file))["player"], True)
        with mock.patch.object(server, "discover_renderers", return_value=[self.PHILIPS]):
            server.refresh_tv()  # the only renderer found must not replace the chosen box
        self.assertEqual((server.TV["ip"], server.TV["name"]), ("10.0.0.9", server.PLAYER_NAME))

    def test_single_renderer_is_still_auto_selected_without_a_player_choice(self):
        server.TV["player"] = False
        server.TV["ip"] = "10.0.0.99"
        with mock.patch.object(server, "discover_renderers", return_value=[self.PHILIPS]):
            server.refresh_tv()
        self.assertEqual(server.TV["ip"], "10.0.0.2")


class TestEnvironment(unittest.TestCase):
    def test_reports_each_missing_piece(self):
        with mock.patch.object(server.os.path, "exists", return_value=False), mock.patch.object(server.shutil, "which", return_value=None):
            problems = server.check_environment()
        self.assertEqual(len(problems), 5)
        self.assertTrue(any("yt-dlp" in p for p in problems) and any("adb" in p for p in problems))

    def test_clean_setup_reports_nothing(self):
        with mock.patch.object(server.os.path, "exists", return_value=True), mock.patch.object(server.shutil, "which", return_value="/x"):
            if server.check_environment():
                self.skipTest("playwright is not installed here")
            self.assertEqual(server.check_environment(), [])


class TestSessionLimits(unittest.TestCase):
    def setUp(self):
        self.saved = (dict(server.active_sessions), server.current_session_id, dict(server._direct_sessions))
        server.active_sessions.clear()
        server._direct_sessions.clear()
        p = mock.patch.object(server, "cleanup_cache")
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._restore)

    def _restore(self):
        server.active_sessions.clear()
        server.active_sessions.update(self.saved[0])
        server.current_session_id = self.saved[1]
        server._direct_sessions.clear()
        server._direct_sessions.update(self.saved[2])

    def test_only_the_newest_running_sessions_survive(self):
        ids = []
        for _ in range(server.MAX_ACTIVE_SESSIONS + 3):
            sid, _f = server.new_session(seamless=True)  # seamless keeps the previous one running, like rapid seeks
            ids.append(sid)
            time.sleep(0.002)
        running = [s for s in ids if server.active_sessions[s]["active"]]
        self.assertEqual(running, ids[-server.MAX_ACTIVE_SESSIONS:])
        self.assertTrue(all(server.active_sessions[s]["cancel_event"].is_set() for s in ids[:-server.MAX_ACTIVE_SESSIONS]))

    def test_old_finished_sessions_are_forgotten(self):
        for i in range(server.KEEP_FINISHED_SESSIONS + 10):
            server.active_sessions[i + 1] = {"file": "x", "active": False, "cancel_event": threading.Event(), "procs": set()}
            server._direct_sessions[i + 1] = {"url": "u"}
        server.new_session()
        self.assertLessEqual(len(server.active_sessions), server.KEEP_FINISHED_SESSIONS + 1)
        self.assertNotIn(1, server.active_sessions)
        self.assertNotIn(1, server._direct_sessions)
        self.assertIn(server.current_session_id, server.active_sessions)

    def test_cache_put_drops_oldest(self):
        c = {}
        for i in range(5):
            server._cache_put(c, i, i, limit=3)
        self.assertEqual(list(c), [2, 3, 4])


class TestCache(unittest.TestCase):
    def test_size_cap_drops_oldest(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(server, "CACHE_DIR", d), mock.patch.object(server, "CACHE_MAX_BYTES", 150):
            for i, name in enumerate(["old", "mid", "new"]):
                p = os.path.join(d, name)
                open(p, "wb").write(b"x" * 100)
                os.utime(p, (1000 + i, 1000 + i))
            server.cleanup_cache(keep=(), max_age=10 ** 12)
            self.assertEqual(sorted(os.listdir(d)), ["new"])


if __name__ == "__main__":
    unittest.main()
