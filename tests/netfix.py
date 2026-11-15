"""Record/replay the network for tests, so they pass without internet and do not break when a site changes.

Replay (default): `with netfix.replay(): ...` answers urllib.request.urlopen, fetch_page, curl_fetch and the yt-dlp/node
subprocess calls from tests/fixtures/net.json (+ pages.json); anything not recorded raises AssertionError.
Record: `python3 tests/netfix.py` re-runs the scenarios below against the live sites and rewrites net.json.
"""
import contextlib
import functools
import io
import json
import os
import re
import subprocess
import sys
import urllib.request
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
NET_JSON = os.path.join(FIX, "net.json")
PAGES_JSON = os.path.join(FIX, "pages.json")
PAGE_FUNCS = ("fetch_page", "curl_fetch")

SCENARIOS = {  # name -> url passed to server.inspect_url
    "sportacentrs": "https://sportacentrs.com/sportacentrs_tv/live/29102026-b_ezerzeme_du_homelike_robezsardze_rsu_b_",
    "facebook": "https://www.facebook.com/vklotos/videos/2142310449990727/",
    "lsm": "https://replay.lsm.lv/lv/skaties/tiesraide/ltv7/merdoka-noslepumi-9-detektivserials-originalvalodas-izvele",
    "kinozed": "https://hd.kinozed.org/movie/ulica-straha-koroleva-vypusknogo-2025/",
}


class FakeResp(io.BytesIO):
    status = 200
    headers = {}
    url = ""

    def geturl(self):
        return self.url

    def info(self):
        return self.headers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return 200


_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def scrub(text):
    """Fixtures are committed: drop tokens (they embed the client's IP) and any IPv4 address (TEST-NET-3 placeholder)."""
    return _IPV4.sub("203.0.113.1", _JWT.sub("JWT", text))


def _req_key(req):
    if isinstance(req, str):
        return scrub(req)
    data = req.data.decode("utf-8", "ignore") if getattr(req, "data", None) else ""
    return scrub(req.full_url + ("|POST|" + data if data else ""))


def _run_key(cmd):
    cmd = [str(c) for c in cmd]
    return scrub(os.path.basename(cmd[0]) + "|" + "|".join(a for a in cmd[1:] if not a.startswith("/") and not a.startswith("node:")))


def _load(path):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}


@contextlib.contextmanager
def _patched(fake_urlopen, fake_run, fake_pages):
    real_run = subprocess.run
    patches = [mock.patch.object(urllib.request, "urlopen", fake_urlopen)]

    def run(cmd, *a, **kw):
        if isinstance(cmd, (list, tuple)) and os.path.basename(str(cmd[0])) in ("yt-dlp", "node"):
            return fake_run(cmd, real_run, *a, **kw)
        return real_run(cmd, *a, **kw)
    patches.append(mock.patch.object(subprocess, "run", run))
    import server
    import extractors.net as net
    originals = {name: getattr(net, name) for name in PAGE_FUNCS}
    for mod in list(sys.modules.values()):  # every module that imported the helpers by name gets the fake
        for name in PAGE_FUNCS:
            if mod is not None and getattr(mod, name, None) is originals[name]:
                patches.append(mock.patch.object(mod, name, fake_pages(name, originals[name])))
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in reversed(patches):
            p.stop()


@contextlib.contextmanager
def replay():
    net, pages = _load(NET_JSON), _load(PAGES_JSON)

    def urlopen(req, *a, **kw):
        key = _req_key(req)
        if key not in net.get("urlopen", {}):
            raise AssertionError("urlopen not in fixtures: " + key)
        resp = FakeResp(net["urlopen"][key].encode("utf-8"))
        resp.url = key.split("|POST|")[0]
        return resp

    def run(cmd, real_run, *a, **kw):
        key = _run_key(cmd)
        if key not in net.get("run", {}):
            raise AssertionError("subprocess not in fixtures: " + key)
        rec = net["run"][key]
        return subprocess.CompletedProcess(cmd, rec["returncode"], rec["stdout"], rec["stderr"])

    def pages_fn(name, original):
        def fn(url, *a, **kw):
            if scrub(url) not in pages:
                raise AssertionError("page not in fixtures: " + url)
            return pages[scrub(url)]
        return fn
    with _patched(urlopen, run, pages_fn):
        yield


def offline(fn):
    """Decorator: run a test with the recorded network."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        with replay():
            return fn(*a, **kw)
    return wrapper


def _record():
    net = {"urlopen": {}, "run": {}}
    pages = {scrub(k): scrub(v) for k, v in _load(PAGES_JSON).items()}
    real_urlopen = urllib.request.urlopen

    def urlopen(req, *a, **kw):
        resp = real_urlopen(req, *a, **kw)
        body = resp.read()
        net["urlopen"][_req_key(req)] = scrub(body.decode("utf-8", "ignore"))
        out = FakeResp(body)
        out.url = _req_key(req).split("|POST|")[0]
        return out

    def run(cmd, real_run, *a, **kw):
        p = real_run(cmd, *a, **kw)
        net["run"][_run_key(cmd)] = {"returncode": p.returncode, "stdout": scrub(p.stdout or ""), "stderr": scrub(p.stderr or "")}
        return p

    def pages_fn(name, original):
        def fn(url, *a, **kw):
            text = original(url, *a, **kw)
            pages[scrub(url)] = scrub(text)
            return text
        return fn
    import server
    with _patched(urlopen, run, pages_fn):
        for name, url in SCENARIOS.items():
            try:
                server.inspect_url(url)
                if name == "kinozed":
                    server.normalize_url(url)
                print("recorded", name)
            except Exception as e:
                print("FAILED", name, e)
    json.dump(net, open(NET_JSON, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(pages, open(PAGES_JSON, "w", encoding="utf-8"), ensure_ascii=False)
    print({k: len(v) for k, v in net.items()}, "pages:", len(pages))


if __name__ == "__main__":
    _record()
