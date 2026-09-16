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
# 按节分页（唱诗/打印用）：
#   python tool/show_score.py 12 --by-stanza              # 每页 = 全部谱行 + 该节词行
#   python tool/show_score.py 12 --by-stanza --out 12.txt # 输出到文件（可 lp/编辑器直接打印）
#   python tool/show_score.py 12 --by-stanza --formfeed   # 页尾追加换页符 \f（打印系统分页）
#
# 退出码：0 正常；1 有编号未找到（可作 CI 断言用）。
#
# 两个已修的渲染坑（2026-09-16）：
#   - **尾部裁剪**：`spread` 曾 rstrip 掉行尾空列 → 谱行末尾是延长线（非空）而词行末尾常无字，
#     两行宽度不等 → 右侧 `│` 错位（#1 乐句 2）。现在按元素数定宽输出，不裁剪。
#   - **各节词行同文**：`hymn_score_char` 只存**第 1 节**的几何对位，旧实现把同一套 cells
#     打印到每一节 → 词①/词②/词③ 三行完全一样。现在各节用自己的文本，
#     按第 1 节的 `note_index` 序列作列位模板落字（一谱多词：各节字数相等）。

import argparse
import os
import sqlite3
import sys
import unicodedata
from collections.abc import Collection
from typing import Any

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
# 副歌行的标签（与 `词①` 同显示宽度：`ⓒ` 与 `①` 都是东亚"模糊宽度"字符）
CHORUS_TAG = "词ⓒ"
# 乐句内分隔线 / 首尾双线
RULE = "─" * 78
DOUBLE = "═" * 78
# 换页符：`--formfeed` 时写进输出，打印系统（lp / 文本编辑器）据此分页
PAGE_FEED = "\f"


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

    why **不 rstrip**（回归锚点）：谱行末尾是元素（多为延长线 `-`，非空），词行末尾常常"没有字"
    （空列），若裁掉尾部空格，词行就比谱行短 → 右侧 `│` 错位（#1 乐句 2 曾出现）。
    所有行按 `len(notes)` 个列定宽输出，右边界才对得齐。
    """
    parts = []
    for i in range(len(notes)):
        parts.append(pad(cells.get(i, ""), width))
        parts.append(" ")
    return "".join(parts)


def cell_width(elems: list[str], chars: list[dict[str, Any]], width: int | None = None) -> int:
    """本行列宽：取「谱元素宽 / 字宽 / 下限（默认 2）」的最大值（东亚宽度按 2 列）"""
    widths = [display_width(c) for c in elems] + [display_width(c["syllable"]) for c in chars]
    return max([width or 2] + widths)


def line_stats(ln: dict[str, Any]) -> tuple[str, str]:
    """谱行 → (统计尾巴 `拍X 音符Y 延长Z 字W Δd`, 结论尾巴 `✔ 一字一音等长` / 无词说明)"""
    d = ln["count_delta"]
    dtxt = f"{d:+d}" if d else "0"
    tail = (f"拍{ln['beat_count']} 音符{ln['note_count']} 延长{ln['hold_count']} "
            f"字{ln['syllable_count']} Δ{dtxt}")
    if not ln["syllable_count"]:
        verdict = ("　（和声声部，本身无词）" if not ln["is_primary"]
                   else "　（无词乐句：间奏或第二段旋律，不参与等长判定）")
    else:
        verdict = " " + delta_note(d)
    return tail, verdict


def stanza_tag(stanza_no: int) -> str:
    """节号 → 行标签（`词①`；超过 10 节用普通数字）"""
    return "词" + (CIRCLED[stanza_no - 1] if 1 <= stanza_no <= 10 else str(stanza_no))


def stanza_numbers(rec: "db.ScoreRecord") -> list[int]:
    """曲谱里出现的节号（升序）——即正歌有几节（副歌不算节）"""
    return sorted({ly["stanza_no"] for ly in rec["lyrics"]})


def stanza_cells(chars: list[dict[str, Any]], text: str, stanza_no: int) -> dict[int, str]:
    """某节在某行的「列位 → 字」

    - 第 1 节：直接用 `hymn_score_char`（PDF 几何对位的结果，即库内的真值）
    - 第 k 节：**按字序**落到第 1 节的列位模板上

    为什么第 k 节能用模板：`hymn_score_char` 只有第 1 节（全库 473 首均如此），
    而一谱多词的标准唱法是各节字数相等（DB 侧各节的 `syllable_count` 一致），
    故「第 i 个字 ↔ 第 1 节第 i 个字的 `note_index`」成立。旧实现把第 1 节的 cells
    原样打印给每一节 → 词②词③显示的是第 1 节的词（肉眼像是"重复的假数据"）。
    """
    if stanza_no == 1:
        return {c["note_index"]: c["syllable"] for c in chars if c["note_index"] >= 0}
    slots = [c["note_index"] for c in sorted(chars, key=lambda c: c["char_no"])
             if c["note_index"] >= 0]
    syllables = [ch for ch in str(text or "") if not ch.isspace()]
    return {slot: syllables[i] for i, slot in enumerate(slots) if i < len(syllables)}


def norm_text(text: str) -> str:
    """归一化歌词文本：只留字母 / 数字 / 汉字（去标点、空白）

    用途：曲谱词行与官网 `chorus` 的**逐行比对**——同一句在两边的标点、断句不同，
    去标点后逐字相等才算同一句。
    """
    return "".join(ch for ch in str(text) if ch.isalnum())


def load_chorus(db_path=DB_PATH) -> dict[str, str]:
    """读 `tjc_hymn` 的「编号 → 官网副歌文本」（表/字段缺失返回 {}）"""
    conn = sqlite3.connect(db_path)
    try:
        return {str(r[0]): (r[1] or "") for r in conn.execute(
            "SELECT hymn_number, chorus FROM tjc_hymn")}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def chorus_line_nos(rec: "db.ScoreRecord", chorus: str) -> set[int]:
    """曲谱里属于**副歌**的行号集合（判据：只有第 1 节 + 与官网副歌某行吻合）

    why 双重判据：只凭"只有第 1 节"会把「第 2/3 节词缺失」的行也当成副歌
    （反例 #17 L9/L13「愛我」并非副歌，官网副歌是「耶穌慈愛，我難計算…」），
    故必须与 `tjc_hymn.chorus` 的某一行对得上：
    整行相等，或（≥6 字）互相包含——短句如「愛我」「阿們」不参与宽松匹配，避免误判。

    全库实测：271 首含"只有第 1 节"的行，其中 267 首的官网 chorus 非空且文本吻合。
    """
    if not chorus:
        return set()
    by_line: dict[int, set[int]] = {}
    for ly in rec["lyrics"]:
        by_line.setdefault(ly["line_no"], set()).add(ly["stanza_no"])
    targets = {norm_text(ln) for ln in str(chorus).splitlines()}
    targets.discard("")
    out = set()
    for ly in rec["lyrics"]:
        if ly["stanza_no"] != 1 or len(by_line.get(ly["line_no"], ())) != 1:
            continue
        t = norm_text(ly["text"])
        if t and any(t == c or (len(t) >= 6 and (t in c or c in t)) for c in targets):
            out.add(ly["line_no"])
    return out


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


def print_chorus_note(chorus_text: str, chorus_lines: Collection[int]) -> None:
    """页尾副歌说明；曲谱里没对位到副歌行时把官网副歌文本附上（每页都能照唱）"""
    if not chorus_text or chorus_lines:
        return
    print("📌 副歌（官网文本，曲谱里未对位到谱行）")
    for part in str(chorus_text).splitlines():
        if part.strip():
            print(f"   {part.strip()}")


def render_body(lines: list[dict[str, Any]], mapping: dict[int, str],
                lyr_by_line: dict[int, list[dict[str, Any]]],
                char_by_line: dict[int, list[dict[str, Any]]], *,
                all_parts: bool = False, width: int | None = None,
                with_chars: bool = False, only_stanza: int | None = None,
                chorus_lines: Collection[int] = ()) -> None:
    """打印主体：按乐句分组，谱行 + 词行竖排对齐

    - `only_stanza=None` → 每行打印**所有**节；= k → 只打第 k 节（分页模式）
    - `chorus_lines`（副歌行号）：不论当前哪一节都用第 1 节的词、标签 `词ⓒ` —— 副歌每页重复
    - `with_chars` 的逐字明细只对第 1 节打印：拍位 / Δ 来自 PDF 几何对位，只对第 1 节成立
    """
    phrase = None
    for ln in lines:
        if not all_parts and not ln["is_primary"]:
            continue
        if ln["phrase_no"] != phrase:
            phrase = ln["phrase_no"]
            print(RULE)
            print(f"【乐句 {phrase}】")
        elems = decode_elements(ln, mapping)
        cells_chars = char_by_line.get(ln["line_no"], [])
        w = cell_width(elems, cells_chars, width)
        head = f"{'谱' if ln['part'] == 'melody' else '和'} L{ln['line_no']}"
        tail, verdict = line_stats(ln)
        print(f"{pad(head, LBL)}│"
              f"{spread(elems, {i: c for i, c in enumerate(elems)}, w)}│ {tail}{verdict}")
        if "".join(elems) != ln["notes"]:
            print(f"{pad('  ⚠️', LBL)}│ 记号串与入库时的 notes 不一致"
                  f"（映射来源不同？notes={ln['notes']}）")
        slots = [c["note_index"] for c in cells_chars if c["note_index"] >= 0]
        is_chorus = ln["line_no"] in chorus_lines
        for ly in lyr_by_line.get(ln["line_no"], []):
            st = ly["stanza_no"]
            if is_chorus:
                if st != 1:
                    continue                      # 副歌只有一份词（记在第 1 节）
            elif only_stanza is not None and st != only_stanza:
                continue
            tag = CHORUS_TAG if is_chorus else stanza_tag(st)
            print(f"{pad(tag, LBL)}│"
                  f"{spread(elems, stanza_cells(cells_chars, ly['text'], st), w)}│")
            if st != 1:
                n_syl = sum(1 for ch in ly["text"] if not ch.isspace())
                if n_syl != len(slots):
                    print(f"{pad('  ⚠️', LBL)}│ {tag} 字数 {n_syl} ≠ 第 1 节 {len(slots)}"
                          f"（未对位的字不显示）")
            if st == 1 and cells_chars:
                if with_chars:
                    print(f"{pad('逐字', LBL)}│" + " ".join(
                        f"{c['syllable']}#{c['char_no']}({c['note'] or '?'}@{c['beat']}"
                        f"Δ{c['delta']:.0f}{'*' if c['span'] == 2 else ''})"
                        for c in cells_chars))
                missing = [c for c in cells_chars if c["note_index"] < 0]
                if missing:
                    print(f"{pad('  ⚠️', LBL)}│ 未对位的字："
                          + "、".join(f"{c['syllable']}#{c['char_no']}" for c in missing))


def show(num, db_path=DB_PATH, with_chars=False, all_parts=False, width=None,
         titles=None, mapping=None, chorus=None, paged=False, formfeed=False):
    """打印一首：`paged=False` 一页列全部节；`paged=True` 按节分页

    `paged=True` 时每页 = 全部谱行（主旋律）+ 该节词行；副歌行每页重复（标签 `词ⓒ`），
    若官网副歌在曲谱里没有对应词行，则每页页尾附官网副歌文本。返回 False 表示库里没这首。
    """
    rec = db.load_score(num, db_path)
    if not rec:
        print(f"⚠️ #{num}：库里没有曲谱记录（先跑 python tool/build_score.py --only {num}）")
        return False
    mapping = mapping if mapping is not None else load_mapping(db_path)
    h, lines = rec["hymn"], rec["lines"]
    lyr_by_line: dict[int, list[dict[str, Any]]] = {}
    char_by_line: dict[int, list[dict[str, Any]]] = {}
    for ly in rec["lyrics"]:
        lyr_by_line.setdefault(ly["line_no"], []).append(ly)
    for ch in rec["chars"]:
        char_by_line.setdefault(ch["line_no"], []).append(ch)
    title = (titles or {}).get(str(num), "")
    chorus_text = (chorus or {}).get(str(num), "")
    chorus_lines = chorus_line_nos(rec, chorus_text)
    stats = (f"   页 {h['page_count']} | 乐句 {h['phrase_count']} | 谱行 {h['line_count']} | "
             f"歌词行 {h['lyric_count']} | 拍 {h['beat_total']} | 字 {h['syllable_total']} | "
             f"校验{'通过' if h['align_ok'] else '待复核'}")

    if not paged:
        print(DOUBLE)
        print(f"🎼 #{num}  {title}".rstrip())
        print(f"   PDF：{h['pdf_path']}")
        print(stats)
        if chorus_text and chorus_lines:
            print(f"   🎵 副歌：曲谱行 {sorted(chorus_lines)}（标签 {CHORUS_TAG}）")
        if h["review_reason"]:
            print(f"   ⚠️ {h['review_reason']}")
        render_body(lines, mapping, lyr_by_line, char_by_line, all_parts=all_parts, width=width,
                    with_chars=with_chars, chorus_lines=chorus_lines)
        print_chorus_note(chorus_text, chorus_lines)
        print(DOUBLE)
        return True

    stanzas = stanza_numbers(rec) or [1]
    total = len(stanzas)
    for idx, st in enumerate(stanzas, 1):
        print(DOUBLE)
        print(f"🎼 #{num}  {title}　【第 {idx} 页 / 共 {total} 页】{stanza_tag(st)}")
        if idx == 1:
            print(f"   PDF：{h['pdf_path']}")
            print(stats)
            if h["review_reason"]:
                print(f"   ⚠️ {h['review_reason']}")
        if chorus_text:
            print("   🎵 副歌：" + (f"每页重复（标签 {CHORUS_TAG}）" if chorus_lines
                                   else "见本页页尾官网文本"))
        render_body(lines, mapping, lyr_by_line, char_by_line, all_parts=all_parts, width=width,
                    with_chars=with_chars, only_stanza=st, chorus_lines=chorus_lines)

        print_chorus_note(chorus_text, chorus_lines)
        print(RULE)
        print(f"　（第 {idx} 页 / 共 {total} 页结束）")
        if formfeed:
            print(PAGE_FEED)
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
    ap.add_argument("--by-stanza", action="store_true",
                    help="按节分页打印（每页=全部谱行+该节词行；副歌行每页重复）")
    ap.add_argument("--formfeed", action="store_true",
                    help="分页模式：每页末尾写换页符 \\f（打印系统据此分页）")
    ap.add_argument("--out", default=None, metavar="FILE",
                    help="把输出写入文件（便于存档/打印；默认打到终端）")
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
    chorus = load_chorus(args.db)
    nums = parse_numbers(args.numbers)
    if args.formfeed and not args.by_stanza:
        print("⚠️ --formfeed 只在 --by-stanza 分页模式下生效（本次忽略）")
    stream = open(args.out, "w", encoding="utf-8") if args.out else None
    old_stdout = sys.stdout
    if stream:
        sys.stdout = stream
    missing = 0
    try:
        found = sum(1 for n in nums
                    if show(n, args.db, args.chars, args.all_parts, args.width, titles, mapping,
                            chorus=chorus, paged=args.by_stanza, formfeed=args.formfeed))
        missing = len(nums) - found
        if missing:
            print(f"⚠️ 有 {missing} 个编号库里没有曲谱记录")
    finally:
        if stream:
            sys.stdout = old_stdout
            stream.close()
    if args.out:
        print(f"✅ 已写入 {args.out}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
