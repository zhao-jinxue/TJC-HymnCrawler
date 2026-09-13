# crawler_core/config.py
# 全局配置
#
# 参数均可用环境变量覆盖（默认值即 §5.9.5 定稿值），便于临时调参与离线测试：
#   CRAWL_ENGINE / API_MAX_WORKERS / API_RETRIES / API_BACKOFF / API_TIMEOUT
#   API_CACHE / PROBE_URL_CHECK / PROBE_URL_WORKERS
#   DOWNLOAD_RETRIES / DOWNLOAD_BACKOFF / USE_SELENIUM_FALLBACK

import os

BASE_URL = "https://sacredmusic.tjc.org.tw"
LIST_URL = "https://sacredmusic.tjc.org.tw/hymn"

# 官网 JSON API（诗歌详情：lyrics[] 正歌 + lyrics_chorus 副歌），{no} 为诗歌编号
API_HYMN_URL = BASE_URL + "/api/hymn/{}"
# 列表接口：?page=N，每页固定 10 条（per_page 被服务端忽略）→ 48 页 474 首
API_LIST_URL = BASE_URL + "/api/hymn"
# 分类详情接口
API_CATEGORY_URL = BASE_URL + "/api/hymn/category/{}"

SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAVE_ROOT = os.path.join(SCRIPT_DIR, "Hymn_Downloads")
DB_PATH = os.path.join(SCRIPT_DIR, "tjc_hymn.db")
MAP_FILE = os.path.join(SAVE_ROOT, "url_map.txt")
PROBE_REPORT = os.path.join(SCRIPT_DIR, "probe_report.json")
# API 响应磁盘缓存（48 页 JSON ≈ 2.2 MB，纳入 git 跟踪，供离线对账/复现）
API_CACHE_DIR = os.path.join(SAVE_ROOT, "api_cache")


def _env_int(name, default):
    """读取 int 型环境变量（非法值回落默认）"""
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name, default):
    """读取 float 型环境变量（非法值回落默认）"""
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_bool(name, default):
    """读取 bool 型环境变量：1/true/yes/on 为真，0/false/no/off 为假"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# ---- API 客户端参数（§5.9.5）----
API_TIMEOUT = _env_int("API_TIMEOUT", 30)
API_MAX_WORKERS = _env_int("API_MAX_WORKERS", 8)
API_RETRIES = _env_int("API_RETRIES", 3)
API_BACKOFF = _env_float("API_BACKOFF", 1.5)
# 是否使用 api_cache/（True 时命中缓存不再请求；CLI --refresh-api-cache 可强制刷新）
API_CACHE = _env_bool("API_CACHE", True)

# ---- 资源 URL 预检参数（§5.4 / §5.9.3）----
PROBE_URL_CHECK = _env_bool("PROBE_URL_CHECK", True)
PROBE_URL_WORKERS = _env_int("PROBE_URL_WORKERS", 10)

# ---- 下载重试参数（§5.9.5）----
DOWNLOAD_RETRIES = _env_int("DOWNLOAD_RETRIES", 2)
DOWNLOAD_BACKOFF = _env_float("DOWNLOAD_BACKOFF", 2.0)

# ---- 引擎开关（§4.4）----
# api：纯 API（默认，零浏览器依赖）；selenium：旧 DOM 引擎；auto：API 优先，逐首失败才降级
CRAWL_ENGINE = os.environ.get("CRAWL_ENGINE", "api").strip().lower() or "api"
VALID_ENGINES = ("api", "selenium", "auto")
# auto 模式下记录级 DOM 降级开关（selenium 已安装时才生效）
USE_SELENIUM_FALLBACK = _env_bool("USE_SELENIUM_FALLBACK", False)

os.makedirs(SAVE_ROOT, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": BASE_URL
}
