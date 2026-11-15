import ast
import html
import json
import re
import urllib.parse
import urllib.request


def is_sportacentrs(url):
    if not url:
        return False
    u = url.lower()
    return "sportacentrs.com" in u or "tiesraides.lv" in u

def extract_sportacentrs(url, quality="720p"):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=12) as r:
        page = r.read().decode("utf-8", errors="ignore")

    m_title = (re.search(r'<meta[^>]+property=["\x27]og:title["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', page)
               or re.search(r'<title>(.*?)</title>', page, re.DOTALL))
    title = html.unescape(m_title.group(1).strip()) if m_title else "Sportacentrs TV"
    title = re.sub(r'\s*&ndash;\s*Sportacentrs.*$', '', title)
    title = re.sub(r'\s*–\s*Sportacentrs.*$', '', title)
    title = title.strip()

    m_thumb = re.search(r'<meta[^>]+property=["\x27]og:image["\x27][^>]+content=["\x27]([^"\x27]+)["\x27]', page)
    thumb = html.unescape(m_thumb.group(1).strip()) if m_thumb else None

    if "live.tiesraides.lv" in url or "cdn.tiesraides.lv" in url:
        embed_url = url
        embed_html = page
    else:
        m_iframe = re.search(r'<iframe[^>]+src=["\x27]([^"\x27]*(?:tiesraides\.lv|sportacentrs)[^"\x27]*)["\x27]', page)
        if m_iframe:
            embed_url = urllib.parse.urljoin(url, html.unescape(m_iframe.group(1)))
            req_embed = urllib.request.Request(embed_url, headers={"User-Agent": "Mozilla/5.0", "Referer": url})
            with urllib.request.urlopen(req_embed, timeout=12) as r2:
                embed_html = r2.read().decode("utf-8", errors="ignore")
        else:
            embed_url = url
            embed_html = page

    m_qual = re.search(r'this\.qualities\s*=\s*(\[\[.*?\]\]);', embed_html, re.DOTALL)
    if m_qual:
        try:
            raw_matrix = ast.literal_eval(m_qual.group(1))
        except Exception:
            raw_matrix = json.loads(m_qual.group(1).replace("'", '"'))
        btns = re.findall(r'class=[\"\x27][^\"\x27]*quality-button[^\"\x27]*[\"\x27][^>]*>.*?<span>(.*?)</span>', embed_html, re.DOTALL)
        streams = {}
        for i, u_stream in enumerate(raw_matrix[0]):
            label = btns[i] if i < len(btns) else f"{i}"
            m_h = re.search(r'(\d+p)', label)
            q = m_h.group(1) if m_h else label.strip()
            hls_u = re.sub(r'/manifest\.f4m(?:\?[^\s\'"]*)?$', '/playlist.m3u8', u_stream)
            if hls_u.startswith("http://"):
                hls_u = "https://" + hls_u[7:]
            streams[q] = hls_u

        qualities = list(streams.keys())
        qualities.sort(key=lambda x: int(re.sub(r"\D", "", x) or 0), reverse=True)
        chosen = streams.get(quality) or (streams.get("720p") if "720p" in streams else None) or (streams[qualities[0]] if streams else "")
        return {
            "title": title,
            "kind": "live",
            "is_live": True,
            "qualities": qualities,
            "stream_url": chosen,
            "streams": streams,
            "thumb": thumb,
        }

    m_srcs = re.findall(r'src:\s*[\"\x27]([^\"\x27]+)[\"\x27]', embed_html)
    if m_srcs:
        vod_urls = []
        for s in m_srcs:
            s_abs = s if s.startswith("http") else ("https:" + s if s.startswith("//") else urllib.parse.urljoin(embed_url, s))
            vod_urls.append(s_abs)
        if not thumb:
            m_p = re.search(r'poster:\s*[\"\x27]([^\"\x27]+)[\"\x27]', embed_html)
            if m_p:
                p_url = m_p.group(1)
                thumb = p_url if p_url.startswith("http") else ("https:" + p_url if p_url.startswith("//") else urllib.parse.urljoin(embed_url, p_url))
        return {
            "title": title,
            "kind": "movie",
            "is_live": False,
            "qualities": ["720p"],
            "stream_url": vod_urls[0] if vod_urls else None,
            "urls": vod_urls,
            "thumb": thumb,
        }

    raise Exception("Could not find playable stream on Sportacentrs / Tiesraides page")
