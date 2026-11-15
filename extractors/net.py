import gzip
import urllib.error
import urllib.request


def urlopen_fast(req, connect_timeout=4, read_timeout=30, attempts=3, timeout=None):
    """urlopen that gives up on a dead address after a few seconds (servers with several A records)."""
    if timeout is not None:
        connect_timeout = timeout
        read_timeout = timeout
    last = None
    for _ in range(attempts):
        try:
            r = urllib.request.urlopen(req, timeout=connect_timeout)
            try:
                r.fp.raw._sock.settimeout(read_timeout)
            except Exception:
                pass
            return r
        except urllib.error.HTTPError:
            raise
        except Exception as e:
            last = e
    raise last


def fetch_page(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen_fast(req, connect_timeout=6, read_timeout=timeout) as resp:
        data = resp.read()
    try:
        return gzip.decompress(data).decode("utf-8", errors="ignore")
    except Exception:
        return data.decode("utf-8", errors="ignore")
