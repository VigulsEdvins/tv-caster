import gzip
import json
import re
import socket
import subprocess
import time
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


DNS_CACHE = {}

def get_clean_ip(hostname):
    if not hostname:
        return None
    if hostname in DNS_CACHE:
        return DNS_CACHE[hostname]
    try:
        req = urllib.request.Request(
            f"https://dns.google/resolve?name={hostname}&type=A",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            data = json.loads(r.read().decode())
            for a in data.get("Answer", []):
                if a.get("type") == 1:
                    ip = a.get("data")
                    DNS_CACHE[hostname] = ip
                    return ip
    except Exception as e:
        print("DoH error for", hostname, e)
    return None

_orig_getaddrinfo = socket.getaddrinfo

def _custom_getaddrinfo(host, port, *args, **kwargs):
    try:
        return _orig_getaddrinfo(host, port, *args, **kwargs)
    except socket.gaierror:
        clean_ip = get_clean_ip(host)
        if clean_ip:
            return _orig_getaddrinfo(clean_ip, port, *args, **kwargs)
        raise

socket.getaddrinfo = _custom_getaddrinfo

def curl_fetch(url, headers=None, head_only=False, max_time=15):
    h = headers or {}
    parsed = urllib.parse.urlparse(url)
    ip = get_clean_ip(parsed.hostname)
    for _ in range(2):
        cmd = ["curl", "-k", "-s"]
        if head_only:
            cmd.append("-i")
        cmd.extend(["--max-time", str(max_time)])
        for k, v in h.items():
            cmd.extend(["-H", f"{k}: {v}"])
        if ip:
            port = parsed.port or (443 if parsed.scheme == "https" or url.startswith("https") else 80)
            cmd.extend(["--resolve", f"{parsed.hostname}:{port}:{ip}"])
        cmd.append(url)
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.stdout:
            return p.stdout
        time.sleep(0.3)
    return ""

def curl_fetch_bytes(url, headers=None, max_time=15):
    h = headers or {"User-Agent": "Mozilla/5.0"}
    parsed = urllib.parse.urlparse(url)
    ip = get_clean_ip(parsed.hostname)
    for _ in range(3):
        cmd = ["curl", "-k", "-s", "--connect-timeout", "5", "--max-time", str(max_time)]
        for k, v in h.items():
            cmd.extend(["-H", f"{k}: {v}"])
        if ip:
            port = parsed.port or (443 if parsed.scheme == "https" or url.startswith("https") else 80)
            cmd.extend(["--resolve", f"{parsed.hostname}:{port}:{ip}"])
        cmd.append(url)
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=max_time + 2)
            if p.returncode == 0 and len(p.stdout) > 0:
                return p.stdout
        except Exception:
            pass
        time.sleep(0.2)
    return b""

def resolve_redirects(url, headers):
    curr = url
    for _ in range(6):
        res = curl_fetch(curr, headers, head_only=True, max_time=5)
        m = re.search(r"(?i)location:\s*([^\r\n]+)", res)
        if m:
            loc = m.group(1).strip()
            curr = loc if loc.startswith("http") else urllib.parse.urljoin(curr, loc)
        else:
            break
    return curr

DL_HEADERS = {"User-Agent": "Mozilla/5.0"}
