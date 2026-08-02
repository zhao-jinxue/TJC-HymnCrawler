# crawler_core/config.py
# 全局配置

import os

BASE_URL = "https://sacredmusic.tjc.org.tw"
LIST_URL = "https://sacredmusic.tjc.org.tw/hymn"

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVE_ROOT = os.path.join(SCRIPT_DIR, "Hymn_Downloads")
DB_PATH = os.path.join(SCRIPT_DIR, "tjc_hymn.db")
MAP_FILE = os.path.join(SAVE_ROOT, "url_map.txt")
PROBE_REPORT = os.path.join(SCRIPT_DIR, "probe_report.json")

os.makedirs(SAVE_ROOT, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": BASE_URL
}
