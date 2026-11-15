import concurrent.futures
import gzip
import re
import time
import urllib.request

from extractors.net import fetch_page


_rezka_streams_cache = {}

def extract_rezka(url, quality="720p"):
    now = time.time()
    cached = _rezka_streams_cache.get(url)
    if cached and (now - cached[2]) < 1800:
        title, streams_map = cached[0], cached[1]
    else:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
            try:
                html_text = gzip.decompress(data).decode('utf-8', errors='ignore')
            except Exception:
                html_text = data.decode('utf-8', errors='ignore')

        m = re.search(r'\"streams\":\s*\"([^\"]+)\"', html_text)
        if not m:
            raise Exception("Could not locate stream metadata on page")

        streams_raw = m.group(1).replace(r'\/', '/')
        title_m = re.search(r'<title>(.*?)</title>', html_text)
        raw_title = title_m.group(1) if title_m else "Web Episode"
        title = re.sub(r'смотреть онлайн.*$', '', raw_title).strip()
        title = title.replace('Смотреть сериал ', '').strip()
        title = re.sub(r'\s*онлайн.*$', '', title).strip()

        streams_map = {}
        for item in streams_raw.split(','):
            m_q = re.match(r'\[(\w+)\]', item)
            if m_q:
                q = m_q.group(1)
                for l in item.split(' or '):
                    clean_l = re.sub(r'^\[\w+\]', '', l).strip()
                    if clean_l.endswith('.mp4'):
                        streams_map[q] = clean_l
                        break
        if streams_map:
            _rezka_streams_cache[url] = (title, streams_map, now)

    q_clean = quality.lower().replace(" ", "").replace("fullhd", "").replace("hd", "")
    chosen = streams_map.get(q_clean) or streams_map.get('720p') or streams_map.get('480p') or streams_map.get('1080p') or list(streams_map.values())[0]
    return f"{title} [{quality}]", chosen, list(streams_map.keys())

REZKA_URL_RE = re.compile(r'^(https?://[^/]+)/([^/]+)/(\d+)-season(?:/(\d+)-episode)?')

def is_rezka(url):
    return "tulsa-king.net" in url or "rezka" in url

def rezka_episode_id(url):
    m = REZKA_URL_RE.match(url)
    if m and m.group(4):
        return "s%02de%02d" % (int(m.group(3)), int(m.group(4)))
    return None

def rezka_target_url(url, episode, voice=None):
    """Page URL for the chosen season/episode/voice (voice = the '623-1win-studio' path segment)."""
    m = REZKA_URL_RE.match(url)
    em = re.match(r'^s(\d+)e(\d+)$', episode or "")
    if not m or not em:
        return url
    slug = voice if voice and re.match(r'^\d+-[\w-]+$', voice) else m.group(2)
    return f"{m.group(1)}/{slug}/{int(em.group(1))}-season/{int(em.group(2))}-episode"

def rezka_structure(url):
    """Seasons, episodes and voice-overs of a series page (same shape as the Kinogo info)."""
    m = REZKA_URL_RE.match(url)
    if not m:
        return None
    base, cur_voice, cur_season = m.group(1), m.group(2), int(m.group(3))
    page = fetch_page(url)
    title_m = re.search(r"<title>(.*?)</title>", page, re.S)
    title = re.sub(r"\s*(онлайн|смотреть онлайн).*$", "", (title_m.group(1) if title_m else "Series"), flags=re.S).replace("Смотреть сериал ", "").strip()
    title = re.sub(r"\s+\d+\s+сезон.*$", "", title).strip() or title

    voices, seen = [], set()
    for href, name in re.findall(r'<a[^>]+href="/([^"/]+)/[^"]*"[^>]*class="voiceover-item[^"]*"[^>]*>\s*<span class="v-title">(.*?)</span>', page, re.S):
        if href not in seen:
            seen.add(href)
            voices.append({"id": href, "title": re.sub(r"<[^>]+>", "", name).strip()})
    voices.sort(key=lambda v: v["id"] != cur_voice)  # the voice of the pasted link first = default

    seasons_found = []
    for n in re.findall(r'class="season-list-item[^"]*"[^>]*data-value="(\d+)"', page):
        if int(n) not in seasons_found:
            seasons_found.append(int(n))
    if not seasons_found:
        seasons_found = sorted({int(n) for n in re.findall(r'/(\d+)-season', page)}) or [cur_season]

    def episodes_of(n):
        html_n = page if n == cur_season and "episode-list-item" in page else fetch_page(f"{base}/{cur_voice}/{n}-season")
        nums = []
        for e in re.findall(rf'href="[^"]*?/{n}-season/(\d+)-episode"[^>]*class="episode-list-item', html_n):
            if int(e) not in nums:
                nums.append(int(e))
        return n, sorted(nums)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        found = dict(ex.map(episodes_of, seasons_found))
    seasons = [{
        "id": "s%02d" % n, "title": f"Сезон {n}",
        "episodes": [{"id": "s%02de%02d" % (n, e), "title": f"Серия {e}", "translations": voices} for e in found.get(n, [])],
    } for n in seasons_found if found.get(n)]
    if not seasons:
        raise Exception("Could not locate stream metadata on page")
    return {"title": title, "kind": "series", "seasons": seasons, "translations": voices}
