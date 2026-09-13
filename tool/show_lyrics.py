#!/usr/bin/env python3
# tool/show_lyrics.py
# 🔎 入库歌词复核工具：直接看 tjc_hymn.db 里某一首的「正歌 + 副歌」，并可与 api_raw 原文逐字比对。
#
# 用途（2026-09-13 加入）：人工确认「歌词是否缺副歌」这类问题的**取证工具**——
#   - `verse_*`：官网 API `lyrics[].text`（正歌）
#   - `chorus` ：官网 API `lyrics_chorus`（副歌；站点该字段为空时，库里就是空，非抓取丢失）
#
# 用法（项目根执行）：
#   python tool/show_lyrics.py 12            # 单首
#   python tool/show_lyrics.py 1-20          # 区间
#   python tool/show_lyrics.py 12 30 349     # 多首
#   python tool/show_lyrics.py --stats       # 全库副歌覆盖率统计
#   python tool/show_lyrics.py --no-chorus   # 列出官网未提供副歌的诗歌编号
#
# 退出码：0 正常；1 编号未找到或与 API 原文不一致（可作 CI 断言用）。

import argparse
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "tjc_hymn.db")


def _norm(text):
    """统一换行并去首尾空白（与 crawler_core/api_client.normalize_text 同规则）"""
    if not text:
        return ""
    return str(text).replace("\r\n", "\n").replace("\r", "\n").strip()


def _load_rows(numbers=None):
    """读库；numbers 为 None 时取全量，按诗词编号数值序返回"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        sql = "SELECT * FROM tjc_hymn"
        rows = [dict(r) for r in conn.execute(sql)]
    finally:
        conn.close()
    if numbers is not None:
        want = {str(n) for n in numbers}
        rows = [r for r in rows if r["hymn_number"] in want]
    rows.sort(key=lambda r: (len(r["hymn_number"]), r["hymn_number"]))
    return rows


def _api_lyrics(row):
    """api_raw → (API 正歌列表, API 副歌)；无 api_raw 返回 ([], "")"""
    raw = row.get("api_raw") or ""
    try:
        rec = json.loads(raw) if raw else {}
    except ValueError:
        rec = {}
    if not isinstance(rec, dict):
        rec = {}
    verses = []
    for item in rec.get("lyrics") or []:
        text = _norm(item.get("text") if isinstance(item, dict) else item)
        if text:
            verses.append(text)
    return verses, _norm(rec.get("lyrics_chorus"))


def show(row):
    """打印单首：正歌逐节 + 副歌 + 与 api_raw 的一致性结论"""
    db_verses = [_norm(row[f"verse_{i}"]) for i in range(1, 11)]
    db_verses = [v for v in db_verses if v]
    db_chorus = _norm(row["chorus"])
    api_verses, api_chorus = _api_lyrics(row)

    print("=" * 66)
    print(f"#{row['hymn_number']}  {row['title']}")
    print(f"   词：{row['lyricist']}    曲：{row['composer']}    "
          f"节数：{row['verse_count']}  副歌：{'有' if db_chorus else '无（官网 lyrics_chorus 为空）'}")
    for i, v in enumerate(db_verses, 1):
        print(f"   [{i}] " + v.replace("\n", "\n       "))
    if db_chorus:
        print("   【副歌】" + db_chorus.replace("\n", "\n           "))

    if api_verses or api_chorus:
        if db_verses == api_verses and db_chorus == api_chorus:
            print("   ✅ 与 api_raw（官网原始记录）逐字一致")
        else:
            print("   ❌ 与 api_raw 不一致："
                  f"节数 {len(db_verses)}/{len(api_verses)}，"
                  f"副歌 {len(db_chorus)}/{len(api_chorus)} 字")
            return False
    else:
        print("   ℹ️ 该行无 api_raw（DOM 时代数据），无法逐字比对")
    return True


def parse_numbers(tokens):
    """["1-20", "349"] → [1..20, 349]"""
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="查看入库歌词（正歌 + 副歌）并复核与官网 API 原文是否一致")
    parser.add_argument("numbers", nargs="*", help="诗词编号或区间（如 12 / 1-20 / 51_a）")
    parser.add_argument("--stats", action="store_true", help="输出全库副歌覆盖率统计")
    parser.add_argument("--no-chorus", action="store_true", help="列出官网未提供副歌（lyrics_chorus 为空）的编号")
    args = parser.parse_args(argv)

    if not os.path.exists(DB_PATH):
        print(f"❌ 数据库不存在：{DB_PATH}")
        return 1

    if args.stats or args.no_chorus:
        rows = _load_rows()
        with_chorus = [r["hymn_number"] for r in rows if _norm(r["chorus"])]
        without = [r["hymn_number"] for r in rows if not _norm(r["chorus"])]
        mismatch = [r["hymn_number"] for r in rows
                    if _api_lyrics(r)[1] != _norm(r["chorus"])]
        print(f"库内诗歌：{len(rows)} 首｜有副歌：{len(with_chorus)}｜无副歌：{len(without)}")
        print(f"与 api_raw 副歌不一致：{len(mismatch)} {mismatch[:10]}")
        if args.no_chorus:
            print("无副歌编号：" + ", ".join(without))
        return 0

    if not args.numbers:
        parser.print_help()
        return 1

    rows = _load_rows(parse_numbers(args.numbers))
    found = {r["hymn_number"] for r in rows}
    missing = [n for n in {str(t) for t in parse_numbers(args.numbers)} if n not in found]
    ok = all(show(r) for r in rows) if rows else False
    if missing:
        print(f"⚠️ 库中未找到编号：{', '.join(sorted(missing))}")
    return 0 if (ok and not missing) else 1


if __name__ == "__main__":
    sys.exit(main())
