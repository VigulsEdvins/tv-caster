import html
import json
import re
import urllib.request
from extractors.net import curl_fetch, fetch_page, urlopen_fast


def is_nextembed(url):
    u = url or ""
    return "nextembed" in u or "kinogoo." in u or "gidonline" in u or "kinogram" in u or "synchroncode" in u or "moy.su" in u or "kinogo-hd" in u

def _ep_num(val):
    m = re.search(r"\d+", str(val or "0"))
    return int(m.group(0)) if m else 0

def nextembed_data(url):
    """(title, seasons, movie) for an embed URL: seasons for a series, movie={'hls','names','thumb'} for a film."""
    try:
        page = fetch_page(url)
        if "nextembed" not in url and "kinogram" not in url and "synchroncode" not in url:
            em_iframe = re.search(r'(?:data-src|src)=["\'](https?://[^"\']*(?:nextembed|kinogram|synchroncode)\.[^"\']+)["\']', page)
            if em_iframe:
                page = fetch_page(html.unescape(em_iframe.group(1)))
            else:
                next_m = re.search(r'(?:https?:)?(//(?:api\.)?nextembed\.ws/embed/[^"\'\s<>]+)', page)
                if next_m:
                    em_url = "https:" + next_m.group(1) if next_m.group(1).startswith("//") else next_m.group(1)
                    page = fetch_page(em_url)
                elif "gidonline" in url:
                    kp_m = re.search(r'(\d{3,8})\?partner=gidonline', page)
                    if kp_m:
                        embed_url = f"https://api.kinogram.best/embed/kp/{kp_m.group(1)}"
                        page = fetch_page(embed_url)
                    else:
                        tm_raw = re.search(r'property=["\x27]og:title["\x27]\s+content=["\x27]([^"\x27]+)["\x27]', page)
                        t_val = tm_raw.group(1).strip() if tm_raw else ""
                        if t_val:
                            link_api = f"https://api.kinogram.best/autochange/info/link?title={urllib.parse.quote(t_val)}"
                            req = urllib.request.Request(link_api, headers={"User-Agent": "Mozilla/5.0"})
                            with urlopen_fast(req, connect_timeout=4, read_timeout=6) as r:
                                data = json.loads(r.read().decode("utf-8", errors="ignore"))
                                if data.get("url"):
                                    page = fetch_page(data["url"])
                else:
                    kp_m = re.search(r'kp[=/](\d{3,8})', page)
                    if kp_m:
                        page = fetch_page(f"https://api.nextembed.ws/embed/kp/{kp_m.group(1)}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise Exception("not in player: this title is not available")
        raise
    tm = re.search(r"<title>(.*?)</title>", page, re.S)
    title = re.sub(r"\s*\(\d+\s+сезон\).*$", "", html.unescape(tm.group(1)).strip()) if tm else "Video"
    title = re.sub(r"\s*-\s*смотреть онлайн.*$", "", title, flags=re.I).strip()
    pm = re.search(r'poster:\s*["\x27]([^"\x27]+)["\x27]', page)
    thumb = pm.group(1).replace("\\/", "/") if pm else ""
    sm = re.search(r'\bseasons:\s*\[', page)  # the site switched from "seasons:[" to "seasons: [" at some point
    if sm:
        seasons, _ = json.JSONDecoder().raw_decode(page[sm.end() - 1:])
        seasons = [sn for sn in seasons if not sn.get("blocked")]
        seasons.sort(key=lambda sn: _ep_num(sn["season"]))
        for sn in seasons:
            sn["episodes"].sort(key=lambda e: _ep_num(e["episode"]))
        return title, seasons, None
    hm = re.search(r'hls:\s*"([^"]+master\.m3u8[^"]*)"', page)
    if not hm:
        raise Exception("Could not locate stream metadata on page")
    names = []
    am = re.search(r'audio:\s*(\{)', page[hm.start():])
    if am:
        try:
            obj, _ = json.JSONDecoder().raw_decode(page[hm.start() + am.start(1):])
            names = obj.get("names") or []
        except ValueError:
            pass
    names = [n for n in names if n and n.lower() != "delete"]
    return title, None, {"hls": hm.group(1), "names": names, "thumb": thumb}

def nextembed_structure(url):
    title, seasons, movie = nextembed_data(url)
    if movie:
        thumb = movie.get("thumb") or ""
        if not thumb or thumb.startswith("data:image"):
            kp_m = re.search(r'(?:kp[=/]|movie/)(\d{3,8})', url)
            if kp_m:
                thumb = f"https://st.kp.yandex.net/images/film_iphone/iphone360_{kp_m.group(1)}.jpg"
        return {"title": title, "kind": "movie", "seasons": [],
                "translations": [{"id": str(i), "title": nm} for i, nm in enumerate(movie["names"])],
                "thumb": thumb}
    out = []
    for sn in seasons:
        n = _ep_num(sn["season"])
        eps = []
        for e in sn["episodes"]:
            names = (e.get("audio") or {}).get("names") or []
            names = [n for n in names if n and n.lower() != "delete"]
            ep_n = _ep_num(e["episode"])
            eps.append({"id": "s%02de%02d" % (n, ep_n), "title": f"Серия {e['episode']}",
                        "translations": [{"id": str(i), "title": nm} for i, nm in enumerate(names)]})
        out.append({"id": "s%02d" % n, "title": f"Сезон {n}", "episodes": eps})
    kp_m = re.search(r'(?:kp[=/]|movie/)(\d{3,8})', url)
    thumb = f"https://st.kp.yandex.net/images/film_iphone/iphone360_{kp_m.group(1)}.jpg" if kp_m else (movie.get("thumb") if movie else "")
    return {"title": title, "kind": "series", "seasons": out, "translations": [], "thumb": thumb}

def nextembed_resolve(url, episode, voice_id=None, voice_title=None):
    """Fresh HLS master URL (tokens expire) and the audio track index for the chosen episode/voice."""
    title, seasons, movie = nextembed_data(url)
    if movie:
        names, hls, label, ep_id = movie["names"], movie["hls"], title, None
    else:
        em = re.match(r'^s(\d+)e(\d+)$', episode or "")
        ep = None
        for sn in seasons:
            for e in sn["episodes"]:
                if em and _ep_num(sn["season"]) == int(em.group(1)) and _ep_num(e["episode"]) == int(em.group(2)):
                    ep = (sn, e)
        if not ep:
            sm = re.search(r'(?:season=(\d+)|-(\d+)-sezon)', url or "", re.I)
            want_sn = int(sm.group(1) or sm.group(2)) if sm else None
            sn = next((s for s in seasons if _ep_num(s["season"]) == want_sn), seasons[0]) if want_sn else seasons[0]
            ep = (sn, sn["episodes"][0])
        sn, e = ep
        names = (e.get("audio") or {}).get("names") or []
        names = [n for n in names if n and n.lower() != "delete"]
        hls = e["hls"]
        label = f"{title} - Сезон {_ep_num(sn['season'])}, Серия {e['episode']}"
        ep_id = "s%02de%02d" % (_ep_num(sn["season"]), _ep_num(e["episode"]))
    track = 0
    if voice_id and str(voice_id).isdigit() and int(voice_id) < len(names):
        track = int(voice_id)
    if voice_title in names:
        track = names.index(voice_title)
    if names:
        label += f" ({names[track]})"
    return label, hls, track, ep_id

def parse_master(master, master_url):
    """([(height, url)], [(name, url)]) from an HLS master playlist; the first mirror of each is kept."""
    lines = master.splitlines()
    variants, audios, seen_v, seen_a = [], [], set(), set()
    for i, l in enumerate(lines):
        if l.startswith("#EXT-X-STREAM-INF"):
            hm = re.search(r"RESOLUTION=\d+x(\d+)", l)
            height = int(hm.group(1)) if hm else 0
            if height not in seen_v and i + 1 < len(lines):
                seen_v.add(height)
                variants.append((height, urllib.parse.urljoin(master_url, lines[i + 1].strip())))
        elif l.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in l:
            nm = re.search(r'NAME="([^"]+)"', l)
            um = re.search(r'URI="([^"]+)"', l)
            if nm and um and nm.group(1) not in seen_a:
                seen_a.add(nm.group(1))
                audios.append((nm.group(1), urllib.parse.urljoin(master_url, um.group(1))))
    return variants, audios

def media_segments(playlist_url, headers):
    body = curl_fetch(playlist_url, headers, max_time=15)
    base = playlist_url.rsplit("?", 1)[0].rsplit("/", 1)[0] + "/"
    segs = [urllib.parse.urljoin(base, l.strip()) for l in body.splitlines() if l.strip() and not l.startswith("#")]
    if not segs:
        raise Exception("No TS segments found in playlist")
    return segs
