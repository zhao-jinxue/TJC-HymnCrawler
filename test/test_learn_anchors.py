#!/usr/bin/env python3
"""pytest 单元测试：拍位级跨源学习（crawler_core/pdf_score.py 的 learn_anchors 系列）

为什么单测这些纯函数：整条「码位 → 音级」映射靠它重建，而它的正确性完全取决于
几条判据（覆盖率闸门 / 区间等长 / 端点验证），这些判据的实现漂移会让**库里的记号
静默变错**——所以每条判据都要有独立的回归用例。

覆盖：
  - grade_of / grade_seq：记号 → 音级（合成字形归一、`?`/`#`/`-`/`|` → None）
  - ppt_grade_seq：剔除小节线/叠加修饰后只留可定音级的记号
  - anchor_blocks：最长公共连续段的锚点定位
  - _gap_votes / votes_from_pair：覆盖率闸门、区间等长、已知全等、端点验证
  - learn_elements：谱行元素的取舍（延长线/线类/已知非音级/紧贴未解码修饰 → 剔除）
  - 真实数据回归锚点（有 PDF 时）：#334 首行与它的 PPT 行对覆盖率 = 1.0

运行: /home/zjx/python_env/bin/python -m pytest -c config/pytest.ini test/test_learn_anchors.py -v
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


class Elem:
    """投票用的最小元素替身（`votes_from_pair` 只用到 `.cp`）"""

    def __init__(self, cp):
        self.cp = cp


def elems(*cps):
    return [Elem(cp) for cp in cps]


def test_grade_of_semantics():
    """音级提取：数字/归一后的合成字形 → 音级；修饰与未知 → None"""
    assert S.grade_of("1") == "1"
    assert S.grade_of("0") == "0"
    assert S.grade_of("5-") == "5"          # 减时线变体
    assert S.grade_of("2^---") == "2"       # 高八度点 + 三条减时线变体
    assert S.grade_of("t") == "5"           # PPT 合成字形（5 + 减时线）经 normalize_sym 归一
    assert S.grade_of("?") is None
    assert S.grade_of("") is None
    assert S.grade_of(None) is None
    for sym in ("#", "-", "|", "@"):
        assert S.grade_of(sym) is None, sym


def test_grade_seq_keeps_unknown_as_none():
    assert S.grade_seq(["1", "?", "3", "#"]) == ["1", None, "3", None]


def test_ppt_grade_seq_drops_line_marks_and_overlays():
    """PPT 侧：空格/小节线 `\\`/附点 `.`/减时线 `/` 不进序列，合成字形 `q` 归一成音级 1"""
    _syms, grades = S.ppt_grade_seq("3.w    q  w  3  5 / \\\\6.t  q  w \\\\ 3//\\\\")
    assert grades == ["3", "2", "1", "2", "3", "5", "6", "5", "1", "2", "3"]


def test_anchor_blocks_finds_longest_common_run():
    """#334 实测：PPT 首行（11 音级）与 PDF 首行的前 11 个音级完全一致"""
    pdf = list("3212356512321234")
    ppt = list("32123565123")
    assert S.anchor_blocks(pdf, ppt) == [(0, 0, 11)]


def test_anchor_blocks_unknown_is_not_wildcard():
    """未解码码位（None）不与任何记号相等：不会把两段「未知」凑成锚点"""
    assert S.anchor_blocks([None, None, None], ["3", "2", "1"]) == []


def test_gap_votes_requires_equal_length():
    assert S._gap_votes(elems(7, 8), ["5", None], ["5", "6"], 0, 2, 0, 1) == []
    assert S._gap_votes(elems(7, 8), ["5", None], ["5", "6"], 0, 2, 0, 2) == [(8, "6")]


def test_gap_votes_rejects_when_known_grade_differs():
    """区间内出现「已知音级不相等」→ 整段作废（对应关系已不是 1:1）"""
    assert S._gap_votes(elems(1), ["1", "9"], ["5", "6"], 0, 2, 0, 2) == []


def test_votes_from_pair_single_anchor_with_endpoint_check():
    """单锚点（`1123`，长度 4）+ 右端已知音级验证：中间两个未解码码位按 1:1 投票"""
    pdf = ["1", "1", "2", "3", None, None, "5"]
    ppt = ["1", "1", "2", "3", "4", "5", "5"]
    votes = S.votes_from_pair(elems(*range(7)), pdf, ppt)
    assert votes == [(4, "4"), (5, "5")]


def test_votes_from_pair_endpoint_mismatch_yields_nothing():
    """端点验证失败（PDF 的 `4` 对不上 PPT 的 `3`）→ 不投票，宁缺勿错"""
    pdf = ["1", "1", "2", None, None, "4"]
    ppt = ["1", "1", "2", "2", "3", "3"]
    assert S.votes_from_pair(elems(10, 11, 12, 13, 14, 15), pdf, ppt) == []


def test_votes_from_pair_low_coverage_is_rejected():
    """覆盖率闸门：只撞上 3 个音级的行对（假对应）绝不参与投票"""
    pdf = ["5", "5", "5", None, "2", "2", "7", "7"]
    ppt = ["5", "5", "5", "1", "1", "4", "4", "6"]
    assert S.votes_from_pair(elems(*range(8)), pdf, ppt) == []


def test_votes_from_pair_between_two_anchors():
    """两锚点（`199` 与 `5566`）夹出的等长区间 → 逐位投票；锚点本身不投票"""
    pdf = ["1", "9", "9", None, "5", "5", "6", "6"]
    ppt = ["1", "9", "9", "4", "5", "5", "6", "6"]
    assert S.votes_from_pair(elems(*range(8)), pdf, ppt) == [(3, "4")]


def _char(cp, x, ih=0.4, iw=0.2):
    """构造一个谱层字符（advance 框 7×28pt，中心 x = cx）"""
    return P.PdfChar(cp=cp, font=P.NOTE_FONT, size=28.0, x0=x - 3.5, y0=100.0,
                     x1=x + 3.5, y1=128.0, page=0, iw=iw, ih=ih)


def test_learn_elements_drops_non_beat_items():
    """谱行元素取舍：留音符与「拍位上的未解码」；剔延长线/线类/已知非音级/紧贴未解码"""
    mapping = {0x4E52: "1", 0x5D1F: "-", 0x602D: "|", 0x5D26: "#"}
    chars = [
        _char(0x4E52, 100),                     # 音符（留）
        _char(0x5D1F, 121, ih=0.046, iw=0.21),  # 延长线（剔）
        _char(0x602D, 142, ih=1.163, iw=0.03),  # 小节线（剔：线类）
        _char(0x5D26, 163, ih=0.246, iw=0.11),  # 升号（剔：已知非音级）
        _char(0x4F5A, 166, ih=0.44, iw=0.22),   # 未解码但紧贴上一个（剔：修饰）
        _char(0x4F5B, 187),                     # 未解码且在拍位上（留 → 待投票）
    ]
    row = P.SheetRow(y=100.0, page=0, chars=chars)
    out = S.learn_elements(row, mapping)
    assert [e.cp for e in out] == [0x4E52, 0x4F5B]
    assert S.grade_seq([e.sym for e in out]) == ["1", None]


PDF_334 = P.pdf_path("334")


@pytest.mark.skipif(not PDF_334, reason="需要 Hymn_Downloads 的 #334 简谱 PDF")
def test_real_pair_coverage_separates_true_from_false():
    """真实数据回归锚点：#334 首行 ↔ 它自己的 PPT 行覆盖率 1.0；与别的句子 ≤0.6

    这组数字是「覆盖率闸门」的实测依据（见会话日志 2026-09-16）——真对应 0.82~1.0、
    假对应 ≤0.6（多为 0），两者不重叠，闸门才敢设在 0.6。
    """
    recs = db.load_jianpu("334")
    if not recs:
        pytest.skip("库里还没有 #334 的 hymn_jianpu 记录")
    rows = P.melody_rows(P.read_chars(PDF_334))
    if not rows:
        pytest.skip("#334 未解析出谱行")
    mapping = {**S.MANUAL_SEED, **db.load_codepoint_map()}
    row = rows[0]
    pdf_grades = S.grade_seq([e.sym for e in S.learn_elements(row, mapping)])

    def coverage(notes):
        _s, pg = S.ppt_grade_seq(notes)
        if not pg:
            return 0.0
        anc = sum(n for _i, _j, n in S.anchor_blocks(pdf_grades, pg))
        known = sum(1 for g in pdf_grades if g)
        return anc / max(1, min(known, len(pg)))

    lines = recs[0]["lines"]
    best = max(coverage(ln.get("notes") or "") for ln in lines)
    assert best >= S.PAIR_COV_MIN
    assert "".join(g if g else "?" for g in pdf_grades).startswith("32123565123")
