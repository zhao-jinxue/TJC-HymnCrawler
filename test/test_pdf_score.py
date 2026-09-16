#!/usr/bin/env python3
"""pytest 单元测试：官方简谱 PDF → 「曲谱 + 歌词 + 拍位 + 逐字对应」（crawler_core.pdf_score，DB v9）

覆盖：
  - 拍位归一：按 x 间距累积（首元素第 1 拍、至少 1 拍、延长线占拍）
  - 延长线判据：极矮横线（ih ≤ 0.15）占时值；全宽连音线不算（已由 is_dot 剔除）
  - 乐句分组：同页 y 间隔 ≤40pt 同组，跨乐句分离
  - 歌词块归属：谱层下方窗口内取最近一块 + 字数众数剔除标题行
  - 主旋律层选择：去延长线元素数与歌词字数相等者优先
  - 逐字对位：字 ↔ 记号 / 拍位 / Δ / span（跳过延长线）
  - build_score：合成 PDF 字符（monkeypatch）→ 记录结构 + 等长标志
  - DB 往返：save_score_records → load_score / score_stats / 码位映射表
  - 真实 PDF（资源就位时）：#334 定位正确、歌词一致、等长行存在、逐字可靠率 >90%

运行: /home/zjx/python_env/bin/python -m pytest -c config/pytest.ini test/test_pdf_score.py -v
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（test/ 的上级）
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from crawler_core import db
from crawler_core import pdf_jianpu as P
from crawler_core import pdf_score as S

# 实测码位（#1/#334 官谱）：1/2/3/5/6 + 延长线
CP1, CP2, CP3, CP5, CP6 = 0x4E52, 0x4E53, 0x4E56, 0x4E59, 0x4E5C
CP_EXT = 0x5D1F                        # 延长线 `-`
CP_NEW = 0x4F5A                        # 未收录码位（验证「未解码 → `?`」路径）
NOTE_IH, HOLD_IH = 0.4, 0.046          # 音符 / 延长线的墨迹高（em）
MAP = {CP1: "1", CP2: "2", CP3: "3", CP5: "5", CP6: "6", CP_EXT: "-"}


def mk(cp, x, y=100.0, iw=0.22, ih=NOTE_IH, page=0):
    """造合成的简谱字符（bbox 宽 7pt、高 28pt，与实测一致）"""
    return P.PdfChar(cp=cp, font=P.NOTE_FONT, size=28.0, x0=x - 3.5, y0=y - 14,
                     x1=x + 3.5, y1=y + 14, page=page, iw=iw, ih=ih)


def hold(x, y=100.0, page=0):
    """造延长线字符（极矮横线）"""
    return mk(CP_EXT, x, y=y, iw=0.206, ih=HOLD_IH, page=page)


def cjk(ch, x, y=130.0, page=0):
    """造歌词汉字（14pt、非简谱字体 —— lyric_rows 只认该尺寸窗口内的汉字）"""
    return P.PdfChar(cp=ord(ch), font="DFKai-SB", size=14.0, x0=x - 7.0, y0=y - 7.0,
                     x1=x + 7.0, y1=y + 7.0, page=page, iw=0.5, ih=0.5)


def seq(cps, x0=100.0, y=100.0, pitch=21.0, page=0):
    """按 21pt 栅格造一串元素（`-` 造延长线，其余造音符）"""
    out = []
    for i, cp in enumerate(cps):
        x = x0 + pitch * i
        out.append(hold(x, y=y, page=page) if cp == "-" else mk(cp, x, y=y, page=page))
    return out


def elems_of(chars, mapping=None):
    """合成字符 → [ScoreElement]（写入拍位），mapping 缺省用本模块 MAP"""
    row = P.SheetRow(y=chars[0].cy, page=chars[0].page, chars=list(chars))
    return S._elements_of(row, MAP if mapping is None else mapping)


# ===================== 零、记号归一化 =====================

def test_normalize_sym_maps_ppt_composite_glyphs():
    """PPT 合成字形 → 简谱记号：`t`（5 + 一条减时线）→ `5-`；`h`（5 + 高八度 + 三条）→ `5^---`"""
    assert S.normalize_sym("t") == "5-"
    assert S.normalize_sym("q") == "1-"
    assert S.normalize_sym("T") == "5^-"
    assert S.normalize_sym("h") == "5^---"
    assert S.normalize_sym("5") == "5"          # 纯数字原样
    assert S.normalize_sym("-") == "-"          # 延长线原样
    assert S.normalize_sym("?") == "?"          # 未解码原样


# ===================== 一、拍位 =====================

def test_assign_beats_accumulates_from_x_gap():
    """拍位 = 首元素第 1 拍，其后按 x 间距 / 21pt 四舍五入累积（至少 1 拍）"""
    elems = [S.ScoreElement(index=i, cp=0, sym="1", x=x, y=0.0, iw=0.2, ih=NOTE_IH)
             for i, x in enumerate([100.0, 121.0, 142.0, 175.0])]
    S.assign_beats(elems)
    assert [e.beat for e in elems] == [1, 2, 3, 5]
    assert [e.span for e in elems] == [1, 1, 2, 1]


def test_assign_beats_hold_occupies_beat():
    """延长线占拍：`1 - 2` 的第二元素（延长线）拿第 2 拍，第三个元素第 3 拍"""
    elems = elems_of(seq([CP1, "-", CP2]))
    assert [e.beat for e in elems] == [1, 2, 3]
    assert [e.is_hold for e in elems] == [False, True, False]
    assert elems[1].sym == "-"


def test_is_hold_char_only_flat_line():
    """延长线判据只认「极矮且窄」：音符/全宽连音线都不是"""
    assert S.is_hold_char(hold(100.0))
    assert not S.is_hold_char(mk(CP1, 100.0))
    assert not S.is_hold_char(mk(0x5E61, 100.0, iw=0.930, ih=0.111)), "全宽连音线由 is_dot 剔除"


# ===================== 二、乐句 / 歌词块 / 主旋律 =====================

def test_phrase_groups_split_by_gap():
    """同一乐句内相邻谱层 ≈24pt 归同组；跨乐句 ≥90pt 分组（取自 #334 实测 y 值）"""
    rows = [P.SheetRow(y=y, page=0, chars=[]) for y in (179.5, 203.5, 299.6, 323.6)]
    groups = S.phrase_groups(rows)
    assert [[r.y for r in g] for g in groups] == [[179.5, 203.5], [299.6, 323.6]]


def test_lyric_block_for_keeps_mode_count():
    """歌词块取谱层下方窗口内的最近一块；块内只留字数众数的行（剔除标题行）"""
    phrase = [P.SheetRow(y=100.0, page=0, chars=[])]
    lyrics = [[cjk(ch, 100.0 + 20.0 * i, y=130.0) for i, ch in enumerate("一二三")],
              [cjk(ch, 100.0 + 20.0 * i, y=145.0) for i, ch in enumerate("四五六")],
              [cjk(ch, 100.0 + 20.0 * i, y=160.0) for i, ch in enumerate("七八")]]
    block = S.lyric_block_for(phrase, lyrics)
    assert ["".join(chr(c.cp) for c in ln) for ln in block] == ["一二三", "四五六"]


def test_pick_melody_prefers_equal_note_count():
    """主旋律层 = 去延长线元素数等于歌词字数的那层（其它声部差 >0）"""
    block = [[cjk(ch, 100.0 + 21.0 * i, y=200.0) for i, ch in enumerate("甲乙丙丁")]]
    top = P.SheetRow(y=100.0, page=0, chars=seq([CP1, CP2, CP3, "-", CP5]))       # core 4
    low = P.SheetRow(y=124.0, page=0, chars=seq([CP1, CP2, CP3, CP5, CP6, CP1]))  # core 6
    assert S.pick_melody([top, low], block) is top


# ===================== 三、逐字对位 =====================

def test_align_chars_maps_syllable_to_note_and_beat():
    """逐字对位：字 ↔ 记号 / 拍位；第 4 字对准第 5 个元素（跳过中间的延长线）"""
    chars = seq([CP1, CP2, CP3, "-", CP5])
    row = P.SheetRow(y=100.0, page=0, chars=chars)
    elems = S._elements_of(row, MAP)
    block = [[cjk("甲", 100.0), cjk("乙", 121.0), cjk("丙", 142.0), cjk("丁", 184.0)]]
    cells = S.align_chars(block, row.elements, elems)
    assert [c["syllable"] for c in cells] == list("甲乙丙丁")
    assert [c["note"] for c in cells] == ["1", "2", "3", "5"]
    assert [c["beat"] for c in cells] == [1, 2, 3, 5]
    assert all(c["align_ok"] for c in cells)


def test_align_chars_marks_multi_note_span():
    """字落在两元素中点附近（Δ 超容差）→ span=2（一字多音）且 align_ok=0，供人工复核"""
    chars = seq([CP1, CP2])
    row = P.SheetRow(y=100.0, page=0, chars=chars)
    elems = S._elements_of(row, MAP)
    mid = (chars[0].cx + chars[1].cx) / 2 + 1.0        # 靠近中点 → 一字跨两音
    cells = S.align_chars([[cjk("甲", mid, y=130.0)]], row.elements, elems)
    assert cells[0]["span"] == 2
    assert cells[0]["align_ok"] == 0


# ===================== 四、build_score（合成字符，不依赖真实 PDF） =====================

def test_build_score_synthetic(monkeypatch):
    """整体链路：谱行 + 歌词块 + 拍位 + 逐字对应 + 等长标志（monkeypatch 掉 PDF 解析）"""
    chars = seq([CP1, CP2, CP3, "-", CP5]) + [
        cjk(ch, x, y=130.0) for ch, x in zip("甲乙丙丁", (100.0, 121.0, 142.0, 184.0))]
    monkeypatch.setattr(S.P, "pdf_path", lambda n, root=None: "fake/334_简谱.pdf")
    monkeypatch.setattr(S.P, "read_chars", lambda path: chars)
    rec = S.build_score("334", mapping=MAP, min_elems=4)
    assert rec.line_count == 1 and rec.lyric_count == 1 and rec.phrase_count == 1
    ln = rec.lines[0]
    assert ln["notes"] == "123-5" and ln["notes_core"] == "1235"
    assert ln["beat_count"] == 5 and ln["note_count"] == 4 and ln["hold_count"] == 1
    assert ln["syllable_count"] == 4 and ln["count_delta"] == 0 and ln["align_ok"] == 1
    assert ln["is_primary"] == 1 and ln["part"] == "melody"
    assert rec.lyrics[0]["text"] == "甲乙丙丁" and rec.lyrics[0]["align_ok"] == 1
    assert [c["beat"] for c in rec.chars] == [1, 2, 3, 5]
    assert rec.beat_total == 5 and rec.syllable_total == 4 and rec.align_ok == 1


def test_build_score_reports_reason_instead_of_silence(monkeypatch):
    """没有 PDF / 码位未解码：都要在 review_reason 与 align_ok 上体现（不静默丢数据）"""
    monkeypatch.setattr(S.P, "pdf_path", lambda n, root=None: None)
    rec = S.build_score("999")
    assert rec.line_count == 0 and "未找到" in rec.review_reason

    chars = seq([CP_NEW, CP_NEW, CP_NEW, "-", CP_NEW]) + [
        cjk(ch, 100.0 + 21.0 * i, y=130.0) for i, ch in enumerate("甲乙丙丁")]
    monkeypatch.setattr(S.P, "pdf_path", lambda n, root=None: "fake/999_简谱.pdf")
    monkeypatch.setattr(S.P, "read_chars", lambda path: chars)
    rec = S.build_score("999", mapping={}, min_elems=4)
    assert "?" in rec.lines[0]["notes"]
    # 未解码只影响记号列可读性，不影响拍位/字数/逐字对位 → 不否定 align_ok
    assert "未解码" in rec.review_reason and rec.align_ok == 1


def test_manual_seed_decodes_334_first_line():
    """人工种子：按 #334 首行「3. 2 1 2 | 3 5 - | 6. 5 1 2 | 3 - - | 2. #1 2 3 | 4.」逐位对齐的码位表"""
    cps = [0x4E56, 0x4EE5, 0x4EE4, 0x4EE5, 0x4E56, 0x4E59, 0x5D1F, 0x4E5C,
           0x4EF0, 0x4EE4, 0x4EE5, 0x4E56, 0x5D1F, 0x5D1F, 0x4E53, 0x5D26,
           0x4EE4, 0x4EE5, 0x4EE8, 0x4E58]
    assert "".join(S.MANUAL_SEED[cp] for cp in cps) == "321235-65123--2#1234"
    assert [i for i, cp in enumerate(cps) if cp == 0x5D1F] == [6, 12, 13]


def test_build_score_no_lyric_block_is_not_counted_as_unequal(monkeypatch):
    """乐句找不到歌词块时只记「未找到歌词块」，不能算成「音符数不足」

    回归：早期实现按 `delta = core - 0` 判等长，会把**谱面本身无词**的乐句
    （实测约占四成，如 #334 的间奏段）误报为「音符数与字数不等」，进而把整首判 0。
    """
    chars = seq([CP1, CP2, CP3, "-", CP5])
    monkeypatch.setattr(S.P, "pdf_path", lambda n, root=None: "fake/1_简谱.pdf")
    monkeypatch.setattr(S.P, "read_chars", lambda path: chars)
    rec = S.build_score("1", mapping=MAP, min_elems=4)
    assert rec.line_count == 1 and rec.lyrics == []
    assert "未找到歌词块" in rec.review_reason
    assert "不足" not in rec.review_reason and "不等" not in rec.review_reason
    assert rec.lines[0]["count_delta"] == 0 and rec.align_ok == 1


# ===================== 五、DB 往返（v9 四表 + 码位映射表） =====================

def _rec():
    """一个最小可入库记录（结构与 pdf_score.ScoreRecord 输出一致）"""
    return {
        "hymn_number": "334", "pdf_path": "Hymn_Downloads/339_334耶穌沙崙玫瑰/334_简谱.pdf",
        "pdf_md5": "", "page_count": 1, "phrase_count": 1, "line_count": 1, "lyric_count": 1,
        "beat_total": 5, "syllable_total": 4, "align_ok": 1, "review_reason": "",
        "extractor": S.EXTRACTOR,
        "lines": [{"line_no": 1, "page": 0, "phrase_no": 1, "part": "melody",
                   "is_primary": 1, "y": 100.0, "x0": 100.0, "beat_count": 5,
                   "notes": "123-5", "notes_core": "1235", "code_seq": "4e52 4e53",
                   "note_count": 4, "hold_count": 1, "rest_count": 0,
                   "syllable_count": 4, "count_delta": 0, "align_ok": 1}],
        "lyrics": [{"line_no": 1, "stanza_no": 1, "text": "甲乙丙丁",
                    "syllable_count": 4, "align_ok": 1}],
        "chars": [{"line_no": 1, "char_no": 1, "syllable": "甲", "note_index": 0,
                   "note": "1", "beat": 1, "delta": 0.0, "span": 1, "align_ok": 1}],
    }


def test_save_and_load_score_roundtrip(tmp_path):
    """save_score_records → load_score：四表往返一致；重复写幂等；未知编号返回 None"""
    dbp = str(tmp_path / "t.db")
    stats = db.save_score_records([_rec()], dbp)
    assert stats == {"hymns": 1, "lines": 1, "lyrics": 1, "chars": 1, "skipped": []}
    got = db.load_score("334", dbp)
    assert got is not None
    assert got["hymn"]["syllable_total"] == 4 and got["hymn"]["align_ok"] == 1
    assert got["lines"][0]["notes"] == "123-5" and got["lines"][0]["count_delta"] == 0
    assert got["lyrics"][0]["text"] == "甲乙丙丁"
    assert got["chars"][0]["note"] == "1" and got["chars"][0]["beat"] == 1
    db.save_score_records([_rec()], dbp)                    # 幂等：明细表先删后插
    sstats = db.score_stats(dbp)
    assert sstats is not None and sstats["chars"] == 1
    assert db.load_score("999", dbp) is None


def test_save_score_skips_records_without_lines(tmp_path):
    """没有曲谱行的记录跳过并回报原因（不写空壳主行）"""
    dbp = str(tmp_path / "t.db")
    bad = _rec()
    bad["lines"], bad["review_reason"] = [], "未找到简谱 PDF"
    stats = db.save_score_records([bad], dbp)
    assert stats["hymns"] == 0 and stats["skipped"] == [("334", "未找到简谱 PDF")]
    assert db.load_score("334", dbp) is None


def test_codepoint_map_roundtrip(tmp_path):
    """码位映射表：空表返回 {}；写入后可按 int 码位读回（存储为十六进制文本）"""
    dbp = str(tmp_path / "t.db")
    assert db.load_codepoint_map(dbp) == {}
    saved = db.save_codepoint_map({CP1: "1", CP_EXT: "-"}, {CP1: ("1", 7, 9)}, db_path=dbp)
    assert saved == 2
    m = db.load_codepoint_map(dbp)
    assert m[CP1] == "1" and m[CP_EXT] == "-"


# ===================== 六、真实 PDF（资源就位时） =====================

PDF334, PDF349 = P.pdf_path(334), P.pdf_path(349)
skip_no_pdf = pytest.mark.skipif(not PDF334, reason="Hymn_Downloads 未就位（大字库资源）")


@skip_no_pdf
def test_real_pdf_334_is_the_right_hymn():
    """#334：必须解析到「耶穌沙崙玫瑰」那份 PDF（回归：旧 pdf_path 错配到「天父我神」）"""
    assert PDF334 is not None and PDF334.endswith("334_简谱.pdf")
    rec = S.build_score(334)
    texts = {ly["text"] for ly in rec.lyrics}
    assert texts, "应抽到歌词"
    assert any("耶穌沙崙玫瑰" in t for t in texts), f"歌词不对：{sorted(texts)[:3]}"
    assert all("天父我神" not in t for t in texts), "旧错配会把 329 号「天父我神」的谱当 #334"


@skip_no_pdf
def test_real_pdf_334_has_equal_lines_and_reliable_align():
    """#334：存在「去延长线音符数 == 歌词字数」的等长乐句；逐字几何对位可靠率 >90%"""
    rec = S.build_score(334)
    prim = [ln for ln in rec.lines if ln["is_primary"]]
    assert prim, "至少要识别出一个主旋律行"
    assert [ln for ln in prim if ln["count_delta"] == 0], "至少一个乐句要等长"
    cells = rec.chars
    assert cells
    assert len([c for c in cells if c["align_ok"]]) / len(cells) > 0.9


@pytest.mark.skipif(not PDF349, reason="Hymn_Downloads 未就位（大字库资源）")
def test_real_pdf_349_uses_its_own_file():
    """#349（奇妙的耶穌）：目录是 `354_349…`（首位 ≠ 编号），必须定位到自己那份 PDF

    实测该份 PDF 与网站一致但**无简谱文本层**（网站单独上传的图片版：`score/<hash>.pdf`，
    不是标准的 `num/349.pdf`）→ 抽取结果应是「0 行 + 明确原因」，而不是静默或拿别的诗。
    """
    rec = S.build_score(349)
    assert rec.pdf_path.endswith("349_简谱.pdf") and "354_349" in rec.pdf_path
    if rec.line_count:
        assert rec.lines and rec.lyric_count >= 0
    else:
        assert rec.review_reason, "无文本层必须给出原因（不静默）"
        assert "天父我神" not in rec.review_reason and "暴風雨" not in rec.review_reason