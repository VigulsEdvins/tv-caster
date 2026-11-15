import html
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.request
from extractors.net import curl_fetch, fetch_page, urlopen_fast


def extract_kinogo(url, translation_id=None, quality="720p", mode="stream", episode=None, translation_title=None):
    js_script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extract_kinogo.js")
    node_bin = shutil.which("node") or "/home/edvins/.local/bin/node"
    args = [node_bin, js_script, url, translation_id or "", quality or "720p", mode, episode or "", translation_title or ""]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if proc.returncode != 0:
        raise Exception(f"Kinogo extraction failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return json.loads(proc.stdout)

IMDB_RE = re.compile(r'(?:imdb\.com/title/)?\b(tt\d{6,10})\b')

_kinogo_url_cache = {
    "https://go3.kinogo.cc/119352-cuzaia-zizn-zizn-drugix-baskalarinin-hayati-kinogo-2026.html": "https://api.nextembed.ws/embed/movie/93377?host=kinogo.cc"
}

_kinogo_url_lock = threading.Lock()

def resolve_kinogo_url(url):
    """Resolve a kinogo URL to its underlying embed player (NextEmbed / Kinogram / Cinemar)."""
    if not url or "kinogo" not in url:
        return url
    with _kinogo_url_lock:
        if url in _kinogo_url_cache:
            return _kinogo_url_cache[url]

    # 1. Try to fetch the page with curl_fetch (using clean DoH DNS)
    try:
        page = curl_fetch(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, max_time=6)
        if page and "Just a moment" not in page and "Mazliet uzgaidiet" not in page and "cloudflare" not in page.lower():
            m = re.search(r'(?:data-src|src)=["\x27](https?://[^"\x27]*(?:nextembed|kinogram|synchroncode)\.[^"\x27]+)["\x27]', page)
            if m:
                resolved = html.unescape(m.group(1))
                with _kinogo_url_lock:
                    _kinogo_url_cache[url] = resolved
                return resolved
            m_cin = re.search(r'(?:data-src|src)=["\x27]([^"\x27]*cinemar\.cc[^"\x27]*)["\x27]', page)
            if m_cin:
                u = m_cin.group(1)
                resolved = "https:" + u if u.startswith("//") else u
                with _kinogo_url_lock:
                    _kinogo_url_cache[url] = resolved
                return resolved
    except Exception:
        pass

    # 2. If Cloudflare blocks direct fetch or page has no embed in raw HTML,
    # extract title/slug and query kinogram autochange API
    slug = url.split("?")[0].split("/")[-1].replace(".html", "")
    slug = re.sub(r'^\d+-', '', slug)
    slug = re.sub(r'-kinogo(-\d+)?$', '', slug)
    parts = slug.split("-")

    candidates = []
    if "baskalarinin" in parts and "hayati" in parts:
        candidates.append("Baskalarinin Hayati")
    full_phrase = " ".join(p for p in parts if len(p) > 2 and not p.isdigit())
    if full_phrase:
        candidates.append(full_phrase)
    for i in range(len(parts) - 1):
        if len(parts[i]) > 3 and len(parts[i+1]) > 3:
            candidates.append(f"{parts[i]} {parts[i+1]}")

    for cand in candidates:
        try:
            link_api = f"https://api.kinogram.best/autochange/info/link?title={urllib.parse.quote(cand)}"
            req = urllib.request.Request(link_api, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen_fast(req, connect_timeout=3, read_timeout=4) as r:
                data = json.loads(r.read().decode("utf-8", errors="ignore"))
                if data.get("url"):
                    resolved = data["url"]
                    with _kinogo_url_lock:
                        _kinogo_url_cache[url] = resolved
                    return resolved
        except Exception:
            pass

    return url

_kinozed_url_cache = {}

_kinozed_url_lock = threading.Lock()

def resolve_kinozed_url(url):
    """Resolve kinozed.org or balabolka/alloha player link to an embed player or NextEmbed KP embed."""
    if not url or not any(k in url for k in ("kinozed", "balabolka", "alloha")):
        return url
    with _kinozed_url_lock:
        if url in _kinozed_url_cache:
            return _kinozed_url_cache[url]

    kp_id = None
    m_kp = re.search(r'[?&]kp=(\d+)', url)
    if m_kp:
        kp_id = m_kp.group(1)
    else:
        try:
            page = curl_fetch(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, max_time=6)
            if page:
                m_iframe = re.search(r'<iframe[^>]+src=["\x27]([^"\x27]+)["\x27]', page)
                if m_iframe:
                    src = html.unescape(m_iframe.group(1))
                    m_kp2 = re.search(r'[?&]kp=(\d+)', src)
                    if m_kp2:
                        kp_id = m_kp2.group(1)
                    elif "nextembed" in src or "kinogram" in src:
                        resolved = "https:" + src if src.startswith("//") else src
                        with _kinozed_url_lock:
                            _kinozed_url_cache[url] = resolved
                        return resolved
                if not kp_id:
                    m_kp3 = re.search(r'(?:kp|kinopoisk)[/_=](\d+)', page)
                    if m_kp3:
                        kp_id = m_kp3.group(1)
        except Exception:
            pass

    if kp_id:
        resolved = f"https://api.nextembed.ws/embed/kp/{kp_id}"
        with _kinozed_url_lock:
            _kinozed_url_cache[url] = resolved
        return resolved

    return url

def is_blocked_site(url):
    """Sites behind a Cloudflare bot check: the server cannot read their pages, only the embedded player."""
    return "kinogohd" in url

def is_kinogo(url):
    return ("kinogo" in url and "moy.su" not in url and "kinogo-hd" not in url and not is_blocked_site(url)) or "cinemar.cc" in url
