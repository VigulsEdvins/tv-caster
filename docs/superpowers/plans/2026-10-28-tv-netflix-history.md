# Netflix-Style TV History & Series Browser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Netflix-style "Continue Watching" home screen and on-demand catalog overlay on the Android TV player so users can browse their watch history with the TV remote and choose either to resume the last episode where they left off or pick any season/episode.

**Architecture:**
1. **Server History Grouping & TV Launch Actions (`server.py`)**: Group `history.json` entries by show URL in `build_tv_history_payload()` so multi-episode series appear as a single show card with the latest episode's resume position plus a per-episode `watched_episodes` progress map. Expose this grouped list in `/api/tv/state` (`history` field) and `/api/tv/history`, fix `update_history` so thumbnails attach to series entries, and add `cast_from_tv` to `POST /api/tv/action`.
2. **Netflix-Style TV Home Screen & Control Button (`templates/tv_player.html` & `static/tv/tv_player.css`)**: Transform `#standby-screen` into a 10-foot Netflix dashboard with a top Hero Spotlight banner (`#hero-banner`) and a D-pad navigable horizontal poster carousel (`#tv-history-carousel`). Add an **"📺 История"** button (`#btn-history`) to the playback control bar so users can open the browse screen mid-playback.
3. **D-Pad Carousel Controller & Resume/Episode Picker Modal (`static/tv/tv_player.js`)**: Navigate cards with Left/Right D-pad keys, dynamically updating the Hero Spotlight banner. Pressing `ENTER` on a card opens a modal offering **"▶ Продолжить (Серия X • с MM:SS)"**, **"🎬 Выбрать серию"** (which loads seasons/episodes via `/api/inspect` into the season/episode grid), and **"🔄 Смотреть сначала"**.

**Tech Stack:** Python 3 (`server.py`), HTML5 / CSS3 / ES6 JavaScript (`templates/tv_player.html`, `static/tv/tv_player.css`, `static/tv/tv_player.js`), `unittest`.

**Spec:** Approved design from conversation on 2026-10-28: Netflix-style home screen on TV with top spotlight banner and horizontal "Continue Watching" card row; clicking a card lets the user choose between continuing the last episode right where they left off or opening the season/episode selector; accessible at startup and anytime during playback via the TV remote.

## Global Constraints

- Full D-pad remote navigation (`LEFT`, `RIGHT`, `UP`, `DOWN`, `ENTER`, `BACK`) with clear visual focus rings and smooth horizontal scrolling.
- Series with multiple watched episodes in `history.json` must be deduplicated into a single card per show URL on the main carousel, displaying the most recently watched episode and preserving a map of watched episodes.
- Selecting "Выбрать серию" on any history card (even one not currently playing) must inspect that show's URL and display its seasons and episodes in the TV modal.
- Preserve existing player overlay navigation, direct streaming, and modal behavior.

## Review Focus

1. **Duplicate Series Cards in History:** `history.json` stores a separate entry per `(url, episode)`. `build_tv_history_payload()` must group by normalized `url` so *Король Талсы* appears once with its latest watched episode and a `watched_episodes` map.
2. **Missing Poster Thumbnails on Series Entries:** `fetch_thumb(url)` previously called `update_history(url, thumb=img)` with `episode=None`, which failed to match entries that had a non-null `episode`. `update_history` must update `thumb` across all entries matching `url` when `episode is None`.
3. **Inspecting a Non-Playing Show's Episodes from TV:** Opening "Выбрать серию" for a history card that isn't the currently playing video must query `/api/inspect` for that card's `url` and dispatch `cast_from_tv` with that card's `url` and chosen `episode`.
4. **Opening & Closing Browse Screen Mid-Playback:** Opening the Netflix browse screen while a video is playing must allow returning to the active video with `BACK` without losing playback state.
5. **Empty History Fallback:** If `history.json` is empty, the TV home screen must render a clean fallback message without JS errors or broken D-pad focus.

---

### Task 1: Server-Side Grouped TV History & `cast_from_tv` Action (`server.py`)

**Files:**
- Modify: `server.py`
- Test: `tests/test_tv_history.py`

**Interfaces:**
- Produces in `server.py`:
  - `clean_show_title(raw_title: str) -> str`
  - `build_tv_history_payload() -> list[dict]` returning items with `{title, series_title, url, episode, position, duration, progress_pct, thumb, quality, speed, translation_id, translation_title, timestamp, is_series, watched_episodes}`
  - `GET /api/tv/history` returning `{"ok": True, "items": build_tv_history_payload()}`
  - `history` field included in `build_tv_state_payload()`
  - `POST /api/tv/action` with `action == "cast_from_tv"`: accepts `{url, episode, start_at, quality, speed, translation_id, translation_title}` and triggers `handle_cast_request(...)`.

- [x] **Step 1: Write failing tests in `tests/test_tv_history.py`**

```python
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
```

- [x] **Step 2: Run `python3 tests/test_tv_history.py` to verify it fails**

Run: `python3 tests/test_tv_history.py`
Expected: FAIL with `AttributeError: module 'server' has no attribute 'build_tv_history_payload'`

- [x] **Step 3: Implement `clean_show_title`, `build_tv_history_payload`, `/api/tv/history`, and `cast_from_tv` in `server.py`**

Implement in `server.py`:
- Update `update_history(url, episode=None, **fields)` so when `episode is None`, any entry with `h.get("url") == url` is updated if `_same(h, url, None)` or `"thumb" in fields`.
- Implement `clean_show_title(raw_title)` to strip `(\d+\s*сезон\s*\d+\s*серия.*|\[.*?\]|\(.*?\))` suffixes when extracting the clean series name.
- Implement `build_tv_history_payload(raw_history=None)` grouping by normalized `url`, merging any available `thumb` across episodes of the same show, calculating `progress_pct`, setting `is_series = bool(entry.get("episode") or re.search(r"сезон|серия|s\d+e\d+", entry.get("title", ""), re.I))`, and attaching `watched_episodes`.
- Add `"history": build_tv_history_payload()` to `build_tv_state_payload()`.
- Add `GET /api/tv/history` in `TVCasterHandler.do_GET`.
- Add `elif action == "cast_from_tv":` in `TVCasterHandler.do_POST` (`/api/tv/action`) to launch `handle_cast_request` with the provided `url`, `episode`, `start_at`, `quality`, `speed`, `translation_id`, `translation_title`.

- [x] **Step 4: Run `python3 tests/test_tv_history.py` and `python3 tests/test_tv_api.py`**

Run: `python3 tests/test_tv_history.py && python3 tests/test_tv_api.py`
Expected: PASS

- [x] **Step 5: Commit Task 1**

```bash
git add server.py tests/test_tv_history.py
git commit -m "feat(server): add grouped TV history payload and cast_from_tv action"
```

---

### Task 2: Netflix-Style Home Screen HTML & CSS (`templates/tv_player.html` & `static/tv/tv_player.css`)

**Files:**
- Modify: `templates/tv_player.html`
- Modify: `static/tv/tv_player.css`
- Test: `tests/test_tv_ui.py`

**Interfaces:**
- DOM IDs in `templates/tv_player.html`:
  - `#standby-screen` containing `#tv-home-dashboard`:
    - `#hero-banner`, `#hero-backdrop`, `#hero-title`, `#hero-meta`, `#hero-badge-ep`, `#hero-badge-voice`, `#hero-badge-quality`, `#hero-progress-bar`, `#hero-progress-fill`, `#hero-progress-text`, `#hero-actions-hint`
    - `#loading-box`, `#loading-status-text`, `#loading-speed-text`, `#loading-bar-fill`
    - `#tv-history-section`, `#tv-history-carousel`
  - `#btn-history` (`data-row="2"`, `data-action="open-history"`) in `#tv-overlay`

- [x] **Step 1: Add HTML/CSS assertions for Netflix Home UI to `tests/test_tv_ui.py`**

Update `tests/test_tv_ui.py` to assert `/tv` contains `id="hero-banner"`, `id="tv-history-carousel"`, `data-action="open-history"`, and `/static/tv/tv_player.css` contains `.tv-history-card` and `.hero-banner`.

- [x] **Step 2: Run `python3 tests/test_tv_ui.py` to verify it fails**

Run: `python3 tests/test_tv_ui.py`
Expected: FAIL with `'id="hero-banner"' not found`

- [x] **Step 3: Update `templates/tv_player.html` and `static/tv/tv_player.css`**

Update `templates/tv_player.html` and `static/tv/tv_player.css` with the Netflix-style Hero Spotlight banner, horizontal card carousel, card progress bar, episode badges, and `#btn-history` button in the player overlay.

- [x] **Step 4: Run `python3 tests/test_tv_ui.py` to verify it passes**

Run: `python3 tests/test_tv_ui.py`
Expected: PASS

- [x] **Step 5: Commit Task 2**

```bash
git add templates/tv_player.html static/tv/tv_player.css tests/test_tv_ui.py
git commit -m "feat(tv-ui): add Netflix-style home spotlight and history carousel layout"
```

---

### Task 3: D-Pad Carousel Navigation, Resume Dialog & Episode Selector (`static/tv/tv_player.js`)

**Files:**
- Modify: `static/tv/tv_player.js`
- Test: `tests/test_tv_ui.py`

**Interfaces:**
- Functions in `static/tv/tv_player.js`:
  - `renderHistoryCarousel(items)`
  - `updateHeroSpotlight(item)`
  - `openHistoryBrowser()` / `closeHistoryBrowser()`
  - `openHistoryItemModal(item)` — shows modal with:
    1. `▶ Продолжить просмотр` (resumes at `item.position` with `action: "cast_from_tv"`)
    2. `🎬 Выбрать сезон и серию` (fetches `/api/inspect` for `item.url` and opens `openSeriesPickerForHistoryItem(item, inspectData)`)
    3. `🔄 Смотреть сначала (с 00:00)` (if `item.position >= 15`)

- [x] **Step 1: Add JS assertions for history carousel and modal actions in `tests/test_tv_ui.py`**

Update `tests/test_tv_ui.py` to assert `static/tv/tv_player.js` contains `renderHistoryCarousel`, `openHistoryItemModal`, `cast_from_tv`, and passes `node --check`.

- [x] **Step 2: Run `python3 tests/test_tv_ui.py` to verify it fails**

Run: `python3 tests/test_tv_ui.py`
Expected: FAIL with `'renderHistoryCarousel' not found`

- [x] **Step 3: Implement Netflix carousel navigation and action/episode modals in `static/tv/tv_player.js`**

Update `static/tv/tv_player.js`:
- Render `serverState.history` cards inside `#tv-history-carousel`.
- Update `#hero-banner` whenever `historyFocusIndex` changes.
- Handle D-pad `LEFT`, `RIGHT`, `ENTER`, and `BACK` when `homeScreenVisible` is active.
- Implement `openHistoryItemModal(item)` and `openSeriesPickerForHistoryItem(item, info)` so the user can either immediately resume where they left off or pick any season and episode on the TV.
- Wire `#btn-history` (`data-action="open-history"`) and `BACK` key to open/close the Netflix browse screen.

- [x] **Step 4: Run all test suites**

Run: `python3 -m unittest discover -s tests -p "test_*.py" -v`
Expected: All tests PASS.

- [x] **Step 5: Commit Task 3**

```bash
git add static/tv/tv_player.js tests/test_tv_ui.py docs/superpowers/plans/2026-10-28-tv-netflix-history.md
git commit -m "feat(tv-ui): implement interactive Netflix history browser and episode picker"
```
