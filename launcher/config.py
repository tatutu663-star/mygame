"""ค่าตั้งต้นของ Launcher — อ่านจาก config.json (ที่รากโปรเจกต์) ไม่ต้องแก้ไฟล์นี้

repo / branch / เวอร์ชัน ถูกเขียนลง build_info.py อัตโนมัติโดย GitHub Actions ตอน build
ถ้ารันจากซอร์สเพื่อทดสอบ ให้ตั้ง environment variable:  MYGAME_REPO=user/repo
"""
import json
import os
import sys
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):  # ตอนเป็น .exe (PyInstaller --add-data)
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


ICON_DIR = _base_dir() / "icons"   # ไอคอนปุ่มมุมขวาบน: gear.png / minimize.png / close.png

_cfg = json.loads((_base_dir() / "config.json").read_text("utf-8"))

APP_NAME = _cfg["app_name"]
GAME_EXE = _cfg["game_exe"]
GAME_ARGS = list(_cfg.get("game_args", []))
LAUNCH_SECRET = str(_cfg.get("launch_secret", ""))  # ต้องตรงกับค่าใน LauncherGate.cs ของ Unity
CHANNELS = list(_cfg.get("channels", ["stable"]))
DEFAULT_CHANNEL = _cfg.get("default_channel", CHANNELS[0])

try:
    import build_info  # สร้างโดย workflow
    REPO, BRANCH, LAUNCHER_VERSION = build_info.REPO, build_info.BRANCH, build_info.VERSION
except ImportError:
    REPO = os.environ.get("MYGAME_REPO", "")
    BRANCH = os.environ.get("MYGAME_BRANCH", "main")
    LAUNCHER_VERSION = "0.0.0"

# ที่อยู่ไฟล์ที่ Launcher อ่าน (ทั้งหมดอยู่ใน repo เดียว ไม่ต้องใช้ GitHub Pages)
#   META_BASE : manifest / launcher.json  (แนบอยู่ใน Release ชื่อ "meta" สร้างอัตโนมัติ)
#   RAW_BASE  : news.json / status.json / banners  (ไฟล์ในโฟลเดอร์ site/ ของ repo)
META_BASE = f"https://github.com/{REPO}/releases/download/meta/"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/site/"

# ใช้ทดสอบกับเซิร์ฟเวอร์ในเครื่องเท่านั้น (ไม่มีผลเมื่อเป็น .exe)
ALLOW_INSECURE_HTTP = False
if not getattr(sys, "frozen", False):
    META_BASE = os.environ.get("MYGAME_META_BASE", META_BASE)
    RAW_BASE = os.environ.get("MYGAME_RAW_BASE", RAW_BASE)
    ALLOW_INSECURE_HTTP = os.environ.get("MYGAME_ALLOW_HTTP") == "1"
