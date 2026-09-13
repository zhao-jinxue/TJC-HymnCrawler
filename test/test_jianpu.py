#!/usr/bin/env python3
"""pytest 单元测试：PPT「带简谱文字歌词」提取（crawler_core.ppt_jianpu + db v8 两表）

覆盖：
  - 记号语义表：全库字符集必须被 NOTE_HEADS/修饰/休止/噪声 覆盖（防新增字符被静默漏计）
  - 计数规则：音符数（含半宽/高八度合成字形）/ 休止符 / 音节数（去标点）
  - 行解析：标题行（含旧版括号号、甲/乙版本标记、行尾 (三) 标签）、节标签、`k/M` 节号、尾部噪声
  - 归一比对：繁简 + 异体字折叠（爲→為）、标题→编号、歌词→正歌覆盖率
  - 曲调周期：P=1 / P=2 / 无周期
  - 真实 PPT（存在时）：#1 等长全通过、记号行文本与音符数符合官方简谱
  - DB v8：建表幂等、写库/读回、重复导入不残留、甲/乙同编号共存、统计

运行: /home/zjx/python_env/bin/python -m pytest -c config/pytest.ini test/test_jianpu.py -v
"""
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（test/ 的上级）
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from crawler_core import db
from crawler_core import ppt_jianpu as J

HAS_PPT = os.path.isdir(J.PPT_DIR) and bool(
    [f for f in os.listdir(J.PPT_DIR) if f.lower().endswith(".ppt")])
skip_no_ppt = pytest.mark.skipif(not HAS_PPT, reason="data/赞美诗PPT 未就位（大字库资源）")


# ===================== 记号语义 / 计数 =====================

def test_glyph_sets_disjoint():
    """音符/休止/修饰三组不得重叠（重叠会让计数规则自相矛盾）"""
    assert not (J.NOTE_HEADS & J.REST_CHARS)
    assert not (J.NOTE_HEADS & J.OVERLAY_MARKS)
    assert not (J.REST_CHARS & J.OVERLAY_MARKS)


def test_count_notes_and_syllables():
    # #1 第 1 行：11 个音符（含 `\\` 小节线、`/` 延长线不计）
    assert J.count_notes("1  1    3  3 \\ 5/5/\\6/6  6\\5/3/\\") == 11
    # #5 第 1 行：字母合成字形 = 音符 + 减时线；`//` 是延长线
    assert J.count_notes("qw \\ 3  5  3  2 \\ 1//") == 7
    # 半宽字形（a/d/f/g/h/j/s = 数字+三条减时线）也是音符；休止符 ` 不算
    assert J.count_notes("a d f g h j s") == 7
    assert J.count_notes("` 1 1") == 2
    assert J.count_rests("` 1 ` 1") == 2
    # 音节：汉字 1 字 1 音节，标点/空白不计；连续英文算 1 音节
    assert J.count_syllables("圣哉，圣哉，圣哉，全能大主宰！") == 11
    assert J.count_syllables("哈利 路亚，赞美 圣父！") == 8
    assert J.count_syllables("Amen 阿们") == 3          # 1 个英文词 + 2 个汉字


def test_noise_chars_detected():
    assert J.noise_chars("1 – 2 ︱ 3") == ["–", "︱"]
    assert J.noise_chars("1 2 3") == []


# ===================== 行 / 节 / 标题解析 =====================

def test_parse_header_variants():
    a = J.parse_header("349 (475) 、救主正在等待    F大调 3/4   ♩=126")
    assert (a["old_no"], a["new_no"], a["title"]) == ("349", "475", "救主正在等待")
    assert (a["key_sig"], a["time_sig"], a["tempo"]) == ("F大调", "3/4", "♩=126")
    b = J.parse_header("51 (甲)、万古灵磐   降D大调 3/4     ♩=100")
    assert (b["ver"], b["old_no"], b["title"]) == ("甲", "51", "万古灵磐")
    b2 = J.parse_header("51 (乙)万古灵磐       降B大调 3/4    ♩=84")
    assert (b2["ver"], b2["title"]) == ("乙", "万古灵磐")
    c = J.parse_header("23、圣经宝贵        D大调 3/2     ♩=80   (三)")
    assert (c["title"], c["tail_label"]) == ("圣经宝贵", "三")
    d = J.parse_header("31、主全为我    G大调 6/8     ♩.=50")
    assert d["title"].rstrip(".") == "主全为我"       # 速度符号残留需可容忍


def test_parse_slide_structure_and_label():
    text = ("5、万有赞美天父    降E大调 4/4\r"
            "        qw \\ 3  5  3  2 \\ 1//\r"
            "  万  有赞美天 父，\r"
            " (副歌)\r"
            "        we \\ 4  6  5  3 \\ 2//\r"
            "  永  在、全能之 神，\r"
            "6/6\r"
            "。\r")
    sl = J.parse_slide(text, 6)
    assert sl["marker_k"] == 6 and sl["marker_m"] == 6
    assert sl["label_kind"] == "chorus" and sl["label"] == "副歌"
    assert len(sl["pairs"]) == 2 and sl["orphans"] == []
    assert sl["pairs"][0]["notes"] == "qw \\ 3  5  3  2 \\ 1//"
    assert sl["pairs"][0]["lyric"] == "万  有赞美天 父，"
    assert sl["header"]["title"] == "万有赞美天父"


def test_tune_period():
    def slide(notes, no):
        return {"pairs": [{"notes": notes}], "stanza_no": no}
    assert J.tune_period([slide("1 2", 1), slide("1 2", 2)]) == (1, True)
    assert J.tune_period([slide("1 2", 1), slide("3 4", 2),
                          slide("1 2", 3), slide("3 4", 4)]) == (2, True)
    assert J.tune_period([slide("1 2", 1), slide("3 4", 2), slide("5 6", 3)]) == (0, False)
    # 末小节线有无属排版习惯，不算曲调差异
    assert J.tune_period([slide("1 2 \\", 1), slide("1 2", 2)]) == (1, True)
    assert J.tune_period([slide("1 2", 1)]) == (1, True)          # 单张幻灯片视为同调


# ===================== 归一比对 / 编号与归属 =====================

def test_normalize_variant_folding():
    assert J.normalize_text("尊主为王") == J.normalize_text("尊主為王")
    assert J.normalize_text("在花园里") == J.normalize_text("在花園裡")
    assert J.normalize_text("你") == J.normalize_text("禰") == J.normalize_text("祢")


def _idx(titles=None, verses=None, choruses=None):
    return {"titles": titles or {}, "verses": verses or {}, "choruses": choruses or {}}


def test_match_hymn_number_exact_and_fuzzy():
    titles = {J.normalize_text("尊主為王"): ["20"], J.normalize_text("快親近主"): ["103"]}
    assert J.match_hymn_number("尊主为王", _idx(titles))["title_match"] == "exact"
    got = J.match_hymn_number("快亲近神", _idx(titles))     # 一字之差 → 模糊候选
    assert got["title_match"] == "fuzzy" and got["hymn_number"] == "103"
    assert J.match_hymn_number("完全不存在曲名", _idx(titles))["title_match"] == "none"


def test_match_verse_and_chorus_coverage():
    verses = {"5": [J.normalize_text("萬有讚美天父，永在、全能之神，天地萬物互相應聲，讚美祂大尊名")]}
    no, cov = J.match_verse("万有赞美天父永在全能之神", verses, "5")
    assert no == 1 and cov >= 0.95
    _n, cov2 = J.match_verse("天地万物亙相应声赞美祂大尊名", verses, "5")   # 用字变体不压垮覆盖率
    assert cov2 >= 0.75
    chorus = {"354": J.normalize_text("只要信祂，只要信祂，今日信靠祂")}
    assert J.match_chorus("只要信祂只要信祂今日信靠祂", chorus, "354") >= 0.9


def test_resolve_by_lyrics():
    verses = {"13": [J.normalize_text("沒有人能比全能的耶穌沒有人沒有人")]}
    no, cov = J.resolve_by_lyrics("没有 人能比 全 能的耶稣", _idx(verses=verses))
    assert no == "13" and cov >= 0.9
    assert J.resolve_by_lyrics("完全不相干的一句歌词", _idx(verses=verses))[0] == ""


# ===================== DB v8 两表 =====================

def _fresh_db(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    conn = sqlite3.connect(path)
    c = conn.cursor()
    db._create_table_v4(c)
    db.ensure_jianpu_tables(c)
    c.execute("INSERT INTO tjc_hymn (hymn_number, title, verse_1, chorus) "
              "VALUES ('1', '頌讚獨一真神', '聖哉，聖哉，聖哉，全能大主宰！', '')")
    conn.commit()
    conn.close()
    return path


def _rec(ppt="001.ppt", no="1", **over):
    rec = {
        "ppt_file": ppt, "hymn_number": no, "version": "", "ppt_old_no": "1",
        "ppt_new_no": "", "title": "颂赞独一真神", "title_line": "1、颂赞独一真神",
        "key_sig": "降E大调", "time_sig": "4/4", "tempo": "♩=76", "slide_count": 1,
        "chorus_slides": 0, "pair_count": 1, "note_total": 11, "tune_period": 1,
        "title_match": "exact", "title_score": 1.0, "verse_match": "ok", "verse_score": 1.0,
        "marker_ok": 1, "structure_ok": 1, "tune_ok": 1, "align_ok": 1, "review_reason": "",
        "src_md5": "x" * 32, "extractor": J.EXTRACTOR,
        "lines": [{"stanza_no": 1, "line_no": 1, "verse_no": 1, "is_chorus": 0, "label": "",
                   "notes": "1  1    3  3 \\ 5/5/\\6/6  6\\5/3/\\",
                   "lyric": "圣哉，圣哉，圣哉，全能大主宰！",
                   "note_count": 11, "rest_count": 0, "syllable_count": 11,
                   "count_delta": 0, "align_ok": 1}],
    }
    rec.update(over)
    return rec


def test_ensure_tables_idempotent(tmp_path, monkeypatch):
    path = _fresh_db(tmp_path, monkeypatch)
    conn = sqlite3.connect(path)
    db.ensure_jianpu_tables(conn.cursor())          # 再执行一次不得报错
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    conn.close()
    assert {"hymn_jianpu", "hymn_jianpu_line", "idx_jianpu_number",
            "idx_jianpu_line_review"} <= names


def test_save_load_and_rewrite(tmp_path, monkeypatch):
    path = _fresh_db(tmp_path, monkeypatch)
    stats = db.save_jianpu_records([_rec()], db_path=path)
    assert stats["hymns"] == 1 and stats["lines"] == 1 and stats["skipped"] == []

    got = db.load_jianpu("1", path)
    assert len(got) == 1
    hymn, lines = got[0]["hymn"], got[0]["lines"]
    assert hymn["title"] == "颂赞独一真神" and hymn["note_total"] == 11
    assert lines[0]["notes"] == "1  1    3  3 \\ 5/5/\\6/6  6\\5/3/\\"
    assert (lines[0]["note_count"], lines[0]["syllable_count"]) == (11, 11)

    rec = _rec(pair_count=0, note_total=0)
    rec["lines"] = []                                # 重复导入且行数减少 → 行表重写、不留残行
    db.save_jianpu_records([rec], db_path=path)
    assert db.load_jianpu("1", path)[0]["lines"] == []
    assert db.jianpu_stats(path)["hymns"] == 1


def test_dual_versions_same_number(tmp_path, monkeypatch):
    """甲/乙两版同编号：主键为 ppt_file → 两份都保留、都能按编号取回"""
    path = _fresh_db(tmp_path, monkeypatch)
    db.save_jianpu_records([_rec(ppt="051a.ppt", no="51", version="甲"),
                            _rec(ppt="051b.ppt", no="51", version="乙")], db_path=path)
    got = db.load_jianpu("51", path)
    assert {g["hymn"]["ppt_file"] for g in got} == {"051a.ppt", "051b.ppt"}
    assert {g["hymn"]["version"] for g in got} == {"甲", "乙"}


def test_skip_parse_error(tmp_path, monkeypatch):
    path = _fresh_db(tmp_path, monkeypatch)
    bad = _rec(ppt="999.ppt", no="")
    bad["parse_error"] = "不是 OLE/CFB 复合文档"
    stats = db.save_jianpu_records([bad], db_path=path)
    assert stats["hymns"] == 0 and len(stats["skipped"]) == 1
    assert db.load_jianpu("999.ppt", path) == []


def test_jianpu_stats_none_when_absent(tmp_path):
    path = str(tmp_path / "empty.db")
    sqlite3.connect(path).close()
    assert db.jianpu_stats(path) is None


def test_init_db_creates_jianpu_tables(tmp_path, monkeypatch):
    """init_db()（v8）应幂等建出两表，且不打乱 tjc_hymn 的 28 列结构"""
    path = str(tmp_path / "init.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr(db, "PROBE_REPORT", str(tmp_path / "nope.json"))
    db.init_db()
    conn = sqlite3.connect(path)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    cols = [c[1] for c in conn.execute("PRAGMA table_info(tjc_hymn)")]
    conn.close()
    assert {"tjc_hymn", "hymn_jianpu", "hymn_jianpu_line"} <= names
    assert cols[-1] == "updated_at" and len(cols) == 28


# ===================== 真实 PPT（大字库资源就位时） =====================

# 474 个 PPT 记号行的全量字符集（2026-09-14 统计）——防新增字符被静默漏计
CORPUS_CHARS = " \t\\/58.3126etqw=490yi7-ru!|kQs`Pdhfgapj@W+#AoES)(_DR$T]–。︱:?"


def test_glyph_table_covers_corpus_charset():
    known = (J.NOTE_HEADS | J.REST_CHARS | J.NON_NOTE_COLUMNS
             | J.OVERLAY_MARKS | J.NOISE_CHARS)
    unknown = sorted({c for c in CORPUS_CHARS if not c.isspace() and c not in known})
    assert unknown == [], f"下列字符未归类（会让音符数失真）：{unknown}"


@skip_no_ppt
def test_real_ppt_001_fully_aligned():
    rec = J.parse_hymn_ppt(os.path.join(J.PPT_DIR, "001.ppt"), J.load_db_index())
    assert rec["hymn_number"] == "1" and rec["title_match"] == "exact"
    assert (rec["key_sig"], rec["time_sig"], rec["tempo"]) == ("降E大调", "4/4", "♩=76")
    assert rec["slide_count"] == 3 and rec["pair_count"] == 12
    assert rec["marker_ok"] and rec["structure_ok"] and rec["tune_ok"]
    assert rec["tune_period"] == 1 and rec["align_ok"] == 1
    first = rec["lines"][0]
    assert first["notes"] == "1  1    3  3 \\ 5/5/\\6/6  6\\5/3/\\"
    assert first["lyric"] == "圣哉，圣哉，圣哉，全能大主宰！"
    assert (first["note_count"], first["syllable_count"], first["count_delta"]) == (11, 11, 0)


@skip_no_ppt
def test_real_ppt_005_period_two_halves():
    """#5 一节正歌分两个半段（曲调周期 2）→ 幻灯片数 = 正歌节数 × 2"""
    rec = J.parse_hymn_ppt(os.path.join(J.PPT_DIR, "005.ppt"), J.load_db_index())
    assert rec["hymn_number"] == "5" and rec["slide_count"] == 6 and rec["tune_period"] == 2
    assert rec["tune_ok"] and rec["marker_ok"] and rec["structure_ok"]
    assert rec["lines"][0]["notes"] == "qw \\ 3  5  3  2 \\ 1//"
    assert rec["lines"][0]["note_count"] == 7
    assert rec["lines"][0]["count_delta"] > 0      # 首音节一字多音（官方简谱为 1̲2̲ 两音）


@skip_no_ppt
def test_real_ppt_351_dual_version_label():
    """甲/乙版本：PPT `51 (乙)万古灵磐` + 文件名 051b → DB `51_b`（DB 标题带 (乙) 后缀）"""
    rec = J.parse_hymn_ppt(os.path.join(J.PPT_DIR, "051b.ppt"), J.load_db_index())
    assert rec["version"] == "乙"
    assert rec["title"] == "万古灵磐" and rec["hymn_number"] == "51_b"
    rec_a = J.parse_hymn_ppt(os.path.join(J.PPT_DIR, "051a.ppt"), J.load_db_index())
    assert rec_a["version"] == "甲" and rec_a["hymn_number"] == "51_a"


@skip_no_ppt
def test_extract_all_only_filter():
    records, stats = J.extract_all(only=["349"])
    assert len(records) == 1 and records[0]["ppt_file"] == "349.ppt"
    assert records[0]["ppt_new_no"] == "475"       # PPT 内旧版编号 + 括号标注的新版号
    assert stats["files"] == 1