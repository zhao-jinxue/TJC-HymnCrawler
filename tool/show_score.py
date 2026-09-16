#!/usr/bin/env python3
# tool/show_score.py
# 🎼 v9「官方简谱曲谱」人读输出：从 tjc_hymn.db 读出来，把**曲谱行**与**对应歌词**按列对齐打印到终端。
#
# 为什么单独成工具（与 `tool/build_score.py --show` 的分工）：
#   - `build_score.py --show`：以**谱行**为主轴的技术明细（拍数/音符数/Δ/逐字一串），用于抽查抽取是否正常；
#   - `show_score.py`（本工具）：以**人读**为主轴 —— 谱行 × 歌词行**竖排对齐**，一眼看出「哪个字落在哪个音上」，
#     适合校对、贴进文档、或作为 API/前端出数据的参照。
#
# 对齐原理（**不重新对位，只做渲染**）：
#   `hymn_score_char.note_index` = 该字对应的元素在**行内元素序列中的序号**（抽取时由几何对位确定），
#   于是谱行与词行可以共用同一套列位置：第 i 列 = 第 i 个谱元素（音符 / 延长线 `-` / 未解码 `?`）。
#   元素边界取自 `hymn_score_line.code_seq`（空格分隔的**逐元素码位**）——
#   **不能**按 `notes` 串逐字符切分：合成字形（`5-`、`#1`）一个元素占两个字符，字符数与元素数不等。
#   记号用 `pdf_score.MANUAL_SEED` + 库内 `hymn_codepoint_map` 还原（与入库时同一来源，可逐字校验）。
#   列宽按**东亚显示宽度**计算（汉字 = 2 列），保证中英文混排不错位。
#
# 读的表（全部只读，不写库）：
#   hymn_score（封面信息）· hymn_score_line（谱行）· hymn_score_lyric（每节歌词）· hymn_score_char（逐字对应）
#
# 用法（项目根执行）：
#   python tool/show_score.py 1 14           # 指定编号（示例：第 1 首、第 14 首）
#   python tool/show_score.py 1-14           # 区间
#   python tool/show_score.py 1 --chars      # 追加逐字明细：字 / 记号 / 拍位 / 偏差 Δ
#   python tool/show_score.py 1 --all-parts  # 连和声声部一起打印（默认只打主旋律行）
#   python tool/show_score.py --list         # 列出库内已有曲谱的编号（含标题与校验状态）
#
# 退出码：0 正常；1 有编号未找到（可作 CI 断言用）。

import argparse
import os
import sqlite3
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DB_PATH = os.path.join(ROOT, "tjc_hymn.db")

from crawler_core import db
from crawler_core import pdf_score as S

# 行标签的显示宽度（`谱 L12` / `词⑩` 都要对齐到它）
LBL = 9
# 节号 → 带圈数字（超过 9 节用普通数字）
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
# 未解码码位的占位记号（与 pdf_score.UNKNOWN_SYM 同值）
UNKNOWN = "?"


def display_width(text):
    """终端显示宽度（东亚全宽记 2 列）"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in str(text))


def pad(text, width):
    """按显示宽度右侧补空格（不截断）"""
    text = str(text)
    return text + " " * max(0, width - display_width(text))


def spread(notes, cells, width):
    """(曲谱元素序列, {元素序号: 文本}, 列宽) → 单行对齐文本

    第 i 个元素占 `width` 列 + 1 列间隔；cells 里没有的列留空（如延长线下方无字）。
    """
    parts = []
    for i in range(len(notes)):
        parts.append(pad(cells.get(i, ""), width))
        parts.append(" ")
    return "".join(parts).rstrip()


def load_mapping(db_path=DB_PATH):
    """解码用映射：人工种子兜底 + 库内学到的（后者优先，与 `build_score` 入库时同序）"""
    return {**S.MANUAL_SEED, **db.load_codepoint_map(db_path)}


def decode_elements(line, mapping):
    """谱行 → 元素序列（按 `code_seq` 逐元素还原记号）

    why 不按 `notes` 串逐字符切分：合成字形（`5-`、`#1`）一个元素占两个字符，字符数 ≠ 元素数，
    而 `note_index` 是**元素序号**——按字符切会整体错位。
    `code_seq` 缺失（旧数据）时退化为逐字符，保证仍能打印。
    """
    cps = str(line.get("code_seq") or "").split()
    if not cps:
        return list(line.get("notes") or "")
    out = []
    for cp in cps:
        try:
            # 与入库同口径：先查映射，再归一化（PPT 合成字形 `t` → `5-`）
            out.append(S.normalize_sym(mapping.get(int(cp, 16)) or UNKNOWN))
        except ValueError:
            out.append(UNKNOWN)
    return out


def parse_numbers(tokens):
    """["1-14", "349"] → [1..14, 349]（与 tool/show_lyrics.py 同规则）"""
    out = []
    for tok in tokens:
        tok = tok.strip()
        if "-" in tok:
            a, _, b = tok.partition("-")
            if a.strip().isdigit() and b.strip().isdigit():
                out.extend(range(int(a), int(b) + 1))
                continue
        out.append(tok)
    return out


def load_titles(db_path=DB_PATH):
    """读 `tjc_hymn` 的「编号 → 标题」（表不存在返回 {}）"""
    conn = sqlite3.connect(db_path)
    try:
        return {str(r[0]): (r[1] or "") for r in conn.execute("SELECT hymn_number, title FROM tjc_hymn")}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def delta_note(delta):
    """count_delta（音符数 − 字数）→ 人读结论（三种取值对应三种语义）"""
    if delta == 0:
        return "✔ 一字一音等长"
    if delta > 0:
        return f"⚠️ 一字多音（多 {delta} 个音）"
    return f"❌ 音符数不足（少 {-delta} 个）"


def show(num, db_path=DB_PATH, with_chars=False, all_parts=False, width=None,
         titles=None, mapping=None):
    """打印一首：按乐句分组，谱行 + 每节歌词竖排对齐（返回 False 表示库里没这首）"""
    rec = db.load_score(num, db_path)
    if not rec:
        print(f"⚠️ #{num}：库里没有曲谱记录（先跑 python tool/build_score.py --only {num}）")
        return False
    mapping = mapping if mapping is not None else load_mapping(db_path)
    h, lines = rec["hymn"], rec["lines"]
    lyr_by_line, char_by_line = {}, {}
    for ly in rec["lyrics"]:
        lyr_by_line.setdefault(ly["line_no"], []).append(ly)
    for ch in rec["chars"]:
        char_by_line.setdefault(ch["line_no"], []).append(ch)

    print("═" * 78)
    print(f"🎼 #{num}  {(titles or {}).get(str(num), '')}".rstrip())
    print(f"   PDF：{h['pdf_path']}")
    print(f"   页 {h['page_count']} | 乐句 {h['phrase_count']} | 谱行 {h['line_count']} | "
          f"歌词行 {h['lyric_count']} | 拍 {h['beat_total']} | 字 {h['syllable_total']} | "
          f"校验{'通过' if h['align_ok'] else '待复核'}")
    if h["review_reason"]:
        print(f"   ⚠️ {h['review_reason']}")

    phrase = None
    for ln in lines:
        if not all_parts and not ln["is_primary"]:
            continue
        if ln["phrase_no"] != phrase:
            phrase = ln["phrase_no"]
            print("─" * 78)
            print(f"【乐句 {phrase}】")
        elems = decode_elements(ln, mapping)
        cells_chars = char_by_line.get(ln["line_no"], [])
        widths = ([display_width(c) for c in elems] +
                  [display_width(c["syllable"]) for c in cells_chars])
        w = max([width or 2] + widths)
        cells = {c["note_index"]: c["syllable"] for c in cells_chars if c["note_index"] >= 0}
        stanzas = lyr_by_line.get(ln["line_no"], [])
        head = f"{'谱' if ln['part'] == 'melody' else '和'} L{ln['line_no']}"
        d = ln["count_delta"]
        if not ln["syllable_count"]:
            verdict = ("　（和声声部，本身无词）" if not ln["is_primary"]
                       else "　（无词乐句：间奏或第二段旋律，不参与等长判定）")
        else:
            verdict = " " + delta_note(d)
        dtxt = f"{d:+d}" if d else "0"
        tail = (f"拍{ln['beat_count']} 音符{ln['note_count']} 延长{ln['hold_count']} "
                f"字{ln['syllable_count']} Δ{dtxt}")
        print(f"{pad(head, LBL)}│"
              f"{spread(elems, {i: c for i, c in enumerate(elems)}, w)}│ {tail}{verdict}")
        if "".join(elems) != ln["notes"]:
            print(f"{pad('  ⚠️', LBL)}│ 记号串与入库时的 notes 不一致"
                  f"（映射来源不同？notes={ln['notes']}）")
        for ly in stanzas:
            tag = "词" + (CIRCLED[ly["stanza_no"] - 1] if 1 <= ly["stanza_no"] <= 10
                          else str(ly["stanza_no"]))
            print(f"{pad(tag, LBL)}│{spread(elems, cells, w)}│")
        if with_chars and cells_chars:
            print(f"{pad('逐字', LBL)}│" + " ".join(
                f"{c['syllable']}#{c['char_no']}({c['note'] or '?'}@{c['beat']}"
                f"Δ{c['delta']:.0f}{'*' if c['span'] == 2 else ''})" for c in cells_chars))
        missing = [c for c in cells_chars if c["note_index"] < 0]
        if missing:
            print(f"{pad('  ⚠️', LBL)}│ 未对位的字："
                  + "、".join(f"{c['syllable']}#{c['char_no']}" for c in missing))
    print("═" * 78)
    return True


def list_hymns(db_path=DB_PATH):
    """列出库内已有曲谱的编号（编号 / 标题 / 谱行 / 词行 / 校验）"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT h.hymn_number, h.line_count, h.lyric_count, h.beat_total, h.syllable_total, "
            "       h.align_ok, t.title "
            "FROM hymn_score h LEFT JOIN tjc_hymn t ON t.hymn_number = h.hymn_number "
            "ORDER BY CAST(h.hymn_number AS INTEGER), h.hymn_number").fetchall()
    except sqlite3.OperationalError:
        print("❌ 库里还没有 v9 表（先跑 python tool/build_score.py）")
        return 1
    finally:
        conn.close()
    ok = sum(1 for r in rows if r["align_ok"])
    print(f"📚 库内有曲谱 {len(rows)} 首（校验通过 {ok} 首）")
    for r in rows:
        print(f"  #{pad(r['hymn_number'], 6)} {pad(r['title'] or '', 18)}"
              f" 谱行 {r['line_count']:>3} 词行 {r['lyric_count']:>3} "
              f"拍 {r['beat_total']:>4} 字 {r['syllable_total']:>4} "
              f"{'✔' if r['align_ok'] else '⚠️'}")
    print("🔍 查看某首：python tool/show_score.py <编号>")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="打印「官方简谱曲谱 + 对应歌词」（读 v9 表，竖排对齐）")
    ap.add_argument("numbers", nargs="*", help="诗歌编号或区间（如 1 14 / 1-14）")
    ap.add_argument("--db", default=DB_PATH, help=f"数据库（默认 {DB_PATH}）")
    ap.add_argument("--chars", action="store_true", help="追加逐字明细（字/记号/拍位/Δ）")
    ap.add_argument("--all-parts", action="store_true", help="连和声声部一起打印")
    ap.add_argument("--width", type=int, default=None, help="列宽（默认按内容自适应，最小 2）")
    ap.add_argument("--list", action="store_true", help="列出库内已有曲谱的编号")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"❌ 数据库不存在：{args.db}")
        return 1
    if args.list:
        return list_hymns(args.db)
    if not args.numbers:
        ap.print_help()
        return 1

    titles = load_titles(args.db)
    mapping = load_mapping(args.db)
    nums = parse_numbers(args.numbers)
    found = sum(1 for n in nums
                if show(n, args.db, args.chars, args.all_parts, args.width, titles, mapping))
    missing = len(nums) - found
    if missing:
        print(f"⚠️ 有 {missing} 个编号库里没有曲谱记录")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
