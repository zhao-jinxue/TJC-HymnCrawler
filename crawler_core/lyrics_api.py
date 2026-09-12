# crawler_core/lyrics_api.py
# 歌词补全（v6）：从官网 JSON API 刷新正歌 + 副歌
#
# 背景（2026-09-12 修复）：
#   官网详情页每个「第N節」Tab 下依次有多个 .lyrics_box，第 1 个是本节的歌词，
#   其后为副歌（每节重复同一段）。旧版 extractor 在每个 Tab 中只取第一个
#   .lyrics_box 便 break，导致 270 首有副歌的诗歌副歌整段丢失（合计 559 行）。
#   官网 API（GET /api/hymn/{no}）本身把正歌与副歌分开：lyrics[].text 与 lyrics_chorus。
#   本模块以 API 为权威数据源，全量刷新 verse_1..10 + chorus，不依赖浏览器。
#
# 用法：
#   python -c "from crawler_core.lyrics_api import run; run()"
#   python -c "from crawler_core.lyrics_api import run; run(force=True, reset=True)"

import json
import os
import sqlite3
import time

import requests

from .config import API_HYMN_URL, DB_PATH, HEADERS, SAVE_ROOT
from .db import ensure_chorus_field

# 断点进度文件（记录已成功刷新的 hymn_number，被 .gitignore 忽略）
PROGRESS_FILE = os.path.join(SAVE_ROOT, "lyrics_progress.json")

# 进度落盘间隔（首）
PROGRESS_FLUSH_EVERY = 50

# 每首之间的间隔（秒）：限速，避免给官网造成压力
REQUEST_INTERVAL = 0.15


def normalize_text(text):
    """统一换行（\\r\\n → \\n）并去除首尾空白"""
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def fetch_hymn_lyrics(hymn_number, retries=3, timeout=20):
    """调用官网 API 获取单首诗歌歌词

    Args:
        hymn_number: 诗歌编号（如 "12" / "51_a"）
        retries: 失败重试次数
        timeout: 单次请求超时秒数
    Returns:
        dict: {"verses": [str, ...], "chorus": str, "error": str | None}
              失败时 verses 为空列表且 error 为原因描述
    """
    url = API_HYMN_URL.format(hymn_number)
    last_err = None
    for attempt in range(retries):
        try:
            # 目标站点为自有证书环境, 与 downloader/probe 保持一致刻意关闭校验
            resp = requests.get(url, headers=HEADERS, timeout=timeout, verify=False)  # nosec B501
            if resp.status_code != 200:
                last_err = f"HTTP {resp.status_code}"
            else:
                data = resp.json()
                verses = [normalize_text(item.get("text")) for item in (data.get("lyrics") or [])]
                verses = [v for v in verses if v]
                return {
                    "verses": verses,
                    "chorus": normalize_text(data.get("lyrics_chorus")),
                    "error": None,
                }
        except Exception as e:  # noqa: BLE001 - 网络/解析异常统一降级为错误描述
            last_err = f"{type(e).__name__}: {e}"
        if attempt < retries - 1:
            time.sleep(0.5 * (attempt + 1))
    return {"verses": [], "chorus": "", "error": last_err or "unknown error"}


def load_progress():
    """读取断点进度 -> set[hymn_number]；无文件或损坏返回空集"""
    if not os.path.exists(PROGRESS_FILE):
        return set()
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f).get("done", []))
    except Exception:  # noqa: BLE001 - 进度文件损坏时从头开始
        return set()


def save_progress(done_set):
    """持久化断点进度"""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"done": sorted(done_set)}, f, ensure_ascii=False, indent=2)


def clear_progress():
    """删除断点进度文件（全量重跑用）；不存在时静默"""
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)

def _update_row(cursor, hymn_number, verses, chorus):
    """更新单首歌词字段（不触碰资源路径 / 下载状态等字段）"""
    verses = list(verses[:10])
    verse_count = len(verses)
    verses += [""] * (10 - len(verses))
    cursor.execute(
        """
        UPDATE tjc_hymn SET verse_count = ?, chorus = ?,
            verse_1 = ?, verse_2 = ?, verse_3 = ?, verse_4 = ?, verse_5 = ?,
            verse_6 = ?, verse_7 = ?, verse_8 = ?, verse_9 = ?, verse_10 = ?,
            updated_at = datetime('now', 'localtime')
        WHERE hymn_number = ?
        """,
        [verse_count, chorus] + verses + [hymn_number],
    )
    return cursor.rowcount


def refresh_one(hymn_number):
    """按编号刷新单首歌词（API 优先）；返回 {"ok": bool, "error": str | None}"""
    result = fetch_hymn_lyrics(hymn_number)
    if not result["verses"]:
        return {"ok": False, "error": result["error"] or "API 无歌词"}

    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        ensure_chorus_field(c)
        _update_row(c, hymn_number, result["verses"], result["chorus"])
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "error": None}


def get_targets():
    """待刷新的诗歌编号列表（按 DB 主键顺序）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        return [r[0] for r in conn.execute("SELECT hymn_number FROM tjc_hymn ORDER BY id")]
    finally:
        conn.close()
def backfill(force=False, reset=False, limit=None):
    """全量/增量刷新歌词：逐首调用官网 API，写入 verse_1..10 + chorus

    Args:
        force: True 时忽略进度文件，全量刷新
        reset: True 时先删除进度文件
        limit: 仅处理前 N 首（调试用）
    Returns:
        {"updated": int, "skipped": int, "failed": [{"no":..., "error":...}]}
    """
    if reset:
        clear_progress()

    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        ensure_chorus_field(c)
        conn.commit()
    finally:
        conn.close()

    targets = get_targets()
    if limit:
        targets = targets[:limit]

    done = set() if force else load_progress()
    pending = [n for n in targets if n not in done]
    skipped = len(targets) - len(pending)

    if skipped:
        print(f"⏭️ 断点续跑：已跳过 {skipped} 首（进度文件 {PROGRESS_FILE}）")
        print("   如需全量重抓：run(force=True, reset=True)")

    total = len(pending)
    print(f"🎵 歌词 API 刷新：待处理 {total} 首")
    updated = 0
    failed = []
    pending_flush = 0
    start = time.time()

    for i, no in enumerate(pending):
        result = fetch_hymn_lyrics(no)
        if not result["verses"]:
            failed.append({"no": no, "error": result["error"] or "API 无歌词"})
            print(f"  [{i+1}/{total}] {no} ⚠️ {result['error']}")
        else:
            _write_one(no, result["verses"], result["chorus"])
            updated += 1
            done.add(no)
            pending_flush += 1
            if pending_flush >= PROGRESS_FLUSH_EVERY:
                save_progress(done)
                pending_flush = 0
            if (i + 1) % 25 == 0 or (i + 1) == total:
                elapsed = time.time() - start
                speed = (i + 1) / elapsed if elapsed > 0 else 0
                rem = (total - i - 1) / speed if speed > 0 else 0
                print(f"  [{i+1}/{total}] ✅ 已刷新 {updated} 首 | {speed:.1f}首/s | 预计剩余 {rem:.0f}s")
        time.sleep(REQUEST_INTERVAL)

    # 失败重试一轮（官网偶发 SSL/超时抖动）
    if failed:
        print(f"\n🔁 失败 {len(failed)} 首，重试一轮...")
        still_failed = []
        for item in failed:
            no = item["no"]
            result = fetch_hymn_lyrics(no, retries=4)
            if not result["verses"]:
                still_failed.append({"no": no, "error": result["error"] or "API 无歌词"})
                continue
            _write_one(no, result["verses"], result["chorus"])
            updated += 1
            done.add(no)
            time.sleep(REQUEST_INTERVAL)
        failed = still_failed

    if updated:
        save_progress(done)

    elapsed = time.time() - start
    print(f"\n🏁 歌词刷新完成：成功 {updated} | 跳过 {skipped} | 失败 {len(failed)} | 耗时 {elapsed:.1f}s")
    if failed:
        print("   ❌ 仍失败的编号：" + ", ".join(f"{f['no']}({f['error']})" for f in failed[:10]))
    return {"updated": updated, "skipped": skipped, "failed": failed}


def _write_one(hymn_number, verses, chorus):
    """写库：单首歌词 + 副歌"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        _update_row(c, hymn_number, verses, chorus)
        conn.commit()
    finally:
        conn.close()


def run(force=False, reset=False, limit=None):
    """菜单入口：刷新全部歌词并打印副歌覆盖统计"""
    result = backfill(force=force, reset=reset, limit=limit)
    show_lyrics_status()
    return result


def show_lyrics_status():
    """打印歌词 / 副歌覆盖统计"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        total = c.execute("SELECT COUNT(*) FROM tjc_hymn").fetchone()[0] or 0
        has_lyrics = c.execute("SELECT COUNT(*) FROM tjc_hymn WHERE verse_count > 0").fetchone()[0]
        has_chorus = c.execute(
            "SELECT COUNT(*) FROM tjc_hymn WHERE chorus != '' AND chorus IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    print(f"📊 歌词覆盖：有歌词 {has_lyrics}/{total} | 有副歌 {has_chorus}/{total}")
    return {"total": total, "has_lyrics": has_lyrics, "has_chorus": has_chorus}

