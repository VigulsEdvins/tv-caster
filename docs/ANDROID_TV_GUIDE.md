# Android TV Player — Installation & Remote Control Guide

## 1. Enable Wireless ADB on Your Android TV (One-Time Setup)

1. On your Android TV, open **Settings** (⚙️) → **Device Preferences** → **About**.
2. Scroll down to **Android TV OS build** (or **Build**) and click **OK** on your remote **7 times** until it says *"You are now a developer!"*.
3. Press **Back** once and open **Developer options**.
4. Turn on **USB debugging** (and **Network debugging** / **Wireless debugging** if listed separately).

---

## 2. Install & Launch the App from Your Computer

Run the deployment script from the `tv-caster` directory:

```bash
python3 tools/tv_deploy.py
```

- **First time connecting:** A prompt will appear on your TV screen asking *"Allow USB debugging?"*. Check **"Always allow from this computer"** and click **Allow**, then run `python3 tools/tv_deploy.py` once more.
- The script automatically installs `tv-caster-player.apk` and launches **TV Caster Player** on your TV.
- Whenever you start a video from your phone or computer (`http://192.168.1.116:8080`), the server automatically wakes and launches the TV Caster Player app and begins playback.

---

## 3. TV Remote Control Navigation

| Remote Button | When Overlay is Hidden | When Overlay is Visible |
|---|---|---|
| **OK / Enter** | Opens control bar | Activates focused button / option |
| **◀ Left / ▶ Right** | Seeks **-15s / +15s** immediately | Moves between controls (or seeks when on timeline) |
| **▲ Up / ▼ Down** | Opens control bar | Moves between Top/Bottom toggle, Timeline, and Controls |
| **Back (↩)** | — | Closes open menu or hides the control bar |
| **Play / Pause (⏯)** | Toggles Play / Pause | Toggles Play / Pause |

---

## 4. On-Screen Features

- **🎬 Серии (Seasons & Episodes):** Switch between seasons and episodes directly from your TV remote.
- **🎙 Озвучка (Voice Acting / Audio Tracks):** Switch between voice-over studios or embedded audio tracks while preserving your current playback position.
- **📺 Качество (Quality):** Switch between `1080p`, `720p`, `480p`, and `360p` on the fly.
- **⚡ Скорость (Speed):** Instantly adjust playback speed (`0.75x`, `1.0x`, `1.25x`, `1.5x`, `2.0x`).
- **⇅ Панель (Снизу / Сверху):** Move the control bar between the **bottom** and the **top** of your TV screen.
