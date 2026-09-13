#!/usr/bin/env python3
"""pytest 单元测试：DB v7（api_raw / hymn_category 重建）+ 增量同步计划 + 引擎隔离

覆盖（P0 验收 5、P1 验收 1/2/5）：
  - ensure_v7_fields 幂等；_create_table_v4 建表即含 api_raw
  - save_to_db：写 api_raw；文本字段「空值不覆盖旧值」（title/作者/源考）
  - rebuild_hymn_category：分类数/每类数量与 API 一致、可重复执行（幂等）
  - sync.plan：水位判定（未变→same、变→changed、缺 api_raw→changed、API 无→site_only）
  - 引擎隔离：`--engine api` 下 sys.modules 不含 selenium（legacy 包亦不预先导入）

运行: /home/zjx/python_env/bin/python -m pytest test/test_db_v7.py -v
"""
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（test/ 的上级）
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from crawler_core import api_client, db, sync
from crawler_core.api_client import api_raw_json


def _fresh_db(tmp_path, monkeypatch):
    """指向临时 DB 并建表"""
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    conn = sqlite3.connect(path)
    db._create_table_v4(conn.cursor())
    conn.commit()
    conn.close()
    return path


def _row(path, no, fields):
    conn = sqlite3.connect(path)
    row = conn.execute(f"SELECT {', '.join(fields)} FROM tjc_hymn WHERE hymn_number = ?",
                       (no,)).fetchone()
    conn.close()
    return row


def _api_rec(no="12", **over):
    rec = {
        "no": no, "name": "耶穌尊名",
        "lyrics": [{"text": "第一節"}, {"text": "第二節"}],
        "lyrics_chorus": "副歌",
        "sheet_score_pdf_url": f"https://e.org/sheet/{no}.pdf",
        "num_score_pdf_url": f"https://e.org/num/{no}.pdf",
        "history": "<p>源考</p>",
        "lyricists": [{"name": "作者"}], "composers": [{"name": "作曲"}],
        "audio_files": [], "category": {"id": 2, "name": "讚美耶穌", "slug": None,
                                        "updated_at": "2022-01-01T00:00:00Z"},
        "updated_at": "2023-07-26T03:08:19.000000Z",
    }
    rec.update(over)
    return rec


# ================= v7 字段迁移 =================

class TestV7Fields:
    def test_ensure_v7_idempotent(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "t.db")
        c = conn.cursor()
        c.execute("CREATE TABLE tjc_hymn (id INTEGER PRIMARY KEY, hymn_number TEXT)")
        assert db.ensure_v7_fields(c) is True
        assert db.ensure_v7_fields(c) is False
        assert "api_raw" in {row[1] for row in c.execute("PRAGMA table_info(tjc_hymn)")}
        conn.close()

    def test_create_table_has_api_raw(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "t.db")
        db._create_table_v4(conn.cursor())
        assert "api_raw" in {row[1] for row in conn.execute("PRAGMA table_info(tjc_hymn)")}


# ================= save_to_db（api_raw + 空值守卫） =================

class TestSaveToDb:
    def test_writes_api_raw(self, tmp_path, monkeypatch):
        path = _fresh_db(tmp_path, monkeypatch)
        db.save_to_db(api_client.to_db_record(_api_rec()))
        raw, vc, chorus = _row(path, "12", ["api_raw", "verse_count", "chorus"])
        assert api_client.api_category_name(raw) == "讚美耶穌"
        assert vc == 2 and chorus == "副歌"

    def test_empty_values_do_not_overwrite(self, tmp_path, monkeypatch):
        path = _fresh_db(tmp_path, monkeypatch)
        db.save_to_db(api_client.to_db_record(_api_rec()))
        # 模拟 API 侧缺标题/作者/源考（如 #25/#31/#66/#299 无 lyricists、#349 无 history）
        db.save_to_db(api_client.to_db_record(_api_rec(
            name="", lyricists=[], composers=None, history="")))
        title, lyricist, composer, source = _row(
            path, "12", ["title", "lyricist", "composer", "source_info"])
        assert (title, lyricist, composer, source) == ("耶穌尊名", "作者", "作曲", "源考")

    def test_dom_path_without_api_raw_keeps_old(self, tmp_path, monkeypatch):
        path = _fresh_db(tmp_path, monkeypatch)
        db.save_to_db(api_client.to_db_record(_api_rec()))
        dom_data = api_client.to_db_record(_api_rec())
        dom_data["api_raw"] = ""      # DOM 保底路径不带 api_raw
        db.save_to_db(dom_data)
        (raw,) = _row(path, "12", ["api_raw"])
        assert api_client.api_category_name(raw) == "讚美耶穌"


# ================= hymn_category 重建 =================

class TestRebuildCategory:
    def test_rebuild_and_idempotent(self, tmp_path, monkeypatch):
        _fresh_db(tmp_path, monkeypatch)
        records = [_api_rec("1", category={"id": 1, "name": "讚美天父", "slug": None,
                                           "updated_at": "2022-07-05T12:00:00Z"}),
                   _api_rec("2", category={"id": 1, "name": "讚美天父", "slug": None,
                                           "updated_at": "2022-07-05T12:00:00Z"}),
                   _api_rec("3", category={"id": 2, "name": "讚美耶穌", "slug": "praise",
                                           "updated_at": "2022-07-05T12:00:00Z"})]
        stats = db.rebuild_hymn_category(records)
        assert stats["categories"] == 2 and stats["hymns"] == 3
        rows = db.load_hymn_category()
        assert [r["name"] for r in rows] == ["讚美天父", "讚美耶穌"]
        assert [r["hymn_count"] for r in rows] == [2, 1]

        stats2 = db.rebuild_hymn_category(records)      # 幂等
        assert stats2["categories"] == 2 and stats2["hymns"] == 3
        conn = sqlite3.connect(db.DB_PATH)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "hymn_category_new" not in tables

    def test_rebuild_skips_broken_category(self, tmp_path, monkeypatch):
        _fresh_db(tmp_path, monkeypatch)
        stats = db.rebuild_hymn_category([
            {"no": "1"}, {"no": "2", "category": None},
            {"no": "3", "category": {"name": ""}},
            {"no": "4", "category": {"id": 9, "name": "有效"}},
        ])
        assert stats["categories"] == 1 and stats["hymns"] == 1



# ================= 增量同步计划（纯函数） =================

class TestSyncPlan:
    def test_watermark_and_diff(self):
        recs = [_api_rec("1"), _api_rec("2", updated_at="2024-01-01T00:00:00Z")]
        state = {
            "1": {"api_raw": api_raw_json(_api_rec("1")),
                  "updated_at": "2023-07-26T03:08:19.000000Z",
                  "verse_count": 2, "has_chorus": True, "audio": []},
            "2": {"api_raw": api_raw_json(_api_rec("2")),
                  "updated_at": "2023-01-01T00:00:00Z",
                  "verse_count": 2, "has_chorus": True, "audio": []},
            "409": {"api_raw": "", "updated_at": "", "verse_count": 0,
                    "has_chorus": False, "audio": []},
        }
        result = sync.plan(recs, state)
        assert result["same"] == ["1"]
        assert [c["no"] for c in result["changed"]] == ["2"]
        assert result["site_only"] == ["409"]
        assert result["watermark"] == "2023-07-26T03:08:19.000000Z"

    def test_missing_api_raw_counts_as_changed(self):
        result = sync.plan([_api_rec("1")], {"1": {"api_raw": "", "updated_at": "",
                                                   "verse_count": 0, "has_chorus": False,
                                                   "audio": []}})
        assert [c["no"] for c in result["changed"]] == ["1"]
        assert "缺少 api_raw" in result["changed"][0]["notes"]

    def test_second_run_zero_updates(self):
        """P1 验收 2：全量落库后二次运行 → 0 首需要更新（水位判定正确）"""
        recs = [_api_rec("1"), _api_rec("2")]
        state = {r["no"]: {"api_raw": api_raw_json(r), "updated_at": r["updated_at"],
                           "verse_count": 2, "has_chorus": True, "audio": []} for r in recs}
        result = sync.plan(recs, state)
        assert result["changed"] == [] and len(result["same"]) == 2

    def test_pending_downloads(self):
        rec = _api_rec("12", audio_files=[
            {"file_url": "https://e.org/a/piano.m4a", "audio_category": {"name": "鋼琴"}},
            {"file_url": None, "audio_category": {"name": "人聲"}},
        ])
        state = {"12": {"api_raw": "", "updated_at": "", "verse_count": 2,
                        "has_chorus": True, "audio": ["人聲版"]}}
        # 预检把 鋼琴版 判为不可用 → 不进队列；人聲版 DB 已有 → 也不进
        probe = [{"hymn_number": "12", "audio_versions": {
            "鋼琴版": {"url": None, "_http_status": 404, "_unavailable": "http_4xx"},
            "人聲版": {"url": None, "_unavailable": "api_null"},
        }}]
        assert sync.pending_downloads([rec], state, probe_entries=probe) == []
        pending2 = sync.pending_downloads([rec], state, probe_entries=[
            {"hymn_number": "12", "audio_versions": {}},
        ])
        assert [p["filename"] for p in pending2] == ["12_鋼琴版.m4a"]

    def test_pending_downloads_skips_probe_unavailable(self):
        """#62 型：API 给了 URL 但预检判定 404 → 不进下载队列"""
        rec = _api_rec("62", audio_files=[
            {"file_url": "https://e.org/a/62.m4a", "audio_category": {"name": "鋼琴"}},
            {"file_url": "https://e.org/a/bad.m4a", "audio_category": {"name": "人聲"}},
        ])
        state = {"62": {"api_raw": "", "updated_at": "", "verse_count": 2,
                        "has_chorus": False, "audio": ["鋼琴版"]}}
        probe = [{"hymn_number": "62", "audio_versions": {
            "鋼琴版": {"url": "https://e.org/a/62.m4a", "ext": "m4a", "_http_status": 200},
            "人聲版": {"url": None, "ext": "m4a", "_http_status": 404, "_unavailable": "http_4xx"},
        }}]
        assert sync.pending_downloads([rec], state, probe_entries=probe) == []


# ================= 引擎隔离（P0 验收 5） =================

class TestEngineIsolation:
    def test_api_engine_imports_no_selenium(self):
        code = ("import sys, crawler_fast, crawler_core.probe, crawler_core.scanner;"
                "print('selenium' in sys.modules)")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                             capture_output=True, text=True, check=True)
        assert out.stdout.strip() == "False"

    def test_legacy_modules_lazy_import_selenium(self):
        code = ("import sys, crawler_core.selenium_legacy.driver as d,"
                "crawler_core.selenium_legacy.extractor_dom as e,"
                "crawler_core.selenium_legacy.probe_audio as p;"
                "print('selenium' in sys.modules)")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                             capture_output=True, text=True, check=True)
        assert out.stdout.strip() == "False"
