# ExoPlayer & Direct Stream Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the Android TV player app from basic `VideoView` to AndroidX Media3 `ExoPlayer` and add Direct Stream Mode to `server.py` and `tv_player.js` so HLS (`.m3u8`) and MP4 streams play immediately without laptop FFmpeg transcoding or full-file disk caching.

**Architecture:**
1. **Server Direct Stream & Lightweight Pass-Through Proxy (`server.py`)**: When casting to the custom Android TV player (`use_custom_player=True`), bypass full-file downloads and FFmpeg remuxing/re-encoding for single-stream MP4 and HLS sources. Expose `/direct/stream` and `/direct/hls.m3u8` pass-through endpoints that attach required upstream headers (`Referer`, `User-Agent`) and DNS overrides in memory with zero disk writes, while also providing `direct_url` and `direct_headers` in `/api/tv/state`. Handle `set_speed` and `seek` actions instantaneously via player commands instead of restarting FFmpeg.
2. **TV Player Frontend (`static/tv/tv_player.js`)**: Prefer direct/pass-through streams and pass upstream headers and initial playback speed to `AndroidBridge.loadStreamWithHeaders(url, headersJson, startAtSec, speed)`. Perform native seeking and speed changes directly on the player at any playback speed without server re-transcoding, with automatic fallback to the server pass-through proxy if a direct CDN URL fails.
3. **Android TV ExoPlayer APK (`android-tv/` + `tools/build_apk.sh`)**: Replace `VideoView` + `MediaPlayer` in `MainActivity.java` with AndroidX Media3 `ExoPlayer` + `SurfaceView`, supporting HLS, MP4, and MPEG-TS streams, custom HTTP request headers via `DefaultHttpDataSource.Factory`, hardware-accelerated decoding, native `PlaybackParameters` speed control, and embedded audio track switching.

**Tech Stack:** Python 3 (`http.server`, `urllib`), JavaScript (ES6 TV UI), Java 8 / Android SDK + AndroidX Media3 ExoPlayer (`1.2.1`), Docker APK build pipeline (`aapt`, `javac`, `d8`, `apksigner`).

**Spec:** Approved design from conversation on 2026-10-27: Upgrade `tv-caster-player.apk` to ExoPlayer (Media3) with custom header and native HLS/MP4 support; add Direct Stream Mode in `server.py` & `tv_player.js` to eliminate laptop FFmpeg re-encoding and disk caching; preserve existing FFmpeg pipeline as fallback for split DASH streams and legacy DLNA mode.

## Global Constraints

- Preserve full backward compatibility with legacy DLNA mode (`use_custom_player=False`) and split video+audio streams (YouTube DASH / NextEmbed).
- Maintain existing `AndroidBridge` methods (`loadStream`, `play`, `pause`, `stop`, `seekTo`, `setSpeed`, `getPosition`, `getDuration`, `isPlaying`) while adding `loadStreamWithHeaders`, `getAudioTracks`, and `selectAudioTrack`.
- Zero disk writes to `media_cache/` when streaming in Direct Stream Mode.
- Changing playback speed (`0.75x`–`2.0x`) on an active direct stream must never spawn `ffmpeg` or restart `handle_cast_request`.
- APK must compile deterministically in `tools/build_apk.sh` and be served at `/player.apk` and `/static/tv-caster-player.apk`.

## Review Focus

1. **CDN Referer / IP-Lock Rejection on TV:** Some upstream CDNs (e.g. Kinogo/`cinemar.cc` or Rezka mirrors) reject requests missing `Referer` or require the server's DNS bypass (`get_clean_ip`). The server pass-through proxy (`/direct/stream` and `/direct/hls.m3u8`) and TV player fallback must seamlessly handle upstream headers and relative HLS segment URLs.
2. **Playback Speed Timeline Distortion:** When playback speed (`1.5x`) is applied natively by ExoPlayer or HTML5 `<video>`, `getPosition()` and `getDuration()` already report actual media seconds; `tv_player.js` must not multiply native position/duration by `currentSpeed` for direct streams.
3. **Seeking at Non-1.0x Speed:** Seeking forward/backward while watching at `1.25x` or `1.5x` in Direct Stream Mode must seek natively in the player without triggering a server FFmpeg reload.
4. **HLS Relative Variant & Segment URLs:** When `/direct/hls.m3u8` proxies a master or media playlist, relative URLs (e.g. `720.m3u8` or `seg-1.ts`) must be rewritten to absolute `/direct/hls.m3u8` or `/direct/stream` proxy URLs so the player does not request missing paths on the local server.
5. **Rapid Session Switching / Cancellation:** Starting a new cast while a direct stream is active must cleanly update `current_session_id` and stop the previous stream without leaving stale state.

---

### Task 1: Server Direct Stream Mode & Memory Pass-Through Proxy (`server.py`)

**Files:**
- Modify: `server.py`
- Test: `tests/test_direct_stream.py`

**Interfaces:**
- Produces in `server.py`:
  - `settings["direct_stream"] = True` (default enabled)
  - `_direct_sessions: dict[int, dict]` storing `{"url": str, "headers": dict, "kind": "mp4" | "hls", "title": str}`
  - `start_direct_stream(session_id: int, title: str, stream_url: str, headers: dict | None = None, kind: str = "mp4", speed: str = "1.0x", start_at: float = 0) -> None`
  - `rewrite_hls_playlist(m3u8_text: str, base_url: str, session_id: int) -> str`
  - HTTP routes in `TVCasterHandler.do_GET`:
    - `GET /direct/stream?v=<sid>[&u=<url_b64>]`: streams upstream MP4 or TS segment with HTTP `Range` support and session headers, zero disk cache.
    - `GET /direct/hls.m3u8?v=<sid>[&u=<url_b64>]`: fetches upstream `.m3u8` and rewrites relative/variant/segment URLs to proxy through `/direct/hls.m3u8` and `/direct/stream`.
  - Updated `build_tv_state_payload()` fields:
    - `direct_stream: bool`
    - `direct_url: str` (the `/direct/hls.m3u8?v=<sid>` or `/direct/stream?v=<sid>` URL)
    - `cdn_url: str` (the raw upstream URL)
    - `direct_headers: dict[str, str]`
  - Fast-path in `POST /api/tv/action` for `set_speed` and `seek`: when `info.get("direct_stream")` is `True`, update state and issue TV command (`speed` / `seek`) immediately without spawning `handle_cast_request`.

- [x] **Step 1: Write failing tests in `tests/test_direct_stream.py`**

```python
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
            "#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1280x720\n"
            "720p/index.m3u8\n"
        )
        rewritten = server.rewrite_hls_playlist(
            raw_master, "https://cdn.example.com/hls/master.m3u8", session_id=101
        )
        self.assertIn("/direct/hls.m3u8?v=101&u=", rewritten)

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
```

- [x] **Step 2: Run `python3 tests/test_direct_stream.py` to verify it fails**

Run: `python3 tests/test_direct_stream.py`
Expected: FAIL with `AttributeError: module 'server' has no attribute 'rewrite_hls_playlist'`

- [x] **Step 3: Implement Direct Stream functions, routes, and fast-path actions in `server.py`**

Implement in `server.py`:
- Add `"direct_stream": True` to default `settings`.
- Add `_direct_sessions = {}` and `start_direct_stream(session_id, title, stream_url, headers=None, kind="mp4", speed="1.0x", start_at=0)`:
  - Sets `info["direct_stream"] = True`, `info["active"] = False`, `info["direct_kind"] = kind`, `info["direct_url"] = stream_url`, `info["direct_headers"] = headers or {}`, `info["start_at"] = start_at`.
  - Sets `local_proxy_url = f"http://{LOCAL_IP}:{SERVER_PORT}/direct/hls.m3u8?v={session_id}" if kind == "hls" else f"http://{LOCAL_IP}:{SERVER_PORT}/direct/stream?v={session_id}"`.
  - Updates `app_state` (`status="playing"`, `stage="tv"`, `status_text="Играет на ТВ (Прямой поток)"`, `title=title`, `download_progress=100`, `download_speed="Direct Stream"`) and calls `tv_cast_url(local_proxy_url, title)`.
- Add `rewrite_hls_playlist(m3u8_text, base_url, session_id)` using URL-safe base64 encoding (`base64.urlsafe_b64encode`) for sub-playlists (`/direct/hls.m3u8?v={session_id}&u={b64}`) and media segments (`/direct/stream?v={session_id}&u={b64}`), including `#EXT-X-MEDIA:...URI="..."` and `#EXT-X-KEY:...URI="..."` tags.
- Add `serve_direct_hls(self)` and `serve_direct_stream(self, send_body=True)` to `TVCasterHandler` (`do_GET` and `do_HEAD`), forwarding `Range` requests and upstream `Content-Type`, `Content-Length`, `Content-Range`, `Accept-Ranges`, and `Access-Control-Allow-Origin: *`.
- Update `handle_cast_request` so when `tv_player_state.get("use_custom_player", True)` and `settings.get("direct_stream", True)` are enabled:
  - Kinogo / VK / single-URL `.m3u8` streams resolve redirects once and call `start_direct_stream(..., kind="hls")`.
  - Rezka / single-URL `.mp4` streams call `start_direct_stream(..., kind="mp4")`.
- Update `POST /api/tv/action` (`set_speed` and `seek`): when `info.get("direct_stream")` is `True`, update `opts["speed"]` / `tv_player_state["position"]` and call `_issue_tv_command("speed", target=spd)` or `tv_seek(target_sec)` without spawning a new `handle_cast_request`.

- [x] **Step 4: Run `python3 tests/test_direct_stream.py` and `python3 tests/test_tv_api.py`**

Run: `python3 tests/test_direct_stream.py && python3 tests/test_tv_api.py`
Expected: PASS

- [x] **Step 5: Commit Task 1**

```bash
git add server.py tests/test_direct_stream.py
git commit -m "feat(server): add direct stream mode and zero-disk HLS/MP4 proxy"
```

---

### Task 2: TV Player Frontend Direct Stream & Native Speed/Seek Support (`static/tv/tv_player.js`)

**Files:**
- Modify: `static/tv/tv_player.js`
- Test: `tests/test_tv_ui.py`

**Interfaces:**
- Consumes from `server.py` (`/api/tv/state`):
  - `direct_stream: bool`, `direct_url: str`, `cdn_url: str`, `direct_headers: dict`
- Calls on `window.AndroidBridge`:
  - `loadStreamWithHeaders(url: string, headersJson: string, startAtSec: number, speed: number)` (if defined, else falls back to `loadStream(url, startAtSec, speed)`)
- Exposes on `window`:
  - `window.onNativePlayerError = function(failedUrl)` — automatically switches from `cdn_url` to local proxy `direct_url` if direct CDN playback fails on the TV.

- [x] **Step 1: Add direct stream assertions to `tests/test_tv_ui.py`**

Add `test_tv_js_direct_stream_support` in `tests/test_tv_ui.py` checking that `static/tv/tv_player.js` contains `loadStreamWithHeaders`, `direct_stream`, `onNativePlayerError`, and passes `node --check`.

- [x] **Step 2: Run `python3 tests/test_tv_ui.py` to verify it fails**

Run: `python3 tests/test_tv_ui.py`
Expected: FAIL with `'loadStreamWithHeaders' not found`

- [x] **Step 3: Update `static/tv/tv_player.js` for Direct Stream Mode**

Modify `static/tv/tv_player.js`:
- In `playerLoad(stateObj, startAtSec, streamOffsetSec, forceReload)`:
  - When `stateObj.direct_stream` is true:
    - Set `baseSeekOffset = 0`.
    - For `hasNativeBridge`: use `stateObj.direct_url` (or `stateObj.cdn_url` if no special IP-bound proxy is required, with fallback to `stateObj.direct_url` in `window.onNativePlayerError`), pass `currentSpeed` directly to `loadStreamWithHeaders(targetUrl, JSON.stringify(stateObj.direct_headers || {}), Math.floor(startSec), currentSpeed)` (falling back to `loadStream(targetUrl, Math.floor(startSec), currentSpeed)`).
    - For browser mode (`!hasNativeBridge`): set `videoEl.src = stateObj.direct_url` and `videoEl.playbackRate = currentSpeed`.
- In `playerGetPosition()` and `playerGetDuration()`:
  - When `serverState.direct_stream` is true, do not multiply `raw` position or duration by `currentSpeed` (since ExoPlayer and HTML5 `<video>` report real media time when using native playback rate).
- In `playerSeekTo(targetSec, fromRemote)`:
  - When `serverState.direct_stream` is true and `hasNativeBridge`, call `window.AndroidBridge.seekTo(Math.max(0, Math.floor(targetSec)))` directly at any speed and notify `/api/tv/report` instead of triggering a server re-cast.

- [x] **Step 4: Run `python3 tests/test_tv_ui.py` to verify it passes**

Run: `python3 tests/test_tv_ui.py`
Expected: PASS

- [x] **Step 5: Commit Task 2**

```bash
git add static/tv/tv_player.js tests/test_tv_ui.py
git commit -m "feat(tv-ui): support direct stream loading, headers, and native speed/seek"
```

---

### Task 3: Upgrade Android TV APK to AndroidX Media3 ExoPlayer (`android-tv/` & `tools/build_apk.sh`)

**Files:**
- Modify: `android-tv/app/src/main/java/com/tvcaster/player/MainActivity.java`
- Modify: `tools/build_apk.sh`
- Modify: `static/tv-caster-player.apk`
- Test: `tests/test_apk.py`

**Interfaces:**
- `AndroidBridge` methods exposed to WebView JS:
  - `loadStream(String url, int startAtSec, float speed)`
  - `loadStreamWithHeaders(String url, String headersJson, int startAtSec, float speed)`
  - `play()`, `pause()`, `stop()`, `seekTo(int sec)`, `setSpeed(float speed)`
  - `getPosition() -> int`, `getDuration() -> int`, `isPlaying() -> boolean`
  - `getAudioTracks() -> String` (JSON array of `{index, lang, label, codec}`)
  - `selectAudioTrack(int index)`

- [x] **Step 1: Add ExoPlayer assertions to `tests/test_apk.py`**

Update `tests/test_apk.py` to assert:
- `MainActivity.java` uses `ExoPlayer`, `DefaultHttpDataSource`, `SurfaceView`, and exposes `loadStreamWithHeaders`, `getAudioTracks`, and `selectAudioTrack`.
- `static/tv-caster-player.apk` is a valid signed APK containing `classes.dex` with compiled ExoPlayer classes.

- [x] **Step 2: Run `python3 tests/test_apk.py` to verify it fails**

Run: `python3 tests/test_apk.py`
Expected: FAIL with `'ExoPlayer' not found in MainActivity.java`

- [x] **Step 3: Update `tools/build_apk.sh` to cache and compile AndroidX Media3 ExoPlayer**

Update `tools/build_apk.sh`:
- Download and cache Android 34 `android.jar` and the Media3 `1.2.1` AARs/JARs (`media3-exoplayer`, `media3-exoplayer-hls`, `media3-exoplayer-dash`, `media3-common`, `media3-container`, `media3-datasource`, `media3-decoder`, `media3-extractor`, `media3-database`, `annotation`, `annotation-experimental`, `collection`, `core`, `guava`, `failureaccess`) into `/tmp/android-sdk-cache/media3-jars/`.
- Compile `MainActivity.java` against `android.jar` + `media3-jars/*` and run `d8` (or `R8` shrinking/desugaring) so the resulting APK includes ExoPlayer + HLS + DASH support.

- [x] **Step 4: Rewrite `MainActivity.java` to use `ExoPlayer` + `SurfaceView`**

Update `android-tv/app/src/main/java/com/tvcaster/player/MainActivity.java`:
- Replace `VideoView` with `SurfaceView` + `ExoPlayer`.
- Build a per-stream `DefaultHttpDataSource.Factory` configured with custom headers parsed from `headersJson` (`org.json.JSONObject`), cross-protocol redirects enabled, and `DefaultMediaSourceFactory`.
- Implement `Player.Listener` (`onPlaybackStateChanged`, `onPlayerError`):
  - Apply pending seek (`exoPlayer.seekTo(pendingSeekSec * 1000L)`) and speed (`exoPlayer.setPlaybackParameters(new PlaybackParameters(pendingSpeed))`) immediately upon preparation.
  - On `PlaybackException`, notify `webView.evaluateJavascript("if(window.onNativePlayerError)window.onNativePlayerError(...)", null)` so JS can fall back to `/direct/stream` if a direct CDN link failed.
- Implement `getAudioTracks()` and `selectAudioTrack(int index)` using ExoPlayer's `TrackSelectionOverride` / `Tracks`.

- [x] **Step 5: Build the APK and run all test suites**

Run: `bash tools/build_apk.sh && python3 tests/test_apk.py && python3 tests/test_tv_ui.py && python3 tests/test_tv_api.py && python3 tests/test_direct_stream.py`
Expected: APK builds and signs cleanly; all tests PASS.

- [x] **Step 6: Commit Task 3**

```bash
git add android-tv/ tools/build_apk.sh static/tv-caster-player.apk tests/test_apk.py
git commit -m "feat(android-tv): upgrade player to AndroidX Media3 ExoPlayer"
```
