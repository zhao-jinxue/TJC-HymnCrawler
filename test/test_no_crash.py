#!/usr/bin/env python3
"""pytest 单元测试：不崩断言（§5.9.6，离线；对应 P0 验收 4）

四项断言：
  1. 坏 URL（404 / 空串 / 非法域名 / 超大延迟）→ 全链不崩、退出码 0、报告含 `_unavailable`
  2. API 不可达时 Step 2 → 每首登记 failed、不抛异常、`step2_progress.json` 可续跑
  3. `file_url=null` 的资源 → **不产生任何下载请求**
  4. API 结构被改（缺 `lyrics`）→ 触发降级/跳过，绝不 KeyError 崩栈

运行: /home/zjx/python_env/bin/python -m pytest test/test_no_crash.py -v
"""
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（test/ 的上级）
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from crawler_core import api_client, db, downloader, extractor, naming, probe


def _rec(no="12", **over):
    rec = {
        "no": no, "name": "耶穌尊名",
        "lyrics": [{"text": "第一節"}], "lyrics_chorus": "",
        "sheet_score_pdf_url": f"https://e.org/sheet/{no}.pdf",
        "num_score_pdf_url": f"https://e.org/num/{no}.pdf",
        "history": "<p>源考</p>", "lyricists": [{"name": "作者"}], "composers": [],
        "audio_files": [{"file_url": f"https://e.org/a/{no}.m4a",
                         "audio_category": {"name": "鋼琴"}}],
        "updated_at": "2023-01-01T00:00:00Z", "category": {"id": 1, "name": "讚美天父"},
    }
    rec.update(over)
    return rec


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    conn = sqlite3.connect(db.DB_PATH)
    db._create_table_v4(conn.cursor())
    conn.commit()
    conn.close()


# ================= 1. 坏 URL 全链不崩 =================

class TestBadUrls:
    def test_check_url_invalid_and_unreachable(self):
        assert probe.check_url("")["ok"] is False
        assert probe.check_url("not-a-url")["ok"] is False
        res = probe.check_url("https://nonexistent-domain-xyz.invalid/a.pdf",
                              retries=1, timeout=2)
        assert res["ok"] is False and res["_error"]

    def test_precheck_marks_unavailable(self, monkeypatch):
        entry = api_client.to_probe_entry(_rec())
        entry["audio_versions"]["壞曲版"] = {"url": "https://e.org/a/bad.m4a", "ext": "m4a"}
        entry["numbered_pdf"] = "https://e.org/num/404.pdf"

        def fake_check(url, retries=None, timeout=None):
            if "404" in url or "bad" in url:
                return {"ok": False, "_http_status": 404, "_error": "HTTP 404"}
            return {"ok": True, "_http_status": 200, "_error": None}

        monkeypatch.setattr(probe, "check_url", fake_check)
        stats = probe._apply_url_precheck([entry])

        assert stats["checked"] == 4          # 2 PDF + 2 音频
        assert stats["unavailable"] == 2      # 1 PDF + 1 音频
        assert entry["numbered_pdf"] is None
        assert entry["audio_versions"]["壞曲版"]["url"] is None
        assert entry["audio_versions"]["壞曲版"]["_unavailable"] == api_client.UNAVAILABLE_HTTP
        assert api_client.count_unavailable([entry]) == 2
        assert set(entry["_unavailable"]) == {"简谱", "壞曲版"}
        assert api_client.is_available(entry["audio_versions"]["鋼琴版"]) is True

    def test_network_failure_is_retryable_class(self, monkeypatch):
        entry = api_client.to_probe_entry(_rec())
        monkeypatch.setattr(probe, "check_url",
                            lambda url, retries=None, timeout=None: {
                                "ok": False, "_http_status": None, "_error": "ReadTimeout"})
        probe._apply_url_precheck([entry])
        assert entry["audio_versions"]["鋼琴版"]["_unavailable"] == api_client.UNAVAILABLE_NETWORK


# ================= 2. Step 2 断网不崩 + 可续跑 =================

class TestStep2Offline:
    def test_all_fail_but_no_exception(self, tmp_path, monkeypatch):
        """真断网 + 禁用分页缓存 → 每首登记 failed，不抛异常，进度文件可续跑"""
        _fresh_db(tmp_path, monkeypatch)
        monkeypatch.setattr(extractor, "PROGRESS_FILE", str(tmp_path / "step2_progress.json"))
        monkeypatch.setattr(api_client.time, "sleep", lambda *_: None)

        def boom(*a, **k):
            raise ConnectionError("network down")

        monkeypatch.setattr(api_client.requests, "get", boom)
        songs = [{"hymn_number": "12", "title": "x", "url": "u"}]
        result = extractor.Extractor(engine="api").extract_all(
            songs, resume=False, engine="api", use_cache=False)
        assert result == {"success": 0, "failed": 1, "skipped": 0}
        with open(extractor.PROGRESS_FILE, encoding="utf-8") as f:
            assert json.load(f) == {"done": []}

    def test_offline_but_cache_makes_step2_work(self, tmp_path, monkeypatch):
        """断网但 api_cache/ 命中 → Step 2 仍能完成（离线复现能力，多一层容错）"""
        monkeypatch.setattr(api_client, "API_CACHE", True)
        monkeypatch.setattr(api_client, "API_CACHE_DIR", str(tmp_path / "api_cache"))
        payload = {"data": [_rec("12")], "last_page": 1, "total": 1}
        api_client.cache_put(1, payload)
        _fresh_db(tmp_path, monkeypatch)
        monkeypatch.setattr(extractor, "PROGRESS_FILE", str(tmp_path / "p.json"))
        monkeypatch.setattr(api_client.time, "sleep", lambda *_: None)

        def boom(*a, **k):
            raise ConnectionError("network down")

        monkeypatch.setattr(api_client.requests, "get", boom)
        songs = [{"hymn_number": "12", "title": "x", "url": "u"}]
        result = extractor.Extractor(engine="api").extract_all(songs, resume=False, engine="api")
        assert result["success"] == 1

    def test_progress_resume_skips_done(self, tmp_path, monkeypatch):
        monkeypatch.setattr(extractor, "PROGRESS_FILE", str(tmp_path / "step2_progress.json"))
        extractor.save_progress({"12"})
        monkeypatch.setattr(api_client.time, "sleep", lambda *_: None)

        def boom(*a, **k):
            raise ConnectionError("down")

        monkeypatch.setattr(api_client.requests, "get", boom)
        monkeypatch.setattr(api_client, "fetch_all", lambda **k: [])
        monkeypatch.setattr(api_client, "fetch_hymns", lambda nos, **k: {})
        songs = [{"hymn_number": "12", "title": "x", "url": "u"},
                 {"hymn_number": "13", "title": "y", "url": "u"}]
        result = extractor.Extractor(engine="api").extract_all(songs, engine="api")
        assert result["skipped"] == 1 and result["failed"] == 1
        extractor.clear_progress()


# ================= 3. null URL 不产生下载请求 =================

class TestNoDownloadForNullUrl:
    def test_null_url_not_in_expected_and_not_requested(self, tmp_path, monkeypatch):
        rec = _rec("999999", audio_files=[
            {"file_url": None, "audio_category": {"name": "人聲"}},
            {"file_url": "", "audio_category": {"name": "四部合唱"}},
        ])
        audio = api_client.to_audio_versions(rec)
        assert all(not api_client.is_available(info) for k, info in audio.items()
                   if naming.is_audio_version_key(k))

        calls = []

        def fake_get(url, **k):
            calls.append(url)
            pytest.fail("不应发起下载请求")

        monkeypatch.setattr(downloader.requests, "get", fake_get)
        monkeypatch.setattr(downloader, "SAVE_ROOT", str(tmp_path))
        downloader.run_download([api_client.to_probe_entry(rec)])   # 不请求、不崩
        assert calls == []

    def test_62_style_404_excluded_from_downloads(self, tmp_path, monkeypatch):
        """#62 型：URL 存在但预检 404 → 摘除后 downloader 不请求该 URL"""
        entry = api_client.to_probe_entry(_rec("62"))
        entry["audio_versions"]["人聲版"] = {"url": "https://e.org/a/404.m4a", "ext": "m4a"}

        def fake_check(url, retries=None, timeout=None):
            bad = "404" in url
            return {"ok": not bad, "_http_status": 404 if bad else 200, "_error": None}

        monkeypatch.setattr(probe, "check_url", fake_check)
        probe._apply_url_precheck([entry])
        assert api_client.is_available(entry["audio_versions"]["人聲版"]) is False

        requested = []

        def fake_get(url, **k):
            requested.append(url)
            pytest.fail("404 资源不应下载")

        monkeypatch.setattr(downloader, "SAVE_ROOT", str(tmp_path))
        monkeypatch.setattr(downloader.requests, "get", fake_get)
        downloader.run_download([entry])
        assert requested == []


# ================= 4. API 结构被改（缺 lyrics）不崩 =================

class TestApiSchemaChange:
    def test_validate_reports_missing_lyrics(self):
        problems = api_client.validate_record({"no": "1", "name": "x",
                                               "sheet_score_pdf_url": "u",
                                               "num_score_pdf_url": "u"})
        assert "lyrics" in problems

    def test_extractor_records_failed_without_crash(self, tmp_path, monkeypatch):
        _fresh_db(tmp_path, monkeypatch)
        monkeypatch.setattr(extractor, "PROGRESS_FILE", str(tmp_path / "p.json"))

        broken = {"no": "12", "name": "x", "sheet_score_pdf_url": "u", "num_score_pdf_url": "u"}
        monkeypatch.setattr(api_client, "fetch_all", lambda **k: [broken])
        monkeypatch.setattr(api_client, "fetch_hymns", lambda nos, **k: {})
        songs = [{"hymn_number": "12", "title": "x", "url": "u"}]
        result = extractor.Extractor(engine="api").extract_all(songs, resume=False, engine="api")
        assert result["failed"] == 1 and result["success"] == 0

    def test_to_db_record_survives_broken_types(self):
        rec = {"no": "1", "name": "x", "lyrics": "oops", "lyrics_chorus": None,
               "history": 123, "lyricists": "not-a-list", "composers": [None],
               "sheet_score_pdf_url": "u", "num_score_pdf_url": "u"}
        data = api_client.to_db_record(rec)         # 不抛异常
        assert data["verses"] == [""] * 10 and data["lyricist"] == "Unknown"
        assert api_client.to_lyrics(rec) == ([], "")
        assert api_client.to_source_info(rec).startswith("123")
