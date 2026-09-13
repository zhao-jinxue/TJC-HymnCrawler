# crawler_core/probe.py
# 资源探测（v2，2026-09-12 API 重构，§5.4/§5.9.3）
#
# 主路径（API）：
#   1 次列表接口拿全量 → PDF/音频 URL 由 API 直接给出（音频 0 次点击）→ URL 预检（HEAD，失败再 GET Range）
#   → 写出 probe_report.json（键名/结构与历史完全一致，downloader 零改动）。
#
# 与旧版的语义差异（关键）：
#   - 不可用资源（4xx / file_url=null / 重试后仍网络失败）**不进期望文件集合**：
#     其 url 置 None + `_unavailable` 标注 → downloader/verify 自然跳过，
#     于是 #62 从「永久 partial(3/4) + failed」归正为 `completed`（§5.9.3）；
#   - 「官网已下架但本地留档」记 `_site_removed`（不计 `_unavailable`，避免误报，如 #349）；
#   - 失败原因/HTTP 状态落盘（`_http_status`/`_error`），下次运行可区分「没试过」与「试过且 404」。
#
# 保底路径（Selenium）：`--engine selenium` 或 API 缺 audio_files 时逐首点击播放按钮
# （实现见 crawler_core/selenium_legacy/probe_audio.py）。

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3

from . import api_client, naming
from .config import (
    BASE_URL,
    CRAWL_ENGINE,
    HEADERS,
    MAP_FILE,
    PROBE_REPORT,
    PROBE_URL_CHECK,
    PROBE_URL_WORKERS,
    SAVE_ROOT,
)

urllib3.disable_warnings()

# URL 预检参数（§5.4：HEAD 失败再 GET Range，各重试 2 次）
URL_CHECK_RETRIES = 2
URL_CHECK_BACKOFF = 1.5
URL_CHECK_TIMEOUT = 10


# ================= 入口 =================

def run_probe(force=False, engine=None):
    """资源探测入口

    force=True: 重新探测；force=False: 若 probe_report.json 存在则交互确认是否重探
    """
    if os.path.exists(PROBE_REPORT) and not force:
        print(f"✅ probe_report.json 已存在（{os.path.getsize(PROBE_REPORT)//1024}KB）")
        while True:
            try:
                ans = input("  是否重新探测资源？[y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
                print()
            if ans in ("", "n", "no"):
                print("  使用现有 probe_report.json")
                return load_probe_report()
            if ans in ("y", "yes"):
                print("  开始重新探测...")
                break
            print("  请输入 Y 或 N")

    return _do_probe(engine=engine)


def load_probe_report():
    """加载已有的探测报告（缺失返回 []）"""
    if not os.path.exists(PROBE_REPORT):
        print("❌ probe_report.json 不存在")
        return []
    with open(PROBE_REPORT, 'r', encoding='utf-8') as f:
        return json.load(f)


# ================= 主流程 =================

def _do_probe(engine=None):
    """执行完整的资源探测流程（按引擎分发）"""
    engine = (engine or CRAWL_ENGINE or "api").strip().lower()
    if engine == "selenium":
        return _do_probe_legacy()
    try:
        return _do_probe_api()
    except api_client.ApiError as e:
        if engine == "auto":
            print(f"⚠️ API 探测失败（{e}）→ 降级 Selenium 保底")
            return _do_probe_legacy()
        raise


def _do_probe_api():
    """API 主路径：一次拿全量 → 构建清单 → URL 预检 → 落盘"""
    print(f"\n{'='*55}")
    print("📌 资源探测（官网 API：PDF/音频 URL 直出 + 可达性预检）")
    print(f"{'='*55}")

    start = time.time()
    recs = api_client.fetch_all()
    if not recs:
        print("❌ API 未返回任何记录")
        return []

    previous = {e["hymn_number"]: e for e in load_probe_report() if isinstance(e, dict)}
    dir_map = _dir_map()

    manifest = []
    for rec in recs:
        no = str(rec.get("no") or "")
        entry = api_client.to_probe_entry(rec, previous=previous.get(no),
                                          local_dir=_hymn_dir(no, dir_map))
        old = previous.get(no) or {}
        for key in ("download_status", "integrity_status"):
            if key in old:
                entry[key] = old[key]
        manifest.append(entry)

    stats = _apply_url_precheck(manifest)
    _write_report(manifest)

    elapsed = time.time() - start
    print(f"\n⏱️ 探测耗时 {elapsed:.1f}s（URL 预检 {stats['checked']} 个 | "
          f"可用 {stats['ok']} | 不可用 {stats['unavailable']}）")
    _print_summary(manifest)
    return manifest


def _do_probe_legacy():
    """Selenium 保底路径：PDF HEAD + 音频点击捕获（等价重构前行为）"""
    songs = _load_songs()
    if not songs:
        return []

    print(f"📂 已加载 {len(songs)} 首诗歌（Selenium 保底引擎）")
    songs = _probe_pdfs(songs)
    songs = _probe_audios_legacy(songs)

    manifest = [{
        "hymn_number": s["hymn_number"],
        "title": s["title"],
        "staff_pdf": s.get("staff_pdf"),
        "numbered_pdf": s.get("numbered_pdf"),
        "audio_versions": s.get("audio_versions", {})
    } for s in songs]

    _write_report(manifest)
    _print_summary(manifest)


def run_probe_missing(engine=None):
    """增量补探：仅处理「API 与本地清单的差集」（接口保留，语义按 §5.4 更新）

    逐首比对：API 给出的可用音频版本集合 vs probe_report 中的可用版本集合；
    有新增/变化者才重建条目并做 URL 预检，其余条目（含 download_status）原样保留。
    """
    if not os.path.exists(PROBE_REPORT):
        print("❌ probe_report.json 不存在，请先执行资源探测")
        return []

    report = load_probe_report()
    previous = {e["hymn_number"]: e for e in report if isinstance(e, dict)}
    try:
        recs = api_client.fetch_all()
    except api_client.ApiError as e:
        print(f"❌ 拉取 API 失败：{e}")
        return report

    dir_map = _dir_map()
    changed = []
    for rec in recs:
        no = str(rec.get("no") or "")
        entry = previous.get(no)
        if entry is None:
            continue
        old_available = {k for k, v in (entry.get("audio_versions") or {}).items()
                         if naming.is_audio_version_key(k) and api_client.is_available(v)}
        new_versions = api_client.to_audio_versions(
            rec, local_dir=_hymn_dir(no, dir_map), previous=entry.get("audio_versions"))
        new_available = {k for k, v in new_versions.items()
                         if naming.is_audio_version_key(k) and api_client.is_available(v)}
        if new_available != old_available:
            changed.append((rec, entry))

    if not changed:
        print("✅ 无需补探：API 与本地清单一致（音频版本集合无差异）")
        _print_summary(report)
        return report

    print(f"\n🔍 发现 {len(changed)} 首音频清单有差异，开始增量补探...")
    print(f"   {[r['no'] for r, _ in changed]}")

    fresh = []
    for rec, entry in changed:
        no = str(rec.get("no") or "")
        item = api_client.to_probe_entry(rec, previous=entry.get("audio_versions"),
                                        local_dir=_hymn_dir(no, dir_map))
        for key in ("download_status", "integrity_status"):
            if key in entry:
                item[key] = entry[key]
        fresh.append(item)

    stats = _apply_url_precheck(fresh)
    fresh_map = {e["hymn_number"]: e for e in fresh}
    for idx, old_entry in enumerate(report):
        if old_entry.get("hymn_number") in fresh_map:
            report[idx] = fresh_map[old_entry["hymn_number"]]

    _write_report(report)
    print(f"💾 probe_report.json 已更新（{stats['checked']} 个 URL 预检 | "
          f"不可用 {stats['unavailable']} 条）")
    _print_summary(report)
    return report


# ================= URL 预检（§5.9.3 状态机） =================

def check_url(url, retries=URL_CHECK_RETRIES, timeout=URL_CHECK_TIMEOUT):
    """URL 可达性预检：先 HEAD，非 200 再用 GET + `Range: bytes=0-0` 复测（各重试 retries 次）

    Returns:
        {"ok": bool, "_http_status": int|None, "_error": str|None}
    """
    if not url or not str(url).startswith("http"):
        return {"ok": False, "_http_status": None, "_error": "empty or invalid url"}

    attempts = max(1, retries)
    status = None
    err = None

    for i in range(attempts):
        try:
            resp = requests.head(url, headers=HEADERS, timeout=timeout,
                                 allow_redirects=True, verify=False)  # nosec B501
            status = resp.status_code
            if status == 200:
                return {"ok": True, "_http_status": 200, "_error": None}
            err = f"HTTP {status}"
            if not api_client._is_retryable_status(status):
                break  # 4xx（非 429）→ 交 GET Range 复测，避免 HEAD 不被支持的误判
        except Exception as e:  # noqa: BLE001 - 网络抖动按可重试处理
            err = f"{type(e).__name__}: {e}"
            status = None
        if i < attempts - 1:
            time.sleep(URL_CHECK_BACKOFF ** (i + 1))

    for i in range(attempts):
        try:
            resp = requests.get(url, headers={**HEADERS, "Range": "bytes=0-0"}, timeout=timeout,
                                allow_redirects=True, stream=True, verify=False)  # nosec B501
            status = resp.status_code
            resp.close()
            if status in (200, 206):
                return {"ok": True, "_http_status": status, "_error": None}
            err = f"HTTP {status}"
            if not api_client._is_retryable_status(status):
                break
        except Exception as e:  # noqa: BLE001 - 网络抖动按可重试处理
            err = f"{type(e).__name__}: {e}"
            status = None
        if i < attempts - 1:
            time.sleep(URL_CHECK_BACKOFF ** (i + 1))

    return {"ok": False, "_http_status": status, "_error": err}



def _classify_failure(result):
    """预检失败 → `_unavailable` 原因（4xx 为真缺失，其余视为网络问题可下次重试）"""
    status = result.get("_http_status")
    if status and 400 <= status < 500 and status != 429:
        return api_client.UNAVAILABLE_HTTP
    return api_client.UNAVAILABLE_NETWORK


def _apply_url_precheck(manifest):
    """对清单中所有「可用」URL 做预检，失败者从期望集合摘除（url=None + 标注）

    Returns:
        {"checked": n, "ok": n, "unavailable": n, "by_reason": {...}}
    """
    stats = {"checked": 0, "ok": 0, "unavailable": 0, "by_reason": {}}
    if not PROBE_URL_CHECK:
        print("   ℹ️ PROBE_URL_CHECK=False，跳过 URL 预检（全部按可用处理）")
        return stats

    tasks = []
    for entry in manifest:
        no = entry.get("hymn_number")
        for kind, key in (("staff", "staff_pdf"), ("numbered", "numbered_pdf")):
            if entry.get(key):
                tasks.append((no, "pdf", kind, entry[key]))
        for ver, info in (entry.get("audio_versions") or {}).items():
            if naming.is_audio_version_key(ver) and isinstance(info, dict) and info.get("url"):
                tasks.append((no, "audio", ver, info["url"]))

    if not tasks:
        _refresh_unavailable_summary(manifest)
        return stats

    print(f"\n🔎 URL 预检：{len(tasks)} 个资源（HEAD→GET Range 复测，{PROBE_URL_WORKERS} 线程）...")
    index = {e.get("hymn_number"): e for e in manifest}
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, PROBE_URL_WORKERS)) as pool:
        futures = {pool.submit(check_url, t[3]): t for t in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            no, kind, tag, _url = futures[fut]
            try:
                results[(no, kind, tag)] = fut.result()
            except Exception as e:  # noqa: BLE001 - 预检异常视为网络问题
                results[(no, kind, tag)] = {"ok": False, "_http_status": None,
                                            "_error": f"{type(e).__name__}: {e}"}
            if i % 200 == 0 or i == len(tasks):
                print(f"    ⏳ 预检 {i}/{len(tasks)}")

    for (no, kind, tag), result in results.items():
        entry = index.get(no)
        if entry is None:
            continue
        stats["checked"] += 1

        if kind == "pdf":
            url = entry.get(tag + "_pdf")
            if result["ok"]:
                entry.setdefault("_pdf_status", {})[tag] = result.get("_http_status") or 200
                stats["ok"] += 1
            else:
                entry.setdefault("_pdf_status", {})[tag] = {
                    "_http_status": result.get("_http_status"),
                    "_error": result.get("_error"),
                    "_url": url,
                }
                entry[tag + "_pdf"] = None      # 摘出期望集合（downloader/verify 自动跳过）
                stats["unavailable"] += 1
                reason = _classify_failure(result)
                stats["by_reason"][reason] = stats["by_reason"].get(reason, 0) + 1
            continue

        info = (entry.get("audio_versions") or {}).get(tag)
        if not isinstance(info, dict):
            continue
        if result["ok"]:
            info["_http_status"] = result.get("_http_status") or 200
            stats["ok"] += 1
        else:
            info["_url"] = info.get("url")
            info["url"] = None
            info["_http_status"] = result.get("_http_status")
            info["_error"] = result.get("_error")
            info["_unavailable"] = _classify_failure(result)
            stats["unavailable"] += 1
            reason = info["_unavailable"]
            stats["by_reason"][reason] = stats["by_reason"].get(reason, 0) + 1

    _refresh_unavailable_summary(manifest)
    return stats


def _refresh_unavailable_summary(manifest):
    """写入/清理每条记录的顶层 `_unavailable` 汇总（verify.py 数据驱动归档用）"""
    for entry in manifest:
        unavailable = api_client.unavailable_items(entry)
        if unavailable:
            entry["_unavailable"] = unavailable
        elif "_unavailable" in entry:
            del entry["_unavailable"]



# ================= 本地目录映射 =================

def _dir_map():
    """url_map.txt → {hymn_number: 目录名}"""
    mapping = {}
    if not os.path.exists(MAP_FILE):
        return mapping
    with open(MAP_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split('|')
            if len(parts) >= 3:
                url = parts[2]
                no = url.strip('/').split('/')[-1].split('?')[0]
                mapping[no] = parts[1]
    return mapping


def _hymn_dir(hymn_number, dir_map=None):
    """该首的本地目录绝对路径（无映射/目录不存在返回 None）"""
    dir_name = (dir_map if dir_map is not None else _dir_map()).get(hymn_number)
    if not dir_name:
        return None
    path = os.path.join(SAVE_ROOT, dir_name)
    return path if os.path.isdir(path) else None


def _write_report(manifest):
    """写 probe_report.json（键名/结构与历史一致）"""
    with open(PROBE_REPORT, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"💾 资源清单已保存: {PROBE_REPORT}")


# ================= 保底路径（Selenium） =================

def _load_songs():
    """从 url_map.txt 加载诗歌"""
    songs = []
    if not os.path.exists(MAP_FILE):
        print(f"❌ url_map.txt 不存在: {MAP_FILE}")
        return songs
    with open(MAP_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            parts = line.strip().split('|')
            if len(parts) >= 3:
                url = parts[2]
                h = url.strip('/').split('/')[-1].split('?')[0]
                songs.append({
                    "hymn_number": h,
                    "title": parts[1],
                    "url": url
                })
    return songs


def _probe_pdfs(songs):
    """多线程 PDF 探测（保底路径：URL 由编号推导，仍用 HEAD 校验）"""
    print("\n📌 [PDF] 多线程验证乐谱 PDF...")

    def head_ok(url):
        try:
            return requests.head(url, timeout=5, allow_redirects=True,
                                 verify=False).status_code == 200  # nosec B501
        except Exception:  # noqa: BLE001 - 网络异常视为探测失败
            return False

    staff_ok, num_ok = {}, {}
    tasks = []
    for s in songs:
        h = s["hymn_number"]
        tasks.append(("staff", h, f"{BASE_URL}/storage/uploads/hymn/score/sheet/{h}.pdf"))
        tasks.append(("num", h, f"{BASE_URL}/storage/uploads/hymn/score/num/{h}.pdf"))

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(lambda t=t: (t[0], t[1], t[2], head_ok(t[2]))): t for t in tasks}
        for f in as_completed(futures):
            kind, h, url, ok = f.result()
            if ok:
                if kind == "staff":
                    staff_ok[h] = url
                else:
                    num_ok[h] = url

    print(f"  ✅ PDF: {len(staff_ok)} 五线谱 + {len(num_ok)} 简谱")
    for s in songs:
        h = s["hymn_number"]
        s["staff_pdf"] = staff_ok.get(h)
        s["numbered_pdf"] = num_ok.get(h)
    return songs


def _probe_audios_legacy(songs, max_workers=4):
    """音频捕获（保底引擎，转发 selenium_legacy/probe_audio.py）"""
    from .selenium_legacy.probe_audio import probe_audios_legacy

    return probe_audios_legacy(songs, max_workers=max_workers)


def _capture_song_audio_legacy(song):
    """单首音频兜底（API 无 audio_files 时调用；转发保底引擎）"""
    from .selenium_legacy.probe_audio import probe_one_audio

    return probe_one_audio(song)


# ================= 摘要 =================

def _print_summary(manifest):
    """打印探测报告摘要（含不可用资源分布）"""
    total = len(manifest)
    staff = sum(1 for m in manifest if m.get("staff_pdf"))
    num = sum(1 for m in manifest if m.get("numbered_pdf"))
    audio = sum(1 for m in manifest if m.get("audio_versions"))

    vcount = {}
    audio_total = 0
    for m in manifest:
        for v, info in (m.get("audio_versions") or {}).items():
            if not naming.is_audio_version_key(v):
                continue
            vcount[v] = vcount.get(v, 0) + 1
            if api_client.is_available(info):
                audio_total += 1

    print(f"\n{'='*55}")
    print("📊 资源探测统计")
    print(f"{'='*55}")
    print(f"  总数:    {total} 首")
    print(f"  五线谱:  {staff}/{total} ({100*staff//max(total,1)}%)")
    print(f"  简谱:    {num}/{total} ({100*num//max(total,1)}%)")
    print(f"  有音频:  {audio}/{total} ({100*audio//max(total,1)}%) | 可用音频条目 {audio_total}")
    if vcount:
        print("  音频版本分布:")
        for v, cnt in sorted(vcount.items(), key=lambda x: -x[1]):
            print(f"    {v}: {cnt} ({100*cnt//max(total,1)}%)")

    unavailable = api_client.count_unavailable(manifest)
    if unavailable:
        print(f"  不可用资源: {unavailable} 条（已摘出期望集合，详见 _unavailable 字段）")
    site_removed = sum(1 for m in manifest
                       for info in api_client.unavailable_items(m).values()
                       if info.get("_site_removed"))
    if site_removed:
        print(f"  官网已下架但本地留档（site_removed）: {site_removed} 条")
    return {"total": total, "staff": staff, "numbered": num, "audio": audio,
            "audio_available": audio_total, "unavailable": unavailable}

    return manifest
