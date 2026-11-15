#!/usr/bin/env python3
"""
Dual Monitor Screen Caster for Linux -> Android TV / H20 Box
Captures two monitors (or custom layout), encodes with ultra-low latency H.264,
serves MPEG-TS over HTTP, and triggers Just Player on the TV box via ADB.
"""

import os
import sys
import time
import socket
import signal
import shutil
import argparse
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import queue
import collections

DEFAULT_PORT = 8088
DEFAULT_BITRATE = "5000k"
DEFAULT_FPS = 30
TV_IP = "192.168.1.105"
TV_PORT = 5555

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def detect_monitors():
    """Detect connected monitors, resolutions and offsets using xrandr."""
    monitors = []
    try:
        out = subprocess.check_output(["xrandr", "--current"], text=True)
        import re
        for line in out.splitlines():
            if " connected" in line:
                m = re.search(r"^(\S+) connected.*?\b(\d+)x(\d+)\+(\d+)\+(\d+)", line)
                if m:
                    monitors.append({
                        "name": m.group(1),
                        "width": int(m.group(2)),
                        "height": int(m.group(3)),
                        "x": int(m.group(4)),
                        "y": int(m.group(5)),
                        "primary": "primary" in line
                    })
    except Exception as e:
        print(f"[!] xrandr error: {e}")
    # Sort left-to-right based on x position
    monitors.sort(key=lambda m: m["x"])
    return monitors

class TSBroadcaster:
    def __init__(self):
        self.clients = []
        self.lock = threading.Lock()
        self.recent_buffer = collections.deque(maxlen=16)

    def register(self, client_q):
        with self.lock:
            for chunk in self.recent_buffer:
                try:
                    client_q.put_nowait(chunk)
                except queue.Full:
                    break
            self.clients.append(client_q)

    def unregister(self, client_q):
        with self.lock:
            if client_q in self.clients:
                self.clients.remove(client_q)

    def broadcast(self, chunk):
        with self.lock:
            self.recent_buffer.append(chunk)
            for q in list(self.clients):
                try:
                    q.put_nowait(chunk)
                except queue.Full:
                    try:
                        q.get_nowait()
                        q.put_nowait(chunk)
                    except Exception:
                        pass

broadcaster = TSBroadcaster()

class StreamHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/live.ts") or self.path == "/" or self.path.startswith("/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.send_header("Connection", "close")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            client_q = queue.Queue(maxsize=120)
            broadcaster.register(client_q)
            print(f"[+] Client connected: {self.client_address[0]}")
            try:
                while True:
                    try:
                        chunk = client_q.get(timeout=2.0)
                        self.wfile.write(chunk)
                    except queue.Empty:
                        continue
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                broadcaster.unregister(client_q)
                print(f"[-] Client disconnected: {self.client_address[0]}")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

def wake_and_launch_tv(stream_url, tv_ip=TV_IP, player="just", max_retries=20):
    target = f"{tv_ip}:{TV_PORT}"
    adb = shutil.which("adb") or "/usr/bin/adb"
    print(f"[*] Ожидание подключения к ТВ-приставке {target}...")
    
    connected = False
    for attempt in range(max_retries):
        res = subprocess.run([adb, "connect", target], capture_output=True, text=True, timeout=5)
        if "connected to" in res.stdout or "already connected" in res.stdout:
            connected = True
            break
        time.sleep(2.5)

    if connected:
        print(f"[✓] Успешно подключено к ТВ {target}")
        subprocess.run([adb, "-s", target, "shell", "input", "keyevent", "KEYCODE_WAKEUP"], capture_output=True)
        time.sleep(0.5)

        if player == "just":
            cmd = [
                adb, "-s", target, "shell", "am", "start",
                "-a", "android.intent.action.VIEW",
                "-d", stream_url,
                "-t", "video/*",
                "-n", "com.brouken.player/.PlayerActivity"
            ]
        elif player == "tvcaster":
            cmd = [
                adb, "-s", target, "shell", "am", "start",
                "-a", "android.intent.action.VIEW",
                "-d", stream_url,
                "-t", "video/*",
                "-n", "com.tvcaster.player/.MainActivity"
            ]
        else:
            cmd = [
                adb, "-s", target, "shell", "am", "start",
                "-a", "android.intent.action.VIEW",
                "-d", stream_url,
                "-t", "video/*"
            ]
        print(f"[*] Запуск плеера на ТВ ({player})...")
        subprocess.run(cmd, capture_output=True)
        print("[✓] Трансляция успешно запущена на ТВ экранах!")
    else:
        print(f"[!] ТВ-приставка ({target}) не отвечает. Включите приставку пультом.")

def close_tv_player(tv_ip=TV_IP):
    target = f"{tv_ip}:{TV_PORT}"
    adb = shutil.which("adb") or "/usr/bin/adb"
    try:
        subprocess.run([adb, "-s", target, "shell", "input", "keyevent", "KEYCODE_HOME"], capture_output=True, timeout=2)
    except Exception:
        pass

def main():
    parser = argparse.ArgumentParser(description="Dual-Monitor Screen Caster to Android TV")
    parser.add_argument("--layout", choices=["side-by-side", "pip", "stacked", "monitor1", "monitor2"],
                        default="side-by-side", help="Screen arrangement on TV (default: side-by-side)")
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS, help=f"Framerate (default: {DEFAULT_FPS})")
    parser.add_argument("--bitrate", default=DEFAULT_BITRATE, help=f"Video bitrate (default: {DEFAULT_BITRATE})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"HTTP stream port (default: {DEFAULT_PORT})")
    parser.add_argument("--tv-ip", default=TV_IP, help=f"TV Box IP (default: {TV_IP})")
    parser.add_argument("--audio", action="store_true", help="Include PC audio in stream")
    parser.add_argument("--player", choices=["just", "tvcaster", "auto"], default="just", help="Player on TV")
    parser.add_argument("--no-launch", action="store_true", help="Do not trigger ADB launch on TV")
    args = parser.parse_args()

    local_ip = get_local_ip()
    stream_url = f"http://{local_ip}:{args.port}/live.ts"

    monitors = detect_monitors()
    print("=" * 60)
    print("📺 DUAL-MONITOR SCREEN CASTER (Linux -> Android TV)")
    print("=" * 60)
    print(f"▶ Обнаружено мониторов: {len(monitors)}")
    for i, m in enumerate(monitors):
        p = " (Primary)" if m["primary"] else ""
        print(f"   [{i}] {m['name']}: {m['width']}x{m['height']}+{m['x']}+{m['y']}{p}")

    if not monitors:
        print("[!] Ошибка: мониторы не найдены через xrandr!")
        sys.exit(1)

    m1 = monitors[0]
    m2 = monitors[1] if len(monitors) > 1 else monitors[0]

    ffmpeg_bin = "/usr/bin/ffmpeg"
    if not os.path.exists(ffmpeg_bin):
        ffmpeg_bin = shutil.which("ffmpeg")

    ffmpeg_cmd = [ffmpeg_bin, "-hide_banner", "-loglevel", "warning"]

    # Inputs and Filter complex
    if args.layout == "side-by-side":
        # Capture both monitors, scale each 16:9 to 960x540, hstack to 1920x540, pad to 1920x1080
        ffmpeg_cmd += [
            "-f", "x11grab", "-framerate", str(args.fps), "-video_size", f"{m1['width']}x{m1['height']}",
            "-i", f":0.0+{m1['x']},{m1['y']}",
            "-f", "x11grab", "-framerate", str(args.fps), "-video_size", f"{m2['width']}x{m2['height']}",
            "-i", f":0.0+{m2['x']},{m2['y']}",
            "-filter_complex",
            "[0:v]scale=960:540:flags=fast_bilinear[v0];"
            "[1:v]scale=960:540:flags=fast_bilinear[v1];"
            "[v0][v1]hstack=inputs=2[h];"
            "[h]pad=1920:1080:0:270:black[out]",
            "-map", "[out]"
        ]
    elif args.layout == "pip":
        # Monitor 2 is full 1080p, Monitor 1 is PiP (480x270) in bottom-right corner
        ffmpeg_cmd += [
            "-f", "x11grab", "-framerate", str(args.fps), "-video_size", f"{m2['width']}x{m2['height']}",
            "-i", f":0.0+{m2['x']},{m2['y']}",
            "-f", "x11grab", "-framerate", str(args.fps), "-video_size", f"{m1['width']}x{m1['height']}",
            "-i", f":0.0+{m1['x']},{m1['y']}",
            "-filter_complex",
            "[0:v]scale=1920:1080:flags=fast_bilinear[main];"
            "[1:v]scale=480:270:flags=fast_bilinear,drawbox=x=0:y=0:w=iw:h=ih:color=white@0.8:t=2[pip];"
            "[main][pip]overlay=W-w-24:H-h-24[out]",
            "-map", "[out]"
        ]
    elif args.layout == "stacked":
        # Monitor 1 on top, Monitor 2 below (each 960x540, total 960x1080 centered on 1920x1080)
        ffmpeg_cmd += [
            "-f", "x11grab", "-framerate", str(args.fps), "-video_size", f"{m1['width']}x{m1['height']}",
            "-i", f":0.0+{m1['x']},{m1['y']}",
            "-f", "x11grab", "-framerate", str(args.fps), "-video_size", f"{m2['width']}x{m2['height']}",
            "-i", f":0.0+{m2['x']},{m2['y']}",
            "-filter_complex",
            "[0:v]scale=960:540:flags=fast_bilinear[v0];"
            "[1:v]scale=960:540:flags=fast_bilinear[v1];"
            "[v0][v1]vstack=inputs=2[v];"
            "[v]pad=1920:1080:480:0:black[out]",
            "-map", "[out]"
        ]
    elif args.layout == "monitor1":
        ffmpeg_cmd += [
            "-f", "x11grab", "-framerate", str(args.fps), "-video_size", f"{m1['width']}x{m1['height']}",
            "-i", f":0.0+{m1['x']},{m1['y']}",
            "-vf", "scale=1920:1080:flags=fast_bilinear"
        ]
    elif args.layout == "monitor2":
        ffmpeg_cmd += [
            "-f", "x11grab", "-framerate", str(args.fps), "-video_size", f"{m2['width']}x{m2['height']}",
            "-i", f":0.0+{m2['x']},{m2['y']}",
            "-vf", "scale=1920:1080:flags=fast_bilinear"
        ]

    if args.audio:
        ffmpeg_cmd += [
            "-f", "pulse", "-i", "default",
            "-c:a", "aac", "-b:a", "128k", "-async", "1"
        ]

    gop = max(10, args.fps // 2)
    ffmpeg_cmd += [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-pix_fmt", "yuv420p",
        "-profile:v", "baseline",
        "-g", str(gop),
        "-keyint_min", str(gop),
        "-b:v", args.bitrate,
        "-maxrate", f"{int(int(args.bitrate.replace('k','')) * 1.3)}k",
        "-bufsize", f"{int(int(args.bitrate.replace('k','')) * 0.5)}k",
        "-f", "mpegts",
        "-"
    ]

    print(f"▶ Режим: {args.layout}")
    print(f"▶ Ссылка потока: {stream_url}")
    print("▶ Нажмите Ctrl+C для завершения трансляции")
    print("-" * 60)

    # Start HTTP server
    httpd = HTTPServer(("0.0.0.0", args.port), StreamHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    # Start FFmpeg process
    proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def read_ffmpeg():
        while proc.poll() is None:
            chunk = proc.stdout.read(8192)
            if chunk:
                broadcaster.broadcast(chunk)
            else:
                break

    stream_thread = threading.Thread(target=read_ffmpeg, daemon=True)
    stream_thread.start()

    # Trigger TV player
    if not args.no_launch:
        threading.Thread(target=wake_and_launch_tv, args=(stream_url, args.tv_ip, args.player), daemon=True).start()

    # Graceful shutdown handler
    def cleanup():
        print("\n[*] Завершение трансляции...")
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
        httpd.shutdown()
        if not args.no_launch:
            close_tv_player(args.tv_ip)
        print("[✓] Трансляция остановлена.")

    try:
        while proc.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()

if __name__ == "__main__":
    main()
