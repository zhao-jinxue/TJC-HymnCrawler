#!/usr/bin/env python3
"""pytest 单元测试：歌词 API 刷新 + DOM box 归并（不依赖网络 / Selenium）

覆盖 2026-09-12 修复的「副歌整段丢失」缺陷：
  - group_lyrics_boxes：每个 Tab 下多个 .lyrics_box → (verses, chorus) 归并规则
  - normalize_text：CRLF 归一
  - fetch_hymn_lyrics：成功 / 重试 / 异常降级（monkeypatch，无真实网络）
  - _update_row + ensure_chorus_field：v6 chorus 字段迁移与写库

运行: /home/zjx/python_env/bin/python -m pytest test/test_lyrics_api.py -v
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（test/ 的上级）
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from crawler_core import api_client, lyrics_api
from crawler_core.db import _create_table_v4, ensure_chorus_field
from crawler_core.extractor import group_lyrics_boxes


# ---------- DOM box 归并（缺陷根因） ----------
class TestGroupLyricsBoxes:
    def test_verse_plus_chorus(self):
        verses, chorus = group_lyrics_boxes([
            ["節1", "副歌"], ["節2", "副歌"], ["節3", "副歌"], ["節4", "副歌"],
        ])
        assert verses == ["節1", "節2", "節3", "節4"]
        assert chorus == "副歌"

    def test_multi_line_chorus(self):
        verses, chorus = group_lyrics_boxes([["A", "R1", "R2"], ["B", "R1", "R2"]])
        assert verses == ["A", "B"]
        assert chorus == "R1\nR2"

    def test_no_chorus(self):
        verses, chorus = group_lyrics_boxes([["A"], ["B"], ["C"]])
        assert verses == ["A", "B", "C"]
        assert chorus == ""

    def test_uneven_boxes_keep_all_text(self):
        # 位置不一致 → 视为本节内容，不得丢文本
        verses, chorus = group_lyrics_boxes([["A", "X"], ["B", "Y"]])
        assert verses == ["A\nX", "B\nY"]
        assert chorus == ""

    def test_blank_boxes_filtered(self):
        verses, chorus = group_lyrics_boxes([[" A ", "   "], ["B", ""]])
        assert verses == ["A", "B"]
        assert chorus == ""

    def test_empty_input(self):
        assert group_lyrics_boxes([]) == ([], "")
        assert group_lyrics_boxes([[], ["  "]]) == ([], "")


# ---------- 文本归一 ----------
class TestNormalizeText:
    def test_crlf(self):
        assert lyrics_api.normalize_text("a\r\nb\r\n") == "a\nb"

    def test_blank(self):
        assert lyrics_api.normalize_text(None) == ""
        assert lyrics_api.normalize_text("  ") == ""


# ---------- API 取词（monkeypatch，无网络） ----------
class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class TestFetchHymnLyrics:
    def test_success(self, monkeypatch):
        payload = {"lyrics": [{"text": "第一節\r\n第二行"}, {"text": ""}], "lyrics_chorus": "副歌\r\n"}
        monkeypatch.setattr(api_client.requests, "get", lambda *a, **k: _FakeResp(200, payload))
        r = lyrics_api.fetch_hymn_lyrics("12")
        assert r["verses"] == ["第一節\n第二行"]  # 空歌词项被过滤
        assert r["chorus"] == "副歌"
        assert r["error"] is None

    def test_http_error_retries(self, monkeypatch):
        monkeypatch.setattr(api_client.time, "sleep", lambda *_: None)
        calls = {"n": 0}

        def fake_get(*a, **k):
            calls["n"] += 1
            return _FakeResp(500)

        monkeypatch.setattr(api_client.requests, "get", fake_get)
        r = lyrics_api.fetch_hymn_lyrics("12", retries=2)
        assert r["verses"] == []
        assert "HTTP 500" in r["error"]
        assert calls["n"] == 2

    def test_exception_degrades(self, monkeypatch):
        monkeypatch.setattr(api_client.time, "sleep", lambda *_: None)

        def boom(*a, **k):
            raise TimeoutError("boom")

        monkeypatch.setattr(api_client.requests, "get", boom)
        r = lyrics_api.fetch_hymn_lyrics("12", retries=2)
        assert r["verses"] == [] and r["chorus"] == ""
        assert "TimeoutError" in r["error"]

    def test_404_returns_error(self, monkeypatch):
        monkeypatch.setattr(api_client.time, "sleep", lambda *_: None)
        monkeypatch.setattr(api_client.requests, "get", lambda *a, **k: _FakeResp(404))
        r = lyrics_api.fetch_hymn_lyrics("999")
        assert r["verses"] == [] and "404" in r["error"]



# ---------- v6 chorus 字段迁移 + 写库 ----------
class TestChorusFieldAndWrite:
    def test_ensure_chorus_field_idempotent(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "t.db")
        c = conn.cursor()
        c.execute("CREATE TABLE tjc_hymn (id INTEGER PRIMARY KEY, hymn_number TEXT)")
        assert ensure_chorus_field(c) is True
        assert ensure_chorus_field(c) is False  # 第二次不再重复添加
        cols = {r[1] for r in c.execute("PRAGMA table_info(tjc_hymn)")}
        assert "chorus" in cols
        conn.close()

    def test_create_table_has_chorus(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "t.db")
        c = conn.cursor()
        _create_table_v4(c)
        cols = {r[1] for r in c.execute("PRAGMA table_info(tjc_hymn)")}
        assert "chorus" in cols
        conn.close()

    def test_update_row_writes_verses_and_chorus(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "t.db")
        c = conn.cursor()
        _create_table_v4(c)
        c.execute("INSERT INTO tjc_hymn (hymn_number, verse_count) VALUES ('12', 0)")
        lyrics_api._update_row(c, "12", ["v1", "v2"], "副歌")
        conn.commit()
        row = c.execute(
            "SELECT verse_count, verse_1, verse_2, verse_3, chorus FROM tjc_hymn WHERE hymn_number='12'"
        ).fetchone()
        assert row == (2, "v1", "v2", "", "副歌")
        conn.close()

    def test_update_row_caps_at_ten_verses(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "t.db")
        c = conn.cursor()
        _create_table_v4(c)
        c.execute("INSERT INTO tjc_hymn (hymn_number, verse_count) VALUES ('x', 0)")
        lyrics_api._update_row(c, "x", [f"v{i}" for i in range(12)], "")
        conn.commit()
        row = c.execute("SELECT verse_count, verse_10 FROM tjc_hymn WHERE hymn_number='x'").fetchone()
        assert row == (10, "v9")
        conn.close()


# ---------- 断点进度 ----------
class TestProgress:
    def test_save_load_clear(self, tmp_path, monkeypatch):
        pf = tmp_path / "lyrics_progress.json"
        monkeypatch.setattr(lyrics_api, "PROGRESS_FILE", str(pf))
        assert lyrics_api.load_progress() == set()
        lyrics_api.save_progress({"12", "51_a"})
        assert lyrics_api.load_progress() == {"12", "51_a"}
        lyrics_api.clear_progress()
        assert lyrics_api.load_progress() == set()

    def test_corrupted_progress(self, tmp_path, monkeypatch):
        pf = tmp_path / "lyrics_progress.json"
        pf.write_text("{ not json", encoding="utf-8")
        monkeypatch.setattr(lyrics_api, "PROGRESS_FILE", str(pf))
        assert lyrics_api.load_progress() == set()
