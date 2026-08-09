# crawler_core/db.py
# 数据库管理 - 含迁移与初始化
# v4: +download_status +integrity_status
# v5: +staff_png_path +numbered_png_path（图片转 PNG 的保存路径）

import json
import os
import sqlite3

from .config import DB_PATH


def init_db():
    """初始化数据库，迁移到最新结构（v5: 含 PNG 图片路径字段）"""
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
                _migrate_v5_png_fields(c)
                _backfill_from_probe(c, conn)
                print("📊 数据库结构已是最新版（v5）。")
        else:
            _create_table_v4(c)

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

    # nosec B608 - 列名来自 PRAGMA 白名单过滤(非用户输入), SQL 值均参数化
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


def _backfill_from_probe(c, conn):
    """从 probe_report.json 回填 audio_versions / audio_version_list / download_status"""
    pr_path = os.path.join(os.path.dirname(DB_PATH), "probe_report.json")
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
    """创建 v5 版 tjc_hymn 表（v4 字段 + PNG 图片路径字段）"""
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
                staff_img_path TEXT,
                numbered_img_path TEXT,
                audio_versions TEXT DEFAULT '{}',
                updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
                audio_version_list TEXT DEFAULT '[]',
                staff_png_path TEXT,
                numbered_png_path TEXT,
                download_status TEXT DEFAULT 'pending',
                integrity_status TEXT DEFAULT 'unchecked'
            )''')


# ================= 数据写入 =================

def save_to_db(hymn_data):
    """保存单首数据到数据库 (UPSERT)，自动维护 audio_version_list"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()

        av = hymn_data.get("audio_versions", {})
        audio_json = json.dumps(av, ensure_ascii=False)
        version_list_json = json.dumps(list(av.keys()), ensure_ascii=False)
        ds = hymn_data.get("download_status", "pending")
        ins = hymn_data.get("integrity_status", "unchecked")

        sql = '''INSERT INTO tjc_hymn
                 (hymn_number, title, lyricist, composer, source_info, verse_count,
                  verse_1, verse_2, verse_3, verse_4, verse_5,
                  verse_6, verse_7, verse_8, verse_9, verse_10,
                  staff_img_path, numbered_img_path,
                  audio_versions, audio_version_list,
                  download_status, integrity_status)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(hymn_number) DO UPDATE SET
                    title = excluded.title, lyricist = excluded.lyricist,
                    composer = excluded.composer, source_info = excluded.source_info,
                    verse_count = excluded.verse_count,
                    verse_1 = excluded.verse_1, verse_2 = excluded.verse_2,
                    verse_3 = excluded.verse_3, verse_4 = excluded.verse_4,
                    verse_5 = excluded.verse_5, verse_6 = excluded.verse_6,
                    verse_7 = excluded.verse_7, verse_8 = excluded.verse_8,
                    verse_9 = excluded.verse_9, verse_10 = excluded.verse_10,
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
                    updated_at = datetime('now', 'localtime')
                '''
        params = [
            hymn_data["hymn_number"], hymn_data["title"],
            hymn_data["lyricist"], hymn_data["composer"],
            hymn_data["source_info"], hymn_data["verse_count"]
        ]
        params.extend(hymn_data["verses"])
        params.extend([hymn_data["staff_img_path"], hymn_data["numbered_img_path"],
                       audio_json, version_list_json, ds, ins])
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
    pr_path = os.path.join(os.path.dirname(DB_PATH), "probe_report.json")
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
    pr_path = os.path.join(os.path.dirname(DB_PATH), "probe_report.json")
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

def print_db_status():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("SELECT COUNT(*) FROM tjc_hymn")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tjc_hymn WHERE verse_count > 0")
        has_lyrics = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tjc_hymn WHERE staff_img_path != ''")
        has_staff = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tjc_hymn WHERE numbered_img_path != ''")
        has_numbered = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tjc_hymn WHERE audio_version_list != '[]' AND audio_version_list != ''")
        has_audio = c.fetchone()[0]

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
        print(f"   五线谱: {has_staff}/{total} {'✅' if has_staff > 0 else '❌'}")
        print(f"   简谱:   {has_numbered}/{total} {'✅' if has_numbered > 0 else '❌'}")
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
