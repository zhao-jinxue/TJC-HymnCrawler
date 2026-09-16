#!/usr/bin/env python3
"""pytest 单元测试：tool/show_score.py（v9 曲谱 + 歌词 竖排对齐输出）

覆盖：
  - display_width / pad：东亚宽度按 2 列 —— 谱行（数字）与词行（汉字）能列对齐的前提
  - spread：**多字符记号（合成字形 `5-`）只占一列**
    （回归锚点：曾按 `notes` 串逐字符定位，导致 `5-` 之后的字整体右移一格）
  - decode_elements：元素边界取自 `code_seq`（元素数 == 码位数）；未解码 → `?`；合成字形经 normalize_sym 归一
  - delta_note / parse_numbers
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
    assert sheet == "5- 1  -"                                  # 每列 2 显示宽 + 1 间隔
    assert T.display_width(sheet[:sheet.index("1")]) == 3       # 元素 1 落在第 2 列
    assert T.display_width(words[:words.index("萬")]) == 0      # 元素 0 的字落在第 1 列
    assert T.display_width(words[:words.index("王")]) == 6      # 元素 2 的字落在第 3 列


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
