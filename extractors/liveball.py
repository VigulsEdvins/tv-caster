import html as _html
import re
import shutil
import time
import urllib.parse

from extractors.net import fetch_page


def is_liveball(url):
    return "liveball." in (url or "")

_liveball_cache = {}
LIVE_WINDOW = 3 * 3600  # the page marks running matches "up" (live status is filled in by JS), so "started < 3 h ago" means running

def is_liveball_home(url):
    """liveball.* page that is not a single match (home, league, day list): shown as a list of matches to pick from."""
    return is_liveball(url) and "/match/" not in (url or "")

def liveball_match_url(url, episode):
    """The match page to open: a match link as is, or the match picked (episode id "m<ID>") on a list page."""
    if not is_liveball_home(url):
        return url
    m = re.match(r"^m(\d+)$", episode or "")
    if not m:
        raise Exception("liveball: pick a match from the list")
    return urllib.parse.urljoin(url, f"/match/{m.group(1)}")

def liveball_matches(url, now=None, page=None):
    """[{id, title, ts, live}] from a list page: running matches first, then upcoming by time; finished ones are skipped."""
    page = page if page is not None else fetch_page(url)
    now = time.time() if now is None else now
    out = []
    for block in re.findall(r'<a class="lb-fixture"[^>]*>.*?</a>', page, re.S):
        mid = re.search(r'href="/match/(\d+)', block)
        ts = re.search(r'data-ts="(\d+)"', block)
        st = re.search(r'data-st="([^"]*)"', block)
        home = re.search(r'lb-fixture__home"><span>(.*?)</span>', block, re.S)
        away = re.search(r'lb-fixture__away">.*?<span>(.*?)</span></span>', block, re.S)
        if not (mid and ts and home and away) or (st and st.group(1) == "fin"):
            continue
        ts = int(ts.group(1))
        if now - ts > LIVE_WINDOW:
            continue  # started long ago and not marked finished: stale
        live = ts <= now
        title = f"{time.strftime('%H:%M', time.localtime(ts))} {_html.unescape(home.group(1)).strip()} – {_html.unescape(away.group(1)).strip()}" + (" 🔴" if live else "")
        out.append({"id": "m" + mid.group(1), "title": title, "ts": ts, "live": live})
    out.sort(key=lambda m: (not m["live"], m["ts"]))
    return out[:60]

def extract_liveball(url):
    """LiveBall hides a tokenised HLS URL behind JS: load the page in headless Chrome and catch the play.m3u8 request."""
    c = _liveball_cache.get(url)
    if c and time.time() - c["ts"] < 600:
        return c
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise Exception("liveball needs playwright: pip install -r requirements.txt")
    ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    found = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=shutil.which("google-chrome") or None, headless=True,
                              args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        try:
            pg = b.new_context(user_agent=ua).new_page()
            # pictures and fonts are not needed to reach play.m3u8 (CSS is kept: the page checks that the player is visible)
            pg.route("**/*", lambda route: route.abort() if route.request.resource_type in ("image", "font") else route.continue_())
            pg.on("request", lambda r: found.append(r) if ".m3u8" in r.url else None)
            pg.goto(url, wait_until="domcontentloaded", timeout=20000)
            for _ in range(150):
                if any("play.m3u8" in r.url for r in found):  # preroll ads may request other playlists first
                    break
                pg.wait_for_timeout(200)
            title = re.sub(r"\s*[:|].*$", "", pg.title()).strip() or "LiveBall"
        finally:
            b.close()
    if not found:
        raise Exception("liveball: stream not found (maybe login required in your country)")
    pick = next((r for r in found if "play.m3u8" in r.url), found[0])
    hdr = {"User-Agent": ua, "Referer": pick.headers.get("referer", "https://liveball.sx/")}
    res = {"title": title, "stream_url": pick.url, "headers": hdr, "ts": time.time()}
    _liveball_cache[url] = res
    return res
