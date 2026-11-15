import os
import shutil


VENV_DIR = os.environ.get("TV_CASTER_VENV", "/home/edvins/.gemini/antigravity/scratch/ffmpeg_venv")

YT_DLP_BIN = shutil.which("yt-dlp") or os.path.join(VENV_DIR, "bin", "yt-dlp")

NODE_BIN = shutil.which("node") or "/home/edvins/.local/bin/node"

YT_DLP_BASE = [YT_DLP_BIN, "--no-check-certificates", "--js-runtimes", f"node:{NODE_BIN}"]
