# crawler_core/sync.py
# 增量同步（P1，§5.7）：以 `api_raw.updated_at` 为水位，输出「API vs 本地 DB」差异报表
#
# 设计：
#   水位 W = 库内 api_raw.updated_at 的最大值；逐首比较 rec.updated_at：
#     - 库内无该编号            → new（新诗歌，需建目录/提取/探测）
#     - rec.updated_at != 库内值 → changed（歌词/元数据/资源可能变化）
#     - 相同                    → same（不动）
#     - 库内有而 API 无          → site_only（官网已下架，留档不动）
#   `run(apply=False)` 只打印报表（人工确认后再落库，符合 P1 验收 3）；
#   `run(apply=True)` 才写库，并返回「需补下载的资源」清单。

import json
import os
import sqlite3

from . import api_client
from .config import DB_PATH


def load_db_state():
    """库内状态 → {no: {"api_raw", "updated_at", "verse_count", "has_chorus", "audio"}}"""
    conn = sqlite3.connect(DB_PATH)
    try:
        state = {}
        for no, raw, vc, chorus, av in conn.execute(
                "SELECT hymn_number, api_raw, verse_count, chorus, audio_version_list "
                "FROM tjc_hymn"):
            state[no] = {
                "api_raw": raw or "",
                "updated_at": api_client.api_updated_at(raw),
                "verse_count": vc or 0,
                "has_chorus": bool((chorus or "").strip()),
                "audio": _loads_list(av),
            }
        return state
    finally:
        conn.close()


def _loads_list(value):
    """audio_version_list → [版本, ...]（脏数据返回 []）"""
    try:
        parsed = json.loads(value or "[]")
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def plan(records, db_state):
    """纯函数：比较 API 记录与库内状态 → 差异计划

    Returns:
        {"new": [no...], "changed": [{"no", "db", "api", "notes"}...],
         "same": [no...], "site_only": [no...], "watermark": W}
    """
    result = {"new": [], "changed": [], "same": [], "site_only": [], "watermark": ""}
    api_nos = set()

    for rec in records or []:
        no = str(rec.get("no") or "")
        if not no:
            continue
        api_nos.add(no)
        state = db_state.get(no)
        if state is None:
            result["new"].append(no)
            continue

        notes = []
        verses, chorus = api_client.to_lyrics(rec)
        if len(verses) != state["verse_count"]:
            notes.append(f"节数 {state['verse_count']}→{len(verses)}")
        if bool(chorus) != state["has_chorus"]:
            notes.append(f"副歌 {'有' if state['has_chorus'] else '无'}→{'有' if chorus else '无'}")

        if not state["api_raw"]:
            # 库内无 api_raw（DOM 时代数据）→ 至少补一次原始记录
            result["changed"].append({"no": no, "db": "", "api": rec.get("updated_at") or "",
                                      "notes": notes + ["缺少 api_raw"]})
        elif api_client.is_newer(state["api_raw"], rec):
            result["changed"].append({"no": no, "db": state["updated_at"],
                                      "api": rec.get("updated_at") or "", "notes": notes})
        else:
            result["same"].append(no)

    result["site_only"] = sorted(set(db_state) - api_nos)
    levels = [s["updated_at"] for s in db_state.values() if s["updated_at"]]
    result["watermark"] = max(levels) if levels else ""
    return result


def pending_downloads(records, db_state, probe_entries=None):
    """API 可用音频版本 - 库内已有版本 → 待下载清单 [{no, version, url, filename}]

    `probe_entries`（probe_report 条目，缺省自动读取）用于排除**预检判定不可用**的资源
    （如 #62 人聲版 404、9 条 file_url=null）——它们不属期望集合，不该进下载队列。
    """
    probe_map = {}
    if probe_entries is not None:
        probe_map = {e.get("hymn_number"): e for e in probe_entries if isinstance(e, dict)}
    else:
        # 注意：_load_probe_entries() 返回 list，必须按编号建索引；
        # 直接赋值会让下面的 probe_map.get(no) 在 list 上抛 AttributeError（pyright 曾报此类型错）
        probe_map = {e.get("hymn_number"): e for e in _load_probe_entries() if isinstance(e, dict)}

    pending = []
    for rec in records or []:
        no = str(rec.get("no") or "")
        have = set(db_state.get(no, {}).get("audio") or [])
        probed = (probe_map.get(no) or {}).get("audio_versions") or {}
        for ver, info in api_client.to_audio_versions(rec).items():
            if not api_client.is_available(info) or ver in have:
                continue
            probe_info = probed.get(ver)
            if probe_info is not None and not api_client.is_available(probe_info):
                continue  # 预检已判定不可用（4xx/空记录/已下架）→ 不下载、不重试
            pending.append({"no": no, "version": ver, "url": info["url"],
                            "filename": f"{no}_{ver}.{info.get('ext', 'm4a')}"})
    return pending


def _load_probe_entries() -> list:
    """读取 probe_report.json → [entry, ...]（缺失/损坏返回 []）"""
    from .config import PROBE_REPORT

    if not os.path.exists(PROBE_REPORT):
        return []
    try:
        with open(PROBE_REPORT, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []



def report(records=None, db_state=None, use_cache=None):
    """打印差异报表（不落库）→ 返回 {"plan": ..., "pending": [...]}"""
    records = api_client.fetch_all(use_cache=use_cache) if records is None else records
    db_state = load_db_state() if db_state is None else db_state
    diff = plan(records, db_state)
    pending = pending_downloads(records, db_state)

    print("\n" + "=" * 58)
    print("🔄 增量同步差异报表（API vs 本地 DB，未落库）")
    print("=" * 58)
    print(f"  API 记录: {len(records)} | DB 记录: {len(db_state)} | 水位 W = {diff['watermark'] or '—'}")
    print(f"  新增: {len(diff['new'])} | 变更: {len(diff['changed'])} | "
          f"未变: {len(diff['same'])} | 仅本地(官网已下架): {len(diff['site_only'])}")
    if diff["new"]:
        print(f"  新增编号: {diff['new'][:20]}{' …' if len(diff['new']) > 20 else ''}")
    if diff["changed"]:
        print("  变更明细（前 20 条）:")
        for item in diff["changed"][:20]:
            notes = f" | {'; '.join(item['notes'])}" if item["notes"] else ""
            print(f"    - #{item['no']} {item['db']} → {item['api']}{notes}")
    if diff["site_only"]:
        print(f"  仅本地编号（留档不删）: {diff['site_only']}")
    if pending:
        print(f"  待下载资源: {len(pending)} 个（示例 {pending[0]['filename']}）")
    return {"plan": diff, "pending": pending}


def run(apply=False, use_cache=None):
    """增量同步入口

    Args:
        apply: False 仅报表；True 落库（新增/变更 + 重建 hymn_category）
    Returns:
        {"plan": ..., "pending": [...], "updated": n}
    """
    from .db import rebuild_hymn_category, save_to_db

    records = api_client.fetch_all(use_cache=use_cache)
    db_state = load_db_state()
    result = report(records, db_state)
    diff = result["plan"]

    if not apply:
        print("\n  ℹ️ 当前为「仅报表」模式（未落库）；确认无误后再落库（run(apply=True)）。")
        result["updated"] = 0
        return result

    todo = set(diff["new"]) | {item["no"] for item in diff["changed"]}
    updated = 0
    for rec in records:
        no = str(rec.get("no") or "")
        if no not in todo:
            continue
        problems = api_client.validate_record(rec)
        if problems:
            print(f"  ⚠️ #{no} 字段异常 {problems}，跳过")
            continue
        save_to_db(api_client.to_db_record(rec))
        updated += 1
    print(f"\n💾 增量落库完成：更新 {updated} 首"
          f"（新增 {len(diff['new'])} / 变更 {len(diff['changed'])}）")

    rebuild_hymn_category(records)
    result["updated"] = updated
    return result
