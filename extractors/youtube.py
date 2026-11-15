import os
import subprocess
import sys
import threading
import time
from extractors.tools import YT_DLP_BASE, YT_DLP_BIN


_ytdlp_cache = {}

UPDATE_COOLDOWN = 6 * 3600  # at most one pip upgrade per 6 h, so a persistent failure cannot loop
# YouTube changes its player often and an old yt-dlp then fails like this; a removed/private video does not, so no update for those
OUTDATED_MARKERS = ("unable to extract", "nsig", "http error 403", "challenge solving failed",
                    "requested format is not available", "no video formats", "player response")
_update = {"last": 0.0}
_update_lock = threading.Lock()

def looks_outdated(message):
    m = (message or "").lower()
    return any(k in m for k in OUTDATED_MARKERS)

def _pip_python():
    for name in ("python3", "python"):
        p = os.path.join(os.path.dirname(YT_DLP_BIN), name)  # the interpreter of the venv that owns yt-dlp
        if os.path.exists(p):
            return p
    return sys.executable

def ytdlp_version():
    try:
        return subprocess.run([YT_DLP_BIN, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""

def update_ytdlp(now=None):
    """pip install -U yt-dlp; True if the installed version changed. Rate limited by UPDATE_COOLDOWN."""
    with _update_lock:
        now = time.time() if now is None else now
        if now - _update["last"] < UPDATE_COOLDOWN:
            return False
        _update["last"] = now
        before = ytdlp_version()
        try:
            subprocess.run([_pip_python(), "-m", "pip", "install", "-U", "yt-dlp", "--quiet"], capture_output=True, text=True, timeout=300)
        except Exception as e:
            print("yt-dlp update failed:", e, flush=True)
            return False
        after = ytdlp_version()
        print(f"yt-dlp update: {before} -> {after}", flush=True)
        return bool(after) and after != before

def ytdlp_streams(url, quality):
    """(title, [direct media URLs], duration) preferring H.264 video + AAC audio; cached 20 min so seeks skip yt-dlp."""
    hit = _ytdlp_cache.get((url, quality))
    if hit and time.time() - hit[0] < 1200:
        return hit[1]
    try:
        res = _ytdlp_streams(url, quality)
    except Exception as e:
        if not looks_outdated(str(e)) or not update_ytdlp():
            raise
        res = _ytdlp_streams(url, quality)  # one retry with the freshly updated yt-dlp
    _ytdlp_cache[(url, quality)] = (time.time(), res)
    return res

def _ytdlp_streams(url, quality):
    q = quality.replace("p", "") if quality else "720"
    fmt = (f"bv*[height<={q}][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<={q}][vcodec^=avc1]/"
           f"bv*[height<={q}]+ba/b[height<={q}]/b")
    proc = subprocess.run(YT_DLP_BASE + ["--no-playlist", "--print", "title", "--print", "duration", "--print", "urls", "-f", fmt, url],
                          capture_output=True, text=True, timeout=90)
    lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    if len(lines) < 3:
        last = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        raise Exception("yt-dlp could not extract: " + last)
    try:
        dur = float(lines[1])
    except ValueError:
        dur = 0.0
    return lines[0], lines[2:4], dur
