# TV Caster — справка для Claude

Веб-сервер на Python (стандартная библиотека + внешние `yt-dlp`, `ffmpeg`, `node`, `adb`), который берёт ссылку на видео
(сайты, YouTube, прямые эфиры, файлы с ноутбука), достаёт из неё поток и показывает его на Android-ТВ приставке через
своё приложение-плеер (TV Caster Player, ExoPlayer). Управление: веб-интерфейс `http://<LOCAL_IP>:8080`, пульт ТВ, API.

Язык общения с пользователем — русский. Идентификаторы и код — как есть.

## Запуск, тесты, окружение

```bash
./start.sh                      # python3 server.py, порт 8080
python3 -m pytest -q tests      # все тесты (часть ходит в живую сеть)
unshare -rn python3 -m pytest -q tests/test_sources_offline.py tests/test_new_sources.py   # без сети, должны проходить
python3 tools/tv_deploy.py      # установить/запустить APK на ТВ по wifi-ADB
tools/build_apk.sh              # собрать static/tv-caster-player.apk
```

- При старте сервер печатает `WARNING:` из `check_environment()` (нет yt-dlp, /usr/bin/ffmpeg, adb, node, playwright/chrome).
- Внешние программы: `yt-dlp` (YouTube и общие ссылки), `/usr/bin/ffmpeg` (потоковая склейка), `~/.local/bin/ffmpeg`
  (встроенная статическая сборка 2018 года, `get_ffmpeg()`), `node` (kinogo/cinemar и `yt-dlp` JS), `adb`, `google-chrome` +
  Python-пакет `playwright` (только для LiveBall; см. `requirements.txt`).
- Сервер работает как systemd-юнит пользователя `tv-caster` (шаблон `deploy/tv-caster.service`, установка `tools/install_service.sh [--start]`;
  автозапуск при загрузке, `linger=yes`). Перезапуск: `systemctl --user restart tv-caster`, логи: `journalctl --user -u tv-caster -f`,
  состояние: `systemctl --user status tv-caster`. Юнит перезапускается сам после падения/`kill` (`Restart=always`).
  Ручной запуск (`./start.sh`) занимает порт 8080 — сначала `systemctl --user stop tv-caster`.
  **Не** убивать сервер через `kill`/`pkill -f "python3 server.py"`: pkill внутри команды Bash совпадает с самой командой и убивает оболочку (exit 144).

## Железо и сеть (на момент написания)

- Ноутбук (сервер): `LOCAL_IP` определяется автоматически (было 192.168.1.116), порт `SERVER_PORT = 8080`.
- **Приставка с плеером: 192.168.1.105**, `adb connect 192.168.1.105:5555`, пакет `com.tvcaster.player/.MainActivity`.
- `tv.json` — выбранное устройство (в git лежит старая запись Philips 192.168.1.110; локально выбрана приставка, файл не коммитим).
  В списке ТВ (`/api/tvs`, `list_tvs()`) кроме UPnP-рендереров есть «TV Caster Player» (`player_device()`: приставка без UPnP,
  находится по открытому порту ADB 5555). Выбранная приставка помечается `"player": true`, и `refresh_tv()` не подменяет её
  единственным найденным рендерером. `adb_launch_tv_player()` пробует по очереди: последний рабочий хост (`_adb_host`) →
  `TV["ip"]` → `TV_IP` (105).
- Скриншот ТВ для проверки: `adb -s 192.168.1.105:5555 exec-out screencap -p > файл.png` и открыть Read-ом.
- Если плеер не запущен, ТВ не подключится к серверу и кастить некуда: `adb ... shell am force-stop com.tvcaster.player`
  и новый каст перезапустит его. JS плеера кэшируется в WebView приставки: после правок `static/tv/tv_player.js`
  приложение нужно перезапустить (force-stop).

## Карта кода

```
server.py                 ~4650 строк: состояние, сессии, склейка потоков, HTTP-обработчик, ТВ-управление
extractors/               «чистые» извлекатели ссылок, только stdlib (+ друг на друга и на net/tools)
  net.py                  urlopen_fast, fetch_page, DNS-обход через DoH (socket.getaddrinfo патчится при импорте),
                          curl_fetch, curl_fetch_bytes, resolve_redirects, DL_HEADERS
  tools.py                VENV_DIR, YT_DLP_BIN, NODE_BIN, YT_DLP_BASE
  youtube.py              ytdlp_streams(url, quality) -> (title, [video_url, audio_url], duration); кэш 20 мин (_ytdlp_cache);
                          при ошибке вида «unable to extract/nsig/403» один раз в 6 ч делает `pip install -U yt-dlp` в окружении, где он стоит, и повторяет запрос
  nextembed.py            nextembed_data/structure/resolve, parse_master, media_segments, is_nextembed (nextembed/kinogram/gidonline/moy.su)
  kinogo.py               resolve_kinogo_url, resolve_kinozed_url, extract_kinogo (node + extract_kinogo.js), is_kinogo, is_blocked_site
  rezka.py                tulsa-king.net / rezka: структура сезонов и озвучек, extract_rezka
  seasonvar.py            seasonvar.ru: озвучки + MP4 по сериям (Playerjs '#2' base64)
  liveball.py             liveball.*: headless Chrome ловит play.m3u8 (токен привязан к браузерной сессии)
  sportacentrs.py, mover.py, vk.py, facebook.py, lsm.py   остальные источники
extract_kinogo.js         node-скрипт для kinogo/cinemar
static/tv/tv_player.{js,css}   страница плеера на ТВ (ходит на /api/tv/*)
templates/index.html, tv_player.html   веб-интерфейс и страница ТВ
android-tv/app/...        Java-код приложения (MainActivity, BridgeTask, AndroidBridge); APK в static/
tools/                    tv_deploy.py (установка APK), build_apk.sh, find_adb, get_default_tv_ip, get_local_ip
tests/                    см. раздел «Тесты»
history.json, favorites.json, settings.json, tv.json   состояние (в корне проекта)
media_cache/              временные файлы потоков (stream_<id>.ts), чистится cleanup_cache (возраст 45 мин + лимит 4 ГБ)
docs/ANDROID_TV_GUIDE.md  установка приложения и кнопки пульта
```

`server.py` импортирует всё нужное из `extractors/` и держит эти имена в своём пространстве (`server.ytdlp_streams`,
`server.nextembed_structure`, ...). Общие изменяемые объекты (`_ytdlp_cache`, `_lsm_sessions`, `_kinogo_url_cache`) —
один и тот же объект в обоих модулях: **никогда не переприсваивать, только мутировать**.

## Как идёт каст (handle_cast_request)

`POST /api/cast {url, quality, speed, start_at, audio_index, episode, translation_id, translation_title}` →
`handle_cast_request` создаёт сессию (`new_session`, id = время в мс), нормализует ссылку (`normalize_url`: IMDb/kinopoisk →
nextembed, kinogo → встроенный плеер), и дальше ветвится по типу источника. Три способа доставки на ТВ:

1. **Прямой поток** (`start_direct_stream`): сервер отдаёт ТВ прокси `/direct/stream` или `/direct/hls.m3u8` с нужными
   заголовками, ffmpeg не нужен. Для: Seasonvar, rezka, nextembed (дорожка 0), kinogo/cinemar, LiveBall, VK, Facebook, YouTube-HLS.
   Условие: `tv_player_state["use_custom_player"]` и `settings["direct_stream"]` (оба True по умолчанию).
   Перемотка и скорость — командой плееру (`tv_seek`, `_issue_tv_command`), сессия не перезапускается.
2. **Живая склейка** (`stream_dash_live`): ffmpeg пишет `.ts` в `media_cache/stream_<id>.ts`, ТВ стартует после ~2 МБ
   (`tv_cast_url` → `/stream.ts?v=<id>`). Для YouTube/DASH с раздельными видео+аудио, nextembed с не основной дорожкой
   (`stream_nextembed_live`), любая скорость (при ≠1.0x — перекодирование libx264 ultrafast + atempo).
3. **Полная загрузка** (`download_dash`, `download_nextembed`, `download_and_stream_hls`, `stream_mp4_live`, ...): старый путь,
   остаётся запасным (аргумент `fallback`) и для режима без прямого потока.

Файлы с ноутбука: `stream_local_video` (ffmpeg → `.ts`), поиск/обзор `resolve_local_path`, `browse_local_dir`.

### Перемотка и позиция

- Позиция, которую ТВ присылает в `/api/tv/report`, — **абсолютная** (в `tv_player.js`: `baseSeekOffset + позиция_плеера`).
  `info["stream_offset"]` — с какой секунды исходника начался текущий `.ts`.
- Прямой поток: seek = команда `seek` плееру. Живая/растущая склейка: файл не перематывается, поэтому
  `POST /api/tv/action {action:"seek"}` ставит статус «Перематываю…» и запускает `handle_cast_request(..., start_at=target, seamless=True)`
  — новый ffmpeg с `-ss`. Старая сессия отменяется в `tv_cast_url`.
- Авто-продолжение: `neighbor_episode`, `series_episodes` (идентификаторы серий вида `s01e05`), кнопка `/api/next`.

### Устойчивость `stream_dash_live`

- Нет `/usr/bin/ffmpeg` → читает через локальные range-прокси `_start_range_proxy` (встроенный ffmpeg падает по SIGSEGV на https).
- ffmpeg умер до старта на ТВ → вызывается `fallback()` (старая полная загрузка).
- ffmpeg умер после старта → `on_drop()` перезапускает каст с позиции ТВ (`recast_from_here` в `handle_cast_request`,
  чистит `_ytdlp_cache`), не более 3 раз за 10 минут (`_drop_times`).
- Процесс ffmpeg получает `PR_SET_PDEATHSIG` (`_die_with_parent`) — умирает вместе с сервером.
- Каждое изменение статуса, которое должен увидеть ТВ, нужно сопровождать `_notify_tv_listeners()`
  (иначе плашка загрузки на ТВ не появится: ТВ получает состояние по SSE `/api/tv/events`).

## Источники ссылок

| Ссылка | Модуль / функция | Доставка |
|---|---|---|
| youtu.be, youtube.com | `youtube.ytdlp_streams` | живая склейка (2 URL) или HLS напрямую |
| seasonvar.ru | `seasonvar.seasonvar_structure` | прямой MP4; озвучки = отдельные плейлисты `pl[ID]`, «Трейлеры» отбрасываются |
| tulsa-king.net, rezka | `rezka.rezka_structure`, `extract_rezka` | прямой MP4 |
| kinogo*, gidonline, moy.su, nextembed, kinogram, IMDb, kinopoisk | `normalize_url` → `nextembed.*` | прямой HLS (дорожка 0) или `stream_nextembed_live` |
| cinemar.cc | `kinogo.extract_kinogo` (node) | прямой HLS, Referer cinemar.cc |
| liveball.* | `liveball.extract_liveball` | прямой HLS (токен ~4 ч), нужен headless Chrome. Главная/список (`liveball.sx/`) открывается как «сериал»: матчи = «серии» с id `m<ID>` (`liveball_matches`, идущие 🔴 первыми; «идёт» = начался <3 ч назад, т.к. в HTML статус только `up`/`fin`) |
| replay.lsm.lv, ltv.lsm.lv | `lsm.extract_replay_lsm` | собственный master-плейлист `/lsm/master.m3u8` |
| sportacentrs.com, tiesraides.lv | `sportacentrs.*` | HLS / DASH |
| mover.uz | `mover.extract_mover` | прямой MP4 |
| vkvideo.ru, vk.com | `vk.extract_vk` | прямой HLS |
| facebook.com, fb.watch | `facebook.extract_facebook` | прямой поток / DASH |
| magnet: | TorrServer на `TV_IP:8090` | — |
| любая другая | `yt-dlp` в ветке `else` | HLS/MP4/DASH |
| файл на ноутбуке | `resolve_local_path` | ffmpeg → `.ts` |

Сайты за Cloudflare (`kinogo` с проверкой «Just a moment», `kinogohd`) отдают 403 любому запросу без браузера:
экстрактор сообщает «page is behind a bot check», `is_blocked_site` помечает такие. **Не обходить защиту подменой заголовков
или headless-браузером** — это сознательно не поддерживается (автоматическая проверка прав это блокирует).

## HTTP API (TVCasterHandler)

GET: `/` `/tv` (HTML), `/api/status`, `/api/history`, `/api/favorites`, `/api/tvs`, `/api/search`, `/api/settings`,
`/api/tv/state`, `/api/tv/events` (SSE для ТВ), `/api/tv/history`, `/api/local/browse|find`, `/stream.ts`, `/stream.mp4`,
`/direct/stream|hls.m3u8|dash.mpd`, `/lsm/master.m3u8`, `/api/thumb/*`.
POST: `/api/inspect` (структура: сезоны, серии, озвучки), `/api/cast`, `/api/next`, `/api/prev`, `/api/audio_track`,
`/api/tv/action` (play, pause, seek, speed, stop, volume, next_episode, switch_episode, cast_from_tv, ...), `/api/tv/report`
(ТВ → сервер: состояние, позиция, длительность), `/api/history/delete|clear`, `/api/favorites/toggle`, `/api/tv`, `/api/settings`, `/api/control`.

Состояние: `app_state` (что показывает веб-интерфейс: status, status_text, stage, title, opts, ...), `tv_player_state`
(что сообщает плеер: state, position, duration), `active_sessions`, `_direct_sessions`.
Статусы `app_state["status"]`: idle, preparing, downloading, remuxing, playing, error.

## Тесты

- `tests/test_new_sources.py`: Seasonvar (на настоящей разметке), LiveBall (подменённый playwright), живая склейка
  (откат/перезапуск/лимит), кэш, `check_environment`.
- `tests/test_sources_offline.py` + `tests/fixtures/pages.json`: rezka, kinogo, gidonline, kinogo-hd, nextembed на сохранённых страницах.
  Большие страницы обрезаны до того, что читает код. Обновление: заново скачать страницу и заменить запись.
- `tests/netfix.py`: запись/проигрывание сети для тестов. `@netfix.offline` (или `with netfix.replay()`) подменяет
  `urllib.request.urlopen`, `fetch_page`, `curl_fetch` и вызовы `yt-dlp`/`node` ответами из `tests/fixtures/net.json` + `pages.json`;
  незаписанный запрос = `AssertionError`. Обновить записи: `python3 tests/netfix.py` (ходит на живые сайты, сценарии в `SCENARIOS`).
  Записи **очищаются** (`scrub`): JWT → `JWT`, любой IPv4 → `203.0.113.1` (в токенах LSM зашит внешний IP клиента) — не коммитить сырые ответы.
- Весь набор без интернета: `unshare -rn sh -c 'ip link set lo up && python3 -m pytest -q tests --deselect tests/test_kinozed.py'`
  (loopback нужен тестам с локальным сервером; `tests/test_kinozed.py` — личный файл пользователя, ходит на живой сайт,
  офлайн-версия — `tests/test_kinozed_offline.py`).
- **Подменять имя там, где его ищет код**: после выноса в `extractors/` функция использует свой `fetch_page`/`curl_fetch`,
  поэтому патчить нужно `extractors.nextembed.fetch_page`, `extractors.rezka.fetch_page`, `extractors.kinogo.curl_fetch` и т.д.,
  а не `server.fetch_page`. Проверять запуском без сети (команда выше): иначе тест «проходит» через интернет.
- Живая проверка на ТВ: `curl -X POST localhost:8080/api/cast -d '{"url":...}'`, затем `/api/status` и скриншот ТВ.

## Подводные камни (выучено на практике)

- Встроенный `~/.local/bin/ffmpeg` падает (SIGSEGV, код -11) на любом https-входе; `/usr/bin/ffmpeg` работает и втрое быстрее.
- Страница nextembed сменила `seasons:[` на `seasons: [` — парсер должен терпеть пробелы (регресс-тест есть).
- Seasonvar: озвучки — это и словарь `pl = {'0': ...}`, и отдельные `pl[144] = "..."`; названия берутся только из `<li data-translate>`.
- LiveBall: User-Agent headless Chrome по умолчанию получает 403 — нужен обычный UA. Трансляция есть только у идущих матчей;
  токен HLS ~4 ч, плейлист из одного варианта. Картинки и шрифты блокируются ради скорости, CSS нет (страница проверяет видимость плеера).
- RuDub-озвучки Seasonvar иногда содержат рекламную заставку в самом файле — это файл, а не баг.
- `ConnectionResetError` в логе при смене потока — сброс соединения ТВ, воспроизведение не ломает.
- `ps`/`pgrep` по ffmpeg: сироты остаются только от серверов, запущенных до `_die_with_parent`.

## Состояние репозитория

- Основная ветка `main`, remote `origin` = GitHub `VigulsEdvins/tv-caster`. Коммиты и `git push` — только по просьбе пользователя.
- Не принадлежат работе над кодом и не должны попадать в коммиты без просьбы: `templates/index.html`, `templates/tv_player.html`
  (правки пользователя), `favorites.json`, `restore_tv.sh`, `stream_dual_screen.py`, `stream_screens.sh`, `graphify-out/`,
  `android-tv/.../banner.png`, `static/tv-caster-player.apk.idsig`, `tests/test_kinozed.py`, `tests/test_local_video.py`, `.playwright-mcp/`.

## Не проверено / известные пробелы

- Kinogo-сериалы на `go3.kinogo.cc` (Cloudflare) — нет ни теста, ни рабочего примера.
- Показ плашки «Перематываю…» на самом ТВ (в браузере на `/tv` работает; на ТВ быстрые снимки её не поймали).
- Озвучка по слуху: проверяется по файлу и названию, а не на слух.
- Автообновление токена LiveBall не делалось (не нужно: токен 4 ч, матч короче).
- Идеи на потом: список идущих матчей LiveBall, прогрев следующей серии, дальнейший вынос из `server.py`
  (оставшееся — сессии, склейка, HTTP-обработчик, локальные файлы, `inspect_url`, `normalize_url`).
