#!/usr/bin/env python3
"""pytest 单元测试：音频时长（DB v10 `audio_durations`）

覆盖:
  - `crawler_core.audio_duration`：后缀分派读取（**合成** MP3 / 最小 MP4 真文件，无需外部素材）、
    缺文件 / 非音频 / 未知后缀的失败语义、`resolve_audio_path` 相对与绝对路径
  - `tool/build_audio_durations.py`：时长键集与 `audio_versions` 一一匹配、写库 / 幂等 /
    `--dry-run` 不动库、读不出的条目落 `null` 并进报告、键集不一致报警
  - `crawler_core.db`：`ensure_audio_durations_field` 幂等补列、`audio_duration_stats` 统计

运行: /home/zjx/python_env/bin/python -m pytest -c config/pytest.ini test/test_audio_duration.py -v
      （`tool/` 由下方 sys.path.insert 注入 → `import build_audio_durations` 行带 pyright ignore 注释）
"""
import json
import os
import sqlite3
import struct
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（test/ 的上级）
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tool"))
os.chdir(ROOT)

import build_audio_durations as bad  # pyright: ignore[reportMissingImports]

from crawler_core import audio_duration as AD
from crawler_core import db
from crawler_core.naming import is_audio_version_key

MP3_SECONDS = 2.6122  # 100 帧 × 1152 采样 ÷ 44100 Hz


# ================= 合成素材（无外部依赖） =================

def _write_mp3(path, frames=100):
    """合成 MPEG1 Layer3 CBR 音频：128 kbps / 44.1 kHz，帧长 = 144*128000/44100 = 417 字节"""
    header = bytes([0xFF, 0xFB, 0x90, 0x00])
    path.write_bytes((header + b"\x00" * 413) * frames)
    return path


def _box(name, payload):
    """MP4 box：`长度(4) + 类型(4) + 载荷`"""
    return struct.pack(">I4s", len(payload) + 8, name) + payload


def _full_box(name, payload):
    """MP4 full box：box 头前插 `version(0) + flags(0)`"""
    return _box(name, b"\x00" * 4 + payload)


def _write_m4a(path, seconds=12.5, timescale=48000, audio_track=True):
    """合成最小可解析 MP4：`ftyp + moov[ mvhd, trak[ mdia[ hdlr('soun'), mdhd ] ] ]`

    mutagen 取时长优先读音频轨 `mdhd`；无 `soun` 轨时回落 `mvhd`（两条路径都测）。
    """
    ftyp = _box(b"ftyp", b"M4A \x00\x00\x00\x00M4A mp42ison")
    samples = int(seconds * timescale)
    mvhd = _full_box(b"mvhd", struct.pack(">IIII", 0, 0, timescale, samples))
    if audio_track:
        hdlr = _box(b"hdlr", b"\x00" * 8 + b"soun" + b"\x00" * 12)
        mdhd = _full_box(b"mdhd", struct.pack(">IIII", 0, 0, timescale, samples) + b"\x00" * 4)
        moov = _box(b"moov", mvhd + _box(b"trak", _box(b"mdia", hdlr + mdhd)))
    else:
        moov = _box(b"moov", mvhd)
    path.write_bytes(ftyp + moov)
    return path


def _make_db(tmp_path, rows):
    """建临时库（`_create_table_v4` 全字段）并插入记录；`rows` = [(编号, audio_versions dict), ...]"""
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    c = conn.cursor()
    db._create_table_v4(c)
    for no, versions in rows:
        real = [k for k in versions if is_audio_version_key(k)]
        c.execute(
            "INSERT INTO tjc_hymn (hymn_number, audio_versions, audio_version_list) VALUES (?, ?, ?)",
            (no, json.dumps(versions, ensure_ascii=False),
             json.dumps(real, ensure_ascii=False)),
        )
    conn.commit()
    conn.close()
    return str(path)


def _durations(db_path, no):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT audio_durations FROM tjc_hymn WHERE hymn_number = ?",
                           (no,)).fetchone()
    finally:
        conn.close()
    return json.loads(row[0])


# ================= crawler_core.audio_duration =================

class TestReader:
    def test_reads_synthetic_mp3(self, tmp_path):
        """合成 MP3（无 ID3 / 有 ID3 两种）都能按后缀分派读出时长"""
        plain = _write_mp3(tmp_path / "a.mp3")
        tagged = tmp_path / "b.mp3"
        tagged.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00" + plain.read_bytes())
        for path in (plain, tagged):
            seconds, reason = AD.read_duration_ex(str(path))
            assert reason == ""
            assert seconds == pytest.approx(MP3_SECONDS, abs=0.05)

    def test_reads_synthetic_m4a(self, tmp_path):
        """最小 MP4：优先读音频轨 mdhd；无 soun 轨时回落 mvhd（两条路径）"""
        for audio_track in (True, False):
            path = _write_m4a(tmp_path / f"t{int(audio_track)}.m4a", seconds=12.5,
                              audio_track=audio_track)
            seconds, reason = AD.read_duration_ex(str(path))
            assert reason == ""
            assert seconds == pytest.approx(12.5, abs=0.001)

    def test_missing_file(self, tmp_path):
        seconds, reason = AD.read_duration_ex(str(tmp_path / "nope.m4a"))
        assert seconds is None and "不存在" in reason
        assert AD.read_duration("") is None

    def test_broken_and_unknown_files(self, tmp_path):
        """非音频内容 / 未知后缀 → None + 说明（单个坏文件不抛异常）"""
        broken = tmp_path / "broken.mp3"
        broken.write_text("not audio at all", encoding="utf-8")
        unknown = tmp_path / "x.txt"
        unknown.write_text("text", encoding="utf-8")
        for path in (broken, unknown):
            seconds, reason = AD.read_duration_ex(str(path))
            assert seconds is None and reason
        assert AD.read_duration_ex(None)[1]

    def test_resolve_audio_path(self):
        assert AD.resolve_audio_path("") == ""
        assert AD.resolve_audio_path("/abs/x.m4a") == "/abs/x.m4a"
        rel = AD.resolve_audio_path("Hymn_Downloads/001_x/1_鋼琴版.m4a")
        assert rel.startswith(ROOT) and rel.endswith("1_鋼琴版.m4a")
        assert AD.resolve_audio_path("Hymn_Downloads/a.m4a", root="/tmp") == "/tmp/Hymn_Downloads/a.m4a"

    def test_durations_for_keeps_all_keys(self, tmp_path):
        """读不出的版本**键仍保留**（值 None）→ 键集与 audio_versions 一致"""
        good = _write_mp3(tmp_path / "1_鋼琴版.mp3")
        versions = {"鋼琴版": str(good), "人聲版": str(tmp_path / "missing.mp3"),
                    "_error": "元信息键应被剔除"}
        durations, issues = AD.durations_for(versions)
        assert set(durations) == {"鋼琴版", "人聲版"}
        assert durations["鋼琴版"] == pytest.approx(MP3_SECONDS, abs=0.05)
        assert durations["人聲版"] is None
        assert set(issues) == {"人聲版"}


# ================= db：v10 字段与统计 =================

class TestDbField:
    def test_ensure_field_idempotent_and_backfills_default(self, tmp_path):
        """旧库（无该列）→ 幂等补列；已有行按 DEFAULT '{}' 回填"""
        path = tmp_path / "old.db"
        conn = sqlite3.connect(path)
        c = conn.cursor()
        c.execute("CREATE TABLE tjc_hymn (id INTEGER PRIMARY KEY, hymn_number TEXT, "
                  "audio_versions TEXT DEFAULT '{}', audio_version_list TEXT DEFAULT '[]')")
        c.execute("INSERT INTO tjc_hymn (hymn_number) VALUES ('1')")
        assert db.ensure_audio_durations_field(c) is True
        assert db.ensure_audio_durations_field(c) is False  # 第二次不再添加
        assert c.execute("SELECT audio_durations FROM tjc_hymn").fetchone()[0] == "{}"
        conn.close()

    def test_create_table_has_audio_durations_after_version_list(self, tmp_path):
        conn = sqlite3.connect(tmp_path / "t.db")
        db._create_table_v4(conn.cursor())
        cols = [row[1] for row in conn.execute("PRAGMA table_info(tjc_hymn)")]
        conn.close()
        assert cols[cols.index("audio_version_list") + 1] == "audio_durations"

    def test_save_and_stats(self, tmp_path):
        path = _make_db(tmp_path, [("1", {"鋼琴版": "a.m4a", "人聲版": "b.mp3"}),
                                   ("2", {"鋼琴版": "c.m4a"})])
        assert db.save_audio_durations("1", {"鋼琴版": 100.5, "人聲版": None}, path) == 1
        st = db.audio_duration_stats(path)
        assert (st["rows"], st["filled"], st["entries"]) == (2, 1, 3)
        assert (st["entries_filled"], st["entries_null"]) == (1, 1)
        assert st["seconds_total"] == pytest.approx(100.5)
        assert st["mismatch"] == 1        # #2 未写时长 → 键集不一致
        assert st["by_version"]["鋼琴版"] == [1, pytest.approx(100.5)]


def _version_list(db_path, no):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT audio_version_list FROM tjc_hymn WHERE hymn_number = ?",
                           (no,)).fetchone()
    finally:
        conn.close()
    return json.loads(row[0])


def _set_version_list(db_path, no, values):
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE tjc_hymn SET audio_version_list = ? WHERE hymn_number = ?",
                 (json.dumps(values, ensure_ascii=False), no))
    conn.commit()
    conn.close()


# ================= tool/build_audio_durations.py =================

class TestBuildAudioDurations:
    def test_writes_one_to_one_with_version_list(self, tmp_path):
        piano = _write_mp3(tmp_path / "1_鋼琴版.mp3")
        vocal = _write_m4a(tmp_path / "1_人聲版.m4a", seconds=30.25)
        path = _make_db(tmp_path, [("1", {"鋼琴版": str(piano), "人聲版": str(vocal),
                                          "_error": "元信息键不进时长"})])

        summary = bad.run(db_path=path, root=str(tmp_path), quiet=True)

        assert (summary["hymns"], summary["entries"], summary["read"], summary["null"]) == (1, 2, 2, 0)
        assert summary["mismatch"] == [] and summary["issues"] == []
        durations = _durations(path, "1")
        assert set(durations) == set(_version_list(path, "1")) == {"鋼琴版", "人聲版"}
        assert durations["鋼琴版"] == pytest.approx(MP3_SECONDS, abs=0.05)
        assert durations["人聲版"] == pytest.approx(30.25, abs=0.001)

    def test_rerun_is_idempotent(self, tmp_path):
        piano = _write_mp3(tmp_path / "2_鋼琴版.mp3")
        path = _make_db(tmp_path, [("2", {"鋼琴版": str(piano)})])
        first = bad.run(db_path=path, root=str(tmp_path), quiet=True)
        second = bad.run(db_path=path, root=str(tmp_path), quiet=True)
        assert first["written"] == 1
        assert (second["written"], second["unchanged"]) == (0, 1)

    def test_missing_file_records_null_and_reports(self, tmp_path):
        """读不出的版本：键保留、值 null、原因进报告（不中断整批统计）"""
        path = _make_db(tmp_path, [("3", {"鋼琴版": str(tmp_path / "gone.m4a")})])
        summary = bad.run(db_path=path, root=str(tmp_path), quiet=True)
        assert (summary["read"], summary["null"]) == (0, 1)
        assert set(_durations(path, "3")) == {"鋼琴版"} and _durations(path, "3")["鋼琴版"] is None
        assert summary["issues"] and "不存在" in summary["issues"][0][2]

    def test_reports_key_mismatch_with_version_list(self, tmp_path):
        piano = _write_mp3(tmp_path / "4_鋼琴版.mp3")
        vocal = _write_mp3(tmp_path / "4_人聲版.mp3")
        path = _make_db(tmp_path, [("4", {"鋼琴版": str(piano), "人聲版": str(vocal)})])
        _set_version_list(path, "4", ["鋼琴版"])          # 人为制造不一致

        summary = bad.run(db_path=path, root=str(tmp_path), quiet=True)

        assert summary["mismatch"] == [("4", ["人聲版", "鋼琴版"], ["鋼琴版"])]
        assert db.audio_duration_stats(path)["mismatch"] == 1

    def test_dry_run_keeps_db(self, tmp_path):
        piano = _write_mp3(tmp_path / "5_鋼琴版.mp3")
        path = _make_db(tmp_path, [("5", {"鋼琴版": str(piano)})])
        summary = bad.run(db_path=path, root=str(tmp_path), dry_run=True, quiet=True)
        assert summary["written"] == 1 and _durations(path, "5") == {}

    def test_only_and_limit_select_rows(self, tmp_path):
        rows = [(str(i), {"鋼琴版": str(_write_mp3(tmp_path / f"{i}_鋼琴版.mp3"))})
                for i in (6, 7, 8)]
        path = _make_db(tmp_path, rows)
        bad.run(numbers=["7"], db_path=path, root=str(tmp_path), quiet=True)
        assert _durations(path, "6") == {} and _durations(path, "8") == {}
        assert _durations(path, "7")["鋼琴版"] > 0

        bad.run(limit=1, db_path=path, root=str(tmp_path), quiet=True)   # 只处理第 1 行
        assert _durations(path, "6")["鋼琴版"] > 0 and _durations(path, "8") == {}

    def test_legacy_db_gets_column_automatically(self, tmp_path):
        """旧库没有 audio_durations 列 → 工具先幂等补列再统计"""
        path = tmp_path / "legacy.db"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE tjc_hymn (id INTEGER PRIMARY KEY, hymn_number TEXT, "
                     "audio_versions TEXT DEFAULT '{}', audio_version_list TEXT DEFAULT '[]')")
        conn.execute("INSERT INTO tjc_hymn (hymn_number, audio_versions, audio_version_list) "
                     "VALUES ('9', '{}', '[]')")
        conn.commit()
        conn.close()
        rows = bad.load_rows(str(path))
        assert rows == [("9", "{}", "[]", "{}")]          # 补列后 DEFAULT '{}' 生效

    def test_show_and_stats(self, tmp_path, capsys):
        piano = _write_mp3(tmp_path / "10_鋼琴版.mp3")
        path = _make_db(tmp_path, [("10", {"鋼琴版": str(piano)})])
        bad.run(db_path=path, root=str(tmp_path), quiet=True)

        assert bad.show("10", path) == 0
        assert "0:03" in capsys.readouterr().out            # 2.61 秒 → 0:03（四舍五入）
        st = bad.stats(path)
        assert (st["filled"], st["entries_filled"], st["mismatch"]) == (1, 1, 0)
        assert bad.show("999", path) == 1                   # 库内无此编号

    def test_main_only_flag(self, tmp_path):
        piano = _write_mp3(tmp_path / "11_鋼琴版.mp3")
        path = _make_db(tmp_path, [("11", {"鋼琴版": str(piano)})])
        assert bad.main(["--only", "11", "--db", path, "--quiet"]) == 0
        assert _durations(path, "11")["鋼琴版"] == pytest.approx(MP3_SECONDS, abs=0.05)
        assert bad.main(["--stats", "--db", path]) == 0
        assert bad.main(["--show", "11", "--db", path]) == 0
