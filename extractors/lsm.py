import html
import json
import re
import shutil
import subprocess
import time
import urllib.request
from extractors.tools import NODE_BIN


_lsm_sessions = {}

def is_replay_lsm(url):
    if not url:
        return False
    u = url.lower()
    return "replay.lsm.lv" in u or "ltv.lsm.lv" in u or "lsm.lv/skaties" in u

def extract_replay_lsm(url, translation_id=None, quality="720p"):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        html_text = resp.read().decode('utf-8', errors='ignore')

    embed_url = None
    if "ltv.lsm.lv/embed" in url:
        embed_url = url
    else:
        m_embed = re.search(r'https:(?:\\\\u002F|/){2}ltv\.lsm\.lv(?:\\\\u002F|/)embed[^\"]+', html_text)
        if m_embed:
            embed_url = m_embed.group(0).replace(r'\\u002F', '/')

    title = None
    m_rep = re.search(r'<script>window\.__REPLAY__=(.*?);</script>', html_text, re.DOTALL)
    if m_rep:
        try:
            node_bin = shutil.which("node") or NODE_BIN
            node_cmd = [node_bin, '-e', 'let w={}; eval("w.r="+process.argv[1]); console.log(JSON.stringify(w.r.data[0]))', m_rep.group(1)]
            node_res = subprocess.run(node_cmd, capture_output=True, text=True, timeout=8)
            if node_res.returncode == 0:
                d = json.loads(node_res.stdout)
                if 'upcomingGuide' in d:
                    act = [g for g in d['upcomingGuide'] if g.get('isActive')]
                    if act and act[0].get('title'):
                        title = act[0].get('title')
                if not title and 'channel' in d:
                    ch = d['channel']
                    title = ch.get('title_long') or ch.get('name')
                if not title and 'mediaItem' in d:
                    title = d['mediaItem'].get('title')
                if not title and 'metadata' in d:
                    title = d['metadata'].get('title')
                if not embed_url:
                    embed_url = ((d.get('playback') or {}).get('service') or {}).get('url')
        except Exception:
            pass

    if not embed_url:
        raise Exception("Could not find embed player on LSM page")

    req_embed = urllib.request.Request(embed_url, headers={'Referer': 'https://replay.lsm.lv/', 'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req_embed, timeout=10) as resp:
        embed_html = resp.read().decode('utf-8', errors='ignore')

    m_live = re.search(r':embed-data=\"([^\"]+)\"', embed_html)
    if m_live:
        embed_data = json.loads(html.unescape(m_live.group(1)))
    else:
        m_vod = re.search(r'window\.ltvEmbedPayload\s*=\s*(\{.*?\})\s*;?\s*</script>', embed_html, re.DOTALL)
        if m_vod:
            embed_data = json.loads(m_vod.group(1))
        else:
            raise Exception("Could not find LSM embed payload")

    src = embed_data.get('source') or {}
    parent = embed_data.get('parentInfo') or {}
    if not title:
        title = parent.get('title') or 'LSM Video'

    asset_id = str(src.get('item_id'))
    if not asset_id or asset_id == 'None':
        raise Exception("Could not extract LSM asset ID")

    app_id = src.get('app_id', 'replay')
    embed_token = src.get('embedtoken')
    item_type = src.get('item_type') or parent.get('type')
    is_live = (item_type == 'channel') or ('live' in embed_url)
    video_type = 'LIVE' if is_live else 'MOVIE'
    tenant_uid = {'lsm': 'LsnP', 'bernistaba': 'BrnP', 'replay': 'LsmP'}.get(app_id, 'LsmP')

    cfg_url = f'https://telpa.lsm.lv/api/products/{asset_id}/videos/player/configuration?platform=BROWSER&videoType={video_type}&tenantUid={tenant_uid}'
    req_cfg = urllib.request.Request(cfg_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req_cfg, timeout=10) as resp:
        cfg_data = json.loads(resp.read().decode('utf-8'))

    playlist_url = cfg_data.get('playlistUrl') or (cfg_data.get('playlist') or {}).get('url')
    if not playlist_url:
        raise Exception("Could not get LSM playlist URL from configuration")
    if embed_token and 'cdnToken=' not in playlist_url:
        sep = '&' if '?' in playlist_url else '?'
        playlist_url += f'{sep}cdnToken={embed_token}'

    req_pl = urllib.request.Request(playlist_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req_pl, timeout=10) as resp:
        pl_data = json.loads(resp.read().decode('utf-8'))

    hls_sources = (pl_data.get('sources') or {}).get('HLS') or []
    if not hls_sources:
        raise Exception("No HLS streams in LSM playlist")

    master_hls_url = hls_sources[0].get('src', '')
    if master_hls_url.startswith('//'):
        master_hls_url = 'https:' + master_hls_url

    req_m3u8 = urllib.request.Request(master_hls_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req_m3u8, timeout=10) as resp:
        final_master_url = resp.geturl()
        m3u8_text = resp.read().decode('utf-8', errors='ignore')

    translations = []
    raw_qualities_num = []
    video_streams = {}
    audio_streams = {}
    curr_stream_inf = None
    for line in m3u8_text.splitlines():
        line_s = line.strip()
        if line_s.startswith('#EXT-X-MEDIA:TYPE=AUDIO'):
            attrs = {}
            for match in re.finditer(r'([A-Z0-9\-]+)=(?:\"([^\"]*)\"|([^,]*))', line_s):
                attrs[match.group(1)] = match.group(2) if match.group(2) is not None else match.group(3)
            lang = attrs.get('LANGUAGE', '').lower()
            name = attrs.get('NAME', '')
            uri = attrs.get('URI', '')
            if uri:
                uri = uri if uri.startswith('http') else urllib.parse.urljoin(final_master_url, uri)
            t_id = lang or name
            if lang == 'lav':
                t_name = 'Latviešu valoda'
            elif lang:
                t_name = f'Oriģinālvaloda ({lang.upper()})'
            else:
                t_name = name or 'Audio celiņš'
            if not any(tr['id'] == t_id for tr in translations):
                translations.append({'id': t_id, 'title': t_name, 'lang': lang, 'name': name, 'uri': uri})
            if t_id and uri:
                audio_streams[t_id] = uri
                if lang:
                    audio_streams[lang] = uri
        elif line_s.startswith('#EXT-X-STREAM-INF'):
            curr_stream_inf = line_s
            m_res = re.search(r'RESOLUTION=\d+x(\d+)', line_s)
            if m_res:
                q_int = int(m_res.group(1))
                if q_int not in raw_qualities_num:
                    raw_qualities_num.append(q_int)
        elif curr_stream_inf and not line_s.startswith('#') and line_s:
            v_url = line_s if line_s.startswith('http') else urllib.parse.urljoin(final_master_url, line_s)
            m_res = re.search(r'RESOLUTION=\d+x(\d+)', curr_stream_inf)
            if m_res:
                q_str = f"{m_res.group(1)}p"
                video_streams[q_str] = v_url
            if "default" not in video_streams:
                video_streams["default"] = v_url
            curr_stream_inf = None

    raw_qualities_num.sort(reverse=True)
    qualities = [f"{q}p" for q in raw_qualities_num]
    if not qualities:
        qualities = ["1080p", "720p", "480p", "360p"]

    selected_translation = None
    orig_requested = "originalvalodas-izvele" in url.lower() or "original" in url.lower()
    if orig_requested:
        for tr in translations:
            if tr.get('lang') != 'lav':
                selected_translation = tr['id']
                break

    if not selected_translation and translations:
        selected_translation = translations[0]['id']

    if selected_translation and translations and translations[0]['id'] != selected_translation:
        sel_idx = next((i for i, t in enumerate(translations) if t['id'] == selected_translation), None)
        if sel_idx is not None:
            translations.insert(0, translations.pop(sel_idx))

    return {
        "title": title,
        "is_live": is_live,
        "master_hls_url": master_hls_url,
        "final_master_url": final_master_url,
        "raw_playlist": m3u8_text,
        "translations": translations,
        "qualities": qualities,
        "video_streams": video_streams,
        "audio_streams": audio_streams,
        "selected_translation": selected_translation,
    }

def select_lsm_video_url(video_streams, quality="720p"):
    if not video_streams:
        return None
    if quality in video_streams:
        return video_streams[quality]
    m = re.search(r'(\d+)', quality or '')
    req_h = int(m.group(1)) if m else 720
    parsed = []
    for k, v in video_streams.items():
        mk = re.search(r'(\d+)', k)
        if mk:
            parsed.append((int(mk.group(1)), v))
    parsed.sort(key=lambda x: x[0], reverse=True)
    for h, v in parsed:
        if h <= req_h:
            return v
    if parsed:
        return parsed[-1][1]
    return list(video_streams.values())[0]

def select_lsm_audio_url(audio_streams, chosen_lang=None):
    if not audio_streams:
        return None
    if chosen_lang and chosen_lang in audio_streams:
        return audio_streams[chosen_lang]
    if chosen_lang:
        low = chosen_lang.lower()
        for k, v in audio_streams.items():
            if k.lower() == low or low in k.lower():
                return v
    return list(audio_streams.values())[0]

def generate_lsm_master_playlist(lsm_data, chosen_lang=None, chosen_quality=None):
    raw = lsm_data.get("raw_playlist", "")
    base_url = lsm_data.get("final_master_url", "")
    chosen_lang = (chosen_lang or lsm_data.get("chosen_lang") or "").lower()

    # Re-fetch raw playlist if older than 1 hour to keep CDN session alive
    if time.time() - lsm_data.get("fetched_at", 0) > 3600 and lsm_data.get("master_url"):
        try:
            req = urllib.request.Request(lsm_data["master_url"], headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8) as r:
                base_url = r.geturl()
                raw = r.read().decode('utf-8', errors='ignore')
                lsm_data["raw_playlist"] = raw
                lsm_data["final_master_url"] = base_url
                lsm_data["fetched_at"] = time.time()
        except Exception:
            pass

    lines = []
    stream_inf_blocks = []
    current_inf = None

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA:TYPE=AUDIO"):
            m_lang = re.search(r'LANGUAGE="?([^",]+)"?', line)
            m_name = re.search(r'NAME="?([^",]+)"?', line)
            lang = (m_lang.group(1) if m_lang else "").lower()
            name = (m_name.group(1) if m_name else "").lower()

            is_match = False
            if chosen_lang:
                if chosen_lang in (lang, name):
                    is_match = True
                elif chosen_lang == "orig" and lang != "lav":
                    is_match = True
            else:
                is_match = (lang == "lav") or ('DEFAULT="YES"' in line.upper())

            line = re.sub(r'DEFAULT="?(?:YES|NO)"?', f'DEFAULT={"YES" if is_match else "NO"}', line)
            line = re.sub(r'AUTOSELECT="?(?:YES|NO)"?', f'AUTOSELECT={"YES" if is_match else "NO"}', line)
            m_uri = re.search(r'URI="([^"]+)"', line)
            if m_uri:
                abs_uri = urllib.parse.urljoin(base_url, m_uri.group(1))
                line = line.replace(f'URI="{m_uri.group(1)}"', f'URI="{abs_uri}"')
            lines.append(line)
        elif line.startswith("#EXT-X-STREAM-INF"):
            current_inf = line
        elif current_inf is not None:
            abs_stream_url = urllib.parse.urljoin(base_url, line)
            stream_inf_blocks.append((current_inf, abs_stream_url))
            current_inf = None
        else:
            lines.append(line)

    q_num = (chosen_quality or lsm_data.get("quality") or "720").replace("p", "")
    def q_score(block):
        inf_line, _ = block
        m = re.search(r'RESOLUTION=\d+x(\d+)', inf_line)
        if m and m.group(1) == q_num:
            return 100000000
        bw = re.search(r'BANDWIDTH=(\d+)', inf_line)
        return int(bw.group(1)) if bw else 0

    stream_inf_blocks.sort(key=q_score, reverse=True)
    for inf_line, stream_url in stream_inf_blocks:
        lines.append(inf_line)
        lines.append(stream_url)

    return "\n".join(lines) + "\n"
