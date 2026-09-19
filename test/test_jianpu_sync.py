#!/usr/bin/env python3
"""pytest 单元测试：简谱数据入库的**增量同步编排**（`crawler_core/jianpu_sync.py`）

覆盖:
  - v9 曲谱：`score_pending_numbers`（未入库 / `pdf_path` 变化 / v9 表缺失）、`sync_scores`
    （增量写库 / `--dry-run` / `--force` / `on_rec` / 单首异常不阻断 / 无谱行进报告）
  - v8 PPT：`ppt_pending_files`（按 `src_md5` 判增量）、`sync_ppt_jianpu`（写库 / 目录缺失返回空）
  - crawler_api 接入：`--step 12|score`、`--step 13|jianpu`、`--force` 传递、全流程（菜单 7）末尾调用

素材策略：**不依赖真实 PDF / PPT**——`pdf_jianpu._pdf_index` 与
`pdf_score.build_score` / `ppt_jianpu.extract_all` 就地 monkeypatch，
被测的是「判据 + 写库 + 报告」这套编排逻辑（真实抽取已由 test_pdf_score / test_jianpu 覆盖）。

运行: /home/zjx/python_env/bin/python -m pytest -c config/pytest.ini test/test_jianpu_sync.py -v
"""
import hashlib
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（test/ 的上级）
sys.path.insert(0, ROOT)
os.chdir(ROOT)


from crawler_core import db
from crawler_core import jianpu_sync as jw
from crawler_core import pdf_score as S


def _make_db(tmp_path, rows=()):
    """临时库：`tjc_hymn`（全字段）+ 指定编号行"""
    path = str(tmp_path / "t.db")
    conn = sqlite3.connect(path)
    c = conn.cursor()
    db._create_table_v4(c)
    for no in rows:
        c.execute("INSERT INTO tjc_hymn (hymn_number, audio_versions, audio_version_list) "
                  "VALUES (?, '{}', '[]')", (str(no),))
    conn.commit()
    conn.close()
    return path


def _fake_index(tmp_path, numbers):
    """造假简谱 PDF 文件并返回 {编号: 绝对路径}（供 `P._pdf_index` monkeypatch）"""
    out = {}
    for n in numbers:
        p = tmp_path / f"{n}_简谱.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        out[str(n)] = str(p)
    return out


def _score_rec(num, lines=1, pdf_path=""):
    """最小可入库的 `ScoreRecord`（主旋律 1 行 / 2 字 2 音；`pdf_path` 需与假索引同口径）"""
    return S.ScoreRecord(
        hymn_number=str(num), pdf_path=pdf_path or f"Hymn_Downloads/x/{num}_简谱.pdf",
        page_count=1, phrase_count=1, line_count=lines, lyric_count=1,
        beat_total=2 * lines, syllable_total=2 * lines, align_ok=1,
        lines=[{"line_no": i + 1, "phrase_no": 1, "part": "主旋律", "notes": "1 2",
                "notes_core": "1 2", "code_seq": "", "beat_count": 2, "note_count": 2,
                "hold_count": 0, "rest_count": 0, "syllable_count": 2, "count_delta": 0,
                "is_primary": 1, "align_ok": 1} for i in range(lines)],
        lyrics=[{"line_no": 1, "stanza_no": 1, "text": "一二", "syllable_count": 2,
                 "align_ok": 1}],
        chars=[{"line_no": 1, "char_no": 1, "syllable": "一", "note": "1", "beat": 0,
                "delta": 0.0, "span": 1}],
    )


def _score_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        try:
            return conn.execute("SELECT hymn_number, pdf_path FROM hymn_score "
                                "ORDER BY hymn_number").fetchall()
        except sqlite3.OperationalError:  # v9 表尚未创建
            return []
    finally:
        conn.close()


def _count(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:  # 表尚未创建
            return 0
    finally:
        conn.close()


def _md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


def _ppt_rec(name, ppt_dir):
    """最小可入库的 PPT 解析记录（1 行「记号 + 歌词」）"""
    return {
        "ppt_file": name, "hymn_number": "1", "version": "甲",
        "ppt_old_no": "1", "ppt_new_no": "1", "title": "頌讚獨一真神",
        "title_line": "頌讚獨一真神", "key_sig": "C", "time_sig": "4/4", "tempo": "4/4",
        "slide_count": 1, "chorus_slides": 0, "pair_count": 1, "note_total": 2,
        "tune_period": 1, "title_match": "ok", "title_score": 1.0, "verse_match": "ok",
        "verse_score": 1.0, "marker_ok": 1, "structure_ok": 1, "tune_ok": 1, "align_ok": 1,
        "review_reason": "", "src_md5": _md5(os.path.join(ppt_dir, name)),
        "extractor": "test/1.0",
        "lines": [{"hymn_number": "1", "stanza_no": 1, "line_no": 1, "verse_no": 1,
                   "is_chorus": 0, "label": "", "notes": "12", "lyric": "一二",
                   "note_count": 2, "rest_count": 0, "syllable_count": 2,
                   "count_delta": 0, "align_ok": 1}],
    }


def _patch_index(monkeypatch, index):
    """把 `pdf_jianpu._pdf_index` 固定为给定索引（跳过真实目录扫描）"""
    monkeypatch.setattr(jw.P, "_pdf_index", lambda root=None: index)


def _patch_build(monkeypatch, fn):
    """替换 `pdf_score.build_score`（jianpu_sync 内按名引用 `S.build_score`）"""
    monkeypatch.setattr(jw.S, "build_score", fn)


def _builder(index):
    """假 build_score：按索引路径生成记录（`pdf_path` 与 `score_pending_numbers` 同口径）"""
    def fake(num, **kw):
        return _score_rec(num, pdf_path=jw._rel_pdf(index[str(num)]))

    return fake


# ================= v9：待处理判据 =================

class TestScorePending:
    def test_detects_missing_and_changed(self, tmp_path, monkeypatch):
        """未入库 / `pdf_path` 已变 → 待处理；已入库且路径一致 → 跳过"""
        index = _fake_index(tmp_path, [1, 2, 3])
        _patch_index(monkeypatch, index)
        db_path = _make_db(tmp_path, [1, 2, 3])
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        db.ensure_score_tables(c)
        c.execute("INSERT INTO hymn_score (hymn_number, pdf_path) VALUES ('1', ?)",
                  (jw._rel_pdf(index["1"]),))
        c.execute("INSERT INTO hymn_score (hymn_number, pdf_path) VALUES ('2', '旧路径.pdf')")
        conn.commit()
        conn.close()

        assert jw.score_pending_numbers(db_path) == ["2", "3"]

    def test_all_done(self, tmp_path, monkeypatch):
        index = _fake_index(tmp_path, [7])
        _patch_index(monkeypatch, index)
        db_path = _make_db(tmp_path, [7])
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        db.ensure_score_tables(c)
        c.execute("INSERT INTO hymn_score (hymn_number, pdf_path) VALUES ('7', ?)",
                  (jw._rel_pdf(index["7"]),))
        conn.commit()
        conn.close()
        assert jw.score_pending_numbers(db_path) == []

    def test_without_v9_tables_all_pending(self, tmp_path, monkeypatch):
        """v9 表还没建（老库首次跑）→ 全部有 PDF 的编号都待处理"""
        _patch_index(monkeypatch, _fake_index(tmp_path, [5, 10]))
        db_path = _make_db(tmp_path, [5, 10])
        assert jw.score_pending_numbers(db_path) == ["5", "10"]


# ================= v9：增量入库 =================

class TestSyncScores:
    def test_incremental_writes_then_idempotent(self, tmp_path, monkeypatch):
        index = _fake_index(tmp_path, [1, 2])
        _patch_index(monkeypatch, index)
        _patch_build(monkeypatch, _builder(index))
        db_path = _make_db(tmp_path, [1, 2])
        seen = []

        first = jw.sync_scores(db_path=db_path, mapping={},
                               on_rec=lambda rec, i, total: seen.append(
                                   (i, total, rec.hymn_number)))

        assert (first["pending"], first["processed"], first["hymns"]) == (2, 2, 2)
        assert first["lines"] == 2 and first["chars"] == 2 and first["align_ok"] == 2
        assert seen == [(1, 2, "1"), (2, 2, "2")]
        assert [n for n, _ in _score_rows(db_path)] == ["1", "2"]
        assert _count(db_path, "hymn_score_line") == 2

        second = jw.sync_scores(db_path=db_path, mapping={})   # 增量：无待处理
        assert (second["pending"], second["processed"], second["hymns"]) == (0, 0, 0)
        assert _count(db_path, "hymn_score_line") == 2

    def test_dry_run_keeps_db(self, tmp_path, monkeypatch):
        index = _fake_index(tmp_path, [1])
        _patch_index(monkeypatch, index)
        _patch_build(monkeypatch, _builder(index))
        db_path = _make_db(tmp_path, [1])

        summary = jw.sync_scores(db_path=db_path, dry_run=True)

        assert (summary["pending"], summary["processed"], summary["hymns"]) == (1, 1, 0)
        assert _score_rows(db_path) == []

    def test_force_reprocesses_all(self, tmp_path, monkeypatch):
        index = _fake_index(tmp_path, [1, 2])
        _patch_index(monkeypatch, index)
        _patch_build(monkeypatch, _builder(index))
        db_path = _make_db(tmp_path, [1, 2])
        jw.sync_scores(db_path=db_path)
        forced = jw.sync_scores(db_path=db_path, force=True)
        assert (forced["pending"], forced["processed"], forced["hymns"]) == (2, 2, 2)

    def test_numbers_and_limit(self, tmp_path, monkeypatch):
        index = _fake_index(tmp_path, [1, 2, 3])
        _patch_index(monkeypatch, index)
        _patch_build(monkeypatch, _builder(index))
        db_path = _make_db(tmp_path, [1, 2, 3])
        jw.sync_scores(numbers=["2"], db_path=db_path)
        assert [n for n, _ in _score_rows(db_path)] == ["2"]
        limited = jw.sync_scores(limit=1, db_path=db_path)     # 剩 1、3 → 只处理 1
        assert limited["processed"] == 1
        assert [n for n, _ in _score_rows(db_path)] == ["1", "2"]

    def test_build_error_does_not_abort_batch(self, tmp_path, monkeypatch):
        index = _fake_index(tmp_path, [1, 2])

        def fake_build(num, **kw):
            if num == "1":
                return _score_rec(num, pdf_path=jw._rel_pdf(index[str(num)]))
            raise ValueError("boom")

        _patch_index(monkeypatch, index)
        _patch_build(monkeypatch, fake_build)
        db_path = _make_db(tmp_path, [1, 2])
        summary = jw.sync_scores(db_path=db_path)
        assert summary["hymns"] == 1 and [n for n, _ in _score_rows(db_path)] == ["1"]
        assert any(num == "2" and "ValueError" in reason for num, reason in summary["skipped"])

    def test_records_without_lines_are_reported(self, tmp_path, monkeypatch):
        """无谱行（如 #349 异版 PDF）→ 进 skipped 报告；dry-run 也要给出原因"""
        _patch_index(monkeypatch, _fake_index(tmp_path, [349]))
        _patch_build(monkeypatch, lambda num, **kw: S.ScoreRecord(
            hymn_number=str(num), review_reason="未识别到谱层（元素数均 < 8）"))
        db_path = _make_db(tmp_path, [349])
        reason = "未识别到谱层（元素数均 < 8）"

        dry = jw.sync_scores(db_path=db_path, dry_run=True)
        assert dry["skipped"] == [("349", reason)]

        real = jw.sync_scores(db_path=db_path)
        assert real["hymns"] == 0 and real["skipped"] == [("349", reason)]


# ================= v8：PPT 增量入库 =================

def _patch_extract(monkeypatch):
    """替换 `ppt_jianpu.extract_all`：按 `only` 过滤假记录（不读真实 PPT）"""
    def fake_extract(ppt_dir, db_path=None, only=None):
        want = {str(o).lower().removesuffix(".ppt") for o in (only or [])}
        names = sorted(f for f in os.listdir(ppt_dir) if f.lower().endswith(".ppt"))
        recs = [_ppt_rec(n, ppt_dir) for n in names
                if not want or n[:-4].lower() in want]
        return recs, {"files": len(recs)}

    monkeypatch.setattr(jw.J, "extract_all", fake_extract)


class TestPptSync:
    def test_md5_based_incremental(self, tmp_path, monkeypatch):
        """首轮全待处理 → 入库后 `src_md5` 一致即跳过 → 源文件改动后再次待处理"""
        ppt_dir = tmp_path / "ppt"
        ppt_dir.mkdir()
        (ppt_dir / "001.ppt").write_bytes(b"ppt-A-v1")
        (ppt_dir / "002.ppt").write_bytes(b"ppt-B-v1")
        db_path = _make_db(tmp_path, [1])
        _patch_extract(monkeypatch)

        assert jw.ppt_pending_files(str(ppt_dir), db_path) == ["001.ppt", "002.ppt"]

        summary = jw.sync_ppt_jianpu(ppt_dir=str(ppt_dir), db_path=db_path)

        assert (summary["total"], summary["pending"], summary["hymns"]) == (2, 2, 2)
        assert summary["lines"] == 2
        assert _count(db_path, "hymn_jianpu") == 2
        assert jw.ppt_pending_files(str(ppt_dir), db_path) == []      # 幂等

        (ppt_dir / "001.ppt").write_bytes(b"ppt-A-v2")                # 素材更新 → 重算该份
        assert jw.ppt_pending_files(str(ppt_dir), db_path) == ["001.ppt"]

    def test_dry_run_does_not_write(self, tmp_path, monkeypatch):
        ppt_dir = tmp_path / "ppt"
        ppt_dir.mkdir()
        (ppt_dir / "001.ppt").write_bytes(b"ppt")
        db_path = _make_db(tmp_path, [1])
        _patch_extract(monkeypatch)

        summary = jw.sync_ppt_jianpu(ppt_dir=str(ppt_dir), db_path=db_path, dry_run=True)

        assert (summary["pending"], summary["processed"], summary["hymns"]) == (1, 1, 0)
        assert _count(db_path, "hymn_jianpu") == 0

    def test_missing_dir_returns_zeros(self, tmp_path):
        db_path = _make_db(tmp_path, [1])
        summary = jw.sync_ppt_jianpu(ppt_dir=str(tmp_path / "nope"), db_path=db_path)
        assert summary == {"total": 0, "pending": 0, "processed": 0, "hymns": 0,
                           "lines": 0, "skipped": [], "seconds": 0.0}
        assert jw.ppt_pending_files(str(tmp_path / "nope"), db_path) == []


# ================= crawler_api 全链接入 =================

class TestCrawlerApiWiring:
    def _summary(self, **over):
        base = {"pending": 0, "processed": 0, "hymns": 0, "lines": 0, "lyrics": 0,
                "chars": 0, "align_ok": 0, "skipped": [], "seconds": 0.0,
                "total": 0}
        base.update(over)
        return base

    def test_step_12_score_and_13_jianpu(self, monkeypatch):
        import crawler_api

        calls = []
        monkeypatch.setattr(crawler_api, "run_step_scores",
                            lambda *a, **kw: calls.append(("score", kw)) or self._summary())
        monkeypatch.setattr(crawler_api, "run_step_jianpu",
                            lambda *a, **kw: calls.append(("jianpu", kw)) or self._summary())

        assert crawler_api._run_single_step("12", "api", True) == 0
        assert crawler_api._run_single_step("score", "api", True) == 0
        assert crawler_api._run_single_step("13", "api", True) == 0
        assert crawler_api._run_single_step("jianpu", "api", True) == 0
        assert [c[0] for c in calls] == ["score", "score", "jianpu", "jianpu"]

        calls.clear()
        crawler_api._run_single_step("12", "api", True, force=True)   # --force 传递
        assert calls[0] == ("score", {"force": True})

    def test_full_pipeline_ends_with_scores(self, monkeypatch):
        import crawler_api

        called = []

        def record(name, ret=None):
            def _fn(*a, **kw):
                called.append(name)
                return ret
            return _fn

        for name, ret in (("run_step1", [{"x": 1}]), ("run_step2", None), ("run_probe", []),
                          ("run_download", None), ("run_step4", None), ("run_step5", None),
                          ("run_step7", None), ("run_step6", None), ("run_step_audio", None),
                          ("run_step_scores", None), ("run_step_jianpu", None)):
            monkeypatch.setattr(crawler_api, name, record(name, ret))

        crawler_api.run_step10_full("api")
        assert called[-1] == "run_step_scores"

        called.clear()
        monkeypatch.setattr(crawler_api, "_ask_yes_no", lambda *a, **kw: False)
        crawler_api._dispatch("7", "api", 0, True)
        assert called[-1] == "run_step_scores"

        called.clear()
        crawler_api._dispatch("13", "api", 0, True)
        assert called == ["run_step_jianpu"]
