#!/usr/bin/env python3
"""pytest 单元测试：官方简谱 PDF ↔ PPT 的「码位学习 + 逐字对位」（crawler_core.pdf_jianpu，POC）

覆盖：
  - 记号序列清洗：PPT notes 去空白/零宽修饰/小节线（小节线在 PDF 里不是文本）
  - 字符分类：空白占位、贴边行首行尾记号、附点/减时线（小标记）、时值元素
  - 结构同构判定：长度相等 + 同码位同记号；冲突即失败
  - 带跳过的对齐：线类跳过 / 紧贴修饰跳过 / 拍位上的新码位当场学习
  - 跨首联合学习：共享映射、冲突剔除、线类固化（被跳过 ≥2 次且从未成为音符）
  - 逐字对位：最近邻 + Δ、一字多音（居中于两元素）
  - 歌词块聚类、歌词行筛选、PDF 路径查找、字体子集前缀剥离
  - 真实 PDF（资源就位时）：#1 四个乐句组全命中且逐字对位 Δ≤容差

运行: /home/zjx/python_env/bin/python -m pytest -c config/pytest.ini test/test_pdf_jianpu.py -v
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（test/ 的上级）
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from crawler_core import pdf_jianpu as P

# 实测码位（#1 官谱）：1/2/3/4/5/6/7、延长线、休止符、5+减时线
CP1, CP2, CP3, CP4, CP5, CP6, CP7 = 0x4E52, 0x4E53, 0x4E56, 0x4E58, 0x4E59, 0x4E5C, 0x4E5D
CP_EXT, CP_REST = 0x5D1F, 0x5D4C            # 延长线 `/`、休止符 `` ` ``
CP_NEW = 0x4F5A                             # 未学码位
NOTE_IH, LINE_IH, DOT_IH = 0.4, 0.046, 0.073  # 音符/延长线/附点的墨迹高（em）


def mk(cp, x, y=100.0, iw=0.22, ih=NOTE_IH, page=0):
    """造合成 PdfChar（bbox 宽 7pt、高 28pt，与实测一致；墨迹尺寸可指定）"""
    return P.PdfChar(cp=cp, font=P.NOTE_FONT, size=28.0, x0=x - 3.5, y0=y - 14,
                     x1=x + 3.5, y1=y + 14, page=page, iw=iw, ih=ih)


def row(cps, y=100.0, page=0):
    """按 21pt 拍距造一行谱（x 从 100 起）"""
    return P.SheetRow(y=y, page=page,
                      chars=[mk(cp, 100.0 + 21.0 * i, y=y) for i, cp in enumerate(cps)])


def lyric(cp, x, y=130.0):
    """造合成歌词汉字（14pt、非简谱字体 —— lyric_rows 只认汉字尺寸的非简谱字符）"""
    return P.PdfChar(cp=cp, font="DFKai-SB", size=14.0, x0=x - 7.0, y0=y - 7.0,
                     x1=x + 7.0, y1=y + 7.0, page=0, iw=0.5, ih=0.5)


# ===================== 一、记号序列清洗 =====================

def test_ppt_symbols_strips_blank_overlay_and_barline():
    """PPT notes → 记号序列：去空白、零宽修饰、小节线/终止线（PDF 里不是文本）"""
    assert P.ppt_symbols("1  1    3  3 \\ 5/5/\\6/6  6\\5/3/\\") == list("11335/5/6/665/3/")
    assert P.ppt_symbols("5.t    5  5") == list("5t55")          # `.` 是零宽修饰
    assert P.ppt_symbols("") == []


def test_ppt_symbols_keeps_rest_and_sharp():
    """休止符与升号记号要保留（它们在 PDF 侧占独立时值）"""
    assert P.ppt_symbols("2qwe4tuy5/43/`") == list("2qwe4tuy5/43/`")


# ===================== 二、字符分类 =====================

def test_char_is_dot_by_ink_box():
    """「小标记」判定：极矮的横线（减时线）或又矮又窄的点（附点）；音符/延长线不算"""
    assert mk(CP1, 100.0, ih=DOT_IH, iw=0.073).is_dot          # 附点
    assert mk(CP1, 100.0, ih=0.02, iw=0.26).is_dot             # 减时线（极矮）
    assert not mk(CP1, 100.0).is_dot                            # 音符
    assert not mk(CP_EXT, 100.0, ih=LINE_IH, iw=0.206).is_dot   # 延长线（矮但更宽）
    assert not mk(CP1, 100.0, ih=0.0).is_dot                    # 墨迹未知 → 不误杀


def test_sheet_row_elements_filters_blank_dot_and_margin():
    """时值元素：剔除空白占位（0x20/0x3021）、小标记、贴边行首行尾记号"""
    chars = [mk(0x600B, 76.0, ih=1.13),                     # 行首双纵线（贴边、高瘦）
             mk(0x20, 83.0, ih=0.0, iw=0.0),                 # 空白占位
             mk(CP1, 100.0),
             mk(0x5D3D, 107.3, ih=DOT_IH, iw=0.073),         # 附点
             mk(CP3, 121.0),
             mk(0x602D, 522.0, ih=1.16)]                     # 行尾终止线（贴边）
    r = P.SheetRow(y=100.0, page=0, chars=chars)
    assert [c.cp for c in r.elements] == [CP1, CP3]
    assert [c.cp for c in r.dots] == [0x5D3D]
    assert [c.cp for c in r.edges] == [0x600B, 0x602D]


def test_melody_rows_filters_short_layers():
    """旋律行候选：元素数不足的装饰层（下层减时线）要被过滤掉"""
    chars = [mk(CP1, 100.0), mk(CP3, 121.0), mk(CP5, 142.0),
             mk(0x5E61, 105.0, y=104.0, ih=0.11, iw=0.93)]   # 下层减时线（另成一层）
    rows = P.melody_rows(chars, min_elems=4)
    assert all(len(r.elements) >= 4 for r in rows)


# ===================== 三、结构同构判定 =====================

def test_row_matches_requires_same_length_and_map():
    assert P.row_matches([CP1, CP3], list("13")) == {CP1: "1", CP3: "3"}
    assert P.row_matches([CP1, CP3], list("1")) is None           # 长度不等
    assert P.row_matches([CP1, CP1], list("13")) is None          # 同码位对两个记号
    assert P.row_matches([], []) is None
    # 多对一允许：两个码位映射到同一记号（高八度等变体）
    assert P.row_matches([CP1, CP_NEW], list("11")) == {CP1: "1", CP_NEW: "1"}
    # 新码位在严格匹配中当场学出（从零开始时两者都是新码位）
    assert P.row_matches([CP1, CP3], list("15")) == {CP1: "1", CP3: "5"}


# ===================== 四、带跳过的对齐 =====================

def test_align_sequence_skips_line_mark_and_adjacent_modifier():
    """线类（休止/记号）与紧贴前一元素的修饰可跳过；已映射码位必须逐位对上"""
    r = P.SheetRow(y=100.0, page=0, chars=[
        mk(CP1, 100.0), mk(CP3, 121.0),
        mk(CP_NEW, 124.0),                           # 紧贴前一元素（gap 3pt）→ 判为修饰跳过
        mk(CP_REST, 145.0),
    ])
    got = P.align_sequence(r.elements, list("13"), {CP1: "1", CP3: "3", CP_REST: P.LINE_MARK})
    assert got is not None and got[1] == [CP_NEW]


def test_align_sequence_learns_new_note_on_beat():
    """落在拍位上的未知码位 = 新音符：当场学出映射（破解「没映射就永远对不上」的死锁）"""
    r = P.SheetRow(y=100.0, page=0, chars=[mk(CP1, 100.0), mk(CP_NEW, 121.0)])
    got = P.align_sequence(r.elements, list("13"), {CP1: "1"})
    assert got is not None and got[0][CP_NEW] == "3" and got[1] == []


def test_align_sequence_fails_on_conflicting_symbol():
    r = P.SheetRow(y=100.0, page=0, chars=[mk(CP1, 100.0), mk(CP3, 121.0)])
    assert P.align_sequence(r.elements, list("15"), {CP1: "1", CP3: "3"}) is None


def test_match_rows_to_lines_strict_and_bootstrap():
    """严格匹配用于第一轮；给定映射后走带跳过的自举匹配"""
    rows = [row([CP1, CP3, CP5, CP6])]
    lines = [{"stanza_no": 1, "line_no": 1, "notes": "1 3 5 6", "lyric": "甲"},
             {"stanza_no": 1, "line_no": 2, "notes": "1 3 5 6 6", "lyric": "乙"}]
    strict = P.match_rows_to_lines(rows, lines, mapping=None)
    assert len(strict) == 1 and strict[0][2]["line_no"] == 1
    boot = P.match_rows_to_lines(rows, lines, mapping={CP1: "1", CP3: "3", CP5: "5", CP6: "6"})
    assert len(boot) == 1 and boot[0][2]["line_no"] == 1


# ===================== 五、跨首联合学习 =====================

def test_learn_codepoint_map_votes():
    m = P.learn_codepoint_map([(CP1, "1"), (CP1, "1"), (CP1, "2"), (CP3, "3")])
    assert m[CP1] == ("1", 2, 3)
    assert m[CP3] == ("3", 1, 1)


def test_learn_across_shares_map_between_songs():
    """跨首联合：干净首交出映射供其余首使用（单首里可能一行干净谱都没有）"""
    s1 = ([row([CP1, CP3, CP5, CP6])],
          [{"stanza_no": 1, "line_no": 1, "notes": "1 3 5 6", "lyric": "甲"}])
    s2 = ([row([CP2, CP4, CP6, CP7])],
          [{"stanza_no": 1, "line_no": 1, "notes": "2 4 6 7", "lyric": "乙"}])
    learned, stats, hits = P.learn_across([s1, s2], rounds=3)
    assert learned[CP1] == "1" and learned[CP2] == "2" and learned[CP7] == "7"
    assert hits >= 1 and stats[CP1][0] == "1"


def test_learn_across_drops_conflicting_codepoint():
    """同一码位在不同首被学成两种记号 → 判为冲突并剔除（防学习污染）"""
    s1 = ([row([CP1, CP3, CP5, CP6])],
          [{"stanza_no": 1, "line_no": 1, "notes": "1 3 5 6", "lyric": "甲"}])
    s2 = ([row([CP1, CP3, CP5, CP6])],
          [{"stanza_no": 1, "line_no": 1, "notes": "2 3 5 6", "lyric": "乙"}])
    learned, _stats, _hits = P.learn_across([s1, s2], rounds=3)
    assert CP1 not in learned


def test_learn_across_marks_line_class_after_repeated_skips():
    """反复被跳过且从未成为音符的码位 → 固化为线类（PDF 有、PPT 侧被剔除的记号）"""
    s = ([row([CP1, CP3, CP5, CP_REST])],
         [{"stanza_no": 1, "line_no": 1, "notes": "1 3 5", "lyric": "甲"}])
    learned, _stats, _hits = P.learn_across([s], rounds=3)
    assert learned.get(CP_REST) == P.LINE_MARK


def test_bootstrap_returns_matches_and_map():
    rows = [row([CP1, CP3, CP5, CP6])]
    lines = [{"stanza_no": 1, "line_no": 1, "notes": "1 3 5 6", "lyric": "甲"}]
    matches, learned = P.bootstrap(rows, lines)
    assert matches and learned[CP1] == "1"


def test_analyze_pairs_row_with_lyric_block_below():
    """analyze：命中行 + 其下方窗口内的歌词行 → 逐字对位（上方标题/调号不被当歌词）"""
    chars = [mk(cp, 100.0 + 21.0 * i) for i, cp in enumerate([CP1, CP3, CP5, CP6])]
    chars += [lyric(0x8056, 100.0, y=130.0), lyric(0x54C9, 121.0, y=130.0)]   # 谱行下方歌词
    chars += [lyric(0x8056, 100.0, y=60.0), lyric(0x54C9, 121.0, y=60.0)]     # 谱行上方标题
    lines = [{"stanza_no": 1, "line_no": 1, "notes": "1 3 5 6", "lyric": "圣哉"}]
    res = P.analyze(chars, lines)
    assert len(res["pairs"]) == 1
    pair = res["pairs"][0]
    assert len(pair["block"]) == 1 and len(pair["cells"]) == 2
    assert all(c.delta <= P.ALIGN_TOL for c in pair["cells"])
    assert pair["line"]["line_no"] == 1


# ===================== 六、逐字对位 =====================

def test_align_lyric_nearest():
    elems = row([CP1, CP3, CP5, CP6]).elements
    syl = [lyric(0x8056, 100.5), lyric(0x54C9, 121.0)]
    cells = P.align_lyric(syl, elems)
    assert [c.syllable for c in cells] == ["聖", "哉"]
    assert cells[0].index == 0 and cells[0].delta == pytest.approx(0.5)
    assert all(c.span == 1 for c in cells)


def test_align_lyric_marks_multi_note_span():
    """字落在两元素正中 → 一字多音（span=2），交由复核图人工终审"""
    elems = row([CP1, CP3, CP5]).elements          # x = 100 / 121 / 142
    syl = [lyric(0x8056, 110.5)]                   # 居中于 100 与 121
    cells = P.align_lyric(syl, elems)
    assert cells[0].span == 2


def test_align_lyric_without_elements():
    cells = P.align_lyric([lyric(0x8056, 100.0)], [])
    assert cells[0].index == -1 and cells[0].delta == -1.0


def test_lyric_blocks_split_by_gap():
    def blk(y):
        return [lyric(0x8056, 100.0, y=y), lyric(0x54C9, 120.0, y=y)]
    assert [len(b) for b in P.lyric_blocks([blk(200.0), blk(215.0), blk(260.0)], gap=20.0)] == [2, 1]


def test_lyric_rows_only_cjk_at_lyric_size():
    """歌词行只取汉字尺寸的 CJK 字符（24pt 标题大字、简谱字体都不算）"""
    chars = [lyric(0x8056, 100.0, y=200.0), lyric(0x54C9, 120.0, y=200.0),
             P.PdfChar(cp=0x8056, font="DFKai-SB", size=24.0, x0=100.0, y0=210.0, x1=124.0,
                       y1=234.0, page=0, iw=0.5, ih=0.5),
             mk(CP1, 100.0, y=220.0)]
    rows = P.lyric_rows(chars)
    assert len(rows) == 1 and [chr(c.cp) for c in rows[0]] == ["聖", "哉"]


# ===================== 七、路径与字体名 =====================

def test_pdf_path_lookup(tmp_path):
    d = tmp_path / "001_1頌讚獨一真神"
    d.mkdir()
    (d / "1_简谱.pdf").write_bytes(b"%PDF-1.4\n")
    assert P.pdf_path(1, root=str(tmp_path)).endswith("1_简谱.pdf")
    assert P.pdf_path(999, root=str(tmp_path)) is None
    assert P.pdf_path(1, root=str(tmp_path / "nope")) is None


def test_pdf_path_ignores_website_index_prefix(tmp_path):
    """目录首位是**网站列表序号**、不是诗歌编号（334 号诗的目录叫 `339_334耶穌沙崙玫瑰`）

    回归（2026-09-15）：旧实现拿编号做 `startswith("334_")` 会命中 `334_329天父我神`
    → 把整首「耶穌沙崙玫瑰」解析成「天父我神」（40 首抽样里 16 首错配、命中 0 处）。
    """
    d1 = tmp_path / "334_329天父我神"
    d1.mkdir()
    (d1 / "329_简谱.pdf").write_bytes(b"%PDF-1.4\n")
    d2 = tmp_path / "339_334耶穌沙崙玫瑰"
    d2.mkdir()
    (d2 / "334_简谱.pdf").write_bytes(b"%PDF-1.4\n")
    assert P.pdf_path(334, root=str(tmp_path)).endswith("334_简谱.pdf")
    assert P.pdf_path(329, root=str(tmp_path)).endswith("329_简谱.pdf")
    assert P.pdf_path(999, root=str(tmp_path)) is None


def test_pdf_path_accepts_letter_suffix(tmp_path):
    """带字母的编号（`51_b`）文件名同名，不做数字补零（目录名里也是 `51_b`）"""
    d = tmp_path / "052_51_b萬古靈磐乙"
    d.mkdir()
    (d / "51_b_简谱.pdf").write_bytes(b"%PDF-1.4\n")
    assert P.pdf_path("51_b", root=str(tmp_path)).endswith("51_b_简谱.pdf")


def test_full_width_line_is_dot():
    """全宽横线（连音线/减时线，实测 0.93×0.111 / 1.44×0.134）不占时值

    只按高度判（DOT_MAX_H=0.10）会漏掉它们（0.111 > 0.10）→ 混进拍位形成「幽灵拍」。
    """
    line = mk(0x5E61, 100.0, iw=0.930, ih=0.111)
    wide = mk(0x5E63, 140.0, iw=1.443, ih=0.134)
    note = mk(CP1, 120.0, iw=0.229, ih=0.374)
    hold = mk(CP_EXT, 160.0, iw=0.206, ih=0.046)      # 延长线：占时值，必须保留
    assert line.is_dot and wide.is_dot
    assert not note.is_dot
    assert not hold.is_dot, "延长线占时值，不能被当小标记剔除"
    r = P.SheetRow(y=100.0, page=0, chars=[line, note, wide, hold])
    assert [c.cp for c in r.elements] == [CP1, CP_EXT]


def test_melody_rows_drops_line_layers():
    """线类层不是谱行：小节线（ih≈1.13）/贴边双纵线（ih≈1.16）会自成 y 层且元素数够多

    它们混进候选后与 DB 行同构匹配必然失败，还会把线类码位当音符投票、污染跨首学习。
    判据是「层内超高元素占多数」——真实谱层偶尔夹带个别贴边记号仍要保留。
    """
    notes = row([CP1, CP2, CP3, CP4, CP5, CP6], y=100.0)
    bars = [mk(0x602D, 100.0 + 21.0 * i, y=140.0, iw=0.034, ih=1.163) for i in range(6)]
    mixed = row([CP1, CP2, CP3, CP4, CP5, CP6], y=180.0)
    mixed.chars.append(mk(0x602D, 250.0, y=180.0, iw=0.229, ih=1.120))
    ys = [r.y for r in P.melody_rows(notes.chars + bars + mixed.chars)]
    assert ys == [100.0, 180.0]


def test_font_name_strips_subset_prefix():
    """子集前缀是 6 字母 + `+`，各文件随机（实测 ABCDEE+ / BCDLEE+ / BCDMEE+ 都出现过）"""
    assert P._font_name("ABCDEE+MMP2005") == "MMP2005"
    assert P._font_name("BCDLEE+MMP2005") == "MMP2005"
    assert P._font_name("MMP2005") == "MMP2005"
    assert P._font_name("") == ""


# ===================== 八、真实 PDF（资源就位时）=====================

PDF1 = P.pdf_path(1)
skip_no_pdf = pytest.mark.skipif(not PDF1, reason="Hymn_Downloads 未就位（大字库资源）")


@skip_no_pdf
def test_real_pdf_hash1_hits_four_groups():
    """#1：四个乐句组全命中；学到 1/3/5/6 与延长线；逐字对位全部落在容差内"""
    from crawler_core import db
    recs = db.load_jianpu(1)
    if not recs:
        pytest.skip("hymn_jianpu_line 无 #1 记录（先跑 tool/extract_jianpu.py）")
    chars = P.read_chars(PDF1)
    res = P.analyze(chars, recs[0]["lines"])
    assert len(res["pairs"]) == 4, "四个乐句组都应配对到歌词块"
    learned = {cp: v[0] for cp, v in res["learned"].items()}
    assert learned.get(0x4E52) == "1" and learned.get(0x4E56) == "3"
    assert learned.get(0x5D1F) == "/"
    for pair in res["pairs"]:
        assert len(pair["block"]) >= 3, "一谱多词：歌词块应有多行"
        for c in pair["cells"]:
            assert c.index >= 0 and c.delta <= P.ALIGN_TOL

