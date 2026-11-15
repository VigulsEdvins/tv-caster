import subprocess
import time
from extractors.tools import YT_DLP_BASE


_ytdlp_cache = {}

def ytdlp_streams(url, quality):
    """(title, [direct media URLs], duration) preferring H.264 video + AAC audio; cached 20 min so seeks skip yt-dlp."""
    hit = _ytdlp_cache.get((url, quality))
    if hit and time.time() - hit[0] < 1200:
        return hit[1]
    res = _ytdlp_streams(url, quality)
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
