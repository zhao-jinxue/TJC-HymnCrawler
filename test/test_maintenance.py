#!/usr/bin/env python3
"""pytest 单元测试：维护类工具（表列顺序重排 / checksums 重建 / 指定 PDF 转图）

覆盖:
  - `tool/reorder_table_columns.py`：列顺序解析（锚定 `db.py::_create_table_v4`）、
    旧库（ALTER 追加型）重排后「列顺序 / 列定义 / 数据 / sqlite_sequence / 分类表」保持、
    幂等、dry-run 不动库
  - `crawler_core.checksums.rebuild_all(base=...)`：登记 EXTS 文件、跳过无可登记文件的目录
  - `crawler_core.images.select_pdfs`：`--pdf` 指定清单校验（后缀 / 存在性 / 去重）

运行: /home/zjx/python_env/bin/python -m pytest -c config/pytest.ini test/test_maintenance.py -v
      （`tool/` 由下方 sys.path.insert 注入 → `import reorder_table_columns` 行带 pyright ignore 注释）
"""
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（test/ 的上级）
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tool"))
os.chdir(ROOT)

import reorder_table_columns as roc  # pyright: ignore[reportMissingImports]

from crawler_core.checksums import rebuild_all, sha256_file
from crawler_core.images import select_pdfs

# 代码 `_create_table_v4` 的列顺序（独立回归锚点：db.py 若再调 DDL 顺序，此用例会提醒）
EXPECTED_COLUMNS = (
    ["id", "hymn_number", "title", "lyricist", "composer", "source_info", "verse_count"]
    + [f"verse_{i}" for i in range(1, 11)]
    + ["chorus", "staff_img_path", "numbered_img_path", "staff_png_path", "numbered_png_path",
       "audio_versions", "audio_version_list", "audio_durations", "api_raw", "download_status",
       "integrity_status", "updated_at"]
)

# 2026-09-13 之前真实库的形态：v5/v6/v7 列由 ALTER TABLE 追加 → 落在表尾
LEGACY_DDL = """CREATE TABLE tjc_hymn (
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
    integrity_status TEXT DEFAULT 'unchecked',
    chorus TEXT DEFAULT '',
    api_raw TEXT DEFAULT '',
    audio_durations TEXT DEFAULT '{}'
)"""


def _make_legacy_db(path):
    """造一个"ALTER 追加型"旧库（列顺序与 2026-09-13 前真实库一致）"""
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute(LEGACY_DDL)
    c.execute("""CREATE TABLE hymn_category (
                    id          INTEGER PRIMARY KEY,
                    name        TEXT NOT NULL,
                    slug        TEXT DEFAULT '',
                    hymn_count  INTEGER DEFAULT 0,
                    updated_at  TEXT DEFAULT ''
                )""")
    c.executemany(
        "INSERT INTO tjc_hymn (id, hymn_number, title, verse_count, verse_1, chorus, "
        "staff_png_path, numbered_png_path, audio_versions, download_status, api_raw) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "1", "頌讚獨一真神", 2, "第一節", "副歌一", "a/1.png", "a/1n.png",
             json.dumps({"鋼琴版": "a/1.m4a"}, ensure_ascii=False), "completed",
             json.dumps({"no": "1", "category": {"name": "讚美天父"}}, ensure_ascii=False)),
            (2, "349", "奇妙的耶穌", 4, "雖然有時候日子淒涼", "奇妙的，奇妙的耶穌，",
             "b/349.png", "b/349n.png", "{}", "completed", ""),
            (3, "51_a", "萬古靈磐甲", 1, "第一節", "", None, None, "{}", "pending", ""),
        ],
    )
    c.executemany("INSERT INTO hymn_category (id, name, hymn_count) VALUES (?, ?, ?)",
                  [(1, "讚美天父", 3), (2, "救恩", 0)])
    c.execute("UPDATE sqlite_sequence SET seq = 949 WHERE name = 'tjc_hymn'")  # 真实库的计数现状
    conn.commit()
    conn.close()
    return path


def _rows(path, cols):
    """按给定列顺序读取全表（稳定排序），用于重排前后比对"""
    conn = sqlite3.connect(path)
    cl = ", ".join(f'"{c}"' for c in cols)
    rows = conn.execute(f"SELECT {cl} FROM tjc_hymn ORDER BY hymn_number").fetchall()
    conn.close()
    return rows


def _order(path):
    conn = sqlite3.connect(path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tjc_hymn)")]
    conn.close()
    return cols


# ================= 列顺序解析 =================

class TestColumnsParsing:
    def test_code_ddl_columns_match_expected(self):
        """`_create_table_v4` 的列顺序 == 锚点清单（代码被改动时立刻暴露）"""
        _, cols = roc.load_code_ddl()
        assert cols == EXPECTED_COLUMNS

    def test_columns_of_skips_table_constraints(self):
        """表级约束行不算列；DEFAULT 里的逗号不会把列名截断"""
        ddl = ("CREATE TABLE t (\n"
               "    a INTEGER PRIMARY KEY AUTOINCREMENT,\n"
               "    b TEXT DEFAULT 'x,y',\n"
               "    UNIQUE(b),\n"
               "    c TIMESTAMP DEFAULT (datetime('now', 'localtime'))\n"
               ")")
        assert roc.columns_of(ddl) == ["a", "b", "c"]

    def test_ddl_body_ignores_table_name_style(self):
        """DDL 主体比较忽略表名/IF NOT EXISTS 写法差异（RENAME 后 SQLite 会加引号）"""
        plain = "CREATE TABLE tjc_hymn (\n  a TEXT\n)"
        quoted = 'CREATE TABLE "tjc_hymn" (\n  a TEXT\n)'
        assert roc.ddl_body(plain) == roc.ddl_body(quoted)


# ================= 表列顺序重排 =================

class TestReorderTable:
    def test_reorder_legacy_order_keeps_data(self, tmp_path):
        db = _make_legacy_db(tmp_path / "old.db")
        before_rows = _rows(db, EXPECTED_COLUMNS)
        assert _order(db) != EXPECTED_COLUMNS  # 前置条件：确为"追加型"旧顺序

        res = roc.reorder(db_path=str(db), backup_dir=str(tmp_path))

        assert res["ok"] is True
        assert res["changed"] is True
        assert res["after"] == EXPECTED_COLUMNS
        assert _order(db) == EXPECTED_COLUMNS
        assert _rows(db, EXPECTED_COLUMNS) == before_rows  # 数据逐行逐列不变
        assert os.path.exists(res["backup"])              # 重排前已备份

        conn = sqlite3.connect(db)
        seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='tjc_hymn'").fetchone()[0]
        cats = conn.execute("SELECT * FROM hymn_category ORDER BY id").fetchall()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        assert seq == 949                                  # AUTOINCREMENT 计数不回退
        assert cats == [(1, "讚美天父", "", 3, ""), (2, "救恩", "", 0, "")]
        assert integrity == "ok"
        assert all(ok for _, ok in res["checks"])
        conn = sqlite3.connect(db)
        assert conn.execute("PRAGMA freelist_count").fetchone()[0] == 0  # VACUUM 后无空闲页虚胖
        conn.close()

    def test_reorder_is_idempotent(self, tmp_path):
        db = _make_legacy_db(tmp_path / "old.db")
        assert roc.reorder(db_path=str(db), backup_dir=str(tmp_path))["changed"] is True
        again = roc.reorder(db_path=str(db), backup_dir=str(tmp_path))
        assert again["changed"] is False and again["ok"] is True
        assert again["before"] == EXPECTED_COLUMNS

    def test_dry_run_keeps_legacy_order(self, tmp_path):
        db = _make_legacy_db(tmp_path / "old.db")
        legacy = _order(db)
        res = roc.reorder(db_path=str(db), dry_run=True, backup_dir=str(tmp_path))
        assert res["ok"] is True and res["changed"] is False
        assert _order(db) == legacy                       # 未改动
        assert not [f for f in os.listdir(tmp_path) if ".bak-" in f]  # 未备份（没动库）


# ================= checksums.json 全量重建 =================

class TestRebuildChecksums:
    def test_registers_exts_and_skips_empty_dirs(self, tmp_path):
        """登记 EXTS 后缀文件；无可登记文件的目录不生成 `[]` 噪音清单"""
        base = tmp_path / "res"
        hymn, cache, empty = base / "001_x", base / "api_cache", base / "empty"
        for d in (hymn, cache, empty):
            d.mkdir(parents=True)
        (hymn / "1_简谱.pdf").write_bytes(b"%PDF-1.4 fake")
        (hymn / "1_简谱.txt").write_text("歌詞", encoding="utf-8")
        (hymn / "note.md").write_text("非登记后缀", encoding="utf-8")
        (cache / "page_01.json").write_text("{}", encoding="utf-8")

        total = rebuild_all(base=str(base))

        entries = json.loads((hymn / "checksums.json").read_text(encoding="utf-8"))
        assert [e["file"] for e in entries] == ["1_简谱.pdf", "1_简谱.txt"]
        assert entries[0]["sha256"] == sha256_file(str(hymn / "1_简谱.pdf"))
        assert total == 2
        assert not (cache / "checksums.json").exists()
        assert not (empty / "checksums.json").exists()


# ================= PDF 转图的指定清单 =================

class TestSelectPdfs:
    def test_ok_sorted_and_deduped(self, tmp_path):
        a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
        a.write_bytes(b"%PDF")
        b.write_bytes(b"%PDF")
        assert select_pdfs([str(b), str(a), str(b)]) == sorted([str(a), str(b)])

    def test_rejects_non_pdf_and_missing(self, tmp_path):
        txt = tmp_path / "a.txt"
        txt.write_text("x", encoding="utf-8")
        with pytest.raises(ValueError):
            select_pdfs([str(txt)])
        with pytest.raises(ValueError):
            select_pdfs([str(tmp_path / "nope.pdf")])
