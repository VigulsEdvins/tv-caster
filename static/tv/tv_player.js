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
  let knownDuration = 0;
  let isPaused = false;
  let currentSpeed = 1.0;

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
    document.querySelectorAll(".focused").forEach((node) => node.classList.remove("focused"));
    focusedEl = el;
    el.classList.add("focused");
    try {
      el.focus({ preventScroll: false });
    } catch (_) {}
  }

  // Player Adapter (works with Native AndroidBridge OR HTML5 Video)
  function playerLoad(stateObj, startAtSec) {
    const sid = stateObj.session_id;
    if (!sid) return;
    currentSessionId = sid;
    baseSeekOffset = Math.max(0, Number(startAtSec || 0));
    currentSpeed = parseSpeed((stateObj.opts && stateObj.opts.speed) || "1.0x");
    isPaused = false;

    if (hasNativeBridge) {
      const fullTsUrl = window.location.origin + (stateObj.ts_url || `/stream.ts?v=${sid}`);
      window.AndroidBridge.loadStream(fullTsUrl, Math.floor(baseSeekOffset), currentSpeed);
    } else {
      const mp4Url = `/stream.mp4?v=${sid}` + (baseSeekOffset > 0 ? `&t=${Math.floor(baseSeekOffset)}` : "");
      videoEl.src = mp4Url;
      videoEl.playbackRate = currentSpeed;
      videoEl.play().catch(() => {});
    }
    standbyScreen.classList.add("hidden");
    updatePlayButtonUI(false);
    showOverlay();
    setFocus(document.getElementById("btn-play"));
  }

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
    if (hasNativeBridge) {
      return Number(window.AndroidBridge.getPosition() || 0);
    }
    return baseSeekOffset + Number(videoEl.currentTime || 0);
  }

  function playerGetDuration() {
    if (!currentSessionId) return 0;
    if (hasNativeBridge) {
      const d = Number(window.AndroidBridge.getDuration() || 0);
      if (d > 0 && d < 43200) return d;
    } else {
      const d = Number(videoEl.duration || 0);
      if (isFinite(d) && d > 0 && d < 43200) return baseSeekOffset + d;
    }
    return Math.max(knownDuration, Number(serverState.duration || 0));
  }

  function playerSeekTo(targetSec) {
    if (!currentSessionId) return;
    const dur = playerGetDuration();
    targetSec = Math.max(0, dur > 0 ? Math.min(targetSec, dur - 2) : targetSec);

    if (hasNativeBridge) {
      window.AndroidBridge.seekTo(Math.floor(targetSec));
    } else {
      const relTarget = targetSec - baseSeekOffset;
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
      } else if (!serverState.is_active_download) {
        baseSeekOffset = Math.floor(targetSec);
        videoEl.src = `/stream.mp4?v=${currentSessionId}&t=${baseSeekOffset}`;
        videoEl.playbackRate = currentSpeed;
        if (!isPaused) videoEl.play().catch(() => {});
      } else if (relTarget >= 0) {
        try {
          videoEl.currentTime = relTarget;
        } catch (_) {}
      }
    }
    updateTimelineUI(targetSec, dur);
  }

  function playerSeekRelative(deltaSec) {
    if (!currentSessionId) return;
    const nextPos = Math.max(0, playerGetPosition() + deltaSec);
    playerSeekTo(nextPos);
    postAction({ action: "seek", target: Math.floor(nextPos) });
    showToast((deltaSec > 0 ? "⏩ +" : "⏪ ") + deltaSec + " сек (" + formatTime(nextPos) + ")");
    resetOverlayTimer();
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
      subVoice.textContent = voiceTitle.slice(0, 16);
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

    if (data.is_active_download) {
      overlayBufferInfo.textContent = `Буферизация: ${data.download_progress || 0}% (${data.download_speed || "0 MB/s"})`;
    } else if (data.status === "playing") {
      overlayBufferInfo.textContent = "Загружено 100%";
    } else {
      overlayBufferInfo.textContent = data.status_text || "";
    }

    // Standby / Loading states
    if (data.status === "preparing" || (data.status === "downloading" && !data.stream_url)) {
      standbyScreen.classList.remove("hidden");
      standbyTitle.textContent = data.title || "Подготовка видео…";
      standbySubtitle.textContent = data.status_text || "Пожалуйста, подождите…";
      loadingBox.classList.add("active");
      loadingStatusText.textContent = data.status_text || "Загрузка…";
      loadingSpeedText.textContent = data.download_speed || "";
      loadingBarFill.style.width = (data.download_progress || 10) + "%";
    } else if (data.status === "error") {
      standbyScreen.classList.remove("hidden");
      standbyTitle.textContent = data.error || "Ошибка воспроизведения";
      standbySubtitle.textContent = data.error_hint || "Попробуйте выбрать другое качество или озвучку.";
      loadingBox.classList.remove("active");
    }

    // Handle Commands from Server
    const cmd = data.last_command;
    if (cmd && cmd.seq && cmd.seq !== lastCommandSeq) {
      lastCommandSeq = cmd.seq;
      if (cmd.type === "load" && cmd.target && cmd.target.session_id) {
        playerLoad(data, cmd.target.start_at || 0);
      } else if (cmd.type === "play") {
        playerPlay();
      } else if (cmd.type === "pause") {
        playerPause();
      } else if (cmd.type === "seek") {
        playerSeekTo(Number(cmd.target || 0));
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
        loadingBox.classList.remove("active");
        standbyTitle.textContent = "TV Caster Ready";
        standbySubtitle.textContent = "Воспроизведение остановлено.";
      }
    } else if (data.session_id && data.stream_url && currentSessionId !== data.session_id) {
      // Auto-attach if TV opened mid-session
      playerLoad(data, data.position || 0);
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
          showToast(`📺 Качество: ${q}`);
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
          playerSetSpeed(s);
          postAction({ action: "set_speed", speed: s });
        };
        modalOptionsGrid.appendChild(btn);
      });
    }

    // Focus active option or first option inside modal
    const gridBtns = Array.from(modalOptionsGrid.querySelectorAll(".modal-option-btn"));
    if (gridBtns.length) {
      modalZone = "grid";
      const activeIdx = gridBtns.findIndex((b) => b.classList.contains("active"));
      modalGridIndex = activeIdx >= 0 ? activeIdx : 0;
      setFocus(gridBtns[modalGridIndex]);
    }
  }

  function renderEmptyModal(msg) {
    const btn = document.createElement("button");
    btn.className = "modal-option-btn active";
    btn.innerHTML = `<span>${msg}</span><span>Закрыть</span>`;
    btn.onclick = closeModal;
    modalOptionsGrid.appendChild(btn);
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

  window.addEventListener("keydown", (e) => {
    const dir = normalizeKey(e);
    if (!dir) return;
    e.preventDefault();
    e.stopPropagation();

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
      const tabs = Array.from(modalSeasonTabs.querySelectorAll(".modal-tab"));
      const gridBtns = Array.from(modalOptionsGrid.querySelectorAll(".modal-option-btn"));

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
        const cols = Math.max(1, Math.round(modalOptionsGrid.clientWidth / (gridBtns[0].offsetWidth || 220)));
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

    // 2. When Overlay is Hidden
    if (!overlayVisible) {
      if (dir === "LEFT") {
        playerSeekRelative(-15);
      } else if (dir === "RIGHT") {
        playerSeekRelative(15);
      } else if (dir === "ENTER" || dir === "UP" || dir === "DOWN") {
        showOverlay();
        setFocus(document.getElementById("btn-play"));
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
    const row0 = [document.getElementById("btn-dock")];
    const row1 = [document.getElementById("timeline-scrubber")];
    const row2 = Array.from(document.querySelectorAll('#tv-overlay .focusable[data-row="2"]'));

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
        setFocus(row0[0]);
      } else if (dir === "DOWN") {
        setFocus(row2[0]);
      }
      return;
    }

    if (row === 0) {
      if (dir === "DOWN") {
        setFocus(row1[0]);
      }
      return;
    }

    if (row === 2) {
      const idx = Math.max(0, row2.indexOf(focusedEl));
      if (dir === "LEFT") {
        setFocus(row2[Math.max(0, idx - 1)]);
      } else if (dir === "RIGHT") {
        setFocus(row2[Math.min(row2.length - 1, idx + 1)]);
      } else if (dir === "UP") {
        setFocus(row1[0]);
      }
    }
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
  }, 1500);

  // Connect to Server SSE + Fallback Polling
  function startStateSync() {
    fetch("/api/tv/state")
      .then((r) => r.json())
      .then(applyServerState)
      .catch(() => {});

    if (window.EventSource) {
      const es = new EventSource("/api/tv/events");
      es.onmessage = (ev) => {
        try {
          applyServerState(JSON.parse(ev.data));
        } catch (_) {}
      };
    }

    setInterval(() => {
      fetch("/api/tv/state")
        .then((r) => r.json())
        .then(applyServerState)
        .catch(() => {});
    }, 2500);
  }

  setFocus(document.getElementById("btn-play"));
  startStateSync();
})();
