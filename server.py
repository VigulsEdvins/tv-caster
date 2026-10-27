import concurrent.futures
import glob
import html
import http.cookiejar
import http.server
import json
import os
import re
import socket
import socketserver
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import shutil

TV_IP = "192.168.1.105"
TV_PORT = 38388
SERVER_PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "media_cache")
CURRENT_FILE = os.path.join(CACHE_DIR, "current.ts")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
# Optional venv with yt-dlp and imageio-ffmpeg; override with TV_CASTER_VENV
VENV_DIR = os.environ.get("TV_CASTER_VENV", "/home/edvins/.gemini/antigravity/scratch/ffmpeg_venv")
YT_DLP_BIN = shutil.which("yt-dlp") or os.path.join(VENV_DIR, "bin", "yt-dlp")
NODE_BIN = shutil.which("node") or "/home/edvins/.local/bin/node"
YT_DLP_BASE = [YT_DLP_BIN, "--no-check-certificates", "--js-runtimes", f"node:{NODE_BIN}"]
CACHE_MAX_AGE = 5 * 60  # seconds; older cache files are removed when a new cast starts

os.makedirs(CACHE_DIR, exist_ok=True)

def cleanup_cache(keep=(), max_age=CACHE_MAX_AGE):
    now = time.time()
    for name in os.listdir(CACHE_DIR):
        path = os.path.join(CACHE_DIR, name)
        try:
            if path in keep or not os.path.isfile(path):
                continue
            if now - os.path.getmtime(path) >= max_age:
                os.remove(path)
        except OSError:
            pass

# State management
app_state = {
    "status": "idle", # idle, preparing, downloading, remuxing, casting, playing, error
    "status_text": "Ready to cast",
    "title": "No media playing",
    "url": "",
    "download_progress": 0,
    "download_speed": "0 MB/s",
    "total_size": "0 MB",
    "last_error": "",
    "error_hint": "",
    "stage": "extract",  # extract, download, convert, tv
    "started_at": 0,
    "audio_tracks": [],
    "audio_index": 0,
    "opts": {},
    "episode": None,
}
active_sessions = {}
current_session_id = 0
session_lock = threading.Lock()

def get_ffmpeg():
    import sys
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    sys.path.extend(glob.glob(os.path.join(VENV_DIR, "lib", "python3*", "site-packages")))
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((TV_IP, 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "192.168.1.116"

LOCAL_IP = get_local_ip()

# ---------------- TV (DLNA renderer) discovery ----------------
TV_STATE_FILE = os.path.join(BASE_DIR, "tv.json")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
TV = {
    "ip": TV_IP,
    "name": "TV",
    "online": True,
    "controls": {
        "AVTransport": {"url": f"http://{TV_IP}:{TV_PORT}/serviceControl/AVTransport",
                        "type": "urn:schemas-upnp-org:service:AVTransport:1"},
        "RenderingControl": {"url": f"http://{TV_IP}:{TV_PORT}/serviceControl/RenderingControl",
                             "type": "urn:schemas-upnp-org:service:RenderingControl:1"},
    },
}
tv_lock = threading.Lock()
_last_discover = 0.0

def _localname(tag):
    return tag.rsplit('}', 1)[-1]

def ssdp_search(timeout=3.0):
    """Return {ip: location_url} for UPnP MediaRenderers on the LAN."""
    found = {}
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
           "MX: 2\r\nST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n").encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(LOCAL_IP))
        except OSError:
            pass
        sock.settimeout(0.5)
        for _ in range(2):
            sock.sendto(msg, ("239.255.255.250", 1900))
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            m = re.search(r'(?im)^location:\s*(\S+)', data.decode('utf-8', 'ignore'))
            if m:
                found[addr[0]] = m.group(1)
    except OSError as e:
        print("SSDP error:", e)
    finally:
        sock.close()
    return found

def describe_renderer(ip, location):
    req = urllib.request.Request(location, headers={"User-Agent": "tv-caster"})
    with urllib.request.urlopen(req, timeout=4) as r:
        root = ET.fromstring(r.read())
    name = ip
    base = location
    controls = {}
    for el in root.iter():
        tag = _localname(el.tag)
        if tag == "friendlyName" and el.text and name == ip:
            name = el.text.strip()
        elif tag == "URLBase" and el.text:
            base = el.text.strip()
        elif tag == "service":
            st = cu = None
            for ch in el:
                if _localname(ch.tag) == "serviceType":
                    st = (ch.text or "").strip()
                elif _localname(ch.tag) == "controlURL":
                    cu = (ch.text or "").strip()
            for wanted in ("AVTransport", "RenderingControl"):
                if st and cu and f":service:{wanted}:" in st:
                    controls[wanted] = {"url": urllib.parse.urljoin(base, cu), "type": st}
    return {"ip": ip, "name": name, "controls": controls}

def discover_renderers():
    devices = []
    for ip, loc in ssdp_search().items():
        try:
            dev = describe_renderer(ip, loc)
            if "AVTransport" in dev["controls"]:
                devices.append(dev)
        except Exception as e:
            print("Renderer description failed for", ip, e)
    return devices

def select_tv(dev):
    with tv_lock:
        TV["ip"] = dev["ip"]
        TV["name"] = dev["name"]
        TV["controls"] = dev["controls"]
        TV["online"] = True
    try:
        with open(TV_STATE_FILE, "w") as f:
            json.dump({"ip": dev["ip"], "name": dev["name"], "controls": dev["controls"]}, f, indent=2)
    except OSError:
        pass

def refresh_tv(prefer_ip=None):
    """Re-run discovery; keep the current/saved TV if it still answers, else pick the only one found."""
    global _last_discover
    _last_discover = time.time()
    devices = discover_renderers()
    want = prefer_ip or TV["ip"]
    for dev in devices:
        if dev["ip"] == want:
            select_tv(dev)
            return devices
    if len(devices) == 1:
        select_tv(devices[0])
    return devices

def _load_saved_tv():
    try:
        with open(TV_STATE_FILE) as f:
            saved = json.load(f)
        if saved.get("controls", {}).get("AVTransport"):
            TV.update({"ip": saved["ip"], "name": saved.get("name", saved["ip"]), "controls": saved["controls"]})
    except Exception:
        pass

def _note_tv_failure():
    TV["online"] = False
    if time.time() - _last_discover > 30:
        threading.Thread(target=refresh_tv, daemon=True).start()

_load_saved_tv()

# ---------------- Settings ----------------
settings = {"autonext": True}
try:
    with open(SETTINGS_FILE) as f:
        settings.update(json.load(f))
except Exception:
    pass

def save_settings():
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass

# ---------------- History ----------------
HISTORY_LIMIT = 50
history_lock = threading.Lock()

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def _save_history(hist):
    try:
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(hist, f, indent=2, ensure_ascii=False)
        os.replace(tmp, HISTORY_FILE)
    except Exception:
        pass

def _same(h, url, episode):
    return h.get("url") == url and (h.get("episode") or None) == (episode or None)

def add_history(title, url, episode=None, **extra):
    with history_lock:
        hist = load_history()
        old = next((h for h in hist if _same(h, url, episode)), {})
        hist = [h for h in hist if not _same(h, url, episode)]
        entry = dict(old)
        entry.update({"title": title, "url": url, "timestamp": int(time.time())})
        if episode:
            entry["episode"] = episode
        entry.update({k: v for k, v in extra.items() if v is not None})
        hist.insert(0, entry)
        _save_history(hist[:HISTORY_LIMIT])

def update_history(url, episode=None, **fields):
    with history_lock:
        hist = load_history()
        for h in hist:
            if _same(h, url, episode):
                h.update(fields)
                _save_history(hist)
                return

def delete_history(url=None, episode=None):
    with history_lock:
        hist = [] if url is None else [h for h in load_history() if not _same(h, url, episode)]
        _save_history(hist)

def fetch_thumb(url):
    """Best-effort og:image lookup so history entries get a picture."""
    if not url.startswith("http"):
        return
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            page = r.read(400000).decode("utf-8", errors="ignore")
        m = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page)
             or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', page))
        if m:
            img = urllib.parse.urljoin(url, html.unescape(m.group(1)))
            if img.startswith("http"):
                update_history(url, thumb=img)
    except Exception:
        pass

def shift_episode_url(url, delta):
    """Guess a neighbouring episode URL from common patterns (…/3-episode, …/episode-3)."""
    for pat in (r'(\d+)(-episode)', r'(episode[-_/=]?)(\d+)', r'(\d+)(-seriya)'):
        m = re.search(pat, url)
        if m:
            g = m.groups()
            num_i = 0 if g[0].isdigit() else 1
            n = int(g[num_i]) + delta
            if n < 1:
                return None
            repl = (str(n) + g[1]) if num_i == 0 else (g[0] + str(n))
            return url[:m.start()] + repl + url[m.end():]
    return None

def next_episode_url(url):
    return shift_episode_url(url, 1)

def prev_episode_url(url):
    return shift_episode_url(url, -1)

# ---------------- Errors ----------------
_phase_t0 = {}

def phase(session_id, name):
    """Log how long each step of a cast took (visible in `journalctl --user -u tv-caster`)."""
    t0 = _phase_t0.setdefault(session_id, time.time())
    print(f"[cast {session_id % 100000}] {name} +{time.time() - t0:.1f}s", flush=True)

def nice_title(title, url):
    """yt-dlp names direct playlists 'index'; show the site instead."""
    if (title or "").strip().lower() in ("", "index", "master", "playlist", "video", "stream", "manifest"):
        return f"Видео ({urllib.parse.urlparse(url).hostname})"
    return title

def friendly_error(e):
    """Map low-level exceptions to a short message plus a hint on what to do."""
    s = str(e)
    low = s.lower()
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403):
            return ("Источник отказал в доступе (%d)" % e.code, "Ссылка могла устареть или нужна авторизация. Откройте её в браузере и скопируйте заново.")
        if e.code == 404:
            return ("Видео не найдено (404)", "Проверьте ссылку: страница удалена или адрес изменился.")
        if e.code == 429:
            return ("Источник просит подождать (429)", "Слишком много запросов. Повторите через минуту.")
        return ("Источник ответил ошибкой %d" % e.code, "Повторите попытку позже.")
    if isinstance(e, (subprocess.TimeoutExpired, socket.timeout, TimeoutError)) or "timed out" in low:
        return ("Источник отвечает слишком медленно", "Повторите или выберите меньшее качество.")
    if "could not locate stream metadata" in low or "kinogo extraction failed" in low:
        return ("Не удалось получить видео со страницы", "Если страница открывается в браузере, сайт мог поменять вёрстку и нужно обновить экстрактор.")
    if "vk" in low and ("hls" in low or "video id" in low or "playlist" in low):
        return ("VK-видео недоступно", "Оно закрыто, удалено или ссылка неполная.")
    if "not in player" in low:
        return ("Этого названия нет в плеере", "Попробуйте другое название или найдите сериал на другом сайте.")
    if "cloudflare" in low:
        return ("Сайт закрыт защитой от ботов", "Откройте страницу в браузере, найдите плеер (адрес вида api.nextembed.ws/embed/movie/…) и вставьте его ссылку сюда.")
    if "voice-over unavailable" in low:
        return ("Этой озвучки нет для выбранной серии", "Выберите другую озвучку (например, LostFilm) и запустите снова.")
    if "no ts segments" in low:
        return ("Плейлист видео пустой", "Источник не отдал сегменты. Попробуйте другое качество или озвучку.")
    if "ffmpeg" in low:
        return ("Не удалось подготовить видео для ТВ", "Формат не поддерживается. Попробуйте другое качество.")
    if ("yt-dlp" in low or "generic" in low) and ("404" in low or "403" in low or "denied" in low):
        return ("Сайт не отдал страницу серверу", "Возможно, там проверка «я не робот». Откройте страницу в браузере, найдите в DevTools → Network ссылку .m3u8 или .mp4 и вставьте её сюда.")
    if "yt-dlp" in low or "unsupported url" in low:
        return ("Не получилось найти видео по ссылке", "Сайт не поддерживается или видео закрыто.")
    if isinstance(e, (urllib.error.URLError, ConnectionError, OSError)):
        return ("Нет соединения с источником", "Проверьте интернет и повторите.")
    return ("Ошибка: " + s[:200], "Нажмите «Повторить». Если повторяется, посмотрите журнал: journalctl --user -u tv-caster.")

def set_error(session_id, e):
    msg, hint = friendly_error(e) if isinstance(e, BaseException) else (str(e), "")
    print("Error:", e)
    if session_id == current_session_id:
        app_state.update(status="error", status_text=msg, last_error=msg, error_hint=hint)

def sec_to_hms(sec):
    sec = max(0, int(sec))
    return "%02d:%02d:%02d" % (sec // 3600, (sec % 3600) // 60, sec % 60)

def hms_to_sec(t):
    try:
        parts = [int(float(x)) for x in t.split(":")]
    except Exception:
        return 0
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return 0

# DLNA SOAP Helpers
def soap_call(service, action, body_xml):
    with tv_lock:
        ctl = TV["controls"].get(service)
    if not ctl:
        return f"TV has no {service} control"
    soap = f'''<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:{action} xmlns:u="{ctl["type"]}">
      {body_xml}
    </u:{action}>
  </s:Body>
</s:Envelope>'''
    headers = {
        'Content-Type': 'text/xml; charset="utf-8"',
        'SOAPAction': f'"{ctl["type"]}#{action}"'
    }
    req = urllib.request.Request(ctl["url"], data=soap.encode('utf-8'), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            TV["online"] = True
            return r.read().decode('utf-8', errors='ignore')
    except urllib.error.HTTPError as e:
        TV["online"] = True  # the TV answered, it just rejected this action
        return str(e)
    except Exception as e:
        _note_tv_failure()
        return str(e)

def tv_stop():
    return soap_call("AVTransport", "Stop", "<InstanceID>0</InstanceID>")

def tv_play():
    return soap_call("AVTransport", "Play", "<InstanceID>0</InstanceID><Speed>1</Speed>")

def tv_pause():
    return soap_call("AVTransport", "Pause", "<InstanceID>0</InstanceID>")

def tv_seek(target_time): # HH:MM:SS
    return soap_call("AVTransport", "Seek", f"<InstanceID>0</InstanceID><Unit>REL_TIME</Unit><Target>{target_time}</Target>")

def tv_set_volume(vol):
    return soap_call("RenderingControl", "SetVolume", f"<InstanceID>0</InstanceID><Channel>Master</Channel><DesiredVolume>{vol}</DesiredVolume>")

def tv_get_volume():
    res = soap_call("RenderingControl", "GetVolume", "<InstanceID>0</InstanceID><Channel>Master</Channel>")
    m = re.search(r'<CurrentVolume>(\d+)</CurrentVolume>', res)
    return int(m.group(1)) if m else 50

def tv_cast_url(stream_url, title="Media Stream"):
    tv_stop()
    time.sleep(0.3)
    didl = f'''<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">
<item id="1" parentID="0" restricted="1">
<dc:title>{html.escape(title)}</dc:title>
<upnp:class>object.item.videoItem</upnp:class>
<res protocolInfo="http-get:*:video/mp2t:*">{html.escape(stream_url)}</res>
</item>
</DIDL-Lite>'''
    body = f'''<InstanceID>0</InstanceID>
<CurrentURI>{html.escape(stream_url)}</CurrentURI>
<CurrentURIMetaData>{html.escape(didl)}</CurrentURIMetaData>'''
    soap_call("AVTransport", "SetAVTransportURI", body)
    time.sleep(0.4)
    soap_call("AVTransport", "Play", "<InstanceID>0</InstanceID><Speed>1</Speed>")

def tv_get_position():
    res = soap_call("AVTransport", "GetPositionInfo", "<InstanceID>0</InstanceID>")
    rel_m = re.search(r'<RelTime>([^<]+)</RelTime>', res)
    dur_m = re.search(r'<TrackDuration>([^<]+)</TrackDuration>', res)
    rel = rel_m.group(1) if rel_m else "00:00:00"
    dur = dur_m.group(1) if dur_m else "00:00:00"

    t_res = soap_call("AVTransport", "GetTransportInfo", "<InstanceID>0</InstanceID>")
    state_m = re.search(r'<CurrentTransportState>([^<]+)</CurrentTransportState>', t_res)
    state = state_m.group(1) if state_m else "STOPPED"
    return {"state": state, "rel_time": rel, "duration": dur}

# Stream extractors
def extract_kinogo(url, translation_id=None, quality="720p", mode="stream", episode=None, translation_title=None):
    js_script = os.path.join(os.path.dirname(__file__), "extract_kinogo.js")
    node_bin = shutil.which("node") or "/home/edvins/.local/bin/node"
    args = [node_bin, js_script, url, translation_id or "", quality or "720p", mode, episode or "", translation_title or ""]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if proc.returncode != 0:
        raise Exception(f"Kinogo extraction failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return json.loads(proc.stdout)

def extract_rezka(url, quality="720p"):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    import gzip
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
        try:
            html_text = gzip.decompress(data).decode('utf-8', errors='ignore')
        except:
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

    q_clean = quality.lower().replace(" ", "").replace("fullhd", "").replace("hd", "")
    chosen = streams_map.get(q_clean) or streams_map.get('720p') or streams_map.get('480p') or streams_map.get('1080p') or list(streams_map.values())[0]
    return f"{title} [{quality}]", chosen, list(streams_map.keys())

REZKA_URL_RE = re.compile(r'^(https?://[^/]+)/([^/]+)/(\d+)-season(?:/(\d+)-episode)?')

def is_rezka(url):
    return "tulsa-king.net" in url or "rezka" in url

def fetch_page(url, timeout=15):
    import gzip
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen_fast(req, connect_timeout=6, read_timeout=timeout) as resp:
        data = resp.read()
    try:
        return gzip.decompress(data).decode("utf-8", errors="ignore")
    except Exception:
        return data.decode("utf-8", errors="ignore")

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
    return {"title": title, "kind": "series", "seasons": seasons, "translations": []}

# ---------------- nextembed player (kinogohd.online and similar) ----------------
IMDB_RE = re.compile(r'(?:imdb\.com/title/)?\b(tt\d{6,10})\b')

def normalize_url(url):
    """An IMDb id or imdb.com link becomes the player link; anything else is left alone."""
    url = (url or "").strip()
    if not url.startswith("http") or "imdb.com" in url:
        m = IMDB_RE.search(url)
        if m and (url.startswith("tt") or "imdb.com" in url):
            return f"https://api.nextembed.ws/embed/imdb/{m.group(1)}"
    return url

def search_titles(query):
    """Title search through IMDb suggestions (works for Russian queries too)."""
    q = (query or "").strip().lower()
    if len(q) < 2:
        return []
    req = urllib.request.Request(f"https://v2.sg.media-imdb.com/suggestion/x/{urllib.parse.quote(q, safe='')}.json",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urlopen_fast(req, connect_timeout=5, read_timeout=10) as r:
        data = json.loads(r.read().decode("utf-8", errors="ignore"))
    kinds = {"tvSeries": "сериал", "tvMiniSeries": "мини-сериал", "movie": "фильм", "tvMovie": "фильм"}
    out = []
    for x in data.get("d", []):
        if x.get("id", "").startswith("tt") and x.get("qid") in kinds:
            out.append({"id": x["id"], "title": x.get("l", ""), "year": x.get("y"), "type": kinds[x["qid"]],
                        "cast": x.get("s", ""), "image": (x.get("i") or {}).get("imageUrl", "")})
    return out[:8]

def is_nextembed(url):
    return "nextembed" in url

def is_blocked_site(url):
    """Sites behind a Cloudflare bot check: the server cannot read their pages, only the embedded player."""
    return "kinogohd" in url

def nextembed_data(url):
    """(title, seasons, movie) for an embed URL: seasons for a series, movie={'hls','names'} for a film."""
    try:
        page = fetch_page(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise Exception("not in player: this title is not available")
        raise
    tm = re.search(r"<title>(.*?)</title>", page, re.S)
    title = re.sub(r"\s*\(\d+\s+сезон\).*$", "", html.unescape(tm.group(1)).strip()) if tm else "Video"
    i = page.find("seasons:[")
    if i >= 0:
        seasons, _ = json.JSONDecoder().raw_decode(page[i + len("seasons:"):])
        seasons = [sn for sn in seasons if not sn.get("blocked")]
        seasons.sort(key=lambda sn: int(sn["season"]))
        for sn in seasons:
            sn["episodes"].sort(key=lambda e: int(e["episode"]))
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
    return title, None, {"hls": hm.group(1), "names": names}

def nextembed_structure(url):
    title, seasons, movie = nextembed_data(url)
    if movie:
        return {"title": title, "kind": "movie", "seasons": [],
                "translations": [{"id": str(i), "title": nm} for i, nm in enumerate(movie["names"])]}
    out = []
    for sn in seasons:
        n = int(sn["season"])
        eps = []
        for e in sn["episodes"]:
            names = (e.get("audio") or {}).get("names") or []
            eps.append({"id": "s%02de%02d" % (n, int(e["episode"])), "title": f"Серия {int(e['episode'])}",
                        "translations": [{"id": str(i), "title": nm} for i, nm in enumerate(names)]})
        out.append({"id": "s%02d" % n, "title": f"Сезон {n}", "episodes": eps})
    return {"title": title, "kind": "series", "seasons": out, "translations": []}

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
                if em and int(sn["season"]) == int(em.group(1)) and int(e["episode"]) == int(em.group(2)):
                    ep = (sn, e)
        if not ep:
            sn = seasons[0]
            ep = (sn, sn["episodes"][0])
        sn, e = ep
        names = (e.get("audio") or {}).get("names") or []
        hls = e["hls"]
        label = f"{title} - Сезон {int(sn['season'])}, Серия {int(e['episode'])}"
        ep_id = "s%02de%02d" % (int(sn["season"]), int(e["episode"]))
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

def fetch_segment(u, headers, procs, cancel_event):
    host = urllib.parse.urlparse(u).hostname
    ip = get_clean_ip(host)
    cmd = ["curl", "-k", "-s", "--connect-timeout", "5", "--max-time", "30", "-y", "5", "-Y", "100000"]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    if ip:
        cmd += ["--resolve", f"{host}:443:{ip}"]
    cmd.append(u)
    for _ in range(4):
        if cancel_event.is_set():
            return b""
        try:
            rc, out = run_tracked(cmd, procs, 35)
            if rc == 0 and out:
                return out
        except Exception:
            pass
        time.sleep(0.2)
    return b""

def download_segments(urls, dest, headers, info, on_segment, workers=12):
    """Fetch segments in parallel, write them to dest in playlist order."""
    cancel_event, procs = info["cancel_event"], info["procs"]
    results, nxt, written = {}, 0, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex, open(dest, "wb") as out:
        pending = {ex.submit(fetch_segment, urls[i], headers, procs, cancel_event): i for i in range(min(workers * 2, len(urls)))}
        queued = len(pending)
        while pending and not cancel_event.is_set():
            done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in done:
                idx = pending.pop(fut)
                results[idx] = fut.result()
                if queued < len(urls):
                    pending[ex.submit(fetch_segment, urls[queued], headers, procs, cancel_event)] = queued
                    queued += 1
            while nxt in results:
                chunk = results.pop(nxt)
                if not chunk and not cancel_event.is_set():
                    raise Exception(f"timed out: segment {nxt + 1} of {len(urls)} failed")
                out.write(chunk)
                written += len(chunk)
                nxt += 1
                on_segment(len(chunk))
    return written

def download_nextembed(session_id, title, master_url, track, quality="720p", speed="1.0x"):
    """Video + chosen audio track come as separate HLS streams: download both, merge, convert, cast."""
    info = active_sessions.get(session_id)
    if not info:
        return
    cancel_event = info["cancel_event"]
    vpath = os.path.join(CACHE_DIR, f"temp_{session_id}.v.ts")
    apath = os.path.join(CACHE_DIR, f"temp_{session_id}.a.ts")
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://api.nextembed.ws/"}
    errors = []
    try:
        if session_id == current_session_id:
            app_state.update(status="downloading", stage="download", status_text="Загружаю видео", title=title, download_progress=0)
        master = curl_fetch(master_url, headers, max_time=15)
        variants, audios = parse_master(master, master_url)
        if not variants:
            raise Exception("No TS segments found in playlist")
        want = int(re.sub(r"\D", "", quality) or 720)
        below = sorted([v for v in variants if v[0] <= want], reverse=True)
        height, vurl = below[0] if below else min(variants)
        aurl = audios[track][1] if audios and track < len(audios) else (audios[0][1] if audios else None)
        phase(session_id, f"master parsed: {height}p, audio {track}")

        vsegs = media_segments(vurl, headers)
        asegs = media_segments(aurl, headers) if aurl else []
        total, done_n, got = len(vsegs) + len(asegs), [0], [0]
        start_time, lock = time.time(), threading.Lock()

        def on_segment(n):
            with lock:
                done_n[0] += 1
                got[0] += n
                if session_id == current_session_id:
                    dur = max(time.time() - start_time, 0.1)
                    app_state["download_progress"] = min(99, int(done_n[0] * 100 / total))
                    app_state["download_speed"] = f"{got[0] / 1048576 / dur:.1f} MB/s"
                    app_state["total_size"] = f"{got[0] / 1048576:.0f} MB"

        def run(urls, dest):
            try:
                download_segments(urls, dest, headers, info, on_segment)
            except Exception as e:
                errors.append(e)
                cancel_event.set()  # stop the sibling download too
        threads = [threading.Thread(target=run, args=(vsegs, vpath), daemon=True)]
        if asegs:
            threads.append(threading.Thread(target=run, args=(asegs, apath), daemon=True))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise errors[0]
        if cancel_event.is_set():
            return
        phase(session_id, "segments downloaded")

        if session_id == current_session_id:
            app_state.update(status="remuxing", stage="convert", status_text="Склеиваю видео и звук", download_progress=100)
        plain = speed in ("1.0x", "1.0", "1") and (not asegs or all(x["codec"] in SAFE_AUDIO for x in probe_streams(path=apath) if x["type"] == "Audio"))
        ff = get_ffmpeg()
        maps = ["-map", "0:v:0"] + (["-map", "1:a:0"] if asegs else [])
        inputs = ["-i", vpath] + (["-i", apath] if asegs else [])
        if plain:
            # video + AAC audio: merge straight into the TV format in one pass
            out_ts = os.path.join(CACHE_DIR, f"temp_{session_id}.ts")
            proc = subprocess.run([ff, "-y"] + inputs + maps + ["-c", "copy", "-f", "mpegts", out_ts], capture_output=True, timeout=900)
            if proc.returncode != 0 or not os.path.exists(out_ts) or os.path.getsize(out_ts) == 0:
                tail = proc.stderr.decode("utf-8", errors="ignore").strip().splitlines()[-1:] or [""]
                raise Exception("FFmpeg merge failed: " + tail[0])
            os.replace(out_ts, info["file"])
            for part in (vpath, apath):
                try:
                    os.remove(part)
                except OSError:
                    pass
            info["active"] = False
            if cancel_event.is_set():
                return
            phase(session_id, "merged to ts")
            tv_cast_url(f"http://{LOCAL_IP}:{SERVER_PORT}/stream.ts?v={session_id}", title)
            if session_id == current_session_id:
                app_state.update(status="playing", stage="tv", status_text="Играет на ТВ")
            return
        src = os.path.join(CACHE_DIR, f"temp_{session_id}.mkv")
        proc = subprocess.run([ff, "-y"] + inputs + maps + ["-c", "copy", "-f", "matroska", src], capture_output=True, timeout=900)
        if proc.returncode != 0:
            tail = proc.stderr.decode("utf-8", errors="ignore").strip().splitlines()[-1:] or [""]
            raise Exception("FFmpeg merge failed: " + tail[0])
        for part in (vpath, apath):
            try:
                os.remove(part)
            except OSError:
                pass
        info["src_mp4"], info["speed"] = src, speed
        finish_mp4(session_id, title, src, speed, 0)
    except Exception as e:
        info["active"] = False
        if errors and not isinstance(e, type(errors[0])):
            e = errors[0]
        set_error(session_id, e)

_series_cache = {}

def is_kinogo(url):
    return ("kinogo" in url and not is_blocked_site(url)) or "cinemar.cc" in url

def series_episodes(url, info=None):
    """Flat ordered list of episode ids for a Kinogo series page (cached 10 min)."""
    now = time.time()
    cached = _series_cache.get(url)
    if cached and now - cached[0] < 600:
        return cached[1]
    try:
        info = info or (nextembed_structure(url) if is_nextembed(url) else extract_kinogo(url, mode="info") if is_kinogo(url) else rezka_structure(url))
        eps = [e["id"] for sn in info.get("seasons", []) for e in sn["episodes"]]
    except Exception:
        eps = []
    _series_cache[url] = (now if eps else now - 540, eps)  # retry failures after a minute
    return eps

def neighbor_episode(url, episode, delta):
    """(url, episode) of the previous/next episode, or None."""
    if episode and (is_kinogo(url) or is_rezka(url) or is_nextembed(url)):
        eps = series_episodes(url)
        if episode in eps:
            i = eps.index(episode) + delta
            if 0 <= i < len(eps):
                return url, eps[i]
        return None
    target = shift_episode_url(url, delta)
    return (target, None) if target else None

def inspect_url(url):
    url = normalize_url(url)
    if is_blocked_site(url):
        raise Exception("cloudflare: page is behind a bot check")
    if is_nextembed(url):
        data = nextembed_structure(url)
        if data["kind"] == "series":
            series_episodes(url, info=data)
        return {"ok": True, "title": data["title"], "kind": data["kind"], "seasons": data["seasons"],
                "translations": data["translations"], "qualities": ["720p", "360p"], "speeds": ["1.0x", "1.25x", "1.5x"]}
    if "kinogo" in url or "cinemar.cc" in url:
        data = extract_kinogo(url, mode="info")
        if data.get("kind") == "series":
            series_episodes(url, info=data)
        return {
            "ok": True,
            "title": data.get("title", "Kinogo Media"),
            "kind": data.get("kind", "movie"),
            "seasons": data.get("seasons", []),
            "translations": data.get("translations", []),
            "qualities": ["720p", "1080p", "480p", "360p"],
            "speeds": ["1.0x", "1.25x", "1.5x"]
        }
    elif is_rezka(url) and REZKA_URL_RE.match(url):
        data = rezka_structure(url)
        series_episodes(url, info=data)
        return {
            "ok": True, "title": data["title"], "kind": "series", "seasons": data["seasons"], "translations": [],
            "qualities": ["720p", "1080p", "480p", "360p"], "speeds": ["1.0x", "1.25x", "1.5x"],
        }
    elif "tulsa-king.net" in url or "rezka" in url:
        title, chosen, qualities = extract_rezka(url)
        return {
            "ok": True,
            "title": title,
            "translations": [],
            "qualities": qualities or ["720p", "1080p", "480p", "360p"],
            "speeds": ["1.0x", "1.25x", "1.5x"]
        }
    else:
        return {
            "ok": True,
            "title": "Online Video",
            "translations": [],
            "qualities": ["720p", "1080p", "480p"],
            "speeds": ["1.0x", "1.25x", "1.5x"]
        }

def apply_speed_to_chunk(chunk_bytes, speed_str):
    if not speed_str or speed_str in ("1.0x", "1.0", "1"):
        return chunk_bytes
    try:
        rate = float(speed_str.replace("x", "").strip())
        if rate == 1.0:
            return chunk_bytes
        ffmpeg_bin = get_ffmpeg()
        pts_factor = round(1.0 / rate, 3)
        cmd = [
            ffmpeg_bin, "-y", "-i", "pipe:0",
            "-filter_complex", f"[0:v]setpts={pts_factor}*PTS[v];[0:a]atempo={rate}[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            "-c:a", "aac",
            "-f", "mpegts", "pipe:1"
        ]
        proc = subprocess.run(cmd, input=chunk_bytes, capture_output=True, timeout=12)
        if proc.returncode == 0 and len(proc.stdout) > 0:
            return proc.stdout
    except Exception as e:
        print("Speed transform error:", e)
    return chunk_bytes

SAFE_AUDIO = {"aac", "mp3"}  # codecs the TV is known to play

def probe_streams(path=None, data=None):
    """Return [{index, type, codec, lang}] by parsing `ffmpeg -i` output."""
    ff = get_ffmpeg()
    try:
        if path:
            proc = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, timeout=30)
        else:
            proc = subprocess.run([ff, "-hide_banner", "-i", "pipe:0"], input=data, capture_output=True, timeout=30)
        err = proc.stderr.decode("utf-8", errors="ignore")
    except Exception as e:
        print("Probe error:", e)
        return []
    streams = []
    for m in re.finditer(r'Stream #0:(\d+)(?:\[[^\]]*\])?(?:\((\w+)\))?: (Video|Audio|Subtitle): (\w+)', err):
        streams.append({"index": int(m.group(1)), "lang": m.group(2) or "", "type": m.group(3), "codec": m.group(4).lower()})
    return streams

def fix_audio_chunk(chunk):
    """Re-encode the audio of one TS chunk to AAC stereo, keeping the video untouched."""
    try:
        cmd = [get_ffmpeg(), "-y", "-i", "pipe:0", "-map", "0:v?", "-map", "0:a:0?",
               "-c:v", "copy", "-c:a", "aac", "-ac", "2", "-b:a", "192k",
               "-copyts", "-f", "mpegts", "pipe:1"]
        proc = subprocess.run(cmd, input=chunk, capture_output=True, timeout=30)
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    except Exception as e:
        print("Audio fix error:", e)
    return chunk

def extract_vk(url):
    m = re.search(r'video(-?\d+_\d+)', url)
    if not m:
        raise Exception("Could not find VK video ID in URL")
    video_id = m.group(1)

    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    try:
        opener.open(urllib.request.Request(f'https://vkvideo.ru/video{video_id}', headers={'User-Agent': 'Mozilla/5.0'}))
    except Exception:
        pass

    data = urllib.parse.urlencode({'act': 'show', 'al': '1', 'video': video_id}).encode('utf-8')
    req = urllib.request.Request(
        'https://vkvideo.ru/al_video.php',
        data=data,
        headers={
            'User-Agent': 'Mozilla/5.0',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': f'https://vkvideo.ru/video{video_id}'
        }
    )
    with opener.open(req) as resp:
        raw = resp.read().decode('windows-1251', errors='ignore')

    title_match = re.search(r'\{\"payload\":\[\d+,\s*\[\"([^\"]+)\"', raw)
    title = title_match.group(1) if title_match else f"VK Video {video_id}"

    clean = raw.replace('\\/', '/')
    urls = set(re.findall(r'https?://[^\s\"\'<>]+\.m3u8[^\s\"\'<>]*', clean))
    if not urls:
        raise Exception("Could not find HLS playlist in VK response")

    master_m3u8 = list(urls)[0].replace('&amp;', '&')
    req_m = urllib.request.Request(master_m3u8, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req_m) as resp:
        master_body = resp.read().decode('utf-8', errors='ignore')

    stream_url = None
    for line in master_body.splitlines():
        line = line.strip()
        if line.startswith('/') or line.startswith('http'):
            stream_url = line if line.startswith('http') else 'https://' + urllib.parse.urlparse(master_m3u8).netloc + line
            if 'type/3' in line: # 720p HD
                break

    if not stream_url:
        raise Exception("Failed to resolve stream playlist from master HLS")

    return title, stream_url

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

def run_tracked(cmd, procs, timeout):
    """Run a subprocess that cancel_session() can kill; returns (returncode, stdout bytes)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    procs.add(p)
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        return -1, b""
    finally:
        procs.discard(p)

def cancel_session(info):
    """Stop a session and kill its running downloads at once, so they stop holding connections."""
    info["cancel_event"].set()
    info["active"] = False
    for p in list(info.get("procs", ())):
        try:
            p.kill()
        except OSError:
            pass

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

def download_and_stream_hls(session_id, title, hls_url, extra_headers=None, speed="1.0x", quality="720p"):
    global app_state
    session_info = active_sessions.get(session_id)
    if not session_info:
        return
    cancel_event = session_info["cancel_event"]
    session_file = session_info["file"]

    try:
        req_headers = {"User-Agent": "Mozilla/5.0"}
        if extra_headers:
            req_headers.update(extra_headers)

        # 1. Resolve direct storage server URL through all 302/307 redirects
        phase(session_id, "hls thread started")
        direct_hls_url = resolve_redirects(hls_url, req_headers)
        phase(session_id, "redirects resolved")
        if cancel_event.is_set():
            return

        # 2. Fetch playlist text
        m3u8_body = curl_fetch(direct_hls_url, req_headers, max_time=8)
        phase(session_id, "playlist fetched")

        # 3. Check if this is a Master playlist pointing to variant streams
        has_sub_playlists = any(".m3u8" in l and not l.strip().startswith("#") for l in m3u8_body.splitlines())
        has_ts = any(".ts" in l and not l.strip().startswith("#") for l in m3u8_body.splitlines())

        if has_sub_playlists and not has_ts:
            variant_lines = [l.strip() for l in m3u8_body.splitlines() if ".m3u8" in l and not l.strip().startswith("#")]
            q_num = quality.replace("p", "") if quality else "720"
            chosen_var = None
            for vl in variant_lines:
                if q_num in vl:
                    chosen_var = vl
                    break
            if not chosen_var:
                for vl in variant_lines:
                    if "720" in vl:
                        chosen_var = vl
                        break
            if not chosen_var and variant_lines:
                chosen_var = variant_lines[0]

            if chosen_var:
                var_url = chosen_var if chosen_var.startswith("http") else urllib.parse.urljoin(direct_hls_url, chosen_var)
                direct_hls_url = resolve_redirects(var_url, req_headers)
                m3u8_body = curl_fetch(direct_hls_url, req_headers, max_time=8)

        if cancel_event.is_set():
            return

        base_dir = direct_hls_url.rsplit('/', 1)[0] + '/'
        segments = [l.strip() for l in m3u8_body.splitlines() if '.ts' in l and not l.strip().startswith('#')]

        if not segments:
            session_info["active"] = False
            raise Exception("No TS segments found in playlist")

        if session_id == current_session_id:
            app_state["status"] = "downloading"
            app_state["stage"] = "download"
            app_state["status_text"] = "Загружаю видео"
            app_state["title"] = title
            app_state["download_progress"] = 0
            app_state["total_size"] = f"{len(segments)} segments"

        cast_triggered = False
        local_stream_url = f"http://{LOCAL_IP}:{SERVER_PORT}/stream.ts?v={session_id}"

        parsed_base = urllib.parse.urlparse(base_dir)
        base_ip = get_clean_ip(parsed_base.hostname)

        def fetch_seg(seg_name):
            seg_clean = seg_name.lstrip('./')
            u = seg_clean if seg_clean.startswith('http') else base_dir + seg_clean
            # abort stalled connections quickly (<100 KB/s for 5 s) so one slow segment cannot block the ordered writer
            cmd = ["curl", "-k", "-s", "--connect-timeout", "5", "--max-time", "30", "-y", "5", "-Y", "100000"]
            for k, v in req_headers.items():
                cmd.extend(["-H", f"{k}: {v}"])
            parsed_u = urllib.parse.urlparse(u)
            u_ip = base_ip if parsed_u.hostname == parsed_base.hostname else get_clean_ip(parsed_u.hostname)
            if u_ip:
                cmd.extend(["--resolve", f"{parsed_u.hostname}:443:{u_ip}"])
            cmd.append(u)

            for _ in range(4):
                if cancel_event.is_set():
                    return b""
                try:
                    rc, out = run_tracked(cmd, session_info["procs"], 35)
                    if rc == 0 and len(out) > 0:
                        return out
                except Exception:
                    pass
                time.sleep(0.2)
            return b""

        needs_audio_fix = False
        max_workers = 16
        buffer_dict = {}
        next_to_write = 0
        total_bytes = 0
        start_time = time.time()

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor, open(session_file, "wb") as out_f:
            future_to_idx = {
                executor.submit(fetch_seg, segments[i]): i
                for i in range(min(max_workers * 2, len(segments)))
            }
            queued_idx = len(future_to_idx)

            while future_to_idx and not cancel_event.is_set():
                done, _ = concurrent.futures.wait(future_to_idx, return_when=concurrent.futures.FIRST_COMPLETED)
                for fut in done:
                    idx = future_to_idx.pop(fut)
                    data = fut.result()
                    buffer_dict[idx] = data

                    if queued_idx < len(segments):
                        future_to_idx[executor.submit(fetch_seg, segments[queued_idx])] = queued_idx
                        queued_idx += 1

                while next_to_write in buffer_dict:
                    chunk = buffer_dict.pop(next_to_write)
                    if next_to_write == 0:
                        if not chunk:
                            raise Exception("timed out: first video segment was not delivered")
                        audio = [x for x in probe_streams(data=chunk[:3_000_000]) if x["type"] == "Audio"]
                        needs_audio_fix = bool(audio) and audio[0]["codec"] not in SAFE_AUDIO
                    if speed and speed != "1.0x":
                        chunk = apply_speed_to_chunk(chunk, speed)
                    elif needs_audio_fix:
                        chunk = fix_audio_chunk(chunk)
                    out_f.write(chunk)
                    out_f.flush()
                    total_bytes += len(chunk)
                    next_to_write += 1

                    # As soon as we have 6 segments (~36 sec buffer), trigger TV playback!
                    if next_to_write == 6 and not cast_triggered and not cancel_event.is_set():
                        cast_triggered = True
                        if session_id == current_session_id:
                            app_state["status_text"] = "Играет на ТВ, догружаю"
                            app_state["stage"] = "tv"
                        tv_cast_url(local_stream_url, title)

                    dur = time.time() - start_time
                    spd = (total_bytes / (1024 * 1024)) / dur if dur > 0 else 0
                    pct = int((next_to_write / len(segments)) * 100)
                    if session_id == current_session_id:
                        app_state["download_progress"] = pct
                        app_state["download_speed"] = f"{spd:.1f} MB/s"
                        app_state["total_size"] = f"{total_bytes / (1024 * 1024):.1f} MB"

            out_f.flush()

        if cancel_event.is_set():
            return

        session_info["active"] = False
        if not cast_triggered:
            tv_cast_url(local_stream_url, title)

        if session_id == current_session_id:
            app_state["status"] = "playing"
            app_state["stage"] = "tv"
            app_state["status_text"] = "Играет на ТВ"
            app_state["download_progress"] = 100

    except Exception as e:
        session_info["active"] = False
        set_error(session_id, e)

def remux_mp4(session_id, src_mp4, session_file, speed, audio_idx):
    """Convert a downloaded MP4 into MPEG-TS the TV can play; fixes unsupported audio on the way."""
    ff = get_ffmpeg()
    streams = probe_streams(path=src_mp4)
    audios = [x for x in streams if x["type"] == "Audio"]
    tracks = [{"n": i, "lang": a_["lang"], "codec": a_["codec"]} for i, a_ in enumerate(audios)]
    if not (0 <= audio_idx < len(audios)):
        audio_idx = 0
    if session_id == current_session_id:
        app_state["audio_tracks"] = tracks
        app_state["audio_index"] = audio_idx

    rate = 1.0
    if speed and speed not in ("1.0x", "1.0", "1"):
        rate = float(speed.replace("x", "").strip())
    temp_ts = os.path.join(CACHE_DIR, f"temp_{session_id}.ts")
    size_gb = os.path.getsize(src_mp4) / 1e9

    a_map = ["-map", f"0:a:{audio_idx}"] if audios else []
    if rate != 1.0:
        pts_factor = round(1.0 / rate, 3)
        cmd = [ff, "-y", "-i", src_mp4,
               "-filter_complex", f"[0:v:0]setpts={pts_factor}*PTS[v];[0:a:{audio_idx}]atempo={rate}[a]",
               "-map", "[v]", "-map", "[a]",
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22", "-c:a", "aac",
               "-f", "mpegts", temp_ts]
        timeout = 3600
    else:
        convert_audio = bool(audios) and audios[audio_idx]["codec"] not in SAFE_AUDIO
        if convert_audio and session_id == current_session_id:
            app_state["status_text"] = f"Конвертирую звук {audios[audio_idx]['codec'].upper()} в AAC"
        cmd = [ff, "-y", "-i", src_mp4, "-map", "0:v:0"] + a_map + ["-c:v", "copy"]
        cmd += ["-c:a", "aac", "-ac", "2", "-b:a", "192k"] if convert_audio else ["-c:a", "copy"]
        cmd += ["-f", "mpegts", temp_ts]
        timeout = 300 + int(size_gb * 300)

    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if proc.returncode != 0 or not os.path.exists(temp_ts) or os.path.getsize(temp_ts) == 0:
        tail = proc.stderr.decode("utf-8", errors="ignore").strip().splitlines()[-1:] or [""]
        raise Exception("FFmpeg failed: " + tail[0])
    os.replace(temp_ts, session_file)

def finish_mp4(session_id, title, src_mp4, speed, audio_idx):
    info = active_sessions[session_id]
    if session_id == current_session_id:
        app_state.update(status="remuxing", stage="convert", status_text="Готовлю видео для ТВ", download_progress=100)
    remux_mp4(session_id, src_mp4, info["file"], speed, audio_idx)
    info["active"] = False
    if info["cancel_event"].is_set():
        return
    tv_cast_url(f"http://{LOCAL_IP}:{SERVER_PORT}/stream.ts?v={session_id}", title)
    if session_id == current_session_id:
        app_state.update(status="playing", stage="tv", status_text="Играет на ТВ")

DL_WORKERS = 8
DL_CHUNK = 8 * 1024 * 1024
DL_HEADERS = {"User-Agent": "Mozilla/5.0"}

def urlopen_fast(req, connect_timeout=4, read_timeout=30, attempts=3):
    """urlopen that gives up on a dead address after a few seconds (servers with several A records)."""
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

def remote_size(url):
    """Size in bytes if the server supports Range requests, else None."""
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={**DL_HEADERS, "Range": "bytes=0-0"})
            with urllib.request.urlopen(req, timeout=6) as r:
                cr = r.headers.get("Content-Range")
                if r.status == 206 and cr and "/" in cr and cr.rsplit("/", 1)[1].isdigit():
                    return int(cr.rsplit("/", 1)[1])
                return None  # answered, but no Range support
        except Exception:
            time.sleep(0.5)
    return None

def wants_parallel(url):
    """Only YouTube's CDN throttles single streams and rewards ranged chunks; others are fastest as ONE stream."""
    host = urllib.parse.urlparse(url).hostname or ""
    return host.endswith("googlevideo.com")

def parallel_download(url, dest, cancel_event, on_bytes, total=None, parallel=True, on_total=None):
    """Download url into dest with several concurrent Range requests (single stream fallback)."""
    if not parallel or not total or total < 2 * DL_CHUNK:
        done = 0
        with open(dest, "wb") as f:
            for attempt in range(4):
                try:
                    hdr = dict(DL_HEADERS)
                    if done:
                        hdr["Range"] = f"bytes={done}-"
                    req = urllib.request.Request(url, headers=hdr)
                    with urlopen_fast(req) as resp:
                        if done and resp.status != 206:
                            raise IOError("server cannot resume")
                        if not done and on_total and resp.headers.get("Content-Length", "").isdigit():
                            on_total(int(resp.headers["Content-Length"]))
                            total = total or int(resp.headers["Content-Length"])
                        while not cancel_event.is_set():
                            buf = resp.read(1024 * 1024)
                            if not buf:
                                break
                            f.write(buf)
                            done += len(buf)
                            on_bytes(len(buf))
                    return
                except Exception:
                    if attempt == 3 or (done and not total):
                        raise
                    time.sleep(1 + attempt)
        return

    with open(dest, "wb") as f:
        f.truncate(total)
    fd = os.open(dest, os.O_WRONLY)
    ranges = iter([(o, min(o + DL_CHUNK, total) - 1) for o in range(0, total, DL_CHUNK)])
    lock = threading.Lock()
    failed = threading.Event()
    errors = []

    def worker():
        while not cancel_event.is_set() and not failed.is_set():
            with lock:
                rng = next(ranges, None)
            if rng is None:
                return
            first, last = rng
            for attempt in range(4):
                got = 0
                try:
                    req = urllib.request.Request(url, headers={**DL_HEADERS, "Range": f"bytes={first}-{last}"})
                    with urllib.request.urlopen(req, timeout=30) as r:
                        if r.status != 206:
                            raise IOError(f"server ignored Range (HTTP {r.status})")
                        pos = first
                        while not cancel_event.is_set():
                            buf = r.read(256 * 1024)
                            if not buf:
                                break
                            os.pwrite(fd, buf, pos)
                            pos += len(buf)
                            got += len(buf)
                            on_bytes(len(buf))
                    if pos != last + 1 and not cancel_event.is_set():
                        raise IOError("short read")
                    break
                except Exception as e:
                    on_bytes(-got)
                    if attempt == 3:
                        errors.append(e)
                        failed.set()
                        return
                    time.sleep(0.5 * (attempt + 1))

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(DL_WORKERS)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        os.close(fd)
    if errors:
        raise errors[0]

def download_dash(session_id, title, urls, speed="1.0x", audio_idx=0):
    """Download one URL (MP4) or two (separate video + audio, e.g. YouTube) fast, merge, convert, cast."""
    session_info = active_sessions.get(session_id)
    if not session_info:
        return
    cancel_event = session_info["cancel_event"]
    parts = [os.path.join(CACHE_DIR, f"temp_{session_id}.part{i}") for i in range(len(urls))]
    stop_monitor = threading.Event()
    try:
        if session_id == current_session_id:
            app_state.update(status="downloading", stage="download", status_text="Загружаю видео", title=title, download_progress=0)
        sizes = [remote_size(u) if wants_parallel(u) else None for u in urls]
        expected = [sum(sz or 0 for sz in sizes)]
        got = [0]
        start_time = time.time()

        def on_bytes(n):
            got[0] += n

        def on_total(n):
            expected[0] += n

        def monitor():
            while not stop_monitor.wait(0.4):
                if session_id != current_session_id:
                    continue
                dur = max(time.time() - start_time, 0.1)
                app_state["download_speed"] = f"{got[0] / 1048576 / dur:.1f} MB/s"
                app_state["total_size"] = f"{got[0] / 1048576:.0f} MB"
                if expected[0]:
                    app_state["download_progress"] = min(99, int(got[0] * 100 / expected[0]))
        threading.Thread(target=monitor, daemon=True).start()

        for u, dest, sz in zip(urls, parts, sizes):
            parallel_download(u, dest, cancel_event, on_bytes, total=sz, parallel=wants_parallel(u), on_total=on_total)
        stop_monitor.set()
        if cancel_event.is_set():
            return

        if len(parts) == 1:
            src = parts[0]
        else:
            if session_id == current_session_id:
                app_state.update(status="remuxing", stage="convert", status_text="Склеиваю видео и звук", download_progress=100)
            src = os.path.join(CACHE_DIR, f"temp_{session_id}.mkv")
            cmd = [get_ffmpeg(), "-y", "-i", parts[0], "-i", parts[1], "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-f", "matroska", src]
            proc = subprocess.run(cmd, capture_output=True, timeout=600)
            if proc.returncode != 0:
                tail = proc.stderr.decode("utf-8", errors="ignore").strip().splitlines()[-1:] or [""]
                raise Exception("FFmpeg merge failed: " + tail[0])
            for part in parts:
                try:
                    os.remove(part)
                except OSError:
                    pass
        session_info["src_mp4"] = src
        session_info["speed"] = speed
        finish_mp4(session_id, title, src, speed, audio_idx)
    except Exception as e:
        session_info["active"] = False
        set_error(session_id, e)
    finally:
        stop_monitor.set()

LIVE_START_BYTES = 5 * 1024 * 1024  # start the TV once this much converted video exists

def moov_in_head(head):
    """True when the MP4 index (moov atom) is fully inside the first bytes, so it can be streamed."""
    pos = 0
    while pos + 8 <= len(head):
        size, kind = struct.unpack(">I4s", head[pos:pos + 8])
        if kind == b"moov":
            return size >= 8 and pos + size <= len(head)
        if size < 8:
            return False
        pos += size
    return False

def stream_mp4_live(session_id, title, url, speed="1.0x", audio_idx=0):
    """Pipe a 'fast start' MP4 through ffmpeg into the growing session file; the TV starts after a few MB."""
    info = active_sessions.get(session_id)
    if not info:
        return
    cancel_event = info["cancel_event"]
    resp = proc = None
    try:
        if session_id == current_session_id:
            app_state.update(status="downloading", stage="download", status_text="Загружаю видео", title=title, download_progress=0)
        resp = urlopen_fast(urllib.request.Request(url, headers=DL_HEADERS))
        total = int(resp.headers.get("Content-Length") or 0)
        head = resp.read(4 * 1024 * 1024)
        if not moov_in_head(head):
            resp.close()
            return download_dash(session_id, title, [url], speed, audio_idx)

        audios = [x for x in probe_streams(data=head) if x["type"] == "Audio"]
        if not (0 <= audio_idx < len(audios)):
            audio_idx = 0
        convert_audio = bool(audios) and audios[audio_idx]["codec"] not in SAFE_AUDIO
        cmd = [get_ffmpeg(), "-y", "-nostdin", "-loglevel", "error", "-i", "pipe:0", "-map", "0:v:0"]
        cmd += ["-map", f"0:a:{audio_idx}"] if audios else []
        cmd += ["-c:v", "copy"] + (["-c:a", "aac", "-ac", "2", "-b:a", "192k"] if convert_audio else ["-c:a", "copy"])
        cmd += ["-f", "mpegts", info["file"]]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        fed = [0]
        start_time = time.time()

        def feed():
            try:
                proc.stdin.write(head)
                fed[0] += len(head)
                while not cancel_event.is_set():
                    buf = resp.read(256 * 1024)
                    if not buf:
                        break
                    proc.stdin.write(buf)
                    fed[0] += len(buf)
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
        feeder = threading.Thread(target=feed, daemon=True)
        feeder.start()

        cast_triggered = False
        local_stream_url = f"http://{LOCAL_IP}:{SERVER_PORT}/stream.ts?v={session_id}"
        while proc.poll() is None:
            if cancel_event.is_set():
                proc.kill()
                return
            if session_id == current_session_id:
                dur = max(time.time() - start_time, 0.1)
                app_state["download_speed"] = f"{fed[0] / 1048576 / dur:.1f} MB/s"
                app_state["total_size"] = f"{fed[0] / 1048576:.0f} MB"
                if total:
                    app_state["download_progress"] = min(99, int(fed[0] * 100 / total))
            if not cast_triggered and os.path.exists(info["file"]) and os.path.getsize(info["file"]) >= LIVE_START_BYTES:
                cast_triggered = True
                if session_id == current_session_id:
                    app_state.update(stage="tv", status_text="Играет на ТВ, догружаю")
                tv_cast_url(local_stream_url, title)
            time.sleep(0.25)

        feeder.join(5)
        if cancel_event.is_set():
            return
        if proc.returncode != 0:
            tail = proc.stderr.read().decode("utf-8", errors="ignore").strip().splitlines()[-1:] or [""]
            raise Exception("FFmpeg failed: " + tail[0])
        if total and fed[0] < total:
            raise Exception(f"timed out: download interrupted at {fed[0] * 100 // total}%")
        info["active"] = False
        if not cast_triggered:
            tv_cast_url(local_stream_url, title)
        elif session_id == current_session_id:
            # live TS has no duration and cannot seek: reload the finished file at the same spot
            pos = tv_get_position()
            if pos["state"] == "PLAYING":
                at = hms_to_sec(pos["rel_time"])
                info["resumed"] = at < 10
                tv_cast_url(local_stream_url, title)
                if at >= 10:
                    threading.Thread(target=resume_seek, args=(session_id, at + 2), daemon=True).start()
        if session_id == current_session_id:
            app_state.update(status="playing", stage="tv", status_text="Играет на ТВ", download_progress=100)
    except Exception as e:
        info["active"] = False
        set_error(session_id, e)
    finally:
        if proc and proc.poll() is None:
            proc.kill()
        if resp:
            try:
                resp.close()
            except Exception:
                pass

def download_and_stream_mp4(session_id, title, mp4_url, speed="1.0x", audio_idx=0):
    if speed in ("1.0x", "1.0", "1", None):
        stream_mp4_live(session_id, title, mp4_url, speed, audio_idx)
    else:
        download_dash(session_id, title, [mp4_url], speed, audio_idx)

def ytdlp_streams(url, quality):
    """One yt-dlp call: returns (title, [direct media URLs]) preferring H.264 video + AAC audio."""
    q = quality.replace("p", "") if quality else "720"
    fmt = (f"bv*[height<={q}][vcodec^=avc1]+ba[acodec^=mp4a]/b[height<={q}][vcodec^=avc1]/"
           f"bv*[height<={q}]+ba/b[height<={q}]/b")
    proc = subprocess.run(YT_DLP_BASE + ["--no-playlist", "--get-title", "-g", "-f", fmt, url],
                          capture_output=True, text=True, timeout=90)
    lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    if len(lines) < 2:
        last = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        raise Exception("yt-dlp could not extract: " + last)
    return lines[0], lines[1:3]

def new_session(keep_extra=()):
    """Cancel the running session and register a new one; returns (session_id, session_file)."""
    global current_session_id
    with session_lock:
        if current_session_id in active_sessions:
            cancel_session(active_sessions[current_session_id])
        session_id = int(time.time() * 1000)
        current_session_id = session_id
        session_file = os.path.join(CACHE_DIR, f"stream_{session_id}.ts")
        active_sessions[session_id] = {
            "file": session_file,
            "active": True,
            "cancel_event": threading.Event(),
            "procs": set(),
        }
        cleanup_cache(keep={session_file, *keep_extra})
    return session_id, session_file

def resume_seek(session_id, start_at):
    """Jump to start_at once the TV plays and the file is complete (live-growing streams cannot seek)."""
    if start_at < 10:
        return
    deadline = time.time() + 40 * 60
    while time.time() < deadline:
        time.sleep(1.5)
        info = active_sessions.get(session_id)
        if session_id != current_session_id or not info or info["cancel_event"].is_set():
            return
        if info.get("active"):
            continue
        pos = tv_get_position()
        if pos["state"] == "PLAYING" and hms_to_sec(pos["duration"]) > start_at + 5:
            tv_seek(sec_to_hms(start_at))
            info["resumed"] = True
            return

def handle_cast_request(url, translation_id=None, quality="720p", speed="1.0x", start_at=0, audio_idx=0, episode=None, translation_title=None):
    global app_state

    session_id, session_file = new_session()
    phase(session_id, "session created")
    info = active_sessions[session_id]
    info["start_at"] = start_at
    if not start_at:
        info["resumed"] = True

    # Stop TV immediately on new cast request
    tv_stop()
    time.sleep(0.3)
    phase(session_id, "tv stopped")

    url = normalize_url(url)
    if not episode and is_rezka(url):
        episode = rezka_episode_id(url)
    app_state.update(status="preparing", stage="extract", status_text="Ищу видео", title="Подготовка…", url=url,
                     last_error="", error_hint="", download_progress=0, download_speed="0 MB/s",
                     total_size="0 MB", started_at=time.time(), audio_tracks=[], audio_index=0,
                     episode=episode,
                     opts={"translation_id": translation_id, "quality": quality, "speed": speed,
                           "translation_title": translation_title})

    def remember(title):
        had_thumb = any(h.get("url") == url and h.get("thumb") for h in load_history())
        add_history(title, url, episode=episode, quality=quality, speed=speed, translation_id=translation_id,
                    translation_title=translation_title)
        if not had_thumb:
            threading.Thread(target=fetch_thumb, args=(url,), daemon=True).start()

    try:
        if is_blocked_site(url):
            raise Exception("cloudflare: page is behind a bot check")
        if is_nextembed(url):
            title, master_url, track, ep_id = nextembed_resolve(url, episode, translation_id, translation_title)
            if not episode:
                episode = ep_id
                app_state["episode"] = ep_id
            phase(session_id, "nextembed resolved")
            if speed and speed != "1.0x":
                title += f" ({speed})"
            remember(title)
            threading.Thread(target=download_nextembed, args=(session_id, title, master_url, track, quality, speed), daemon=True).start()
        elif "kinogo" in url or "cinemar.cc" in url:
            data = extract_kinogo(url, translation_id=translation_id, quality=quality, mode="stream",
                                  episode=episode, translation_title=translation_title)
            phase(session_id, "kinogo extracted")
            title = data.get("title", "Kinogo Media")
            stream_url = data.get("stream_url")
            if speed and speed != "1.0x":
                title += f" ({speed})"
            remember(title)
            threading.Thread(target=download_and_stream_hls, args=(session_id, title, stream_url, {"Referer": "https://cinemar.cc/"}, speed, quality), daemon=True).start()
        elif "vkvideo.ru" in url or "vk.com/video" in url:
            title, stream_url = extract_vk(url)
            if speed and speed != "1.0x":
                title += f" ({speed})"
            remember(title)
            threading.Thread(target=download_and_stream_hls, args=(session_id, title, stream_url, None, speed, quality), daemon=True).start()
        elif "tulsa-king.net" in url or "rezka" in url:
            title, stream_url, _ = extract_rezka(rezka_target_url(url, episode, translation_id), quality=quality)
            # a voice-over without this episode makes the site silently show another one: catch that
            em = re.match(r'^s(\d+)e(\d+)$', episode or "")
            tm = re.search(r'(\d+)\s+сезон(?:\s+(\d+)\s+сери)?', title)
            if em and tm and (int(tm.group(1)) != int(em.group(1)) or int(tm.group(2) or 0) != int(em.group(2))):
                raise Exception("voice-over unavailable for this episode")
            if translation_title and episode:
                title += f" ({translation_title})"
            if speed and speed != "1.0x":
                title += f" ({speed})"
            remember(title)
            threading.Thread(target=download_and_stream_mp4, args=(session_id, title, stream_url, speed, audio_idx), daemon=True).start()
        elif url.startswith("magnet:"):
            torr_data = json.dumps({"action": "add", "link": url, "save_to_db": True}).encode("utf-8")
            req = urllib.request.Request(f"http://{TV['ip']}:8090/torrents", data=torr_data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                res = json.loads(r.read().decode())
                h = res.get("hash")
            title = "Torrent Stream"
            stream_url = f"http://127.0.0.1:8090/stream?link={h}&index=1&play"
            tv_cast_url(stream_url, title)
            info["active"] = False
            app_state.update(status="playing", stage="tv", status_text="Играет через TorrServer", title=title)
            remember(title)
        else:
            is_youtube = bool(re.search(r'(youtube\.com|youtu\.be)/', url))
            q_val = quality.replace("p", "") if quality else "720"
            lines = []
            proc = None
            if is_youtube:
                title, urls = ytdlp_streams(url, quality)
                if speed and speed != "1.0x":
                    title += f" ({speed})"
                remember(title)
                if len(urls) == 1 and ".m3u8" in urls[0]:
                    threading.Thread(target=download_and_stream_hls, args=(session_id, title, urls[0], None, speed, quality), daemon=True).start()
                else:
                    threading.Thread(target=download_dash, args=(session_id, title, urls, speed, audio_idx), daemon=True).start()
            else:
                cmd = YT_DLP_BASE + ["--get-title", "--get-url", "-f", f"best[height<={q_val}]/best", url]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                lines = proc.stdout.strip().splitlines()
            if is_youtube:
                pass
            elif len(lines) >= 3:
                # separate video/audio streams (DASH): download both in parallel and merge
                title = lines[0]
                if speed and speed != "1.0x":
                    title += f" ({speed})"
                remember(title)
                threading.Thread(target=download_dash, args=(session_id, title, lines[1:3], speed, audio_idx), daemon=True).start()
            elif len(lines) >= 2:
                direct_url = lines[1]
                title = nice_title(lines[0], direct_url)
                if speed and speed != "1.0x":
                    title += f" ({speed})"
                remember(title)
                if ".m3u8" in direct_url:
                    threading.Thread(target=download_and_stream_hls, args=(session_id, title, direct_url, None, speed, quality), daemon=True).start()
                else:
                    threading.Thread(target=download_and_stream_mp4, args=(session_id, title, direct_url, speed, audio_idx), daemon=True).start()
            elif re.search(r'\.(mp4|m3u8|mkv|webm|ts|mov)(\?|$)', url, re.I):
                tv_cast_url(url, "Web Stream")
                info["active"] = False
                app_state.update(status="playing", stage="tv", status_text="Играет на ТВ", title="Web Stream")
                remember("Web Stream")
            else:
                last = (proc.stderr.strip().splitlines() or ["no output"])[-1]
                raise Exception("yt-dlp could not extract: " + last)
        if start_at >= 10:
            threading.Thread(target=resume_seek, args=(session_id, start_at), daemon=True).start()
    except Exception as e:
        info["active"] = False
        set_error(session_id, e)

def switch_audio(index):
    """Re-mux the already downloaded MP4 with another audio track and resume at the same position."""
    info = active_sessions.get(current_session_id)
    src = info.get("src_mp4") if info else None
    if not src or not os.path.exists(src):
        return False, "Смена дорожки доступна только после загрузки файла"
    pos = hms_to_sec(tv_get_position()["rel_time"])
    title, speed = app_state["title"], info.get("speed", "1.0x")
    session_id, _ = new_session(keep_extra={src})
    new_info = active_sessions[session_id]
    new_info.update(src_mp4=src, speed=speed, start_at=pos, resumed=pos < 10)

    def work():
        try:
            finish_mp4(session_id, title, src, speed, index)
            resume_seek(session_id, pos)
        except Exception as e:
            new_info["active"] = False
            set_error(session_id, e)
    threading.Thread(target=work, daemon=True).start()
    return True, ""

def cast_neighbor(delta):
    """Cast the previous/next episode with the current options; returns (ok, error)."""
    target = neighbor_episode(app_state["url"], app_state.get("episode"), delta)
    if not target:
        return False, "Для этой ссылки нет %s серии" % ("следующей" if delta > 0 else "предыдущей")
    o = app_state.get("opts") or {}
    threading.Thread(target=handle_cast_request, kwargs=dict(
        url=target[0], translation_id=o.get("translation_id"), quality=o.get("quality", "720p"),
        speed=o.get("speed", "1.0x"), episode=target[1], translation_title=o.get("translation_title")), daemon=True).start()
    return True, ""

def tracker_loop():
    """Every few seconds: remember the playback position and start the next episode when one ends."""
    seen = {"sid": None, "playing": False, "rel": 0, "dur": 0, "fired": False}
    while True:
        time.sleep(4)
        try:
            url, sid, episode = app_state["url"], current_session_id, app_state.get("episode")
            if not url or app_state["status"] not in ("playing", "downloading"):
                continue
            if seen["sid"] != sid:
                seen.update(sid=sid, playing=False, rel=0, dur=0, fired=False)
            info = active_sessions.get(sid) or {}
            pos = tv_get_position()
            state, rel, dur = pos["state"], hms_to_sec(pos["rel_time"]), hms_to_sec(pos["duration"])
            if state == "PLAYING" or state.startswith("PAUSED"):
                seen["playing"] = True
                if dur > 0:
                    seen["rel"], seen["dur"] = rel, dur
                if dur > 60 and rel > 15 and info.get("resumed", True):
                    update_history(url, episode, position=rel, duration=dur)
            elif state in ("STOPPED", "NO_MEDIA_PRESENT") and seen["playing"] and not seen["fired"]:
                ended = seen["dur"] > 60 and seen["rel"] >= seen["dur"] - 25
                if ended and not info.get("active"):
                    seen["fired"] = True
                    update_history(url, episode, position=0)
                    if settings["autonext"] and neighbor_episode(url, episode, 1):
                        app_state.update(status="preparing", status_text="Включаю следующую серию")
                        cast_neighbor(1)
                    else:
                        app_state.update(status="idle", status_text="Воспроизведение завершено")
        except Exception as e:
            print("Tracker error:", e)

MANIFEST = {
    "name": "TV Caster Hub",
    "short_name": "TV Caster",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0b1120",
    "theme_color": "#0b1120",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}],
}
ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#38bdf8"/><stop offset="1" stop-color="#818cf8"/></linearGradient></defs><rect width="512" height="512" rx="112" fill="#0b1120"/><rect x="96" y="132" width="320" height="200" rx="28" fill="none" stroke="url(#g)" stroke-width="28"/><path d="M226 190l76 42-76 42z" fill="url(#g)"/><path d="M160 396h192" stroke="url(#g)" stroke-width="28" stroke-linecap="round"/></svg>"""
TEMPLATE_FILE = os.path.join(BASE_DIR, "templates", "index.html")

class TVCasterHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        if "/api/status" in (self.path or ""):
            return  # polled every second, would flood the log
        super().log_message(fmt, *args)

    def do_HEAD(self):
        if self.path.startswith("/stream.ts"):
            self.serve_stream(send_body=False)
        else:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_GET(self):
        clean = self.path.split("?")[0]
        if clean in ("/", "/index.html"):
            self.serve_ui()
        elif clean == "/api/status":
            self.serve_status()
        elif clean == "/api/history":
            self.serve_history()
        elif clean == "/api/tvs":
            devices = refresh_tv()
            self.send_json({"current": TV["ip"], "devices": [{"ip": d["ip"], "name": d["name"]} for d in devices]})
        elif clean == "/api/search":
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0]
            try:
                self.send_json({"ok": True, "results": search_titles(q)})
            except Exception as e:
                self.send_json({"ok": False, "error": "Поиск недоступен: " + friendly_error(e)[0], "results": []})
        elif clean == "/api/settings":
            self.send_json(settings)
        elif clean == "/manifest.webmanifest":
            self.send_bytes(json.dumps(MANIFEST).encode(), "application/manifest+json")
        elif clean == "/icon.svg":
            self.send_bytes(ICON_SVG.encode(), "image/svg+xml")
        elif clean.startswith("/stream.ts"):
            self.serve_stream(send_body=True)
        else:
            self.send_error(404)

    def do_POST(self):
        clean = self.path.split("?")[0]
        content_len = int(self.headers.get("Content-Length", 0))
        post_body = self.rfile.read(content_len).decode("utf-8")
        try:
            data = json.loads(post_body)
        except Exception:
            data = {}

        if clean == "/api/inspect":
            url = data.get("url", "")
            if not url:
                self.send_json({"ok": False, "error": "URL required"})
                return
            try:
                info = inspect_url(url)
                self.send_json(info)
            except Exception as e:
                self.send_json({"ok": False, "error": friendly_error(e)[0]})

        elif clean == "/api/cast":
            url = data.get("url", "")
            if not url:
                self.send_json({"ok": False, "error": "URL required"})
                return
            args = (url, data.get("translation_id"), data.get("quality", "720p"), data.get("speed", "1.0x"),
                    int(data.get("start_at") or 0), int(data.get("audio_index") or 0),
                    data.get("episode") or None, data.get("translation_title") or None)
            threading.Thread(target=handle_cast_request, args=args, daemon=True).start()
            self.send_json({"ok": True, "message": "Cast initiated"})

        elif clean in ("/api/next", "/api/prev"):
            ok, err = cast_neighbor(1 if clean == "/api/next" else -1)
            self.send_json({"ok": ok, "error": err})

        elif clean == "/api/audio_track":
            ok, err = switch_audio(int(data.get("index", 0)))
            self.send_json({"ok": ok, "error": err})

        elif clean == "/api/history/delete":
            delete_history(data.get("url"), data.get("episode"))
            self.send_json({"ok": True})

        elif clean == "/api/history/clear":
            delete_history(None)
            self.send_json({"ok": True})

        elif clean == "/api/tv":
            ip = data.get("ip")
            dev = next((d for d in refresh_tv(prefer_ip=ip) if d["ip"] == ip), None)
            if dev:
                select_tv(dev)
            self.send_json({"ok": bool(dev), "current": TV["ip"], "name": TV["name"]})

        elif clean == "/api/settings":
            if "autonext" in data:
                settings["autonext"] = bool(data["autonext"])
            save_settings()
            self.send_json(settings)

        elif clean == "/api/control":
            action = data.get("action", "")
            if action == "play":
                tv_play()
            elif action == "pause":
                tv_pause()
            elif action == "stop":
                with session_lock:
                    if current_session_id in active_sessions:
                        cancel_session(active_sessions[current_session_id])
                tv_stop()
                app_state["status"] = "idle"
                app_state["status_text"] = "Остановлено"
            elif action == "seek":
                target = data.get("target", "00:00:00")
                tv_seek(target)
            elif action == "volume":
                vol = max(0, min(100, int(data.get("value", 50))))
                tv_set_volume(vol)
            self.send_json({"ok": True})
        else:
            self.send_error(404)

    def send_bytes(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj):
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def serve_status(self):
        tv_pos = tv_get_position()
        vol = tv_get_volume()
        status = app_state["status"]
        info = active_sessions.get(current_session_id) or {}
        progress = app_state["download_progress"]
        eta = None
        if status == "downloading" and progress >= 3 and app_state["started_at"]:
            elapsed = time.time() - app_state["started_at"]
            eta = int(elapsed * (100 - progress) / progress)
        status_payload = {
            "app_status": status,
            "stage": app_state["stage"],
            "status_text": app_state["status_text"],
            "title": app_state["title"],
            "url": app_state["url"],
            "progress": progress,
            "buffered": progress if info.get("active") and status in ("downloading", "playing") else 100,
            "eta": eta,
            "speed": app_state["download_speed"],
            "size": app_state["total_size"],
            "error": app_state["last_error"],
            "error_hint": app_state["error_hint"],
            "tv_state": tv_pos["state"],
            "rel_time": tv_pos["rel_time"],
            "duration": tv_pos["duration"],
            "volume": vol,
            "tv_ip": TV["ip"],
            "tv_name": TV["name"],
            "tv_online": TV["online"],
            "audio_tracks": app_state["audio_tracks"],
            "audio_index": app_state["audio_index"],
            "next_url": bool(app_state["url"] and neighbor_episode(app_state["url"], app_state.get("episode"), 1)),
            "prev_url": bool(app_state["url"] and neighbor_episode(app_state["url"], app_state.get("episode"), -1)),
            "episode": app_state.get("episode"),
            "autonext": settings["autonext"],
        }
        self.send_json(status_payload)

    def serve_history(self):
        self.send_json(load_history())

    def serve_stream(self, send_body=True):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        v_param = query.get("v", [None])[0]
        try:
            req_session_id = int(v_param) if v_param else current_session_id
        except Exception:
            req_session_id = current_session_id

        session_info = active_sessions.get(req_session_id)
        target_file = session_info["file"] if (session_info and os.path.exists(session_info["file"])) else CURRENT_FILE
        if not session_info and v_param and v_param.isdigit():
            # server restarted mid-playback: a finished file from the old session can still be served
            leftover = os.path.join(CACHE_DIR, f"stream_{v_param}.ts")
            if os.path.exists(leftover):
                target_file = leftover

        if not os.path.exists(target_file):
            self.send_error(404, "Stream file not ready yet")
            return

        is_active = session_info.get("active", False) if session_info else False
        cancel_event = session_info.get("cancel_event") if session_info else threading.Event()
        range_header = self.headers.get("Range")

        if is_active:
            # File is actively growing in background for this session. Continuous live streaming!
            self.send_response(200, "OK")
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Accept-Ranges", "none")
            self.send_header("transferMode.dlna.org", "Streaming")
            self.send_header("Connection", "close")
            self.end_headers()

            if not send_body:
                return

            try:
                with open(target_file, "rb") as f:
                    while not cancel_event.is_set():
                        chunk = f.read(65536)
                        if not chunk:
                            if session_info.get("active", False):
                                time.sleep(0.1)
                                continue
                            else:
                                break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                print("Live stream error:", e)
            return

        # Download is finished! Full file on disk. Support Range requests and seeking.
        file_size = os.path.getsize(target_file)
        start = 0
        end = file_size - 1
        is_range = False

        if range_header:
            m = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if m:
                is_range = True
                start = int(m.group(1))
                if m.group(2):
                    end = int(m.group(2))
                if end >= file_size:
                    end = file_size - 1

        if start > end or start >= file_size:
            self.send_error(416, "Range Not Satisfiable")
            return

        content_length = (end - start) + 1

        if is_range:
            self.send_response(206, "Partial Content")
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        else:
            self.send_response(200, "OK")

        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Content-Length", str(content_length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("transferMode.dlna.org", "Streaming")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        if not send_body:
            return

        try:
            with open(target_file, "rb") as f:
                f.seek(start)
                rem = content_length
                while rem > 0 and not cancel_event.is_set():
                    chunk = f.read(min(65536, rem))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    rem -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            print("Completed file stream error:", e)

    def serve_ui(self):
        try:
            with open(TEMPLATE_FILE, "rb") as f:
                body = f.read()
        except OSError:
            self.send_error(500, "templates/index.html is missing")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

if __name__ == "__main__":
    cleanup_cache(max_age=60 * 60)  # keep the last hour so a restart does not cut playback
    threading.Thread(target=refresh_tv, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    server = ThreadedServer(("0.0.0.0", SERVER_PORT), TVCasterHandler)
    print(f"TV Caster Web App running on http://{LOCAL_IP}:{SERVER_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
