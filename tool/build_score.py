#!/usr/bin/env python3
"""tool/build_score.py — 从官方简谱 PDF 抽取「曲谱 + 歌词 + 拍位 + 逐字对应」并写入 v9 表

为什么以官方 PDF 为准（2026-09-15 定）：
  `Hymn_Downloads/<序号>_<编号><标题>/<编号>_简谱.pdf` 来自网站 sacredmusic.tjc.org.tw，
  是**矢量文本**：每个音符、每个字都带精确坐标 → 可算拍位、可逐字对位、可校验等长；
  `data/赞美诗PPT/*.ppt`（v8 `hymn_jianpu*`）是作者手工排版的笔记谱，只作旁证。

写库形态（v9；不动 `tjc_hymn` 与 `hymn_jianpu*`）：
  `hymn_score`        每首一行：PDF 路径 / 页数 / 乐句数 / 谱行数 / 拍数 / 字数 / 汇总校验
  `hymn_score_line`   每行谱一条：notes（曲谱串）+ notes_core（去延长线）+ 拍数 + 音符数/字数/等长
  `hymn_score_lyric`  每节歌词一条：text + 字数 + 是否与曲谱等长（一谱多词 → 多行）
  `hymn_score_char`   每个字一条：字 ↔ 记号 / 拍位 / Δ / span（2 = 一字多音）
  `hymn_codepoint_map` 码位 → 记号（MMP2005 是固定字体，学一次即可解码全部）

用法（项目根执行）：
  python tool/build_score.py --learn                # 先学码位映射（PDF 谱层 × PPT 行），再全量抽取入库
  python tool/build_score.py --only 334 349 1       # 只处理指定编号
  python tool/build_score.py --limit 20 --dry-run   # 抽样试跑，不写库
  python tool/build_score.py --stats                # 库内覆盖统计
  python tool/build_score.py --show 334             # 打印某首的曲谱/歌词/逐字对应（人读）
"""
import argparse
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from crawler_core import db, pdf_jianpu as P, pdf_score as S  # noqa: E402


def all_numbers():
    """全部可处理编号：以 PDF 文件索引为准（数据源是官方谱），按数字序排序"""
    def key(name):
        m = re.match(r"(\d+)", name)
        return (int(m.group(1)) if m else 10 ** 6, name)

    return sorted(P._pdf_index().keys(), key=key)


def learn_mapping(nums, limit=None, quiet=False):
    """跨源学习「码位 → 记号」：PDF 谱层 × PPT 记号行联合自举

    只在「PDF 与 hymn_jianpu 同时存在」的编号上学习（v8 表提供 PPT 侧的记号串）。
    返回 (learned, stats)；无样本时 ({}, {})。
    """
    samples, used = [], 0
    for num in nums:
        if limit and used >= limit:
            break
        path = P.pdf_path(num)
        recs = db.load_jianpu(num)
        if not path or not recs:
            continue
        samples.append((P.melody_rows(P.read_chars(path)), recs[0]["lines"]))
        used += 1
    if not samples:
        print("⚠️ 没有可用于学习的样本（需要 PDF 与 hymn_jianpu 同时存在）")
        return {}, {}
    t0 = time.time()
    learned, stats, hits = P.learn_across(samples)
    if not quiet:
        print(f"🔤 跨源学习：样本 {len(samples)} 首 / 同构命中 {hits} 处 / "
              f"学到 {len(learned)} 个码位 / 耗时 {time.time() - t0:.1f}s")
    return learned, stats


def show(num, db_path):
    """打印一首的曲谱 / 歌词 / 逐字对应（人读格式，用于抽样复核）"""
    rec = db.load_score(num, db_path)
    if not rec:
        print(f"#{num}：hymn_score 里没有记录（先跑 python tool/build_score.py --only {num}）")
        return 1
    h = rec["hymn"]
    print(f"\n{'=' * 78}\n#{num}  {h['pdf_path']}")
    print(f"  页 {h['page_count']} | 乐句 {h['phrase_count']} | 谱行 {h['line_count']} | "
          f"歌词行 {h['lyric_count']} | 拍 {h['beat_total']} | 字 {h['syllable_total']} | "
          f"align_ok={h['align_ok']}")
    if h["review_reason"]:
        print(f"  ⚠️ {h['review_reason']}")
    lyric_by_line, chars_by_line = {}, {}
    for ly in rec["lyrics"]:
        lyric_by_line.setdefault(ly["line_no"], []).append(ly)
    for c in rec["chars"]:
        chars_by_line.setdefault(c["line_no"], []).append(c)
    for ln in rec["lines"]:
        print(f"  {'★' if ln['is_primary'] else ' '} L{ln['line_no']:<3} 句{ln['phrase_no']:<3}"
              f"{ln['part']:<8} 拍{ln['beat_count']:<3} 音符{ln['note_count']:<3} "
              f"延长{ln['hold_count']:<2} 字{ln['syllable_count']:<3} "
              f"Δ{ln['count_delta']:+d} ok={ln['align_ok']}")
        print(f"      曲谱: {ln['notes']}")
        if ln["notes_core"]:
            print(f"      配字: {ln['notes_core']}")
        for ly in lyric_by_line.get(ln["line_no"], []):
            print(f"      词{ly['stanza_no']}: {ly['text']}")
        cells = chars_by_line.get(ln["line_no"], [])
        if cells:
            print("      逐字: " + " ".join(
                f"{c['syllable']}#{c['char_no']}({c['note'] or '?'}@{c['beat']}"
                f"Δ{c['delta']:.0f}{'*' if c['span'] == 2 else ''})" for c in cells))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="官方简谱 PDF → 曲谱/歌词/拍位/逐字对应（v9 入库）")
    ap.add_argument("numbers", nargs="*", help="诗歌编号（缺省 = 全部有 PDF 的编号）")
    ap.add_argument("--only", nargs="*", default=None, help="同位置参数，便于脚本里显式书写")
    ap.add_argument("--limit", type=int, default=None, help="最多处理多少首")
    ap.add_argument("--db", default=db.DB_PATH, help=f"数据库（默认 {db.DB_PATH}）")
    ap.add_argument("--learn", action="store_true", help="先学码位映射并写 hymn_codepoint_map")
    ap.add_argument("--learn-limit", type=int, default=120, help="学习用样本上限（默认 120 首）")
    ap.add_argument("--no-map", action="store_true", help="不使用库里的码位映射（记号列为 `?`）")
    ap.add_argument("--dry-run", action="store_true", help="只抽取与统计，不写库")
    ap.add_argument("--stats", action="store_true", help="只打印库内统计")
    ap.add_argument("--show", nargs="*", default=None, help="打印指定编号的抽取结果后退出")
    ap.add_argument("--quiet", action="store_true", help="不打印每首明细")
    args = ap.parse_args(argv)

    if args.stats:
        print(f"📊 {db.score_stats(args.db)}")
        return 0
    if args.show:
        for n in args.show:
            show(n, args.db)
        return 0

    nums = args.only or args.numbers or all_numbers()
    if args.limit:
        nums = nums[: args.limit]
    print(f"🎼 待处理 {len(nums)} 首：{nums[:6]}{' …' if len(nums) > 6 else ''}")

    mapping = {} if args.no_map else db.load_codepoint_map(args.db)
    if mapping:
        print(f"🔤 已载入库内码位映射 {len(mapping)} 个")
    if args.learn:
        learned, stats = learn_mapping(nums, args.learn_limit, quiet=args.quiet)
        if learned and not args.dry_run:
            n = db.save_codepoint_map(learned, stats, source="learned", db_path=args.db)
            print(f"💾 码位映射已写入 hymn_codepoint_map：{n} 条")
        mapping = {**mapping, **learned}

    records, t0 = [], time.time()
    for i, num in enumerate(nums, 1):
        rec = S.build_score(num, mapping=mapping)
        records.append(rec)
        if not args.quiet:
            print(f"  [{i}/{len(nums)}] #{num}: 乐句{rec.phrase_count} 谱行{rec.line_count} "
                  f"词{rec.lyric_count} 拍{rec.beat_total} 字{rec.syllable_total} "
                  f"ok={rec.align_ok}" + (f" | {rec.review_reason}" if rec.review_reason else ""))
    ok = sum(1 for r in records if r.align_ok)
    print(f"✅ 抽取完成：{len(records)} 首 / 全部校验通过 {ok} 首 / 耗时 {time.time() - t0:.1f}s")

    if args.dry_run:
        print("（--dry-run：未写库）")
        return 0
    saved = db.save_score_records(records, args.db)
    print(f"💾 已写入 hymn_score {saved['hymns']} 首 / hymn_score_line {saved['lines']} 行 / "
          f"hymn_score_lyric {saved['lyrics']} 行 / hymn_score_char {saved['chars']} 字")
    for num, reason in saved["skipped"][:10]:
        print(f"   跳过 {num}：{reason}")
    if len(saved["skipped"]) > 10:
        print(f"   …… 其余 {len(saved['skipped']) - 10} 首同上")
    print(f"📊 库内统计：{db.score_stats(args.db)}")
    print("🔍 复核：python tool/build_score.py --show <编号>")
    return 0


if __name__ == "__main__":
    main()