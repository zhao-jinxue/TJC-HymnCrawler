# crawler_core/db.py
# 数据库管理 - 含迁移与初始化
# v4: +download_status +integrity_status
# v5: +staff_png_path +numbered_png_path（图片转 PNG 的保存路径）
# v6: +chorus（副歌；官网 API lyrics_chorus，此前因采集缺陷整段丢失）
# v8: +hymn_jianpu / hymn_jianpu_line（PPT 带简谱文字歌词，独立两表，不做 ALTER）

import json
import os
import sqlite3

from .config import DB_PATH, PROBE_REPORT, SAVE_ROOT


def init_db():
    """初始化数据库，迁移到最新结构（v7: api_raw + chorus 副歌字段）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()

        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tjc_hymn'")
        has_table = c.fetchone()

        if has_table:
            c.execute("PRAGMA table_info(tjc_hymn)")
            columns = {col[1] for col in c.fetchall()}

            if "piano_audio_path" in columns and "audio_versions" not in columns:
                _migrate_v1_to_v4(c, columns)
            elif "audio_versions" in columns and "audio_version_list" not in columns:
                _migrate_v2_to_v4(c)
            else:
                _migrate_v3_to_v4(c)
            # 所有迁移路径统一幂等补 PNG 图片字段（v5）
            _migrate_v5_png_fields(c)
            # 幂等补副歌字段（v6）
            ensure_chorus_field(c)
            # 幂等补 API 原始记录字段（v7）
            ensure_v7_fields(c)
            # 幂等创建带简谱文字歌词两表（v8；独立于 tjc_hymn，不做 ALTER）
            ensure_jianpu_tables(c)
            _backfill_from_probe(c, conn)
            print("📊 数据库结构已是最新版（v7 + 带简谱歌词表 v8）。")
        else:
            _create_table_v4(c)
            ensure_jianpu_tables(c)

        conn.commit()
    except Exception:
        conn.rollback()  # 迁移失败时回滚, 避免残留部分变更
        raise
    finally:
        conn.close()


# ================= 迁移函数 =================

def _migrate_v1_to_v4(c, columns):
    """v1（piano_audio_path + vocal_audio_path）→ v4"""
    print("📦 检测到 v1 旧版数据库，正在迁移到 v4...")
    conn = c.connection
    c.execute("ALTER TABLE tjc_hymn RENAME TO tjc_hymn_old")
    _create_table_v4(c)

    old_cols = [col for col in columns
                if col not in ("piano_audio_path", "vocal_audio_path",
                               "audio_versions", "audio_version_list",
                               "download_status", "integrity_status")]
    oc = ", ".join(old_cols)

    # 列名来自 PRAGMA 白名单过滤(非用户输入), SQL 值均参数化
    sql = f"""
        INSERT INTO tjc_hymn ({oc}, audio_versions, audio_version_list)
        SELECT {oc},
               CASE WHEN piano_audio_path != '' OR vocal_audio_path != ''
                    THEN json_object(
                        CASE WHEN piano_audio_path != '' THEN '钢琴版' ELSE NULL END,
                        json_object('url','','filename',piano_audio_path,'ext','m4a'),
                        CASE WHEN vocal_audio_path != '' THEN '人声版' ELSE NULL END,
                        json_object('url','','filename',vocal_audio_path,'ext','mp3')
                    )
                    ELSE '{{}}'
               END,
               CASE
                   WHEN piano_audio_path != '' AND vocal_audio_path != '' THEN '["钢琴版","人声版"]'
                   WHEN piano_audio_path != '' THEN '["钢琴版"]'
                   WHEN vocal_audio_path != '' THEN '["人声版"]'
                   ELSE '[]'
               END
        FROM tjc_hymn_old
    """
    c.execute(sql)  # nosec B608 - 列名来自 PRAGMA 白名单过滤(非用户输入), SQL 值均参数化
    c.execute("DROP TABLE tjc_hymn_old")
    conn.commit()

    _backfill_from_probe(c, conn)
    print("✅ v1 → v4 迁移完成。")


def _migrate_v2_to_v4(c):
    """v2（有 audio_versions 但缺 audio_version_list）→ v4"""
    print("📦 检测到 v2 数据库（缺 audio_version_list），正在升级到 v4...")
    conn = c.connection
    c.execute("ALTER TABLE tjc_hymn ADD COLUMN audio_version_list TEXT DEFAULT '[]'")

    c.execute("SELECT rowid, audio_versions FROM tjc_hymn WHERE audio_versions != '{}' AND audio_versions != ''")
    rows = c.fetchall()
    updated = 0
    for rowid, av_json in rows:
        try:
            av = json.loads(av_json) if av_json else {}
            if av:
                keys = json.dumps(list(av.keys()), ensure_ascii=False)
                c.execute("UPDATE tjc_hymn SET audio_version_list = ? WHERE rowid = ?", (keys, rowid))
                updated += 1
        except (json.JSONDecodeError, TypeError):  # 历史数据可能非 JSON, 跳过
            pass
    conn.commit()
    print(f"✅ 已为 {updated} 条记录生成 audio_version_list。")

    # 再加 download_status + integrity_status
    c.execute("ALTER TABLE tjc_hymn ADD COLUMN download_status TEXT DEFAULT 'pending'")
    c.execute("ALTER TABLE tjc_hymn ADD COLUMN integrity_status TEXT DEFAULT 'unchecked'")
    conn.commit()

    _backfill_from_probe(c, conn)
    print("✅ v2 → v4 升级完成。")


def _migrate_v3_to_v4(c):
    """v3（有 audio_versions + audio_version_list）→ v4（补添 download_status + integrity_status）"""
    c.execute("PRAGMA table_info(tjc_hymn)")
    columns = {col[1] for col in c.fetchall()}
    conn = c.connection
    altered = False

    if "download_status" not in columns:
        c.execute("ALTER TABLE tjc_hymn ADD COLUMN download_status TEXT DEFAULT 'pending'")
        altered = True
    if "integrity_status" not in columns:
        c.execute("ALTER TABLE tjc_hymn ADD COLUMN integrity_status TEXT DEFAULT 'unchecked'")
        altered = True

    if altered:
        conn.commit()
        print("📦 数据库新增 download_status + integrity_status 字段。")


def _migrate_v5_png_fields(c):
    """v5: 幂等补添 staff_png_path / numbered_png_path（图片转 PNG 的保存路径）"""
    c.execute("PRAGMA table_info(tjc_hymn)")
    columns = {col[1] for col in c.fetchall()}
    conn = c.connection
    altered = False

    if "staff_png_path" not in columns:
        c.execute("ALTER TABLE tjc_hymn ADD COLUMN staff_png_path TEXT")
        altered = True
    if "numbered_png_path" not in columns:
        c.execute("ALTER TABLE tjc_hymn ADD COLUMN numbered_png_path TEXT")
        altered = True

    if altered:
        conn.commit()
        print("📦 数据库新增 staff_png_path + numbered_png_path 字段（v5）。")


def ensure_chorus_field(c):
    """v6: 幂等补添 chorus（副歌）字段

    官网数据模型把正歌（lyrics[]）与副歌（lyrics_chorus）分开存放，
    历史采集只取了正歌的第一个片段，副歌整段丢失；副歌独立成列后不再混入 verse_*。
    """
    c.execute("PRAGMA table_info(tjc_hymn)")
    columns = {col[1] for col in c.fetchall()}
    if "chorus" not in columns:
        c.execute("ALTER TABLE tjc_hymn ADD COLUMN chorus TEXT DEFAULT ''")
        c.connection.commit()
        print("📦 数据库新增 chorus 字段（v6，副歌）。")
        return True
    return False


def ensure_v7_fields(c):
    """v7: 幂等补添 `api_raw`（API 原始记录 JSON）字段

    只加这一列（决策 ⑤）：分类/标签/YouTube/updated_at/prev_no/next_no/history HTML
    等官网 API 独有信息全部塞进 JSON，现有列语义不变 → 旧代码与旧查询零影响。
    读取侧统一用 `api_client.api_field(value, "category.name")` 等取值助手。
    """
    c.execute("PRAGMA table_info(tjc_hymn)")
    columns = {col[1] for col in c.fetchall()}
    if "api_raw" not in columns:
        c.execute("ALTER TABLE tjc_hymn ADD COLUMN api_raw TEXT DEFAULT ''")
        c.connection.commit()
        print("📦 数据库新增 api_raw 字段（v7，API 原始记录 JSON）。")
        return True
    return False


def _backfill_from_probe(c, conn):
    """从 probe_report.json 回填 audio_versions / audio_version_list / download_status"""
    pr_path = PROBE_REPORT
    if not os.path.exists(pr_path):
        return

    with open(pr_path, 'r', encoding='utf-8') as f:
        report = json.load(f)

    probe_map = {}
    for entry in report:
        av = entry.get("audio_versions", {})
        ds = entry.get("download_status", "pending")
        if av:
            probe_map[entry["hymn_number"]] = {
                "av": av,
                "download_status": ds
            }

    if not probe_map:
        return

    # 回填 audio_versions（仅空记录）
    c.execute("SELECT hymn_number FROM tjc_hymn WHERE audio_versions = '{}' OR audio_versions = ''")
    empty_rows = c.fetchall()
    filled = 0
    for (hnum,) in empty_rows:
        if hnum in probe_map:
            av = probe_map[hnum]["av"]
            av_json = json.dumps(av, ensure_ascii=False)
            vl_json = json.dumps(list(av.keys()), ensure_ascii=False)
            c.execute("UPDATE tjc_hymn SET audio_versions = ?, audio_version_list = ? WHERE hymn_number = ?",
                      (av_json, vl_json, hnum))
            filled += 1

    if filled > 0:
        conn.commit()
        print(f"📌 从 probe_report.json 回填了 {filled} 首诗歌的音频信息。")

    # 回填 download_status
    c.execute("SELECT hymn_number FROM tjc_hymn WHERE download_status = 'pending' OR download_status IS NULL")
    pending_rows = c.fetchall()
    updated_ds = 0
    for (hnum,) in pending_rows:
        if hnum in probe_map:
            ds_val = probe_map[hnum]["download_status"]
            if ds_val != "pending":
                c.execute("UPDATE tjc_hymn SET download_status = ? WHERE hymn_number = ?", (ds_val, hnum))
                updated_ds += 1

    if updated_ds > 0:
        conn.commit()
        print(f"📌 回填了 {updated_ds} 首诗歌的 download_status。")


# ================= 建表 =================

def _create_table_v4(c):
    """创建 v7 版 tjc_hymn 表（v4 字段 + PNG 图片路径 + chorus 副歌 + api_raw 原始记录）"""
    c.execute('''CREATE TABLE IF NOT EXISTS tjc_hymn (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hymn_number TEXT UNIQUE NOT NULL,
                title TEXT,
                lyricist TEXT DEFAULT 'Unknown',
                composer TEXT DEFAULT 'Unknown',
                source_info TEXT,
                verse_count INTEGER DEFAULT 0,
                verse_1 TEXT DEFAULT '',
                verse_2 TEXT DEFAULT '',
                verse_3 TEXT DEFAULT '',
                verse_4 TEXT DEFAULT '',
                verse_5 TEXT DEFAULT '',
                verse_6 TEXT DEFAULT '',
                verse_7 TEXT DEFAULT '',
                verse_8 TEXT DEFAULT '',
                verse_9 TEXT DEFAULT '',
                verse_10 TEXT DEFAULT '',
                chorus TEXT DEFAULT '',
                staff_img_path TEXT,
                numbered_img_path TEXT,
                staff_png_path TEXT,
                numbered_png_path TEXT,
                audio_versions TEXT DEFAULT '{}',
                audio_version_list TEXT DEFAULT '[]',
                api_raw TEXT DEFAULT '',
                download_status TEXT DEFAULT 'pending',
                integrity_status TEXT DEFAULT 'unchecked',
                updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
            )''')


# ================= 数据写入 =================

def save_to_db(hymn_data):
    """保存单首数据到数据库 (UPSERT)，自动维护 audio_version_list

    v7：新增 `api_raw`（API 原始记录 JSON）写入；title/作者/源考同样遵循
    「空值不覆盖旧值」——API 侧缺失（如 #25/#31/#66/#299 无 lyricists、#349 无 history）
    时保留库内既有值，避免文本回退成 Unknown/空串。
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        ensure_chorus_field(c)  # 幂等确保 v6 字段存在
        ensure_v7_fields(c)     # 幂等确保 v7 字段存在

        av = hymn_data.get("audio_versions", {})
        audio_json = json.dumps(av, ensure_ascii=False)
        version_list_json = json.dumps(list(av.keys()), ensure_ascii=False)
        ds = hymn_data.get("download_status", "pending")
        ins = hymn_data.get("integrity_status", "unchecked")
        api_raw = hymn_data.get("api_raw", "") or ""

        sql = '''INSERT INTO tjc_hymn
                 (hymn_number, title, lyricist, composer, source_info, verse_count,
                  verse_1, verse_2, verse_3, verse_4, verse_5,
                  verse_6, verse_7, verse_8, verse_9, verse_10, chorus,
                  staff_img_path, numbered_img_path,
                  audio_versions, audio_version_list,
                  download_status, integrity_status, api_raw)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(hymn_number) DO UPDATE SET
                    -- 文本字段：新值为空/Unknown 时保留旧值（API 缺失不覆盖既有成果）
                    title = CASE WHEN excluded.title IS NULL OR excluded.title IN ('', 'Unknown')
                                 THEN tjc_hymn.title ELSE excluded.title END,
                    lyricist = CASE WHEN excluded.lyricist IS NULL OR excluded.lyricist IN ('', 'Unknown')
                                    THEN tjc_hymn.lyricist ELSE excluded.lyricist END,
                    composer = CASE WHEN excluded.composer IS NULL OR excluded.composer IN ('', 'Unknown')
                                    THEN tjc_hymn.composer ELSE excluded.composer END,
                    source_info = CASE WHEN excluded.source_info IS NULL OR excluded.source_info = ''
                                       THEN tjc_hymn.source_info ELSE excluded.source_info END,
                    verse_count = excluded.verse_count,
                    verse_1 = excluded.verse_1, verse_2 = excluded.verse_2,
                    verse_3 = excluded.verse_3, verse_4 = excluded.verse_4,
                    verse_5 = excluded.verse_5, verse_6 = excluded.verse_6,
                    verse_7 = excluded.verse_7, verse_8 = excluded.verse_8,
                    verse_9 = excluded.verse_9, verse_10 = excluded.verse_10,
                    -- 副歌：新值为空时保留旧值（无副歌的诗歌不得清掉已抓取的副歌）
                    chorus = CASE WHEN excluded.chorus IS NULL OR excluded.chorus = ''
                                  THEN tjc_hymn.chorus ELSE excluded.chorus END,
                    -- 路径/状态类字段：新值为空时保留旧值（防止文本提取覆盖已回写的资源路径）
                    staff_img_path = CASE WHEN excluded.staff_img_path IS NULL OR excluded.staff_img_path = ''
                                          THEN tjc_hymn.staff_img_path ELSE excluded.staff_img_path END,
                    numbered_img_path = CASE WHEN excluded.numbered_img_path IS NULL OR excluded.numbered_img_path = ''
                                             THEN tjc_hymn.numbered_img_path ELSE excluded.numbered_img_path END,
                    audio_versions = CASE WHEN excluded.audio_versions IS NULL OR excluded.audio_versions IN ('', '{}')
                                          THEN tjc_hymn.audio_versions ELSE excluded.audio_versions END,
                    audio_version_list = CASE WHEN excluded.audio_version_list IS NULL OR excluded.audio_version_list IN ('', '[]')
                                              THEN tjc_hymn.audio_version_list ELSE excluded.audio_version_list END,
                    -- 状态字段同理：Step 2 文本提取的默认 pending/unchecked 不得覆盖下载/校验结果
                    download_status = CASE WHEN excluded.download_status IS NULL OR excluded.download_status IN ('', 'pending')
                                           THEN tjc_hymn.download_status ELSE excluded.download_status END,
                    integrity_status = CASE WHEN excluded.integrity_status IS NULL OR excluded.integrity_status IN ('', 'unchecked')
                                            THEN tjc_hymn.integrity_status ELSE excluded.integrity_status END,
                    -- API 原始记录：空值时保留旧值（DOM 保底路径不带 api_raw）
                    api_raw = CASE WHEN excluded.api_raw IS NULL OR excluded.api_raw = ''
                                   THEN tjc_hymn.api_raw ELSE excluded.api_raw END,
                    updated_at = datetime('now', 'localtime')
                '''
        params = [
            hymn_data["hymn_number"], hymn_data["title"],
            hymn_data["lyricist"], hymn_data["composer"],
            hymn_data["source_info"], hymn_data["verse_count"]
        ]
        params.extend(hymn_data["verses"])
        params.append(hymn_data.get("chorus", "") or "")
        params.extend([hymn_data["staff_img_path"], hymn_data["numbered_img_path"],
                       audio_json, version_list_json, ds, ins, api_raw])
        c.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


# ================= 状态同步 =================

def sync_download_status_to_db(probe_report):
    """将 probe_report.json 中的 download_status 同步至数据库"""
    if not probe_report:
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    updated = 0
    for m in probe_report:
        h = m["hymn_number"]
        ds = m.get("download_status", "pending")
        c.execute("UPDATE tjc_hymn SET download_status = ?, updated_at = datetime('now', 'localtime') WHERE hymn_number = ?",
                  (ds, h))
        if c.rowcount > 0:
            updated += 1
    conn.commit()
    conn.close()
    if updated > 0:
        print(f"📌 同步 {updated} 首 download_status 到数据库。")


def update_integrity_status(hymn_number, integrity_status, probe_report_obj=None):
    """更新单首诗歌的完整性状态，同步到数据库和 probe_report.json

    Args:
        hymn_number: 诗歌编号
        integrity_status: 状态值（passed / failed / unchecked）
        probe_report_obj: 可选，当前已加载的 probe_report 对象
    Returns:
        dict 或 None: 如果传入了 probe_report_obj，返回更新后的对象
    """
    # 更新数据库
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE tjc_hymn SET integrity_status = ?, updated_at = datetime('now', 'localtime') WHERE hymn_number = ?",
        (integrity_status, hymn_number)
    )
    conn.commit()
    conn.close()

    # 更新 probe_report.json（如果传入了对象则直接修改，否则读写文件）
    pr_path = PROBE_REPORT
    if probe_report_obj is not None:
        for entry in probe_report_obj:
            if entry.get("hymn_number") == hymn_number:
                entry["integrity_status"] = integrity_status
                break
        return probe_report_obj
    elif os.path.exists(pr_path):
        with open(pr_path, 'r', encoding='utf-8') as f:
            report = json.load(f)
        for entry in report:
            if entry.get("hymn_number") == hymn_number:
                entry["integrity_status"] = integrity_status
                break
        with open(pr_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    return None


def batch_update_integrity(integrity_map, probe_report_obj=None):
    """批量更新完整性状态

    Args:
        integrity_map: dict，{hymn_number: integrity_status}
        probe_report_obj: 可选，当前已加载的 probe_report 对象
    Returns:
        如果传入了 probe_report_obj，返回更新后的完整对象
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    updated = 0
    for hnum, status in integrity_map.items():
        c.execute(
            "UPDATE tjc_hymn SET integrity_status = ?, updated_at = datetime('now', 'localtime') WHERE hymn_number = ?",
            (status, hnum)
        )
        if c.rowcount > 0:
            updated += 1
    conn.commit()
    conn.close()

    # 同步到 probe_report.json
    pr_path = PROBE_REPORT
    if probe_report_obj is not None:
        status_map = {e.get("hymn_number"): e for e in probe_report_obj}
        for hnum, status in integrity_map.items():
            if hnum in status_map:
                status_map[hnum]["integrity_status"] = status
        return probe_report_obj
    elif os.path.exists(pr_path):
        with open(pr_path, 'r', encoding='utf-8') as f:
            report = json.load(f)
        entry_map = {e.get("hymn_number"): e for e in report}
        for hnum, status in integrity_map.items():
            if hnum in entry_map:
                entry_map[hnum]["integrity_status"] = status
        with open(pr_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    return updated


# ================= 查询 =================

def get_failed_songs():
    """从数据库获取提取失败的诗歌列表"""
    from .config import BASE_URL, MAP_FILE

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("SELECT hymn_number, title FROM tjc_hymn WHERE verse_count = 0")
        failed_records = c.fetchall()
    except Exception:  # noqa: BLE001 - 表未初始化时视为无失败记录
        failed_records = []
    finally:
        conn.close()

    if not failed_records:
        return []

    url_map = {}
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split('|')
                if len(parts) >= 3:
                    h = parts[2].strip('/').split('/')[-1].split('?')[0]
                    url_map[h] = parts[2]

    songs = []
    for hymn_number, title in failed_records:
        url = url_map.get(hymn_number, f"{BASE_URL}/hymn/{hymn_number}")
        songs.append({
            "seq_num": "",
            "hymn_number": hymn_number,
            "title": title,
            "url": url
        })
    return songs


def count_failed():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("SELECT COUNT(*) FROM tjc_hymn WHERE verse_count = 0")
        return c.fetchone()[0]
    except Exception:  # noqa: BLE001 - 表未初始化时返回 0
        return 0
    finally:
        conn.close()


# ================= 打印状态 =================

def rebuild_hymn_category(records):
    """用 API 记录重建 `hymn_category` 整表（决策 ⑥；先建临时表再原子替换，失败回滚）

    表结构（§5.6）：id / name（繁体）/ slug / hymn_count / updated_at

    Args:
        records: `api_client.fetch_all()` 的全量记录（含 category 对象）
    Returns:
        {"categories": n, "hymns": n, "top": [(name, count), ...]}
    """
    stats = {}
    for rec in records or []:
        cat = rec.get("category") if isinstance(rec, dict) else None
        if not isinstance(cat, dict):
            continue
        cid = cat.get("id")
        name = (cat.get("name") or "").strip()
        if cid is None or not name:
            continue
        item = stats.setdefault(cid, {
            "id": int(cid), "name": name, "slug": (cat.get("slug") or ""),
            "hymn_count": 0, "updated_at": (cat.get("updated_at") or ""),
        })
        item["hymn_count"] += 1

    rows = [stats[cid] for cid in sorted(stats)]
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute("DROP TABLE IF EXISTS hymn_category_new")
        c.execute('''CREATE TABLE hymn_category_new (
                        id          INTEGER PRIMARY KEY,
                        name        TEXT NOT NULL,
                        slug        TEXT DEFAULT '',
                        hymn_count  INTEGER DEFAULT 0,
                        updated_at  TEXT DEFAULT ''
                    )''')
        c.executemany(
            "INSERT INTO hymn_category_new (id, name, slug, hymn_count, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [(r["id"], r["name"], r["slug"], r["hymn_count"], r["updated_at"]) for r in rows],
        )
        c.execute("DROP TABLE IF EXISTS hymn_category")
        c.execute("ALTER TABLE hymn_category_new RENAME TO hymn_category")
        conn.commit()
    except Exception:
        conn.rollback()  # 重建失败回滚, 保留旧表
        raise
    finally:
        conn.close()

    top = sorted(((r["name"], r["hymn_count"]) for r in rows), key=lambda x: -x[1])[:5]
    print(f"📚 hymn_category 已用 API 重建：{len(rows)} 类 / {sum(r['hymn_count'] for r in rows)} 首")
    print(f"   分布前 5: {', '.join(f'{n}({c})' for n, c in top)}")
    return {"categories": len(rows), "hymns": sum(r["hymn_count"] for r in rows), "top": top}


def load_hymn_category():
    """读取 hymn_category → [{id, name, slug, hymn_count, updated_at}]（表不存在返回 []）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT id, name, slug, hymn_count, updated_at FROM hymn_category ORDER BY id")
        cols = ("id", "name", "slug", "hymn_count", "updated_at")
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def api_raw_stats():
    """api_raw 覆盖情况 → {"rows": n, "latest": iso, "audio_total": n}"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        try:
            rows = c.execute("SELECT COUNT(*) FROM tjc_hymn WHERE api_raw != ''").fetchone()[0]
            latest = c.execute(
                "SELECT json_extract(api_raw, '$.updated_at') FROM tjc_hymn "
                "WHERE api_raw != '' ORDER BY json_extract(api_raw, '$.updated_at') DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:  # 未迁移 v7 时不报错
            return {"rows": 0, "latest": None, "audio_total": 0}
        audio_total = 0
        for (av,) in c.execute("SELECT audio_version_list FROM tjc_hymn"):
            try:
                audio_total += len(json.loads(av or "[]"))
            except (json.JSONDecodeError, TypeError):  # 历史脏数据忽略
                continue
        return {"rows": rows, "latest": latest[0] if latest else None, "audio_total": audio_total}
    finally:
        conn.close()


def print_db_status():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("SELECT COUNT(*) FROM tjc_hymn")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tjc_hymn WHERE verse_count > 0")
        has_lyrics = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tjc_hymn WHERE chorus != '' AND chorus IS NOT NULL")
        has_chorus = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tjc_hymn WHERE staff_img_path != ''")
        has_staff = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tjc_hymn WHERE numbered_img_path != ''")
        has_numbered = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tjc_hymn WHERE staff_png_path != ''")
        has_staff_png = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tjc_hymn WHERE numbered_png_path != ''")
        has_numbered_png = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tjc_hymn WHERE audio_version_list != '[]' AND audio_version_list != ''")
        has_audio = c.fetchone()[0]

        # ---- DB 路径 vs 磁盘文件交叉校验（v5 增强）----
        # 检查"数据库有记录，但记录指向的文件是否真实存在"（防悬挂引用）
        c.execute("SELECT staff_img_path, numbered_img_path, staff_png_path, numbered_png_path, audio_versions FROM tjc_hymn")
        missing_files = {
            "五线谱PDF": [], "简谱PDF": [], "五线谱图片": [], "简谱图片": [], "音频": []
        }
        for row in c.fetchall():
            staff_pdf, numbered_pdf, staff_png, numbered_png, av_json = row
            for field, key in (
                (staff_pdf, "五线谱PDF"),
                (numbered_pdf, "简谱PDF"),
                (staff_png, "五线谱图片"),
                (numbered_png, "简谱图片"),
            ):
                if field and not os.path.exists(field):
                    missing_files[key].append(field)
            if av_json:
                try:
                    av = json.loads(av_json)
                except (json.JSONDecodeError, TypeError):
                    av = {}
                for ver, rel in av.items():
                    if rel and not os.path.exists(rel):
                        missing_files["音频"].append(f"{ver}: {rel}")

        # 统计具体版本分布
        c.execute("SELECT audio_version_list FROM tjc_hymn WHERE audio_version_list != '[]'")
        version_counts = {}
        for (vl,) in c.fetchall():
            try:
                for v in json.loads(vl):
                    version_counts[v] = version_counts.get(v, 0) + 1
            except (json.JSONDecodeError, TypeError):  # 非 JSON 数据, 跳过该版本
                pass

        # 统计 download_status 分布
        c.execute("SELECT download_status, COUNT(*) FROM tjc_hymn GROUP BY download_status")
        ds_counts = dict(c.fetchall())

        # 统计 integrity_status 分布
        c.execute("SELECT integrity_status, COUNT(*) FROM tjc_hymn GROUP BY integrity_status")
        is_counts = dict(c.fetchall())

        print(f"📊 数据库状态（{DB_PATH}）：")
        print(f"   总记录: {total} 首")
        print(f"   有歌词: {has_lyrics}/{total}")
        print(f"   有副歌: {has_chorus}/{total}")
        print(f"   五线谱PDF: {has_staff}/{total} {'✅' if has_staff > 0 else '❌'}")
        print(f"   简谱PDF:   {has_numbered}/{total} {'✅' if has_numbered > 0 else '❌'}")
        print(f"   五线谱图片: {has_staff_png}/{total} {'✅' if has_staff_png > 0 else '❌'}")
        print(f"   简谱图片:   {has_numbered_png}/{total} {'✅' if has_numbered_png > 0 else '❌'}")
        print(f"   有音频: {has_audio}/{total} {'✅' if has_audio > 0 else '❌'}")
        if version_counts:
            print("   音频版本分布:")
            for v, cnt in sorted(version_counts.items(), key=lambda x: -x[1]):
                print(f"     {v}: {cnt} ({100*cnt//total}%)")
        if ds_counts:
            print("   下载状态:")
            for s, cnt in sorted(ds_counts.items(), key=lambda x: -x[1]):
                print(f"     {s}: {cnt}")
        if is_counts:
            print("   完整性状态:")
            for s, cnt in sorted(is_counts.items(), key=lambda x: -x[1]):
                print(f"     {s}: {cnt}")

        # ---- v7：api_raw / hymn_category / 音频条数 / 最新 API 更新时间（§5.6）----
        v7 = api_raw_stats()
        print(f"   API 原始记录(api_raw): {v7['rows']}/{total}"
              f"{' ✅' if v7['rows'] >= total else '（执行 Step 2 或菜单 10 补齐）'}")
        if v7["latest"]:
            print(f"   最新 API updated_at: {v7['latest']}")
        print(f"   音频条目数（audio_version_list 合计）: {v7['audio_total']}")
        cats = load_hymn_category()
        if cats:
            print(f"   hymn_category 分类数: {len(cats)}"
                  f"（API 重建，最近更新 {max(c['updated_at'] for c in cats) or '—'}）")

        # ---- 文件存在性校验汇总 ----
        total_missing = sum(len(v) for v in missing_files.values())
        if total_missing == 0:
            print("   🗂️ 文件一致性: 数据库引用的文件全部存在 ✅")
        else:
            print(f"   🗂️ 文件一致性: 发现 {total_missing} 个缺失文件 ⚠️")
            for key, paths in missing_files.items():
                if paths:
                    print(f"      {key} 缺失 {len(paths)} 个:")
                    for p in paths[:5]:
                        print(f"         - {p}")

        if total > 0 and has_lyrics < total:
            print(f"   ⚠️ 其中 {total - has_lyrics} 首歌词提取失败")
    except Exception as e:  # noqa: BLE001 - 初始化前打印友好提示
        print(f"📊 数据库状态：尚未初始化（{e}）")
    finally:
        conn.close()


def print_url_map_status():
    from .config import MAP_FILE
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE, 'r', encoding='utf-8') as f:
            lines = [line.strip() for line in f if line.strip()]
        print(f"📋 url_map.txt：{len(lines)} 条记录")
        return lines
    else:
        print("📋 url_map.txt：不存在")
        return []


# ================= PNG 图片路径入库（原 step7_png_db.py，合并） =================

PROGRESS_FILE = os.path.join(SAVE_ROOT, "step7_progress.json")
PROGRESS_VERSION = 1
PROGRESS_FLUSH_EVERY = 50


def load_png_progress():
    """加载 PNG 回填进度: {hymn_number: 'done'}"""
    if not os.path.exists(PROGRESS_FILE):
        return {}
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") != PROGRESS_VERSION:
            return {}
        return {h: "done" for h in data.get("completed", [])}
    except Exception:  # noqa: BLE001 - 进度文件损坏时从头开始
        return {}


def save_png_progress(completed_list):
    """写 PNG 进度文件(原子替换)"""
    tmp = PROGRESS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": PROGRESS_VERSION, "completed": completed_list},
                  fh, ensure_ascii=False, indent=2)
    os.replace(tmp, PROGRESS_FILE)


def reset_png_progress():
    """清空 PNG 进度文件"""
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
        print("已清空 PNG 回填进度文件")


def resolve_png(pdf_path):
    """PDF 路径 -> 对应 PNG 路径(同名, 双页已拼接为同名整图)"""
    if not pdf_path:
        return None
    png = pdf_path[:-4] + ".png"  # xxx.pdf -> xxx.png
    if os.path.exists(png):
        return png
    return None


def backfill_png(conn, progress, force=False):
    """从 PDF 路径推导并回填 PNG 路径到 staff_png_path/numbered_png_path

    Args:
        conn: 数据库连接
        progress: 进度集合 {hymn_number: 'done'}
        force: True 时忽略进度全量回填
    Returns:
        (staff_ok, numbered_ok, staff_miss, numbered_miss, resumed)
    """
    cur = conn.cursor()
    cur.execute("SELECT hymn_number, staff_img_path, numbered_img_path FROM tjc_hymn")
    rows = cur.fetchall()
    staff_ok = numbered_ok = staff_miss = numbered_miss = 0
    resumed = 0

    completed = list(progress.keys())
    dirty = 0

    for num, staff_pdf, numbered_pdf in rows:
        # 断点: 已完成的编号直接跳过
        if num in progress and not force:
            resumed += 1
            continue

        sp = resolve_png(staff_pdf)
        if sp:
            cur.execute("UPDATE tjc_hymn SET staff_png_path=? WHERE hymn_number=?", (sp, num))
            staff_ok += 1
        elif staff_pdf:
            staff_miss += 1

        np_ = resolve_png(numbered_pdf)
        if np_:
            cur.execute("UPDATE tjc_hymn SET numbered_png_path=? WHERE hymn_number=?", (np_, num))
            numbered_ok += 1
        elif numbered_pdf:
            numbered_miss += 1

        # 已回填的记入进度(增量落盘)
        if num not in completed:
            completed.append(num)
            dirty += 1
            if dirty >= PROGRESS_FLUSH_EVERY:
                conn.commit()          # 先提交数据
                save_png_progress(completed)
                dirty = 0

    conn.commit()
    if dirty > 0 and completed:
        save_png_progress(completed)
    return staff_ok, numbered_ok, staff_miss, numbered_miss, resumed


def delete_pages():
    """删除 _p1/_p2 分页小图(仅当同名整图存在)"""
    deleted = skipped = 0
    for dirpath, _d, files in os.walk(SAVE_ROOT):
        for f in files:
            if not f.endswith(("_p1.png", "_p2.png")):
                continue
            base = f.replace("_p1.png", "").replace("_p2.png", "")
            whole = base + ".png"
            if whole in files:
                os.remove(os.path.join(dirpath, f))
                deleted += 1
            else:
                skipped += 1  # 无整图则保留, 防误删
    return deleted, skipped


def update_png_paths(force=False, reset=False):
    """PNG 图片路径入库入口（原 step7_png_db.run()）:
      幂等补字段(迁移已含) + 回填 PNG 路径 + 清理分页图 + 断点进度"""
    if reset:
        reset_png_progress()

    progress = load_png_progress()
    if progress and not force:
        print(f"断点续跑: 已有 {len(progress)} 个编号的进度记录")

    conn = sqlite3.connect(DB_PATH)
    try:
        _migrate_v5_png_fields(conn.cursor())  # 幂等确保字段存在
        s_ok, n_ok, s_miss, n_miss, resumed = backfill_png(conn, progress, force=force)
    finally:
        conn.close()
    print(f"[回填] staff_png_path {s_ok} 条, numbered_png_path {n_ok} 条"
          + (f", 跳过(进度) {resumed} 条" if resumed else ""))
    if s_miss or n_miss:
        print(f"[回填] 警告: staff 缺失 {s_miss}, numbered 缺失 {n_miss}")
    deleted, skipped = delete_pages()
    print(f"[清理] 删除分页图 {deleted} 张, 保留(无整图) {skipped} 张")
    print("完成")
    return {"staff": s_ok, "numbered": n_ok, "deleted_pages": deleted, "resumed": resumed}


# ================= 带简谱文字歌词表（v8: hymn_jianpu / hymn_jianpu_line） =================
#
# 来源：`data/赞美诗PPT/*.ppt`（解析见 `crawler_core/ppt_jianpu.py`，取证见会话日志任务 3）
#
# 设计取舍（为什么不扩充 tjc_hymn）：
#   - 一首诗天然是「节 × 行」两级、行数不定（1:N）。塞进 `tjc_hymn` 只能①再走
#     `ALTER TABLE ADD COLUMN` 追加 verse*_jianpu（重演 v5~v7 的列序错位）或②塞 JSON 大字段
#     （SQL 里无法做等长/归属校验）。两种都违背「可查询、可校验、可复核」的目标。
#   - 拆两张表：`hymn_jianpu`（每首一行：来源 + 元数据 + 汇总校验）+ `hymn_jianpu_line`
#     （每行：notes/lyric + 音符数/字数 + 等长标志）→ 行级异常可直接 SQL 查出。
#   - 关联：`hymn_jianpu.hymn_number` ↔ `tjc_hymn.hymn_number`。PPT 文件名是**旧版编号**，
#     入库前由标题匹配换算为 DB 编号（`ppt_jianpu.match_hymn_number`）。

JIANPU_HYMN_DDL = """CREATE TABLE IF NOT EXISTS hymn_jianpu (
    ppt_file        TEXT PRIMARY KEY,
    hymn_number     TEXT DEFAULT '',
    version         TEXT DEFAULT '',
    ppt_old_no      TEXT DEFAULT '',
    ppt_new_no      TEXT DEFAULT '',
    title           TEXT DEFAULT '',
    title_line      TEXT DEFAULT '',
    key_sig         TEXT DEFAULT '',
    time_sig        TEXT DEFAULT '',
    tempo           TEXT DEFAULT '',
    slide_count     INTEGER DEFAULT 0,
    chorus_slides   INTEGER DEFAULT 0,
    pair_count      INTEGER DEFAULT 0,
    note_total      INTEGER DEFAULT 0,
    tune_period     INTEGER DEFAULT 0,
    title_match     TEXT DEFAULT '',
    title_score     REAL DEFAULT 0,
    verse_match     TEXT DEFAULT '',
    verse_score     REAL DEFAULT 0,
    marker_ok       INTEGER DEFAULT 0,
    structure_ok    INTEGER DEFAULT 0,
    tune_ok         INTEGER DEFAULT 0,
    align_ok        INTEGER DEFAULT 0,
    review_reason   TEXT DEFAULT '',
    src_md5         TEXT DEFAULT '',
    extractor       TEXT DEFAULT '',
    updated_at      TIMESTAMP DEFAULT (datetime('now', 'localtime'))
)"""

JIANPU_LINE_DDL = """CREATE TABLE IF NOT EXISTS hymn_jianpu_line (
    ppt_file        TEXT NOT NULL,
    hymn_number     TEXT DEFAULT '',
    stanza_no       INTEGER NOT NULL,
    line_no         INTEGER NOT NULL,
    verse_no        INTEGER DEFAULT 0,
    is_chorus       INTEGER DEFAULT 0,
    label           TEXT DEFAULT '',
    notes           TEXT NOT NULL,
    lyric           TEXT NOT NULL,
    note_count      INTEGER DEFAULT 0,
    rest_count      INTEGER DEFAULT 0,
    syllable_count  INTEGER DEFAULT 0,
    count_delta     INTEGER DEFAULT 0,
    align_ok        INTEGER DEFAULT 0,
    PRIMARY KEY (ppt_file, stanza_no, line_no)
)"""


def ensure_jianpu_tables(c):
    """v8: 幂等创建「带简谱文字歌词」两表 + 索引（不改动 tjc_hymn）

    主键用 `ppt_file`：同一首诗在 PPT 库里可能存在**甲/乙两版**（如 051a/051b、132a/132b），
    用 hymn_number 做主键会互相覆盖；`hymn_number` 降为关联列（未匹配时为空串）。
    """
    c.execute(JIANPU_HYMN_DDL)
    c.execute(JIANPU_LINE_DDL)
    c.execute("CREATE INDEX IF NOT EXISTS idx_jianpu_number ON hymn_jianpu(hymn_number)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_jianpu_line_review "
              "ON hymn_jianpu_line(align_ok, count_delta)")
    c.connection.commit()


# 列清单（写库/读取共用；顺序即建表顺序，与 DDL 保持一致）
JIANPU_HYMN_COLS = ("ppt_file", "hymn_number", "version", "ppt_old_no", "ppt_new_no",
                    "title", "title_line", "key_sig", "time_sig", "tempo",
                    "slide_count", "chorus_slides", "pair_count", "note_total",
                    "tune_period", "title_match", "title_score", "verse_match",
                    "verse_score", "marker_ok", "structure_ok", "tune_ok", "align_ok",
                    "review_reason", "src_md5", "extractor")
JIANPU_LINE_COLS = ("ppt_file", "hymn_number", "stanza_no", "line_no", "verse_no",
                    "is_chorus", "label", "notes", "lyric", "note_count", "rest_count",
                    "syllable_count", "count_delta", "align_ok")


def save_jianpu_records(records, db_path=DB_PATH):
    """写入「带简谱文字歌词」（每首 UPSERT 主行 + 重写行表；幂等，事务内失败回滚）

    - 未映射到 DB 编号 / 解析失败 的记录**跳过并回报**（不静默丢弃）
    - 行表先按 hymn_number 删除再插入 → 同一首重复导入不残留旧行
    返回 {"hymns": n, "lines": n, "skipped": [(ppt_file, reason), ...]}
    """
    stats = {"hymns": 0, "lines": 0, "skipped": []}
    conn = sqlite3.connect(db_path)
    try:
        c = conn.cursor()
        ensure_jianpu_tables(c)
        hcols = ", ".join(JIANPU_HYMN_COLS)
        hph = ", ".join("?" * len(JIANPU_HYMN_COLS))
        hupd = ", ".join(f"{col} = excluded.{col}" for col in JIANPU_HYMN_COLS[1:])
        lcols = ", ".join(JIANPU_LINE_COLS)
        lph = ", ".join("?" * len(JIANPU_LINE_COLS))
        for rec in records:
            if rec.get("parse_error") or not rec.get("ppt_file"):
                stats["skipped"].append((rec.get("ppt_file", "?"),
                                         rec.get("review_reason") or "解析失败"))
                continue
            # 列名来自本模块常量白名单，值全部参数化
            c.execute(f"INSERT INTO hymn_jianpu ({hcols}) VALUES ({hph}) "
                      f"ON CONFLICT(ppt_file) DO UPDATE SET {hupd}",  # nosec B608
                      [rec.get(col) for col in JIANPU_HYMN_COLS])
            c.execute("DELETE FROM hymn_jianpu_line WHERE ppt_file = ?", (rec["ppt_file"],))
            rows = [[rec["ppt_file"]] +
                    [(rec.get("hymn_number") if col == "hymn_number" else ln.get(col))
                     for col in JIANPU_LINE_COLS[1:]]
                    for ln in rec.get("lines", [])]
            c.executemany(f"INSERT INTO hymn_jianpu_line ({lcols}) VALUES ({lph})",  # nosec B608
                          rows)
            stats["hymns"] += 1
            stats["lines"] += len(rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return stats


def load_jianpu(hymn_number, db_path=DB_PATH):
    """按编号（或 ppt 文件名）读带简谱歌词 → [{"hymn": {...}, "lines": [...]}, ...]

    同一首可能有**多份**（甲/乙版本、重复 PPT），故返回列表；表不存在返回 []。
    """
    out = []
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            f"SELECT {', '.join(JIANPU_HYMN_COLS)} FROM hymn_jianpu "  # nosec B608
            "WHERE hymn_number = ? OR ppt_file = ? ORDER BY ppt_file",
            (str(hymn_number), str(hymn_number)))
        for row in cur.fetchall():
            hymn = dict(zip(JIANPU_HYMN_COLS, row))
            lines = conn.execute(
                f"SELECT {', '.join(JIANPU_LINE_COLS)} FROM hymn_jianpu_line "  # nosec B608
                "WHERE ppt_file = ? ORDER BY stanza_no, line_no",
                (hymn["ppt_file"],)).fetchall()
            out.append({"hymn": hymn,
                        "lines": [dict(zip(JIANPU_LINE_COLS, r)) for r in lines]})
    except sqlite3.OperationalError:  # 未建表
        return []
    finally:
        conn.close()
    return out


def jianpu_stats(db_path=DB_PATH):
    """带简谱歌词覆盖统计（表不存在返回 None）"""
    conn = sqlite3.connect(db_path)
    try:
        total, mapped_n, usable = conn.execute(
            "SELECT COUNT(*), SUM(hymn_number != ''), SUM(align_ok = 1) "
            "FROM hymn_jianpu").fetchone()
        lines = conn.execute("SELECT COUNT(*) FROM hymn_jianpu_line").fetchone()[0]
        eq = conn.execute(
            "SELECT COUNT(*) FROM hymn_jianpu_line WHERE count_delta = 0").fetchone()[0]
        short = conn.execute(
            "SELECT COUNT(*) FROM hymn_jianpu_line WHERE count_delta < 0").fetchone()[0]
        db_matched = conn.execute(
            "SELECT COUNT(*) FROM hymn_jianpu j JOIN tjc_hymn h "
            "ON h.hymn_number = j.hymn_number").fetchone()[0]
        return {"hymns": total, "mapped": mapped_n or 0, "usable": usable or 0,
                "lines": lines, "lines_equal": eq, "lines_short": short,
                "db_matched": db_matched}
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
