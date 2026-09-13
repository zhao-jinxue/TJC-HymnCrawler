# crawler_core/api_client.py
# 官网 JSON API 统一客户端（v1）
#
# 设计要点（§4.3 / §5.1 / §5.9）：
#   - 列表接口每页固定 10 条 → 全量 474 首仅需 48 个请求（≈2.2 MB）；
#     列表记录 == 详情记录（仅详情多 prev_no/next_no），故默认路径不需要逐首请求详情。
#   - 所有请求带退避重试：429 / 5xx / 超时 / SSL / 连接重置 → 重试 API_RETRIES 次；
#     其它 4xx 视为「真缺失」，不重试（§5.9.2 L1）。
#   - 48 页响应落盘 `Hymn_Downloads/api_cache/page_XX.json`（纳入 git 跟踪），
#     二次运行命中缓存、不重复请求；refresh=True 强制刷新。
#   - 领域映射全部写成纯函数（便于离线单测）：to_song / to_dirname / to_audio_versions
#     / to_source_info / to_lyrics / to_metadata / to_db_record / to_probe_entry。
#   - 资源可用性状态机（§5.9.3）：不可用资源 **不进入期望文件集合**（不下载、不重试），
#     仅在 url=None + `_unavailable` 下划线键上留痕，从而对旧消费者（downloader）零破坏。
#
# 术语：no（诗歌编号，可能是 "51_b"）≠ seq（列表位置 1…474），二者 423/474 不相等。

import html
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3

from . import naming
from .config import (
    API_BACKOFF,
    API_CACHE,
    API_CACHE_DIR,
    API_CATEGORY_URL,
    API_HYMN_URL,
    API_LIST_URL,
    API_MAX_WORKERS,
    API_RETRIES,
    API_TIMEOUT,
    HEADERS,
)

urllib3.disable_warnings()

# 必填字段（缺失即触发记录级降级 / 跳过，绝不 KeyError，§5.9.2 L2）
REQUIRED_FIELDS = ("no", "name", "lyrics", "sheet_score_pdf_url", "num_score_pdf_url")

# 资源不可用原因（写入 `_unavailable`）
UNAVAILABLE_API_NULL = "api_null"          # API 返回 file_url=null（空记录，永不可用）
UNAVAILABLE_HTTP = "http_4xx"              # 4xx（非 429）→ 服务端真缺失
UNAVAILABLE_NETWORK = "network"            # 重试后仍失败（超时/5xx/SSL）→ 下次运行自动重试
UNAVAILABLE_SITE_REMOVED = "site_removed"  # 官网已下架但本地留档（历史用例：#349 换诗期；归档删除后不再触发）

# 分页本地缓存文件名（page_01.json …）
CACHE_FILE_FMT = "page_{:02d}.json"


# ================= 异常 =================

class ApiError(RuntimeError):
    """API 请求失败（含重试后仍失败）"""

    def __init__(self, message, status_code=None, url=None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class ApiUnavailableError(ApiError):
    """记录级失败（必填字段缺失 / 编号不匹配），应降级 DOM 或登记 failed"""


# ================= 基础请求 =================

def _is_retryable_status(status):
    """429 / 5xx 可重试；其它 4xx 为真缺失"""
    return status == 429 or 500 <= status < 600


def fetch_with_retry(url, params=None, retries=None, backoff=None, timeout=None, session=None):
    """GET（verify=False，站点自有证书环境）+ 指数退避重试

    Args:
        url: 请求地址
        params: 查询参数
        retries: 最大尝试次数（默认 config.API_RETRIES）
        backoff: 退避倍数（默认 config.API_BACKOFF，第 n 次等待 backoff**n 秒）
        timeout: 单次超时秒数（默认 config.API_TIMEOUT）
        session: 可选 requests.Session（复用连接）
    Returns:
        requests.Response（status_code == 200）
    Raises:
        ApiError: 非 200 或异常且重试耗尽（错误信息含异常类型名，便于定位）
    """
    retries = API_RETRIES if retries is None else retries
    backoff = API_BACKOFF if backoff is None else backoff
    timeout = API_TIMEOUT if timeout is None else timeout
    getter = (session or requests).get
    last_err = None
    last_status = None

    for attempt in range(max(1, retries)):
        try:
            # 目标站点为自有证书环境, 与 downloader/probe 保持一致刻意关闭校验
            resp = getter(url, params=params, headers=HEADERS, timeout=timeout, verify=False)  # nosec B501
            if resp.status_code == 200:
                return resp
            last_status = resp.status_code
            last_err = f"HTTP {resp.status_code}"
            if not _is_retryable_status(resp.status_code):
                break  # 4xx(非 429) 真缺失, 不重试
        except Exception as e:  # noqa: BLE001 - 网络/解析异常统一按可重试处理
            last_err = f"{type(e).__name__}: {e}"
            last_status = None
        if attempt < retries - 1:
            time.sleep(backoff ** (attempt + 1))

    raise ApiError(last_err or "unknown error", status_code=last_status, url=url)


def fetch_json(url, params=None, **kwargs):
    """GET 并解析 JSON；HTTP/解析失败抛 ApiError"""
    resp = fetch_with_retry(url, params=params, **kwargs)
    try:
        return resp.json()
    except Exception as e:
        raise ApiError(f"JSONDecodeError: {e}", status_code=resp.status_code, url=url) from e



# ================= 磁盘缓存（api_cache/） =================

def cache_path(page):
    """第 N 页缓存文件路径"""
    return os.path.join(API_CACHE_DIR, CACHE_FILE_FMT.format(int(page)))


def cache_get(page):
    """读取缓存页；不存在/损坏返回 None（损坏时静默重建，不抛异常）"""
    path = cache_path(page)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def cache_put(page, payload):
    """写入缓存页（覆盖）"""
    os.makedirs(API_CACHE_DIR, exist_ok=True)
    with open(cache_path(page), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def cache_pages():
    """已缓存的页号列表（升序）"""
    if not os.path.isdir(API_CACHE_DIR):
        return []
    pages = []
    for name in os.listdir(API_CACHE_DIR):
        m = re.fullmatch(r"page_(\d+)\.json", name)
        if m:
            pages.append(int(m.group(1)))
    return sorted(pages)


def cache_clear():
    """清空缓存目录中的分页文件，返回删除数量"""
    removed = 0
    for page in cache_pages():
        try:
            os.remove(cache_path(page))
            removed += 1
        except OSError:
            pass
    return removed


# ================= 分页 / 全量 =================

def fetch_page(page, use_cache=None, refresh=False):
    """拉取第 N 页原始响应（含 data/current_page/last_page/total）

    Args:
        use_cache: 是否使用磁盘缓存（默认 config.API_CACHE）
        refresh: True 时忽略缓存并写回最新响应
    """
    use_cache = API_CACHE if use_cache is None else use_cache
    if use_cache and not refresh:
        cached = cache_get(page)
        if cached is not None:
            return cached
    payload = fetch_json(API_LIST_URL, params={"page": page})
    if use_cache or refresh:
        cache_put(page, payload)
    return payload


def page_records(payload):
    """从分页响应中取出记录列表（结构异常时返回空列表，不抛异常）"""
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data
        return []
    if isinstance(payload, list):
        return payload
    return []


def fetch_all(workers=None, use_cache=None, refresh=False, progress=True):
    """一次拿全量诗歌记录（474 首 ≈ 5–10 s）

    先取第 1 页得到 last_page/total，再并发拉其余页；结果按接口顺序（即 seq 顺序）返回。
    记录数与接口 total 不一致时打印告警（站点改版早发现），但仍返回已取到的记录。
    """
    workers = API_MAX_WORKERS if workers is None else workers
    first = fetch_page(1, use_cache=use_cache, refresh=refresh)
    last_page = int((first or {}).get("last_page") or 1)
    total = int((first or {}).get("total") or 0)
    records = list(page_records(first))

    if last_page > 1:
        pages = list(range(2, last_page + 1))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(fetch_page, p, use_cache, refresh): p for p in pages}
            fetched = {}
            for i, fut in enumerate(as_completed(futures), 1):
                page = futures[fut]
                try:
                    fetched[page] = page_records(fut.result())
                except ApiError as e:
                    print(f"  ⚠️ 第 {page} 页失败（其余页继续）: {e}")
                    fetched[page] = []
                if progress and (i % 12 == 0 or i == len(pages)):
                    print(f"  ⏳ 列表页 {i}/{len(pages)}")
        for p in pages:
            records.extend(fetched.get(p, []))

    if total and len(records) != total:
        print(f"  ⚠️ 记录数({len(records)}) 与接口 total({total}) 不一致，请检查站点改版")
    return records


def iter_hymns(page_limit=None, workers=None, use_cache=None):
    """生成器：按顺序逐首 yield 记录（大清单场景按需消费）"""
    records = fetch_all(workers=workers, use_cache=use_cache, progress=False)
    limit = page_limit * 10 if page_limit else None
    for i, rec in enumerate(records):
        if limit is not None and i >= limit:
            break
        yield rec


def fetch_hymn(no, use_cache=None, retries=None, timeout=None):
    """详情接口：单首补抓（含 prev_no/next_no）；404 返回 None"""
    url = API_HYMN_URL.format(no)
    try:
        return fetch_json(url, retries=retries, timeout=timeout)
    except ApiError as e:
        if e.status_code == 404:
            return None
        raise


def fetch_hymns(nos, workers=None, use_cache=None):
    """批量补抓指定编号 → {no: record}（增量同步用；失败者缺席）"""
    workers = API_MAX_WORKERS if workers is None else workers
    out = {}
    nos = list(nos)
    if not nos:
        return out
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_hymn, no, use_cache): no for no in nos}
        for fut in as_completed(futures):
            no = futures[fut]
            try:
                rec = fut.result()
            except ApiError as e:
                print(f"  ⚠️ 详情 #{no} 失败: {e}")
                continue
            if rec:
                out[no] = rec
    return out


def fetch_category(cid):
    """分类详情；失败返回 None"""
    try:
        return fetch_json(API_CATEGORY_URL.format(cid))
    except ApiError:
        return None


# ================= 字段映射（纯函数） =================

def normalize_text(text):
    """统一换行（\\r\\n → \\n）并去除首尾空白"""
    if not text:
        return ""
    return str(text).replace("\r\n", "\n").replace("\r", "\n").strip()


def to_lyrics(rec):
    """API → (verses, chorus)：正歌取 lyrics[].text（逐节去空），副歌取 lyrics_chorus"""
    raw = rec.get("lyrics") or []
    verses = []
    if isinstance(raw, list):
        for item in raw:
            text = normalize_text(item.get("text") if isinstance(item, dict) else item)
            if text:
                verses.append(text)
    return verses, normalize_text(rec.get("lyrics_chorus"))


def _join_names(items):
    """[{name: ...}] → "甲、乙"；空/异常返回 "Unknown"（与 DOM 路径默认值一致）"""
    names = []
    if isinstance(items, list):
        for it in items:
            name = ""
            if isinstance(it, dict):
                name = (it.get("name") or "").strip()
            elif isinstance(it, str):
                name = it.strip()
            if name:
                names.append(name)
    return "、".join(names) if names else "Unknown"


_TAG_RE = re.compile(r"<[^>]+>")


def _text(value):
    """任意值 → 字符串（None → ""；非字符串强转，避免站点改版时 .strip() 崩栈）"""
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def to_source_info(rec):
    """history（HTML）→ 纯文本：实体反转义、<br>/段落转行、去标签（§5.1）

    DOM 路径（Selenium innerText）得到的就是这个形态，故 API 路径可与之互换。
    `history` 非字符串（站点改版）时强转，绝不抛 AttributeError。
    """
    text = _text(rec.get("history"))
    if not text.strip():
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>\s*<p[^>]*>", "\n", text)
    text = re.sub(r"(?i)</?(p|div|li|ul|ol|h[1-6])[^>]*>", "\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = [ln.strip() for ln in text.split("\n")]
    out = []
    for ln in lines:
        if not ln and out and not out[-1]:
            continue  # 连续空行折叠为一行
        out.append(ln)
    return "\n".join(out).strip()


def to_metadata(rec):
    """元数据视图（分类/标签/YouTube/更新时间等，用于报表与 api_raw 取值）"""
    cat = rec.get("category") or {}
    cat = cat if isinstance(cat, dict) else {}
    youtube = rec.get("youtube_urls") or []
    return {
        "hymn_number": _text(rec.get("no")).strip(),
        "title": _text(rec.get("name")).strip(),
        "lyricist": _join_names(rec.get("lyricists")),
        "composer": _join_names(rec.get("composers")),
        "category_id": cat.get("id") if isinstance(cat, dict) else None,
        "category_name": (cat.get("name") or "") if isinstance(cat, dict) else "",
        "tags": [t.get("name") if isinstance(t, dict) else t for t in (rec.get("tags") or [])],
        "youtube_urls": [y.get("url") for y in youtube if isinstance(y, dict) and y.get("url")],
        "updated_at": rec.get("updated_at") or "",
        "prev_no": rec.get("prev_no"),
        "next_no": rec.get("next_no"),
        "staff_pdf_url": rec.get("sheet_score_pdf_url") or "",
        "numbered_pdf_url": rec.get("num_score_pdf_url") or "",
    }


def to_song(rec, seq):
    """API 记录 → scanner 的 song 结构（url_map.txt 用）

    url 规则与历史一致：{BASE_URL}/hymn/{no}
    """
    from .config import BASE_URL

    no = _text(rec.get("no")).strip()
    return {
        "seq_num": f"{int(seq):03d}",
        "hymn_number": no,
        "title": _text(rec.get("name")).strip(),
        "url": f"{BASE_URL}/hymn/{no}",
    }


def to_dirname(seq, rec):
    """目录名（委托 naming：`{seq:03d}_{sanitize(no + name)}`）"""
    return naming.to_dirname(seq, _text(rec.get("no")), _text(rec.get("name")))


def validate_record(rec):
    """字段完整性自检 → 缺失/异常的必填字段列表（空列表即通过，§5.9.2 L2）"""
    problems = []
    if not isinstance(rec, dict):
        return ["record:not_dict"]
    for field in REQUIRED_FIELDS:
        value = rec.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            problems.append(field)
    lyrics = rec.get("lyrics")
    if lyrics is not None and not isinstance(lyrics, list):
        problems.append("lyrics:not_list")
    elif isinstance(lyrics, list) and not [x for x in lyrics if normalize_text(
            x.get("text") if isinstance(x, dict) else x)]:
        problems.append("lyrics:empty")
    return problems


def to_audio_versions(rec, local_dir=None, previous=None):
    """API audio_files → probe_report 的 audio_versions（§5.1 / §5.9.3 / §5.9.4）

    规则：
      - 版本名 = `audio_category.name` + 「版」（如 鋼琴 → 鋼琴版）；
      - `file_url` 为空 → `url=None` + `_unavailable=api_null`（不进期望集合）；
      - 后缀 `.mp4` → `.m4a`（同时落在 `filename`/`ext`，下游统计不漏项）；
      - 同名版本多条（实测仅 #305 有 2 条鋼琴）→ **最后一条为准**（与历史 Selenium
        逐按钮覆盖的行为一致），被覆盖者记入 `_duplicates` 保留线索；
      - `previous`（旧 probe 条目）中 API 已下架、而本地目录仍有文件的版本
        → `_site_removed` 留档（不计 `_unavailable`，避免误报，§10 新增待办）。

    Args:
        rec: API 记录
        local_dir: 该首本地目录（用于判断「已下架但留档」）；None 时跳过该判定
        previous: 旧 probe_report 条目的 audio_versions
    Returns:
        {版本名: {"url", "filename", "ext", ...下划线元信息}}
    """
    out = {}
    duplicates = []
    for item in rec.get("audio_files") or []:
        if not isinstance(item, dict):
            continue
        raw_cat = item.get("audio_category")
        cat = raw_cat if isinstance(raw_cat, dict) else {}
        ver = naming.audio_version_name(cat.get("name"))
        url = _text(item.get("file_url")).strip()
        if not url:
            if ver in out:
                duplicates.append({"version": ver, "url": None, "reason": "api_null"})
            out[ver] = {"url": None, "_url": None, "filename": None, "ext": None,
                        "_http_status": None, "_unavailable": UNAVAILABLE_API_NULL}
            continue
        filename = url.rstrip("/").split("/")[-1]
        raw_ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
        info = {"url": url, "filename": filename, "ext": naming.normalize_audio_ext(raw_ext)}
        if ver in out:
            duplicates.append({"version": ver, "url": out[ver].get("url"),
                               "reason": "duplicate_version"})
        out[ver] = info

    if duplicates:
        out["_duplicates"] = duplicates

    for ver, old in (previous or {}).items():
        if not naming.is_audio_version_key(ver) or ver in out:
            continue
        old_info = old if isinstance(old, dict) else {}
        ext = naming.normalize_audio_ext(old_info.get("ext") or "")
        target = naming.audio_name(rec.get("no"), ver, ext)
        if local_dir and os.path.exists(os.path.join(local_dir, target)):
            out[ver] = {
                "url": None,
                "_url": old_info.get("url"),
                "filename": old_info.get("filename"),
                "ext": ext,
                "_http_status": None,
                "_site_removed": True,
            }
    return out


# ================= 资源可用性（§5.9.3 状态机） =================

def is_available(info):
    """该资源是否应进入期望文件集合（有 url 即视为可用/待下载）"""
    return isinstance(info, dict) and bool(info.get("url"))


def unavailable_reason(info):
    """不可用原因（api_null / http_4xx / network / site_removed）；可用返回 None"""
    if not isinstance(info, dict):
        return UNAVAILABLE_NETWORK
    if info.get("_site_removed"):
        return UNAVAILABLE_SITE_REMOVED
    if is_available(info):
        return None
    return info.get("_unavailable") or UNAVAILABLE_NETWORK


def unavailable_items(entry):
    """汇总单条 probe 记录的不可用资源 → {标签: info}（含音频版本与 PDF）

    标签：音频用版本名（如 "人聲版"），PDF 用 "五线谱"/"简谱"；
    `_site_removed`（本地留档）也会返回，便于报表区分「真缺失」与「已下架留档」。
    """
    found = {}
    for ver, info in (entry.get("audio_versions") or {}).items():
        if not naming.is_audio_version_key(ver):
            continue
        reason = unavailable_reason(info)
        if reason:
            found[ver] = dict(info, _unavailable=reason) if isinstance(info, dict) else {}
    for kind, label, key in (("staff", "五线谱", "staff_pdf"), ("numbered", "简谱", "numbered_pdf")):
        if entry.get(key):
            continue
        status = (entry.get("_pdf_status") or {}).get(kind)
        if isinstance(status, dict) and status.get("_error"):
            found[label] = status
    return found


def count_unavailable(manifest):
    """统计 manifest 中的不可用资源条数（验收：恰 10 条 = 9 空记录 + #62 的 404）

    `_site_removed`（官网下架、本地留档）不计入，避免误报。
    """
    return sum(1 for entry in manifest for info in unavailable_items(entry).values()
               if not info.get("_site_removed"))


# ================= API 原始记录（DB v7 `api_raw`） =================

def api_raw_json(rec):
    """API 记录 → 入库 JSON 字符串（保留 history/分类/标签/YouTube/邻接编号）"""
    if not isinstance(rec, dict):
        return ""
    try:
        return json.dumps(rec, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""


def parse_api_raw(value):
    """api_raw（str/dict/None）→ dict（损坏或空返回 {}，绝不抛异常）"""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def api_field(value, path, default=None):
    """按点号路径取 api_raw 字段：api_field(row, "category.name")"""
    cur = parse_api_raw(value)
    for key in str(path).split("."):
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        elif isinstance(cur, list) and key.isdigit() and int(key) < len(cur):
            cur = cur[int(key)]
        else:
            return default
    return cur


def api_updated_at(value) -> str:
    """api_raw.updated_at（增量水位依据；无则空串；非字符串亦归一为字符串）"""
    return _text(api_field(value, "updated_at")).strip()


def api_category_name(value) -> str:
    """api_raw.category.name（繁体分类名；无则空串；非字符串亦归一为字符串）"""
    return _text(api_field(value, "category.name")).strip()


def api_youtube(value):
    """api_raw.youtube_urls → [url, ...]"""
    items = api_field(value, "youtube_urls", []) or []
    return [y.get("url") for y in items if isinstance(y, dict) and y.get("url")]


def api_tags(value):
    """api_raw.tags → [name, ...]"""
    items = api_field(value, "tags", []) or []
    return [t.get("name") if isinstance(t, dict) else t for t in items]


def is_newer(existing_raw, rec):
    """增量判定：API 记录的 updated_at 与库内 api_raw 不同且非空 → 需更新"""
    new = rec.get("updated_at") if isinstance(rec, dict) else ""
    return bool(new) and new != api_updated_at(existing_raw)


# ================= 产物映射（DB / probe_report） =================

def to_db_record(rec):
    """API 记录 → `db.save_to_db()` 的 hymn_data（键名与 DOM 路径完全一致 + api_raw）

    注意：
      - `verses` 固定补齐到 10 项、`verse_count` 取实际节数（>10 节截断并告警）；
      - 作者多值用「、」连接，空值回落 `Unknown`（`save_to_db` 已有「空值不覆盖旧值」
        守卫，故不会把库里已有的作者/源考冲成 Unknown/空串）。
    """
    no = _text(rec.get("no")).strip()
    verses, chorus = to_lyrics(rec)
    if len(verses) > 10:
        print(f"  ⚠️ #{no} 有 {len(verses)} 节歌词，超 10 节截断（疑似站点改版）")
    verses = list(verses[:10])
    return {
        "hymn_number": no,
        "title": _text(rec.get("name")).strip() or "Unknown",
        "lyricist": _join_names(rec.get("lyricists")),
        "composer": _join_names(rec.get("composers")),
        "source_info": to_source_info(rec),
        "verse_count": len(verses),
        "verses": verses + [""] * (10 - len(verses)),
        "chorus": chorus,
        "staff_img_path": "",
        "numbered_img_path": "",
        "audio_versions": {},
        "api_raw": api_raw_json(rec),
    }


def to_probe_entry(rec, previous=None, local_dir=None):
    """API 记录 → probe_report 条目（键名/结构与历史完全一致，可被 downloader 直接消费）

    `previous` 为同一首的旧 probe 条目（dict），用于识别「官网已下架但本地留档」的版本。
    """
    no = _text(rec.get("no")).strip()
    prev_av = (previous or {}).get("audio_versions") or {}
    return {
        "hymn_number": no,
        "title": _text(rec.get("name")).strip(),
        "staff_pdf": _text(rec.get("sheet_score_pdf_url")).strip() or None,
        "numbered_pdf": _text(rec.get("num_score_pdf_url")).strip() or None,
        "audio_versions": to_audio_versions(rec, local_dir=local_dir, previous=prev_av),
    }




