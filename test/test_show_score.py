#!/usr/bin/env python3
"""pytest 单元测试：tool/show_score.py（v9 曲谱 + 歌词 竖排对齐输出）

覆盖：
  - display_width / pad：东亚宽度按 2 列 —— 谱行（数字）与词行（汉字）能列对齐的前提
  - spread：**多字符记号（合成字形 `5-`）只占一列**；**行尾空列必须保留**（否则词行比谱行短、右边界错位）
    （回归锚点 ①：曾按 `notes` 串逐字符定位，导致 `5-` 之后的字整体右移一格
      回归锚点 ②：曾 rstrip 掉行尾空格，导致 #1 乐句 2 的 `│` 对不齐）
  - stanza_cells：第 1 节取几何对位（char 表），第 k 节按字序落到同一套列位模板
    （回归锚点：`hymn_score_char` 只存第 1 节 → 旧实现词①②③ 三行完全相同）
  - chorus_line_nos / norm_text：副歌判据（只有第 1 节词 + 与官网 `chorus` 某行吻合）与 #17 反例
  - 分页模式（`paged=True`）：每页一节的词、副歌行每页重复；官网副歌没对位到谱行时附在页尾
  - decode_elements：元素边界取自 `code_seq`（元素数 == 码位数）；未解码 → `?`；合成字形经 normalize_sym 归一
  - delta_note / parse_numbers / `--out` 写文件 + `--formfeed` 换页符
  - 真实库自检（有 tjc_hymn.db 时）：notes 串 == 逐元素解码结果；每个字的 note_index 都在元素范围内

运行: /home/zjx/python_env/bin/python -m pytest -c config/pytest.ini test/test_show_score.py -v
      （`tool/` 由下方 sys.path.insert 注入 → `import show_score` 行带 pyright ignore 注释）
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根（test/ 的上级）
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tool"))
os.chdir(ROOT)

import show_score as T  # pyright: ignore[reportMissingImports]

from crawler_core import db

DB = os.path.join(ROOT, "tjc_hymn.db")


def test_display_width_counts_cjk_as_two():
    assert T.display_width("聖哉") == 4
    assert T.display_width("5-") == 2
    assert T.display_width("?") == 1


def test_pad_uses_display_width():
    assert T.pad("1", 2) == "1 "
    assert T.pad("聖", 2) == "聖"
    assert T.pad("聖", 4) == "聖  "
    assert T.pad("聖哉", 2) == "聖哉"          # 不截断


def test_spread_keeps_multichar_symbol_in_one_column():
    """`5-` 是**一个**元素：后一列仍是第 2 个元素（按显示列验证，防合成字形被当两列）

    旧 bug：按 `notes` 串逐字符定位 → `5-` 之后的所有字右移一格（#14 L2「王」落到延长线上）。
    """
    notes = ["5-", "1", "-"]
    sheet = T.spread(notes, {i: c for i, c in enumerate(notes)}, 2)
    words = T.spread(notes, {0: "萬", 2: "王"}, 2)
    assert sheet == "5- 1  -  "                                # 每列 2 显示宽 + 1 间隔（行尾空格保留）
    assert T.display_width(sheet[:sheet.index("1")]) == 3       # 元素 1 落在第 2 列
    assert T.display_width(words[:words.index("萬")]) == 0      # 元素 0 的字落在第 1 列
    assert T.display_width(words[:words.index("王")]) == 6      # 元素 2 的字落在第 3 列


def test_spread_keeps_trailing_empty_columns_for_alignment():
    """回归锚点：谱行末尾是元素（延长线 `-`，非空），词行末尾常常"没有字"（空列）

    旧实现 rstrip 掉行尾空格 → 词行比谱行短 → 右侧 `│` 错位（#1 乐句 2 曾如此）。
    两行的显示宽度必须都等于 `元素数 × (列宽 + 1)`。
    """
    notes = ["5", "-", "-"]
    sheet = T.spread(notes, {i: c for i, c in enumerate(notes)}, 2)
    words = T.spread(notes, {0: "天"}, 2)
    assert T.display_width(sheet) == 9
    assert T.display_width(words) == 9            # 尾部两个空列仍占位
    assert words.startswith("天") and words.endswith("  ")


def test_stanza_cells_first_stanza_from_geometry_and_later_by_order():
    """第 1 节用几何对位（char 表），第 k 节按**字序**落到同一套列位

    回归锚点：`hymn_score_char` 只存第 1 节，旧实现把同一套 cells 打印给每一节
    → 词①/词②/词③ 三行完全一样（#1 曾如此）。
    """
    chars = [{"char_no": 1, "syllable": "聖", "note_index": 0},
             {"char_no": 2, "syllable": "哉", "note_index": 2}]
    assert T.stanza_cells(chars, "聖哉", 1) == {0: "聖", 2: "哉"}
    assert T.stanza_cells(chars, "鴻恩", 3) == {0: "鴻", 2: "恩"}
    assert T.stanza_cells(chars, "鴻恩遍宇內", 3) == {0: "鴻", 2: "恩"}   # 多的字无处可放


def test_norm_text_drops_punctuation_and_whitespace():
    assert T.norm_text("美哉，大哉！耶穌我主") == "美哉大哉耶穌我主"
    assert T.norm_text(" 5- ") == "5"


def _rec(lyrics):
    """构造 load_score 形状的最小记录（只用到 lyrics）"""
    return {"hymn": {}, "lines": [], "lyrics": lyrics, "chars": []}


def test_chorus_line_nos_requires_stanza1_only_and_official_match():
    """副歌判据：只有第 1 节 **且** 与官网 chorus 某行吻合（#12 的两行副歌）"""
    rec = _rec([
        {"line_no": 2, "stanza_no": 1, "text": "耶穌尊名入我耳中好像和諧美妙樂聲"},
        {"line_no": 2, "stanza_no": 2, "text": "耶穌尊名至高至榮超乎世間古今萬名"},
        {"line_no": 10, "stanza_no": 1, "text": "美哉大哉耶穌我主創造救贖權能無比"},
    ])
    chorus = "美哉，大哉，耶穌我主！創造救贖，權能無比；\n離去天上，降生世間，捨身替我受死。"
    assert T.chorus_line_nos(rec, chorus) == {10}


def test_chorus_line_nos_rejects_short_line_merely_contained_in_chorus():
    """反例 #17：L9「愛我」只有第 1 节，却不是副歌（官网副歌为「耶穌慈愛，我難計算…」）

    「愛我」恰好出现在「慈**愛我**難計算」里——若用无长度门槛的包含匹配就会误判，
    于是每页都重复一个两字的假副歌。短句（<6 字）不参与宽松匹配。
    """
    rec = _rec([
        {"line_no": 1, "stanza_no": 1, "text": "我深知道耶穌慈愛勝過世上一切"},
        {"line_no": 1, "stanza_no": 2, "text": "我深知道耶穌慈愛足解我心憂悶"},
        {"line_no": 9, "stanza_no": 1, "text": "愛我"},
    ])
    chorus = "耶穌慈愛，我難計算，永遠繼續不斷；\n耶穌慈愛，我難計算，只有常常頌讚。"
    assert T.chorus_line_nos(rec, chorus) == set()
    assert T.chorus_line_nos(rec, "") == set()          # 官网没副歌 → 不判


def test_decode_elements_uses_code_seq_boundaries():
    """元素数 = 码位数（`notes` 串字符数不可用于切分）"""
    line = {"notes": "121-", "code_seq": "4e52 4e53 4e56 5d1f"}
    mapping = {0x4E52: "1", 0x4E53: "2", 0x4E56: "3", 0x5D1F: "-"}
    out = T.decode_elements(line, mapping)
    assert out == ["1", "2", "3", "-"]
    assert len(out) == len(line["code_seq"].split())


def test_decode_elements_unknown_codepoint_is_question_mark():
    assert T.decode_elements({"notes": "?", "code_seq": "4f5a"}, {}) == ["?"]


def test_decode_elements_normalizes_composite_glyph():
    """库内映射存的是 PPT 字形 `t`，输出须归一成 `5-`（与入库时 notes 同口径）"""
    assert T.decode_elements({"notes": "5-", "code_seq": "5e0a"}, {0x5E0A: "t"}) == ["5-"]
    assert T.decode_elements({"notes": "5-", "code_seq": "5e0a"}, {}) == ["?"]


def test_decode_elements_without_code_seq_falls_back_to_notes():
    assert T.decode_elements({"notes": "121", "code_seq": ""}, {}) == ["1", "2", "1"]


def test_delta_note_semantics():
    assert "等长" in T.delta_note(0)
    assert "一字多音" in T.delta_note(2)
    assert "音符数不足" in T.delta_note(-1)


def test_parse_numbers_range():
    assert T.parse_numbers(["1-3", "349"]) == [1, 2, 3, "349"]


@pytest.mark.skipif(not os.path.exists(DB), reason="需要 tjc_hymn.db")
def test_real_db_lines_consistent_with_code_seq(capsys):
    """真实库自检（#1/#14）：元素切分与入库一致、逐字序号都在元素范围内、输出无错位告警"""
    mapping = T.load_mapping(DB)
    checked = 0
    for num in ("1", "14"):
        rec = db.load_score(num, DB)
        if not rec:
            continue
        for ln in rec["lines"]:
            if not ln["code_seq"]:
                continue
            elems = T.decode_elements(ln, mapping)
            assert len(elems) == len(ln["code_seq"].split())
            if all(int(cp, 16) in mapping for cp in ln["code_seq"].split()):
                assert "".join(elems) == ln["notes"], f"#{num} L{ln['line_no']} 记号串不一致"
            for c in rec["chars"]:
                if c["line_no"] == ln["line_no"] and c["note_index"] >= 0:
                    assert c["note_index"] < len(elems)
            checked += 1
        if T.show(num, DB, mapping=mapping):
            assert "记号串与入库时的 notes 不一致" not in capsys.readouterr().out
    if not checked:
        pytest.skip("库里还没有 #1/#14 的曲谱记录")


def _bar_column(line):
    """行首到**第二个** `│` 的显示宽度（谱行与词行相等即右边界对齐）"""
    head, _, rest = line.partition("│")
    body, _, _ = rest.partition("│")
    return T.display_width(head + "│" + body + "│")


@pytest.mark.skipif(not os.path.exists(DB), reason="需要 tjc_hymn.db")
def test_show_stanza_rows_differ_and_align_with_score_rows(capsys):
    """回归两个坑：#1 各节词各显其文 + 谱行/词行右边界对齐（乐句 2 曾错位）"""
    assert T.show("1", DB, mapping=T.load_mapping(DB))
    rows = [ln for ln in capsys.readouterr().out.splitlines() if "│" in ln]
    w1 = [ln for ln in rows if ln.lstrip().startswith("词①")]
    w2 = [ln for ln in rows if ln.lstrip().startswith("词②")]
    assert len(w1) == len(w2) == 4
    assert w1 != w2, "各节词行不该相同（旧 bug：都打第 1 节的词）"
    assert "群聖皆跪拜" in "".join(w2).replace(" ", "")
    score_rows = [ln for ln in rows if ln.lstrip().startswith("谱 L")]
    assert len(score_rows) >= len(w1)
    assert ([_bar_column(ln) for ln in score_rows[:len(w1)]]
            == [_bar_column(ln) for ln in w1]), "谱行与词行右边界未对齐"


@pytest.mark.skipif(not os.path.exists(DB), reason="需要 tjc_hymn.db")
def test_show_by_stanza_pages_and_repeats_chorus(capsys):
    """分页模式：#12 有 4 节 → 4 页；副歌行（标签 词ⓒ）每页重复；第 4 节的词要出现"""
    chorus = T.load_chorus(DB)
    assert T.show("12", DB, mapping=T.load_mapping(DB), chorus=chorus, paged=True)
    out = capsys.readouterr().out
    pages = [ln for ln in out.splitlines() if ln.startswith("🎼 #12")]
    assert len(pages) == 4
    assert "【第 4 页 / 共 4 页】词④" in pages[3]
    assert out.count("词ⓒ") >= 8                     # 2 行副歌 × 4 页
    assert "耶穌尊名至善至聖" in out.replace(" ", "")   # 第 4 节的词（旧实现没有）


@pytest.mark.skipif(not os.path.exists(DB), reason="需要 tjc_hymn.db")
def test_show_by_stanza_appends_official_chorus_when_not_aligned(capsys):
    """#17 副歌在曲谱里没有词行 → 每页页尾附官网文本，而不是每页重复「愛我」"""
    chorus = T.load_chorus(DB)
    assert T.show("17", DB, mapping=T.load_mapping(DB), chorus=chorus, paged=True)
    out = capsys.readouterr().out
    assert out.count("📌 副歌") == 3                  # 3 节 → 3 页，每页都附
    assert "永遠繼續不斷" in out
    assert "词ⓒ" not in out                          # 「愛我」不该被当成副歌


def test_main_by_stanza_writes_file_with_formfeed(tmp_path):
    """`--out` 写文件 + `--formfeed` 出换页符（给打印系统分页）"""
    if not os.path.exists(DB):
        pytest.skip("需要 tjc_hymn.db")
    dst = tmp_path / "1.txt"
    rc = T.main(["1", "--by-stanza", "--formfeed", "--out", str(dst)])
    assert rc == 0
    text = dst.read_text(encoding="utf-8")
    assert "【第 1 页 / 共 3 页】词①" in text
    assert text.count("\f") == 3
