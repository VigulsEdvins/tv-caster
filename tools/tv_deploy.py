#!/usr/bin/env python3
"""Wireless ADB installer and launcher for the TV Caster Android TV Player."""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_APK = os.path.join(ROOT_DIR, "static", "tv-caster-player.apk")
TV_JSON = os.path.join(ROOT_DIR, "tv.json")


def find_adb():
    adb = shutil.which("adb")
    if adb:
        return adb
    local_adb = os.path.expanduser("~/.local/bin/adb")
    if os.path.isfile(local_adb) and os.access(local_adb, os.X_OK):
        return local_adb
    return None


def get_default_tv_ip():
    if os.path.isfile(TV_JSON):
        try:
            with open(TV_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("ip"):
                    return data["ip"]
        except Exception:
            pass
    return "192.168.1.105"


def get_local_ip(tv_ip):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((tv_ip, 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "192.168.1.116"


def deploy_to_tv(tv_ip=None, port=5555, apk_path=None, server_url=None):
    adb = find_adb()
    if not adb:
        return False, "ADB binary not found. Please install adb or check ~/.local/bin/adb."

    tv_ip = tv_ip or get_default_tv_ip()
    apk_path = apk_path or DEFAULT_APK
    if not os.path.isfile(apk_path):
        return False, f"APK file not found at {apk_path}"

    local_ip = get_local_ip(tv_ip)
    server_url = server_url or f"http://{local_ip}:8080/tv"
    target = f"{tv_ip}:{port}"

    # 1. Connect via Wireless ADB
    conn = subprocess.run([adb, "connect", target], capture_output=True, text=True, timeout=15)
    out = (conn.stdout or "") + "\n" + (conn.stderr or "")
    if conn.returncode != 0 or "refused" in out.lower() or "failed to connect" in out.lower():
        return False, (
            f"Could not connect to {target} ({out.strip()}).\n"
            "Make sure Wireless/USB Debugging is enabled on your Android TV:\n"
            "  Settings -> Device Preferences -> About -> Click 'Build' 7 times -> Developer Options -> Enable USB/Network Debugging."
        )

    # 2. Install APK
    inst = subprocess.run([adb, "-s", target, "install", "-r", apk_path], capture_output=True, text=True, timeout=60)
    inst_out = (inst.stdout or "") + "\n" + (inst.stderr or "")
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in inst_out:
        subprocess.run([adb, "-s", target, "uninstall", "com.tvcaster.player"], capture_output=True, timeout=15)
        inst = subprocess.run([adb, "-s", target, "install", "-r", apk_path], capture_output=True, text=True, timeout=60)
        inst_out = (inst.stdout or "") + "\n" + (inst.stderr or "")
    if inst.returncode != 0 or "Success" not in inst_out:
        if "unauthorized" in inst_out.lower():
            return False, (
                "TV requires authorization! Look at your Android TV screen right now, "
                "check 'Always allow from this computer', and click Allow. Then run this command again."
            )
        return False, f"APK install failed: {inst_out.strip()}"

    # 3. Wake TV and Launch TV Caster Player
    subprocess.run([adb, "-s", target, "shell", "input", "keyevent", "KEYCODE_WAKEUP"], capture_output=True, timeout=5)
    launch = subprocess.run(
        [
            adb, "-s", target, "shell", "am", "start",
            "-n", "com.tvcaster.player/.MainActivity",
            "-e", "server_url", server_url,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if launch.returncode != 0:
        return False, f"Installed APK, but failed to launch activity: {(launch.stderr or launch.stdout).strip()}"

    return True, f"Successfully installed and launched TV Caster Player on {target} (-> {server_url})"


def main():
    parser = argparse.ArgumentParser(description="Deploy TV Caster Player APK to Android TV over Wireless ADB.")
    parser.add_argument("--ip", default=None, help="Android TV IP address (default: from tv.json or 192.168.1.105)")
    parser.add_argument("--port", type=int, default=5555, help="Wireless ADB port (default: 5555)")
    parser.add_argument("--apk", default=DEFAULT_APK, help="Path to APK file")
    parser.add_argument("--url", default=None, help="Override TV Caster server URL")
    args = parser.parse_args()

    ok, msg = deploy_to_tv(tv_ip=args.ip, port=args.port, apk_path=args.apk, server_url=args.url)
    print(msg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
