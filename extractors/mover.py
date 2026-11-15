import base64
import json
import re
import urllib.request


def is_mover(url):
    return bool(re.search(r'mover\.uz/(watch|video/embed)/', url or ""))

def extract_mover(url, quality="720p"):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=12) as r:
        html = r.read().decode("utf-8", errors="ignore")
    m = re.search(r'data-config="#2([^"]+)"', html)
    if not m:
        m_embed = re.search(r'new Playerjs\(.#2([^"\x27]+)', html)
        if not m_embed:
            raise Exception("Could not find mover.uz video player config")
        payload = m_embed.group(1)
    else:
        payload = m.group(1)
    clean = re.sub(r"//.{32}", "", payload)
    clean += "=" * ((4 - len(clean) % 4) % 4)
    cfg = json.loads(base64.b64decode(clean).decode("utf-8"))
    title = cfg.get("title") or "Mover.uz Video"
    file_spec = cfg.get("file", "")
    streams = {}
    if "[" in file_spec:
        for q_match, u_match in re.findall(r"\[([0-9]+p)\](https?://[^\s,]+)", file_spec):
            streams[q_match] = u_match
    elif file_spec.startswith("http"):
        streams["720p"] = file_spec
    qualities = list(streams.keys()) or ["720p"]
    qualities.sort(key=lambda x: int(re.sub(r"\D", "", x) or 0), reverse=True)
    chosen = streams.get(quality) or (streams.get("720p") if "720p" in streams else None) or (streams[qualities[0]] if streams else file_spec)
    return title, chosen, qualities, cfg.get("duration", 0), cfg.get("poster")
