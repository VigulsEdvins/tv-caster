import base64
import json
import re
import urllib.parse
import urllib.request

from extractors.net import fetch_page


def is_seasonvar(url):
    return "seasonvar." in (url or "")

def _sv_file(f):
    """Playerjs '#2' links: base64 with '//b2xvbG8=' junk spliced in."""
    if f.startswith("#2"):
        f = base64.b64decode(f[2:].replace("//b2xvbG8=", "") + "===").decode("utf-8")
    return "https:" + f if f.startswith("//") else f

def _sv_flatten(items):
    """Playlists may nest episodes in folders."""
    for it in items:
        if isinstance(it.get("folder"), list):
            yield from _sv_flatten(it["folder"])
        else:
            yield it

def seasonvar_structure(url):
    """Series info + direct MP4 per episode and voice-over from a seasonvar.ru season page.
    shortcut: voice-over names/folders are read blindly (only a single-voice page was seen), check on a multi-voice series."""
    ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    page = fetch_page(url)
    # the default playlist is a dict entry, the other voice-overs are separate `pl[144] = "..."` assignments
    pl = dict(re.findall(r"""['"](\d+)['"]\s*:\s*["'](/playls2/[^"']+/plist\.txt[^"']*)["']""", page))
    pl.update(re.findall(r"""pl\[(\d+)\]\s*=\s*["'](/playls2/[^"']+/plist\.txt[^"']*)["']""", page))
    if not pl:
        raise Exception("Could not find seasonvar playlist")
    names = {tid: re.sub(r"<[^>]+>", "", nm).strip() for tid, nm in re.findall(r'<li[^>]*data-translate="(\d+)"[^>]*>(.*?)</li>', page, re.S)}
    t = re.search(r"<title>(.*?)</title>", page, re.S)
    title = re.sub(r"\s*(Сериал|смотреть онлайн).*$", "", re.sub(r"^\s*Сериал\s+", "", t.group(1) if t else "Seasonvar"), flags=re.S).strip() or "Seasonvar"
    title = re.sub(r"\s+\d+\s*сезон.*$", "", title).strip() or title  # "Гангстерленд 1 сезон MobLand" -> "Гангстерленд"
    sm = re.search(r"(\d+)\s*сезон", t.group(1) if t else "")
    season = int(sm.group(1)) if sm else 1
    files, voices = {}, []
    for tid, path in pl.items():
        if names.get(tid) == "Трейлеры":
            continue  # not a voice-over: a one-episode playlist of trailers
        req = urllib.request.Request(urllib.parse.urljoin(url, path), headers={**ua, "Referer": url})
        with urllib.request.urlopen(req, timeout=15) as r:
            items = list(_sv_flatten(json.loads(r.read().decode("utf-8"))))
        files[tid] = {}
        for i, it in enumerate(items, 1):
            n = int((re.match(r"\s*(\d+)", it.get("title", "")) or [0, i])[1])
            files[tid]["s%02de%02d" % (season, n)] = _sv_file(it.get("file", ""))
        voices.append({"id": tid, "title": names.get(tid) or ("Стандартный" if tid == "0" else f"Перевод {tid}")})
    first = files[voices[0]["id"]]
    if not first:
        raise Exception("seasonvar: empty playlist")
    th = re.search(r'property="og:image" content="([^"]+)"', page)
    thumb = th.group(1) if th else ""
    # the files carry their quality in the name (…1080p…, …720p…); the list shown for choosing must not promise more than exists
    found = {int(h) for fm in files.values() for f in fm.values() for h in re.findall(r"(?<!\d)(\d{3,4})p", f.rsplit("/", 1)[-1])}
    qualities = [f"{h}p" for h in sorted(found, reverse=True)] or ["1080p"]
    eps = [{"id": e, "title": f"Серия {int(e[4:])}", "translations": voices if len(voices) > 1 else []} for e in first]
    return {"title": title, "kind": "series", "seasons": [{"id": "s%02d" % season, "title": f"Сезон {season}", "episodes": eps}],
            "translations": voices if len(voices) > 1 else [], "files": files, "qualities": qualities, "thumb": "https:" + thumb if thumb.startswith("//") else thumb}
