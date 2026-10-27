const https = require('https');
const http = require('http');
const url = require('url');

function fetchUrl(targetUrl, headers = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(targetUrl);
    const client = u.protocol === 'https:' ? https : http;
    const req = client.get(targetUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ...headers
      }
    }, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => resolve({ body: data, headers: res.headers }));
    });
    req.on('error', reject);
    req.setTimeout(10000, () => { req.destroy(); reject(new Error('Timeout')); });
  });
}

function postJson(targetUrl, payload, headers = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(targetUrl);
    const postBody = JSON.stringify(payload);
    const req = https.request({
      hostname: u.hostname,
      path: u.pathname + u.search,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(postBody),
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ...headers
      }
    }, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => resolve(JSON.parse(data)));
    });
    req.on('error', reject);
    req.setTimeout(10000, () => { req.destroy(); reject(new Error('Timeout')); });
    req.write(postBody);
    req.end();
  });
}

async function extract(inputUrl, translationId = null, quality = '720p', mode = 'stream', episodeId = null, translationTitle = null) {
  let cinemarUrl = inputUrl;
  let title = 'Kinogo Media';

  if (!inputUrl.includes('cinemar.cc')) {
    const page = await fetchUrl(inputUrl);
    let titleMatch = page.body.match(/<title>(.*?)<\/title>/);
    let rawTitle = titleMatch ? titleMatch[1] : 'Kinogo Video';
    title = rawTitle.replace(/смотреть онлайн.*$/, '').replace(/Смотреть сериал/g, '').trim();

    const iframeMatch = page.body.match(/<iframe[^>]+src=["']([^"']*cinemar\.cc[^"']*)["']/);
    if (!iframeMatch) {
      throw new Error('No Cinemar player found on page');
    }
    cinemarUrl = iframeMatch[1];
  }
  if (cinemarUrl.startsWith('//')) cinemarUrl = 'https:' + cinemarUrl;

  const cinemarPage = await fetchUrl(cinemarUrl, { 'Referer': inputUrl });
  const cinemarMatch = cinemarPage.body.match(/Cinemar\((.*?)\)<\/script>/s);
  if (!cinemarMatch) {
    throw new Error('Could not find Cinemar data');
  }
  const cinemarConfig = JSON.parse(cinemarMatch[1]);
  const file = cinemarConfig.file;
  if (!file.startsWith('#2')) {
    throw new Error('Unsupported Cinemar file format: ' + file.substr(0, 10));
  }

  const self = {
    dm: file.substr(2, 2),
    _ml: 32,
    ex: ["slice","atob","substr","salt","temp","escape","push","this","btoa","pop","JSON.stringify","length","JSON.parse","forEach","splice","decodeURIComponent","unshift","","clone","insert"]
  };
  function slice(e) {
    return e.split(String.fromCharCode(self.dm)).map(function(e){
      var t = parseInt(e.slice(-1));
      return e.length > self._ml ? e.substr(2*t, e.length - 3*t - 1) + e.substr(0, t) : e;
    }).join(self.ex[17]);
  }
  function readString(val, ...actions) {
    var padding = val.length % 4;
    if (padding) val += "=".repeat(4 - padding);
    actions.forEach(function(v) {
      val = eval(self.ex[v] + "(val)");
    });
    return val;
  }
  const playlist = readString(slice(file.substr(4)), 1, 5, 15, 12);
  if (!playlist || !playlist.length) {
    throw new Error('Empty playlist decoded');
  }

  const clean = (t) => (t || '').replace(/<[^>]+>/g, '').trim();
  const isSeries = playlist.length > 0 && Array.isArray(playlist[0].folder);

  if (mode === 'info') {
    if (isSeries) {
      return {
        title: title,
        kind: 'series',
        seasons: playlist.map(sn => ({
          id: sn.id,
          title: clean(sn.title),
          episodes: (sn.folder || []).map(ep => ({
            id: ep.id,
            title: clean(ep.title),
            translations: (ep.folder || []).map(v => ({ title: clean(v.title), id: v.id }))
          }))
        })),
        translations: [],
        qualities: ['720p', '1080p', '480p', '360p'],
        speeds: ['1.0x', '1.25x', '1.5x']
      };
    }
    return {
      title: title,
      kind: 'movie',
      translations: playlist.map(p => ({ title: clean(p.title), id: p.id })),
      qualities: ['720p', '1080p', '480p', '360p'],
      speeds: ['1.0x', '1.25x', '1.5x']
    };
  }

  // Choose the requested item (movie: voice; series: episode, then voice) or the first one
  let item;
  let epLabel = '';
  if (isSeries) {
    let season = null, episode = null;
    for (const sn of playlist) {
      for (const ep of (sn.folder || [])) {
        if (ep.id === episodeId) { season = sn; episode = ep; }
      }
    }
    if (!episode) { season = playlist[0]; episode = (season.folder || [])[0]; }
    if (!episode) throw new Error('Series has no episodes');
    const voices = episode.folder || [];
    item = voices.find(v => v.id === translationId)
      || voices.find(v => clean(v.title) === translationTitle)
      || voices[0];
    if (!item) throw new Error('Episode has no voice-over');
    epLabel = `${clean(season.title)}, ${clean(episode.title)}`;
  } else {
    item = playlist.find(p => p.id === translationId) || playlist[0];
  }

  const cleanTitle = clean(item.title) || 'HD';
  const fullTitle = isSeries ? `${title} - ${epLabel} (${cleanTitle})` : `${title} (${cleanTitle})`;

  // Request stream info
  const loadRes = await postJson('https://cinemar.cc/api/playlist/load', item.data, {
    'Referer': cinemarUrl,
    'Origin': 'https://cinemar.cc'
  });

  if (!loadRes || !loadRes.file) {
    throw new Error('Failed to retrieve file from Cinemar API');
  }

  let masterHls = loadRes.file;
  // Normalize quality (e.g. "720p" -> "720")
  let qNum = quality.replace(/\D/g, '') || '720';
  const streamUrl = masterHls.replace(/\/hls\.m3u8$/, `/${qNum}.mp4:hls:manifest.m3u8`);

  return {
    title: fullTitle,
    stream_url: streamUrl,
    duration: loadRes.duration || 0,
    translations: []
  };
}

const inputUrl = process.argv[2];
const translationId = process.argv[3] || null;
const quality = process.argv[4] || '720p';
const mode = process.argv[5] || 'stream';
const episodeId = process.argv[6] || null;
const translationTitle = process.argv[7] || null;

if (!inputUrl) {
  console.error("Usage: node extract_kinogo.js <kinogo_url> [translation_id] [quality] [mode] [episode_id] [translation_title]");
  process.exit(1);
}

extract(inputUrl, translationId, quality, mode, episodeId, translationTitle)
  .then(res => console.log(JSON.stringify(res)))
  .catch(err => {
    console.error(err.message);
    process.exit(1);
  });
