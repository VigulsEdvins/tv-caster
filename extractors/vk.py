import http.server
import re
import urllib.request


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
