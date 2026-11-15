import json
import re
import subprocess
import threading
import time
from extractors.tools import YT_DLP_BASE


_fb_cache = {}

_fb_cache_lock = threading.Lock()

def is_facebook(url):
    if not url:
        return False
    u = str(url).lower()
    return bool(re.search(r'(facebook\.com|fb\.watch|fb\.com)', u))

def extract_facebook(url, quality="720p"):
    q_val = quality.replace("p", "") if quality else "720"
    fmt = f"bv*[height<={q_val}]+ba/b[height<={q_val}]/bv*+ba/b"
    cache_key = f"{url}|{quality}"
    with _fb_cache_lock:
        cached = _fb_cache.get(cache_key)
        if cached and (time.time() - cached["ts"]) < 300:
            return cached["data"]

    cmd_info = YT_DLP_BASE + ["--dump-single-json", "--skip-download", url]
    proc_info = subprocess.run(cmd_info, capture_output=True, text=True, timeout=20)
    if proc_info.returncode != 0:
        err = (proc_info.stderr.strip().splitlines() or ["Facebook extraction failed"])[-1]
        raise Exception(f"Facebook extraction error: {err}")
    data = json.loads(proc_info.stdout)
    title = data.get("title") or "Facebook Video"
    dur = float(data.get("duration") or 0.0)
    thumb = data.get("thumbnail") or ""
    is_live = bool(data.get("is_live"))

    cmd_url = YT_DLP_BASE + ["-f", fmt, "--get-url", url]
    proc_url = subprocess.run(cmd_url, capture_output=True, text=True, timeout=20)
    lines = [l.strip() for l in proc_url.stdout.strip().splitlines() if l.strip()]
    if not lines:
        raise Exception("Could not retrieve Facebook stream URL")
    stream_url = lines[0]
    kind = "dash" if (".mpd" in stream_url or "/dash/" in stream_url) else ("hls" if ".m3u8" in stream_url else "mp4")
    res = {
        "title": title,
        "stream_url": stream_url,
        "kind": kind,
        "duration": dur,
        "thumb": thumb,
        "is_live": is_live,
    }
    with _fb_cache_lock:
        _fb_cache[cache_key] = {"data": res, "ts": time.time()}
    return res
