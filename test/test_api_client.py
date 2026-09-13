#!/usr/bin/env python3
"""pytest 单元测试：官网 JSON API 客户端（离线 fixture，不联网 / 不依赖 Selenium）

覆盖（§8 测试计划 1）：
  - 分页拼接（多页 fixture → 顺序/数量）与 page_records 容错
  - 字段映射：lyrics → verses/chorus、history → source_info、audio_files → 版本名
  - 目录名规则与既有 url_map.txt 的一致性（用 api_cache 离线复现，474 行逐条比对）
  - 重试退避：500 → 重试、404 → 立即放弃、超时 → 类型名进错误信息
  - 磁盘缓存：写入/命中/清理
  - 可用性状态机：api_null / http_4xx / `.mp4 → .m4a` / site_removed / 同名版本
  - DB 映射：to_db_record（10 节补齐）、to_probe_entry（结构不变）
  - api_raw 取值助手与增量判定

运行: /home/zjx/python_env/bin/python -m pytest test/test_api_client.py -v
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（test/ 的上级）
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from crawler_core import api_client, naming

# ================= fixture：最小 API 记录 =================

def make_rec(**over):
    rec = {
        "no": "12",
        "name": "耶穌尊名",
        "lyrics": [{"text": "第一節\r\n第二行"}, {"text": "第三節"}],
        "lyrics_chorus": "副歌\r\n",
        "sheet_score_pdf_url": "https://example.org/sheet/12.pdf",
        "num_score_pdf_url": "https://example.org/num/12.pdf",
        "history": "<p>源考一段&ldquo;引文&rdquo;</p><p>第二段<br />換行</p>",
        "lyricists": [{"name": "Issac Watts"}],
        "composers": [{"name": "Austin C. Lovelace"}, {"name": "某人"}],
        "category": {"id": 2, "name": "讚美耶穌", "slug": None,
                     "updated_at": "2022-07-05T12:00:00.000000Z"},
        "tags": [],
        "youtube_urls": [{"label": "12", "url": "https://youtu.be/x"}],
        "audio_files": [
            {"file_url": "https://example.org/audio/12.m4a",
             "audio_category": {"id": 1, "name": "鋼琴"}},
            {"file_url": "https://example.org/audio/abc.mp3",
             "audio_category": {"id": 4, "name": "人聲"}},
        ],
        "updated_at": "2023-07-26T03:08:19.000000Z",
        "prev_no": "11", "next_no": "13",
    }
    rec.update(over)
    return rec


class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def close(self):
        pass


# ================= 分页 =================

class TestPaging:
    def test_page_records_shapes(self):
        assert api_client.page_records({"data": [1, 2]}) == [1, 2]
        assert api_client.page_records([3]) == [3]
        assert api_client.page_records({"data": "oops"}) == []
        assert api_client.page_records(None) == []

    def test_fetch_all_orders_pages(self, monkeypatch):
        pages = {
            1: {"data": [{"no": "1"}, {"no": "2"}], "last_page": 3, "total": 6},
            2: {"data": [{"no": "3"}, {"no": "4"}], "last_page": 3, "total": 6},
            3: {"data": [{"no": "5"}, {"no": "6"}], "last_page": 3, "total": 6},
        }
        monkeypatch.setattr(api_client, "fetch_page",
                            lambda p, use_cache=None, refresh=False: pages[p])
        recs = api_client.fetch_all(progress=False)
        assert [r["no"] for r in recs] == ["1", "2", "3", "4", "5", "6"]

    def test_fetch_all_survives_broken_page(self, monkeypatch):
        def fake_page(page, use_cache=None, refresh=False):
            if page == 2:
                raise api_client.ApiError("boom")
            return {"data": [{"no": "1"}], "last_page": 2, "total": 2}

        monkeypatch.setattr(api_client, "fetch_page", fake_page)
        assert len(api_client.fetch_all(progress=False)) == 1  # 单页失败不影响其它页

    def test_iter_hymns_limit(self, monkeypatch):
        monkeypatch.setattr(api_client, "fetch_all",
                            lambda **k: [{"no": str(i)} for i in range(25)])


# ================= 字段映射 =================

class TestMapping:
    def test_to_lyrics(self):
        verses, chorus = api_client.to_lyrics(make_rec())
        assert verses == ["第一節\n第二行", "第三節"]  # CRLF 归一；空歌词项被过滤
        assert chorus == "副歌"

    def test_to_source_info_html(self):
        text = api_client.to_source_info(make_rec())
        assert text == "源考一段“引文”\n第二段\n換行"   # 实体反转义 + 去标签 + <br> 换行

    def test_to_source_info_empty(self):
        assert api_client.to_source_info({"history": None}) == ""
        assert api_client.to_source_info({"history": "   "}) == ""

    def test_names_joined_and_fallback(self):
        data = api_client.to_db_record(make_rec())
        assert data["lyricist"] == "Issac Watts"
        assert data["composer"] == "Austin C. Lovelace、某人"
        fallback = api_client.to_db_record(make_rec(lyricists=[], composers=None))
        assert fallback["lyricist"] == "Unknown" and fallback["composer"] == "Unknown"

    def test_to_metadata_fields(self):
        meta = api_client.to_metadata(make_rec())
        assert meta["category_name"] == "讚美耶穌"
        assert meta["youtube_urls"] == ["https://youtu.be/x"]
        assert meta["updated_at"].startswith("2023-07-26")
        assert meta["prev_no"] == "11" and meta["next_no"] == "13"

    def test_to_song_shape(self):
        song = api_client.to_song(make_rec(), 12)
        assert song == {"seq_num": "012", "hymn_number": "12", "title": "耶穌尊名",
                        "url": "https://sacredmusic.tjc.org.tw/hymn/12"}

    def test_to_db_record_pads_verses(self):
        rec = make_rec(lyrics=[{"text": f"v{i}"} for i in range(12)])
        data = api_client.to_db_record(rec)
        assert data["verse_count"] == 10 and len(data["verses"]) == 10
        assert data["verses"][0] == "v0" and data["verses"][-1] == "v9"
        assert data["chorus"] == "副歌"
        assert json.loads(data["api_raw"])["no"] == "12"

    def test_to_probe_entry_structure(self):
        entry = api_client.to_probe_entry(make_rec())
        assert set(entry) == {"hymn_number", "title", "staff_pdf", "numbered_pdf", "audio_versions"}
        assert entry["staff_pdf"].endswith("/sheet/12.pdf")
        assert set(entry["audio_versions"]) == {"鋼琴版", "人聲版"}

    def test_validate_record(self):
        assert api_client.validate_record(make_rec()) == []
        problems = api_client.validate_record({"no": "1", "name": "", "lyrics": []})
        assert "name" in problems and "lyrics:empty" in problems
        assert "sheet_score_pdf_url" in problems
        assert api_client.validate_record({"no": "1", "lyrics": "oops"}) != []
        assert api_client.validate_record(None) == ["record:not_dict"]


# ================= 目录名 / 文件名规则 =================

class TestNaming:
    def test_naming_helpers(self):
        assert naming.sanitize("12耶穌尊名!?") == "12耶穌尊名"
        assert naming.to_dirname(51, "51_a", "萬古靈磐甲") == "051_51_a萬古靈磐甲"
        assert naming.pdf_name("12", "staff") == "12_五线谱.pdf"
        assert naming.pdf_name("12", "numbered") == "12_简谱.pdf"
        assert naming.audio_name("201", "人聲", "mp4") == "201_人聲版.m4a"
        assert naming.audio_version_name("鋼琴") == "鋼琴版"
        assert naming.audio_version_name("鋼琴版") == "鋼琴版"
        assert naming.normalize_audio_ext(".MP4") == "m4a"
        assert naming.rel_path("001_1頌讚", "1_五线谱.pdf") == \
            os.path.join("Hymn_Downloads", "001_1頌讚", "1_五线谱.pdf")
        assert naming.is_audio_version_key("鋼琴版") is True
        assert naming.is_audio_version_key("_duplicates") is False

    def test_dirname_matches_url_map(self):
        """用 api_cache（git 跟踪的 48 页）复算目录名，与 url_map.txt 逐条比对（474/474）"""
        pages = api_client.cache_pages()
        if not pages:
            pytest.skip("api_cache/ 不存在")
        records = []
        for page in pages:
            records.extend(api_client.page_records(api_client.cache_get(page)))
        with open(os.path.join(ROOT, "Hymn_Downloads", "url_map.txt"), encoding="utf-8") as f:
            map_lines = [ln.strip().split("|") for ln in f if ln.strip()]
        assert len(records) == len(map_lines)

        mismatch = [(idx, parts[1], api_client.to_dirname(idx, rec))
                    for idx, (rec, parts) in enumerate(zip(records, map_lines), 1)
                    if parts[1] != api_client.to_dirname(idx, rec)]
        assert mismatch == [], f"目录名规则不匹配: {mismatch[:5]}"



# ================= 重试退避 =================

class TestRetry:
    def test_retries_then_success(self, monkeypatch):
        monkeypatch.setattr(api_client.time, "sleep", lambda *_: None)
        calls = {"n": 0}

        def fake_get(url, **kwargs):
            calls["n"] += 1
            return _FakeResp(500) if calls["n"] < 3 else _FakeResp(200, {"ok": True})

        monkeypatch.setattr(api_client.requests, "get", fake_get)
        resp = api_client.fetch_with_retry("https://example.org/api", retries=3)
        assert resp.status_code == 200 and calls["n"] == 3

    def test_4xx_not_retried(self, monkeypatch):
        monkeypatch.setattr(api_client.time, "sleep", lambda *_: None)
        calls = {"n": 0}

        def fake_get(url, **kwargs):
            calls["n"] += 1
            return _FakeResp(404)

        monkeypatch.setattr(api_client.requests, "get", fake_get)
        with pytest.raises(api_client.ApiError) as err:
            api_client.fetch_with_retry("https://example.org/api", retries=3)
        assert err.value.status_code == 404
        assert calls["n"] == 1  # 真缺失 → 不重试

    def test_timeout_reports_type(self, monkeypatch):
        monkeypatch.setattr(api_client.time, "sleep", lambda *_: None)

        def boom(*a, **k):
            raise TimeoutError("boom")

        monkeypatch.setattr(api_client.requests, "get", boom)
        with pytest.raises(api_client.ApiError) as err:
            api_client.fetch_with_retry("https://example.org/api", retries=2)
        assert "TimeoutError" in str(err.value)

    def test_fetch_hymn_404_returns_none(self, monkeypatch):
        monkeypatch.setattr(api_client.time, "sleep", lambda *_: None)
        monkeypatch.setattr(api_client.requests, "get", lambda *a, **k: _FakeResp(404))
        assert api_client.fetch_hymn("999") is None


# ================= 磁盘缓存 =================

class TestCache:
    def test_put_get_clear(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api_client, "API_CACHE_DIR", str(tmp_path / "api_cache"))
        assert api_client.cache_get(1) is None
        api_client.cache_put(1, {"data": [{"no": "1"}], "last_page": 1})
        assert api_client.cache_pages() == [1]
        assert api_client.cache_get(1)["data"][0]["no"] == "1"
        assert api_client.cache_clear() == 1
        assert api_client.cache_pages() == []

    def test_corrupted_cache_ignored(self, tmp_path, monkeypatch):
        cache_dir = tmp_path / "api_cache"
        cache_dir.mkdir()
        (cache_dir / "page_01.json").write_text("{ not json", encoding="utf-8")
        monkeypatch.setattr(api_client, "API_CACHE_DIR", str(cache_dir))
        assert api_client.cache_get(1) is None

    def test_fetch_page_uses_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api_client, "API_CACHE_DIR", str(tmp_path / "api_cache"))
        calls = {"n": 0}

        def fake_fetch_json(url, params=None, **k):
            calls["n"] += 1
            return {"data": [], "last_page": 1, "total": 0}

        monkeypatch.setattr(api_client, "fetch_json", fake_fetch_json)
        api_client.fetch_page(1)
        api_client.fetch_page(1)
        assert calls["n"] == 1  # 第二次命中缓存

        api_client.fetch_page(1, refresh=True)
        assert calls["n"] == 2  # refresh 强制刷新



# ================= 资源可用性状态机（§5.9.3 / §5.9.4） =================

class TestAudioVersions:
    def test_basic_versions_and_ext(self):
        av = api_client.to_audio_versions(make_rec())
        assert av["鋼琴版"]["ext"] == "m4a"
        assert av["人聲版"]["ext"] == "mp3"
        assert av["鋼琴版"]["url"].endswith("12.m4a")

    def test_mp4_normalized_to_m4a(self):
        rec = make_rec(audio_files=[{"file_url": "https://e.org/a/9821.mp4",
                                     "audio_category": {"name": "人聲"}}])
        av = api_client.to_audio_versions(rec)
        assert av["人聲版"]["ext"] == "m4a"
        assert naming.audio_name("201", "人聲", av["人聲版"]["ext"]) == "201_人聲版.m4a"

    def test_null_url_is_unavailable(self):
        rec = make_rec(audio_files=[{"file_url": None, "audio_category": {"name": "人聲"}}])
        av = api_client.to_audio_versions(rec)
        assert av["人聲版"]["url"] is None
        assert av["人聲版"]["_unavailable"] == api_client.UNAVAILABLE_API_NULL
        assert api_client.is_available(av["人聲版"]) is False
        entry = {"hymn_number": "12", "audio_versions": av, "staff_pdf": "u", "numbered_pdf": "u"}
        assert api_client.unavailable_items(entry)["人聲版"]["_unavailable"] == "api_null"
        assert api_client.count_unavailable([entry]) == 1

    def test_duplicate_version_last_wins(self):
        rec = make_rec(audio_files=[
            {"file_url": "https://e.org/a/first.m4a", "audio_category": {"name": "鋼琴"}},
            {"file_url": "https://e.org/a/last.mp3", "audio_category": {"name": "鋼琴"}},
        ])
        av = api_client.to_audio_versions(rec)
        assert av["鋼琴版"]["url"].endswith("last.mp3")
        assert av["_duplicates"][0]["url"].endswith("first.m4a")
        assert naming.is_audio_version_key("_duplicates") is False

    def test_site_removed_keeps_local_archive(self, tmp_path):
        rec = make_rec(audio_files=[{"file_url": "https://e.org/a/piano.mp3",
                                     "audio_category": {"name": "鋼琴"}}])
        (tmp_path / "12_人聲版.mp3").write_bytes(b"ID3")
        previous = {"人聲版": {"url": "https://e.org/a/old.mp3", "ext": "mp3", "filename": "old.mp3"}}
        av = api_client.to_audio_versions(rec, local_dir=str(tmp_path), previous=previous)
        assert av["人聲版"]["_site_removed"] is True
        assert av["人聲版"]["url"] is None
        assert api_client.unavailable_reason(av["人聲版"]) == "site_removed"
        # site_removed 不计入「源站不可用」条数（验收：恰 10 条）
        entry = {"hymn_number": "12", "audio_versions": av}
        assert api_client.count_unavailable([entry]) == 0

    def test_previous_version_without_local_file_dropped(self, tmp_path):
        rec = make_rec(audio_files=[])
        previous = {"人聲版": {"url": "https://e.org/a/old.mp3", "ext": "mp3"}}
        av = api_client.to_audio_versions(rec, local_dir=str(tmp_path), previous=previous)
        assert av == {}

    def test_pdf_failure_counted(self):
        entry = {
            "hymn_number": "62",
            "staff_pdf": "https://e.org/sheet.pdf",
            "numbered_pdf": None,
            "audio_versions": {"人聲版": {"url": None, "_http_status": 404,
                                          "_unavailable": api_client.UNAVAILABLE_HTTP}},
            "_pdf_status": {"numbered": {"_http_status": 404, "_error": "HTTP 404"}},
        }
        items = api_client.unavailable_items(entry)
        assert set(items) == {"人聲版", "简谱"}
        assert api_client.count_unavailable([entry]) == 2


# ================= api_raw 取值助手 / 增量 =================

class TestApiRawHelpers:
    def test_raw_json_roundtrip(self):
        raw = api_client.api_raw_json(make_rec())
        assert api_client.api_field(raw, "category.name") == "讚美耶穌"
        assert api_client.api_field(raw, "no") == "12"
        assert api_client.api_updated_at(raw).startswith("2023-07-26")
        assert api_client.api_category_name(raw) == "讚美耶穌"
        assert api_client.api_youtube(raw) == ["https://youtu.be/x"]
        assert api_client.api_tags(raw) == []
        assert api_client.api_field(raw, "missing.path", "d") == "d"

    def test_parse_api_raw_bad_input(self):
        assert api_client.parse_api_raw("") == {}
        assert api_client.parse_api_raw("{ bad json") == {}
        assert api_client.parse_api_raw("[1,2]") == {}
        assert api_client.parse_api_raw({"a": 1}) == {"a": 1}

    def test_is_newer(self):
        raw = api_client.api_raw_json(make_rec())
        assert api_client.is_newer(raw, make_rec(updated_at="2024-01-01T00:00:00Z")) is True
        assert api_client.is_newer(raw, make_rec(updated_at="2023-07-26T03:08:19.000000Z")) is False
        assert api_client.is_newer(raw, {"no": "12"}) is False
        assert api_client.is_newer("", make_rec()) is True

        assert len(list(api_client.iter_hymns(page_limit=2))) == 20
