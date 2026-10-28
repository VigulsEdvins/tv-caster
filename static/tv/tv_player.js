(function () {
  "use strict";

  const hasNativeBridge = typeof window.AndroidBridge !== "undefined";
  if (hasNativeBridge) {
    document.body.classList.remove("browser-mode");
    document.body.classList.add("native-bridge");
  }

  // DOM Elements
  const videoEl = document.getElementById("tv-video");
  const standbyScreen = document.getElementById("standby-screen");
  const standbyTitle = document.getElementById("standby-title");
  const standbySubtitle = document.getElementById("standby-subtitle");
  const loadingBox = document.getElementById("loading-box");
  const loadingStatusText = document.getElementById("loading-status-text");
  const loadingSpeedText = document.getElementById("loading-speed-text");
  const loadingBarFill = document.getElementById("loading-bar-fill");

  const overlayEl = document.getElementById("tv-overlay");
  const overlayTitle = document.getElementById("overlay-title");
  const overlayBufferInfo = document.getElementById("overlay-buffer-info");
  const badgeEpisode = document.getElementById("badge-episode");
  const badgeVoice = document.getElementById("badge-voice");
  const badgeQuality = document.getElementById("badge-quality");
  const badgeSpeed = document.getElementById("badge-speed");
  const labelDock = document.getElementById("label-dock");

  const timeCurrentEl = document.getElementById("time-current");
  const timeDurationEl = document.getElementById("time-duration");
  const timelineTrack = document.getElementById("timeline-track");
  const timelineBuffer = document.getElementById("timeline-buffer");
  const timelineProgress = document.getElementById("timeline-progress");
  const timelineThumb = document.getElementById("timeline-thumb");

  const playIcon = document.getElementById("play-icon");
  const playLabel = document.getElementById("play-label");
  const subEpisode = document.getElementById("sub-episode");
  const subVoice = document.getElementById("sub-voice");
  const subQuality = document.getElementById("sub-quality");
  const subSpeed = document.getElementById("sub-speed");

  const modalBackdrop = document.getElementById("tv-modal-backdrop");
  const modalTitle = document.getElementById("modal-title");
  const modalSeasonTabs = document.getElementById("modal-season-tabs");
  const modalOptionsGrid = document.getElementById("modal-options-grid");
  const actionToast = document.getElementById("action-toast");

  const heroBackdrop = document.getElementById("hero-backdrop");
  const heroBadge = document.getElementById("hero-badge");
  const heroTitle = document.getElementById("hero-title");
  const heroMeta = document.getElementById("hero-meta");
  const heroProgressWrap = document.getElementById("hero-progress-wrap");
  const heroProgressFill = document.getElementById("hero-progress-fill");
  const heroProgressLabel = document.getElementById("hero-progress-label");
  const homeCloseHint = document.getElementById("home-close-hint");
  const historyCarousel = document.getElementById("tv-history-carousel");

  // Player State
  let serverState = {
    status: "idle",
    title: "No media playing",
    stream_url: "",
    mp4_url: "",
    ts_url: "",
    session_id: 0,
    episode: null,
    seasons: [],
    translations: [],
    qualities: ["1080p", "720p", "480p", "360p"],
    speeds: ["0.75x", "1.0x", "1.25x", "1.5x", "2.0x"],
    audio_tracks: [],
    audio_index: 0,
    opts: { quality: "720p", speed: "1.0x" },
  };

  let currentSessionId = 0;
  let lastCommandSeq = 0;
  let baseSeekOffset = 0;
  let pendingSeekTarget = null;
  let seekCommitTimer = null;
  let knownDuration = 0;
  let isPaused = false;
  let currentSpeed = 1.0;
  let directProxyFallbackUsed = false;

  // UI Navigation State
  let dockPosition = localStorage.getItem("tv_dock_position") || "bottom";
  let overlayVisible = true;
  let hideOverlayTimer = null;
  let toastTimer = null;
  let focusedEl = document.getElementById("btn-play");

  let modalOpen = false;
  let modalReturnFocus = null;
  let activeSeasonIndex = 0;
  let modalZone = "grid"; // "tabs" or "grid"
  let modalTabIndex = 0;
  let modalGridIndex = 0;
  let cachedModalTabs = [];
  let cachedModalBtns = [];
  let cachedModalCols = 3;

  let historyItems = [];
  let historyCardEls = [];
  let historyIndex = 0;
  let lastHistorySignature = "";
  let homeBrowserOpen = true;
  let historyInspectCache = {};
  let activeHistorySeasonIdx = 0;

  const row0Els = [document.getElementById("btn-dock")];
  const row1Els = [document.getElementById("timeline-scrubber")];
  const row2Els = Array.from(document.querySelectorAll('#tv-overlay .focusable[data-row="2"]'));

  function applyDockPosition() {
    if (dockPosition === "top") {
      overlayEl.classList.remove("dock-bottom");
      overlayEl.classList.add("dock-top");
      labelDock.textContent = "Сверху";
    } else {
      overlayEl.classList.remove("dock-top");
      overlayEl.classList.add("dock-bottom");
      labelDock.textContent = "Снизу";
    }
  }
  applyDockPosition();

  function formatTime(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    return [h, m, s].map((v) => String(v).padStart(2, "0")).join(":");
  }

  function parseSpeed(spdStr) {
    if (!spdStr) return 1.0;
    const val = parseFloat(String(spdStr).replace("x", "").trim());
    return isNaN(val) || val <= 0 ? 1.0 : val;
  }

  function showToast(text) {
    actionToast.textContent = text;
    actionToast.classList.add("visible");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      actionToast.classList.remove("visible");
    }, 1400);
  }

  function showOverlay() {
    overlayVisible = true;
    overlayEl.classList.remove("hidden");
    resetOverlayTimer();
  }

  function hideOverlay() {
    if (modalOpen || isPaused || !currentSessionId) return;
    overlayVisible = false;
    overlayEl.classList.add("hidden");
  }

  function resetOverlayTimer() {
    if (hideOverlayTimer) clearTimeout(hideOverlayTimer);
    if (!modalOpen && !isPaused && currentSessionId) {
      hideOverlayTimer = setTimeout(hideOverlay, 4500);
    }
  }

  function setFocus(el) {
    if (!el) return;
    if (focusedEl && focusedEl !== el) {
      focusedEl.classList.remove("focused");
    }
    focusedEl = el;
    el.classList.add("focused");
    if (modalOpen && cachedModalBtns.length > 9) {
      try {
        el.scrollIntoView({ block: "nearest" });
      } catch (_) {}
    }
  }

  // Player Adapter (works with Native AndroidBridge OR HTML5 Video)
  function playerLoad(stateObj, startAtSec, streamOffsetSec, forceReload, forceServerProxy) {
    const sid = stateObj.session_id;
    if (!sid) return;
    if (currentSessionId !== sid) {
      directProxyFallbackUsed = false;
    }
    currentSessionId = sid;
    const isDirect = Boolean(stateObj.direct_stream);
    const startSec = Math.max(0, Number(startAtSec || 0));
    const offsetSec = isDirect ? 0 : Math.max(0, Number(streamOffsetSec !== undefined ? streamOffsetSec : (stateObj.stream_offset || 0)));
    baseSeekOffset = offsetSec;
    pendingSeekTarget = null;
    if (seekCommitTimer) {
      clearTimeout(seekCommitTimer);
      seekCommitTimer = null;
    }
    const relSeek = Math.max(0, Math.floor(startSec - offsetSec));
    currentSpeed = parseSpeed((stateObj.opts && stateObj.opts.speed) || "1.0x");
    isPaused = false;

    if (hasNativeBridge) {
      if (isDirect) {
        const useProxy = Boolean(forceServerProxy || directProxyFallbackUsed || !stateObj.cdn_url);
        const rawTarget = useProxy
          ? (stateObj.direct_url || `/direct/stream?v=${sid}`)
          : stateObj.cdn_url;
        let fullTargetUrl = (rawTarget.startsWith("http://") || rawTarget.startsWith("https://"))
          ? rawTarget
          : window.location.origin + rawTarget;
        if (forceReload && useProxy) {
          fullTargetUrl += (fullTargetUrl.includes("?") ? "&" : "?") + `r=${Date.now()}`;
        }
        const headersJson = JSON.stringify(useProxy ? {} : (stateObj.direct_headers || {}));
        if (typeof window.AndroidBridge.loadStreamWithHeaders === "function") {
          window.AndroidBridge.loadStreamWithHeaders(fullTargetUrl, headersJson, relSeek, currentSpeed);
        } else {
          window.AndroidBridge.loadStream(fullTargetUrl, relSeek, currentSpeed);
        }
      } else {
        const rawTs = stateObj.ts_url || `/stream.ts?v=${sid}`;
        let fullTsUrl = (rawTs.startsWith("http://") || rawTs.startsWith("https://")) ? rawTs : window.location.origin + rawTs;
        if (forceReload) {
          fullTsUrl += (fullTsUrl.includes("?") ? "&" : "?") + `r=${Date.now()}`;
        }
        window.AndroidBridge.loadStream(fullTsUrl, relSeek, 1.0);
      }
    } else {
      if (isDirect) {
        const directPath = stateObj.direct_url || `/direct/stream?v=${sid}`;
        videoEl.src = directPath;
        videoEl.currentTime = relSeek;
        videoEl.playbackRate = currentSpeed;
        videoEl.play().catch(() => {});
      } else {
        const mp4Url = `/stream.mp4?v=${sid}` + (relSeek > 0 ? `&t=${relSeek}` : "");
        videoEl.src = mp4Url;
        videoEl.playbackRate = 1.0;
        videoEl.play().catch(() => {});
      }
    }
    standbyScreen.classList.add("hidden");
    homeBrowserOpen = false;
    if (homeCloseHint) homeCloseHint.style.display = "none";
    updatePlayButtonUI(false);
    showOverlay();
    if (!modalOpen) {
      setFocus(document.getElementById("btn-play"));
    }
  }

  window.onNativePlayerError = function () {
    if (serverState && serverState.direct_stream && !directProxyFallbackUsed) {
      directProxyFallbackUsed = true;
      const curPos = playerGetPosition();
      playerLoad(serverState, curPos, 0, true, true);
    }
  };

  function playerPlay() {
    isPaused = false;
    if (hasNativeBridge) {
      window.AndroidBridge.play();
    } else {
      videoEl.play().catch(() => {});
    }
    updatePlayButtonUI(false);
    showToast("▶ Воспроизведение");
    resetOverlayTimer();
  }

  function playerPause() {
    isPaused = true;
    if (hasNativeBridge) {
      window.AndroidBridge.pause();
    } else {
      videoEl.pause();
    }
    updatePlayButtonUI(true);
    showToast("⏸ Пауза");
    showOverlay();
  }

  function playerTogglePlay() {
    if (isPaused) {
      playerPlay();
      postAction({ action: "play" });
    } else {
      playerPause();
      postAction({ action: "pause" });
    }
  }

  function playerGetPosition() {
    if (!currentSessionId) return 0;
    if (pendingSeekTarget !== null) return pendingSeekTarget;
    const isDirect = Boolean(serverState && serverState.direct_stream);
    const spdFactor = isDirect ? 1.0 : currentSpeed;
    if (hasNativeBridge) {
      const raw = Number(window.AndroidBridge.getPosition() || 0);
      return baseSeekOffset + raw * spdFactor;
    }
    return baseSeekOffset + Number(videoEl.currentTime || 0) * spdFactor;
  }

  function playerGetDuration() {
    if (!currentSessionId) return 0;
    const isDirect = Boolean(serverState && serverState.direct_stream);
    const spdFactor = isDirect ? 1.0 : currentSpeed;
    const srvDur = Math.max(knownDuration, Number(serverState.duration || 0));
    if (srvDur > 0 && srvDur < 43200) return srvDur;
    if (hasNativeBridge) {
      const d = Number(window.AndroidBridge.getDuration() || 0);
      if (d > 0 && d < 43200) return baseSeekOffset + d * spdFactor;
    } else {
      const d = Number(videoEl.duration || 0);
      if (isFinite(d) && d > 0 && d < 43200) return baseSeekOffset + d * spdFactor;
    }
    return srvDur;
  }

  function playerSeekTo(targetSec, fromRemote) {
    if (!currentSessionId) return;
    const dur = playerGetDuration();
    targetSec = Math.max(0, dur > 0 ? Math.min(targetSec, dur - 2) : targetSec);
    const isDirect = Boolean(serverState && serverState.direct_stream);

    if (hasNativeBridge) {
      if (isDirect) {
        window.AndroidBridge.seekTo(Math.max(0, Math.floor(targetSec)));
        pendingSeekTarget = null;
        if (fromRemote) {
          postAction({ action: "seek", target: Math.floor(targetSec), from_tv: true });
        }
      } else {
        const nativeDur = Number(window.AndroidBridge.getDuration() || 0);
        const relTarget = Math.floor((targetSec - baseSeekOffset) / (currentSpeed || 1.0));
        if (nativeDur > 0 && currentSpeed === 1.0 && targetSec >= baseSeekOffset && !serverState.is_active_download) {
          window.AndroidBridge.seekTo(Math.max(0, relTarget));
          pendingSeekTarget = null;
        } else if (fromRemote) {
          postAction({ action: "seek", target: Math.floor(targetSec), from_tv: true });
        }
      }
    } else {
      if (isDirect) {
        videoEl.currentTime = Math.max(0, targetSec);
        pendingSeekTarget = null;
        if (fromRemote) {
          postAction({ action: "seek", target: Math.floor(targetSec), from_tv: true });
        }
      } else {
        const relTarget = (targetSec - baseSeekOffset) / (currentSpeed || 1.0);
        let canSeekInBuffer = false;
        if (relTarget >= 0 && videoEl.buffered && videoEl.buffered.length > 0) {
          for (let i = 0; i < videoEl.buffered.length; i++) {
            if (relTarget >= videoEl.buffered.start(i) && relTarget <= videoEl.buffered.end(i)) {
              canSeekInBuffer = true;
              break;
            }
          }
        }
        if (canSeekInBuffer) {
          videoEl.currentTime = relTarget;
          pendingSeekTarget = null;
        } else if (fromRemote) {
          postAction({ action: "seek", target: Math.floor(targetSec), from_tv: true });
        }
      }
    }
    updateTimelineUI(targetSec, dur);
  }

  function playerSeekRelative(deltaSec) {
    if (!currentSessionId) return;
    const dur = playerGetDuration();
    const basePos = pendingSeekTarget !== null ? pendingSeekTarget : playerGetPosition();
    const nextPos = Math.max(0, dur > 0 ? Math.min(basePos + deltaSec, dur - 2) : basePos + deltaSec);
    pendingSeekTarget = nextPos;
    updateTimelineUI(nextPos, dur);
    showToast((deltaSec > 0 ? "⏩ +" : "⏪ ") + deltaSec + " сек (" + formatTime(nextPos) + ")");
    resetOverlayTimer();

    if (seekCommitTimer) clearTimeout(seekCommitTimer);
    seekCommitTimer = setTimeout(() => {
      seekCommitTimer = null;
      const target = pendingSeekTarget !== null ? pendingSeekTarget : nextPos;
      playerSeekTo(target, true);
    }, 450);
  }

  function playerSetSpeed(spdStr) {
    currentSpeed = parseSpeed(spdStr);
    if (hasNativeBridge) {
      window.AndroidBridge.setSpeed(currentSpeed);
    } else {
      videoEl.playbackRate = currentSpeed;
    }
    badgeSpeed.textContent = spdStr;
    subSpeed.textContent = spdStr;
    showToast("⚡ Скорость " + spdStr);
  }

  function updatePlayButtonUI(paused) {
    playIcon.textContent = paused ? "▶" : "⏸";
    playLabel.textContent = paused ? "Играть" : "Пауза";
  }

  function updateTimelineUI(pos, dur) {
    timeCurrentEl.textContent = formatTime(pos);
    timeDurationEl.textContent = dur > 0 ? formatTime(dur) : "--:--:--";
    const pct = dur > 0 ? Math.min(100, Math.max(0, (pos / dur) * 100)) : 0;
    timelineProgress.style.width = pct + "%";
    timelineThumb.style.left = pct + "%";
    timelineBuffer.style.width = (serverState.download_progress || 100) + "%";
  }

  // API Helpers
  function postAction(payload) {
    return fetch("/api/tv/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then((r) => r.json()).catch(() => ({}));
  }

  // Sync UI with Server State
  function applyServerState(data) {
    if (!data) return;
    serverState = data;
    if (data.duration && data.duration > 0) {
      knownDuration = data.duration;
    }

    // Update Titles & Badges
    overlayTitle.textContent = data.title || "Нет активного видео";
    const opts = data.opts || {};
    const q = opts.quality || "720p";
    const spd = opts.speed || "1.0x";
    badgeQuality.textContent = q;
    subQuality.textContent = q;
    badgeSpeed.textContent = spd;
    subSpeed.textContent = spd;

    if (data.episode) {
      badgeEpisode.style.display = "inline-flex";
      badgeEpisode.textContent = data.episode.toUpperCase();
      subEpisode.textContent = data.episode.toUpperCase();
    } else if (data.seasons && data.seasons.length > 0) {
      badgeEpisode.style.display = "none";
      subEpisode.textContent = "Сезоны (" + data.seasons.length + ")";
    } else {
      badgeEpisode.style.display = "none";
      subEpisode.textContent = "Фильм";
    }

    const voiceTitle = opts.translation_title ||
      (data.translations && data.translations.find((t) => String(t.id) === String(opts.translation_id)) || {}).title;
    if (voiceTitle) {
      badgeVoice.style.display = "inline-flex";
      badgeVoice.textContent = voiceTitle;
      const shortVoice = voiceTitle.replace(/\s*\(.*?\)\s*/g, "").trim();
      subVoice.textContent = (shortVoice || voiceTitle).slice(0, 12);
    } else if (data.audio_tracks && data.audio_tracks.length > 0) {
      const tr = data.audio_tracks[data.audio_index || 0] || data.audio_tracks[0];
      const label = tr.lang ? tr.lang.toUpperCase() : "Дорожка " + (tr.n + 1);
      badgeVoice.style.display = "inline-flex";
      badgeVoice.textContent = label;
      subVoice.textContent = label;
    } else {
      badgeVoice.style.display = "none";
      subVoice.textContent = data.translations && data.translations.length ? "Выбрать" : "По умолч.";
    }

    if (data.download_progress >= 100 || data.status === "playing") {
      overlayBufferInfo.textContent = "Загружено 100%";
    } else if (data.is_active_download) {
      overlayBufferInfo.textContent = `Буферизация: ${data.download_progress || 0}% (${data.download_speed || "0 MB/s"})`;
    } else {
      overlayBufferInfo.textContent = data.status_text || "";
    }

    // Update Netflix History Carousel if history payload is present
    if (Array.isArray(data.history)) {
      renderHistoryCarousel(data.history);
    }

    // Standby / Loading states (only show when no stream is currently playing on screen)
    if (!currentSessionId && (data.status === "preparing" || (data.status === "downloading" && !data.stream_url))) {
      standbyScreen.classList.remove("hidden");
      homeBrowserOpen = true;
      standbyTitle.textContent = data.title || "Подготовка видео…";
      standbySubtitle.textContent = data.status_text || "Пожалуйста, подождите…";
      loadingBox.classList.add("active");
      loadingStatusText.textContent = data.status_text || "Загрузка…";
      loadingSpeedText.textContent = data.download_speed || "";
      loadingBarFill.style.width = (data.download_progress || 10) + "%";
    } else if (data.status === "error") {
      standbyScreen.classList.remove("hidden");
      homeBrowserOpen = true;
      standbyTitle.textContent = data.error || "Ошибка воспроизведения";
      standbySubtitle.textContent = data.error_hint || "Попробуйте выбрать другое качество или озвучку.";
      loadingBox.classList.remove("active");
    } else if (data.status === "playing" || data.status === "paused") {
      loadingBox.classList.remove("active");
    }

    // Handle Commands from Server
    const cmd = data.last_command;
    if (cmd && cmd.seq && cmd.seq !== lastCommandSeq) {
      lastCommandSeq = cmd.seq;
      if (cmd.type === "load" && cmd.target && cmd.target.session_id) {
        playerLoad(
          data,
          cmd.target.start_at || 0,
          cmd.target.stream_offset !== undefined ? cmd.target.stream_offset : (data.stream_offset || 0),
          Boolean(cmd.target.reload)
        );
      } else if (cmd.type === "play") {
        playerPlay();
      } else if (cmd.type === "pause") {
        playerPause();
      } else if (cmd.type === "seek") {
        playerSeekTo(Number(cmd.target || 0), false);
      } else if (cmd.type === "speed") {
        playerSetSpeed(cmd.target || "1.0x");
      } else if (cmd.type === "stop") {
        currentSessionId = 0;
        if (hasNativeBridge) {
          window.AndroidBridge.stop();
        } else {
          videoEl.pause();
          videoEl.removeAttribute("src");
        }
        standbyScreen.classList.remove("hidden");
        homeBrowserOpen = true;
        if (homeCloseHint) homeCloseHint.style.display = "none";
        loadingBox.classList.remove("active");
        standbyTitle.textContent = "TV Caster";
        standbySubtitle.textContent = "Воспроизведение остановлено. Выберите сериал или фильм из истории.";
        if (historyCardEls.length) {
          focusHistoryCard(historyIndex);
        }
      }
    } else if (data.session_id && data.stream_url && currentSessionId !== data.session_id) {
      // Auto-attach if TV opened mid-session
      playerLoad(data, data.position || 0, data.stream_offset || 0, false);
    }
  }

  // Modal Builders (Seasons/Episodes, Voice Acting, Quality, Speed)
  function openModal(type, triggerBtn) {
    modalReturnFocus = triggerBtn || focusedEl;
    modalOpen = true;
    modalBackdrop.classList.add("open");
    modalSeasonTabs.style.display = "none";
    modalSeasonTabs.innerHTML = "";
    modalOptionsGrid.innerHTML = "";

    if (type === "episodes") {
      modalTitle.textContent = "🎬 Выбор сезона и серии";
      const seasons = serverState.seasons || [];
      if (!seasons.length) {
        renderEmptyModal("Для этого видео список серий недоступен.");
        return;
      }
      // Find season containing current episode
      if (serverState.episode) {
        const foundIdx = seasons.findIndex((s) =>
          (s.episodes || []).some((e) => String(e.id) === String(serverState.episode))
        );
        if (foundIdx >= 0) activeSeasonIndex = foundIdx;
      }
      if (activeSeasonIndex >= seasons.length) activeSeasonIndex = 0;

      if (seasons.length > 1) {
        modalSeasonTabs.style.display = "flex";
        seasons.forEach((s, idx) => {
          const tab = document.createElement("button");
          tab.className = "tv-btn modal-tab" + (idx === activeSeasonIndex ? " active" : "");
          tab.textContent = s.title || `Сезон ${s.season || idx + 1}`;
          tab.onclick = () => {
            activeSeasonIndex = idx;
            openModal("episodes", modalReturnFocus);
          };
          modalSeasonTabs.appendChild(tab);
        });
      }

      const episodes = (seasons[activeSeasonIndex] && seasons[activeSeasonIndex].episodes) || [];
      episodes.forEach((ep) => {
        const btn = document.createElement("button");
        const isCurrent = String(ep.id) === String(serverState.episode);
        btn.className = "modal-option-btn" + (isCurrent ? " active" : "");
        btn.innerHTML = `<span>${ep.title || ep.id}</span><span>${isCurrent ? "✓ Играет" : "▶"}</span>`;
        btn.onclick = () => {
          closeModal();
          showToast(`🎬 Включаю ${ep.title || ep.id}…`);
          postAction({ action: "switch_episode", episode: ep.id });
        };
        modalOptionsGrid.appendChild(btn);
      });
    } else if (type === "voices") {
      modalTitle.textContent = "🎙 Выбор озвучки / аудиодорожки";
      const translations = serverState.translations || [];
      const audioTracks = serverState.audio_tracks || [];
      const curTrId = (serverState.opts || {}).translation_id;
      const curTrTitle = (serverState.opts || {}).translation_title;

      if (!translations.length && !audioTracks.length) {
        renderEmptyModal("Дополнительные озвучки для этого видео не найдены.");
        return;
      }

      translations.forEach((tr) => {
        const isCurrent =
          (curTrId && String(tr.id) === String(curTrId)) ||
          (curTrTitle && tr.title === curTrTitle);
        const btn = document.createElement("button");
        btn.className = "modal-option-btn" + (isCurrent ? " active" : "");
        btn.innerHTML = `<span>${tr.title || "Озвучка " + tr.id}</span><span>${isCurrent ? "✓" : ""}</span>`;
        btn.onclick = () => {
          closeModal();
          showToast(`🎙 Озвучка: ${tr.title}`);
          postAction({
            action: "switch_translation",
            translation_id: tr.id,
            translation_title: tr.title,
          });
        };
        modalOptionsGrid.appendChild(btn);
      });

      audioTracks.forEach((tr, idx) => {
        const isCurrent = idx === (serverState.audio_index || 0);
        const btn = document.createElement("button");
        btn.className = "modal-option-btn" + (isCurrent ? " active" : "");
        const label = `Дорожка #${idx + 1} (${tr.lang || tr.codec || "AAC"})`;
        btn.innerHTML = `<span>${label}</span><span>${isCurrent ? "✓" : ""}</span>`;
        btn.onclick = () => {
          closeModal();
          showToast(`🎙 ${label}`);
          postAction({ action: "switch_audio_track", index: idx });
        };
        modalOptionsGrid.appendChild(btn);
      });
    } else if (type === "quality") {
      modalTitle.textContent = "📺 Качество видео";
      const qualities = serverState.qualities && serverState.qualities.length
        ? serverState.qualities
        : ["1080p", "720p", "480p", "360p"];
      const curQ = (serverState.opts || {}).quality || "720p";

      qualities.forEach((q) => {
        const isCurrent = q === curQ;
        const btn = document.createElement("button");
        btn.className = "modal-option-btn" + (isCurrent ? " active" : "");
        btn.innerHTML = `<span>${q} ${q === "1080p" ? "Full HD" : q === "720p" ? "HD" : ""}</span><span>${isCurrent ? "✓" : ""}</span>`;
        btn.onclick = () => {
          closeModal();
          if (q === curQ) return;
          if (!serverState.opts) serverState.opts = {};
          serverState.opts.quality = q;
          badgeQuality.textContent = q;
          subQuality.textContent = q;
          showToast(`📺 Качество: ${q}…`);
          postAction({ action: "switch_quality", quality: q });
        };
        modalOptionsGrid.appendChild(btn);
      });
    } else if (type === "speed") {
      modalTitle.textContent = "⚡ Скорость воспроизведения";
      const speeds = serverState.speeds && serverState.speeds.length
        ? serverState.speeds
        : ["0.75x", "1.0x", "1.25x", "1.5x", "2.0x"];
      const curSpd = (serverState.opts || {}).speed || "1.0x";

      speeds.forEach((s) => {
        const isCurrent = s === curSpd;
        const btn = document.createElement("button");
        btn.className = "modal-option-btn" + (isCurrent ? " active" : "");
        btn.innerHTML = `<span>${s} ${s === "1.0x" ? "(Обычная)" : ""}</span><span>${isCurrent ? "✓" : ""}</span>`;
        btn.onclick = () => {
          closeModal();
          if (s === curSpd) return;
          if (!serverState.opts) serverState.opts = {};
          serverState.opts.speed = s;
          badgeSpeed.textContent = s;
          subSpeed.textContent = s;
          playerSetSpeed(s);
          postAction({ action: "set_speed", speed: s });
        };
        modalOptionsGrid.appendChild(btn);
      });
    }

    // Focus active option or first option inside modal
    cachedModalTabs = Array.from(modalSeasonTabs.querySelectorAll(".modal-tab"));
    cachedModalBtns = Array.from(modalOptionsGrid.querySelectorAll(".modal-option-btn"));
    if (cachedModalBtns.length) {
      modalZone = "grid";
      const activeIdx = cachedModalBtns.findIndex((b) => b.classList.contains("active"));
      modalGridIndex = activeIdx >= 0 ? activeIdx : 0;
      setFocus(cachedModalBtns[modalGridIndex]);
      requestAnimationFrame(() => {
        if (cachedModalBtns[0]) {
          cachedModalCols = Math.max(1, Math.round(modalOptionsGrid.clientWidth / (cachedModalBtns[0].offsetWidth || 220)));
        }
      });
    }
  }

  function renderEmptyModal(msg) {
    const btn = document.createElement("button");
    btn.className = "modal-option-btn active";
    btn.innerHTML = `<span>${msg}</span><span>Закрыть</span>`;
    btn.onclick = closeModal;
    modalOptionsGrid.appendChild(btn);
    cachedModalTabs = [];
    cachedModalBtns = [btn];
    cachedModalCols = 1;
    modalZone = "grid";
    modalGridIndex = 0;
    setFocus(btn);
  }

  function closeModal() {
    modalOpen = false;
    modalBackdrop.classList.remove("open");
    if (modalReturnFocus) {
      setFocus(modalReturnFocus);
    }
    resetOverlayTimer();
  }

  // Netflix Home Screen & History Carousel
  function updateHeroSpotlight(item) {
    if (!item || !heroTitle) return;
    heroTitle.textContent = item.series_title || item.title || "Без названия";
    if (heroBadge) {
      if (item.episode) {
        heroBadge.textContent = "СЕРИАЛ • " + String(item.episode).toUpperCase();
      } else if (item.is_series) {
        heroBadge.textContent = "СЕРИАЛ";
      } else {
        heroBadge.textContent = "ФИЛЬМ";
      }
    }
    const metaParts = [];
    if (item.episode) {
      metaParts.push("Остановились на: " + String(item.episode).toUpperCase());
    }
    if (item.translation_title) {
      metaParts.push("Озвучка: " + item.translation_title);
    }
    if (item.quality) {
      metaParts.push(item.quality);
    }
    if (item.position > 0) {
      metaParts.push("Позиция: " + formatTime(item.position));
    }
    if (heroMeta) {
      heroMeta.textContent = metaParts.join("  •  ") || item.title || "";
    }
    if (heroBackdrop) {
      if (item.thumb) {
        heroBackdrop.style.backgroundImage = `url("${item.thumb}")`;
      } else {
        heroBackdrop.style.backgroundImage = "none";
      }
    }
    if (heroProgressWrap && heroProgressFill && heroProgressLabel) {
      if (item.position > 0 || item.duration > 0) {
        heroProgressWrap.style.display = "flex";
        heroProgressFill.style.width = (item.progress_pct || 0) + "%";
        heroProgressLabel.textContent =
          formatTime(item.position || 0) +
          (item.duration > 0 ? " / " + formatTime(item.duration) + ` (${item.progress_pct || 0}%)` : "");
      } else {
        heroProgressWrap.style.display = "none";
      }
    }
  }

  function focusHistoryCard(idx) {
    if (!historyCardEls.length) return;
    historyIndex = Math.max(0, Math.min(historyCardEls.length - 1, idx));
    const card = historyCardEls[historyIndex];
    setFocus(card);
    try {
      card.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    } catch (_) {}
    if (historyItems[historyIndex]) {
      updateHeroSpotlight(historyItems[historyIndex]);
    }
  }

  function renderHistoryCarousel(items) {
    if (!historyCarousel) return;
    historyItems = Array.isArray(items) ? items : [];
    const sig = historyItems
      .map((it) => `${it.url}|${it.episode || ""}|${Math.floor(it.position || 0)}|${it.thumb || ""}`)
      .join(";;");
    if (sig === lastHistorySignature && historyCardEls.length === historyItems.length) {
      return;
    }
    lastHistorySignature = sig;
    historyCarousel.innerHTML = "";
    historyCardEls = [];

    if (!historyItems.length) {
      const empty = document.createElement("div");
      empty.style.color = "#94a3b8";
      empty.style.padding = "12px 4px";
      empty.textContent = "История просмотров пока пуста. Включите фильм или сериал с телефона или ноутбука.";
      historyCarousel.appendChild(empty);
      return;
    }

    historyItems.forEach((item, idx) => {
      const card = document.createElement("button");
      card.className = "tv-history-card focusable";
      card.type = "button";

      if (item.thumb) {
        const img = document.createElement("img");
        img.className = "tv-history-card-thumb";
        img.src = item.thumb;
        img.alt = item.series_title || item.title || "";
        img.loading = "lazy";
        card.appendChild(img);
      }

      const grad = document.createElement("div");
      grad.className = "tv-history-card-gradient";
      card.appendChild(grad);

      const badge = document.createElement("div");
      badge.className = "tv-history-card-badge";
      badge.textContent = item.episode ? String(item.episode).toUpperCase() : item.is_series ? "СЕРИАЛ" : "ФИЛЬМ";
      card.appendChild(badge);

      const body = document.createElement("div");
      body.className = "tv-history-card-body";

      const titleEl = document.createElement("div");
      titleEl.className = "tv-history-card-title";
      titleEl.textContent = item.series_title || item.title || "Без названия";
      body.appendChild(titleEl);

      const subEl = document.createElement("div");
      subEl.className = "tv-history-card-sub";
      const subParts = [];
      if (item.episode) subParts.push(String(item.episode).toUpperCase());
      if (item.position > 0) subParts.push("с " + formatTime(item.position));
      if (item.translation_title) subParts.push(item.translation_title);
      subEl.textContent = subParts.join(" • ") || "Смотреть";
      body.appendChild(subEl);

      card.appendChild(body);

      if (item.progress_pct > 0) {
        const progTrack = document.createElement("div");
        progTrack.className = "tv-history-card-progress";
        const progFill = document.createElement("div");
        progFill.className = "tv-history-card-progress-fill";
        progFill.style.width = Math.min(100, Math.max(5, item.progress_pct)) + "%";
        progTrack.appendChild(progFill);
        card.appendChild(progTrack);
      }

      card.addEventListener("focus", () => {
        historyIndex = idx;
        updateHeroSpotlight(item);
      });

      card.addEventListener("click", () => {
        historyIndex = idx;
        updateHeroSpotlight(item);
        openHistoryItemModal(item, card);
      });

      historyCarousel.appendChild(card);
      historyCardEls.push(card);
    });

    if (historyIndex >= historyCardEls.length) {
      historyIndex = 0;
    }
    updateHeroSpotlight(historyItems[historyIndex]);
    if (!standbyScreen.classList.contains("hidden") && !modalOpen) {
      focusHistoryCard(historyIndex);
    }
  }

  function openHistoryBrowser() {
    homeBrowserOpen = true;
    standbyScreen.classList.remove("hidden");
    if (homeCloseHint) {
      homeCloseHint.style.display = currentSessionId ? "block" : "none";
    }
    overlayEl.classList.add("hidden");
    overlayVisible = false;
    fetch("/api/tv/history")
      .then((r) => r.json())
      .then((res) => {
        if (res && Array.isArray(res.items)) {
          renderHistoryCarousel(res.items);
        }
        if (historyCardEls.length) {
          focusHistoryCard(historyIndex);
        }
      })
      .catch(() => {
        if (historyCardEls.length) {
          focusHistoryCard(historyIndex);
        }
      });
  }

  function closeHistoryBrowser() {
    if (!currentSessionId) return;
    homeBrowserOpen = false;
    standbyScreen.classList.add("hidden");
    if (homeCloseHint) homeCloseHint.style.display = "none";
    showOverlay();
    setFocus(document.getElementById("btn-history") || document.getElementById("btn-play"));
  }

  function castFromTv(item, episodeId, startAtSec) {
    closeModal();
    standbyScreen.classList.remove("hidden");
    homeBrowserOpen = true;
    loadingBox.classList.add("active");
    const epLabel = episodeId ? " (" + String(episodeId).toUpperCase() + ")" : "";
    loadingStatusText.textContent = `Запуск: ${item.series_title || item.title}${epLabel}…`;
    loadingSpeedText.textContent = startAtSec > 0 ? "с " + formatTime(startAtSec) : "с начала";
    loadingBarFill.style.width = "15%";
    showToast(`🎬 Запускаю ${item.series_title || item.title}${epLabel}…`);
    postAction({
      action: "cast_from_tv",
      url: item.url,
      episode: episodeId || null,
      start_at: Math.max(0, Math.floor(startAtSec || 0)),
      quality: item.quality || (serverState.opts && serverState.opts.quality) || "720p",
      speed: item.speed || (serverState.opts && serverState.opts.speed) || "1.0x",
      translation_id: item.translation_id || null,
      translation_title: item.translation_title || null,
    });
  }

  function openHistoryItemModal(item, triggerEl) {
    if (!item) return;
    modalReturnFocus = triggerEl || focusedEl;
    modalOpen = true;
    modalBackdrop.classList.add("open");
    modalSeasonTabs.style.display = "none";
    modalSeasonTabs.innerHTML = "";
    modalOptionsGrid.innerHTML = "";

    const epTag = item.episode ? ` (${String(item.episode).toUpperCase()})` : "";
    modalTitle.textContent = `🎬 ${item.series_title || item.title}${epTag}`;

    // Option 1: Continue watching from saved position
    const resumePos = Math.floor(item.position || 0);
    const resumeBtn = document.createElement("button");
    resumeBtn.className = "modal-option-btn active";
    const resumeRight =
      resumePos > 0
        ? `${item.episode ? String(item.episode).toUpperCase() + " • " : ""}${formatTime(resumePos)}`
        : item.episode
          ? String(item.episode).toUpperCase()
          : "▶";
    resumeBtn.innerHTML = `<span>▶ Продолжить просмотр</span><span>${resumeRight}</span>`;
    resumeBtn.onclick = () => {
      castFromTv(item, item.episode, resumePos);
    };
    modalOptionsGrid.appendChild(resumeBtn);

    // Option 2: Choose season and episode
    const pickEpBtn = document.createElement("button");
    pickEpBtn.className = "modal-option-btn";
    const watchedCount = item.watched_episodes ? Object.keys(item.watched_episodes).length : 0;
    pickEpBtn.innerHTML = `<span>🎬 Выбрать сезон и серию</span><span>${watchedCount > 0 ? watchedCount + " просм." : "Список"}</span>`;
    pickEpBtn.onclick = () => {
      openHistoryEpisodePicker(item, triggerEl);
    };
    modalOptionsGrid.appendChild(pickEpBtn);

    // Option 3: Watch from beginning (00:00)
    if (resumePos > 15) {
      const restartBtn = document.createElement("button");
      restartBtn.className = "modal-option-btn";
      restartBtn.innerHTML = `<span>🔄 Смотреть сначала</span><span>00:00:00</span>`;
      restartBtn.onclick = () => {
        castFromTv(item, item.episode, 0);
      };
      modalOptionsGrid.appendChild(restartBtn);
    }

    cachedModalTabs = [];
    cachedModalBtns = Array.from(modalOptionsGrid.querySelectorAll(".modal-option-btn"));
    cachedModalCols = 1;
    modalZone = "grid";
    modalGridIndex = 0;
    setFocus(cachedModalBtns[0]);
  }

  function renderHistoryEpisodesModal(item, seasons, triggerEl) {
    modalReturnFocus = triggerEl || focusedEl;
    modalOpen = true;
    modalBackdrop.classList.add("open");
    modalSeasonTabs.style.display = "none";
    modalSeasonTabs.innerHTML = "";
    modalOptionsGrid.innerHTML = "";
    modalTitle.textContent = `🎬 ${item.series_title || item.title} — Выбор серии`;

    if (!seasons || !seasons.length) {
      renderEmptyModal("Для этого видео список сезонов и серий не найден.");
      return;
    }

    if (activeHistorySeasonIdx >= seasons.length) {
      activeHistorySeasonIdx = 0;
    }

    if (seasons.length > 1) {
      modalSeasonTabs.style.display = "flex";
      seasons.forEach((s, idx) => {
        const tab = document.createElement("button");
        tab.className = "tv-btn modal-tab" + (idx === activeHistorySeasonIdx ? " active" : "");
        tab.textContent = s.title || `Сезон ${s.id || idx + 1}`;
        tab.onclick = () => {
          activeHistorySeasonIdx = idx;
          renderHistoryEpisodesModal(item, seasons, triggerEl);
        };
        modalSeasonTabs.appendChild(tab);
      });
    }

    const watchedMap = item.watched_episodes || {};
    const episodes = (seasons[activeHistorySeasonIdx] && seasons[activeHistorySeasonIdx].episodes) || [];
    episodes.forEach((ep) => {
      const btn = document.createElement("button");
      const isLastWatched = String(ep.id) === String(item.episode);
      const wInfo = watchedMap[ep.id];
      btn.className = "modal-option-btn" + (isLastWatched ? " active" : "");
      let statusBadge = "▶";
      if (wInfo && wInfo.position > 0) {
        statusBadge = "⏱ " + formatTime(wInfo.position);
      } else if (isLastWatched) {
        statusBadge = "✓ Текущая";
      }
      btn.innerHTML = `<span>${ep.title || ep.id}</span><span>${statusBadge}</span>`;
      btn.onclick = () => {
        const savedPos = wInfo && wInfo.position > 30 ? Math.floor(wInfo.position) : 0;
        if (savedPos > 30) {
          // Offer resume or start from 00:00 for this specific episode
          const subItem = Object.assign({}, item, {
            episode: ep.id,
            position: savedPos,
            duration: wInfo.duration || 0,
          });
          openHistoryItemModal(subItem, triggerEl);
        } else {
          castFromTv(item, ep.id, 0);
        }
      };
      modalOptionsGrid.appendChild(btn);
    });

    cachedModalTabs = Array.from(modalSeasonTabs.querySelectorAll(".modal-tab"));
    cachedModalBtns = Array.from(modalOptionsGrid.querySelectorAll(".modal-option-btn"));
    if (cachedModalBtns.length) {
      modalZone = "grid";
      const activeIdx = cachedModalBtns.findIndex((b) => b.classList.contains("active"));
      modalGridIndex = activeIdx >= 0 ? activeIdx : 0;
      setFocus(cachedModalBtns[modalGridIndex]);
      requestAnimationFrame(() => {
        if (cachedModalBtns[0]) {
          cachedModalCols = Math.max(1, Math.round(modalOptionsGrid.clientWidth / (cachedModalBtns[0].offsetWidth || 220)));
        }
      });
    }
  }

  function openHistoryEpisodePicker(item, triggerEl) {
    // If the currently loaded show on server matches this URL and already has seasons, use them immediately
    if (serverState.url === item.url && serverState.seasons && serverState.seasons.length) {
      const foundIdx = serverState.seasons.findIndex((s) =>
        (s.episodes || []).some((e) => String(e.id) === String(item.episode))
      );
      activeHistorySeasonIdx = foundIdx >= 0 ? foundIdx : 0;
      renderHistoryEpisodesModal(item, serverState.seasons, triggerEl);
      return;
    }

    if (historyInspectCache[item.url]) {
      const seasons = historyInspectCache[item.url];
      const foundIdx = seasons.findIndex((s) =>
        (s.episodes || []).some((e) => String(e.id) === String(item.episode))
      );
      activeHistorySeasonIdx = foundIdx >= 0 ? foundIdx : 0;
      renderHistoryEpisodesModal(item, seasons, triggerEl);
      return;
    }

    modalTitle.textContent = `⏳ Загрузка списка серий: ${item.series_title || item.title}…`;
    modalSeasonTabs.style.display = "none";
    modalOptionsGrid.innerHTML = "";
    renderEmptyModal("Запрашиваем список сезонов и серий…");

    fetch("/api/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: item.url }),
    })
      .then((r) => r.json())
      .then((res) => {
        const seasons = (res && res.seasons) || [];
        if (seasons.length) {
          historyInspectCache[item.url] = seasons;
        }
        const foundIdx = seasons.findIndex((s) =>
          (s.episodes || []).some((e) => String(e.id) === String(item.episode))
        );
        activeHistorySeasonIdx = foundIdx >= 0 ? foundIdx : 0;
        renderHistoryEpisodesModal(item, seasons, triggerEl);
      })
      .catch(() => {
        renderEmptyModal("Не удалось загрузить список серий.");
      });
  }

  // Button Click Bindings
  document.querySelectorAll("#tv-overlay .focusable").forEach((el) => {
    el.addEventListener("click", () => {
      const action = el.getAttribute("data-action");
      if (action === "toggle-play") {
        playerTogglePlay();
      } else if (action === "seek-back") {
        playerSeekRelative(-15);
      } else if (action === "seek-fwd") {
        playerSeekRelative(15);
      } else if (action === "prev-ep") {
        showToast("⏮ Предыдущая серия…");
        postAction({ action: "prev_episode" });
      } else if (action === "next-ep") {
        showToast("⏭ Следующая серия…");
        postAction({ action: "next_episode" });
      } else if (action === "open-history") {
        openHistoryBrowser();
      } else if (action === "open-episodes") {
        openModal("episodes", el);
      } else if (action === "open-voices") {
        openModal("voices", el);
      } else if (action === "open-quality") {
        openModal("quality", el);
      } else if (action === "open-speed") {
        openModal("speed", el);
      } else if (action === "toggle-dock") {
        dockPosition = dockPosition === "bottom" ? "top" : "bottom";
        localStorage.setItem("tv_dock_position", dockPosition);
        applyDockPosition();
        showToast(dockPosition === "top" ? "⬆ Панель сверху" : "⬇ Панель снизу");
      } else if (action === "timeline") {
        playerTogglePlay();
      }
    });
  });

  // D-Pad Remote Navigation Handler
  function normalizeKey(e) {
    const k = e.key || "";
    const c = e.keyCode || e.which || 0;
    if (k === "ArrowUp" || c === 19 || c === 38) return "UP";
    if (k === "ArrowDown" || c === 20 || c === 40) return "DOWN";
    if (k === "ArrowLeft" || c === 21 || c === 37) return "LEFT";
    if (k === "ArrowRight" || c === 22 || c === 39) return "RIGHT";
    if (k === "Enter" || k === " " || c === 23 || c === 66 || c === 13) return "ENTER";
    if (k === "Escape" || k === "Backspace" || k === "GoBack" || c === 4 || c === 27 || c === 8) return "BACK";
    if (k === "MediaPlayPause" || c === 85 || c === 179) return "PLAY_PAUSE";
    if (k === "MediaRewind" || c === 89) return "REWIND";
    if (k === "MediaFastForward" || c === 90) return "FF";
    return null;
  }

    function handleRemoteKey(dir) {
    if (!dir) return;

    if (dir === "PLAY_PAUSE") {
      showOverlay();
      playerTogglePlay();
      return;
    }
    if (dir === "REWIND") {
      playerSeekRelative(-15);
      return;
    }
    if (dir === "FF") {
      playerSeekRelative(15);
      return;
    }

    // 1. Modal Navigation
    if (modalOpen) {
      if (dir === "BACK") {
        closeModal();
        return;
      }
      const tabs = cachedModalTabs;
      const gridBtns = cachedModalBtns;

      if (dir === "ENTER") {
        if (focusedEl) focusedEl.click();
        return;
      }

      if (modalZone === "tabs" && tabs.length) {
        if (dir === "LEFT") {
          modalTabIndex = Math.max(0, modalTabIndex - 1);
          setFocus(tabs[modalTabIndex]);
        } else if (dir === "RIGHT") {
          modalTabIndex = Math.min(tabs.length - 1, modalTabIndex + 1);
          setFocus(tabs[modalTabIndex]);
        } else if (dir === "DOWN" && gridBtns.length) {
          modalZone = "grid";
          setFocus(gridBtns[Math.min(modalGridIndex, gridBtns.length - 1)]);
        }
        return;
      }

      if (gridBtns.length) {
        const cols = cachedModalCols || 3;
        if (dir === "LEFT") {
          modalGridIndex = Math.max(0, modalGridIndex - 1);
          setFocus(gridBtns[modalGridIndex]);
        } else if (dir === "RIGHT") {
          modalGridIndex = Math.min(gridBtns.length - 1, modalGridIndex + 1);
          setFocus(gridBtns[modalGridIndex]);
        } else if (dir === "UP") {
          if (modalGridIndex - cols >= 0) {
            modalGridIndex -= cols;
            setFocus(gridBtns[modalGridIndex]);
          } else if (tabs.length) {
            modalZone = "tabs";
            modalTabIndex = Math.min(activeSeasonIndex, tabs.length - 1);
            setFocus(tabs[modalTabIndex]);
          }
        } else if (dir === "DOWN") {
          if (modalGridIndex + cols < gridBtns.length) {
            modalGridIndex += cols;
            setFocus(gridBtns[modalGridIndex]);
          }
        }
      }
      return;
    }

    // 1.5 Netflix Home / History Screen Navigation (when standby screen is visible)
    if (!standbyScreen.classList.contains("hidden")) {
      if (dir === "BACK") {
        if (currentSessionId) {
          closeHistoryBrowser();
        }
        return;
      }
      if (historyCardEls.length) {
        if (dir === "LEFT") {
          focusHistoryCard(historyIndex - 1);
        } else if (dir === "RIGHT") {
          focusHistoryCard(historyIndex + 1);
        } else if (dir === "UP" || dir === "DOWN") {
          focusHistoryCard(historyIndex);
        } else if (dir === "ENTER") {
          if (historyCardEls[historyIndex]) {
            historyCardEls[historyIndex].click();
          }
        }
      }
      return;
    }

    // 2. When Overlay is Hidden
    if (!overlayVisible) {
      if (dir === "LEFT") {
        playerSeekRelative(-15);
      } else if (dir === "RIGHT") {
        playerSeekRelative(15);
      } else if (dir === "ENTER" || dir === "UP" || dir === "DOWN") {
        showOverlay();
        setFocus(row2Els[0]);
      }
      return;
    }

    // 3. When Overlay is Visible
    resetOverlayTimer();

    if (dir === "BACK") {
      hideOverlay();
      return;
    }

    const row = Number((focusedEl && focusedEl.getAttribute("data-row")) || 2);

    if (dir === "ENTER") {
      if (focusedEl) focusedEl.click();
      return;
    }

    if (row === 1) {
      // Timeline Scrubber focused
      if (dir === "LEFT") {
        playerSeekRelative(-15);
      } else if (dir === "RIGHT") {
        playerSeekRelative(15);
      } else if (dir === "UP") {
        setFocus(row0Els[0]);
      } else if (dir === "DOWN") {
        setFocus(row2Els[0]);
      }
      return;
    }

    if (row === 0) {
      if (dir === "DOWN") {
        setFocus(row1Els[0]);
      }
      return;
    }

    if (row === 2) {
      const idx = Math.max(0, row2Els.indexOf(focusedEl));
      if (dir === "LEFT") {
        setFocus(row2Els[Math.max(0, idx - 1)]);
      } else if (dir === "RIGHT") {
        setFocus(row2Els[Math.min(row2Els.length - 1, idx + 1)]);
      } else if (dir === "UP") {
        setFocus(row1Els[0]);
      }
    }
  }

  window.onTvKey = handleRemoteKey;

  window.addEventListener("keydown", (e) => {
    const dir = normalizeKey(e);
    if (!dir) return;
    e.preventDefault();
    e.stopPropagation();
    handleRemoteKey(dir);
  });

  // Periodic Progress UI & Server Reporting
  setInterval(() => {
    if (!currentSessionId) return;
    const pos = playerGetPosition();
    const dur = playerGetDuration();
    updateTimelineUI(pos, dur);

    fetch("/api/tv/report", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        state: isPaused ? "PAUSED_PLAYBACK" : "PLAYING",
        position: Math.floor(pos),
        duration: Math.floor(dur),
      }),
    }).catch(() => {});
  }, 2500);

  // Connect to Server SSE + Fallback Polling
  function startStateSync() {
    fetch("/api/tv/state")
      .then((r) => r.json())
      .then(applyServerState)
      .catch(() => {});

    let sseConnected = false;
    if (window.EventSource) {
      const es = new EventSource("/api/tv/events");
      es.onopen = () => {
        sseConnected = true;
      };
      es.onerror = () => {
        sseConnected = false;
      };
      es.onmessage = (ev) => {
        try {
          applyServerState(JSON.parse(ev.data));
        } catch (_) {}
      };
    }

    setInterval(() => {
      if (sseConnected) return;
      fetch("/api/tv/state")
        .then((r) => r.json())
        .then(applyServerState)
        .catch(() => {});
    }, 5000);
  }

  setFocus(document.getElementById("btn-play"));
  startStateSync();
})();
