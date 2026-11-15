import re
import shutil
import time


def is_liveball(url):
    return "liveball." in (url or "")

_liveball_cache = {}

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
